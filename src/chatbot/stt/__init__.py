"""음성인식(STT) 백엔드."""

from __future__ import annotations

from ..config import SttConfig
from .base import SttBackend, Transcription
from .dummy import DummyStt

__all__ = ["SttBackend", "Transcription", "DummyStt", "create_stt"]


def create_stt(cfg: SttConfig) -> SttBackend:
    if cfg.backend == "dummy":
        return DummyStt()
    if cfg.backend == "faster-whisper":
        from .faster_whisper_stt import FasterWhisperStt

        return FasterWhisperStt(
            model=cfg.model,
            device=cfg.device,
            compute_type=cfg.compute_type,
            language=cfg.language,
            beam_size=cfg.beam_size,
        )
    raise ValueError(f"알 수 없는 stt.backend: {cfg.backend!r}")
