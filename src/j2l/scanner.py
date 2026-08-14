"""BLE scan and controller discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict

import asyncio
import subprocess

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
    """Scan for nearby Switch 2 controllers via BLE advertising.

    Returns
    -------
    Dict[str, DeviceInfo]
        MAC address (lowercase) → device metadata.
    """
    results: Dict[str, DeviceInfo] = {}

    # --- Try bleak active scan (may conflict with desktop BlueZ) ----------
    bleak_worked = False
    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            async with BleakScanner() as scanner:
                devices = await scanner.discover(
                    timeout=timeout, return_on_first_found=False, passive=True
                )
            bleak_worked = True
            break
        except BleakDBusError as e:
            if "InProgress" in str(e):
                if attempt < max_attempts - 1:
                    logger.warning("BlueZ scan conflict, retrying in 1.5s...")
                    await asyncio.sleep(1.5)
                    continue
                logger.info("BlueZ D-Bus busy — falling back to hcitool")
                break
            raise

    # --- Parse bleak results (if it worked) -------------------------------
    if bleak_worked:
        for dev in devices:
            info = _classify_device(dev)
            if info is not None:
                mac = dev.address.lower()
                results[mac] = info

    # --- Fallback: bluetoothctl (avoids D-Bus conflict) --------------------
    if not bleak_worked and not results:
        results = _scan_with_bluetoothctl(timeout=timeout)

    logger.info("BLE scan complete — found %d controller(s)", len(results))
    return results

    for dev in devices:
        info = _classify_device(dev)
        if info is not None:
            mac = dev.address.lower()
            results[mac] = info

    logger.info("BLE scan complete — found %d controller(s)", len(results))
    return results


def _classify_device(dev) -> DeviceInfo | None:
    """Decide whether *dev* is a known Switch 2 controller and which type."""

    # --- heuristic 1: manufacturer data contains Nintendo company ID ----------
    has_nintendo_mfr = False
    if dev.details and hasattr(dev.details, "manufacturer_data"):
        for _cid, data in dev.details.manufacturer_data.items():
            if _cid == NINTENDO_COMPANY_ID:
                has_nintendo_mfr = True
                break

    # --- heuristic 2: advertised service UUIDs contain the Switch 2 service --
    has_sw2_service = False
    if dev.details and hasattr(dev.details, "advertisement"):
        adv = dev.details.advertisement
        svc_uuids = getattr(adv, "service_uuids", []) or []
        for uuid_str in svc_uuids:
            if str(uuid_str).lower() == SW2_SERVICE_UUID.lower():
                has_sw2_service = True
                break
            # Some adapters expose the characteristic UUID directly
            if str(uuid_str).lower() == INPUT_REPORT_UUID.lower():
                has_sw2_service = True
                break

    if not has_nintendo_mfr and not has_sw2_service:
        return None

    # --- determine controller type -------------------------------------------
    # Check for rumble characteristic UUIDs in advertised services
    type_ = _infer_type(dev)

    name = dev.name if dev.name else "Unknown Controller"
    rssi = getattr(dev, "rssi", -127)

    return DeviceInfo(name=name, rssi=rssi, type=type_)


def _scan_with_bluetoothctl(timeout: int = 10) -> Dict[str, DeviceInfo]:
    """Fallback scanner using bluetoothctl CLI (avoids BlueZ D-Bus conflicts).
    
    bluetoothctl is pre-installed on Steam Deck / Bazzite and uses its own
    internal D-Bus connection, so it doesn't conflict with bleak.
    """
    results: Dict[str, DeviceInfo] = {}

    try:
        # Run bluetoothctl scan, wait for timeout, then list devices
        # We use a non-interactive approach: scan on/off with timeout
        import select
        
        proc = subprocess.Popen(
            ["bluetoothctl", "--timeout", str(timeout)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        # Send scan commands
        proc.stdin.write("scan on\n")
        proc.stdin.flush()
        
        # Wait for scan duration
        proc.stdin.write(f"exit\n")
        proc.stdin.flush()
        stdout, _ = proc.communicate(timeout=timeout + 10)
        
        # Parse discovered devices from output
        # Format: "<MAC> <Name>" lines in scan output
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Skip non-device lines
            if line.startswith("[NEW]") or "Scanning" in line or "Discovery" in line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            mac_candidate = parts[0].upper()
            # Validate MAC format (XX:XX:XX:XX:XX:XX)
            if ":" not in mac_candidate or len(mac_candidate) != 17:
                continue
            name = parts[1]
            name_lower = name.lower()
            if "joy-con" in name_lower or "pro controller" in name_lower:
                mac = mac_candidate.lower()
                type_ = ControllerType.PRO_CONTROLLER2
                if "left" in name_lower:
                    type_ = ControllerType.JOYCON2_LEFT
                elif "right" in name_lower:
                    type_ = ControllerType.JOYCON2_RIGHT
                results[mac] = DeviceInfo(name=name, rssi=-1, type=type_)
                logger.info("bluetoothctl found: %s %s (%s)", mac, name, type_.value)
                
    except FileNotFoundError:
        logger.warning("bluetoothctl not found; cannot fallback scan")
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning("bluetoothctl scan timed out after %ds", timeout)
    except Exception as e:
        logger.warning("bluetoothctl fallback failed: %s", e)

    return results


def _infer_type(dev) -> ControllerType:
    """Best-effort type inference from advertising data."""

    # Check advertised service UUIDs for rumble characteristic UUIDs
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

    # If device name contains a hint
    if dev.name:
        name_lower = dev.name.lower()
        if "joy-con" in name_lower and "left" in name_lower:
            return ControllerType.JOYCON2_LEFT
        if "joy-con" in name_lower and "right" in name_lower:
            return ControllerType.JOYCON2_RIGHT
        if "pro" in name_lower:
            return ControllerType.PRO_CONTROLLER2

    # Default fallback — will be refined after GATT discovery at connect time
    return ControllerType.PRO_CONTROLLER2
