"""faster-whisper 로 한국어 음성인식.

GPU 가 있으면 자동으로 쓴다. CPU 만 있으면 small 모델까지가 현실적이다.
"""

from __future__ import annotations

import asyncio
import logging

from ..audio.vad import Segment
from .base import Transcription

log = logging.getLogger(__name__)

# Whisper 가 무음/배경음악 구간에서 자주 지어내는 문장들.
# 이게 그대로 채팅으로 나가면 굉장히 뜬금없어서 걸러낸다.
HALLUCINATIONS = (
    "시청해주셔서 감사합니다",
    "구독과 좋아요",
    "구독 좋아요",
    "좋아요와 구독",
    "감사합니다 시청",
    "이 영상은",
    "자막 제공",
    "자막",
    "mbc 뉴스",
    "kbs 뉴스",
    "sbs 뉴스",
    "다음 영상에서",
    "한글자막",
    "thanks for watching",
    "subscribe",
)

MIN_CHARS = 2
MAX_NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.1

# 웃음/감탄은 길게 반복돼도 정상이고, 봇이 반응하기 좋은 신호다.
LAUGHTER_CHARS = set("ㅋㅎㅏㅓㅜㅠ아어오우으하허흐~!?.")


def _looks_hallucinated(text: str) -> bool:
    lowered = text.lower().replace(" ", "")
    for phrase in HALLUCINATIONS:
        if phrase.replace(" ", "") in lowered:
            return True
    # 긴 글자 반복은 거의 환각이다. 단 웃음소리는 예외.
    chars = set(text.replace(" ", ""))
    if len(text.strip()) >= 12 and len(chars) <= 2 and not chars <= LAUGHTER_CHARS:
        return True
    return False


class FasterWhisperStt:
    def __init__(
        self,
        model: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        language: str = "ko",
        beam_size: int = 1,
    ) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._beam_size = beam_size
        self._model: object | None = None
        self._load_lock = asyncio.Lock()

    async def _ensure_model(self) -> object:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            self._model = await asyncio.to_thread(self._load)
        return self._model

    def _load(self) -> object:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - 환경 문제
            raise RuntimeError(
                "faster-whisper 가 설치되지 않았습니다.\n"
                "  pip install faster-whisper\n"
                "모델 없이 테스트하려면 config.yaml 에서 stt.backend: dummy 로 바꾸세요."
            ) from exc

        log.info(
            "Whisper 모델 로딩 중 (%s, device=%s)… 처음이면 다운로드에 시간이 걸립니다",
            self._model_name,
            self._device,
        )
        model = WhisperModel(
            self._model_name,
            device=self._device,
            compute_type=self._compute_type,
        )
        log.info("Whisper 모델 준비 완료")
        return model

    async def transcribe(self, segment: Segment) -> Transcription | None:
        model = await self._ensure_model()
        try:
            return await asyncio.to_thread(self._transcribe_sync, model, segment)
        except Exception:  # noqa: BLE001 - 한 구간 실패로 봇이 죽지 않게
            log.exception("음성인식 실패")
            return None

    def _transcribe_sync(self, model: object, segment: Segment) -> Transcription | None:
        import numpy as np

        audio = (
            np.frombuffer(segment.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        )
        if audio.size == 0:
            return None

        segments, _info = model.transcribe(  # type: ignore[attr-defined]
            audio,
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=True,
            condition_on_previous_text=False,  # 환각 전파를 줄인다
        )

        parts: list[str] = []
        logprobs: list[float] = []
        no_speech: list[float] = []
        for s in segments:
            text = (getattr(s, "text", "") or "").strip()
            if text:
                parts.append(text)
            lp = getattr(s, "avg_logprob", None)
            if lp is not None:
                logprobs.append(float(lp))
            ns = getattr(s, "no_speech_prob", None)
            if ns is not None:
                no_speech.append(float(ns))

        text = " ".join(parts).strip()
        if len(text) < MIN_CHARS:
            return None

        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else None
        max_no_speech = max(no_speech) if no_speech else None

        if max_no_speech is not None and max_no_speech > MAX_NO_SPEECH_PROB:
            log.debug("무음 판정으로 버림 (no_speech=%.2f): %s", max_no_speech, text)
            return None
        if avg_logprob is not None and avg_logprob < MIN_AVG_LOGPROB:
            log.debug("신뢰도 낮아 버림 (logprob=%.2f): %s", avg_logprob, text)
            return None
        if _looks_hallucinated(text):
            log.debug("환각으로 보여 버림: %s", text)
            return None

        return Transcription(
            text=text,
            duration_sec=segment.duration_sec,
            language=self._language,
            avg_logprob=avg_logprob,
            no_speech_prob=max_no_speech,
        )

    async def close(self) -> None:
        self._model = None
