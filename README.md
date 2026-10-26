# Thermal / Agent Printer

A local bridge between a coding agent and a tiny Bluetooth thermal printer.

It gives you three things:

- **A web UI** for composing and previewing receipts, notes, stickers, QR codes, and images.
- **A CLI + HTTP API** for automation and other agents.
- **An OpenCode plugin** so the agent can print physical notes with a tool call.

The primary target is the **MXW01** mini thermal printer (sold as a “cat printer” and used with
the **Fun Print** app), which exposes a vendor BLE service and a small binary command protocol.
The same tooling also supports the common **DP-L1S / LuckPrinter** family as a fallback, plus a
plain ESC/POS serial path and a mock printer for development.

```
┌────────────────────┐       HTTP        ┌──────────────────────┐       BLE        ┌──────────────┐
│ OpenCode / CLI /   │ ────────────────► │ thermal serve        │ ───────────────► │ 57 mm printer│
│ browser / curl     │  127.0.0.1:8787   │ render + BLE driver  │  ae30/ae01/ae03  │ 384 px, 1-bit│
└────────────────────┘                   └──────────────────────┘                  └──────────────┘
```

## Quick start

Requirements: macOS/Linux, Bluetooth, [`uv`](https://docs.astral.sh/uv/), and Python 3.11–3.13.

```sh
uv sync
```

### 1. Connect the printer

Turn the printer on, wake it with its button, and force-quit **Fun Print** on your phone. These
printers accept one BLE connection at a time.

```sh
uv run thermal scan
uv run thermal connect --name MXW01 --protocol mxw01
```

`thermal scan` should list `* MXW01`. `thermal connect` saves the name and protocol to
`~/.config/thermal-printer/config.json` and prints the model, firmware, battery, and paper
status. Run it before starting the daemon, since it opens its own BLE connection; if the web app
is already running, use its **Connect** button instead. Once saved, every command below uses the
MXW01 automatically. See [Connecting the printer](#connecting-the-printer) for details and
fallback models.

### 2. Print from the CLI

```sh
uv run thermal print "Hello from my laptop" --title "HELLO"
```

More examples:

```sh
uv run thermal print --style task --title "TODAY" \
  "[x] connect printer
[ ] print a note"

uv run thermal print "Scan for logs" --qr "https://example.com/logs"
uv run thermal print "Hello" --dry-run     # preview only, no paper
```

If the local daemon is running, `thermal print` uses it; otherwise it connects directly. See
[Printing from the CLI](#printing-from-the-cli) for styles, images, receipts, and more.

### 3. Open the web app

```sh
uv run thermal serve --open
```

This starts the local daemon and opens <http://127.0.0.1:8787>. If it is already running, just
open that URL. In the Printer panel, click **Scan Bluetooth**, pick `* MXW01`, then compose and
click **Print**.

### If the printer disconnects

The printer drops the BLE connection when it sleeps or when the phone app takes it over.

1. Press the printer's power/feed button to wake it.
2. Force-quit **Fun Print**, and turn off phone Bluetooth if it keeps reconnecting.
3. Reconnect:
   - **Web app:** click **Connect** in the Printer panel, then pick `* MXW01`.
   - **CLI:** just print again — the daemon reconnects automatically before printing:
     ```sh
     uv run thermal print "Back online" --title "RECONNECTED"
     ```
   - **Stuck daemon:** restart it, then it auto-connects on startup:
     ```sh
     pkill -f "thermal serve" || true
     uv run thermal serve --open
     ```
4. Confirm with `uv run thermal status`; it should say `standby` and show the battery.

> If `thermal scan` does not list `MXW01`, the printer is asleep, still connected to the phone,
> or out of range. Wake it, force-quit the phone app, and scan again.

## Printing from the CLI

```sh
# A simple note
uv run thermal print "The build is green." --title "BUILD COMPLETE"

# Markdown-lite: headings, bullets, checkboxes, quotes, code, bold, italic
uv run thermal print --title "TODAY" --style task \
  "[x] review the diff
[ ] fix the flaky test
[ ] take a walk"

# A receipt with right-aligned amounts (two or more spaces separate columns)
uv run thermal print --style receipt --title "AGENT RECEIPT" \
  "feature work        1
tests               12
bugs introduced      0"

# A big sticker
uv run thermal print --style sticker "SHIP
IT"

# QR code + image
uv run thermal print "Scan for the deploy logs" --qr "https://example.com/logs"
uv run thermal print "Latest screenshot" --image ./screenshot.png

# See the exact bitmap without touching the printer
uv run thermal print "Hello" --dry-run
uv run thermal preview "Hello" --out preview.png
```

`thermal print` uses the local daemon if it is running; otherwise it connects directly. Use
`--no-daemon` to force a direct connection.

## Connecting the printer

1. Turn the printer on. It usually prints a small “ready” receipt.
2. **Disconnect the phone app** (“Fun Print”, “Luck Jingle”, etc.). These printers usually accept
   only one BLE connection at a time. On iOS/Android, also force-quit the app and turn off phone
   Bluetooth if the printer still does not appear.
3. Do **not** pair it in macOS System Settings. BLE devices do not need OS pairing; the app talks
   to the GATT service directly. A BLE-only printer often never appears in the macOS Bluetooth
   menu, which is normal.
4. Run `uv run thermal scan --all`. The real printer name is exactly **`MXW01`**; it is marked
   with `*`:

   ```text
   Scanning BLE for 8s...
   * MXW01   6D717764-D3E3-2239-C52D-42759E25147F   -55 dBm
       services: 0000af30-0000-1000-8000-00805f9b34fb
   ```

   The long identifier is a macOS CoreBluetooth UUID, not a MAC address; it is machine-specific
   and may change, so prefer the name. The advertised `af30` service is not the command service;
   the actual GATT service is `ae30` (see [`PROTOCOL.md`](./PROTOCOL.md)).
5. Connect and save it by name, forcing the MXW01 protocol if you want to skip auto-detection:

   ```sh
   uv run thermal connect --name MXW01 --protocol mxw01
   ```

   This writes `device_name: "MXW01"` and `protocol: "mxw01"` to the config. From then on,
   `thermal print`, `thermal status`, the web UI, and the agent plugin use it automatically. The
   relevant part of the saved config looks like:

   ```json
   {
     "transport": "ble",
     "protocol": "mxw01",
     "device_name": "MXW01",
     "width_px": 384
   }
   ```

   `transport: "auto"` and `protocol: "auto"` also work; with `device_name: "MXW01"` the driver
   selects the MXW01 implementation by name, and as a final fallback it identifies the protocol
   from the GATT table after connecting.
6. Run `uv run thermal doctor` to see the GATT services and query the model/battery/status. With
   `protocol` set to `auto` (the default), the driver identifies MXW01 vs LuckPrinter from the GATT
   layout automatically.

The DP-L1S fallback family is usually named `C&Co 3128_BLE`, `DP-L1S`, or another rebadged name.
It is dual-mode: use the BLE identity ending in `_BLE`.

On macOS, the first BLE scan triggers a Bluetooth permission prompt. Grant it to the terminal app
or OpenCode. If you deny it, reset it under **System Settings → Privacy & Security → Bluetooth** or
with `tccutil reset Bluetooth`.

### Verified MXW01 behavior

The implementation was verified against a real MXW01 with these results:

| Check | Result |
| --- | --- |
| Advertised name | `MXW01` |
| Firmware (`B1`) | `1.9.3.1.1` |
| Status (`A1`) | `standby`, battery 90%, 20–21 °C, `ok` |
| Print request (`A9`) | accepted |
| Print complete (`AA`) | received (`acked: true`) |
| Typical short note | 384 px wide, 128–218 px tall, ~8–10 s over BLE |

The printer name is the stable identifier. The CoreBluetooth UUID shown by `thermal scan` is
specific to the scanning Mac and should not be copied into documentation or committed config.

### About the USB-C port

On this class of printer the USB-C port is almost always **charging only**; the product listings
describe the hardware interface as Bluetooth. To check yours:

```sh
uv run thermal usb-probe
```

Then plug in the printer and run it again. If a new USB/serial device appears, there is a chance
the port does data. If nothing appears, USB-C is power-only, as expected. The tool includes an
ESC/POS-over-serial transport for the rare device that exposes a serial interface:

```sh
uv run thermal config set transport serial
uv run thermal config set serial_port /dev/cu.usbserial-0001
uv run thermal config set baudrate 9600
uv run thermal print "hello over serial"
```

## Agent integration (OpenCode)

The plugin lives at `.opencode/plugins/thermal/index.ts` and is loaded automatically when this
project is opened in OpenCode. A companion skill at
`.opencode/skills/thermal-printer/SKILL.md` gives the agent formatting and safety guidance. The
plugin registers two tools:

- **`print`** — render and print a note. Supports `title`, `style`, `footer`, `qr`, `image_path`,
  `copies`, `density`, `feed_dots`, `label_mode`, and `dry_run`.
- **`status`** — check connection, battery, paper/cover, and model.

Ask your agent things like:

> Print a receipt of what you changed in this session.

> Make a big sticker that says “SHIP IT”.

> Print this summary as a quote with a QR code to the PR.

The plugin starts the local service lazily with `uv run --project <project> thermal serve` if it
is not already running. Logs go to `.thermal/daemon.log`. To use a different service URL or
disable auto-start, pass plugin options in `opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [
    {
      "package": "./.opencode/plugins/thermal",
      "options": {
        "url": "http://127.0.0.1:8787",
        "autoStart": true
      }
    }
  ]
}
```

> Note: plugins in `.opencode/plugins/` are auto-discovered. Only add the `plugins` entry if you
> want to pass options; adding it does not double-load the plugin.

The same tool is available to any agent that can make HTTP requests:

```sh
curl -s http://127.0.0.1:8787/api/print \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "FROM THE AGENT",
    "message": "**Done.** The tests pass.\n\n- docs updated\n- shipped",
    "style": "note",
    "source": "my-agent"
  }'
```

## Web UI

The UI is intentionally local-only and served by the same process as the API:

- Live 384 px preview, including QR codes and images
- `note`, `receipt`, `quote`, `task`, and `sticker` layouts
- Markdown-lite text, density/feed controls, label mode
- Bluetooth scan + connect, mock printer, battery/status readout
- Recent print history with PNG previews

To connect the real MXW01 from the UI:

1. Open <http://127.0.0.1:8787>.
2. Wake the printer and force-quit **Fun Print**.
3. Click **Scan Bluetooth**.
4. Pick the row marked `* MXW01` (the address is a machine-specific CoreBluetooth UUID).
5. Wait for the status pill to say **online**; the panel should show model `MXW01`, firmware
   `1.9.3.1.1`, and the battery percentage.
6. Compose a short note and click **Print**. The message at the bottom reports the transport and
   elapsed time; the printer sends `AA` when the physical print completes.

If the scan lists `MXW01` but connecting fails, run
`uv run thermal connect --name MXW01 --protocol mxw01` once from the project directory. That saves
the correct name and protocol for the UI and the agent plugin.

The printer bitmap is a 1-bit image. Text is rendered with high-quality system fonts and
thresholded for crisp output; photos are Floyd–Steinberg dithered.

## Configuration

Config is stored at `~/.config/thermal-printer/config.json`. Inspect or change it with:

```sh
uv run thermal config show
uv run thermal config path
uv run thermal config set density 2
uv run thermal config set feed_dots 100
uv run thermal config set label_mode true
```

Useful environment overrides:

| Variable | Meaning |
| --- | --- |
| `THERMAL_DEVICE` | BLE device name (substring match) |
| `THERMAL_ADDRESS` | BLE address/UUID |
| `THERMAL_TRANSPORT` | `auto`, `ble`, `serial`, or `mock` |
| `THERMAL_PROTOCOL` | `auto` (default), `mxw01`, `luck`, or `escpos` |
| `THERMAL_WIDTH` | Print width in pixels (default 384) |
| `THERMAL_DENSITY` | 0–2 |
| `THERMAL_FEED` | Feed dots after a print |
| `THERMAL_API_PORT` | Web/API port (default 8787) |
| `THERMAL_MOCK=1` | Use the mock printer |

## How it works

There are two BLE protocol families behind the same renderer/UI:

**MXW01 / Fun Print** (primary):

| Service | Command write | Notify | Data write |
| --- | --- | --- | --- |
| `0000ae30-...` | `0000ae01-...` | `0000ae02-...` | `0000ae03-...` |

Commands are framed as `22 21 <cmd> 00 <len LE16> <payload> <crc8> FF`. The driver checks status
(`A1`), sets intensity (`A2`), declares the print height (`A9`), streams 1-bit rows to `AE03`
(LSB first, `1` = black), flushes (`AD`), and waits for the `AA` print-complete notification.
See [`PROTOCOL.md`](./PROTOCOL.md) for the full command set.

**DP-L1S / LuckPrinter** (fallback):

| Service | Write | Notify |
| --- | --- | --- |
| `0000ff00-...` | `0000ff02-...` | `0000ff01-...` |

The driver sends a small LuckPrinter preamble (`10 FF F1 03`, twelve null wake bytes), then a
standard ESC/POS `GS v 0` raster image (384 px = 48 bytes per row, MSB first), a feed command,
and a stop command.

With `protocol = "auto"`, the driver connects once, inspects the GATT layout, and switches to the
matching implementation. You can force either one with `thermal config set protocol mxw01` or
`thermal config set protocol luck`. Custom UUIDs can be set with `service_uuid`, `write_uuid`,
`notify_uuid`, and `data_uuid`. If it speaks plain ESC/POS over the same service, set `protocol`
to `escpos`. If it uses a completely different protocol, the rendering/UI layer is still
reusable; add a transport under `src/thermal/transports/`.

## Troubleshooting

**“No likely printer found”**
- Wake the printer (press its button; it may print a status slip).
- Close the phone app and turn off phone Bluetooth if it is holding the connection.
- Run `uv run thermal scan --all` and look for a new device.
- Set `THERMAL_DEVICE` / `THERMAL_ADDRESS` explicitly.

**Connected, but nothing prints**
- Check `thermal status`: paper empty, cover open, low battery, or overheating will block printing.
- Try `density 2`; thermal paper can vary.
- Start with a short message and `feed_dots 100`.
- Run `uv run thermal doctor --json` and check the raw model/firmware responses.
- If the printer uses a different protocol, the raster may be ignored. Capture the GATT dump and
  set `protocol` explicitly, or adapt the transport.

**MXW01-specific**
- The printer accepts one BLE connection. Force-quit **Fun Print** and turn off phone Bluetooth
  if a scan or connect fails.
- It does not need to be paired in macOS Bluetooth settings; it may never appear there.
- `thermal status` reports the A1 fields: state, battery %, temperature, and errors such as
  `no paper`, `overheated`, or `low battery`.
- The `AA` notification means the physical print finished. A missing `AA` with a correct-looking
  print usually means the completion notification was dropped; the job still printed.

**Print is garbled or cut off**
- The width should be 384 px for a 48 mm print head. If your model is 576 px, set
  `uv run thermal config set width_px 576`.
- Make sure you are using the correct paper side (the smooth, heat-sensitive side).

**macOS Bluetooth errors**
- Grant Bluetooth permission to the terminal/OpenCode.
- `pkill -f "thermal serve"` and retry if a stale process is holding the device.
- BLE devices can take a few seconds to reappear after a disconnect.

**Linux: `br-connection-profile-unavailable`**
- The DP-L1S is dual-mode. BlueZ may try the classic BR/EDR side first and fail. Set
  `ControllerMode = le` under `[General]` in `/etc/bluetooth/main.conf`, restart Bluetooth, or
  use a dedicated BLE dongle. See the source notes in [`PROTOCOL.md`](./PROTOCOL.md).

## Safety / privacy

The API binds to `127.0.0.1` by default. Do not expose it publicly without adding authentication.
Printed text is stored as PNG previews and job metadata under
`~/.local/share/thermal-printer/history/`. Do not ask the agent to print secrets, tokens, or
private user data.

## Development

```sh
uv run pytest
uv run ruff check .
uv run thermal serve --mock --open   # no hardware needed
```

Layout:

```
src/thermal/
  render.py          # markdown-lite -> 1-bit bitmap
  protocol.py        # MXW01 + LuckPrinter + ESC/POS commands
  printer.py         # manager, history, retries
  service.py         # FastAPI + static web UI
  cli.py             # thermal ...
  transports/        # MXW01/Luck BLE, serial, mock
  web/               # no-build HTML/CSS/JS UI
.opencode/plugins/thermal/index.ts
```

## Credits

The MXW01 protocol was implemented from public reverse-engineering work:
[PinThePenguinOne/MXW01_Thermal-Printer-Tool](https://github.com/PinThePenguinOne/MXW01_Thermal-Printer-Tool),
[MaikelChan/CatPrinterBLE](https://github.com/MaikelChan/CatPrinterBLE), and
[clementvp/mxw01-thermal-printer](https://github.com/clementvp/mxw01-thermal-printer). The
LuckPrinter / DP-L1S fallback is based on
[ChiaraCannolee/thermal-pocket-printer-basic](https://github.com/ChiaraCannolee/thermal-pocket-printer-basic)
and the earlier cat-printer work by rbaron. This project is an independent implementation focused
on agent workflows, a local web UI, and OpenCode integration.

## License

MIT. See [LICENSE](./LICENSE).
