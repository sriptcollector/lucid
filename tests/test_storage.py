"""SQLite storage round-trips against an isolated temp DB."""
from __future__ import annotations

from server import storage
from server.models import Analysis, Recording, Status


def _rec(rec_id="r1", **kw):
    kw.setdefault("filename", "a.wav")
    return Recording(id=rec_id, **kw)


def test_save_get_roundtrip():
    storage.init_db()
    storage.save(_rec("r1", source="upload", analysis=Analysis(headline="Hello")))
    got = storage.get("r1")
    assert got is not None
    assert got.id == "r1"
    assert got.source == "upload"
    assert got.analysis.headline == "Hello"


def test_get_missing_returns_none():
    storage.init_db()
    assert storage.get("nope") is None


def test_list_orders_by_created_desc():
    storage.init_db()
    storage.save(_rec("old", created_at="2026-01-01T00:00:00"))
    storage.save(_rec("new", created_at="2026-02-01T00:00:00"))
    assert [r.id for r in storage.list_recordings()][:2] == ["new", "old"]


def test_update_status_records_error():
    storage.init_db()
    storage.save(_rec("r1"))
    storage.update_status("r1", Status.ERROR, error="boom")
    got = storage.get("r1")
    assert got.status is Status.ERROR
    assert got.error == "boom"


def test_delete_removes_row():
    storage.init_db()
    storage.save(_rec("r1"))
    storage.delete("r1")
    assert storage.get("r1") is None
