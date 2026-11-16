# voice-studio-omnivoice-runtime
Self-contained GPU runtime for OmniVoice multilingual voice cloning and TTS, built for Docker/GHCR deployment and remote Voice Studio workers.


## Durable job recovery and diagnostics

Job records are reloaded from disk on restart; interrupted jobs are queued
only when their reference input still exists. Completed/failed job history
remains queryable through `GET /v1/jobs/{job_id}`. Status files are written
atomically; uploaded reference WAVs are deleted after terminal completion.

Inspect preserved CPU, RAM, disk IO, GPU utilization and VRAM from the
container terminal (including after the job finishes):

```bash
python -m app.job_telemetry YOUR_JOB_ID --root /tmp/omnivoice/jobs --json
python -m app.job_telemetry YOUR_JOB_ID --root /tmp/omnivoice/jobs --follow
```

Use `--root "$OMNIVOICE_JOBS_DIR"` when the job volume is mounted elsewhere.
GPU readings are device-wide, while CPU/RAM/IO readings are container-wide.

## Real model smoke and versioned images

Versioned GHCR tags use `omnivoice-profiles-v0.1.3-<sha7>` and never
modify the live Salad container automatically. The HTTP service accepts
`/health`, `/ready`, `/runtime` alongside legacy `/healthz`,
`/readyz`, `/runtimez`. Per-job telemetry is available from
`GET /v1/jobs/{job_id}/resources`, the job status payload and
`python -m app.job_telemetry JOB_ID --json`.

On a real GPU worker, run `python scripts/smoke_inference.py --help` for
options. A successful CLI run requires CUDA, a ready model and valid WAV
output; passing CPU-only CI cannot establish GPU readiness.

Set `OMNIVOICE_JOBS_DIR`, `OMNIVOICE_VOICES_DIR` and
`OMNIVOICE_DESIGN_PREVIEWS_DIR` to existing persistent storage paths
when configured in Salad. Defaults remain unchanged to protect prior data.
