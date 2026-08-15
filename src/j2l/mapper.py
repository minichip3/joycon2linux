"""Joy-Con 2 binary → evdev event mapping."""

from __future__ import annotations

from evdev import ecodes
from j2l.protocol import Buttons, InputReport

# ---------------------------------------------------------------------------
# Button bitmask → evdev BTN mapping
# ---------------------------------------------------------------------------

BUTTON_TO_EVDEV: dict[int, int] = {
    Buttons.A: ecodes.BTN_SOUTH,
    Buttons.B: ecodes.BTN_EAST,
    Buttons.X: ecodes.BTN_NORTH,
    Buttons.Y: ecodes.BTN_WEST,
    Buttons.LR: ecodes.BTN_TL,
    Buttons.R: ecodes.BTN_TR,
    Buttons.MINUS: ecodes.BTN_SELECT,
    Buttons.PLUS: ecodes.BTN_START,
    Buttons.LS: ecodes.BTN_THUMB,
    Buttons.XR: ecodes.BTN_BASE2,
    Buttons.HOME: ecodes.BTN_MODE,
    Buttons.SL: ecodes.BTN_TL2,
    Buttons.SR: ecodes.BTN_TR2,
    Buttons.CAPTURE: ecodes.BTN_TRIGGER,
    Buttons.ZR: ecodes.BTN_TRIGGER_HAPPY,
    # Buttons.ZL → handled via ABS_Z trigger axis
}

# D-pad buttons are NOT mapped to BTN_ keys; they drive ABS_HAT0X/HAT0Y.
DPAD_BUTTONS: set[int] = {
    Buttons.UP,
    Buttons.DOWN,
    Buttons.DLEFT,
    Buttons.DRIGHT,
}

# Stick deadzone — raw 12-bit values within ±DEADZONE of center (2048) are zeroed.
STICK_DEADZONE: int = 128


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_stick(raw: int) -> int:
    """Map a 12-bit stick value (0-4095, 2048=center) to -32767..32767.

    Values within ±*STICK_DEADZONE* of center are clamped to 0.
    """
    offset = raw - 2048  # -2048..2047
    if abs(offset) <= STICK_DEADZONE:
        return 0
    return int(offset * 32767 / 2048)


def _map_trigger(raw: int) -> int:
    """Map a 0-255 trigger value to 0-32767."""
    return int(raw * 32767 / 255)


def _dpad_to_hat(buttons: int) -> tuple[int, int]:
    """Convert D-pad button bits to (HAT_X, HAT_Y) values."""
    h_x = 0
    h_y = 0

    if buttons & Buttons.DLEFT:
        h_x = -1
    if buttons & Buttons.DRIGHT:
        h_x = 1 if h_x == 0 else 0

    if buttons & Buttons.UP:
        h_y = -1
    if buttons & Buttons.DOWN:
        h_y = 1 if h_y == 0 else 0

    return h_x, h_y


# ---------------------------------------------------------------------------
# Main mapping function
# ---------------------------------------------------------------------------


def joycon_to_evdev(report: InputReport, prev_buttons: int = 0) -> list[InputEvent]:
    """Convert a Joy-Con 2 input report into a list of evdev events."""
    events: list[InputEvent] = []

    # ---- Stick axes -------------------------------------------------------
    axis_map = [
        (report.lx, ecodes.ABS_X),
        (report.ly, ecodes.ABS_Y),
        (report.rx, ecodes.ABS_RX),
        (report.ry, ecodes.ABS_RY),
    ]
    for raw, code in axis_map:
        events.append((ecodes.EV_ABS, code, _map_stick(raw)))

    # ---- Trigger axes -----------------------------------------------------
    events.append((ecodes.EV_ABS, ecodes.ABS_Z, _map_trigger(report.trigger_l)))
    events.append((ecodes.EV_ABS, ecodes.ABS_RZ, _map_trigger(report.trigger_r)))

    # ---- D-pad → HAT ------------------------------------------------------
    h_x, h_y = _dpad_to_hat(report.buttons)
    events.append((ecodes.EV_ABS, ecodes.ABS_HAT0X, h_x))
    events.append((ecodes.EV_ABS, ecodes.ABS_HAT0Y, h_y))

    # ---- Button edges -----------------------------------------------------
    for name, mask in Buttons._ALL.items():
        if name in ("UP", "DOWN", "DLEFT", "DRIGHT"):
            continue
        btn_pressed = bool(report.buttons & mask)
        prev_pressed = bool(prev_buttons & mask)
        if btn_pressed == prev_pressed:
            continue
        evdev_code = BUTTON_TO_EVDEV.get(mask)
        if evdev_code is None:
            continue
        events.append((ecodes.EV_KEY, evdev_code, 1 if btn_pressed else 0))

    return events
