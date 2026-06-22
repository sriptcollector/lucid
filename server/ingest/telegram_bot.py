"""Telegram ingest: receive audio sent to your bot and feed it into Lucid.

Uses long-polling (getUpdates) so it works on a *local* machine with no public
URL / webhook. When you share a recording from the Plaud app to your bot (as an
audio file or voice message), this:

  1. downloads the file from Telegram,
  2. queues it through transcribe -> translate -> analyze, and
  3. replies in the same chat ("got it") — the finished summary is pushed back
     by notify.telegram.notify_done when processing completes.

Runs in a background thread, started from main.py when TELEGRAM_ENABLED=true.
Telegram's getFile download limit is ~20MB; for larger recordings use the
web Upload button or the USB folder instead.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import httpx

from ..config import settings
from ..notify import telegram
from . import intake

_API = "https://api.telegram.org"

# Chats that issued /enroll and whose next audio is a voice sample: chat -> name
_pending_enroll: dict[str, str] = {}


def _enroll_bytes(data: bytes, filename: str, name: str) -> bool:
    from ..pipeline import voiceid
    tmp = settings.data_path / "enroll_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower() or ".oga"
    p = tmp / f"{uuid.uuid4().hex[:8]}{ext}"
    p.write_bytes(data)
    try:
        return voiceid.enroll(str(p), name)
    except Exception:
        return False


def _base() -> str:
    return f"{_API}/bot{settings.telegram_bot_token}"


def _get_updates(offset: int | None) -> list[dict]:
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    try:
        r = httpx.get(f"{_base()}/getUpdates", params=params, timeout=40)
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception:
        time.sleep(3)
        return []


def _download_file(file_id: str) -> bytes | None:
    try:
        meta = httpx.get(f"{_base()}/getFile", params={"file_id": file_id}, timeout=30)
        meta.raise_for_status()
        path = meta.json()["result"]["file_path"]
        data = httpx.get(f"{_API}/file/bot{settings.telegram_bot_token}/{path}", timeout=120)
        data.raise_for_status()
        return data.content
    except Exception:
        return None


def _pick_audio(msg: dict) -> tuple[str, str] | None:
    """Return (file_id, filename) for any audio-bearing message, else None."""
    if "audio" in msg:
        a = msg["audio"]
        name = a.get("file_name") or f"audio_{a['file_unique_id']}.mp3"
        return a["file_id"], name
    if "voice" in msg:                       # voice note (.oga/opus)
        v = msg["voice"]
        return v["file_id"], f"voice_{v['file_unique_id']}.oga"
    if "video_note" in msg:
        v = msg["video_note"]
        return v["file_id"], f"note_{v['file_unique_id']}.mp4"
    if "document" in msg:                    # shared file; accept audio mimetypes
        d = msg["document"]
        mime = d.get("mime_type", "")
        name = d.get("file_name", "")
        if mime.startswith("audio") or intake.is_audio_name(name):
            return d["file_id"], name or f"doc_{d['file_unique_id']}"
    return None


def _send_link(chat_id: str) -> None:
    """Reply with the current public link (token-aware), or a wait message."""
    base = settings.current_public_url().rstrip("/")
    if not base:
        telegram.send_message(
            chat_id, "⚠️ The public link isn't up yet — give it a few seconds and text "
            "<b>link</b> again.")
        return
    tok = f"?k={settings.link_token}" if settings.link_token else ""
    telegram.send_message(
        chat_id,
        "🔗 <b>Your Lucid link</b>\n"
        f"{base}/{tok}\n\n"
        f'👥 <a href="{base}/people{tok}">People</a>  ·  '
        f'<a href="{base}/directory{tok}">Directory</a>\n\n'
        "<i>Open once and add to your home screen.</i>",
    )


def _handle(msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    raw = (msg.get("text") or "").strip()
    text = raw.lower()

    # Remember this chat as the delivery target for automatic (cloud-poll) results.
    telegram.save_default_chat(chat_id)

    # Voice enrollment: "/enroll [Name]" then send a voice clip.
    if text.startswith("/enroll"):
        name = raw[7:].strip() or "Me"
        _pending_enroll[chat_id] = name
        telegram.send_message(
            chat_id,
            f"🎙️ <b>Voice enrollment for “{telegram._esc(name)}”</b>\n\n"
            "Now send me <b>one voice message of just you talking for 30–60 seconds</b>:\n"
            "• Somewhere quiet — no TV, music, or other people.\n"
            "• Hold the phone normally and talk naturally (read anything).\n"
            "• Tap-and-hold the 🎤 mic in this chat, speak, release to send.\n\n"
            "The next audio you send will be used as your voiceprint.",
        )
        return

    # "/link" (or any message mentioning "link"/"site") -> reply with the live URL.
    if text in ("/link", "/site", "site", "link") or "link" in text:
        _send_link(chat_id)
        return

    if text in ("/start", "/help"):
        telegram.send_message(
            chat_id,
            "👋 <b>Lucid</b> is connected — and I'll deliver your Plaud results here "
            "automatically.\n\nJust record on your Plaud (with Private Cloud Sync on) "
            "and I'll send back a summary + timeline when each one syncs. You can also "
            "send me an audio file directly.\n\n"
            "💬 Text <b>link</b> anytime to get your current site link.\n"
            f"<i>Delivering results to this chat (id <code>{chat_id}</code>).</i>",
        )
        return

    picked = _pick_audio(msg)
    if not picked:
        if text:
            telegram.send_message(chat_id, "Send me an audio file or voice note 🎙️")
        return

    file_id, filename = picked

    # If we're waiting for an enrollment clip from this chat, use it as the voiceprint.
    if chat_id in _pending_enroll:
        name = _pending_enroll.pop(chat_id)
        telegram.send_message(chat_id, "🔎 Got your sample — building your voiceprint…")
        data = _download_file(file_id)
        if not data:
            telegram.send_message(chat_id, "⚠️ Couldn't download that clip. Try again with /enroll.")
            return
        if _enroll_bytes(data, filename, name):
            telegram.send_message(
                chat_id,
                f"✅ <b>Enrolled “{telegram._esc(name)}.”</b>\n\nYour voice will now be "
                "labelled on new recordings. To apply it to an existing recording, open it "
                "and tap <b>Re-analyze</b>.",
            )
        else:
            telegram.send_message(
                chat_id,
                "⚠️ That clip was too short or unclear. Send /enroll again and record a "
                "longer (30–60s), clear sample with just your voice.",
            )
        return

    telegram.send_message(chat_id, "🎧 Got it — transcribing &amp; analyzing…")
    data = _download_file(file_id)
    if not data:
        telegram.send_message(
            chat_id, "⚠️ Couldn't download that (Telegram caps bot downloads at "
            "~20MB). Try the web Upload button for large files."
        )
        return
    intake.intake_bytes(data, filename, source="telegram", notify_chat=chat_id)


def _run() -> None:
    # Skip the backlog of old messages so a restart doesn't reprocess everything.
    offset: int | None = None
    initial = _get_updates(None)
    if initial:
        offset = initial[-1]["update_id"] + 1

    while True:
        for upd in _get_updates(offset):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("channel_post")
            if msg:
                try:
                    _handle(msg)
                except Exception:
                    pass


def start() -> threading.Thread | None:
    if not (settings.telegram_enabled and settings.telegram_bot_token):
        return None
    t = threading.Thread(target=_run, name="lucid-telegram", daemon=True)
    t.start()
    return t
