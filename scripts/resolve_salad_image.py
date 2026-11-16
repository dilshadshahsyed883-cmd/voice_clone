#!/usr/bin/env python3
"""Validate branch-scoped image selection without touching live Salad resources."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "imagen": ("dilshadshahsyed883-cmd/Imagen", "vps1:imagen", "salad:imagen"),
    "heygen": ("dilshadshahsyed883-cmd/heygen", "vps1:heygen", "salad:heygen"),
    "voice_clone": ("dilshadshahsyed883-cmd/voice_clone", "vps1:voice_clone", "salad:voice_clone"),
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true", help="validate on-disk branch contract")
    parser.add_argument("--branch", help="expected exact git branch name")
    parser.add_argument("--sha", help="full 40-character source commit SHA to resolve")
    parser.add_argument("--json", action="store_true", help="print resolved image as JSON")
    args = parser.parse_args()
    cfg = json.loads((ROOT / "deployment" / "image-contract.json").read_text())
    assert cfg.get("version") == 1 and cfg.get("policy") == "one-stable-web-control-and-one-stable-salad-container-per-repository"
    project = cfg["project"]
    repo, web, salad = PROJECTS[project]
    assert (cfg["repository"], cfg["web_control_slot"], cfg["salad_container_slot"]) == (repo, web, salad)
    assert cfg["container_port"] == 8000 and cfg["health_path"] == "/health"
    assert cfg["deployment_mode"] == "explicit_image_swap_only"
    assert cfg["branch_role"] in ("index", "model", "feature")
    assert isinstance(cfg["source_branch"], str) and cfg["source_branch"]
    if args.branch and args.branch != cfg["source_branch"]:
        parser.error("branch mismatch; refusing cross-branch image selection")
    image = cfg["image"]
    if cfg["branch_role"] == "model":
        assert isinstance(image, dict) and cfg["engine"], "model branch must define an image"
        assert image["repository"] == "ghcr.io/" + repo.lower(), "cross-project image prohibited"
        assert re.fullmatch(r"[a-z0-9][a-z0-9.-]*-", image["tag_prefix"]), "invalid tag prefix"
        assert image["sha_chars"] in (7, 40)
        workflow = ROOT / image["workflow"]
        assert workflow.is_file(), "image-publishing workflow missing"
        yaml = workflow.read_text()
        assert cfg["source_branch"] in yaml and image["tag_prefix"] in yaml, "workflow tag or branch mismatch"
    else:
        assert image is None and cfg["engine"] is None, "non-model branch cannot auto-deploy an image"
    if args.sha:
        if cfg["branch_role"] != "model":
            parser.error("index/feature branch has no independently published model image")
        sha = args.sha.lower()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            parser.error("--sha must be a full 40-character commit SHA")
        ref = image["repository"] + ":" + image["tag_prefix"] + sha[:image["sha_chars"]]
        result = {"project": project, "source_branch": cfg["source_branch"], "image": ref,
                  "web_control_slot": web, "salad_container_slot": salad,
                  "switch_requires_manual_approval_and_gpu_validation": True}
        print(json.dumps(result, sort_keys=True) if args.json else ref)
    elif args.validate:
        print("PASS: branch image contract", repo, cfg["source_branch"])
    else:
        parser.error("pass --validate, --sha FULL_SHA, or both")

if __name__ == "__main__":
    main()
