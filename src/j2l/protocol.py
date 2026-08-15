"""Nintendo Switch 2 protocol constants, data classes, and parsing helpers.

Based on Joy-Con 2 / Pro Controller 2 input reports (63-byte format).
Protocol reference: switch2-controllers-linux (Nadeflore/bitaxislabs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# GATT Characteristic UUIDs
# ---------------------------------------------------------------------------

COMMAND_WRITE_UUID: Final = "649d4ac9-8eb7-4e6c-af44-1ea54fe5f005"
COMMAND_RESPONSE_UUID: Final = "c765a961-d9d8-4d36-a20a-5315b111836a"
INPUT_REPORT_UUID: Final = "ab7de9be-89fe-49ad-828f-118f09df7fd2"
RUMBLE_PRO_UUID: Final = "cc483f51-9258-427d-a939-630c31f72b05"
RUMBLE_JOYCON_L_UUID: Final = "289326cb-a471-485d-a8f4-240c14f18241"
RUMBLE_JOYCON_R_UUID: Final = "fa19b0fb-cd1f-46a7-84a1-bbb09e00c149"

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

NINTENDO_COMPANY_ID: Final = 0x0553
NINTENDO_VENDOR_ID: Final = 0x057E

# Product IDs
JOYCON2_RIGHT_PID: Final = 0x2066
JOYCON2_LEFT_PID: Final = 0x2067
PRO_CONTROLLER2_PID: Final = 0x2069

# Joy-Con 2 input report is 63 bytes
JOYCON2_REPORT_SIZE: Final = 63
# Pro Controller 2 input report is 64 bytes
PRO2_REPORT_SIZE: Final = 64


# ---------------------------------------------------------------------------
# Input report data class (Joy-Con 2)
# ---------------------------------------------------------------------------


@dataclass
class InputReport:
    """Decoded Switch 2 controller input report (Joy-Con 2 / Pro 2).

    Joy-Con 2: 63 bytes
    Pro Controller 2: 64 bytes (same structure, +1 padding byte)
    """

    timestamp: int  # u32 LE, data[0:4]
    buttons: int  # u32 LE, data[4:8]
    lx: int  # 12-bit 0-4095, packed in data[10:13]
    ly: int  # 12-bit 0-4095, packed in data[10:13]
    rx: int  # 12-bit 0-4095, packed in data[13:16]
    ry: int  # 12-bit 0-4095, packed in data[13:16]
    trigger_l: int  # u8, data[0x3C]
    trigger_r: int  # u8, data[0x3D]
    gyro_x: int  # i16 LE, data[0x36:0x38]
    gyro_y: int  # i16 LE, data[0x38:0x3A]
    gyro_z: int  # i16 LE, data[0x3A:0x3C]
    accel_x: int  # i16 LE, data[0x30:0x32]
    accel_y: int  # i16 LE, data[0x32:0x34]
    accel_z: int  # i16 LE, data[0x34:0x36]
    battery_mv: int  # u16 LE, data[0x1F:0x21]


# ---------------------------------------------------------------------------
# Button bitmask constants (Switch 2)
# ---------------------------------------------------------------------------


class Buttons:
    """Button bitmask constants for Switch 2 controllers (32-bit LE)."""

    Y: Final = 0x00000001
    X: Final = 0x00000002
    B: Final = 0x00000004
    A: Final = 0x00000008
    SR: Final = 0x00000010  # SR_R / SR_L (right or left)
    SL: Final = 0x00000020  # SL_R / SL_L
    R: Final = 0x00000040
    ZR: Final = 0x00000080
    MINUS: Final = 0x00000100
    PLUS: Final = 0x00000200
    XR: Final = 0x00000400  # R_STK
    LS: Final = 0x00000800  # L_STK
    HOME: Final = 0x00001000
    CAPTURE: Final = 0x00002000
    C: Final = 0x00004000  # GameCube only
    DOWN: Final = 0x00010000
    UP: Final = 0x00020000
    DRIGHT: Final = 0x00040000
    DLEFT: Final = 0x00080000
    # Left-side buttons (Joy-Con Left / Pro)
    SR_L: Final = 0x00100000
    SL_L: Final = 0x00200000
    LR: Final = 0x00400000  # L trigger digital
    ZL: Final = 0x00800000
    # GameCube grips
    GR: Final = 0x01000000
    GL: Final = 0x02000000

    _ALL = {
        "Y": Y,
        "X": X,
        "B": B,
        "A": A,
        "SR": SR,
        "SL": SL,
        "R": R,
        "ZR": ZR,
        "MINUS": MINUS,
        "PLUS": PLUS,
        "XR": XR,
        "LS": LS,
        "HOME": HOME,
        "CAPTURE": CAPTURE,
        "DOWN": DOWN,
        "UP": UP,
        "DRIGHT": DRIGHT,
        "DLEFT": DLEFT,
        "LR": LR,
        "ZL": ZL,
    }

    @classmethod
    def pressed(cls, buttons: int) -> list[str]:
        return [name for name, mask in cls._ALL.items() if buttons & mask]


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _decode_stick_12bit(data: bytes) -> tuple[int, int]:
    """Decode 3 packed bytes into two 12-bit (0..4095) stick axis values."""
    value = int.from_bytes(data, byteorder="little", signed=False)
    return value & 0xFFF, value >> 12


def decode_input_report(raw: bytes) -> InputReport:
    """Decode a Joy-Con 2 / Pro 2 input report (63 or 64 bytes).

    Joy-Con 2 layout (63 bytes):
      [0:4]  timestamp (u32 LE)
      [4:8]  buttons (u32 LE)
      [8:10] padding / reserved
      [10:13] left stick (packed 12-bit LE)
      [13:16] right stick (packed 12-bit LE)
      ... various reserved bytes ...
      [0x1F:0x21] battery_mv (u16 LE)
      [0x30:0x32] accel_x (i16 LE)
      [0x32:0x34] accel_y (i16 LE)
      [0x34:0x36] accel_z (i16 LE)
      [0x36:0x38] gyro_x (i16 LE)
      [0x38:0x3A] gyro_y (i16 LE)
      [0x3A:0x3C] gyro_z (i16 LE)
      [0x3C] trigger_l (u8)
      [0x3D] trigger_r (u8)
    """
    if len(raw) < JOYCON2_REPORT_SIZE:
        raise ValueError(
            f"Input report too short: expected {JOYCON2_REPORT_SIZE} bytes, got {len(raw)}"
        )

    timestamp = int.from_bytes(raw[0:4], "little")
    buttons = int.from_bytes(raw[4:8], "little")
    lx, ly = _decode_stick_12bit(raw[10:13])
    rx, ry = _decode_stick_12bit(raw[13:16])
    battery_mv = int.from_bytes(raw[0x1F:0x21], "little")

    accel_x = int.from_bytes(raw[0x30:0x32], "little", signed=True)
    accel_y = int.from_bytes(raw[0x32:0x34], "little", signed=True)
    accel_z = int.from_bytes(raw[0x34:0x36], "little", signed=True)

    gyro_x = int.from_bytes(raw[0x36:0x38], "little", signed=True)
    gyro_y = int.from_bytes(raw[0x38:0x3A], "little", signed=True)
    gyro_z = int.from_bytes(raw[0x3A:0x3C], "little", signed=True)

    trigger_l = raw[0x3C] if len(raw) > 0x3C else 0
    trigger_r = raw[0x3D] if len(raw) > 0x3D else 0

    return InputReport(
        timestamp=timestamp,
        buttons=buttons,
        lx=lx,
        ly=ly,
        rx=rx,
        ry=ry,
        trigger_l=trigger_l,
        trigger_r=trigger_r,
        gyro_x=gyro_x,
        gyro_y=gyro_y,
        gyro_z=gyro_z,
        accel_x=accel_x,
        accel_y=accel_y,
        accel_z=accel_z,
        battery_mv=battery_mv,
    )


# ---------------------------------------------------------------------------
# Command encoding
# ---------------------------------------------------------------------------


def encode_command(cmd_id: int, sub_id: int, payload: bytes = b"") -> bytes:
    """Encode a Switch 2 controller command.

    Framing (0x91 protocol):
        cmd_id + 0x91 0x01 + sub_id + 0x00 + len + 0x00 0x00 + payload
    """
    return (
        bytes([cmd_id])
        + bytes([0x91, 0x01])
        + bytes([sub_id])
        + bytes([0x00])
        + bytes([len(payload)])
        + bytes([0x00, 0x00])
        + payload
    )


# ---------------------------------------------------------------------------
# HD Rumble encoding
# ---------------------------------------------------------------------------


def encode_rumble_frame(
    high_freq: int, low_freq: int, high_amp: int, low_amp: int
) -> bytes:
    """Encode a single 5-byte HD Rumble frame."""
    hf = high_freq & 0x1FF
    hf_amp = high_amp & 0x3FF
    lf = low_freq & 0x1FF
    lf_amp = low_amp & 0x3FF

    frame = bytearray(5)
    frame[0] = hf & 0xFF
    frame[1] = ((hf >> 8) & 0x03) | ((hf_amp >> 2) & 0xFC)
    frame[2] = ((hf_amp >> 6) & 0x0F) | ((lf >> 4) & 0xF0)
    frame[3] = ((lf >> 8) & 0x03) | ((lf_amp >> 4) & 0xFC)
    frame[4] = ((lf_amp >> 8) & 0x03) | 0xFC
    return bytes(frame)


def encode_rumble_packet(
    high_freq: int,
    low_freq: int,
    high_amp: int = 0,
    low_amp: int = 0,
    num_frames: int = 3,
) -> bytes:
    """Encode an HD Rumble packet for writing to a rumble UUID."""
    frame = encode_rumble_frame(high_freq, low_freq, high_amp, low_amp)
    buf = bytearray()
    buf.append(0x00)
    buf.append(0x50)
    for _ in range(num_frames):
        buf.extend(frame)
    return bytes(buf)
