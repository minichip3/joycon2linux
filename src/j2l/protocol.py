"""Nintendo GATT protocol constants, data classes, and parsing helpers.

Covers Switch 2 controller input reports (Joy-Con 2 / Pro Controller 2),
command encoding, and HD Rumble packet generation.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# GATT Characteristic UUIDs
# ---------------------------------------------------------------------------

COMMAND_WRITE_UUID: Final = "649d4ac9-8eb7-4e6c-af44-1ea54fe5f005"
COMMAND_RESPONSE_UUID: Final = "c765a961-d9d8-4d36-a20a-5315b111836a"
INPUT_REPORT_UUID: Final = "ab7de9be-89fe-49ad-828f-118f09df7fd2"
INPUT_REPORT_LEGACY_UUID: Final = "7492866c-ec3e-4619-8258-32755ffcc0f8"
RUMBLE_PRO_UUID: Final = "cc483f51-9258-427d-a939-630c31f72b05"
RUMBLE_JOYCON_L_UUID: Final = "289326cb-a471-485d-a8f4-240c14f18241"
RUMBLE_JOYCON_R_UUID: Final = "fa19b0fb-cd1f-46a7-84a1-bbb09e00c149"

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

NINTENDO_COMPANY_ID: Final = 0x0553
NINTENDO_INPUT_REPORT_ID: Final = 0x05
REPORT_SIZE: Final = 64  # bytes — Switch 2 input report size

# ---------------------------------------------------------------------------
# Input report data class
# ---------------------------------------------------------------------------


@dataclass
class InputReport:
    """Decoded Switch 2 controller input report.

    Common layout for Joy-Con 2 and Pro Controller 2.
    """

    report_id: int  # 0x05 (NINTENDO_INPUT_REPORT_ID)
    channel: int  # BLE channel ID
    sequence: int  # frame sequence number
    buttons: int  # button state bitmask
    lx: int  # left stick X (0-255, 128=center)
    ly: int  # left stick Y (0-255, 128=center)
    rx: int  # right stick X (0-255, 128=center)
    ry: int  # right stick Y (0-255, 128=center)
    trigger_l: int  # left trigger (0-255)
    trigger_r: int  # right trigger (0-255)
    gyro_x: int  # signed gyro X (16-bit little-endian)
    gyro_y: int  # signed gyro Y
    gyro_z: int  # signed gyro Z
    accel_x: int  # signed accel X (16-bit little-endian)
    accel_y: int  # signed accel Y
    accel_z: int  # signed accel Z
    battery: int  # battery level (0-3)
    timestamp: int  # controller internal timestamp


# ---------------------------------------------------------------------------
# Button bitmask constants
# ---------------------------------------------------------------------------


class Buttons:
    """Button bitmask constants for Switch 2 controllers."""

    R: Final = 1 << 0
    ZR: Final = 1 << 1
    DRIGHT: Final = 1 << 2
    B: Final = 1 << 3
    Y: Final = 1 << 4
    XR: Final = 1 << 5
    HOME: Final = 1 << 6
    PLUS: Final = 1 << 7
    MINUS: Final = 1 << 8
    LS: Final = 1 << 9
    LR: Final = 1 << 10
    A: Final = 1 << 11
    X: Final = 1 << 12
    DLEFT: Final = 1 << 13
    DOWN: Final = 1 << 14
    UP: Final = 1 << 15
    SL: Final = 1 << 16
    SR: Final = 1 << 17
    CAPTURE: Final = 1 << 18  # Joy-Con only
    # Additional bits (19+) reserved for future / manufacturer-specific use.

    # Convenience: all button masks in one set for iteration.
    _ALL = {
        "R": R,
        "ZR": ZR,
        "DRIGHT": DRIGHT,
        "B": B,
        "Y": Y,
        "XR": XR,
        "HOME": HOME,
        "PLUS": PLUS,
        "MINUS": MINUS,
        "LS": LS,
        "LR": LR,
        "A": A,
        "X": X,
        "DLEFT": DLEFT,
        "DOWN": DOWN,
        "UP": UP,
        "SL": SL,
        "SR": SR,
        "CAPTURE": CAPTURE,
    }

    @classmethod
    def pressed(cls, buttons: int) -> list[str]:
        """Return a list of button names that are pressed in *buttons* mask."""
        return [name for name, mask in cls._ALL.items() if buttons & mask]


# ---------------------------------------------------------------------------
# Struct format strings (cached)
# ---------------------------------------------------------------------------

# Input report: 27 bytes of structured data, then 37 bytes padding.
# Byte layout (little-endian):
#   0:   u8  report_id
#   1:   u8  channel
#   2-3: H  sequence (LE u16)
#   4-5: H  buttons (LE u16)
#   6:   B  lx
#   7:   B  ly
#   8:   B  rx
#   9:   B  ry
#   10:  B  trigger_l
#   11:  B  trigger_r
#   12-13: h  gyro_x (LE signed i16)
#   14-15: h  gyro_y
#   16-17: h  gyro_z
#   18-19: h  accel_x
#   20-21: h  accel_y
#   22-23: h  accel_z
#   24:  B  battery
#   25-26: H  timestamp (LE u16)

_INPUT_REPORT_FMT: Final = "<BBHHBBBBBBhhhhhhhBH"
#                            ^^ ^^ ^^^^^^^^ ^^^^^^^ ^^ ^
#                            ridch seqbtn lxlyrxrytltr gxgy gz axayaz bat ts
_INPUT_REPORT_STRUCT: Final = struct.Struct(_INPUT_REPORT_FMT)
_INPUT_REPORT_SIZE: Final = _INPUT_REPORT_STRUCT.size  # == 27


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def decode_input_report(raw: bytes) -> InputReport:
    """Decode a 64-byte Switch 2 input report.

    Raises:
        ValueError: If *raw* is shorter than :data:`REPORT_SIZE`.
    """
    if len(raw) < REPORT_SIZE:
        raise ValueError(
            f"Input report too short: expected {REPORT_SIZE} bytes, got {len(raw)}"
        )

    # Unpack only the first 27 bytes; the remaining 37 bytes are padding.
    vals = _INPUT_REPORT_STRUCT.unpack_from(raw)
    return InputReport(
        report_id=vals[0],
        channel=vals[1],
        sequence=vals[2],
        buttons=vals[3],
        lx=vals[4],
        ly=vals[5],
        rx=vals[6],
        ry=vals[7],
        trigger_l=vals[8],
        trigger_r=vals[9],
        gyro_x=vals[10],
        gyro_y=vals[11],
        gyro_z=vals[12],
        accel_x=vals[13],
        accel_y=vals[14],
        accel_z=vals[15],
        battery=vals[16],
        timestamp=vals[17],
    )


# ---------------------------------------------------------------------------
# Command encoding
# ---------------------------------------------------------------------------


def encode_command(cmd_type: int, payload: bytes = b"") -> bytes:
    """Encode a command packet for writing to :data:`COMMAND_WRITE_UUID`.

    Layout:
        Byte 0: command_type
        Byte 1: payload length (max 62, leaving 2 bytes for header)
        Byte 2-N: payload
        Remaining bytes up to :data:`REPORT_SIZE`: zero-padded

    Raises:
        ValueError: If *cmd_type* or *payload* length exceeds limits.
    """
    max_payload = REPORT_SIZE - 2  # 2 header bytes
    if not (0 <= cmd_type <= 255):
        raise ValueError(f"cmd_type must be 0-255, got {cmd_type}")
    if len(payload) > max_payload:
        raise ValueError(
            f"payload too long: max {max_payload} bytes, got {len(payload)}"
        )

    buf = bytearray(REPORT_SIZE)
    buf[0] = cmd_type
    buf[1] = len(payload)
    buf[2 : 2 + len(payload)] = payload
    return bytes(buf)


# ---------------------------------------------------------------------------
# HD Rumble encoding
# ---------------------------------------------------------------------------


def encode_rumble_frame(
    high_freq: int, low_freq: int, high_amp: int, low_amp: int
) -> bytes:
    """Encode a single 5-byte HD Rumble frame.

    Nintendo HD Rumble bit layout (5 bytes, little-endian):

        Byte 0: bits [7:0]   → high frequency (lower 8 bits)
        Byte 1: bits [9:8]   → high frequency (upper 2 bits)
               bits [13:10]  → high amplitude (lower 4 bits)
        Byte 2: bits [17:14] → high amplitude (upper 4 bits)
               bits [19:16]  → low frequency (lower 4 bits)
        Byte 3: bits [21:20] → low frequency (upper 2 bits)
               bits [29:22]  → low amplitude (lower 6 bits)
        Byte 4: bits [31:30] → low amplitude (upper 2 bits)

    Values are masked to their valid bit-widths (9-bit freq, 10-bit amp).

    Returns:
        5 bytes packed frame.
    """
    hf = high_freq & 0x1FF  # 9 bits
    hf_amp = high_amp & 0x3FF  # 10 bits
    lf = low_freq & 0x1FF  # 9 bits
    lf_amp = low_amp & 0x3FF  # 10 bits

    frame = bytearray(5)
    # Byte 0: HF[7:0]
    frame[0] = hf & 0xFF
    # Byte 1: HF[9:8] | HF_AMP[13:10]
    frame[1] = ((hf >> 8) & 0x03) | ((hf_amp >> 2) & 0xFC)
    # Byte 2: HF_AMP[19:16] | LF[19:16]  →  HF_AMP upper 4 bits | LF lower 4 bits
    frame[2] = ((hf_amp >> 6) & 0x0F) | ((lf >> 4) & 0xF0)
    # Byte 3: LF[21:20] | LF_AMP[29:22]  →  LF upper 2 bits | LF_AMP lower 6 bits
    frame[3] = ((lf >> 8) & 0x03) | ((lf_amp >> 4) & 0xFC)
    # Byte 4: LF_AMP[31:30]  →  LF_AMP upper 2 bits
    frame[4] = ((lf_amp >> 8) & 0x03) | 0xFC  # upper 6 bits zero-filled
    return bytes(frame)


def encode_rumble_packet(
    high_freq: int,
    low_freq: int,
    high_amp: int = 0,
    low_amp: int = 0,
    num_frames: int = 3,
) -> bytes:
    """Encode an HD Rumble packet for writing to a rumble UUID.

    Nintendo rumble packets contain 3 consecutive 5-byte frames (spanning
    ~20 ms of playback at 144 Hz).  This function generates *num_frames*
    copies of the same frame and prepends the 2-byte header.

    Header layout:
        Byte 0: motor command (0x00)
        Byte 1: packet ID (0x50 | rolling 4-bit ID)

    Args:
        high_freq: High-frequency motor frequency (0-511).
        low_freq: Low-frequency motor frequency (0-511).
        high_amp: High-frequency motor amplitude (0-1023).
        low_amp: Low-frequency motor amplitude (0-1023).
        num_frames: Number of 5-byte frames (default 3).

    Returns:
        Bytes ready to write to the rumble characteristic.
    """
    frame = encode_rumble_frame(high_freq, low_freq, high_amp, low_amp)
    buf = bytearray()
    buf.append(0x00)  # motor command
    buf.append(0x50)  # packet ID (rolling ID left to caller/firmware)
    for _ in range(num_frames):
        buf.extend(frame)
    return bytes(buf)
