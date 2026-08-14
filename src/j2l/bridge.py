"""BLE ↔ uinput bridge main loop."""

# TODO: Implement the main bridge loop:
#   - Connect BLE (ble.py) and create uinput gamepad (gamepad.py)
#   - On input notification, decode via protocol.py → mapper.py → evdev events
#   - On evdev rumble events, encode and write via rumble.py → BLE rumble characteristic
#   - Handle graceful shutdown on SIGINT/SIGTERM
