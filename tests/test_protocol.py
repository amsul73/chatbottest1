"""치지직 채팅 프로토콜 — 프레임을 정확히 만드는지."""

import json

import pytest

from chatbot.chzzk.protocol import (
    Cmd,
    MsgType,
    chat_server_id,
    chat_ws_url,
    connect_payload,
    parse_chat_frame,
    ping_payload,
    pong_payload,
    recent_chat_payload,
    send_chat_payload,
)


def test_server_id_is_stable_and_in_range():
    for cid in ("abc123", "N1W6yx", "000000", "zzzzzz"):
        sid = chat_server_id(cid)
        assert 1 <= sid <= 9
        assert chat_server_id(cid) == sid  # 같은 입력 → 같은 서버
    assert chat_ws_url("abc123").startswith("wss://kr-ss")
    assert chat_ws_url("abc123").endswith(".chat.naver.com/chat")


def test_server_id_rejects_empty():
    with pytest.raises(ValueError):
        chat_server_id("")


def test_connect_payload_send_vs_read():
    with_uid = connect_payload("cid1", "TOKEN", "uid-hash")
    assert with_uid["cmd"] == int(Cmd.CONNECT)
    assert with_uid["bdy"]["auth"] == "SEND"
    assert with_uid["bdy"]["uid"] == "uid-hash"
    assert with_uid["bdy"]["accTkn"] == "TOKEN"
    assert with_uid["bdy"]["devType"] == 2001
    assert with_uid["cid"] == "cid1"
    assert with_uid["svcid"] == "game"
    assert with_uid["ver"] == "2"
    assert with_uid["tid"] == 1

    read_only = connect_payload("cid1", "TOKEN")
    assert read_only["bdy"]["auth"] == "READ"
    assert read_only["bdy"]["uid"] is None


def test_send_chat_payload_shape():
    payload = send_chat_payload("cid1", "SID", "안녕", msg_time=1700000000000)
    assert payload["cmd"] == int(Cmd.SEND_CHAT) == 3101
    assert payload["sid"] == "SID"
    assert payload["retry"] is False
    assert payload["bdy"]["msg"] == "안녕"
    assert payload["bdy"]["msgTypeCode"] == int(MsgType.TEXT)
    assert payload["bdy"]["msgTime"] == 1700000000000

    # extras 는 'JSON 을 담은 문자열' 이어야 한다
    extras = payload["bdy"]["extras"]
    assert isinstance(extras, str)
    parsed = json.loads(extras)
    assert parsed["chatType"] == "STREAMING"
    assert parsed["osType"] == "PC"
    assert parsed["streamingChannelId"] == "cid1"


def test_send_chat_payload_autofills_time():
    payload = send_chat_payload("cid", "sid", "hi")
    assert payload["bdy"]["msgTime"] > 1_600_000_000_000


def test_ping_pong_and_recent():
    assert ping_payload() == {"cmd": 0, "ver": "2"}
    assert pong_payload() == {"cmd": 10000, "ver": "2"}
    recent = recent_chat_payload("cid", "sid", 30)
    assert recent["cmd"] == int(Cmd.REQUEST_RECENT_CHAT)
    assert recent["bdy"]["recentMessageCount"] == 30


def _chat_item(nick="시청자", msg="ㅋㅋ", **extra):
    item = {
        "profile": json.dumps(
            {"userIdHash": "u1", "nickname": nick, "userRoleCode": "common_user"}
        ),
        "msg": msg,
        "msgTypeCode": 1,
        "msgTime": 123,
    }
    item.update(extra)
    return item


def test_parse_chat_list_body():
    chats = parse_chat_frame({"cmd": int(Cmd.CHAT), "bdy": [_chat_item()]})
    assert len(chats) == 1
    assert chats[0].nickname == "시청자"
    assert chats[0].message == "ㅋㅋ"
    assert chats[0].is_text
    assert not chats[0].is_recent


def test_parse_recent_chat_uses_message_list_and_flags_recent():
    frame = {
        "cmd": int(Cmd.RECENT_CHAT),
        "bdy": {"messageList": [_chat_item(msg="옛날 채팅")]},
    }
    chats = parse_chat_frame(frame)
    assert len(chats) == 1
    assert chats[0].is_recent is True


def test_parse_handles_alternate_field_names():
    frame = {
        "cmd": int(Cmd.CHAT),
        "bdy": [
            {
                "profile": json.dumps({"userIdHash": "u", "nickname": "n"}),
                "content": "대체 필드",
                "messageTypeCode": 1,
                "messageTime": 9,
                "messageStatusType": "HIDDEN",
            }
        ],
    }
    chats = parse_chat_frame(frame)
    assert chats[0].message == "대체 필드"
    assert chats[0].time_ms == 9
    assert chats[0].hidden is True


def test_parse_survives_anonymous_and_broken_profile():
    frame = {
        "cmd": int(Cmd.DONATION),
        "bdy": [
            {"profile": None, "msg": "익명후원", "msgTypeCode": 10},
            {"profile": "{깨진 json", "msg": "그래도 살아남기", "msgTypeCode": 1},
        ],
    }
    chats = parse_chat_frame(frame)
    assert [c.nickname for c in chats] == ["익명", "익명"]
    assert chats[0].msg_type == 10


def test_parse_ignores_unrelated_frames():
    assert parse_chat_frame({"cmd": int(Cmd.CONNECTED), "bdy": {"sid": "x"}}) == []
    assert parse_chat_frame({"cmd": int(Cmd.PING)}) == []
    assert parse_chat_frame({}) == []
    assert parse_chat_frame({"cmd": int(Cmd.CHAT), "bdy": None}) == []
