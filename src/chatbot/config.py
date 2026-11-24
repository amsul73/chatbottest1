"""config.yaml + .env 로딩.

비밀값(API 키, 네이버 쿠키)은 환경변수에서만 읽는다. YAML 에는 넣지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar, get_type_hints

import yaml

T = TypeVar("T")


# --------------------------------------------------------------------------
# 스키마
# --------------------------------------------------------------------------
@dataclass
class ChannelConfig:
    id: str = ""


@dataclass
class BotConfig:
    dry_run: bool = True


@dataclass
class VadConfig:
    silence_threshold: float = 0.012
    min_speech_sec: float = 0.7
    max_segment_sec: float = 12.0
    trailing_silence_sec: float = 0.45


@dataclass
class AudioConfig:
    enabled: bool = True
    ffmpeg_path: str = "ffmpeg"
    prefer_audio_only: bool = True
    sample_rate: int = 16000
    vad: VadConfig = field(default_factory=VadConfig)


@dataclass
class SttConfig:
    backend: str = "faster-whisper"
    model: str = "small"
    device: str = "auto"
    compute_type: str = "auto"
    language: str = "ko"
    beam_size: int = 1


@dataclass
class LlmConfig:
    model: str = "claude-opus-5-5"
    effort: str = "low"
    max_tokens: int = 400


@dataclass
class HumanChatBackoffConfig:
    window_sec: float = 60.0
    messages: int = 8
    interval_multiplier: float = 2.5


@dataclass
class DirectorConfig:
    min_interval_sec: float = 45.0
    max_per_10min: int = 8
    lull_sec: float = 25.0
    min_new_chars: int = 25
    startup_grace_sec: float = 20.0
    # LLM 에게 물어보는 최소 간격(안 보내기로 한 경우에도 적용)
    attempt_interval_sec: float = 10.0
    human_chat_backoff: HumanChatBackoffConfig = field(
        default_factory=HumanChatBackoffConfig
    )


@dataclass
class PersonaConfig:
    name: str = "따숨"
    description: str = "스트리머를 오래 봐온 단골 시청자. 조용하지만 다정하다."
    interests: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)


@dataclass
class SafetyConfig:
    max_chars: int = 90
    dedupe_window: int = 12
    # 최근 메시지와 이 정도 이상 비슷하면 안 보낸다(0~1).
    # 낮추면 더 깐깐하게, 높이면 비슷한 말도 통과.
    similarity_limit: float = 0.82
    blocked_words: list[str] = field(default_factory=list)


@dataclass
class LogConfig:
    level: str = "INFO"
    file: str | None = "logs/bot.log"


@dataclass
class Secrets:
    anthropic_api_key: str | None = None
    nid_aut: str | None = None
    nid_ses: str | None = None

    @property
    def can_send_chat(self) -> bool:
        return bool(self.nid_aut and self.nid_ses)


@dataclass
class Config:
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    director: DirectorConfig = field(default_factory=DirectorConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    log: LogConfig = field(default_factory=LogConfig)
    secrets: Secrets = field(default_factory=Secrets)


# --------------------------------------------------------------------------
# 로딩
# --------------------------------------------------------------------------
class ConfigError(Exception):
    pass


@lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    """`from __future__ import annotations` 때문에 필드 타입이 문자열로 들어온다.

    실제 타입 객체로 풀어줘야 중첩 dataclass 를 알아볼 수 있다.
    """
    return get_type_hints(cls)


def _build(cls: type[T], data: Any, path: str = "") -> T:
    """dict -> dataclass. 모르는 키는 에러로 알려준다(오타 잡기)."""
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"{path or 'config'}: 사전(mapping) 형태여야 합니다")

    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(
            f"{path or 'config'}: 알 수 없는 설정 키 {sorted(unknown)} "
            f"(가능한 키: {sorted(known)})"
        )

    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for name in known:
        if name not in data:
            continue
        sub = f"{path}.{name}" if path else name
        ftype = hints.get(name)
        if isinstance(ftype, type) and is_dataclass(ftype):
            kwargs[name] = _build(ftype, data[name], sub)
        else:
            kwargs[name] = data[name]
    return cls(**kwargs)


def _load_dotenv(path: Path) -> None:
    """의존성 없이 .env 를 읽어 os.environ 에 넣는다(이미 있는 값은 유지)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> Config:
    config_path = Path(config_path)
    _load_dotenv(Path(env_path))

    if not config_path.is_file():
        raise ConfigError(
            f"설정 파일이 없습니다: {config_path}\n"
            f"  cp config.example.yaml {config_path}  후 채널 ID를 넣어주세요."
        )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    cfg = _build(Config, raw)

    cfg.secrets = Secrets(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        nid_aut=os.environ.get("NID_AUT") or None,
        nid_ses=os.environ.get("NID_SES") or None,
    )

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    problems: list[str] = []

    cid = cfg.channel.id.strip()
    if not cid or cid.startswith("여기에"):
        problems.append("channel.id 를 실제 치지직 채널 ID로 채워주세요.")
    elif not cid.isalnum():
        problems.append(f"channel.id 가 이상합니다: {cid!r} (URL 의 /live/ 뒤 부분)")

    if cfg.stt.backend not in ("faster-whisper", "dummy"):
        problems.append(f"stt.backend 는 faster-whisper 또는 dummy 입니다: {cfg.stt.backend!r}")

    if cfg.llm.effort not in ("low", "medium", "high"):
        problems.append(f"llm.effort 는 low/medium/high 입니다: {cfg.llm.effort!r}")

    if cfg.director.min_interval_sec < 5:
        problems.append("director.min_interval_sec 는 최소 5초 이상으로 두세요(도배 방지).")

    if cfg.safety.max_chars < 1 or cfg.safety.max_chars > 200:
        problems.append("safety.max_chars 는 1~200 사이여야 합니다.")

    if not 0.0 < cfg.safety.similarity_limit <= 1.0:
        problems.append("safety.similarity_limit 는 0 초과 1 이하여야 합니다.")

    if problems:
        raise ConfigError("설정 오류:\n  - " + "\n  - ".join(problems))
