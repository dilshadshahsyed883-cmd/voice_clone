"""CPU-only telemetry regression tests; no NVIDIA runtime required."""
import json
import time

from app import telemetry


def test_records_live_and_final_resource_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "_gpu_sample", lambda: {
        "gpu_device_utilization_pct": 24.0,
        "gpu_device_vram_used_mib": 800.0,
        "gpu_device_vram_total_mib": 8192.0,
    })
    monkeypatch.setattr(telemetry, "_rss_mib", lambda: 128.5)
    with telemetry.JobTelemetry(tmp_path, interval=0.1) as record:
        running = json.loads((tmp_path / "resources.json").read_text())
        assert running["status"] == "running"
        assert running["sample_count"] >= 1
        time.sleep(0.12)
    final = json.loads((tmp_path / "resources.json").read_text())
    assert final["status"] == "completed"
    assert final["peak_process_rss_mib"] == 128.5
    assert final["peak_gpu_device_vram_used_mib"] == 800.0
    assert final["sample_count"] >= 2
    assert len((tmp_path / "resource-samples.jsonl").read_text().splitlines()) >= 2
    assert record.summary()["status"] == "completed"


def test_persists_failure_and_tolerates_missing_nvidia_smi(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(telemetry.subprocess, "run", missing)
    try:
        with telemetry.JobTelemetry(tmp_path) as record:
            raise RuntimeError("inference failed")
    except RuntimeError:
        pass
    summary = record.summary()
    assert summary["status"] == "failed"
    assert summary["last"]["gpu"] is None


def test_terminal_report_uses_persisted_job_data(tmp_path, monkeypatch, capsys):
    import sys
    job_id = "a" * 32
    folder = tmp_path / job_id
    folder.mkdir()
    (folder / "status.json").write_text('{"state": "completed", "metrics": {"speed_factor": 3.2}}')
    monkeypatch.setattr(sys, "argv", ["telemetry", job_id, "--job-dir", str(tmp_path)])
    telemetry.main()
    output = json.loads(capsys.readouterr().out)
    assert output["job"]["metrics"]["speed_factor"] == 3.2
