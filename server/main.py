"""Lucid FastAPI server.

A self-hosted AI notetaker: it auto-pulls Plaud recordings, transcribes and
analyzes them into clean sorted notes, and serves a web app. A first-run setup
wizard (served when the app isn't configured yet) collects the Anthropic key,
transcription choice, Plaud login, and an app password, then auto-deploys a
public Cloudflare quick tunnel.

Core API:
  POST   /api/upload                       multipart audio -> queue processing
  GET    /api/recordings                   list notes (summaries)
  GET    /api/recordings/{id}              full note + analysis
  GET    /api/recordings/{id}/audio        stream audio
  POST   /api/recordings/{id}/reanalyze    re-run the analysis layer
  DELETE /api/recordings/{id}
  GET    /api/health
Setup / account:
  GET    /api/setup/state
  POST   /api/setup/anthropic | /transcription | /plaud | /password | /finish
  POST   /api/login                        password -> bearer token
  GET    /api/tunnel  | POST /api/tunnel/restart
  GET/POST /api/settings
"""
from __future__ import annotations

import asyncio
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import agent, backup, beliefs, businesses, email_login, setup_service, storage, tunnel
from .config import settings
from .ingest import intake, plaud_cloud, telegram_bot, watcher
from .models import Status
from .pipeline import analyze, assistant, directory, projects, relationships, runner, ventures
from .pipeline.rename import rename_person as _rename_in

app = FastAPI(title="Lucid", version="1.0.0")


@app.middleware("http")
async def _fresh_assets(request: Request, call_next):
    """Never serve a stale app shell or CSS/JS — make the browser revalidate each load.
    This is a frequently-updated self-hosted app; without it, edits look broken until a
    hard refresh (the cause of the 'it looks terrible' stale-CSS issue)."""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html", ".webmanifest")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lucid-pipe")
# Frozen (PyInstaller) builds bundle web/ as data; resolve it from _MEIPASS.
if getattr(sys, "frozen", False):
    WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "web"
else:
    WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_UPLOAD = 400 * 1024 * 1024     # 400 MB cap on a public upload endpoint
_START = time.time()

# Tracks which always-on background services have been started, so we can start
# them lazily right after onboarding finishes without double-spawning.
_services = {"started": False}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def auth(request: Request) -> None:
    """Require a valid bearer token once one is configured.

    Before the user sets an app password there are no tokens, so the API is
    open (localhost-only at that point — the tunnel isn't up until setup ends).
    """
    tokens = settings.tokens
    if not tokens:
        return
    tok = ""
    authz = request.headers.get("authorization", "")
    if authz.startswith("Bearer "):
        tok = authz.split(" ", 1)[1].strip()
    if not tok:
        tok = request.query_params.get("k", "") or request.query_params.get("token", "")
    if tok not in tokens:
        raise HTTPException(401, "Invalid or missing token")


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def setup_or_auth(request: Request) -> None:
    """Gate the first-run setup endpoints.

    Before setup is complete the API has no token yet, so to stop a LAN
    neighbour from racing the owner to claim a fresh instance, onboarding may
    only be driven from the local machine (loopback). Once setup is complete the
    endpoints require the normal bearer token.
    """
    if settings.setup_complete:
        auth(request)
        return
    if not _is_loopback(request):
        raise HTTPException(
            403, "Finish setup on the computer running Lucid (open http://127.0.0.1:8000).")


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def start_runtime_services() -> None:
    """Start the always-on background services (idempotent).

    Called at startup when already configured, and again right after the setup
    wizard finishes. The tunnel manager is itself idempotent; the others are
    guarded by ``_services['started']``.
    """
    if settings.tunnel_enabled:
        tunnel.start()
    if _services["started"]:
        return
    _services["started"] = True
    plaud_cloud.start()        # no-op unless plaud_cloud_enabled
    telegram_bot.start()       # no-op unless telegram_enabled


@app.on_event("startup")
def _startup() -> None:
    storage.init_db()
    backup.start()        # periodic consistent snapshots of the notes DB
    intake.set_enqueue(lambda rec_id: _pool.submit(runner.process, rec_id))
    watcher.start()
    # Re-kick recordings that never finished (queued / transcribing / error).
    # Server restarts interrupt in-flight jobs, and analysis used to die on
    # exhausted API credits; both left recordings stuck with no note. Now
    # analysis falls back to the Claude CLI, so a re-run actually completes.
    try:
        stuck = [r for r in storage.list_recordings(limit=500)
                 if (r.status.value if hasattr(r.status, "value") else
                     str(r.status)) != "done"]
        for r in stuck[:25]:
            _pool.submit(runner.process, r.id)
        if stuck:
            print("[startup] re-enqueued %d unfinished recording(s)"
                  % len(stuck[:25]), flush=True)
    except Exception as e:  # noqa: BLE001
        print("[startup] re-enqueue failed: %s" % str(e)[:120], flush=True)
    if settings.is_configured:
        start_runtime_services()
        # warm the business/project grouping in the background so the first
        # visit to that view is already sorted
        try:
            businesses.warm_async(storage.list_recordings(limit=500))
        except Exception:  # noqa: BLE001
            pass


@app.on_event("shutdown")
def _shutdown() -> None:
    try:
        tunnel.stop()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Health + systems
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "configured": settings.is_configured,
        "transcribe_backend": settings.transcribe_backend,
        "model": settings.analysis_model,
        "translate_to": settings.translate_to,
        "plaud_cloud_enabled": settings.plaud_cloud_enabled,
        "plaud_logged_in": settings.plaud_logged_in,
        "tunnel": tunnel.status(),
        "stable_url": settings.stable_public_url,
    }


def _fmt_age(minutes: float) -> str:
    if minutes < 1:
        return f"{int(minutes * 60)}s"
    if minutes < 60:
        return f"{int(minutes)}m"
    if minutes < 60 * 24:
        return f"{int(minutes // 60)}h {int(minutes % 60)}m"
    return f"{int(minutes // (60 * 24))}d"


