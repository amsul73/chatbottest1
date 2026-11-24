"""방송 오디오를 ffmpeg 로 받아 16kHz mono PCM 으로 흘려준다.

스트리머 PC 에 아무것도 설치하지 않아도 되는 대신, 방송 송출 지연
(보통 10~30초)만큼 늦게 듣는다는 한계가 있다.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class FfmpegMissingError(RuntimeError):
    pass


class AudioSourceEnded(RuntimeError):
    """ffmpeg 가 끝났다(방송 종료, 네트워크 끊김 등)."""


class HlsAudioSource:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        sample_rate: int = 16000,
        chunk_ms: int = 200,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.sample_rate = sample_rate
        # 16bit mono 기준 바이트 수
        self.chunk_bytes = max(2, int(sample_rate * chunk_ms / 1000) * 2)
        self._proc: asyncio.subprocess.Process | None = None

    def ensure_available(self) -> None:
        if shutil.which(self.ffmpeg_path) is None:
            raise FfmpegMissingError(
                f"ffmpeg 를 찾을 수 없습니다: {self.ffmpeg_path!r}\n"
                "  Windows: https://ffmpeg.org/download.html 에서 받아 PATH 에 추가\n"
                "  macOS:   brew install ffmpeg\n"
                "  Linux:   sudo apt install ffmpeg"
            )

    def _args(self, source: str) -> list[str]:
        args = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
        ]
        # -user_agent / -reconnect* 는 HTTP 입력에만 있는 옵션이다.
        # 로컬 파일에 붙이면 ffmpeg 가 "Option not found" 로 죽는다.
        if source.startswith(("http://", "https://")):
            args += [
                "-user_agent", USER_AGENT,
                # 끊겼을 때 ffmpeg 가 알아서 다시 붙게
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ]
        args += [
            "-i", source,
            # 영상/자막/데이터는 버리고 오디오만
            "-vn", "-sn", "-dn",
            "-ac", "1",
            "-ar", str(self.sample_rate),
            "-f", "s16le",
            "pipe:1",
        ]
        return args

    async def stream(self, source: str) -> AsyncIterator[bytes]:
        """source(HLS 주소 또는 로컬 파일)의 오디오를 PCM 조각으로 흘려준다."""
        self.ensure_available()
        proc = await asyncio.create_subprocess_exec(
            *self._args(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        self._proc = proc
        assert proc.stdout is not None

        stderr_task = asyncio.create_task(self._drain_stderr(proc))
        try:
            while True:
                chunk = await proc.stdout.read(self.chunk_bytes)
                if not chunk:
                    break
                yield chunk
        finally:
            stderr_task.cancel()
            await self._terminate(proc)
            self._proc = None

        code = proc.returncode
        if code not in (0, None, -15, 255):
            raise AudioSourceEnded(f"ffmpeg 가 코드 {code} 로 종료되었습니다")
        raise AudioSourceEnded("오디오 스트림이 끝났습니다")

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            if text:
                log.debug("ffmpeg: %s", text)

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def stop(self) -> None:
        proc = self._proc
        if proc is not None:
            await self._terminate(proc)
