from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from . import __version__
from .config import CONFIG_PATH, Config
from .printer import PrinterManager
from .render import STYLES, PrintRequest, render
from .transports import TransportError, list_serial_ports

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _daemon_url(config: Config) -> str:
    return config.api_url.rstrip("/")


def _daemon_request(
    config: Config,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    url = f"{_daemon_url(config)}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        # HTTPError subclasses URLError, so it must be handled first.
        try:
            body = exc.read().decode()
            parsed = json.loads(body)
            detail = parsed.get("detail", body)
            if isinstance(detail, list):
                detail = "; ".join(
                    str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                    for item in detail
                )
            return {"error": detail, "status": exc.code}
        except Exception:
            return {"error": str(exc), "status": exc.code}
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
        return None


def _make_request(args: argparse.Namespace, config: Config) -> PrintRequest:
    message = " ".join(args.text) if getattr(args, "text", None) else ""
    if not message and not sys.stdin.isatty() and not getattr(args, "image", None):
        message = sys.stdin.read()
    image = None
    if getattr(args, "image", None):
        if Image is None:
            raise SystemExit("Pillow is not available; run `uv sync`.")
        image = Image.open(args.image)
    return PrintRequest(
        message=message,
        title=args.title,
        style=args.style,
        footer=args.footer,
        qr=args.qr,
        image=image,
        invert_image=getattr(args, "invert_image", False),
        width_px=config.width_px,
        density=config.density,
        feed_dots=config.feed_dots,
        label_mode=config.label_mode,
        source=getattr(args, "source", "cli"),
    )


def _print_result(
    result: dict[str, Any],
    config: Config,
    *,
    json_output: bool = False,
    show_daemon_url: bool = True,
) -> None:
    if json_output:
        _print_json(result)
        return
    job = result.get("job", {})
    print(f"Printed {job.get('width', '?')}x{job.get('height', '?')} px")
    if result.get("dry_run"):
        print("Mode: dry run (nothing sent to the printer)")
    else:
        print(f"Transport: {job.get('transport', '?')}, {job.get('duration_ms', 0)} ms")
    if job.get("preview_url"):
        if show_daemon_url:
            print(f"Preview: {_daemon_url(config)}{job['preview_url']}")
        elif result.get("preview_path"):
            print(f"Preview: {result['preview_path']}")


def _direct_manager(config: Config) -> PrinterManager:
    return PrinterManager(config)


# ---------------------------------------------------------------------------
# Commands


async def _cmd_scan(args: argparse.Namespace, config: Config) -> int:
    print(f"Scanning BLE for {args.timeout:.0f}s...", file=sys.stderr)
    try:
        devices = await PrinterManager(config).scan(timeout=args.timeout)
    except TransportError as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1
    if not devices:
        print("No BLE devices found. Make sure Bluetooth is on and the printer is awake.", file=sys.stderr)
        return 1
    for device in devices:
        if not args.all and not device.likely_printer:
            continue
        marker = "*" if device.likely_printer else " "
        rssi = f"{device.rssi:4d} dBm" if device.rssi is not None else "   ? dBm"
        print(f"{marker} {device.name:<32} {device.address:<40} {rssi}")
        if device.service_uuids:
            print(f"    services: {', '.join(device.service_uuids)}")
    if not any(d.likely_printer for d in devices):
        print(
            "\nNo obvious printer names found. Re-run with --all to inspect every BLE device.\n"
            "The MXW01 advertises as \"MXW01\"; the DP-L1S family usually appears as\n"
            "something like C&Co 3128_BLE."
        )
    return 0


async def _cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    if args.mock:
        config.transport = "mock"
    if args.name:
        config.device_name = args.name
    if args.address:
        config.device_address = args.address
    if args.transport:
        config.transport = args.transport
    if args.protocol:
        config.protocol = args.protocol
    manager = _direct_manager(config)
    print(f"Connecting via {config.transport}...", file=sys.stderr)
    try:
        await manager.connect(persist=False)
        data = await manager.diagnostics()
    except Exception as exc:
        print(f"Doctor failed: {exc}", file=sys.stderr)
        print(
            "\nTroubleshooting:\n"
            "  - turn the printer on and load paper\n"
            "  - disconnect the phone app (BLE printers accept one connection)\n"
            "  - run `uv run thermal scan --all` and set THERMAL_DEVICE or THERMAL_ADDRESS\n"
            "  - grant Bluetooth permission to your terminal/OpenCode when macOS asks",
            file=sys.stderr,
        )
        await manager.close()
        return 1
    if args.json:
        _print_json(data)
    else:
        print(f"Device: {data.get('device', '?')}")
        print(f"Write:  {data.get('write_uuid', '?')}")
        print(f"Notify: {data.get('notify_uuid', '?')}")
        services = data.get("services") or []
        print("\nGATT services:")
        for service in services:
            print(f"  {service.get('uuid')}")
            for char in service.get("characteristics", []):
                print(f"    {char.get('uuid')}  {', '.join(char.get('properties', []))}")
        info = data.get("info") or {}
        print("\nDevice info:")
        for key in ("model", "firmware", "serial", "all_info"):
            if info.get(key):
                print(f"  {key}: {info[key]}")
        if data.get("status") is not None:
            print(f"Status: {json.dumps(data.get('status'), default=str)}")
        if data.get("battery") is not None:
            print(f"Battery: {data.get('battery')}%")
        for key in ("info_error", "status_error", "battery_error"):
            if data.get(key):
                print(f"{key}: {data[key]}")
    await manager.close()
    return 0


async def _print_direct(args: argparse.Namespace, config: Config) -> int:
    request = _make_request(args, config)
    manager = _direct_manager(config)
    try:
        result = await manager.print_request(
            request, dry_run=args.dry_run, copies=args.copies
        )
    except Exception as exc:
        print(f"Print failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await manager.close()
    _print_result(result, config, json_output=args.json, show_daemon_url=False)
    return 0


def _cmd_print(args: argparse.Namespace, config: Config) -> int:
    request_payload = {
        "message": " ".join(args.text) if args.text else "",
        "title": args.title,
        "style": args.style,
        "footer": args.footer,
        "qr": args.qr,
        "copies": args.copies,
        "dry_run": args.dry_run,
        "invert_image": args.invert_image,
        "source": "cli",
    }
    if not request_payload["message"] and not sys.stdin.isatty() and not args.image:
        request_payload["message"] = sys.stdin.read()
    if args.image:
        import base64

        request_payload["image_base64"] = base64.b64encode(Path(args.image).read_bytes()).decode()

    if not args.no_daemon:
        health = _daemon_request(config, "GET", "/api/health", timeout=0.6)
        if health and health.get("ok"):
            result = _daemon_request(config, "POST", "/api/print", request_payload, timeout=180)
            if result is None:
                print("The local daemon disappeared during printing.", file=sys.stderr)
                return 1
            if result.get("error"):
                print(f"Print failed: {result['error']}", file=sys.stderr)
                return 1
            _print_result(result, config, json_output=args.json)
            return 0

    return asyncio.run(_print_direct(args, config))


def _cmd_preview(args: argparse.Namespace, config: Config) -> int:
    request = _make_request(args, config)
    try:
        rendered = render(request)
    except Exception as exc:
        print(f"Could not render preview: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out or "thermal-preview.png")
    rendered.save_png(out)
    print(f"Wrote {out} ({rendered.width}x{rendered.height}px, {rendered.height} raster rows)")
    return 0


async def _cmd_status(args: argparse.Namespace, config: Config) -> int:
    if not args.no_daemon:
        # A cold status call can take a while: the BLE transport may query
        # status, battery, and device info before replying.
        result = _daemon_request(config, "GET", "/api/status?refresh=true", timeout=25.0)
        if result and not result.get("error"):
            _print_json(result)
            return 0
    manager = _direct_manager(config)
    try:
        await manager.connect(persist=False)
        result = await manager.status(refresh=True)
    except Exception as exc:
        result = {"connected": False, "error": str(exc), "label": "not connected"}
    finally:
        await manager.close()
    _print_json(result)
    return 0 if result.get("connected") else 1


async def _cmd_connect(args: argparse.Namespace, config: Config) -> int:
    if args.transport:
        config.transport = args.transport
    if args.protocol:
        config.protocol = args.protocol
    if args.name:
        config.device_name = args.name
    if args.address:
        config.device_address = args.address
    manager = _direct_manager(config)
    try:
        result = await manager.connect(persist=not args.no_save)
    except Exception as exc:
        print(f"Connect failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await manager.close()
    _print_json(result)
    return 0


def _cmd_usb_probe(_args: argparse.Namespace) -> int:
    print("Serial ports:")
    ports = list_serial_ports()
    if not ports:
        print("  (none)")
    for port in ports:
        print(f"  {port['device']:<28} {port.get('description', '')}")
    if sys.platform == "darwin":
        print("\nmacOS USB devices:")
        try:
            raw = subprocess.check_output(
                ["system_profiler", "SPUSBDataType", "-json"], text=True, timeout=20
            )
            data = json.loads(raw)
            devices: list[dict[str, Any]] = []

            def walk(node: Any) -> None:
                if isinstance(node, dict):
                    if "USB Product Name" in node or "_name" in node:
                        devices.append(node)
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(data)
            for device in devices:
                name = device.get("USB Product Name") or device.get("_name") or "?"
                vendor = device.get("USB Vendor Name", "")
                print(f"  {name}  {vendor}")
        except Exception as exc:
            print(f"  (could not query USB: {exc})")
        print(
            "\nNote: the USB-C port on most of these pocket printers is charging-only. "
            "If no new USB/serial device appears when you connect the cable, use Bluetooth."
        )
    return 0


def _cmd_config(args: argparse.Namespace, config: Config) -> int:
    if args.action == "path":
        print(CONFIG_PATH)
        return 0
    if args.action == "show" or args.action is None:
        _print_json({"path": str(CONFIG_PATH), "config": asdict(config)})
        return 0
    if args.action == "set":
        if not args.key:
            print("Usage: thermal config set <key> <value>", file=sys.stderr)
            return 2
        known = {f.name for f in fields(Config)}
        if args.key not in known:
            print(f"Unknown setting {args.key!r}. Known: {', '.join(sorted(known))}", file=sys.stderr)
            return 2
        raw = " ".join(args.value)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        if args.key in {"width_px", "density", "feed_dots", "chunk_size", "chunk_delay_ms", "baudrate", "api_port"}:
            try:
                value = int(value)
            except (TypeError, ValueError):
                print(f"{args.key} must be an integer", file=sys.stderr)
                return 2
        if args.key in {"label_mode", "auto_connect"}:
            value = str(value).lower() in {"1", "true", "yes", "on"}
        setattr(config, args.key, value)
        path = config.save()
        print(f"Updated {path}")
        _print_json({args.key: value})
        return 0
    print("Usage: thermal config [show|path|set <key> <value>]", file=sys.stderr)
    return 2


def _cmd_serve(args: argparse.Namespace, config: Config) -> int:
    import uvicorn

    from .service import create_app

    if args.mock:
        config.transport = "mock"
    if args.host:
        config.api_host = args.host
    if args.port:
        config.api_port = args.port
    app = create_app(config=config, mock=args.mock)
    url = config.api_url
    print(f"Thermal printer service: {url}")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=config.api_host, port=config.api_port, log_level="info")
    return 0


# ---------------------------------------------------------------------------
# Parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thermal",
        description="Print notes from your coding agent to a tiny BLE thermal printer.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan for nearby BLE printers")
    p_scan.add_argument("--timeout", type=float, default=8.0)
    p_scan.add_argument("--all", action="store_true", help="Show every BLE device, not just likely printers")
    p_scan.set_defaults(func=lambda a, c: asyncio.run(_cmd_scan(a, c)))

    p_doctor = sub.add_parser("doctor", help="Connect and dump GATT + device diagnostics")
    p_doctor.add_argument("--name", help="BLE device name")
    p_doctor.add_argument("--address", help="BLE address/UUID")
    p_doctor.add_argument("--transport", choices=["ble", "serial", "mock"])
    p_doctor.add_argument("--protocol", choices=["auto", "mxw01", "luck", "escpos"])
    p_doctor.add_argument("--mock", action="store_true")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=lambda a, c: asyncio.run(_cmd_doctor(a, c)))

    def add_print_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("text", nargs="*", help="Message to print (stdin is used when omitted)")
        p.add_argument("--title", help="Heading at the top")
        p.add_argument("--style", choices=STYLES, default="note")
        p.add_argument("--footer", help="Small footer line (use '' to hide)")
        p.add_argument("--qr", help="Encode a URL/text as a QR code")
        p.add_argument("--image", help="Image file to print")
        p.add_argument("--invert-image", action="store_true")
        p.add_argument("--copies", type=int, default=1, choices=range(1, 6))
        p.add_argument("--dry-run", action="store_true", help="Render and save a preview without printing")
        p.add_argument("--json", action="store_true")
        p.add_argument("--source", default="cli", help=argparse.SUPPRESS)

    p_print = sub.add_parser("print", help="Render and print a message")
    add_print_options(p_print)
    p_print.add_argument("--no-daemon", action="store_true", help="Connect directly instead of using the local service")
    p_print.set_defaults(func=_cmd_print)

    p_preview = sub.add_parser("preview", help="Render a PNG preview without printing")
    add_print_options(p_preview)
    p_preview.add_argument("--out", default="thermal-preview.png")
    p_preview.set_defaults(func=_cmd_preview)

    p_status = sub.add_parser("status", help="Show printer status")
    p_status.add_argument("--no-daemon", action="store_true")
    p_status.set_defaults(func=lambda a, c: asyncio.run(_cmd_status(a, c)))

    p_connect = sub.add_parser("connect", help="Connect and save the selected device")
    p_connect.add_argument("--name")
    p_connect.add_argument("--address")
    p_connect.add_argument("--transport", choices=["ble", "serial", "mock"])
    p_connect.add_argument("--protocol", choices=["auto", "mxw01", "luck", "escpos"])
    p_connect.add_argument("--no-save", action="store_true")
    p_connect.set_defaults(func=lambda a, c: asyncio.run(_cmd_connect(a, c)))

    p_usb = sub.add_parser("usb-probe", help="List USB/serial devices (test whether USB-C does data)")
    p_usb.set_defaults(func=lambda a, c: _cmd_usb_probe(a))

    p_config = sub.add_parser("config", help="Show or edit config")
    p_config.add_argument("action", nargs="?", choices=["show", "path", "set"], default="show")
    p_config.add_argument("key", nargs="?")
    p_config.add_argument("value", nargs="*")
    p_config.set_defaults(func=_cmd_config)

    p_serve = sub.add_parser("serve", help="Run the local web UI and API")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--mock", action="store_true", help="Serve with a fake printer that writes PNGs")
    p_serve.add_argument("--open", action="store_true", help="Open the web UI in a browser")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = Config.load()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    try:
        code = args.func(args, config)
    except KeyboardInterrupt:
        code = 130
    except TransportError as exc:
        print(f"Printer error: {exc}", file=sys.stderr)
        code = 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code if code is not None else 0)
