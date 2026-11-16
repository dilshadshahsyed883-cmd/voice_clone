# Shared Salad container architecture — Voice Clone

Applies to `dilshadshahsyed883-cmd/voice_clone` branch `engine/qwen3-tts-0.6b` (model). This model publishes `ghcr.io/dilshadshahsyed883-cmd/voice_clone:qwen3-tts-0.6b-<short-7-character-commit-sha>`. This is the **existing** GHCR workflow tag; validate that the tag exists, and prefer a verified digest when pinning a container.

**Fixed architecture:** Imagen, HeyGen and Voice Clone are **three separate repositories**, each with its **one existing VPS_1 web control** and **one existing Salad GPU container**. The Voice Clone control and Salad container are stable across all Voice Clone model branches. Never create new controls/containers for a model, cross-connect projects or move their user jobs/media.

**Switch mode:** After publishing and real-GPU functional validation, finish active jobs and *explicitly* replace **only the Docker image reference** in the *existing Voice Clone Salad container*. Keep current network URL, TCP port 8000, authenticated secrets, GPU/CPU/memory resources, persistent storage, job history and VPS controls. Check API and adapter compatibility: Qwen and OmniVoice job endpoints/payloads differ, so an adapter or matching implementation must pass before a user-facing model switch. Validate health and readiness, real inference output and resource telemetry. Retain previous working image digest for rollback. Pushes to GitHub **never** automatically mutate Salad deployments; no `:latest` overwrites or main merges are required.

**Machine-readable contract:** `deployment/image-contract.json` identifies the one web-control slot and one Salad-container slot. `scripts/resolve_salad_image.py` enforces the repo, branch, GHCR tag/workflow mapping, and is a **read-only image resolver**. CI in `.github/workflows/deployment-contract.yml` validates this. The tooling never contacts Salad and never embeds credentials or customer media.

```bash
python3 scripts/resolve_salad_image.py --validate --branch 'engine/qwen3-tts-0.6b'
python3 scripts/resolve_salad_image.py --sha FULL_40_CHARACTER_SHA --json
```

**Production gates:** verify build and published image, API/auth/job compatibility, actual GPU inference success and output quality, then apply manually with guarded rollback. Docs/CI alone do not prove production readiness.
