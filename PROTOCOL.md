# Protocol notes: 57 mm BLE pocket printers

Two protocol families are documented here:

- **MXW01** (“Fun Print” / cat printer) — the primary target.
- **LuckPrinter SDK / DP-L1S** — the fallback used by many rebadged mini sticker printers.

> This is an independent implementation based on public reverse-engineering work. Verify against
> your own hardware with `uv run thermal doctor --json` before relying on any command.

## MXW01 (Fun Print)

### Device profile

| Property | Value |
| --- | --- |
| Print head | 384 pixels (48 mm printable width) |
| Resolution | 203 dpi (8 dots/mm) |
| Paper | ~57 mm wide thermal roll |
| Connection | BLE |
| Advertised name | `MXW01` |
| Firmware tested | `1.9.3.1.1` |
| Service | `0000ae30-0000-1000-8000-00805f9b34fb` |

### GATT layout

| Characteristic | Properties | Purpose |
| --- | --- | --- |
| `0000ae01-...` | write-without-response | Command/control frames |
| `0000ae02-...` | notify | Responses and print-complete events |
| `0000ae03-...` | write-without-response | Raw 1-bit image stream |
| `0000ae04-...` | notify | Secondary notifications (unused) |
| `0000ae05-...` | indicate | Secondary indications (unused) |
| `0000ae10-...` | read/write | Vendor config block (unused) |

The printer also exposes a secondary service `0000ae3a-...` with `ae3b` (write) and `ae3c`
(notify). The driver uses the `ae30` service. The BLE advertisement may report a different
vendor UUID (for example `af30`); connect and inspect the GATT table before assuming the
advertised service is the command service.

### Framing

```
22 21 <command> 00 <length LE16> <payload> <crc8> FF
```

- `length` counts the payload only.
- `crc8` is CRC-8 with polynomial `0x07`, init `0x00`, computed over the payload only.
- `FF` terminates the frame.
- Some reference implementations use `00 00` instead of `crc8 FF` for `A9`/`AD`. Both forms are
  accepted by the hardware; this driver uses the CRC form consistently.

### Commands

| Command | Data | Meaning |
| --- | --- | --- |
| `A1` | `00` | Get status; responds with an `A1` notification |
| `A2` | intensity `00`–`64` | Set print intensity (93 = `0x5D` is the common default) |
| `A3` | lines LE16 | Eject/feed paper |
| `A4` | lines LE16 | Retract paper |
| `A7` | `00` | Query print count |
| `A9` | height LE16, `30`, mode | Print request: rows, 48 bytes/row, mode (`00` mono) |
| `AA` | — | Print-complete notification |
| `AB` | `00` | Battery level; responds with one byte, 0–100 |
| `AC` | — | Cancel print |
| `AD` | `00` | End of image data / flush |
| `B0` | `00` | Get print type (`01` low pressure, `FF` unknown) |
| `B1` | `00` | Get firmware version (UTF-8 string) |

`A9`’s payload is four bytes: `height & 0xFF`, `height >> 8`, `0x30` (48 bytes per row), and the
mode. Monochrome mode is `0x00`; `0x01` is a similar mono mode with less post-print feed; `0x02`
is 4bpp grayscale.

### Status response (`A1`)

Offsets are within the notification payload (after the 6-byte header):

| Offset | Meaning |
| --- | --- |
| 0 | State: `0` standby, `1` printing, `2` feeding, `3` ejecting |
| 3 | Battery percentage (0–100) |
| 4 | Temperature in °C |
| 6 | Status: `0` = OK |
| 7 | Error code when status is non-zero: `01`/`09` no paper, `04` overheated, `08` low battery |

Example from an MXW01 on firmware `1.9.3.1.1`:

```
payload: 00 00 00 5a 14 00 00 00 f2 00
         state=standby, battery=90%, temp=20C, ok
```

### Print sequence

```
1. A1 00                                  status check
2. A2 5D                                  intensity (optional but recommended)
3. A9 <height LE16> 30 00                 print request; wait for A9 ack
4. <image rows>                           stream to AE03, 48 bytes per row
5. AD 00                                  end of data
6. wait for AA                            print complete
```

