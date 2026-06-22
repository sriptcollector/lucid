"""First-run onboarding + in-app settings logic.

Everything the setup wizard needs that isn't a route:

  * validating the Anthropic API key (a real, cheap auth check)
  * choosing + validating a transcription backend
  * connecting a Plaud account (delegates to the pure-Python plaud client)
  * setting the app password (PBKDF2 hash; never stores the plaintext) and
    minting/keeping the bearer token the SPA uses
  * reporting wizard state to the UI

Routes in ``main.py`` are thin wrappers over these functions. All persistence
goes through ``settings.save_config`` (atomic write to data/config.json).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from .config import settings

_PBKDF2_ROUNDS = 200_000
_PBKDF2_ALGO = "sha256"


# --------------------------------------------------------------------------- #
# App password  (the "login info" a friend sets to protect their public link)
# --------------------------------------------------------------------------- #
def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return dk.hex()


def set_password(password: str) -> str:
    """Store a PBKDF2 hash of ``password`` and return the bearer token.

    An existing token is preserved (so already-signed-in devices keep working
    when the password is merely changed); otherwise a fresh one is minted.
    """
    password = (password or "").strip()
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    salt = secrets.token_bytes(16)
    record = {
        "algo": _PBKDF2_ALGO,
        "rounds": _PBKDF2_ROUNDS,
        "salt": salt.hex(),
        "hash": _hash_password(password, salt),
    }
    token = settings.link_token or secrets.token_urlsafe(24)
    settings.save_config({"password": record, "api_tokens": token})
    return token


def verify_password(password: str) -> str | None:
    """Return the bearer token if ``password`` matches, else ``None``."""
    record = settings.get_password_record()
    if not record:
        return None
    try:
        salt = bytes.fromhex(record["salt"])
        rounds = int(record.get("rounds", _PBKDF2_ROUNDS))
        algo = record.get("algo", _PBKDF2_ALGO)
        expected = record["hash"]
    except (KeyError, ValueError):
        return None
    dk = hashlib.pbkdf2_hmac(algo, (password or "").encode("utf-8"), salt, rounds).hex()
    if hmac.compare_digest(dk, expected):
        return settings.link_token or None
    return None


# --------------------------------------------------------------------------- #
# Anthropic key validation
# --------------------------------------------------------------------------- #
def validate_anthropic(key: str) -> tuple[bool, str]:
    """Lightweight live check that an Anthropic key is real and accepted."""
    key = (key or "").strip()
    if not key.startswith("sk-ant-"):
        return False, "That doesn't look like an Anthropic key (should start with sk-ant-)."
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        # models.list is a cheap, token-free authenticated call.
        client.models.list()
        return True, "Anthropic key verified."
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "Authentication" in name or "PermissionDenied" in name or "401" in str(exc):
            return False, "Anthropic rejected that key. Double-check it and try again."
        return False, f"Couldn't reach Anthropic to verify the key ({name}). Check your connection."


def save_anthropic(key: str) -> None:
    settings.save_config({"anthropic_api_key": (key or "").strip()})


# --------------------------------------------------------------------------- #
# Transcription choice
# --------------------------------------------------------------------------- #
_LOCAL_MODELS = {"tiny", "base", "small", "medium", "large-v3"}


def save_transcription(mode: str, *, model: str = "", openai_key: str = "",
                       deepgram_key: str = "") -> tuple[bool, str]:
    """Persist the transcription backend choice. ``mode`` is local|openai|deepgram."""
    mode = (mode or "").strip().lower()
    if mode in ("local", "faster_whisper", "whisper"):
        model = (model or "small").strip()
        if model not in _LOCAL_MODELS:
            model = "small"
        settings.save_config({"transcribe_backend": "faster_whisper", "whisper_model": model})
        return True, f"Local transcription set ({model})."
    if mode == "openai":
        key = (openai_key or "").strip()
        if not key:
            return False, "An OpenAI API key is required for cloud transcription."
        settings.save_config({"transcribe_backend": "openai", "openai_api_key": key})
        return True, "Cloud transcription set (OpenAI)."
    if mode == "deepgram":
        key = (deepgram_key or "").strip()
        if not key:
            return False, "A Deepgram API key is required for cloud transcription."
        settings.save_config({"transcribe_backend": "deepgram", "deepgram_api_key": key})
        return True, "Cloud transcription set (Deepgram)."
    return False, "Pick a transcription option."


# --------------------------------------------------------------------------- #
# Plaud connection
# --------------------------------------------------------------------------- #
def connect_plaud(email: str, password: str, region: str = "us") -> dict[str, Any]:
    """Log in to Plaud, persist the token, enable cloud polling, return a summary.

    Raises on bad credentials (caller maps to an HTTP 400 with the message).
    """
    from .ingest import plaud_client

    email = (email or "").strip()
    if not email or not password:
        raise ValueError("Enter your Plaud email and password.")
    info = plaud_client.connect(email, password, region=region or "us")

    tok = settings.get_plaud_token() or {}
    settings.save_config({
        "plaud_cloud_enabled": True,
        "plaud_email": email,
        "plaud_region": tok.get("region", region or "us"),
    })

    count = None
    try:
        count = len(plaud_client.PlaudCloud().list_recordings())
    except Exception:  # noqa: BLE001 - the connection itself already succeeded
        count = None

    return {
        "email": info.get("email") or email,
        "nickname": info.get("nickname") or "",
        "recordings": count,
    }


def disconnect_plaud() -> None:
    settings.clear_plaud_token()
    settings.save_config({"plaud_cloud_enabled": False})


# --------------------------------------------------------------------------- #
# Wizard state
# --------------------------------------------------------------------------- #
def setup_state() -> dict[str, Any]:
    """Snapshot of which onboarding steps are satisfied (drives the wizard UI)."""
    return {
        "configured": settings.is_configured,
        "setup_complete": settings.setup_complete,
        "steps": {
            "anthropic": bool(settings.anthropic_api_key),
            "transcription": bool(settings.transcribe_backend),
            "plaud": settings.plaud_logged_in,
            "password": settings.has_password,
        },
        "transcribe_backend": settings.transcribe_backend,
        "whisper_model": settings.whisper_model,
        "plaud_email": settings.plaud_email,
        "plaud_region": settings.plaud_region,
        "needs_password": not settings.has_password,
    }


def finish_setup() -> None:
    """Mark onboarding complete. The app then starts the tunnel + Plaud poller."""
    settings.save_config({"setup_complete": True})
