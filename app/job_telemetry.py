"""Durable, best-effort job telemetry. CPU/RAM/IO are container-wide; GPU readings are device-wide."""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

MIB = 1024 * 1024

def _read_int(path, key=None):
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
        if key:
            for row in value.splitlines():
                fields = row.split()
                if len(fields) >= 2 and fields[0] == key:
                    return int(fields[1])
            return None
        return int(value)
    except (OSError, ValueError):
        return None

def _gpu():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=False)
        if result.returncode or not result.stdout.strip():
            return {}
        fields = [f.strip() for f in result.stdout.splitlines()[0].split(",")]
        if len(fields) != 6:
            return {}
        def number(v):
            try:
                return float(v)
            except ValueError:
                return None
        return {"gpu_name": fields[0], "gpu_util_pct": number(fields[1]),
                "vram_used_mib": number(fields[2]), "vram_total_mib": number(fields[3]),
                "gpu_power_w": number(fields[4]), "gpu_temperature_c": number(fields[5])}
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return {}

def _io():
    try:
        totals = {"rbytes": 0, "wbytes": 0}
        for line in Path("/sys/fs/cgroup/io.stat").read_text(encoding="utf-8").splitlines():
            for token in line.split()[1:]:
                key, _, value = token.partition("=")
                if key in totals:
                    totals[key] += int(value)
        return {"disk_read_bytes": totals["rbytes"], "disk_write_bytes": totals["wbytes"]}
    except (OSError, ValueError):
        return {"disk_read_bytes": None, "disk_write_bytes": None}

def _snapshot():
    ram = _read_int("/sys/fs/cgroup/memory.current")
    return {"at": datetime.now(timezone.utc).isoformat(),
            "cpu_usage_usec": _read_int("/sys/fs/cgroup/cpu.stat", "usage_usec"),
            "ram_used_mib": round(ram / MIB, 2) if ram is not None else None,
            **_io(), **_gpu()}

