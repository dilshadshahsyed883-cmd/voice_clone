"""CPU-only fake inference: contract test, NOT evidence of a live GPU/model."""
import numpy as np
import pytest
import soundfile as sf
from app.engine import QwenEngine


class FakeModel:
    def __init__(self):
        self.prompts = []
        self.calls = []

    def create_voice_clone_prompt(self, **kwargs):
        self.prompts.append(kwargs)
        return {"fake": "prompt"}

    def generate_voice_clone(self, **kwargs):
        self.calls.append(kwargs)
        return [np.ones(2400, dtype=np.float32) * 0.05], 24000


def test_reusable_prompt_multi_chunk_wav(tmp_path):
    reference = tmp_path / "reference.wav"
    output = tmp_path / "output.wav"
    sf.write(reference, np.ones(24000 * 3, dtype=np.float32) * 0.02, 24000)
    fake = FakeModel()
    eng = QwenEngine()
    eng.model, eng.ready = fake, True
    text = ("This example is spoken with a consented voice. " * 10).strip()
    metrics = eng.render(text=text, language="English", ref_audio=reference,
                         ref_text="Consent reference transcript", chunk_chars=100, output=output)
    assert len(fake.prompts) == 1
    assert len(fake.calls) == metrics["chunk_count"] > 1
    assert all(call["voice_clone_prompt"] == {"fake": "prompt"} for call in fake.calls)
    assert sf.info(output).samplerate == 24000
    assert metrics["audio_duration_seconds"] > 0
    assert metrics["generation_seconds"] > 0
    assert metrics["speed_factor"] > 0


def test_render_rejects_unsupported_language_before_generation(tmp_path):
    reference = tmp_path / "reference.wav"
    sf.write(reference, np.zeros(24000 * 3), 24000)
    eng = QwenEngine()
    eng.ready, eng.model = True, FakeModel()
    with pytest.raises(ValueError, match="unsupported language"):
        eng.render(text="hello", language="not-a-language",
                   ref_audio=reference, ref_text="hello",
                   chunk_chars=100, output=tmp_path / "out.wav")
