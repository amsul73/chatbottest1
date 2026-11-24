"""발화 구간 분리기."""

from chatbot.audio.vad import Segment, SpeechSegmenter, frame_rms

SR = 16000


def feed_all(seg: SpeechSegmenter, data: bytes, step: int = 1234):
    out = []
    for i in range(0, len(data), step):
        out += seg.feed(data[i : i + step])
    return out + seg.flush()


def test_frame_rms():
    assert frame_rms(b"\x00\x00" * 160) == 0.0
    assert frame_rms(b"") == 0.0
    assert frame_rms(b"\x01") == 0.0  # 홀수 바이트도 안 터짐


def test_splits_on_silence(make_tone, make_silence):
    seg = SpeechSegmenter(SR)
    stream = (
        make_silence(0.5)
        + make_tone(1.5)
        + make_silence(1.0)
        + make_tone(2.0)
        + make_silence(1.0)
    )
    segments = feed_all(seg, stream)
    assert len(segments) == 2
    # 앞 0.25초 + 꼬리 0.15초 패딩이 붙는다
    assert 1.6 <= segments[0].duration_sec <= 2.1
    assert 2.1 <= segments[1].duration_sec <= 2.6


def test_short_blip_is_dropped(make_tone, make_silence):
    """기침·클릭음(0.1초)은 앞뒤 패딩 때문에 기준을 넘어도 버려야 한다."""
    seg = SpeechSegmenter(SR, min_speech_sec=0.7)
    segments = feed_all(seg, make_silence(0.5) + make_tone(0.1) + make_silence(1.0))
    assert segments == []


def test_pure_silence_yields_nothing(make_silence):
    seg = SpeechSegmenter(SR)
    assert feed_all(seg, make_silence(5.0)) == []


def test_long_speech_is_force_split(make_tone):
    seg = SpeechSegmenter(SR, max_segment_sec=1.0)
    segments = feed_all(seg, make_tone(3.3))
    assert len(segments) >= 3
    assert all(s.duration_sec <= 1.05 for s in segments)


def test_flush_emits_pending_speech(make_tone, make_silence):
    seg = SpeechSegmenter(SR)
    # 무음으로 끝나지 않아도 flush 하면 나와야 한다
    out = seg.feed(make_silence(0.3) + make_tone(1.5))
    assert out == []
    flushed = seg.flush()
    assert len(flushed) == 1
    assert flushed[0].duration_sec >= 1.4


def test_chunk_size_does_not_change_result(make_tone, make_silence):
    stream = make_silence(0.3) + make_tone(1.2) + make_silence(0.9) + make_tone(1.1) + make_silence(0.9)
    results = []
    for step in (320, 1234, 7919, len(stream)):
        results.append(
            [round(s.duration_sec, 2) for s in feed_all(SpeechSegmenter(SR), stream, step)]
        )
    assert all(r == results[0] for r in results), results


def test_quiet_speech_needs_lower_threshold(make_tone, make_silence):
    quiet = make_tone(1.5, amplitude=200)
    assert feed_all(SpeechSegmenter(SR, silence_threshold=0.05), make_silence(0.3) + quiet) == []
    loud_enough = feed_all(
        SpeechSegmenter(SR, silence_threshold=0.003), make_silence(0.3) + quiet
    )
    assert len(loud_enough) == 1


def test_segment_duration_math():
    assert Segment(b"\x00\x00" * SR, SR).duration_sec == 1.0
