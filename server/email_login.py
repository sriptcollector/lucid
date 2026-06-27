"""Passwordless email-code login.

The owner signs in by receiving a 6-digit code at ``settings.owner_email`` and
typing it back — no password. The code is emailed through the already-configured
gmailassistant Gmail account (reused via a short subprocess, same pattern as
``crm_sync``), so there is no new mail provider to set up. A correct code returns
the app bearer token, which the browser stores in localStorage and reuses
forever (that's the "remember login").

Security: codes are 6 digits, single-use, expire in 10 min, max 5 guesses, and
sends are throttled. We never reveal whether a typed address is the owner's
(always answer ``{ok:true}``), so the endpoint can't be used to probe the email.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import subprocess
import time
from pathlib import Path

from .config import settings

CODE_TTL = 600          # seconds a code stays valid
RESEND_GAP = 30         # min seconds between sends to one address
MAX_TRIES = 5           # wrong guesses before a code is burned

_CODES: dict[str, dict] = {}        # email -> {hash, exp, tries}
_LAST_SENT: dict[str, float] = {}   # email -> ts of last send (spam guard)


def _owner_email() -> str:
    return (settings.owner_email or "").strip().lower()


def _hash(code: str) -> str:
    return hashlib.sha256(("lucid-otp:" + (code or "")).encode("utf-8")).hexdigest()


def _gmail_paths() -> tuple[str, str]:
    """(gmailassistant dir, its python). Same side-by-side layout as crm_sync."""
    code = Path(__file__).resolve().parents[2]                      # .../code
    d = os.environ.get("GMAILASSISTANT_DIR") or str(code / "gmailassistant")
    for cand in (os.environ.get("GMAILASSISTANT_PY"),
                 str(code / "gmailassistant" / ".venv" / "Scripts" / "python.exe"),
                 str(code / "gmailassistant" / ".venv" / "bin" / "python")):
        if cand and Path(cand).exists():
            return d, cand
    return d, "python"


_SEND_SCRIPT = r"""
import sys; sys.path.insert(0, ".")
from gmail_auth import get_service
from gmail_client import Gmail
to = sys.argv[1]; code = sys.argv[2]
svc = get_service()
me = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
sub = "Your Lucid sign-in code: " + code
body = ("Your Lucid login code is " + code + "\n\n"
        "It expires in 10 minutes. If you didn't request it, ignore this email.")
html = (
  '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:420px;'
  'margin:0 auto;padding:28px 24px;color:#26211d">'
  '<div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#9a8f86">Lucid</div>'
  '<div style="font-size:17px;margin:14px 0 18px">Here\'s your sign-in code</div>'
  '<div style="font-size:34px;font-weight:700;letter-spacing:.18em;'
  'background:#f4f1ec;border-radius:12px;padding:16px 0;text-align:center">' + code + '</div>'
  '<div style="font-size:13px;color:#9a8f86;margin-top:18px;line-height:1.5">'
  'Expires in 10 minutes. If you didn\'t request this, you can ignore it.</div></div>')
Gmail(svc, me).send_message([to], sub, body, html)
print("SENT")
"""


def _send_code_email(to: str, code: str) -> bool:
    d, py = _gmail_paths()
    if not Path(d, "gmail_client.py").exists():
        return False
    try:
        r = subprocess.run([py, "-c", _SEND_SCRIPT, to, code], cwd=d,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and "SENT" in (r.stdout or "")
    except Exception:
        return False


def configured() -> bool:
    """Email login is offered only when an owner address is set and Gmail is reachable."""
    d, _ = _gmail_paths()
    return bool(_owner_email()) and Path(d, "gmail_client.py").exists()


def request_code(email: str) -> dict:
    """Generate + email a code if ``email`` is the owner. Always returns ok (no
    enumeration); ``sent`` reflects the real outcome only for the owner."""
    email = (email or "").strip().lower()
    owner = _owner_email()
    now = time.time()
    gap = now - _LAST_SENT.get(email, 0.0)
    if gap < RESEND_GAP:
        return {"ok": True, "retry_in": int(RESEND_GAP - gap)}
    _LAST_SENT[email] = now
    if not owner or email != owner:
        return {"ok": True}                       # silent for non-owner addresses
    code = f"{secrets.randbelow(1_000_000):06d}"
    _CODES[email] = {"hash": _hash(code), "exp": now + CODE_TTL, "tries": 0}
    return {"ok": True, "sent": _send_code_email(owner, code)}


def verify_code(email: str, code: str) -> str | None:
    """Return the bearer token if the code is right + live, else None."""
    email = (email or "").strip().lower()
    rec = _CODES.get(email)
    if not rec:
        return None
    if time.time() > rec["exp"]:
        _CODES.pop(email, None)
        return None
    rec["tries"] += 1
    if rec["tries"] > MAX_TRIES:
        _CODES.pop(email, None)
        return None
    if hmac.compare_digest(rec["hash"], _hash((code or "").strip())):
        _CODES.pop(email, None)
        return _issue_token()
    return None


def _issue_token() -> str:
    tok = settings.link_token or secrets.token_urlsafe(24)
    if not settings.link_token:
        settings.save_config({"api_tokens": tok})
    return tok
