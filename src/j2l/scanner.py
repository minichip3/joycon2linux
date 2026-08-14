"""BLE scan and controller discovery."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Dict

from bleak import BleakScanner
from bleak.exc import BleakDBusError

from j2l.protocol import (
    INPUT_REPORT_UUID,
    RUMBLE_JOYCON_L_UUID,
    RUMBLE_JOYCON_R_UUID,
    RUMBLE_PRO_UUID,
)

logger = logging.getLogger(__name__)

NINTENDO_COMPANY_ID = 0x0553

# Switch 2 service UUID (the service that holds INPUT_REPORT_UUID, etc.)
SW2_SERVICE_UUID = "ab7de9be-89fe-49ad-828f-118f09df7fd0"


class ControllerType(str, Enum):
    """Identify the Switch 2 controller family."""

    JOYCON2_LEFT = "joycon2_left"
    JOYCON2_RIGHT = "joycon2_right"
    PRO_CONTROLLER2 = "pro_controller2"


@dataclass
class DeviceInfo:
    """Summary of a discovered Nintendo controller."""

    name: str
    rssi: int
    type: ControllerType


async def scan(
    timeout: int = 10,
) -> Dict[str, DeviceInfo]:
    """Scan for nearby Switch 2 controllers.

    Strategy:
    1. Try bleak passive scan (may conflict with desktop BlueZ)
    2. On D-Bus InProgress, fall back to reading cached devices via
       ``bluetoothctl devices`` — Steam Deck/Bazzite already scans
       continuously, so Nintendo controllers are likely already cached.

    Returns
    -------
    Dict[str, DeviceInfo]
        MAC address (lowercase) → device metadata.
    """
    results: Dict[str, DeviceInfo] = {}

    # --- Try bleak passive scan ------------------------------------------
    bleak_worked = False
    for attempt in range(2):
        try:
            async with BleakScanner() as scanner:
                devices = await scanner.discover(
                    timeout=timeout, return_on_first_found=False, passive=True
                )
            bleak_worked = True
            for dev in devices:
                info = _classify_device(dev)
                if info is not None:
                    results[dev.address.lower()] = info
            break
        except BleakDBusError as e:
            if "InProgress" in str(e):
                if attempt == 0:
                    logger.warning("BlueZ D-Bus busy, retrying...")
                    await asyncio.sleep(1.5)
                    continue
                logger.info("BlueZ D-Bus busy — falling back to cached devices")
                break
            raise

    # --- Fallback: read cached devices from bluetoothctl ------------------
    if not bleak_worked and not results:
        results = _scan_cached_devices()

    logger.info("BLE scan complete — found %d controller(s)", len(results))
    return results


def _classify_device(dev) -> DeviceInfo | None:
    """Decide whether *dev* is a known Switch 2 controller and which type."""
    has_nintendo_mfr = False
    if dev.details and hasattr(dev.details, "manufacturer_data"):
        for _cid, _data in dev.details.manufacturer_data.items():
            if _cid == NINTENDO_COMPANY_ID:
                has_nintendo_mfr = True
                break

    has_sw2_service = False
    if dev.details and hasattr(dev.details, "advertisement"):
        adv = dev.details.advertisement
        svc_uuids = getattr(adv, "service_uuids", []) or []
        for uuid_str in svc_uuids:
            if str(uuid_str).lower() == SW2_SERVICE_UUID.lower():
                has_sw2_service = True
                break
            if str(uuid_str).lower() == INPUT_REPORT_UUID.lower():
                has_sw2_service = True
                break

    if not has_nintendo_mfr and not has_sw2_service:
        return None

    type_ = _infer_type(dev)
    name = dev.name if dev.name else "Unknown Controller"
    rssi = getattr(dev, "rssi", -127)
    return DeviceInfo(name=name, rssi=rssi, type=type_)


def _scan_cached_devices() -> Dict[str, DeviceInfo]:
    """Read Nintendo controllers from BlueZ's cached device list.

    Uses ``bluetoothctl devices`` which queries D-Bus for already-discovered
    devices without starting a new scan, avoiding the InProgress conflict.
    """
    results: Dict[str, DeviceInfo] = {}

    try:
        proc = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, text=True, timeout=10,
        )
        # Output format: "Device AA:BB:CC:DD:EE:FF DeviceName"
        for line in proc.stdout.strip().splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) < 3 or parts[0] != "Device":
                continue
            mac_raw = parts[1]
            name = parts[2]
            # Validate MAC format
            if ":" not in mac_raw or len(mac_raw) != 17:
                continue
            mac = mac_raw.lower()
            name_lower = name.lower()
            if "joy-con" not in name_lower and "pro controller" not in name_lower:
                continue
            type_ = ControllerType.PRO_CONTROLLER2
            if "left" in name_lower:
                type_ = ControllerType.JOYCON2_LEFT
            elif "right" in name_lower:
                type_ = ControllerType.JOYCON2_RIGHT
            results[mac] = DeviceInfo(name=name, rssi=-1, type=type_)
            logger.info("Cached device: %s %s (%s)", mac, name, type_.value)
    except FileNotFoundError:
        logger.warning("bluetoothctl not found")
    except Exception as e:
        logger.warning("Cached device scan failed: %s", e)

    return results


def _infer_type(dev) -> ControllerType:
    """Best-effort type inference from advertising data."""
    if dev.details and hasattr(dev.details, "advertisement"):
        adv = dev.details.advertisement
        svc_uuids = set(
            str(u).lower() for u in (getattr(adv, "service_uuids", []) or [])
        )
        if RUMBLE_JOYCON_L_UUID.lower() in svc_uuids:
            return ControllerType.JOYCON2_LEFT
        if RUMBLE_JOYCON_R_UUID.lower() in svc_uuids:
            return ControllerType.JOYCON2_RIGHT
        if RUMBLE_PRO_UUID.lower() in svc_uuids:
            return ControllerType.PRO_CONTROLLER2

    if dev.name:
        name_lower = dev.name.lower()
        if "joy-con" in name_lower and "left" in name_lower:
            return ControllerType.JOYCON2_LEFT
        if "joy-con" in name_lower and "right" in name_lower:
            return ControllerType.JOYCON2_RIGHT
        if "pro" in name_lower:
            return ControllerType.PRO_CONTROLLER2

    return ControllerType.PRO_CONTROLLER2
