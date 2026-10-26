from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from PIL import Image

from .. import protocol
from ..config import Config
from .base import Device, PrinterTransport, ProtocolMismatch, TransportError

try:  # imported lazily in some environments
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except Exception as exc:  # pragma: no cover - only when optional dependency missing
    BleakClient = None  # type: ignore[assignment]
    BleakScanner = None  # type: ignore[assignment]

    class BleakError(Exception):  # type: ignore[no-redef]
        pass

    _BLE_IMPORT_ERROR = exc
else:
    _BLE_IMPORT_ERROR = None


DEFAULT_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"
DEFAULT_WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"
DEFAULT_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"

PRINTER_HINTS = (
    "print",
    "thermal",
    "pocket",
    "mini",
    "luck",
    "jingle",
    "sticker",
    "label",
    "huijutchen",
    "c&co",
    "coco",
    "dp-",
    "mx0",
    "mxw",
    "mx1",
    "l1s",
    "p50",
    "p58",
    "gp-",
    "cat printer",
)

# Names such as "L1-1234" or "L1_ABCD" are common for this printer family.  A
# plain substring search for "l1" would match too many unrelated devices, so
# require a token boundary.
_L1_NAME = re.compile(r"(^|[^a-z0-9])l1([^a-z0-9]|$)")


def normalize_uuid(value: str) -> str:
    value = value.strip().lower()
    if len(value) == 4:
        return f"0000{value}-0000-1000-8000-00805f9b34fb"
    return value


def _looks_like_printer(name: str, services: list[str]) -> bool:
    lowered = name.lower()
    if any(hint in lowered for hint in PRINTER_HINTS):
        return True
    if _L1_NAME.search(lowered):
        return True
    return any(
        "ff00" in normalize_uuid(s) or "ae30" in normalize_uuid(s) or "af30" in normalize_uuid(s)
        for s in services
    )


