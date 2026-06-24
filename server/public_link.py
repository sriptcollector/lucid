"""Give friends ONE permanent link, even though the tunnel URL keeps changing.

The Cloudflare *quick* tunnel hands out a new random ``*.trycloudflare.com``
hostname on every (re)launch, so any link a friend bookmarks dies on the next
restart. To fix that we keep a tiny redirect page on the repo's ``gh-pages``
branch — served by GitHub Pages at a stable address like
``https://<owner>.github.io/<repo>/`` — and rewrite it to point at the current
tunnel URL each time the tunnel comes up.

Publishing goes through the GitHub Contents API via the already-authenticated
``gh`` CLI, so there is no token to handle here. Everything is best-effort: if
``gh`` is missing, unauthenticated, or offline, we log and move on — the tunnel
must never break because a convenience link couldn't update.

Enabled only when ``stable_link_repo`` is set (e.g. ``"owner/repo"``). Friends
who clone Lucid leave it blank and simply use the live tunnel URL directly.
"""
from __future__ import annotations

import base64
import shutil
import subprocess

from .config import settings

# A calm, on-brand "you're being forwarded" page. Forwards three ways so it
# works with JS off, too: meta-refresh, a manual button, and location.replace.
_REDIRECT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Opening Lucid…</title>
<meta http-equiv="refresh" content="0; url={url}">
<link rel="icon" href="data:,">
<style>
  body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#f4f3f2;color:#1a1a1a;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}}
  .card{{background:#fff;border-radius:24px;padding:40px 48px;max-width:340px;text-align:center;
        box-shadow:0 20px 60px rgba(0,0,0,.08)}}
  h1{{font-size:20px;margin:0 0 6px;font-weight:600}}
  p{{color:#8a8a8a;font-size:14px;margin:0 0 22px}}
  a{{display:inline-block;background:#111;color:#fff;text-decoration:none;
    padding:12px 24px;border-radius:999px;font-size:14px;font-weight:500}}
</style>
</head>
<body>
  <div class="card">
    <h1>Opening Lucid…</h1>
    <p>Taking you to the live app.</p>
    <a href="{url}">Continue to Lucid →</a>
  </div>
  <script>location.replace("{url}");</script>
</body>
</html>
"""


def render(url: str) -> str:
    """The redirect page that forwards a visitor to the live tunnel URL."""
    return _REDIRECT_HTML.format(url=url)


def _gh(args: list[str], timeout: float = 30.0) -> tuple[bool, str, str]:
    """Run the gh CLI; return (ok, stdout, stderr). Never raises."""
    exe = settings.gh_path.strip() or shutil.which("gh") or "gh"
    try:
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - missing gh / timeout / OS error
        return False, "", str(exc)
    return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()


def _current_sha(repo: str, branch: str, path: str) -> str:
    """Blob SHA of ``path`` on ``branch`` (required to update an existing file)."""
    ok, out, _ = _gh(
        ["api", f"repos/{repo}/contents/{path}?ref={branch}", "--jq", ".sha"]
    )
    return out if ok else ""


def publish(live_url: str) -> bool:
    """Rewrite the stable redirect page to point at ``live_url``.

    Returns True on a successful push. Safe to call from any thread; never
    raises. A no-op (returns False) unless ``stable_link_repo`` is configured.
    """
    repo = settings.stable_link_repo.strip()
    if not repo or not live_url:
        return False
    branch = settings.stable_link_branch.strip() or "gh-pages"
    content = base64.b64encode(render(live_url).encode("utf-8")).decode("ascii")
    args = [
        "api", "-X", "PUT", f"repos/{repo}/contents/index.html",
        "-f", f"message=Point Lucid link at {live_url}",
        "-f", f"content={content}",
        "-f", f"branch={branch}",
    ]
    sha = _current_sha(repo, branch, "index.html")
    if sha:
        args += ["-f", f"sha={sha}"]
    ok, _, err = _gh(args)
    if ok:
        print(f"[link] stable link -> {live_url}")
    else:
        print(f"[link] could not update stable link: {err[:200]}")
    return ok


def stable_url() -> str:
    """The public, unchanging link (``https://owner.github.io/repo/``) or ''."""
    return settings.stable_public_url
