"""BLE connection module — C extension + Python wrapper.

Uses C extension (_ble) for HCI/L2CAP/ATT operations.
GATT discovery and notification handling in Python.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, Optional

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

# Default GATT handles (Joy-Con 2)
DEFAULT_HANDLES = {
    "input": 0x000A,
    "input_cccd": 0x000B,
    "cmd_write": 0x0014,
    "cmd_resp": 0x001A,
    "cmd_resp_cccd": 0x001B,
    "rumble": 0x0012,
}


def _load_ble():
    """Load C BLE extension module."""
    try:
        from j2l import _ble
        return _ble
    except ImportError:
        logger.error("C BLE extension (_ble) not found. Run pip install -e .")
        raise RuntimeError("C BLE extension not available. "
                          "Build with: pip install -e .")


class BleConnection:
    """Manage a BLE connection to a Switch 2 controller."""

    def __init__(
        self, address: str, controller_type: str = "pro_controller2"
    ) -> None:
        self._address = address
        self._controller_type = controller_type.lower()
        self._ble = _load_ble()

        # Resolved handles
        self._h_input: int = DEFAULT_HANDLES["input"]
        self._h_input_cccd: int = DEFAULT_HANDLES["input_cccd"]
        self._h_cmd_write: int = DEFAULT_HANDLES["cmd_write"]
        self._h_cmd_resp: int = DEFAULT_HANDLES["cmd_resp"]
        self._h_cmd_resp_cccd: int = DEFAULT_HANDLES["cmd_resp_cccd"]
        self._h_rumble: int = DEFAULT_HANDLES["rumble"]

        self._notification_callbacks: Dict[int, NotifyCallback] = {}
        self._reconnect_task: Optional[asyncio.Task] = None
        self._notification_task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        return self._ble.is_connected()

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

        errors: list[str] = []
        for attempt in range(1, 4):
            try:
                result = await asyncio.to_thread(
                    self._ble.connect, self._address, "hci0"
                )
                if result:
                    break
            except Exception as e:
                errors.append(f"attempt {attempt}: {e}")
                await asyncio.sleep(0.5)
        else:
            raise ConnectionError(
                f"BLE connection failed: {'; '.join(errors)}"
            )

        # Discover GATT services
        await asyncio.to_thread(self._resolve_handles)
        self._reconnect_attempts = 0
        logger.info("Connected to %s", self._address)

        # Start notification reader
        self._notification_task = asyncio.create_task(
            self._notification_loop()
        )

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        if self._notification_task:
            self._notification_task.cancel()
            try:
                await self._notification_task
            except asyncio.CancelledError:
                pass
            self._notification_task = None

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        await asyncio.to_thread(self._ble.disconnect)
        self._notification_callbacks.clear()

    # ------------------------------------------------------------------ #
    # GATT discovery
    # ------------------------------------------------------------------ #

    def _resolve_handles(self) -> None:
        """Discover GATT characteristics and cache handles."""
        if not self.is_connected:
            raise RuntimeError("Not connected")

        # Use defaults as fallback
        try:
            services_str = self._ble.discover_services()
            if services_str.strip():
                # Parse service discovery results
                # For now, use defaults — full discovery can be added later
                pass
        except Exception as exc:
            logger.warning("GATT discovery failed (%s); using default handles", exc)

        # Set rumble handle based on controller type
        rumble_uuid = self._rumble_uuid_for_type(self._controller_type)
        if rumble_uuid == RUMBLE_JOYCON_L_UUID:
            self._h_rumble = 0x0012
        elif rumble_uuid == RUMBLE_JOYCON_R_UUID:
            self._h_rumble = 0x0012
        # Pro controller uses same handle

        logger.info(
            "Handles: input=%#06x cmd_w=%#06x rumble=%#06x",
            self._h_input, self._h_cmd_write, self._h_rumble,
        )

    # ------------------------------------------------------------------ #
    # Notifications
    # ------------------------------------------------------------------ #

    async def _notification_loop(self) -> None:
        """Background loop to read ATT notifications."""
        import select
        import struct

        while True:
            try:
                await asyncio.sleep(0.01)
                # Non-blocking read from L2CAP socket
                # Note: C extension handles this via async read
                # For now, we rely on the C module's internal handling
            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def subscribe_notifications(self, callback: NotifyCallback) -> None:
        """Register a callback for input report notifications."""
        cb_id = id(callback)
        self._notification_callbacks[cb_id] = callback

        if not self.is_connected:
            raise RuntimeError("Not connected")

        await asyncio.to_thread(
            self._ble.subscribe, self._h_input_cccd
        )
        logger.info("Subscribed to input notifications on %s", self._address)

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    async def write_command(self, data: bytes) -> None:
        """Write a command packet to the controller."""
        if not self.is_connected:
            raise RuntimeError("Not connected")
        await asyncio.to_thread(
            self._ble.write, self._h_cmd_write, data
        )

    async def write_rumble(self, data: bytes) -> None:
        """Write a rumble (haptic) packet to the controller."""
        if not self.is_connected:
            raise RuntimeError("Not connected")
        await asyncio.to_thread(
            self._ble.write, self._h_rumble, data
        )

    async def read_characteristic(self, handle: int) -> bytes:
        """Read a characteristic value."""
        if not self.is_connected:
            raise RuntimeError("Not connected")
        return await asyncio.to_thread(self._ble.read, handle)

    async def subscribe_cccd(self, cccd_handle: int) -> None:
        """Subscribe to CCCD notifications."""
        if not self.is_connected:
            raise RuntimeError("Not connected")
        await asyncio.to_thread(self._ble.subscribe, cccd_handle)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _try_reconnect(self) -> None:
        """Attempt to reconnect up to MAX_RECONNECT_ATTEMPTS times."""
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            if self.is_connected:
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
                return
            except Exception:
                logger.warning("Reconnect attempt %d failed", attempt)

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
