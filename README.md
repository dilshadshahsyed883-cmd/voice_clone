# Qwen3-TTS 0.6B GPU voice-clone engine

Dedicated `engine/qwen3-tts-0.6b` branch for English and multilingual
voice cloning. **Separate from OmniVoice**; no merge to `main` required.

- Model: `Qwen/Qwen3-TTS-12Hz-0.6B-Base` (Apache-2.0)
- Package: `qwen-tts==0.1.1` (upstream)
- Runtime: single-worker FastAPI with CUDA-only model inference
- Model files: baked into the image (including `speech_tokenizer/`), offline at runtime
- GPU: CUDA 12.8 PyTorch 2.8 base, SDPA (no FlashAttention build required)
- Long-form narration: split into bounded text chunks, generate with reusable
  cloning prompt, concatenate WAV with 180 ms separator
- Metrics: audio duration, GPU generation seconds, real-time factor and speed factor
- Persistent job metadata and restart recovery, single in-flight inference

## CI and GHCR

Pushing to this branch starts unit tests and a separate GHCR image build.
The build publishes **branch-only** image tags (never `latest`):

```text
ghcr.io/dilshadshahsyed883-cmd/voice_clone:qwen3-tts-0.6b-candidate
ghcr.io/dilshadshahsyed883-cmd/voice_clone:qwen3-tts-0.6b-<commit-sha>
```

Do not use for production until the GHCR workflow succeeds and a real
Salad GPU generation qualifies speed and audio quality.

## Salad container

Set image to the candidate tag; expose TCP port 8000, allocate an NVIDIA CUDA
GPU and configure `QWEN_API_TOKEN` as a secret environment variable.
No secrets are committed. Set a persistent volume on `/data/jobs` if
jobs and result audio must survive container replacement.

```bash
curl -fsS http://YOUR_WORKER:8000/health
curl -fsS http://YOUR_WORKER:8000/ready
```

`/ready` returns HTTP 503 until the GPU model has loaded. There is **no
CPU fallback**.

## Generate a cloned English narration

Use an authorized clean 2.5–30 second WAV reference and its exact transcript.
Do not clone other people's voices without their consent.

```python
import base64, json, urllib.request
ref = base64.b64encode(open('voice-reference.wav','rb').read()).decode()
payload = {
  'text': 'This is a narrated story about a remarkable discovery.',
  'language': 'English',
  'ref_audio_b64': ref,
  'ref_text': 'The exact spoken words in the reference clip.',
  'chunk_chars': 300
}
req = urllib.request.Request(
  'http://YOUR_WORKER:8000/v1/jobs',
  data=json.dumps(payload).encode(),
  headers={'Authorization': 'Bearer YOUR_QWEN_API_TOKEN',
           'Content-Type': 'application/json'},
  method='POST'
)
print(urllib.request.urlopen(req).read().decode())
```

Poll `GET /v1/jobs/{job_id}`, then download
`GET /v1/jobs/{job_id}/audio` with the bearer token.

## Health and interfaces

| Route | Purpose |
|---|---|
| `GET /health` | Service alive |
| `GET /ready` | GPU model loaded or HTTP 503 |
| `GET /runtime` | Authenticated GPU memory and load metrics |
| `POST /v1/jobs` | Submit a voice-cloning job |
| `GET /v1/jobs/{id}` | Job progress and speed metrics |
| `GET /v1/jobs/{id}/audio` | Download completed WAV |

## Known limitations

A unit-test pass or pushed image **does not** prove GPU audio quality or
generation speed. Run real 30-second and 5-minute tests on the target Salad GPU
and inspect output before a production deployment. Endpoints are not compatible
with the old OmniVoice worker without an adapter.
