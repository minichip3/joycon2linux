"""BLE scan and controller discovery."""

from __future__ import annotations

import asyncio
import logging
import os
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

    Steam Deck / Bazzite에서 desktop 환경(GNOME)이 BlueZ 스캔을 계속 차지하고
    있어서 bleak의 D-Bus 스캔이 ``InProgress`` 에러를 발생시킨다.

    해결책: ``bluetoothctl`` interactive 모드로 직접 스캔하고, ``[NEW]`` 라인을
    파싱하여 결과를 수집함.

    Returns
    -------
    Dict[str, DeviceInfo]
        MAC address (lowercase) → device metadata.
    """
    return await _scan_with_bluetoothctl(timeout=timeout)


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


async def _scan_with_bluetoothctl(timeout: int = 10) -> Dict[str, DeviceInfo]:
    """Scan via ``bluetoothctl`` interactive mode, parsing ``[NEW]`` lines.

    bluetoothctl uses the user D-Bus session bus. When run under sudo we
    must pass ``DBUS_SESSION_BUS_ADDRESS`` from the real user (``deck`` on
    Steam Deck). Without it bluetoothctl refuses to connect.

    Output format:
        [NEW] Device AA:BB:CC:DD:EE:FF DeviceName
    """
    results: Dict[str, DeviceInfo] = {}

    # Preserve user D-Bus session for bluetoothctl (needed under sudo)
    env = dict(os.environ)
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        # Under sudo, user D-Bus session is /run/user/1000/bus (Steam Deck uid)
        for uid_dir in [str(os.getuid()), "1000"]:
            sock = f"/run/user/{uid_dir}/bus"
            if os.path.exists(sock):
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={sock}"
                break

    try:
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        proc.stdin.write("scan on\n")
        proc.stdin.flush()

        # Wait for scan duration, collecting stdout
        await asyncio.sleep(timeout)

        proc.stdin.write("scan off\n")
        proc.stdin.flush()
        await asyncio.sleep(0.5)

        proc.stdin.write("exit\n")
        proc.stdin.flush()
        stdout, _ = proc.communicate(timeout=5)

        # Parse stdout: collect devices + Nintendo manufacturer data from [CHG]
        all_devices: Dict[str, str] = {}  # mac -> name
        nintendo_macs: set[str] = set()

        for line in stdout.strip().splitlines():
            line = line.strip()

            # [NEW] Device AA:BB:CC:DD:EE:FF Name
            if line.startswith("[NEW]"):
                parts = line.split()
                if len(parts) >= 4:
                    mac_candidate = parts[2]
                    if ":" in mac_candidate and len(mac_candidate) == 17:
                        all_devices[mac_candidate.lower()] = parts[3]

            # [CHG] Device AA:BB:CC:DD:EE:FF ManufacturerData.Key: 0x0553
            elif "ManufacturerData" in line and "0x0553" in line:
                # Parse MAC from this [CHG] line
                chg_parts = line.split()
                for i, part in enumerate(chg_parts):
                    if part == "Device" and i + 1 < len(chg_parts):
                        cand = chg_parts[i + 1]
                        if ":" in cand and len(cand) == 17:
                            nintendo_macs.add(cand.lower())
                            break

        # Build results: name match OR Nintendo mfr data
        for mac_raw, name in all_devices.items():
            name_lower = name.lower()
            type_ = ControllerType.PRO_CONTROLLER2
            is_nintendo = False

            # Direct name match
            if "joy-con" in name_lower or "pro controller" in name_lower:
                is_nintendo = True
                if "left" in name_lower:
                    type_ = ControllerType.JOYCON2_LEFT
                elif "right" in name_lower:
                    type_ = ControllerType.JOYCON2_RIGHT

            # Nintendo manufacturer data (0x0553)
            if mac_raw in nintendo_macs:
                is_nintendo = True

            if is_nintendo:
                results[mac_raw] = DeviceInfo(name=name, rssi=-1, type=type_)
                logger.info("Found: %s %s (%s)", mac_raw, name, type_.value)

    except FileNotFoundError:
        logger.warning("bluetoothctl not found")
    except Exception as e:
        logger.warning("bluetoothctl scan failed: %s", e)
        proc.kill()

    logger.info("BLE scan complete — found %d controller(s)", len(results))
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
