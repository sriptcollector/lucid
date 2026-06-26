"""Notion as a client directory (read-only) for Lucid.

One job, best-effort and dependency-free (stdlib ``urllib`` only): pull your
Notion "clients" database into a local cache (``data/crm_contacts.json``) so the
analyzer knows the real client names. Spoken names then map to the correct
spelling instead of being guessed — Lucid never writes anything back to Notion.
Nothing here raises into the pipeline.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from ..config import settings

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_lock = threading.RLock()
_HEX32 = re.compile(r"[0-9a-f]{32}")


class NotionError(Exception):
    """A failed Notion API call, carrying a short human-readable message."""


# --------------------------------------------------------------------------- #
# low-level API
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.get_notion_token()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, headers=_headers(), method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode(errors="ignore")).get("message", "")
        except Exception:
            pass
        raise NotionError(detail or f"Notion error {e.code}")
    except Exception as e:  # noqa: BLE001 - network/JSON/etc.
        raise NotionError(str(e))


def extract_db_id(s: str) -> str:
    """Pull the 32-char database id out of a pasted Notion URL (or raw id)."""
    m = _HEX32.search((s or "").replace("-", "").lower())
    return m.group(0) if m else ""


# --------------------------------------------------------------------------- #
# property extraction (Notion's typed property -> plain string)
# --------------------------------------------------------------------------- #
def _prop_text(prop: dict) -> str:
    t = prop.get("type")
    v = prop.get(t)
    if v is None:
        return ""
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v).strip()
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(x.get("name", "") for x in v)
    if t == "people":
        return ", ".join(x.get("name", "") for x in v)
    if t in ("email", "phone_number", "url"):
        return v or ""
    if t == "number":
        return "" if v is None else str(v)
    if t == "date":
        return (v or {}).get("start", "") or ""
    if t == "formula":
        inner = v or {}
        return str(inner.get(inner.get("type"), "") or "")
    return ""


def _title_key(db: dict) -> str:
    for k, p in db.get("properties", {}).items():
        if p.get("type") == "title":
            return k
    return ""


def _page_to_contact(page: dict, title_key: str) -> dict:
    props = page.get("properties", {})
    flat = {k: _prop_text(p) for k, p in props.items()}
    name = flat.get(title_key, "") if title_key else ""

    def pick(*needles: str) -> str:
        for k in props:
            kl = k.lower()
            if any(n in kl for n in needles) and flat.get(k):
                return flat[k]
        return ""

    return {
        "id": page.get("id", ""),
        "url": page.get("url", ""),
        "name": name,
        "company": pick("company", "organisation", "organization", "account"),
        "status": pick("status", "stage", "pipeline"),
        "email": pick("email"),
        "props": {k: v for k, v in flat.items() if v and k != title_key},
    }


# --------------------------------------------------------------------------- #
# fetch + cache
# --------------------------------------------------------------------------- #
def test_and_describe() -> tuple[bool, str, str]:
    """Probe the configured database. Returns (ok, message, db_title)."""
    if not settings.get_notion_token():
        return False, "No Notion secret saved.", ""
    if not settings.crm_database_id:
        return False, "No database link saved.", ""
    try:
        db = _api("GET", f"/databases/{settings.crm_database_id}")
    except NotionError as e:
        return False, str(e), ""
    title = "".join(x.get("plain_text", "") for x in db.get("title", []))
    return True, "ok", title


def fetch_contacts(max_pages: int = 25) -> list[dict]:
    db_id = settings.crm_database_id
    if not db_id or not settings.get_notion_token():
        return []
    db = _api("GET", f"/databases/{db_id}")
    tkey = _title_key(db)
    out: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = _api("POST", f"/databases/{db_id}/query", body)
        for page in res.get("results", []):
            c = _page_to_contact(page, tkey)
            if c["name"]:
                out.append(c)
        if res.get("has_more") and res.get("next_cursor"):
            cursor = res["next_cursor"]
        else:
            break
    return out


def refresh_contacts() -> int:
    """Re-pull the client list into the local cache. Returns the count."""
    contacts = fetch_contacts()
    with _lock:
        path = settings.crm_contacts_path
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": time.time(), "contacts": contacts}))
        os.replace(tmp, path)
    return len(contacts)


def load_contacts() -> list[dict]:
    try:
        d = json.loads(settings.crm_contacts_path.read_text())
        return d.get("contacts", []) if isinstance(d, dict) else []
    except Exception:
        return []


def last_refresh() -> float:
    try:
        return float(json.loads(settings.crm_contacts_path.read_text()).get("fetched_at", 0))
    except Exception:
        return 0.0


def roster_names(limit: int = 400) -> list[str]:
    """Distinct client names for the analyzer's known-people roster."""
    seen: set[str] = set()
    out: list[str] = []
    for c in load_contacts():
        nm = (c.get("name") or "").strip()
        k = nm.lower()
        if nm and k not in seen:
            seen.add(k)
            out.append(nm)
        if len(out) >= limit:
            break
    return out