@app.get("/api/systems", dependencies=[Depends(auth)])
def systems_status() -> dict:
    """Live health of each subsystem — feeds the Settings status view."""
    out: list[dict] = []

    def add(key: str, label: str, ok: str, detail: str) -> None:
        out.append({"key": key, "label": label, "ok": ok, "detail": detail})

    add("server", "Lucid server", "up",
        f"up {_fmt_age((time.time() - _START) / 60)} | "
        f"{settings.transcribe_backend} -> {settings.analysis_model}")

    recs = storage.list_recordings()
    busy = [r for r in recs if r.status in
            (Status.QUEUED, Status.TRANSCRIBING, Status.TRANSLATING, Status.ANALYZING)]
    errs = [r for r in recs if r.status == Status.ERROR]
    if errs:
        add("pipeline", "Processing", "warn",
            f"{len(errs)} in error | {len(busy)} in flight | {len(recs)} total")
    elif busy:
        add("pipeline", "Processing", "up", f"{len(busy)} processing now | {len(recs)} total")
    else:
        add("pipeline", "Processing", "up", f"idle | {len(recs)} notes")

    if not settings.plaud_cloud_enabled:
        add("plaud", "Plaud sync", "warn", "not connected")
    elif not settings.plaud_logged_in:
        add("plaud", "Plaud sync", "down", "session expired — reconnect in Settings")
    else:
        add("plaud", "Plaud sync", "up",
            f"connected as {settings.plaud_email or 'your account'} | "
            f"every {settings.plaud_poll_interval}s")

    ts = tunnel.status()
    if ts["url"]:
        add("tunnel", "Public link", "up", ts["url"])
    elif ts["enabled"]:
        add("tunnel", "Public link", "warn", "starting…")
    else:
        add("tunnel", "Public link", "warn", "disabled")

    add("anthropic", "Anthropic key", "up" if settings.anthropic_api_key else "down",
        "configured" if settings.anthropic_api_key else "missing")

    try:
        from .pipeline import transcribe as _tr
        loaded = getattr(_tr, "_fw_model", None) is not None
    except Exception:  # noqa: BLE001
        loaded = False
    if settings.transcribe_backend == "faster_whisper":
        add("transcribe", "Transcriber", "up",
            f"{settings.whisper_model} loaded" if loaded
            else f"local {settings.whisper_model} | loads on first note")
    else:
        add("transcribe", "Transcriber", "up", f"cloud ({settings.transcribe_backend})")

    worst = ("down" if any(s["ok"] == "down" for s in out)
             else "warn" if any(s["ok"] == "warn" for s in out) else "up")
    return {"overall": worst, "checked_at": time.time(), "systems": out}


# --------------------------------------------------------------------------- #
# Setup wizard + account
# --------------------------------------------------------------------------- #
@app.get("/api/setup/state", dependencies=[Depends(setup_or_auth)])
def setup_state() -> dict:
    state = setup_service.setup_state()
    state["tunnel"] = tunnel.status()
    return state


@app.post("/api/setup/anthropic", dependencies=[Depends(setup_or_auth)])
async def setup_anthropic(request: Request) -> dict:
    key = (await request.json()).get("key", "")
    ok, msg = await asyncio.to_thread(setup_service.validate_anthropic, key)
    if not ok:
        raise HTTPException(400, msg)
    setup_service.save_anthropic(key)
    return {"ok": True, "message": msg}


