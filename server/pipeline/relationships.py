"""Relationship tracker — the cross-recording memory layer.

Every recording is analysed in isolation by ``analyze.py``. This module stitches
those isolated analyses together into a longitudinal view of the PEOPLE in your
life: who you keep talking to, how often, what the dynamics between you are, and
whether each relationship is warming, cooling, or steady over time.

It is computed on the fly from stored recordings (no extra tables) so it is
always consistent with what you've recorded. For a personal/self-hosted corpus
(tens–hundreds of recordings) this is plenty fast.

Identity resolution is name-based: a person is keyed by the normalised form of
their (user-editable) name/label, so renaming someone in one recording — or
across them — merges their history. It is heuristic, not perfect; that's the
right trade-off for a personal tool where you can fix names yourself.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Optional

from .. import storage
from ..config import settings
from ..models import Recording

_hidden_lock = threading.Lock()
_BIG = 1_000_000   # effectively "all recordings" for full-corpus operations

# Labels that mean "the owner of the recorder" rather than another person.
# (If/when a voice is enrolled, those names are added too.)
_SELF = {"me", "myself", "i", "narrator", "self", "owner", "user"}


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _self_names() -> set[str]:
    names = set(_SELF)
    try:
        from . import voiceid
        for n in voiceid.enrolled_names():
            if _norm(n):
                names.add(_norm(n))
    except Exception:
        pass
    return names


def _matches(speaker: Optional[str], name_norm: str, label_norm: str) -> bool:
    """Does a free-text speaker/owner string refer to this person?"""
    s = _norm(speaker)
    if not s:
        return False
    if s == name_norm or s == label_norm:
        return True
    for key in (name_norm, label_norm):
        if key and (f" {key} " in f" {s} " or f" {s} " in f" {key} "):
            return True
    return False


def _tok_eq(a: str, b: str) -> bool:
    """Whole-token match between two already-normalized strings (no substrings,
    so 'sam' never matches 'samantha')."""
    if not a or not b:
        return False
    return a == b or f" {a} " in f" {b} " or f" {b} " in f" {a} "


def _people_in(rel_people: str) -> list[str]:
    """Split a relationship_dynamic's "people" string into normalised tokens.
    e.g. "Dad, Son" -> ["dad","son"];  "Son & Val/Belle (ex)" -> ...subtokens."""
    parts = re.split(r"[,&/]|\band\b|\bwith\b|\bvs\b", rel_people or "")
    return [_norm(p) for p in parts if _norm(p)]


def _collect() -> dict[str, dict]:
    """One pass over all finished recordings -> {person_key: rich record}."""
    selves = _self_names()
    people: dict[str, dict] = {}

    def rec_for(key: str, name: str, label: str) -> dict:
        r = people.get(key)
        if not r:
            r = {
                "key": key,
                "name": name or label,
                "label": label,
                "names": {},          # display name -> count (pick most common)
                "raw": set(),         # every raw name/label string ever used
                "roles": [],          # most-recent first
                "_byrec": {},         # rec_id -> interaction record
            }
            people[key] = r
        if name:
            r["names"][name] = r["names"].get(name, 0) + 1
            r["raw"].add(name)
        if label:
            r["raw"].add(label)
        return r

    # oldest first so "first_seen" / trend ordering is natural
    recs = sorted(
        (r for r in storage.list_recordings(limit=_BIG)
         if r.status.value == "done" and r.analysis),
        key=lambda r: r.created_at or "",
    )

    for rec in recs:
        a = rec.analysis
        date = rec.created_at or ""
        headline = a.headline or "Untitled"
        sentiment = a.sentiment or ""

        # index this recording's people for dynamic-attribution
        plist = []
        for p in a.people:
            nm, lb = (p.name or p.label), p.label
            key = _norm(nm) or _norm(lb)
            if not key or key in selves:
                continue
            rec_obj = rec_for(key, nm, lb)
            inter = rec_obj["_byrec"].setdefault(rec.id, {
                "rec_id": rec.id, "date": date, "headline": headline,
                "sentiment": sentiment, "role": p.role or "",
                "psych": [], "relationship": [], "quotes": [],
                "plans": [], "commitments": [],
            })
            if p.role:
                inter["role"] = p.role
            for q in p.identity_quotes:
                inter["quotes"].append(
                    {"text": q.text, "t": q.t, "significance": q.significance})
            plist.append((key, _norm(nm), _norm(lb), rec_obj, inter))

        def find(speaker):
            for key, nm_n, lb_n, rec_obj, inter in plist:
                if _matches(speaker, nm_n, lb_n):
                    return rec_obj, inter
            return None, None

        for d in a.psychological_dynamics:
            _, inter = find(d.speaker)
            if inter is not None:
                inter["psych"].append({
                    "label": d.label, "observation": d.observation,
                    "valence": d.valence or "neutral", "t": d.t,
                })

        for q in a.notable_quotes:
            _, inter = find(q.speaker)
            if inter is not None:
                inter["quotes"].append(
                    {"text": q.text, "t": q.t, "significance": q.significance})

        for pl in a.plans:
            _, inter = find(pl.who)
            if inter is not None:
                inter["plans"].append({"text": pl.text, "t": pl.t})
        for cm in a.commitments:
            _, inter = find(cm.who)
            if inter is not None:
                inter["commitments"].append({"text": cm.text, "t": cm.t})

        # relationship dynamics: attribute to every named participant
        for rd in a.relationship_dynamics:
            toks = _people_in(rd.people)
            with_self = any(t in selves for t in toks)
            for key, nm_n, lb_n, rec_obj, inter in plist:
                hit = any(_tok_eq(t, nm_n) or _tok_eq(t, lb_n) for t in toks)
                if hit:
                    others = [t for t in toks
                              if t not in (nm_n, lb_n) and t not in selves]
                    inter["relationship"].append({
                        "people": rd.people, "nature": rd.nature,
                        "description": rd.description, "t": rd.t,
                        "with_self": with_self,
                        "with": ", ".join(o.title() for o in others),
                    })

    # finalise roles ordering (most recent recording's role first)
    for r in people.values():
        ordered = sorted(r["_byrec"].values(),
                         key=lambda i: i["date"], reverse=True)
        roles = []
        for i in ordered:
            if i["role"] and i["role"] not in roles:
                roles.append(i["role"])
        r["roles"] = roles
        if r["names"]:
            r["name"] = max(r["names"], key=r["names"].get)
    return people


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #
def _valence_counts(interactions: list[dict]) -> tuple[int, int, int]:
    pos = neg = neu = 0
    for i in interactions:
        for d in i["psych"]:
            v = d.get("valence")
            if v == "positive":
                pos += 1
            elif v == "negative":
                neg += 1
            else:
                neu += 1
    return pos, neg, neu


def _tone(pos: int, neg: int) -> str:
    if pos == 0 and neg == 0:
        return "neutral"
    if pos >= neg * 1.6:
        return "warm"
    if neg >= pos * 1.6:
        return "strained"
    return "mixed"


def _score(i: dict) -> int:
    """Net emotional valence of a single interaction (+good / -concerning)."""
    return sum((1 if d.get("valence") == "positive"
                else -1 if d.get("valence") == "negative" else 0)
               for d in i["psych"])


def _trend(interactions: list[dict]) -> str:
    """Compare the older half vs the newer half of interactions."""
    scored = [i for i in interactions if i["psych"]]
    if len(scored) < 2:
        return "steady"
    mid = len(scored) // 2
    early = sum(_score(i) for i in scored[:mid]) / max(1, mid)
    late = sum(_score(i) for i in scored[mid:]) / max(1, len(scored) - mid)
    if late - early > 0.5:
        return "warming"
    if early - late > 0.5:
        return "cooling"
    return "steady"


def _ordered_interactions(rec: dict) -> list[dict]:
    return sorted(rec["_byrec"].values(), key=lambda i: i["date"])


# --------------------------------------------------------------------------- #
# Hidden / deleted people (non-destructive; reversible)
# --------------------------------------------------------------------------- #
def _hidden_path():
    return settings.data_path / "people_hidden.json"


def hidden_keys() -> set[str]:
    try:
        return {_norm(k) for k in json.loads(_hidden_path().read_text())}
    except Exception:
        return set()


def set_hidden(key: str, hide: bool = True) -> list[str]:
    with _hidden_lock:
        keys = hidden_keys()
        k = _norm(key)
        keys.add(k) if hide else keys.discard(k)
        try:
            p = _hidden_path()
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(keys)))
            os.replace(tmp, p)
        except Exception:
            pass
        return sorted(keys)


def raw_names_map() -> dict[str, list[str]]:
    """{person_key: [every raw name/label string used for them]}."""
    return {k: sorted(v["raw"]) for k, v in _collect().items()}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def list_people() -> list[dict]:
    """Summary card per person, most-interacted first."""
    people = _collect()
    hidden = hidden_keys()
    out = []
    for r in people.values():
        if r["key"] in hidden:
            continue
        inter = _ordered_interactions(r)
        if not inter:
            continue
        pos, neg, neu = _valence_counts(inter)
        natures = []
        for i in inter:
            for rd in i["relationship"]:
                if rd["nature"] and rd["nature"] not in natures:
                    natures.append(rd["nature"])
        out.append({
            "key": r["key"],
            "name": r["name"],
            "role": r["roles"][0] if r["roles"] else "",
            "interactions": len(inter),
            "first_seen": inter[0]["date"],
            "last_seen": inter[-1]["date"],
            "last_headline": inter[-1]["headline"],
            "positive": pos, "negative": neg, "neutral": neu,
            "tone": _tone(pos, neg),
            "trend": _trend(inter),
            "natures": natures[:5],
        })
    out.sort(key=lambda p: (p["interactions"], p["last_seen"]), reverse=True)
    return out


def get_person(key: str) -> Optional[dict]:
    """Full longitudinal profile for one person."""
    people = _collect()
    r = people.get(_norm(key)) or people.get(key)
    if not r:
        return None
    inter = _ordered_interactions(r)
    pos, neg, neu = _valence_counts(inter)
    natures = []
    for i in inter:
        for rd in i["relationship"]:
            if rd["nature"] and rd["nature"] not in natures:
                natures.append(rd["nature"])
    return {
        "key": r["key"],
        "name": r["name"],
        "roles": r["roles"],
        "interactions": len(inter),
        "first_seen": inter[0]["date"] if inter else "",
        "last_seen": inter[-1]["date"] if inter else "",
        "positive": pos, "negative": neg, "neutral": neu,
        "tone": _tone(pos, neg),
        "trend": _trend(inter),
        "natures": natures,
        # newest first for display
        "timeline": list(reversed(inter)),
    }


# --------------------------------------------------------------------------- #
# AI duplicate detection — "which of these are the same person?"
# --------------------------------------------------------------------------- #
_SUGGEST_TOOL = {
    "name": "emit_groups",
    "description": "Return groups of person entries that refer to the same real person.",
    "input_schema": {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "description": "Only groups with 2+ members that are the SAME person.",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical_name": {
                            "type": "string",
                            "description": "Best display name to keep for the merged person.",
                        },
                        "members": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "The exact `key` values that are this same person.",
                        },
                        "confidence": {"type": "number", "description": "0.0-1.0"},
                        "reason": {"type": "string", "description": "one line: why they're the same"},
                    },
                    "required": ["canonical_name", "members", "reason"],
                },
            }
        },
        "required": ["groups"],
    },
}

_SUGGEST_SYSTEM = """You are de-duplicating a personal relationship roster built \
from many separate audio recordings. The same real person can appear under \
different handles across recordings (e.g. "Dad" and "John"; "Son" and a name; \
"Ex" and "Orion"). Group entries that are clearly the SAME real person.

