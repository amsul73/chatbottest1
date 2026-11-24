"""치지직 채팅 WebSocket 프로토콜.

⚠️ 치지직은 봇용 공식 채팅 API 를 공개하지 않았다. 아래 내용은 공개된
   커뮤니티 구현체에서 확인한 "비공식" 프로토콜이며, 네이버가 예고 없이
   바꿀 수 있다. 어느 날 갑자기 멈추면 먼저 이 파일을 의심하면 된다.

이 모듈은 순수 함수만 둔다(네트워크 없음) → 테스트하기 쉽게.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

SVC_ID = "game"
VER = "2"
DEV_TYPE = 2001  # PC 웹

# 치지직 채팅 서버는 kr-ss1 ~ kr-ss9 로 샤딩되어 있다.
CHAT_SERVER_COUNT = 9


class Cmd(IntEnum):
    """WebSocket 프레임의 cmd 값."""

    PING = 0
    PONG = 10000
    CONNECT = 100
    CONNECTED = 10100
    REQUEST_RECENT_CHAT = 5101
    RECENT_CHAT = 15101
    SEND_CHAT = 3101
    EVENT = 93006
    CHAT = 93101
    DONATION = 93102
    KICK = 94005
    BLOCK = 94006
    BLIND = 94008
    NOTICE = 94010
    PENALTY = 94015


class MsgType(IntEnum):
    """채팅 메시지 종류(msgTypeCode)."""

    TEXT = 1
    IMAGE = 2
    STICKER = 3
    VIDEO = 4
    RICH = 5
    DONATION = 10
    SUBSCRIPTION = 11
    SYSTEM_MESSAGE = 30


def chat_server_id(chat_channel_id: str) -> int:
    """chatChannelId 로부터 접속할 채팅 서버 번호(1~9)를 구한다."""
    if not chat_channel_id:
        raise ValueError("chat_channel_id 가 비어 있습니다")
    total = sum(ord(c) for c in chat_channel_id)
    return abs(total) % CHAT_SERVER_COUNT + 1


def chat_ws_url(chat_channel_id: str) -> str:
    return f"wss://kr-ss{chat_server_id(chat_channel_id)}.chat.naver.com/chat"


def _base(chat_channel_id: str) -> dict[str, Any]:
    return {"cid": chat_channel_id, "svcid": SVC_ID, "ver": VER}


def connect_payload(
    chat_channel_id: str,
    access_token: str,
    uid: str | None = None,
) -> dict[str, Any]:
    """접속 핸드셰이크.

    uid(userIdHash)가 있으면 auth="SEND" → 채팅을 보낼 수 있다.
    없으면 auth="READ" → 읽기 전용.
    """
    return {
        "bdy": {
            "accTkn": access_token,
            "auth": "SEND" if uid else "READ",
            "devType": DEV_TYPE,
            "uid": uid,
        },
        "cmd": int(Cmd.CONNECT),
        "tid": 1,
        **_base(chat_channel_id),
    }


def recent_chat_payload(
    chat_channel_id: str, sid: str, count: int = 50
) -> dict[str, Any]:
    return {
        "bdy": {"recentMessageCount": count},
        "cmd": int(Cmd.REQUEST_RECENT_CHAT),
        "sid": sid,
        "tid": 2,
        **_base(chat_channel_id),
    }


def send_chat_payload(
    chat_channel_id: str,
    sid: str,
    message: str,
    msg_time: int | None = None,
    emojis: dict[str, str] | None = None,
) -> dict[str, Any]:
    """채팅 전송 프레임.

    extras 는 JSON 객체가 아니라 'JSON 을 문자열로 담은' 필드다. 서버가
    그렇게 기대하므로 json.dumps 한 문자열을 넣어야 한다.
    """
    extras = {
        "chatType": "STREAMING",
        "emojis": emojis or {},
        "osType": "PC",
        "streamingChannelId": chat_channel_id,
    }
    return {
        "bdy": {
            "extras": json.dumps(extras, ensure_ascii=False),
            "msg": message,
            "msgTime": msg_time if msg_time is not None else int(time.time() * 1000),
            "msgTypeCode": int(MsgType.TEXT),
        },
        "retry": False,
        "cmd": int(Cmd.SEND_CHAT),
        "sid": sid,
        "tid": 3,
        **_base(chat_channel_id),
    }


def ping_payload() -> dict[str, Any]:
    return {"cmd": int(Cmd.PING), "ver": VER}


def pong_payload() -> dict[str, Any]:
    return {"cmd": int(Cmd.PONG), "ver": VER}


# --------------------------------------------------------------------------
# 수신 파싱
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class IncomingChat:
    """채팅창에 올라온 메시지 한 개."""

    user_id_hash: str
    nickname: str
    message: str
    msg_type: int
    time_ms: int
    role: str = "common_user"
    is_recent: bool = False
    hidden: bool = False

    @property
    def is_text(self) -> bool:
        return self.msg_type == int(MsgType.TEXT)


def _loads(value: Any) -> dict[str, Any]:
    """profile / extras 는 JSON 문자열로 온다. None 이거나 깨져도 죽지 않게."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse_chat_frame(frame: dict[str, Any]) -> list[IncomingChat]:
    """CHAT / RECENT_CHAT / DONATION 프레임을 IncomingChat 목록으로."""
    cmd = frame.get("cmd")
    if cmd not in (
        int(Cmd.CHAT),
        int(Cmd.RECENT_CHAT),
        int(Cmd.DONATION),
    ):
        return []

    body = frame.get("bdy")
    if isinstance(body, dict):
        items = body.get("messageList") or []
    elif isinstance(body, list):
        items = body
    else:
        return []

    is_recent = cmd == int(Cmd.RECENT_CHAT)
    out: list[IncomingChat] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        profile = _loads(item.get("profile"))
        message = item.get("msg") or item.get("content") or ""
        raw_type = item.get("msgTypeCode") or item.get("messageTypeCode") or 0
        status = item.get("msgStatusType") or item.get("messageStatusType") or ""
        try:
            msg_type = int(raw_type)
        except (TypeError, ValueError):
            msg_type = 0
        out.append(
            IncomingChat(
                user_id_hash=profile.get("userIdHash") or "",
                # 익명 후원은 profile 이 없다.
                nickname=profile.get("nickname") or "익명",
                message=str(message),
                msg_type=msg_type,
                time_ms=int(item.get("msgTime") or item.get("messageTime") or 0),
                role=profile.get("userRoleCode") or "common_user",
                is_recent=is_recent,
                hidden=status == "HIDDEN",
            )
        )
    return out