@app.post("/api/setup/transcription", dependencies=[Depends(setup_or_auth)])
async def setup_transcription(request: Request) -> dict:
    body = await request.json()
    ok, msg = setup_service.save_transcription(
        body.get("mode", ""),
        model=body.get("model", ""),
        openai_key=body.get("openai_key", ""),
        deepgram_key=body.get("deepgram_key", ""),
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@app.post("/api/setup/plaud", dependencies=[Depends(setup_or_auth)])
async def setup_plaud(request: Request) -> dict:
    body = await request.json()
    try:
        info = await asyncio.to_thread(
            setup_service.connect_plaud,
            body.get("email", ""), body.get("password", ""), body.get("region", "us"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 - bad creds / network surface as 400
        raise HTTPException(400, str(exc) or "Could not connect to Plaud.")
    # Plaud is now enabled — (re)start the poller immediately. Idempotent, so it
    # also covers connecting Plaud after onboarding already finished.
    plaud_cloud.start()
    return {"ok": True, **info}


@app.delete("/api/setup/plaud", dependencies=[Depends(auth)])
def setup_plaud_disconnect() -> dict:
    setup_service.disconnect_plaud()
    return {"ok": True}


@app.post("/api/setup/telegram", dependencies=[Depends(setup_or_auth)])
async def setup_telegram(request: Request) -> dict:
    token = (await request.json()).get("token", "")
    try:
        info = await asyncio.to_thread(setup_service.connect_telegram, token)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc) or "Could not connect Telegram.")
    return {"ok": True, **info}


@app.get("/api/setup/telegram/status", dependencies=[Depends(setup_or_auth)])
async def setup_telegram_status() -> dict:
    return await asyncio.to_thread(setup_service.telegram_status)


@app.post("/api/setup/telegram/test", dependencies=[Depends(setup_or_auth)])
async def setup_telegram_test() -> dict:
    sent = await asyncio.to_thread(setup_service.send_phone_link)
    return {"ok": True, "sent": sent}


@app.delete("/api/setup/telegram", dependencies=[Depends(auth)])
def setup_telegram_disconnect() -> dict:
    setup_service.disconnect_telegram()
    return {"ok": True}


@app.post("/api/setup/password", dependencies=[Depends(setup_or_auth)])
async def setup_password(request: Request) -> dict:
    pw = (await request.json()).get("password", "")
    try:
        token = await asyncio.to_thread(setup_service.set_password, pw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "token": token}


@app.post("/api/setup/finish", dependencies=[Depends(setup_or_auth)])
def setup_finish() -> dict:
    if not settings.anthropic_api_key:
        raise HTTPException(400, "Add your Anthropic API key first.")
    if not settings.has_password:
        raise HTTPException(400, "Set an app password first.")
    setup_service.finish_setup()
    start_runtime_services()
    return {"ok": True, "tunnel": tunnel.status()}


# Simple in-process brute-force guard for the public, unauthenticated /api/login.
_login_guard = {"fails": 0, "until": 0.0}


@app.post("/api/login")
async def login(request: Request) -> dict:
    pw = (await request.json()).get("password", "")
    if not settings.has_password:
        raise HTTPException(400, "No password is set yet.")
    now = time.time()
    if _login_guard["until"] > now:
        raise HTTPException(429, "Too many attempts — wait a moment and try again.")
    # PBKDF2 is CPU-bound; run it off the event loop so logins can't stall the app.
    token = await asyncio.to_thread(setup_service.verify_password, pw)
    if not token:
        _login_guard["fails"] += 1
        if _login_guard["fails"] >= 5:
            _login_guard["until"] = now + 30      # lock out for 30s after 5 misses
            _login_guard["fails"] = 0
        await asyncio.sleep(0.5)                   # blunt online guessing
        raise HTTPException(401, "Incorrect password.")
    _login_guard["fails"] = 0
    _login_guard["until"] = 0.0
    return {"ok": True, "token": token}


@app.get("/api/login/email/status")
def login_email_status() -> dict:
    """Tells the login screen whether to offer passwordless email sign-in."""
    return {"available": email_login.configured(), "hint": email_login.owner_hint()}


@app.post("/api/login/email/request")
async def login_email_request(request: Request) -> dict:
    """Email a one-time code to the owner. Always {ok:true} (no enumeration)."""
    email = (await request.json()).get("email", "")
    return await asyncio.to_thread(email_login.request_code, email)


@app.post("/api/login/email/verify")
async def login_email_verify(request: Request) -> dict:
    """Exchange a correct code for the bearer token (shares the brute guard)."""
    body = await request.json()
    now = time.time()
    if _login_guard["until"] > now:
        raise HTTPException(429, "Too many attempts — wait a moment and try again.")
    token = await asyncio.to_thread(
        email_login.verify_code, body.get("email", ""), body.get("code", ""))
    if not token:
        _login_guard["fails"] += 1
        if _login_guard["fails"] >= 8:
            _login_guard["until"] = now + 30
            _login_guard["fails"] = 0
        await asyncio.sleep(0.4)
        raise HTTPException(401, "Invalid or expired code.")
    _login_guard["fails"] = 0
    _login_guard["until"] = 0.0
    return {"ok": True, "token": token}


@app.get("/api/tunnel", dependencies=[Depends(auth)])
def tunnel_status() -> dict:
    return tunnel.status()


@app.post("/api/tunnel/restart", dependencies=[Depends(auth)])
def tunnel_restart() -> dict:
    tunnel.restart()
    return {"ok": True, "tunnel": tunnel.status()}


@app.get("/api/settings", dependencies=[Depends(auth)])
def get_settings() -> dict:
    return {
        "analysis_model": settings.analysis_model,
        "transcribe_backend": settings.transcribe_backend,
        "whisper_model": settings.whisper_model,
        "translate_to": settings.translate_to,
        "plaud_email": settings.plaud_email,
        "plaud_region": settings.plaud_region,
        "plaud_connected": settings.plaud_logged_in,
        "plaud_poll_interval": settings.plaud_poll_interval,
        "tunnel_enabled": settings.tunnel_enabled,
        "public_url": settings.current_public_url(),
        "stable_url": settings.stable_public_url,
        "telegram_connected": bool(settings.telegram_enabled and settings.telegram_bot_token),
        "telegram_chat_known": _telegram_chat_known(),
        "owner_name": settings.owner_name,
        "crm_connected": settings.crm_connected,
        "cal_connected": settings.cal_connected,
    }


def _telegram_chat_known() -> bool:
    try:
        from .notify import telegram as tg
        return bool(tg.default_chat())
    except Exception:  # noqa: BLE001
        return False


@app.post("/api/settings", dependencies=[Depends(auth)])
async def update_settings(request: Request) -> dict:
    body = await request.json()
    allowed = {
        "analysis_model", "translate_to", "plaud_poll_interval",
        "tunnel_enabled", "whisper_model", "owner_name",
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    if "owner_name" in updates:
        updates["owner_name"] = str(updates["owner_name"] or "").strip()[:80]
    # Validate the few values that could break the pipeline if mistyped.
    if "whisper_model" in updates and updates["whisper_model"] not in \
            {"tiny", "base", "small", "medium", "large-v3"}:
        updates.pop("whisper_model")
    if "plaud_poll_interval" in updates:
        try:
            updates["plaud_poll_interval"] = max(60, int(updates["plaud_poll_interval"]))
        except (TypeError, ValueError):
            updates.pop("plaud_poll_interval")
    if updates:
        settings.save_config(updates)
    if "tunnel_enabled" in updates:
        # tunnel.stop()/restart() do blocking joins — keep them off the loop.
        await asyncio.to_thread(tunnel.restart if updates["tunnel_enabled"] else tunnel.stop)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Client manager (Notion CRM)
# --------------------------------------------------------------------------- #
def _crm_status() -> dict:
    from .integrations import notion_crm
    return {
        "connected": settings.crm_connected,
        "enabled": settings.crm_enabled,
        "database_id": settings.crm_database_id,
        "owner_name": settings.owner_name,
        "contact_count": len(notion_crm.load_contacts()),
        "last_refresh": notion_crm.last_refresh(),
    }


@app.get("/api/crm/status", dependencies=[Depends(auth)])
def crm_status() -> dict:
    return _crm_status()


@app.post("/api/crm/connect", dependencies=[Depends(auth)])
async def crm_connect(request: Request) -> dict:
    from .integrations import notion_crm
    body = await request.json()
    token = (body.get("token") or "").strip()
    db_in = (body.get("database") or body.get("database_id") or "").strip()
    owner = (body.get("owner_name") or "").strip()

    if token:
        settings.set_notion_token(token)
    updates: dict = {}
    if db_in:
        db_id = notion_crm.extract_db_id(db_in)
        if not db_id:
            raise HTTPException(400, "That doesn't look like a Notion database link.")
        updates["crm_database_id"] = db_id
    if owner:
        updates["owner_name"] = owner[:80]
    if updates:
        settings.save_config(updates)

    ok, msg, title = notion_crm.test_and_describe()
    if not ok:
        raise HTTPException(400, f"Couldn't reach that Notion database — {msg}")
    settings.save_config({"crm_enabled": True})
    count = await asyncio.to_thread(notion_crm.refresh_contacts)
    sample = [c["name"] for c in notion_crm.load_contacts()[:8]]
    return {"ok": True, "database_title": title, "contact_count": count,
            "sample": sample, "status": _crm_status()}


@app.delete("/api/crm/connect", dependencies=[Depends(auth)])
def crm_disconnect() -> dict:
    settings.clear_notion_token()
    settings.save_config({"crm_enabled": False})
    return {"ok": True}


@app.post("/api/crm/refresh", dependencies=[Depends(auth)])
async def crm_refresh() -> dict:
    from .integrations import notion_crm
    if not settings.crm_connected:
        raise HTTPException(400, "Not connected to Notion.")
    count = await asyncio.to_thread(notion_crm.refresh_contacts)
    return {"ok": True, "contact_count": count}


# --------------------------------------------------------------------------- #
# Calendar matching (read-only iCal URL)
# --------------------------------------------------------------------------- #
def _cal_status() -> dict:
    from .integrations import calendar_ics
    return {
        "connected": settings.cal_connected,
        "event_count": len(calendar_ics.load_events()),
        "last_refresh": calendar_ics.last_refresh(),
    }


@app.get("/api/cal/status", dependencies=[Depends(auth)])
def cal_status() -> dict:
    return _cal_status()


@app.post("/api/cal/connect", dependencies=[Depends(auth)])
async def cal_connect(request: Request) -> dict:
    from .integrations import calendar_ics
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "Paste the secret iCal URL (it starts with https://).")
    settings.set_cal_ics_url(url)
    ok, msg, count = await asyncio.to_thread(calendar_ics.test_and_describe)
    if not ok:
        raise HTTPException(400, f"Couldn't read that calendar — {msg}")
    settings.save_config({"cal_enabled": True})
    count = await asyncio.to_thread(calendar_ics.refresh_events)
    return {"ok": True, "event_count": count, "status": _cal_status()}


@app.delete("/api/cal/connect", dependencies=[Depends(auth)])
def cal_disconnect() -> dict:
    settings.clear_cal_ics_url()
    settings.save_config({"cal_enabled": False})
    return {"ok": True}


@app.post("/api/cal/refresh", dependencies=[Depends(auth)])
async def cal_refresh() -> dict:
    from .integrations import calendar_ics
    if not settings.cal_connected:
        raise HTTPException(400, "No calendar connected.")
    count = await asyncio.to_thread(calendar_ics.refresh_events)
    return {"ok": True, "event_count": count}


# --------------------------------------------------------------------------- #
# Data API — read-only programmatic access to Lucid's data for external code.
# Authenticated by a dedicated data key (X-API-Key / Bearer / ?key=), separate
# from the app login token. The owner manages the key from Settings.
# --------------------------------------------------------------------------- #
_ALL = 1_000_000  # "give me everything" — far above any real note count


def data_auth(request: Request) -> None:
    """Allow the read API through with the data key OR a normal app token."""
    key = settings.get_data_api_key()
    tokens = settings.tokens
    if not key and not tokens:
        return  # not yet configured (loopback-only at that point)
    authz = request.headers.get("authorization", "")
    bearer = authz.split(" ", 1)[1].strip() if authz.startswith("Bearer ") else ""
    provided = (
        request.headers.get("x-api-key", "").strip()
        or bearer
        or request.query_params.get("key", "").strip()
    )
    if (key and provided == key) or (provided and provided in tokens):
        return
    raise HTTPException(401, "Invalid or missing API key")


def _note_link(rec_id: str) -> str:
    base = settings.stable_public_url or settings.current_public_url()
    return f"{base.rstrip('/')}/r/{rec_id}" if base else ""


def _note_data(rec, full: bool) -> dict:
    a = rec.analysis
    out = {
        "id": rec.id,
        "created_at": rec.created_at,
        "source": rec.source,
        "status": rec.status.value,
        "duration": rec.duration,
        "language": rec.language,
        "headline": a.headline if a else None,
        "summary": a.summary if a else None,
        "sentiment": a.sentiment if a else None,
        "people": [{"name": p.name or p.label, "role": p.role} for p in a.people] if a else [],
        "key_points": a.key_points if a else [],
        "action_items": [ai.model_dump() for ai in a.action_items] if a else [],
        "link": _note_link(rec.id),
    }
    if full and a:
        out.update({
            "ideas": [i.model_dump() for i in a.ideas],
            "plans": [p.model_dump() for p in a.plans],
            "commitments": [c.model_dump() for c in a.commitments],
            "notable_quotes": [q.model_dump() for q in a.notable_quotes],
            "topics": [t.model_dump() for t in a.topics],
            "timeline": [e.model_dump() for e in a.timeline],
            "relationship_dynamics": [r.model_dump() for r in a.relationship_dynamics],
        })
    return out


@app.get("/api/data", dependencies=[Depends(data_auth)])
def data_index() -> dict:
    return {
        "lucid_data_api": "1",
        "endpoints": {
            "notes": "/api/data/notes?limit=&offset=&since=YYYY-MM-DD&full=true",
            "note": "/api/data/notes/{id}",
            "people": "/api/data/people",
            "action_items": "/api/data/action-items",
        },
        "auth": "Send the data key as 'X-API-Key: <key>', 'Authorization: Bearer <key>', or '?key='.",
        "counts": {
            "notes": len(storage.list_recordings(limit=_ALL)),
            "people": len(directory.list_directory()),
        },
    }


@app.get("/api/data/notes", dependencies=[Depends(data_auth)])
def data_notes(limit: int = 50, offset: int = 0, since: str = "", full: bool = False) -> JSONResponse:
    recs = storage.list_recordings(limit=_ALL)
    if since:
        recs = [r for r in recs if (r.created_at or "") >= since]
    total = len(recs)
    offset = max(0, offset)
    page = recs[offset: offset + max(1, min(limit, 500))]
    return JSONResponse({
        "total": total, "offset": offset, "count": len(page),
        "notes": [_note_data(r, full) for r in page],
    })


@app.get("/api/data/notes/{rec_id}", dependencies=[Depends(data_auth)])
def data_note(rec_id: str) -> JSONResponse:
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "No such note")
    return JSONResponse(_note_data(rec, full=True))


