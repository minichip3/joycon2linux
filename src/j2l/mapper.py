"""Joy-Con binary → evdev event mapping."""

# TODO: Implement input report → evdev event mapping:
#   - Button bits → EV_KEY events
#   - Stick raw values → EV_ABS axis events (deadzone, curve)
#   - Gyro/accel → reserved for DSU passthrough
#   - Handle both single Joy-Con and combined mode mappings
