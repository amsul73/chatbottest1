"""치지직 비공식 REST API.

필요한 것 네 가지만 쓴다.
  1. 방송 상태 + chatChannelId   (채팅방 접속용)
  2. 방송 재생 정보              (HLS 오디오 주소 → 음성인식용)
  3. 채팅 accessToken            (채팅방 접속용)
  4. 내 userIdHash               (채팅 전송 권한용)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx

log = logging.getLogger(__name__)

CHZZK_BASE = "https://api.chzzk.naver.com"
GAME_BASE = "https://comm-api.game.naver.com/nng_main"

# 평범한 브라우저처럼 보이게. 이 헤더가 없으면 일부 엔드포인트가 막힌다.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# 연령 제한 방송인데 로그인 안 했을 때 돌아오는 코드
CODE_ADULT_ONLY = 42601


class ChzzkError(Exception):
    """치지직 API 가 기대와 다르게 응답했을 때."""


class NotLiveError(ChzzkError):
    """방송이 켜져 있지 않음."""


@dataclass
class LiveStatus:
    status: str  # "OPEN" | "CLOSE"
    chat_channel_id: str | None
    title: str = ""
    viewers: int = 0
    adult: bool = False

    @property
    def is_live(self) -> bool:
        return self.status == "OPEN"


class ChzzkApi:
    """치지직 REST 클라이언트. `async with` 로 쓴다."""

    def __init__(
        self,
        nid_aut: str | None = None,
        nid_ses: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._nid_aut = nid_aut
        self._nid_ses = nid_ses
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.has_auth:
            headers["Cookie"] = f"NID_AUT={nid_aut}; NID_SES={nid_ses}"
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    @property
    def has_auth(self) -> bool:
        return bool(self._nid_aut and self._nid_ses)

    async def __aenter__(self) -> ChzzkApi:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- 내부
    async def _get_content(self, url: str) -> dict[str, Any]:
        """치지직 응답은 {"code":200,"content":{...}} 형태로 감싸져 온다."""
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise ChzzkError(f"{url} 요청 실패: {exc}") from exc

        if resp.status_code >= 400:
            raise ChzzkError(f"{url} → HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ChzzkError(f"{url} → JSON 아님 (로그인/차단 의심)") from exc

        if not isinstance(data, dict):
            raise ChzzkError(f"{url} → 예상과 다른 응답")

        code = data.get("code")
        if code == CODE_ADULT_ONLY:
            raise ChzzkError(
                "연령 제한 방송입니다. .env 에 NID_AUT / NID_SES 를 넣어야 접속할 수 있습니다."
            )
        if code not in (200, None):
            raise ChzzkError(f"{url} → code={code} message={data.get('message')!r}")

        content = data.get("content")
        return content if isinstance(content, dict) else {}

    # ---------------------------------------------------------------- 공개
    async def live_status(self, channel_id: str) -> LiveStatus:
        """방송 켜짐 여부와 chatChannelId."""
        content = await self._get_content(
            f"{CHZZK_BASE}/polling/v2/channels/{channel_id}/live-status"
        )
        if not content:
            raise ChzzkError(
                f"채널을 찾을 수 없습니다: {channel_id} (채널 ID가 맞는지 확인해주세요)"
            )
        return LiveStatus(
            status=str(content.get("status") or "CLOSE"),
            chat_channel_id=content.get("chatChannelId"),
            title=str(content.get("liveTitle") or ""),
            viewers=int(content.get("concurrentUserCount") or 0),
            adult=bool(content.get("adult")),
        )

    async def chat_access_token(self, chat_channel_id: str) -> tuple[str, str]:
        """(accessToken, extraToken)."""
        content = await self._get_content(
            f"{GAME_BASE}/v1/chats/access-token"
            f"?channelId={chat_channel_id}&chatType=STREAMING"
        )
        token = content.get("accessToken")
        if not token:
            raise ChzzkError("채팅 accessToken 을 받지 못했습니다")
        return str(token), str(content.get("extraToken") or "")

    async def user_id_hash(self) -> str | None:
        """로그인한 계정의 userIdHash. 쿠키가 없으면 None."""
        if not self.has_auth:
            return None
        content = await self._get_content(f"{GAME_BASE}/v1/user/getUserStatus")
        if not content.get("loggedIn"):
            log.warning("네이버 쿠키가 만료된 것 같습니다 (loggedIn=false)")
            return None
        uid = content.get("userIdHash")
        if uid:
            log.info("로그인 확인: %s", content.get("nickname") or uid)
        return str(uid) if uid else None

    async def live_audio_url(
        self, channel_id: str, prefer_audio_only: bool = True
    ) -> str:
        """음성인식에 쓸 HLS 주소.

        가능하면 오디오 전용 트랙을 쓴다(대역폭/CPU 절약). 없으면 그냥
        HLS 주소를 주고 ffmpeg 가 영상을 버리게 한다.
        """
        content = await self._get_content(
            f"{CHZZK_BASE}/service/v2/channels/{channel_id}/live-detail"
        )
        if str(content.get("status") or "") != "OPEN":
            raise NotLiveError("방송이 꺼져 있습니다")

        playback = content.get("livePlaybackJson")
        if isinstance(playback, str):
            try:
                playback = json.loads(playback)
            except ValueError as exc:
                raise ChzzkError("livePlaybackJson 파싱 실패") from exc
        if not isinstance(playback, dict):
            raise ChzzkError("재생 정보를 받지 못했습니다 (비공개/성인 방송?)")

        media_list = playback.get("media")
        if not isinstance(media_list, list) or not media_list:
            raise ChzzkError("재생 가능한 미디어가 없습니다")

        hls = next(
            (
                m
                for m in media_list
                if isinstance(m, dict)
                and str(m.get("protocol") or "").upper() == "HLS"
            ),
            None,
        )
        if hls is None:
            hls = media_list[0]

        if prefer_audio_only:
            for track in hls.get("encodingTrack") or []:
                if isinstance(track, dict) and track.get("audioOnly") and track.get("path"):
                    log.info("오디오 전용 트랙 사용")
                    return str(track["path"])

        path = hls.get("path")
        if not path:
            raise ChzzkError("HLS 주소를 찾지 못했습니다")
        return str(path)
