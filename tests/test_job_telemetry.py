import json
import time
from pathlib import Path

from app import job_telemetry as telemetry

def test_persistent_attempt_and_terminal_report(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(telemetry, "_gpu", lambda: {"gpu_name": "fake-gpu", "gpu_util_pct": 60.0,
                                                    "vram_used_mib": 512.0, "vram_total_mib": 1024.0,
                                                    "gpu_power_w": 110.0})
    job = tmp_path / "abcdef0123456789"
    with telemetry.ResourceRecorder("abcdef0123456789", job, interval=0.25):
        time.sleep(0.28)
    report = json.loads((job / "resource_usage.json").read_text())
    assert report["sample_count"] >= 2
    assert report["gpu_util_avg_pct"] == 60.0
    assert report["vram_peak_mib"] == 512.0
    assert telemetry.main(["abcdef0123456789", "--root", str(tmp_path), "--json"]) == 0
    assert '"cpu_avg_cores"' in capsys.readouterr().out
    with telemetry.ResourceRecorder("abcdef0123456789", job, interval=0.25):
        pass
    assert len(list((job / "telemetry").glob("attempt-*/summary.json"))) == 2

def test_unavailable_gpu_is_unknown_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "_gpu", lambda: {})
    with telemetry.ResourceRecorder("abcdef0123456789", tmp_path, interval=0.25):
        pass
    report = json.loads((tmp_path / "resource_usage.json").read_text())
    assert report["gpu_util_avg_pct"] is None
    assert report["vram_peak_mib"] is None
