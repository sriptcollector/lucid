"""Ventures — every business idea you and the people around you have raised,
turned into a buildable plan.

It harvests the `ideas` extracted from each recording (see analyze.py), clusters
the same venture across conversations, and — on demand — asks Claude to expand a
venture into a complete BUILD SPEC: everything an engineer (or Claude Code) would
need to actually build the MVP. Gaps are predicted and clearly flagged as
assumptions, so the spec is self-contained.

Specs are cached in data/ventures.json keyed by a hash of the source idea, so
they only regenerate when the underlying discussion changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from typing import Optional

import anthropic

from .. import storage
from ..config import settings

_lock = threading.RLock()
_BIG = 1_000_000


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _path():
    return settings.data_path / "ventures.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text())
    except Exception:
        d = {}
    d.setdefault("specs", {})     # venture_id -> {"hash":..., "spec":{...}}
    return d


def _save(d: dict) -> None:
    p = _path()
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, p)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _collect() -> dict[str, dict]:
    """Cluster ideas from every recording into ventures keyed by title."""
    ventures: dict[str, dict] = {}
    recs = sorted(
        (r for r in storage.list_recordings(limit=_BIG)
         if r.status.value == "done" and r.analysis and r.analysis.ideas),
        key=lambda r: r.created_at or "",
    )
    for rec in recs:
        for idea in rec.analysis.ideas:
            key = _norm(idea.title)[:64] or _norm(idea.summary)[:64]
            if not key:
                continue
            v = ventures.get(key)
            if not v:
                v = {
                    "id": key, "title": idea.title, "summary": idea.summary,
                    "details": idea.details or "", "status": idea.status or "",
                    "proposed_by": idea.proposed_by or "",
                    "perspectives": [], "sources": [], "first_seen": rec.created_at or "",
                }
                ventures[key] = v
            # keep the richest summary/details seen across mentions
            if len(idea.summary or "") > len(v["summary"]):
                v["summary"] = idea.summary
            if len(idea.details or "") > len(v["details"]):
                v["details"] = idea.details
            v["proposed_by"] = v["proposed_by"] or idea.proposed_by or ""
            v["status"] = idea.status or v["status"]
            v["last_seen"] = rec.created_at or ""
            for p in idea.perspectives:
                v["perspectives"].append(
                    {"person": p.person, "stance": p.stance, "view": p.view})
            v["sources"].append({
                "rec_id": rec.id, "date": rec.created_at or "",
                "headline": rec.analysis.headline or "",
            })
    return ventures


def _fingerprint(v: dict) -> str:
    """Hash of a venture's substance — changes only if the discussion changes,
    so a cached spec is reused until then."""
    persp = "|".join(f"{p['person']}:{p['stance']}:{p['view']}" for p in v["perspectives"])
    blob = f"{v['title']}\n{v['summary']}\n{v['details']}\n{persp}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"


def _business_ids(ventures: dict) -> set[str]:
    """Keep only genuine business/product/startup ventures — drop personal
    decisions, logistics, legal/admin, comp talk, and abstract beliefs. Cached;
    only re-runs when the set of ideas changes."""
    ids = sorted(ventures.keys())
    if not ids:
        return set()
    h = hashlib.sha1(json.dumps(ids).encode("utf-8")).hexdigest()[:16]
    cached = _load().get("classify")
    if cached and cached.get("hash") == h:
        return set(cached.get("business_ids", []))

    items = [{"id": v["id"], "title": v["title"], "summary": (v["summary"] or "")[:160]}
             for v in ventures.values()]
    keep = set(ids)  # fail-open: if classification fails, show everything
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        tool = {
            "name": "pick",
            "description": "Return the ids that are real business/product/startup ventures.",
            "input_schema": {
                "type": "object",
                "properties": {"business_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["business_ids"],
            },
        }
        msg = client.messages.create(
            model=_CLASSIFY_MODEL,
            max_tokens=1500,
            system="From a list of ideas pulled from conversations, return ONLY the "
                   "ids that are genuine business / product / startup / venture ideas — "
                   "something someone could build and monetize. EXCLUDE personal "
                   "decisions, logistics, legal/admin tasks, compensation or pay "
                   "negotiations, one-off errands, and abstract beliefs or observations.",
            tools=[tool],
            tool_choice={"type": "tool", "name": "pick"},
            messages=[{"role": "user", "content": json.dumps(items)}],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "pick":
                picked = {i for i in block.input.get("business_ids", []) if i in ventures}
                if picked:
                    keep = picked
    except Exception:
        pass
    with _lock:
        d = _load()
        d["classify"] = {"hash": h, "business_ids": sorted(keep)}
        _save(d)
    return keep


def list_ventures() -> list[dict]:
    specs = _load()["specs"]
    collected = _collect()
    business = _business_ids(collected)
    out = []
    for v in collected.values():
        if v["id"] not in business:
            continue
        people = sorted({p["person"] for p in v["perspectives"] if p["person"]})
        cached = specs.get(v["id"])
        out.append({
            "id": v["id"],
            "title": v["title"],
            "summary": v["summary"],
            "status": v["status"],
            "proposed_by": v["proposed_by"],
            "people": people,
            "mentions": len(v["sources"]),
            "has_spec": bool(cached) and cached.get("hash") == _fingerprint(v),
            "first_seen": v["first_seen"],
            "last_seen": v.get("last_seen", v["first_seen"]),
        })
    out.sort(key=lambda x: (x["mentions"], x["last_seen"]), reverse=True)
    return out


def get_venture(vid: str) -> Optional[dict]:
    v = _collect().get(_norm(vid)) or _collect().get(vid)
    if not v:
        return None
    cached = _load()["specs"].get(v["id"])
    v["spec"] = cached["spec"] if (cached and cached.get("hash") == _fingerprint(v)) else None
    v["spec_stale"] = bool(cached) and cached.get("hash") != _fingerprint(v)
    return v


# --------------------------------------------------------------------------- #
# AI build-spec generator
# --------------------------------------------------------------------------- #
_BUILD_SYSTEM = """You are a sharp technical co-founder. You take a raw business \
idea pulled from a recorded conversation and turn it into a COMPLETE, BUILDABLE \
spec — everything an engineer (or an AI coding agent like Claude Code) would need \
to build the MVP without talking to anyone.

