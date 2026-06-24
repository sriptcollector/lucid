"""Config layering, token parsing, and derived-URL logic."""
from __future__ import annotations

from server.config import Settings, settings


def test_token_parsing_splits_and_strips():
    s = Settings(api_tokens="a, b ,c,", _env_file=None)
    assert s.tokens == {"a", "b", "c"}
    assert s.link_token == "a"


def test_no_tokens_is_empty():
    s = Settings(api_tokens="", _env_file=None)
    assert s.tokens == set()
    assert s.link_token == ""


def test_stable_public_url_derivation():
    s = Settings(stable_link_repo="Owner/MyRepo", _env_file=None)
    assert s.stable_public_url == "https://owner.github.io/MyRepo/"


def test_stable_public_url_blank_or_invalid():
    assert Settings(stable_link_repo="", _env_file=None).stable_public_url == ""
    assert Settings(stable_link_repo="noslash", _env_file=None).stable_public_url == ""


def test_translate_target_none_aliases():
    assert Settings(translate_to="English", _env_file=None).translate_target == "English"
    assert Settings(translate_to="none", _env_file=None).translate_target is None
    assert Settings(translate_to="", _env_file=None).translate_target is None


def test_current_public_url_prefers_live_tunnel():
    settings.public_url_file.write_text("https://live.trycloudflare.com")
    assert settings.current_public_url() == "https://live.trycloudflare.com"


def test_current_public_url_falls_back_to_static(monkeypatch):
    try:
        settings.public_url_file.unlink()
    except OSError:
        pass
    monkeypatch.setattr(settings, "public_base_url", "https://static.example.com")
    assert settings.current_public_url() == "https://static.example.com"