@app.get("/api/data/people", dependencies=[Depends(data_auth)])
def data_people() -> JSONResponse:
    return JSONResponse({"people": directory.list_directory()})


@app.get("/api/data/action-items", dependencies=[Depends(data_auth)])
def data_action_items() -> JSONResponse:
    out = []
    for r in storage.list_recordings(limit=_ALL):
        a = r.analysis
        if not a:
            continue
        for ai in a.action_items:
            item = ai.model_dump()
            item.update({"note_id": r.id, "note_headline": a.headline, "created_at": r.created_at})
            out.append(item)
    return JSONResponse({"count": len(out), "action_items": out})


# --- Data key management (owner-only; lets you hand a key to code) ---------- #
@app.get("/api/data/key", dependencies=[Depends(auth)])
def data_key() -> dict:
    return {"key": settings.get_data_api_key(), "enabled": bool(settings.get_data_api_key())}


@app.post("/api/data/key/rotate", dependencies=[Depends(auth)])
def data_key_rotate() -> dict:
    key = "lkd_" + secrets.token_hex(20)
    settings.set_data_api_key(key)
    return {"key": key}


@app.delete("/api/data/key", dependencies=[Depends(auth)])
def data_key_clear() -> dict:
    settings.clear_data_api_key()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Recordings / notes
# --------------------------------------------------------------------------- #
@app.post("/api/upload", dependencies=[Depends(auth)])
async def upload(request: Request, file: UploadFile = File(...)) -> dict:
    if not intake.is_audio_name(file.filename or ""):
        raise HTTPException(400, "Unsupported file type — audio only")
    # Read in bounded chunks and abort as soon as the cap is exceeded, so a
    # client can't exhaust memory by omitting/spoofing Content-Length.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD:
            raise HTTPException(413, f"File too large (max {MAX_UPLOAD // (1024*1024)} MB)")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "Empty file")
    rec = intake.intake_bytes(data, file.filename or "upload.wav", source="upload")
    return {"id": rec.id, "status": rec.status.value}


@app.get("/api/recordings", dependencies=[Depends(auth)])
def list_recordings() -> list[dict]:
    out = []
    for r in storage.list_recordings():
        a = r.analysis
        out.append({
            "id": r.id,
            "source": r.source,
            "status": r.status.value,
            "created_at": r.created_at,
            "duration": r.duration,
            "language": r.language,
            "headline": a.headline if a else None,
            "summary": a.summary if a else None,
            "sentiment": a.sentiment if a else None,
            "topics": [t.label for t in a.topics] if a else [],
            "people": [(p.name or p.label) for p in a.people] if a else [],
            "action_items": len(a.action_items) if a else 0,
            "ideas": len(a.ideas) if a else 0,
        })
    return out


@app.get("/api/recordings/{rec_id}", dependencies=[Depends(auth)])
def get_recording(rec_id: str) -> JSONResponse:
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "Not found")
    return JSONResponse(rec.model_dump())


@app.get("/api/recordings/{rec_id}/audio", dependencies=[Depends(auth)])
def get_audio(rec_id: str):
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "Not found")
    p = Path(rec.filename)
    if not p.exists():
        raise HTTPException(404, "Audio file missing")
    return FileResponse(p)


@app.post("/api/recordings/{rec_id}/reanalyze", dependencies=[Depends(auth)])
def reanalyze(rec_id: str) -> dict:
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "Not found")

    def _job() -> None:
        storage.update_status(rec_id, Status.ANALYZING)
        r = storage.get(rec_id)
        if not r:
            return
        try:
            from .pipeline import voiceid
            if settings.voiceid_enabled and voiceid.has_enrollment():
                r.segments = voiceid.label_segments(r.filename, r.segments)
        except Exception:
            pass
        r.analysis = analyze.analyze(r)
        r.status = Status.DONE
        try:
            directory.apply_known_names(r)
        except Exception:
            pass
        storage.save(r)
        try:
            directory.learn_from_recording(r)
        except Exception:
            pass
        from .notify import telegram
        telegram.notify_done(r)

    _pool.submit(_job)
    return {"id": rec_id, "status": "analyzing"}


