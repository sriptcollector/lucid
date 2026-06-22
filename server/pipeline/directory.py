"""People directory — the part of Lucid that LEARNS who's who over time.

Every finished recording feeds this directory. For each named person it
accumulates:
  • a **speech signature** — the distinctive words they use and memorable lines
    they've said, which grows more characteristic the more they talk;
  • a running **voiceprint** (averaged speaker embedding) that is also pushed
    into voice-ID, so future recordings recognise them by voice automatically;
  • every **alias** you've ever called them, so when the AI mislabels someone and
    you correct it, that correction sticks and auto-fills next time.

Net effect: the more you record, the better Lucid gets at naming people without
you doing anything — and your manual fixes are never lost.

Stored as one JSON file (data/people_directory.json); safe to delete to reset.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Optional

from ..config import settings

# One process-wide lock guards the load→mutate→save cycle for the directory
# JSON so the two pipeline workers (and API threads) can't clobber each other.
_lock = threading.RLock()

# words too common to be a "signature"
_STOP = set((
    "the a an and or but to of in on at for with is are was were be been being am "
    "i you he she it we they me my your his her our their this that these those as "
    "so if then than too very just really like yeah yes no ok okay um uh got get go "
    "going gonna want know think right well thing things stuff kind sort lot also "
    "can will would could should do does did not have has had what when where who "
    "why how there here about would there's that's it's i'm you're don't can't "
    "gonna wanna up out down now then because they're we're he's she's of off into "
    "one two some any all more most much many even still back over again any"
).split())

_SELF = {"me", "myself", "i", "narrator", "self", "owner", "user"}


def _path():
    return settings.data_path / "people_directory.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text())
    except Exception:
        d = {}
    d.setdefault("people", {})
    d.setdefault("alias", {})      # normalized alias -> person id
    return d


def _save(d: dict) -> None:
    """Atomic write: a crash mid-write can never truncate the directory."""
    p = _path()
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, p)
    except Exception:
        pass


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _is_self(name: str) -> bool:
    return _norm(name) in _SELF


def _is_anon(name: str) -> bool:
    n = _norm(name)
    return (not n) or n.startswith("speaker") or n in ("someone", "unknown")


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z']{3,}", (text or "").lower())
            if w not in _STOP and not w.startswith("'")]


def _new_entry(name: str) -> dict:
    return {"name": name, "aliases": [], "role": "", "rec_ids": [],
            "seen_count": 0, "corrections": 0, "first_seen": "", "last_seen": "",
            "vocab": {}, "phrases": [], "natures": {},
            "voiceprint": None, "voice_n": 0}


# --------------------------------------------------------------------------- #
# alias index
# --------------------------------------------------------------------------- #
def _resolve_id(d: dict, name: str) -> Optional[str]:
    return d["alias"].get(_norm(name))


def _add_alias(d: dict, name: str, pid: str) -> None:
    n = _norm(name)
    if not n or _is_self(name) or _is_anon(name):
        return
    d["alias"][n] = pid
    ent = d["people"].get(pid)
    if ent and name and name not in ([ent["name"]] + ent["aliases"]):
        ent["aliases"].append(name)


# --------------------------------------------------------------------------- #
# speaker <-> person matching (mirrors relationships.py)
# --------------------------------------------------------------------------- #
def _seg_matches(speaker: Optional[str], names: list[str]) -> bool:
    s = _norm(speaker)
    if not s:
        return False
    for nm in names:
        n = _norm(nm)
        if n and (s == n or f" {n} " in f" {s} " or f" {s} " in f" {n} "):
            return True
    return False


def _tok_in(name: Optional[str], hay: Optional[str]) -> bool:
    """True if `name` appears as a whole token in `hay` (not a substring)."""
    n = _norm(name)
    return bool(n) and f" {n} " in f" {_norm(hay)} "


# --------------------------------------------------------------------------- #
# voice learning (best-effort; needs resemblyzer + decodable audio)
# --------------------------------------------------------------------------- #
def _learn_voice(rec, names: list[str], ent: dict) -> None:
    try:
        import numpy as np

        from . import voiceid
        segs = [s for s in rec.segments if _seg_matches(s.speaker, names)]
        if sum((s.end or 0) - (s.start or 0) for s in segs) < 4:   # need ~4s
            return
        wav = voiceid._decode_16k_mono(rec.filename)
        if wav is None:
            return
        sr = 16000
        clips = []
        for s in segs:
            c = wav[int(max(0, s.start) * sr): int((s.end or 0) * sr)]
            if len(c) >= int(0.6 * sr):
                clips.append(c)
        if not clips:
            return
        clip = np.concatenate(clips)
        if len(clip) < sr:
            return
        emb = voiceid._enc().embed_utterance(clip)
        prev, n = ent.get("voiceprint"), ent.get("voice_n", 0)
        avg = emb if not prev else (np.array(prev) * n + emb) / (n + 1)
        ent["voiceprint"] = np.asarray(avg, dtype=float).tolist()
        ent["voice_n"] = n + 1
        # push to voice-ID so future recordings auto-recognise this person
        with voiceid._lock:
            vp = voiceid._load()
            vp[ent["name"]] = ent["voiceprint"]
            voiceid._save(vp)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# learning entry points
# --------------------------------------------------------------------------- #
def learn_from_recording(rec) -> None:
    """Absorb everything this recording teaches us about its people.

    Idempotent per recording: re-analyzing the same recording updates aliases /
    role / last_seen but does NOT re-count vocabulary, phrases, natures, or
    re-average the voiceprint (which would inflate the signature over re-runs).
    """
    a = getattr(rec, "analysis", None)
    if not a:
        return
  # serialize the whole read-modify-write
    with _lock:
        d = _load()
        date = rec.created_at or ""
        for p in a.people:
            nm = (p.name or p.label or "").strip()
            if not nm or _is_self(nm) or _is_anon(nm):
                continue
            names = [n for n in (p.name, p.label) if n]
            pid = _resolve_id(d, nm) or _norm(nm)
            ent = d["people"].setdefault(pid, _new_entry(nm))
            if not ent.get("name"):
                ent["name"] = nm
            _add_alias(d, p.name, pid)
            _add_alias(d, p.label, pid)
            first_time = rec.id not in ent["rec_ids"]
            if first_time:
                ent["rec_ids"].append(rec.id)
            ent["seen_count"] = len(ent["rec_ids"])
            ent["last_seen"] = max(ent.get("last_seen", ""), date)
            ent["first_seen"] = ent["first_seen"] or date
            if p.role:
                ent["role"] = p.role
            if first_time:
                _absorb_signature(rec, a, p, names, ent)
        _save(d)


def _absorb_signature(rec, a, p, names, ent) -> None:
    """Speech signature: distinctive vocabulary from everything attributable to
    this person — their transcript turns (when speaker-labelled) AND their
    attributed quotes (which carry a speaker even before voice-ID)."""
    own_text = [(s.text_translated or s.text or "")
                for s in rec.segments if _seg_matches(s.speaker, names)]
    for q in p.identity_quotes:
        _add_phrase(ent, q.text)
        own_text.append(q.text or "")
    for q in a.notable_quotes:
        if _seg_matches(q.speaker, names):
            _add_phrase(ent, q.text)
            own_text.append(q.text or "")
    for w in _tokens(" ".join(own_text)):
        ent["vocab"][w] = ent["vocab"].get(w, 0) + 1
    # relationship natures over time
    for rd in a.relationship_dynamics:
        if any(_tok_in(nm, rd.people) for nm in names) and rd.nature:
            ent["natures"][rd.nature] = ent["natures"].get(rd.nature, 0) + 1
    _learn_voice(rec, names, ent)


def _add_phrase(ent: dict, text: str) -> None:
    text = (text or "").strip()
    if not text or len(text) < 8:
        return
    existing = {p.lower() for p in ent["phrases"]}
    if text.lower() not in existing:
        ent["phrases"].append(text)
        ent["phrases"] = ent["phrases"][-12:]   # keep the most recent dozen


def record_correction(src: str, dst: str, rec=None) -> None:
    """The user fixed a name. Remember it forever: src becomes an alias of dst,
    entries merge, and (if we have audio) we learn dst's voice from this clip."""
    if not src or not dst or _norm(src) == _norm(dst):
        return
    with _lock:
        d = _load()
        did = _resolve_id(d, dst) or _norm(dst)
        dent = d["people"].setdefault(did, _new_entry(dst))
        dent["name"] = dst
        dent["corrections"] = dent.get("corrections", 0) + 1
        _add_alias(d, dst, did)

        sid = _resolve_id(d, src)
        if sid and sid != did and sid in d["people"]:
            _merge_entries(d, did, sid)
        _add_alias(d, src, did)

        # carry the voiceprint stored under the old name over to the new name
        try:
            from . import voiceid
            vp = voiceid._load()
            if src in vp:
                vp[dst] = vp.pop(src)
                voiceid._save(vp)
        except Exception:
            pass

        if rec is not None:
            _learn_voice(rec, [dst, src], dent)
        _save(d)


