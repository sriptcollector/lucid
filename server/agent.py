"""TALK TO A PROJECT — run a real Claude Code session on a project repo.

From Lucid you describe a change; this runs the actual Claude Code CLI
(headless, full tools) IN THAT PROJECT'S REPOSITORY on THIS computer, so it
has every capability an interactive Claude Code session would. The session
edits the repo; deploy is a separate, gated step (deploy.py).

WHY LOCAL-ONLY. The repos, the Claude Code CLI, and Orion's Max
subscription all live on this machine, so the session must run here. Lucid
already runs here and is reached through the named tunnel, so "hosted on
this computer" is the only architecture that works - there is no cloud
worker. The request arrives over the authenticated API; the work happens
locally.

MODEL. Opus 4.8 (--model claude-opus-4-8), as requested.

PERMISSIONS. --permission-mode acceptEdits: the session may edit files
autonomously (that is the whole point) but does not get blanket bypass of
every check. A job runs in a background thread with a hard timeout; its
full transcript and the resulting `git status` are captured.

SECURITY NOTE. This endpoint runs code-editing sessions on the host,
triggered through the public tunnel. It is gated behind the app bearer
token (same as every other write endpoint). Anyone with that token can
drive it - treat the token like an SSH key.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

from .config import settings
from . import businesses

MODEL = "claude-opus-4-8"
JOB_TIMEOUT = 1800            # 30 min hard cap per session
DEPLOY_TIMEOUT = 900
HISTORY_FILE = "agent_jobs.json"
_URL_RE = re.compile(
    r"https?://[^\s'\"]+\.(?:vercel\.app|pages\.dev|workers\.dev|netlify\.app|"
    r"github\.io|fly\.dev|onrender\.com|railway\.app|surge\.sh)[^\s'\"]*")

_lock = threading.RLock()
_jobs: dict[str, dict] = {}   # job_id -> record (in-memory, live logs)


def _claude() -> str | None:
    import shutil
    for c in (os.path.join(os.path.expanduser("~"), ".local", "bin",
                           "claude.exe"), "claude.exe", "claude"):
        if os.path.isabs(c) and os.path.exists(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


WORKSPACE = os.path.join(os.path.expanduser("~"), "lucid-workspaces")


def ensure_local(proj: dict, log=None) -> str:
    """Return a local checkout path for a project, cloning it from GitHub on
    first use so ANY repo can be talked to, not just ones already on disk.

    The workspace persists (a plain `git pull` on later changes), and gh's
    git credential helper handles private repos."""
    cwd = proj.get("cwd") or ""
    if cwd and os.path.isdir(cwd):
        return cwd
    url = proj.get("url") or ""
    name = proj.get("orig_name") or proj.get("name") or ""
    if not url or not name:
        return ""
    os.makedirs(WORKSPACE, exist_ok=True)
    dest = os.path.join(WORKSPACE, name)
    import subprocess
    try:
        if os.path.isdir(os.path.join(dest, ".git")):
            p = subprocess.run(["git", "pull", "--ff-only"], cwd=dest,
                               capture_output=True, text=True, timeout=180)
            if p.returncode != 0 and log is not None:
                log[0] += "[agent] git pull skipped: %s\n" % (
                    (p.stderr or "").strip()[:200])
        else:
            if log is not None:
                log[0] += "[agent] cloning %s …\n" % name
            p = subprocess.run(["git", "clone", "--depth", "50", url, dest],
                               capture_output=True, text=True, timeout=600)
            if p.returncode != 0 and log is not None:
                # surface the real reason (private repo auth, bad url, …)
                log[0] += "[agent] clone failed: %s\n" % (
                    (p.stderr or "").strip()[-300:])
        return dest if os.path.isdir(dest) else ""
    except Exception as e:
        if log is not None:
            log[0] += "[agent] checkout error: %s\n" % str(e)[:200]
        return dest if os.path.isdir(dest) else ""


def _git(cwd: str, *args) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=30)
        return (p.stdout or "").strip()
    except Exception:
        return ""


def project_for(pid: str) -> dict | None:
    for p in businesses.all_projects():
        if p["id"] == pid:
            return p
    return None


def _default_title(text: str) -> str:
    t = " ".join((text or "").split())
    return (t[:57] + "…") if len(t) > 58 else (t or "Session")


def _persist(rec: dict) -> None:
    try:
        path = os.path.join(str(settings.data_path), HISTORY_FILE)
        hist = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        slim = {k: rec[k] for k in ("id", "kind", "project", "project_name",
                                    "title", "status", "started", "finished",
                                    "changed", "prod", "committed",
                                    "cli_session") if k in rec}
        # keep enough of the session to re-open it as a chat after a restart:
        # the request (the "user message") and the tail of the terminal log.
        slim["request"] = (rec.get("request") or "")[:4000]
        slim["log"] = (rec.get("log") or "")[-6000:]
        slim["url"] = rec.get("url_out", "") if rec.get("kind") == "deploy" else ""
        hist = [h for h in hist if h.get("id") != rec["id"]]
        hist.insert(0, slim)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hist[:200], f, indent=2)
    except Exception:
        pass


def rename(job_id: str, title: str) -> dict:
    """Rename a session (live or finished) — powers the chats bar."""
    title = " ".join((title or "").split())[:80]
    if not title:
        return {"ok": False, "error": "empty title"}
    found = False
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["title"] = title
            found = True
    try:
        path = os.path.join(str(settings.data_path), HISTORY_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
            for hh in hist:
                if hh.get("id") == job_id:
                    hh["title"] = title
                    found = True
            with open(path, "w", encoding="utf-8") as f:
                json.dump(hist, f, indent=2)
    except Exception:
        pass
    if not found:
        return {"ok": False, "error": "no such job"}
    return {"ok": True, "title": title}


_PAGES_CACHE = {"ts": 0.0, "projects": []}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _cf_pages_projects() -> list[str]:
    """Names of the existing Cloudflare Pages projects on the logged-in
    account, cached ~5 min. Lets us auto-deploy a repo to its already-set-up
    Pages project without the user configuring anything."""
    if time.time() - _PAGES_CACHE["ts"] < 300 and _PAGES_CACHE["projects"]:
        return _PAGES_CACHE["projects"]
    names: list[str] = []
    try:
        cmd = ["npx", "-y", "wrangler", "pages", "project", "list"]
        if sys.platform == "win32":
            cmd = subprocess.list2cmdline(cmd)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           shell=(sys.platform == "win32"))
        for line in (p.stdout or "").splitlines():
            # table rows: │ project-name │ domains │ …
            cells = [c.strip() for c in line.split("│") if c.strip()]
            if cells and re.match(r"^[a-z0-9][a-z0-9-]*$", cells[0]) \
                    and cells[0] not in ("Project Name",):
                names.append(cells[0])
    except Exception:
        pass
    if names:
        _PAGES_CACHE["ts"] = time.time()
        _PAGES_CACHE["projects"] = names
    return names


def _match_pages_project(orig_name: str) -> str:
    """Find the existing Pages project that belongs to this repo by matching
    normalized names (e.g. 'cratertv-tmdb-…' -> 'crater-tv',
    'orion-jones-com-personal-site' -> 'orion-jones'). Longest prefix wins."""
    n = _norm(orig_name)
    if not n:
        return ""
    best = ""
    for p in _cf_pages_projects():
        np = _norm(p)
        if np and (n.startswith(np) or np in n):
            if len(np) > len(_norm(best)):
                best = p
    return best


def _static_publish_dir(cwd: str) -> str:
    """Where the deployable static site lives, relative to the repo root.
    A prebuilt output dir if present, else the root when it has an index/worker."""
    for d in ("dist", "build", "public", "out", "_site"):
        p = os.path.join(cwd, d)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "index.html")):
            return d
    if os.path.exists(os.path.join(cwd, "index.html")) or \
            os.path.exists(os.path.join(cwd, "_worker.js")):
        return "."
    return ""


def detect_deploy(cwd: str, orig_name: str = "") -> dict:
    """How does this repo deploy, and the preview/production commands.

    Detected from repo markers (most specific first):
      Vercel        .vercel/ or vercel.json      -> `vercel` / `--prod`
      Cloudflare    wrangler.toml/.jsonc         -> wrangler (Workers or Pages)
      Cloudflare    _worker.js / static site     -> Pages direct-upload, auto-
                    (+ matching Pages project)      mapped to the repo's project
    Both flows are preview-first: production is only ever the second,
    explicit command, never a side effect of the change."""
    if not cwd or not os.path.isdir(cwd):
        return {"method": "none", "note": "no local folder"}
    has = lambda n: os.path.exists(os.path.join(cwd, n))
    if has(".vercel") or has("vercel.json"):
        return {"method": "vercel", "ready": has(".vercel"),
                "preview": ["vercel", "deploy", "--yes"],
                "prod": ["vercel", "deploy", "--prod", "--yes"],
                "note": "" if has(".vercel") else
                "run `vercel link` in the repo once so deploys are non-interactive"}
    wr = "wrangler.toml" if has("wrangler.toml") else (
        "wrangler.jsonc" if has("wrangler.jsonc") else "")
    if wr:
        pages = False
        try:
            with open(os.path.join(cwd, wr), encoding="utf-8") as f:
                pages = "pages_build_output_dir" in f.read()
        except OSError:
            pass
        if pages:
            return {"method": "cloudflare-pages", "ready": True,
                    "preview": ["npx", "-y", "wrangler", "pages", "deploy",
                                "--branch", "preview"],
                    "prod": ["npx", "-y", "wrangler", "pages", "deploy",
                             "--branch", "production"], "note": ""}
        return {"method": "cloudflare-workers", "ready": True,
                "preview": ["npx", "-y", "wrangler", "versions", "upload"],
                "prod": ["npx", "-y", "wrangler", "deploy"], "note": ""}
    # No config file, but it's a static / _worker.js site → Cloudflare Pages
    # direct upload. Auto-map to the repo's existing Pages project.
    pub = _static_publish_dir(cwd)
    if pub:
        proj = _match_pages_project(orig_name)
        if proj:
            base = ["npx", "-y", "wrangler", "pages", "deploy", pub,
                    "--project-name", proj]
            return {"method": "cloudflare-pages", "ready": True,
                    "preview": base + ["--branch", "preview"],
                    "prod": base + ["--branch", "production"],
                    "note": "→ Cloudflare Pages project '%s'" % proj}
    # No standard config. NEVER a dead end: a Claude Code session works out
    # the deploy (creates the Pages project, builds first, whatever it takes)
    # and runs it — the same workaround a person at the terminal would find.
    return {"method": "agent", "ready": True,
            "note": "no standard config, so a Claude Code session figures "
                    "out the deploy and runs it"}


def request_deploy(pid: str, prod: bool = False) -> dict:
    proj = project_for(pid)
    if not proj or not (proj.get("cwd") or proj.get("url")):
        return {"ok": False, "error": "project has no repo to deploy"}
    job_id = uuid.uuid4().hex[:12]
    rec = {"id": job_id, "kind": "deploy", "project": pid,
           "project_name": proj["name"], "cwd": proj.get("cwd", ""),
           "url": proj.get("url", ""), "orig_name": proj.get("orig_name", ""),
           "request": ("Production deploy" if prod else "Preview deploy"),
           "title": ("Production deploy" if prod else "Preview deploy"),
           "cmd": None, "prod": prod,
           "status": "queued", "log": "", "changed": [], "url_out": "",
           "started": time.time(), "finished": 0.0}
    with _lock:
        _jobs[job_id] = rec
    threading.Thread(target=_run, args=(job_id,),
                     name="lucid-deploy-%s" % job_id, daemon=True).start()
    return {"ok": True, "job": job_id}


def _run_deploy(rec: dict) -> None:
    rec["status"] = "running"
    # clone-on-demand, then detect the deploy command from the real files
    cwd = rec.get("cwd") or ""
    if not (cwd and os.path.isdir(cwd)):
        holder = [rec["log"]]
        cwd = ensure_local({"cwd": cwd, "url": rec.get("url", ""),
                            "orig_name": rec.get("orig_name", "")}, holder)
        rec["log"] = holder[0]
        rec["cwd"] = cwd
    if not cwd or not os.path.isdir(cwd):
        rec["status"] = "error"
        rec["log"] += "\n[deploy] could not obtain a local checkout"
        rec["finished"] = time.time()
        _persist(rec)
        return
    dep = detect_deploy(cwd, rec.get("orig_name", ""))
    rec["cmd"] = dep.get("prod" if rec.get("prod") else "preview")
    rec["request"] += " (" + dep.get("method", "?") + ")"
    if not rec["cmd"]:
        return _agent_deploy(rec, dep)
    try:
        # On Windows, `vercel`/`npx`/`wrangler` are .cmd shims that a bare
        # shell=False exec can't resolve (FileNotFoundError). Run them through
        # the shell with a properly quoted command line so deploys actually work.
        if sys.platform == "win32":
            cmd = subprocess.list2cmdline(rec["cmd"])
            p = subprocess.run(cmd, cwd=rec["cwd"], capture_output=True,
                               text=True, timeout=DEPLOY_TIMEOUT, shell=True)
        else:
            p = subprocess.run(rec["cmd"], cwd=rec["cwd"], capture_output=True,
                               text=True, timeout=DEPLOY_TIMEOUT, shell=False)
        rec["log"] += (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        m = _URL_RE.search(rec["log"])
        if m:
            rec["url_out"] = m.group(0)
        rec["status"] = "done" if p.returncode == 0 else "error"
        if p.returncode != 0:
            rec["log"] += "\n[deploy] exit %d" % p.returncode
    except subprocess.TimeoutExpired:
        rec["status"] = "timeout"
        rec["log"] += "\n[deploy] exceeded %ds" % DEPLOY_TIMEOUT
    except Exception as e:
        rec["status"] = "error"
        rec["log"] += "\n[deploy] %s" % str(e)[:300]
    rec["finished"] = time.time()
    _persist(rec)


def _agent_deploy(rec: dict, dep: dict) -> None:
    """No (working) deploy config — do what a person at the terminal would:
    open a Claude Code session in the repo and have it work out the deploy,
    set anything up that's missing, and run it. The old behaviour was a dead
    'no Vercel or Cloudflare config found' error; this is the workaround."""
    exe = _claude()
    if not exe:
        rec["status"] = "error"
        rec["log"] += "\n[deploy] %s (and the Claude CLI isn't available to " \
                      "work around it)" % (dep.get("note") or "no deploy method")
        rec["finished"] = time.time()
        _persist(rec)
        return
    prod = bool(rec.get("prod"))
    name = rec.get("orig_name") or rec.get("project_name") or "this-repo"
    prompt = (
        "You are deploying the repository in the current working directory. "
        "Goal: get it live as a %s deploy. There is no committed deploy "
        "config, so figure out the right way to deploy it and DO IT. Do not "
        "stop at analysis.\n\n"
        "Work through these in order:\n"
        "1. If a .vercel folder or vercel.json exists, use the Vercel CLI "
        "(`vercel deploy --yes`%s).\n"
        "2. Check the logged-in Cloudflare account for an existing Pages "
        "project that matches this repo (`npx -y wrangler pages project "
        "list`); if one fits, deploy to it with `npx -y wrangler pages "
        "deploy <dir> --project-name <project> --branch %s`.\n"
        "3. If this is a static site or has a build script: install deps and "
        "build if needed (npm install / npm run build), create a Cloudflare "
        "Pages project named like the repo (e.g. `npx -y wrangler pages "
        "project create %s --production-branch=main`), then deploy the "
        "output directory to it with the branch flag above.\n"
        "4. If none of that fits (server app, worker, etc.), pick the "
        "simplest platform already authenticated on this machine (wrangler / "
        "vercel / gh) and make it work.\n\n"
        "Rules: a preview deploy must NOT touch production. Do not push to "
        "git. When done, print the final deployed URL alone on the last "
        "line." % (
            "PRODUCTION" if prod else "PREVIEW",
            ", add `--prod` since this is production" if prod else "",
            "production" if prod else "preview",
            re.sub(r"[^a-z0-9-]", "-", name.lower())[:50].strip("-") or "site"))
    rec["log"] += ("[deploy] no standard config. Starting a Claude Code "
                   "session to work out the deploy…\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ANTHROPIC_")
           and not k.startswith("CLAUDE_CODE_USE_")}
    try:
        rc = _stream_claude(rec, exe, prompt, rec["cwd"], env,
                            permission_mode="bypassPermissions")
        rec["status"] = "done" if rc in (0, None) else "error"
        if rc not in (0, None):
            rec["log"] += "\n[deploy] exit %s" % rc
    except _JobTimeout:
        rec["status"] = "timeout"
        rec["log"] += "\n[deploy] session exceeded %ds and was stopped" % JOB_TIMEOUT
    except Exception as e:
        rec["status"] = "error"
        rec["log"] += "\n[deploy] %s" % str(e)[:300]
    if rec.get("stopped"):
        rec["status"] = "stopped"
        rec["log"] += "\n[deploy] stopped by you"
    urls = _URL_RE.findall(rec["log"])
    if urls:
        rec["url_out"] = urls[-1].rstrip(".,)")
    else:
        # the session was told to end with the URL alone on the last line
        m = re.search(r"(?im)^\s*(https?://\S+)\s*$", rec["log"][-1500:])
        if m:
            rec["url_out"] = m.group(1).rstrip(".,)")
    if rec["status"] == "done" and not rec["url_out"]:
        rec["log"] += "\n[deploy] finished, but no URL was printed (check the log above)"
    rec["finished"] = time.time()
    _persist(rec)


class _JobTimeout(Exception):
    pass


def _tool_line(name: str, inp: dict) -> str:
    """One short human line describing a tool call, for the live log."""
    inp = inp or {}
    if name in ("Edit", "Write", "Read", "NotebookEdit"):
        return "→ %s %s" % (name, inp.get("file_path") or inp.get("path") or "")
    if name == "Bash":
        cmd = (inp.get("command") or "").replace("\n", " ")
        return "→ Bash: %s" % (cmd[:120])
    if name in ("Grep", "Glob"):
        return "→ %s %s" % (name, inp.get("pattern") or "")
    if name == "TodoWrite":
        return "→ planning…"
    return "→ %s" % name


def _stream_claude(rec, exe, prompt, cwd, env, permission_mode="acceptEdits",
                   resume=""):
    """Run the Claude Code CLI with streaming JSON output and append readable
    progress to rec['log'] LIVE, so the UI shows work as it happens instead of
    a frozen 'cloning…' until the whole session finishes. Returns exit code.

    resume: a CLI session id to continue, so a chat reply keeps the context
    of the previous session instead of starting cold."""
    args = [exe, "-p", "--model", MODEL, "--permission-mode", permission_mode,
            "--output-format", "stream-json", "--verbose"]
    if resume:
        args += ["--resume", resume]
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=cwd, env=env)
    rec["_proc"] = proc   # live handle so stop() can kill the session
    timed_out = {"v": False}

    def _kill():
        timed_out["v"] = True
        try:
            proc.kill()
        except Exception:
            pass

    killer = threading.Timer(JOB_TIMEOUT, _kill)
    killer.daemon = True
    killer.start()
    try:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except Exception:
            pass
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                # the CLI session id — lets the next chat message in this
                # project RESUME this session with its full context
                sid = ev.get("session_id") or ""
                if sid:
                    rec["cli_session"] = sid
            if t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    bt = b.get("type")
                    if bt == "text" and (b.get("text") or "").strip():
                        rec["log"] += b["text"].rstrip() + "\n"
                    elif bt == "tool_use":
                        rec["log"] += _tool_line(b.get("name", ""),
                                                 b.get("input", {})) + "\n"
            elif t == "result":
                r = ev.get("result")
                # only append the final summary if it isn't already the tail
                # (the model often emits it once as text and again as result)
                if r and isinstance(r, str) and r.strip() and r.strip() not in rec["log"][-1200:]:
                    rec["log"] += "\n" + r.strip() + "\n"
        proc.wait()
    finally:
        killer.cancel()
        rec.pop("_proc", None)
    if timed_out["v"]:
        raise _JobTimeout()
    return proc.returncode


def stop(job_id: str) -> dict:
    """Kill a running session — the ⏹ Stop button. The job thread notices the
    dead process, marks the record 'stopped', and persists it as usual."""
    with _lock:
        rec = _jobs.get(job_id)
    if not rec:
        return {"ok": False, "error": "no such live job"}
    if rec.get("finished"):
        return {"ok": False, "error": "already finished"}
    rec["stopped"] = True
    p = rec.get("_proc")
    if p is None:
        return {"ok": False, "error": "this job can't be stopped mid-run"}
    try:
        p.kill()
    except Exception:
        pass
    rec["log"] += "\n[agent] stop requested; ending the session…"
    return {"ok": True}


def delete_job(job_id: str) -> dict:
    """Remove a finished session from the chats history."""
    with _lock:
        rec = _jobs.get(job_id)
        if rec and not rec.get("finished"):
            return {"ok": False, "error": "stop it before deleting"}
        _jobs.pop(job_id, None)
    try:
        path = os.path.join(str(settings.data_path), HISTORY_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
            hist = [hh for hh in hist if hh.get("id") != job_id]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(hist, f, indent=2)
    except Exception:
        pass
    return {"ok": True}


def _run(job_id: str) -> None:
    rec = _jobs[job_id]
    if rec.get("kind") == "deploy":
        return _run_deploy(rec)
    # clone-on-demand: resolve (or fetch) a local checkout
    cwd = rec.get("cwd") or ""
    if not (cwd and os.path.isdir(cwd)):
        rec["status"] = "running"
        holder = [rec["log"]]
        cwd = ensure_local({"cwd": cwd, "url": rec.get("url", ""),
                            "orig_name": rec.get("orig_name", "")}, holder)
        rec["log"] = holder[0]
        rec["cwd"] = cwd
    exe = _claude()
    if not exe or not cwd or not os.path.isdir(cwd):
        rec["status"] = "error"
        rec["log"] += "\n[agent] could not obtain a local checkout of the repo"
        rec["finished"] = time.time()
        _persist(rec)
        return
    before = _git(cwd, "rev-parse", "HEAD")
    before_status = set(_git(cwd, "status", "--porcelain").splitlines())
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ANTHROPIC_")
           and not k.startswith("CLAUDE_CODE_USE_")}
    prompt = rec.get("prompt") or rec["request"]
    rec["status"] = "running"
    try:
        rc = _stream_claude(rec, exe, prompt, cwd, env,
                            resume=rec.get("resume", ""))
        if rc not in (0, None) and not rec.get("stopped"):
            rec["log"] += "\n[agent] exit %s" % rc
    except _JobTimeout:
        rec["status"] = "timeout"
        rec["log"] += "\n[agent] session exceeded %ds and was stopped" % JOB_TIMEOUT
    except Exception as e:
        rec["status"] = "error"
        rec["log"] += "\n[agent] %s" % str(e)[:300]
    if rec.get("stopped"):
        rec["status"] = "stopped"
        rec["log"] += "\n[agent] session stopped by you"
    # what THIS session changed = working-tree delta vs the pre-session state
    # (not every pre-existing uncommitted file)
    after_status = _git(cwd, "status", "--porcelain").splitlines()
    changed = [ln[3:] for ln in after_status
               if ln.strip() and ln not in before_status]
    after = _git(cwd, "rev-parse", "HEAD")
    rec["committed"] = bool(before and after and before != after)
    # if the session COMMITTED, git status is clean — recover the file list from
    # the commit diff so the UI doesn't show "committed, 0 files".
    if rec["committed"]:
        diff = _git(cwd, "diff", "--name-only", before, after).splitlines()
        for f in diff:
            if f and f not in changed:
                changed.append(f)
    rec["changed"] = changed[:100]
    if rec["status"] == "running":
        rec["status"] = "done"
    # mark the notes that fed this change as read — but ONLY now that it
    # actually succeeded (queue-time marking silently drops updates on failure)
    seen_ids = rec.get("seen_ids")
    if seen_ids and rec["status"] == "done":
        try:
            businesses.mark_copied(rec.get("project", ""), seen_ids)
        except Exception:
            pass
    rec["finished"] = time.time()
    _persist(rec)


def _save_screenshot(job_id: str, image: str) -> str:
    """Decode a base64 data-URL screenshot to a file the agent can read."""
    if not image:
        return ""
    import base64
    try:
        b64 = image.split(",", 1)[1] if image.startswith("data:") else image
        raw = base64.b64decode(b64)
        d = os.path.join(WORKSPACE, "_screenshots")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, job_id + ".png")
        with open(path, "wb") as f:
            f.write(raw)
        return path
    except Exception:
        return ""


def request_change(pid: str, text: str, image: str = "",
                   seen_ids: list | None = None, preamble: str = "",
                   resume: str = "") -> dict:
    """preamble: extra context prepended to the CLI prompt (e.g. the product
    playbook) but NOT shown as the chat bubble. resume: CLI session id to
    continue, so a reply keeps the previous session's context."""
    proj = project_for(pid)
    if not proj:
        return {"ok": False, "error": "unknown project"}
    if not (proj.get("cwd") or proj.get("url")):
        return {"ok": False, "error": "this project has no repo to change"}
    job_id = uuid.uuid4().hex[:12]
    text = text.strip()
    shot = _save_screenshot(job_id, image)
    prompt = text
    if shot:
        prompt += ("\n\nI attached a screenshot showing what I mean. Look at "
                   "the image file at: %s" % shot)
    rec = {"id": job_id, "project": pid, "project_name": proj["name"],
           "cwd": proj.get("cwd", ""), "url": proj.get("url", ""),
           "orig_name": proj.get("orig_name", ""),
           "request": text + ("\n\n📎 screenshot attached" if shot else ""),
           "prompt": (preamble or "") + prompt,
           "resume": re.sub(r"[^0-9a-zA-Z-]", "", resume or "")[:64],
           "title": _default_title(text),
           "status": "queued", "log": "", "changed": [],
           "seen_ids": list(seen_ids or []),
           "started": time.time(), "finished": 0.0}
    with _lock:
        _jobs[job_id] = rec
        # keep memory bounded
        if len(_jobs) > 40:
            for k in sorted(_jobs, key=lambda x: _jobs[x]["started"])[:10]:
                if _jobs[k].get("finished"):
                    _jobs.pop(k, None)
    threading.Thread(target=_run, args=(job_id,),
                     name="lucid-agent-%s" % job_id, daemon=True).start()
    return {"ok": True, "job": job_id}


