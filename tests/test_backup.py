"""Database backup snapshots + retention."""
from __future__ import annotations

import sqlite3

from server import backup, storage
from server.models import Recording


def _rec(rec_id, **kw):
    kw.setdefault("filename", "a.wav")
    return Recording(id=rec_id, **kw)


def test_snapshot_is_complete_and_valid():
    storage.init_db()
    storage.save(_rec("r1"))
    storage.save(_rec("r2"))
    snap = backup.create_snapshot()
    assert snap is not None and snap.exists()
    con = sqlite3.connect(str(snap))
    assert con.execute("select count(*) from recordings").fetchone()[0] == 2
    assert con.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_snapshot_none_when_no_db():
    # fresh temp data dir (autouse fixture) — no DB file has been created
    assert backup.create_snapshot() is None


def test_prune_keeps_newest_n():
    storage.init_db()
    for i in range(5):
        # timestamp-named files; lexical sort == chronological
        (backup.backup_dir() / f"lucid-2026010{i}-000000.db").write_bytes(b"x")
    backup.prune(keep=2)
    remaining = sorted(backup.backup_dir().glob("lucid-*.db"))
    assert len(remaining) == 2
    assert remaining[-1].name == "lucid-20260104-000000.db"   # newest kept
