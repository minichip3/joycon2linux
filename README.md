# joycon2linux

Joy-Con 2 and Switch 2 Pro Controller bridge for Linux via BLE GATT.

## Features
- BLE GATT connection (via bleak) — no raw L2CAP needed
- uinput virtual gamepad (Xbox-style evdev layout)
- HD Rumble support
- DSU/cemuhook UDP server for gyro passthrough
- Single + combined Joy-Con 2 modes
- Pro Controller 2 support

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
sudo scripts/setup.sh
```

## Usage
```bash
j2l scan          # Discover controllers
j2l pair <MAC>    # Pair a controller
j2l run           # Auto-connect and expose as gamepad
j2l run --combined  # Combine left+right Joy-Con
j2l run --dsu     # Enable DSU gyro server
```

## Requirements
- Python 3.10+
- root privileges for uinput / Bluetooth
- working BlueZ / D-Bus environment
