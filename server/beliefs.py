"""BELIEFS — Orion's product playbook page.

One page = the overall guide on how he builds great products. Two sources:
  1. The playbook he writes/pastes himself (business-focused, freeform).
  2. Beliefs auto-extracted from his voice notes: any time a note states a
     conviction about HOW to build winning products, it's pulled out and
     added here with the note it came from.

Mirrors the Mental warm pipeline in businesses.py: cache-only reads that are
instant, background Claude-CLI batches that fill the cache, and a persisted
removed-list so deleting an extracted belief sticks across rescans.
"""
from __future__ import annotations

import threading
import time

from .businesses import _cli_json, _load, _lock, _save, _sig

TEXT_FILE = "beliefs.json"
AUTO_CACHE = "beliefs_auto.json"
REMOVED_FILE = "beliefs_removed.json"

_warming = threading.Event()
_warm_lock = threading.Lock()


def get_text() -> str:
    with _lock:
        return (_load(TEXT_FILE, {}) or {}).get("text", "")


def set_text(text: str) -> None:
    with _lock:
        _save(TEXT_FILE, {"text": text, "updated": time.time()})


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def remove(text: str) -> None:
    """Hide an auto-extracted belief (persists across rescans)."""
    n = _norm(text)
    if not n:
        return
    with _lock:
        rem = _load(REMOVED_FILE, [])
        if n not in rem:
            rem.append(n)
            _save(REMOVED_FILE, rem)


def _classify(batch: list) -> dict:
    """{rec_id: [belief, …]} — product/business-building convictions stated
    in each note, written as short standalone principles."""
    lines = []
    for r in batch:
        a = r.analysis
        dig = (a.headline if a else "") or "Note"
        if a and a.summary:
            dig += " — " + a.summary
        tx = (r.full_text_translated or r.full_text or "").strip()
        lines.append("[%s] %s\n    said: %s" % (
            r.id, dig[:220], tx[:900].replace("\n", " ")))
    prompt = (
        "Below are a founder's voice notes. From EACH note, extract any "
        "BELIEFS about how to build great products or a winning business: "
        "convictions about what makes a product succeed, how to launch, who "
        "to build for, how to decide, how to sell, what to focus on. "
        "Business/product philosophy only — NOT tasks, NOT feature requests, "
        "NOT feelings or self-reflection.\n"
        "Write each belief as a short standalone principle (max 20 words), "
        "as a rule ('Build toward the feeling the user gets', 'The first "
        "audience shapes who buys next'). No first person.\n\n"
        "NOTES:\n%s\n\n"
        'Reply with ONLY JSON: {"items":[{"id":"..","beliefs":["..",".."]}]}. '
        "Include EVERY note id, with an empty beliefs list when there are "
        "none." % "\n".join(lines))
    res = _cli_json(prompt, timeout=240)
    out = {}
    for it in (res.get("items") or []):
        rid = str(it.get("id", "")).strip()
        if rid:
            bl = [(b or "").strip() for b in (it.get("beliefs") or [])]
            out[rid] = [b for b in bl if b][:6]
    return out


def _warm(recs: list) -> None:
    # atomic start guard, same shape as businesses._mental_warm
    if not _warm_lock.acquire(blocking=False):
        return
    _warming.set()
    try:
        with _lock:
            cache = _load(AUTO_CACHE, {})
        todo = [r for r in recs
                if (cache.get(r.id) or {}).get("sig") != _sig(r)]
        B = 12
        for i in range(0, len(todo), B):
            batch = todo[i:i + B]
            got = _classify(batch)
            # total CLI failure: leave the batch uncached so it retries later
            if not got:
                continue
            updates = {}
            for r in batch:
                updates[r.id] = {"sig": _sig(r), "beliefs": got.get(r.id, [])}
            with _lock:
                fresh = _load(AUTO_CACHE, {})
                fresh.update(updates)
                _save(AUTO_CACHE, fresh)
    finally:
        _warming.clear()
        _warm_lock.release()


def index(recs: list) -> dict:
    """The Beliefs page payload. Cache-only and instant; warms uncached notes
    in the background."""
    with _lock:
        cache = _load(AUTO_CACHE, {})
        removed = set(_load(REMOVED_FILE, []))
    if any((cache.get(r.id) or {}).get("sig") != _sig(r) for r in recs):
        threading.Thread(target=_warm, args=(list(recs),),
                         name="lucid-beliefs-warm", daemon=True).start()
    items, seen = [], set()
    for r in recs:
        c = cache.get(r.id) or {}
        for b in c.get("beliefs") or []:
            n = _norm(b)
            if not n or n in removed or n in seen:
                continue
            seen.add(n)
            items.append({"text": b, "note": r.id,
                          "headline": (r.analysis.headline if r.analysis
                                       else "") or "Note",
                          "created_at": r.created_at})
    items.sort(key=lambda x: x["created_at"] or "", reverse=True)
    analyzed = sum(1 for r in recs
                   if (cache.get(r.id) or {}).get("sig") == _sig(r))
    return {"text": get_text(), "items": items, "count": len(items),
            "warming": _warming.is_set(),
            "analyzed": analyzed, "total": len(recs)}
