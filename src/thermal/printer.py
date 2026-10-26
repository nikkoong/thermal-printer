from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from .config import Config
from .history import HistoryStore, JobRecord
from .render import PrintRequest, RenderedJob, render
from .transports import (
    Device,
    LuckBleTransport,
    MockTransport,
    PrinterTransport,
    ProtocolMismatch,
    SerialEscPosTransport,
    TransportError,
    make_ble_transport,
    make_transport,
)


class PrinterManager:
    def __init__(self, config: Config, *, history: HistoryStore | None = None) -> None:
        self.config = config
        self.history = history or HistoryStore()
        self._transport: PrinterTransport | None = None
        self._lock = asyncio.Lock()
        self._last_print_at = 0.0
        self._status_cache: dict[str, Any] | None = None
        self._status_at = 0.0
        self._info_cache: dict[str, Any] | None = None
        self._last_error: str | None = None

    @property
    def transport(self) -> PrinterTransport | None:
        return self._transport

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _make_transport(self, name: str | None = None) -> PrinterTransport:
        transport_name = (name or self.config.transport or "auto").lower()
        if transport_name in {"mock", "virtual"}:
            return MockTransport(self.config)
        if transport_name == "serial":
            return SerialEscPosTransport(self.config)
        if transport_name == "ble":
            return make_ble_transport(self.config)
        if transport_name == "auto":
            return make_transport(self.config)
        raise ValueError(f"Unknown transport {transport_name!r}")

    async def connect(
        self,
        *,
        transport: str | None = None,
        address: str | None = None,
        name: str | None = None,
        protocol: str | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            requested = (transport or "").lower()
            if address:
                self.config.device_address = address
            if name:
                self.config.device_name = name
            if protocol:
                self.config.protocol = protocol

            if not requested and (address or name) and self.config.transport != "serial":
                # A scanned BLE address/name should switch us out of mock or
                # auto mode; only an explicit serial config wins.
                requested = "ble"
            if requested:
                self.config.transport = requested

            if requested and self._transport is not None and self._transport.name != requested:
                await self._transport.disconnect()
                self._transport = None
            if (address or name) and self._transport is not None:
                # Pointing at a different device: drop the old BLE link rather
                # than leaving it connected in the background.
                await self._transport.disconnect()
                self._transport = None

            if self._transport is None:
                self._transport = self._make_transport(requested or None)
            try:
                await self._transport.connect()
            except ProtocolMismatch as exc:
                if (self.config.protocol or "auto").lower() != "auto":
                    self._last_error = str(exc)
                    raise
                # Auto mode: the GATT layout identified the protocol for us.
                # Recreate the transport with the detected protocol and retry.
                await self._transport.disconnect()
                self.config.protocol = exc.suggested
                self._transport = self._make_transport(requested or None)
                try:
                    await self._transport.connect()
                except Exception as retry_exc:
                    self._last_error = str(retry_exc)
                    raise
            except Exception as exc:
                self._last_error = str(exc)
                raise
            self._last_error = None
            self._status_at = 0.0
            self._info_cache = None
            if persist:
                with contextlib.suppress(OSError):
                    self.config.save()
            return await self.status(refresh=True)

    async def disconnect(self) -> None:
        async with self._lock:
            if self._transport is not None:
                await self._transport.disconnect()
            self._status_cache = None
            self._status_at = 0.0

    async def scan(self, timeout: float = 8.0) -> list[Device]:
        return await LuckBleTransport.scan(timeout=timeout)

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        transport = self._transport
        if transport is None:
            return {
                "connected": False,
                "transport": self.config.transport,
                "label": self.config.device_name or self.config.device_address or "not connected",
                "mock": (self.config.transport or "").lower() == "mock",
                "last_error": self._last_error,
            }
        if not transport.connected:
            return {
                "connected": False,
                "transport": transport.name,
                "label": transport.label,
                "last_error": self._last_error or "printer is disconnected",
            }

        now = time.monotonic()
        if self._status_cache is not None and not refresh and now - self._status_at < 30:
            return self._status_cache

        payload: dict[str, Any] = {
            "connected": True,
            "transport": transport.name,
            "label": transport.label,
            "mock": transport.name == "mock",
            "last_error": self._last_error,
        }
        try:
            status = await transport.status()
            if status is not None:
                payload["status"] = status.as_dict()
                payload["status_text"] = status.describe()
        except Exception as exc:
            payload["status_error"] = str(exc)
        try:
            battery = await transport.battery()
            if battery is not None:
                payload["battery"] = battery
        except Exception as exc:
            payload["battery_error"] = str(exc)
        if self._info_cache is None:
            try:
                self._info_cache = await transport.info()
            except Exception as exc:
                payload["info_error"] = str(exc)
        if self._info_cache is not None:
            payload["info"] = self._info_cache

        self._status_cache = payload
        self._status_at = now
        return payload

    async def diagnostics(self) -> dict[str, Any]:
        if self._transport is None:
            self._transport = self._make_transport()
        if not self._transport.connected:
            await self._transport.connect()
        result = await self._transport.diagnostics()
        result["config"] = {
            "transport": self.config.transport,
            "protocol": self.config.protocol,
            "width_px": self.config.width_px,
            "chunk_size": self.config.chunk_size,
            "chunk_delay_ms": self.config.chunk_delay_ms,
            "device_name": self.config.device_name,
            "device_address": self.config.device_address,
        }
        return result

    async def print_request(
        self,
        request: PrintRequest,
        *,
        dry_run: bool = False,
        copies: int = 1,
    ) -> dict[str, Any]:
        copies = max(1, min(5, int(copies)))
        rendered: RenderedJob = render(request)
        async with self._lock:
            if not dry_run:
                if self._transport is None:
                    self._transport = self._make_transport()
                if not self._transport.connected:
                    await self._transport.connect()
                    self._status_at = 0.0

                wait = 0.8 - (time.monotonic() - self._last_print_at)
                if wait > 0:
                    await asyncio.sleep(wait)

            record: JobRecord | None = None
            results: list[dict[str, Any]] = []
            try:
                if not dry_run:
                    assert self._transport is not None
                    for _ in range(copies):
                        results.append(
                            await self._transport.print_image(
                                rendered.image,
                                density=request.density,
                                feed_dots=request.feed_dots,
                                label_mode=request.label_mode,
                            )
                        )
                        self._last_print_at = time.monotonic()
                result = results[-1] if results else {}
                record = self.history.save(
                    rendered.image,
                    title=rendered.title,
                    style=rendered.style,
                    source=request.source,
                    dry_run=dry_run,
                    printed=not dry_run,
                    byte_count=int(result.get("bytes", 0)),
                    duration_ms=int(result.get("duration_ms", 0)),
                    transport=str(result.get("transport", "dry-run")),
                )
                self._last_error = None
            except Exception as exc:
                # Keep a preview of the failed job so the UI can show what was
                # attempted, then surface the hardware error.
                record = self.history.save(
                    rendered.image,
                    title=rendered.title,
                    style=rendered.style,
                    source=request.source,
                    dry_run=True,
                    printed=False,
                    transport=self._transport.name if self._transport else "",
                )
                self._last_error = str(exc)
                raise TransportError(
                    f"Print failed: {exc}. Preview saved at {record.preview_url}"
                ) from exc

            self._status_cache = None
            return {
                "ok": True,
                "dry_run": dry_run,
                "job": record.as_dict(),
                "result": result,
                "copies": copies,
                "preview_url": record.preview_url,
                "preview_path": str(self.history.directory / f"{record.id}.png"),
            }

    async def close(self) -> None:
        await self.disconnect()
