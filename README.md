# Voice Clone

Repository index and deployment contracts for Voice Clone model runtimes.

**Branch role:** `main` is the index branch. It validates fixed web-control and Salad container slots; it does not include an inference server, Dockerfile or model weights and does not publish a model image.

## Validate the index locally

Python 3.11 or newer is sufficient; the validator uses only the standard library.

```bash
python3 scripts/resolve_salad_image.py --validate --branch main
```

The index has no independently published image, so resolving `--sha` on this branch is intentionally rejected. Model/runtime instructions belong to their engine/build branches. At the source review on 21 November 2026 these included `build/omnivoice-runtime-v0.1.0` and `engine/qwen3-tts-0.6b`; check their own source, dependencies and workflow before use.

## Architecture and runtime requirements

See [the shared architecture](docs/SHARED_SALAD_ARCHITECTURE.md) and [machine-readable contract](deployment/image-contract.json). Each model uses the existing Voice Clone web control and Salad container. Select GPU resources and model dependencies from the chosen runtime branch; this index does not specify tested VRAM or certify live inference.

A model switch requires compatible API/auth/job handling, real GPU inference validation and the previous working image digest for rollback. GitHub pushes do not automatically replace the deployed image. Keep credentials, voice samples and generated user audio out of source and CI artifacts.

## Repository maintenance

See the [repository guide](docs/REPOSITORY_GUIDE.md) for source ownership, validation and release/storage policy, and [CONTRIBUTING.md](CONTRIBUTING.md) for changes and review.
