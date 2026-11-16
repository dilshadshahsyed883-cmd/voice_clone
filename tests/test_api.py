import os
os.environ.pop("QWEN_API_TOKEN", None)

from fastapi.testclient import TestClient
from app.main import app


def test_health_does_not_require_gpu_or_token():
    client = TestClient(app)
    result = client.get("/health")
    assert result.status_code == 200
    assert result.json()["service"] == "qwen3-tts-0.6b"


def test_job_creation_fails_closed_without_token():
    client = TestClient(app)
    result = client.post("/v1/jobs", json={"text": "Hi", "ref_text": "Hi", "ref_audio_b64": "AA=="})
    assert result.status_code == 503


def test_invalid_job_id_not_resolved():
    client = TestClient(app)
    result = client.get("/v1/jobs/../../", headers={"Authorization": "Bearer hi"})
    assert result.status_code in (403, 404, 503)
