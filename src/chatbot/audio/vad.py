"""아주 단순한 음량 기반 발화 구간 분리기(VAD).

왜 직접 만들었나: 끝없이 흘러오는 방송 오디오를 "문장 단위"로 잘라야
음성인식에 넘길 수 있다. webrtcvad 같은 외부 C 라이브러리를 깔지 않아도
방송 오디오(배경음악이 깔린 말소리)에는 음량 기반으로 충분히 쓸 만하다.

입력: 16bit little-endian mono PCM 바이트
출력: 말소리로 보이는 구간들(Segment)
"""

from __future__ import annotations

import array
import math
from dataclasses import dataclass

BYTES_PER_SAMPLE = 2
FRAME_MS = 20


@dataclass(frozen=True)
class Segment:
    """말소리 한 덩어리."""

    pcm: bytes
    sample_rate: int

    @property
    def duration_sec(self) -> float:
        return len(self.pcm) / (self.sample_rate * BYTES_PER_SAMPLE)


def frame_rms(frame: bytes) -> float:
    """프레임의 RMS 음량을 0.0~1.0 으로."""
    if not frame:
        return 0.0
    samples = array.array("h")
    # 홀수 바이트가 남으면 버린다(프레임 경계가 깨진 경우)
    usable = len(frame) - (len(frame) % BYTES_PER_SAMPLE)
    if usable <= 0:
        return 0.0
    samples.frombytes(frame[:usable])
    total = 0
    for s in samples:
        total += s * s
    return math.sqrt(total / len(samples)) / 32768.0


class SpeechSegmenter:
    """PCM 을 흘려 넣으면 말소리 구간을 뱉는다.

    사용:
        seg = SpeechSegmenter(16000)
        for segment in seg.feed(pcm_chunk):
            ...
        for segment in seg.flush():   # 끝날 때
            ...
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        silence_threshold: float = 0.012,
        min_speech_sec: float = 0.7,
        max_segment_sec: float = 12.0,
        trailing_silence_sec: float = 0.45,
        preroll_sec: float = 0.25,
        keep_tail_sec: float = 0.15,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate 는 양수여야 합니다")

        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.min_speech_sec = min_speech_sec
        self.max_segment_sec = max_segment_sec
        self.trailing_silence_sec = trailing_silence_sec
        self.keep_tail_sec = min(keep_tail_sec, trailing_silence_sec)

        self._frame_bytes = int(sample_rate * FRAME_MS / 1000) * BYTES_PER_SAMPLE
        self._buf = bytearray()          # 아직 프레임이 안 찬 잔여 바이트
        self._speech = bytearray()       # 현재 모으고 있는 말소리
        self._preroll = bytearray()      # 말 시작 직전 조각(앞이 잘리지 않게)
        self._preroll_max = max(1, int(preroll_sec * 1000 / FRAME_MS)) * self._frame_bytes
        self._silence_run = 0.0          # 연속 무음 길이(초)
        self._voiced_sec = 0.0           # 실제 말소리로 판정된 누적 길이
        self._in_speech = False

    # ------------------------------------------------------------------
    @property
    def _frame_sec(self) -> float:
        return FRAME_MS / 1000.0

    def _bytes_to_sec(self, n: int) -> float:
        return n / (self.sample_rate * BYTES_PER_SAMPLE)

    def feed(self, pcm: bytes) -> list[Segment]:
        """PCM 을 넣고, 완성된 구간들을 돌려받는다."""
        self._buf.extend(pcm)
        out: list[Segment] = []

        while len(self._buf) >= self._frame_bytes:
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            out.extend(self._consume_frame(frame))

        return out

    def _consume_frame(self, frame: bytes) -> list[Segment]:
        out: list[Segment] = []
        loud = frame_rms(frame) >= self.silence_threshold

        if not self._in_speech:
            if loud:
                # 말이 시작됐다. 직전 조각을 앞에 붙여 시작이 잘리지 않게.
                self._in_speech = True
                self._silence_run = 0.0
                self._voiced_sec = self._frame_sec
                self._speech = bytearray(self._preroll)
                self._speech.extend(frame)
                self._preroll.clear()
            else:
                self._preroll.extend(frame)
                if len(self._preroll) > self._preroll_max:
                    del self._preroll[: len(self._preroll) - self._preroll_max]
            return out

        # 말하는 중
        self._speech.extend(frame)
        if loud:
            self._silence_run = 0.0
            self._voiced_sec += self._frame_sec
        else:
            self._silence_run += self._frame_sec

        if self._silence_run >= self.trailing_silence_sec:
            segment = self._close_segment()
            if segment is not None:
                out.append(segment)
        elif self._bytes_to_sec(len(self._speech)) >= self.max_segment_sec:
            # 말이 너무 길면 일단 한 번 끊어서 넘긴다(쉬지 않고 떠드는 경우)
            segment = self._close_segment(force=True)
            if segment is not None:
                out.append(segment)
        return out

    def _close_segment(self, force: bool = False) -> Segment | None:
        pcm = bytes(self._speech)
        voiced_sec = self._voiced_sec
        trim_sec = max(0.0, self._silence_run - self.keep_tail_sec)

        self._speech.clear()
        self._in_speech = False
        self._silence_run = 0.0
        self._voiced_sec = 0.0
        self._preroll.clear()

        # 길이 판정은 앞뒤 무음 패딩이 아니라 '실제 말소리 길이'로 한다.
        # 그러지 않으면 0.1초짜리 기침도 패딩 덕에 기준을 넘어버린다.
        if voiced_sec < self.min_speech_sec and not force:
            return None
        if not pcm:
            return None

        # 꼬리 무음은 조금만 남기고 잘라낸다(음성인식에 넘길 양을 줄임).
        trim_bytes = int(trim_sec * self.sample_rate) * BYTES_PER_SAMPLE
        if 0 < trim_bytes < len(pcm):
            pcm = pcm[: len(pcm) - trim_bytes]

        return Segment(pcm=pcm, sample_rate=self.sample_rate)

    def flush(self) -> list[Segment]:
        """남은 걸 모두 내보낸다(스트림 종료 시)."""
        if not self._in_speech:
            self._buf.clear()
            return []
        if self._buf:
            self._speech.extend(self._buf)
            self._buf.clear()
        segment = self._close_segment()
        return [segment] if segment is not None else []
