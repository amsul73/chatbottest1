"""파이프라인 배선 검증.

치지직 서버와 Claude 는 가짜로 바꿔 끼우고, '들린 말 → 판단 → 필터 →
전송'까지가 실제로 이어지는지 본다.
"""

import time

import pytest

from chatbot.app import ChatBotApp
from chatbot.brain.generator import Draft
from chatbot.chzzk.protocol import IncomingChat
from chatbot.config import (
    BotConfig,
    ChannelConfig,
    Config,
    DirectorConfig,
    Secrets,
    SttConfig,
)


class FakeChat:
    def __init__(self, connected=True, ok=True):
        self.connected = connected
        self._ok = ok
        self.sent: list[str] = []

    async def send(self, message: str) -> bool:
        self.sent.append(message)
        return self._ok

    async def close(self) -> None:
        pass


class FakeGenerator:
    def __init__(self, draft: Draft | None):
        self.draft = draft
        self.calls: list = []

    async def generate(self, ctx):
        self.calls.append(ctx)
        return self.draft

    async def close(self) -> None:
        pass


def make_app(dry_run=False, draft=None, connected=True, send_ok=True):
    cfg = Config(
        channel=ChannelConfig(id="abc123"),
        bot=BotConfig(dry_run=dry_run),
        stt=SttConfig(backend="dummy"),
        director=DirectorConfig(startup_grace_sec=0, attempt_interval_sec=0),
        secrets=Secrets(anthropic_api_key="sk-test", nid_aut="a", nid_ses="b"),
    )
    cfg.audio.enabled = False
    app = ChatBotApp(cfg)
    app._chat = FakeChat(connected=connected, ok=send_ok)
    app._generator = FakeGenerator(draft)
    return app


def hear(app, text="다들 저녁 먹었어요?", age_sec=1.0):
    """스트리머가 `age_sec` 초 전에 이 말을 했다고 둔다.

    _tick() 은 실제 시계(time.monotonic)를 쓰므로 테스트도 같은 시계를
    기준으로 넣어야 한다. 임의의 숫자를 쓰면 '너무 오래된 발화'로 간주돼
    버퍼에서 정리된다.
    """
    app._transcript.add(text, now=time.monotonic() - age_sec)


@pytest.mark.asyncio
class TestTick:
    async def test_sends_when_model_says_speak(self):
        app = make_app(draft=Draft(True, "저는 먹었어요 ㅋㅋ", "질문에 답"))
        hear(app)
        await app._tick()
        assert app._chat.sent == ["저는 먹었어요 ㅋㅋ"]
        assert app._sent_count == 1
        # 보낸 말은 중복 검사용으로 기억된다
        assert app._filter.recent == ["저는 먹었어요 ㅋㅋ"]

    async def test_dry_run_does_not_send(self):
        app = make_app(dry_run=True, draft=Draft(True, "보낼 뻔한 말", ""))
        hear(app)
        await app._tick()
        assert app._chat.sent == []
        # 그래도 쿨타임/중복 기록은 실제처럼 돌아간다
        assert app._sent_count == 1
        assert app._filter.recent == ["보낼 뻔한 말"]

    async def test_respects_model_declining(self):
        app = make_app(draft=Draft(False, "", "반응할 거리가 없음"))
        hear(app)
        await app._tick()
        assert app._chat.sent == []
        assert app._sent_count == 0

    async def test_generator_failure_is_survivable(self):
        app = make_app(draft=None)
        hear(app)
        await app._tick()
        assert app._chat.sent == []
        assert app._sent_count == 0

    async def test_filter_blocks_bad_message(self):
        app = make_app(draft=Draft(True, "여기 http://spam.com 보세요", ""))
        hear(app)
        await app._tick()
        assert app._chat.sent == []
        assert app._sent_count == 0

    async def test_message_is_cleaned_before_sending(self):
        app = make_app(draft=Draft(True, '  "**대박**\n진짜요?"  ', ""))
        hear(app)
        await app._tick()
        assert app._chat.sent == ["대박 진짜요?"]

    async def test_does_nothing_while_disconnected(self):
        app = make_app(draft=Draft(True, "뭐라도", ""), connected=False)
        hear(app)
        await app._tick()
        assert app._generator.calls == []
        assert app._chat.sent == []

    async def test_send_failure_is_not_counted(self):
        app = make_app(draft=Draft(True, "전송 실패할 말", ""), send_ok=False)
        hear(app)
        await app._tick()
        assert app._sent_count == 0
        assert app._filter.recent == []

    async def test_same_utterance_is_not_reacted_twice(self):
        app = make_app(draft=Draft(True, "첫 반응이요", ""))
        hear(app)
        await app._tick()
        assert len(app._generator.calls) == 1

        # 새로 들린 말이 없으면 다시 묻지 않는다
        app._director.note_sent(now=0.0)  # 쿨타임은 비켜둠
        await app._tick()
        assert len(app._generator.calls) == 1

    async def test_context_passed_to_model(self):
        app = make_app(draft=Draft(False, "", ""))
        hear(app, "이거 어떻게 하는 거지")
        app._on_chat(
            IncomingChat(
                user_id_hash="u1",
                nickname="시청자1",
                message="ㅋㅋㅋ",
                msg_type=1,
                time_ms=1,
            )
        )
        await app._tick()
        ctx = app._generator.calls[0]
        assert ctx.trigger == "question"
        assert "어떻게 하는 거지" in ctx.heard_recent
        assert ctx.chat_lines == [("시청자1", "ㅋㅋㅋ")]


