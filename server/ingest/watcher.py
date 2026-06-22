"""Watches the inbox folder for newly dropped audio (e.g. files copied off the
Plaud over USB, or a synced folder) and feeds them into the pipeline.

Runs in a background thread. On startup it also sweeps any files already sitting
in the inbox so nothing is missed.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from watchfiles import Change, watch

from ..config import settings
from . import intake

_seen: set[str] = set()


def _ingest_if_stable(path: Path) -> None:
    """Only ingest once the file has finished being written (size stable)."""
    if not path.exists() or not intake.is_audio(path):
        return
    key = str(path.resolve())
    if key in _seen:
        return
    try:
        size = path.stat().st_size
        time.sleep(1.0)
        if path.stat().st_size != size:
            return  # still being written; a later event will catch it
    except FileNotFoundError:
        return
    _seen.add(key)
    # move (not copy) out of inbox into managed store so inbox stays clean
    intake.intake_file(path, source="usb", copy=False)


def _sweep_existing() -> None:
    for p in settings.inbox_path.iterdir():
        if p.is_file():
            _ingest_if_stable(p)


def _run() -> None:
    _sweep_existing()
    for changes in watch(settings.inbox_path, force_polling=True):
        for change, fpath in changes:
            if change in (Change.added, Change.modified):
                _ingest_if_stable(Path(fpath))


def start() -> threading.Thread:
    t = threading.Thread(target=_run, name="lucid-watcher", daemon=True)
    t.start()
    return t
