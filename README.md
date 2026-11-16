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