Rules:
- Be concrete and exhaustive. Real feature names, real tech choices, real data \
models, real first steps. No vague filler.
- The transcript only gives you the seed. PREDICT and fill every gap with the \
most sensible, modern choice — but list anything you inferred in `assumptions` so \
it's honest about what came from the conversation vs. what you decided.
- Preserve the founders' actual intent and any specific details/constraints they \
voiced (use the perspectives). If people disagreed, reflect the strongest version.
- Make `first_build_steps` something you could hand straight to Claude Code to \
start building today (stack init, schema, first endpoints/screens).
- Default to a lean, modern, shippable stack unless the idea demands otherwise."""

_BUILD_TOOL = {
    "name": "emit_spec",
    "description": "Return the complete build spec for this venture.",
    "input_schema": {
        "type": "object",
        "properties": {
            "one_liner": {"type": "string", "description": "the venture in one sentence"},
            "viability": {
                "type": "object",
                "properties": {
                    "score": {"type": "number", "description": "0-10 buildability/opportunity"},
                    "read": {"type": "string", "description": "honest take on whether/why this could work"},
                },
            },
            "problem": {"type": "string"},
            "solution": {"type": "string"},
            "target_customer": {"type": "string"},
            "value_prop": {"type": "string"},
            "core_features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {"type": "string", "enum": ["must", "should", "could"]},
                    },
                    "required": ["name", "description"],
                },
            },
            "mvp_scope": {"type": "string", "description": "exactly what's in v1 (and what's out)"},
            "tech_stack": {
                "type": "object",
                "properties": {
                    "frontend": {"type": "string"}, "backend": {"type": "string"},
                    "database": {"type": "string"}, "ai": {"type": "string"},
                    "apis": {"type": "string"}, "infra": {"type": "string"},
                },
            },
            "data_model": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string"},
                        "fields": {"type": "string", "description": "key fields + types"},
                        "notes": {"type": "string"},
                    },
                    "required": ["entity", "fields"],
                },
            },
            "user_flows": {"type": "array", "items": {"type": "string"}},
            "monetization": {"type": "string"},
            "pricing": {"type": "string"},
            "go_to_market": {"type": "string"},
            "competitors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "note": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "differentiation": {"type": "string"},
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"risk": {"type": "string"}, "mitigation": {"type": "string"}},
                    "required": ["risk"],
                },
            },
            "key_metrics": {"type": "array", "items": {"type": "string"}},
            "roadmap": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "phase": {"type": "string"},
                        "goal": {"type": "string"},
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["phase"],
                },
            },
            "cost_estimate": {"type": "string"},
            "team_needs": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
            "first_build_steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "concrete steps to start building NOW (hand to Claude Code)",
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "things you predicted/filled that weren't in the conversation",
            },
        },
        "required": ["one_liner", "problem", "solution", "core_features",
                     "mvp_scope", "tech_stack", "first_build_steps"],
    },
}


def _venture_context(v: dict) -> str:
    lines = [
        f"VENTURE: {v['title']}",
        f"What it is: {v['summary']}" if v["summary"] else "",
        f"Details discussed: {v['details']}" if v["details"] else "",
        f"Status in conversation: {v['status']}" if v["status"] else "",
        f"Proposed by: {v['proposed_by']}" if v["proposed_by"] else "",
    ]
    if v["perspectives"]:
        lines.append("\nWhat the people involved think:")
        for p in v["perspectives"]:
            who = p.get("person") or "Someone"
            st = f" ({p['stance']})" if p.get("stance") else ""
            lines.append(f"- {who}{st}: {p.get('view', '')}")
    heads = "; ".join(s["headline"] for s in v["sources"] if s["headline"])
    if heads:
        lines.append(f"\nDiscussed in: {heads}")
    return "\n".join(l for l in lines if l)


def _generate_spec(v: dict) -> dict:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.analysis_model,
        max_tokens=8000,
        system=[{"type": "text", "text": _BUILD_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        tools=[_BUILD_TOOL],
        tool_choice={"type": "tool", "name": "emit_spec"},
        messages=[{"role": "user",
                   "content": "Build the complete spec for this venture:\n\n"
                              + _venture_context(v)}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_spec":
            return block.input
    return {}


def build_spec(vid: str) -> Optional[dict]:
    """Generate (and cache) the full build spec for a venture. Returns the spec."""
    v = _collect().get(_norm(vid)) or _collect().get(vid)
    if not v:
        return None
    spec = _generate_spec(v)
    if not spec:
        return None
    with _lock:
        d = _load()
        d["specs"][v["id"]] = {"hash": _fingerprint(v), "spec": spec}
        _save(d)
    return spec
