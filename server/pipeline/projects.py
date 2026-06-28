"""Projects (a.k.a. folders) — user-curated collections that group together any
mix of notes, people, and ideas under a named project.

Unlike ventures (auto-derived from recordings) or people (auto-clustered), a
project is something the user builds by hand: multi-select a few notes, the
people involved, and the ideas they spawned, and file them under one project.

Persisted to data/projects.json with the same best-effort _load()/_save()
pattern as ventures.py — a failed read or write never raises into a request.

A project:
    {
      "id":         "<8 hex chars>",
      "name":       "Acme launch",
      "created_at": "2026-06-25T10:00:00Z",
      "updated_at": "2026-06-25T10:00:00Z",
      "items": [
        {"type": "note",   "ref": "<rec_id>",     "added_at": "..."},
        {"type": "person", "ref": "<person key>", "added_at": "..."},
        {"type": "idea",   "ref": "<venture id>", "added_at": "..."}
      ]
    }
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Optional

from ..config import settings

_lock = threading.RLock()
_TYPES = {"note", "person", "idea"}
# tolerate plural / synonym types coming from the frontend
_ALIASES = {
    "notes": "note", "recording": "note", "recordings": "note", "rec": "note",
    "people": "person", "persons": "person",
    "ideas": "idea", "venture": "idea", "ventures": "idea",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _path():
    return settings.data_path / "projects.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text())
    except Exception:
        d = {}
    d.setdefault("projects", {})   # project_id -> project dict
    return d


def _save(d: dict) -> None:
    p = _path()
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, p)
    except Exception:
        pass


def _new_id() -> str:
    return secrets.token_hex(4)


def _norm_item(type_, ref) -> Optional[tuple]:
    """Normalize/validate an (type, ref) pair. Returns (type, ref) or None."""
    t = (type_ or "").strip().lower()
    t = _ALIASES.get(t, t)
    r = ("" if ref is None else str(ref)).strip()
    if t not in _TYPES or not r:
        return None
    return t, r


def _summary(p: dict) -> dict:
    """Public list-row shape: project meta + item counts (no items array)."""
    items = p.get("items", []) or []
    counts = {"note": 0, "person": 0, "idea": 0}
    for it in items:
        t = it.get("type")
        if t in counts:
            counts[t] += 1
    return {
        "id": p.get("id"),
        "name": p.get("name", ""),
        "created_at": p.get("created_at", ""),
        "updated_at": p.get("updated_at", ""),
        "item_count": len(items),
        "counts": counts,
    }


def _full(p: dict) -> dict:
    """Public detail shape: summary + the full items array."""
    out = _summary(p)
    out["items"] = [dict(it) for it in (p.get("items", []) or [])]
    return out


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def list_projects() -> list[dict]:
    try:
        projects = _load()["projects"].values()
    except Exception:
        return []
    out = [_summary(p) for p in projects]
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


def get_project(pid: str) -> Optional[dict]:
    try:
        p = _load()["projects"].get(pid)
    except Exception:
        return None
    return _full(p) if p else None


def projects_for(type_, ref) -> list[dict]:
    """Which projects contain this item — [{id, name}] (for showing membership)."""
    norm = _norm_item(type_, ref)
    if not norm:
        return []
    t, r = norm
    out = []
    try:
        for p in _load()["projects"].values():
            for it in p.get("items", []) or []:
                if it.get("type") == t and it.get("ref") == r:
                    out.append({"id": p.get("id"), "name": p.get("name", "")})
                    break
    except Exception:
        return []
    return out


# --------------------------------------------------------------------------- #
# Mutations (best-effort — never raise)
# --------------------------------------------------------------------------- #
def create_project(name: str) -> dict:
    name = (name or "").strip() or "Untitled project"
    now = _now()
    pid = _new_id()
    proj = {"id": pid, "name": name, "created_at": now, "updated_at": now, "items": []}
    try:
        with _lock:
            d = _load()
            while pid in d["projects"]:          # avoid the rare id clash
                pid = _new_id()
                proj["id"] = pid
            d["projects"][pid] = proj
            _save(d)
    except Exception:
        pass
    return _full(proj)


def rename_project(pid: str, name: str) -> Optional[dict]:
    name = (name or "").strip()
    if not name:
        return None
    try:
        with _lock:
            d = _load()
            p = d["projects"].get(pid)
            if not p:
                return None
            p["name"] = name
            p["updated_at"] = _now()
            _save(d)
            return _full(p)
    except Exception:
        return None


def delete_project(pid: str) -> bool:
    try:
        with _lock:
            d = _load()
            if pid in d["projects"]:
                del d["projects"][pid]
                _save(d)
                return True
    except Exception:
        pass
    return False


def attach(pid: str, type_, ref) -> bool:
    """Add one item, deduped. False if no such project, bad item, or already present."""
    norm = _norm_item(type_, ref)
    if not norm:
        return False
    t, r = norm
    try:
        with _lock:
            d = _load()
            p = d["projects"].get(pid)
            if not p:
                return False
            items = p.setdefault("items", [])
            for it in items:
                if it.get("type") == t and it.get("ref") == r:
                    return False             # dedupe: already filed here
            items.append({"type": t, "ref": r, "added_at": _now()})
            p["updated_at"] = _now()
            _save(d)
            return True
    except Exception:
        return False


def attach_many(pid: str, items: list) -> dict:
    """Batch add for multi-select. Returns {ok, added, project}."""
    added = 0
    try:
        with _lock:
            d = _load()
            p = d["projects"].get(pid)
            if not p:
                return {"ok": False, "added": 0}
            existing = p.setdefault("items", [])
            seen = {(it.get("type"), it.get("ref")) for it in existing}
            for raw in (items or []):
                if not isinstance(raw, dict):
                    continue
                norm = _norm_item(raw.get("type"), raw.get("ref"))
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                existing.append({"type": norm[0], "ref": norm[1], "added_at": _now()})
                added += 1
            if added:
                p["updated_at"] = _now()
                _save(d)
            return {"ok": True, "added": added, "project": _full(p)}
    except Exception:
        return {"ok": False, "added": 0}


def detach(pid: str, type_, ref) -> bool:
    """Remove one item. False if no such project, bad item, or nothing removed."""
    norm = _norm_item(type_, ref)
    if not norm:
        return False
    t, r = norm
    try:
        with _lock:
            d = _load()
            p = d["projects"].get(pid)
            if not p:
                return False
            items = p.setdefault("items", [])
            kept = [it for it in items
                    if not (it.get("type") == t and it.get("ref") == r)]
            if len(kept) == len(items):
                return False                 # nothing matched
            p["items"] = kept
            p["updated_at"] = _now()
            _save(d)
            return True
    except Exception:
        return False