@app.delete("/api/recordings/{rec_id}", dependencies=[Depends(auth)])
def delete_recording(rec_id: str) -> dict:
    rec = storage.get(rec_id)
    if rec:
        try:
            Path(rec.filename).unlink(missing_ok=True)
        except Exception:
            pass
        storage.delete(rec_id)
    return {"deleted": rec_id}


# --------------------------------------------------------------------------- #
# People / directory / ventures
# --------------------------------------------------------------------------- #
@app.get("/api/people", dependencies=[Depends(auth)])
def list_people() -> list[dict]:
    return relationships.list_people()


@app.get("/api/ventures", dependencies=[Depends(auth)])
def list_ventures() -> list[dict]:
    return ventures.list_ventures()


@app.get("/api/ventures/{vid}", dependencies=[Depends(auth)])
def get_venture(vid: str) -> JSONResponse:
    v = ventures.get_venture(vid)
    if not v:
        raise HTTPException(404, "No such venture")
    return JSONResponse(v)


@app.post("/api/ventures/{vid}/build", dependencies=[Depends(auth)])
def build_venture(vid: str) -> JSONResponse:
    spec = ventures.build_spec(vid)
    if spec is None:
        raise HTTPException(404, "No such venture")
    return JSONResponse({"spec": spec})


@app.get("/api/directory", dependencies=[Depends(auth)])
def get_directory() -> list[dict]:
    return directory.list_directory()


@app.delete("/api/directory/{pid}", dependencies=[Depends(auth)])
def forget_person(pid: str) -> dict:
    directory.forget(pid)
    return {"forgotten": pid}


@app.get("/api/people/autofill", dependencies=[Depends(auth)])
def people_autofill(q: str = "") -> list[str]:
    return directory.autofill(q)


@app.get("/api/people/suggest", dependencies=[Depends(auth)])
def suggest_people() -> list[dict]:
    return relationships.suggest_merges()


@app.post("/api/people/merge", dependencies=[Depends(auth)])
async def merge_people(request: Request) -> dict:
    body = await request.json()
    keys = [k for k in (body.get("keys") or []) if k]
    into = (body.get("into") or "").strip()
    if len({relationships._norm(k) for k in keys}) < 2:
        raise HTTPException(400, "Select at least two people to combine")

    rawmap = relationships.raw_names_map()
    summaries = {p["key"]: p for p in relationships.list_people()}
    nkeys = [relationships._norm(k) for k in keys]
    if not into:
        best = max(nkeys, key=lambda k: summaries.get(k, {}).get("interactions", 0))
        into = summaries.get(best, {}).get("name", "") or rawmap.get(best, [""])[0]
    if not into:
        raise HTTPException(400, "Could not determine a name to keep")

    sources = {nm for k in nkeys for nm in rawmap.get(k, []) if nm and nm != into}
    touched = 0
    for rec in storage.list_recordings(limit=relationships._BIG):
        snap = rec.model_dump_json()
        for src in sources:
            _rename_in(rec, src, into)
        if rec.model_dump_json() != snap:
            storage.save(rec)
            touched += 1
    relationships.set_hidden(into, False)
    return {"ok": True, "into": into, "recordings_updated": touched}


@app.delete("/api/people/{key}", dependencies=[Depends(auth)])
def delete_person(key: str) -> dict:
    relationships.set_hidden(key, True)
    return {"deleted": key}


@app.post("/api/people/{key}/unhide", dependencies=[Depends(auth)])
def unhide_person(key: str) -> dict:
    relationships.set_hidden(key, False)
    return {"ok": True}


@app.get("/api/people/{key}", dependencies=[Depends(auth)])
def get_person(key: str) -> JSONResponse:
    prof = relationships.get_person(key)
    if not prof:
        raise HTTPException(404, "No such person")
    return JSONResponse(prof)


# --------------------------------------------------------------------------- #
# Projects (folders) + venture deletion  (ADDED section)
# Projects are user-curated collections of notes / people / ideas — see
# server/pipeline/projects.py. Deleting a venture tombstones it (ventures are
# auto-derived from notes) so it never re-appears — see ventures.delete_venture.
# --------------------------------------------------------------------------- #
@app.delete("/api/ventures/{vid}", dependencies=[Depends(auth)])
def delete_venture(vid: str) -> dict:
    ventures.delete_venture(vid)
    return {"deleted": vid}


@app.get("/api/businesses", dependencies=[Depends(auth)])
def list_businesses() -> JSONResponse:
    """Notes grouped under the Claude Code project each is about, with
    per-project new/uncopied counts. Cache-based and instant; a background
    warmer classifies any new notes."""
    recs = storage.list_recordings(limit=500)
    return JSONResponse(businesses.build_groups(recs))


@app.get("/api/projects/all", dependencies=[Depends(auth)])
def all_projects_index() -> JSONResponse:
    """Every project (all GitHub repos + custom subjects), with note counts,
    for the browsable list. Cache-based; a background warmer classifies."""
    recs = storage.list_recordings(limit=500)
    idx = businesses.project_index(recs)
    if any(r.id not in businesses._load(businesses.ASSIGN_CACHE, {}) for r in recs):
        businesses.warm_async(recs)
    return JSONResponse(idx)


@app.get("/api/mental", dependencies=[Depends(auth)])
def mental() -> JSONResponse:
    """The 'Mental' view: notes where the speaker reflects on themselves, plus
    a synthesized read of their recurring psychological patterns. Cache-based
    and instant; warms uncached notes in the background."""
    recs = storage.list_recordings(limit=500)
    idx = businesses.mental_index(recs)
    idx["patterns"] = businesses.mental_patterns(recs) if idx.get("count", 0) >= 2 else ""
    return JSONResponse(idx)


@app.post("/api/github/refresh", dependencies=[Depends(auth)])
def github_refresh() -> dict:
    """Re-pull the GitHub repo list + regenerate friendly names, then re-sort."""
    businesses.github_repos(force=True)
    businesses.friendly_names(businesses.github_repos())
    businesses.warm_async(storage.list_recordings(limit=500), force=True)
    return {"ok": True}


@app.post("/api/businesses/refresh", dependencies=[Depends(auth)])
def refresh_businesses(force: bool = False) -> dict:
    """Re-sort. force=true re-classifies EVERY note from scratch (used after
    adding a project, so existing notes get a chance to match it)."""
    businesses.discover_projects(force=True)
    recs = storage.list_recordings(limit=500)
    if force:
        businesses.warm_async(recs, force=True)
    else:
        businesses.warm_async(recs)
    return {"ok": True}


