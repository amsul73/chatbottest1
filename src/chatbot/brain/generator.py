"""Claude 로 채팅 한 줄 만들기."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from ..config import LlmConfig, PersonaConfig, SafetyConfig
from .persona import GenerationContext, build_system_prompt, build_user_message

log = logging.getLogger(__name__)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "speak": {
            "type": "boolean",
            "description": "지금 채팅을 칠지 여부. 애매하면 false.",
        },
        "message": {
            "type": "string",
            "description": "채팅 내용 한 줄. speak 가 false 면 빈 문자열.",
        },
        "why": {
            "type": "string",
            "description": "그렇게 정한 이유 한 마디(로그용, 채팅창에 나가지 않음).",
        },
    },
    "required": ["speak", "message", "why"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Draft:
    speak: bool
    message: str
    why: str = ""


class ChatGenerator:
    def __init__(
        self,
        llm: LlmConfig,
        persona: PersonaConfig,
        safety: SafetyConfig,
        api_key: str | None = None,
    ) -> None:
        self._llm = llm
        # 바이트 단위로 고정 → 프롬프트 캐시가 먹는다
        self._system = build_system_prompt(persona, safety)
        self._client = anthropic.AsyncAnthropic(
            **({"api_key": api_key} if api_key else {}),
            timeout=20.0,
            max_retries=1,  # 라이브 채팅이라 오래 기다릴 가치가 없다
        )

    @property
    def system_prompt(self) -> str:
        return self._system

    async def generate(self, ctx: GenerationContext) -> Draft | None:
        """실패하면 None (봇은 그냥 조용히 넘어간다)."""
        try:
            response = await self._client.messages.create(
                model=self._llm.model,
                max_tokens=self._llm.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": self._system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": build_user_message(ctx)}],
                output_config={
                    "effort": self._llm.effort,
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                },
            )
        except anthropic.RateLimitError:
            log.warning("Claude API 호출 한도에 걸렸습니다. 이번 차례는 넘어갑니다")
            return None
        except anthropic.AuthenticationError:
            log.error("ANTHROPIC_API_KEY 가 잘못되었습니다")
            return None
        except anthropic.APIStatusError as exc:
            log.warning("Claude API 오류 %s: %s", exc.status_code, exc)
            return None
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            log.warning("Claude API 연결 실패: %s", exc)
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("모델이 응답을 거부했습니다. 넘어갑니다")
            return None

        text = next(
            (b.text for b in response.content if getattr(b, "type", "") == "text"),
            None,
        )
        if not text:
            log.debug("빈 응답")
            return None

        try:
            data = json.loads(text)
        except ValueError:
            log.warning("JSON 파싱 실패: %r", text[:200])
            return None
        if not isinstance(data, dict):
            return None

        usage = getattr(response, "usage", None)
        if usage is not None:
            log.debug(
                "토큰 in=%s cache_read=%s out=%s",
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "cache_read_input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
            )

        return Draft(
            speak=bool(data.get("speak")),
            message=str(data.get("message") or ""),
            why=str(data.get("why") or ""),
        )

    async def close(self) -> None:
        await self._client.close()
