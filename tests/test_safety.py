"""보내기 직전 필터."""

import pytest

from chatbot.brain.safety import MessageFilter, Rejected, normalize
from chatbot.config import SafetyConfig


def mk(**kw):
    cfg = SafetyConfig(
        max_chars=kw.pop("max_chars", 90),
        dedupe_window=kw.pop("dedupe_window", 12),
        similarity_limit=kw.pop("similarity_limit", 0.82),
        blocked_words=kw.pop("blocked_words", []),
    )
    return MessageFilter(cfg)


class TestCleaning:
    def test_strips_quotes_and_whitespace(self):
        assert mk().clean('  "오 그거 좋네요"  ') == "오 그거 좋네요"
        assert mk().clean("“완전 공감”") == "완전 공감"

    def test_collapses_newlines(self):
        # 채팅은 여러 줄을 보낼 수 없다
        assert mk().clean("대박\n진짜요?") == "대박 진짜요?"
        assert "\n" not in mk().clean("a\n\n\nb")

    def test_strips_markdown(self):
        assert mk().clean("**대박**") == "대박"
        assert mk().clean("- 그거 맞아요") == "그거 맞아요"

    def test_strips_leading_mention(self):
        assert mk().clean("@스트리머 화이팅") == "화이팅"

    def test_keeps_korean_chat_particles(self):
        assert mk().clean("ㅋㅋㅋ") == "ㅋㅋㅋ"
        assert mk().clean("ㅠㅠ 아깝다") == "ㅠㅠ 아깝다"


class TestRejection:
    def test_rejects_empty(self):
        for bad in ("", "   ", "\n\n"):
            with pytest.raises(Rejected):
                mk().clean(bad)

    def test_rejects_links(self):
        for bad in ("여기 http://spam.com 보세요", "www.example.com 가봐"):
            with pytest.raises(Rejected, match="링크"):
                mk().clean(bad)

    def test_rejects_blocked_words(self):
        f = mk(blocked_words=["도박", "투자"])
        with pytest.raises(Rejected, match="금지어"):
            f.clean("이거 도박 아니에요?")
        assert f.clean("이건 괜찮은 말") == "이건 괜찮은 말"

    def test_rejects_content_free_message(self):
        with pytest.raises(Rejected):
            mk().clean("...")


class TestLength:
    def test_truncates_at_word_boundary(self):
        f = mk(max_chars=20)
        out = f.clean("이렇게 아주 길게 이야기를 늘어놓으면 잘려야 정상입니다")
        assert len(out) <= 20
        assert not out.endswith(" ")

    def test_hard_truncates_when_no_space(self):
        f = mk(max_chars=10)
        out = f.clean("가" * 50)
        assert len(out) == 10

    def test_short_message_untouched(self):
        assert mk(max_chars=90).clean("짧아요") == "짧아요"


class TestDedupe:
    def test_exact_duplicate_rejected(self):
        f = mk()
        msg = f.clean("오 그거 좋네요")
        f.remember(msg)
        with pytest.raises(Rejected, match="중복"):
            f.clean("오 그거 좋네요")

    def test_near_duplicate_rejected(self):
        f = mk()
        f.remember("오 그거 좋네요")
        for similar in ("오 그거 좋네요!", "오 그거 좋네요 ㅋㅋ", "오그거좋네요"):
            with pytest.raises(Rejected, match="중복"):
                f.clean(similar)

    def test_different_message_passes(self):
        f = mk()
        f.remember("오 그거 좋네요")
        assert f.clean("완전 다른 이야기입니다") == "완전 다른 이야기입니다"

    def test_window_forgets_old_messages(self):
        f = mk(dedupe_window=2)
        f.remember("완전히 다른 첫 이야기")
        f.remember("ㅋㅋㅋ 대박이네")
        f.remember("오늘 컨디션 좋아보여요")  # 첫 이야기가 창에서 밀려남
        assert f.clean("완전히 다른 첫 이야기") == "완전히 다른 첫 이야기"

    def test_similarity_is_deliberately_strict_for_short_korean(self):
        """짧은 한국어는 글자가 몇 개만 겹쳐도 비슷하다고 본다.

        의도된 동작이다(애매하면 안 보낸다). 느슨하게 하려면
        safety.similarity_limit 을 올린다.
        """
        strict = mk()
        strict.remember("오 그거 좋네요")
        with pytest.raises(Rejected, match="중복"):
            strict.clean("오 그거 싫네요")

        loose = mk(similarity_limit=0.95)
        loose.remember("오 그거 좋네요")
        assert loose.clean("오 그거 싫네요") == "오 그거 싫네요"
        # 그래도 완전히 같은 말은 막는다
        with pytest.raises(Rejected, match="중복"):
            loose.clean("오 그거 좋네요")

    def test_recent_exposes_history(self):
        f = mk()
        f.remember("하나")
        f.remember("둘")
        assert f.recent == ["하나", "둘"]


def test_normalize():
    assert normalize("오 그거 좋네요!") == "오그거좋네요"
    assert normalize("ABC abc") == "abcabc"
    assert normalize("...") == ""
