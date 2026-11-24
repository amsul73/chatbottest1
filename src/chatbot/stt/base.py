"""STT 백엔드 인터페이스."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..audio.vad import Segment


@dataclass(frozen=True)
class Transcription:
    text: str
    duration_sec: float
    language: str = "ko"
    # 신뢰도 관련(백엔드가 주면 채워진다)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


@runtime_checkable
class SttBackend(Protocol):
    async def transcribe(self, segment: Segment) -> Transcription | None:
        """구간을 글자로. 쓸 만한 결과가 없으면 None."""
        ...

    async def close(self) -> None: ...
