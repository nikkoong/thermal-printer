from __future__ import annotations

import asyncio
import base64
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from . import __version__
from .config import CONFIG_PATH, DATA_DIR, Config
from .printer import PrinterManager
from .render import STYLES, PrintRequest
from .transports import TransportError

log = logging.getLogger("thermal")

WEB_DIR = Path(__file__).parent / "web"


class PrintPayload(BaseModel):
    message: str = Field(default="", max_length=8000)
    title: str | None = Field(default=None, max_length=200)
    style: Literal["note", "receipt", "quote", "sticker", "task"] = "note"
    footer: str | None = Field(default=None, max_length=500)
    qr: str | None = Field(default=None, max_length=900)
    image_base64: str | None = None
    invert_image: bool = False
    copies: int = Field(default=1, ge=1, le=5)
    dry_run: bool = False
    density: int | None = Field(default=None, ge=0, le=2)
    feed_dots: int | None = Field(default=None, ge=0, le=255)
    label_mode: bool | None = None
    source: str | None = Field(default="web", max_length=100)


class ConnectPayload(BaseModel):
    transport: str | None = None
    protocol: str | None = None
    address: str | None = None
    name: str | None = None
    persist: bool = True


class ConfigPatch(BaseModel):
    transport: str | None = None
    protocol: str | None = None
    device_name: str | None = None
    device_address: str | None = None
    width_px: int | None = Field(default=None, ge=128, le=832)
    density: int | None = Field(default=None, ge=0, le=2)
    feed_dots: int | None = Field(default=None, ge=0, le=255)
    label_mode: bool | None = None
    chunk_size: int | None = Field(default=None, ge=20, le=512)
    chunk_delay_ms: int | None = Field(default=None, ge=0, le=1000)
    service_uuid: str | None = None
    write_uuid: str | None = None
    notify_uuid: str | None = None
    data_uuid: str | None = None
    serial_port: str | None = None
    baudrate: int | None = Field(default=None, ge=1200, le=921600)
    auto_connect: bool | None = None


def _decode_image(value: str) -> Image.Image:
    raw = value
    if "," in raw and raw.strip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid image_base64: {exc}") from exc
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is larger than 12 MB")
    try:
        return Image.open(io.BytesIO(data))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not decode image: {exc}") from exc


def _request_from_payload(payload: PrintPayload, config: Config) -> PrintRequest:
    image = _decode_image(payload.image_base64) if payload.image_base64 else None
    return PrintRequest(
        message=payload.message,
        title=payload.title,
        style=payload.style,
        footer=payload.footer,
        qr=payload.qr,
        image=image,
        invert_image=payload.invert_image,
        width_px=config.width_px,
        density=config.density if payload.density is None else payload.density,
        feed_dots=config.feed_dots if payload.feed_dots is None else payload.feed_dots,
        label_mode=config.label_mode if payload.label_mode is None else payload.label_mode,
        source=payload.source,
    )


