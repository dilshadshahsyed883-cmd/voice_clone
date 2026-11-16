"""CUDA-only Qwen3-TTS Base voice-clone inference, isolated from OmniVoice."""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

MODEL_DIR = Path(os.getenv("QWEN_MODEL_DIR", "/opt/models/qwen3-tts-0.6b"))
SUPPORTED = ("English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian")


def split_text(text: str, max_chars: int = 300) -> list[str]:
    if not 80 <= max_chars <= 600:
        raise ValueError("chunk_chars must be between 80 and 600")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("text must not be empty")
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = sentence.split()
        for word in words:
            if len(word) > max_chars:
                raise ValueError("one text token exceeds chunk_chars")
            candidate = f"{current} {word}".strip()
            if len(candidate) > max_chars:
                if current:
                    chunks.append(current)
                current = word
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def validate_reference(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("reference audio exceeds 16 MiB")
    try:
        info = sf.info(path)
    except Exception as exc:
        raise ValueError("reference audio must be a readable WAV") from exc
    if info.format != "WAV" or not 8000 <= info.samplerate <= 96000:
        raise ValueError("reference must be WAV with supported sample rate")
    if not 2.5 <= info.duration <= 30:
        raise ValueError("reference clip must be 2.5 to 30 seconds")
    if info.channels not in (1, 2):
        raise ValueError("reference clip must be mono or stereo")
    return {"duration_seconds": round(info.duration, 3), "sample_rate": info.samplerate}


class QwenEngine:
    def __init__(self):
        self.model = None
        self.torch = None
        self.ready = False
        self.loading = False
        self.error = None
        self.model_load_seconds = None
        self.lock = threading.Lock()

    def load(self):
        self.loading = True
        start = time.perf_counter()
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA GPU required; CPU fallback disabled")
            if not (MODEL_DIR / "config.json").is_file():
                raise RuntimeError(f"model weights not bundled: {MODEL_DIR}")
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            self.model = Qwen3TTSModel.from_pretrained(
                str(MODEL_DIR), device_map="cuda:0", dtype=dtype,
                attn_implementation="sdpa",
            )
            self.torch = torch
            self.ready = True
            self.model_load_seconds = round(time.perf_counter() - start, 3)
            self.error = None
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.ready = False
        finally:
            self.loading = False

    def gpu_usage(self):
        if self.torch is None or not self.torch.cuda.is_available():
            return None
        torch = self.torch
        free, total = torch.cuda.mem_get_info()
        return {
            "name": torch.cuda.get_device_name(0),
            "vram_total_mib": round(total / 1048576),
            "vram_free_mib": round(free / 1048576),
            "vram_allocated_mib": round(torch.cuda.memory_allocated() / 1048576),
        }

    def render(self, *, text: str, language: str, ref_audio: Path,
               ref_text: str, chunk_chars: int, output: Path) -> dict:
        if not self.ready:
            raise RuntimeError(self.error or "model not ready")
        chunks = split_text(text, chunk_chars)
        validate_reference(ref_audio)
        if language not in SUPPORTED:
            raise ValueError(f"unsupported language: {language}")
        with self.lock:
            started = time.perf_counter()
            prompt = self.model.create_voice_clone_prompt(
                ref_audio=str(ref_audio), ref_text=ref_text,
                x_vector_only_mode=False,
            )
            samples: list[np.ndarray] = []
            sample_rate = None
            for i, part in enumerate(chunks):
                waves, sr = self.model.generate_voice_clone(
                    text=part, language=language,
                    voice_clone_prompt=prompt,
                    non_streaming_mode=True,
                )
                if sample_rate is not None and sample_rate != int(sr):
                    raise RuntimeError("model sample rate changed across chunks")
                sample_rate = int(sr)
                wave = np.asarray(waves[0], dtype=np.float32).reshape(-1)
                if wave.size == 0:
                    raise RuntimeError(f"empty generated audio in chunk {i+1}")
                samples.append(wave)
                if i < len(chunks) - 1:
                    samples.append(np.zeros(round(sample_rate * 0.18), dtype=np.float32))
            full = np.concatenate(samples)
            output.parent.mkdir(parents=True, exist_ok=True)
            sf.write(output, full, sample_rate, subtype="PCM_16")
            generation_seconds = time.perf_counter() - started
        duration = len(full) / sample_rate
        return {
            "chunk_count": len(chunks),
            "sample_rate": sample_rate,
            "audio_duration_seconds": round(duration, 3),
            "generation_seconds": round(generation_seconds, 3),
            "realtime_factor": round(generation_seconds / duration, 4),
            "speed_factor": round(duration / generation_seconds, 3),
        }


engine = QwenEngine()
