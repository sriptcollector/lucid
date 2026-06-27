"""Execute action-card intents for real.

The confirm-and-execute layer: turn a spoken intent ("put that on the calendar",
"I'll email them") into an actual Google Calendar event or a sent email, through the
already-authorized gmailassistant account (a short subprocess in its venv — same pattern
as email_login / crm_sync), and draft email bodies with the configured Anthropic key.
Best-effort; never raises — always returns {ok: bool, ...}.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

TZ = os.environ.get("LUCID_TZ", "America/New_York")


def _gmail_paths() -> tuple[str, str]:
    code = Path(__file__).resolve().parents[3]                  # .../code
    d = os.environ.get("GMAILASSISTANT_DIR") or str(code / "gmailassistant")
    for c in (os.environ.get("GMAILASSISTANT_PY"),
              str(code / "gmailassistant" / ".venv" / "Scripts" / "python.exe"),
              str(code / "gmailassistant" / ".venv" / "bin" / "python")):
        if c and Path(c).exists():
            return d, c
    return d, "python"


def _run(script: str, payload: dict, timeout: int = 60) -> dict:
    d, py = _gmail_paths()
    if not Path(d, "gmail_auth.py").exists():
        return {"ok": False, "error": "gmail account not found"}
    try:
        r = subprocess.run([py, "-c", script, json.dumps(payload)], cwd=d,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                           capture_output=True, text=True, timeout=timeout)
        for line in reversed((r.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return {"ok": False, "error": ((r.stderr or r.stdout or "failed").strip())[-240:]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


_CAL_SCRIPT = r'''
import sys, json
sys.path.insert(0, ".")
from gmail_auth import get_calendar_service
p = json.loads(sys.argv[1])
svc = get_calendar_service()
body = {
    "summary": p["title"],
    "description": p.get("description", ""),
    "start": {"dateTime": p["start"], "timeZone": p.get("tz")},
    "end":   {"dateTime": p["end"],   "timeZone": p.get("tz")},
}
if p.get("location"):
    body["location"] = p["location"]
ats = [a for a in (p.get("attendees") or []) if a and "@" in a]
if ats:
    body["attendees"] = [{"email": a} for a in ats]
ev = svc.events().insert(calendarId="primary", body=body,
                         sendUpdates="all" if ats else "none").execute()
print(json.dumps({"ok": True, "id": ev.get("id"), "link": ev.get("htmlLink")}))
'''


def create_event(p: dict) -> dict:
    """p: {title, start(iso), end(iso), description?, location?, attendees?[], tz?}."""
    if not p.get("title") or not p.get("start") or not p.get("end"):
        return {"ok": False, "error": "title, start, end required"}
    p.setdefault("tz", TZ)
    return _run(_CAL_SCRIPT, p)


_EMAIL_SCRIPT = r'''
import sys, json
sys.path.insert(0, ".")
from gmail_auth import get_service
from gmail_client import Gmail
p = json.loads(sys.argv[1])
svc = get_service()
me = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
to = p["to"] if isinstance(p["to"], list) else [p["to"]]
Gmail(svc, me).send_message(to, p["subject"], p["body"], p.get("html"))
print(json.dumps({"ok": True, "from": me}))
'''


def send_email(p: dict) -> dict:
    """p: {to(str|list), subject, body, html?}."""
    if not p.get("to") or not p.get("subject"):
        return {"ok": False, "error": "to + subject required"}
    return _run(_EMAIL_SCRIPT, p)


def draft_email(context: str, intent: str, owner_name: str = "", to_name: str = "") -> dict:
    """Draft a short email with the configured Anthropic key (cheap model, on demand)."""
    try:
        from ..config import settings
        key = settings.anthropic_api_key
    except Exception:
        key = ""
    if not key:
        return {"ok": False, "error": "no api key"}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        sys_p = ("You draft one short, warm, professional email the user can send as-is. "
                 "Return ONLY compact JSON {\"subject\":\"...\",\"body\":\"...\"} — no preamble, "
                 "no markdown. Keep the body to 2-5 sentences; sign off as the sender's first name.")
        usr = (f"My notes mention: {context}\n\nThe thing I want to do: {intent}\n"
               f"I am {owner_name or 'the sender'}." + (f" The recipient is {to_name}." if to_name else "")
               + " Draft the email I should send.")
        m = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=600,
                                   system=sys_p, messages=[{"role": "user", "content": usr}])
        txt = "".join(getattr(b, "text", "") for b in m.content)
        import re
        mm = re.search(r"\{.*\}", txt, re.S)
        d = json.loads(mm.group(0)) if mm else {"subject": intent[:70], "body": txt.strip()}
        return {"ok": True, "subject": d.get("subject", "")[:140], "body": d.get("body", "")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
