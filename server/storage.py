"""SQLite-backed storage for recordings + their analysis.

Recordings are stored as a single JSON blob per row — simple, portable, and
plenty fast for a personal/self-hosted deployment. Swap for Postgres later by
re-implementing this module's small surface area.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import settings
from .models import Recording, Status

# Serializes read-modify-write status updates so a concurrent full-row save
# from the pipeline can't be lost between this module's get() and save().
_wlock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    source      TEXT,
    status      TEXT,
    created_at  TEXT,
    data        TEXT NOT NULL          -- full Recording JSON
);
CREATE INDEX IF NOT EXISTS idx_created ON recordings(created_at);
"""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(settings.db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(_SCHEMA)


def save(rec: Recording) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO recordings (id, filename, source, status, created_at, data) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "filename=excluded.filename, source=excluded.source, "
            "status=excluded.status, created_at=excluded.created_at, data=excluded.data",
            (
                rec.id,
                rec.filename,
                rec.source,
                rec.status.value,
                rec.created_at,
                rec.model_dump_json(),
            ),
        )


def update_status(rec_id: str, status: Status, error: Optional[str] = None) -> None:
    with _wlock:
        rec = get(rec_id)
        if not rec:
            return
        rec.status = status
        if error is not None:
            rec.error = error
        save(rec)


def get(rec_id: str) -> Optional[Recording]:
    with _conn() as con:
        row = con.execute(
            "SELECT data FROM recordings WHERE id=?", (rec_id,)
        ).fetchone()
    return Recording.model_validate_json(row["data"]) if row else None


def list_recordings(limit: int = 200) -> list[Recording]:
    with _conn() as con:
        rows = con.execute(
            "SELECT data FROM recordings ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [Recording.model_validate_json(r["data"]) for r in rows]


def delete(rec_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
