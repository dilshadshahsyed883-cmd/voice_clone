FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    QWEN_MODEL_DIR=/opt/models/qwen3-tts-0.6b \
    QWEN_JOB_DIR=/data/jobs \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 libsox-dev sox ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Do not install flash-attn from source during image builds: SDPA is used
# for reproducible CUDA operation without GPU architecture-specific wheels.
RUN python -m pip install --no-cache-dir \
    qwen-tts==0.1.1 \
    fastapi==0.115.12 \
    uvicorn==0.34.2 \
    soundfile==0.13.1 \
    numpy==1.26.4

# Bake complete 0.6B Base model AND its speech_tokenizer assets into the image.
# Inference needs no Hugging Face network access.
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 python - <<'PY'
from huggingface_hub import snapshot_download
from pathlib import Path
folder = Path('/opt/models/qwen3-tts-0.6b')
snapshot_download(repo_id='Qwen/Qwen3-TTS-12Hz-0.6B-Base', local_dir=str(folder))
for relative in ('config.json', 'speech_tokenizer/config.json'):
    assert (folder / relative).is_file(), f'missing model asset: {relative}'
print('Qwen3-TTS-0.6B weights packaged locally')
PY

COPY app /app/app
RUN mkdir -p /data/jobs && chmod 700 /data/jobs
EXPOSE 8000
HEALTHCHECK NONE
LABEL org.opencontainers.image.source="https://github.com/dilshadshahsyed883-cmd/voice_clone" \
      ai.engine="qwen3-tts-12hz-0.6b-base" \
      ai.model.license="Apache-2.0"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "::", "--port", "8000", "--workers", "1"]