def _stats(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return (round(sum(vals) / len(vals), 3), round(max(vals), 3)) if vals else (None, None)

def _delta(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return max(0, int(vals[-1] - vals[0])) if len(vals) > 1 else None

def _save_json(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)

class ResourceRecorder:
    """Sample resource use during render; keep separate attempts after retries."""
    def __init__(self, job_id, job_dir, interval=1.0):
        self.job_id = job_id
        self.job_dir = Path(job_dir)
        self.interval = max(0.25, float(interval))
        self.samples = []
        self.stop_signal = threading.Event()
        self.thread = None
        self.attempt = None
        self.started = 0.0

    def __enter__(self):
        self.started = time.monotonic()
        try:
            self.attempt = self.job_dir / "telemetry" / (
                datetime.now(timezone.utc).strftime("attempt-%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:8])
            self.attempt.mkdir(parents=True, exist_ok=False)
            self._sample()
            self.thread = threading.Thread(target=self._loop, name="job-resource-sampler", daemon=True)
            self.thread.start()
        except (OSError, RuntimeError, ValueError):
            self.attempt = None  # Sensor failure must never fail a render.
        return self

    def _sample(self):
        if self.attempt is None:
            return
        try:
            data = _snapshot()
            self.samples.append(data)
            with (self.attempt / "samples.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(data, separators=(",", ":")) + "\n")
        except (OSError, ValueError, TypeError):
            pass

    def _loop(self):
        while not self.stop_signal.wait(self.interval):
            self._sample()

    def __exit__(self, exc_type, exc, tb):
        self.stop_signal.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        if self.attempt is None:
            return False
        self._sample()
        elapsed = max(0.0, time.monotonic() - self.started)
        cpu_usec = _delta(self.samples, "cpu_usage_usec")
        cores = round(cpu_usec / (elapsed * 1_000_000), 3) if cpu_usec is not None and elapsed else None
        gpu_avg, gpu_peak = _stats(self.samples, "gpu_util_pct")
        ram_avg, ram_peak = _stats(self.samples, "ram_used_mib")
        vram_avg, vram_peak = _stats(self.samples, "vram_used_mib")
        power_avg, _ = _stats(self.samples, "gpu_power_w")
        summary = {
            "schema_version": 1, "job_id": self.job_id, "attempt": self.attempt.name,
            "started_at": self.samples[0]["at"] if self.samples else None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "capture_status": "exception" if exc_type else "closed",
            "elapsed_seconds": round(elapsed, 3), "sample_count": len(self.samples),
            "resource_scope": "container-wide cgroup CPU/RAM/IO; device-wide NVIDIA GPU/VRAM/power",
            "cpu_avg_cores": cores, "cpu_avg_pct_one_core": round(cores * 100, 1) if cores is not None else None,
            "ram_avg_mib": ram_avg, "ram_peak_mib": ram_peak,
            "gpu_util_avg_pct": gpu_avg, "gpu_util_peak_pct": gpu_peak,
            "vram_avg_mib": vram_avg, "vram_peak_mib": vram_peak,
            "gpu_power_avg_w": power_avg,
            "gpu_energy_estimate_wh": round(power_avg * elapsed / 3600, 4) if power_avg is not None else None,
            "gpu_name": next((s["gpu_name"] for s in self.samples if s.get("gpu_name")), None),
            "gpu_vram_total_mib": next((s["vram_total_mib"] for s in self.samples if s.get("vram_total_mib") is not None), None),
            "disk_read_bytes": _delta(self.samples, "disk_read_bytes"),
            "disk_write_bytes": _delta(self.samples, "disk_write_bytes"),
        }
        for key in ("GPU_HOURLY_USD", "GPU_USD_PER_HOUR", "IMAGEN_GPU_HOURLY_USD"):
            if os.getenv(key):
                try:
                    rate = float(os.environ[key])
                    if 0 <= rate <= 100:
                        summary["gpu_cost_estimate_usd"] = round(rate * elapsed / 3600, 6)
                except ValueError:
                    pass
                break
        try:
            _save_json(self.attempt / "summary.json", summary)
            _save_json(self.job_dir / "resource_usage.json", summary)
        except OSError:
            pass
        return False

def _resolve(job_id, root=None):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", job_id):
        raise ValueError("Invalid job ID")
    candidates = [root] if root else [
        os.getenv("JOB_TELEMETRY_ROOT"), os.getenv("OMNIVOICE_JOBS_DIR"),
        os.getenv("QWEN_JOB_DIR"), os.getenv("OUTPUT_ROOT"),
        os.getenv("IMAGEN_OUTPUT_ROOT"), os.getenv("JOB_DIR"),
        "/data/jobs", "/data/outputs", "/tmp/omnivoice/jobs", "/data"]
    for base in filter(None, candidates):
        path = Path(base) / job_id
        if path.is_dir():
            return path
    raise FileNotFoundError("Job directory not found; specify --root /path/to/jobs")

def _job_details(folder):
    for name in ("manifest.json", "status.json", "job.json"):
        try:
            row = json.loads((folder / name).read_text(encoding="utf-8"))
            return {"job_status": row.get("status", row.get("state")),
                    "performance": row.get("performance"), "render_metrics": row.get("metrics"),
                    "render_seconds": row.get("render_seconds"),
                    "wall_seconds": row.get("wall_seconds")}
        except (OSError, ValueError, TypeError):
            continue
    return {}

def main(argv=None):
    cli = argparse.ArgumentParser(description="Read saved job performance and resource usage")
    cli.add_argument("job_id")
    cli.add_argument("--root", help="Parent directory of job folders")
    cli.add_argument("--json", action="store_true")
    cli.add_argument("--follow", action="store_true", help="Print live samples until job finishes")
    args = cli.parse_args(argv)
    try:
        folder = _resolve(args.job_id, args.root)
        final = folder / "resource_usage.json"
        if args.follow:
            while not final.exists():
                attempts = sorted((folder / "telemetry").glob("attempt-*/samples.jsonl"))
                if attempts:
                    rows = attempts[-1].read_text(encoding="utf-8").splitlines()
                    if rows:
                        sample = json.loads(rows[-1])
                        print(json.dumps({"at": sample.get("at"), "gpu_util_pct": sample.get("gpu_util_pct"),
                                          "vram_used_mib": sample.get("vram_used_mib"),
                                          "ram_used_mib": sample.get("ram_used_mib")}), flush=True)
                if _job_details(folder).get("job_status") in ("completed", "failed", "cancelled"):
                    break
                time.sleep(2)
        report = (json.loads(final.read_text(encoding="utf-8")) if final.exists()
                  else {"capture_status": "incomplete_or_not_started"})
        report.update(_job_details(folder))
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            for key, val in report.items():
                print(f"{key}: {json.dumps(val) if isinstance(val, (dict, list)) else val}")
        return 0 if final.exists() else 2
    except (OSError, ValueError) as error:
        cli.exit(2, f"error: {error}\n")

if __name__ == "__main__":
    raise SystemExit(main())
