/* Lucid — clean white+navy note-taker. Vanilla JS SPA. */
(() => {
  "use strict";
  const root = document.getElementById("root");
  const $toast = document.getElementById("toast");

  // ---- auth token (supports ?k=<token> auto-login) ----
  const url0 = new URL(location.href);
  if (url0.searchParams.get("k")) {
    localStorage.setItem("lucid_token", url0.searchParams.get("k"));
    url0.searchParams.delete("k");
    history.replaceState({}, "", url0.pathname + url0.hash);
  }
  let token = localStorage.getItem("lucid_token") || "";

  // ---- path → hash bridge ----
  // This app routes off the URL hash only. But note SHARE links and old
  // bookmarks come in as real paths (/r/<id>, /search, /people …). Without
  // this, every one of them silently dumps the user on the Notes list. Map the
  // known ones to their hash route; send unknown/dead paths to Notes.
  if (!location.hash && location.pathname && location.pathname !== "/") {
    const p = location.pathname.replace(/\/+$/, "");
    const seg = p.split("/").filter(Boolean);
    let hash = "";
    if (seg[0] === "r" && seg[1]) hash = "#/note/" + seg[1];
    else if (seg[0] === "note" && seg[1]) hash = "#/note/" + seg[1];
    else if (seg[0] === "project" && seg[1]) hash = "#/project/" + seg[1];
    else if (["notes", "projects", "chats", "beliefs", "mental", "search"].includes(seg[0])) hash = "#/" + seg[0];
    else hash = "#/notes";  // dead/old routes (/people, /crm, /ventures, …)
    history.replaceState({}, "", "/" + hash);
  }

  // ---- helpers ----
  const h = (s) => (s == null ? "" : String(s)).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  let _tt;
  function toast(msg) { $toast.textContent = msg; $toast.classList.add("on"); clearTimeout(_tt); _tt = setTimeout(() => $toast.classList.remove("on"), 2600); }
  function rel(iso) {
    if (!iso) return "";
    const d = new Date(iso), now = new Date(), s = (now - d) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    if (s < 604800) return Math.floor(s / 86400) + "d ago";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function fmtDur(s) { if (!s) return ""; s = Math.round(s); const m = Math.floor(s / 60), r = s % 60; return m + ":" + String(r).padStart(2, "0"); }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (e) {
      const ta = document.createElement("textarea"); ta.value = text;
      ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta);
      ta.focus(); ta.select(); let ok = false;
      try { ok = document.execCommand("copy"); } catch (_) {}
      document.body.removeChild(ta); return ok;
    }
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (token) headers.Authorization = "Bearer " + token;
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401 || res.status === 403) { showLogin(); throw new Error("auth"); }
    if (!res.ok) throw new Error("Server error " + res.status);
    const ct = res.headers.get("content-type") || "";
    return ct.includes("json") ? res.json() : res;
  }

  // ---- clean, business-perspective note summary (no jargon, no transcript) ----
  function businessSummary(n) {
    const L = [];
    L.push(n.headline || "Note");
    if (n.created_at) L.push(new Date(n.created_at).toLocaleString());
    if (n.summary) { L.push(""); L.push(n.summary); }
    if ((n.key_points || []).length) { L.push(""); L.push("KEY POINTS"); n.key_points.forEach(k => L.push("• " + k)); }
    const dec = [...(n.plans || []), ...(n.commitments || [])];
    if (dec.length) { L.push(""); L.push("DECISIONS & NEXT STEPS"); dec.forEach(x => L.push("• " + x.text + (x.who ? " (" + x.who + ")" : ""))); }
    if ((n.action_items || []).length) { L.push(""); L.push("ACTION ITEMS"); n.action_items.forEach(x => L.push("• " + x.text + (x.owner ? " — " + x.owner : ""))); }
    if ((n.ideas || []).length) { L.push(""); L.push("IDEAS DISCUSSED"); n.ideas.forEach(i => L.push("• " + i.title + (i.summary ? ": " + i.summary : ""))); }
    if ((n.people || []).length) { L.push(""); L.push("PEOPLE"); n.people.forEach(p => L.push("• " + (p.name || p.label) + (p.role ? " — " + p.role : ""))); }
    return L.join("\n").trim();
  }

  // ---- shell + navigation ----
  const NAV = [
    { id: "notes", label: "Notes", ic: "▤", route: "#/notes" },
    { id: "projects", label: "Projects", ic: "◈", route: "#/projects" },
    { id: "chats", label: "Chats", ic: "⌸", route: "#/chats" },
    { id: "beliefs", label: "Beliefs", ic: "✦", route: "#/beliefs" },
    { id: "mental", label: "Mental", ic: "◑", route: "#/mental" },
    { id: "search", label: "Search", ic: "⌕", route: "#/search" },
  ];
  let newCount = 0;

  function shell(active, bodyHTML) {
    const navBtns = (cls) => NAV.map(n => `<button class="${n.id === active ? "on" : ""}" data-go="${n.route}">
      <span class="ic">${n.ic}</span>${cls === "side" ? n.label : n.label}${n.id === "projects" && newCount ? `<span class="badge">${newCount}</span>` : ""}</button>`).join("");
    root.innerHTML = `<div class="app">
      <aside class="side">
        <div class="brand"><span class="dot"></span>Lucid</div>
        <nav class="nav">${navBtns("side")}</nav>
        <div class="side-chats"><div class="sc-h">Recent chats</div><div class="sc-list" id="scList"></div></div>
        <div class="side-foot">
          <div class="nav"><button data-act="upload"><span class="ic">＋</span>Add recording</button>
          <button data-act="logout"><span class="ic">⇤</span>Sign out</button></div>
        </div>
      </aside>
      <main class="main"><div id="view">${bodyHTML}</div></main>
      <nav class="tabbar">${navBtns("tab")}
        <button data-act="upload"><span class="ic">＋</span>Add</button></nav>
    </div>`;
    root.querySelectorAll("[data-go]").forEach(b => b.onclick = () => { location.hash = b.dataset.go; });
    root.querySelectorAll('[data-act="upload"]').forEach(b => b.onclick = pickUpload);
    root.querySelectorAll('[data-act="logout"]').forEach(b => b.onclick = () => { localStorage.removeItem("lucid_token"); token = ""; showLogin(); });
    paintSideChats();
  }
  const view = () => document.getElementById("view");
  const topbar = (title, right = "") => `<div class="topbar"><h1>${h(title)}</h1>${right}</div><div class="content">`;
  const skels = (n) => Array(n).fill('<div class="skel"></div>').join("");

  // ---- login ----
  function showLogin(err) {
    root.innerHTML = `<div class="login-wrap"><form class="login" id="lf">
      <div class="l-brand">Lucid</div><p>Enter your password to open your notes.</p>
      <div class="err">${err ? h(err) : ""}</div>
      <input id="pw" type="password" placeholder="Password" autocomplete="current-password" />
      <button class="btn primary" style="width:100%" type="submit">Open Lucid</button>
    </form></div>`;
    const f = document.getElementById("lf");
    document.getElementById("pw").focus();   // just type, no click
    f.onsubmit = async (e) => {
      e.preventDefault();
      const pw = document.getElementById("pw").value;
      try {
        const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }) });
        if (!r.ok) return showLogin(r.status === 401 ? "Incorrect password." : "Couldn't log in.");
        const d = await r.json(); token = d.token || ""; localStorage.setItem("lucid_token", token);
        if (!location.hash) location.hash = "#/notes";
        route();
      } catch (_) { showLogin("Network error."); }
    };
  }

  // ---- NOTES feed ----
  let _pollTimer = null;
  async function showNotes() {
    shell("notes", topbar("Notes", `<button class="btn primary sm" data-act="upload">＋ Add</button>`) + skels(4) + "</div>");
    root.querySelector('[data-act="upload"]').onclick = pickUpload;
    let data; try { data = await api("/api/data/notes?limit=200"); } catch (e) { return; }
    const notes = data.notes || [];
    if (!notes.length) { view().innerHTML = topbar("Notes") + `<div class="empty"><div class="big">◍</div>No notes yet.<div class="hint">Record on your Plaud or add an audio file — notes appear here automatically.</div></div></div>`; bindUpload(); return; }
    view().innerHTML = topbar("Notes", `<button class="btn primary sm" data-act="upload">＋ Add</button>`) +
      `<div class="feed">${notes.map(noteCard).join("")}</div></div>`;
    view().querySelectorAll("[data-note]").forEach(c => c.onclick = () => { location.hash = "#/note/" + c.dataset.note; });
    bindUpload();
    // poll while anything is still processing
    const processing = notes.some(n => n.status && n.status !== "done");
    clearTimeout(_pollTimer);
    if (processing) _pollTimer = setTimeout(() => { if (location.hash.startsWith("#/notes")) showNotes(); }, 5000);
  }
  function noteCard(n) {
    const st = n.status || "done";
    const done = st === "done", failed = st === "error";
    const title = n.headline || (failed ? "Couldn't process this recording" : done ? "Untitled note" : "Writing up your note…");
    const ppl = (n.people || []).length;
    return `<button class="note" data-note="${h(n.id)}">
      <div class="n-h">${h(title)}</div>
      ${n.summary ? `<div class="n-s">${h(n.summary)}</div>` : (failed ? "" : (done ? "" : `<div class="n-s">Transcribing and analyzing — this can take a couple minutes.</div>`))}
      <div class="n-m">
        ${failed ? `<span class="chip fail">failed</span>` : done ? "" : `<span class="chip warn">processing</span>`}
        ${n.duration ? `<span class="chip">${fmtDur(n.duration)}</span>` : ""}
        ${ppl ? `<span class="chip">${ppl} ${ppl > 1 ? "people" : "person"}</span>` : ""}
        <span class="time">${h(rel(n.created_at))}</span>
      </div></button>`;
  }

  // ---- NOTE detail ----
  async function showNote(id) {
    shell("notes", topbar("Note") + skels(3) + "</div>");
    let n; try { n = await api("/api/data/notes/" + encodeURIComponent(id)); } catch (e) { return; }
    const done = !n.status || n.status === "done";
    const dmeta = [rel(n.created_at), n.duration ? fmtDur(n.duration) : "", n.language || ""].filter(Boolean).map(h).join(" · ");
    const sec = [];
    if (n.summary) sec.push(`<div class="panel"><h2>Summary</h2><p class="lead">${h(n.summary)}</p></div>`);
    if ((n.key_points || []).length) sec.push(`<div class="panel"><h2>Key points</h2><ul class="kp">${n.key_points.map(k => `<li>${h(k)}</li>`).join("")}</ul></div>`);
    const dec = [...(n.plans || []), ...(n.commitments || [])];
    if (dec.length) sec.push(`<div class="panel"><h2>Decisions &amp; next steps</h2>${dec.map(x => `<div class="li"><span class="bx">›</span><span>${h(x.text)} ${x.who ? `<span class="who">— ${h(x.who)}</span>` : ""}</span></div>`).join("")}</div>`);
    if ((n.action_items || []).length) sec.push(`<div class="panel"><h2>Action items</h2>${n.action_items.map(a => `<div class="li"><span class="bx">☐</span><span>${h(a.text)} ${a.owner ? `<span class="who">— ${h(a.owner)}</span>` : ""}</span></div>`).join("")}</div>`);
    if ((n.ideas || []).length) sec.push(`<div class="panel"><h2>Ideas</h2>${n.ideas.map(i => `<div class="idea"><div class="t">${h(i.title)}</div>${i.summary ? `<div class="d">${h(i.summary)}</div>` : ""}</div>`).join("")}</div>`);
    if ((n.people || []).length) sec.push(`<div class="panel"><h2>People</h2>${n.people.map(p => `<div class="person"><span class="p-n">${h(p.name || p.label)}</span>${p.role ? `<span class="p-r">${h(p.role)}</span>` : ""}</div>`).join("")}</div>`);
    if ((n.notable_quotes || []).length) sec.push(`<div class="panel"><h2>Notable quotes</h2>${n.notable_quotes.map(q => `<div class="quote">${h(q.text)}${q.speaker ? `<div class="q-w">— ${h(q.speaker)}</div>` : ""}</div>`).join("")}</div>`);
    const tx = (n.transcript || n.full_text || (n.segments || []).map(s => (s.speaker ? s.speaker + ": " : "") + (s.text_translated || s.text)).join("\n"));
    const txPanel = tx ? `<div class="panel"><details class="tx"><summary>Full transcript</summary><pre>${h(tx)}</pre></details></div>` : "";

    view().innerHTML = topbar("Note") +
      `<button class="back" data-back>‹ Notes</button>
      <div class="hero"><div class="hero-main"><h1>${h(n.headline || "Note")}</h1>
        <div class="dmeta">${dmeta || (done ? "" : "processing…")}</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn primary" id="copyBtn">⧉ Copy summary</button>
          <button class="btn" id="copyReal" title="Clean, third-person info a dev or company would want — no personal chatter">⧉ Copy real info</button></div></div>
      <div id="audioSlot"></div>
      <div id="folderSlot"></div>
      <div id="fixSlot"></div>
      ${sec.join("") || `<div class="panel muted">${n.status === "error" ? "This recording couldn't be processed (often empty or unclear audio). Try Re-analyze below." : done ? "No analysis for this note." : "Transcribing and analyzing — check back in a minute."}</div>`}
      ${txPanel}
      <div class="panel"><div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn ghost sm" id="reBtn">↻ Re-analyze</button>
        <button class="btn ghost sm" id="delBtn" style="color:var(--danger)">Delete</button>
      </div></div></div>`;
    view().querySelector("[data-back]").onclick = () => { location.hash = "#/notes"; };
    document.getElementById("copyBtn").onclick = async () => { const ok = await copyText(businessSummary(n)); toast(ok ? "Summary copied" : "Copy failed"); };
    document.getElementById("copyReal").onclick = async (e) => {
      const b = e.target; b.disabled = true; const lbl = b.textContent; b.textContent = "Preparing…";
      let d; try { d = await api("/api/notes/" + id + "/brief"); } catch (_) { toast("Couldn't build"); b.disabled = false; b.textContent = lbl; return; }
      const ok = await copyText((d.brief || "").trim() || businessSummary(n));
      toast(ok ? "Real info copied" : "Copy failed"); b.disabled = false; b.textContent = lbl;
    };
    // read receipt: opening a note marks it seen (clears its blue marker)
    api("/api/notes/" + id + "/seen", { method: "POST" }).then(() => { _biz = null; _projAll = null; }).catch(() => {});
    document.getElementById("reBtn").onclick = async () => { try { await api("/api/recordings/" + id + "/reanalyze", { method: "POST" }); toast("Re-analyzing…"); } catch (_) { toast("Couldn't re-analyze"); } };
    // two-click delete (no native modal): first click arms, second confirms
    const delBtn = document.getElementById("delBtn");
    let delArmed = false, delTimer = null;
    delBtn.onclick = async () => {
      if (!delArmed) { delArmed = true; const t = delBtn.textContent; delBtn.textContent = "Click again to delete"; delBtn.classList.add("danger"); delTimer = setTimeout(() => { delArmed = false; delBtn.textContent = t; delBtn.classList.remove("danger"); }, 3000); return; }
      clearTimeout(delTimer);
      try { await api("/api/recordings/" + id, { method: "DELETE" }); toast("Deleted"); location.hash = "#/notes"; } catch (_) { toast("Couldn't delete"); }
    };
    // audio (fetch as blob so the auth header applies)
    try {
      const r = await api("/api/recordings/" + id + "/audio");
      const blob = await r.blob(); const u = URL.createObjectURL(blob);
      document.getElementById("audioSlot").innerHTML = `<div class="panel"><audio controls preload="metadata" src="${u}"></audio></div>`;
    } catch (_) {}
    // folder picker + "fix what I said". One /target call tells us both which
    // project this note is currently filed under (for the picker) and whether
    // that project is a repo we can change (for the Fix panel).
    try {
      const t = await api("/api/notes/" + id + "/target");
      setupFolder(id, t.project ? t.project.id : "");
      if (t.has_repo && t.project) setupFix(t);
    } catch (_) { setupFolder(id, ""); }
  }

  // ---- manual sort: file a note under a specific project folder ----
  async function setupFolder(id, current) {
    const slot = document.getElementById("folderSlot"); if (!slot) return;
    if (!_projAll) { try { _projAll = (await api("/api/projects/all")).projects || []; } catch (_) { _projAll = []; } }
    // notes-first, then A-Z, so the likely targets are up top
    const list = _projAll.slice().sort((a, b) =>
      (b.count || 0) - (a.count || 0) || (a.name || "").localeCompare(b.name || ""));
    const opts = [`<option value="__unsorted"${!current ? " selected" : ""}>Unsorted</option>`]
      .concat(list.map(p => `<option value="${h(p.id)}"${p.id === current ? " selected" : ""}>${h(p.name)}${p.count ? ` (${p.count})` : ""}</option>`))
      .join("");
    slot.innerHTML = `<div class="panel folderpanel"><h2>Folder</h2>
      <div class="soft" style="font-size:13px;margin:-4px 0 10px">Which project this note is filed under. Pick one to sort it yourself — your choice sticks and the auto-sorter won't override it.</div>
      <select id="folderSel" class="search-in">${opts}</select></div>`;
    const sel = document.getElementById("folderSel");
    sel.onchange = async () => {
      const pid = sel.value, label = sel.options[sel.selectedIndex].text.replace(/\s*\(\d+\)$/, "");
      sel.disabled = true;
      try {
        await api("/api/notes/" + id + "/assign", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project: pid }) });
        toast(pid === "__unsorted" ? "Moved to Unsorted" : "Filed under " + label);
        _biz = null; _projAll = null; newCount = 0;   // force the project lists to refetch
      } catch (_) { toast("Couldn't move"); }
      sel.disabled = false;
    };
  }

  function setupFix(t) {
    const slot = document.getElementById("fixSlot"); if (!slot) return;
    slot.innerHTML = `<div class="panel fixpanel">
      <h2>Fix &amp; deploy in ${h(t.project.name)}</h2>
      <div class="soft" style="font-size:13px;margin:-4px 0 10px">This note is about <b>${h(t.project.name)}</b>${t.project.orig_name ? ` <span class="mono">(${h(t.project.orig_name)})</span>` : ""}. A Claude Code agent (Opus 4.8) makes the change you described, then deploys it <b>live</b> automatically.</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="fixBtn">▷ Fix it &amp; deploy live</button></div>
      <div id="fixOut"></div></div>`;
    const btn = document.getElementById("fixBtn"), out = document.getElementById("fixOut");
    const pid = t.project.id;
    const controls = { disable: () => { btn.disabled = true; }, enable: () => { btn.disabled = false; } };
    resumeJob(pid, out, controls);  // survive a page reload mid-session
    btn.onclick = () => chainChangeDeploy(pid, { endpoint: "/api/businesses/" + encodeURIComponent(pid) + "/request", body: { text: t.prompt } }, out, controls);
  }

  // poll an agent/deploy job until it finishes; call render(job) each tick
  async function pollJob(jid, render) {
    while (true) {
      let j; try { j = await api("/api/agent/jobs/" + jid); } catch (e) { await sleep(3000); continue; }
      render(j);
      if (j.status !== "queued" && j.status !== "running") return j;
      await sleep(3000);
    }
  }
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  // ---- change→deploy engine (shared by note "Fix", project "Talk", "Make all changes") ----
  // Persist the in-flight job per project so a page RELOAD re-attaches to the
  // live session instead of losing it.
  const jobKey = (pid) => "lucid_job_" + pid;
  function saveJob(pid, jobId, phase) { try { localStorage.setItem(jobKey(pid), JSON.stringify({ jobId, phase, t: Date.now() })); } catch (_) {} }
  function clearJob(pid) { try { localStorage.removeItem(jobKey(pid)); } catch (_) {} }
  function getJob(pid) { try { const v = JSON.parse(localStorage.getItem(jobKey(pid)) || "null"); return (v && Date.now() - (v.t || 0) < 3600e3) ? v : null; } catch (_) { return null; } }

  // Run (or resume) a change session, then auto-deploy live. Renders into `out`.
  // opts: {endpoint, body, jobId, phase, deploy}. controls: {disable, enable}.
  async function chainChangeDeploy(pid, opts, out, controls) {
    // terminal-style live log: keep it pinned to the newest line each render
    const stage = (html) => {
      out.innerHTML = `<div class="fixstages">${html}</div>`;
      const lg = out.querySelector(".asklog"); if (lg) lg.scrollTop = lg.scrollHeight;
    };
    controls.disable();
    const renderChange = (j) => {
      const active = j.status === "queued" || j.status === "running";
      stage(`<div class="askstatus">${active ? '<span class="spin sm"></span>' : (j.status === "done" ? "✓ " : "⚠ ")}1. Change — ${active ? "working…" : (j.status === "done" ? "done" : j.status)}</div>
        ${j.log ? `<pre class="asklog">${h(j.log.slice(-4000))}</pre>` : ""}${(j.changed || []).length ? `<div class="askchanged"><b>Files changed:</b> ${j.changed.map(h).join(", ")}</div>` : ""}`);
    };
    // Phase "deploy": resume straight into a running deploy job.
    if (opts.phase !== "deploy") {
      let changeJobId = opts.jobId;
      if (!changeJobId) {
        stage(`<div class="askstatus"><span class="spin sm"></span>1. Making the change…</div>`);
        let r; try { r = await api(opts.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body || {}) }); }
        catch (e) { stage(`<div class="askjob err">Couldn't start the session.</div>`); controls.enable(); clearJob(pid); return; }
        if (!r.ok) { stage(`<div class="askjob err">${h(r.error || "Couldn't start")}</div>`); controls.enable(); clearJob(pid); return; }
        if (!r.job) { stage(`<div class="askjob">${h(r.note || "Nothing new to change.")}</div>`); controls.enable(); clearJob(pid); return; }
        changeJobId = r.job;
      }
      saveJob(pid, changeJobId, "change");
      let changeLog = "";
      const cj = await pollJob(changeJobId, (j) => { changeLog = j.log || ""; renderChange(j); });
      if (cj.status !== "done") { controls.enable(); clearJob(pid); return; }
      if (!(cj.changed || []).length) { stage(`<div class="askstatus">✓ No code change was needed.</div><pre class="asklog">${h(changeLog.slice(-2500))}</pre>`); controls.enable(); clearJob(pid); toast("Nothing to change"); return; }
      if (opts.deploy === false) { controls.enable(); clearJob(pid); toast("Change complete"); return; }
      // deploy-owner guard: two tabs sharing localStorage could both resume the
      // same change-done job and each POST /deploy → double live deploy. First
      // one to claim this change's guard key wins; the other stops here.
      try {
        const gk = "lucid_dep_" + changeJobId;
        if (localStorage.getItem(gk)) { controls.enable(); clearJob(pid); return; }
        localStorage.setItem(gk, "1");
      } catch (_) {}
      // start deploy
      stage(`<div class="askstatus done">✓ 1. Change made</div><div class="askstatus"><span class="spin sm"></span>2. Deploying live…</div>`);
      let dr; try { dr = await api("/api/businesses/" + encodeURIComponent(pid) + "/deploy?prod=true", { method: "POST" }); }
      catch (e) { stage(`<div class="askstatus done">✓ 1. Change made</div><div class="askjob err">Change made, but couldn't start deploy.</div>`); controls.enable(); clearJob(pid); return; }
      if (!dr.ok) { stage(`<div class="askstatus done">✓ 1. Change made</div><div class="askjob err">Change made. Deploy: ${h(dr.error || "unavailable")}</div>`); controls.enable(); clearJob(pid); return; }
      opts.jobId = dr.job;
    }
    // deploy phase
    saveJob(pid, opts.jobId, "deploy");
    const dj = await pollJob(opts.jobId, (j) => {
      const active = j.status === "queued" || j.status === "running";
      const url = j.url ? `<div class="askchanged"><b>Live:</b> <a href="${h(j.url)}" target="_blank" rel="noopener">${h(j.url)}</a></div>` : "";
      stage(`<div class="askstatus done">✓ 1. Change made</div>
        <div class="askstatus ${j.status === "done" ? "done" : ""}">${active ? '<span class="spin sm"></span>' : (j.status === "done" ? "✓ " : "⚠ ")}2. Deploy — ${active ? "publishing…" : (j.status === "done" ? "live" : j.status)}</div>
        ${j.log ? `<pre class="asklog">${h(j.log.slice(-3000))}</pre>` : ""}${url}`);
    });
    controls.enable(); clearJob(pid);
    _biz = null; _projAll = null;
    toast(dj.status === "done" ? "Fixed & deployed live" : "Change made; deploy had trouble");
  }

  // If a change/deploy for this project is still running (or was mid-flight at
  // reload), re-attach to it live. Returns true if it took over `out`.
  // `out` can be an element, or a function(job) that builds and returns the
  // element (the project chat uses this to append a live session to the thread).
  async function resumeJob(pid, out, controls, onDone) {
    const saved = getJob(pid);
    if (!saved) return false;
    controls.disable();  // synchronous: close the gap where the user could start a 2nd job mid-lookup
    let j; try { j = await api("/api/agent/jobs/" + saved.jobId); } catch (_) { j = null; }
    if (!j) { clearJob(pid); controls.enable(); return false; }
    const active = j.status === "queued" || j.status === "running";
    // still running, or a change that finished with edits but hadn't deployed yet
    if (active || (saved.phase === "change" && j.status === "done" && (j.changed || []).length)) {
      const slot = (typeof out === "function") ? out(j) : out;
      chainChangeDeploy(pid, { jobId: saved.jobId, phase: saved.phase }, slot, controls)
        .then(() => { if (onDone) onDone(); });
      return true;
    }
    clearJob(pid);
    controls.enable();
    return false;
  }

  // ---- chats: recent sessions across all projects (sidebar + page) ----
  let _chats = null, _chatsAt = 0;
  async function fetchChats(force) {
    if (!force && _chats && Date.now() - _chatsAt < 30000) return _chats;
    try { _chats = (await api("/api/agent/jobs")).jobs || []; _chatsAt = Date.now(); }
    catch (_) { _chats = _chats || []; }
    return _chats;
  }
  const chatTitle = (j) => j.title || (j.request || "").split("\n")[0].slice(0, 58) || "Session";
  const relTs = (sec) => sec ? rel(new Date(sec * 1000).toISOString()) : "";
  const isLive = (j) => j.status === "queued" || j.status === "running";
  const dotCls = (j) => isLive(j) ? "live" : (j.status === "done" ? "ok" : "bad");

  function paintSideChats() {
    const box = document.getElementById("scList"); if (!box) return;
    fetchChats().then(list => {
      const cur = document.getElementById("scList"); if (!cur) return;
      cur.innerHTML = list.slice(0, 8).map(j => `<button class="sc-row" data-cj="${h(j.id)}" data-cp="${h(j.project || "")}">
          <span class="sc-dot ${dotCls(j)}"></span>
          <span class="sc-t">${h(chatTitle(j))}</span>
          <span class="sc-m">${h(j.project_name || "")}</span></button>`).join("")
        || `<div class="sc-empty">No sessions yet</div>`;
      cur.querySelectorAll(".sc-row").forEach(b => b.onclick = () => {
        location.hash = b.dataset.cp ? "#/project/" + b.dataset.cp : "#/chats";
      });
    });
  }

  // inline rename for a session title (chats page + project chat)
  function renameUI(titleEl, j, cls) {
    const inp = document.createElement("input");
    inp.className = "ren-in"; inp.value = chatTitle(j);
    titleEl.replaceWith(inp); inp.focus(); inp.select();
    let done = false;
    const commit = async (save) => {
      if (done) return; done = true;
      const nv = inp.value.trim();
      if (save && nv && nv !== chatTitle(j)) {
        try {
          await api("/api/agent/jobs/" + j.id + "/rename", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: nv }) });
          j.title = nv; _chats = null; toast("Renamed"); paintSideChats();
        } catch (_) { toast("Couldn't rename"); }
      }
      const div = document.createElement("div"); div.className = cls; div.setAttribute("data-title", "");
      div.textContent = chatTitle(j);
      inp.replaceWith(div);
    };
    inp.onkeydown = (e) => { if (e.key === "Enter") commit(true); if (e.key === "Escape") commit(false); };
    inp.onblur = () => commit(true);
    // stop the row's own click (open project) while renaming
    inp.onclick = (e) => e.stopPropagation();
  }

  async function showChats() {
    shell("chats", topbar("Chats") + skels(3) + "</div>");
    const list = await fetchChats(true);
    paintSideChats();
    const rows = list.map((j, i) => `<div class="chatrow" data-ci="${i}" data-cp="${h(j.project || "")}">
        <span class="sc-dot ${dotCls(j)}"></span>
        <div class="cr-main">
          <div class="cr-t" data-title>${h(chatTitle(j))}</div>
          <div class="cr-m">${h(j.project_name || "")}${j.kind === "deploy" ? " · deploy" : ""} · ${h(relTs(j.started))} · ${isLive(j) ? "running" : h(j.status || "")}${(j.changed || []).length ? ` · ${j.changed.length} file${j.changed.length != 1 ? "s" : ""}` : ""}</div>
        </div>
        <button class="icbtn" data-ren="${i}" title="Rename">✎</button>
      </div>`).join("");
    view().innerHTML = topbar("Chats") +
      `<div class="p-blurb" style="margin-bottom:14px">Every Claude Code session you've run from Lucid, changes and deploys across all projects. Click one to open its project chat; ✎ renames it.</div>` +
      (rows ? `<div class="panel" style="padding:6px 10px">${rows}</div>` : `<div class="empty"><div class="big">⌸</div>No sessions yet.<div class="hint">Open a project and ask it for a change. The session shows up here.</div></div>`) + "</div>";
    view().querySelectorAll(".chatrow").forEach(r => r.onclick = () => {
      if (r.querySelector(".ren-in")) return;
      if (r.dataset.cp) location.hash = "#/project/" + r.dataset.cp;
    });
    view().querySelectorAll("[data-ren]").forEach(b => b.onclick = (e) => {
      e.stopPropagation();
      const i = parseInt(b.dataset.ren, 10);
      const row = b.closest(".chatrow");
      renameUI(row.querySelector("[data-title]"), list[i], "cr-t");
    });
    // keep the page fresh while anything is running
    if (list.some(isLive)) setTimeout(() => { if (location.hash.startsWith("#/chats")) showChats(); }, 5000);
  }

  // ---- PROJECTS ----
  let _biz = null;
  const _compile = { on: false, sel: new Set(), recent: true };  // combine & compile
  const _filt = { days: 0, content: "all" };  // copy filters
  const CONTENT_SECTIONS = { all: "", decisions: "decisions", actions: "actions", ideas: "ideas" };
  function copyQuery(all) {
    const p = [];
    if (all) p.push("all=true");
    if (_filt.days) p.push("days=" + _filt.days);
    const s = CONTENT_SECTIONS[_filt.content]; if (s) p.push("sections=" + s);
    return p.length ? "?" + p.join("&") : "";
  }
  let _projAll = null;
  let _projQuery = "";   // remember the search so Back doesn't wipe it
  async function showProjects() {
    _compile.on = false; _compile.sel.clear();   // compile is a transient action
    shell("projects", topbar("Projects", `<button class="btn ghost sm" id="reSort">↻ Re-sort</button>`) + skels(4) + "</div>");
    let d; try { d = await api("/api/projects/all"); } catch (e) { return; }
    _projAll = d.projects || [];
    newCount = _projAll.reduce((a, g) => a + (g.new_count || 0), 0);
    const render = (q) => {
      const t = (q || "").trim().toLowerCase();
      const list = !t ? _projAll : _projAll.filter(p =>
        (p.name || "").toLowerCase().includes(t) || (p.orig_name || "").toLowerCase().includes(t) || (p.blurb || "").toLowerCase().includes(t));
      const cards = list.map(g => {
        const sel = _compile.sel.has(g.id);
        return `<button class="pcard${g.new_count ? " hasnew" : ""}${_compile.on ? " selectable" : ""}${sel ? " sel" : ""}" data-p="${h(g.id)}">
        ${_compile.on ? `<span class="pcheck">${sel ? "✓" : ""}</span>` : ""}
        <div class="p-name">${h(g.name)}${g.new_count ? `<span class="pill">${g.new_count} new</span>` : ""}${g.is_website ? `<span class="tag-web">site</span>` : ""}</div>
        ${g.orig_name && g.orig_name.toLowerCase() !== (g.name || "").toLowerCase() ? `<div class="p-orig">${h(g.orig_name)}</div>` : ""}
        ${g.blurb ? `<div class="p-blurb">${h(g.blurb)}</div>` : ""}
        <div class="p-meta">${g.count ? g.count + " note" + (g.count != 1 ? "s" : "") : "no notes yet"}</div></button>`;
      }).join("");
      const grid = document.getElementById("pjgrid");
      if (grid) grid.innerHTML = cards || `<div class="empty">No projects match “${h(q)}”.</div>`;
      view().querySelectorAll("[data-p]").forEach(c => c.onclick = () => {
        if (_compile.on) toggleSel(c.dataset.p);
        else location.hash = "#/project/" + c.dataset.p;
      });
    };
    function toggleSel(id) {
      if (_compile.sel.has(id)) _compile.sel.delete(id); else _compile.sel.add(id);
      render(_projQuery); updateBar();
    }
    function updateBar() {
      const bar = document.getElementById("compileBar"); if (bar) bar.style.display = _compile.on ? "flex" : "none";
      const cnt = document.getElementById("crCount"); if (cnt) cnt.textContent = _compile.sel.size + " selected";
      const go = document.getElementById("crGo"); if (go) go.disabled = _compile.sel.size === 0;
      const tg = document.getElementById("compileToggle"); if (tg) tg.textContent = _compile.on ? "✕ Done" : "⿻ Compile";
    }
    view().innerHTML = topbar("Projects", `<button class="btn sm" id="newProj">＋ New</button><button class="btn ghost sm" id="compileToggle">⿻ Compile</button><button class="btn ghost sm" id="reSort2">↻ Re-sort</button>`) +
      `<input class="search-in" id="pjSearch" placeholder="Search ${_projAll.length} projects by name or original repo name…" autocomplete="off" style="margin-bottom:16px" />
       <div id="newProjForm" class="panel" style="display:none;margin-bottom:16px">
         <input class="search-in" id="npName" placeholder="Project or business name" autocomplete="off" style="margin-bottom:8px" />
         <input class="search-in" id="npBlurb" placeholder="Optional: what it's about (helps sort notes that don't say the name)" autocomplete="off" style="margin-bottom:10px" />
         <div style="display:flex;gap:8px"><button class="btn primary sm" id="npCreate">Create</button><button class="btn ghost sm" id="npCancel">Cancel</button></div></div>
       <div id="compileHint" class="hint" style="text-align:left;margin:-6px 0 12px;display:none">Pick the projects to combine, then compile them into one brief you can copy.</div>
       ${(d.unsorted ? `<div class="hint" style="text-align:left;margin:-6px 0 12px">${d.unsorted} note${d.unsorted != 1 ? "s" : ""} not yet sorted${d.warming ? " · sorting…" : ""}</div>` : "")}
       <div class="pgrid" id="pjgrid"></div>
       <div id="compileBar" class="compilebar" style="display:none">
         <span id="crCount" class="crcount">0 selected</span>
         <label class="crecent"><input type="checkbox" id="crRecent" checked /> Recent notes only</label>
         <button class="btn ghost sm" id="crCancel">Cancel</button>
         <button class="btn primary sm" id="crGo" disabled>⧉ Compile &amp; copy</button>
       </div></div>`;
    const psi = document.getElementById("pjSearch");
    psi.value = _projQuery;
    psi.oninput = (e) => { _projQuery = e.target.value; render(e.target.value); };
    // combine & compile: toggle select-mode, choose projects, copy one brief
    document.getElementById("compileToggle").onclick = () => {
      _compile.on = !_compile.on;
      if (!_compile.on) _compile.sel.clear();
      const ch = document.getElementById("compileHint"); if (ch) ch.style.display = _compile.on ? "block" : "none";
      render(_projQuery); updateBar();
    };
    document.getElementById("crRecent").onchange = (e) => { _compile.recent = e.target.checked; };
    document.getElementById("crCancel").onclick = () => {
      _compile.on = false; _compile.sel.clear();
      const ch = document.getElementById("compileHint"); if (ch) ch.style.display = "none";
      render(_projQuery); updateBar();
    };
    document.getElementById("crGo").onclick = async () => {
      const ids = Array.from(_compile.sel);
      if (!ids.length) { toast("Select projects first"); return; }
      const go = document.getElementById("crGo"); go.disabled = true; const lbl = go.textContent; go.textContent = "Compiling…";
      let pay; try { pay = await api("/api/businesses/compile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, recent: _compile.recent ? 5 : 0 }) }); }
      catch (e) { toast("Couldn't compile"); go.disabled = false; go.textContent = lbl; return; }
      go.textContent = lbl; updateBar();
      if (!pay.text) { toast("No notes to compile in those projects"); return; }
      const ok = await copyText(pay.text);
      toast(ok ? `Compiled ${pay.count} note${pay.count != 1 ? "s" : ""} from ${(pay.projects || []).length} project${(pay.projects || []).length != 1 ? "s" : ""}` : "Copy failed");
    };
    document.getElementById("reSort2").onclick = async () => { toast("Re-sorting…"); try { await api("/api/businesses/refresh", { method: "POST" }); } catch (_) {} setTimeout(() => { _biz = null; _projAll = null; showProjects(); }, 2500); };
    // inline new-project form (no native prompt() dialogs)
    const npForm = document.getElementById("newProjForm");
    document.getElementById("newProj").onclick = () => { npForm.style.display = npForm.style.display === "none" ? "block" : "none"; if (npForm.style.display === "block") document.getElementById("npName").focus(); };
    document.getElementById("npCancel").onclick = () => { npForm.style.display = "none"; };
    const createProj = async () => {
      const name = document.getElementById("npName").value.trim();
      if (!name) { toast("Name it first"); document.getElementById("npName").focus(); return; }
      const blurb = document.getElementById("npBlurb").value.trim();
      toast("Creating & sorting…");
      try { await api("/api/businesses/custom", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, blurb }) }); }
      catch (e) { toast("Couldn't create"); return; }
      setTimeout(() => { _biz = null; _projAll = null; showProjects(); }, 3000);
    };
    document.getElementById("npCreate").onclick = createProj;
    document.getElementById("npName").onkeydown = (e) => { if (e.key === "Enter") createProj(); };
    document.getElementById("npBlurb").onkeydown = (e) => { if (e.key === "Enter") createProj(); };
    render(_projQuery);
    root.querySelectorAll('[data-go="#/projects"] .badge').forEach(b => b.remove());
  }

  async function showProject(pid) {
    if (!_projAll) { try { _projAll = (await api("/api/projects/all")).projects || []; } catch (e) { _projAll = []; } }
    if (!_biz) { try { _biz = await api("/api/businesses"); } catch (e) { _biz = { projects: [], unsorted: [] }; } }
    let meta, notes;
    if (pid === "__unsorted") { meta = { id: "__unsorted", name: "Unsorted" }; notes = _biz.unsorted || []; }
    else {
      meta = (_projAll || []).find(x => x.id === pid) || (_biz.projects || []).find(x => x.id === pid);
      if (!meta) { location.hash = "#/projects"; return; }
      const grp = (_biz.projects || []).find(x => x.id === pid);
      notes = (grp && grp.notes) || [];
    }
    const g = Object.assign({}, meta, { notes, count: notes.length, new_count: notes.filter(n => !n.copied).length });
    const canCopy = pid !== "__unsorted" && g.count > 0;
    const canChange = pid !== "__unsorted" && !!(meta.is_website || meta.has_local || meta.url);
    shell("projects", topbar("Projects") +
      `<button class="back" data-back>‹ Projects</button>
      <div class="hero"><div class="hero-main"><h1>${h(g.name)}</h1>
        ${g.orig_name && g.orig_name.toLowerCase() !== (g.name || "").toLowerCase() ? `<div class="p-orig">${h(g.orig_name)}${g.url ? ` · <a href="${h(g.url)}" target="_blank" rel="noopener">GitHub ↗</a>` : ""}</div>` : ""}
        ${g.blurb ? `<div class="p-blurb">${h(g.blurb)}</div>` : ""}
        <div class="dmeta">${g.count} note${g.count != 1 ? "s" : ""}${g.new_count ? ` · ${g.new_count} new` : ""}</div></div>
        ${canCopy ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn primary" id="copyNew" ${g.new_count ? "" : "disabled"}>⧉ Copy new${g.new_count ? ` (${g.new_count})` : ""}</button>
          <button class="btn" id="copyRecent" ${g.count ? "" : "disabled"}>⧉ Copy recent</button>
          <button class="btn" id="copyAll" ${g.count ? "" : "disabled"}>⧉ Copy all</button></div>` : ""}</div>
      ${(!canCopy && canChange) ? "" : ""}
      ${canCopy ? `<div class="filtbar">
        <div class="filtgrp" data-filt="days">
          <span class="filtlab">When</span>
          <button class="fchip${_filt.days === 0 ? " on" : ""}" data-v="0">Any time</button>
          <button class="fchip${_filt.days === 30 ? " on" : ""}" data-v="30">30 days</button>
          <button class="fchip${_filt.days === 7 ? " on" : ""}" data-v="7">7 days</button>
        </div>
        <div class="filtgrp" data-filt="content">
          <span class="filtlab">Include</span>
          <button class="fchip${_filt.content === "all" ? " on" : ""}" data-v="all">Everything</button>
          <button class="fchip${_filt.content === "decisions" ? " on" : ""}" data-v="decisions">Decisions</button>
          <button class="fchip${_filt.content === "actions" ? " on" : ""}" data-v="actions">Action items</button>
          <button class="fchip${_filt.content === "ideas" ? " on" : ""}" data-v="ideas">Ideas</button>
        </div></div>` : ""}
      ${(canChange && g.new_count) ? `<div class="panel updpanel"><h2>${g.new_count} new update${g.new_count != 1 ? "s" : ""}</h2>
        <div class="soft" style="font-size:13px;margin:-4px 0 12px">Review the new notes below, then send them all to a Claude Code agent as <b>one</b> change and deploy it live.</div>
        <button class="btn primary" id="fixAllBtn">▷ Make all ${g.new_count} change${g.new_count != 1 ? "s" : ""} &amp; deploy live</button>
        <div id="fixAllOut"></div></div>` : ""}
      ${g.count ? `<div class="panel">${(g.notes || []).map(n => `<div class="pnote${n.copied ? "" : " new"}" data-note="${h(n.id)}">
        <span class="pn-dot"></span><div><div class="pn-h">${h(n.headline)}</div>
        <div class="pn-m">${h(rel(n.created_at))}${n.copied ? "" : " · new"}</div></div></div>`).join("")}</div>` : ""}
      ${canChange ? `<div class="panel askpanel"><h2>Chat with this project</h2>
        <div class="soft" style="font-size:13px;margin:-4px 0 10px">Every message runs a real Claude Code session (Opus 4.8) on this repo, a live terminal session right here in the chat. It makes the change, then deploys it live.</div>
        <div id="chatThread" class="chatthread"><div class="sc-empty" style="color:var(--soft)">Loading sessions…</div></div>
        <div class="composer">
          <textarea id="askText" class="search-in" style="min-height:64px;resize:vertical" placeholder="Ask for a change in plain words, e.g. make the hero headline bigger"></textarea>
          <div class="comp-row">
            <label class="btn ghost sm" for="askShot" style="cursor:pointer" title="Attach a screenshot so the agent can see what you mean">📎 Screenshot</label>
            <input id="askShot" type="file" accept="image/*" style="display:none" />
            <span id="askShotName" class="soft" style="font-size:12px"></span>
            <button class="btn primary" id="askRun" style="margin-left:auto">▷ Make the change &amp; deploy</button></div>
        </div></div>
        <div class="panel deploypanel"><h2>Deploy</h2>
          <div id="depInfo" class="soft" style="font-size:13px">Checking deploy setup…</div>
          <div id="depBtns" style="display:none;gap:8px;margin-top:10px">
            <button class="btn" id="depPreview">◱ Deploy preview</button>
            <button class="btn primary" id="depProd">▲ Push to production</button></div>
          <div id="depOut"></div></div>` : ""}
      </div>`);
    view().querySelector("[data-back]").onclick = () => { location.hash = "#/projects"; };
    setupAsk(pid); setupDeploy(pid);
    view().querySelectorAll("[data-note]").forEach(el2 => el2.onclick = () => { location.hash = "#/note/" + el2.dataset.note; });
    // filter chips
    view().querySelectorAll(".filtgrp").forEach(grp => {
      const key = grp.dataset.filt;
      grp.querySelectorAll(".fchip").forEach(ch => ch.onclick = () => {
        _filt[key] = key === "days" ? parseInt(ch.dataset.v, 10) : ch.dataset.v;
        grp.querySelectorAll(".fchip").forEach(x => x.classList.toggle("on", x === ch));
      });
    });
    const doCopy = async (btn, all) => {
      btn.disabled = true; const label = btn.textContent; btn.textContent = "Copying…";
      let pay; try { pay = await api("/api/businesses/" + encodeURIComponent(pid) + "/copytext" + copyQuery(all)); }
      catch (e) { toast("Couldn't build summary"); btn.disabled = false; btn.textContent = label; return; }
      if (!pay.text) { toast(all ? "Nothing to copy yet" : "Nothing new to copy"); btn.disabled = false; btn.textContent = label; return; }
      const ok = await copyText(pay.text);
      if (!ok) { toast("Copy failed"); btn.disabled = false; btn.textContent = label; return; }
      if (all) {
        toast(`Copied all ${pay.count} note${pay.count != 1 ? "s" : ""} — paste into Claude Code`);
        btn.disabled = false; btn.textContent = label;
      } else {
        // "Copy new" advances the tracking so these stop showing as new
        try { await api("/api/businesses/" + encodeURIComponent(pid) + "/copied", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: pay.ids }) }); } catch (_) {}
        toast(`Copied ${pay.count} new note${pay.count != 1 ? "s" : ""} — paste into Claude Code`);
        // update the cache in place (mark these notes copied) and re-render from
        // cache — no server round-trip, no repaint flash
        const grp = (_biz && _biz.projects || []).find(x => x.id === pid);
        if (grp) { const s = new Set(pay.ids); (grp.notes || []).forEach(n => { if (s.has(n.id)) n.copied = true; }); }
        showProject(pid);
      }
    };
    const cn = document.getElementById("copyNew"); if (cn) cn.onclick = () => doCopy(cn, false);
    const ca = document.getElementById("copyAll"); if (ca) ca.onclick = () => doCopy(ca, true);
    // "Copy recent" — the N newest notes, regardless of copied state (a re-grab
    // of the latest activity, so it does NOT advance the copied tracker).
    const cr = document.getElementById("copyRecent");
    if (cr) cr.onclick = async () => {
      cr.disabled = true; const lbl = cr.textContent; cr.textContent = "Copying…";
      const s = CONTENT_SECTIONS[_filt.content];
      const q = "?recent=5" + (s ? "&sections=" + s : "");
      let pay; try { pay = await api("/api/businesses/" + encodeURIComponent(pid) + "/copytext" + q); }
      catch (e) { toast("Couldn't build summary"); cr.disabled = false; cr.textContent = lbl; return; }
      if (!pay.text) { toast("Nothing to copy yet"); cr.disabled = false; cr.textContent = lbl; return; }
      const ok = await copyText(pay.text);
      toast(ok ? `Copied ${pay.count} most-recent note${pay.count != 1 ? "s" : ""}` : "Copy failed");
      cr.disabled = false; cr.textContent = lbl;
    };
  }

  // read a File as a data-URL (for screenshot attach)
  const readFile = (f) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });

  // ---- project chat: every message = a Claude Code session on the repo ----
  // The thread shows past sessions as chat turns (your ask → the session's
  // terminal), streams live ones, and auto-deploys after each change.
  function sessHTML(j, i) {
    const live = isLive(j);
    const files = (j.changed || []).length ? `<div class="askchanged"><b>Files:</b> ${(j.changed || []).map(h).join(", ")}</div>` : "";
    const url = (j.url && j.kind === "deploy") ? `<div class="askchanged"><b>Live:</b> <a href="${h(j.url)}" target="_blank" rel="noopener">${h(j.url)}</a></div>` : "";
    return `<div class="csess" data-sess="${h(j.id)}">
      <div class="csess-head"><span class="cs-title" data-title>${h(chatTitle(j))}</span>
        <button class="icbtn" data-ren="${i}" title="Rename">✎</button>
        <span class="cs-meta">${h(relTs(j.started))} · ${live ? "running" : h(j.status || "")}${j.kind === "deploy" ? " · deploy" : ""}</span></div>
      <div class="cmsg user"><div class="bubble">${h((j.request || "").slice(0, 700))}</div></div>
      <div class="cmsg agent">${j.log ? `<details class="termwrap"${live ? " open" : ""}><summary>▤ Terminal session</summary><pre class="term">${h((j.log || "").slice(-6000))}</pre></details>` : ""}${files}${url}</div>
    </div>`;
  }

  function setupAsk(pid) {
    const run = document.getElementById("askRun");
    if (!run) return;
    const ta = document.getElementById("askText"), thread = document.getElementById("chatThread");
    const shot = document.getElementById("askShot"), shotName = document.getElementById("askShotName");
    let shotData = "";
    const controls = { disable: () => { run.disabled = true; ta.disabled = true; }, enable: () => { run.disabled = false; ta.disabled = false; } };

    // a live session appended to the thread: user bubble + streaming slot
    const appendLiveSess = (reqText) => {
      const th = document.getElementById("chatThread"); if (!th) return document.createElement("div");
      const empty = th.querySelector(".sc-empty"); if (empty) empty.remove();
      const wrap = el(`<div class="csess live"><div class="csess-head"><span class="cs-title">${h((reqText || "Session").split("\n")[0].slice(0, 58))}</span><span class="cs-meta">now · running</span></div>
        <div class="cmsg user"><div class="bubble">${h((reqText || "").slice(0, 700))}</div></div>
        <div class="cmsg agent"><div class="liveout"></div></div></div>`);
      th.appendChild(wrap);
      th.scrollTop = th.scrollHeight;
      return wrap.querySelector(".liveout");
    };

    // render past sessions (oldest → newest, like a chat)
    const renderThread = async () => {
      let jobs = [];
      try { jobs = (await api("/api/agent/jobs?project=" + encodeURIComponent(pid) + "&logs=1&limit=20")).jobs || []; } catch (_) {}
      const th = document.getElementById("chatThread"); if (!th) return;
      const liveEls = Array.from(th.querySelectorAll(".csess.live"));   // keep in-flight sessions
      const hist = jobs.filter(j => !isLive(j)).reverse();
      th.innerHTML = hist.map((j, i) => sessHTML(j, i)).join("")
        || (liveEls.length ? "" : `<div class="sc-empty" style="color:var(--soft)">No sessions yet. Ask for your first change below.</div>`);
      liveEls.forEach(e2 => th.appendChild(e2));
      th.querySelectorAll("[data-ren]").forEach(b => b.onclick = (e) => {
        e.stopPropagation();
        renameUI(b.closest(".csess-head").querySelector("[data-title]"), hist[parseInt(b.dataset.ren, 10)], "cs-title");
      });
      th.scrollTop = th.scrollHeight;
    };
    const refresh = () => { _chats = null; paintSideChats(); renderThread(); };
    renderThread().then(() => {
      // re-attach to a session left running when the page was reloaded — as a
      // live chat turn (its history twin, if merged already, gets replaced)
      resumeJob(pid, (j) => {
        const th = document.getElementById("chatThread");
        const dup = th && th.querySelector(`[data-sess="${j.id}"]`); if (dup) dup.remove();
        return appendLiveSess(j.request || "Session");
      }, controls, refresh);
    });

    if (shot) shot.onchange = async () => {
      const f = shot.files && shot.files[0];
      if (!f) { shotData = ""; shotName.textContent = ""; return; }
      try { shotData = await readFile(f); shotName.textContent = "📎 " + f.name; } catch (_) { shotData = ""; shotName.textContent = "couldn't read image"; }
    };
    run.onclick = async () => {
      const text = ta.value.trim();
      if (!text) { toast("Describe the change first"); return; }
      ta.value = "";
      const slot = appendLiveSess(text);
      chainChangeDeploy(pid, { endpoint: "/api/businesses/" + encodeURIComponent(pid) + "/request", body: { text, image: shotData } }, slot, controls)
        .then(refresh);
      shotData = ""; shotName.textContent = ""; if (shot) shot.value = "";
    };
    // "Make all N changes" — combine every unread note into one change, deploy
    const fixAll = document.getElementById("fixAllBtn");
    if (fixAll) {
      const fixOut = document.getElementById("fixAllOut");
      const fc = { disable: () => { fixAll.disabled = true; }, enable: () => { fixAll.disabled = false; } };
      fixAll.onclick = () => chainChangeDeploy(pid, { endpoint: "/api/businesses/" + encodeURIComponent(pid) + "/fix-all", body: {} }, fixOut, fc).then(refresh);
    }
  }

  // ---- deploy: preview URL, then a separate deliberate push to production ----
  const DEP_LABEL = { "vercel": "Vercel", "cloudflare-pages": "Cloudflare Pages", "cloudflare-workers": "Cloudflare Workers", "auto": "Auto-detected on deploy", "agent": "Claude works it out", "none": "No deploy setup" };
  async function setupDeploy(pid) {
    const info = document.getElementById("depInfo"); if (!info) return;
    const btns = document.getElementById("depBtns"), out = document.getElementById("depOut");
    let d; try { d = await api("/api/businesses/" + encodeURIComponent(pid) + "/deploy/info"); } catch (e) { info.textContent = "Couldn't check deploy setup."; return; }
    const named = DEP_LABEL[d.method] || d.method;
    if (d.method === "none") { info.innerHTML = `${h(named)}. ${h(d.note || "")}`; return; }
    if (d.method === "agent") info.innerHTML = `No standard deploy config, so a <b>Claude Code session</b> figures out the right way (sets it up if needed) and deploys it. Preview first, then push to production.`;
    else info.innerHTML = `Deploys via <b>${h(named)}</b>${d.ready === false ? ` — <span style="color:var(--warn)">${h(d.note || "needs one-time link")}</span>` : ""}. Preview first, then push to production.`;
    btns.style.display = "flex";
    const jobOut = (title) => { out.innerHTML = `<div class="askjob"><div class="askstatus"><span class="spin sm"></span>${h(title)}</div></div>`; };
    const runDep = async (prod) => {
      document.getElementById("depPreview").disabled = true; document.getElementById("depProd").disabled = true;
      jobOut(prod ? "Deploying to production…" : "Building a preview…");
      let r; try { r = await api("/api/businesses/" + encodeURIComponent(pid) + "/deploy?prod=" + (prod ? "true" : "false"), { method: "POST" }); } catch (e) { out.innerHTML = `<div class="askjob err">Couldn't start deploy.</div>`; reEnable(); return; }
      if (!r.ok) { out.innerHTML = `<div class="askjob err">${h(r.error || "Deploy unavailable")}</div>`; reEnable(); return; }
      pollDep(r.job, prod);
    };
    const reEnable = () => { document.getElementById("depPreview").disabled = false; document.getElementById("depProd").disabled = false; };
    async function pollDep(jid, prod) {
      let j; try { j = await api("/api/agent/jobs/" + jid); } catch (e) { return; }
      const active = j.status === "queued" || j.status === "running";
      const urlHTML = j.url ? `<div class="askchanged"><b>${prod ? "Live" : "Preview"} URL:</b> <a href="${h(j.url)}" target="_blank" rel="noopener">${h(j.url)}</a></div>` : "";
      out.innerHTML = `<div class="askjob ${j.status}"><div class="askstatus">${active ? '<span class="spin sm"></span>' : (j.status === "done" ? "✓ " : "⚠ ")}${active ? (prod ? "Deploying…" : "Building preview…") : (j.status === "done" ? (prod ? "Live" : "Preview ready") : j.status)}</div>
        ${j.log ? `<pre class="asklog">${h(j.log.slice(-3000))}</pre>` : ""}${urlHTML}</div>`;
      if (active) setTimeout(() => pollDep(jid, prod), 3000);
      else { reEnable(); if (j.status === "done") toast(prod ? "Deployed to production" : "Preview ready"); }
    }
    document.getElementById("depPreview").onclick = () => runDep(false);
    // two-click confirm for the live push (deliberate, but no native modal)
    const prodBtn = document.getElementById("depProd");
    let prodArmed = false, prodT = null;
    prodBtn.onclick = () => {
      if (!prodArmed) { prodArmed = true; const t = prodBtn.textContent; prodBtn.textContent = "Confirm live push"; prodBtn.classList.add("danger"); prodT = setTimeout(() => { prodArmed = false; prodBtn.textContent = t; prodBtn.classList.remove("danger"); }, 3500); return; }
      clearTimeout(prodT); prodArmed = false;
      runDep(true);
    };
  }

  // ---- MENTAL: how you talk about yourself / your patterns ----
  async function showMental() {
    shell("mental", topbar("Mental") + skels(3) + "</div>");
    let d; try { d = await api("/api/mental"); } catch (e) { view().innerHTML = topbar("Mental") + `<div class="empty">Couldn't load.</div></div>`; return; }
    const items = d.items || [];
    const patterns = (d.patterns || "").trim();
    const warming = d.warming || (d.analyzed < d.total);
    const intro = `<div class="hero"><div class="hero-main"><h1>Mental</h1>
      <div class="p-blurb">Everything you say about yourself — your mindset, mood, motivation, and the patterns in how you think and work. Pulled automatically from your notes.</div>
      <div class="dmeta">${d.count} reflection${d.count != 1 ? "s" : ""}${warming ? ` · analyzing ${d.analyzed}/${d.total}…` : ""}</div></div></div>`;
    const patternsHTML = patterns
      ? `<div class="panel mentalpat"><h2>Your patterns</h2><div class="patbody">${patterns.split("\n").filter(l => l.trim()).map(l => `<div class="patline">${h(l.replace(/^[-•*]\s*/, ""))}</div>`).join("")}</div></div>`
      : (d.count >= 2 ? `<div class="panel mentalpat"><h2>Your patterns</h2><div class="soft" style="font-size:13px">Synthesizing your recurring patterns…</div></div>` : "");
    const list = items.length
      ? `<div class="panel">${items.map(n => `<div class="pnote" data-note="${h(n.id)}"><span class="pn-dot" style="background:var(--accent)"></span>
          <div><div class="pn-h">${n.insight ? h(n.insight) : h(n.headline)}</div>
          <div class="pn-m">${h(n.headline)} · ${h(rel(n.created_at))}</div></div></div>`).join("")}</div>`
      : `<div class="empty">${warming ? "Reading your notes for self-reflection…" : "Nothing personal captured yet. Talk about how you're feeling or working and it shows up here."}</div>`;
    shell("mental", topbar("Mental", warming ? `<button class="btn ghost sm" id="mRefresh">↻ Refresh</button>` : "") + intro + patternsHTML + list + "</div>");
    view().querySelectorAll("[data-note]").forEach(c => c.onclick = () => { location.hash = "#/note/" + c.dataset.note; });
    const rb = document.getElementById("mRefresh"); if (rb) rb.onclick = () => showMental();
    if (warming) setTimeout(() => { if (location.hash.startsWith("#/mental")) showMental(); }, 6000);
  }

  // ---- BELIEFS: the product playbook — how Orion builds great products ----
  let _beliefsPoll = null;
  async function showBeliefs() {
    shell("beliefs", topbar("Beliefs") + skels(3) + "</div>");
    let d; try { d = await api("/api/beliefs"); } catch (e) { view().innerHTML = topbar("Beliefs") + `<div class="empty">Couldn't load.</div></div>`; return; }
    const items = d.items || [];
    const warming = d.warming || (d.analyzed < d.total);
    view().innerHTML = topbar("Beliefs", `<button class="btn sm" id="bCopy">⧉ Copy guide</button>`) +
      `<div class="hero"><div class="hero-main"><h1>How you build great products</h1>
        <div class="p-blurb">Your product playbook, business-focused. Paste in the beliefs you build by; anything you say in a note about how to build winners gets added automatically.</div>
        <div class="dmeta">${items.length} from your notes${warming ? ` · reading ${d.analyzed}/${d.total}…` : ""}</div></div></div>
      <div class="panel"><h2>Your playbook</h2>
        <div class="soft" style="font-size:13px;margin:-4px 0 10px">The overall guide, in your words. Edit or paste freely; it saves as one document.</div>
        <textarea id="bText" class="search-in" style="min-height:240px;resize:vertical" placeholder="e.g. Pin what the user will FEEL using it, and build toward that feeling. Launch to a specific audience, they shape who buys next…"></textarea>
        <div style="display:flex;gap:10px;margin-top:10px;align-items:center"><button class="btn primary" id="bSave">Save playbook</button><span id="bStat" class="soft" style="font-size:12px"></span></div></div>
      <div class="panel mentalpat"><h2>From your notes</h2>
        <div class="soft" style="font-size:13px;margin:-4px 0 10px">Beliefs you've said out loud, pulled from your recordings. ✕ removes one for good.</div>
        <div id="bAuto"></div></div></div>`;
    document.getElementById("bText").value = d.text || "";
    const paintAuto = () => {
      const box = document.getElementById("bAuto"); if (!box) return;
      box.innerHTML = items.length
        ? `<div class="patbody">${items.map((it, i) => `<div class="patline bline">
            <span class="bl-t">${h(it.text)}</span>
            <span class="bl-src" data-note="${h(it.note)}" title="Open the note this came from">${h(it.headline)} · ${h(rel(it.created_at))}</span>
            <button class="icbtn bl-x" data-bx="${i}" title="Remove">✕</button></div>`).join("")}</div>`
        : `<div class="soft" style="font-size:13px">${warming ? "Reading your notes for product beliefs…" : "Nothing yet. Say how you build products in a note and it lands here."}</div>`;
      box.querySelectorAll(".bl-src").forEach(s => s.onclick = () => { location.hash = "#/note/" + s.dataset.note; });
      box.querySelectorAll("[data-bx]").forEach(b => b.onclick = async () => {
        const i = parseInt(b.dataset.bx, 10);
        try { await api("/api/beliefs/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: items[i].text }) }); }
        catch (_) { toast("Couldn't remove"); return; }
        items.splice(i, 1); paintAuto(); toast("Removed");
      });
    };
    paintAuto();
    const saveText = async (quiet) => {
      const stat = document.getElementById("bStat");
      try {
        await api("/api/beliefs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: document.getElementById("bText").value }) });
        if (!quiet) toast("Playbook saved");
        if (stat) { stat.textContent = "Saved " + new Date().toLocaleTimeString(); }
      } catch (_) { if (!quiet) toast("Couldn't save"); }
    };
    document.getElementById("bSave").onclick = () => saveText(false);
    document.getElementById("bText").onblur = () => saveText(true);   // never lose a paste
    document.getElementById("bCopy").onclick = async () => {
      const L = [];
      const t = document.getElementById("bText").value.trim();
      if (t) { L.push("HOW I BUILD GREAT PRODUCTS"); L.push(""); L.push(t); }
      if (items.length) { L.push(""); L.push("FROM MY NOTES"); items.forEach(it => L.push("• " + it.text)); }
      const ok = await copyText(L.join("\n").trim() || "No beliefs yet.");
      toast(ok ? "Guide copied" : "Copy failed");
    };
    clearTimeout(_beliefsPoll);
    if (warming) _beliefsPoll = setTimeout(() => { if (location.hash.startsWith("#/beliefs")) showBeliefs(); }, 8000);
  }

  // ---- SEARCH ----
  let _allNotes = null;
  async function showSearch() {
    shell("search", topbar("Search") + `<input class="search-in" id="q" placeholder="Search your notes…" autocomplete="off" /><div id="results" style="margin-top:16px"></div></div>`);
    const q = document.getElementById("q"), res = document.getElementById("results");
    if (!_allNotes) { try { _allNotes = (await api("/api/data/notes?limit=500")).notes || []; } catch (e) { return; } }
    const run = () => {
      const t = q.value.trim().toLowerCase();
      if (!t) { res.innerHTML = `<div class="hint">Type to search across every note.</div>`; return; }
      const hits = _allNotes.filter(n => {
        const hay = [(n.headline || ""), (n.summary || ""), ...(n.key_points || []), ...(n.people || []).map(p => p.name || "")].join(" ").toLowerCase();
        return hay.includes(t);
      });
      res.innerHTML = hits.length ? `<div class="feed">${hits.map(noteCard).join("")}</div>` : `<div class="empty">No matches for “${h(q.value)}”.</div>`;
      res.querySelectorAll("[data-note]").forEach(c => c.onclick = () => { location.hash = "#/note/" + c.dataset.note; });
    };
    q.oninput = run; q.focus(); run();
  }

  // ---- upload / import ----
  function pickUpload() {
    // multi-select: import one OR many voice notes in a single pick
    const inp = document.createElement("input"); inp.type = "file"; inp.accept = "audio/*,video/*"; inp.multiple = true;
    inp.onchange = async () => { const files = Array.from(inp.files || []); if (files.length) await doImport(files); };
    inp.click();
  }
  function bindUpload() { const b = view().querySelector('[data-act="upload"]'); if (b) b.onclick = pickUpload; }
  async function doUpload(file) {
    toast("Uploading…");
    const fd = new FormData(); fd.append("file", file);
    try {
      await api("/api/upload", { method: "POST", body: fd });
      toast("Uploaded — processing"); _allNotes = null;
      location.hash = "#/notes"; showNotes();
    } catch (e) { toast("Upload failed"); }
  }
  // import several audio/video files at once — uploaded one by one so each gets
  // queued and processed independently; a single failure doesn't sink the rest.
  async function doImport(files) {
    if (files.length === 1) return doUpload(files[0]);
    let ok = 0, fail = 0;
    for (let i = 0; i < files.length; i++) {
      toast(`Importing ${i + 1}/${files.length}…`);
      const fd = new FormData(); fd.append("file", files[i]);
      try { await api("/api/upload", { method: "POST", body: fd }); ok++; }
      catch (e) { fail++; }
    }
    _allNotes = null;
    toast(`Imported ${ok} note${ok != 1 ? "s" : ""}${fail ? `, ${fail} failed` : ""} — processing`);
    location.hash = "#/notes"; showNotes();
  }

  // ---- router ----
  function route() {
    if (!token) return showLogin();
    const hash = location.hash || "#/notes";
    const m = hash.slice(2).split("/"); // strip "#/"
    const [page, arg] = m;
    if (page === "note" && arg) return showNote(arg);
    if (page === "project" && arg) return showProject(arg);
    if (page === "projects") return showProjects();
    if (page === "chats") return showChats();
    if (page === "beliefs") return showBeliefs();
    if (page === "mental") return showMental();
    if (page === "search") return showSearch();
    return showNotes();
  }
  window.addEventListener("hashchange", route);
  // warm the projects badge count once
  if (token) { api("/api/businesses").then(d => { newCount = (d.projects || []).reduce((a, g) => a + (g.new_count || 0), 0); if (newCount && location.hash.startsWith("#/notes")) route(); }).catch(() => {}); }
  route();
})();
