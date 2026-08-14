"""BLE ↔ uinput bridge main loop.

Connects a Switch 2 controller over BLE to a virtual gamepad via uinput,
translating raw input reports into evdev events in real time.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import TYPE_CHECKING

from j2l.ble import BleConnection
from j2l.gamepad import UinputGamepad
from j2l.mapper import joycon_to_evdev
from j2l.protocol import decode_input_report

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ControllerBridge:
    """Connect a BLE controller to a uinput virtual gamepad.

    Lifecycle
    ---------
    1. ``await bridge.start()``  – connects BLE, subscribes to notifications
    2. BLE input notifications are decoded, mapped, and injected into uinput
       automatically via the notification callback.
    3. ``await bridge.stop()``   – disconnects BLE, closes uinput device

    Context-manager usage
    ---------------------
    .. code-block:: python

        async with ControllerBridge(address, "pro_controller2") as bridge:
            await asyncio.sleep(300)  # runs for 5 minutes
    """

    def __init__(
        self,
        address: str,
        controller_type: str,
        gamepad_name: str | None = None,
        combined: bool = True,
    ) -> None:
        """Create a bridge (does not connect yet).

        Args:
            address: BLE MAC address (e.g. ``"AA:BB:CC:DD:EE:FF"``).
            controller_type: One of ``"joycon2_left"``, ``"joycon2_right"``,
                or ``"pro_controller2"``.
            gamepad_name: Name reported by the uinput device.  Defaults to
                ``"Joy-Con 2 (<address>)"``.
            combined: If *True*, expose a full Pro Controller layout.  If
                *False*, expose a half-size Joy-Con layout.
        """
        self._ble = BleConnection(address, controller_type)
        self._gamepad = UinputGamepad(
            name=gamepad_name or f"Joy-Con 2 ({address})",
            combined=combined,
        )
        self._prev_buttons: int = 0
        self._running: bool = False
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect BLE, subscribe to input notifications, and start polling.

        After calling this the bridge is live: every BLE notification will
        be decoded and injected into the virtual gamepad automatically.
        """
        if self._running:
            logger.warning("Bridge already running for %s", self._ble.address)
            return

        logger.info("Starting bridge for %s", self._ble.address)
        await self._ble.connect()
        await self._ble.subscribe_notifications(self._on_notification)
        self._running = True
        logger.info("Bridge started — waiting for notifications")

    async def stop(self) -> None:
        """Disconnect BLE and close the uinput device.

        Blocks until the BLE link is cleanly torn down and the virtual
        gamepad is released.
        """
        if not self._running:
            return

        logger.info("Stopping bridge for %s", self._ble.address)
        self._running = False
        await self._ble.disconnect()
        self._gamepad.close()
        self._stop_event.set()
        logger.info("Bridge stopped")

    @property
    def is_running(self) -> bool:
        """Whether the bridge is currently active."""
        return self._running

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------

    def _on_notification(self, data: bytes) -> None:
        """Decode an incoming BLE input report and inject evdev events.

        Called by :class:`BleConnection` on every notification.  Errors
        here are logged but do not crash the event loop.
        """
        try:
            report = decode_input_report(bytes(data))
            events = joycon_to_evdev(report, self._prev_buttons)
            if events:
                self._gamepad.emit_events(events)
            self._prev_buttons = report.buttons
        except Exception:
            logger.exception("Error processing input report")

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> ControllerBridge:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()


# ------------------------------------------------------------------
# CLI entry-point / main loop with signal handling
# ------------------------------------------------------------------


async def run_bridge(
    address: str,
    controller_type: str,
    gamepad_name: str | None = None,
    combined: bool = True,
) -> None:
    """Run the bridge loop with graceful SIGINT/SIGTERM handling.

    Args:
        address: BLE MAC address.
        controller_type: Controller type string.
        gamepad_name: Optional uinput device name.
        combined: Full vs. half layout.
    """
    loop = asyncio.get_running_loop()
    bridge = ControllerBridge(
        address, controller_type, gamepad_name=gamepad_name, combined=combined
    )

    def _handle_signal() -> None:
        logger.info("Received shutdown signal, stopping bridge...")
        if not bridge._stop_event.is_set():
            bridge._stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            logger.warning("Signal handling unavailable on this platform")

    await bridge.start()
    logger.info("Bridge active; press Ctrl+C to stop")
    await bridge._stop_event.wait()
    await bridge.stop()
