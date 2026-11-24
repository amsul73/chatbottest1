"""자체 점검.

치지직 비공식 API 는 네이버가 예고 없이 바꿀 수 있고, 이 컨테이너가 아닌
'사용자 PC'에서만 실제로 접속이 된다. 그래서 어디가 막혔는지 바로
알려주는 점검 명령을 따로 둔다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from dataclasses import dataclass

from .audio.hls_source import HlsAudioSource
from .chzzk.api import ChzzkApi, ChzzkError
from .chzzk.protocol import Cmd, chat_ws_url, connect_payload
from .config import Config

OK = "✓"
FAIL = "✗"
WARN = "!"


@dataclass
class Result:
    mark: str
    name: str
    detail: str = ""
    hint: str = ""


def _print(result: Result) -> None:
    print(f"  {result.mark} {result.name}" + (f" — {result.detail}" if result.detail else ""))
    if result.hint:
        for line in result.hint.splitlines():
            print(f"      {line}")


async def run_doctor(cfg: Config, check_llm: bool = True) -> bool:
    print("\n치지직 채팅봇 자체 점검")
    print("=" * 56)
    results: list[Result] = []

    # 1. ffmpeg
    if shutil.which(cfg.audio.ffmpeg_path):
        results.append(Result(OK, "ffmpeg", shutil.which(cfg.audio.ffmpeg_path) or ""))
    else:
        results.append(
            Result(
                FAIL if cfg.audio.enabled else WARN,
                "ffmpeg",
                "찾을 수 없음",
                "macOS: brew install ffmpeg / Linux: sudo apt install ffmpeg\n"
                "Windows: ffmpeg.org 에서 받아 PATH 에 추가",
            )
        )

    # 2. 음성인식 패키지
    if cfg.stt.backend == "faster-whisper":
        try:
            import faster_whisper  # noqa: F401

            results.append(Result(OK, "faster-whisper 설치됨"))
        except ImportError:
            results.append(
                Result(
                    FAIL,
                    "faster-whisper",
                    "설치되지 않음",
                    "pip install faster-whisper\n"
                    "또는 config.yaml 에서 stt.backend: dummy",
                )
            )
    else:
        results.append(Result(WARN, "음성인식", f"backend={cfg.stt.backend} (테스트용)"))

    # 3. Claude API 키
    if cfg.secrets.anthropic_api_key:
        results.append(Result(OK, "ANTHROPIC_API_KEY", "있음"))
    else:
        results.append(
            Result(FAIL, "ANTHROPIC_API_KEY", "없음", ".env 에 넣어주세요")
        )

    # 4. 네이버 쿠키
    if cfg.secrets.can_send_chat:
        results.append(Result(OK, "네이버 쿠키", "NID_AUT / NID_SES 있음"))
    else:
        results.append(
            Result(
                WARN,
                "네이버 쿠키",
                "없음 → 읽기 전용(채팅 전송 불가)",
                "채팅을 보내려면 .env 에 NID_AUT / NID_SES 를 넣어주세요",
            )
        )

    for r in results:
        _print(r)

    # ------------------------------------------------------------ 네트워크
    print("\n치지직 API")
    print("-" * 56)
    net: list[Result] = []
    chat_channel_id: str | None = None
    access_token: str | None = None
    uid: str | None = None

    async with ChzzkApi(cfg.secrets.nid_aut, cfg.secrets.nid_ses) as api:
        # 로그인 확인
        if api.has_auth:
            try:
                uid = await api.user_id_hash()
                if uid:
                    net.append(Result(OK, "네이버 로그인", f"userIdHash={uid[:8]}…"))
                else:
                    net.append(
                        Result(
                            FAIL,
                            "네이버 로그인",
                            "쿠키가 만료됨",
                            "치지직에 다시 로그인해서 NID_AUT / NID_SES 를 새로 복사해주세요",
                        )
                    )
            except ChzzkError as exc:
                net.append(Result(FAIL, "네이버 로그인", str(exc)))

        # 방송 상태
        try:
            status = await api.live_status(cfg.channel.id)
            if status.is_live:
                net.append(
                    Result(
                        OK,
                        "방송 상태",
                        f"ON · {status.title or '제목 없음'} · 시청자 {status.viewers}명",
                    )
                )
                chat_channel_id = status.chat_channel_id
            else:
                net.append(
                    Result(
                        WARN,
                        "방송 상태",
                        "OFF",
                        "방송이 켜져 있을 때 다시 점검하면 채팅방까지 확인할 수 있습니다",
                    )
                )
        except ChzzkError as exc:
            net.append(
                Result(
                    FAIL,
                    "방송 상태 조회",
                    str(exc),
                    "channel.id 가 맞는지, 네트워크가 막혀 있지 않은지 확인해주세요",
                )
            )

        # 채팅 토큰
        if chat_channel_id:
            net.append(Result(OK, "chatChannelId", chat_channel_id))
            try:
                access_token, _ = await api.chat_access_token(chat_channel_id)
                net.append(Result(OK, "채팅 accessToken", "발급됨"))
            except ChzzkError as exc:
                net.append(Result(FAIL, "채팅 accessToken", str(exc)))

            # 오디오 주소
            try:
                url = await api.live_audio_url(
                    cfg.channel.id, cfg.audio.prefer_audio_only
                )
                net.append(Result(OK, "오디오(HLS) 주소", url.split("?")[0][:60] + "…"))
            except ChzzkError as exc:
                net.append(Result(FAIL, "오디오(HLS) 주소", str(exc)))

        for r in net:
            _print(r)

        # 채팅 WebSocket
        if chat_channel_id and access_token:
            print("\n채팅 WebSocket")
            print("-" * 56)
            _print(await _check_ws(chat_channel_id, access_token, uid))

    # Claude API
    if check_llm and cfg.secrets.anthropic_api_key:
        print("\nClaude API (실제 호출 1회, 비용 아주 적음)")
        print("-" * 56)
        _print(await _check_llm(cfg))

    print("\n" + "=" * 56)
    failed = [r for r in results + net if r.mark == FAIL]
    if failed:
        print(f"{FAIL} 문제 {len(failed)}건. 위 안내를 먼저 해결해주세요.")
        return False
    print(f"{OK} 점검 통과.  python run.py 로 실행하세요 (처음엔 dry_run: true 권장)")
    return True


async def _check_ws(
    chat_channel_id: str, access_token: str, uid: str | None
) -> Result:
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        return Result(FAIL, "websockets", "설치되지 않음", "pip install websockets")

    url = chat_ws_url(chat_channel_id)
    try:
        async with connect(url, open_timeout=10, ping_interval=None) as ws:
            await ws.send(
                json.dumps(
                    connect_payload(chat_channel_id, access_token, uid),
                    ensure_ascii=False,
                )
            )
            deadline = asyncio.get_running_loop().time() + 10
            while asyncio.get_running_loop().time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                with contextlib.suppress(ValueError, TypeError):
                    frame = json.loads(raw)
                    if isinstance(frame, dict) and frame.get("cmd") == int(Cmd.CONNECTED):
                        mode = "읽기+쓰기" if uid else "읽기 전용"
                        return Result(OK, f"채팅방 접속 ({mode})", url)
            return Result(FAIL, "채팅방 접속", "CONNECTED 응답이 오지 않음")
    except Exception as exc:  # noqa: BLE001
        return Result(
            FAIL,
            "채팅방 접속",
            f"{type(exc).__name__}: {exc}",
            "치지직이 비공식 채팅 프로토콜을 바꿨을 수 있습니다 "
            "(src/chatbot/chzzk/protocol.py 확인)",
        )


async def _check_llm(cfg: Config) -> Result:
    import anthropic

    from .brain.generator import ChatGenerator
    from .brain.persona import GenerationContext

    gen = ChatGenerator(
        cfg.llm, cfg.persona, cfg.safety, cfg.secrets.anthropic_api_key
    )
    try:
        draft = await gen.generate(
            GenerationContext(
                trigger="question",
                trigger_label="스트리머가 질문을 던졌다. 짧게 대답해줘.",
                heard_recent="오늘 방송 봐주시는 분들 다들 저녁은 드셨어요?",
                heard_new="다들 저녁은 드셨어요?",
            )
        )
    except anthropic.AnthropicError as exc:
        return Result(FAIL, "Claude 호출", str(exc))
    finally:
        await gen.close()

    if draft is None:
        return Result(FAIL, "Claude 호출", "응답을 받지 못했습니다 (위 로그 확인)")
    if not draft.speak:
        return Result(OK, "Claude 호출", f"응답 받음 (이번엔 speak=false: {draft.why})")
    return Result(OK, "Claude 호출", f"예시 채팅 → {draft.message!r}")
