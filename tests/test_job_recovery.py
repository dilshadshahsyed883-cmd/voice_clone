"""Persistent OmniVoice jobs survive worker restarts without exposing reference paths."""
import importlib
import json
from types import SimpleNamespace

module = importlib.import_module("app.jobs")


def test_recover_completed_job_and_output(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "settings", SimpleNamespace(jobs_dir=tmp_path))
    jid = "a" * 32
    folder = tmp_path / jid
    folder.mkdir()
    output = folder / "output.wav"
    output.write_bytes(b"fake wav")
    manager = module.JobManager()
    manager._persist(module.JobRecord(
        id=jid, state="completed", created_at=1.0, updated_at=2.0,
        request={"mode": "auto", "text": "hello"}, output_path=str(output),
        result={"audio": {"duration_seconds": 1.2}},
    ))
    recovered = module.JobManager()
    recovered._restore()
    assert recovered.get(jid).state == "completed"
    assert recovered.get(jid).public()["audio_ready"] is True
    assert recovered.queue.empty()


def test_interrupted_auto_job_is_requeued(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "settings", SimpleNamespace(jobs_dir=tmp_path))
    jid = "b" * 32
    manager = module.JobManager()
    manager._persist(module.JobRecord(
        id=jid, state="running", created_at=1.0, updated_at=2.0,
        request={"mode": "auto", "text": "test"}, output_path=str(tmp_path / jid / "output.wav"),
    ))
    recovered = module.JobManager()
    recovered._restore()
    assert recovered.get(jid).state == "queued"
    assert recovered.queue.get_nowait() == jid
    assert json.loads((tmp_path / jid / "job.json").read_text())["state"] == "queued"


def test_missing_reference_is_failed_not_invented(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "settings", SimpleNamespace(jobs_dir=tmp_path))
    jid = "c" * 32
    manager = module.JobManager()
    manager._persist(module.JobRecord(
        id=jid, state="running", created_at=1.0, updated_at=2.0,
        request={"mode": "clone", "text": "test", "ref_text": "sample"},
        output_path=str(tmp_path / jid / "output.wav"),
    ))
    recovered = module.JobManager()
    recovered._restore()
    assert recovered.get(jid).state == "failed"
    assert recovered.queue.empty()
    assert "reference" in recovered.get(jid).error
