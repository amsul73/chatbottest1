"""'언제' 채팅을 칠지 정하는 부분.

봇이 미움받는 가장 빠른 길은 도배다. 그래서 '무슨 말을 할까'보다
'지금 말해도 되나'를 먼저, 그리고 보수적으로 판단한다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ..config import DirectorConfig
from .transcript import ChatActivity, TranscriptBuffer

# STT 결과에는 물음표가 자주 빠진다. 그래서 어미로도 질문을 찾는다.
QUESTION_PATTERNS = (
    r"\?",
    # 물음표 없이 끝나는 의문형 어미
    r"(까요|을까|ㄹ까|나요|가요|래요|죠|지요)\s*$",
    r"(거지|건지|는지|을지|ㄹ지)\s*$",
    r"(맞나|되나|인가|일까|있나|없나)\s*$",
    # 어디에 있어도 질문으로 볼 표현
    r"(맞지|그치|안\s*그래|어때|어떄|어떻게|어떡|어쩌지)",
    r"(뭐지|뭘까|뭐임|뭐야|왜지|누구지|언제지)",
    # 의문사로 시작하면 물음표가 없어도 질문인 경우가 많다
    r"(^|\s)왜(\s|$)",
    r"(^|\s)(누가|언제|어디|몇|무슨)\s",
    r"(있어요|있나요|봤어요|봤나|아세요|아시는|해볼까|할까)",
)

# 스트리머가 시청자를 부르는 말 → 반응해주면 좋은 순간
ADDRESS_PATTERNS = (
    r"(채팅|챗창|챗)",
    r"(여러분|다들|니네|너네|얘들아|친구들)",
    r"(시청자|보고\s*있는\s*사람|누구\s*있어)",
)

REACTION_PATTERNS = (
    r"(ㅋ{2,}|ㅎ{2,}|ㅠ{2,}|ㅜ{2,})",
    r"(대박|헐|미친|와\s|우와|진짜|레전드|개잘|아\s*이거)",
    r"(아\s*아깝|아쉽|다행|살았|죽었|이겼|졌)",
)

_QUESTION_RE = [re.compile(p) for p in QUESTION_PATTERNS]
_ADDRESS_RE = [re.compile(p) for p in ADDRESS_PATTERNS]
_REACTION_RE = [re.compile(p) for p in REACTION_PATTERNS]


@dataclass(frozen=True)
class SpeakDecision:
    speak: bool
    trigger: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.speak


def classify(text: str) -> str | None:
    """발화에서 반응할 거리를 찾는다. 없으면 None."""
    if not text.strip():
        return None
    if any(r.search(text) for r in _QUESTION_RE):
        return "question"
    if any(r.search(text) for r in _ADDRESS_RE):
        return "address_chat"
    if any(r.search(text) for r in _REACTION_RE):
        return "reaction"
    return None


TRIGGER_LABEL = {
    "question": "스트리머가 질문을 던졌다. 짧게 대답해줘.",
    "address_chat": "스트리머가 채팅창/시청자를 불렀다. 반응해줘.",
    "reaction": "스트리머가 크게 반응했다(웃음·감탄). 같이 맞장구쳐줘.",
    "lull": "스트리머가 한동안 말이 없다. 가볍게 말 걸어줘.",
    "flow": "방송 내용이 좀 쌓였다. 자연스럽게 한마디 거들어줘.",
}


class Director:
    def __init__(self, cfg: DirectorConfig) -> None:
        self.cfg = cfg
        self._started_at = time.monotonic()
        self._last_sent_at: float | None = None
        self._last_attempt_at: float | None = None
        self._sent_times: list[float] = []

    # ------------------------------------------------------------------
    def note_sent(self, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._last_sent_at = now
        self._last_attempt_at = now
        self._sent_times.append(now)
        self._sent_times = [t for t in self._sent_times if t > now - 600]

    def note_attempt(self, now: float | None = None) -> None:
        """Claude 에게 물어본 시점. 안 보내기로 했어도 기록한다.

        이게 없으면 '스트리머가 조용함' 같은 조건이 계속 참이라서
        2초마다 API 를 두드린다.
        """
        self._last_attempt_at = now if now is not None else time.monotonic()

    def reset_clock(self, now: float | None = None) -> None:
        """방송에 새로 붙었을 때 시작 유예를 다시 준다."""
        self._started_at = now if now is not None else time.monotonic()

    def sent_last_10min(self, now: float | None = None) -> int:
        now = now if now is not None else time.monotonic()
        return sum(1 for t in self._sent_times if t > now - 600)

    def required_interval(
        self, chat: ChatActivity, now: float | None = None
    ) -> float:
        """사람들이 활발하면 봇은 간격을 더 벌린다."""
        base = self.cfg.min_interval_sec
        backoff = self.cfg.human_chat_backoff
        if chat.count(now) >= backoff.messages:
            return base * backoff.interval_multiplier
        return base

    # ------------------------------------------------------------------
    def decide(
        self,
        transcript: TranscriptBuffer,
        chat: ChatActivity,
        now: float | None = None,
    ) -> SpeakDecision:
        now = now if now is not None else time.monotonic()

        # 1. 켜고 나서 잠깐은 듣기만 한다(맥락 없이 끼어들지 않게)
        if now - self._started_at < self.cfg.startup_grace_sec:
            return SpeakDecision(False, detail="시작 직후 대기 중")

        # 2. 10분 한도
        if self.sent_last_10min(now) >= self.cfg.max_per_10min:
            return SpeakDecision(False, detail="10분 전송 한도 도달")

        # 3. 최소 간격
        interval = self.required_interval(chat, now)
        if self._last_sent_at is not None and now - self._last_sent_at < interval:
            left = interval - (now - self._last_sent_at)
            return SpeakDecision(False, detail=f"쿨타임 {left:.0f}초 남음")

        # 3-b. 안 보내기로 한 경우에도 너무 자주 물어보지 않는다
        if (
            self._last_attempt_at is not None
            and now - self._last_attempt_at < self.cfg.attempt_interval_sec
        ):
            return SpeakDecision(False, detail="직전 판단 후 대기 중")

        # 들은 게 아예 없으면 할 말이 없다
        if transcript.is_empty:
            return SpeakDecision(False, detail="아직 들은 말이 없음")

        pending = transcript.unreacted()
        pending_text = " ".join(u.text for u in pending)

        # 4. 반응할 거리가 있나
        trigger = classify(pending_text) if pending_text else None
        if trigger:
            return SpeakDecision(True, trigger, TRIGGER_LABEL[trigger])

        # 5. 스트리머가 조용해졌다 → 말 걸어주기
        silent_for = transcript.seconds_since_last(now)
        if silent_for is not None and silent_for >= self.cfg.lull_sec:
            return SpeakDecision(True, "lull", TRIGGER_LABEL["lull"])

        # 6. 특별한 건 없지만 내용이 충분히 쌓였다
        if transcript.unreacted_chars() >= self.cfg.min_new_chars:
            return SpeakDecision(True, "flow", TRIGGER_LABEL["flow"])

        return SpeakDecision(
            False,
            detail=f"반응할 거리가 없음 (새 내용 {transcript.unreacted_chars()}자)",
        )
