"""치지직 채팅 WebSocket 클라이언트.

하는 일
  - 방송이 켜질 때까지 기다렸다가 채팅방에 붙는다
  - 올라오는 채팅을 콜백으로 넘긴다 (분위기 파악용)
  - 우리 채팅을 보낸다
  - 끊기면 다시 붙는다. 방송이 재시작되어 chatChannelId 가 바뀌어도 따라간다
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection, connect

from .api import ChzzkApi, ChzzkError
from .protocol import (
    Cmd,
    IncomingChat,
    chat_ws_url,
    connect_payload,
    parse_chat_frame,
    ping_payload,
    pong_payload,
    recent_chat_payload,
    send_chat_payload,
)

log = logging.getLogger(__name__)

ChatHandler = Callable[[IncomingChat], Awaitable[None] | None]

PING_INTERVAL = 20.0
LIVE_POLL_INTERVAL = 30.0
RECONNECT_BASE = 2.0
RECONNECT_MAX = 60.0
LIVE_WAIT_INTERVAL = 30.0


class ChzzkChatClient:
    def __init__(
        self,
        channel_id: str,
        api: ChzzkApi,
        on_chat: ChatHandler | None = None,
        send_enabled: bool = True,
    ) -> None:
        self._channel_id = channel_id
        self._api = api
        self._on_chat = on_chat
        self._send_enabled = send_enabled

        self._ws: ClientConnection | None = None
        self._chat_channel_id: str | None = None
        self._sid: str | None = None
        self._uid: str | None = None

        self._connected = asyncio.Event()
        self._stopping = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()

    # ------------------------------------------------------------ 상태
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def can_send(self) -> bool:
        """채팅을 보낼 수 있는 상태인가(로그인 + 접속 완료)."""
        return bool(self._send_enabled and self._uid and self._sid and self.connected)

    # ------------------------------------------------------------ 수명주기
    async def start(self) -> None:
        self._uid = await self._api.user_id_hash()
        if self._send_enabled and not self._uid:
            log.warning(
                "네이버 쿠키가 없거나 만료되어 읽기 전용으로 접속합니다 "
                "(채팅 전송 불가)."
            )
        self._spawn(self._run_forever(), "chat-run")

    async def close(self) -> None:
        self._stopping.set()
        self._connected.clear()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    def _spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    # ------------------------------------------------------------ 접속 루프
    async def _resolve_chat_channel(self) -> str:
        """방송이 켜질 때까지 기다리며 chatChannelId 를 얻는다."""
        warned = False
        while not self._stopping.is_set():
            try:
                status = await self._api.live_status(self._channel_id)
            except ChzzkError as exc:
                log.warning("방송 상태 확인 실패: %s", exc)
                await self._sleep(LIVE_WAIT_INTERVAL)
                continue

            if status.is_live and status.chat_channel_id:
                log.info(
                    "방송 중: %s (시청자 %d명)",
                    status.title or self._channel_id,
                    status.viewers,
                )
                return status.chat_channel_id

            if not warned:
                log.info("방송이 꺼져 있습니다. 켜질 때까지 기다립니다…")
                warned = True
            await self._sleep(LIVE_WAIT_INTERVAL)
        raise asyncio.CancelledError

    async def _run_forever(self) -> None:
        backoff = RECONNECT_BASE
        while not self._stopping.is_set():
            try:
                await self._connect_once()
                backoff = RECONNECT_BASE  # 정상 종료였으면 백오프 초기화
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 어떤 이유든 다시 붙는다
                log.warning("채팅 연결 끊김: %s", exc)
            finally:
                self._connected.clear()
                self._sid = None

            if self._stopping.is_set():
                return
            log.info("%.0f초 후 재접속합니다", backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _connect_once(self) -> None:
        chat_channel_id = await self._resolve_chat_channel()
        access_token, _extra = await self._api.chat_access_token(chat_channel_id)
        self._chat_channel_id = chat_channel_id

        url = chat_ws_url(chat_channel_id)
        log.info("채팅 서버 접속: %s", url)

        async with connect(
            url,
            open_timeout=15,
            close_timeout=5,
            # 애플리케이션 레벨 ping(cmd 0)을 쓰므로 프로토콜 ping 은 끈다
            ping_interval=None,
            max_size=4 * 1024 * 1024,
        ) as ws:
            self._ws = ws
            await self._send_frame(ws, connect_payload(chat_channel_id, access_token, self._uid))

            ping_task = self._spawn(self._ping_loop(ws), "chat-ping")
            poll_task = self._spawn(self._live_poll_loop(chat_channel_id), "chat-poll")
            try:
                await self._recv_loop(ws)
            finally:
                ping_task.cancel()
                poll_task.cancel()
                self._ws = None

    # ------------------------------------------------------------ 루프들
    async def _recv_loop(self, ws: ClientConnection) -> None:
        async for raw in ws:
            if self._stopping.is_set():
                return
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                log.debug("JSON 아닌 프레임 무시")
                continue
            if isinstance(frame, dict):
                await self._handle_frame(ws, frame)

    async def _handle_frame(self, ws: ClientConnection, frame: dict[str, Any]) -> None:
        cmd = frame.get("cmd")

        if cmd == int(Cmd.CONNECTED):
            body = frame.get("bdy")
            self._sid = (body or {}).get("sid") if isinstance(body, dict) else None
            self._connected.set()
            log.info(
                "채팅방 접속 완료 (%s)",
                "읽기+쓰기" if self.can_send else "읽기 전용",
            )
            if self._sid and self._chat_channel_id:
                with contextlib.suppress(Exception):
                    await self._send_frame(
                        ws, recent_chat_payload(self._chat_channel_id, self._sid, 30)
                    )
            return

        if cmd == int(Cmd.PING):
            await self._send_frame(ws, pong_payload())
            return

        if cmd == int(Cmd.PONG):
            return

        for chat in parse_chat_frame(frame):
            if chat.hidden or not chat.message:
                continue
            # 우리가 보낸 채팅이 메아리로 돌아온 것은 무시
            if self._uid and chat.user_id_hash == self._uid:
                continue
            if self._on_chat is None:
                continue
            try:
                result = self._on_chat(chat)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                log.exception("채팅 처리 중 오류")

    async def _ping_loop(self, ws: ClientConnection) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            try:
                await self._send_frame(ws, ping_payload())
            except Exception:  # noqa: BLE001
                return

    async def _live_poll_loop(self, current_chat_channel_id: str) -> None:
        """방송이 재시작되면 chatChannelId 가 바뀐다. 바뀌면 재접속하도록 끊는다."""
        while True:
            await asyncio.sleep(LIVE_POLL_INTERVAL)
            try:
                status = await self._api.live_status(self._channel_id)
            except ChzzkError:
                continue
            if not status.is_live:
                log.info("방송이 종료되었습니다")
                await self._drop_connection()
                return
            if status.chat_channel_id and status.chat_channel_id != current_chat_channel_id:
                log.info("채팅 채널이 바뀌었습니다. 재접속합니다")
                await self._drop_connection()
                return

    async def _drop_connection(self) -> None:
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    # ------------------------------------------------------------ 전송
    async def _send_frame(self, ws: ClientConnection, payload: dict[str, Any]) -> None:
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def send(self, message: str) -> bool:
        """채팅 전송. 성공하면 True."""
        message = message.strip()
        if not message:
            return False
        if not self.can_send:
            log.warning("아직 채팅을 보낼 수 없는 상태입니다")
            return False

        ws = self._ws
        sid = self._sid
        chat_channel_id = self._chat_channel_id
        if ws is None or sid is None or chat_channel_id is None:
            return False

        async with self._send_lock:
            try:
                await self._send_frame(
                    ws, send_chat_payload(chat_channel_id, sid, message)
                )
            except (websockets.WebSocketException, OSError) as exc:
                log.warning("채팅 전송 실패: %s", exc)
                return False
        return True

    async def _sleep(self, seconds: float) -> None:
        """중단 신호가 오면 바로 깨어나는 sleep."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