def _merge_entries(d: dict, keep: str, drop: str) -> None:
    k, s = d["people"][keep], d["people"].pop(drop)
    k["aliases"] = list(dict.fromkeys(k["aliases"] + [s["name"]] + s["aliases"]))
    k["rec_ids"] = list(dict.fromkeys(k["rec_ids"] + s["rec_ids"]))
    k["seen_count"] = len(k["rec_ids"])
    k["phrases"] = (k["phrases"] + s["phrases"])[-12:]
    for w, c in s["vocab"].items():
        k["vocab"][w] = k["vocab"].get(w, 0) + c
    for nt, c in s["natures"].items():
        k["natures"][nt] = k["natures"].get(nt, 0) + c
    k["role"] = k["role"] or s["role"]
    k["first_seen"] = min(x for x in (k["first_seen"], s["first_seen"]) if x) \
        if (k["first_seen"] or s["first_seen"]) else ""
    k["last_seen"] = max(k["last_seen"], s["last_seen"])
    # re-point any aliases that referenced the dropped id
    for al, pid in list(d["alias"].items()):
        if pid == drop:
            d["alias"][al] = keep


# --------------------------------------------------------------------------- #
# read / suggest
# --------------------------------------------------------------------------- #
def resolve_name(name: str) -> Optional[str]:
    """Canonical display name for a known alias, else None."""
    d = _load()
    pid = _resolve_id(d, name)
    if pid and pid in d["people"]:
        canon = d["people"][pid]["name"]
        return canon if _norm(canon) != _norm(name) else None
    return None


