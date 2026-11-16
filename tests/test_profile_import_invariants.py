"""Imported profile metadata cannot spoof computed identity or persist stale design metadata."""
import hashlib
import json
import sys
from types import SimpleNamespace
from app.runtime import OmniRuntime
import app.runtime as module


def test_replace_import_discards_stale_and_spoofed_metadata(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    voices.mkdir()
    monkeypatch.setattr(module, "settings", SimpleNamespace(voices_dir=voices))
    monkeypatch.setitem(sys.modules, "omnivoice", SimpleNamespace(
        VoiceClonePrompt=SimpleNamespace(load=lambda *args, **kwargs: object())
    ))
    (voices / "test.pt").write_bytes(b"old")
    (voices / "test.json").write_text(json.dumps({"profile_type": "designed", "design_prompt": "obsolete"}))
    payload = tmp_path / "incoming.pt"
    payload.write_bytes(b"replacement profile")
    runtime = OmniRuntime()
    info = runtime.import_voice_profile(
        voice_id="test", source_path=payload, replace=True,
        metadata={"voice_id": "spoof", "sha256": "invalid", "bytes": 1, "profile_type": "imported"},
    )
    assert info["voice_id"] == "test"
    assert info["sha256"] == hashlib.sha256(b"replacement profile").hexdigest()
    assert info["bytes"] == len(b"replacement profile")
    assert "design_prompt" not in info
    assert info["profile_type"] == "imported"
    updated = runtime.import_voice_profile(voice_id="test", source_path=payload, replace=True)
    assert updated["profile_type"] == "imported"
    assert "design_prompt" not in updated