@pytest.mark.asyncio
class TestChatIntake:
    async def test_recent_history_is_ignored_for_activity(self):
        """접속 직후 받아오는 과거 채팅은 '지금 분위기'가 아니다."""
        app = make_app()
        app._on_chat(
            IncomingChat("u", "옛날사람", "과거 채팅", 1, 1, is_recent=True)
        )
        assert app._activity.count() == 0
        app._on_chat(IncomingChat("u", "지금사람", "현재 채팅", 1, 1))
        assert app._activity.count() == 1


@pytest.mark.asyncio
class TestSttQueue:
    async def test_drops_oldest_when_backlogged(self):
        """음성인식이 밀리면 오래된 구간을 버리고 최신을 남긴다."""
        from chatbot.audio.vad import Segment

        app = make_app()
        segments = [
            Segment(b"\x00\x00" * (16000 * (i + 1)), 16000) for i in range(6)
        ]
        for seg in segments:
            app._enqueue(seg)

        assert app._segments.qsize() == 4
        kept = []
        while not app._segments.empty():
            kept.append(app._segments.get_nowait().duration_sec)
        # 마지막 구간은 반드시 살아 있어야 한다
        assert 6.0 in kept
        assert 1.0 not in kept


@pytest.mark.asyncio
class TestAudioToTranscript:
    """실제 ffmpeg + 실제 VAD + dummy STT 로 오디오 경로가 이어지는지."""

    async def test_wav_file_ends_up_in_transcript(self, tmp_path, make_tone, make_silence):
        import asyncio
        import wave

        wav = tmp_path / "speech.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(
                make_silence(0.3)
                + make_tone(1.4)
                + make_silence(0.9)
                + make_tone(1.6)
                + make_silence(0.9)
            )

        app = make_app(draft=None)
        app.cfg.audio.enabled = True

        async def fake_audio_url(channel_id, prefer_audio_only=True):
            return str(wav)

        app._api.live_audio_url = fake_audio_url  # type: ignore[assignment]

        audio = asyncio.create_task(app._audio_loop())
        stt = asyncio.create_task(app._stt_loop())
        try:
            for _ in range(80):  # 최대 4초 기다린다
                await asyncio.sleep(0.05)
                if len(app._transcript.recent(600)) >= 2:
                    break
        finally:
            audio.cancel()
            stt.cancel()
            await asyncio.gather(audio, stt, return_exceptions=True)

        heard = app._transcript.recent(600)
        assert len(heard) >= 2, f"들린 말이 모자람: {[u.text for u in heard]}"
        assert all(u.text for u in heard)
