"""Pure-Python client for the Plaud cloud REST API.

Plaud devices with **Private Cloud Sync** enabled upload each recording to
Plaud's cloud automatically. This module talks to that (unofficial,
account-based) REST API directly with `httpx` — no Node, no `plaud-toolkit`,
no subprocess. It handles login (email/password -> JWT), the US/EU region
split, listing recordings, downloading audio, and reading user info.

Credentials are never stored here: `login()` returns a token dict and the
caller persists it via the settings contract (`settings.set_plaud_token`).
Authenticated calls go through `PlaudCloud`, which reads the stored token via
`settings.get_plaud_token()` and refreshes the persisted region on a -302
region-mismatch redirect.

The cloud API was reverse-engineered from the official toolkit source. Base
URLs, paths, and response shapes are documented inline next to each call.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

# Per-region API roots. The cloud sharded accounts across two stacks; a login
# (or any call) on the wrong one comes back with status -302 + the right host.
_BASE_URLS: dict[str, str] = {
    "us": "https://api.plaud.ai",
    "eu": "https://api-euc1.plaud.ai",
}

# A desktop Chrome UA is required — the API rejects/!throttles unknown clients.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Plaud file ids are URL-path segments; validate before interpolating so a
# malformed/hostile id can never escape the path.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")

_HTTP_TIMEOUT = httpx.Timeout(30.0, read=120.0)


class PlaudError(Exception):
    """Any Plaud cloud API failure (HTTP error or non-zero API status)."""


class PlaudAuthError(PlaudError):
    """Not logged in, or the stored token is missing/expired."""


def _region_of(region: str | None) -> str:
    """Normalize an arbitrary region string to a known key ('us'|'eu')."""
    r = (region or "us").lower()
    return r if r in _BASE_URLS else "us"


def _base_url(region: str | None) -> str:
    return _BASE_URLS[_region_of(region)]


def _region_from_domain(domain: str) -> str:
    """Map a redirect target host to a region key. 'euc1' => eu, else us."""
    return "eu" if "euc1" in (domain or "").lower() else "us"


def _decode_jwt_exp(token: str) -> float:
    """Read the 'exp' (epoch seconds) claim from a JWT's payload segment.

    The middle segment is base64url with stripped padding; we re-pad before
    decoding. Returns 0.0 if the token can't be parsed (caller treats a
    non-positive expiry conservatively).
    """
    try:
        payload_seg = token.split(".")[1]
        padding = "=" * (-len(payload_seg) % 4)
        raw = base64.urlsafe_b64decode(payload_seg + padding)
        claims = json.loads(raw)
        exp = claims.get("exp")
        return float(exp) if exp is not None else 0.0
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return 0.0


def _redirect_region(data: dict[str, Any]) -> str | None:
    """If `data` is a -302 region redirect, return the region to switch to."""
    if not isinstance(data, dict) or data.get("status") != -302:
        return None
    api_host = (
        data.get("data", {}).get("domains", {}).get("api")
        if isinstance(data.get("data"), dict)
        else None
    )
    if not api_host:
        return None
    return _region_from_domain(api_host)


def _headers(token: str | None = None, *, form: bool = False) -> dict[str, str]:
    h = {"User-Agent": _USER_AGENT}
    h["Content-Type"] = (
        "application/x-www-form-urlencoded" if form else "application/json"
    )
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _login_once(client: httpx.Client, region: str, email: str, password: str) -> dict[str, Any]:
    """Single login attempt against one region. Returns the parsed JSON."""
    url = f"{_base_url(region)}/auth/access-token"
    resp = client.post(
        url,
        data={"username": email, "password": password},
        headers=_headers(form=True),
    )
    resp.raise_for_status()
    return resp.json()


def login(email: str, password: str, region: str = "us") -> dict[str, Any]:
    """Authenticate and return a token dict. Does NOT persist anything.

    Returns: {'access_token', 'expires_at' (epoch float), 'region', 'email'}.
    On a -302 region mismatch we retry the indicated (other) region once and
    report the region that actually worked, so the caller stores the right one.
    """
    region = _region_of(region)
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        data = _login_once(client, region, email, password)

        # Region mismatch: the API tells us which stack owns this account.
        new_region = _redirect_region(data)
        if new_region and new_region != region:
            region = new_region
            data = _login_once(client, region, email, password)

    if data.get("status") != 0 or not data.get("access_token"):
        msg = data.get("msg") or "login failed (check email/password)"
        raise PlaudAuthError(f"Plaud login failed: {msg}")

    token = data["access_token"]
    exp = _decode_jwt_exp(token)
    # JWTs without a readable exp default to ~300 days (Plaud token lifetime).
    if exp <= 0:
        exp = time.time() + 300 * 24 * 3600

    return {
        "access_token": token,
        "expires_at": exp,
        "region": region,
        "email": email,
    }


class PlaudCloud:
    """Authenticated session against the Plaud cloud, using the stored token.

    Pulls `{access_token, expires_at, region, email}` from
    `settings.get_plaud_token()` on construction. If the region is corrected
    mid-flight (a -302 redirect), we update `self.region` and re-persist the
    token dict so future runs start on the right stack.
    """

    def __init__(self) -> None:
        tok = settings.get_plaud_token()
        if not tok or not tok.get("access_token"):
            raise PlaudAuthError("Not connected to Plaud — log in first.")
        self._token: str = tok["access_token"]
        self._region: str = _region_of(tok.get("region"))
        self._email: str | None = tok.get("email")
        self._expires_at: float = float(tok.get("expires_at") or 0.0)

    @property
    def token(self) -> str:
        return self._token

    @property
    def region(self) -> str:
        return self._region

    def _ensure_fresh(self) -> None:
        # A small skew guard: treat a token expiring within 60s as expired.
        if self._expires_at and self._expires_at <= time.time() + 60:
            raise PlaudAuthError(
                "Plaud token has expired — log in again to refresh it."
            )

    def _persist_region(self) -> None:
        """Re-save the token dict with the corrected region."""
        settings.set_plaud_token(
            {
                "access_token": self._token,
                "expires_at": self._expires_at,
                "region": self._region,
                "email": self._email,
            }
        )

    def _request(
        self,
        path: str,
        method: str = "GET",
        *,
        stream_to: Path | None = None,
        _retried: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Issue an authed request. Returns parsed JSON, or the dest Path when
        `stream_to` is set (body streamed to disk as raw bytes).

        Handles the -302 region redirect by switching region + persisting, then
        retrying once. Raises PlaudError with a clear message on HTTP failure.
        """
        self._ensure_fresh()
        url = f"{_base_url(self._region)}{path}"
        headers = {**_headers(self._token), **kwargs.pop("headers", {})}

        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                if stream_to is not None:
                    return self._stream_download(client, method, url, headers, stream_to, **kwargs)
                resp = client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise PlaudError(f"Plaud request to {path} failed: {exc}") from exc

        if resp.status_code >= 400:
            raise PlaudError(
                f"Plaud request to {path} returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        data = self._parse_json(resp, path)

        # Region mismatch -> switch, persist, retry once.
        if not _retried:
            new_region = _redirect_region(data) if isinstance(data, dict) else None
            if new_region and new_region != self._region:
                self._region = new_region
                self._persist_region()
                return self._request(
                    path, method, stream_to=stream_to, _retried=True, **kwargs
                )

        if isinstance(data, dict) and data.get("status") not in (None, 0):
            msg = data.get("msg") or f"status {data.get('status')}"
            raise PlaudError(f"Plaud request to {path} failed: {msg}")
        return data

    def _stream_download(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        headers: dict[str, str],
        dest: Path,
        **kwargs: Any,
    ) -> Path:
        """Stream a binary response body to `dest`."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with client.stream(method, url, headers=headers, **kwargs) as resp:
            if resp.status_code >= 400:
                # Drain enough text for a useful message without buffering all.
                resp.read()
                raise PlaudError(
                    f"Plaud download returned HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    if chunk:
                        fh.write(chunk)
        return dest

    @staticmethod
    def _parse_json(resp: httpx.Response, path: str) -> Any:
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise PlaudError(
                f"Plaud request to {path} returned non-JSON body."
            ) from exc

    def list_recordings(self) -> list[dict[str, Any]]:
        """Return non-trashed recordings. Each item has at least id, filename,
        fullname, filesize, duration, start_time, end_time, is_trash, is_trans.
        """
        data = self._request("/file/simple/web")
        if not isinstance(data, dict):
            return []
        items = data.get("data_file_list")
        if items is None:
            items = data.get("data")
        if not isinstance(items, list):
            return []
        return [it for it in items if isinstance(it, dict) and not it.get("is_trash")]

    def download_audio(self, file_id: str, dest_path: Path) -> Path:
        """Download a recording's audio (mp3) to `dest_path` (forced .mp3).

        Tries the direct binary endpoint first; on failure, falls back to the
        signed temp-url endpoint and fetches that. Returns the written path.
        """
        if not _ID_RE.match(file_id or ""):
            raise PlaudError(f"Refusing to download invalid Plaud id: {file_id!r}")

        dest = Path(dest_path).with_suffix(".mp3")
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._request(f"/file/download/{file_id}", stream_to=dest)
            if dest.exists() and dest.stat().st_size > 0:
                return dest
        except PlaudError as exc:
            print(f"[plaud_client] direct download failed for {file_id}: {exc}")

        # Fallback: ask for a signed URL, then fetch it directly.
        url = self._temp_url(file_id)
        if not url:
            raise PlaudError(f"No download URL available for {file_id}.")
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                with client.stream("GET", url, headers={"User-Agent": _USER_AGENT}) as resp:
                    resp.raise_for_status()
                    with dest.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            if chunk:
                                fh.write(chunk)
        except httpx.HTTPError as exc:
            raise PlaudError(f"Temp-url download failed for {file_id}: {exc}") from exc
        return dest

    def _temp_url(self, file_id: str) -> str | None:
        """Resolve a signed temporary download URL for `file_id`, or None."""
        data = self._request(f"/file/temp-url/{file_id}", params={"is_opus": "false"})
        if isinstance(data, str):
            return data or None
        if isinstance(data, dict):
            for key in ("url", "temp_url"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    return val
            inner = data.get("data")
            if isinstance(inner, str) and inner:
                return inner
            if isinstance(inner, dict):
                val = inner.get("url")
                if isinstance(val, str) and val:
                    return val
        return None

    def user_info(self) -> dict[str, Any]:
        """Return the account's user record: id, nickname, email, country, and
        membership type (flattened from data_state.membership_type)."""
        data = self._request("/user/me")
        if not isinstance(data, dict):
            return {}
        user = data.get("data_user")
        if not isinstance(user, dict):
            user = data.get("data") if isinstance(data.get("data"), dict) else {}
        info = dict(user)
        state = data.get("data_state")
        if isinstance(state, dict) and "membership_type" in state:
            info.setdefault("membership_type", state.get("membership_type"))
        return info


def connect(email: str, password: str, region: str = "us") -> dict[str, Any]:
    """Log in, persist the token via settings, and return the user info dict.

    Onboarding uses this to confirm "Connected as <email>" and can immediately
    follow up with `list_recordings()` for an N-recordings count.
    """
    tok = login(email, password, region=region)
    settings.set_plaud_token(
        {
            "access_token": tok["access_token"],
            "expires_at": tok["expires_at"],
            "region": tok["region"],
            "email": tok["email"],
        }
    )
    return PlaudCloud().user_info()


def is_connected() -> bool:
    """True when a valid (present, unexpired) Plaud token is stored."""
    return bool(settings.plaud_logged_in)
