"""Automatic ingest: poll your Plaud cloud and pull new recordings.

This is the zero-touch path. Your Plaud device's **Private Cloud Sync** uploads
each recording to Plaud's cloud automatically after capture. This module then,
on a schedule, asks the pure-Python `PlaudCloud` client for the list of
recordings, downloads any it hasn't seen as MP3, and drops them into the
pipeline — which transcribes, analyzes, and pushes results to your phone.

You record. Nothing else. Results appear.

Setup (one time): complete the onboarding wizard's "Connect Plaud" step, which
stores a ~300-day token via the settings contract, and enable
`PLAUD_CLOUD_ENABLED`. We never re-handle your Plaud password here — the stored
token is used. Recording ids are validated by the client before use.

Robustness: every poll cycle is wrapped so a transient API/network error logs
and is retried next cycle rather than killing the background thread.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..config import settings
from . import intake
from .plaud_client import PlaudCloud, PlaudError


def _ledger_path() -> Path:
    return settings.data_path / "plaud_seen.json"


def _load_ledger() -> set[str] | None:
    """Returns the set of seen recording ids, or None if we've never run."""
    p = _ledger_path()
    if not p.exists():
        return None
    try:
        return set(json.loads(p.read_text()))
    except Exception:  # noqa: BLE001 — a corrupt ledger should not crash polling
        return set()


def _save_ledger(seen: set[str]) -> None:
    _ledger_path().write_text(json.dumps(sorted(seen)))


def _poll_once() -> None:
    seen = _load_ledger()
    first_run = seen is None
    if seen is None:
        seen = set()

    client = PlaudCloud()
    recordings = client.list_recordings()
    if not recordings:
        return

    tmp = settings.data_path / "plaud_tmp"
    new_count = 0
    for rec in recordings:
        rec_id = str(rec.get("id") or "")
        if not rec_id or rec_id in seen:
            continue

        # On the very first run, optionally skip the existing backlog so we
        # don't suddenly transcribe (and bill) months of history.
        if first_run and not settings.plaud_process_backlog:
            seen.add(rec_id)
            continue

        dest = tmp / f"{rec_id}.mp3"
        try:
            audio = client.download_audio(rec_id, dest)
        except PlaudError as exc:
            # Audio may not be fully available on Plaud's cloud yet (just
            # recorded). Do NOT mark it seen — retry on the next poll.
            print(f"[plaud_cloud] {rec_id} not downloadable yet ({exc}); "
                  "will retry next cycle.")
            continue
        if not audio.exists() or audio.stat().st_size == 0:
            print(f"[plaud_cloud] {rec_id} downloaded empty; will retry next cycle.")
            continue

        intake.intake_file(audio, source="plaud_cloud", copy=False)
        new_count += 1
        seen.add(rec_id)
        _save_ledger(seen)  # persist incrementally so a crash won't reprocess

    _save_ledger(seen)
    if first_run and not settings.plaud_process_backlog:
        print(f"[plaud_cloud] first run: marked {len(seen)} existing recording(s) "
              "as seen; will process only new ones from now on.")
    elif new_count:
        print(f"[plaud_cloud] ingested {new_count} new recording(s).")


def _run() -> None:
    if not settings.plaud_logged_in:
        print("[plaud_cloud] not connected to Plaud yet. Finish the 'Connect "
              "Plaud' onboarding step. Will keep checking.")

    while True:
        try:
            if settings.plaud_logged_in:
                _poll_once()
        except Exception as exc:  # noqa: BLE001 — never let the thread die
            print(f"[plaud_cloud] poll error: {exc}")
        time.sleep(max(60, settings.plaud_poll_interval))


_thread: threading.Thread | None = None


def start() -> threading.Thread | None:
    """Start the poll loop (idempotent — safe to call from startup AND from the
    /api/setup/plaud route once Plaud is connected). A second call while the
    thread is alive is a no-op, so it never spawns a duplicate poller."""
    global _thread
    if not settings.plaud_cloud_enabled:
        return None
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_run, name="lucid-plaud-cloud", daemon=True)
    _thread.start()
    return _thread
