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


@pytest.mark.asyncio
class TestRehearsal:
    """--rehearse: 방송이 꺼져 있어도 녹음 파일로 전체 파이프라인이 돌아간다."""

    def _wav(self, tmp_path, make_tone, make_silence):
        import wave

        path = tmp_path / "clip.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(
                make_silence(0.2)
                + make_tone(1.3)
                + make_silence(0.8)
                + make_tone(1.5)
                + make_silence(0.8)
                + make_tone(1.2)
                + make_silence(0.8)
            )
        return path

    async def test_runs_without_chzzk_and_reports_what_it_would_say(
        self, tmp_path, make_tone, make_silence
    ):
        import asyncio

        app = make_app(draft=Draft(True, "오 그거 좋네요", "리허설"))
        app.cfg.bot.offline = True
        app.cfg.audio.enabled = True
        app.cfg.audio.source_file = str(self._wav(tmp_path, make_tone, make_silence))

        # 치지직에 접속하면 안 된다 — 접속을 시도하면 테스트가 실패하게 둔다
        async def must_not_be_called(*_a, **_kw):
            raise AssertionError("리허설 모드에서 치지직에 접속하려 했다")

        app._chat.start = must_not_be_called  # type: ignore[assignment]
        app._api.live_audio_url = must_not_be_called  # type: ignore[assignment]
        app._api.user_id_hash = must_not_be_called  # type: ignore[assignment]

        await asyncio.wait_for(app.run(), timeout=60)

        # 파일이 끝나면 스스로 종료한다
        assert app._stopping.is_set()
        # 들은 말이 있고, 보냈을 메시지가 있다
        assert len(app._transcript.recent(600)) >= 2
        assert app._sent_count >= 1
        # 리허설은 절대 실제로 보내지 않는다
        assert app._chat.sent == []
        assert app.cfg.bot.dry_run is True

    async def test_refuses_without_audio_file(self):
        app = make_app()
        app.cfg.bot.offline = True
        app.cfg.audio.enabled = True
        app.cfg.audio.source_file = None
        await app.run()  # 에러 로그만 남기고 조용히 끝난다
        assert app._sent_count == 0

    async def test_forces_dry_run_even_if_config_says_send(
        self, tmp_path, make_tone, make_silence
    ):
        import asyncio

        app = make_app(dry_run=False, draft=Draft(True, "보내면 안 되는 말", ""))
        app.cfg.bot.offline = True
        app.cfg.audio.enabled = True
        app.cfg.audio.source_file = str(self._wav(tmp_path, make_tone, make_silence))
        app._chat.start = lambda *a, **k: None  # type: ignore[assignment]

        await asyncio.wait_for(app.run(), timeout=60)
        assert app.cfg.bot.dry_run is True
        assert app._chat.sent == []


@pytest.mark.asyncio
async def test_dropped_segments_do_not_block_queue_join():
    """밀려서 버린 구간도 task_done 처리되어야 join() 이 끝난다."""
    import asyncio

    from chatbot.audio.vad import Segment

    app = make_app()
    for i in range(10):
        app._enqueue(Segment(b"\x00\x00" * 16000, 16000))
    while not app._segments.empty():
        app._segments.get_nowait()
        app._segments.task_done()
    await asyncio.wait_for(app._segments.join(), timeout=5)
