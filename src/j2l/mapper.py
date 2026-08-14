"""Joy-Con binary → evdev event mapping."""

from __future__ import annotations

from evdev import ecodes
from evdev import InputEvent

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
    # ZR is the right trigger axis, mapped via ABS_RZ — handled separately
}

# D-pad buttons are NOT mapped to BTN_ keys; they drive ABS_HAT0X/HAT0Y.
DPAD_BUTTONS: set[int] = {
    Buttons.UP,
    Buttons.DOWN,
    Buttons.DLEFT,
    Buttons.DRIGHT,
}

# Stick deadzone — raw values within ±15 of center (128) are zeroed out.
STICK_DEADZONE: int = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_stick(raw: int) -> int:
    """Map a 0-255 stick value (128=center) to -32767..32767 with deadzone.

    The raw value 0 maps to -32767, 128 to 0, and 255 to 32767.
    Values within ±*STICK_DEADZONE* of center are clamped to 0.
    """
    offset = raw - 128  # -128..127
    if abs(offset) <= STICK_DEADZONE:
        return 0
    # Scale: offset ∈ [-128, 127] → [-32767, 32767]
    return int(offset * 32767 / 128)


def _map_trigger(raw: int) -> int:
    """Map a 0-255 trigger value to 0-32767."""
    return int(raw * 32767 / 255)


def _dpad_to_hat(buttons: int) -> tuple[int, int]:
    """Convert D-pad button bits to (HAT_X, HAT_Y) values.

    Supports all 8 directions plus center (no buttons pressed).
    Conflicting bits (e.g. UP + DOWN) resolve to neutral on that axis.
    """
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
    """Convert a Joy-Con :class:`InputReport` into a list of evdev events.

    Args:
        report: Decoded input report from the controller.
        prev_buttons: Button bitmask from the previous frame. Used to detect
            press/release edges for button events.

    Returns:
        List of :class:`evdev.InputEvent` objects ready for injection into
        a :class:`UinputGamepad`. Callers should pass ``prev_buttons`` on
        subsequent calls to avoid stale button events.
    """
    events: list[InputEvent] = []

    # ---- Stick axes -------------------------------------------------------
    axis_map = [
        (report.lx, ecodes.ABS_X),
        (report.ly, ecodes.ABS_Y),
        (report.rx, ecodes.ABS_RX),
        (report.ry, ecodes.ABS_RY),
    ]
    for raw, code in axis_map:
        events.append(InputEvent(ecodes.EV_ABS, code, _map_stick(raw)))

    # ---- Trigger axes -----------------------------------------------------
    events.append(InputEvent(ecodes.EV_ABS, ecodes.ABS_Z, _map_trigger(report.trigger_l)))
    events.append(InputEvent(ecodes.EV_ABS, ecodes.ABS_RZ, _map_trigger(report.trigger_r)))

    # ZR button → right trigger axis (ABS_RZ) — redundant with trigger_r above,
    # but included here for completeness if firmware reports it separately.
    # In practice trigger_r already covers ZR.

    # ---- D-pad → HAT ------------------------------------------------------
    h_x, h_y = _dpad_to_hat(report.buttons)
    events.append(InputEvent(ecodes.EV_ABS, ecodes.ABS_HAT0X, h_x))
    events.append(InputEvent(ecodes.EV_ABS, ecodes.ABS_HAT0Y, h_y))

    # ---- Button edges -----------------------------------------------------
    for name, mask in Buttons._ALL.items():
        if name in ("UP", "DOWN", "DLEFT", "DRIGHT"):
            # D-pad handled above via HAT
            continue
        btn_pressed = bool(report.buttons & mask)
        prev_pressed = bool(prev_buttons & mask)
        if btn_pressed == prev_pressed:
            continue
        evdev_code = BUTTON_TO_EVDEV.get(mask)
        if evdev_code is None:
            continue
        events.append(InputEvent(ecodes.EV_KEY, evdev_code, 1 if btn_pressed else 0))

    return events
