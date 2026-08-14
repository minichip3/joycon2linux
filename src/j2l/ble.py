"""BLE connection and notification subscription via bleak."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic

from j2l.protocol import (
    COMMAND_RESPONSE_UUID,
    COMMAND_WRITE_UUID,
    INPUT_REPORT_UUID,
    RUMBLE_JOYCON_L_UUID,
    RUMBLE_JOYCON_R_UUID,
    RUMBLE_PRO_UUID,
)

logger = logging.getLogger(__name__)

# How many times to try reconnecting after an unexpected disconnect
MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_SECONDS = 2.0


# Type alias for the notification callback signature
#   callback(client: BleakClient, data: bytearray) -> None
NotifyCallback = Callable[[BleakClient, bytearray], None]


class BleConnection:
    """Manage a BLE GATT connection to a Switch 2 controller.

    Responsibilities
    ----------------
    * Connect / disconnect lifecycle
    * GATT service & characteristic discovery
    * Subscribe to input-report notifications
    * Write commands and rumble payloads
    * Auto-reconnect on unexpected disconnect (up to MAX_RECONNECT_ATTEMPTS)
    """

    def __init__(self, address: str, controller_type: str = "pro_controller2") -> None:
        """Create a connection object (does not connect yet).

        Parameters
        ----------
        address : str
            BLE MAC address (e.g. "AA:BB:CC:DD:EE:FF").
        controller_type : str
            One of ``"joycon2_left"``, ``"joycon2_right"``, or
            ``"pro_controller2"``. Determines which rumble UUID to use.
        """
        self._address = address
        self._client: Optional[BleakClient] = None
        self._controller_type = controller_type.lower()

        # Cached characteristic handles
        self._input_notify_char: Optional[BleakGATTCharacteristic] = None
        self._command_write_char: Optional[BleakGATTCharacteristic] = None
        self._command_response_char: Optional[BleakGATTCharacteristic] = None
        self._rumble_char: Optional[BleakGATTCharacteristic] = None

        self._disconnect_callback: Optional[NotifyCallback] = None
        self._reconnect_attempts = 0
        self._reconnect_task: Optional[asyncio.Task] = None
        self._notification_callbacks: Dict[str, NotifyCallback] = {}

    # --- properties --------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def address(self) -> str:
        return self._address

    @property
    def controller_type(self) -> str:
        return self._controller_type

    # --- connection lifecycle -----------------------------------------------

    async def connect(self) -> None:
        """Establish a BLE connection and discover GATT services.

        Raises
        ------
        ConnectionError
            If the connection fails.
        """
        if self.is_connected:
            logger.warning("Already connected to %s", self._address)
            return

        logger.info("Connecting to BLE device %s", self._address)

        self._client = BleakClient(
            self._address,
            disconnected_callback=self._on_disconnect,
        )

        await self._client.connect()
        await self.discover_services()

        self._reconnect_attempts = 0
        logger.info("Connected to %s", self._address)

    async def disconnect(self) -> None:
        """Gracefully disconnect and clean up resources."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self._input_notify_char = None
        self._command_write_char = None
        self._command_response_char = None
        self._rumble_char = None
        self._notification_callbacks.clear()

    # --- GATT discovery -----------------------------------------------------

    async def discover_services(self) -> None:
        """Discover GATT characteristics and cache them.

        After calling this the four internal characteristic references are
        populated and ready for use.
        """
        if self._client is None:
            raise RuntimeError("Call connect() before discover_services()")

        all_chars: Dict[str, BleakGATTCharacteristic] = {}
        for service in self._client.services:
            for char in service.characteristics:
                uuid_lower = str(char.uuid).lower()
                all_chars[uuid_lower] = char

        # Input report notification
        self._input_notify_char = all_chars.get(INPUT_REPORT_UUID.lower())

        # Command write
        self._command_write_char = all_chars.get(COMMAND_WRITE_UUID.lower())

        # Command response
        self._command_response_char = all_chars.get(COMMAND_RESPONSE_UUID.lower())

        # Rumble — depends on controller type
        rumble_uuid = self._rumble_uuid_for_type(self._controller_type)
        self._rumble_char = all_chars.get(rumble_uuid.lower())

        missing: list[str] = []
        if self._input_notify_char is None:
            missing.append("INPUT_REPORT")
        if self._command_write_char is None:
            missing.append("COMMAND_WRITE")
        if self._command_response_char is None:
            missing.append("COMMAND_RESPONSE")
        if self._rumble_char is None:
            missing.append("RUMBLE")

        if missing:
            logger.warning(
                "Missing characteristics for %s: %s",
                self._address,
                ", ".join(missing),
            )
        else:
            logger.info("GATT discovery complete for %s", self._address)

    # --- notifications -----------------------------------------------------

    async def subscribe_notifications(self, callback: NotifyCallback) -> None:
        """Register a callback for input report notifications.

        Multiple callbacks can be registered; all will be invoked on each
        notification.
        """
        if self._input_notify_char is None:
            raise RuntimeError(
                "Input report characteristic not discovered; "
                "call connect() or discover_services() first."
            )
        if self._client is None:
            raise RuntimeError("Not connected")

        cb_id = id(callback)
        self._notification_callbacks[cb_id] = callback

        if not self._client.services:
            await self._client.get_services()

        await self._client.start_notify(
            self._input_notify_char.uuid, self._on_notification
        )
        logger.info(
            "Subscribed to notifications on %s (char %s)",
            self._address,
            self._input_notify_char.uuid,
        )

    async def unsubscribe_notifications(self, callback: NotifyCallback) -> None:
        """Remove a previously registered notification callback."""
        cb_id = id(callback)
        self._notification_callbacks.pop(cb_id, None)
        if not self._notification_callbacks:
            if self._client and self._input_notify_char:
                await self._client.stop_notify(self._input_notify_char.uuid)
            logger.info("Unsubscribed from all notifications on %s", self._address)

    # --- writes ------------------------------------------------------------

    async def write_command(self, data: bytes) -> None:
        """Write a command packet to the controller."""
        if self._command_write_char is None:
            raise RuntimeError("Command write characteristic not discovered")
        if self._client is None:
            raise RuntimeError("Not connected")

        await self._client.write_gatt_char(
            self._command_write_char,
            data,
            response=False,
        )

    async def write_rumble(self, data: bytes) -> None:
        """Write a rumble (haptic) packet to the controller."""
        if self._rumble_char is None:
            raise RuntimeError("Rumble characteristic not discovered")
        if self._client is None:
            raise RuntimeError("Not connected")

        await self._client.write_gatt_char(
            self._rumble_char,
            data,
            response=False,
        )

    # --- internal helpers --------------------------------------------------

    def _on_notification(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Fan out incoming notification data to registered callbacks."""
        for cb in list(self._notification_callbacks.values()):
            try:
                cb(self._client, data)  # type: ignore[arg-type]
            except Exception:
                logger.exception("Error in notification callback")

    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected disconnect — trigger auto-reconnect."""
        logger.warning("BLE disconnected from %s", self._address)
        self._client = None

        if self._disconnect_callback:
            try:
                self._disconnect_callback(client, bytearray())  # type: ignore[arg-type]
            except Exception:
                logger.exception("Error in disconnect callback")

        # Only auto-reconnect if this was unexpected (not from explicit disconnect())
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._try_reconnect())

    async def _try_reconnect(self) -> None:
        """Attempt to reconnect up to MAX_RECONNECT_ATTEMPTS times."""
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            if self._client and self._client.is_connected:
                self._reconnect_task = None
                return  # Already reconnected by another path
            if attempt > 1:
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

            try:
                logger.info(
                    "Reconnect attempt %d/%d for %s",
                    attempt,
                    MAX_RECONNECT_ATTEMPTS,
                    self._address,
                )
                await self.connect()
                # Re-subscribe if we had callbacks
                if self._notification_callbacks:
                    await self.subscribe_notifications(
                        list(self._notification_callbacks.values())[0]
                    )
                logger.info("Reconnected to %s", self._address)
                self._reconnect_task = None
                return
            except Exception:
                logger.warning("Reconnect attempt %d failed: %s", attempt, _)

        logger.error(
            "Max reconnect attempts (%d) exhausted for %s",
            MAX_RECONNECT_ATTEMPTS,
            self._address,
        )
        self._reconnect_task = None

    @staticmethod
    def _rumble_uuid_for_type(controller_type: str) -> str:
        """Return the rumble characteristic UUID for the given controller type."""
        if controller_type == "joycon2_left":
            return RUMBLE_JOYCON_L_UUID
        if controller_type == "joycon2_right":
            return RUMBLE_JOYCON_R_UUID
        return RUMBLE_PRO_UUID