def job(job_id: str) -> dict | None:
    rec = _jobs.get(job_id)
    if not rec:
        # finished before a restart: recover the session from persisted
        # history so old chats still open (request + log tail survive there)
        try:
            path = os.path.join(str(settings.data_path), HISTORY_FILE)
            with open(path, encoding="utf-8") as f:
                for hh in json.load(f):
                    if hh.get("id") == job_id:
                        return hh
        except Exception:
            pass
        return None
    out = {k: rec[k] for k in ("id", "kind", "project", "project_name",
                               "request", "title", "status", "log", "changed",
                               "committed", "prod", "started",
                               "finished", "cli_session") if k in rec}
    # deploy result URL (kept separate from the input GitHub url)
    out["url"] = rec.get("url_out", "") if rec.get("kind") == "deploy" else rec.get("url", "")
    return out


def history(pid: str | None = None, limit: int = 30,
            include_log: bool = False) -> list:
    try:
        path = os.path.join(str(settings.data_path), HISTORY_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        else:
            hist = []
    except Exception:
        hist = []
    # merge any live (unfinished) jobs so the UI sees them immediately.
    # snapshot the keys under the lock first — iterating _jobs live races with
    # request_change/_run mutating it (dictionary changed size during iteration).
    with _lock:
        keys = list(_jobs)
    live = [job(k) for k in keys]
    seen = {h.get("id") for h in hist}
    for lv in live:
        if lv and lv["id"] not in seen:
            e = {k: lv.get(k) for k in
                 ("id", "kind", "project", "project_name", "title",
                  "status", "started", "finished", "changed", "prod",
                  "committed", "url", "cli_session")}
            e["request"] = (lv.get("request") or "")[:4000]
            e["log"] = (lv.get("log") or "")[-6000:]
            hist.insert(0, e)
    if pid:
        hist = [h for h in hist if h.get("project") == pid]
    hist = hist[:limit]
    if not include_log:
        hist = [{k: v for k, v in h.items() if k != "log"} for h in hist]
    return hist
