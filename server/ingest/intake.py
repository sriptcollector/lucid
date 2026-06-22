"""Shared intake: register a new audio file as a Recording and queue it.

Used by every ingest path (USB watcher, HTTP upload, Plaud API pull) so they
all funnel through one consistent entry point.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import storage
from ..config import settings
from ..models import Recording, Status

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".webm"}

# Set by main.py at startup: a function that schedules runner.process(rec_id).
_enqueue: Callable[[str], None] | None = None


def set_enqueue(fn: Callable[[str], None]) -> None:
    global _enqueue
    _enqueue = fn


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def is_audio_name(name: str) -> bool:
    return Path(name).suffix.lower() in AUDIO_EXTS


def intake_file(src: Path, source: str = "usb", copy: bool = True) -> Recording:
    """Register `src` as a new recording. Copies it into the managed audio store
    (unless copy=False) and queues processing."""
    rec_id = uuid.uuid4().hex[:12]
    dest = settings.audio_path / f"{rec_id}{_safe_suffix(src.name)}"
    if copy:
        shutil.copy2(src, dest)
    else:
        shutil.move(str(src), dest)

    rec = Recording(
        id=rec_id,
        filename=str(dest),
        source=source,
        status=Status.QUEUED,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    storage.save(rec)
    if _enqueue:
        _enqueue(rec_id)
    return rec


def _safe_suffix(name: str) -> str:
    """Only ever derive an on-disk name from a validated audio extension, never
    from arbitrary client input."""
    suffix = Path(name or "").suffix.lower()
    return suffix if suffix in AUDIO_EXTS else ".wav"


def intake_bytes(
    data: bytes,
    original_name: str,
    source: str = "upload",
    notify_chat: str | None = None,
) -> Recording:
    rec_id = uuid.uuid4().hex[:12]
    suffix = _safe_suffix(original_name)
    dest = settings.audio_path / f"{rec_id}{suffix}"
    dest.write_bytes(data)

    rec = Recording(
        id=rec_id,
        filename=str(dest),
        source=source,
        status=Status.QUEUED,
        created_at=datetime.now(timezone.utc).isoformat(),
        notify_chat=notify_chat,
    )
    storage.save(rec)
    if _enqueue:
        _enqueue(rec_id)
    return rec
