"""언제 말할지 — 도배 방지 규칙."""

from chatbot.brain.director import Director, classify
from chatbot.brain.transcript import ChatActivity, TranscriptBuffer
from chatbot.config import DirectorConfig, HumanChatBackoffConfig

CFG = DirectorConfig(
    min_interval_sec=45,
    max_per_10min=8,
    lull_sec=25,
    min_new_chars=25,
    startup_grace_sec=20,
    attempt_interval_sec=10,
)


def fresh(now=1000.0, cfg=CFG):
    """시작 유예가 이미 지난 상태의 Director."""
    d = Director(cfg)
    d.reset_clock(now - cfg.startup_grace_sec - 1)
    return d


class TestClassify:
    def test_questions(self):
        for text in (
            "이거 어떻게 하는 거지",
            "다들 저녁 먹었어요?",
            "이게 맞나",
            "왜 이게 안 되지",
            "누가 이거 해봤어",
        ):
            assert classify(text) == "question", text

    def test_address_chat(self):
        assert classify("채팅창 조용하네") == "address_chat"
        assert classify("여러분 잠깐만요") == "address_chat"

    def test_reaction(self):
        assert classify("ㅋㅋㅋㅋ 대박") == "reaction"
        assert classify("아 진짜 아깝다") == "reaction"

    def test_plain_statements_are_not_triggers(self):
        for text in (
            "음 그래서 이렇게 하면 됩니다",
            "자 그럼 다음 판 가볼게요",
            "오늘 날씨 좋네요",
            "왜냐하면 이게 더 좋아서",
        ):
            assert classify(text) is None, text

    def test_empty(self):
        assert classify("") is None
        assert classify("   ") is None


class TestGates:
    def test_startup_grace_blocks(self):
        d = Director(CFG)
        t = Director(CFG)._started_at  # noqa: SLF001
        tr = TranscriptBuffer()
        tr.add("다들 있어요?", now=t)
        assert not d.decide(tr, ChatActivity(), now=t + 5).speak

    def test_nothing_heard_yet(self):
        d = fresh()
        assert not d.decide(TranscriptBuffer(), ChatActivity(), now=1000.0).speak

    def test_fires_on_question(self):
        d = fresh()
        tr = TranscriptBuffer()
        tr.add("다들 저녁 먹었어요?", now=999.0)
        decision = d.decide(tr, ChatActivity(), now=1000.0)
        assert decision.speak
        assert decision.trigger == "question"

    def test_cooldown_blocks_next_message(self):
        d = fresh()
        tr = TranscriptBuffer()
        tr.add("다들 있어요?", now=999.0)
        assert d.decide(tr, ChatActivity(), now=1000.0).speak
        d.note_sent(now=1000.0)

        tr.add("또 질문이에요?", now=1010.0)
        assert not d.decide(tr, ChatActivity(), now=1010.0).speak
        # 45초 지나면 풀린다
        tr.add("이제 되나요?", now=1046.0)
        assert d.decide(tr, ChatActivity(), now=1046.0).speak

    def test_attempt_interval_blocks_repeat_asks(self):
        """안 보내기로 했어도 2초마다 Claude 를 두드리지 않는다."""
        d = fresh()
        tr = TranscriptBuffer()
        tr.add("뭐지 이거", now=999.0)
        assert d.decide(tr, ChatActivity(), now=1000.0).speak
        d.note_attempt(now=1000.0)
        tr.add("이건 또 뭐지", now=1002.0)
        assert not d.decide(tr, ChatActivity(), now=1002.0).speak
        tr.add("아직도 뭐지", now=1011.0)
        assert d.decide(tr, ChatActivity(), now=1011.0).speak

    def test_ten_minute_cap(self):
        d = fresh()
        tr = TranscriptBuffer()
        now = 1000.0
        for i in range(CFG.max_per_10min):
            d.note_sent(now=now + i)
        tr.add("질문이요?", now=now + 500)
        assert not d.decide(tr, ChatActivity(), now=now + 500).speak
        assert d.sent_last_10min(now=now + 500) == CFG.max_per_10min
        # 10분이 지나면 창이 비워진다
        assert d.sent_last_10min(now=now + 1200) == 0

    def test_backs_off_when_humans_are_chatting(self):
        cfg = DirectorConfig(
            min_interval_sec=40,
            startup_grace_sec=0,
            attempt_interval_sec=0,
            human_chat_backoff=HumanChatBackoffConfig(
                window_sec=60, messages=5, interval_multiplier=3.0
            ),
        )
        d = fresh(cfg=cfg)
        quiet, busy = ChatActivity(60), ChatActivity(60)
        # 판단 시점(1061초)에서 1분 창 안에 들어오도록 최근에 쌓는다
        for i in range(6):
            busy.add(f"사람{i}", "ㅋㅋㅋ", now=1050.0)

        assert d.required_interval(quiet, now=1061.0) == 40
        assert d.required_interval(busy, now=1061.0) == 120

        tr = TranscriptBuffer()
        tr.add("다들 있어요?", now=999.0)
        d.note_sent(now=1000.0)
        # 마지막 전송 61초 뒤: 조용하면 OK, 사람들이 떠들면 아직 대기
        assert d.decide(tr, quiet, now=1061.0).speak
        assert not d.decide(tr, busy, now=1061.0).speak
        # 2분이 지나면 떠드는 중에도 풀린다
        assert d.decide(tr, busy, now=1121.0).speak


class TestTriggerPriority:
    def test_lull_when_streamer_goes_quiet(self):
        d = fresh()
        tr = TranscriptBuffer()
        tr.add("음 이거를 이렇게", now=1000.0)
        tr.mark(now=1000.0)  # 이미 반응했다고 표시
        assert not d.decide(tr, ChatActivity(), now=1010.0).speak
        decision = d.decide(tr, ChatActivity(), now=1030.0)
        assert decision.speak
        assert decision.trigger == "lull"

    def test_flow_when_enough_new_content(self):
        d = fresh()
        tr = TranscriptBuffer()
        # 트리거 단어가 없는 평서문을 25자 넘게 쌓는다
        tr.add("음 그래서 이렇게 하면 됩니다 자 그럼 계속 진행해 볼게요", now=999.0)
        decision = d.decide(tr, ChatActivity(), now=1000.0)
        assert decision.speak
        assert decision.trigger == "flow"

    def test_not_enough_new_content(self):
        d = fresh()
        tr = TranscriptBuffer()
        tr.add("네", now=999.0)
        assert not d.decide(tr, ChatActivity(), now=1000.0).speak

    def test_mark_prevents_reacting_twice(self):
        d = fresh()
        tr = TranscriptBuffer()
        tr.add("다들 저녁 먹었어요?", now=999.0)
        assert d.decide(tr, ChatActivity(), now=1000.0).speak
        tr.mark(now=1000.0)
        d.note_attempt(now=1000.0)
        # 같은 말에 다시 반응하지 않는다(lull 조건에 닿기 전까지)
        assert not d.decide(tr, ChatActivity(), now=1015.0).speak
