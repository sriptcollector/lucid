"""Cross-platform resolver/installer for the `cloudflared` binary.

Lucid auto-deploys a free Cloudflare *quick tunnel* so a non-technical friend
gets a public https://<random>.trycloudflare.com URL with no Cloudflare account
and no domain. That only needs the `cloudflared` executable, which this module
locates or downloads on demand.

Resolution order (see :func:`ensure_binary`):
    1. an explicit ``settings.cloudflared_path``
    2. a ``cloudflared`` already on ``PATH``
    3. a copy we previously downloaded into ``settings.bin_path``
    4. otherwise download the correct release for this OS/CPU from GitHub

Everything is keyed off :data:`sys.platform` / :func:`platform.machine` so the
same code works on Windows, macOS (Intel + Apple Silicon) and Linux.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import stat
import sys
import tarfile
from pathlib import Path

import httpx

from .config import settings

# GitHub serves the newest stable build at a stable "latest/download" path, so
# we never have to query the releases API or pin a version.
_RELEASE_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"

# How long to allow for the (cold) download of a ~30-40 MB binary.
_DOWNLOAD_TIMEOUT = 120.0


def _arch() -> str:
    """Normalise :func:`platform.machine` to cloudflared's arch suffixes.

    Returns one of: ``amd64``, ``arm64``, ``386``, ``arm``. Falls back to
    ``amd64`` for anything unrecognised (by far the most common desktop CPU).
    """
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        return "amd64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("i386", "i686", "x86", "386"):
        return "386"
    if machine in ("armv7l", "armv6l", "arm"):
        return "arm"
    # Unknown CPU: 64-bit Intel/AMD is the safest default.
    return "amd64"


def _is_windows() -> bool:
    return sys.platform.startswith("win") or platform.system().lower() == "windows"


def _is_macos() -> bool:
    return sys.platform == "darwin" or platform.system().lower() == "darwin"


def _local_name() -> str:
    """Filename we save the binary under inside ``settings.bin_path``."""
    return "cloudflared.exe" if _is_windows() else "cloudflared"


def _asset_name() -> str:
    """The exact GitHub release asset to fetch for this OS + CPU.

    macOS assets are ``.tgz`` archives; Windows/Linux are bare binaries.
    """
    arch = _arch()
    if _is_windows():
        # Cloudflare ships only 386 + amd64 for Windows (no arm64 asset).
        # Windows-on-ARM runs the amd64 binary transparently via emulation.
        win_arch = "386" if arch == "386" else "amd64"
        return f"cloudflared-windows-{win_arch}.exe"
    if _is_macos():
        # cloudflared only ships amd64/arm64 mac builds; arm64 covers Apple Silicon.
        mac_arch = "arm64" if arch == "arm64" else "amd64"
        return f"cloudflared-darwin-{mac_arch}.tgz"
    # Linux (and any other POSIX): bare binary, no extension.
    return f"cloudflared-linux-{arch}"


def _make_executable(path: Path) -> None:
    """chmod +x on POSIX; a no-op on Windows."""
    if _is_windows():
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        # Non-fatal: the file may already be executable, or on a filesystem
        # that ignores the bit. Let the launch attempt surface any real issue.
        pass


def installed_path() -> str | None:
    """Best-effort path to a usable cloudflared *without* downloading anything.

    Returns ``None`` if nothing is found locally — callers that need a binary
    no matter what should use :func:`ensure_binary` instead.
    """
    configured = settings.cloudflared_path
    if configured and Path(configured).exists():
        return str(Path(configured))

    on_path = shutil.which("cloudflared")
    if on_path:
        return on_path

    downloaded = settings.bin_path / _local_name()
    if downloaded.exists():
        return str(downloaded)

    return None


def ensure_binary() -> str:
    """Return a path to a runnable ``cloudflared``, downloading it if needed.

    Tries every cheap option first (config override, ``PATH``, a prior
    download) and only fetches from GitHub as a last resort.

    Raises:
        RuntimeError: with a friendly, actionable message if a binary can be
            neither found nor downloaded.
    """
    # (1)-(3): anything already on disk wins — no network needed.
    existing = installed_path()
    if existing:
        return existing

    # (4): download the right release for this machine.
    target = settings.bin_path / _local_name()
    asset = _asset_name()
    url = _RELEASE_BASE + asset

    print(f"[cloudflared] no local binary found; downloading {asset}")
    print(f"[cloudflared] from {url}")
    try:
        if asset.endswith(".tgz"):
            _download_macos_tgz(url, target)
        else:
            _download_binary(url, target)
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - turn any failure into a clear message
        raise RuntimeError(
            "Could not download cloudflared automatically "
            f"({type(exc).__name__}: {exc}).\n"
            "Check your internet connection, or install cloudflared manually "
            "from https://developers.cloudflare.com/cloudflare-one/connections/"
            "connect-apps/install-and-setup/installation/ and set "
            "CLOUDFLARED_PATH to its location."
        ) from exc

    _make_executable(target)
    if not target.exists():
        raise RuntimeError(
            "cloudflared download finished but the binary is missing at "
            f"{target}. Please install cloudflared manually and set "
            "CLOUDFLARED_PATH."
        )
    print(f"[cloudflared] ready at {target}")
    return str(target)


def _download_binary(url: str, target: Path) -> None:
    """Stream a bare binary asset straight to ``target`` (atomic-ish via .part)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    total = 0
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
    ) as resp:
        resp.raise_for_status()
        expected = int(resp.headers.get("content-length", 0))
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=65536):
                fh.write(chunk)
                total += len(chunk)
                _print_progress(total, expected)
    print()  # newline after the progress line
    os.replace(tmp, target)


def _download_macos_tgz(url: str, target: Path) -> None:
    """Download a macOS ``.tgz`` into memory and extract the ``cloudflared`` member."""
    target.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    total = 0
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
    ) as resp:
        resp.raise_for_status()
        expected = int(resp.headers.get("content-length", 0))
        for chunk in resp.iter_bytes(chunk_size=65536):
            buf.write(chunk)
            total += len(chunk)
            _print_progress(total, expected)
    print()  # newline after the progress line

    buf.seek(0)
    tmp = target.with_suffix(target.suffix + ".part")
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        member = _find_cloudflared_member(tar)
        if member is None:
            raise RuntimeError(
                "downloaded macOS archive did not contain a 'cloudflared' binary"
            )
        extracted = tar.extractfile(member)
        if extracted is None:
            raise RuntimeError("could not read 'cloudflared' from the macOS archive")
        with open(tmp, "wb") as fh:
            shutil.copyfileobj(extracted, fh)
    os.replace(tmp, target)


def _find_cloudflared_member(tar: tarfile.TarFile) -> tarfile.TarInfo | None:
    """Locate the ``cloudflared`` file inside a release tarball.

    The archive layout has historically been a single top-level ``cloudflared``
    file, but we match on basename to be resilient to any nesting.
    """
    for member in tar.getmembers():
        if not member.isfile():
            continue
        if Path(member.name).name == "cloudflared":
            return member
    return None


def _print_progress(done: int, total: int) -> None:
    """Print a single-line [cloudflared] progress indicator."""
    mb = done / (1024 * 1024)
    if total > 0:
        pct = done * 100 // total
        print(
            f"\r[cloudflared] downloading… {pct:3d}%  ({mb:.1f} MB)",
            end="",
            flush=True,
        )
    else:
        print(
            f"\r[cloudflared] downloading… {mb:.1f} MB",
            end="",
            flush=True,
        )
