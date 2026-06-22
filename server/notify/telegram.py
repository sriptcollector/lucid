"""Push a finished recording's summary to Telegram (your 'Max' bot).

Best-effort: failures are swallowed so a notification problem never fails the
pipeline. Sends the headline, summary, key points, and a deep link to the
timeline in the web UI.
"""
from __future__ import annotations

import httpx

from ..config import settings
from ..models import Recording


def _saved_chat_path():
    return settings.data_path / "telegram_chat.txt"


def save_default_chat(chat_id: str) -> None:
    """Remember the chat that talked to the bot, so cloud-poll / USB results
    have somewhere to go without the user pasting a chat id."""
    try:
        _saved_chat_path().write_text(str(chat_id))
    except Exception:
        pass


def default_chat() -> str:
    if settings.telegram_chat_id:
        return settings.telegram_chat_id
    try:
        return _saved_chat_path().read_text().strip()
    except Exception:
        return ""


def send_message(chat_id: str, text: str) -> None:
    """Low-level send. Best-effort; swallows errors."""
    if not settings.telegram_bot_token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception:
        pass


def notify_done(rec: Recording) -> None:
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return
    a = rec.analysis
    if not a:
        return
    chat = rec.notify_chat or default_chat()
    if not chat:
        return

    points = "\n".join(f"• {p}" for p in a.key_points[:6])
    actions = "\n".join(f"☑ {ai.text}" for ai in a.action_items[:5])
    people = ", ".join(
        (p.name or p.label) for p in (a.people or [])
        if (p.name or p.label) and (p.name or p.label).strip().lower()
        not in ("me", "myself", "narrator", "self")
    )
    parts = [
        f"<b>{_esc(a.headline or 'New recording processed')}</b>",
        _esc(a.summary),
    ]
    if people:
        parts.append(f"\n👥 <b>With</b> {_esc(people)}")
    if points:
        parts.append(f"\n<b>Key points</b>\n{_esc(points)}")
    if actions:
        parts.append(f"\n<b>Action items</b>\n{_esc(actions)}")
    if a.sentiment:
        parts.append(f"\n<i>Tone: {_esc(a.sentiment)}</i>")
    base = settings.current_public_url()
    if base:
        b = base.rstrip("/")
        tok = f"?k={settings.link_token}" if settings.link_token else ""
        parts.append(f'\n<a href="{b}/r/{rec.id}{tok}">Open interactive timeline →</a>')
        parts.append(f'<a href="{b}/people{tok}">See people & relationships →</a>')
    send_message(chat, "\n".join(parts))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
