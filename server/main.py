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
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import setup_service, storage, tunnel
from .config import settings
from .ingest import intake, plaud_cloud, telegram_bot, watcher
from .models import Status
from .pipeline import analyze, assistant, directory, relationships, runner, ventures
from .pipeline.rename import rename_person as _rename_in

app = FastAPI(title="Lucid", version="1.0.0")

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lucid-pipe")
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
    intake.set_enqueue(lambda rec_id: _pool.submit(runner.process, rec_id))
    watcher.start()
    if settings.is_configured:
        start_runtime_services()


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
        "telegram_connected": bool(settings.telegram_enabled and settings.telegram_bot_token),
        "telegram_chat_known": _telegram_chat_known(),
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
        "tunnel_enabled", "whisper_model",
    }
    updates = {k: v for k, v in body.items() if k in allowed}
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
# Web UI — setup gate + SPA (served last so /api/* wins)
# --------------------------------------------------------------------------- #
def _spa() -> FileResponse:
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
@app.get("/search")
@app.get("/settings")
@app.get("/people")
@app.get("/directory")
@app.get("/ventures")
def spa_routes(rec_id: str = "", key: str = "", vid: str = ""):
    return _spa() if settings.is_configured else RedirectResponse("/setup")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=False), name="web")


def main() -> None:
    import uvicorn

    uvicorn.run("server.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
