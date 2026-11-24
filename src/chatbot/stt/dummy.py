"""모델 없이 파이프라인만 돌려보기 위한 가짜 STT."""

from __future__ import annotations

import itertools

from ..audio.vad import Segment
from .base import Transcription

SAMPLES = [
    "아 이거 어떻게 하는 거지",
    "오늘 좀 피곤하네 어제 잠을 못 자서",
    "어 이거 봤어요? 방금 완전 아슬아슬했는데",
    "자 그럼 다음 판 가볼까요",
    "채팅창 조용하네 다들 있어요?",
]


class DummyStt:
    def __init__(self) -> None:
        self._cycle = itertools.cycle(SAMPLES)

    async def transcribe(self, segment: Segment) -> Transcription | None:
        return Transcription(
            text=next(self._cycle),
            duration_sec=segment.duration_sec,
        )

    async def close(self) -> None:
        return None
