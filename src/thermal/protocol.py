from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

DLE = 0x10
FF = 0xFF
GS = 0x1D
ESC = 0x1B

DEFAULT_WIDTH_PX = 384

# ---------------------------------------------------------------------------
# MXW01 ("Fun Print" / cat printer)
#
# Public reverse-engineering sources agree on this framing:
#   22 21 <cmd> 00 <len LE16> <payload> <crc8> ff
# The CRC is CRC-8 with polynomial 0x07 and init 0x00 over the payload only.

MXW01_HEADER = b"\x22\x21"
MXW01_TERMINATOR = 0xFF
MXW01_WIDTH_PX = 384
MXW01_WIDTH_BYTES = MXW01_WIDTH_PX // 8

MXW01_CMD_STATUS = 0xA1
MXW01_CMD_INTENSITY = 0xA2
MXW01_CMD_EJECT = 0xA3
MXW01_CMD_RETRACT = 0xA4
MXW01_CMD_QUERY_COUNT = 0xA7
MXW01_CMD_PRINT = 0xA9
MXW01_CMD_PRINT_COMPLETE = 0xAA
MXW01_CMD_BATTERY = 0xAB
MXW01_CMD_CANCEL = 0xAC
MXW01_CMD_FLUSH = 0xAD
MXW01_CMD_PRINT_TYPE = 0xB0
MXW01_CMD_VERSION = 0xB1

_MXW01_STATES = {0: "standby", 1: "printing", 2: "feeding paper", 3: "ejecting paper"}
_MXW01_ERRORS = {
    0x01: "no paper",
    0x09: "no paper",
    0x04: "overheated",
    0x08: "low battery",
}

# LuckPrinter / DP-L1S control commands.
CMD_ENABLE = bytes([DLE, FF, 0xF1, 0x03])
CMD_WAKE = bytes(12)
CMD_STOP = bytes([DLE, FF, 0xF1, 0x45])
CMD_RESET = bytes([DLE, FF, 0x04])
CMD_STATUS = bytes([DLE, FF, 0x40])
CMD_BATTERY = bytes([DLE, FF, 0x50, 0xF1])
CMD_MODEL = bytes([DLE, FF, 0x20, 0xF0])
CMD_SERIAL = bytes([DLE, FF, 0x20, 0xF2])
CMD_FIRMWARE = bytes([DLE, FF, 0x20, 0xF1])
CMD_BOOT_INFO = bytes([DLE, FF, 0x20, 0xEF])
CMD_ALL_INFO = bytes([DLE, FF, 0x70])
CMD_DENSITY = bytes([DLE, FF, 0x11])
CMD_SPEED = bytes([DLE, FF, 0x20, 0xA0])


@dataclass(frozen=True)
class PrinterStatus:
    raw: int

    @property
    def printing(self) -> bool:
        return bool(self.raw & (1 << 0))

    @property
    def cover_open(self) -> bool:
        return bool(self.raw & (1 << 1))

    @property
    def paper_empty(self) -> bool:
        return bool(self.raw & (1 << 2))

    @property
    def low_battery(self) -> bool:
        return bool(self.raw & (1 << 3))

    @property
    def overheating(self) -> bool:
        return bool(self.raw & (1 << 4)) or bool(self.raw & (1 << 6))

    @property
    def charging(self) -> bool:
        return bool(self.raw & (1 << 5))

    @property
    def ready(self) -> bool:
        return not (self.cover_open or self.paper_empty or self.overheating)

    def as_dict(self) -> dict[str, object]:
        return {
            "raw": self.raw,
            "hex": f"0x{self.raw:02x}",
            "printing": self.printing,
            "cover_open": self.cover_open,
            "paper_empty": self.paper_empty,
            "low_battery": self.low_battery,
            "overheating": self.overheating,
            "charging": self.charging,
            "ready": self.ready,
        }

    def describe(self) -> str:
        states: list[str] = []
        if self.printing:
            states.append("printing")
        if self.cover_open:
            states.append("cover open")
        if self.paper_empty:
            states.append("paper empty")
        if self.low_battery:
            states.append("low battery")
        if self.overheating:
            states.append("overheating")
        if self.charging:
            states.append("charging")
        return ", ".join(states) if states else "ready"


