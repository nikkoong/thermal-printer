from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import DATA_DIR, Config
from .base import PrinterTransport


class MockTransport(PrinterTransport):
    """A printer that writes PNGs to disk, useful before hardware arrives."""

    name = "mock"

    def __init__(self, config: Config, output_dir: Path | None = None) -> None:
        self.config = config
        self.output_dir = output_dir or (DATA_DIR / "mock")
        self._connected = False
        self.last_path: Path | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def label(self) -> str:
        return "Mock printer (PNG output)"

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def print_image(
        self,
        image: Image.Image,
        *,
        density: int | None = None,
        feed_dots: int = 80,
        label_mode: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.last_path = self.output_dir / f"mock-{stamp}.png"
        image.save(self.last_path)
        return {
            "transport": self.name,
            "bytes": 0,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "acked": True,
            "path": str(self.last_path),
            "note": "Mock print: PNG written instead of sent to hardware.",
        }

    async def status(self) -> Any:
        from ..protocol import PrinterStatus

        return PrinterStatus(0)

    async def battery(self) -> int:
        return 100

    async def info(self) -> dict[str, Any]:
        return {
            "model": "Mock thermal printer",
            "firmware": "0.1",
            "serial": "MOCK-0001",
            "all_info": "mock",
        }

    async def diagnostics(self) -> dict[str, Any]:
        return {
            "transport": self.name,
            "output_dir": str(self.output_dir),
            "info": await self.info(),
            "status": (await self.status()).as_dict(),
            "battery": await self.battery(),
        }
