from __future__ import annotations

from app.job_telemetry import ResourceRecorder

import asyncio
import base64
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.audio_validation import validate_reference_wav
from app.config import SUPPORTED_LANGUAGES, settings
from app.runtime import runtime


def _generate_recorded(job_id: str, **kwargs):
    with ResourceRecorder(job_id, settings.jobs_dir / job_id):
        return runtime.generate(**kwargs)


@dataclass
class JobRecord:
    id: str
    state: str
    created_at: float
    updated_at: float
    request: dict
    result: dict | None = None
    error: str | None = None
    output_path: str | None = None
    ref_path: str | None = None
    history: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        data = asdict(self)
        data.pop("ref_path", None)
        data["audio_ready"] = bool(
            self.state == "completed" and self.output_path and Path(self.output_path).is_file()
        )
        return data


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None

    async def start(self, load_task: asyncio.Task | None = None) -> None:
        if not self.jobs:
            self._restore()
        if self.worker_task is None or self.worker_task.done():
            self.worker_task = asyncio.create_task(
                self._worker(load_task), name="omnivoice-job-worker"
            )

    def get(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def _restore(self) -> None:
        """Restore terminal records and safely requeue interrupted jobs."""
        for path in settings.jobs_dir.glob("*/job.json"):
            job_id = path.parent.name
            if not re.fullmatch(r"[a-f0-9]{32}", job_id):
                continue
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if row.get("id") != job_id or not isinstance(row.get("request"), dict):
                    continue
                # Only read fields we own. Never trust persisted paths or
                # arbitrary JSON keys from older job schema versions.
                job = JobRecord(
                    id=job_id, state=str(row["state"]),
                    created_at=float(row["created_at"]),
                    updated_at=float(row["updated_at"]),
                    request=row["request"],
                    result=row.get("result"), error=row.get("error"),
                    output_path=str(path.parent / "output.wav"),
                    ref_path=str(path.parent / "reference.wav")
                    if (path.parent / "reference.wav").is_file() else None,
                    history=row.get("history") if isinstance(row.get("history"), list) else [],
                )
                if job.state in ("running", "queued"):
                    request = job.request
                    needs_ref = request.get("mode", "clone") == "clone" and not request.get("voice_id")
                    if not str(request.get("text") or "").strip() or (needs_ref and not job.ref_path):
                        job.state = "failed"
                        job.error = "request or reference audio unavailable after restart"
                    else:
                        job.state = "queued"
                        job.error = None
                        self.queue.put_nowait(job_id)
                    job.updated_at = time.time()
                    job.history.append({"state": job.state, "at": job.updated_at,
                                        "reason": "worker restart"})
                self.jobs[job_id] = job
                self._persist(job)
            except (ValueError, TypeError, KeyError, OSError):
                # A damaged record cannot stop unrelated jobs recovering.
                continue

    def _persist(self, job: JobRecord) -> None:
        job_dir = settings.jobs_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / "job.json"
        temporary = job_dir / "job.json.tmp"
        temporary.write_text(
            json.dumps(job.public(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)

    async def submit(self, payload: dict) -> JobRecord:
        text = str(payload.get("text") or "")
        language = payload.get("language")
        mode = payload.get("mode", "clone")
        if not text.strip():
            raise ValueError("text is required")
        if len(text) > settings.max_text_chars:
            raise ValueError(f"text exceeds {settings.max_text_chars} characters")
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                "language must be one of: " + ", ".join(sorted(SUPPORTED_LANGUAGES))
            )
        if mode not in {"clone", "auto", "design"}:
            raise ValueError("mode must be clone, auto, or design")

        job_id = uuid.uuid4().hex
        job_dir = settings.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        ref_path: Path | None = None

        if mode == "clone":
            voice_id = str(payload.get("voice_id") or "").strip()
            if voice_id:
                runtime._validate_voice_id(voice_id)
                if not runtime.voice_profile_exists(voice_id):
                    raise ValueError(f"voice profile not found: {voice_id}")
                payload["voice_id"] = voice_id
                payload["ref_audio_b64"] = None
                payload["ref_text"] = None
            else:
                ref_text = str(payload.get("ref_text") or "").strip()
                ref_audio_b64 = str(payload.get("ref_audio_b64") or "")
                if not ref_text:
                    raise ValueError(
                        "clone mode requires voice_id or ref_text; runtime ASR is intentionally disabled"
                    )
                if not ref_audio_b64:
                    raise ValueError("clone mode requires voice_id or ref_audio_b64")
                try:
                    raw = base64.b64decode(ref_audio_b64, validate=True)
                except Exception as exc:
                    raise ValueError(f"invalid base64 reference audio: {exc}") from exc
                if len(raw) > 20 * 1024 * 1024:
                    raise ValueError("reference WAV exceeds 20 MiB")
                ref_path = job_dir / "reference.wav"
                ref_path.write_bytes(raw)
                payload["reference_validation"] = validate_reference_wav(ref_path)
                payload["ref_audio_b64"] = "<stored>"
        elif mode == "design" and not str(payload.get("instruct") or "").strip():
            raise ValueError("design mode requires instruct")

        now = time.time()
        output = job_dir / "output.wav"
        job = JobRecord(
            id=job_id,
            state="queued",
            created_at=now,
            updated_at=now,
            request=payload,
            output_path=str(output),
            ref_path=str(ref_path) if ref_path else None,
            history=[{"state": "queued", "at": now}],
        )
        self.jobs[job_id] = job
        self._persist(job)
        await self.queue.put(job_id)
        return job

    async def _worker(self, load_task: asyncio.Task | None = None) -> None:
        # Recovered queued jobs wait for asynchronous GPU model initialization.
        if load_task is not None:
            await load_task
        while True:
            job_id = await self.queue.get()
            job = self.jobs[job_id]
            try:
                if not runtime.ready:
                    raise RuntimeError(runtime.error or "runtime is not ready")
                job.state = "running"
                job.updated_at = time.time()
                job.history.append({"state": "running", "at": job.updated_at})
                self._persist(job)

                request = job.request
                result = await asyncio.to_thread(
                    _generate_recorded, job_id,
                    text=request["text"],
                    output_path=Path(job.output_path),
                    mode=request.get("mode", "clone"),
                    language=request.get("language"),
                    ref_audio=Path(job.ref_path) if job.ref_path else None,
                    ref_text=request.get("ref_text"),
                    voice_id=request.get("voice_id"),
                    instruct=request.get("instruct"),
                    num_step=int(request.get("num_step", 32)),
                    speed=float(request.get("speed", 1.0)),
                )
                job.result = result
                job.state = "completed"
                job.updated_at = time.time()
                job.history.append({"state": "completed", "at": job.updated_at})
            except Exception as exc:
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.updated_at = time.time()
                job.history.append(
                    {"state": "failed", "at": job.updated_at, "error": job.error}
                )
            finally:
                if job.state in ("completed", "failed") and job.ref_path:
                    # Do not retain the uploaded WAV once a job is terminal.
                    Path(job.ref_path).unlink(missing_ok=True)
                    job.ref_path = None
                self._persist(job)
                self.queue.task_done()


jobs = JobManager()
