"""Free public hosting via a Cloudflare quick tunnel (no account, no domain).

Lucid runs the tunnel *in-process* as a daemon thread so a friend only has to
start one program (``python start.py``). On startup we launch::

    cloudflared tunnel --url http://localhost:PORT --no-autoupdate

which prints an ephemeral ``https://<random>.trycloudflare.com`` address. We
capture that URL, persist it to ``settings.public_url_file`` so the rest of the
app can build absolute links, and keep the process alive — relaunching if
cloudflared exits. Quick-tunnel URLs change on every (re)launch, which is fine:
readers always go through ``settings.current_public_url()``.

Advanced users can still run this standalone with ``python -m server.tunnel``.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time

from . import cloudflared
from .config import settings

# Matches the quick-tunnel hostname cloudflared prints once the tunnel is up.
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# How long to wait before relaunching after cloudflared exits unexpectedly.
_RELAUNCH_DELAY = 8.0


class _TunnelManager:
    """Owns the background thread and the cloudflared subprocess.

    All public state transitions are guarded by a lock so ``start``/``stop``/
    ``restart`` are safe to call from request handlers or the main thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Start the tunnel thread if enabled and not already running.

        Idempotent: a second call while running is a no-op.
        """
        if not settings.tunnel_enabled:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="lucid-tunnel", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Signal the loop to exit and terminate the cloudflared subprocess."""
        with self._lock:
            self._stop.set()
            proc = self._proc
            thread = self._thread
        self._terminate(proc)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)
        with self._lock:
            self._thread = None
        self._clear_url()

    def restart(self) -> None:
        """Stop (if running) then start fresh — picks up a new port/config."""
        self.stop()
        self.start()

    # ---- introspection ------------------------------------------------

    def current_url(self) -> str:
        """The live public URL, or '' if the tunnel isn't up yet."""
        try:
            return settings.public_url_file.read_text().strip()
        except Exception:  # noqa: BLE001 - missing file is the normal "not up" case
            return ""

    def status(self) -> dict:
        """Snapshot for the UI / health checks."""
        running = self._thread is not None and self._thread.is_alive()
        return {
            "running": bool(running),
            "url": self.current_url(),
            "enabled": bool(settings.tunnel_enabled),
        }

    # ---- internals ----------------------------------------------------

    def _run(self) -> None:
        """Thread body: keep a cloudflared quick tunnel alive until stopped.

        Wrapped so that no exception can ever escape and kill the thread
        silently — failures are logged and (unless stopping) retried.
        """
        try:
            exe = cloudflared.ensure_binary()
        except Exception as exc:  # noqa: BLE001
            print(f"[tunnel] cannot obtain cloudflared: {exc}")
            return

        while not self._stop.is_set():
            try:
                self._run_once(exe)
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                print(f"[tunnel] error: {exc}")

            self._clear_url()
            if self._stop.is_set():
                break
            print(f"[tunnel] cloudflared exited; relaunching in "
                  f"{int(_RELAUNCH_DELAY)}s")
            # Interruptible sleep: stop() can wake us immediately.
            if self._stop.wait(timeout=_RELAUNCH_DELAY):
                break

    def _run_once(self, exe: str) -> None:
        """Launch cloudflared once and pump its output until it exits."""
        # No shell=True, list-args only -> cross-platform and injection-safe.
        proc = subprocess.Popen(
            [
                exe,
                "tunnel",
                "--url",
                f"http://localhost:{settings.port}",
                "--no-autoupdate",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._proc = proc

        announced = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                if not announced:
                    match = _URL_RE.search(line)
                    if match:
                        announced = True
                        self._on_url(match.group(0))
        finally:
            # Drain/await the process so we don't leak zombies.
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self._terminate(proc)
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def _on_url(self, url: str) -> None:
        """Persist the fresh public URL and announce it locally."""
        try:
            settings.public_url_file.write_text(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[tunnel] could not persist URL: {exc}")
        print(f"[tunnel] live at {url}")
        self._maybe_notify(url)

    def _maybe_notify(self, url: str) -> None:
        """Best-effort, generic notification if Telegram is configured.

        Entirely optional and self-contained: any import or send failure is
        swallowed so the tunnel keeps working without notifications.
        """
        if not settings.telegram_enabled:
            return
        try:
            from .notify import telegram  # type: ignore

            chat = telegram.default_chat()
            if chat:
                telegram.send_message(chat, f"Lucid is live: {url}")
        except Exception:  # noqa: BLE001 - notifications must never break the tunnel
            pass

    def _clear_url(self) -> None:
        try:
            settings.public_url_file.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _terminate(proc: subprocess.Popen[str] | None) -> None:
        """Politely stop cloudflared, escalating to kill if it lingers."""
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


# Module-level singleton + thin functional facade (matches the rest of the app).
_manager = _TunnelManager()


def start() -> None:
    """Start the in-process tunnel (idempotent; respects ``tunnel_enabled``)."""
    _manager.start()


def stop() -> None:
    """Stop the tunnel and clear the persisted public URL."""
    _manager.stop()


def restart() -> None:
    """Restart the tunnel (e.g. after the port changes)."""
    _manager.restart()


def current_url() -> str:
    """Live public URL from ``settings.public_url_file`` (may be '')."""
    return _manager.current_url()


def status() -> dict:
    """``{'running': bool, 'url': str, 'enabled': bool}``."""
    return _manager.status()


# Run standalone for advanced users: `python -m server.tunnel`.
if __name__ == "__main__":
    start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop()
