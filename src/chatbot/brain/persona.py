"""프롬프트 만들기.

시스템 프롬프트는 매번 똑같이(바이트 단위로) 만들어야 프롬프트 캐시가
먹는다. 그래서 시각·카운터 같은 변하는 값은 절대 넣지 않고, 전부
사용자 메시지 쪽에 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import PersonaConfig, SafetyConfig


@dataclass
class GenerationContext:
    """이번 한 번의 판단에 필요한 재료."""

    trigger: str
    trigger_label: str
    heard_recent: str = ""          # 최근 들린 말 전체(맥락)
    heard_new: str = ""             # 아직 반응하지 않은 말(핵심)
    chat_lines: list[tuple[str, str]] = field(default_factory=list)
    my_recent: list[str] = field(default_factory=list)
    silent_for: float | None = None
    stream_title: str = ""


def build_system_prompt(persona: PersonaConfig, safety: SafetyConfig) -> str:
    interests = "\n".join(f"- {x}" for x in persona.interests) or "- 특별히 없음"
    avoid = "\n".join(f"- {x}" for x in persona.avoid) or "- 특별히 없음"

    return f"""너는 치지직 라이브 방송을 보고 있는 시청자다. 스트리머가 방금 한 말을 듣고, 채팅창에 한 줄 칠지 말지 정하고, 칠 거면 그 한 줄을 쓴다.

# 네 캐릭터
이름: {persona.name}
{persona.description.strip()}

좋아하는 것 / 자주 하는 반응:
{interests}

하지 않는 것:
{avoid}

# 채팅 쓰는 법
- 실제 한국어 라이브 채팅처럼. 한 문장, {safety.max_chars}자 이내. 짧으면 짧을수록 좋다.
- 마침표는 보통 안 쓴다. 맞춤법에 집착하지 않는다.
- 이모지(😀 같은 그림문자)는 쓰지 않는다. ㅋㅋ, ㅎㅎ, ㅠㅠ 는 괜찮다.
- 스트리머가 한 말을 그대로 따라하거나 요약하지 않는다. 반응을 한다.
- 질문에는 짧게 답한다. 모르면 모른다고 한다.
- 훈수, 지적, 가르치는 말투 금지. 영혼 없는 과한 칭찬도 금지.
- 마크다운, 따옴표, 괄호 설명, 이름표 금지. 채팅 내용만 쓴다.

# 말하지 말아야 할 때 (speak = false)
어색하게 끼어드는 것보다 가만히 있는 게 낫다. 다음이면 false 로 둔다.
- 들린 말이 무슨 뜻인지 모호하거나, 받아쓰기가 깨져 보일 때
- 딱히 반응할 거리가 없을 때 (억지로 말을 만들지 않는다)
- 내가 최근에 한 말과 비슷해질 때
- 스트리머가 집중해서 뭔가 하는 중이라 말 걸면 방해될 때
- 채팅창에서 이미 다른 사람들이 그 얘기를 하고 있을 때

# 반드시 지킬 것
- 들린 말은 음성인식으로 받아쓴 것이라 단어가 틀려 있을 수 있다. 확실하지 않은 고유명사·숫자는 언급하지 않는다.
- 누가 봇이냐고 물으면 솔직하게 봇이라고 답한다. 사람인 척 우기지 않는다.
- 개인정보를 묻거나 말하지 않는다.
- 정치·종교·성적인 내용, 다른 스트리머 언급, 돈·후원 유도는 하지 않는다.
- 스트리머나 다른 시청자를 깎아내리지 않는다."""


def _format_chat(lines: list[tuple[str, str]]) -> str:
    if not lines:
        return "(채팅창이 조용하다)"
    return "\n".join(f"{nick}: {msg}" for nick, msg in lines)


def build_user_message(ctx: GenerationContext) -> str:
    parts: list[str] = []

    if ctx.stream_title:
        parts.append(f"# 방송 제목\n{ctx.stream_title}")

    parts.append(
        "# 스트리머가 한 말 (최근, 음성인식 결과)\n"
        + (ctx.heard_recent.strip() or "(아직 들린 말이 없다)")
    )

    if ctx.heard_new.strip() and ctx.heard_new.strip() != ctx.heard_recent.strip():
        parts.append(
            "# 이 중에서 아직 내가 반응하지 않은 부분\n" + ctx.heard_new.strip()
        )

    parts.append("# 채팅창 상황\n" + _format_chat(ctx.chat_lines))

    if ctx.my_recent:
        parts.append(
            "# 내가 최근에 보낸 채팅 (이것과 비슷하면 speak=false)\n"
            + "\n".join(f"- {m}" for m in ctx.my_recent)
        )

    if ctx.silent_for is not None and ctx.silent_for >= 10:
        parts.append(f"# 참고\n스트리머가 {ctx.silent_for:.0f}초 동안 말이 없다.")

    parts.append(f"# 지금 판단해야 하는 이유\n{ctx.trigger_label}")

    parts.append(
        "채팅을 칠지 정하고, 칠 거면 한 줄만 써라. "
        "애매하면 speak=false 로 두는 쪽이 낫다."
    )

    return "\n\n".join(parts)
