"""Notion as a client manager (CRM) for Lucid.

Two jobs, both best-effort and dependency-free (stdlib ``urllib`` only):

  • **READ (accuracy)** — pull your Notion "clients" database into a local cache
    (``data/crm_contacts.json``). The analyzer is then told the real client names
    so spoken names map to the correct spelling instead of being guessed.
  • **WRITE (logging)** — after a recording is analyzed, append a tidy note
    (summary + key points + action items + a link back) onto each matched
    client's Notion page. Idempotent per (page, recording).

Matching is precision-first: an EXACT name match auto-links and (optionally)
logs; anything uncertain is queued to ``data/crm_state.json`` → ``pending`` for
the user to confirm in Settings. Nothing here ever raises into the pipeline.
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


# --------------------------------------------------------------------------- #
# matching (note person -> client)
# --------------------------------------------------------------------------- #
def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _dedupe(contacts: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for c in contacts:
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


def _match(person: str, contacts: list[dict]) -> tuple[Optional[dict], list[dict]]:
    """Returns (strong_match, pending_candidates).

    A *strong* match is a single exact (normalized) name match — safe to
    auto-link. Otherwise we surface 1-4 plausible candidates for the user to
    confirm, and stay silent when it's too ambiguous to be useful.
    """
    pn = _norm(person)
    if not pn:
        return None, []
    exact = [c for c in contacts if _norm(c["name"]) == pn]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, _dedupe(exact)[:4]
    ptoks = set(pn.split())
    cand = []
    for c in contacts:
        ctoks = set(_norm(c["name"]).split())
        if ptoks and ctoks and (ptoks <= ctoks or ctoks <= ptoks):
            cand.append(c)
    cand = _dedupe(cand)
    return (None, cand) if 1 <= len(cand) <= 4 else (None, [])


# --------------------------------------------------------------------------- #
# state (links + pushed + pending)
# --------------------------------------------------------------------------- #
def _state_path():
    return settings.data_path / "crm_state.json"


def _load_state() -> dict:
    try:
        d = json.loads(_state_path().read_text())
    except Exception:
        d = {}
    d.setdefault("links", {})     # normalized person name -> notion page id
    d.setdefault("pushed", {})    # f"{page_id}:{rec_id}" -> ts (idempotency)
    d.setdefault("pending", [])   # [{rec_id, rec_title, person, candidates, created}]
    return d


def _save_state(d: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


# --------------------------------------------------------------------------- #
# write-back (Notion block builders)
# --------------------------------------------------------------------------- #
def _rt(text: str, link: Optional[str] = None) -> list:
    t = {"type": "text", "text": {"content": (text or "")[:1900]}}
    if link:
        t["text"]["link"] = {"url": link}
    return [t]


def _block(kind: str, text: str, **extra) -> dict:
    payload = {"rich_text": _rt(text)}
    payload.update(extra)
    return {"object": "block", "type": kind, kind: payload}


def _note_link(rec) -> str:
    base = settings.stable_public_url or settings.current_public_url()
    return f"{base.rstrip('/')}/r/{rec.id}" if base else ""


def _note_blocks(rec, a) -> list:
    date = (rec.created_at or "")[:10]
    head = (a.headline or a.summary or "Conversation").strip()[:80]
    blocks = [
        {"object": "block", "type": "divider", "divider": {}},
        _block("heading_3", f"\U0001f7e3 Lucid · {date} · {head}".strip(" ·")),
    ]
    if a.summary:
        blocks.append(_block("paragraph", a.summary))
    for kp in (a.key_points or [])[:5]:
        blocks.append(_block("bulleted_list_item", kp))
    items = getattr(a, "action_items", None) or []
    if items:
        blocks.append(_block("paragraph", "Action items:"))
        for ai in items[:8]:
            who = f" — {ai.owner}" if getattr(ai, "owner", "") else ""
            blocks.append(_block("to_do", f"{ai.text}{who}", checked=False))
    link = _note_link(rec)
    if link:
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rt("Open in Lucid ↗", link)},
        })
    return blocks


def _push_note(contact: dict, rec, a, state: dict) -> bool:
    key = f"{contact['id']}:{rec.id}"
    if key in state["pushed"] or not contact.get("id"):
        return False
    try:
        _api("PATCH", f"/blocks/{contact['id']}/children", {"children": _note_blocks(rec, a)})
        state["pushed"][key] = time.time()
        return True
    except NotionError:
        return False


# --------------------------------------------------------------------------- #
# pipeline entry point
# --------------------------------------------------------------------------- #
def _rec_title(rec) -> str:
    a = getattr(rec, "analysis", None)
    if a and (a.headline or a.summary):
        return (a.headline or a.summary)[:80]
    return (rec.created_at or rec.id)[:80]


def link_and_push(rec) -> None:
    """After analysis: link each note-person to a client and log the note.

    Strong (exact) matches auto-link and — when auto-log is on — get the note
    appended to their Notion page. Uncertain people are queued for confirmation.
    """
    if not (settings.crm_enabled and settings.get_notion_token() and settings.crm_database_id):
        return
    a = getattr(rec, "analysis", None)
    if not a or not a.people:
        return
    contacts = load_contacts()
    if not contacts:
        return
    owner = _norm(settings.owner_name)
    with _lock:
        state = _load_state()
        for p in a.people:
            nm = (p.name or p.label or "").strip()
            if not nm or (owner and _norm(nm) == owner):
                continue
            strong, cands = _match(nm, contacts)
            if not strong:
                linked = state["links"].get(_norm(nm))
                strong = next((c for c in contacts if c["id"] == linked), None) if linked else None
            if strong:
                state["links"][_norm(nm)] = strong["id"]
                if settings.crm_autopush:
                    _push_note(strong, rec, a, state)
            elif cands:
                _queue_pending(state, rec, nm, cands)
        _save_state(state)


def _queue_pending(state: dict, rec, person: str, cands: list[dict]) -> None:
    for it in state["pending"]:
        if it.get("rec_id") == rec.id and _norm(it.get("person", "")) == _norm(person):
            return
    state["pending"].append({
        "rec_id": rec.id,
        "rec_title": _rec_title(rec),
        "person": person,
        "candidates": [{"id": c["id"], "name": c["name"], "url": c["url"]} for c in cands],
        "created": time.time(),
    })


# --------------------------------------------------------------------------- #
# pending confirmation (driven from Settings)
# --------------------------------------------------------------------------- #
def list_pending() -> list[dict]:
    return _load_state().get("pending", [])


def resolve_pending(rec_id: str, person: str, confirm: bool, page_id: Optional[str] = None) -> bool:
    """Confirm (link + log) or dismiss a queued match. Returns True if found."""
    with _lock:
        state = _load_state()
        pend = state.get("pending", [])
        idx = next(
            (i for i, it in enumerate(pend)
             if it.get("rec_id") == rec_id and _norm(it.get("person", "")) == _norm(person)),
            -1,
        )
        if idx < 0:
            return False
        item = pend.pop(idx)
        if confirm:
            pid = page_id or (item["candidates"][0]["id"] if item.get("candidates") else "")
            if pid:
                state["links"][_norm(item["person"])] = pid
                contact = next((c for c in load_contacts() if c["id"] == pid), None)
                rec = None
                try:
                    from .. import storage
                    rec = storage.get(rec_id)
                except Exception:
                    pass
                if contact and rec and getattr(rec, "analysis", None) and settings.crm_autopush:
                    _push_note(contact, rec, rec.analysis, state)
        _save_state(state)
        return True
