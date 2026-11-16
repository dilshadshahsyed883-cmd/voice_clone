"""HTTP aliases and durable resource summaries; no GPU inference is claimed."""
import json
from types import SimpleNamespace
from fastapi.testclient import TestClient
import app.main as main


def test_aliases_share_status():
    client = TestClient(main.app)
    assert client.get("/health").status_code == client.get("/healthz").status_code == 200
    assert client.get("/ready").status_code == client.get("/readyz").status_code
    assert client.get("/runtime").status_code == client.get("/runtimez").status_code


def test_resources_endpoint_reports_saved_data(tmp_path, monkeypatch):
    job_id = "a" * 32
    folder = tmp_path / job_id
    folder.mkdir()
    (folder / "resource_usage.json").write_text(json.dumps({"gpu_util_avg_pct": 47.5}))
    monkeypatch.setattr(main, "settings", SimpleNamespace(jobs_dir=tmp_path, api_token=""))
    monkeypatch.setattr(main.jobs, "jobs", {job_id: SimpleNamespace(state="completed")})
    client = TestClient(main.app)
    assert client.get("/v1/jobs/" + job_id + "/resources").json()["resources"]["gpu_util_avg_pct"] == 47.5
    assert client.get("/v1/jobs/" + "b" * 32 + "/resources").status_code == 404
