"""YAML configuration persistence."""

# TODO: Implement config management:
#   - Load/save config.yaml (paired MACs, preferences, DSU settings)
#   - CLI flags override file defaults
#   - Handle missing config gracefully (create on first pair)

import yaml
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "joycon2linux"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

DEFAULT_CONFIG = {
    "controllers": {},  # MAC -> {type, name, combined, gamepad_name}
    "auto_connect": True,
    "dsu_enabled": False,
    "dsu_port": 26760,
    "log_level": "INFO",
}


class Config:
    """Loads and persists joycon2linux configuration as YAML."""

    def __init__(self, path=None):
        self._path = Path(path) if path else CONFIG_PATH
        self._data = self._load()

    def _load(self):
        if self._path.exists():
            with open(self._path) as f:
                data = yaml.safe_load(f)
            if data is None:
                data = {}
            # Merge in any missing defaults (e.g. after config schema updates).
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
        return dict(DEFAULT_CONFIG)

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def get_controller(self, mac: str):
        return self._data["controllers"].get(mac)

    def add_controller(self, mac, controller_type, name=None, combined=True, gamepad_name=None):
        self._data["controllers"][mac] = {
            "type": controller_type,
            "name": name or "Unknown",
            "combined": combined,
            "gamepad_name": gamepad_name,
        }
        self.save()

    def remove_controller(self, mac):
        self._data["controllers"].pop(mac, None)
        self.save()

    def list_controllers(self):
        return dict(self._data["controllers"])
