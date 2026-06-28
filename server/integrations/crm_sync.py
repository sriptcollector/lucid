"""Keep the orionscrm CRM roster fresh from inside Lucid.

orionscrm writes ``data/crm_export.json`` (the roster Lucid's CRM page serves) when its sync runs.
The cloud cron updates Notion but can't reach this PC, so the LOCAL export only refreshes when a
local sync runs. This module runs that sync as a subprocess on demand (when you open the CRM and
it's stale, or hit Refresh), so the page is current while Lucid is up. Best-effort, never raises.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_STATE = {"running": False, "last_run": 0.0, "last_ok": None, "last_msg": ""}


def _paths() -> tuple[str, str]:
    """(orionscrm dir, python exe). Defaults assume the apps live side-by-side under code/ and
    orionscrm runs under the gmailassistant venv (same as when run by hand). Overridable via env."""
    code = Path(__file__).resolve().parents[3]                 # .../code
    crm_dir = os.environ.get("ORIONSCRM_DIR") or str(code / "orionscrm")
    py = os.environ.get("ORIONSCRM_PY") or str(code / "gmailassistant" / ".venv" / "Scripts" / "python.exe")
    if not Path(py).exists():
        py = "python"
    return crm_dir, py


def available() -> bool:
    crm_dir, _ = _paths()
    return Path(crm_dir, "cli.py").exists()


def status() -> dict:
    return dict(_STATE)


def run_once(timeout: int = 600) -> bool:
    """Run `cli.py sync` in the orionscrm dir. Returns True on success. Non-reentrant."""
    crm_dir, py = _paths()
    if not Path(crm_dir, "cli.py").exists():
        _STATE["last_msg"] = "orionscrm not found"
        return False
    if not _LOCK.acquire(blocking=False):
        return False
    _STATE["running"] = True
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        if not env.get("ANTHROPIC_API_KEY"):          # orionscrm classifies via this key
            try:
                from ..config import settings
                if settings.anthropic_api_key:
                    env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
            except Exception:
                pass
        r = subprocess.run([py, "cli.py", "sync"], cwd=crm_dir, env=env,
                           capture_output=True, text=True, timeout=timeout)
        ok = r.returncode == 0
        _STATE.update(last_run=time.time(), last_ok=ok,
                      last_msg=((r.stdout or "") + (r.stderr or "")).strip()[-300:])
        return ok
    except Exception as e:  # noqa: BLE001
        _STATE.update(last_run=time.time(), last_ok=False, last_msg=str(e)[:200])
        return False
    finally:
        _STATE["running"] = False
        _LOCK.release()


def refresh_async() -> bool:
    """Kick a refresh in the background if one isn't already running. Returns True if started."""
    if _STATE["running"] or not available():
        return False
    threading.Thread(target=run_once, daemon=True, name="crm-sync").start()
    return True


def set_override(email: str, action: str) -> bool:
    """Write a sticky correction via `cli.py act <promote|lead|remove> <email>` (from the review
    queue). Quick (no sync); the caller then triggers a refresh to surface the change."""
    crm_dir, py = _paths()
    if action not in ("promote", "lead", "remove") or not Path(crm_dir, "cli.py").exists() or not email:
        return False
    try:
        r = subprocess.run([py, "cli.py", "act", action, email], cwd=crm_dir,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def merge_contacts(primary: str, duplicate: str) -> bool:
    """Record a sticky duplicate merge via `cli.py merge <primary> <duplicate>` so future syncs
    fold the duplicate's touchpoints into the primary contact. Caller then refreshes."""
    crm_dir, py = _paths()
    if not primary or not duplicate or primary == duplicate or not Path(crm_dir, "cli.py").exists():
        return False
    try:
        r = subprocess.run([py, "cli.py", "merge", primary, duplicate], cwd=crm_dir,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False