@app.post("/api/businesses/custom", dependencies=[Depends(auth)])
async def create_custom_project(request: Request) -> JSONResponse:
    """Create a project that isn't a Claude Code repo (e.g. a game being
    discussed before its code exists), then re-sort every note against it."""
    body = await request.json()
    try:
        proj = businesses.add_custom_project(
            body.get("name", ""), body.get("blurb", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    businesses.warm_async(storage.list_recordings(limit=500), force=True)
    return JSONResponse({"ok": True, "project": proj})


@app.get("/api/businesses/{pid}/copytext", dependencies=[Depends(auth)])
def business_copytext(pid: str, all: bool = False, days: int = 0,
                      sections: str = "", recent: int = 0) -> JSONResponse:
    """Clean, filterable project-only summary.
    all: false -> new (uncopied) notes; true -> every note about the project.
    days: 0 -> any time; else only notes from the last N days.
    recent: 0 -> off; else the N most recent notes (Copy recent).
    sections: comma list of gist,decisions,actions,ideas (blank = all)."""
    recs = storage.list_recordings(limit=500)
    secs = [s.strip() for s in sections.split(",") if s.strip()] or None
    return JSONResponse(businesses.copy_payload(
        pid, recs, include_all=all, since_days=max(0, days), sections=secs,
        recent=max(0, recent)))


@app.post("/api/businesses/compile", dependencies=[Depends(auth)])
async def business_compile(request: Request) -> JSONResponse:
    """Combine several projects into ONE compiled brief the user can copy.
    Body: {ids:[...], recent:int (N newest per project), days:int, sections}."""
    body = await request.json()
    ids = [str(i) for i in (body.get("ids") or []) if str(i).strip()]
    if not ids:
        raise HTTPException(400, "Select at least one project")
    try:
        recent = int(body.get("recent") or 0)
    except (TypeError, ValueError):
        recent = 0
    try:
        days = int(body.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    sections = body.get("sections") or None
    if isinstance(sections, str):
        sections = [s.strip() for s in sections.split(",") if s.strip()] or None
    recs = storage.list_recordings(limit=500)
    return JSONResponse(businesses.compile_projects(
        ids, recs, recent=max(0, recent), since_days=max(0, days),
        sections=sections))


@app.post("/api/businesses/{pid}/copied", dependencies=[Depends(auth)])
async def business_copied(pid: str, request: Request) -> dict:
    """Mark a project's notes as copied so they stop showing as new."""
    body = await request.json()
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(400, "ids must be a list")
    businesses.mark_copied(pid, [str(i) for i in ids])
    return {"ok": True, "copied": len(ids)}


@app.post("/api/businesses/{pid}/request", dependencies=[Depends(auth)])
async def business_request_change(pid: str, request: Request) -> JSONResponse:
    """Run a real Claude Code session (Opus 4.8, full tools) on this
    project's repo, on THIS machine, to make the requested change. Optional
    'image' (base64 data URL) attaches a screenshot the agent can look at."""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Describe the change you want")
    return JSONResponse(agent.request_change(pid, text, image=body.get("image")))


@app.post("/api/businesses/{pid}/fix-all", dependencies=[Depends(auth)])
def business_fix_all(pid: str) -> JSONResponse:
    """Combine every UNREAD note about this project into ONE change request
    and run it. Marks those notes read."""
    recs = storage.list_recordings(limit=500)
    payload = businesses.combine_change_prompt(pid, recs)
    if not payload:
        return JSONResponse({"ok": False, "error": "no new updates"})
    # pass the note ids to the job; they're marked read only when the change
    # actually SUCCEEDS, so a failed/timed-out agent doesn't silently drop them.
    r = agent.request_change(pid, payload["prompt"], seen_ids=payload["ids"])
    return JSONResponse(r)


@app.post("/api/notes/{rec_id}/seen", dependencies=[Depends(auth)])
def note_seen(rec_id: str) -> dict:
    """Read receipt: mark a note seen so its blue 'new' marker clears."""
    rec = storage.get(rec_id)
    if rec:
        businesses.mark_seen([rec_id], [rec])
    return {"ok": True}


@app.post("/api/notes/{rec_id}/assign", dependencies=[Depends(auth)])
async def note_assign(rec_id: str, request: Request) -> JSONResponse:
    """Manually sort a note into a specific project folder, or to Unsorted.
    Body: {project: <project id> | "" | "__unsorted"}. The choice is sticky —
    the auto-sorter will not override it."""
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "No such note")
    body = await request.json()
    pid = str(body.get("project") or "").strip()
    if pid in ("", "__unsorted", "none", "null"):
        pid = None
    return JSONResponse(businesses.set_assignment(rec, pid))


@app.get("/api/notes/{rec_id}/brief", dependencies=[Depends(auth)])
def note_brief(rec_id: str) -> JSONResponse:
    """Clean, third-person dev/company briefing of the note (no personal
    chatter, no 'Orion said'). Behind the 'Copy real info' button."""
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "No such note")
    return JSONResponse({"brief": businesses.note_brief(rec)})


@app.get("/api/businesses/{pid}/deploy/info", dependencies=[Depends(auth)])
def deploy_info(pid: str) -> JSONResponse:
    """How this project deploys (vercel / cloudflare) and whether it's ready."""
    proj = agent.project_for(pid)
    if not proj:
        raise HTTPException(404, "No such project")
    info = agent.detect_deploy(proj.get("cwd", ""), proj.get("orig_name", ""))
    # A repo that isn't cloned locally yet reads as method "none" (no folder),
    # but the deploy job clones on demand and would work. Tell the UI it's
    # deployable-after-clone instead of showing a false "no deploy setup".
    if info.get("method") == "none" and not proj.get("cwd") and proj.get("url"):
        info = {"method": "auto", "ready": True,
                "note": "Deploy target is detected on first deploy (clones the "
                        "repo, then Vercel/Cloudflare)."}
    return JSONResponse(info)


@app.post("/api/businesses/{pid}/deploy", dependencies=[Depends(auth)])
def deploy_project(pid: str, prod: bool = False) -> JSONResponse:
    """Deploy the project. prod=false -> preview URL; prod=true -> live
    domain. Production is deliberately a separate call from preview."""
    return JSONResponse(agent.request_deploy(pid, prod))


@app.get("/api/notes/{rec_id}/target", dependencies=[Depends(auth)])
def note_target(rec_id: str) -> JSONResponse:
    """Which project this note is about (if any), whether that project has a
    repo we can change, and the note turned into a change prompt. Powers the
    'send this to a Claude Code agent and fix it' button on a note."""
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "No such note")
    assign = businesses._cached_assign([rec])
    pid = assign.get(rec_id)
    proj = next((p for p in businesses.all_projects() if p["id"] == pid), None)
    has_repo = bool(proj and (proj.get("cwd") or proj.get("url")))
    a = rec.analysis
    transcript = (rec.full_text_translated or rec.full_text or "").strip()
    prompt = ("I recorded a voice note about this project. Read what I said "
              "and make the change(s) I'm asking for in the code. Keep it "
              "focused on exactly what I describe.\n\n")
    if a and a.summary:
        prompt += "Summary of my note:\n" + a.summary + "\n\n"
    if a and a.action_items:
        prompt += "Things I want done:\n" + "\n".join(
            "- " + x.text for x in a.action_items) + "\n\n"
    prompt += "My exact words (transcript):\n" + transcript[:6000]
    return JSONResponse({
        "project": ({"id": proj["id"], "name": proj["name"],
                     "orig_name": proj.get("orig_name", ""),
                     "url": proj.get("url", "")} if proj else None),
        "has_repo": has_repo,
        "summary": (a.summary if a else "") or "",
        "prompt": prompt,
    })


@app.get("/api/agent/jobs/{job_id}", dependencies=[Depends(auth)])
def agent_job(job_id: str) -> JSONResponse:
    j = agent.job(job_id)
    if not j:
        raise HTTPException(404, "No such job")
    return JSONResponse(j)