Be conservative: only group people you're genuinely confident are identical, \
using role, relationships (natures), and quotes as evidence. Different people who \
merely share a role (two different friends) must NOT be grouped. Generic group \
labels like "Teens" are usually NOT a single person — leave them alone. Return \
the exact `key` strings provided. Omit anyone with no duplicate."""


def suggest_merges() -> list[dict]:
    """Ask Claude which people are likely duplicates. Returns scored groups."""
    summaries = list_people()
    if len(summaries) < 2:
        return []
    people = _collect()
    items = []
    for p in summaries:
        rec = people.get(p["key"])
        quotes = []
        if rec:
            for i in _ordered_interactions(rec):
                for q in i["quotes"][:1]:
                    if q.get("text"):
                        quotes.append(q["text"][:140])
        items.append({
            "key": p["key"], "name": p["name"], "role": p["role"],
            "natures": p["natures"], "interactions": p["interactions"],
            "sample_quotes": quotes[:2],
        })

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model=settings.analysis_model,
            max_tokens=1500,
            system=_SUGGEST_SYSTEM,
            tools=[_SUGGEST_TOOL],
            tool_choice={"type": "tool", "name": "emit_groups"},
            messages=[{"role": "user",
                       "content": "People roster:\n" + json.dumps(items, indent=1)}],
        )
        groups = []
        valid = {p["key"] for p in summaries}
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_groups":
                for g in block.input.get("groups", []) or []:
                    members = [_norm(m) for m in g.get("members", [])]
                    members = [m for m in members if m in valid]
                    if len(set(members)) >= 2:
                        groups.append({
                            "canonical_name": g.get("canonical_name", ""),
                            "members": sorted(set(members)),
                            "confidence": g.get("confidence", 0.6),
                            "reason": g.get("reason", ""),
                        })
        return groups
    except Exception:
        return []
