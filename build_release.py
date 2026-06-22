"""Build a clean, shippable LUCID release zip.

This is an *allowlist* packager: it copies only known-good product files into a
fresh zip, never touches the user's ``data/`` directory (except for the empty
``data/.gitkeep`` placeholder), and refuses to ship if it spots anything that
looks like a real secret leaking into a tracked file.

Stdlib only. Cross-platform (uses pathlib + zipfile, no shelling out).

Usage::

    python build_release.py

Produces ``dist/lucid-<VERSION>.zip`` next to this script.
"""

from __future__ import annotations

import os
import re
import sys
import zipfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

VERSION = "1.0.0"

# Repo root is wherever this script lives.
ROOT = Path(__file__).resolve().parent

# Top-level single files we ship verbatim (skipped silently if absent — some
# are written by other parts of the build in parallel and may not exist yet).
TOP_LEVEL_FILES = [
    "start.py",
    "requirements.txt",
    "README.md",
    "SETUP.md",
    "LICENSE",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
]

# Whole directories shipped by glob. Each entry is (relative_dir, glob_pattern).
# The walk is an allowlist: only files matching one of these patterns are even
# considered, which is far safer than trying to enumerate everything to exclude.
TREE_GLOBS = [
    ("server", "**/*.py"),   # all Python under server/ (recursive)
    ("web", "**/*"),          # everything under web/ (html/css/js/assets)
]

# File extensions we never package even if they sneak into an allowlisted tree.
BINARY_OR_JUNK_SUFFIXES = {".pyc", ".pyo", ".db", ".log", ".seen"}

# Directory names that must never appear anywhere in a packaged path.
FORBIDDEN_PATH_PARTS = {
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "bin",
    "dist",
    ".git",
}

# Extensions we treat as text and therefore scan for secrets.
TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".html", ".css", ".json", ".txt", ".md",
    ".yml", ".yaml", ".cfg", ".ini", ".toml", ".env", ".example",
    "",  # extension-less files like LICENSE / Dockerfile
}

# The one file allowed to contain secret-shaped *placeholders*.
SECRET_SCAN_EXEMPT = {".env.example"}

# --------------------------------------------------------------------------- #
# Secret scanning
# --------------------------------------------------------------------------- #

# Patterns that strongly indicate a real, leaked secret (not a placeholder).
# Each is (label, compiled_regex). We deliberately avoid matching obvious
# placeholders like "your-key-here", "xxxx", "<...>", "changeme".
_PLACEHOLDER_HINT = re.compile(
    r"your[-_ ]?|<[^>]*>|xxx+|changeme|example|placeholder|\.\.\.|sk-ant-xxxx",
    re.IGNORECASE,
)