@dataclass(frozen=True)
class Mxw01Status:
    """Decoded A1 status notification from an MXW01."""

    state: int = 0
    battery: int | None = None
    temperature: int | None = None
    ok: bool = True
    error_code: int | None = None
    raw: bytes = b""

    @property
    def state_name(self) -> str:
        return _MXW01_STATES.get(self.state, f"state {self.state}")

    @property
    def error(self) -> str | None:
        if self.ok:
            return None
        if self.error_code is None:
            return "unknown error"
        return _MXW01_ERRORS.get(self.error_code, f"error 0x{self.error_code:02x}")

    @property
    def ready(self) -> bool:
        return self.ok and self.state == 0

    def describe(self) -> str:
        if not self.ok:
            return self.error or "error"
        return self.state_name

    def as_dict(self) -> dict[str, object]:
        return {
            "raw": self.raw.hex(" "),
            "state": self.state,
            "state_name": self.state_name,
            "battery": self.battery,
            "temperature": self.temperature,
            "ok": self.ok,
            "error": self.error,
            "error_code": self.error_code,
            "ready": self.ready,
        }


def mxw01_crc8(data: bytes) -> int:
    """CRC-8 (polynomial 0x07, init 0x00), as used by the MXW01 framing."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def mxw01_frame(command: int, data: bytes = b"", *, with_crc: bool = True) -> bytes:
    """Build one MXW01 command frame."""
    if not 0 <= int(command) <= 0xFF:
        raise ValueError("command out of range")
    payload = bytes(data)
    if len(payload) > 0xFFFF:
        raise ValueError("payload too large")
    header = (
        MXW01_HEADER
        + bytes([int(command), 0x00])
        + len(payload).to_bytes(2, "little")
    )
    checksum = mxw01_crc8(payload) if with_crc else 0
    return header + payload + bytes([checksum, MXW01_TERMINATOR])


def mxw01_intensity(density: int | None) -> int:
    """Map the UI's 0..2 density to the printer's 0..100 intensity."""
    level = 1 if density is None else max(0, min(2, int(density)))
    return {0: 0x3C, 1: 0x5D, 2: 0x64}[level]


_REVERSE_BITS = tuple(int(f"{value:08b}"[::-1], 2) for value in range(256))


def mxw01_image_data(image: Image.Image, width_px: int = MXW01_WIDTH_PX) -> bytes:
    """Pack an image the way the MXW01 expects it.

    One bit per pixel, LSB first, ``1`` = black, each row padded to
    ``width_px / 8`` bytes.  Pillow's 1-bit mode uses 0 for black, so the
    bytes are inverted before the bit order is reversed.
    """
    width_px = max(8, min(MXW01_WIDTH_PX, int(width_px) // 8 * 8))
    if image.mode != "1":
        image = image.convert("1", dither=Image.Dither.NONE)
    if image.width > width_px:
        height = max(1, round(image.height * width_px / image.width))
        image = (
            image.convert("L")
            .resize((width_px, height), Image.Resampling.LANCZOS)
            .convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        )
    elif image.width < width_px:
        canvas = Image.new("1", (width_px, image.height), 1)
        canvas.paste(image, (0, 0))
        image = canvas

    raw = image.tobytes()
    return bytes(_REVERSE_BITS[255 - value] for value in raw)


def mxw01_raster(
    image: Image.Image,
    *,
    feed_dots: int = 0,
    width_px: int = MXW01_WIDTH_PX,
) -> tuple[bytes, int, int]:
    """Return ``(pixel_data, total_rows, width_bytes)`` including paper feed."""
    width_px = max(8, min(MXW01_WIDTH_PX, int(width_px) // 8 * 8))
    data = mxw01_image_data(image, width_px=width_px)
    width_bytes = width_px // 8
    rows = len(data) // width_bytes
    feed = max(0, min(255, int(feed_dots)))
    if feed:
        data += bytes(width_bytes * feed)
    return data, rows + feed, width_bytes


def parse_mxw01_status(payload: bytes) -> Mxw01Status | None:
    """Decode the payload of an A1 status notification.

    Layout (offsets within the payload, verified against CatPrinterBLE):
    0 state, 3 battery %, 4 temperature, 6 status (0 = ok), 7 error code.
    """
    if not payload:
        return None
    ok = payload[6] == 0 if len(payload) > 6 else True
    battery = payload[3] if len(payload) > 3 and payload[3] <= 100 else None
    temperature = payload[4] if len(payload) > 4 else None
    error_code = payload[7] if len(payload) > 7 and not ok else None
    return Mxw01Status(
        state=payload[0],
        battery=battery,
        temperature=temperature,
        ok=ok,
        error_code=error_code,
        raw=bytes(payload),
    )


def parse_mxw01_battery(payload: bytes) -> int | None:
    if payload and payload[0] <= 100:
        return payload[0]
    return None


def decode_mxw01_version(payload: bytes) -> str:
    if not payload:
        return ""
    text = payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    return "".join(ch for ch in text if ch.isprintable()).strip()


def feed(dots: int) -> bytes:
    """Feed continuous paper by ``dots`` (0-255)."""
    dots = max(0, min(255, int(dots)))
    return bytes([ESC, 0x4A, dots])


def set_density(level: int) -> bytes:
    level = max(0, min(2, int(level)))
    return bytes([DLE, FF, 0x10, 0x00, level])


def raster_header(width_bytes: int, height: int) -> bytes:
    if width_bytes < 1 or width_bytes > 0xFFFF:
        raise ValueError("width_bytes out of range")
    if height < 0 or height > 0xFFFF:
        raise ValueError("height out of range")
    return (
        bytes([GS, 0x76, 0x30, 0x00])
        + width_bytes.to_bytes(2, "little")
        + height.to_bytes(2, "little")
    )


def image_to_raster(image: Image.Image, width_px: int = DEFAULT_WIDTH_PX) -> bytes:
    """Return a GS v 0 raster command for a one-bit image.

    Pillow's 1-bit mode uses 0 = black, while ESC/POS uses 1 = black, so the
    bitmap is inverted before packing.  Pillow's ``tobytes()`` is MSB-first
    and row-padded, exactly what ESC/POS expects.
    """
    if image.mode != "1":
        image = image.convert("1")
    if image.width != width_px:
        canvas = Image.new("1", (width_px, image.height), 1)
        canvas.paste(image, (0, 0))
        image = canvas
    packed = image.convert("L").point(lambda p: 255 - p).convert("1", dither=Image.Dither.NONE)
    width_bytes = (image.width + 7) // 8
    return raster_header(width_bytes, image.height) + packed.tobytes()


def build_luck_job(
    image: Image.Image,
    *,
    feed_dots: int = 80,
    label_mode: bool = False,
    density: int | None = None,
    width_px: int = DEFAULT_WIDTH_PX,
) -> bytes:
    """Build the command sequence for a LuckPrinter/DP-L1S style device."""
    parts: list[bytes] = [CMD_ENABLE, CMD_WAKE]
    if density is not None:
        parts.append(set_density(density))
    if label_mode:
        parts.append(bytes([0x1F, 0x11, 0x51]))  # label position: start
    parts.append(image_to_raster(image, width_px=width_px))
    if label_mode:
        parts.append(bytes([0x1D, 0x0C]))  # position to next label
        parts.append(bytes([0x1F, 0x11, 0x50]))  # label position: end
    else:
        parts.append(feed(feed_dots))
    parts.append(CMD_STOP)
    return b"".join(parts)


def build_escpos_job(
    image: Image.Image,
    *,
    feed_dots: int = 80,
    density: int | None = None,
    width_px: int = DEFAULT_WIDTH_PX,
) -> bytes:
    """Plain ESC/POS raster sequence, for serial/USB or generic BLE bridges."""
    parts: list[bytes] = []
    if density is not None:
        # Most generic ESC/POS printers accept this as "print density".
        parts.append(bytes([ESC, 0x7C, max(0, min(2, density))]))
    parts.append(image_to_raster(image, width_px=width_px))
    parts.append(feed(feed_dots))
    return b"".join(parts)


def parse_status(payload: bytes) -> PrinterStatus | None:
    if not payload:
        return None
    return PrinterStatus(payload[0])


def parse_battery(payload: bytes) -> int | None:
    """Battery command returns two bytes; byte[1] is the percentage."""
    if len(payload) >= 2 and 0 <= payload[1] <= 100:
        return payload[1]
    if len(payload) == 1 and 0 <= payload[0] <= 100:
        return payload[0]
    return None


def decode_ascii(payload: bytes) -> str:
    cleaned = bytes(b for b in payload if 32 <= b < 127 or b in (9, 10, 13))
    return cleaned.decode("ascii", errors="replace").strip()
