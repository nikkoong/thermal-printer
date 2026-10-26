from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("THERMAL_CONFIG_DIR", "~/.config/thermal-printer")).expanduser()
CONFIG_PATH = APP_DIR / "config.json"
DATA_DIR = Path(
    os.environ.get("THERMAL_DATA_DIR", "~/.local/share/thermal-printer")
).expanduser()


@dataclass
class Config:
    """User-editable settings.

    The defaults target the MXW01 ("Fun Print") printer.  The LuckPrinter /
    DP-L1S family is supported as a fallback; every value can be overridden
    with an environment variable or the config file at
    ``~/.config/thermal-printer``.
    """

    transport: str = "auto"  # auto | ble | serial | mock
    protocol: str = "auto"  # auto | mxw01 | luck | escpos
    device_name: str | None = None
    device_address: str | None = None

    width_px: int = 384
    density: int = 1  # 0..2
    feed_dots: int = 80
    label_mode: bool = False

    chunk_size: int = 512
    chunk_delay_ms: int = 10
    connect_timeout: float = 20.0

    # Override these only if your printer exposes a different GATT layout.
    service_uuid: str | None = None
    write_uuid: str | None = None
    notify_uuid: str | None = None
    data_uuid: str | None = None

    serial_port: str | None = None
    baudrate: int = 9600

    api_host: str = "127.0.0.1"
    api_port: int = 8787
    auto_connect: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        cfg = cls()
        config_path = path or CONFIG_PATH
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Could not read {config_path}: {exc}") from exc
            cfg = cfg.merged(raw)
        return cfg.merged(_env_overrides())

    def merged(self, values: dict[str, Any]) -> Config:
        known = {f.name for f in fields(self)}
        data = asdict(self)
        data.update({k: v for k, v in values.items() if k in known and v is not None})
        return Config(**data)

    def save(self, path: Path | None = None) -> Path:
        config_path = path or CONFIG_PATH
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return config_path

    def public(self) -> dict[str, Any]:
        """Config safe to expose to the local web UI."""
        return asdict(self)

    @property
    def api_url(self) -> str:
        host = self.api_host
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        return f"http://{host}:{self.api_port}"


def _env_overrides() -> dict[str, Any]:
    mapping = {
        "THERMAL_TRANSPORT": ("transport", str),
        "THERMAL_PROTOCOL": ("protocol", str),
        "THERMAL_DEVICE": ("device_name", str),
        "THERMAL_ADDRESS": ("device_address", str),
        "THERMAL_WIDTH": ("width_px", int),
        "THERMAL_DENSITY": ("density", int),
        "THERMAL_FEED": ("feed_dots", int),
        "THERMAL_LABEL_MODE": ("label_mode", _as_bool),
        "THERMAL_CHUNK_SIZE": ("chunk_size", int),
        "THERMAL_CHUNK_DELAY_MS": ("chunk_delay_ms", int),
        "THERMAL_SERVICE_UUID": ("service_uuid", str),
        "THERMAL_WRITE_UUID": ("write_uuid", str),
        "THERMAL_NOTIFY_UUID": ("notify_uuid", str),
        "THERMAL_DATA_UUID": ("data_uuid", str),
        "THERMAL_SERIAL_PORT": ("serial_port", str),
        "THERMAL_BAUDRATE": ("baudrate", int),
        "THERMAL_API_HOST": ("api_host", str),
        "THERMAL_API_PORT": ("api_port", int),
        "THERMAL_AUTO_CONNECT": ("auto_connect", _as_bool),
    }
    out: dict[str, Any] = {}
    for env, (key, caster) in mapping.items():
        value = os.environ.get(env)
        if value is not None and value != "":
            out[key] = caster(value)
    if os.environ.get("THERMAL_MOCK", "").lower() in {"1", "true", "yes"}:
        out["transport"] = "mock"
    return out


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
