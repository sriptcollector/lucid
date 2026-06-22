"""Rename a person consistently across a recording's transcript + analysis.

Shared by the API (manual rename / chat edits / merges), the runner (auto-fill
of names the directory has learned), and the directory. Exact match on the
structured speaker/owner fields; whole-word substitution in free text.
"""
from __future__ import annotations

import re


def rename_person(rec, src: str, dst: str) -> None:
    if not src or not dst or src == dst:
        return
    pat = re.compile(rf"\b{re.escape(src)}\b")
    exact = lambda s: dst if s == src else s            # noqa: E731
    txt = lambda s: pat.sub(dst, s) if s else s          # noqa: E731

    for seg in rec.segments:
        if seg.speaker:
            seg.speaker = exact(seg.speaker)
    a = rec.analysis
    if not a:
        return
    a.headline = txt(a.headline)
    a.summary = txt(a.summary)
    for p in a.people:
        if p.name == src:
            p.name = dst
        if p.label == src:
            p.label = dst
        p.role = txt(p.role)
        for q in p.identity_quotes:
            q.speaker = exact(q.speaker) if q.speaker else q.speaker
            q.significance = txt(q.significance)
    for q in a.notable_quotes:
        if q.speaker:
            q.speaker = exact(q.speaker)
        q.significance = txt(q.significance)
    for d in a.psychological_dynamics:
        if d.speaker:
            d.speaker = exact(d.speaker)
        d.observation = txt(d.observation)
    for pl in a.plans:
        if pl.who:
            pl.who = exact(pl.who)
        pl.text = txt(pl.text)
    for cm in a.commitments:
        if cm.who:
            cm.who = exact(cm.who)
        cm.text = txt(cm.text)
    for rd in a.relationship_dynamics:
        rd.people = txt(rd.people)
        rd.description = txt(rd.description)
    for ai in a.action_items:
        if ai.owner:
            ai.owner = exact(ai.owner)
        ai.text = txt(ai.text)
    for e in a.timeline:
        if e.speaker:
            e.speaker = exact(e.speaker)
        e.title, e.detail = txt(e.title), txt(e.detail)
    a.speakers = {txt(k): txt(v) for k, v in a.speakers.items()}
