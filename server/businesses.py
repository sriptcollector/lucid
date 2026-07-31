"""Businesses / projects — sort every note under the project it's about.

The projects are the user's own Claude Code projects (``~/.claude/projects``).
Their folder names are encoded working-directory paths and are often NOT an
accurate label, so each project's real identity is derived by DEEP-DIVING the
session data itself (the cwd, the first substantive prompt, any summary line).
A note is matched to at most one project by Claude, using that derived
identity; a note that clearly belongs to none is left Unsorted, and a project
the user never talks about simply never appears.

Copy tracking: each project remembers which notes have already been copied, so
the per-project "Copy new" action emits only the notes added since the last
copy and the dashboard can badge a project that has an uncopied update.

Everything is best-effort and cached; a failed read, a missing key, or an
Anthropic hiccup never raises into a request — it just leaves notes unsorted.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import threading
import time
from typing import Optional

from .config import settings

_lock = threading.RLock()

CC_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
CUSTOM_FILE = "custom_projects.json"
PROJECTS_CACHE = "claude_projects.json"
ASSIGN_CACHE = "note_business.json"
FOCUS_CACHE = "note_focus.json"
COPIED_FILE = "business_copied.json"
PROJECTS_TTL = 6 * 3600

# folders that are never a real project
_SKIP = ("scratchpad", "dummyproj", "handoffs", "-Temp-", "AppData-Local-Temp")


# ----------------------------------------------------------- json helpers
def _path(name: str) -> str:
    return str(settings.data_path / name)


def _load(name: str, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name: str, obj) -> None:
    try:
        tmp = _path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, _path(name))
    except OSError:
        pass


# ------------------------------------------------- discover CC projects
def _decode_name(folder: str) -> str:
    """Best-effort friendly name from an encoded project folder name."""
    for anchor in ("-Desktop-code-", "-Desktop-"):
        if anchor in folder:
            return folder.split(anchor, 1)[1].replace("-", " ").strip()
    return folder.split("-")[-1]


def _extract(pdir: str) -> dict:
    """Deep-dive a project's sessions for its real identity."""
    cwd = summary = first_user = ""
    files = sorted(glob.glob(os.path.join(pdir, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)
    newest = os.path.getmtime(files[0]) if files else 0.0
    for f in files[:5]:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if not cwd and d.get("cwd"):
                        cwd = d["cwd"]
                    if not summary and d.get("type") == "summary":
                        summary = str(d.get("summary", ""))[:240]
                    if not first_user and d.get("type") == "user":
                        m = d.get("message", {})
                        c = m.get("content") if isinstance(m, dict) else None
                        txt = ""
                        if isinstance(c, str):
                            txt = c
                        elif isinstance(c, list):
                            for b in c:
                                if isinstance(b, dict) and b.get("type") == "text":
                                    txt = b.get("text", "")
                                    break
                        txt = (txt or "").strip()
                        if txt and not txt.startswith("<") and len(txt) > 10:
                            first_user = txt[:280]
        except OSError:
            continue
        if cwd and (summary or first_user):
            break
    name = os.path.basename(cwd) if cwd else _decode_name(os.path.basename(pdir))
    blurb = (summary or first_user or "").replace("\n", " ").strip()
    return {"name": name, "cwd": cwd, "blurb": blurb[:280], "mtime": newest}


def discover_projects(force: bool = False) -> list[dict]:
    """Every real Claude Code project with a derived name + blurb, cached."""
    with _lock:
        cache = _load(PROJECTS_CACHE, {})
        if (not force and cache.get("ts")
                and time.time() - cache["ts"] < PROJECTS_TTL):
            return cache.get("projects", [])
        out, seen = [], set()
        try:
            folders = os.listdir(CC_ROOT)
        except OSError:
            folders = []
        for folder in folders:
            pdir = os.path.join(CC_ROOT, folder)
            if not os.path.isdir(pdir) or len(folder) < 5:
                continue
            if any(s in folder for s in _SKIP):
                continue
            info = _extract(pdir)
            key = (info["cwd"] or info["name"]).lower()
            # require a usable identity and skip the bare home/parent dirs
            if not info["blurb"] or not info["name"]:
                continue
            if info["name"].lower() in ("orion", "code", "claude", "desktop"):
                continue
            if key in seen:
                continue
            seen.add(key)
            pid = hashlib.md5(key.encode()).hexdigest()[:8]
            out.append({"id": pid, "name": info["name"],
                        "blurb": info["blurb"], "cwd": info["cwd"],
                        "mtime": info["mtime"]})
        out.sort(key=lambda p: -p["mtime"])
        _save(PROJECTS_CACHE, {"ts": time.time(), "projects": out})
        return out


# ------------------------------------------------------- classification
def _note_digest(rec) -> str:
    a = rec.analysis
    if a is None:
        # analysis failed (often: API out of credits) - fall back to the raw
        # transcript so the note can still be sorted on its actual content
        segs = getattr(rec, "segments", None) or []
        txt = " ".join((getattr(s, "text", "") or "") for s in segs).strip()
        if len(txt) > 40:
            return txt[:900]
        return (rec.filename or rec.id)[:120]
    bits = [a.headline or "", a.summary or ""]
    bits += [k for k in (a.key_points or [])[:6]]
    bits += [i.title for i in (a.ideas or [])[:6] if getattr(i, "title", "")]
    bits += [t.label for t in (a.topics or [])[:6] if getattr(t, "label", "")]
    return " | ".join(b for b in bits if b)[:900]


def _sig(rec) -> str:
    return hashlib.md5(_note_digest(rec).encode()).hexdigest()[:10]


def _claude_exe() -> Optional[str]:
    import shutil
    for c in (os.path.join(os.path.expanduser("~"), ".local", "bin",
                           "claude.exe"),
              "claude.exe", "claude"):
        if os.path.isabs(c) and os.path.exists(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


def _cli_json(prompt: str, timeout: int = 180) -> dict:
    """Run a prompt through the Claude Code CLI (Max subscription, no API
    credits) and parse the first JSON object out of stdout.

    The paid Anthropic API on this machine runs out of credits, so - exactly
    like the trader desk - classification goes through the CLI instead. The
    child env strips ANTHROPIC_* / CLAUDE_CODE_USE_* so the CLI uses
    subscription OAuth and not the dead API key (which fails slowly)."""
    import subprocess
    exe = _claude_exe()
    if not exe:
        return {}
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ANTHROPIC_") and not k.startswith("CLAUDE_CODE_USE_")}
    try:
        p = subprocess.run(
            [exe, "-p", "--output-format", "text"],
            input=prompt, capture_output=True, text=True,
            env=env, timeout=timeout,
            cwd=os.path.expanduser("~"))
        out = p.stdout or ""
        s, e = out.find("{"), out.rfind("}")
        if s >= 0 and e > s:
            return json.loads(out[s:e + 1])
    except Exception:
        pass
    return {}


def _cli_text(prompt: str, timeout: int = 180) -> str:
    """Run a prompt through the Claude CLI and return the raw text reply."""
    import subprocess
    exe = _claude_exe()
    if not exe:
        return ""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ANTHROPIC_") and not k.startswith("CLAUDE_CODE_USE_")}
    try:
        p = subprocess.run([exe, "-p", "--output-format", "text"],
                           input=prompt, capture_output=True, text=True,
                           env=env, timeout=timeout,
                           cwd=os.path.expanduser("~"))
        return p.stdout or ""
    except Exception:
        return ""


def _complete_text(system: str, user: str, max_tokens: int = 1400,
                   cli_timeout: int = 150) -> str:
    """Reliable one-shot text completion. Tries the paid Anthropic API FIRST
    (fast, a few seconds) and falls back to the Claude Code CLI (subscription)
    - the same resilient path the analysis layer uses. Returns '' only when
    BOTH fail.

    'Copy real info' used to go straight to the slow CLI (up to ~3 min, and it
    sometimes returned empty), which is why it felt broken; this gives it a
    fast API path with the CLI as a safety net."""
    try:
        import anthropic
        if settings.anthropic_api_key:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            msg = client.messages.create(
                model=settings.analysis_model, max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}])
            txt = "".join(b.text for b in msg.content
                          if getattr(b, "type", "") == "text").strip()
            if txt:
                return txt
    except Exception:
        pass
    return _cli_text(system + "\n\n" + user, timeout=cli_timeout).strip()


def _classify_batch(projects: list[dict], batch: list[tuple]) -> dict:
    """Ask Claude to map each (rec_id, digest) to a project id or 'none'."""
    plist = "\n".join(
        "- id=%s | %s: %s" % (p["id"], p["name"], p["blurb"][:180])
        for p in projects)
    notes = "\n".join("[%s] %s" % (rid, dig) for rid, dig in batch)
    prompt = (
        "You sort a person's voice notes under the PROJECT each note is "
        "about. Below is the project list (id, name, what it is) and some "
        "notes.\n\n"
        "A note OFTEN DOES NOT NAME its project. Use CONTEXTUAL CLUES - the "
        "subject, the product's features or mechanics, an audit/review/"
        "critique of a specific app or game, the goals or people involved - "
        "and match it to the project whose description fits, EVEN WHEN THE "
        "NAME IS NEVER SAID. Work hard to place a note that is clearly about "
        "a project's subject; a note that discusses or audits a project's "
        "product belongs to that project. Use \"none\" only when a note is "
        "genuinely unrelated to every project (pure personal chatter, an "
        "unrelated meeting).\n\n"
        "PROJECTS:\n%s\n\nNOTES:\n%s\n\n"
        "Reply with ONLY a JSON object mapping each note id (the value in "
        "square brackets) to a project id or \"none\". No prose." % (
            plist, notes))
    return _cli_json(prompt)


def classify(recs: list, force: bool = False) -> dict:
    """{rec_id: project_id or None}, cached per note by content signature."""
    projects = all_projects()
    valid = {p["id"] for p in projects}
    with _lock:
        cache = _load(ASSIGN_CACHE, {})
    todo = []
    for r in recs:
        c = cache.get(r.id)
        if force or not c or c.get("sig") != _sig(r):
            todo.append(r)
    for i in range(0, len(todo), 12):
        chunk = todo[i:i + 12]
        batch = [(r.id, _note_digest(r)) for r in chunk]
        mapping = _classify_batch(projects, batch) if projects else {}
        for r in chunk:
            raw = str(mapping.get(r.id, "none")).strip()
            pid = raw if raw in valid else None
            cache[r.id] = {"project": pid, "sig": _sig(r)}
    with _lock:
        _save(ASSIGN_CACHE, cache)
    return {r.id: (cache.get(r.id) or {}).get("project") for r in recs}


# --------------------------------------------------------- copy tracking
def note_summary(rec) -> str:
    """Clean, business-perspective summary of one note - no transcript, no
    tone/psychology/relationship analysis. Matches the web 'Copy summary'."""
    a = rec.analysis
    if a is None:
        return ""
    L = []
    L.append(a.headline or (rec.filename or "Note"))
    if rec.created_at:
        L.append(rec.created_at[:10])
    if a.summary:
        L.append("")
        L.append(a.summary)
    if a.key_points:
        L.append("")
        L.append("KEY POINTS")
        L += ["- " + k for k in a.key_points]
    decisions = list(a.plans or []) + list(a.commitments or [])
    if decisions:
        L.append("")
        L.append("DECISIONS & NEXT STEPS")
        L += ["- " + d.text + (" (" + d.who + ")" if getattr(d, "who", "") else "")
              for d in decisions]
    if a.action_items:
        L.append("")
        L.append("ACTION ITEMS")
        L += ["- " + x.text + (" — " + x.owner if getattr(x, "owner", "") else "")
              for x in a.action_items]
    if a.ideas:
        L.append("")
        L.append("IDEAS DISCUSSED")
        L += ["- " + i.title + (": " + i.summary if getattr(i, "summary", "") else "")
              for i in a.ideas]
    return "\n".join(L).strip()


SECTIONS = ("gist", "decisions", "actions", "ideas")


def _focus_batch(project: dict, items: list) -> dict:
    """{note_id: {gist, decisions[], actions[], ideas[]} or 'NONE'}.

    A single recording routinely mixes a project discussion with unrelated
    life (the marketplace brainstorm that also gets burgers and looks at
    cars). Each note is distilled to ONLY what concerns this project, and
    SPLIT into typed pieces so the copy can be filtered (full brief vs just
    action items vs just decisions). A note with nothing on-project -> NONE."""
    lines = "\n\n".join("[%s]\n%s" % (nid, txt) for nid, txt in items)
    prompt = (
        "PROJECT: %s — %s\n\n"
        "Below are voice-note summaries. For EACH note, extract ONLY the "
        "parts about THIS PROJECT and return them split by type. Omit "
        "everything unrelated (errands, meals, cars, small talk, other "
        "topics). Fields per note:\n"
        "  gist: 1-3 sentence summary of the project discussion\n"
        "  decisions: array of decisions/conclusions made (strings)\n"
        "  actions: array of action items / next steps (strings)\n"
        "  ideas: array of ideas/proposals raised (strings)\n"
        "If a note has nothing about this project, use the string \"NONE\" "
        "for it instead of an object.\n\n%s\n\n"
        "Reply with ONLY a JSON object mapping each note id (the value in "
        "square brackets) to its object (or \"NONE\"). No prose."
        % (project.get("name", ""), project.get("blurb", "")[:200], lines))
    return _cli_json(prompt, timeout=240)


def _norm_focus(v) -> Optional[dict]:
    """Coerce a model result into {gist,decisions,actions,ideas} or None."""
    if not isinstance(v, dict):
        return None
    out = {"gist": str(v.get("gist", "")).strip()}
    for k in ("decisions", "actions", "ideas"):
        x = v.get(k, [])
        if isinstance(x, str):
            x = [x] if x.strip() else []
        out[k] = [str(i).strip() for i in x if str(i).strip()][:12]
    if not out["gist"] and not any(out[k] for k in ("decisions", "actions", "ideas")):
        return None
    return out


def focus_summaries(pid: str, recs: list, generate: bool = True) -> dict:
    """{note_id: {gist,decisions,actions,ideas} or None} for a project's
    notes, cached by content sig. generate=False reads cache only."""
    projects = {p["id"]: p for p in all_projects()}
    proj = projects.get(pid)
    if not proj:
        return {}
    with _lock:
        cache = _load(FOCUS_CACHE, {})
    out, todo = {}, []
    for r in recs:
        c = cache.get(r.id)
        if c and c.get("project") == pid and c.get("sig") == _sig(r):
            out[r.id] = c.get("d")
        else:
            todo.append(r)
    if todo and generate:
        for i in range(0, len(todo), 8):
            chunk = todo[i:i + 8]
            items = [(r.id, note_summary(r)) for r in chunk if note_summary(r)]
            mapping = _focus_batch(proj, items) if items else {}
            for r in chunk:
                d = _norm_focus(mapping.get(r.id))
                cache[r.id] = {"project": pid, "sig": _sig(r), "d": d}
                out[r.id] = d
        with _lock:
            _save(FOCUS_CACHE, cache)
    return out


_SEC_LABEL = {"decisions": "Decisions", "actions": "Action items",
              "ideas": "Ideas"}


def _render_focus(d: dict, sections) -> str:
    parts = []
    if "gist" in sections and d.get("gist"):
        parts.append(d["gist"])
    for k in ("decisions", "actions", "ideas"):
        if k in sections and d.get(k):
            parts.append(_SEC_LABEL[k] + ":\n" + "\n".join("- " + x for x in d[k]))
    return "\n\n".join(parts).strip()


def copy_payload(pid: str, recs: list, include_all: bool = False,
                 since_days: int = 0, sections=None, recent: int = 0) -> dict:
    """Clean, FILTERABLE brief of a project's notes, distilled to only
    project-relevant content, oldest first.

    include_all: False = only not-yet-copied notes (Copy new); True = every
      note about the project (Copy all).
    since_days: 0 = any time; else only notes from the last N days.
    recent: 0 = off; else the N MOST RECENT notes about the project regardless
      of copied state (Copy recent) - overrides include_all/since_days.
    sections: which content types to include - any of
      {"gist","decisions","actions","ideas"}; default = all.

    Returns ids so the caller marks them copied only after a successful
    clipboard write (Copy all / Copy recent deliberately do not)."""
    sections = set(sections) if sections else set(SECTIONS)
    projects = {p["id"]: p for p in all_projects()}
    proj = projects.get(pid)
    assign = _cached_assign(recs)
    copied = set(_copied().get(pid, []))
    assigned = [r for r in recs if assign.get(r.id) == pid]
    if recent and recent > 0:
        # the N newest notes about the project, whether or not already copied
        assigned.sort(key=lambda r: r.created_at or "", reverse=True)
        mine = assigned[:recent]
    else:
        cutoff = ""
        if since_days:
            import datetime as _dt
            cutoff = (_dt.datetime.now(_dt.timezone.utc)
                      - _dt.timedelta(days=since_days)).isoformat()
        mine = [r for r in assigned
                if (include_all or r.id not in copied)
                and (not cutoff or (r.created_at or "") >= cutoff)]
    mine.sort(key=lambda r: r.created_at or "")   # oldest -> newest
    focus = focus_summaries(pid, mine, generate=True)
    blocks, ids = [], []
    for r in mine:
        d = focus.get(r.id)
        if not isinstance(d, dict):
            # nothing about the project at all: mark copied so a misassigned
            # or empty note stops showing as an uncopied update forever
            ids.append(r.id)
            continue
        body = _render_focus(d, sections)
        if body:
            hdr = (r.analysis.headline if r.analysis else "") or ""
            blocks.append((hdr + "\n" if hdr else "") + body)
            ids.append(r.id)      # copied
        # else: has project content but not in the selected sections -> leave
        # it "new" so its other content is still grabbable later
    label = "recent notes" if recent else ("new notes" if not include_all else "notes")
    header = "%s — %s\n%s" % (proj["name"] if proj else "Project", label, "=" * 40)
    text = (header + "\n\n" + "\n\n----\n\n".join(blocks)) if blocks else ""
    return {"text": text, "ids": ids, "count": len(blocks)}


def compile_projects(pids: list, recs: list, recent: int = 0,
                     since_days: int = 0, sections=None) -> dict:
    """One combined brief across SEVERAL projects - 'combine & compile'.

    Each selected project contributes its notes (optionally only the `recent`
    newest, or the last `since_days` days), distilled to project-only content
    and grouped under a per-project header. Returns {text, count, projects}."""
    sections = set(sections) if sections else set(SECTIONS)
    projects = {p["id"]: p for p in all_projects()}
    assign = _cached_assign(recs)
    cutoff = ""
    if since_days and not recent:
        import datetime as _dt
        cutoff = (_dt.datetime.now(_dt.timezone.utc)
                  - _dt.timedelta(days=since_days)).isoformat()
    doc, meta, total = [], [], 0
    for pid in pids:
        proj = projects.get(pid)
        if not proj:
            continue
        assigned = [r for r in recs if assign.get(r.id) == pid]
        if recent and recent > 0:
            assigned.sort(key=lambda r: r.created_at or "", reverse=True)
            mine = assigned[:recent]
        else:
            mine = [r for r in assigned
                    if not cutoff or (r.created_at or "") >= cutoff]
        mine.sort(key=lambda r: r.created_at or "")   # oldest -> newest
        if not mine:
            continue
        focus = focus_summaries(pid, mine, generate=True)
        blocks = []
        for r in mine:
            d = focus.get(r.id)
            if not isinstance(d, dict):
                continue
            body = _render_focus(d, sections)
            if body:
                hdr = (r.analysis.headline if r.analysis else "") or ""
                blocks.append((hdr + "\n" if hdr else "") + body)
        if not blocks:
            continue
        head = "## " + proj["name"]
        if proj.get("blurb"):
            head += "\n" + proj["blurb"][:200]
        doc.append(head + "\n\n" + "\n\n----\n\n".join(blocks))
        meta.append({"id": pid, "name": proj["name"], "count": len(blocks)})
        total += len(blocks)
    scope = ("most recent" if recent
             else ("last %d days" % since_days if since_days else "all notes"))
    title = "COMPILED BRIEF — %d project%s (%s)\n%s" % (
        len(meta), "" if len(meta) == 1 else "s", scope, "=" * 48)
    text = (title + "\n\n" + "\n\n\n".join(doc)) if doc else ""
    return {"text": text, "count": total, "projects": meta}


def _copied() -> dict:
    return _load(COPIED_FILE, {})


def mark_copied(pid: str, ids: list[str]) -> None:
    with _lock:
        data = _copied()
        have = set(data.get(pid, []))
        have.update(ids)
        data[pid] = sorted(have)
        _save(COPIED_FILE, data)


# --------------------------------------------------------------- groups
def custom_projects() -> list[dict]:
    """User-created projects that are NOT Claude Code repos - a subject to
    sort notes under even when no code session exists for it yet (e.g. a
    game being discussed and audited before its repo exists)."""
    out = []
    for c in _load(CUSTOM_FILE, []):
        if c.get("id") and c.get("name"):
            out.append({"id": c["id"], "name": c["name"],
                        "blurb": c.get("blurb", ""), "cwd": "",
                        "custom": True, "mtime": c.get("ts", 0)})
    return out


def add_custom_project(name: str, blurb: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    with _lock:
        data = _load(CUSTOM_FILE, [])
        for c in data:
            if c.get("name", "").lower() == name.lower():
                c["blurb"] = blurb or c.get("blurb", "")
                _save(CUSTOM_FILE, data)
                return {"id": c["id"], "name": c["name"], "blurb": c["blurb"]}
        pid = hashlib.md5(("custom:" + name.lower()).encode()).hexdigest()[:8]
        rec = {"id": pid, "name": name, "blurb": blurb, "ts": time.time()}
        data.append(rec)
        _save(CUSTOM_FILE, data)
    return {"id": pid, "name": name, "blurb": blurb}


GITHUB_CACHE = "github_repos.json"
FRIENDLY_CACHE = "friendly_names.json"
GITHUB_TTL = 6 * 3600
CODE_DIRS = [os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "code"),
             os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
             os.path.join(os.path.expanduser("~"), "code")]


def _gh_exe() -> Optional[str]:
    import shutil
    for c in (r"C:\Program Files\GitHub CLI\gh.exe", "gh.exe", "gh"):
        if os.path.isabs(c) and os.path.exists(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


def github_repos(force: bool = False) -> list[dict]:
    """Every GitHub repo (name, description, url), cached."""
    with _lock:
        cache = _load(GITHUB_CACHE, {})
        if (not force and cache.get("ts")
                and time.time() - cache["ts"] < GITHUB_TTL):
            return cache.get("repos", [])
    exe = _gh_exe()
    if not exe:
        return _load(GITHUB_CACHE, {}).get("repos", [])
    import subprocess
    try:
        p = subprocess.run(
            [exe, "repo", "list", "--limit", "300", "--json",
             "name,description,url,isPrivate,updatedAt"],
            capture_output=True, text=True, timeout=90)
        repos = json.loads(p.stdout or "[]")
    except Exception:
        return _load(GITHUB_CACHE, {}).get("repos", [])
    out = [{"name": r["name"], "description": r.get("description") or "",
            "url": r.get("url", ""), "private": bool(r.get("isPrivate")),
            "updated": r.get("updatedAt", "")} for r in repos if r.get("name")]
    with _lock:
        _save(GITHUB_CACHE, {"ts": time.time(), "repos": out})
    return out


def _friendly_batch(items: list) -> dict:
    """{repo_name: {display, desc}} - short recognizable names via the CLI."""
    lines = "\n".join("- %s: %s" % (n, (d or "(no description)")[:140])
                      for n, d in items)
    prompt = (
        "For each GitHub repo below (name: description), produce a SHORT, "
        "recognizable display name (2-4 words, Title Case, what the thing "
        "IS) and a one-line plain-English description a non-technical owner "
        "would understand.\n\n%s\n\n"
        "Reply with ONLY a JSON object mapping each repo name to "
        "{\"display\": \"...\", \"desc\": \"...\"}." % lines)
    return _cli_json(prompt, timeout=240)


def friendly_names(repos: list) -> dict:
    """{repo_name: {display, desc}} cached by name+description signature."""
    with _lock:
        cache = _load(FRIENDLY_CACHE, {})
    todo = []
    for r in repos:
        sig = hashlib.md5((r["name"] + "|" + r["description"]).encode()).hexdigest()[:8]
        c = cache.get(r["name"])
        if not c or c.get("sig") != sig:
            todo.append((r, sig))
    for i in range(0, len(todo), 15):
        chunk = todo[i:i + 15]
        mapping = _friendly_batch([(r["name"], r["description"]) for r, _ in chunk])
        for r, sig in chunk:
            m = mapping.get(r["name"]) or {}
            disp = (m.get("display") or "").strip() or _decode_name(r["name"])
            desc = (m.get("desc") or "").strip() or r["description"]
            cache[r["name"]] = {"display": disp, "desc": desc, "sig": sig}
    with _lock:
        _save(FRIENDLY_CACHE, cache)
    return {k: v for k, v in cache.items()}


def _local_clone(repo_name: str) -> str:
    """Path to a local checkout of this repo, if one exists on disk."""
    for base in CODE_DIRS:
        p = os.path.join(base, repo_name)
        if os.path.isdir(os.path.join(p, ".git")) or os.path.isdir(p):
            if os.path.isdir(p):
                return p
    # also match a Claude Code project whose folder basename equals the repo
    for cc in discover_projects():
        if os.path.basename(cc.get("cwd", "")).lower() == repo_name.lower():
            return cc["cwd"]
    return ""


def all_projects() -> list[dict]:
    """The unified project registry: every GitHub repo (with a friendly name
    and the original repo name kept for search), plus user-created custom
    subjects. A repo checked out locally carries its path so it can be
    talked to and deployed."""
    repos = github_repos()
    fr = friendly_names(repos) if repos else {}
    out, seen = [], set()
    for r in repos:
        f = fr.get(r["name"], {})
        cwd = _local_clone(r["name"])
        pid = hashlib.md5(("gh:" + r["name"]).encode()).hexdigest()[:8]
        out.append({"id": pid, "name": f.get("display") or _decode_name(r["name"]),
                    "orig_name": r["name"], "blurb": f.get("desc") or r["description"],
                    "cwd": cwd, "url": r["url"], "source": "github",
                    "mtime": r.get("updated", "")})
        seen.add(r["name"].lower())
    # custom subjects (e.g. a game with no repo yet)
    for c in custom_projects():
        c = dict(c); c["orig_name"] = c["name"]; c["source"] = "custom"; c["url"] = ""
        out.append(c)
    return out


def is_website(cwd: str) -> bool:
    """Best-effort: does this repo look like a deployable website?

    The change-and-deploy feature is websites-only, so a project qualifies
    only if its folder shows web-app markers - a package.json, an index.html,
    a web/ or public/ dir, a framework/deploy config, or loose .html files."""
    if not cwd or not os.path.isdir(cwd):
        return False
    markers = ("package.json", "index.html", "vercel.json", "netlify.toml",
               "next.config.js", "next.config.ts", "next.config.mjs",
               "web", "public", "site", "dist")
    for m in markers:
        if os.path.exists(os.path.join(cwd, m)):
            return True
    try:
        for name in os.listdir(cwd):
            if name.endswith((".html", ".htm")):
                return True
    except OSError:
        pass
    return False


_warming = threading.Event()


def warm(recs: list, force: bool = False) -> None:
    """Classify unassigned notes in the background (CLI calls). force=True
    re-classifies everything (e.g. after a new project is added)."""
    if _warming.is_set():
        return
    _warming.set()
    try:
        assign = classify(recs, force=force)
        # pre-distill each assigned note to its project-only summary so the
        # copy button never has to wait on a CLI call
        by_proj: dict[str, list] = {}
        for r in recs:
            pid = assign.get(r.id)
            if pid:
                by_proj.setdefault(pid, []).append(r)
        for pid, rs in by_proj.items():
            try:
                focus_summaries(pid, rs, generate=True)
            except Exception:
                pass
    finally:
        _warming.clear()


def warm_async(recs: list, force: bool = False) -> None:
    threading.Thread(target=warm, args=(list(recs), force),
                     name="lucid-businesses-warm", daemon=True).start()


def _cached_assign(recs: list) -> dict:
    """Assignments from cache only - never triggers a CLI call."""
    cache = _load(ASSIGN_CACHE, {})
    return {r.id: (cache.get(r.id) or {}).get("project") for r in recs}


def project_index(recs: list) -> dict:
    """EVERY project (all 66 repos + custom), each with its note count and
    uncopied count - powers the browsable project list where the user can
    click any project to talk to it, whether or not it has notes yet."""
    projects = all_projects()
    assign = _cached_assign(recs)
    copied = _copied()
    counts, newc = {}, {}
    for r in recs:
        pid = assign.get(r.id)
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
            if r.id not in set(copied.get(pid, [])):
                newc[pid] = newc.get(pid, 0) + 1
    items = []
    for p in projects:
        items.append({"id": p["id"], "name": p["name"],
                      "orig_name": p.get("orig_name", ""),
                      "blurb": p.get("blurb", ""), "url": p.get("url", ""),
                      "source": p.get("source", ""),
                      "is_website": is_website(p.get("cwd", "")),
                      "has_local": bool(p.get("cwd")),
                      "count": counts.get(p["id"], 0),
                      "new_count": newc.get(p["id"], 0)})
    # projects with notes first, then with a live change target, then A-Z
    items.sort(key=lambda x: (-x["new_count"], -x["count"],
                              not x["is_website"], x["name"].lower()))
    n_un = sum(1 for r in recs if not assign.get(r.id))
    return {"projects": items, "unsorted": n_un, "warming": _warming.is_set()}


BRIEF_CACHE = "note_brief.json"


def note_brief(rec) -> str:
    """A clean, third-person briefing of a note for a software developer or a
    company stakeholder: only the substantive product / technical / business
    facts, decisions, and to-dos. Strips personal chatter, filler, and
    who-said-what attributions ('Orion said …'). Cached by content sig."""
    a = rec.analysis
    if a is None:
        return ""
    with _lock:
        cache = _load(BRIEF_CACHE, {})
    sig = _sig(rec)
    c = cache.get(rec.id)
    if c and c.get("sig") == sig:
        return c.get("brief", "")
    src = note_summary(rec)
    tx = (rec.full_text_translated or rec.full_text or "").strip()
    system = (
        "You turn a personal voice note into a clean, third-person briefing "
        "for a software developer or company stakeholder. Output ONLY the "
        "briefing itself - no preamble, no 'here is', no meta, no code fence.")
    user = (
        "Rewrite the note below as a concise briefing. Rules:\n"
        "- THIRD PERSON, factual, professional.\n"
        "- Only substantive product, technical, or business information: what "
        "is being built or decided, requirements, features, next steps, "
        "numbers, and open questions.\n"
        "- OMIT personal chatter, small talk, filler, and who-said-what "
        "attributions (never 'Orion said', 'he mentioned'). State facts "
        "directly.\n"
        "- Short paragraphs or bullets.\n\nNOTE:\n%s\n\nTRANSCRIPT:\n%s"
        % (src, tx[:6000]))
    brief = _complete_text(system, user, max_tokens=1400).strip()
    if brief:
        # merge-on-save: reload under the lock so a concurrent brief for
        # another note isn't lost (load→modify→save clobber).
        with _lock:
            fresh = _load(BRIEF_CACHE, {})
            fresh[rec.id] = {"sig": sig, "brief": brief}
            _save(BRIEF_CACHE, fresh)
        return brief
    # Both API and CLI came back empty (rare). Return the clean structured
    # summary so the button still yields useful text, but do NOT cache it -
    # a later click retries the model instead of being stuck on the fallback.
    return src


def mark_seen(rec_ids: list, recs: list) -> None:
    """Mark notes as seen/read (clears their blue 'new' marker). Reuses the
    per-project copied set: opening a note or acting on it counts as engaging
    with it, so 'new' means 'not yet seen'."""
    assign = _cached_assign(recs)
    by_proj: dict[str, list] = {}
    for rid in rec_ids:
        pid = assign.get(rid) or "__unsorted"
        by_proj.setdefault(pid, []).append(rid)
    for pid, ids in by_proj.items():
        mark_copied(pid, ids)


def combine_change_prompt(pid: str, recs: list) -> Optional[dict]:
    """One change request built from ALL of a project's unread notes, so the
    user can review the new updates and make every requested change in a
    single agent pass. Returns {prompt, ids} or None when nothing is new."""
    assign = _cached_assign(recs)
    seen = set(_copied().get(pid, []))
    unread = [r for r in recs if assign.get(r.id) == pid and r.id not in seen]
    unread.sort(key=lambda r: r.created_at or "")
    if not unread:
        return None
    blocks = []
    for r in unread:
        a = r.analysis
        tx = (r.full_text_translated or r.full_text or "").strip()
        b = "--- %s (%s) ---\n" % ((a.headline if a else "Note"),
                                   (r.created_at or "")[:10])
        if a and a.summary:
            b += a.summary + "\n"
        if a and a.action_items:
            b += "Wants: " + "; ".join(x.text for x in a.action_items) + "\n"
        if tx:
            b += "Said: " + tx[:2000]
        blocks.append(b)
    prompt = ("I recorded several voice notes about this project. Read them "
              "ALL and make EVERY change I describe, in one pass. My notes, "
              "oldest first:\n\n" + "\n\n".join(blocks))
    return {"prompt": prompt, "ids": [r.id for r in unread]}


MENTAL_CACHE = "mental.json"
_mental_warming = threading.Event()
_mental_lock = threading.Lock()


def _mental_classify(batch: list) -> dict:
    """{rec_id: {self:bool, insight:str}} — which notes are the speaker
    reflecting on THEMSELVES (mindset, feelings, motivation, habits,
    psychological patterns) vs. plain product/task talk, + a one-line insight."""
    lines = []
    for r in batch:
        a = r.analysis
        dig = (a.headline if a else "") or "Note"
        if a and a.summary:
            dig += " — " + a.summary
        tx = (r.full_text_translated or r.full_text or "").strip()
        lines.append("[%s] %s\n    said: %s" % (
            r.id, dig[:220], tx[:700].replace("\n", " ")))
    prompt = (
        "Below are a person's voice notes. For EACH note decide if the speaker "
        "is talking ABOUT THEMSELVES in a personal or psychological way: their "
        "feelings, mood, energy, motivation, fears, self-doubt, confidence, "
        "habits, mental health, how they think or work, or a PATTERN in their "
        "own behaviour. Plain business / product / task / meeting talk is NOT "
        "self-reflection unless they observe something about their own mind or "
        "behaviour.\n"
        "For each note that IS self-reflection, extract ONE short insight "
        "capturing the psychological pattern or self-statement, max 18 words, "
        "no first person (write 'Tends to…' not 'I tend to…').\n\n"
        "NOTES:\n%s\n\n"
        "Reply with ONLY JSON: {\"items\":[{\"id\":\"..\",\"self\":true or "
        "false,\"insight\":\"..\"}]}. Include EVERY note id." % "\n".join(lines))
    res = _cli_json(prompt, timeout=240)
    out = {}
    for it in (res.get("items") or []):
        rid = str(it.get("id", "")).strip()
        if rid:
            out[rid] = {"self": bool(it.get("self")),
                        "insight": (it.get("insight") or "").strip()}
    return out


def _mental_warm(recs: list) -> None:
    # atomic start guard (non-blocking Lock, not a TOCTOU is_set/set on an Event)
    if not _mental_lock.acquire(blocking=False):
        return
    _mental_warming.set()
    try:
        with _lock:
            cache = _load(MENTAL_CACHE, {})
        todo = [r for r in recs
                if (cache.get(r.id) or {}).get("sig") != _sig(r)]
        B = 12
        for i in range(0, len(todo), B):
            batch = todo[i:i + B]
            got = _mental_classify(batch)
            # total CLI failure (timeout / junk → {}): don't poison the cache
            # with self=False for real reflections — leave them to retry.
            if not got:
                continue
            updates = {}
            for r in batch:
                it = got.get(r.id, {})
                updates[r.id] = {"sig": _sig(r),
                                 "self": bool(it.get("self")),
                                 "insight": it.get("insight", "")}
            # merge-on-save: re-load under the lock so a concurrent writer
            # (another cache's warm) isn't clobbered by our stale copy.
            with _lock:
                fresh = _load(MENTAL_CACHE, {})
                fresh.update(updates)
                _save(MENTAL_CACHE, fresh)
    finally:
        _mental_warming.clear()
        _mental_lock.release()


def mental_index(recs: list) -> dict:
    """Cross-cutting 'Mental' view: notes where the speaker reflects on
    themselves, newest first, each with a one-line insight. Cache-only and
    instant; warms uncached notes in the background (a note can be BOTH about a
    project and personal, so this is independent of project sorting)."""
    with _lock:
        cache = _load(MENTAL_CACHE, {})
    if any((cache.get(r.id) or {}).get("sig") != _sig(r) for r in recs):
        threading.Thread(target=_mental_warm, args=(list(recs),),
                         name="lucid-mental-warm", daemon=True).start()
    items = []
    for r in recs:
        c = cache.get(r.id) or {}
        if c.get("self"):
            items.append({"id": r.id,
                          "headline": (r.analysis.headline if r.analysis else "")
                          or "Reflection",
                          "insight": c.get("insight", ""),
                          "created_at": r.created_at})
    items.sort(key=lambda x: x["created_at"] or "", reverse=True)
    analyzed = sum(1 for r in recs if (cache.get(r.id) or {}).get("sig") == _sig(r))
    return {"items": items, "count": len(items),
            "warming": _mental_warming.is_set(),
            "analyzed": analyzed, "total": len(recs)}


PATTERNS_CACHE = "mental_patterns.json"


def mental_patterns(recs: list) -> str:
    """Synthesized read of the recurring psychological patterns across all
    self-reflective notes. Cached against the set of insights so it only
    regenerates when the underlying reflections change."""
    idx = mental_index(recs)
    ins = [i["insight"] for i in idx["items"] if i["insight"]]
    if len(ins) < 2:
        return ""
    sig = hashlib.md5("|".join(sorted(ins)).encode()).hexdigest()[:12]
    with _lock:
        c = _load(PATTERNS_CACHE, {})
    if c.get("sig") == sig:
        return c.get("text", "")
    prompt = (
        "Here are self-observations a person made about themselves over time. "
        "Identify the RECURRING psychological patterns, tensions, and themes: "
        "how they think, what drives them, what blocks them, how their mood or "
        "energy moves. Write 3-6 tight bullets, second person ('You tend "
        "to…'), honest but kind, no preamble.\n\n"
        + "\n".join("- " + x for x in ins[:80]))
    text = _cli_text(prompt, timeout=180).strip()
    if text:
        with _lock:
            _save(PATTERNS_CACHE, {"sig": sig, "text": text})
    return text


def build_groups(recs: list) -> dict:
    """Projects (with >=1 note) + their notes, new/copied counts, + Unsorted.

    Cache-only and instant: notes not yet classified fall into Unsorted until
    the background warmer catches up. Kick warm_async() to fill gaps."""
    projects = all_projects()
    by_id = {p["id"]: p for p in projects}
    assign = _cached_assign(recs)
    unclassified = sum(1 for r in recs if r.id not in _load(ASSIGN_CACHE, {}))
    if unclassified:
        warm_async(recs)
    copied = _copied()

    buckets: dict[str, list] = {}
    unsorted: list = []
    for r in recs:
        pid = assign.get(r.id)
        row = {"id": r.id,
               "headline": (r.analysis.headline if r.analysis else "")
               or (r.filename or "Note"),
               "created_at": r.created_at,
               "copied": r.id in set(copied.get(pid or "", []))}
        (buckets.setdefault(pid, []) if pid in by_id else unsorted).append(row)

    groups = []
    for pid, rows in buckets.items():
        p = by_id[pid]
        rows.sort(key=lambda x: x["created_at"] or "", reverse=True)
        new_count = sum(1 for x in rows if not x["copied"])
        groups.append({"id": pid, "name": p["name"], "blurb": p["blurb"],
                       "orig_name": p.get("orig_name", ""),
                       "url": p.get("url", ""), "source": p.get("source", ""),
                       "count": len(rows), "new_count": new_count,
                       "is_website": is_website(p.get("cwd", "")),
                       "notes": rows})
    # most-recently-updated first, projects with new notes surfaced
    groups.sort(key=lambda g: (g["new_count"] > 0,
                               (g["notes"][0]["created_at"] or "") if g["notes"] else ""),
                reverse=True)
    unsorted.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return {"projects": groups, "unsorted": unsorted,
            "warming": _warming.is_set()}
