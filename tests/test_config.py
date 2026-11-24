"""설정 로딩과 검증."""

import pytest

from chatbot.config import Config, ConfigError, _build, _load_dotenv, load_config


class TestBuild:
    def test_nested_dataclasses(self):
        cfg = _build(
            Config,
            {
                "channel": {"id": "abc"},
                "director": {
                    "min_interval_sec": 30,
                    "human_chat_backoff": {"messages": 3},
                },
            },
        )
        assert cfg.channel.id == "abc"
        assert cfg.director.min_interval_sec == 30
        assert cfg.director.human_chat_backoff.messages == 3
        # 안 쓴 값은 기본값
        assert cfg.director.human_chat_backoff.window_sec == 60.0
        assert cfg.audio.vad.min_speech_sec == 0.7

    def test_defaults_when_empty(self):
        cfg = _build(Config, {})
        assert cfg.llm.model == "claude-opus-5-5"
        assert cfg.bot.dry_run is True

    def test_typo_in_top_level_key(self):
        with pytest.raises(ConfigError, match="channle"):
            _build(Config, {"channle": {"id": "x"}})

    def test_typo_in_nested_key(self):
        with pytest.raises(ConfigError, match="min_intervl_sec"):
            _build(Config, {"director": {"min_intervl_sec": 1}})

    def test_non_mapping(self):
        with pytest.raises(ConfigError):
            _build(Config, {"channel": "그냥 문자열"})


class TestValidation:
    def _write(self, tmp_path, body):
        path = tmp_path / "config.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_placeholder_channel_id_rejected(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        path = self._write(tmp_path, 'channel:\n  id: "여기에_채널ID"\n')
        with pytest.raises(ConfigError, match="channel.id"):
            load_config(path, tmp_path / ".env")

    def test_bad_stt_backend(self, tmp_path):
        path = self._write(tmp_path, 'channel:\n  id: "abc123"\nstt:\n  backend: "whisper"\n')
        with pytest.raises(ConfigError, match="stt.backend"):
            load_config(path, tmp_path / ".env")

    def test_too_fast_interval_rejected(self, tmp_path):
        path = self._write(
            tmp_path, 'channel:\n  id: "abc123"\ndirector:\n  min_interval_sec: 1\n'
        )
        with pytest.raises(ConfigError, match="min_interval_sec"):
            load_config(path, tmp_path / ".env")

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="설정 파일이 없습니다"):
            load_config(tmp_path / "nope.yaml", tmp_path / ".env")

    def test_valid_config_loads(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("NID_AUT", "aut")
        monkeypatch.setenv("NID_SES", "ses")
        path = self._write(tmp_path, 'channel:\n  id: "abc123"\nbot:\n  dry_run: false\n')
        cfg = load_config(path, tmp_path / ".env")
        assert cfg.channel.id == "abc123"
        assert cfg.bot.dry_run is False
        assert cfg.secrets.anthropic_api_key == "sk-test"
        assert cfg.secrets.can_send_chat is True


class TestDotenv:
    def test_reads_values(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FOO_TEST_KEY", raising=False)
        env = tmp_path / ".env"
        env.write_text(
            '# 주석\nFOO_TEST_KEY="값"\n\nBAD LINE\nEMPTY=\n', encoding="utf-8"
        )
        _load_dotenv(env)
        import os

        assert os.environ["FOO_TEST_KEY"] == "값"

    def test_does_not_override_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FOO_TEST_KEY2", "환경변수가 이김")
        env = tmp_path / ".env"
        env.write_text("FOO_TEST_KEY2=파일값\n", encoding="utf-8")
        _load_dotenv(env)
        import os

        assert os.environ["FOO_TEST_KEY2"] == "환경변수가 이김"

    def test_missing_file_is_fine(self, tmp_path):
        _load_dotenv(tmp_path / "nope")


def test_example_config_is_valid(tmp_path, monkeypatch):
    """config.example.yaml 이 실제로 로딩되는지 (문서와 코드가 안 어긋나게)."""
    import pathlib
    import re

    source = pathlib.Path("config.example.yaml").read_text(encoding="utf-8")
    source = source.replace('id: "여기에_채널ID"', 'id: "abc123def456"')
    path = tmp_path / "config.yaml"
    path.write_text(source, encoding="utf-8")
    cfg = load_config(path, tmp_path / ".env")
    assert cfg.persona.name
    assert cfg.director.max_per_10min > 0