def create_app(
    *,
    config: Config | None = None,
    mock: bool = False,
    data_dir: Path | None = None,
) -> FastAPI:
    cfg = config or Config.load()
    if mock:
        cfg.transport = "mock"
    if data_dir:
        # HistoryStore reads from the module constant; override for tests.
        import thermal.history as history_module

        history_module.DATA_DIR = data_dir

    manager = PrinterManager(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.manager = manager
        if cfg.transport == "mock" or (cfg.auto_connect and (cfg.device_name or cfg.device_address)):
            async def connect_later() -> None:
                await asyncio.sleep(0.2)
                for attempt in range(3):
                    try:
                        await manager.connect(persist=False)
                        log.info(
                            "Connected to %s",
                            manager.transport.label if manager.transport else "printer",
                        )
                        return
                    except Exception as exc:
                        if attempt == 2:
                            log.warning("Auto-connect failed: %s", exc)
                        else:
                            await asyncio.sleep(1.5)

            app.state.connect_task = asyncio.create_task(connect_later())
        yield
        task = getattr(app.state, "connect_task", None)
        if task:
            task.cancel()
        await manager.close()

    app = FastAPI(
        title="Thermal Printer",
        version=__version__,
        description="Local bridge between coding agents and a tiny BLE thermal printer.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://{cfg.api_host}:{cfg.api_port}",
            "http://localhost:8787",
            "http://127.0.0.1:8787",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(TransportError)
    async def transport_error_handler(_request: Request, exc: TransportError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "kind": "printer"})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "mock": cfg.transport == "mock"}

    @app.get("/api/status")
    async def status(refresh: bool = False) -> dict[str, Any]:
        return await manager.status(refresh=refresh)

    @app.get("/api/devices")
    async def devices(timeout: float = Query(default=8.0, ge=1.0, le=30.0)) -> dict[str, Any]:
        found = await manager.scan(timeout=timeout)
        return {"devices": [d.as_dict() for d in found]}

    @app.post("/api/scan")
    async def scan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        timeout = float((payload or {}).get("timeout", 8.0))
        found = await manager.scan(timeout=max(1.0, min(30.0, timeout)))
        return {"devices": [d.as_dict() for d in found]}

    @app.post("/api/connect")
    async def connect(payload: ConnectPayload) -> dict[str, Any]:
        result = await manager.connect(
            transport=payload.transport,
            address=payload.address,
            name=payload.name,
            protocol=payload.protocol,
            persist=payload.persist,
        )
        return result

    @app.post("/api/disconnect")
    async def disconnect() -> dict[str, Any]:
        await manager.disconnect()
        return {"ok": True}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return {
            "config": cfg.public(),
            "config_path": str(CONFIG_PATH),
            "data_dir": str(DATA_DIR),
            "styles": list(STYLES),
            "version": __version__,
        }

    @app.patch("/api/config")
    async def patch_config(payload: ConfigPatch) -> dict[str, Any]:
        values = payload.model_dump(exclude_none=True)
        merged = cfg.merged(values)
        for key, value in values.items():
            setattr(cfg, key, value)
        try:
            merged.save()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not save config: {exc}") from exc
        return {"ok": True, "config": cfg.public(), "config_path": str(CONFIG_PATH)}

    @app.post("/api/preview")
    async def preview(payload: PrintPayload) -> Response:
        request = _request_from_payload(payload, cfg)
        try:
            from .render import render

            rendered = render(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(
            content=rendered.png_bytes(),
            media_type="image/png",
            headers={
                "X-Print-Width": str(rendered.width),
                "X-Print-Height": str(rendered.height),
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/print")
    async def print_job(payload: PrintPayload) -> dict[str, Any]:
        request = _request_from_payload(payload, cfg)
        try:
            result = await manager.print_request(
                request,
                dry_run=payload.dry_run,
                copies=payload.copies,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result

    @app.get("/api/history")
    async def history(limit: int = Query(default=24, ge=1, le=200)) -> dict[str, Any]:
        return {"jobs": [record.as_dict() for record in manager.history.list(limit=limit)]}

    @app.get("/api/jobs/{job_id}/preview.png")
    async def job_preview(job_id: str) -> FileResponse:
        found = manager.history.get(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="Job not found")
        _record, path = found
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/diagnostics")
    async def diagnostics() -> dict[str, Any]:
        try:
            return await manager.diagnostics()
        except TransportError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/templates")
    async def templates() -> dict[str, Any]:
        return {
            "styles": [
                {"id": "note", "name": "Note", "description": "Clean title + markdown body."},
                {"id": "receipt", "name": "Receipt", "description": "Monospace receipt with two-column lines."},
                {"id": "quote", "name": "Quote", "description": "Centered serif quote with attribution."},
                {"id": "task", "name": "Task list", "description": "Checkbox list with a title and date."},
                {"id": "sticker", "name": "Sticker", "description": "Auto-sized bold text inside a rounded border."},
            ],
            "samples": [
                {
                    "label": "Ship it",
                    "style": "note",
                    "title": "SHIPPED",
                    "message": "The feature is live.\n\n- tests pass\n- docs updated\n- no incidents (yet)",
                    "footer": "nice work",
                },
                {
                    "label": "Nice work",
                    "style": "quote",
                    "title": "A NOTE FROM YOUR AGENT",
                    "message": "Small steps, shipped consistently, beat big plans that never leave the notebook.",
                    "footer": "keep going",
                },
                {
                    "label": "TODO",
                    "style": "task",
                    "title": "TODAY",
                    "message": "[x] review the diff\n[ ] fix the flaky test\n[ ] take a walk",
                    "footer": "",
                },
                {
                    "label": "Big sticker",
                    "style": "sticker",
                    "title": "",
                    "message": "SHIP\nIT",
                    "footer": "",
                },
            ],
        }

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app
