from __future__ import annotations

import re
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "thermal" / "web"


def test_every_id_used_by_js_exists_in_html() -> None:
    html = (WEB_DIR / "index.html").read_text()
    js = (WEB_DIR / "app.js").read_text()
    used = set(re.findall(r'\$\("([^"]+)"\)', js))
    defined = set(re.findall(r'id="([^"]+)"', html))
    assert used <= defined, f"missing HTML ids: {sorted(used - defined)}"


def test_html_ids_are_unique() -> None:
    html = (WEB_DIR / "index.html").read_text()
    ids = re.findall(r'id="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate HTML ids"
