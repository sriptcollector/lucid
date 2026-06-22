#!/usr/bin/env python3
"""Lucid one-command launcher — the product's front door.

A non-technical friend, after unzipping the release, runs ONE of::

    python start.py

(or double-clicks it). This script bootstraps a fully isolated environment
and opens the app in their browser. It must work identically on Windows,
macOS, and Linux.

IMPORTANT: this file runs BEFORE any third-party dependency is installed, so
it imports the Python standard library ONLY. Do not add any non-stdlib import
here — it would crash on the very first launch.

What it does, in order:
  1. Verify the host Python is new enough (>= 3.10).
  2. Create a virtualenv at <root>/.venv if it does not exist yet.
  3. Install requirements.txt into that venv (skipped on later runs via a
     sentinel marker keyed to the requirements file contents).
  4. Launch the server (``<venv python> -m server.main``), inheriting stdio.
  5. Poll the local health endpoint until the server is ready, then open the
     browser at the app URL.
  6. Stay in the foreground and shut the server down cleanly on Ctrl+C.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants                                                                     #
# --------------------------------------------------------------------------- #

APP_NAME = "Lucid"
MIN_PYTHON = (3, 10)

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"

# Server bind defaults — must mirror server/config.py defaults. The friend can
# override the port with the PORT environment variable.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# How long to wait for the server to come up before giving a friendly nudge.
HEALTH_TIMEOUT_SECONDS = 90
HEALTH_POLL_INTERVAL = 1.0


# --------------------------------------------------------------------------- #
# Pretty, branded, emoji-free output                                            #
# --------------------------------------------------------------------------- #

def say(message: str) -> None:
    """Print a branded status line and flush immediately (so progress shows)."""
    print(f"[{APP_NAME}] {message}", flush=True)


def fail(message: str) -> "None":
    """Print an actionable error and exit with a non-zero status."""
    print(f"\n[{APP_NAME}] ERROR: {message}\n", file=sys.stderr, flush=True)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Environment checks                                                            #
# --------------------------------------------------------------------------- #

def check_python_version() -> None:
    """Abort early with a clear message if the host Python is too old."""
    if sys.version_info < MIN_PYTHON:
        have = ".".join(str(p) for p in sys.version_info[:3])
        need = ".".join(str(p) for p in MIN_PYTHON)
        fail(
            f"{APP_NAME} needs Python {need} or newer, but this is Python {have}.\n"
            f"  Please install Python 3.11+ from https://www.python.org/downloads/\n"
            f"  then run this again:  python start.py"
        )


def venv_python(venv_dir: Path) -> Path:
    """Return the path to the interpreter inside a venv, cross-platform."""
    if os.name == "nt":  # Windows
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


# --------------------------------------------------------------------------- #
# Virtualenv + dependency bootstrap                                             #
# --------------------------------------------------------------------------- #

def ensure_venv() -> Path:
    """Create the virtualenv if needed and return its python interpreter path."""
    py = venv_python(VENV_DIR)
    if py.exists():
        return py

    say("First-time setup — creating a private environment...")
    say(f"(at {VENV_DIR.name}/)")
    try:
        # with_pip=True ensures pip is available inside the venv. clear=False so
        # we never blow away an in-progress environment.
        builder = venv.EnvBuilder(with_pip=True, clear=False, upgrade=False)
        builder.create(str(VENV_DIR))
    except Exception as exc:  # noqa: BLE001 — surface any venv failure clearly
        fail(
            f"Could not create the virtual environment: {exc}\n"
            f"  Make sure your Python install includes the 'venv' module.\n"
            f"  On Debian/Ubuntu you may need:  sudo apt install python3-venv"
        )

    py = venv_python(VENV_DIR)
    if not py.exists():
        fail(
            "The virtual environment was created but its Python interpreter is "
            f"missing (expected at {py}).\n"
            "  Try deleting the .venv folder and running start.py again."
        )
    return py


def _requirements_hash() -> str:
    """Short, stable hash of requirements.txt contents (for the deps marker)."""
    data = REQUIREMENTS.read_bytes() if REQUIREMENTS.exists() else b""
    return hashlib.sha256(data).hexdigest()[:16]


def _deps_marker() -> Path:
    """Sentinel file proving deps for the current requirements.txt are installed."""
    return VENV_DIR / f".lucid-deps-{_requirements_hash()}"


def _run_streaming(cmd: list[str], *, what: str) -> None:
    """Run a subprocess, streaming its output, and abort on non-zero exit."""
    try:
        result = subprocess.run(cmd, cwd=str(ROOT))
    except FileNotFoundError as exc:
        fail(f"Could not run {what} (command not found): {exc}")
    except Exception as exc:  # noqa: BLE001
        fail(f"Unexpected error while running {what}: {exc}")
    if result.returncode != 0:
        fail(
            f"{what} failed (exit code {result.returncode}).\n"
            f"  Scroll up for the detailed error from pip.\n"
            f"  Common fix: install the latest Python 3.11+ from python.org,\n"
            f"  delete the .venv folder, and run:  python start.py"
        )


def ensure_dependencies(py: Path) -> None:
    """Install requirements into the venv unless a matching marker exists."""
    marker = _deps_marker()
    if marker.exists():
        # Already installed for this exact requirements.txt — fast path.
        return

    if not REQUIREMENTS.exists():
        fail(
            f"Missing {REQUIREMENTS.name} next to start.py.\n"
            "  The download may be incomplete — re-unzip the release and retry."
        )

    say("Installing dependencies — first run downloads a few hundred MB, so")
    say("give it a few minutes (one-time). Later launches are instant.")
    say("Upgrading pip...")
    _run_streaming(
        [str(py), "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
        what="pip upgrade",
    )

    say("Installing dependencies (this is the slow part on first run)...")
    _run_streaming(
        [str(py), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        what="dependency install",
    )

    # Record success so subsequent launches skip the install entirely. Clear any
    # stale markers from older requirements first to avoid clutter.
    for old in VENV_DIR.glob(".lucid-deps-*"):
        try:
            old.unlink()
        except OSError:
            pass
    try:
        marker.write_text("ok\n", encoding="utf-8")
    except OSError:
        # Non-fatal: we'd just reinstall next time. Don't block the launch.
        pass
    say("Dependencies ready.")


# --------------------------------------------------------------------------- #
# Server launch + readiness                                                     #
# --------------------------------------------------------------------------- #

def resolve_port() -> int:
    """Pick the port from PORT (OS env first, then a .env file), else default.

    The server reads its port via pydantic-settings, which also loads .env — so
    the launcher must consult .env too, or it would poll/open the wrong port for
    an advanced user who set PORT only in .env.
    """
    raw = os.environ.get("PORT", "").strip()
    if not raw:
        env_file = ROOT / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("PORT=") and not line.startswith("#"):
                        raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            except OSError:
                pass
    if raw:
        try:
            return int(raw)
        except ValueError:
            say(f"Ignoring invalid PORT={raw!r}; using {DEFAULT_PORT}.")
    return DEFAULT_PORT


def start_server(py: Path) -> subprocess.Popen:
    """Launch the FastAPI server as a child process, inheriting our stdio."""
    say("Lucid is starting...")
    try:
        return subprocess.Popen(
            [str(py), "-m", "server.main"],
            cwd=str(ROOT),
        )
    except Exception as exc:  # noqa: BLE001
        fail(f"Could not start the {APP_NAME} server: {exc}")


def wait_for_health(proc: subprocess.Popen, port: int) -> bool:
    """Poll the health endpoint until the server responds or we time out.

    Returns True if the server became healthy, False on timeout. Returns early
    (and reports) if the server process dies before becoming ready.
    """
    url = f"http://{DEFAULT_HOST}:{port}/api/health"
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        # If the server crashed during startup, stop waiting immediately.
        if proc.poll() is not None:
            fail(
                f"The {APP_NAME} server stopped during startup "
                f"(exit code {proc.returncode}).\n"
                "  Scroll up for the server's error output."
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 300:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass  # Server not listening yet — keep polling.
        time.sleep(HEALTH_POLL_INTERVAL)

    return False


def open_browser(port: int) -> None:
    """Open the app in the default browser (best-effort)."""
    url = f"http://{DEFAULT_HOST}:{port}/"
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 — headless boxes have no browser; that's fine
        pass


# --------------------------------------------------------------------------- #
# Main flow                                                                      #
# --------------------------------------------------------------------------- #

def main() -> int:
    say(f"Welcome to {APP_NAME}.")
    say(f"Running on {platform.system()} with Python {platform.python_version()}.")

    check_python_version()

    py = ensure_venv()
    ensure_dependencies(py)

    port = resolve_port()
    app_url = f"http://{DEFAULT_HOST}:{port}/"

    proc = start_server(py)
    try:
        if wait_for_health(proc, port):
            say(f"Open {APP_NAME} at {app_url}")
            open_browser(port)
        else:
            # Timed out, but the process may still be coming up — don't kill it.
            say(
                f"Still starting up. If your browser doesn't open shortly, "
                f"go to {app_url} manually."
            )

        say("Lucid is running. Press Ctrl+C in this window to stop it.")
        # Block until the server exits or the user interrupts us.
        proc.wait()
    except KeyboardInterrupt:
        say("Shutting down...")
    finally:
        _terminate(proc)

    say("Lucid has stopped. Goodbye.")
    return 0


def _terminate(proc: subprocess.Popen) -> None:
    """Stop the server child process cleanly, escalating to kill if needed."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # User pressed Ctrl+C before the server was even up.
        sys.exit(130)