class LuckBleTransport(PrinterTransport):
    """BLE transport for LuckPrinter SDK / DP-L1S style printers.

    The same GATT layout (service ff00, write ff02, notify ff01) is used by
    many rebadged 57 mm pocket printers.  The command set is enabled with a
    "Luck" preamble; a plain ESC/POS escape hatch is available with
    ``protocol = "escpos"``.
    """

    name = "ble"

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client: Any | None = None
        self._device: Any | None = None
        self._write_uuid: str | None = None
        self._notify_uuid: str | None = None
        self._buffer = bytearray()
        self._event = asyncio.Event()
        self._services: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._last_error: str | None = None

    @property
    def connected(self) -> bool:
        return bool(self._client is not None and getattr(self._client, "is_connected", False))

    @property
    def label(self) -> str:
        if self._device is not None:
            return getattr(self._device, "name", None) or getattr(self._device, "address", "BLE printer")
        return self.config.device_name or self.config.device_address or "BLE printer"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ------------------------------------------------------------------
    # Discovery

    @staticmethod
    async def scan(timeout: float = 8.0) -> list[Device]:
        if BleakScanner is None:
            raise TransportError(
                "The 'bleak' package is not installed. Run `uv sync` in the project."
            )
        try:
            found = await asyncio.wait_for(
                BleakScanner.discover(timeout=timeout, return_adv=True),
                timeout=timeout + 4,
            )
        except TimeoutError as exc:
            raise TransportError(
                "Bluetooth scan timed out. Check that Bluetooth is enabled and that "
                "your terminal/OpenCode has Bluetooth permission in System Settings → "
                "Privacy & Security → Bluetooth."
            ) from exc
        except Exception as exc:
            raise TransportError(f"Bluetooth scan failed: {exc}") from exc

        devices: list[Device] = []
        values = found.values() if isinstance(found, dict) else found
        for item in values:
            if isinstance(item, tuple):
                device, adv = item
                name = adv.local_name or device.name or ""
                rssi = adv.rssi
                services = list(adv.service_uuids or [])
            else:
                device = item
                name = device.name or ""
                rssi = getattr(device, "rssi", None)
                services = []
            devices.append(
                Device(
                    address=device.address,
                    name=name or "(unnamed)",
                    rssi=rssi,
                    service_uuids=services,
                    likely_printer=_looks_like_printer(name, services),
                )
            )
        devices.sort(key=lambda d: (not d.likely_printer, -(d.rssi or -999), d.name.lower()))
        return devices

    @staticmethod
    async def _find_by_address(address: str, *, timeout: float = 5.0) -> Any:
        if BleakScanner is None:
            raise TransportError(
                "The 'bleak' package is not installed. Run `uv sync` in the project."
            )
        try:
            return await asyncio.wait_for(
                BleakScanner.find_device_by_address(address, timeout=timeout),
                timeout=timeout + 3,
            )
        except TimeoutError as exc:
            raise TransportError(
                f"Timed out looking for BLE device {address}. Wake the printer and retry."
            ) from exc

    async def _find_device(self) -> Any:
        if BleakScanner is None:
            raise TransportError(
                "The 'bleak' package is not installed. Run `uv sync` in the project."
            )
        address = self.config.device_address
        if address:
            device = await self._find_by_address(address, timeout=self.config.connect_timeout)
            if device is None:
                raise TransportError(f"No BLE device found at address {address}.")
            return device

        wanted = (self.config.device_name or "").strip().lower()
        devices = await self.scan(timeout=self.config.connect_timeout)
        if wanted:
            for item in devices:
                if item.name.lower() == wanted or item.address.lower() == wanted:
                    return await self._find_by_address(item.address, timeout=5)
            for item in devices:
                if wanted in item.name.lower():
                    return await self._find_by_address(item.address, timeout=5)
            raise TransportError(
                f"No BLE device matching {self.config.device_name!r}. "
                "Wake the printer (press its button), force-quit the phone app, "
                "and retry. Run `uv run thermal scan` to list nearby devices, then set "
                "THERMAL_DEVICE or run `uv run thermal config set device_name \"...\"`."
            )

        candidates = [d for d in devices if d.likely_printer]
        if len(candidates) == 1:
            return await self._find_by_address(candidates[0].address, timeout=5)
        if not candidates:
            raise TransportError(
                "No likely printer found. Turn the printer on, disconnect the phone app, "
                "run `uv run thermal scan`, and set the device name or address."
            )
        names = ", ".join(f"{d.name} ({d.address})" for d in candidates[:5])
        raise TransportError(
            f"Multiple possible printers found: {names}. Set THERMAL_DEVICE or THERMAL_ADDRESS."
        )

    # ------------------------------------------------------------------
    # Connection

    async def connect(self) -> None:
        if self.connected:
            return
        if _BLE_IMPORT_ERROR is not None:
            raise TransportError(f"Bluetooth support could not be loaded: {_BLE_IMPORT_ERROR}")
        async with self._lock:
            if self.connected:
                return
            try:
                device = await self._find_device()
                self._device = device
                self._client = BleakClient(
                    device,
                    disconnected_callback=self._on_disconnect,
                    timeout=self.config.connect_timeout,
                )
                await self._client.connect()
                await self._resolve_characteristics()
                if self._notify_uuid:
                    try:
                        await self._client.start_notify(self._notify_uuid, self._on_notify)
                    except Exception:
                        # Notifications are only needed for acks/status.  Some
                        # printers expose the characteristic but refuse to
                        # subscribe; printing can still work without it.
                        self._notify_uuid = None
            except ProtocolMismatch:
                self._last_error = "protocol mismatch"
                await self.disconnect()
                raise
            except Exception as exc:
                self._last_error = str(exc)
                await self.disconnect()
                raise TransportError(f"Could not connect to printer: {exc}") from exc

    async def _resolve_characteristics(self) -> None:
        assert self._client is not None
        services = []
        write_candidates: list[str] = []
        notify_candidates: list[str] = []

        for service in self._client.services:
            chars = []
            for char in service.characteristics:
                props = list(char.properties or [])
                chars.append({"uuid": char.uuid, "properties": props})
                normalized = normalize_uuid(char.uuid)
                if any(p in props for p in ("write", "write-without-response")):
                    write_candidates.append(normalized)
                if "notify" in props or "indicate" in props:
                    notify_candidates.append(normalized)
            services.append({"uuid": service.uuid, "characteristics": chars})
        self._services = services

        service_uuids = {normalize_uuid(service["uuid"]) for service in services}
        if "0000ff00-0000-1000-8000-00805f9b34fb" not in service_uuids and (
            "0000ae30-0000-1000-8000-00805f9b34fb" in service_uuids
            or any(uuid.startswith("0000ae0") for uuid in write_candidates)
        ):
            raise ProtocolMismatch(
                "This device looks like an MXW01 (AE30/AE01 GATT layout), not a "
                "LuckPrinter. Set protocol to 'mxw01' (or 'auto') and retry.",
                "mxw01",
            )

        service_uuid = normalize_uuid(self.config.service_uuid) if self.config.service_uuid else DEFAULT_SERVICE
        write_uuid = normalize_uuid(self.config.write_uuid) if self.config.write_uuid else None
        notify_uuid = normalize_uuid(self.config.notify_uuid) if self.config.notify_uuid else None

        if write_uuid is None:
            # Prefer the documented ff02 characteristic, then any writable one.
            write_uuid = DEFAULT_WRITE if DEFAULT_WRITE in write_candidates else (
                write_candidates[0] if write_candidates else None
            )
        if notify_uuid is None:
            notify_uuid = DEFAULT_NOTIFY if DEFAULT_NOTIFY in notify_candidates else (
                notify_candidates[0] if notify_candidates else None
            )

        # If a custom service was supplied, prefer chars inside it.
        if self.config.service_uuid:
            for service in services:
                if normalize_uuid(service["uuid"]) == service_uuid:
                    inside = [normalize_uuid(c["uuid"]) for c in service["characteristics"]]
                    writable = [u for u in inside if u in write_candidates]
                    notifiable = [u for u in inside if u in notify_candidates]
                    if writable:
                        write_uuid = writable[0]
                    if notifiable:
                        notify_uuid = notifiable[0]
                    break

        if write_uuid is None:
            raise TransportError(
                "No writable BLE characteristic found. The printer may use a different protocol. "
                "Run `uv run thermal doctor` for a full GATT dump."
            )
        self._write_uuid = write_uuid
        self._notify_uuid = notify_uuid

    def _on_disconnect(self, _client: Any) -> None:
        self._event.set()

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        self._buffer.extend(data)
        self._event.set()

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                if self._notify_uuid and getattr(client, "is_connected", False):
                    await client.stop_notify(self._notify_uuid)
            except Exception:
                pass
            try:
                if getattr(client, "is_connected", False):
                    await client.disconnect()
            except Exception:
                pass
        self._write_uuid = None
        self._notify_uuid = None

    # ------------------------------------------------------------------
    # I/O

    async def _write_chunk(self, data: bytes, *, uuid: str | None = None) -> None:
        assert self._client is not None
        target = uuid or self._write_uuid
        assert target is not None
        try:
            await self._client.write_gatt_char(target, data, response=False)
        except (BleakError, ValueError):
            await self._client.write_gatt_char(target, data, response=True)

    async def _write(self, data: bytes) -> None:
        if not self.connected:
            raise TransportError("Printer is not connected")
        assert self._client is not None
        # Prefer the characteristic's advertised write size (the same value
        # the verified reference implementation uses).  Fall back to the
        # negotiated MTU when the backend does not expose it.
        max_write = 0
        try:
            characteristic = self._client.services.get_characteristic(self._write_uuid)
            if characteristic is not None:
                max_write = int(
                    getattr(characteristic, "max_write_without_response_size", 0) or 0
                )
        except Exception:
            max_write = 0
        if max_write <= 0:
            mtu = getattr(self._client, "mtu_size", None) or 23
            max_write = max(20, mtu - 3)
        chunk_size = max(20, min(self.config.chunk_size, max_write))
        delay = max(0, self.config.chunk_delay_ms) / 1000.0
        for start in range(0, len(data), chunk_size):
            await self._write_chunk(data[start : start + chunk_size])
            if delay:
                await asyncio.sleep(delay)

    async def _query_after_write(self, command: bytes, *, timeout: float = 2.5, idle: float = 0.18) -> bytes:
        self._buffer.clear()
        self._event.clear()
        await self._write(command)
        start = time.monotonic()
        first = False
        while time.monotonic() - start < timeout:
            try:
                await asyncio.wait_for(self._event.wait(), timeout=0.1)
            except TimeoutError:
                if first:
                    await asyncio.sleep(idle)
                    break
                continue
            first = True
            self._event.clear()
        return bytes(self._buffer)

    async def _wait_for_ack(self, timeout: float = 12.0) -> bool:
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if b"\xaa" in self._buffer or b"OK" in self._buffer:
                return True
            try:
                await asyncio.wait_for(self._event.wait(), timeout=0.2)
                self._event.clear()
            except TimeoutError:
                continue
        return False

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
            density = self.config.density if density is None else max(0, min(2, density))
            protocol_name = (self.config.protocol or "luck").lower()
            if protocol_name == "escpos":
                payload = protocol.build_escpos_job(
                    image,
                    feed_dots=feed_dots,
                    density=density,
                    width_px=self.config.width_px,
                )
                await self._write(payload)
            else:
                payload = protocol.build_luck_job(
                    image,
                    feed_dots=feed_dots,
                    label_mode=label_mode,
                    density=density,
                    width_px=self.config.width_px,
                )
                head = protocol.CMD_ENABLE + protocol.CMD_WAKE
                if payload.startswith(head):
                    await self._write(protocol.CMD_ENABLE)
                    await asyncio.sleep(0.05)
                    await self._write(protocol.CMD_WAKE)
                    await asyncio.sleep(0.08)
                    await self._write(payload[len(head) :])
                else:
                    await self._write(payload)

            # Only wait for an ack when the printer exposed a notify channel;
            # otherwise we would stall every print for the full timeout.
            acked = await self._wait_for_ack(timeout=5.0) if self._notify_uuid else False
            return {
                "transport": self.name,
                "bytes": len(payload),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "acked": acked,
            }

    async def status(self) -> protocol.PrinterStatus | None:
        async with self._io_lock:
            payload = await self._query_after_write(protocol.CMD_STATUS, timeout=2.0, idle=0.1)
        return protocol.parse_status(payload)

    async def battery(self) -> int | None:
        async with self._io_lock:
            payload = await self._query_after_write(protocol.CMD_BATTERY, timeout=2.0, idle=0.1)
        return protocol.parse_battery(payload)

    async def info(self) -> dict[str, Any]:
        # Hold the I/O lock for the whole sequence so a print cannot start in
        # the middle of the info queries.
        async with self._io_lock:
            model = await self._query_after_write(protocol.CMD_MODEL, timeout=2.0)
            firmware = await self._query_after_write(protocol.CMD_FIRMWARE, timeout=2.0)
            serial = await self._query_after_write(protocol.CMD_SERIAL, timeout=2.0)
            all_info = await self._query_after_write(protocol.CMD_ALL_INFO, timeout=2.0)
        return {
            "model": protocol.decode_ascii(model),
            "firmware": protocol.decode_ascii(firmware),
            "serial": protocol.decode_ascii(serial),
            "all_info": protocol.decode_ascii(all_info),
            "model_raw": model.hex(" "),
            "firmware_raw": firmware.hex(" "),
        }

    async def diagnostics(self) -> dict[str, Any]:
        if not self.connected:
            await self.connect()
        result: dict[str, Any] = {
            "transport": self.name,
            "device": self.label,
            "services": self._services,
            "write_uuid": self._write_uuid,
            "notify_uuid": self._notify_uuid,
        }
        try:
            result["info"] = await self.info()
        except Exception as exc:
            result["info_error"] = str(exc)
        try:
            status = await self.status()
            result["status"] = status.as_dict() if status else None
        except Exception as exc:
            result["status_error"] = str(exc)
        try:
            result["battery"] = await self.battery()
        except Exception as exc:
            result["battery_error"] = str(exc)
        return result
