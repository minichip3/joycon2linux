"""BLE connection via raw L2CAP ATT sockets.

Bypasses BlueZ D-Bus entirely to avoid ``InProgress`` conflicts on
Steam Deck / Bazzite where GNOME keeps a persistent BLE scan active.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Callable, Dict, Optional

from j2l.att import ATTClient, Characteristic
from j2l.protocol import (
    COMMAND_RESPONSE_UUID,
    COMMAND_WRITE_UUID,
    INPUT_REPORT_UUID,
    RUMBLE_JOYCON_L_UUID,
    RUMBLE_JOYCON_R_UUID,
    RUMBLE_PRO_UUID,
)

logger = logging.getLogger(__name__)

MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_SECONDS = 2.0

NotifyCallback = Callable[[bytes], None]


def _stop_le_scan(adapter: str = "hci0") -> None:
    """Stop LE scan at HCI level so raw L2CAP connect succeeds.

    Switch 2 controllers can't accept a connection while the adapter is
    scanning. Steam Deck / Bazzite keeps persistent scans alive.

    Priority:
      1. btmgmt stop-find -l   (HCI-level, most reliable)
      2. busctl StopDiscovery  (D-Bus)
      3. dbus-send CancelDiscovery (legacy D-Bus)
    """
    idx = adapter.replace("hci", "") if "hci" in adapter else "0"

    # 1. btmgmt (HCI-level — stops Steam/decky persistent scans)
    try:
        subprocess.run(
            ["btmgmt", "-i", idx, "stop-find", "-l"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        logger.info("btmgmt stop-find succeeded")
        return
    except Exception:
        pass

    # 2. busctl
    try:
        subprocess.run(
            [
                "busctl", "call", "org.bluez",
                f"/org/bluez/{adapter}",
                "org.bluez.Adapter1", "StopDiscovery",
            ],
            capture_output=True, text=True, timeout=3,
        )
        logger.info("busctl StopDiscovery succeeded")
        return
    except Exception:
        pass

    # 3. dbus-send
    try:
        subprocess.run(
            [
                "dbus-send", "--system", "--dest=org.bluez",
                f"/org/bluez/{adapter}",
                "org.bluez.Adapter1.CancelDiscovery",
            ],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        pass

    logger.warning("Could not stop LE scan")


def _kill_steam_bt_services() -> None:
    """Kill Steam Input and decky BT services that hold persistent scans."""
    for pattern in ["decky-bluetooth-wake-control", "steaminput", "steam-overlay"]:
        try:
            subprocess.run(["pkill", "-f", pattern],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            logger.info("killed %s", pattern)
        except Exception:
            pass


def _hci_stop_le_scan(adapter: str = "hci0") -> bool:
    """Stop LE scan by writing HCI LE Set Scan command to /dev/bluetooth/hciX.

    Writes directly to the HCI device node, bypassing BlueZ/btmgmt entirely.
    HCI LE Set Scan: opcode 0x0808, params: 0x00 (scan disabled)
    """
    import struct
    import os
    idx = int(adapter.replace("hci", "")) if "hci" in adapter else 0
    dev_path = f"/dev/bluetooth/hci{idx}"
    if not os.path.exists(dev_path):
        logger.info("HCI device %s not found", dev_path)
        return False
    try:
        fd = os.open(dev_path, os.O_WRONLY | os.O_NONBLOCK)
        # HCI command: type=1, plen=1, opcode=0x0808, param=0x00
        cmd = struct.pack("<BBHB", 1, 1, 0x0808, 0x00)
        os.write(fd, cmd)
        os.close(fd)
        logger.info("HCI LE scan disabled via %s", dev_path)
        return True
    except Exception as e:
        logger.info("HCI scan disable failed: %s", e)
        return False


def _debug_hci_state(adapter: str = "hci0") -> None:
    """Debug: check HCI controller state."""
    try:
        import socket as _socket
        import struct
        hci_sock = _socket.socket(_socket.AF_BLUETOOTH,
                                  _socket.SOCK_RAW,
                                  _socket.BTPROTO_HCI)
        idx = int(adapter.replace("hci", "")) if "hci" in adapter else 0
        hci_sock.bind((idx,))
        logger.info("HCI socket bound successfully for %s", adapter)
        hci_sock.close()
    except Exception as e:
        logger.info("HCI socket bind failed: %s", e)


def _btmgmt_stop_find(adapter: str = "hci0") -> None:
    """Stop discovery at HCI level via btmgmt."""
    idx = adapter.replace("hci", "") if "hci" in adapter else "0"
    try:
        subprocess.run(
            ["btmgmt", "-i", idx, "stop-find", "-l"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        logger.info("btmgmt stop-find succeeded")
    except Exception:
        pass





class BleConnection:
    """Manage a BLE connection to a Switch 2 controller via raw L2CAP ATT."""

    def __init__(
        self, address: str, controller_type: str = "pro_controller2"
    ) -> None:
        self._address = address
        self._controller_type = controller_type.lower()
        self._att: Optional[ATTClient] = None

        # Resolved handles
        self._h_input: int = 0
        self._h_input_cccd: int = 0
        self._h_cmd_write: int = 0
        self._h_cmd_resp: int = 0
        self._h_cmd_resp_cccd: int = 0
        self._h_rumble: int = 0

        self._notification_callbacks: Dict[int, NotifyCallback] = {}
        self._reconnect_task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        return self._att is not None and self._att.is_connected

    @property
    def address(self) -> str:
        return self._address

    @property
    def controller_type(self) -> str:
        return self._controller_type

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        """Establish BLE connection and discover GATT services."""
        if self.is_connected:
            logger.warning("Already connected to %s", self._address)
            return

        logger.info("Connecting to BLE device %s", self._address)

        # Stop LE scan before connecting
        _btmgmt_stop_find("hci0")
        _stop_le_scan("hci0")

        # Debug: check HCI state
        _debug_hci_state("hci0")
        await asyncio.sleep(0.2)

        self._att = ATTClient(self._address, adapter="hci0")
        self._att.notification_cb = self._on_att_notification
        self._att.disconnect_cb = self._on_att_disconnect

        ok = False
        errors: list[str] = []
        for attempt in range(5):
            _btmgmt_stop_find("hci0")
            await asyncio.sleep(0.1)
            ok, detail = await asyncio.to_thread(self._att.connect, timeout=3.0, retries=1)
            if ok:
                break
            errors.append(f"attempt {attempt+1}: {detail}")
            self._att.close()
            await asyncio.sleep(0.2)
        if not ok:
            self._att.close()
            self._att = None
            raise ConnectionError(f"BLE connection failed: {'; '.join(errors)}")

        await asyncio.to_thread(self._resolve_handles)
        self._reconnect_attempts = 0
        logger.info("Connected to %s", self._address)

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._att:
            await asyncio.to_thread(self._att.close)
        self._att = None
        self._notification_callbacks.clear()

    # ------------------------------------------------------------------ #
    # GATT discovery
    # ------------------------------------------------------------------ #

    def _resolve_handles(self) -> None:
        """Discover GATT characteristics and cache handles."""
        if self._att is None:
            raise RuntimeError("Not connected")

        # Default handles (fallback for GameCube / unknown models)
        self._h_input = 0x000A
        self._h_input_cccd = 0x000B
        self._h_cmd_write = 0x0014
        self._h_cmd_resp = 0x001A
        self._h_cmd_resp_cccd = 0x001B
        self._h_rumble = 0x0012

        try:
            services = self._att.discover_all()
        except Exception as exc:
            logger.warning("GATT discovery failed (%s); using default handles", exc)
            return

        by_uuid: Dict[str, Characteristic] = {}
        for svc in services:
            for ch in svc.characteristics:
                by_uuid[ch.uuid] = ch

        def val(uuid: str, default: int) -> int:
            ch = by_uuid.get(uuid)
            return ch.value_handle if ch else default

        def cccd(uuid: str, default: int) -> int:
            ch = by_uuid.get(uuid)
            return ch.cccd_handle if ch and ch.cccd_handle else default

        self._h_input = val(INPUT_REPORT_UUID, self._h_input)
        self._h_input_cccd = cccd(INPUT_REPORT_UUID, self._h_input_cccd)
        self._h_cmd_write = val(COMMAND_WRITE_UUID, self._h_cmd_write)
        self._h_cmd_resp = val(COMMAND_RESPONSE_UUID, self._h_cmd_resp)
        self._h_cmd_resp_cccd = cccd(COMMAND_RESPONSE_UUID, self._h_cmd_resp_cccd)

        rumble_uuid = self._rumble_uuid_for_type(self._controller_type)
        self._h_rumble = val(rumble_uuid, self._h_rumble)

        logger.info(
            "Handles: input=%#06x cmd_w=%#06x rumble=%#06x",
            self._h_input, self._h_cmd_write, self._h_rumble,
        )

    # ------------------------------------------------------------------ #
    # Notifications
    # ------------------------------------------------------------------ #

    async def subscribe_notifications(self, callback: NotifyCallback) -> None:
        """Register a callback for input report notifications."""
        cb_id = id(callback)
        self._notification_callbacks[cb_id] = callback

        if self._att is None:
            raise RuntimeError("Not connected")

        await asyncio.to_thread(self._att.subscribe, self._h_input_cccd, True)
        logger.info("Subscribed to input notifications on %s", self._address)

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    async def write_command(self, data: bytes) -> None:
        """Write a command packet to the controller."""
        if self._att is None:
            raise RuntimeError("Not connected")
        await asyncio.to_thread(self._att.write_command, self._h_cmd_write, data)

    async def write_rumble(self, data: bytes) -> None:
        """Write a rumble (haptic) packet to the controller."""
        if self._att is None:
            raise RuntimeError("Not connected")
        await asyncio.to_thread(self._att.write_command, self._h_rumble, data)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _on_att_notification(self, handle: int, data: bytes) -> None:
        """Thread: call notification callbacks directly."""
        if handle != self._h_input:
            return
        for cb in list(self._notification_callbacks.values()):
            try:
                cb(data)
            except Exception:
                logger.exception("Error in notification callback")

    def _on_att_disconnect(self) -> None:
        """Thread: signal disconnect to asyncio loop."""
        logger.warning("BLE disconnected from %s", self._address)
        try:
            self._disconnect_event.set()
        except Exception:
            pass

        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._try_reconnect())

    async def _try_reconnect(self) -> None:
        """Attempt to reconnect up to MAX_RECONNECT_ATTEMPTS times."""
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            if self._att and self._att.is_connected:
                self._reconnect_task = None
                return
            if attempt > 1:
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

            try:
                logger.info(
                    "Reconnect attempt %d/%d for %s",
                    attempt, MAX_RECONNECT_ATTEMPTS, self._address,
                )
                await self.connect()
                if self._notification_callbacks:
                    cbs = list(self._notification_callbacks.values())
                    await self.subscribe_notifications(cbs[0])
                logger.info("Reconnected to %s", self._address)
                self._reconnect_task = None
                self._disconnect_event.clear()
                return
            except Exception:
                logger.warning("Reconnect attempt %d failed: %s", attempt, _)

        logger.error(
            "Max reconnect attempts (%d) exhausted for %s",
            MAX_RECONNECT_ATTEMPTS, self._address,
        )
        self._reconnect_task = None

    @staticmethod
    def _rumble_uuid_for_type(controller_type: str) -> str:
        if controller_type == "joycon2_left":
            return RUMBLE_JOYCON_L_UUID
        if controller_type == "joycon2_right":
            return RUMBLE_JOYCON_R_UUID
        return RUMBLE_PRO_UUID
