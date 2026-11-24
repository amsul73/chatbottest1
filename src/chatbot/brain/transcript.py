"""들린 말과 채팅창 분위기를 들고 있는 버퍼."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Utterance:
    """스트리머가 한 말 한 덩어리."""

    text: str
    at: float  # time.monotonic()


class TranscriptBuffer:
    """최근 N초치 발화를 들고 있는다.

    `mark()` 는 "여기까지는 이미 반응했다"는 표시다. 봇이 채팅을 보낸 뒤
    호출해서, 같은 말에 두 번 반응하지 않게 한다.
    """

    def __init__(self, window_sec: float = 180.0, max_items: int = 80) -> None:
        self.window_sec = window_sec
        self._items: deque[Utterance] = deque(maxlen=max_items)
        self._marked_at: float = 0.0

    def add(self, text: str, now: float | None = None) -> Utterance | None:
        text = text.strip()
        if not text:
            return None
        utterance = Utterance(text=text, at=now if now is not None else time.monotonic())
        self._items.append(utterance)
        return utterance

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._items and self._items[0].at < cutoff:
            self._items.popleft()

    def recent(self, seconds: float, now: float | None = None) -> list[Utterance]:
        now = now if now is not None else time.monotonic()
        self._prune(now)
        cutoff = now - seconds
        return [u for u in self._items if u.at >= cutoff]

    def recent_text(self, seconds: float, now: float | None = None) -> str:
        return " ".join(u.text for u in self.recent(seconds, now))

    @property
    def is_empty(self) -> bool:
        return not self._items

    @property
    def last_at(self) -> float | None:
        return self._items[-1].at if self._items else None

    def seconds_since_last(self, now: float | None = None) -> float | None:
        last = self.last_at
        if last is None:
            return None
        now = now if now is not None else time.monotonic()
        return now - last

    # ------------------------------------------------------- 반응 지점 표시
    def mark(self, now: float | None = None) -> None:
        self._marked_at = now if now is not None else time.monotonic()

    def unreacted(self) -> list[Utterance]:
        """아직 반응하지 않은 발화들."""
        return [u for u in self._items if u.at > self._marked_at]

    def unreacted_chars(self) -> int:
        return sum(len(u.text) for u in self.unreacted())


class ChatActivity:
    """채팅창이 얼마나 활발한지. 사람들이 떠들고 있으면 봇은 물러난다."""

    def __init__(self, window_sec: float = 60.0, keep: int = 40) -> None:
        self.window_sec = window_sec
        self._items: deque[tuple[float, str, str]] = deque(maxlen=keep)

    def add(self, nickname: str, message: str, now: float | None = None) -> None:
        message = message.strip()
        if not message:
            return
        self._items.append(
            (now if now is not None else time.monotonic(), nickname, message)
        )

    def count(self, now: float | None = None) -> int:
        now = now if now is not None else time.monotonic()
        cutoff = now - self.window_sec
        return sum(1 for at, _, _ in self._items if at >= cutoff)

    def recent(self, limit: int = 8) -> list[tuple[str, str]]:
        items = list(self._items)[-limit:]
        return [(nick, msg) for _at, nick, msg in items]
