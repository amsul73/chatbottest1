"""전체 파이프라인 연결.

   방송 오디오 ──ffmpeg──> PCM ──VAD──> 발화구간 ──Whisper──> 글자
                                                              │
   채팅창 ──WebSocket──> 분위기 파악                           ▼
                                      Director(지금 말해도 되나?)
                                                              │
                                                              ▼
                                         Claude(무슨 말을 할까?) ──> 안전 필터 ──> 채팅 전송
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .audio.hls_source import AudioSourceEnded, FfmpegMissingError, HlsAudioSource
from .audio.vad import Segment, SpeechSegmenter
from .brain.director import TRIGGER_LABEL, Director
from .brain.generator import ChatGenerator
from .brain.persona import GenerationContext
from .brain.safety import MessageFilter, Rejected
from .brain.transcript import ChatActivity, TranscriptBuffer
from .chzzk.api import ChzzkApi, ChzzkError, NotLiveError
from .chzzk.chat_client import ChzzkChatClient
from .chzzk.protocol import IncomingChat
from .config import Config
from .stt import create_stt

log = logging.getLogger(__name__)

DECIDE_INTERVAL = 2.0
AUDIO_RETRY_INTERVAL = 15.0
# 음성인식이 밀리면 오래된 구간은 버린다. 라이브에서는 최신 내용이 중요하다.
STT_QUEUE_SIZE = 4


class ChatBotApp:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._stopping = asyncio.Event()

        self._api = ChzzkApi(cfg.secrets.nid_aut, cfg.secrets.nid_ses)
        self._chat = ChzzkChatClient(
            channel_id=cfg.channel.id,
            api=self._api,
            on_chat=self._on_chat,
            send_enabled=True,
        )
        self._transcript = TranscriptBuffer()
        self._activity = ChatActivity(
            window_sec=cfg.director.human_chat_backoff.window_sec
        )
        self._director = Director(cfg.director)
        self._filter = MessageFilter(cfg.safety)
        self._generator = ChatGenerator(
            cfg.llm, cfg.persona, cfg.safety, cfg.secrets.anthropic_api_key
        )
        self._stt = create_stt(cfg.stt)
        self._audio = HlsAudioSource(
            ffmpeg_path=cfg.audio.ffmpeg_path, sample_rate=cfg.audio.sample_rate
        )
        self._segments: asyncio.Queue[Segment] = asyncio.Queue(maxsize=STT_QUEUE_SIZE)
        self._stream_title = ""
        self._sent_count = 0

    # ------------------------------------------------------------------
    @property
    def _offline(self) -> bool:
        """리허설 모드: 치지직에 접속하지 않고 녹음 파일로만 돌린다."""
        return self.cfg.bot.offline

    async def run(self) -> None:
        cfg = self.cfg

        if cfg.bot.offline:
            # 리허설은 보내지 않는다. 실수로 보내는 일이 없게 여기서 못박는다.
            cfg.bot.dry_run = True
            if not cfg.audio.source_file:
                log.error("리허설 모드에는 오디오 파일이 필요합니다 (--rehearse <파일>)")
                return
            if not cfg.audio.enabled:
                log.error("리허설 모드에서는 audio.enabled 가 true 여야 합니다")
                return

        log.info("=" * 56)
        if cfg.bot.offline:
            log.info("모드: 리허설 (치지직 접속 안 함, 채팅 안 보냄)")
            log.info("오디오: %s", cfg.audio.source_file)
        else:
            log.info("채널: %s", cfg.channel.id)
            log.info(
                "모드: %s",
                "DRY-RUN (채팅 안 보냄, 로그만)" if cfg.bot.dry_run else "실제 전송",
            )
        log.info("캐릭터: %s", cfg.persona.name)
        log.info("음성인식: %s", cfg.stt.backend if cfg.audio.enabled else "꺼짐")
        log.info("모델: %s (effort=%s)", cfg.llm.model, cfg.llm.effort)
        log.info(
            "속도 제한: 최소 %.0f초 간격, 10분에 %d개까지",
            cfg.director.min_interval_sec,
            cfg.director.max_per_10min,
        )
        log.info("=" * 56)

        if cfg.audio.enabled:
            try:
                self._audio.ensure_available()
            except FfmpegMissingError as exc:
                log.error("%s", exc)
                return

        if not cfg.bot.dry_run and not cfg.secrets.can_send_chat:
            log.error(
                "채팅을 보내려면 .env 에 NID_AUT / NID_SES 가 필요합니다. "
                "(dry_run: true 로 두면 쿠키 없이도 테스트할 수 있습니다)"
            )
            return
        if not cfg.secrets.anthropic_api_key:
            log.error("ANTHROPIC_API_KEY 가 없습니다. .env 를 확인해주세요.")
            return

        tasks = [asyncio.create_task(self._decide_loop(), name="decide")]
        if not self._offline:
            tasks.append(asyncio.create_task(self._chat.start(), name="chat-start"))
        if cfg.audio.enabled:
            tasks.append(asyncio.create_task(self._audio_loop(), name="audio"))
            tasks.append(asyncio.create_task(self._stt_loop(), name="stt"))

        try:
            await self._stopping.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.aclose()

    def stop(self) -> None:
        self._stopping.set()

    async def aclose(self) -> None:
        log.info("정리 중…")
        with contextlib.suppress(Exception):
            await self._chat.close()
        with contextlib.suppress(Exception):
            await self._audio.stop()
        with contextlib.suppress(Exception):
            await self._stt.close()
        with contextlib.suppress(Exception):
            await self._generator.close()
        with contextlib.suppress(Exception):
            await self._api.aclose()
        log.info("보낸 채팅: %d개", self._sent_count)

    # ------------------------------------------------------------------ 채팅 수신
    def _on_chat(self, chat: IncomingChat) -> None:
        if chat.is_recent:
            return  # 접속 직후 받아오는 과거 채팅은 분위기 계산에서 제외
        self._activity.add(chat.nickname, chat.message)
        log.debug("채팅 %s: %s", chat.nickname, chat.message)

    # ------------------------------------------------------------------ 오디오
    async def _audio_loop(self) -> None:
        source_file = self.cfg.audio.source_file
        while not self._stopping.is_set():
            if source_file:
                url = source_file
                log.info("오디오 파일 재생: %s", source_file)
                # 녹음 파일에는 '방금 접속했다'는 개념이 없으므로 시작 유예를
                # 두지 않는다. 그래야 짧은 클립에서도 반응을 볼 수 있다.
                self.cfg.director.startup_grace_sec = 0.0
            else:
                try:
                    url = await self._api.live_audio_url(
                        self.cfg.channel.id, self.cfg.audio.prefer_audio_only
                    )
                except NotLiveError:
                    log.debug("방송 꺼짐 — 오디오 대기")
                    await self._sleep(AUDIO_RETRY_INTERVAL)
                    continue
                except ChzzkError as exc:
                    log.warning("오디오 주소를 받지 못했습니다: %s", exc)
                    await self._sleep(AUDIO_RETRY_INTERVAL)
                    continue
                log.info("오디오 수신 시작")

            self._director.reset_clock()
            vad = self.cfg.audio.vad
            segmenter = SpeechSegmenter(
                sample_rate=self.cfg.audio.sample_rate,
                silence_threshold=vad.silence_threshold,
                min_speech_sec=vad.min_speech_sec,
                max_segment_sec=vad.max_segment_sec,
                trailing_silence_sec=vad.trailing_silence_sec,
            )
            try:
                async for chunk in self._audio.stream(url):
                    for segment in segmenter.feed(chunk):
                        self._enqueue(segment)
            except AudioSourceEnded as exc:
                log.info("오디오 스트림 종료: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("오디오 루프 오류")
            for segment in segmenter.flush():
                self._enqueue(segment)

            if self._offline:
                await self._finish_rehearsal()
                return

            await self._sleep(5.0)

    async def _finish_rehearsal(self) -> None:
        """파일이 끝나면 남은 음성인식을 비우고 마지막 판단까지 해본 뒤 끝낸다."""
        log.info("오디오 파일이 끝났습니다. 남은 음성인식을 처리합니다…")
        try:
            await asyncio.wait_for(self._segments.join(), timeout=180)
        except asyncio.TimeoutError:
            log.warning("음성인식이 끝나지 않아 기다리기를 멈춥니다")

        # 쌓인 발화에 대해 마지막으로 몇 번 더 판단해본다
        for _ in range(3):
            if self._stopping.is_set():
                break
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("리허설 마지막 판단 중 오류")
            await self._sleep(2.0)

        log.info("리허설 종료")
        self.stop()

    def _enqueue(self, segment: Segment) -> None:
        """음성인식 대기열에 넣는다. 꽉 찼으면 가장 오래된 걸 버린다."""
        try:
            self._segments.put_nowait(segment)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                dropped = self._segments.get_nowait()
                # 버린 것도 task_done 을 해줘야 한다. 안 하면 Queue 의 미처리
                # 카운터가 남아서 join() 이 영원히 끝나지 않는다.
                self._segments.task_done()
                log.debug("음성인식이 밀려 %.1f초 구간을 버림", dropped.duration_sec)
            with contextlib.suppress(asyncio.QueueFull):
                self._segments.put_nowait(segment)

    async def _stt_loop(self) -> None:
        while not self._stopping.is_set():
            segment = await self._segments.get()
            try:
                result = await self._stt.transcribe(segment)
            except Exception:  # noqa: BLE001
                log.exception("음성인식 오류")
                continue
            finally:
                self._segments.task_done()
            if result is None or not result.text.strip():
                continue
            self._transcript.add(result.text)
            log.info("들림: %s", result.text)

    # ------------------------------------------------------------------ 판단
    async def _decide_loop(self) -> None:
        while not self._stopping.is_set():
            await self._sleep(DECIDE_INTERVAL)
            if self._stopping.is_set():
                return
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 - 한 번 실패로 봇이 죽지 않게
                log.exception("판단 루프 오류")

    async def _tick(self) -> None:
        if not self._offline and not self._chat.connected:
            return

        decision = self._director.decide(self._transcript, self._activity)
        if not decision.speak:
            if decision.detail:
                log.debug("대기: %s", decision.detail)
            return

        log.debug("판단 시작 (%s)", decision.trigger)
        ctx = GenerationContext(
            trigger=decision.trigger,
            trigger_label=decision.detail or TRIGGER_LABEL.get(decision.trigger, ""),
            heard_recent=self._transcript.recent_text(120),
            heard_new=" ".join(u.text for u in self._transcript.unreacted()),
            chat_lines=self._activity.recent(8),
            my_recent=self._filter.recent[-5:],
            silent_for=self._transcript.seconds_since_last(),
            stream_title=self._stream_title,
        )

        self._director.note_attempt()
        draft = await self._generator.generate(ctx)
        # 같은 내용에 또 반응하지 않도록, 결과와 무관하게 여기까지는 처리한 것으로 본다
        self._transcript.mark()

        if draft is None:
            return
        if not draft.speak:
            log.info("안 보냄 (%s)", draft.why or "모델 판단")
            return

        try:
            message = self._filter.clean(draft.message)
        except Rejected as exc:
            log.info("걸러짐 (%s): %r", exc.reason, draft.message)
            return

        if self.cfg.bot.dry_run:
            log.info("[DRY-RUN] 보냈을 채팅: %s   (%s)", message, draft.why)
            self._filter.remember(message)
            self._director.note_sent()
            self._sent_count += 1
            return

        if await self._chat.send(message):
            log.info("보냄: %s   (%s)", message, draft.why)
            self._filter.remember(message)
            self._director.note_sent()
            self._sent_count += 1
        else:
            log.warning("전송 실패: %s", message)

    # ------------------------------------------------------------------
    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
