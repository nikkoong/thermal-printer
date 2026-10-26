from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .config import DATA_DIR


@dataclass
class JobRecord:
    id: str
    created_at: str
    title: str
    style: str
    width: int
    height: int
    bytes: int = 0
    duration_ms: int = 0
    transport: str = ""
    source: str | None = None
    dry_run: bool = False
    printed: bool = False
    preview_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HistoryStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or (DATA_DIR / "history")

    def save(
        self,
        image: Image.Image,
        *,
        title: str,
        style: str,
        source: str | None,
        dry_run: bool,
        printed: bool,
        byte_count: int = 0,
        duration_ms: int = 0,
        transport: str = "",
    ) -> JobRecord:
        self.directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        job_id = now.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        png_path = self.directory / f"{job_id}.png"
        image.save(png_path, format="PNG", optimize=True)
        record = JobRecord(
            id=job_id,
            created_at=now.isoformat(timespec="seconds"),
            title=title,
            style=style,
            width=image.width,
            height=image.height,
            bytes=byte_count,
            duration_ms=duration_ms,
            transport=transport,
            source=source,
            dry_run=dry_run,
            printed=printed,
            preview_url=f"/api/jobs/{job_id}/preview.png",
        )
        (self.directory / f"{job_id}.json").write_text(json.dumps(record.as_dict(), indent=2) + "\n")
        self.prune()
        return record

    def list(self, limit: int = 30) -> list[JobRecord]:
        if not self.directory.exists():
            return []
        records: list[JobRecord] = []
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text())
                records.append(JobRecord(**data))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if len(records) >= limit:
                break
        return records

    def get(self, job_id: str) -> tuple[JobRecord, Path] | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
            return None
        json_path = self.directory / f"{job_id}.json"
        png_path = self.directory / f"{job_id}.png"
        if not json_path.exists() or not png_path.exists():
            return None
        try:
            record = JobRecord(**json.loads(json_path.read_text()))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        return record, png_path

    def prune(self, keep: int = 200) -> None:
        if not self.directory.exists():
            return
        json_files = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in json_files[keep:]:
            old.unlink(missing_ok=True)
            old.with_suffix(".png").unlink(missing_ok=True)
