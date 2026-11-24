"""들린 말 버퍼와 채팅 활동량."""

from chatbot.brain.transcript import ChatActivity, TranscriptBuffer


class TestTranscriptBuffer:
    def test_add_and_read(self):
        tr = TranscriptBuffer()
        assert tr.is_empty
        tr.add("첫 마디", now=100.0)
        tr.add("둘째 마디", now=101.0)
        assert not tr.is_empty
        assert tr.recent_text(10, now=102.0) == "첫 마디 둘째 마디"

    def test_ignores_blank(self):
        tr = TranscriptBuffer()
        assert tr.add("   ", now=1.0) is None
        assert tr.is_empty

    def test_window_drops_old(self):
        tr = TranscriptBuffer(window_sec=60)
        tr.add("옛날 말", now=100.0)
        tr.add("최근 말", now=200.0)
        assert tr.recent_text(1000, now=200.0) == "최근 말"

    def test_recent_respects_seconds(self):
        tr = TranscriptBuffer()
        tr.add("30초 전", now=100.0)
        tr.add("방금", now=125.0)
        assert tr.recent_text(10, now=130.0) == "방금"
        assert len(tr.recent(60, now=130.0)) == 2

    def test_seconds_since_last(self):
        tr = TranscriptBuffer()
        assert tr.seconds_since_last(now=100.0) is None
        tr.add("말", now=100.0)
        assert tr.seconds_since_last(now=130.0) == 30.0

    def test_mark_and_unreacted(self):
        tr = TranscriptBuffer()
        tr.add("반응한 말", now=100.0)
        tr.mark(now=101.0)
        assert tr.unreacted() == []
        assert tr.unreacted_chars() == 0
        tr.add("새 말", now=102.0)
        assert [u.text for u in tr.unreacted()] == ["새 말"]
        assert tr.unreacted_chars() == len("새 말")

    def test_max_items_cap(self):
        tr = TranscriptBuffer(max_items=3)
        for i in range(10):
            tr.add(f"말{i}", now=100.0 + i)
        assert len(tr.recent(1000, now=120.0)) == 3


class TestChatActivity:
    def test_counts_within_window(self):
        act = ChatActivity(window_sec=60)
        for i in range(5):
            act.add(f"사람{i}", "ㅋㅋ", now=100.0)
        assert act.count(now=130.0) == 5
        assert act.count(now=200.0) == 0  # 창 밖으로 나감

    def test_ignores_blank(self):
        act = ChatActivity()
        act.add("사람", "   ", now=1.0)
        assert act.count(now=1.0) == 0

    def test_recent_returns_latest(self):
        act = ChatActivity()
        for i in range(10):
            act.add(f"사람{i}", f"메시지{i}", now=100.0)
        recent = act.recent(3)
        assert recent == [("사람7", "메시지7"), ("사람8", "메시지8"), ("사람9", "메시지9")]
