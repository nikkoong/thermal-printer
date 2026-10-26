from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from thermal.config import Config
from thermal.service import create_app


def test_preview_and_mock_print(tmp_path: Path) -> None:
    config = Config(transport="mock", api_host="127.0.0.1", api_port=8787)
    app = create_app(config=config, mock=True, data_dir=tmp_path)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        payload = {
            "message": "The build is **green**.",
            "title": "BUILD COMPLETE",
            "style": "note",
            "source": "test",
        }
        preview = client.post("/api/preview", json=payload)
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"
        assert preview.headers["X-Print-Width"] == "384"

        printed = client.post("/api/print", json=payload)
        assert printed.status_code == 200, printed.text
        data = printed.json()
        assert data["ok"] is True
        assert data["job"]["printed"] is True
        assert data["job"]["transport"] == "mock"

        history = client.get("/api/history")
        assert history.status_code == 200
        jobs = history.json()["jobs"]
        assert len(jobs) == 1
        job_id = jobs[0]["id"]

        image = client.get(f"/api/jobs/{job_id}/preview.png")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"


def test_print_without_message_is_422(tmp_path: Path) -> None:
    config = Config(transport="mock")
    app = create_app(config=config, mock=True, data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/print", json={"message": ""})
        assert response.status_code == 422


def test_config_and_templates(tmp_path: Path) -> None:
    config = Config(transport="mock")
    app = create_app(config=config, mock=True, data_dir=tmp_path)
    with TestClient(app) as client:
        data = client.get("/api/config").json()
        assert "styles" in data
        assert "note" in data["styles"]

        templates = client.get("/api/templates").json()
        assert len(templates["samples"]) >= 3
