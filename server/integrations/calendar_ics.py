"""Calendar matching (read-only) via a secret iCal/ICS URL — no OAuth.

Google Calendar (and most others) expose a private "secret address in iCal
format" per calendar. Paste that URL and Lucid fetches your events, caches them
(``data/calendar_events.json``), and for each recording finds the event around
that time. The event's attendee names (correctly spelled) and title become
*known context* for the analyzer — so a meeting's note gets the right names and
knows what it was about. Stdlib-only; nothing is ever written back.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
import urllib.request
from typing import Optional

from ..config import settings

_lock = threading.RLock()


class CalendarError(Exception):
    """A failed calendar fetch/parse, with a short human-readable message."""


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Lucid/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        raise CalendarError(str(e))


# --------------------------------------------------------------------------- #
# ICS parsing (stdlib; tolerant of folding + escaping)
# --------------------------------------------------------------------------- #
def _unfold(text: str) -> list[str]:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for ln in raw.split("\n"):
        if ln[:1] in (" ", "\t") and lines:
            lines[-1] += ln[1:]
        else:
            lines.append(ln)
    return lines


def _split_prop(line: str) -> tuple[str, dict, str]:
    colon = line.find(":")
    if colon < 0:
        return "", {}, ""
    head, value = line[:colon], line[colon + 1:]
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v
    return parts[0].upper(), params, value


def _unescape(v: str) -> str:
    return (v.replace("\\n", " ").replace("\\N", " ")
             .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip())


def _clean(v: str) -> str:
    return v.strip().strip('"').strip()


def _parse_dt(val: str) -> tuple[str, Optional[float]]:
    """ICS datetime -> (YYYY-MM-DD, epoch_seconds | None). Times are read as
    UTC-ish; matching is same-day + closest, so small tz drift is harmless."""
    v = (val or "").strip()
    try:
        if len(v) >= 8 and v[:8].isdigit():
            y, m, d = int(v[:4]), int(v[4:6]), int(v[6:8])
            date_str = f"{y:04d}-{m:02d}-{d:02d}"
            if "T" in v and len(v) >= 15:
                hh, mm, ss = int(v[9:11]), int(v[11:13]), int(v[13:15])
                base = _dt.datetime(y, m, d, hh, mm, ss, tzinfo=_dt.timezone.utc)
            else:
                base = _dt.datetime(y, m, d, tzinfo=_dt.timezone.utc)
            return date_str, base.timestamp()
    except Exception:
        pass
    return "", None


def _parse_events(text: str) -> list[dict]:
    events: list[dict] = []
    cur: Optional[dict] = None
    for ln in _unfold(text):
        if ln == "BEGIN:VEVENT":
            cur = {"summary": "", "organizer": "", "description": "",
                   "location": "", "dtstart": "", "attendees": []}
        elif ln == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None:
            name, params, value = _split_prop(ln)
            if name == "SUMMARY":
                cur["summary"] = _unescape(value)
            elif name == "DESCRIPTION":
                cur["description"] = _unescape(value)
            elif name == "LOCATION":
                cur["location"] = _unescape(value)
            elif name == "DTSTART":
                cur["dtstart"] = value
            elif name == "ATTENDEE":
                cn = _clean(params.get("CN", ""))
                if cn and "@" not in cn:
                    cur["attendees"].append(cn)
            elif name == "ORGANIZER":
                cn = _clean(params.get("CN", ""))
                if cn and "@" not in cn:
                    cur["organizer"] = cn
    return events


# --------------------------------------------------------------------------- #
# fetch + cache
# --------------------------------------------------------------------------- #
def test_and_describe() -> tuple[bool, str, int]:
    url = settings.get_cal_ics_url()
    if not url:
        return False, "No calendar URL saved.", 0
    try:
        text = _fetch(url)
    except CalendarError as e:
        return False, str(e), 0
    if "VEVENT" not in text and "VCALENDAR" not in text:
        return False, "That URL didn't return a calendar (expected iCal/ICS).", 0
    return True, "ok", len(_parse_events(text))


def refresh_events() -> int:
    url = settings.get_cal_ics_url()
    if not url:
        return 0
    parsed = _parse_events(_fetch(url))
    events = []
    for e in parsed:
        date_str, epoch = _parse_dt(e["dtstart"])
        if not date_str:
            continue
        events.append({
            "summary": e["summary"],
            "date": date_str,
            "start_epoch": epoch,
            "attendees": e["attendees"],
            "organizer": e["organizer"],
            "location": e["location"],
            "description": (e["description"] or "")[:500],
        })
    with _lock:
        path = settings.cal_events_path
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": time.time(), "events": events}))
        os.replace(tmp, path)
    return len(events)


def load_events() -> list[dict]:
    try:
        d = json.loads(settings.cal_events_path.read_text())
        return d.get("events", []) if isinstance(d, dict) else []
    except Exception:
        return []


def last_refresh() -> float:
    try:
        return float(json.loads(settings.cal_events_path.read_text()).get("fetched_at", 0))
    except Exception:
        return 0.0


def event_names(limit: int = 400) -> list[str]:
    """Every distinct attendee/organizer name across the calendar."""
    seen: set[str] = set()
    out: list[str] = []
    for e in load_events():
        for nm in [e.get("organizer", "")] + e.get("attendees", []):
            nm = (nm or "").strip()
            k = nm.lower()
            if nm and k not in seen:
                seen.add(k)
                out.append(nm)
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------- #
# per-recording matching
# --------------------------------------------------------------------------- #
def _rec_time(created_at: str) -> tuple[str, Optional[float]]:
    s = (created_at or "").strip()
    epoch = None
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        epoch = d.timestamp()
    except Exception:
        pass
    return s[:10], epoch


def context_for(created_at: str, window_hours: Optional[float] = None) -> tuple[list[str], str]:
    """Find the calendar event(s) around a recording. Returns (names, context)."""
    events = load_events()
    if not events:
        return [], ""
    date, epoch = _rec_time(created_at)
    if not date:
        return [], ""
    window = (settings.cal_window_hours if window_hours is None else window_hours) * 3600
    cand = [e for e in events if e["date"] == date]
    if not cand:
        return [], ""
    if epoch:
        cand.sort(key=lambda e: abs((e["start_epoch"] or epoch) - epoch))
        inwin = [e for e in cand if e["start_epoch"] and abs(e["start_epoch"] - epoch) <= window]
        cand = inwin or cand[:1]
    cand = cand[:2]

    names: list[str] = []
    ctx: list[str] = []
    for e in cand:
        ppl = [p for p in ([e.get("organizer", "")] + e.get("attendees", [])) if p]
        names.extend(ppl)
        bits = []
        if e.get("summary"):
            bits.append(f"\"{e['summary']}\"")
        if ppl:
            bits.append("with " + ", ".join(dict.fromkeys(ppl)))
        if e.get("location"):
            bits.append("at " + e["location"])
        if bits:
            ctx.append("; ".join(bits))
    seen: set[str] = set()
    uniq = []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    return uniq, " | ".join(ctx)
