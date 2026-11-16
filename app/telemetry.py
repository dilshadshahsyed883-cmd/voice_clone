"""Durable, low-overhead process and GPU telemetry for one inference job.

Records device-wide GPU usage (when nvidia-smi is available), process CPU/RSS,
and live samples. Model inference never depends on telemetry availability.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path


def _rss_mib():
    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return round(resident_pages * os.sysconf("SC_PAGE_SIZE") / 1048576, 2)
    except (OSError, ValueError, IndexError):
        return None


def _gpu_sample():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode:
            return None
        fields = [x.strip() for x in result.stdout.splitlines()[0].split(",")]
        return {
            "gpu_device_utilization_pct": float(fields[0]),
            "gpu_device_vram_used_mib": float(fields[1]),
            "gpu_device_vram_total_mib": float(fields[2]),
        }
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class JobTelemetry:
    def __init__(self, folder: Path, interval: float = 5.0):
        self.folder = Path(folder)
        self.interval = max(0.1, float(interval))
        self._stop = threading.Event()
        self._thread = None
        self._started = None
        self._cpu_started = None
        self._last = None
        self._count = 0
        self._peak_rss = 0.0
        self._peak_gpu_used = 0.0
        self._peak_gpu_util = 0.0
        self._status = "not_started"

    def __enter__(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self._started = time.monotonic()
        self._cpu_started = time.process_time()
        self._status = "running"
        self._capture()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-resource-sampler")
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            self._capture()

    def _capture(self):
        # Samples are serialized by the sampler thread except for the final
        # sample, which is taken only after that thread has joined.
        rss = _rss_mib()
        gpu = _gpu_sample()
        elapsed = max(0.0, time.monotonic() - self._started)
        cpu_seconds = max(0.0, time.process_time() - self._cpu_started)
        sample = {
            "at": round(time.time(), 3),
            "elapsed_seconds": round(elapsed, 3),
            "process_cpu_seconds": round(cpu_seconds, 3),
            "process_cpu_average_pct": round(100 * cpu_seconds / max(elapsed, 0.001), 2),
            "process_rss_mib": rss,
            "gpu": gpu,
        }
        self._last = sample
        self._count += 1
        self._peak_rss = max(self._peak_rss, rss or 0.0)
        if gpu:
            self._peak_gpu_used = max(self._peak_gpu_used, gpu["gpu_device_vram_used_mib"])
            self._peak_gpu_util = max(self._peak_gpu_util, gpu["gpu_device_utilization_pct"])
        try:
            with (self.folder / "resource-samples.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(sample) + "\n")
            destination = self.folder / "resources.json"
            temporary = self.folder / "resources.json.tmp"
            temporary.write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")
            temporary.replace(destination)
        except OSError:
            # Disk/telemetry errors must not disrupt a GPU render.
            pass

    def summary(self):
        return {
            "status": self._status,
            "sample_count": self._count,
            "elapsed_seconds": self._last["elapsed_seconds"] if self._last else 0,
            "peak_process_rss_mib": round(self._peak_rss, 2),
            "peak_gpu_device_vram_used_mib": round(self._peak_gpu_used, 2),
            "peak_gpu_device_utilization_pct": round(self._peak_gpu_util, 2),
            "last": self._last,
            "gpu_values_are_device_wide": True,
        }

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=4)
        # The nvidia-smi call has a 2s timeout; join should have completed.
        self._status = "failed" if exc_type else "completed"
        self._capture()
        return False


def main():
    parser = argparse.ArgumentParser(description="Inspect retained Voice Clone per-job performance")
    parser.add_argument("job_id", help="32-character job ID")
    parser.add_argument("--job-dir", default=os.getenv("QWEN_JOB_DIR") or os.getenv("OMNIVOICE_JOBS_DIR") or "/data/jobs")
    parser.add_argument("--samples", type=int, default=0, help="Show last N resource samples")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{32}", args.job_id):
        parser.error("invalid job ID")
    directory = Path(args.job_dir) / args.job_id
    if not directory.is_dir():
        parser.error("job does not exist")
    report = {"job_id": args.job_id}
    for source, key in (("status.json", "job"), ("job.json", "job"), ("resources.json", "resources")):
        file = directory / source
        if file.is_file():
            report[key] = json.loads(file.read_text(encoding="utf-8"))
    if "resources" not in report and "job" not in report:
        parser.error("no persisted job data")
    if args.samples > 0:
        sample_file = directory / "resource-samples.jsonl"
        if sample_file.is_file():
            # Read sequentially with bounded memory even for long renders.
            from collections import deque
            with sample_file.open(encoding="utf-8") as file:
                report["samples"] = [json.loads(line) for line in deque(file, maxlen=min(args.samples, 1000))]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
