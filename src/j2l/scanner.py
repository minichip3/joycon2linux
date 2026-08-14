"""BLE scan and controller discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict

from bleak import BleakScanner

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

    async with BleakScanner() as scanner:
        devices = await scanner.discover(timeout=timeout, return_on_first_found=False)

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
