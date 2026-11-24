"""보내기 직전의 마지막 관문.

LLM 이 뭘 내놓든, 채팅창에 나가는 건 여기를 통과한 것만이다.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from difflib import SequenceMatcher

from ..config import SafetyConfig

log = logging.getLogger(__name__)

QUOTE_CHARS = "\"'“”‘’「」『』《》"
URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
MENTION_RE = re.compile(r"^@\S+\s*")
MARKDOWN_RE = re.compile(r"(\*{1,2}|_{1,2}|`+|^#+\s*|^[-*]\s+)")
WHITESPACE_RE = re.compile(r"\s+")

# 기본 임계값. 짧은 한국어 문장은 글자가 몇 개만 겹쳐도 비율이 높게 나오므로
# 일부러 깐깐하게 잡아 두었다("애매하면 가만히 있는다"는 이 봇의 원칙).
# 너무 자주 걸러진다고 느끼면 config.yaml 의 safety.similarity_limit 을 올린다.
DEFAULT_SIMILARITY_LIMIT = 0.82


def normalize(text: str) -> str:
    """중복 비교용으로 단순화."""
    text = text.lower()
    text = re.sub(r"[^0-9a-z가-힣ㄱ-ㅎㅏ-ㅣ]", "", text)
    return text


class Rejected(Exception):
    """보내지 않기로 한 메시지."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MessageFilter:
    def __init__(self, cfg: SafetyConfig) -> None:
        self.cfg = cfg
        self._recent: deque[str] = deque(maxlen=max(1, cfg.dedupe_window))
        self._blocked = [w.strip().lower() for w in cfg.blocked_words if w.strip()]
        self._similarity_limit = getattr(
            cfg, "similarity_limit", DEFAULT_SIMILARITY_LIMIT
        )

    # ------------------------------------------------------------------
    def clean(self, text: str) -> str:
        """다듬어서 돌려준다. 보내면 안 되는 거면 Rejected 를 던진다."""
        if not text or not text.strip():
            raise Rejected("빈 메시지")

        # 1. 한 줄로 (채팅은 개행이 안 된다)
        out = WHITESPACE_RE.sub(" ", text).strip()

        # 2. LLM 이 따옴표로 감싸는 버릇 제거
        out = out.strip(QUOTE_CHARS).strip()

        # 3. 마크다운 흔적 제거
        out = MARKDOWN_RE.sub("", out).strip()

        # 4. 앞에 붙은 멘션 제거
        out = MENTION_RE.sub("", out).strip()

        if not out:
            raise Rejected("다듬고 나니 빈 메시지")

        # 5. 링크는 보내지 않는다
        if URL_RE.search(out):
            raise Rejected("링크 포함")

        # 6. 금지어
        lowered = out.lower()
        for word in self._blocked:
            if word in lowered:
                raise Rejected(f"금지어 포함: {word!r}")

        # 7. 길이
        out = self._fit_length(out)

        # 8. 최근에 한 말과 겹치면 안 보낸다
        duplicate = self._find_duplicate(out)
        if duplicate is not None:
            raise Rejected(f"최근 메시지와 중복: {duplicate!r}")

        return out

    def _fit_length(self, text: str) -> str:
        limit = self.cfg.max_chars
        if len(text) <= limit:
            return text
        # 되도록 단어 경계에서 자른다
        cut = text[:limit]
        space = cut.rfind(" ")
        if space >= limit * 0.5:
            cut = cut[:space]
        cut = cut.rstrip(" ,.…")
        log.debug("길이 초과로 잘림: %r -> %r", text, cut)
        return cut or text[:limit]

    def _find_duplicate(self, text: str) -> str | None:
        candidate = normalize(text)
        if not candidate:
            raise Rejected("내용이 없는 메시지")
        for previous in self._recent:
            prev_norm = normalize(previous)
            if not prev_norm:
                continue
            if candidate == prev_norm:
                return previous
            if (
                SequenceMatcher(None, candidate, prev_norm).ratio()
                >= self._similarity_limit
            ):
                return previous
        return None

    def remember(self, text: str) -> None:
        """실제로 보낸 메시지를 기억한다(다음 중복 판정용)."""
        self._recent.append(text)

    @property
    def recent(self) -> list[str]:
        return list(self._recent)
