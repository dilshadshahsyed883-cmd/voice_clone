from pathlib import Path
import numpy as np
import pytest
import soundfile as sf

from app.engine import split_text, validate_reference


def test_chunks_keep_order_and_size():
    text = ("A useful narration line. " * 80).strip()
    chunks = split_text(text, 110)
    assert len(chunks) > 2
    assert all(len(c) <= 110 for c in chunks)
    assert " ".join(chunks) == text


def test_reject_blank_text():
    with pytest.raises(ValueError):
        split_text("  ", 100)


def test_reference_wav_validation(tmp_path: Path):
    path = tmp_path / "voice.wav"
    sf.write(path, np.zeros(24000 * 3, dtype=np.float32), 24000)
    info = validate_reference(path)
    assert info["sample_rate"] == 24000
    assert info["duration_seconds"] == 3.0


def test_reject_too_short_reference(tmp_path: Path):
    path = tmp_path / "short.wav"
    sf.write(path, np.zeros(24000, dtype=np.float32), 24000)
    with pytest.raises(ValueError, match="2.5 to 30"):
        validate_reference(path)
