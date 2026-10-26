from __future__ import annotations

import asyncio
import time
from typing import Any

from PIL import Image

from .. import protocol
from ..config import Config
from .base import ProtocolMismatch, TransportError
from .luck import LuckBleTransport, normalize_uuid

MXW01_SERVICE = "0000ae30-0000-1000-8000-00805f9b34fb"
MXW01_CONTROL = "0000ae01-0000-1000-8000-00805f9b34fb"
MXW01_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"
MXW01_DATA = "0000ae03-0000-1000-8000-00805f9b34fb"

LUCK_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"
LUCK_WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"


class Mxw01BleTransport(LuckBleTransport):
    """BLE transport for the MXW01 / "Fun Print" cat printer.

    The MXW01 uses a vendor service (AE30) with three relevant
    characteristics: AE01 for commands, AE02 for notifications, and AE03 for
    the raw 1-bit image stream.  Commands are framed as
    ``22 21 <cmd> 00 <len LE16> <payload> <crc8> FF``.
    """

    name = "ble"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._data_uuid: str | None = None
        self._responses: dict[int, bytes] = {}

    @property
    def protocol_name(self) -> str:
        return "mxw01"

    # ------------------------------------------------------------------
    # GATT discovery

    async def _resolve_characteristics(self) -> None:
        assert self._client is not None
        services: list[dict[str, Any]] = []
        writable: list[str] = []
        notifiable: list[str] = []

        for service in self._client.services:
            chars = []
            for char in service.characteristics:
                props = list(char.properties or [])
                chars.append({"uuid": char.uuid, "properties": props})
                normalized = normalize_uuid(char.uuid)
                if any(p in props for p in ("write", "write-without-response")):
                    writable.append(normalized)
                if "notify" in props or "indicate" in props:
                    notifiable.append(normalized)
            services.append({"uuid": service.uuid, "characteristics": chars})
        self._services = services

        service_uuids = {normalize_uuid(service["uuid"]) for service in services}
        if MXW01_SERVICE not in service_uuids and not any(
            uuid in writable for uuid in (MXW01_CONTROL, MXW01_DATA)
        ):
            if LUCK_SERVICE in service_uuids or LUCK_WRITE in writable:
                raise ProtocolMismatch(
                    "This device exposes the LuckPrinter GATT layout, not MXW01. "
                    "Set protocol to 'luck' (or 'auto') and retry.",
                    "luck",
                )
            raise TransportError(
                "No MXW01 service (AE30) or AE01/AE03 characteristics found. "
                "Run `uv run thermal doctor --json` for a full GATT dump."
            )

        service_uuid = (
            normalize_uuid(self.config.service_uuid)
            if self.config.service_uuid
            else MXW01_SERVICE
        )
        control = normalize_uuid(self.config.write_uuid) if self.config.write_uuid else None
        notify = normalize_uuid(self.config.notify_uuid) if self.config.notify_uuid else None
        data = normalize_uuid(self.config.data_uuid) if self.config.data_uuid else None

        if control is None:
            control = MXW01_CONTROL if MXW01_CONTROL in writable else (
                writable[0] if writable else None
            )
        if notify is None:
            notify = MXW01_NOTIFY if MXW01_NOTIFY in notifiable else (
                notifiable[0] if notifiable else None
            )
        if data is None:
            data = MXW01_DATA if MXW01_DATA in writable else next(
                (uuid for uuid in writable if uuid != control), None
            )

        # A custom service should win over the documented UUIDs.
        if self.config.service_uuid:
            for service in services:
                if normalize_uuid(service["uuid"]) != service_uuid:
                    continue
                inside = [normalize_uuid(c["uuid"]) for c in service["characteristics"]]
                inside_writable = [uuid for uuid in inside if uuid in writable]
                inside_notifiable = [uuid for uuid in inside if uuid in notifiable]
                if inside_writable:
                    control = inside_writable[0]
                    data = next(
                        (uuid for uuid in inside_writable if uuid != control),
                        inside_writable[0],
                    )
                if inside_notifiable:
                    notify = inside_notifiable[0]
                break

        if control is None:
            raise TransportError(
                "No writable MXW01 control characteristic found. "
                "Run `uv run thermal doctor --json` for a full GATT dump."
            )
        if data is None or data == control:
            raise TransportError(
                "No separate MXW01 data characteristic (AE03) found. "
                "Set data_uuid explicitly or run `uv run thermal doctor --json`."
            )

        self._write_uuid = control
        self._notify_uuid = notify
        self._data_uuid = data

    # ------------------------------------------------------------------
    # Notifications

    def _drain_frames(self) -> None:
        while True:
            start = self._buffer.find(protocol.MXW01_HEADER)
            if start < 0:
                # Keep a possible partial header (0x22) for the next packet.
                if len(self._buffer) > 1:
                    del self._buffer[:-1]
                return
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 6:
                return
            length = int.from_bytes(self._buffer[4:6], "little")
            end = 6 + length
            if len(self._buffer) < end:
                return
            command = self._buffer[2]
            payload = bytes(self._buffer[6:end])
            del self._buffer[:end]
            self._responses[command] = payload
            self._event.set()

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        self._buffer.extend(data)
        self._drain_frames()

    async def _wait_for(self, command: int, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while True:
            self._drain_frames()
            if command in self._responses:
                return self._responses.pop(command)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._event.wait(), timeout=min(0.2, remaining))
                self._event.clear()
            except TimeoutError:
                continue

    async def _send_frame(self, command: int, data: bytes = b"") -> None:
        await self._write_chunk(protocol.mxw01_frame(command, data))
        await asyncio.sleep(0.01)

    async def _query(self, command: int, data: bytes = b"\x00", timeout: float = 4.0) -> bytes | None:
        self._responses.pop(command, None)
        await self._send_frame(command, data)
        return await self._wait_for(command, timeout)

    # ------------------------------------------------------------------
    # I/O

    async def _write_data(self, data: bytes) -> None:
        assert self._client is not None and self._data_uuid is not None
        # The MXW01 reference implementations write one 48-byte row at a time.
        # Respect a smaller configured chunk size, but never exceed one row.
        chunk_size = max(20, min(int(self.config.chunk_size or 48), 48))
        delay = max(0, self.config.chunk_delay_ms) / 1000.0
        for start in range(0, len(data), chunk_size):
            await self._write_chunk(data[start : start + chunk_size], uuid=self._data_uuid)
            if delay:
                await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Public operations

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
        async with self._io_lock:
            started = time.monotonic()
            data, height, width_bytes = protocol.mxw01_raster(
                image, feed_dots=feed_dots, width_px=self.config.width_px
            )

            # A1 status check.  If the printer does not answer, keep going:
            # some firmware revisions only notify while printing.
            status_payload = await self._query(protocol.MXW01_CMD_STATUS, timeout=4.0)
            status = protocol.parse_mxw01_status(status_payload or b"")
            if status is not None and not status.ok:
                raise TransportError(f"Printer reports: {status.describe()}")

            intensity = protocol.mxw01_intensity(
                self.config.density if density is None else density
            )
            await self._send_frame(protocol.MXW01_CMD_INTENSITY, bytes([intensity]))

            request = height.to_bytes(2, "little") + bytes([width_bytes, 0x00])
            ack = await self._query(protocol.MXW01_CMD_PRINT, request, timeout=5.0)
            if ack and ack[0] != 0:
                raise TransportError(f"Printer rejected the print request (status {ack[0]})")

            await self._write_data(data)
            await self._send_frame(protocol.MXW01_CMD_FLUSH, b"\x00")

            timeout = max(15.0, height / 20.0)
            complete = await self._wait_for(protocol.MXW01_CMD_PRINT_COMPLETE, timeout)
            return {
                "transport": self.name,
                "protocol": self.protocol_name,
                "bytes": len(data),
                "height": height,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "acked": complete is not None,
                "status": status.as_dict() if status else None,
            }

    async def status(self) -> protocol.Mxw01Status | None:
        async with self._io_lock:
            payload = await self._query(protocol.MXW01_CMD_STATUS, timeout=5.0)
        return protocol.parse_mxw01_status(payload or b"")

    async def battery(self) -> int | None:
        async with self._io_lock:
            payload = await self._query(protocol.MXW01_CMD_BATTERY, timeout=4.0)
        return protocol.parse_mxw01_battery(payload or b"")

    async def info(self) -> dict[str, Any]:
        async with self._io_lock:
            payload = await self._query(protocol.MXW01_CMD_VERSION, timeout=4.0)
        return {
            "model": "MXW01",
            "firmware": protocol.decode_mxw01_version(payload or b""),
            "version_raw": (payload or b"").hex(" "),
        }

    async def diagnostics(self) -> dict[str, Any]:
        result = await super().diagnostics()
        result["protocol"] = self.protocol_name
        result["data_uuid"] = self._data_uuid
        return result

    async def disconnect(self) -> None:
        await super().disconnect()
        self._data_uuid = None
        self._responses.clear()
        self._buffer.clear()
