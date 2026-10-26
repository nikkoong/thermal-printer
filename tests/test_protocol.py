from __future__ import annotations

from PIL import Image

from thermal import protocol


def test_raster_header_and_bitmap() -> None:
    image = Image.new("1", (16, 2), 1)
    image.putpixel((0, 0), 0)
    image.putpixel((15, 0), 0)
    data = protocol.image_to_raster(image, width_px=16)
    assert data[:4] == b"\x1d\x76\x30\x00"
    assert data[4:6] == (2).to_bytes(2, "little")
    assert data[6:8] == (2).to_bytes(2, "little")
    # Row 0: leftmost and rightmost pixels black -> 0b10000000, 0b00000001
    # (bit 7 is the leftmost pixel; ESC/POS uses 1 for black).
    assert data[8:10] == bytes([0x80, 0x01])


def test_image_padding_to_width() -> None:
    image = Image.new("1", (8, 1), 1)
    data = protocol.image_to_raster(image, width_px=16)
    assert data[4:6] == (2).to_bytes(2, "little")
    assert len(data[8:]) == 2
    assert data[8:] == b"\x00\x00"


def test_build_luck_job_sequence() -> None:
    image = Image.new("1", (384, 4), 1)
    job = protocol.build_luck_job(image, feed_dots=50, label_mode=False, density=2)
    assert job.startswith(protocol.CMD_ENABLE + protocol.CMD_WAKE)
    assert protocol.set_density(2) in job
    assert b"\x1d\x76\x30\x00" in job
    assert protocol.feed(50) in job
    assert job.endswith(protocol.CMD_STOP)


def test_label_job_sequence() -> None:
    image = Image.new("1", (384, 4), 1)
    job = protocol.build_luck_job(image, label_mode=True)
    assert bytes([0x1F, 0x11, 0x51]) in job
    assert bytes([0x1D, 0x0C]) in job
    assert bytes([0x1F, 0x11, 0x50]) in job


def test_status_flags() -> None:
    status = protocol.parse_status(bytes([0b00100101]))
    assert status is not None
    assert status.printing
    assert status.paper_empty
    assert status.charging
    assert not status.cover_open
    assert not status.ready


def test_battery_parse() -> None:
    assert protocol.parse_battery(bytes([0x00, 0x64])) == 100
    assert protocol.parse_battery(bytes([0x50])) == 80
    assert protocol.parse_battery(b"") is None


# ---------------------------------------------------------------------------
# MXW01


def test_mxw01_crc8_known_vector() -> None:
    # CRC-8 (poly 0x07, init 0x00) check value for "123456789".
    assert protocol.mxw01_crc8(b"123456789") == 0xF4
    assert protocol.mxw01_crc8(b"\x00") == 0x00


def test_mxw01_frame_layout() -> None:
    assert protocol.mxw01_frame(0xB1, b"\x00") == bytes.fromhex("2221b10001000000ff")
    assert protocol.mxw01_frame(0xA9, b"\x10\x00\x30\x00") == bytes.fromhex(
        "2221a900040010003000"
    ) + bytes([protocol.mxw01_crc8(b"\x10\x00\x30\x00"), 0xFF])
    assert protocol.mxw01_frame(0xA9, b"\x01\x00", with_crc=False) == (
        bytes.fromhex("2221a90002000100") + b"\x00\xff"
    )


def test_mxw01_image_bit_order() -> None:
    # LSB-first, 1 = black, so the leftmost pixel is bit 0.
    image = Image.new("1", (8, 1), 1)
    image.putpixel((0, 0), 0)
    assert protocol.mxw01_image_data(image, width_px=8) == b"\x01"

    image = Image.new("1", (8, 1), 1)
    image.putpixel((7, 0), 0)
    assert protocol.mxw01_image_data(image, width_px=8) == b"\x80"

    black = Image.new("1", (8, 1), 0)
    assert protocol.mxw01_image_data(black, width_px=8) == b"\xff"
    white = Image.new("1", (8, 1), 1)
    assert protocol.mxw01_image_data(white, width_px=8) == b"\x00"


def test_mxw01_raster_pads_width_and_feed() -> None:
    image = Image.new("1", (8, 2), 1)
    data, rows, width_bytes = protocol.mxw01_raster(image, feed_dots=3)
    assert width_bytes == 48
    assert rows == 5
    assert len(data) == 48 * 5


def test_mxw01_status_parse() -> None:
    status = protocol.parse_mxw01_status(bytes([0, 0, 0, 87, 25, 0, 0, 0]))
    assert status is not None
    assert status.state_name == "standby"
    assert status.battery == 87
    assert status.temperature == 25
    assert status.ok
    assert status.ready
    assert status.describe() == "standby"

    error = protocol.parse_mxw01_status(bytes([0, 0, 0, 5, 20, 0, 1, 1]))
    assert error is not None
    assert not error.ok
    assert error.error == "no paper"
    assert not error.ready
    assert error.describe() == "no paper"


def test_mxw01_battery_and_version() -> None:
    assert protocol.parse_mxw01_battery(b"\x64") == 100
    assert protocol.parse_mxw01_battery(b"\xff") is None
    assert protocol.parse_mxw01_battery(b"") is None
    assert protocol.decode_mxw01_version(b"1.0.3\x00garbage") == "1.0.3"
