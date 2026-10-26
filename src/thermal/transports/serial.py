from __future__ import annotations

import contextlib
import time
from typing import Any

from PIL import Image

from .. import protocol
from ..config import Config
from .base import PrinterTransport, TransportError

try:
    import serial
    from serial.tools import list_ports
except Exception as exc:  # pragma: no cover
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]
    _SERIAL_IMPORT_ERROR = exc
else:
    _SERIAL_IMPORT_ERROR = None


class SerialEscPosTransport(PrinterTransport):
    """ESC/POS over a USB serial adapter (only useful if USB-C does data)."""

    name = "serial"

    def __init__(self, config: Config) -> None:
        self.config = config
        self._port: Any | None = None

    @property
    def connected(self) -> bool:
        return bool(self._port is not None and getattr(self._port, "is_open", False))

    @property
    def label(self) -> str:
        return self.config.serial_port or "serial printer"

    async def connect(self) -> None:
        if self.connected:
            return
        if _SERIAL_IMPORT_ERROR is not None:
            raise TransportError(f"pyserial could not be loaded: {_SERIAL_IMPORT_ERROR}")
        if not self.config.serial_port:
            raise TransportError(
                "No serial_port configured. Run `uv run thermal usb-probe`, then set "
                "THERMAL_SERIAL_PORT (for example /dev/cu.usbserial-0001)."
            )
        try:
            self._port = serial.Serial(
                self.config.serial_port,
                baudrate=self.config.baudrate,
                timeout=2,
                write_timeout=20,
            )
        except Exception as exc:
            raise TransportError(f"Could not open {self.config.serial_port}: {exc}") from exc

    async def disconnect(self) -> None:
        port = self._port
        self._port = None
        if port is not None:
            with contextlib.suppress(Exception):
                port.close()

    async def print_image(
        self,
        image: Image.Image,
        *,
        density: int | None = None,
        feed_dots: int = 80,
        label_mode: bool = False,
    ) -> dict[str, Any]:
        if not self.connected:
            await self.connect()
        assert self._port is not None
        started = time.monotonic()
        payload = protocol.build_escpos_job(
            image,
            feed_dots=feed_dots,
            density=self.config.density if density is None else density,
            width_px=self.config.width_px,
        )
        self._port.write(payload)
        self._port.flush()
        return {
            "transport": self.name,
            "bytes": len(payload),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "acked": False,
        }

    async def status(self) -> None:
        return None

    async def battery(self) -> None:
        return None

    async def info(self) -> dict[str, Any]:
        return {"port": self.config.serial_port, "baudrate": self.config.baudrate}

    async def diagnostics(self) -> dict[str, Any]:
        return {
            "transport": self.name,
            "port": self.config.serial_port,
            "baudrate": self.config.baudrate,
            "serial_ports": list_serial_ports(),
        }


def list_serial_ports() -> list[dict[str, Any]]:
    if list_ports is None:
        return []
    out = []
    for port in list_ports.comports():
        out.append(
            {
                "device": port.device,
                "description": port.description,
                "hwid": port.hwid,
                "vid": port.vid,
                "pid": port.pid,
            }
        )
    return out