@app.get("/api/agent/jobs", dependencies=[Depends(auth)])
def agent_jobs(project: str = "", logs: int = 0, limit: int = 30) -> JSONResponse:
    """Recent sessions (changes + deploys). logs=1 includes each session's
    terminal-log tail — used by the per-project chat thread."""
    return JSONResponse({"jobs": agent.history(
        project or None, limit=max(1, min(limit, 200)),
        include_log=bool(logs))})


@app.post("/api/agent/jobs/{job_id}/rename", dependencies=[Depends(auth)])
async def agent_job_rename(job_id: str, request: Request) -> JSONResponse:
    """Rename a session — powers the chats bar's inline rename."""
    body = await request.json()
    r = agent.rename(job_id, str(body.get("title") or ""))
    if not r.get("ok"):
        raise HTTPException(
            404 if r.get("error") == "no such job" else 400,
            r.get("error") or "rename failed")
    return JSONResponse(r)


@app.get("/api/beliefs", dependencies=[Depends(auth)])
def beliefs_get() -> JSONResponse:
    """The Beliefs page: Orion's pasted product playbook + beliefs
    auto-extracted from his notes. Cache-based and instant; warms uncached
    notes in the background (same pattern as Mental)."""
    recs = storage.list_recordings(limit=500)
    return JSONResponse(beliefs.index(recs))


@app.post("/api/beliefs", dependencies=[Depends(auth)])
async def beliefs_save(request: Request) -> dict:
    body = await request.json()
    beliefs.set_text(str(body.get("text") or ""))
    return {"ok": True}


@app.post("/api/beliefs/remove", dependencies=[Depends(auth)])
async def beliefs_remove(request: Request) -> dict:
    """Hide an auto-extracted belief (sticks across rescans)."""
    body = await request.json()
    beliefs.remove(str(body.get("text") or ""))
    return {"ok": True}


@app.get("/api/projects", dependencies=[Depends(auth)])
def list_projects() -> list[dict]:
    return projects.list_projects()


@app.post("/api/projects", dependencies=[Depends(auth)])
async def create_project(request: Request) -> JSONResponse:
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Project name is required")
    return JSONResponse(projects.create_project(name))


@app.get("/api/projects/{pid}", dependencies=[Depends(auth)])
def get_project(pid: str) -> JSONResponse:
    p = projects.get_project(pid)
    if not p:
        raise HTTPException(404, "No such project")
    return JSONResponse(p)


@app.patch("/api/projects/{pid}", dependencies=[Depends(auth)])
async def rename_project(pid: str, request: Request) -> JSONResponse:
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Project name is required")
    p = projects.rename_project(pid, name)
    if not p:
        raise HTTPException(404, "No such project")
    return JSONResponse(p)


@app.delete("/api/projects/{pid}", dependencies=[Depends(auth)])
def delete_project(pid: str) -> dict:
    projects.delete_project(pid)
    return {"deleted": pid}


@app.post("/api/projects/{pid}/attach", dependencies=[Depends(auth)])
async def attach_to_project(pid: str, request: Request) -> JSONResponse:
    body = await request.json()
    items = body.get("items")
    if not isinstance(items, list):
        # tolerate a single {type, ref} body
        items = [{"type": body.get("type"), "ref": body.get("ref")}]
    result = projects.attach_many(pid, items)
    if not result.get("ok"):
        raise HTTPException(404, "No such project")
    return JSONResponse(result)


@app.post("/api/projects/{pid}/detach", dependencies=[Depends(auth)])
async def detach_from_project(pid: str, request: Request) -> JSONResponse:
    body = await request.json()
    ok = projects.detach(pid, body.get("type"), body.get("ref"))
    return JSONResponse({"ok": ok})


# --------------------------------------------------------------------------- #
# Voice enrollment + assistant + rename
# --------------------------------------------------------------------------- #
@app.get("/api/voiceprints", dependencies=[Depends(auth)])
def list_voiceprints() -> dict:
    from .pipeline import voiceid
    return {"enrolled": voiceid.enrolled_names()}


@app.post("/api/enroll", dependencies=[Depends(auth)])
async def enroll_voice(name: str = "", file: UploadFile = File(...)) -> dict:
    from .pipeline import voiceid
    name = (name or "Me").strip()
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    tmp = settings.data_path / "enroll_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"enroll{Path(file.filename or 'a.wav').suffix.lower() or '.wav'}"
    path.write_bytes(data)
    ok = voiceid.enroll(str(path), name)
    if not ok:
        raise HTTPException(400, "Could not read a clear voice sample (need ~30s of speech)")
    return {"ok": True, "name": name, "enrolled": voiceid.enrolled_names()}


@app.post("/api/recordings/{rec_id}/chat", dependencies=[Depends(auth)])
async def chat(rec_id: str, request: Request) -> dict:
    body = await request.json()
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not message:
        raise HTTPException(400, "message required")
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "Not found")
    result = await asyncio.to_thread(assistant.respond, rec, message, history)
    applied = []
    for e in result.get("edits", []):
        src, dst = (e.get("from") or "").strip(), (e.get("to") or "").strip()
        if src and dst and src != dst:
            _rename_in(rec, src, dst)
            directory.record_correction(src, dst, rec)
            applied.append({"from": src, "to": dst})
    if applied:
        storage.save(rec)
    return {"answer": result["answer"], "quotes": result["quotes"], "applied_edits": applied}


@app.post("/api/recordings/{rec_id}/rename", dependencies=[Depends(auth)])
async def rename_person(rec_id: str, request: Request) -> dict:
    body = await request.json()
    src = (body.get("from") or "").strip()
    dst = (body.get("to") or "").strip()
    if not src or not dst:
        raise HTTPException(400, "Both 'from' and 'to' are required")
    rec = storage.get(rec_id)
    if not rec:
        raise HTTPException(404, "Not found")
    _rename_in(rec, src, dst)
    directory.record_correction(src, dst, rec)
    storage.save(rec)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# CRM board — the orionscrm roster (clients / leads / network). orionscrm exports
# its rich roster into data/crm_export.json on each sync; we just read + serve it,
# so Lucid + the CRM are one life manager. Read-only.
# --------------------------------------------------------------------------- #
@app.get("/api/crm/board", dependencies=[Depends(auth)])
def crm_board() -> JSONResponse:
    import json as _json
    import time as _time
    from datetime import datetime as _dt
    from .integrations import crm_sync
    path = settings.crm_contacts_path.parent / "crm_export.json"
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {"generated_at": "", "stats": {}, "contacts": [], "missing": True}
    age = None
    try:
        age = max(0.0, (_time.time() - _dt.fromisoformat(data.get("generated_at")).timestamp()) / 60)
    except Exception:
        try:
            age = max(0.0, (_time.time() - path.stat().st_mtime) / 60)
        except Exception:
            age = None
    data["age_min"] = round(age, 1) if age is not None else None
    data["owner_name"] = settings.owner_name        # personalizes the Brief greeting
    data["refreshing"] = crm_sync.status().get("running", False)
    data["can_refresh"] = crm_sync.available()
    return JSONResponse(data)