def apply_known_names(rec) -> int:
    """Rewrite a recording's people to their learned canonical names. Returns
    how many were auto-filled. This is how recognition 'gets better' silently."""
    from .rename import rename_person
    a = getattr(rec, "analysis", None)
    if not a:
        return 0
    n = 0
    for p in list(a.people):
        for raw in {p.name, p.label}:
            canon = resolve_name(raw) if raw else None
            if canon:
                rename_person(rec, raw, canon)
                n += 1
    return n


def autofill(q: str, limit: int = 8) -> list[str]:
    """Name suggestions for the correction box — known people first."""
    d = _load()
    qn = _norm(q)
    scored = []
    for ent in d["people"].values():
        names = [ent["name"]] + ent["aliases"]
        hay = " ".join(_norm(x) for x in names)
        if not qn:
            scored.append((ent["seen_count"], ent["name"]))
        elif any(_norm(x).startswith(qn) for x in names):
            scored.append((100 + ent["seen_count"], ent["name"]))
        elif qn in hay:
            scored.append((ent["seen_count"], ent["name"]))
    scored.sort(reverse=True)
    out, seen = [], set()
    for _, nm in scored:
        if nm.lower() not in seen:
            seen.add(nm.lower())
            out.append(nm)
        if len(out) >= limit:
            break
    return out


def list_directory() -> list[dict]:
    d = _load()
    out = []
    for pid, e in d["people"].items():
        top_words = [w for w, _ in sorted(e["vocab"].items(),
                     key=lambda kv: kv[1], reverse=True)[:8]]
        out.append({
            "id": pid,
            "name": e["name"],
            "aliases": e["aliases"],
            "role": e.get("role", ""),
            "seen_count": e.get("seen_count", 0),
            "corrections": e.get("corrections", 0),
            "first_seen": e.get("first_seen", ""),
            "last_seen": e.get("last_seen", ""),
            "top_words": top_words,
            "phrases": e.get("phrases", [])[-3:],
            "has_voice": bool(e.get("voiceprint")),
            "voice_samples": e.get("voice_n", 0),
            "recognition": _recognition_strength(e),
        })
    out.sort(key=lambda x: (x["seen_count"], x["last_seen"]), reverse=True)
    return out


def _recognition_strength(e: dict) -> str:
    score = e.get("seen_count", 0) + e.get("voice_n", 0) * 2 + len(e.get("vocab", {})) / 20
    if e.get("voiceprint") and e.get("voice_n", 0) >= 2:
        return "strong"
    if score >= 3 or e.get("voiceprint"):
        return "learning"
    return "new"


def forget(pid: str) -> None:
    with _lock:
        d = _load()
        ent = d["people"].pop(pid, None)
        if ent:
            for al in list(d["alias"]):
                if d["alias"][al] == pid:
                    d["alias"].pop(al, None)
            try:
                from . import voiceid
                vp = voiceid._load()
                if ent["name"] in vp:
                    vp.pop(ent["name"], None)
                    voiceid._save(vp)
            except Exception:
                pass
        _save(d)
