"""uinput virtual gamepad (evdev Xbox-style layout)."""

from __future__ import annotations

from evdev import ecodes
from evdev.uinput import UInput


# ---------------------------------------------------------------------------
# Axis / button constants
# ---------------------------------------------------------------------------

# Stick axes (signed, center 0) — default kernel range already -32768..32767
ABS_AXES: dict[str, int] = {
    "LX": ecodes.ABS_X,
    "LY": ecodes.ABS_Y,
    "RX": ecodes.ABS_RX,
    "RY": ecodes.ABS_RY,
}

# Trigger axes (unsigned) — default kernel range already 0..32767
TRIGGER_AXES: dict[str, int] = {
    "LT": ecodes.ABS_Z,
    "RT": ecodes.ABS_RZ,
}

# D-pad hat — default kernel range already -1..1
HAT_AXES: dict[str, int] = {
    "HAT_X": ecodes.ABS_HAT0X,
    "HAT_Y": ecodes.ABS_HAT0Y,
}

# Button mapping (Joy-Con name → evdev BTN code)
BUTTON_MAP: dict[str, int] = {
    "A": ecodes.BTN_SOUTH,
    "B": ecodes.BTN_EAST,
    "X": ecodes.BTN_NORTH,
    "Y": ecodes.BTN_WEST,
    "LR": ecodes.BTN_TL,
    "RR": ecodes.BTN_TR,
    "MINUS": ecodes.BTN_SELECT,
    "PLUS": ecodes.BTN_START,
    "LS": ecodes.BTN_THUMB,
    "RS": ecodes.BTN_BASE2,
    "HOME": ecodes.BTN_MODE,
    "SL": ecodes.BTN_TL2,
    "SR": ecodes.BTN_TR2,
    "CAPTURE": ecodes.BTN_TRIGGER,
}


# ---------------------------------------------------------------------------
# UinputGamepad
# ---------------------------------------------------------------------------


class UinputGamepad:
    """Virtual Xbox-style gamepad backed by evdev uinput.

    Args:
        name: Device name shown to the kernel (e.g. ``Joy-Con 2 Gamepad``).
        combined: If *True*, expose full Pro Controller layout (both sticks,
            both triggers, D-pad, all buttons).  If *False*, expose a
            half-size Joy-Con layout (one stick, one trigger, fewer buttons).
    """

    def __init__(self, name: str = "Joy-Con 2 Gamepad", combined: bool = False) -> None:
        self._name = name
        self._combined = combined
        self._device: UInput | None = None
        self._create_device()

    # ---- internal ---------------------------------------------------------

    def _create_device(self) -> None:
        """Instantiate the uinput virtual device."""
        # evdev UInput uses an `events` dict: {EV_TYPE: [codes...]}
        abs_codes = list(ABS_AXES.values()) + list(TRIGGER_AXES.values()) + list(HAT_AXES.values())
        key_codes = list(BUTTON_MAP.values())

        events = {
            ecodes.EV_KEY: key_codes,
            ecodes.EV_ABS: abs_codes,
        }

        self._device = UInput(
            events=events,
            name=self._name,
            bustype=3,  # BUS_USB (evdev latest removed BUSTYPE_USB)
            vendor=0x057E,  # Nintendo
            product=0x200E,  # Switch Pro Controller-ish
        )

    # ---- public API -------------------------------------------------------

    def emit_event(self, event) -> None:
        """Inject a single evdev event into the virtual device.

        Args:
            event: An :class:`evdev.InputEvent` with type, code, and value.
        """
        if self._device is None:
            raise RuntimeError("Gamepad device has been closed")
        self._device.write_event(event)
        self._device.syn()

    def emit_events(self, events: list) -> None:
        """Inject multiple events and syn at the end."""
        if self._device is None:
            raise RuntimeError("Gamepad device has been closed")
        for event in events:
            self._device.write_event(event)
        self._device.syn()

    def close(self) -> None:
        """Release the uinput device."""
        if self._device is not None:
            self._device.close()
            self._device = None

    def __enter__(self) -> UinputGamepad:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
