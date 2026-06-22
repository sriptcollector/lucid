"""Desktop entry point for the standalone Lucid app (PyInstaller build).

This is what `Lucid.exe` runs. The friend double-clicks it — no Python, no
terminal, no commands. It:
  * stores all data under a per-user folder (%LOCALAPPDATA%\\Lucid on Windows),
  * starts the Lucid server bound to localhost,
  * opens the browser to the app once it's healthy.

When NOT frozen this still runs (python lucid_app.py) but uses the normal
./data dir, so do not run it from a real install you care about.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)


def _user_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Lucid"


# --- Set up per-user data dir + log BEFORE importing the server/config -------
if FROZEN:
    _home = _user_dir()
    (_home / "data" / "inbox").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATA_DIR", str(_home / "data"))
    os.environ.setdefault("WATCH_FOLDER", str(_home / "data" / "inbox"))
    # Run from the user dir so a stray .env in the launch folder is never read.
    try:
        os.chdir(_home)
    except OSError:
        pass
    # In a --windowed build there is no console; sys.stdout/err are None and any
    # print() would crash. Redirect everything to a log file.
    if sys.stdout is None or sys.stderr is None:
        try:
            _log = open(_home / "lucid.log", "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = _log
        except OSError:
            class _Null:
                def write(self, *_a):
                    return 0

                def flush(self):
                    pass

            sys.stdout = sys.stderr = _Null()

PORT = int(os.environ.get("PORT", "8000") or "8000")
os.environ["PORT"] = str(PORT)

# Imported AFTER the env (DATA_DIR) is set, and at module top-level so the
# PyInstaller analyzer actually bundles the whole `server` package. Passing the
# app object to uvicorn (instead of the "server.main:app" string) is what makes
# the frozen build resolve it.
from server.main import app as _app  # noqa: E402


def _open_when_ready() -> None:
    url = f"http://127.0.0.1:{PORT}/"
    health = f"http://127.0.0.1:{PORT}/api/health"
    for _ in range(180):
        try:
            with urllib.request.urlopen(health, timeout=2) as r:
                if 200 <= r.status < 300:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(1)


def main() -> None:
    import uvicorn

    print(f"[Lucid] starting on http://127.0.0.1:{PORT} …", flush=True)
    threading.Thread(target=_open_when_ready, daemon=True).start()
    # Bind loopback; remote access is via the in-app Cloudflare tunnel only.
    uvicorn.run(_app, host="127.0.0.1", port=PORT, reload=False, log_level="info")


if __name__ == "__main__":
    main()
