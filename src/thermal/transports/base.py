from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image


class TransportError(RuntimeError):
    pass


class ProtocolMismatch(TransportError):
    """Raised when the connected GATT layout belongs to another protocol.

    ``suggested`` is the protocol name that should be tried instead (for
    example ``"mxw01"`` when a LuckPrinter transport finds an AE30 device).
    """

    def __init__(self, message: str, suggested: str) -> None:
        super().__init__(message)
        self.suggested = suggested


@dataclass
class Device:
    address: str
    name: str
    rssi: int | None = None
    service_uuids: list[str] = field(default_factory=list)
    likely_printer: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "name": self.name,
            "rssi": self.rssi,
            "service_uuids": self.service_uuids,
            "likely_printer": self.likely_printer,
        }


class PrinterTransport(Protocol):
    name: str

    @property
    def connected(self) -> bool: ...

    @property
    def label(self) -> str: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def print_image(
        self,
        image: Image.Image,
        *,
        density: int | None = None,
        feed_dots: int = 80,
        label_mode: bool = False,
    ) -> dict[str, Any]: ...

    async def status(self) -> Any: ...

    async def battery(self) -> int | None: ...

    async def info(self) -> dict[str, Any]: ...

    async def diagnostics(self) -> dict[str, Any]: ...
