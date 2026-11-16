#!/usr/bin/env python3
"""Real-server clone smoke test. Requires a running GPU model; never simulates inference."""
from __future__ import annotations
import argparse
import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path


def request(base, path, token, payload=None, binary=False):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    body = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
        method = "POST"
    req = urllib.request.Request(base.rstrip("/") + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as response:
        content = response.read()
    return content if binary else json.loads(content)


def main(argv=None):
    p = argparse.ArgumentParser(description="Submit a real Qwen voice-clone job and validate WAV output")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--ref-wav", type=Path, required=True, help="2.5-30s WAV of a consented voice")
    p.add_argument("--ref-text", required=True, help="Exact transcript of the reference WAV")
    p.add_argument("--text", default="This is a real voice cloning inference readiness test.")
    p.add_argument("--language", default="English")
    p.add_argument("--token", default=os.getenv("QWEN_API_TOKEN", ""))
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--report", type=Path, default=Path("qwen-smoke-report.json"))
    p.add_argument("--output", type=Path, default=Path("qwen-smoke-output.wav"))
    args = p.parse_args(argv)
    if not args.token:
        p.error("QWEN_API_TOKEN or --token is required")
    if args.timeout < 1:
        p.error("--timeout must be positive")
    readiness = request(args.base_url, "/ready", args.token)
    if not readiness.get("ready"):
        raise RuntimeError("GPU model is not ready; no inference attempted")
    submitted = request(args.base_url, "/v1/jobs", args.token, {
        "text": args.text, "language": args.language, "ref_text": args.ref_text,
        "ref_audio_b64": base64.b64encode(args.ref_wav.read_bytes()).decode("ascii"),
    })
    job_id = submitted["job_id"]
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        job = request(args.base_url, "/v1/jobs/" + job_id, args.token)
        if job["state"] in {"completed", "failed"}:
            break
        time.sleep(2)
    else:
        raise TimeoutError("Real inference job did not finish before timeout: " + job_id)
    if job["state"] != "completed":
        raise RuntimeError("Real inference failed: " + str(job.get("error")))
    audio = request(args.base_url, "/v1/jobs/" + job_id + "/audio", args.token, binary=True)
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if wav.getnchannels() not in (1, 2) or wav.getsampwidth() != 2:
            raise RuntimeError("Returned output is not PCM16 WAV audio")
        if wav.getframerate() < 8000 or wav.getnframes() < wav.getframerate() // 20:
            raise RuntimeError("Output WAV is silent-length or invalid sample rate")
        seconds = wav.getnframes() / wav.getframerate()
    metrics = job.get("metrics") or {}
    if not metrics.get("generation_seconds") or not metrics.get("audio_duration_seconds"):
        raise RuntimeError("Missing measured generation performance")
    resources = request(args.base_url, "/v1/jobs/" + job_id + "/resources", args.token)
    report = {"verified": True, "job_id": job_id, "state": job["state"],
              "output_wav_seconds": round(seconds, 3), "metrics": metrics,
              "resources": resources.get("resources"), "resource_capture": resources.get("state")}
    args.output.write_bytes(audio)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc
