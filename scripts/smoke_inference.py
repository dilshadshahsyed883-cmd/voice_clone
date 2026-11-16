#!/usr/bin/env python3
"""Real-server OmniVoice WAV smoke; requires actual ready CUDA model."""
from __future__ import annotations
import argparse
import base64
import io
import json
import os
import time
import urllib.request
import wave
from pathlib import Path

def call(base, path, token, payload=None, binary=False):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    with urllib.request.urlopen(urllib.request.Request(
        base.rstrip("/") + path, data=body, headers=headers,
        method="POST" if body is not None else "GET"), timeout=60) as response:
        data = response.read()
    return data if binary else json.loads(data)

def main(argv=None):
    p = argparse.ArgumentParser(description="Validate real OmniVoice GPU WAV and resource telemetry")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--token", default=os.getenv("OMNIVOICE_API_TOKEN", ""))
    p.add_argument("--mode", choices=("auto", "clone"), default="auto")
    p.add_argument("--ref-wav", type=Path)
    p.add_argument("--ref-text")
    p.add_argument("--language", choices=("hi", "mr", "gu", "bn", "arb"), default="hi")
    p.add_argument("--text", default="यह आवाज़ निर्माण प्रणाली का परीक्षण है।")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--output", type=Path, default=Path("omnivoice-smoke-output.wav"))
    p.add_argument("--report", type=Path, default=Path("omnivoice-smoke-report.json"))
    args = p.parse_args(argv)
    if args.mode == "clone" and (not args.ref_wav or not args.ref_text):
        p.error("clone requires --ref-wav and --ref-text")
    if args.timeout < 1:
        p.error("--timeout must be positive")
    ready = call(args.base_url, "/ready", args.token)
    diagnostics = call(args.base_url, "/runtime", args.token)
    if not ready.get("ready") or not diagnostics.get("gpu", {}).get("cuda_available"):
        raise RuntimeError("CUDA model not ready; no inference attempted")
    payload = {"text": args.text, "language": args.language, "mode": args.mode}
    if args.mode == "clone":
        payload.update(ref_text=args.ref_text, ref_audio_b64=base64.b64encode(args.ref_wav.read_bytes()).decode("ascii"))
    job_id = call(args.base_url, "/v1/jobs", args.token, payload)["job_id"]
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        record = call(args.base_url, "/v1/jobs/" + job_id, args.token)
        if record["state"] in ("completed", "failed"):
            break
        time.sleep(2)
    else:
        raise TimeoutError("Job timeout: " + job_id)
    if record["state"] != "completed":
        raise RuntimeError("GPU generation failed: " + str(record.get("error")))
    audio = call(args.base_url, "/v1/jobs/" + job_id + "/audio", args.token, binary=True)
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnframes() < wav.getframerate() // 20:
            raise RuntimeError("Invalid real PCM16 output")
        duration = wav.getnframes() / wav.getframerate()
    if not (record.get("result") or {}).get("synthesis_seconds"):
        raise RuntimeError("Missing measured render time")
    usage = call(args.base_url, "/v1/jobs/" + job_id + "/resources", args.token)
    report = {"verified": True, "job_id": job_id, "duration_seconds": duration,
              "result": record["result"], "resources": usage.get("resources")}
    args.output.write_bytes(audio)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