SECRET_PATTERNS = [
    # Anthropic keys: sk-ant-... followed by a real-looking blob.
    ("anthropic-api-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    # OpenAI-style keys.
    ("openai-api-key", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9]{20,}\b")),
    # Authorization: Bearer <token>.
    ("bearer-token", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]{16,}")),
    # API_TOKENS=<something with a value> (env var assigned a real value).
    ("api-tokens-assignment", re.compile(r"API_TOKENS\s*=\s*\S+")),
    # Cloudflare quick-tunnel hostnames.
    ("trycloudflare-url", re.compile(r"https?://[A-Za-z0-9\-]+\.trycloudflare\.com")),
    # Telegram bot tokens: 8+ digit bot id, colon, token body.
    ("telegram-bot-token", re.compile(r"\b\d{8,}:[A-Za-z0-9_\-]{20,}\b")),
    # GitHub gist ids referenced in code (32 hex chars).
    ("gist-id", re.compile(r"\bgist(?:\.github)?[^A-Za-z0-9]{0,12}[0-9a-f]{20,}\b", re.IGNORECASE)),
]


def _scan_text_for_secrets(text: str, rel_path: str) -> list[str]:
    """Return a list of human-readable findings for one file's text."""
    findings: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            snippet = match.group(0)
            # Pull a little surrounding context to judge placeholder-ness.
            start = max(0, match.start() - 16)
            end = min(len(text), match.end() + 16)
            context = text[start:end]
            if _PLACEHOLDER_HINT.search(context):
                # Looks like a documented placeholder; don't block on it.
                continue
            # Mask the middle so we never re-print the full secret.
            masked = snippet if len(snippet) <= 12 else f"{snippet[:6]}…{snippet[-4:]}"
            findings.append(f"  {rel_path}: [{label}] {masked}")
    return findings


# --------------------------------------------------------------------------- #
# File collection (allowlist walk)
# --------------------------------------------------------------------------- #

def _is_forbidden(rel: Path) -> bool:
    """True if any path component is a directory we never ship."""
    return any(part in FORBIDDEN_PATH_PARTS for part in rel.parts)


def _touches_data(rel_posix: str) -> bool:
    """True if the path lives under data/ (the user's runtime state)."""
    return rel_posix == "data" or rel_posix.startswith("data/")


def _collect_files() -> list[Path]:
    """Gather absolute paths to every file that belongs in the release."""
    collected: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        path = path.resolve()
        if path not in seen and path.is_file():
            seen.add(path)
            collected.append(path)

    # 1) Top-level single files (skip silently if missing).
    for name in TOP_LEVEL_FILES:
        candidate = ROOT / name
        if candidate.is_file():
            add(candidate)

    # 2) Allowlisted directory trees.
    for subdir, pattern in TREE_GLOBS:
        base = ROOT / subdir
        if not base.is_dir():
            continue
        for path in base.glob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if _is_forbidden(rel):
                continue
            if path.suffix.lower() in BINARY_OR_JUNK_SUFFIXES:
                continue
            add(path)

    # 3) The single empty placeholder under data/. Nothing else from data/.
    gitkeep = ROOT / "data" / ".gitkeep"
    if gitkeep.is_file():
        add(gitkeep)

    return collected


def _final_safety_filter(files: list[Path]) -> list[Path]:
    """Last line of defence: drop anything secret-shaped by *path*."""
    safe: list[Path] = []
    for path in files:
        rel_posix = path.relative_to(ROOT).as_posix()
        name = path.name
        # Never ship a literal .env (the example is fine).
        if name == ".env":
            continue
        # Never ship anything under data/ except the placeholder.
        if _touches_data(rel_posix) and rel_posix != "data/.gitkeep":
            continue
        safe.append(path)
    return safe


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def build(dest_dir: str | os.PathLike[str] | None = None,
          version: str | None = None) -> Path:
    """Build the release zip and return its path.

    Raises ``SystemExit`` (via a printed abort) if a real secret is detected
    in any packaged file other than ``.env.example``.
    """
    version = version or VERSION
    dest = Path(dest_dir) if dest_dir is not None else (ROOT / "dist")
    dest.mkdir(parents=True, exist_ok=True)

    files = _final_safety_filter(_collect_files())
    if not files:
        raise SystemExit("Nothing to package — no product files found.")

    # --- Secret scan across every text file we intend to ship. ------------- #
    all_findings: list[str] = []
    for path in files:
        rel_posix = path.relative_to(ROOT).as_posix()
        if path.name in SECRET_SCAN_EXEMPT:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            # Not decodable as text — treat as binary, skip the text scan.
            continue
        all_findings.extend(_scan_text_for_secrets(text, rel_posix))

    if all_findings:
        print("ABORT: possible secrets found in files to be packaged:")
        print("\n".join(all_findings))
        print("\nNo zip was written. Scrub these values and try again.")
        raise SystemExit(1)

    # --- Write the zip. ---------------------------------------------------- #
    zip_path = dest / f"lucid-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    # All archive paths are nested under a top folder so it extracts cleanly.
    top = f"lucid-{version}"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            arcname = f"{top}/{path.relative_to(ROOT).as_posix()}"
            zf.write(path, arcname)

    size = zip_path.stat().st_size
    print(f"Built {zip_path} ({_human_size(size)}, {len(files)} files)")
    return zip_path


def _human_size(num: int) -> str:
    """Format a byte count as a short human-readable string."""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    out = build()
    sys.exit(0)
