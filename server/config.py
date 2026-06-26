"""Central configuration for Lucid.

Config is layered, lowest priority first:

    1. code defaults (the field defaults below)
    2. environment / a local ``.env``        (advanced / dev overrides)
    3. ``<data_dir>/config.json``            (written by the setup wizard + the
                                              in-app Settings page — the live,
                                              user-managed source of truth)

A friend never edits a file: the first-run web wizard writes ``config.json`` and
the app reloads it. ``.env`` stays supported for power users. Secrets that don't
map cleanly to a flat field (the Plaud token, the app-password hash) live in the
same ``config.json`` and are reached through the helper methods at the bottom.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG_LOCK = threading.RLock()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Anthropic (analysis layer) ---
    anthropic_api_key: str = ""
    analysis_model: str = "claude-opus-4-8"

    # --- Transcription ---
    transcribe_backend: str = "faster_whisper"  # faster_whisper | openai | deepgram
    whisper_model: str = "small"            # tiny|base|small|medium|large-v3
    whisper_device: str = "auto"            # auto | cpu | cuda
    whisper_compute_type: str = "auto"      # auto | int8 | float16 | float32
    whisper_beam_size: int = 5
    whisper_batch_size: int = 16
    whisper_vad: bool = True
    whisper_language: str = ""              # "" = auto-detect per recording
    whisper_prompt: str = ""
    whisper_tag_nonspeech: bool = True
    # speaker identification (local, no tokens)
    voiceid_enabled: bool = True
    voiceid_threshold: float = 0.72
    openai_api_key: str = ""
    deepgram_api_key: str = ""

    # --- Translation ---
    translate_to: str = "English"
    translate_backend: str = "claude"       # whisper | claude

    # --- Ingest / storage ---
    watch_folder: str = "./data/inbox"
    data_dir: str = "./data"

    # --- Backups (consistent SQLite snapshots of the notes DB) ---
    backup_enabled: bool = True
    backup_interval_hours: int = 24
    backup_keep: int = 14                   # snapshots retained (older pruned)
    backup_dir: str = ""                    # default: <data>/backups

    # --- Plaud cloud auto-poll (pure-Python client; device Private Cloud Sync) ---
    plaud_cloud_enabled: bool = False
    plaud_email: str = ""
    plaud_region: str = "us"                # us | eu
    plaud_poll_interval: int = 300          # seconds between cloud checks
    plaud_process_backlog: bool = False     # on first run, process ALL existing

    # --- Client manager (CRM): Notion (optional, off until connected) ---
    crm_enabled: bool = False
    crm_backend: str = "notion"             # notion (only backend for now)
    crm_database_id: str = ""               # the shared Notion "clients" database
    crm_autopush: bool = True               # append note summaries to client pages
    owner_name: str = ""                    # the recorder ('I'/narrator) for solo notes

    # --- Delivery: Telegram (fully optional, off by default) ---
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    public_base_url: str = ""               # static fallback if no live tunnel

    # --- Server ---
    # Bind to loopback by default: remote access should come ONLY through the
    # Cloudflare tunnel (which proxies to localhost). Set HOST=0.0.0.0 to opt
    # into direct LAN access.
    host: str = "127.0.0.1"
    port: int = 8000
    api_tokens: str = ""                    # comma-separated bearer tokens

    # --- Public hosting via Cloudflare quick tunnel ---
    tunnel_enabled: bool = True             # ON by default for the product
    cloudflared_path: str = ""

    # --- Stable public link (optional; owner-only) ---
    # When set to "owner/repo", Lucid keeps a redirect page on that repo's
    # gh-pages branch pointed at the live tunnel URL, giving friends ONE
    # permanent link: https://owner.github.io/repo/ . Requires a logged-in `gh`
    # CLI on the host. Blank = disabled (friends just use the tunnel URL).
    stable_link_repo: str = ""
    stable_link_branch: str = "gh-pages"
    gh_path: str = ""                       # explicit path to gh CLI (else PATH)

    # --- Onboarding state ---
    setup_complete: bool = False

    # Non-field runtime store (Plaud token, password hash, etc.) loaded from
    # config.json. Kept off the pydantic schema so arbitrary nested values are
    # allowed. Populated by _apply_runtime() right after construction.
    _runtime: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Runtime config file (config.json) — the wizard / Settings page writes here
    # ------------------------------------------------------------------ #
    @property
    def config_path(self) -> Path:
        return self.data_path / "config.json"

    def _read_config_file(self) -> dict[str, Any]:
        try:
            raw = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _apply_runtime(self) -> "Settings":
        """Overlay config.json on top of env/defaults (highest priority)."""
        with _CONFIG_LOCK:
            data = self._read_config_file()
            object.__setattr__(self, "_runtime", data)
            fields = type(self).model_fields
            for key, value in data.items():
                if key in fields:
                    try:
                        setattr(self, key, value)
                    except Exception:  # noqa: BLE001 - ignore a bad stored value
                        pass
        return self

    def reload(self) -> None:
        """Re-read config.json and re-apply it onto this singleton."""
        self._apply_runtime()

    def save_config(self, updates: dict[str, Any]) -> None:
        """Atomically merge ``updates`` into config.json, then reload.

        ``None`` values delete a key. Writes via a temp file + os.replace so a
        crash mid-write can never corrupt the config.
        """
        with _CONFIG_LOCK:
            data = self._read_config_file()
            for key, value in updates.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self.config_path.parent), prefix=".config.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
                os.replace(tmp, self.config_path)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        self._apply_runtime()

    # ------------------------------------------------------------------ #
    # Derived paths
    # ------------------------------------------------------------------ #
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def inbox_path(self) -> Path:
        p = Path(self.watch_folder)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def audio_path(self) -> Path:
        p = self.data_path / "audio"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def bin_path(self) -> Path:
        p = self.data_path / "bin"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_path / "lucid.db"

    @property
    def public_url_file(self) -> Path:
        return self.data_path / "public_url.txt"

    @property
    def crm_contacts_path(self) -> Path:
        return self.data_path / "crm_contacts.json"

    @property
    def stable_public_url(self) -> str:
        """Permanent github.io link derived from ``stable_link_repo``, or ''."""
        repo = self.stable_link_repo.strip()
        if "/" not in repo:
            return ""
        owner, _, name = repo.partition("/")
        owner, name = owner.strip(), name.strip()
        if not owner or not name:
            return ""
        return f"https://{owner.lower()}.github.io/{name}/"

    # ------------------------------------------------------------------ #
    # Tokens / auth
    # ------------------------------------------------------------------ #
    @property
    def tokens(self) -> set[str]:
        return {t.strip() for t in self.api_tokens.split(",") if t.strip()}

    @property
    def link_token(self) -> str:
        toks = [t.strip() for t in self.api_tokens.split(",") if t.strip()]
        return toks[0] if toks else ""

    def current_public_url(self) -> str:
        """Live tunnel URL if up, else the static PUBLIC_BASE_URL, else ''."""
        try:
            u = self.public_url_file.read_text().strip()
            if u:
                return u
        except OSError:
            pass
        return self.public_base_url

    @property
    def translate_target(self) -> str | None:
        t = self.translate_to.strip()
        return None if t.lower() in ("", "none") else t

    # ------------------------------------------------------------------ #
    # Onboarding / configured state
    # ------------------------------------------------------------------ #
    @property
    def is_configured(self) -> bool:
        """True once the wizard has finished AND an Anthropic key exists."""
        return bool(self.anthropic_api_key) and self.setup_complete

    # ------------------------------------------------------------------ #
    # Plaud token store (lives in config.json under "plaud_token")
    # ------------------------------------------------------------------ #
    def get_plaud_token(self) -> dict[str, Any] | None:
        tok = self._runtime.get("plaud_token")
        if isinstance(tok, dict) and tok.get("access_token"):
            return tok
        return None

    def set_plaud_token(self, token: dict[str, Any]) -> None:
        self.save_config({"plaud_token": token})

    def clear_plaud_token(self) -> None:
        self.save_config({"plaud_token": None})

    @property
    def plaud_logged_in(self) -> bool:
        tok = self.get_plaud_token()
        if not tok:
            return False
        try:
            exp = float(tok.get("expires_at") or 0.0)
        except (TypeError, ValueError):
            return False
        # No expiry recorded -> assume valid; else require >60s of life left.
        return exp <= 0 or exp > time.time() + 60

    # ------------------------------------------------------------------ #
    # Notion CRM secret (lives in config.json under "notion_token")
    # ------------------------------------------------------------------ #
    def get_notion_token(self) -> str:
        tok = self._runtime.get("notion_token")
        return tok if isinstance(tok, str) else ""

    def set_notion_token(self, token: str) -> None:
        self.save_config({"notion_token": token})

    def clear_notion_token(self) -> None:
        self.save_config({"notion_token": None})

    @property
    def crm_connected(self) -> bool:
        return bool(self.get_notion_token()) and bool(self.crm_database_id)

    # ------------------------------------------------------------------ #
    # App-password store (PBKDF2 hash in config.json; never the plaintext)
    # ------------------------------------------------------------------ #
    @property
    def has_password(self) -> bool:
        creds = self._runtime.get("password")
        return isinstance(creds, dict) and bool(creds.get("hash"))

    def get_password_record(self) -> dict[str, Any] | None:
        creds = self._runtime.get("password")
        return creds if isinstance(creds, dict) else None

    def set_password_record(self, record: dict[str, Any]) -> None:
        self.save_config({"password": record})


settings = Settings()._apply_runtime()