`A2` may be sent more than once; `A1` is optional on firmware that does not answer it before
printing. Some implementations also send `B1 00` as a handshake. The driver tolerates a missing
`A1`/`A9` ack but still waits for `AA` to report completion.

### Image format

- 384 pixels wide = 48 bytes per row.
- 1 bit per pixel, **LSB first**: pixel 0 is bit 0 of byte 0.
- `1` = black, `0` = white.
- Rows are contiguous; there is no per-row header or padding beyond the 48-byte row size.
- Data is written to `AE03` in small chunks. The reference implementations use 48-byte rows or
  20-byte chunks; this driver defaults to one 48-byte row with a 10 ms delay.

To feed paper, append zero rows to the image data and include them in the `A9` height. The `A3`
eject command is an alternative, but its feed distance is less predictable.

### Quirks

- The printer accepts one BLE central at a time. Force-quit the phone app before connecting.
- It usually does not appear in macOS Bluetooth settings; connect to the GATT service directly.
- A missing `AA` after a visually successful print usually means the notification was dropped,
  not that the print failed.
- Some libraries pad the image to at least 90 rows. The hardware used for this project printed
  shorter images without padding.

### Sources

- [PinThePenguinOne/MXW01_Thermal-Printer-Tool](https://github.com/PinThePenguinOne/MXW01_Thermal-Printer-Tool)
- [MaikelChan/CatPrinterBLE](https://github.com/MaikelChan/CatPrinterBLE)
- [clementvp/mxw01-thermal-printer](https://github.com/clementvp/mxw01-thermal-printer)

## LuckPrinter / DP-L1S (fallback)

These notes cover the common **LuckPrinter SDK / DP-L1S** family used by many rebadged mini
sticker printers. The HUIJUTCHEN “mini sticker printer” listings are one rebrand; the companion
apps are usually **Luck Jingle** (`com.dingdang.newprint`) or a white-label variant.

### Device profile

| Property | Value |
| --- | --- |
| Print head | 384 pixels (48 mm printable width) |
| Resolution | 203 dpi (8 dots/mm) |
| Paper | ~57 mm wide thermal roll |
| Connection | BLE 4.0 |
| GATT service | `0000ff00-0000-1000-8000-00805f9b34fb` |
| Write characteristic | `0000ff02-0000-1000-8000-00805f9b34fb` |
| Notify characteristic | `0000ff01-0000-1000-8000-00805f9b34fb` |

The printer often does **not** advertise its service UUID, so scanning by service alone will miss
it. Scan by name and connect to the device, then discover services. The common DP-L1S rebrand
advertises as `C&Co 3128_BLE` (the LE/GATT identity) and may also expose a classic BR/EDR identity
without the `_BLE` suffix. Use the `_BLE` device.

### Framing

Control commands use:

```
10 FF [command] [params...]
```

`0x10` is ASCII DLE; `0xFF` is the prefix used by the vendor SDK. Image data uses standard
ESC/POS `GS v 0` raster commands.

### Command reference

#### Info and status

| Command | Response | Meaning |
| --- | --- | --- |
| `10 FF 20 F0` | ASCII | Model name |
| `10 FF 20 F1` | ASCII | Firmware version |
| `10 FF 20 F2` | ASCII | Serial number |
| `10 FF 20 EF` | ASCII | Boot/device info |
| `10 FF 70` | pipe-delimited ASCII | All printer info, including battery |
| `10 FF 40` | 1 byte | Status bitfield |
| `10 FF 50 F1` | 2 bytes | Battery; second byte is percentage |
| `10 FF 11` | 1 byte | Current density |
| `10 FF 20 A0` | 1 byte | Print speed |

Status bitfield:

| Bit | Meaning |
| --- | --- |
| 0 | Printing |
| 1 | Cover open |
| 2 | Paper empty |
| 3 | Low battery |
| 4 or 6 | Overheating |
| 5 | Charging |

#### Print control

| Command | Meaning |
| --- | --- |
| `10 FF F1 03` | Enable printer (Lujiang mode 3) |
| `00` × 12 | Wake-up sequence |
| `1B 4A n` | Feed n dots (0–255) |
| `10 FF F1 45` | Stop print job; expect `AA` or `OK` |
| `10 FF 04` | Reset/recovery |
| `10 FF 10 00 n` | Set density (0–2) |
| `10 FF 15 lo hi` | Set print width (16-bit little-endian) |
| `1F 70 01 n` | Set heating level |

#### Labels / die-cut stickers

| Command | Meaning |
| --- | --- |
| `1B BB CC` | Mark first label |
| `1B BB AA` | Mark continuation |
| `1B BB BB` | Mark last label |
| `1D 0C` | Position to next label |
| `1F 11 51` | Adjust label start |
| `1F 11 50` | Adjust label end |

The tool’s **label mode** emits `1F 11 51` before the image and `1D 0C` + `1F 11 50` after it.
Use it only with die-cut label stock; continuous paper should use normal mode.

### Raster image format

```
1D 76 30 m wL wH hL hH <pixel data>
```

| Field | Value |
| --- | --- |
| `m` | `0` normal; `1` double width; `2` double height; `3` both |
| `wL wH` | Width in bytes, 16-bit little-endian |
| `hL hH` | Height in pixels, 16-bit little-endian |
| Pixel data | 1 bit/pixel, 8 pixels/byte, MSB first |

For a 384 px head, `wL=0x30`, `wH=0x00`, and each row is 48 bytes. A set bit prints black.

### Normal print sequence

```
1. 10 FF F1 03             enable
2. 00 00 00 00 00 00 00 00 00 00 00 00   wake
3. 10 FF 10 00 01          optional density
4. 1D 76 30 00 30 00 hL hH <data>        raster image
5. 1B 4A 50                feed 80 dots
6. 10 FF F1 45             stop; wait for AA/OK
```

Our implementation sends the enable/wake bytes separately with short delays, then the raster,
feed, and stop bytes in paced BLE writes. Chunk size is capped at 512 bytes and automatically
clamped to the write characteristic's advertised maximum (falling back to the negotiated MTU,
or 20 bytes when neither is available), with a 10 ms delay between chunks. Increase
`chunk_size` only if your adapter and printer are stable with larger writes.

### Label print sequence

```
1. 10 FF F1 03
2. 00 × 12
3. 1F 11 51
4. 1D 76 30 ...
5. 1D 0C
6. 1F 11 50
7. 10 FF F1 45
```

### Plain ESC/POS fallback

Some devices in the same GATT layout accept ESC/POS directly and do not need the Luck preamble or
stop command. Set `protocol` to `escpos`:

```sh
uv run thermal config set protocol escpos
```

This sends `GS v 0` + feed only. It is also the format used by the serial transport.

### USB-C

The USB-C port is normally charging-only. A data-capable port would show up as a USB device or a
`/dev/cu.*` serial port; `thermal usb-probe` checks both. If you find a serial port, the
`serial` transport can send the same ESC/POS raster sequence.

### Scanning tips

- BLE printers often stop advertising after a connection; disconnect the phone app first.
- macOS exposes BLE addresses as CoreBluetooth UUIDs, not MAC addresses. That is normal.
- The printer may reject a second central connection while the phone is connected.
- If no service is found, the device may be a different protocol family. Use `thermal doctor
  --json` to capture its GATT layout and raw command responses.

### LuckPrinter sources

- [ChiaraCannolee/thermal-pocket-printer-basic](https://github.com/ChiaraCannolee/thermal-pocket-printer-basic)
  — LuckPrinter / DP-L1S protocol notes and hardware verification.
- [rbaron/catprinter](https://github.com/rbaron/catprinter) and
  [Reverse-engineering the cat printer Bluetooth protocol](https://rbaron.net/blog/2021/08/15/Reverse-engineering-the-cat-printer-bluetooth-protocol)
  — background on the broader family of BLE thermal printers.
- [0xMH/fichero-printer](https://github.com/0xMH/fichero-printer) — related LuckPrinter-family device.