@app.post("/api/crm/board/refresh", dependencies=[Depends(auth)])
def crm_board_refresh() -> JSONResponse:
    """Run the orionscrm sync in the background to refresh the roster (export JSON)."""
    from .integrations import crm_sync
    started = crm_sync.refresh_async()
    return JSONResponse({"started": started, "running": crm_sync.status().get("running", False),
                         "available": crm_sync.available()})


@app.post("/api/crm/board/override", dependencies=[Depends(auth)])
async def crm_board_override(request: Request) -> JSONResponse:
    """Promote/Lead/Remove a contact from Lucid's review queue — writes a sticky orionscrm
    override, then refreshes so the change shows. body: {email, action: promote|lead|remove}."""
    from .integrations import crm_sync
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    action = (body.get("action") or "").strip()
    if not email or action not in ("promote", "lead", "remove"):
        return JSONResponse({"ok": False, "error": "bad request"}, status_code=400)
    ok = await asyncio.to_thread(crm_sync.set_override, email, action)
    if ok:
        crm_sync.refresh_async()
    return JSONResponse({"ok": ok, "refreshing": crm_sync.status().get("running", False)})


@app.post("/api/crm/board/act", dependencies=[Depends(auth)])
async def crm_board_act(request: Request) -> JSONResponse:
    """One-tap queue actions: 'send' fires the prepared Gmail draft for a contact via
    orionscrm (a real send, not a clipboard copy). body: {email, action: send|done|draft}."""
    from .integrations import crm_sync
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    action = (body.get("action") or "").strip()
    if not email or action not in ("send", "done", "draft"):
        return JSONResponse({"ok": False, "detail": "bad request"}, status_code=400)
    ok, msg = await asyncio.to_thread(crm_sync.act, email, action)
    if ok:
        crm_sync.refresh_async()          # surface the cleared reply-owed state
    return JSONResponse({"ok": ok, "detail": msg})


@app.get("/api/system/health", dependencies=[Depends(auth)])
def system_health() -> JSONResponse:
    """One honest place that says whether the pipelines are alive (Settings card)."""
    import time as _time
    from .ingest import plaud_cloud
    from .integrations import crm_sync
    out = {"plaud": plaud_cloud.health()}
    try:
        exp = settings.crm_contacts_path.parent / "crm_export.json"
        out["crm"] = {"available": crm_sync.available(),
                      "export_age_min": round(max(0.0, (_time.time() - exp.stat().st_mtime) / 60), 1)}
    except Exception:
        out["crm"] = {"available": crm_sync.available(), "export_age_min": None}
    try:
        out["telegram"] = {"enabled": bool(settings.telegram_enabled and settings.telegram_bot_token)}
    except Exception:
        out["telegram"] = {"enabled": False}
    try:
        out["tunnel"] = {"url": settings.current_public_url() or ""}
    except Exception:
        out["tunnel"] = {"url": ""}
    return JSONResponse(out)


@app.post("/api/crm/board/merge", dependencies=[Depends(auth)])
async def crm_board_merge(request: Request) -> JSONResponse:
    """Merge a detected duplicate contact into its primary. body: {primary, duplicate}."""
    from .integrations import crm_sync
    b = await request.json()
    primary = (b.get("primary") or "").strip().lower()
    dup = (b.get("duplicate") or "").strip().lower()
    if not primary or not dup or primary == dup:
        return JSONResponse({"ok": False, "error": "bad request"}, status_code=400)
    ok = await asyncio.to_thread(crm_sync.merge_contacts, primary, dup)
    if ok:
        crm_sync.refresh_async()
    return JSONResponse({"ok": ok, "refreshing": crm_sync.status().get("running", False)})


# --------------------------------------------------------------------------- #
# Action layer — turn spoken intents into real calendar events + sent email
# --------------------------------------------------------------------------- #
@app.post("/api/actions/calendar", dependencies=[Depends(auth)])
async def action_calendar(request: Request) -> JSONResponse:
    """Create a real Google Calendar event. body: {title,start,end,description?,location?,attendees?}."""
    from .integrations import actions
    return JSONResponse(await asyncio.to_thread(actions.create_event, await request.json()))


@app.post("/api/actions/email", dependencies=[Depends(auth)])
async def action_email(request: Request) -> JSONResponse:
    """Send a real email through the connected account. body: {to,subject,body,html?}."""
    from .integrations import actions
    return JSONResponse(await asyncio.to_thread(actions.send_email, await request.json()))


@app.post("/api/actions/draft", dependencies=[Depends(auth)])
async def action_draft(request: Request) -> JSONResponse:
    """Draft an email body from note context. body: {context,intent,owner?,to_name?}."""
    from .integrations import actions
    b = await request.json()
    return JSONResponse(await asyncio.to_thread(
        actions.draft_email, b.get("context", ""), b.get("intent", ""),
        b.get("owner", "") or settings.owner_name, b.get("to_name", "")))


# --------------------------------------------------------------------------- #
# Web UI — setup gate + SPA (served last so /api/* wins)
# --------------------------------------------------------------------------- #
_WEB_VER = {"sig": -1.0, "ver": "1"}


def _asset_version() -> str:
    """Cache-busting token that changes whenever app.js/styles.css change. Cloudflare's edge
    caches .js/.css for hours regardless of our no-cache header, so versioned URLs (a fresh
    URL per deploy) are the only reliable way to push new UI to already-loaded browsers."""
    try:
        import hashlib
        sig = 0.0
        for n in ("app.js", "styles.css"):
            p = WEB_DIR / n
            if p.exists():
                sig += p.stat().st_mtime
        if sig != _WEB_VER["sig"]:
            _WEB_VER["sig"] = sig
            _WEB_VER["ver"] = hashlib.md5(str(sig).encode()).hexdigest()[:8]
        return _WEB_VER["ver"]
    except Exception:
        return "1"


def _spa() -> HTMLResponse | FileResponse:
    """Serve the shell with versioned asset URLs so every deploy busts the browser/edge cache.
    The shell itself is always revalidated (no-cache), so the new version always reaches clients."""
    try:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        v = _asset_version()
        html = html.replace('href="/styles.css"', f'href="/styles.css?v={v}"')
        html = html.replace('src="/app.js"', f'src="/app.js?v={v}"')
        return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})
    except Exception:
        return FileResponse(WEB_DIR / "index.html")


def _setup_page() -> FileResponse:
    return FileResponse(WEB_DIR / "setup.html")


@app.get("/")
def root():
    return _spa() if settings.is_configured else RedirectResponse("/setup")


@app.get("/setup")
def setup_page():
    return _setup_page()


@app.get("/r/{rec_id}")
@app.get("/people/{key}")
@app.get("/ventures/{vid}")
@app.get("/lucid")
@app.get("/lucid/{seg}")
@app.get("/crm/{email}")
@app.get("/journal")
@app.get("/notes")
@app.get("/search")
@app.get("/settings")
@app.get("/people")
@app.get("/directory")
@app.get("/ventures")
@app.get("/crm")
@app.get("/review")
def spa_routes(rec_id: str = "", key: str = "", vid: str = "", seg: str = "", email: str = ""):
    return _spa() if settings.is_configured else RedirectResponse("/setup")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=False), name="web")


def main() -> None:
    import uvicorn

    uvicorn.run("server.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
