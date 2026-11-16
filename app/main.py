"""Qwen3-TTS 0.6B GPU worker API with durable single-worker queue."""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.engine import MODEL_DIR, SUPPORTED, engine, split_text, validate_reference
from app.telemetry import JobTelemetry
from app.job_telemetry import ResourceRecorder

JOB_DIR = Path(os.getenv("QWEN_JOB_DIR", "/data/jobs"))
API_TOKEN = os.getenv("QWEN_API_TOKEN", "")
jobs: dict[str, dict] = {}
queue: asyncio.Queue[str] = asyncio.Queue()


class CloneRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    ref_audio_b64: str = Field(min_length=1, max_length=23000000)
    ref_text: str = Field(min_length=1, max_length=10000)
    language: str = "English"
    chunk_chars: int = Field(default=300, ge=80, le=600)


def require_token(authorization: str | None = Header(default=None)):
    if not API_TOKEN:
        raise HTTPException(503, "QWEN_API_TOKEN is not configured")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix) or not hmac.compare_digest(authorization[len(prefix):], API_TOKEN):
        raise HTTPException(401, "invalid or missing bearer token")


def _job_path(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "job not found")
    return JOB_DIR / job_id


def _save(job_id: str):
    folder = _job_path(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "status.json"
    temp = folder / "status.json.tmp"
    temp.write_text(json.dumps(jobs[job_id]), encoding="utf-8")
    temp.replace(target)


def _render(job_id: str) -> dict:
    folder = _job_path(job_id)
    params = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    ref = base64.b64decode(params.pop("ref_audio_b64"), validate=True)
    reference = folder / "reference.wav"
    reference.write_bytes(ref)
    try:
        validate_reference(reference)
        return engine.render(
            text=params["text"], language=params["language"],
            ref_audio=reference, ref_text=params["ref_text"],
            chunk_chars=params["chunk_chars"],
            output=folder / "output.wav",
        )
    finally:
        reference.unlink(missing_ok=True)


async def _worker(load_task: asyncio.Task):
    # Restored jobs must not fail merely because model initialization is ongoing.
    await load_task
    while True:
        job_id = await queue.get()
        try:
            record = jobs[job_id]
            if not engine.ready:
                record.update(state="failed", error=engine.error or "GPU model not ready", finished_at=time.time())
                (_job_path(job_id) / "request.json").unlink(missing_ok=True)
            else:
                record.update(state="running", started_at=time.time())
                _save(job_id)
                monitor = JobTelemetry(_job_path(job_id))
                try:
                    with ResourceRecorder(job_id, _job_path(job_id)), monitor:
                        metrics = await asyncio.to_thread(_render, job_id)
                    record.update(state="completed", finished_at=time.time(), metrics=metrics)
                except Exception as exc:
                    record.update(state="failed", finished_at=time.time(), error=f"{type(exc).__name__}: {exc}")
                finally:
                    record["resources"] = monitor.summary()
                    (_job_path(job_id) / "request.json").unlink(missing_ok=True)
            _save(job_id)
        finally:
            queue.task_done()


@asynccontextmanager
async def lifespan(_: FastAPI):
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    for path in JOB_DIR.glob("*/status.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            job_id = path.parent.name
            if not re.fullmatch(r"[a-f0-9]{32}", job_id):
                continue
            if row.get("state") in ("queued", "running"):
                if (path.parent / "request.json").exists():
                    row["state"] = "queued"
                else:
                    row.update(state="failed", error="request lost during restart")
            jobs[job_id] = row
            _save(job_id)
            if row["state"] == "queued":
                await queue.put(job_id)
        except (ValueError, OSError, KeyError):
            continue
    load_task = asyncio.create_task(asyncio.to_thread(engine.load))
    worker_task = asyncio.create_task(_worker(load_task))
    try:
        yield
    finally:
        worker_task.cancel()
        load_task.cancel()
        await asyncio.gather(worker_task, load_task, return_exceptions=True)


app = FastAPI(title="Qwen3-TTS 0.6B Clone GPU", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True, "service": "qwen3-tts-0.6b", "ready": engine.ready, "loading": engine.loading}


@app.get("/ready")
def ready(response: Response):
    if not engine.ready:
        response.status_code = 503
    return {"ready": engine.ready, "error": engine.error, "model": str(MODEL_DIR)}


@app.get("/runtime")
def runtime(_: None = Depends(require_token)):
    return {"ready": engine.ready, "loading": engine.loading,
            "error": engine.error, "load_seconds": engine.model_load_seconds,
            "gpu": engine.gpu_usage(), "queued": queue.qsize(), "languages": SUPPORTED}


@app.post("/v1/jobs", status_code=202)
async def create_job(req: CloneRequest, _: None = Depends(require_token)):
    if not engine.ready:
        raise HTTPException(503, engine.error or "GPU model is still loading")
    if req.language not in SUPPORTED:
        raise HTTPException(422, f"Unsupported language: {req.language}")
    try:
        split_text(req.text, req.chunk_chars)
        reference = base64.b64decode(req.ref_audio_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(422, f"invalid clone request: {exc}") from exc
    if len(reference) > 16 * 1024 * 1024:
        raise HTTPException(422, "reference audio exceeds 16 MiB")
    job_id = uuid.uuid4().hex
    folder = _job_path(job_id)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "request.json").write_text(json.dumps(req.model_dump()), encoding="utf-8")
    jobs[job_id] = {"job_id": job_id, "state": "queued", "created_at": time.time(),
                    "language": req.language, "metrics": None, "resources": None, "error": None}
    _save(job_id)
    await queue.put(job_id)
    return {"job_id": job_id, "state": "queued"}


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, _: None = Depends(require_token)):
    _job_path(job_id)
    if job_id not in jobs:
        raise HTTPException(404, "job not found")
    return jobs[job_id]


@app.get("/v1/jobs/{job_id}/audio")
def get_audio(job_id: str, _: None = Depends(require_token)):
    folder = _job_path(job_id)
    if job_id not in jobs or jobs[job_id].get("state") != "completed":
        raise HTTPException(404, "completed audio not found")
    output = folder / "output.wav"
    if not output.is_file():
        raise HTTPException(404, "audio file missing")
    return FileResponse(output, media_type="audio/wav", filename=f"{job_id}.wav")


@app.get("/v1/jobs/{job_id}/resources")
def get_job_resources(job_id: str, _: None = Depends(require_token)):
    folder = _job_path(job_id)
    if job_id not in jobs:
        raise HTTPException(404, "job not found")
    path = folder / "resources.json"
    if not path.is_file():
        return {"job_id": job_id, "state": jobs[job_id]["state"], "resources": None}
    return {"job_id": job_id, "resources": json.loads(path.read_text(encoding="utf-8"))}
