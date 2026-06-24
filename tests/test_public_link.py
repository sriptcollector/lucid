"""Stable redirect-page rendering + publish gating."""
from __future__ import annotations

from server import public_link
from server.config import settings


def test_render_contains_url_and_refresh():
    html = public_link.render("https://x.trycloudflare.com")
    assert "https://x.trycloudflare.com" in html
    assert 'http-equiv="refresh"' in html
    assert "location.replace" in html


def test_render_has_three_redirect_paths():
    # meta-refresh, manual link, and JS replace — robust to JS being off
    html = public_link.render("https://y.example.com")
    assert html.count("https://y.example.com") >= 3


def test_publish_is_noop_without_repo(monkeypatch):
    monkeypatch.setattr(settings, "stable_link_repo", "")
    assert public_link.publish("https://z.example.com") is False
