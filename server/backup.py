"""Automatic, consistent backups of the notes database.

``lucid.db`` is the single source of truth for every recording and its analysis.
This module snapshots it on a schedule using SQLite's *online backup* API, which
produces a consistent copy even while the server is actively reading and writing
— unlike a naive file copy, which can capture a torn, mid-write database.
Snapshots are timestamped and pruned to the most recent ``backup_keep``.

Snapshots are static, closed files, so they are safe to let a cloud-sync tool
(OneDrive, Dropbox, …) replicate off-machine for true off-site safety — it is
only the *live, open* database that is unsafe to sync.

Runs in a daemon thread: one backup shortly after startup, then every
``backup_interval_hours``. Best-effort and self-contained — a backup failure is
logged and never disturbs the app.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .config import settings


def backup_dir() -> Path:
    """Where snapshots live (``backup_dir`` setting, or ``<data>/backups``)."""
    raw = settings.backup_dir.strip()
    p = Path(raw) if raw else (settings.data_path / "backups")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _timestamp() -> str:
    """Filename-safe, sortable local timestamp."""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def create_snapshot() -> Path | None:
    """Write one consistent snapshot of the DB; return its path (or ``None``).

    Uses sqlite3's backup API so it is safe to run while the DB is in use.
    Opens the source read-only. Never raises.
    """
    src_path = settings.db_path
    if not Path(src_path).exists():
        return None
    dest = backup_dir() / f"lucid-{_timestamp()}.db"
    try:
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except Exception as exc:  # noqa: BLE001 - a backup must never crash the app
        print(f"[backup] snapshot failed: {exc}")
        try:
            dest.unlink(missing_ok=True)   # never leave a half-written file
        except Exception:  # noqa: BLE001
            pass
        return None
    print(f"[backup] wrote {dest.name} ({dest.stat().st_size // 1024} KB)")
    return dest


def prune(keep: int | None = None) -> None:
    """Keep only the newest ``keep`` snapshots; delete older ones."""
    keep = settings.backup_keep if keep is None else keep
    if keep <= 0:
        return
    snaps = sorted(backup_dir().glob("lucid-*.db"), key=lambda p: p.name, reverse=True)
    for old in snaps[keep:]:
        try:
            old.unlink()
            print(f"[backup] pruned {old.name}")
        except Exception:  # noqa: BLE001
            pass


def run_once() -> Path | None:
    """Take a snapshot and prune. Returns the new snapshot path (or ``None``)."""
    snap = create_snapshot()
    prune()
    return snap


_thread: threading.Thread | None = None
_stop = threading.Event()


def _loop() -> None:
    # Brief initial delay so the first backup doesn't race startup work.
    if _stop.wait(timeout=30):
        return
    while not _stop.is_set():
        run_once()
        interval = max(1, settings.backup_interval_hours) * 3600
        if _stop.wait(timeout=interval):
            break


def start() -> None:
    """Start the periodic backup thread (idempotent; respects ``backup_enabled``)."""
    global _thread
    if not settings.backup_enabled:
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="lucid-backup", daemon=True)
    _thread.start()


def stop() -> None:
    """Signal the backup loop to exit (used on shutdown / in tests)."""
    _stop.set()
