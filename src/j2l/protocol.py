"""Nintendo GATT protocol constants and parsing helpers."""

# GATT Characteristic UUIDs
COMMAND_WRITE_UUID = "649d4ac9-8eb7-4e6c-af44-1ea54fe5f005"
COMMAND_RESPONSE_UUID = "c765a961-d9d8-4d36-a20a-5315b111836a"
INPUT_REPORT_UUID = "ab7de9be-89fe-49ad-828f-118f09df7fd2"
INPUT_REPORT_LEGACY_UUID = "7492866c-ec3e-4619-8258-32755ffcc0f8"
RUMBLE_PRO_UUID = "cc483f51-9258-427d-a939-630c31f72b05"
RUMBLE_JOYCON_L_UUID = "289326cb-a471-485d-a8f4-240c14f18241"
RUMBLE_JOYCON_R_UUID = "fa19b0fb-cd1f-46a7-84a1-bbb09e00c149"

# TODO: Implement protocol parsing:
#   - Decode input report binary (button states, sticks, gyro, battery)
#   - Encode command packets (initialization, pairing, LED, rumble config)
#   - Handle both Joy-Con 2 and Pro Controller 2 report formats
