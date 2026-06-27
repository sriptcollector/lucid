/* Lucid v2 — calm personal conversation-memory journal. Vanilla JS. */
const App = (() => {
  const app = document.getElementById("app");
  const toastEl = document.getElementById("toast");

  // token from deep link (?k=) kept for backwards-compat; harmless if unused
  const u0 = new URL(location.href);
  if (u0.searchParams.get("k")) { localStorage.setItem("lucid_token", u0.searchParams.get("k"));
    u0.searchParams.delete("k"); history.replaceState({}, "", u0.pathname + u0.hash); }
  let token = localStorage.getItem("lucid_token") || "";

  // theme (system → dark → light) + live status-bar color
  const metaTheme=document.querySelector('meta[name="theme-color"]');
  const PAPER={light:"#faf9f5",dark:"#211d1b"};
  const applyTheme = () => { const t = localStorage.getItem("lucid_theme");
    if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme");
    const dark = t==="dark" || (!t && matchMedia("(prefers-color-scheme:dark)").matches);
    if (metaTheme) metaTheme.setAttribute("content", dark?PAPER.dark:PAPER.light); };
  applyTheme();
  matchMedia("(prefers-color-scheme:dark)").addEventListener?.("change",()=>{ if(!localStorage.getItem("lucid_theme")) applyTheme(); });
  document.getElementById("themeBtn").onclick = () => {
    const cur = localStorage.getItem("lucid_theme");
    const next = cur === "dark" ? "light" : cur === "light" ? "" : "dark";
    next ? localStorage.setItem("lucid_theme", next) : localStorage.removeItem("lucid_theme"); applyTheme();
  };

  // relevance gate — Lucid records ALL audio (incl. audio dramas, therapy, social). By default
  // surface only work-signal: a business keyword AND no clear personal marker. Toggle to see all.
  const _BIZ_RE=/\b(ai|a\.i\.|claude|llm|agentic|agents?|automation|automate|consult\w*|clients?|project|software|saas|strateg\w*|campaign|integration|onboard\w*|contract\w*|invest\w*|funding|startup|founders?|business|proposal|pricing|launch\w*|marketing|sales|product\w*|deliverables?|contractor|demo|deal\b|revenue|partner\w*|pitch|investor\w*|roadmap|hire|hiring)\b/i;
  const _PERSONAL_RE=/\b(audio drama|therapy|therapist|counsel\w*|screenplay|girlfriend|boyfriend|dating|kiss|marry|prank|bedtime|gossip)\b/i;
  let workOnly = localStorage.getItem("lucid_all") !== "1";
  const _wtxt=(r)=>((r&&r.headline)||"")+" "+((r&&r.summary)||"")+" "+(((r&&r.topics)||[]).join(" "));
  const recWork=(r)=>{ const t=_wtxt(r); return _BIZ_RE.test(t) && !_PERSONAL_RE.test(t); };
  const aiWork=(t)=>{ const r=t&&cache.find(x=>x.id===t.note_id);
    if(r) return recWork(r);
    const s=((t&&t.note_headline)||"")+" "+((t&&t.text)||"");
    return _BIZ_RE.test(s) && !_PERSONAL_RE.test(s); };
  const keepRec=(r)=> !workOnly || recWork(r);
  const keepAi=(t)=> !workOnly || aiWork(t);
  function bindWorkBtn(){
    const b=document.getElementById("workBtn"); if(!b) return;
    b.textContent = workOnly ? "Work" : "All";
    b.classList.toggle("on", workOnly);
    b.title = workOnly ? "Showing work-relevant — tap for all" : "Showing all — tap for work-relevant";
    b.onclick = ()=>{ workOnly=!workOnly; localStorage.setItem("lucid_all", workOnly?"0":"1"); bindWorkBtn(); route(); };
  }

  // helpers
  const h = (s) => (s == null ? "" : String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"));
  const fmt = (sec) => { sec = Math.max(0, Math.floor(sec||0));
    const m=Math.floor(sec/60), s=sec%60, hh=Math.floor(m/60), mm=m%60;
    return hh ? `${hh}:${String(mm).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${mm}:${String(s).padStart(2,"0")}`; };
  const rel = (iso) => { if(!iso) return ""; const d=new Date(iso), diff=(Date.now()-d)/1000;
    if (diff<60) return "just now"; if (diff<3600) return `${Math.floor(diff/60)}m ago`;
    if (diff<86400) return `${Math.floor(diff/3600)}h ago`;
    if (diff<7*86400) return `${Math.floor(diff/86400)}d ago`;
    const sy=d.getFullYear()===new Date().getFullYear();
    return d.toLocaleDateString(undefined,{month:"short",day:"numeric",...(sy?{}:{year:"numeric"})}); };
  const dayBucket = (iso) => { const d=new Date(iso); const now=new Date();
    const sd=(x)=>new Date(x.getFullYear(),x.getMonth(),x.getDate()).getTime();
    const days=Math.round((sd(now)-sd(d))/86400000);
    if (days<=0) return "Today"; if (days===1) return "Yesterday"; if (days<7) return "This week";
    if (days<30) return "This month"; return "Earlier"; };
  const toast = (m, action) => {
    clearTimeout(toast._t);
    if(action && action.label){
      toastEl.innerHTML=`<span class="toast-msg"></span><button class="toast-act"></button>`;
      toastEl.querySelector(".toast-msg").textContent=m;
      const btn=toastEl.querySelector(".toast-act"); btn.textContent=action.label;
      btn.onclick=()=>{ toastEl.classList.remove("show"); clearTimeout(toast._t); try{ action.run(); }catch(_){} };
      toastEl.classList.add("show");
      toast._t=setTimeout(()=>toastEl.classList.remove("show"), action.ms||6000);
    } else {
      toastEl.textContent=m; toastEl.classList.add("show");
      toast._t=setTimeout(()=>toastEl.classList.remove("show"),2200);
    }
  };
  async function api(path, opts={}) { const headers=opts.headers||{};
    if (token) headers["Authorization"]="Bearer "+token;
    const res=await fetch(path,{...opts,headers});
    if (res.status===401||res.status===403) throw new Error("auth");
    if (!res.ok) throw new Error((await res.text())||res.status);
    const ct=res.headers.get("content-type")||""; return ct.includes("json")?res.json():res; }

  // mood
  const POS=/(upbeat|positive|warm|friendly|calm|optimis|enthusias|collaborat|happy|support|light|excited|hopeful|relaxed|cordial|playful|grateful)/i;
  const NEG=/(tense|defensiv|conflict|anxious|frustrat|hostile|heated|negativ|sad|confront|awkward|guard|stress|angry|tension|uneasy|somber|distress)/i;
  function mood(rec){ const a=rec.analysis||rec; const s=(a.sentiment||"")+" "+(a.headline||"");
    if (NEG.test(s)) return {k:"tense", c:"var(--ten)", w:"tense"};
    if (POS.test(s)) return {k:"positive", c:"var(--pos)", w:"positive"};
    return {k:"neutral", c:"var(--neu)", w:"calm"}; }
  const kc = (k) => `var(--${k==="topic_shift"?"topic":k})`;
  const ringHTML = (c,pct,glyph="∿") =>
    `<div class="ring" style="--mc:${c};--mp:${pct}%"><span class="glyph">${glyph}</span></div>`;

  // ===== SHELL: dateline masthead + live subline (the Broadsheet opener) =====
  const _MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const _DOW=["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
  const weekNo=(d)=>{ const x=new Date(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()));
    x.setUTCDate(x.getUTCDate()+4-(x.getUTCDay()||7)); const ys=new Date(Date.UTC(x.getUTCFullYear(),0,1));
    return Math.ceil((((x-ys)/86400000)+1)/7); };
  const datelineStr=(d=new Date())=>`${_DOW[d.getDay()]} · ${_MON[d.getMonth()]} ${d.getDate()} · WK ${weekNo(d)}`.toUpperCase();
  const setSubline=(t)=>{ const el=document.getElementById("subline"); if(el) el.textContent=t; };
  // Shared masthead — every department opens with the same dateline + serif headline.
  // title/note may contain trusted HTML (e.g. counts in <b>); greeting is plain text.
  const masthead=({title,note="",greeting="",wide=false,refresh=true})=>`<div class="hero${wide?" hero--wide":""}">
      ${refresh?`<button class="mast-refresh" title="Refresh" aria-label="Refresh">↻</button>`:""}
      <div class="dateline">${datelineStr()}</div>
      ${greeting?`<div class="greeting">${h(greeting)}</div>`:""}
      <h1>${title}</h1>
      ${note?`<div class="ednote">${note}</div>`:""}</div>`;
  // one delegated handler for every masthead ↻ (survives re-renders)
  function refreshCurrent(){ const p=location.pathname;
    brief=null; cache=[]; try{ crmData=null; }catch(_){}
    toast("Refreshing…"); route(); }
  document.addEventListener("click",(e)=>{ const t=e.target.closest&&e.target.closest(".mast-refresh");
    if(t){ e.preventDefault(); e.stopPropagation(); const ic=t; ic.classList.add("spinning"); refreshCurrent(); } });

  // routing
  let cache=[], pollTimer=null, __navPaint=true;
  let homeFilter="all", homeSort="newest";
  const clearPoll=()=>{ if(pollTimer){clearTimeout(pollTimer); pollTimer=null;} };
  const setTab=(n)=>{
    document.querySelectorAll(".tabbar button").forEach(b=>b.classList.toggle("active",b.dataset.tab===n));
    document.querySelectorAll(".appbar [data-nav]").forEach(b=>b.classList.toggle("on",b.dataset.nav===n));
  };
  const go=(p)=>{ history.pushState({},"",p); route(); };
  window.onpopstate=route;
  document.querySelectorAll(".tabbar button").forEach(b=>b.onclick=()=>go(b.dataset.tab==="home"?"/":"/"+b.dataset.tab));
  document.querySelectorAll(".appbar [data-nav]").forEach(b=>b.onclick=()=>go("/"+b.dataset.nav));

  function route(){ clearPoll(); __navPaint=true; window.scrollTo(0,0); const p=location.pathname;
    // Old top-level routes fold into the Lucid hub — old links/bookmarks keep working.
    const RD={"/notes":"/lucid/notes","/people":"/lucid/people","/directory":"/lucid/directory","/ventures":"/lucid/ideas"};
    if (RD[p]){ history.replaceState({},"",RD[p]); return route(); }
    // Detail views — Lucid tab stays lit, hub switcher hidden.
    const m=p.match(/^\/r\/([\w-]+)/);
    if (m){ setTab("lucid"); showLucidBar(false); return showDetail(m[1]); }
    const pm=p.match(/^\/people\/(.+)$/);
    if (pm){ setTab("lucid"); showLucidBar(false); return showPerson(decodeURIComponent(pm[1])); }
    const vm=p.match(/^\/ventures\/(.+)$/);
    if (vm){ setTab("lucid"); showLucidBar(false); return showVenture(decodeURIComponent(vm[1])); }
    // The Lucid hub: /lucid, /lucid/notes|people|ideas, /lucid/directory.
    const lm=p.match(/^\/lucid(?:\/([a-z]+))?$/);
    if (lm){ setTab("lucid"); showLucidBar(true); let seg=lm[1];
      if (seg==="directory"){ setLucidSeg("people"); return showDirectory(); }
      if (!LUCID_SEGS.includes(seg)){ seg=lucidSeg; history.replaceState({},"","/lucid/"+seg); }
      setLucidSeg(seg); return SEG_RENDER[seg](); }
    // Tools + other domains hide the hub switcher.
    showLucidBar(false);
    if (p==="/search"){ setTab("search"); return showSearch(); }
    if (p==="/review"){ setTab("review"); return showReview(); }
    if (p==="/settings"){ setTab("settings"); return showSettings(); }
    const cm=p.match(/^\/crm\/(.+)$/);
    if (cm){ setTab("crm"); return showCRMContact(decodeURIComponent(cm[1])); }
    if (p==="/crm"){ setTab("crm"); return showCRM(); }
    setTab("home"); return showHome(); }

  // first paint after a route animates; poll/filter repaints don't
  function paintDone(){ const _v=app.querySelector('.view');
    if(__navPaint){ __navPaint=false; } else if(_v){ _v.classList.add('is-repaint'); } }

  // ===== LUCID HUB — Notes · People · Ideas under one tab =====
  const LUCID_SEGS=["notes","people","ideas"];
  const SEG_RENDER={notes:showNotes, people:showPeople, ideas:showVentures};
  let lucidSeg=(()=>{ const s=localStorage.getItem("lucid_seg"); return LUCID_SEGS.includes(s)?s:"notes"; })();
  const lucidbarEl=document.getElementById("lucidbar");
  function setLucidSeg(seg){
    lucidSeg=LUCID_SEGS.includes(seg)?seg:"notes"; localStorage.setItem("lucid_seg",lucidSeg);
    lucidbarEl&&lucidbarEl.querySelectorAll("[data-seg]").forEach(b=>{ const on=b.dataset.seg===lucidSeg;
      b.classList.toggle("on",on); b.setAttribute("aria-selected",on?"true":"false"); });
  }
  function showLucidBar(on){ if(lucidbarEl) lucidbarEl.hidden=!on; document.body.classList.toggle("lucid-active",!!on); }
  lucidbarEl&&lucidbarEl.querySelectorAll("[data-seg]").forEach(b=>
    b.onclick=()=>{ const t="/lucid/"+b.dataset.seg; if(location.pathname!==t) go(t); });
  // keep the sticky hub bar flush under the variable-height app bar
  function syncAppH(){ const ab=document.querySelector(".appbar");
    if(ab) document.documentElement.style.setProperty("--app-h",ab.offsetHeight+"px"); }
  syncAppH(); addEventListener("resize",syncAppH);
  // brand → Today; "/" focuses global search on desktop (search is an app-bar tool, not a tab)
  const _brand=document.getElementById("brandHome"); if(_brand) _brand.onclick=()=>go("/");
  document.addEventListener("keydown",(e)=>{ if(e.key==="/" && !e.metaKey && !e.ctrlKey && !e.altKey
    && !/^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement||{}).tagName||"")
    && location.pathname!=="/search"){ e.preventDefault(); go("/search"); } });

  // ===== CRM (orionscrm roster: clients / leads / network) =====
  let crmData=null, crmFilter="all";
  const CRM_GROUPS=[
    {k:"client",label:"Clients",test:c=>c.is_client},
    {k:"lead",label:"Leads",test:c=>!c.is_client&&c.bucket!=="business"},
    {k:"network",label:"Network",test:c=>c.bucket==="business"},
  ];
  const stageColor=(c)=>{ const s=(c.stage||"").toLowerCase();
    if (c.is_client) return "var(--pos)";
    if (s.includes("refus")||s.includes("ghost")) return "var(--ten)";
    if (c.bucket==="business") return "var(--neu)";
    return "var(--accent)"; };
  function crmDay(iso){ if(!iso) return ""; const d=new Date(iso); return isNaN(d)?"":d.toLocaleDateString(undefined,{month:"short",day:"numeric"}); }
  const crmInitials=(c)=>{ const p=(c.company||c.name||"?").trim().split(/\s+/);
    return (((p[0]||"")[0]||"")+((p[1]||"")[0]||"")).toUpperCase(); };
  const tlColor=(t)=> t.lucid?"var(--pos)":t.kind==="meeting"?"var(--decision)":t.kind==="sms"?"var(--neu)":t.direction==="out"?"var(--accent)":"var(--question)";
  const tlMark =(t)=> t.lucid?"∿":t.kind==="meeting"?"◷":t.kind==="sms"?"○":t.direction==="out"?"↗":"↙";

  const agoLabel=(m)=>{ if(m==null) return ""; if(m<1.5) return "Updated just now";
    if(m<60) return "Updated "+Math.round(m)+"m ago"; const hh=Math.round(m/60);
    return "Updated "+hh+"h ago"; };
  let _crmPoll=null;
  async function crmRefresh(manual){
    clearTimeout(_crmPoll);
    let r; try{ r=await api("/api/crm/board/refresh",{method:"POST"}); }catch(e){ return; }
    if(r && r.available===false){ if(manual) toast("orionscrm not found on this PC"); return; }
    if(manual) toast("Refreshing roster…");
    const cf=document.getElementById("crmRefresh"); if(cf) cf.classList.add("spin");
    const t0=(crmData&&crmData.generated_at)||"", started=Date.now();
    const poll=async()=>{
      let d; try{ d=await api("/api/crm/board"); }catch(e){ return; }
      if(d && d.generated_at && d.generated_at!==t0){ crmData=d;
        if(manual) toast("Roster updated"); if(location.pathname==="/crm") paintCRM(); return; }
      if(Date.now()-started<150000) _crmPoll=setTimeout(poll,4000);
      else { const c=document.getElementById("crmRefresh"); if(c) c.classList.remove("spin"); }
    };
    _crmPoll=setTimeout(poll,4000);
  }

  async function showCRM(){
    app.innerHTML=`<div class="view"><div class="hero"><div class="skhero shimmer"></div></div>${skeletons(3)}</div>`;
    let d; try { d=await api("/api/crm/board"); } catch(e){ return authOrError(e,showCRM); }
    crmData=d; document.getElementById("subline").textContent="CRM";
    paintCRM();
    // auto-refresh only when meaningfully stale (>3h) so opening the CRM doesn't keep firing a
    // full sync; the manual ↻ is always there for on-demand. Avoids surprise Gmail/Notion/API cost.
    if (d.can_refresh && !d.refreshing && (d.age_min==null || d.age_min>180)) crmRefresh(false);
  }

  function crmCard(c){
    const col=stageColor(c);
    const owe=(c.action==="reply"||c.action==="follow_up");
    const meta=[ c.next_meeting?("◷ "+crmDay(c.next_meeting)):"",
      (c.todos&&c.todos.length)?("✓ "+c.todos.length+" to-do"+(c.todos.length>1?"s":"")):"" ].filter(Boolean);
    return `<div class="rcard crmcard" data-email="${h(c.email)}" style="--mc:${col}">
      <div class="tile mono"><span>${h(crmInitials(c))}</span></div>
      <div class="rbody">
        <h3>${h(c.company||c.name)}</h3>
        <div class="snip">${h(c.name!==(c.company||c.name)?c.name+" · ":"")}${h(c.summary||"")}</div>
        <div class="rmeta">
          ${owe?`<span class="chip owe">${h(c.situation||"Owe a reply")}</span>`:""}
          ${owe&&c.draft?`<span class="chip draftready">✍ draft ready</span>`:""}
          ${c.stage?`<span class="chip stage">${h(c.stage)}</span>`:""}
          ${meta.map(m=>`<span class="chip stage">${h(m)}</span>`).join("")}
          <span class="time">${rel(new Date(c.last_ts||Date.now()).toISOString())}</span>
        </div>
      </div></div>`;
  }

  function oweRow(c){
    const col=stageColor(c);
    return `<div class="owerow" data-email="${h(c.email)}" style="--mc:${col}">
      <div class="ow-l">
        <div class="ow-name">${h(c.name||c.company)}</div>
        <div class="ow-sit">${h(c.situation||c.summary||"Needs a response from you")}</div>
      </div>
      <span class="ow-t">${rel(new Date(c.last_ts||Date.now()).toISOString())}</span>
      ${c.draft
        ? `<button class="cta solid" data-copy="${h(c.email)}">✍ Copy draft</button>`
        : `<button class="cta line" data-open="${h(c.email)}">Reply →</button>`}
    </div>`;
  }

  function paintCRM(){
    const d=crmData||{contacts:[],stats:{}};
    if (d.missing){ app.innerHTML=`<div class="view"><div class="hero"><h1>CRM</h1></div>
      <div class="empty"><div class="big">◌</div>No CRM export yet.
      <div class="hint">Run the orionscrm sync — it writes the roster here automatically.</div></div></div>`; return; }
    const all=d.contacts||[], s=d.stats||{};
    const owe=all.filter(c=>c.action==="reply"||c.action==="follow_up")
                 .sort((a,b)=>new Date(b.last_ts||0)-new Date(a.last_ts||0));
    const nextMeet=all.filter(c=>c.next_meeting).length;
    const bits=[];
    if(owe.length) bits.push(`<b>${owe.length}</b> ${owe.length>1?"people owe":"person owes"} you a reply`);
    if(nextMeet)   bits.push(`<b>${nextMeet}</b> meeting${nextMeet>1?"s":""} on the books`);
    bits.push(`<b>${all.length}</b> in your book`);
    setSubline(`${owe.length} owe a reply${nextMeet?` · ${nextMeet} upcoming`:""}`);

    const figs=[
      {n:all.length,c:"Contacts",col:"var(--ink)",f:"all"},
      {n:s.clients||0,c:"Clients",col:"var(--pos)",f:"client"},
      {n:s.leads||0,c:"Leads",col:"var(--accent)",f:"lead"},
      {n:s.network||0,c:"Network",col:"var(--neu)",f:"network"},
      {n:owe.length,c:"Owe a reply",col:"var(--accent)",scroll:true},
    ];
    const ribbon=figs.map(f=>`<button class="statcard${(!f.scroll&&crmFilter===f.f)?" on":""}"
      ${f.scroll?`data-scroll="owelane"`:`data-f="${f.f}"`} style="--mc:${f.col}">
      <span class="figure">${f.n}</span><span class="figcap">${f.c}</span></button>`).join("");

    const oweHTML=owe.length?`<div class="owelane" id="owelane">
      <div class="lanehead">Owe a reply <span class="n">${owe.length}</span></div>
      ${owe.map(oweRow).join("")}</div>`:"";

    const groups=crmFilter==="all"?CRM_GROUPS:CRM_GROUPS.filter(g=>g.k===crmFilter);
    const sections=groups.map(g=>{
      const list=all.filter(g.test);
      if(!list.length) return "";
      return `<div class="daygroup crm-group"><div class="daylabel">${g.label}<span class="n">${list.length}</span></div>
        <div class="feed">${list.map(crmCard).join("")}</div></div>`;
    }).join("");

    const chips=[{k:"all",l:"All"},{k:"client",l:`Clients ${s.clients||0}`},{k:"lead",l:`Leads ${s.leads||0}`},{k:"network",l:`Network ${s.network||0}`}]
      .map(f=>`<button class="fchip ${crmFilter===f.k?"on":""}" data-f="${f.k}">${f.l}</button>`).join("");

    // Review queue — everyone we considered but didn't add, so nothing is silently missed.
    const rev=(d.review||[]);
    const revHTML = (crmFilter==="all"&&rev.length)?`<div class="revsec">
      <div class="lanehead">Miss nothing — we skipped these, promote any that belong <span class="n">${rev.length}</span></div>
      ${rev.slice(0,40).map(r=>`<div class="revrow" data-email="${h(r.email)}">
        <div class="rev-l"><div class="rev-name">${h(r.name||r.company||r.email)}</div>
          <div class="rev-why">${h(r.summary||r.category||"unclear")}${(r.name||r.company)?` · ${h(r.email)}`:""}</div></div>
        <div class="rev-acts">
          <button class="cta line" data-ov="promote">+ Client</button>
          <button class="cta line" data-ov="lead">+ Lead</button>
          <button class="rev-x" data-ov="remove" title="Dismiss">✕</button>
        </div></div>`).join("")}</div>`:"";

    app.innerHTML=`<div class="view crm-board">
      <div class="hero">
        <div class="dateline">${datelineStr()}${d.age_min!=null||d.can_refresh?`<span class="freshsep">·</span><span class="fresh">${h(agoLabel(d.age_min))}</span>${d.can_refresh?`<button class="rfbtn${d.refreshing?" spin":""}" id="crmRefresh" title="Refresh roster">↻</button>`:""}`:""}</div>
        <h1>CRM <span class="count">${all.length} contacts</span></h1>
        <div class="ednote">Your book of business, fresh this morning — ${bits.join(" · ")}.</div>
      </div>
      <div class="figrow">${ribbon}</div>
      ${oweHTML}
      <div class="filterbar">${chips}</div>
      ${sections||`<div class="empty"><div class="big">◌</div>Nothing in this view.</div>`}
      ${revHTML}</div>`;
    paintDone();

    app.querySelectorAll(".statcard[data-f]").forEach(b=>b.onclick=()=>{crmFilter=b.dataset.f;paintCRM();});
    const rf=document.getElementById("crmRefresh"); if(rf) rf.onclick=()=>crmRefresh(true);
    const sc=app.querySelector('.statcard[data-scroll]');
    if(sc) sc.onclick=()=>{const el=document.getElementById("owelane"); if(el) el.scrollIntoView({behavior:"smooth",block:"start"});};
    app.querySelectorAll(".filterbar .fchip").forEach(b=>b.onclick=()=>{crmFilter=b.dataset.f;paintCRM();});
    app.querySelectorAll(".crmcard").forEach(c=>c.onclick=()=>go("/crm/"+encodeURIComponent(c.dataset.email)));
    app.querySelectorAll(".owerow").forEach(r=>r.onclick=()=>go("/crm/"+encodeURIComponent(r.dataset.email)));
    app.querySelectorAll(".owerow .cta[data-open]").forEach(b=>b.onclick=(e)=>{e.stopPropagation();go("/crm/"+encodeURIComponent(b.dataset.open));});
    app.querySelectorAll(".owerow .cta[data-copy]").forEach(b=>b.onclick=(e)=>{
      e.stopPropagation();
      const c=all.find(x=>x.email===b.dataset.copy);
      if(c&&c.draft) navigator.clipboard.writeText(c.draft).then(()=>toast("Draft copied"));
    });
    app.querySelectorAll(".revrow [data-ov]").forEach(b=>b.onclick=(e)=>{
      e.stopPropagation(); const row=b.closest(".revrow"); crmOverride(row.dataset.email, b.dataset.ov);
    });
  }

  async function crmOverride(email, action){
    const el=document.querySelector('.revrow[data-email="'+(window.CSS&&CSS.escape?CSS.escape(email):email)+'"]');
    if(el){ el.style.opacity=".4"; el.style.pointerEvents="none"; }
    try{ await api("/api/crm/board/override",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({email,action})}); }
    catch(e){ if(el){el.style.opacity="";el.style.pointerEvents="";} toast("Couldn't save"); return; }
    if(crmData&&crmData.review) crmData.review=crmData.review.filter(r=>r.email!==email);
    toast(action==="remove"?"Dismissed":action==="lead"?"Added as lead":"Added as client");
    if(action!=="remove") crmRefresh(false);   // pull them into the roster
    if(location.pathname==="/crm") paintCRM();
  }

  function showCRMContact(email){
    const c=((crmData||{}).contacts||[]).find(x=>x.email===email);
    if(!c){ go("/crm"); return; }
    const col=stageColor(c);
    setSubline(c.name||"CRM");

    const ghost=/(refus|ghost)/.test((c.stage||"").toLowerCase());
    const step=c.is_client?3:(c.bucket==="business"?1:2);
    const segs=[1,2,3].map(i=>{
      const on=i<=step, sc=(ghost&&i===step)?"var(--ten)":col;
      return `<span style="flex:1;background:${on?sc:"var(--sink)"}"></span>`;
    }).join("");
    const steps=["Network","Lead","Client"].map((l,i)=>`<span class="${i<step?"on":""}">${l}</span>`).join("");
    const stagePanel=`<div class="panel"><h2>Stage</h2>
      <div class="dmeta" style="margin-bottom:9px"><span class="mc" style="font-weight:650;text-transform:capitalize">${h(c.stage||"—")}</span>
        ${c.next_meeting?`<span>&middot; next ◷ ${h(crmDay(c.next_meeting))}</span>`:""}</div>
      <div class="stagebar"><div class="vbar">${segs}</div><div class="stagesteps">${steps}</div></div></div>`;

    const todos=(c.todos&&c.todos.length)?`<div class="panel"><h2>To-dos from your meetings</h2>
      <ul class="kvlist">${c.todos.map(t=>`<li>${h(t)}</li>`).join("")}</ul></div>`:"";

    const draft=c.draft?`<div class="panel"><h2>Suggested ${c.draft_kind==="follow_up"?"follow-up":"reply"}</h2>
      <div class="qcard letter">
        <div class="qtext">${h(c.draft)}</div>
        <div class="qmeta">
          <span class="qspk">Draft${c.name||c.company?` · to ${h(c.name||c.company)}`:""}</span>
          <button class="cta solid" id="copyDraft">✍ Copy</button>
        </div></div></div>`:"";

    const tl=(c.timeline||[]).slice().reverse().map(t=>{
      const tc=tlColor(t);
      const link=t.link?`<a class="topen" href="${h(t.link)}" target="_blank" rel="noopener">open ↗</a>`:"";
      return `<div class="tinter">
        <div class="tdate" style="--tlc:${tc}">${h(t.date)}</div>
        <div class="tcard" style="--tlc:${tc}">
          <div class="thead"><span class="tmark">${tlMark(t)}</span><b>${h(t.subject||"(no subject)")}</b>${link}</div>
          ${t.summary?`<div class="tsum">${h(t.summary)}</div>`:""}
        </div></div>`;
    }).join("");
    const tlPanel=`<div class="panel"><h2>Timeline</h2>
      <div class="timeline-people crmtl">${tl||`<div class="muted" style="font-size:14px">No history yet.</div>`}</div></div>`;

    const links=[
      c.notion_url?`<a class="btn ghost" href="${h(c.notion_url)}" target="_blank" rel="noopener">Open in Notion ↗</a>`:"",
      c.is_client?"":`<button class="btn ghost" data-ov="promote">Mark as client</button>`,
      `<button class="btn ghost danger" data-ov="remove">Remove from CRM</button>`
    ].filter(Boolean).join("");

    app.innerHTML=`<div class="view detail crm-dossier view--wide" style="--mc:${col}">
      <span class="backlink" onclick="history.back()">&larr; CRM</span>
      <div class="dhero">${ringHTML(col,100,h(crmInitials(c)))}
        <div><h1>${h(c.name)}</h1>
          <div class="dmeta"><span class="mc">${h(c.stage)}</span>
            ${c.company?`<span>&middot; ${h(c.company)}</span>`:""}
            ${c.next_meeting?`<span>&middot; ◷ ${h(crmDay(c.next_meeting))}</span>`:""}
            <span>&middot; ${h(c.email)}</span></div></div></div>
      ${c.summary?`<p class="lead">${h(c.summary)}</p>`:""}
      ${links?`<div class="btnrow" style="margin:12px 0 4px">${links}</div>`:""}
      <div class="dossier">
        <div class="dcol">${stagePanel}${todos}</div>
        <div class="dcol">${draft}${tlPanel}</div>
      </div>
    </div>`;
    const cp=document.getElementById("copyDraft");
    if(cp) cp.onclick=()=>{ navigator.clipboard.writeText(c.draft||"").then(()=>toast("Draft copied")); };
    app.querySelectorAll(".crm-dossier [data-ov]").forEach(b=>b.onclick=()=>{
      const act=b.dataset.ov;
      if(act==="remove" && !confirm(`Remove ${c.name||c.email} from your CRM?`)) return;
      crmOverride(c.email, act);
      go("/crm");
    });
  }

  // ===== HOME =====
  function skeletons(n=4){ return `<div class="feed">${Array(n).fill(0).map(()=>`
    <div class="sk"><div class="c shimmer"></div><div class="l">
      <div class="b shimmer" style="width:70%"></div><div class="b shimmer" style="width:95%"></div>
      <div class="b shimmer" style="width:40%;margin-bottom:0"></div></div></div>`).join("")}</div>`; }

  const FILTERS=[
    {k:"all",label:"All",test:()=>true},
    {k:"people",label:"People",test:r=>(r.people||[]).length>0},
    {k:"ideas",label:"Ideas",test:r=>(r.ideas||0)>0},
    {k:"tasks",label:"Tasks",test:r=>(r.action_items||0)>0},
    {k:"tense",label:"Tense",test:r=>mood(r).k==="tense",dot:"var(--ten)"},
  ];

  async function showNotes(){
    if (!cache.length) app.innerHTML=`<div class="view notes-feed">
      ${masthead({title:"Notes"})}
      <div class="figrow">${Array(4).fill('<div class="statcard"><span class="figure">·</span><span class="figcap">&nbsp;</span></div>').join("")}</div>
      ${skeletons()}</div>`;
    let recs; try { recs=await api("/api/recordings"); } catch(e){ return authOrError(e,showNotes); }
    cache=recs; paintNotes();
    if (recs.some(r=>!["done","error"].includes(r.status))) pollTimer=setTimeout(showNotes,4000);
  }

  function paintNotes(){
    const recs=cache;
    const shown=recs.filter(keepRec);                  // honor Work toggle in every count
    const done=shown.filter(r=>r.status==="done");
    const mins=Math.round(done.reduce((a,r)=>a+(r.duration||0),0)/60);
    const tense=done.filter(r=>mood(r).k==="tense").length;
    const seen=new Set();
    done.forEach(r=>(r.people||[]).forEach(p=>{ const n=(typeof p==="string"?p:(p&&(p.name||p.label)))||"";
      if(n.trim()) seen.add(n.trim().toLowerCase()); }));
    const ppl=seen.size;

    const ft=FILTERS.find(f=>f.k===homeFilter)||FILTERS[0];
    let list=recs.filter(ft.test).filter(keepRec);
    if (homeSort==="oldest") list=[...list].reverse();
    else if (homeSort==="longest") list=[...list].sort((a,b)=>(b.duration||0)-(a.duration||0));

    const bits=[`<b>${shown.length}</b> note${shown.length!==1?"s":""}`];
    if(mins)  bits.push(`<b>${mins}</b> min on the record`);
    if(tense) bits.push(`<b>${tense}</b> tense`);
    const ed = recs.length
      ? `Everything you've captured, sorted and ready — ${bits.join(" · ")}.`
      : "Record on your Plaud and your notes land here automatically — transcribed, summarised, sorted.";
    setSubline(shown.length?`${shown.length} note${shown.length!==1?"s":""}${tense?` · ${tense} tense`:""}`:"notes");

    const figs=[
      {n:shown.length, cap:"Notes",        col:"var(--ink)",      f:"all"},
      {n:mins,        cap:"Min captured",  col:"var(--decision)", sort:"longest"},
      {n:tense,       cap:"Tense",         col:"var(--ten)",      f:"tense"},
      {n:ppl,         cap:"People seen",   col:"var(--accent)",   f:"people"},
    ];
    const ribbon=figs.map(f=>{
      const on = f.sort ? homeSort===f.sort : homeFilter===f.f;
      return `<button class="statcard${on?" on":""}" ${f.sort?`data-sort="${f.sort}"`:`data-f="${f.f}"`} style="--mc:${f.col}">
        <span class="figure">${f.n}</span><span class="figcap">${h(f.cap)}</span></button>`;
    }).join("");

    let body;
    if (!recs.length){
      body=`<div class="empty"><div class="big">◐</div>No notes yet.
        <div class="hint">Record on your Plaud — your notes appear here automatically, sorted and ready.</div></div>`;
    } else if (!list.length){
      const hiddenN=recs.filter(ft.test).filter(r=>!keepRec(r)).length;
      body = (workOnly && hiddenN)
        ? `<div class="empty"><div class="big">◌</div>${hiddenN} note${hiddenN>1?"s":""} hidden by the Work filter.
            <div class="hint"><button class="seeall" id="showAllNotes">Show all notes →</button></div></div>`
        : `<div class="empty"><div class="big">◌</div>Nothing matches this filter.<div class="hint">Try “All”.</div></div>`;
    } else if (homeSort==="newest"){
      const groups={}; list.forEach(r=>{ const b=dayBucket(r.created_at)||"Earlier"; (groups[b]=groups[b]||[]).push(r); });
      const order=["Today","Yesterday","This week","This month","Earlier"];
      body=order.filter(k=>groups[k]).map(k=>`<div class="daygroup">
        <div class="daylabel">${k}<span class="n">${groups[k].length}</span></div>
        <div class="feed">${groups[k].map(cardHTML).join("")}</div></div>`).join("");
    } else {
      body=`<div class="feed">${list.map(cardHTML).join("")}</div>`;
    }

    const filterbar=recs.length?`<div class="filterbar">
      ${FILTERS.map(f=>`<button class="fchip${homeFilter===f.k?" on":""}" data-f="${f.k}">${f.dot?`<span class="c" style="background:${f.dot}"></span>`:""}${f.label}</button>`).join("")}
      <span class="sortsel"><select id="sortSel">
        <option value="newest"${homeSort==="newest"?" selected":""}>Newest</option>
        <option value="oldest"${homeSort==="oldest"?" selected":""}>Oldest</option>
        <option value="longest"${homeSort==="longest"?" selected":""}>Longest</option>
      </select></span></div>`:"";

    app.innerHTML=`<div class="view notes-feed">
      ${masthead({title:`Notes <span class="count">${done.length} sorted</span>`, note:ed})}
      ${recs.length?`<div class="figrow">${ribbon}</div>`:""}
      ${filterbar}${body}</div>`;
    paintDone();
    bindCards();
    app.querySelectorAll(".statcard[data-f]").forEach(b=>b.onclick=()=>{ homeFilter=b.dataset.f; paintNotes(); });
    const ms=app.querySelector(".statcard[data-sort]");
    if(ms) ms.onclick=()=>{ homeSort = homeSort===ms.dataset.sort ? "newest" : ms.dataset.sort; paintNotes(); };
    app.querySelectorAll(".filterbar .fchip").forEach(b=>b.onclick=()=>{ homeFilter=b.dataset.f; paintNotes(); });
    const ss=document.getElementById("sortSel"); if(ss) ss.onchange=()=>{ homeSort=ss.value; paintNotes(); };
    const sa=document.getElementById("showAllNotes");
    if(sa) sa.onclick=()=>{ workOnly=false; localStorage.setItem("lucid_all","1"); bindWorkBtn(); paintNotes(); };
  }

  function cardHTML(r){
    const m=mood(r);
    const done=r.status==="done";
    const title=r.headline||(done?"Untitled":"New recording");
    const topics=(r.topics||[]).slice(0,2).map(t=>`<span class="chip">${h(t)}</span>`).join("");
    const proc=["done","error"].includes(r.status)?"":`<span class="proc"><span class="spin"></span>${h(r.status)}…</span>`;
    const ppl=(r.people||[]).length;
    return `<div class="rcard notecard${done?"":" pending"}" data-id="${r.id}" style="--mc:${m.c}">
      <div class="tile ${m.k}"></div>
      <div class="rbody"><h3>${h(title)}</h3>
        ${r.summary?`<div class="snip">${h(r.summary)}</div>`:""}
        <div class="rmeta">
          ${done?`<span class="chip mood">${m.w}</span>`:proc}
          ${r.duration?`<span class="chip">${fmt(r.duration)}</span>`:""}
          ${ppl?`<span class="chip">${ppl} ${ppl>1?"people":"person"}</span>`:""}
          ${topics}
          <span class="time">${h(rel(r.created_at))}</span>
        </div></div>
        <button class="carddel" data-del="${r.id}" title="Delete note" aria-label="Delete note">&#215;</button></div>`;
  }
  const bindCards=()=>{
    app.querySelectorAll(".rcard").forEach(c=>c.onclick=()=>go("/r/"+c.dataset.id));
    app.querySelectorAll(".carddel").forEach(b=>b.onclick=(e)=>{ e.stopPropagation(); del(b.dataset.del); });
  };

  // ===== HOME — "THE BRIEF" (command center, routed at "/") =====
  let brief=null, _briefFns=[];
  const actAttr=(fn)=>{ _briefFns.push(fn); return `data-bf="${_briefFns.length-1}"`; };
  const bindBrief=()=>app.querySelectorAll("[data-bf]").forEach(el=>{
    el.onclick=(e)=>{ e.stopPropagation(); const f=_briefFns[+el.dataset.bf]; if(f) f(e); }; });
  const crmOwe=(c)=>(c.action==="reply"||c.action==="follow_up");
  const copyDraft=(text)=>()=>navigator.clipboard.writeText(text||"")
    .then(()=>toast("Draft copied")).catch(()=>toast("Copy failed"));
  function evTime(d){ const t=new Date(), same=d.toDateString()===t.toDateString(), hm=d.getHours()||d.getMinutes();
    if(same) return hm?d.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"}):"TODAY";
    return d.toLocaleDateString(undefined,{weekday:"short",month:"short",day:"numeric"})
      +(hm?" · "+d.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"}):""); }

  async function showHome(){
    setTab("home");
    if (!brief) app.innerHTML=`<div class="view view--wide brief">${masthead({title:"The Brief",wide:true})}
      <div class="figrow">${Array(6).fill('<div class="statcard"><span class="figure">·</span></div>').join("")}</div>
      ${skeletons(2)}</div>`;
    let recs; try { recs=await api("/api/recordings"); } catch(e){ return authOrError(e,showHome); }
    cache=recs;
    const grab=(p)=>api(p).then(r=>r).catch(()=>null);
    const [ppl,vens,crm,tasks,cal]=await Promise.all([
      grab("/api/people"), grab("/api/ventures"), grab("/api/crm/board"),
      grab("/api/data/action-items"), grab("/api/cal/status")]);
    brief={ recs, ppl:ppl||[], vens:vens||[],
      crm:(crm&&!crm.missing)?crm:(crm||{contacts:[],stats:{}}),
      tasks:(tasks&&tasks.action_items)||[], cal:cal||{} };
    crmData=brief.crm;                       // so Brief→/crm/:email deep links resolve
    paintBrief();
    if (recs.some(r=>!["done","error"].includes(r.status))) pollTimer=setTimeout(showHome,5000);
  }

  function needsYou(b){
    const out=[], seen=briefSeen(), dt=doneTodos();
    const clear=(msg)=>{ paintBrief(); toast(msg||"Marked read"); };
    (b.crm.contacts||[]).filter(crmOwe).forEach(c=>{ const d=!!c.draft, id="reply:"+c.email+":"+(c.last_ts||"");
      out.push({ id, domain:stageColor(c), mark:"✍", sort:d?0:1,
        kind:d?"Draft ready":(c.action==="follow_up"?"Follow up":"Reply"),
        title:c.name||c.company||c.email, sub:c.situation||c.summary||"",
        cta:d?"Copy draft":"Open", copy:d?c.draft:"", go:()=>go("/crm/"+encodeURIComponent(c.email)),
        done:()=>{ markBrief(id); logActivity("read","Reply",c.name||c.company||c.email,"lucid_seen_brief",id); clear(); } }); });
    (b.recs||[]).filter(r=>r.status==="done"&&mood(r).k==="tense"&&keepRec(r)).slice(0,4).forEach(r=>{ const id="tense:"+r.id;
      out.push({ id, domain:"var(--ten)", mark:"⚠", sort:2, kind:"Tense note",
        title:r.headline||"Conversation", sub:r.summary||"", cta:"Review", go:()=>go("/r/"+r.id),
        done:()=>{ markSeen(r.id); markBrief(id); logActivity("read","Tense note",r.headline||"Conversation",[{store:"lucid_seen_brief",sid:id},{store:"lucid_seen_notes",sid:r.id}]); clear(); } }); });
    (b.vens||[]).filter(v=>!v.has_spec).slice(0,3).forEach(v=>{ const id="idea:"+v.id;
      out.push({ id, domain:"var(--topic)", mark:"◆", sort:3, kind:"Idea",
        title:v.title, sub:v.summary||"No build plan yet", cta:"Open idea",
        go:()=>go("/ventures/"+encodeURIComponent(v.id)),
        done:()=>{ markBrief(id); logActivity("read","Idea",v.title,"lucid_seen_brief",id); clear(); } }); });
    (b.tasks||[]).filter(keepAi).forEach(t=>{ const key=todoId(t), id="todo:"+key; if(dt.has(key)) return;
      out.push({ id, domain:"var(--pos)", mark:"☑", sort:4, kind:"To-do", title:t.text,
        sub:[t.owner?("— "+t.owner):"", t.due?("due "+t.due):"", t.note_headline].filter(Boolean).join(" · "),
        cta:"Open", go:()=>go("/r/"+t.note_id),
        done:()=>{ markTodoDone(key); markBrief(id); logActivity("done","To-do",t.text,[{store:"lucid_done_todos",sid:key},{store:"lucid_seen_brief",sid:"todo:"+key}]); clear("Done ✓"); } }); });
    return out.filter(it=>!seen.has(it.id)).sort((a,c)=>a.sort-c.sort);
  }
  function figures(b){
    const recsK=b.recs.filter(keepRec), done=recsK.filter(r=>r.status==="done"), s=b.crm.stats||{};
    const mins=Math.round(done.reduce((a,r)=>a+(r.duration||0),0)/60);
    const openTodos=(b.tasks||[]).filter(keepAi).filter(t=>!doneTodos().has(todoId(t))).length;
    return [
      {n:recsK.length,  cap:"Notes",        domain:"var(--ink)",      go:()=>go("/notes")},
      {n:b.ppl.length,  cap:"People",        domain:"var(--accent)",   go:()=>go("/people")},
      {n:openTodos,     cap:"Open to-dos",   domain:"var(--pos)",      go:()=>go("/review")},
      {n:s.leads||0,    cap:"Leads",         domain:"var(--accent)",   go:()=>go("/crm")},
      {n:s.clients||0,  cap:"Clients",       domain:"var(--pos)",      go:()=>go("/crm")},
      {n:mins,          cap:"Min captured",  domain:"var(--decision)", go:()=>go("/notes")} ];
  }
  function agenda(b){ const now=Date.now();
    return (b.crm.contacts||[]).filter(c=>c.next_meeting).map(c=>({
        when:new Date(c.next_meeting), name:c.company||c.name, who:c.name,
        domain:stageColor(c), go:()=>go("/crm/"+encodeURIComponent(c.email)) }))
      .filter(e=>!isNaN(e.when) && e.when.getTime()>=now-12*3600*1000)
      .sort((a,c)=>a.when-c.when).slice(0,6); }

  function paintBrief(){
    _briefFns=[];
    const b=brief, done=b.recs.filter(r=>r.status==="done").filter(keepRec), s=b.crm.stats||{};
    const hr=new Date().getHours();
    const greet=hr<12?"Good morning":hr<18?"Good afternoon":"Good evening";
    const who=(b.crm.owner_name||"").split(/\s+/)[0];
    if(!b.recs.length){                                  // cold start — nothing captured yet
      app.innerHTML=`<div class="view view--wide brief">
        ${masthead({title:`${greet}${who?(", "+h(who)):""}.`, note:"Let's get Lucid set up.", wide:true, refresh:false})}
        <div class="welcome-card">
          <div class="wc-mark">✦</div>
          <h2>Welcome to Lucid</h2>
          <p>Your notes, people, to-dos and CRM gather here. Connect a source to begin — it fills in automatically.</p>
          <div class="wc-steps">
            <button class="wc-step" ${actAttr(()=>go("/settings"))}><b>Connect Plaud</b><span>Auto-import &amp; transcribe your recordings</span><i>→</i></button>
            <button class="wc-step" ${actAttr(()=>go("/settings"))}><b>Connect Notion + Calendar</b><span>Sync your CRM and meetings</span><i>→</i></button>
          </div>
        </div></div>`;
      setSubline("welcome"); bindBrief(); paintDone(); return;
    }
    const items=needsYou(b), up=agenda(b);
    const openTodos=(b.tasks||[]).filter(keepAi).filter(t=>!doneTodos().has(todoId(t))).length;
    const owe=(b.crm.contacts||[]).filter(crmOwe).length;
    const mtgs=up.filter(e=>e.when.toDateString()===new Date().toDateString()).length;

    const bits=[];
    if(owe)  bits.push(`<b>${owe}</b> ${owe>1?"people owe":"person owes"} you a reply`);
    if(mtgs) bits.push(`<b>${mtgs}</b> meeting${mtgs>1?"s":""} today`);
    if(openTodos) bits.push(`<b>${openTodos}</b> open to-do${openTodos>1?"s":""}`);
    const ed=bits.length?bits.join(" · "):"You're all caught up. Nothing needs you right now.";
    setSubline(bits.length?[owe&&owe+" need you", mtgs&&mtgs+" today", openTodos&&openTodos+" to-dos"]
        .filter(Boolean).join(" · "):"all clear");

    const ribbon=figures(b).map(f=>`<div class="statcard" style="--domain:${f.domain}" ${actAttr(f.go)}>
        <span class="figure">${f.n}</span><span class="figcap">${h(f.cap)}</span></div>`).join("");

    const queue=items.length ? items.slice(0,8).map(it=>`<div class="qrow" style="--domain:${it.domain}" ${actAttr(it.go)}>
        <span class="qmark">${it.mark}</span>
        <div class="qmain"><div class="qkind">${h(it.kind)}</div>
          <div class="qtitle">${h(it.title)}</div>${it.sub?`<div class="qsub">${h(it.sub)}</div>`:""}</div>
        <div class="qacts">
          ${it.copy?`<button class="qcta solid" ${actAttr(copyDraft(it.copy))}>${h(it.cta)}</button>`
                   :`<button class="qcta" ${actAttr(it.go)}>${h(it.cta)}</button>`}
          ${it.done?`<button class="qread" title="Mark as read" aria-label="Mark as read" ${actAttr(it.done)}>✓</button>`:""}
        </div></div>`).join("")
      : `<div class="allclear"><b>Clear desk.</b> No replies owed, no tense notes, every idea has a plan.</div>`;

    const todayRail = up.length
      ? `<div class="todayrail">${up.map(e=>`<div class="evpill" style="--domain:${e.domain}" ${actAttr(e.go)}>
            <div class="evt">${h(evTime(e.when))}</div><div class="evname">${h(e.name)}</div>
            ${e.who&&e.who!==e.name?`<div class="evwho">${h(e.who)}</div>`:""}</div>`).join("")}</div>`
      : `<div class="railnote">${b.cal.connected?"No meetings on the calendar today."
            :"Upcoming meetings from your CRM appear here. Connect a calendar in Settings for the full agenda."}</div>`;

    const w=n=>(100*n/((s.clients||0)+(s.leads||0)+(s.network||0)||1)).toFixed(1)+"%";
    const pipeline=`<div class="vbar"><span style="width:${w(s.clients||0)};background:var(--pos)"></span>
        <span style="width:${w(s.leads||0)};background:var(--accent)"></span>
        <span style="width:${w(s.network||0)};background:var(--neu)"></span></div>
      <div class="vkey"><span><i style="background:var(--pos)"></i>${s.clients||0} clients</span>
        <span><i style="background:var(--accent)"></i>${s.leads||0} leads</span>
        <span><i style="background:var(--neu)"></i>${s.network||0} network</span></div>
      ${owe?`<div class="minirow" style="--domain:var(--accent)" ${actAttr(()=>go("/crm"))}>
        <span class="spinedot"></span><div class="mtxt"><div class="mt1">${owe} awaiting your reply</div>
        <div class="mt2">Top of your CRM queue</div></div><span class="mtime">→</span></div>`:""}`;

    const empty=(t)=>`<div class="mt2" style="padding:8px 0">${t}</div>`;
    const latest=done.slice(0,3).map(r=>{ const m=mood(r);
      return `<div class="minirow" style="--domain:${m.c}" ${actAttr(()=>go("/r/"+r.id))}>
        <span class="spinedot"></span><div class="mtxt">
          <div class="mt1">${h(r.headline||"Untitled")}</div>
          <div class="mt2">${h(r.summary||m.w)}</div></div>
        <span class="mtime">${h(rel(r.created_at))}</span></div>`; }).join("")||empty("No notes yet.");

    const nurture=[...b.ppl].sort((a,c)=>{ const ac=a.trend==="cooling"?0:1, cc=c.trend==="cooling"?0:1;
        return ac!==cc?ac-cc:new Date(a.last_seen||0)-new Date(c.last_seen||0); })
      .slice(0,3).map(p=>`<div class="minirow" style="--domain:var(--${toneClass(p.tone)})" ${actAttr(()=>go("/people/"+encodeURIComponent(p.key)))}>
        <span class="spinedot"></span><div class="mtxt"><div class="mt1">${h(p.name)}</div>
        <div class="mt2">${trendWord(p.trend)} · ${p.interactions} talk${p.interactions>1?"s":""}</div></div>
        <span class="mtime">${h(rel(p.last_seen))}</span></div>`).join("")||empty("No people yet.");

    const ideas=b.vens.slice(0,3).map(v=>`<div class="minirow" style="--domain:var(--topic)" ${actAttr(()=>go("/ventures/"+encodeURIComponent(v.id)))}>
        <span class="spinedot"></span><div class="mtxt"><div class="mt1">${h(v.title)}</div>
        <div class="mt2">${v.has_spec?"Plan ready":"No plan yet"}${v.status?(" · "+h(v.status)):""}</div></div>
        ${v.has_spec?`<span class="mtime">✓</span>`:""}</div>`).join("")||empty("No ideas yet.");

    const _railX = app.querySelector('.todayrail')?.scrollLeft || 0;
    app.innerHTML=`<div class="view view--wide brief">
      ${masthead({title:`${greet}${who?(", "+h(who)):""}.`, note:ed, wide:true})}
      <button class="reviewcta" ${actAttr(()=>go("/review"))}>
        <span class="rc-ic">◴</span>
        <span class="rc-txt"><b>The day in review</b>
          <span>Today's notes, replies, to-dos &amp; people seen</span></span>
        <span class="rc-go">→</span></button>
      <div class="figrow">${ribbon}</div>
      <div class="deck">
        <div class="panel sp-7"><h2>Needs you${items.length?`<span class="hcount">${items.length}</span>`:""}</h2>
          <div class="queue">${queue}</div></div>
        <div class="sp-5 railcol">
          <div class="panel"><h2>Today</h2>${todayRail}</div>
          <div class="panel"><h2>Pipeline</h2>${pipeline}</div>
        </div>
        <div class="panel sp-4"><h2>Latest notes</h2>${latest}
          <span class="seeall" ${actAttr(()=>go("/notes"))}>All notes →</span></div>
        <div class="panel sp-4"><h2>People to nurture</h2>${nurture}
          <span class="seeall" ${actAttr(()=>go("/people"))}>All people →</span></div>
        <div class="panel sp-4"><h2>Ideas in motion</h2>${ideas}
          <span class="seeall" ${actAttr(()=>go("/ventures"))}>All ideas →</span></div>
      </div></div>`;
    paintDone();
    const _r = app.querySelector('.todayrail'); if(_r && _railX) _r.scrollLeft=_railX;
    bindBrief();
  }

  // ===== UNIFIED SEARCH (notes · people · ideas · CRM contacts) =====
  let searchIdx=null, searchType="all", searchTerm="", _searchSel=-1, _searchFlat=[];
  const STYPES=[
    {k:"note",    label:"Notes",    domain:"var(--ink-soft)", tab:"/notes"},
    {k:"person",  label:"People",   domain:"var(--accent)",   tab:"/people"},
    {k:"idea",    label:"Ideas",    domain:"var(--topic)",    tab:"/ventures"},
    {k:"contact", label:"Contacts", domain:"var(--pos)",      tab:"/crm"},
  ];
  const styp=(k)=>STYPES.find(s=>s.k===k)||STYPES[0];

  async function buildSearchIndex(force){
    if(searchIdx && !force) return searchIdx;
    const grab=(p)=>api(p).then(r=>r).catch(()=>null);
    const [recs,ppl,vens,crm]=await Promise.all([
      grab("/api/recordings"), grab("/api/people"), grab("/api/ventures"), grab("/api/crm/board")]);
    if(Array.isArray(recs)) cache=recs;
    if(crm && !crm.missing) crmData=crm;
    const idx=[];
    (recs||[]).forEach(r=>{ if(!r.headline && !r.summary) return;
      idx.push({ type:"note", title:r.headline||"Untitled note", sub:r.summary||"",
        meta:rel(r.created_at), ts:+new Date(r.created_at||0), domain:mood(r).c,
        go:()=>go("/r/"+r.id),
        blob:_norm([r.headline,r.summary,(r.topics||[]).join(" "),(r.people||[]).join(" ")].join(" ")) }); });
    (ppl||[]).forEach(p=>{ if(!p.name) return;
      idx.push({ type:"person", title:p.name,
        sub:p.role||`${p.interactions} conversation${p.interactions>1?"s":""}`,
        meta:rel(p.last_seen), ts:+new Date(p.last_seen||0), domain:`var(--${toneClass(p.tone)})`,
        go:()=>go("/people/"+encodeURIComponent(p.key)),
        blob:_norm([p.name,p.role,(p.natures||[]).join(" ")].join(" ")) }); });
    (vens||[]).forEach(v=>{ if(!v.title) return;
      idx.push({ type:"idea", title:v.title, sub:v.summary||(v.has_spec?"Build plan ready":"No plan yet"),
        meta:v.has_spec?"plan ✓":"", ts:+new Date(v.last_seen||0), domain:"var(--topic)",
        go:()=>go("/ventures/"+encodeURIComponent(v.id)),
        blob:_norm([v.title,v.summary,(v.people||[]).join(" "),v.status].join(" ")) }); });
    (((crm&&!crm.missing&&crm.contacts))||[]).forEach(c=>{ const nm=c.company||c.name; if(!nm) return;
      idx.push({ type:"contact", title:nm,
        sub:[c.name&&c.name!==nm?c.name:"", c.summary||c.stage||""].filter(Boolean).join(" · "),
        meta:c.stage||"", ts:+new Date(c.last_ts||0), domain:stageColor(c),
        go:()=>go("/crm/"+encodeURIComponent(c.email)),
        blob:_norm([c.name,c.company,c.summary,c.stage,c.email].join(" ")) }); });
    searchIdx=idx; return idx;
  }

  function searchRun(term){
    const t=_norm(term); if(!t) return [];
    const words=t.split(" ").filter(Boolean); const out=[];
    searchIdx.forEach(it=>{
      if(searchType!=="all" && it.type!==searchType) return;
      const title=_norm(it.title); let score=0, ok=true;
      for(const w of words){
        if(!it.blob.includes(w)){ ok=false; break; }
        score += title.startsWith(w)?6 : title.includes(w)?4 : 1;
      }
      if(!ok) return;
      if(title===t) score+=10;
      out.push({it,score});
    });
    out.sort((a,b)=> b.score-a.score || (b.it.ts||0)-(a.it.ts||0));
    return out.map(s=>s.it);
  }

  function searchChipsHTML(){
    const counts={}; searchIdx.forEach(x=>counts[x.type]=(counts[x.type]||0)+1);
    const chips=[{k:"all",l:"All",n:searchIdx.length}]
      .concat(STYPES.map(s=>({k:s.k,l:s.label,n:counts[s.k]||0})));
    return chips.map(c=>`<button class="fchip${searchType===c.k?" on":""}" data-st="${c.k}">${c.l}${c.n?` <span class="fn">${c.n}</span>`:""}</button>`).join("");
  }

  function searchRowHTML(it,i){
    const s=styp(it.type), dom=it.domain||s.domain;
    const sub=[it.sub,it.meta].filter(Boolean).join("  ·  ");
    return `<div class="minirow sres" data-si="${i}" style="--domain:${dom}">
      <span class="spinedot"></span>
      <div class="mtxt"><div class="mt1">${h(it.title)}</div>${sub?`<div class="mt2">${h(sub)}</div>`:""}</div>
      <span class="stype">${s.label}</span></div>`;
  }

  function searchLanding(){
    const recent=[...searchIdx].filter(x=>searchType==="all"||x.type===searchType)
      .sort((a,b)=>(b.ts||0)-(a.ts||0)).slice(0,7);
    if(!recent.length) return `<div class="searchtip">Search everything at once — notes, people, ideas, and CRM contacts. Type a name, a topic, or a company.</div>`;
    const rows=recent.map(it=>{ const i=_searchFlat.length; _searchFlat.push(it); return searchRowHTML(it,i); }).join("");
    return `<div class="panel sgroup"><h2>Recent<span class="hcount">${recent.length}</span></h2>${rows}</div>
      <div class="searchtip">One search across notes, people, ideas &amp; contacts. ↑↓ to move · ↵ to open.</div>`;
  }

  function bindSearchRows(){
    app.querySelectorAll(".sres").forEach(r=>r.onclick=()=>{ const it=_searchFlat[+r.dataset.si]; if(it&&it.go) it.go(); });
    app.querySelectorAll("#sresults .seeall[data-tab]").forEach(b=>b.onclick=()=>go(b.dataset.tab));
  }

  function paintSearch(){
    if(!searchIdx) return;
    const sf=document.getElementById("sfilter"), results=document.getElementById("sresults");
    if(sf){ sf.innerHTML=searchChipsHTML();
      sf.querySelectorAll("[data-st]").forEach(b=>b.onclick=()=>{ searchType=b.dataset.st; _searchSel=-1; paintSearch();
        const q=document.getElementById("q"); if(q) q.focus(); }); }
    _searchFlat=[]; _searchSel=-1;
    const term=searchTerm.trim();
    if(!term){ results.innerHTML=searchLanding(); bindSearchRows(); setSubline("search"); return; }
    const hits=searchRun(term);
    setSubline(hits.length?`${hits.length} result${hits.length>1?"s":""}`:"no matches");
    if(!hits.length){ results.innerHTML=`<div class="empty"><div class="big">⌕</div>No matches for “${h(term)}”.
      <div class="hint">Search spans notes, people, ideas, and contacts. Try a name, a topic, or a company.</div></div>`; return; }
    const order=searchType==="all"?["note","person","idea","contact"]:[searchType];
    let html="";
    order.forEach(k=>{ const items=hits.filter(x=>x.type===k); if(!items.length) return;
      const s=styp(k), cap=searchType==="all"?5:300, shown=items.slice(0,cap);
      const rows=shown.map(it=>{ const i=_searchFlat.length; _searchFlat.push(it); return searchRowHTML(it,i); }).join("");
      const more=items.length>cap?`<button class="seeall" data-tab="${s.tab}">See all ${items.length} in ${s.label} →</button>`:"";
      html+=`<div class="panel sgroup"><h2>${s.label}<span class="hcount">${items.length}</span></h2>${rows}${more}</div>`;
    });
    results.innerHTML=html;
    bindSearchRows();
  }

  function onSearchKey(e){
    const rows=[...app.querySelectorAll(".sres")];
    if(e.key==="ArrowDown"||e.key==="ArrowUp"){ e.preventDefault(); if(!rows.length) return;
      const base=_searchSel<0?(e.key==="ArrowDown"?-1:0):_searchSel;
      _searchSel=Math.max(0,Math.min(rows.length-1, base+(e.key==="ArrowDown"?1:-1)));
      rows.forEach((r,i)=>r.classList.toggle("sel",i===_searchSel));
      rows[_searchSel].scrollIntoView({block:"nearest"});
    } else if(e.key==="Enter"){
      const r=rows[_searchSel<0?0:_searchSel]; if(r){ const it=_searchFlat[+r.dataset.si]; if(it&&it.go) it.go(); }
    } else if(e.key==="Escape"){ const q=document.getElementById("q");
      if(searchTerm){ searchTerm=""; if(q) q.value=""; const c=document.getElementById("sclear"); if(c) c.hidden=true; paintSearch(); }
      else if(q) q.blur();
    }
  }

  async function showSearch(){
    setTab("search");
    app.innerHTML=`<div class="view view--search">
      <div class="searchhead"><div class="searchwrap big"><span class="mag">⌕</span>
        <input id="q" type="search" inputmode="search" enterkeyhint="search"
          placeholder="Search notes, people, ideas, contacts…"
          autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
          value="${attr(searchTerm)}">
        <button class="sclear" id="sclear" aria-label="Clear search"${searchTerm?"":" hidden"}>✕</button></div></div>
      <div class="filterbar" id="sfilter"></div>
      <div id="sresults">${skeletons(3)}</div></div>`;
    const q=document.getElementById("q"), clr=document.getElementById("sclear");
    q.oninput=()=>{ searchTerm=q.value; clr.hidden=!searchTerm.trim(); paintSearch(); };
    q.onkeydown=onSearchKey;
    clr.onclick=()=>{ searchTerm=""; q.value=""; clr.hidden=true; q.focus(); paintSearch(); };
    try{ await buildSearchIndex(true); }catch(e){ return authOrError(e,showSearch); }
    paintSearch();
    setTimeout(()=>{ try{ q.focus(); }catch(_){} },60);
  }

  // ===== REVIEW v2 — actionable, relevance-ranked queue (routed at /review) =====
  // Reuses go,api,toast,rel,mood,stageColor,crmOwe,copyDraft,masthead,setSubline,h,
  // paintDone,skeletons,authOrError,cache,crmData,setTab,crmRefresh.
  let _rvData=null, _rvItems=[], reviewFilter="all";
  const _lsGet=(k)=>{ try{ return JSON.parse(localStorage.getItem(k)||"[]"); }catch(_){ return []; } };
  const _lsSet=(k,a)=>{ try{ localStorage.setItem(k,JSON.stringify(a)); }catch(_){ } };
  const todoId   =(t)=>`${t.note_id||""}::${(t.text||"").slice(0,80)}`;
  const doneTodos=()=>new Set(_lsGet("lucid_done_todos"));
  const seenNotes=()=>new Set(_lsGet("lucid_seen_notes"));
  const markSeen =(id)=>{ const s=new Set(_lsGet("lucid_seen_notes")); s.add(id); _lsSet("lucid_seen_notes",[...s]); };
  const briefSeen=()=>new Set(_lsGet("lucid_seen_brief"));
  const markBrief=(id)=>{ const s=new Set(_lsGet("lucid_seen_brief")); s.add(id); _lsSet("lucid_seen_brief",[...s]); };
  // activity log — a visible history of everything you mark read/done (with undo)
  const activityLog=()=>_lsGet("lucid_activity");
  const logActivity=(type,kind,title,store,sid)=>{ try{ const a=_lsGet("lucid_activity");
    const stores=Array.isArray(store)?store:(store?[{store,sid}]:[]);
    a.unshift({type,kind,title:(title||"").slice(0,140),ts:Date.now(),
      store:stores[0]?stores[0].store:"", sid:stores[0]?stores[0].sid:"", stores});
    _lsSet("lucid_activity",a.slice(0,300)); }catch(_){} };
  const undoActivity=(idx)=>{ const a=_lsGet("lucid_activity"); const e=a[idx]; if(!e) return;
    const stores=e.stores||(e.store?[{store:e.store,sid:e.sid}]:[]);
    stores.forEach(({store,sid})=>{ if(store&&sid){ const s=new Set(_lsGet(store)); s.delete(sid); _lsSet(store,[...s]); } });
    a.splice(idx,1); _lsSet("lucid_activity",a); };
  const clearActivity=()=>_lsSet("lucid_activity",[]);
  const markTodoDone=(key)=>{ const s=new Set(_lsGet("lucid_done_todos")); s.add(key); _lsSet("lucid_done_todos",[...s]); };

  async function showReview(){
    setTab("review");
    app.innerHTML=`<div class="view view--wide review2">
      ${masthead({title:"Review", wide:true})}
      <div class="figrow">${Array(5).fill('<div class="statcard"><span class="figure">·</span><span class="figcap">&nbsp;</span></div>').join("")}</div>
      ${skeletons(3)}</div>`;
    const grab=(p)=>api(p).then(r=>r).catch(()=>null);
    let recs; try{ recs = cache.length ? cache : await api("/api/recordings"); }
    catch(e){ return authOrError(e,showReview); }
    cache=recs;
    const [crm,tasks,vens]=await Promise.all([
      grab("/api/crm/board"), grab("/api/data/action-items"), grab("/api/ventures")]);
    const board=(crm&&!crm.missing)?crm:{contacts:[],stats:{},review:[]};
    crmData=board;
    paintReview({ recs, crm:board, tasks:(tasks&&tasks.action_items)||[], vens:vens||[] });
  }

  function reviewBuild(b){
    const now=Date.now(), today=new Date().toDateString();
    const isToday=(iso)=>{ const d=new Date(iso); return !isNaN(d)&&d.toDateString()===today; };
    const done=doneTodos(), seen=seenNotes(), bseen=briefSeen(), items=[];
    // 1 — contacts who owe you a reply (client > lead > network; draft acts faster)
    (b.crm.contacts||[]).filter(crmOwe).forEach(c=>{
      const rid="reply:"+c.email+":"+(c.last_ts||""); if(bseen.has(rid)) return;
      const client=!!c.is_client, lead=!client&&c.bucket!=="business", draft=!!c.draft;
      const ageH=(now-new Date(c.last_ts||now))/3600000;
      const score=(client?100:lead?80:60)+(draft?8:0)+(ageH<24?6:ageH<72?2:0);
      items.push({ type:"reply", score, glyph:draft?"✍":"✉", domain:stageColor(c),
        kind: client?"Client · owes you a reply" : lead?"Lead · owes you a reply" : "Owes you a reply",
        title:c.name||c.company||c.email, sub:c.situation||c.summary||"Waiting on your reply",
        pills: draft?[{t:"draft ready",cls:"go"}]:[],
        time: rel(new Date(c.last_ts||now).toISOString()),
        open:()=>go("/crm/"+encodeURIComponent(c.email)),
        dismiss:()=>{ markBrief(rid); logActivity("read","Reply",c.name||c.company||c.email,"lucid_seen_brief",rid); },
        actions:[ draft ? {label:"Copy draft", primary:true, run:copyDraft(c.draft)}
          : {label:"Reply →", primary:true, run:()=>go("/crm/"+encodeURIComponent(c.email))} ] });
    });
    // 2 — open to-dos (overdue first)
    (b.tasks||[]).filter(keepAi).forEach(t=>{ const id=todoId(t); if(done.has(id)) return;
      const due=(t.due||"").toLowerCase(); const overdue=/(yesterday|overdue|asap|today|now)/.test(due);
      const fresh=isToday(t.created_at); const score=50+(overdue?20:0)+(fresh?6:0);
      items.push({ type:"todo", score, glyph:"✓", domain: overdue?"var(--caution)":"var(--positive)",
        kind: t.owner?("To-do · "+t.owner):"To-do", title:t.text, sub:t.note_headline||"",
        pills: overdue?[{t:t.due,cls:"warn"}]:[], time: fresh?"new":"",
        open: t.note_id?()=>go("/r/"+t.note_id):null,
        actions:[{label:"Done", primary:true, run:(row)=>completeTodo(id,row,t.text)}] });
    });
    // 3 — tense notes today
    (b.recs||[]).filter(r=>r.status==="done"&&mood(r).k==="tense"&&isToday(r.created_at)&&!seen.has(r.id)&&keepRec(r)).forEach(r=>{
      items.push({ type:"triage", score:72, glyph:"⚠", domain:"var(--critical)",
        kind:"Tense note · review", title:r.headline||"Conversation",
        sub:r.summary||"A tense moment worth a second look", pills:[], time:rel(r.created_at),
        seenId:r.id, open:()=>go("/r/"+r.id),
        dismiss:()=>{ markSeen(r.id); logActivity("read","Tense note",r.headline||"Conversation","lucid_seen_notes",r.id); },
        actions:[{label:"Open →", primary:true, run:()=>{markSeen(r.id);go("/r/"+r.id);}}] });
    });
    // 4 — new notes today to triage
    (b.recs||[]).filter(r=>r.status==="done"&&isToday(r.created_at)&&mood(r).k!=="tense"&&!seen.has(r.id)&&keepRec(r)).slice(0,8).forEach(r=>{
      const m=mood(r);
      items.push({ type:"triage", score:52, glyph:"∿", domain:m.c, kind:"New note · triage",
        title:r.headline||"Untitled", sub:r.summary||m.w, pills:[], time:rel(r.created_at),
        seenId:r.id, open:()=>go("/r/"+r.id),
        dismiss:()=>{ markSeen(r.id); logActivity("read","Note",r.headline||"Untitled","lucid_seen_notes",r.id); },
        actions:[{label:"Open →", run:()=>{markSeen(r.id);go("/r/"+r.id);}}] });
    });
    // 5 — ideas with no build plan yet
    (b.vens||[]).filter(v=>!v.has_spec).slice(0,6).forEach(v=>{
      const iid="idea:"+v.id; if(bseen.has(iid)) return;
      items.push({ type:"idea", score:40, glyph:"◆", domain:"var(--accent)", kind:"Idea · no build plan",
        title:v.title, sub:v.summary||"Turn this into a build plan", pills:[], time:v.last_seen?rel(v.last_seen):"",
        open:()=>go("/ventures/"+encodeURIComponent(v.id)),
        dismiss:()=>{ markBrief(iid); logActivity("read","Idea",v.title,"lucid_seen_brief",iid); },
        actions:[{label:"Generate plan", primary:true, gen:true, run:(row)=>generatePlan(v.id,row)}] });
    });
    // 6 — CRM skip-queue: promote / dismiss (lowest signal)
    (b.crm.review||[]).slice(0,12).forEach(r=>{
      items.push({ type:"promote", score:28, glyph:"＋", domain:"var(--ink-3)", multi:true,
        kind:"Maybe a contact?", title:r.name||r.company||r.email,
        sub:r.summary||r.category||"Skipped — promote if it belongs", pills:[], time:"",
        open:()=>go("/crm/"+encodeURIComponent(r.email)),
        actions:[ {label:"+ Client", primary:true, run:(row)=>crmPromote(r.email,"promote",row)},
                  {label:"+ Lead", run:(row)=>crmPromote(r.email,"lead",row)},
                  {label:"✕", x:true, run:(row)=>crmPromote(r.email,"remove",row)} ] });
    });
    return items.sort((a,c)=>c.score-a.score);
  }

  const RV_FILTERS=[
    {f:"all",   cap:"All",     col:"var(--ink)"},
    {f:"reply", cap:"Replies", col:"var(--accent)"},
    {f:"todo",  cap:"To-dos",  col:"var(--positive)"},
    {f:"triage",cap:"Triage",  col:"var(--critical)"},
    {f:"idea",  cap:"Ideas",   col:"var(--accent-ink)"},
  ];
  const RV_BUCKETS=[
    {k:"now",  label:"Decide now",        test:s=>s>=66},
    {k:"soon", label:"Soon",              test:s=>s>=38&&s<66},
    {k:"fyi",  label:"When you have time", test:s=>s<38},
  ];

  function rvRow(it){
    const pills=(it.pills||[]).map(p=>`<span class="rv-pill ${p.cls||""}">${h(p.t)}</span>`).join("");
    const meta=(pills||it.time)?`<div class="rv-meta">${pills}${it.time?`<span class="rv-time">${h(it.time)}</span>`:""}</div>`:"";
    const acts=(it.actions||[]).map((a,ai)=>
      `<button class="rv-cta${a.primary?" is-primary":""}${a.x?" is-x":""}" data-act="${ai}"${a.gen?' data-gen="1"':''}>${h(a.label)}</button>`).join("");
    const dismiss=it.dismiss?`<button class="rv-dismiss" title="Mark read" aria-label="Mark read" data-dismiss="1">✓</button>`:"";
    return `<div class="rvitem${it.multi?" has-multi":""}" data-i="${it._i}" style="--domain:${it.domain}">
      <span class="rv-chip"><span>${it.glyph}</span></span>
      <button class="rv-main"><div class="rv-kind">${h(it.kind)}</div>
        <div class="rv-title">${h(it.title)}</div>
        ${it.sub?`<div class="rv-sub">${h(it.sub)}</div>`:""}${meta}</button>
      <div class="rv-act">${acts}${dismiss}</div></div>`;
  }

  function paintReview(b){
    _rvData=b;
    const all=reviewBuild(b); all.forEach((it,i)=>it._i=i); _rvItems=all;
    const counts={reply:0,todo:0,triage:0,idea:0,promote:0};
    all.forEach(it=>counts[it.type]=(counts[it.type]||0)+1);
    const now=new Date();
    const nNow=all.filter(it=>it.score>=66).length;
    const fil = reviewFilter==="all" ? all : all.filter(it=>it.type===reviewFilter);
    const bits=[];
    if(counts.reply)  bits.push(`<b>${counts.reply}</b> repl${counts.reply>1?"ies":"y"} owed`);
    if(counts.todo)   bits.push(`<b>${counts.todo}</b> to-do${counts.todo>1?"s":""}`);
    if(counts.triage) bits.push(`<b>${counts.triage}</b> to review`);
    if(counts.idea)   bits.push(`<b>${counts.idea}</b> idea${counts.idea>1?"s":""}`);
    const ed = all.length
      ? (nNow?`<b>${nNow}</b> need${nNow>1?"":"s"} you now — `:"")+(bits.join(" · ")||"a few odds and ends")+"."
      : "Nothing needs you. You're all caught up.";
    setSubline(all.length
      ? [nNow&&nNow+" now", counts.reply&&counts.reply+" owed", counts.todo&&counts.todo+" to-dos"].filter(Boolean).join(" · ")
      : "all clear");
    const ribbon=[{f:"all",n:all.length}].concat(RV_FILTERS.slice(1).map(x=>({f:x.f,n:counts[x.f]||0})))
      .map(x=>{ const def=RV_FILTERS.find(r=>r.f===x.f)||{};
        return `<button class="statcard${reviewFilter===x.f?" on":""}" data-f="${x.f}" style="--mc:${def.col}">
          <span class="figure">${x.n}</span><span class="figcap">${h(def.cap)}</span></button>`; }).join("")
      + `<button class="statcard${reviewFilter==="done"?" on":""}" data-f="done" style="--mc:var(--ink-3)">
          <span class="figure">${activityLog().length}</span><span class="figcap">Done</span></button>`;
    let body;
    if(reviewFilter==="done"){
      body=renderActivity(activityLog());
    } else if(!all.length){
      const filed=(b.recs||[]).filter(r=>r.status==="done"&&new Date(r.created_at).toDateString()===now.toDateString()).length;
      body=`<div class="rv-empty"><div class="rv-empty-mark">✓</div>
        <div class="rv-empty-t">Nothing needs you.</div>
        <div class="rv-empty-s">${filed?`${filed} note${filed>1?"s":""} filed today · no replies owed, every idea has a plan.`:"No replies owed, no open to-dos, every idea has a plan."}</div></div>`;
    } else if(!fil.length){
      body=`<div class="rv-empty"><div class="rv-empty-mark">◌</div>
        <div class="rv-empty-t">Clear in this filter.</div>
        <div class="rv-empty-s">Tap “All” to see everything that wants you.</div></div>`;
    } else {
      body=RV_BUCKETS.map(bk=>{ const rows=fil.filter(it=>bk.test(it.score)); if(!rows.length) return "";
        return `<div class="daygroup rvbucket rvb-${bk.k}">
          <div class="daylabel">${bk.label}<span class="n">${rows.length}</span></div>
          <div class="rvqueue">${rows.map(rvRow).join("")}</div></div>`; }).join("");
    }
    const doneN=(b.tasks||[]).filter(t=>doneTodos().has(todoId(t))).length;
    const doneBar = (reviewFilter!=="done" && doneN) ? `<div class="rv-donebar"><span>✓ ${doneN} to-do${doneN>1?"s":""} done today</span>
      <button class="rv-undo" id="rvUndo">Undo last</button></div>` : "";
    app.innerHTML=`<div class="view view--wide review2">
      ${masthead({title:"Review", note:ed, wide:true})}
      <div class="figrow">${ribbon}</div>${body}${doneBar}</div>`;
    paintDone();
    app.querySelectorAll(".figrow .statcard[data-f]").forEach(s=>s.onclick=()=>{ reviewFilter=s.dataset.f; paintReview(_rvData); });
    app.querySelectorAll(".rvitem").forEach(row=>{ const it=_rvItems[+row.dataset.i]; if(!it) return;
      const main=row.querySelector(".rv-main");
      if(main) main.onclick=()=>{ if(it.seenId) markSeen(it.seenId); it.open&&it.open(); };
      row.querySelectorAll(".rv-cta[data-act]").forEach(btn=>btn.onclick=(e)=>{
        e.stopPropagation(); const a=it.actions[+btn.dataset.act]; if(a&&a.run) a.run(row,btn); });
      const dz=row.querySelector("[data-dismiss]");
      if(dz) dz.onclick=(e)=>{ e.stopPropagation(); if(it.dismiss) it.dismiss(); toast("Marked read"); collapseRow(row, ()=>paintReview(_rvData)); }; });
    const ub=document.getElementById("rvUndo");
    if(ub) ub.onclick=()=>{ const log=_lsGet("lucid_activity");
      const i=log.findIndex(e=>e.type==="done"&&(e.store==="lucid_done_todos"||(e.stores||[]).some(s=>s.store==="lucid_done_todos")));
      if(i>=0) undoActivity(i); else { const arr=_lsGet("lucid_done_todos"); arr.pop(); _lsSet("lucid_done_todos",arr); }
      toast("Undone"); paintReview(_rvData); };
    app.querySelectorAll(".actrow [data-undo]").forEach(b=>b.onclick=(e)=>{ e.stopPropagation(); undoActivity(+b.dataset.undo); toast("Restored"); paintReview(_rvData); });
    const ac=document.getElementById("actClear");
    if(ac) ac.onclick=()=>{ if(!confirm("Clear your done/read history?")) return; clearActivity(); toast("History cleared"); paintReview(_rvData); };
  }

  function relTs(ts){ try{ return rel(new Date(ts).toISOString()); }catch(_){ return ""; } }
  function renderActivity(log){
    if(!log.length) return `<div class="rv-empty"><div class="rv-empty-mark">◌</div>
      <div class="rv-empty-t">Nothing cleared yet.</div>
      <div class="rv-empty-s">Everything you mark read or done shows up here — with an undo.</div></div>`;
    const today=new Date().toDateString(), yd=new Date(Date.now()-864e5).toDateString();
    const dl=(d)=> d===today?"Today":d===yd?"Yesterday":new Date(d).toLocaleDateString(undefined,{weekday:"long",month:"short",day:"numeric"});
    const groups={};
    log.forEach((e,i)=>{ const d=new Date(e.ts).toDateString(); (groups[d]=groups[d]||[]).push(Object.assign({_idx:i},e)); });
    return Object.keys(groups).map(d=>`<div class="daygroup">
        <div class="daylabel">${dl(d)}<span class="n">${groups[d].length}</span></div>
        <div class="rvqueue">${groups[d].map(e=>`<div class="rvitem actrow" style="--domain:${e.type==="done"?"var(--positive)":"var(--ink-3)"}">
          <span class="rv-chip"><span>${e.type==="done"?"✓":"·"}</span></span>
          <div class="rv-main"><div class="rv-kind">${h(e.kind)}</div><div class="rv-title">${h(e.title)}</div>
            <div class="rv-meta"><span class="rv-time">${h(relTs(e.ts))}</span></div></div>
          ${e.store?`<div class="rv-act"><button class="rv-cta" data-undo="${e._idx}">Undo</button></div>`:""}
        </div>`).join("")}</div></div>`).join("")
      + `<div class="rv-donebar"><span>${log.length} cleared</span><button class="rv-undo" id="actClear">Clear history</button></div>`;
  }

  function collapseRow(row, after){
    if(!row){ after&&after(); return; }
    if(matchMedia("(prefers-reduced-motion:reduce)").matches){ after&&after(); return; }
    row.style.maxHeight=row.scrollHeight+"px"; row.classList.add("rv-gone");
    requestAnimationFrame(()=>{ row.style.maxHeight="0px"; });
    let fired=false; const fin=()=>{ if(fired)return; fired=true; after&&after(); };
    row.addEventListener("transitionend",fin,{once:true}); setTimeout(fin,340);
  }
  function completeTodo(id,row,title){
    const arr=_lsGet("lucid_done_todos"); if(!arr.includes(id)){ arr.push(id); _lsSet("lucid_done_todos",arr); }
    logActivity("done","To-do",title||"To-do","lucid_done_todos",id);
    toast("Done ✓"); collapseRow(row, ()=>paintReview(_rvData));
  }
  async function generatePlan(vid,row){
    const btn=row.querySelector('.rv-cta[data-gen]');
    if(btn){ btn.disabled=true; btn.innerHTML='<span class="spin"></span>Building…'; }
    try{ await api("/api/ventures/"+encodeURIComponent(vid)+"/build",{method:"POST"}); }
    catch(e){ toast("Couldn't build the plan"); if(btn){ btn.disabled=false; btn.textContent="Generate plan"; } return; }
    (_rvData.vens||[]).forEach(v=>{ if(v.id===vid) v.has_spec=true; });
    toast("Build plan ready ✓"); collapseRow(row, ()=>paintReview(_rvData));
  }
  async function crmPromote(email,action,row){
    try{ await api("/api/crm/board/override",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({email,action})}); }
    catch(e){ toast("Couldn't save"); return; }
    if(_rvData.crm&&_rvData.crm.review) _rvData.crm.review=_rvData.crm.review.filter(r=>r.email!==email);
    if(crmData&&crmData.review)         crmData.review=crmData.review.filter(r=>r.email!==email);
    toast(action==="remove"?"Dismissed":action==="lead"?"Added as lead":"Added as client");
    if(action!=="remove" && typeof crmRefresh==="function") crmRefresh(false);
    collapseRow(row, ()=>paintReview(_rvData));
  }

  // ===== PEOPLE (relationships over time) =====
  const toneClass = (t)=> t==="warm"?"pos":t==="strained"?"ten":"neu";
  const toneWord  = (t)=> t==="warm"?"warm":t==="strained"?"strained":t==="mixed"?"mixed":"neutral";
  const trendWord = (t)=> t==="warming"?"↗ warming":t==="cooling"?"↘ cooling":"→ steady";
  const dateShort = (iso)=>{ if(!iso) return ""; const d=new Date(iso);
    return d.toLocaleDateString(undefined,{month:"short",day:"numeric"}); };
  const pInitials = (name)=>{ const p=String(name||"?").trim().split(/\s+/);
    return ((((p[0]||"")[0]||"")+((p[1]||"")[0]||"")).toUpperCase())||"?"; };
  const daysSince = (iso)=> iso ? (Date.now()-new Date(iso))/86400000 : 1e9;
  function valBar(p){ const tot=(p.positive||0)+(p.negative||0)+(p.neutral||0);
    if(!tot) return `<div class="vbar empty"></div>`;
    const w=(n)=>(100*n/tot).toFixed(1)+"%";
    return `<div class="vbar">
      <span style="width:${w(p.positive)};background:var(--pos)"></span>
      <span style="width:${w(p.neutral)};background:var(--neu)"></span>
      <span style="width:${w(p.negative)};background:var(--ten)"></span></div>`; }

  let pplCache=[], dirCache=[], peopleMode="rel", selMode=false, sel=new Set(), suggestions=null;
  let peopleFilter="all";
  const PEOPLE_GROUPS=[
    {k:"watch", label:"Needs care", test:p=>p.tone==="strained"||p.tone==="mixed"||p.trend==="cooling"},
    {k:"warm",  label:"Warm",       test:p=>!(p.tone==="strained"||p.tone==="mixed"||p.trend==="cooling") && p.tone==="warm"},
    {k:"steady",label:"Steady",     test:p=>!(p.tone==="strained"||p.tone==="mixed"||p.trend==="cooling") && p.tone!=="warm"},
  ];
  function nudgeRow(p){
    const col=`var(--${toneClass(p.tone)})`;
    const why=p.trend==="cooling"?"Cooling lately":`Last talked ${dateShort(p.last_seen)}`;
    return `<div class="owerow" data-key="${attr(p.key)}" style="--mc:${col}">
      <div class="ow-l">
        <div class="ow-name">${h(p.name)}</div>
        <div class="ow-sit">${h(why)}${p.role?` · ${h(p.role)}`:""}</div>
      </div>
      <span class="ow-t">${h(dateShort(p.last_seen))}</span>
      <button class="cta line" data-open="${attr(p.key)}">Open &rarr;</button>
    </div>`;
  }
  const peopleSeg=()=>`<div class="subseg" id="pseg" role="tablist" aria-label="People view">
      <button data-pm="rel" class="${peopleMode==="rel"?"on":""}" role="tab" aria-selected="${peopleMode==="rel"}">Mentions</button>
      <span class="subseg-sep">&middot;</span>
      <button data-pm="dir" class="${peopleMode==="dir"?"on":""}" role="tab" aria-selected="${peopleMode==="dir"}">Directory</button></div>`;
  function bindSeg(){ const s=document.getElementById("pseg"); if(!s) return;
    s.querySelectorAll("[data-pm]").forEach(b=>b.onclick=()=>go(b.dataset.pm==="dir"?"/lucid/directory":"/lucid/people")); }

  async function showPeople(){
    peopleMode="rel";
    app.innerHTML=`<div class="view people-board">${masthead({title:"People"})}${peopleSeg()}
      <div class="figrow">${Array(5).fill('<div class="statcard"><span class="figure">·</span></div>').join("")}</div>${skeletons(3)}</div>`;
    bindSeg();
    let ppl; try { ppl=await api("/api/people"); } catch(e){ return authOrError(e,showPeople); }
    pplCache=ppl; renderPeople();
  }

  async function showDirectory(){
    peopleMode="dir"; selMode=false; sel.clear();
    app.innerHTML=`<div class="view people-board">${masthead({title:"People"})}${peopleSeg()}
      <div class="figrow stats-static">${Array(4).fill('<div class="statcard"><span class="figure">·</span></div>').join("")}</div>${skeletons(3)}</div>`;
    bindSeg();
    let dir; try { dir=await api("/api/directory"); } catch(e){ return authOrError(e,showDirectory); }
    dirCache=dir; renderDirectory();
  }

  function renderDirectory(){
    const dir=dirCache;
    const known   = dir.filter(e=>e.recognition!=="new").length;
    const learning= dir.filter(e=>e.recognition==="learning").length;
    const voiced  = dir.filter(e=>e.has_voice).length;
    setSubline(dir.length?`${dir.length} learned`:"directory");
    const ed = dir.length
      ? `Lucid is learning <b>${dir.length}</b> ${dir.length===1?"voice":"voices"} — ${known} recognized, ${voiced} with a voiceprint.`
      : "As recordings come in, Lucid learns each person's voice and way of speaking.";
    const figs=[
      {n:dir.length, c:"Known"},
      {n:known,      c:"Recognized", col:"var(--pos)"},
      {n:learning,   c:"Learning",   col:"var(--accent)"},
      {n:voiced,     c:"Voiceprints",col:"var(--decision)"},
    ];
    const ribbon=figs.map(f=>`<div class="statcard" style="--mc:${f.col||"var(--ink)"};--domain:${f.col||"var(--ink)"}">
      <span class="figure">${f.n}</span><span class="figcap">${h(f.c)}</span></div>`).join("");

    let body;
    if(!dir.length){
      body=`<div class="empty"><div class="big">&#9737;</div>Nothing learned yet.
        <div class="hint">As recordings come in, Lucid learns each person's voice and way
        of speaking — and remembers every name you set.</div></div>`;
    } else {
      body=`<div class="feed">${dir.map(e=>{
        const words=(e.top_words||[]).slice(0,8).map(w=>`<span class="word">${h(w)}</span>`).join("");
        const phr=(e.phrases||[]).map(p=>`<div class="dirphrase">&ldquo;${h(p)}&rdquo;</div>`).join("");
        const al=(e.aliases||[]).length?`<div class="aliasrow">${e.aliases.map(a=>`<span class="aliaschip">aka ${h(a)}</span>`).join("")}</div>`:"";
        const voice=e.has_voice?`<span>&#127908; voice &times;${e.voice_samples}</span>`:"";
        const col=e.recognition==="strong"?"var(--pos)":e.recognition==="learning"?"var(--accent)":"var(--neu)";
        return `<div class="dircard" data-id="${attr(e.id)}" style="--mc:${col}">
          <div class="dirtop"><div class="tile mono"><span>${h(pInitials(e.name))}</span></div>
            <div class="dirid"><div class="nm">${h(e.name)}</div>
              ${e.role?`<div class="snip">${h(e.role)}</div>`:""}</div>
            <span class="recbadge ${e.recognition}">${e.recognition}</span></div>
          ${al}
          <div class="dirmeta"><span><b>${e.seen_count}</b> conversation${e.seen_count===1?"":"s"}</span>
            ${e.corrections?`<span><b>${e.corrections}</b> correction${e.corrections===1?"":"s"}</span>`:""}
            ${voice}</div>
          ${words?`<div class="dirsec"><div class="lbl">How they speak</div><div class="wordcloud">${words}</div></div>`:""}
          ${phr?`<div class="dirsec"><div class="lbl">Things they've said</div>${phr}</div>`:""}
          <button class="dirforget" data-forget="${attr(e.id)}">Forget this person</button>
        </div>`;
      }).join("")}</div>`;
    }
    app.innerHTML=`<div class="view people-board">
      ${masthead({title:`People <span class="count">${dir.length} learned</span>`, note:ed})}
      ${peopleSeg()}
      <div class="figrow stats-static">${ribbon}</div>
      ${body}</div>`;
    paintDone();
    bindSeg();
    app.querySelectorAll("[data-forget]").forEach(b=>b.onclick=async()=>{
      if(!confirm("Forget everything learned about this person? (their recordings stay)")) return;
      try{ await api("/api/directory/"+encodeURIComponent(b.dataset.forget),{method:"DELETE"}); toast("Forgotten"); showDirectory(); }
      catch(e){ toast("Failed"); }
    });
  }

  function pcardHTML(p){
    const col=`var(--${toneClass(p.tone)})`;
    const nat=(p.natures||[]).slice(0,2).map(n=>`<span class="chip">${h(n)}</span>`).join("");
    const checked=sel.has(p.key);
    const box=selMode?`<span class="pcheck${checked?" on":""}">${checked?"&#10003;":""}</span>`:"";
    return `<div class="rcard pcard${selMode?" selmode":""}${checked?" picked":""}" data-key="${attr(p.key)}" style="--mc:${col}">
      ${box}<div class="tile mono"><span>${h(pInitials(p.name))}</span></div>
      <div class="rbody">
        <h3>${h(p.name)} <span class="pcount">&times;${p.interactions}</span></h3>
        ${p.role?`<div class="snip">${h(p.role)}</div>`:""}
        ${valBar(p)}
        <div class="rmeta">
          <span class="chip mood">${toneWord(p.tone)}</span>
          <span class="chip trend">${trendWord(p.trend)}</span>
          ${nat}
          <span class="time">${h(rel(p.last_seen))}</span>
        </div></div></div>`;
  }

  function renderPeople(){
    const ppl=pplCache;
    const nameOf=(k)=>(ppl.find(p=>p.key===k)||{}).name||k;

    const watch  = ppl.filter(PEOPLE_GROUPS[0].test).length;
    const warm   = ppl.filter(PEOPLE_GROUPS[1].test).length;
    const steady = ppl.filter(PEOPLE_GROUPS[2].test).length;
    const nudge  = ppl.filter(p=>p.trend==="cooling"||daysSince(p.last_seen)>30)
                      .sort((a,b)=>new Date(a.last_seen||0)-new Date(b.last_seen||0)).slice(0,8);

    if(!ppl.length){
      setSubline("relationships");
      app.innerHTML=`<div class="view people-board">
        ${masthead({title:"People", note:"Your relationships, gathered from every conversation."})}
        ${peopleSeg()}
        <div class="empty"><div class="big">&#9737;</div>No people yet.
          <div class="hint">As you record conversations, the people in them — and how
          your relationships evolve — gather here.</div></div></div>`;
      bindSeg(); paintDone(); return;
    }

    const bits=[];
    if(warm)         bits.push(`<b>${warm}</b> warm`);
    if(watch)        bits.push(`<b>${watch}</b> need${watch===1?"s":""} care`);
    if(nudge.length) bits.push(`<b>${nudge.length}</b> to reconnect`);
    const ed=bits.length?bits.join(" · ")+".":"Your roster is calm — nothing needs a nudge.";
    setSubline(`${ppl.length} relationship${ppl.length>1?"s":""}${nudge.length?` · ${nudge.length} to reconnect`:""}`);

    const figs=[
      {n:ppl.length,  c:"People",     col:"var(--ink)",    f:"all"},
      {n:warm,        c:"Warm",       col:"var(--pos)",    f:"warm"},
      {n:steady,      c:"Steady",     col:"var(--neu)",    f:"steady"},
      {n:watch,       c:"Needs care", col:"var(--ten)",    f:"watch"},
      {n:nudge.length,c:"Reconnect",  col:"var(--accent)", scroll:"nudgelane"},
    ];
    const ribbon=figs.map(f=>`<button class="statcard${(!f.scroll&&peopleFilter===f.f)?" on":""}"
      ${f.scroll?`data-scroll="${f.scroll}"`:`data-f="${f.f}"`} style="--mc:${f.col};--domain:${f.col}">
      <span class="figure">${f.n}</span><span class="figcap">${f.c}</span></button>`).join("");

    const nudgeHTML=nudge.length?`<div class="owelane" id="nudgelane">
      <div class="lanehead">Reconnect <span class="n">${nudge.length}</span></div>
      ${nudge.map(nudgeRow).join("")}</div>`:"";

    let sugHTML="";
    if (suggestions){
      sugHTML = suggestions.length ? `<div class="panel sugpanel"><h2>Possible duplicates · AI</h2>
        ${suggestions.map((g,i)=>`<div class="sugitem">
          <div class="sugnames">${g.members.map(k=>`<span class="chip">${h(nameOf(k))}</span>`).join("<span class='plus'>+</span>")}</div>
          <div class="sugreason">${h(g.reason)} → keep <b>${h(g.canonical_name||nameOf(g.members[0]))}</b></div>
          <button class="btn sugmerge" data-sug="${i}">Combine these</button>
        </div>`).join("")}</div>`
        : `<div class="panel sugpanel"><h2>Possible duplicates · AI</h2>
          <p class="muted" style="font-size:14px;margin:0">No likely duplicates found — your roster looks clean.</p></div>`;
    }

    const tools=`<div class="ptools">
        <button class="btn ghost" id="findDup">&#10022; Find duplicates</button>
        <button class="btn ghost" id="selBtn">${selMode?"Done":"Select"}</button>
      </div>`;

    const groups=peopleFilter==="all"?PEOPLE_GROUPS:PEOPLE_GROUPS.filter(g=>g.k===peopleFilter);
    const sections=groups.map(g=>{
      const list=ppl.filter(g.test);
      if(!list.length) return "";
      return `<div class="daygroup people-group"><div class="daylabel">${g.label}<span class="n">${list.length}</span></div>
        <div class="feed">${list.map(pcardHTML).join("")}</div></div>`;
    }).join("");

    app.innerHTML=`<div class="view people-board">
      ${masthead({title:`People <span class="count">${ppl.length}</span>`, note:ed})}
      ${peopleSeg()}
      <div class="figrow">${ribbon}</div>
      ${nudgeHTML}
      ${tools}${sugHTML}
      ${sections||`<div class="empty"><div class="big">&#9737;</div>Nothing in this view.</div>`}
      ${selMode&&sel.size?`<div class="selbar"><span>${sel.size} selected</span>
        <button class="btn" id="combineBtn" ${sel.size<2?"disabled":""}>Combine</button>
        <button class="btn ghost" id="deleteBtn">Delete</button></div>`:""}</div>`;
    paintDone();
    bindSeg();
    app.querySelectorAll(".statcard[data-f]").forEach(b=>b.onclick=()=>{peopleFilter=b.dataset.f;renderPeople();});
    const scb=app.querySelector('.statcard[data-scroll]');
    if(scb) scb.onclick=()=>{const el=document.getElementById(scb.dataset.scroll); if(el) el.scrollIntoView({behavior:"smooth",block:"start"});};
    app.querySelectorAll(".pcard").forEach(c=>c.onclick=()=>{
      const k=c.dataset.key;
      if(selMode){ sel.has(k)?sel.delete(k):sel.add(k); renderPeople(); }
      else go("/people/"+encodeURIComponent(k));
    });
    app.querySelectorAll(".owerow").forEach(r=>r.onclick=()=>go("/people/"+encodeURIComponent(r.dataset.key)));
    app.querySelectorAll(".owerow .cta[data-open]").forEach(b=>b.onclick=(e)=>{e.stopPropagation();go("/people/"+encodeURIComponent(b.dataset.open));});
    const sb=document.getElementById("selBtn"); if(sb) sb.onclick=()=>{ selMode=!selMode; if(!selMode) sel.clear(); renderPeople(); };
    const fd=document.getElementById("findDup"); if(fd) fd.onclick=findDuplicates;
    const cb=document.getElementById("combineBtn"); if(cb) cb.onclick=doCombine;
    const db=document.getElementById("deleteBtn"); if(db) db.onclick=doDelete;
    app.querySelectorAll(".sugmerge").forEach(b=>b.onclick=()=>applySuggestion(suggestions[parseInt(b.dataset.sug)]));
  }

  async function findDuplicates(){
    const btn=document.getElementById("findDup"); if(btn){ btn.disabled=true; btn.textContent="✦ Thinking…"; }
    try { suggestions=await api("/api/people/suggest"); }
    catch(e){ toast("Couldn't analyze"); suggestions=null; }
    renderPeople();
  }
  async function applySuggestion(g){
    if(!g) return;
    try{ await api("/api/people/merge",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({keys:g.members, into:g.canonical_name||""})});
      toast("Combined"); suggestions=null; cache=[]; showPeople();
    }catch(e){ toast("Merge failed"); }
  }
  async function doCombine(){
    const keys=[...sel]; const names=keys.map(k=>(pplCache.find(p=>p.key===k)||{}).name).filter(Boolean);
    const into=await namePicker({ title:"Combine into one person",
      sub:`Merging ${names.join(" + ")}. Choose the name to keep.`, value:names[0]||"" });
    if(!into) return;
    try{ await api("/api/people/merge",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({keys, into})});
      selMode=false; sel.clear(); suggestions=null; cache=[]; toast("Combined"); showPeople();
    }catch(e){ toast(e.message==="auth"?"Auth required":"Combine failed"); }
  }
  async function doDelete(){
    const keys=[...sel];
    if(!confirm(`Remove ${keys.length} ${keys.length>1?"people":"person"} from your relationships?\n(reversible — they're just hidden)`)) return;
    try{ for(const k of keys) await api(`/api/people/${encodeURIComponent(k)}`,{method:"DELETE"});
      selMode=false; sel.clear(); toast("Removed"); showPeople();
    }catch(e){ toast("Delete failed"); }
  }

  async function showPerson(key){
    app.innerHTML=`<div class="view detail person-dossier view--wide">
      <span class="backlink" onclick="App.go('/lucid/people')">&larr; People</span>${skeletons(2)}</div>`;
    let p; try { p=await api("/api/people/"+encodeURIComponent(key)); } catch(e){ return authOrError(e,()=>showPerson(key)); }
    const col=`var(--${toneClass(p.tone)})`;
    setSubline(p.name||"People");
    const span = p.first_seen===p.last_seen ? dateShort(p.first_seen)
      : `${dateShort(p.first_seen)} – ${dateShort(p.last_seen)}`;
    const natures=(p.natures||[]).map(n=>`<span class="chip">${h(n)}</span>`).join("");
    const arc = p.trend==="warming" ? "This relationship has been warming over time."
      : p.trend==="cooling" ? "This relationship has been cooling over time."
      : p.tone==="warm" ? "Consistently warm across your conversations."
      : p.tone==="strained" ? "Has carried recurring tension."
      : "Steady across your conversations.";

    const tot=(p.positive||0)+(p.negative||0)+(p.neutral||0);
    const warmPct=tot?Math.round(100*(p.positive||0)/tot):0;
    const figs=[
      {n:p.interactions,                c:"Conversations", col:"var(--ink)"},
      {n:p.positive||0,                 c:"Positive",      col:"var(--pos)"},
      {n:p.negative||0,                 c:"Concerning",    col:"var(--ten)"},
      {n:`${warmPct}<small>%</small>`,  c:"Warm share",    col:col},
    ];
    const ribbon=figs.map(f=>`<div class="statcard" style="--mc:${f.col};--domain:${f.col}">
      <span class="figure">${f.n}</span><span class="figcap">${h(f.c)}</span></div>`).join("");

    const tl = (p.timeline||[]).map(i=>{
      const ic=mood({sentiment:i.sentiment,headline:i.headline}).c;
      const rels=(i.relationship||[]).map(r=>`<div class="reldyn">
          <div class="top">${r.with_self?`<span class="rpeople">you &amp; ${h(p.name)}</span>`:r.with?`<span class="rpeople">${h(p.name)} &amp; ${h(r.with)}</span>`:""}
            ${r.nature?`<span class="rnat">${h(r.nature)}</span>`:""}</div>
          <div class="obs">${h(r.description)}</div></div>`).join("");
      const psy=(i.psych||[]).map(d=>{const v=d.valence||"neutral"; return `<div class="pdyn v-${v}">
          <div class="top"><span class="lab">${h(d.label)}</span><span class="vbadge v-${v}">${vLabel(v)}</span></div>
          <div class="obs">${h(d.observation)}</div></div>`;}).join("");
      const qs=(i.quotes||[]).slice(0,2).map(q=>`<div class="pquote">&ldquo;${h(q.text)}&rdquo;${q.significance?`<span class="qsig"> ${h(q.significance)}</span>`:""}</div>`).join("");
      const pc=[...(i.plans||[]).map(x=>`<div class="lineitem"><span class="li-ic plan">&#9719;</span><span>${h(x.text)}</span></div>`),
                ...(i.commitments||[]).map(x=>`<div class="lineitem"><span class="li-ic commit">&#10003;</span><span>${h(x.text)}</span></div>`)].join("");
      return `<div class="tinter" data-rid="${i.rec_id}">
        <div class="tdate" style="--tlc:${ic}">${dateShort(i.date)}</div>
        <div class="tcard" style="--tlc:${ic}">
          <h3>${h(i.headline)} <span class="chev">&rsaquo;</span></h3>
          ${i.role?`<div class="prole">${h(i.role)}</div>`:""}
          ${rels}${psy}${qs}${pc}
          ${i.sentiment?`<div class="arc">&#9709; ${h(i.sentiment)}</div>`:""}
        </div></div>`;}).join("");

    const allQuotes=(p.timeline||[]).flatMap(i=>(i.quotes||[])).slice(0,4);
    const quotesPanel=allQuotes.length?`<div class="panel"><h2>Notable quotes</h2>
      ${allQuotes.map(q=>`<div class="pquote">&ldquo;${h(q.text)}&rdquo;${q.significance?`<span class="qsig">${h(q.significance)}</span>`:""}</div>`).join("")}</div>`:"";

    app.innerHTML=`<div class="view detail person-dossier view--wide" style="--mc:${col}">
      <span class="backlink" onclick="App.go('/lucid/people')">&larr; People</span>
      <div class="dhero">${ringHTML(col,100,h(pInitials(p.name)))}
        <div><h1>${h(p.name)}</h1>
          <div class="dmeta"><span class="mc">${toneWord(p.tone)}</span>
            <span>&middot; ${trendWord(p.trend)}</span>
            <span>&middot; ${p.interactions} conversation${p.interactions>1?"s":""}</span>
            <span>&middot; ${span}</span></div></div></div>
      <div class="figrow stats-static">${ribbon}</div>
      <p class="lead">${arc}</p>
      <div class="dossier">
        <div class="dcol">
          <div class="panel"><h2>The relationship</h2>
            ${valBar(p)}
            <div class="vkey"><span><i style="background:var(--pos)"></i>${p.positive} positive</span>
              <span><i style="background:var(--ten)"></i>${p.negative} concerning</span>
              <span><i style="background:var(--neu)"></i>${p.neutral} neutral</span></div>
            ${p.roles&&p.roles.length?`<div class="prole">${h(p.roles[0])}</div>`:""}
            ${natures?`<div class="chips" style="margin-top:12px">${natures}</div>`:""}</div>
          ${quotesPanel}
        </div>
        <div class="dcol">
          <div class="panel"><h2>Conversations${p.interactions?`<span class="hcount">${p.interactions}</span>`:""}</h2>
            <div class="timeline-people crmtl">${tl||`<div class="muted" style="font-size:14px">No history yet.</div>`}</div></div>
        </div>
      </div></div>`;
    paintDone();
    app.querySelectorAll(".tinter").forEach(c=>c.onclick=()=>go("/r/"+c.dataset.rid));
  }

  // ===== IDEAS (business ideas → build specs) =====
  let venData=null, venFilter="all";

  function ventureColor(v){ const t=(v.status||"").toLowerCase();
    if(/(built|launch|ship|live|active|building|in progress|agreed|approv|greenlit|go\b)/.test(t)) return "var(--pos)";
    if(/(reject|dead|parked|dropped|killed|on hold|shelved|abandon|backlog|someday)/.test(t)) return "var(--neu)";
    if(/(risk|blocked|concern|stuck)/.test(t)) return "var(--ten)";
    return "var(--topic)"; }

  function stanceColor(st){ const s=(st||"").toLowerCase();
    if(/(support|propos|agree|for\b|yes|champion)/.test(s)) return "var(--pos)";
    if(/(against|reject|oppos|\bno\b|block)/.test(s)) return "var(--ten)";
    if(/(skeptic|concern|refine|caution|unsure|wary|question)/.test(s)) return "var(--accent)";
    return "var(--neu)"; }

  function ventureCard(v){
    const col=ventureColor(v);
    const ppl=(v.people||[]).slice(0,2).map(p=>`<span class="chip">${h(p)}</span>`).join("");
    return `<div class="rcard idea-card" data-id="${attr(v.id)}" style="--mc:${col}">
      <div class="tile mono"><span>&#9670;</span></div>
      <div class="rbody">
        <h3>${h(v.title)}</h3>
        ${v.summary?`<div class="snip">${h(v.summary)}</div>`:""}
        <div class="rmeta">
          ${v.has_spec?`<span class="chip draftready">&#10003; build plan</span>`:`<span class="chip stage">no plan yet</span>`}
          ${v.status?`<span class="chip stage">${h(v.status)}</span>`:""}
          ${ppl}
          ${v.mentions>1?`<span class="chip">${v.mentions}&times; discussed</span>`:""}
        </div></div></div>`;
  }

  async function showVentures(){
    app.innerHTML=`<div class="view">${masthead({title:"Ideas"})}
      <div class="figrow">${Array(3).fill('<div class="statcard"><span class="figure">&middot;</span></div>').join("")}</div>
      ${skeletons(3)}</div>`;
    let vs; try{ vs=await api("/api/ventures"); }catch(e){ return authOrError(e,showVentures); }
    venData=vs; paintVentures();
  }

  function paintVentures(){
    const vs=venData||[];
    const planned=vs.filter(v=>v.has_spec), unplanned=vs.filter(v=>!v.has_spec);
    setSubline(vs.length?`${vs.length} idea${vs.length>1?"s":""}${unplanned.length?` &middot; ${unplanned.length} to develop`:""}`:"ideas");

    if(!vs.length){
      app.innerHTML=`<div class="view">
        ${masthead({title:"Ideas",note:"Brainstorms from your recordings gather here &mdash; each becomes a buildable plan."})}
        <div class="empty"><div class="big">&#9670;</div>No business ideas yet.
          <div class="hint">When you and the people around you brainstorm in a recording,
          the ideas collect here &mdash; each with a full build plan, ready to hand to Claude Code.</div></div></div>`;
      paintDone(); return;
    }

    const bits=[`<b>${vs.length}</b> idea${vs.length>1?"s":""} on the table`];
    if(planned.length)   bits.push(`<b>${planned.length}</b> with a build plan`);
    if(unplanned.length) bits.push(`<b>${unplanned.length}</b> awaiting one`);
    const ed=bits.join(" &middot; ")+".";

    const figs=[
      {n:vs.length,        c:"Ideas",       col:"var(--topic)",  f:"all"},
      {n:planned.length,   c:"With a plan", col:"var(--pos)",    f:"planned"},
      {n:unplanned.length, c:"Need a plan", col:"var(--accent)", f:"unplanned"},
    ];
    const ribbon=figs.map(f=>`<button class="statcard${venFilter===f.f?" on":""}" data-f="${f.f}" style="--mc:${f.col}">
      <span class="figure">${f.n}</span><span class="figcap">${f.c}</span></button>`).join("");

    const lane=(unplanned.length&&venFilter!=="planned")?`<div class="owelane" id="developlane">
      <div class="lanehead">Develop next <span class="n">${unplanned.length}</span></div>
      ${unplanned.slice(0,5).map(v=>`<div class="owerow" data-id="${attr(v.id)}" style="--mc:${ventureColor(v)}">
        <div class="ow-l"><div class="ow-name">${h(v.title)}</div>
          <div class="ow-sit">${h(v.summary||"No build plan yet — generate one from the discussion.")}</div></div>
        <span class="ow-t">${v.mentions>1?`${v.mentions}× discussed`:"new"}</span>
        <button class="cta line" data-open="${attr(v.id)}">Build plan &rarr;</button>
      </div>`).join("")}</div>`:"";

    const GROUPS=[
      {k:"unplanned", label:"Needs a build plan", list:unplanned},
      {k:"planned",   label:"Has a build plan",   list:planned},
    ];
    const groups=(venFilter==="all"?GROUPS:GROUPS.filter(g=>g.k===venFilter))
      .filter(g=>g.list.length)
      .map(g=>`<div class="daygroup crm-group"><div class="daylabel">${g.label}<span class="n">${g.list.length}</span></div>
        <div class="feed">${g.list.map(ventureCard).join("")}</div></div>`).join("");

    app.innerHTML=`<div class="view idea-board">
      ${masthead({title:"Ideas",note:ed})}
      <div class="figrow">${ribbon}</div>
      ${lane}
      ${groups||`<div class="empty"><div class="big">&#9676;</div>Nothing in this view.</div>`}</div>`;
    paintDone();

    app.querySelectorAll(".statcard[data-f]").forEach(b=>b.onclick=()=>{ venFilter=b.dataset.f; paintVentures(); });
    app.querySelectorAll(".idea-card").forEach(c=>c.onclick=()=>go("/ventures/"+encodeURIComponent(c.dataset.id)));
    app.querySelectorAll(".owerow").forEach(r=>r.onclick=()=>go("/ventures/"+encodeURIComponent(r.dataset.id)));
    app.querySelectorAll(".owerow .cta[data-open]").forEach(b=>b.onclick=(e)=>{e.stopPropagation();go("/ventures/"+encodeURIComponent(b.dataset.open));});
  }

  async function showVenture(id){
    app.innerHTML=`<div class="view detail view--wide"><span class="backlink" onclick="App.go('/lucid/ideas')">&larr; Ideas</span>${skeletons(2)}</div>`;
    let v; try{ v=await api("/api/ventures/"+encodeURIComponent(id)); }catch(e){ return authOrError(e,()=>showVenture(id)); }
    renderVenture(v);
  }

  function renderVenture(v){
    const col=ventureColor(v);
    setSubline(v.title||"Idea");
    const srcN=(v.sources||[]).length, voices=(v.perspectives||[]).length;
    const persp=(v.perspectives||[]).map(p=>{ const ss=(p.stance||"").replace(/\s+/g,"-");
      return `<div class="persp" style="--mc:${stanceColor(p.stance)}"><span class="persp-dot"></span>
        <div><span class="persp-n">${h(p.person)}</span>${p.stance?`<span class="persp-s s-${h(ss)}">${h(p.stance)}</span>`:""}
        <div class="persp-v">${h(p.view)}</div></div></div>`; }).join("");
    const srcs=(v.sources||[]).map(s=>`<span class="vsrc" data-rid="${s.rec_id}">${h(s.headline||"recording")}</span>`).join("");
    const spec=v.spec;
    const via=(spec&&spec.viability&&typeof spec.viability==="object")?spec.viability:{};

    const figs=[
      {n:srcN||1, c:srcN===1?"Mention":"Mentions", col:"var(--topic)"},
      {n:voices,  c:voices===1?"Voice":"Voices",    col:"var(--accent)"},
      via.score!=null
        ? {n:`${h(String(via.score))}<small>/10</small>`, c:"Viability", col:"var(--pos)"}
        : {n:v.has_spec?"&#10003;":"&mdash;", c:v.has_spec?"Has plan":"No plan", col:v.has_spec?"var(--pos)":"var(--neu)"},
    ];
    const ribbon=figs.map(f=>`<div class="statcard" style="--mc:${f.col}">
      <span class="figure">${f.n}</span><span class="figcap">${h(f.c)}</span></div>`).join("");

    const ideaPanel=`<div class="panel"><h2>The idea</h2>
      ${v.summary?`<p class="lead">${h(v.summary)}</p>`:""}
      ${v.details?`<div class="idea-d">${h(v.details)}</div>`:""}
      ${srcs?`<div class="vsrcs">From ${srcs}</div>`:""}</div>`;
    const perspPanel=persp?`<div class="panel"><h2>Around the table</h2><div class="idea-p">${persp}</div></div>`:"";

    const specBlock = spec ? renderSpec(v, spec) :
      `<div class="panel vbuild" style="--mc:${col}"><h2>Build plan</h2>
        <p class="muted" style="font-size:14px;margin-top:0;line-height:1.55">Turn this discussion into a complete, buildable spec &mdash;
        stack, data model, features, roadmap, and first steps &mdash; predicted from what was said and
        ready to hand to Claude Code.</p>
        <button class="btn" id="genBtn">&#10038; Generate build plan</button></div>`;

    app.innerHTML=`<div class="view detail view--wide idea-dossier" style="--mc:${col}">
      <span class="backlink" onclick="App.go('/lucid/ideas')">&larr; Ideas</span>
      <div class="dhero">${ringHTML(col,100,"&#9670;")}
        <div><h1>${h(v.title)}</h1>
          <div class="dmeta"><span class="mc">${v.proposed_by?h(v.proposed_by):"Idea"}</span>
            ${v.status?`<span>&middot; ${h(v.status)}</span>`:""}
            <span>&middot; ${srcN||1} mention${(srcN||1)>1?"s":""}</span>
            <span>&middot; ${v.has_spec?"build plan ready":"no plan yet"}</span></div></div></div>
      <div class="figrow">${ribbon}</div>
      <div class="dossier">
        <div class="dcol">${ideaPanel}${perspPanel}</div>
        <div class="dcol">${specBlock}</div>
      </div></div>`;

    app.querySelectorAll(".vsrc").forEach(s=>s.onclick=()=>go("/r/"+s.dataset.rid));
    const gen=document.getElementById("genBtn"); if(gen) gen.onclick=()=>buildVenture(v.id, gen);
    app.querySelectorAll(".rebuildBtn").forEach(b=>b.onclick=()=>buildVenture(v.id, b));
    const cs=app.querySelector("[data-copy-spec]"); if(cs) cs.onclick=()=>copySummary(cs.closest(".panel"));
  }

  async function buildVenture(id, btn){
    if(btn){ btn.disabled=true; btn.dataset.t=btn.textContent; btn.textContent="✦ Building… (~20s)"; }
    try{ await api(`/api/ventures/${encodeURIComponent(id)}/build`,{method:"POST"});
      const v=await api("/api/ventures/"+encodeURIComponent(id)); toast("Build plan ready"); renderVenture(v);
    }catch(e){ toast("Couldn't build"); if(btn){ btn.disabled=false; btn.textContent=btn.dataset.t||"✦ Generate build plan"; } }
  }

  function renderSpec(v, s){
    const col=ventureColor(v);
    const A=(x)=>Array.isArray(x)?x:[];                 // tolerate any AI output shape
    const sec=(t,html)=> html?`<div class="vsec"><div class="vsec-h">${t}</div>${html}</div>`:"";
    const list=(a)=> A(a).length?`<ul class="vlist">${A(a).map(x=>`<li>${h(x)}</li>`).join("")}</ul>`:"";
    const p=(x)=> x?`<p>${h(x)}</p>`:"";
    const feats=A(s.core_features).map(f=>`<div class="vfeat"><span class="vpri vpri-${h(f.priority||'should')}">${h(f.priority||'')}</span><b>${h(f.name)}</b>${f.description?` — ${h(f.description)}`:""}</div>`).join("");
    const stack=(s.tech_stack&&typeof s.tech_stack==="object")?s.tech_stack:{}; const stackRows=Object.keys(stack).filter(k=>stack[k]).map(k=>`<div class="vkv"><span class="vk">${h(k)}</span><span class="vv">${h(stack[k])}</span></div>`).join("");
    const dm=A(s.data_model).map(d=>`<div class="vdm"><b>${h(d.entity)}</b><div class="vdm-f">${h(d.fields)}</div>${d.notes?`<div class="vdm-n">${h(d.notes)}</div>`:""}</div>`).join("");
    const comp=A(s.competitors).map(c=>`<div class="vrow"><b>${h(c.name||c)}</b>${c.note?` — ${h(c.note)}`:""}</div>`).join("");
    const risks=A(s.risks).map(r=>`<div class="vrow"><b>${h(r.risk||r)}</b>${r.mitigation?`<div class="vmit">→ ${h(r.mitigation)}</div>`:""}</div>`).join("");
    const road=A(s.roadmap).map(r=>`<div class="vphase"><b>${h(r.phase)}</b>${r.goal?` — ${h(r.goal)}`:""}${list(r.items)}</div>`).join("");
    const via=(s.viability&&typeof s.viability==="object")?s.viability:{};
    return `
      ${v.spec_stale?`<div class="vstale">The discussion changed since this plan. <span class="rebuildBtn">Refresh it →</span></div>`:""}
      <div class="panel vplan" style="--mc:${col}"><h2>Build plan</h2>
        ${s.one_liner?`<p class="lead">${h(s.one_liner)}</p>`:""}
        ${via.read?`<div class="vvia">${via.score!=null?`<span class="vscore">${h(String(via.score))}/10</span>`:""}${h(via.read)}</div>`:""}
        ${sec("Problem", p(s.problem))}${sec("Solution", p(s.solution))}
        ${sec("Target customer", p(s.target_customer))}${sec("Value prop", p(s.value_prop))}
        ${sec("Core features", feats)}${sec("MVP scope", p(s.mvp_scope))}
        ${sec("Tech stack", stackRows)}${sec("Data model", dm)}
        ${sec("User flows", list(s.user_flows))}
        ${sec("Monetization", p(s.monetization)+p(s.pricing))}
        ${sec("Go to market", p(s.go_to_market))}${sec("Competitors", comp)}
        ${sec("Differentiation", p(s.differentiation))}${sec("Risks", risks)}
        ${sec("Key metrics", list(s.key_metrics))}${sec("Roadmap", road)}
        ${sec("Cost estimate", p(s.cost_estimate))}${sec("Team needs", list(s.team_needs))}
        ${sec("Open questions", list(s.open_questions))}
        ${sec("First build steps", list(s.first_build_steps))}
        ${sec("Predicted (assumptions)", list(s.assumptions))}</div>
      <div class="panel"><h2>Hand to Claude Code</h2>
        <div class="copyrow"><button class="btn" data-copy-spec>⧉ Copy build spec</button>
          <button class="btn ghost rebuildBtn" style="margin-left:auto">Regenerate</button></div>
        <textarea class="summarybox" readonly>${h(specText(v,s))}</textarea></div>`;
  }

  function specText(v, s){
    const L=[]; const P=x=>L.push(x); const A=(x)=>Array.isArray(x)?x:[];
    P(`# ${v.title}`); if(s.one_liner) P(s.one_liner);
    const via=(s.viability&&typeof s.viability==="object")?s.viability:{}; if(via.read) P(`\n**Viability${via.score!=null?` ${via.score}/10`:""}:** ${via.read}`);
    const sec=(t,b)=>{ if(b&&String(b).trim()){ P(`\n## ${t}`); P(b); } };
    sec("Problem", s.problem); sec("Solution", s.solution);
    sec("Target customer", s.target_customer); sec("Value proposition", s.value_prop);
    if(A(s.core_features).length){ P("\n## Core features"); A(s.core_features).forEach(f=>P(`- ${f.name}${f.priority?` (${f.priority})`:""}: ${f.description||""}`)); }
    sec("MVP scope", s.mvp_scope);
    const st=(s.tech_stack&&typeof s.tech_stack==="object")?s.tech_stack:{}; if(Object.values(st).some(Boolean)){ P("\n## Tech stack"); Object.keys(st).forEach(k=>{ if(st[k]) P(`- ${k}: ${st[k]}`); }); }
    if(A(s.data_model).length){ P("\n## Data model"); A(s.data_model).forEach(d=>P(`- ${d.entity}: ${d.fields}${d.notes?` — ${d.notes}`:""}`)); }
    if(A(s.user_flows).length){ P("\n## User flows"); A(s.user_flows).forEach(x=>P(`- ${x}`)); }
    sec("Monetization", s.monetization); sec("Pricing", s.pricing); sec("Go to market", s.go_to_market);
    if(A(s.competitors).length){ P("\n## Competitors"); A(s.competitors).forEach(c=>P(`- ${c.name||c}${c.note?` — ${c.note}`:""}`)); }
    sec("Differentiation", s.differentiation);
    if(A(s.risks).length){ P("\n## Risks"); A(s.risks).forEach(r=>P(`- ${r.risk||r}${r.mitigation?` → ${r.mitigation}`:""}`)); }
    if(A(s.key_metrics).length){ P("\n## Key metrics"); A(s.key_metrics).forEach(x=>P(`- ${x}`)); }
    if(A(s.roadmap).length){ P("\n## Roadmap"); A(s.roadmap).forEach(r=>{ P(`\n### ${r.phase}${r.goal?` — ${r.goal}`:""}`); A(r.items).forEach(x=>P(`- ${x}`)); }); }
    sec("Cost estimate", s.cost_estimate);
    if(A(s.team_needs).length){ P("\n## Team needs"); A(s.team_needs).forEach(x=>P(`- ${x}`)); }
    if(A(s.open_questions).length){ P("\n## Open questions"); A(s.open_questions).forEach(x=>P(`- ${x}`)); }
    if(A(s.first_build_steps).length){ P("\n## First build steps (for Claude Code)"); A(s.first_build_steps).forEach((x,i)=>P(`${i+1}. ${x}`)); }
    if(A(s.assumptions).length){ P("\n## Assumptions (predicted, not from the conversation)"); A(s.assumptions).forEach(x=>P(`- ${x}`)); }
    if(A(v.perspectives).length){ P("\n## What the people said"); A(v.perspectives).forEach(pp=>P(`- ${pp.person}${pp.stance?` (${pp.stance})`:""}: ${pp.view}`)); }
    return L.join("\n").trim();
  }

  // ===== DETAIL =====
  let audioEl=null, current=null, showOriginal=false, activeTab="overview";
  // Audio is fetched with the auth header into a blob URL so the token is never
  // placed in a URL (which would leak it via tunnel/edge logs & Referer).
  let audioURL=null, audioURLId=null;
  async function loadAudioURL(id){
    if (audioURLId===id && audioURL) return audioURL;
    if (audioURL){ try{ URL.revokeObjectURL(audioURL); }catch(e){} audioURL=null; audioURLId=null; }
    const res = await fetch(`/api/recordings/${encodeURIComponent(id)}/audio`,
      { headers: token?{Authorization:"Bearer "+token}:{} });
    if (!res.ok) throw new Error(res.status===401||res.status===403?"auth":"audio "+res.status);
    const blob = await res.blob();
    audioURL = URL.createObjectURL(blob); audioURLId = id; return audioURL;
  }
  async function showDetail(id){
    app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/lucid/notes')">← Notes</span>${skeletons(1)}
      <div style="height:12px"></div>${skeletons(2)}</div>`;
    let rec; try { rec=await api("/api/recordings/"+id); } catch(e){ return authOrError(e,()=>showDetail(id)); }
    current=rec; activeTab="overview"; showOriginal=false; chatHist=[];

    if (!["done","error"].includes(rec.status)){
      app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/lucid/notes')">← Notes</span>
        <div class="empty"><span class="spin-lg"></span>${h(rec.status)}…
        <div class="hint">transcribe → translate → analyze</div></div></div>`;
      pollTimer=setTimeout(()=>showDetail(id),3500); return;
    }
    if (rec.status==="error"){
      app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/lucid/notes')">← Notes</span>
        <div class="panel"><h2>Error</h2><p style="color:var(--ten);white-space:pre-wrap;font-size:13px">${h(rec.error)}</p>
        <button class="btn" onclick="App.reanalyze('${id}')">Retry</button></div></div>`; return;
    }

    const a=rec.analysis||{}; const m=mood(a);
    app.innerHTML=`<div class="view" style="--mc:${m.c}">
      <span class="backlink" onclick="App.go('/lucid/notes')">← Notes</span>
      <div class="dhero">${ringHTML(m.c,72)}
        <div><h1>${h(a.headline||"Recording")}</h1>
          <div class="dmeta"><span class="mc">${m.w}</span><span>· ${fmt(rec.duration)}</span>
            ${rec.language?`<span>· ${h(rec.language)}</span>`:""}<span>· ${h(rel(rec.created_at))}</span></div>
        </div></div>

      <div class="player">
        <audio id="audio" controls preload="metadata"></audio>
        <div class="scrub" id="scrub"><div class="bands" id="bands"></div>
          <div class="fill" id="fill"></div><div class="head" id="head"></div>
          <div class="ticks"><span>0:00</span><span>${fmt(rec.duration)}</span></div></div>
        <div class="legend">${["decision","question","tension","action","topic_shift","moment"].map(k=>
          `<span><i class="dot" style="background:${kc(k)}"></i>${k.replace("_"," ")}</span>`).join("")}</div>
      </div>

      <div class="tabs">
        <button data-t="overview">Overview</button>
        <button data-t="map">Map</button>
        <button data-t="transcript">Transcript</button>
      </div>
      <div id="tabbody" class="tabbody"></div>
      <button class="fab" onclick="App.chat()">✦ Ask Lucid</button></div>`;

    app.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{ activeTab=b.dataset.t; renderTab(); });
    setupAudio(rec); renderTab();
    loadAudioURL(id).then(u=>{ const a=document.getElementById("audio"); if(a) a.src=u; }).catch(()=>{});
  }

  function renderTab(){
    const a=current.analysis||{};
    app.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("on",b.dataset.t===activeTab));
    const body=document.getElementById("tabbody");
    body.style.animation="none"; void body.offsetWidth; body.style.animation="";
    body.innerHTML = activeTab==="overview"?overviewHTML(a):activeTab==="map"?mapHTML(a):transcriptHTML(current);
    body.querySelectorAll("[data-seek]").forEach(el=>el.onclick=()=>seek(parseFloat(el.dataset.seek)));
    body.querySelectorAll("[data-rename]").forEach(el=>el.onclick=(e)=>{ e.stopPropagation(); rename(current.id, el.dataset.rename); });
    body.querySelectorAll("[data-person]").forEach(el=>el.onclick=(e)=>{ e.stopPropagation(); openPerson(parseInt(el.dataset.person)); });
    body.querySelectorAll("[data-proof]").forEach(el=>el.onclick=(e)=>{ e.stopPropagation(); proof(parseFloat(el.dataset.proof), el.dataset.qt); });
    const cs=body.querySelector("[data-copy-summary]"); if(cs) cs.onclick=()=>copySummary(body);
    if (activeTab==="map") setupMap(a);
    if (activeTab==="transcript") body.querySelectorAll("[data-lang]").forEach(b=>b.onclick=()=>{ showOriginal=b.dataset.lang==="orig"; renderTab(); });
  }

  const attr = (s) => h(s).replace(/"/g, "&quot;");
  const seekAttr = (t) => (t != null ? ` data-seek="${t}"` : "");
  const tchip = (t) => (t != null ? `<span class="qt">${fmt(t)} ▸</span>` : "");
  const whoTag = (w) => (w ? `<span class="who">${h(w)}</span>` : "");
  const vLabel = (v) => (v === "positive" ? "good" : v === "negative" ? "worth noting" : "neutral");

  function summaryText(rec){
    const a=rec.analysis||{}, L=[]; const P=s=>L.push(s);
    const at=t=>(t!=null?` [${fmt(t)}]`:"");
    P(`# ${a.headline||"Session"}`);
    const meta=[];
    if(rec.created_at) meta.push(new Date(rec.created_at).toLocaleString());
    if(rec.duration) meta.push(fmt(rec.duration));
    if(rec.language) meta.push(rec.language);
    if(a.sentiment) meta.push("Tone: "+a.sentiment);
    if(meta.length) P(meta.join("  ·  "));
    if(a.summary){ P("\n## Summary"); P(a.summary); }
    if((a.people||[]).length){ P("\n## People"); a.people.forEach(p=>P(`- ${p.name||p.label}${p.role?` — ${p.role}`:""}`)); }
    if((a.ideas||[]).length){ P("\n## Ideas & perspectives");
      a.ideas.forEach(i=>{
        P(`\n### ${i.title}${i.status?`  [${i.status}]`:""}`);
        if(i.summary) P(i.summary);
        if(i.details) P(`Details: ${i.details}`);
        const by=[]; if(i.proposed_by) by.push(`proposed by ${i.proposed_by}`); if(i.t!=null) by.push(`at ${fmt(i.t)}`);
        if(by.length) P(`(${by.join(", ")})`);
        if((i.perspectives||[]).length){ P("Perspectives:");
          i.perspectives.forEach(pp=>P(`  - ${pp.person}${pp.stance?` (${pp.stance})`:""}: ${pp.view}`)); }
      });
    }
    if((a.key_points||[]).length){ P("\n## Key points"); a.key_points.forEach(k=>P(`- ${k}`)); }
    if((a.plans||[]).length){ P("\n## Plans"); a.plans.forEach(x=>P(`- ${x.text}${x.who?` (${x.who})`:""}${at(x.t)}`)); }
    if((a.commitments||[]).length){ P("\n## Commitments"); a.commitments.forEach(x=>P(`- ${x.text}${x.who?` (${x.who})`:""}${at(x.t)}`)); }
    if((a.relationship_dynamics||[]).length){ P("\n## Relationship dynamics");
      a.relationship_dynamics.forEach(r=>P(`- ${r.people?r.people+": ":""}${r.nature?`[${r.nature}] `:""}${r.description}${at(r.t)}`)); }
    if((a.psychological_dynamics||[]).length){ P("\n## Psychological dynamics");
      a.psychological_dynamics.forEach(d=>P(`- ${d.label}${d.speaker?` (${d.speaker})`:""} — ${d.observation} [${d.valence||"neutral"}]${at(d.t)}`)); }
    if((a.notable_quotes||[]).length){ P("\n## Notable quotes");
      a.notable_quotes.forEach(q=>P(`- "${q.text}"${q.speaker?` — ${q.speaker}`:""}${at(q.t)}${q.significance?`\n    (${q.significance})`:""}`)); }
    if((a.action_items||[]).length){ P("\n## Action items"); a.action_items.forEach(x=>P(`- [ ] ${x.text}${x.owner?` — ${x.owner}`:""}${x.due?` (due ${x.due})`:""}`)); }
    if((a.topics||[]).length){ P("\n## Topics"); a.topics.forEach(t=>P(`- ${t.label} (${fmt(t.start)}–${fmt(t.end)})${t.summary?`: ${t.summary}`:""}`)); }
    if((a.timeline||[]).length){ P("\n## Timeline"); a.timeline.forEach(e=>P(`- ${fmt(e.t)} [${(e.kind||"").replace("_"," ")}] ${e.title}${e.detail?` — ${e.detail}`:""}`)); }
    const segs=rec.segments||[];
    if(segs.length){ P("\n## Transcript"); segs.forEach(s=>P(`[${fmt(s.start)}]${s.speaker?` ${s.speaker}:`:""} ${s.text_translated||s.text}`)); }
    return L.join("\n").trim();
  }

  async function copySummary(scope){
    const ta=scope.querySelector(".summarybox"); if(!ta) return;
    try{ await navigator.clipboard.writeText(ta.value); toast("Summary copied"); }
    catch(e){ ta.focus(); ta.select(); try{ document.execCommand("copy"); toast("Summary copied"); }catch(_){ toast("Select all, then Ctrl/⌘+C"); } }
  }

  function overviewHTML(a){
    const people=a.people||[], plans=a.plans||[], commits=a.commitments||[],
      psy=a.psychological_dynamics||[], rels=a.relationship_dynamics||[], quotes=a.notable_quotes||[];
    return `
      <div class="panel"><h2>Copy session summary</h2>
        <div class="copyrow"><button class="btn" data-copy-summary>&#9106; Copy everything</button>
          <span class="muted" style="font-size:12px">clean text · every detail</span></div>
        <textarea class="summarybox" readonly>${h(summaryText(current))}</textarea></div>

      ${people.length?`<div class="panel"><h2>People · tap to explore</h2>
        ${people.map((p,i)=>{const nm=p.name||p.label; return `<div class="person">
          <div class="phead"><div class="pname" data-person="${i}">${h(nm)} <span class="chev">›</span></div>
            <button class="editname" data-rename="${attr(nm)}">✎ name</button></div>
          ${p.role?`<div class="prole" data-person="${i}">${h(p.role)}</div>`:""}
          ${(p.identity_quotes||[]).slice(0,1).map(q=>`<div class="pquote${q.t!=null?" tap":""}"${q.t!=null?` data-proof="${q.t}" data-qt="${attr(q.text)}"`:""}>“${h(q.text)}”</div>`).join("")}
          <button class="pview" data-person="${i}">Quotes &amp; psychology ›</button>
        </div>`;}).join("")}</div>`:""}

      <div class="panel"><h2>The gist</h2><p class="lead">${h(a.summary)}</p>
        ${a.sentiment?`<div class="arc">◡ ${h(a.sentiment)}</div>`:""}</div>

      ${(a.ideas||[]).length?`<div class="panel"><h2>Ideas &amp; perspectives</h2>
        ${a.ideas.map(i=>{const st=(i.status||"").replace(/\s+/g,"-"); return `<div class="idea${i.t!=null?" tap":""}"${seekAttr(i.t)}>
          <div class="idea-h"><span class="idea-t">${h(i.title)}</span>${i.status?`<span class="idea-st st-${h(st)}">${h(i.status)}</span>`:""}${i.t!=null?`<span class="at">@${fmt(i.t)}</span>`:""}</div>
          ${i.summary?`<div class="idea-s">${h(i.summary)}</div>`:""}
          ${i.details?`<div class="idea-d">${h(i.details)}</div>`:""}
          ${i.proposed_by?`<div class="idea-by">Proposed by ${h(i.proposed_by)}</div>`:""}
          ${(i.perspectives||[]).length?`<div class="idea-p">${i.perspectives.map(pp=>{const ss=(pp.stance||"").replace(/\s+/g,"-"); return `<div class="persp">
            <span class="persp-n">${h(pp.person)}</span>${pp.stance?`<span class="persp-s s-${h(ss)}">${h(pp.stance)}</span>`:""}
            <div class="persp-v">${h(pp.view)}</div></div>`;}).join("")}</div>`:""}
        </div>`;}).join("")}</div>`:""}

      ${plans.length?`<div class="panel"><h2>Plans</h2>
        ${plans.map(p=>`<div class="lineitem${p.t!=null?" tap":""}"${seekAttr(p.t)}><span class="li-ic plan">◷</span>
          <span>${h(p.text)} ${whoTag(p.who)}${tchip(p.t)}</span></div>`).join("")}</div>`:""}

      ${commits.length?`<div class="panel"><h2>Commitments</h2>
        ${commits.map(c=>`<div class="lineitem${c.t!=null?" tap":""}"${seekAttr(c.t)}><span class="li-ic commit">✓</span>
          <span>${h(c.text)} ${whoTag(c.who)}${tchip(c.t)}</span></div>`).join("")}</div>`:""}

      ${psy.length?`<div class="panel"><h2>Psychological dynamics</h2>
        ${psy.map(p=>{const v=p.valence||"neutral"; return `<div class="pdyn v-${v}${p.t!=null?" tap":""}"${seekAttr(p.t)}>
          <div class="top"><span class="lab">${h(p.label)}</span><span class="vbadge v-${v}">${vLabel(v)}</span>${p.t!=null?`<span class="at">@${fmt(p.t)}</span>`:""}</div>
          <div class="obs">${h(p.observation)}${p.speaker?`<span class="who"> ${h(p.speaker)}</span>`:""}</div></div>`;}).join("")}</div>`:""}

      ${rels.length?`<div class="panel"><h2>Relationship dynamics</h2>
        ${rels.map(r=>`<div class="reldyn${r.t!=null?" tap":""}"${seekAttr(r.t)}>
          <div class="top">${r.people?`<span class="rpeople">${h(r.people)}</span>`:""}${r.nature?`<span class="rnat">${h(r.nature)}</span>`:""}${r.t!=null?`<span class="at">@${fmt(r.t)}</span>`:""}</div>
          <div class="obs">${h(r.description)}</div></div>`).join("")}</div>`:""}

      ${a.key_points?.length?`<div class="panel"><h2>What matters most</h2>
        <ul class="kp">${a.key_points.map(p=>`<li>${h(p)}</li>`).join("")}</ul></div>`:""}

      ${quotes.length?`<div class="panel"><h2>Notable quotes</h2>
        ${quotes.map(q=>`<blockquote class="qcard">
          <div class="qtext">${h(q.text)}</div>
          <div class="qmeta">${q.speaker?`<span class="qspk">${h(q.speaker)}</span>`:""}
            ${q.t!=null?`<button class="proof" data-proof="${q.t}" data-qt="${attr(q.text)}">▶ hear it</button><span class="qt">${fmt(q.t)}</span>`:""}</div>
          ${q.significance?`<div class="qsig">${h(q.significance)}</div>`:""}</blockquote>`).join("")}</div>`:""}

      ${a.topics?.length?`<div class="panel"><h2>Topics</h2><div class="chips">
        ${a.topics.map(t=>`<span class="chip tap" data-seek="${t.start}">${h(t.label)} · ${fmt(t.start)}</span>`).join("")}</div></div>`:""}

      ${a.action_items?.length?`<div class="panel"><h2>Action items</h2>
        ${a.action_items.map(ai=>`<div class="act"><span class="bx">☑</span><span>${h(ai.text)}
          ${ai.owner?`<span style="color:var(--muted)"> — ${h(ai.owner)}</span>`:""}</span></div>`).join("")}</div>`:""}

      <div class="panel"><div class="btnrow">
        <button class="btn ghost" onclick="App.reanalyze('${current.id}')">Re-analyze</button>
        <button class="btn ghost" onclick="App.del('${current.id}')">Delete</button></div></div>`;
  }

  const clip = (s,n)=> (s && s.length>n ? s.slice(0,n-1)+"…" : (s||""));

  // ---- interactive conversation MAP (topic lanes over time, pan/zoom) ----
  function mapHTML(a){
    return `<div class="mapwrap">
      <div class="maptools">
        <button class="mbtn" data-zoom="out">–</button>
        <button class="mbtn" data-zoom="reset">FIT</button>
        <button class="mbtn" data-zoom="in">+</button>
        <span class="maphint">drag to move · scroll/pinch to zoom · ▶ to hear</span>
      </div>
      <div class="mapview" id="mapview"><div class="mapcanvas" id="mapcanvas"></div></div>
    </div>`;
  }

  function setupMap(a){
    const view=document.getElementById("mapview"), canvas=document.getElementById("mapcanvas");
    if(!view||!canvas) return;
    const segEnd=(current.segments||[]).reduce((m,s)=>Math.max(m,s.end||0),0);
    const dur=Math.max(1, current.duration||segEnd||1);
    const topics=(a.topics||[]).slice().sort((x,y)=>(x.start||0)-(y.start||0));
    const PADX=90, laneH=170, headerH=132, slotH=50, MISC=topics.length;
    const W=Math.max(1700, Math.min(8200, Math.round(dur*5)));
    const xOf=t=>PADX+(Math.max(0,Math.min(dur,t||0))/dur)*(W-2*PADX);
    const laneOf=t=>{ for(let i=0;i<topics.length;i++){ if(t>=(topics[i].start||0)-2 && t<=(topics[i].end||dur)+2) return i; } return MISC; };
    const laneY=i=>headerH+i*laneH;
    const H=headerH+(topics.length+1)*laneH+50;
    const slots={};
    const place=(lane,x,w)=>{ const rows=slots[lane]=slots[lane]||[];
      for(let r=0;r<rows.length;r++){ if(x>rows[r]+16){ rows[r]=x+w; return r; } }
      rows.push(x+w); return rows.length-1; };

    let html="";
    // people header
    html+=`<div class="mappeople" style="left:${PADX}px">PEOPLE:&nbsp; ${(a.people||[]).map(p=>`<span class="pchip" data-person="${(a.people||[]).indexOf(p)}">${h(p.name||p.label)}</span>`).join("")||"<span class='muted'>—</span>"}</div>`;
    // time ruler
    const te=dur>3600?600:dur>1200?300:dur>300?60:30;
    let ticks="";
    for(let s=0;s<=dur;s+=te){ ticks+=`<div class="tick" style="left:${xOf(s)}px"><b>${fmt(s)}</b></div>`; }
    html+=`<div class="ruler" style="top:${headerH-34}px;width:${W}px">${ticks}</div>`;
    // topic bands
    topics.forEach((tp,i)=>{ const x=xOf(tp.start||0), w=Math.max(70,xOf(tp.end||dur)-x), y=laneY(i);
      html+=`<div class="band" style="left:${x}px;top:${y}px;width:${w}px;height:${laneH-26}px"></div>
        <div class="bandlabel" style="left:${x+8}px;top:${y+6}px">${h(clip(tp.label,40))} · ${fmt(tp.start||0)}</div>`; });
    html+=`<div class="bandlabel misc" style="left:${PADX}px;top:${laneY(MISC)+6}px">OTHER MOMENTS</div>`;
    // moment nodes
    (a.timeline||[]).forEach(e=>{ const lane=laneOf(e.t), x=xOf(e.t), w=176, r=place(lane,x,w), y=laneY(lane)+30+r*slotH;
      html+=`<div class="mnode k-${e.kind}" data-proof="${e.t}" style="left:${x}px;top:${y}px;width:${w}px">
        <div class="mk">${h((e.kind||"").replace("_"," "))}</div>
        <div class="mt">${h(clip(e.title,54))}</div><div class="mtime">${fmt(e.t)} ▶</div></div>`; });
    // quote nodes
    (a.notable_quotes||[]).forEach(q=>{ if(q.t==null) return; const lane=laneOf(q.t), x=xOf(q.t), w=246, r=place(lane,x,w), y=laneY(lane)+30+r*slotH;
      html+=`<div class="qnode" data-proof="${q.t}" data-qt="${attr(q.text)}" style="left:${x}px;top:${y}px;width:${w}px">
        <div class="qn-q">“${h(clip(q.text,92))}”</div>
        <div class="qn-m">${q.speaker?h(q.speaker)+" · ":""}${fmt(q.t)}<span class="qn-play">▶</span></div></div>`; });

    canvas.style.width=W+"px"; canvas.style.height=H+"px"; canvas.innerHTML=html;
    canvas.querySelectorAll("[data-proof]").forEach(el=>el.onclick=ev=>{ ev.stopPropagation();
      proof(parseFloat(el.dataset.proof), el.dataset.qt); el.classList.add("playing"); setTimeout(()=>el.classList.remove("playing"),11000); });
    canvas.querySelectorAll("[data-person]").forEach(el=>el.onclick=ev=>{ ev.stopPropagation(); openPerson(parseInt(el.dataset.person)); });

    // pan + zoom
    let scale=Math.max(0.3, Math.min(1,(view.clientWidth-12)/W)), tx=8, ty=0;
    const apply=()=>canvas.style.transform=`translate(${tx}px,${ty}px) scale(${scale})`;
    const fit=()=>{ scale=Math.max(0.3,Math.min(1,(view.clientWidth-12)/W)); tx=8; ty=0; apply(); };
    fit();
    const pts=new Map(); let pd0=0, ps0=1, pmid=null;
    const zoomAt=(cx,cy,ns)=>{ ns=Math.max(0.22,Math.min(3.5,ns)); tx=cx-(cx-tx)*(ns/scale); ty=cy-(cy-ty)*(ns/scale); scale=ns; apply(); };
    view.onpointerdown=e=>{ if(e.target.closest("[data-proof],[data-person]"))return; pts.set(e.pointerId,{x:e.clientX,y:e.clientY});
      view.setPointerCapture(e.pointerId); view.classList.add("grabbing");
      if(pts.size===2){ const[a1,b1]=[...pts.values()]; pd0=Math.hypot(a1.x-b1.x,a1.y-b1.y); ps0=scale; const r=view.getBoundingClientRect(); pmid={x:(a1.x+b1.x)/2-r.left,y:(a1.y+b1.y)/2-r.top}; } };
    view.onpointermove=e=>{ if(!pts.has(e.pointerId))return; const prev=pts.get(e.pointerId);
      if(pts.size===2){ pts.set(e.pointerId,{x:e.clientX,y:e.clientY}); const[a1,b1]=[...pts.values()]; const d=Math.hypot(a1.x-b1.x,a1.y-b1.y);
        if(pd0>0&&pmid) zoomAt(pmid.x,pmid.y,ps0*(d/pd0)); }
      else { tx+=e.clientX-prev.x; ty+=e.clientY-prev.y; pts.set(e.pointerId,{x:e.clientX,y:e.clientY}); apply(); } };
    const up=e=>{ pts.delete(e.pointerId); if(pts.size<2) pd0=0; if(pts.size===0) view.classList.remove("grabbing"); };
    view.onpointerup=up; view.onpointercancel=up;
    view.onwheel=e=>{ e.preventDefault(); const r=view.getBoundingClientRect(); zoomAt(e.clientX-r.left,e.clientY-r.top, scale*(e.deltaY<0?1.12:0.89)); };
    document.querySelectorAll("[data-zoom]").forEach(b=>b.onclick=()=>{ const z=b.dataset.zoom;
      if(z==="reset") fit(); else zoomAt(view.clientWidth/2,view.clientHeight/2, scale*(z==="in"?1.25:0.8)); });
  }

  const isUnnamedSpk = (sp) => /^speaker\s/i.test(sp||"");
  function transcriptHTML(rec){
    const segs=rec.segments||[]; const hasT=segs.some(s=>s.text_translated&&s.text_translated!==s.text);
    // distinct speakers, in order of first appearance
    const order=[]; const seen=new Set();
    segs.forEach(s=>{ if(s.speaker&&!seen.has(s.speaker)){ seen.add(s.speaker); order.push(s.speaker); } });
    const spkBar = order.length ? `<div class="spkbar">${order.map(sp=>
        `<button class="spkchip ${isUnnamedSpk(sp)?'unnamed':'known'}" data-rename="${attr(sp)}">${isUnnamedSpk(sp)?'🎤 ':'🗣 '}${h(sp)}${isUnnamedSpk(sp)?' · name':''}</button>`).join("")}</div>
      ${order.some(isUnnamedSpk)?`<div class="spkhint">Tap a speaker to name them — Lucid learns their voice and recognizes them automatically in future recordings.</div>`:""}` : "";
    return `<div class="panel">
      ${spkBar}
      ${hasT?`<div class="segtoggle"><button data-lang="trans" class="${showOriginal?"":"on"}">Translated</button>
        <button data-lang="orig" class="${showOriginal?"on":""}">Original</button></div>`:""}
      <div id="transcript">${segs.map((s,i)=>{ const txt=showOriginal?s.text:(s.text_translated||s.text);
        const orig=(!showOriginal&&s.text_translated&&s.text_translated!==s.text)?`<span class="orig">${h(s.text)}</span>`:"";
        return `<div class="seg" data-i="${i}" data-start="${s.start}" data-seek="${s.start}">
          <span class="t">${fmt(s.start)}</span>${s.speaker?`<span class="spk ${isUnnamedSpk(s.speaker)?'unnamed':''}" data-rename="${attr(s.speaker)}" title="Click to name this speaker">${h(s.speaker)}</span>`:""}
          <span>${h(txt)}</span>${orig}</div>`; }).join("")}</div></div>`;
  }

  // audio + scrubber
  function setupAudio(rec){
    audioEl=document.getElementById("audio");
    const scrub=document.getElementById("scrub"), bands=document.getElementById("bands");
    const dur=()=>rec.duration||audioEl.duration||1;
    (rec.analysis?.topics||[]).forEach((t,i)=>{ const d=document.createElement("div");
      const w=100*(t.end-t.start)/dur(); d.style.position="absolute"; d.style.left=(100*t.start/dur())+"%";
      d.style.width=w+"%"; d.style.top="0"; d.style.bottom="0";
      d.style.background=`color-mix(in srgb, var(--topic) ${i%2?7:12}%, transparent)`; bands.appendChild(d); });
    (rec.analysis?.timeline||[]).forEach(e=>{ const d=document.createElement("div");
      d.className="ev"; d.style.left=(100*e.t/dur())+"%"; d.style.background=kc(e.kind);
      d.title=`${fmt(e.t)} — ${e.title}`; d.onclick=(ev)=>{ ev.stopPropagation(); seek(e.t); }; scrub.appendChild(d); });
    scrub.onclick=(ev)=>{ const r=scrub.getBoundingClientRect(); seek(((ev.clientX-r.left)/r.width)*dur()); };
    audioEl.ontimeupdate=()=>{ const pct=100*audioEl.currentTime/dur();
      document.getElementById("fill").style.width=pct+"%"; document.getElementById("head").style.left=pct+"%";
      if (activeTab==="transcript") highlight(audioEl.currentTime); };
  }
  let _lastActive=null, _userScrolledAt=0;
  addEventListener("wheel", ()=>_userScrolledAt=Date.now(), {passive:true});
  addEventListener("touchmove", ()=>_userScrolledAt=Date.now(), {passive:true});
  function highlight(t){ const segs=[...document.querySelectorAll(".seg")]; let act=null;
    for (const s of segs) if (parseFloat(s.dataset.start)<=t) act=s;
    segs.forEach(s=>s.classList.toggle("active",s===act));
    // Only auto-scroll when the active line CHANGES and the user isn't scrolling.
    if (act && act!==_lastActive && Date.now()-_userScrolledAt>4000){
      const r=act.getBoundingClientRect();
      if (r.top<120||r.bottom>innerHeight-90) act.scrollIntoView({block:"center",behavior:"smooth"});
    }
    _lastActive=act; }
  function seek(t){ if (activeTab!=="transcript"){ activeTab="transcript"; renderTab(); }
    if (!audioEl) return; audioEl.currentTime=Math.max(0,t); audioEl.play().catch(()=>{}); }

  // ---- accurate audio proof: align the quote to real transcript segments ----
  const _norm = s => (s||"").toLowerCase().replace(/[^a-z0-9\s]/g," ").replace(/\s+/g," ").trim();

  // segment(s) covering time t (fallback when there's no verbatim text)
  function segRange(t){
    const segs=current.segments||[]; if(!segs.length) return null;
    for(const s of segs){ if(t>=s.start-0.3 && t<=s.end+0.3) return {start:s.start, end:s.end}; }
    let best=null, bd=1e9; segs.forEach(s=>{ const d=Math.min(Math.abs(t-s.start),Math.abs(t-s.end)); if(d<bd){bd=d;best=s;} });
    return best?{start:best.start,end:best.end}:null;
  }
  // find the segment span whose words best match the quote, biased to approxT
  function quoteRange(text, approxT){
    const segs=current.segments||[]; if(!segs.length) return null;
    const qwords=_norm(text).split(" ").filter(Boolean); if(qwords.length<2) return null;
    const words=[]; segs.forEach((s,i)=>_norm(s.text).split(" ").forEach(w=>{ if(w) words.push({w,seg:i}); }));
    if(!words.length) return null;
    const N=qwords.length; let best=-1,bestScore=0,bestEnd=-1;
    for(let i=0;i<words.length;i++){
      if(words[i].w!==qwords[0]) continue;            // anchor on first word
      let j=0,k=i,score=0,last=i,skips=0;
      while(j<N && k<words.length && skips<=6){
        if(words[k].w===qwords[j]){ score++; last=k; j++; k++; }
        else { k++; skips++; }
      }
      const closer = best>=0 && Math.abs(segs[words[i].seg].start-approxT) < Math.abs(segs[words[best].seg].start-approxT);
      if(score>bestScore || (score===bestScore && closer)){ bestScore=score; best=i; bestEnd=last; }
    }
    if(best<0 || bestScore < Math.max(2, Math.floor(N*0.5))) return null;
    return { start: segs[words[best].seg].start, end: segs[words[bestEnd].seg].end };
  }

  // dedicated player — never touches the main top bar; plays an exact range
  let proofAudio = null;
  async function proof(t, text){
    if (!current || isNaN(t)) return;
    let range = (text && text.trim()) ? quoteRange(text, t) : null;
    if (!range) range = segRange(t);
    if (!range) range = { start: Math.max(0, t-0.2), end: t+8 };
    let url; try { url = await loadAudioURL(current.id); } catch(e){ return; }
    if (!proofAudio) proofAudio = new Audio();
    const a = proofAudio;
    if (a.src !== url) { a.src = url; a.dataset.id = current.id; }
    try { document.getElementById("audio")?.pause(); } catch(e){}
    if (a._stop) a.removeEventListener("timeupdate", a._stop);
    a._stop = () => { if (a.currentTime >= range.end) { a.pause(); a.removeEventListener("timeupdate", a._stop); } };
    a.addEventListener("timeupdate", a._stop);
    a.currentTime = Math.max(0, range.start);
    a.play().catch(()=>{});
    clearTimeout(proof._t);
    proof._t = setTimeout(()=>{ try { a.pause(); } catch(e){} }, (range.end-range.start)*1000 + 1500);
    toast("▶ playing the moment…");
  }

  // person-centric sheet: their most important quotes + psychology, with proof
  function matchSpeaker(spk, p){
    if (!spk) return false;
    const s=spk.toLowerCase(), n=(p.name||p.label||"").toLowerCase(), l=(p.label||"").toLowerCase();
    return s===n || s===l || (n && s.includes(n)) || (l && s.includes(l));
  }
  function openPerson(i){
    const a = current.analysis || {}; const p = (a.people||[])[i]; if (!p) return;
    const nm = p.name || p.label;
    const seen = new Set(); const quotes = [];
    (p.identity_quotes||[]).forEach(q=>{ if(q.text&&!seen.has(q.text)){seen.add(q.text); quotes.push(q);} });
    (a.notable_quotes||[]).forEach(q=>{ if(matchSpeaker(q.speaker,p)&&!seen.has(q.text)){seen.add(q.text); quotes.push(q);} });
    const psych = (a.psychological_dynamics||[]).filter(d=>matchSpeaker(d.speaker,p));
    const plans = (a.plans||[]).filter(x=>matchSpeaker(x.who,p));
    const commits = (a.commitments||[]).filter(x=>matchSpeaker(x.who,p));

    const qHTML = quotes.length ? quotes.map(q=>`<div class="sq">
        <div class="sqtext">“${h(q.text)}”</div>
        <div class="sqmeta">${q.t!=null?`<button class="proof" data-proof="${q.t}" data-qt="${attr(q.text)}">▶ hear it</button><span class="qt">${fmt(q.t)}</span>`:""}
          ${q.significance?`<span class="sqsig">${h(q.significance)}</span>`:""}</div></div>`).join("")
      : `<p class="muted" style="font-size:14px">No quotes attributed to ${h(nm)} yet.</p>`;
    const pyHTML = psych.length ? psych.map(d=>{const v=d.valence||"neutral"; return `<div class="pdyn v-${v}${d.t!=null?" tap":""}"${d.t!=null?` data-proof="${d.t}"`:""}>
        <div class="top"><span class="lab">${h(d.label)}</span><span class="vbadge v-${v}">${vLabel(v)}</span>${d.t!=null?`<span class="at">@${fmt(d.t)}</span>`:""}</div>
        <div class="obs">${h(d.observation)}</div></div>`;}).join("")
      : `<p class="muted" style="font-size:14px">No psychology notes for ${h(nm)}.</p>`;
    const pcHTML = (plans.length||commits.length) ? `<h3>Plans &amp; commitments</h3>
        ${plans.map(x=>`<div class="lineitem${x.t!=null?" tap":""}"${x.t!=null?` data-proof="${x.t}"`:""}><span class="li-ic plan">◷</span><span>${h(x.text)}</span></div>`).join("")}
        ${commits.map(x=>`<div class="lineitem${x.t!=null?" tap":""}"${x.t!=null?` data-proof="${x.t}"`:""}><span class="li-ic commit">✓</span><span>${h(x.text)}</span></div>`).join("")}` : "";

    const sheet = document.createElement("div");
    sheet.className = "sheet-wrap";
    sheet.innerHTML = `<div class="sheet-bg"></div>
      <div class="sheet"><div class="sheet-grab"></div>
        <div class="sheet-head">${ringHTML("var(--accent)",72,"◑")}
          <div class="sheet-id"><div class="pname">${h(nm)}</div>${p.role?`<div class="prole">${h(p.role)}</div>`:""}</div>
          <button class="iconbtn sheet-close">✕</button></div>
        <div class="sheet-body">
          <h3>Most important quotes</h3>${qHTML}
          <h3>Psychology</h3>${pyHTML}
          ${pcHTML}</div></div>`;
    document.body.appendChild(sheet);
    document.body.style.overflow = "hidden";
    const close = () => { sheet.remove(); document.body.style.overflow=""; try{proofAudio&&proofAudio.pause();}catch(e){} };
    sheet.querySelector(".sheet-bg").onclick = close;
    sheet.querySelector(".sheet-close").onclick = close;
    attachSheetDrag(sheet.querySelector(".sheet"), close);
    sheet.querySelectorAll("[data-proof]").forEach(el=>el.onclick=(e)=>{ e.stopPropagation(); proof(parseFloat(el.dataset.proof), el.dataset.qt); });
    requestAnimationFrame(()=>sheet.classList.add("open"));
  }

  // drag the sheet (or its grab handle) down to dismiss — translateY follows the
  // finger; release past 110px calls close(). Restores the stylesheet transition for snap-back.
  function attachSheetDrag(s, close){
    if(!s) return; let y0=null;
    s.addEventListener('touchstart',e=>{ if(s.scrollTop>0){ y0=null; return; }
      y0=e.touches[0].clientY; s.style.transition='none'; },{passive:true});
    s.addEventListener('touchmove',e=>{ if(y0==null) return;
      const dy=Math.max(0,e.touches[0].clientY-y0); s.style.transform='translateY('+dy+'px)'; },{passive:true});
    s.addEventListener('touchend',e=>{ if(y0==null) return;
      const dy=e.changedTouches[0].clientY-y0; s.style.transition=''; s.style.transform='';
      if(dy>110) close(); y0=null; });
  }

  // ---- AI assistant: explains with playable quote-proof, can edit names ----
  let chatHist = [];
  function renderChatMsgs(box){
    box.innerHTML = chatHist.length ? chatHist.map(m=>{
      if (m.role==="user") return `<div class="cmsg me">${h(m.content)}</div>`;
      const qs=(m.quotes||[]).map(q=>`<div class="cq"><div class="cqt">“${h(q.text)}”</div>
        <div class="cqm">${q.speaker?`<span class="qspk">${h(q.speaker)}</span> · `:""}${q.t!=null?`<button class="proof" data-proof="${q.t}" data-qt="${attr(q.text)}">▶ hear it</button>`:""}</div></div>`).join("");
      return `<div class="cmsg ai">${h(m.content)}${qs}</div>`;
    }).join("") : `<div class="cwelcome">Ask about what happened, the people, or the dynamics — or just say “rename the friend to Sam”. Answers come with quotes you can play.</div>`;
    box.querySelectorAll("[data-proof]").forEach(el=>el.onclick=(e)=>{ e.stopPropagation(); proof(parseFloat(el.dataset.proof), el.dataset.qt); });
  }
  function chat(){
    if (!current) return;
    const wrap=document.createElement("div"); wrap.className="sheet-wrap";
    wrap.innerHTML=`<div class="sheet-bg"></div>
      <div class="sheet chatsheet"><div class="sheet-grab"></div>
        <div class="sheet-head"><div class="sheet-id"><div class="pname">✦ Ask Lucid</div>
          <div class="prole">answers come with audio proof</div></div>
          <button class="iconbtn sheet-close">✕</button></div>
        <div class="chatmsgs" id="chatmsgs"></div>
        <div class="chatinput"><input id="chatin" placeholder="Ask anything, or “rename the friend to Sam”…" autocomplete="off">
          <button class="csend" id="csend">↑</button></div></div>`;
    document.body.appendChild(wrap); document.body.style.overflow="hidden";
    const close=()=>{ wrap.remove(); document.body.style.overflow=""; try{proofAudio&&proofAudio.pause();}catch(e){} };
    wrap.querySelector(".sheet-bg").onclick=close; wrap.querySelector(".sheet-close").onclick=close;
    attachSheetDrag(wrap.querySelector(".sheet"), close);
    const box=wrap.querySelector("#chatmsgs"), input=wrap.querySelector("#chatin"), send=wrap.querySelector("#csend");
    renderChatMsgs(box);
    const doSend=async()=>{
      const q=input.value.trim(); if(!q) return; input.value="";
      chatHist.push({role:"user", content:q}); renderChatMsgs(box); box.scrollTop=box.scrollHeight;
      const t=document.createElement("div"); t.className="cmsg ai typing"; t.textContent="thinking…"; box.appendChild(t); box.scrollTop=box.scrollHeight;
      try{
        const r=await api(`/api/recordings/${current.id}/chat`,{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({message:q, history:chatHist.slice(0,-1).map(m=>({role:m.role,content:m.content}))})});
        chatHist.push({role:"assistant", content:r.answer||"", quotes:r.quotes||[]});
        if (r.applied_edits && r.applied_edits.length){ cache=[]; toast("Updated names"); try{ current=await api(`/api/recordings/${current.id}`);}catch(e){} }
        renderChatMsgs(box); box.scrollTop=box.scrollHeight;
      }catch(e){ chatHist.push({role:"assistant", content:"⚠ "+(e.message||"failed"), quotes:[]}); renderChatMsgs(box); }
    };
    send.onclick=doSend; input.onkeydown=e=>{ if(e.key==="Enter") doSend(); };
    requestAnimationFrame(()=>wrap.classList.add("open")); setTimeout(()=>input.focus(),320);
  }

  // ===== SETTINGS =====
  async function showSettings(){
    setSubline("Settings");
    app.innerHTML=`<div class="view">${masthead({title:"Settings"})}${skeletons(2)}</div>`;
    let st={}, sys={systems:[]}, crm={}, cal={}, dk={}, vp={enrolled:[]};
    try { st=await api("/api/settings"); } catch(e){ return authOrError(e,showSettings); }
    try { sys=await api("/api/systems"); } catch(e){}
    try { crm=await api("/api/crm/status"); } catch(e){}
    try { cal=await api("/api/cal/status"); } catch(e){}
    try { dk=await api("/api/data/key"); } catch(e){}
    try { vp=await api("/api/voiceprints"); } catch(e){}
    const url=st.public_url||"";
    const share = url ? `<div class="sharebox">
        <div class="badge"><span class="dot"></span>Your link is live</div>
        <div style="font-size:14px;color:var(--ink-soft);margin-bottom:12px;line-height:1.45">Open Lucid from your phone anywhere — it’s protected by your password.</div>
        <div class="linkrow"><span>🔗</span><code id="shareUrl">${h(url)}</code><span class="copy" id="copyLink">Copy</span></div>
        <button class="btn ghost" id="restartTun" style="width:100%">Restart public link</button>
      </div>` : `<div class="sharebox"><div class="badge" style="color:var(--muted)"><span class="dot" style="background:var(--muted);animation:none"></span>Public link starting…</div>
        <div style="font-size:13.5px;color:var(--muted)">Your Cloudflare link will appear here shortly — reopen Settings to refresh.</div></div>`;

    const sysHTML=(sys.systems||[]).map(s=>`<div class="syschip">
        <span class="sdot ${s.ok}"></span><span class="slab">${h(s.label)}</span><span class="sdet">${h(s.detail)}</span></div>`).join("");

    const lastSync = crm.last_refresh ? new Date(crm.last_refresh*1000).toLocaleString() : "never";
    const crmPanel = crm.connected ? `
      <div class="panel"><h2>Client names · Notion</h2>
        <div class="kv"><span class="k">Connection</span><span class="v ok">Connected · ${crm.contact_count||0} clients</span></div>
        <div class="kv"><span class="k">Last synced</span><span class="v">${h(lastSync)}</span></div>
        <p style="color:var(--muted);font-size:13px;margin:10px 0 0;line-height:1.5">Lucid reads these names so it spells your clients right in notes. Read-only — it never writes anything to Notion.</p>
        <div class="btnrow" style="margin-top:14px">
          <button class="btn" id="crmSync">Sync clients now</button>
          <button class="btn ghost" id="crmDisc">Disconnect</button></div>
      </div>` : `
      <div class="panel"><h2>Client names · Notion</h2>
        <p style="color:var(--muted);font-size:14px;margin:0 0 12px;line-height:1.55">Connect your Notion client database so Lucid spells client names right in your notes. <a href="https://www.notion.so/my-integrations" target="_blank" rel="noopener">Create an integration</a>, copy its secret, then open your clients database in Notion → <b>•••</b> → <b>Connections</b> → add it. Read-only — Lucid never writes to Notion.</p>
        <div class="field"><label>Notion integration secret</label><input id="crmToken" placeholder="ntn_… or secret_…" autocomplete="off"></div>
        <div class="field"><label>Clients database link</label><input id="crmDb" placeholder="https://www.notion.so/…" autocomplete="off"></div>
        <div class="btnrow" style="margin-top:6px"><button class="btn" id="crmConnect">Connect Notion</button></div>
        <div id="crmMsg" style="font-size:13px;color:var(--muted);margin-top:10px"></div>
      </div>`;

    const calSync = cal.last_refresh ? new Date(cal.last_refresh*1000).toLocaleString() : "never";
    const calPanel = cal.connected ? `
      <div class="panel"><h2>Calendar matching</h2>
        <div class="kv"><span class="k">Connection</span><span class="v ok">Connected · ${cal.event_count||0} events</span></div>
        <div class="kv"><span class="k">Last synced</span><span class="v">${h(calSync)}</span></div>
        <p style="color:var(--muted);font-size:13px;margin:10px 0 0;line-height:1.5">For each recording, Lucid finds the meeting at that time and uses its attendees' real names + topic for accurate notes. Read-only.</p>
        <div class="btnrow" style="margin-top:14px">
          <button class="btn" id="calSyncBtn">Sync calendar now</button>
          <button class="btn ghost" id="calDisc">Disconnect</button></div>
      </div>` : `
      <div class="panel"><h2>Calendar matching</h2>
        <p style="color:var(--muted);font-size:14px;margin:0 0 12px;line-height:1.55">Paste your calendar's <b>secret iCal address</b> so Lucid can match a recording to the meeting at that time and use the real attendee names + topic. In Google Calendar → calendar <b>Settings</b> → <b>Integrate calendar</b> → copy <b>Secret address in iCal format</b>. No login needed — read-only.</p>
        <div class="field"><label>Secret iCal URL</label><input id="calUrl" placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" autocomplete="off"></div>
        <div class="btnrow" style="margin-top:6px"><button class="btn" id="calConnect">Connect calendar</button></div>
        <div id="calMsg" style="font-size:13px;color:var(--muted);margin-top:10px"></div>
      </div>`;

    const enrolled = (vp.enrolled||[]);
    const ownerPanel = `
      <div class="panel"><h2>Your identity · voice notes</h2>
        <p style="color:var(--muted);font-size:14px;margin:0 0 12px;line-height:1.55">So Lucid spells your name right and attributes your own voice notes to you.</p>
        <div class="field"><label>Your name</label><input id="crmOwner" placeholder="e.g. Orion Jones" value="${attr(crm.owner_name||"")}" autocomplete="off"></div>
        <div class="kv"><span class="k">Voice enrolled</span><span class="v ${enrolled.length?'ok':''}">${enrolled.length?h(enrolled.join(', ')):'not yet'}</span></div>
        <div class="btnrow" style="margin-top:12px">
          <button class="btn ghost" id="ownerSave">Save name</button>
          <button class="btn" id="voiceEnroll">🎙 Record my voice</button></div>
        <div id="voiceMsg" style="font-size:13px;color:var(--muted);margin-top:10px"></div>
      </div>`;

    const baseUrl = (st.public_url||location.origin||"").replace(/\/$/,"");
    const apiPanel = `
      <div class="panel"><h2>Data API · for your code</h2>
        <p style="color:var(--muted);font-size:14px;margin:0 0 12px;line-height:1.55">Give this key to any code that should read your Lucid data — notes, people, and action items — as JSON. Read-only.</p>
        ${dk.key ? `
        <div class="field"><label>Your API key</label>
          <div class="linkrow"><code id="apiKey">${h(dk.key)}</code><span class="copy" id="apiCopy">Copy</span></div></div>
        <div class="kv"><span class="k">Base URL</span><span class="v"><code>${h(baseUrl)}/api/data</code></span></div>
        <p style="color:var(--muted);font-size:12.5px;margin:10px 0 0;line-height:1.5">Try it: <code>curl -H "X-API-Key: YOUR_KEY" ${h(baseUrl)}/api/data/notes</code></p>
        <div class="btnrow" style="margin-top:14px"><button class="btn ghost" id="apiRotate">Regenerate</button><button class="btn ghost" id="apiRevoke">Turn off</button></div>`
        : `<div class="btnrow"><button class="btn" id="apiGen">Generate API key</button></div>`}
      </div>`;

    app.innerHTML=`<div class="view">
      ${masthead({title:"Settings"})}
      ${share}
      <div class="panel"><h2>System status</h2>${sysHTML||'<p class="muted" style="font-size:14px">—</p>'}</div>
      <div class="panel"><h2>Configuration</h2>
        <div class="kv"><span class="k">Analysis model</span><span class="v">${h(st.analysis_model||"?")}</span></div>
        <div class="kv"><span class="k">Transcription</span><span class="v">${h(st.transcribe_backend||"?")}${st.transcribe_backend==="faster_whisper"?" · "+h(st.whisper_model||""):""}</span></div>
        <div class="kv"><span class="k">Translate to</span><span class="v">${h(st.translate_to||"off")}</span></div>
        <div class="kv"><span class="k">Plaud account</span><span class="v ${st.plaud_connected?"ok":"bad"}">${st.plaud_connected?h(st.plaud_email||"connected"):"not connected"}</span></div>
        <div class="kv"><span class="k">Sync interval</span><span class="v">${st.plaud_poll_interval||300}s</span></div>
        <div class="kv"><span class="k">Telegram</span><span class="v ${st.telegram_connected?"ok":""}">${st.telegram_connected?(st.telegram_chat_known?"connected":"connected · message your bot"):"off"}</span></div>
        <div class="btnrow" style="margin-top:14px"><a class="btn ghost" href="/setup">Re-run setup</a>${st.telegram_connected&&st.telegram_chat_known?`<button class="btn ghost" id="tgSend">📲 Send link to my phone</button>`:""}</div>
      </div>
      ${crmPanel}
      ${calPanel}
      ${ownerPanel}
      ${apiPanel}
      <div class="panel"><h2>Appearance</h2>
        <div class="field"><label>Theme</label><select id="themeSel">
          <option value="">Auto (system)</option><option value="dark">Dark</option><option value="light">Light</option></select></div></div>
      <div class="panel"><h2>About</h2>
        <p style="color:var(--muted);font-size:14px;margin:0;line-height:1.5">Lucid turns your Plaud recordings into clean, sorted notes — summaries, people, ideas, and action items — transcribed and analyzed on your own machine. Version 1.0.</p>
        <div class="btnrow" style="margin-top:14px"><button class="btn ghost danger" id="signout">Sign out</button></div></div>
    </div>`;
    const sel=document.getElementById("themeSel"); sel.value=localStorage.getItem("lucid_theme")||"";
    sel.onchange=()=>{ const v=sel.value; v?localStorage.setItem("lucid_theme",v):localStorage.removeItem("lucid_theme"); applyTheme(); };
    const cl=document.getElementById("copyLink"); if(cl) cl.onclick=async()=>{ try{ await navigator.clipboard.writeText(url); toast("Link copied"); }catch(e){ toast("Copy failed"); } };
    const rt=document.getElementById("restartTun"); if(rt) rt.onclick=async()=>{ rt.disabled=true; rt.textContent="Restarting…";
      try{ await api("/api/tunnel/restart",{method:"POST"}); toast("Restarting link…"); setTimeout(showSettings,3500);}catch(e){ toast("Failed"); rt.disabled=false; rt.textContent="Restart public link"; } };
    const ts=document.getElementById("tgSend"); if(ts) ts.onclick=async()=>{ ts.disabled=true;
      try{ const r=await api("/api/setup/telegram/test",{method:"POST"}); toast(r.sent?"Sent to your phone":"Message your bot first"); }catch(e){ toast("Failed"); } ts.disabled=false; };

    // --- Client names (Notion, read-only) ---
    const byId=id=>document.getElementById(id);
    const jh={"Content-Type":"application/json"};
    const errText=e=>{ let m=String(e&&e.message||"Failed"); try{ const j=JSON.parse(m); if(j.detail) m=j.detail; }catch(_){ } return m; };
    const ownerVal=()=>{ const el=byId("crmOwner"); return el?el.value.trim():""; };
    const cc=byId("crmConnect"); if(cc) cc.onclick=async()=>{
      const token=byId("crmToken").value.trim(), db=byId("crmDb").value.trim();
      const msg=byId("crmMsg");
      if(!token||!db){ msg.textContent="Paste both the integration secret and the database link."; return; }
      cc.disabled=true; cc.textContent="Connecting…"; msg.textContent="";
      try{ const r=await api("/api/crm/connect",{method:"POST",headers:jh,body:JSON.stringify({token,database:db,owner_name:ownerVal()})});
        toast(`Connected · ${r.contact_count} clients`); showSettings();
      }catch(e){ msg.textContent=errText(e); cc.disabled=false; cc.textContent="Connect Notion"; } };
    const cs=byId("crmSync"); if(cs) cs.onclick=async()=>{ cs.disabled=true; cs.textContent="Syncing…";
      try{ const r=await api("/api/crm/refresh",{method:"POST"}); toast(`Synced · ${r.contact_count} clients`); }catch(e){ toast(errText(e)); }
      cs.disabled=false; cs.textContent="Sync clients now"; };
    const cd=byId("crmDisc"); if(cd) cd.onclick=async()=>{ if(!confirm("Disconnect Notion? Your client data stays in Notion."))return;
      try{ await api("/api/crm/connect",{method:"DELETE"}); toast("Disconnected"); showSettings(); }catch(e){ toast("Failed"); } };

    // --- Calendar matching (iCal, read-only) ---
    const calc=byId("calConnect"); if(calc) calc.onclick=async()=>{
      const url=byId("calUrl").value.trim(), msg=byId("calMsg");
      if(!url){ msg.textContent="Paste your secret iCal URL."; return; }
      calc.disabled=true; calc.textContent="Connecting…"; msg.textContent="";
      try{ const r=await api("/api/cal/connect",{method:"POST",headers:jh,body:JSON.stringify({url})});
        toast(`Connected · ${r.event_count} events`); showSettings();
      }catch(e){ msg.textContent=errText(e); calc.disabled=false; calc.textContent="Connect calendar"; } };
    const calsb=byId("calSyncBtn"); if(calsb) calsb.onclick=async()=>{ calsb.disabled=true; calsb.textContent="Syncing…";
      try{ const r=await api("/api/cal/refresh",{method:"POST"}); toast(`Synced · ${r.event_count} events`); }catch(e){ toast(errText(e)); }
      calsb.disabled=false; calsb.textContent="Sync calendar now"; };
    const cald=byId("calDisc"); if(cald) cald.onclick=async()=>{ if(!confirm("Disconnect this calendar?"))return;
      try{ await api("/api/cal/connect",{method:"DELETE"}); toast("Disconnected"); showSettings(); }catch(e){ toast("Failed"); } };

    // --- Your identity + voice enrollment ---
    const os=byId("ownerSave"); if(os) os.onclick=async()=>{
      try{ await api("/api/settings",{method:"POST",headers:jh,body:JSON.stringify({owner_name:ownerVal()})}); toast("Saved"); }catch(e){ toast("Failed"); } };
    const ve=byId("voiceEnroll"); if(ve) ve.onclick=()=>enrollVoice(ve, byId("voiceMsg"), ownerVal());

    // --- Data API key ---
    const ag=byId("apiGen"); if(ag) ag.onclick=async()=>{ ag.disabled=true;
      try{ await api("/api/data/key/rotate",{method:"POST"}); toast("API key created"); showSettings(); }catch(e){ toast("Failed"); ag.disabled=false; } };
    const ar=byId("apiRotate"); if(ar) ar.onclick=async()=>{ if(!confirm("Regenerate the key? Code using the old key will stop working."))return;
      try{ await api("/api/data/key/rotate",{method:"POST"}); toast("New key generated"); showSettings(); }catch(e){ toast("Failed"); } };
    const av=byId("apiRevoke"); if(av) av.onclick=async()=>{ if(!confirm("Turn off the data API? Code using the key will stop working."))return;
      try{ await api("/api/data/key",{method:"DELETE"}); toast("Data API off"); showSettings(); }catch(e){ toast("Failed"); } };
    const ak=byId("apiCopy"); if(ak) ak.onclick=async()=>{ try{ await navigator.clipboard.writeText((byId("apiKey")||{}).textContent||""); toast("Key copied"); }catch(e){ toast("Copy failed"); } };
    const so=document.getElementById("signout");
    if(so) so.onclick=()=>{ localStorage.removeItem("lucid_token"); token=""; showLogin(); };
  }

  // Record ~30s of mic audio and enroll it as the owner's voiceprint.
  async function enrollVoice(btn, msg, name){
    if(!name){ if(msg) msg.textContent="Enter your name above first, then record."; return; }
    if(!navigator.mediaDevices||!window.MediaRecorder){ if(msg) msg.textContent="This browser can't record audio."; return; }
    if(btn.dataset.recording==="1"){ btn._stop&&btn._stop(); return; }
    let stream;
    try{ stream=await navigator.mediaDevices.getUserMedia({audio:true}); }
    catch(e){ if(msg) msg.textContent="Microphone permission denied."; return; }
    const rec=new MediaRecorder(stream); const chunks=[]; let secs=0;
    rec.ondataavailable=e=>{ if(e.data&&e.data.size) chunks.push(e.data); };
    const tick=setInterval(()=>{ secs++; if(msg) msg.textContent=`Recording… ${secs}s (tap to stop, ~30s is ideal)`; if(secs>=45) btn._stop(); },1000);
    btn.dataset.recording="1"; btn.textContent="⏹ Stop & save";
    btn._stop=()=>{ clearInterval(tick); try{ rec.stop(); }catch(_){}};
    rec.onstop=async()=>{
      stream.getTracks().forEach(t=>t.stop()); btn.dataset.recording=""; btn.disabled=true; btn.textContent="Saving…";
      if(msg) msg.textContent="Saving your voiceprint…";
      const blob=new Blob(chunks,{type:rec.mimeType||"audio/webm"});
      const fd=new FormData(); fd.append("file", blob, "voice.webm");
      try{
        const r=await fetch("/api/enroll?name="+encodeURIComponent(name),{method:"POST",headers: token?{"Authorization":"Bearer "+token}:{}, body:fd});
        if(!r.ok) throw new Error((await r.text())||"failed");
        toast("Voice enrolled"); showSettings();
      }catch(e){ btn.disabled=false; btn.dataset.recording=""; btn.textContent="🎙 Record my voice";
        let m=String(e.message||"Failed"); try{ const j=JSON.parse(m); if(j.detail) m=j.detail; }catch(_){ }
        if(msg) msg.textContent=m; }
    };
    rec.start();
  }

  async function reanalyze(id){ try{ await api(`/api/recordings/${id}/reanalyze`,{method:"POST"}); toast("Re-analyzing…"); showDetail(id);}catch(e){toast("Failed");} }
  // autofill name picker — suggests people Lucid already knows as you type
  function namePicker({title, sub, value}={}){
    return new Promise((resolve)=>{
      const wrap=document.createElement("div"); wrap.className="namepick";
      wrap.innerHTML=`<div class="bg"></div><div class="namecard">
        <h3>${h(title||"Set name")}</h3>${sub?`<div class="sub">${h(sub)}</div>`:""}
        <input class="nameinput" id="npIn" placeholder="Type a name…" value="${attr(value||"")}" autocomplete="off" autocapitalize="words">
        <div class="suggrow" id="npSug"></div>
        <div class="namebtns"><button class="btn ghost" id="npCancel">Cancel</button>
          <button class="btn" id="npOk">Save</button></div></div>`;
      document.body.appendChild(wrap); document.body.style.overflow="hidden";
      const inp=wrap.querySelector("#npIn"), sug=wrap.querySelector("#npSug");
      const done=(v)=>{ wrap.remove(); document.body.style.overflow=""; resolve(v); };
      wrap.querySelector(".bg").onclick=()=>done(null);
      wrap.querySelector("#npCancel").onclick=()=>done(null);
      wrap.querySelector("#npOk").onclick=()=>{ const v=inp.value.trim(); done(v||null); };
      inp.onkeydown=e=>{ if(e.key==="Enter"){ const v=inp.value.trim(); done(v||null);} if(e.key==="Escape") done(null); };
      let t=null;
      const loadSug=(q)=>{ clearTimeout(t); t=setTimeout(async()=>{
        let names=[]; try{ names=await api("/api/people/autofill?q="+encodeURIComponent(q||"")); }catch(e){}
        names=names.filter(n=>n.toLowerCase()!==(value||"").toLowerCase());
        sug.innerHTML=names.length?names.map(n=>`<span class="sugname" data-n="${attr(n)}"><span class="knw">known</span>${h(n)}</span>`).join("")
          : `<span class="sub" style="font-size:12.5px">No saved people yet — type a name and it'll be remembered.</span>`;
        sug.querySelectorAll(".sugname").forEach(s=>s.onclick=()=>done(s.dataset.n)); },140); };
      inp.oninput=()=>loadSug(inp.value.trim());
      loadSug("");
      requestAnimationFrame(()=>{ inp.focus(); inp.select(); });
    });
  }

  async function rename(id, from){
    const isSpk = isUnnamedSpk(from);
    const to = await namePicker({ title:`Who is “${from}”?`,
      sub: isSpk ? "Name this voice — Lucid will recognize them by voice in future recordings."
                 : "Pick someone Lucid already knows, or type a name — it'll autofill next time.", value: isSpk?"":from });
    if (!to || to === from) return;
    try {
      await api(`/api/recordings/${id}/rename`, { method:"POST",
        headers:{"Content-Type":"application/json"}, body: JSON.stringify({ from, to }) });
      toast(isSpk ? `Got it — learning ${to}’s voice` : "Renamed & learned"); cache = []; showDetail(id);
    } catch(e){ toast("Rename failed"); }
  }
  let _pendingDel=null;
  async function _flushDel(){ const p=_pendingDel; _pendingDel=null;
    if(p){ try{ await api(`/api/recordings/${p.id}`,{method:"DELETE"}); }catch(_){} } }
  async function del(id){
    await _flushDel();                                  // commit any earlier pending delete first
    const idx=cache.findIndex(r=>r.id===id), rec=cache[idx];
    if(idx<0){ try{ await api(`/api/recordings/${id}`,{method:"DELETE"}); }catch(_){} cache=[]; go("/lucid/notes"); return; }
    cache=cache.filter(r=>r.id!==id);                   // optimistic remove
    _pendingDel={id, rec, idx};
    if(location.pathname.startsWith("/r/")) go("/lucid/notes");
    else if(location.pathname.startsWith("/lucid")) paintNotes();
    toast("Note deleted", {label:"Undo", ms:6000, run:()=>{
      if(_pendingDel && _pendingDel.id===id){ const p=_pendingDel; _pendingDel=null;
        cache.splice(Math.min(p.idx,cache.length),0,p.rec);
        if(location.pathname.startsWith("/lucid")) paintNotes(); else route(); } }});
    setTimeout(()=>{ if(_pendingDel && _pendingDel.id===id) _flushDel(); }, 6300);
  }

  function authOrError(e,retry){
    if (String(e.message)==="auth"){ return showLogin(retry); }
    app.innerHTML=`<div class="view"><div class="empty">⚠ ${h(e.message)}<br><br>
      <button class="btn" id="rt">Retry</button></div></div>`; document.getElementById("rt").onclick=retry;
  }

  async function showLogin(retry){
    const finish=(d)=>{ token=(d&&d.token)||""; localStorage.setItem("lucid_token",token); (retry||route)(); };
    let st={};
    try{ st=await fetch("/api/login/email/status").then(r=>r.json()); }catch(_){}
    if(!st.available) return passwordLogin(retry,finish);   // silent fallback only if Gmail is down
    const sendCode=()=>fetch("/api/login/email/request",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}).catch(()=>{});
    sendCode();                                              // code is already waiting when you look
    app.innerHTML=`<div class="login"><div class="login-card">
      <div class="lock">✦</div><h2>Sign in to Lucid</h2>
      <p>We just emailed a 6-digit code to <b>${h(st.hint||"your email")}</b>. Enter it to continue.</p>
      <div class="login-err" id="lerr"></div>
      <input id="lcode" type="text" inputmode="numeric" maxlength="6" placeholder="••• •••" autocomplete="one-time-code" />
      <button class="btn primary" id="lbtn">Sign in</button>
      <button class="loginalt" id="lresend">Resend code</button>
    </div></div>`;
    const c=document.getElementById("lcode"), btn=document.getElementById("lbtn"), err=document.getElementById("lerr");
    document.getElementById("lresend").onclick=async(e)=>{ e.target.disabled=true; await sendCode(); err.textContent="New code sent."; setTimeout(()=>{try{e.target.disabled=false;}catch(_){}},2500); };
    const go=async()=>{ const v=c.value.replace(/\D/g,""); if(v.length<6){ err.textContent="Enter the 6-digit code."; return; }
      if(btn.disabled) return;
      btn.disabled=true; err.textContent="";
      try{ const r=await fetch("/api/login/email/verify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:v})});
        if(!r.ok){ err.textContent=r.status===429?"Too many tries — wait a moment.":"That code is wrong or expired."; btn.disabled=false; return; }
        finish(await r.json());
      }catch(e){ err.textContent="Network error — try again."; btn.disabled=false; } };
    btn.onclick=go; c.onkeydown=e=>{ if(e.key==="Enter") go(); };
    c.oninput=()=>{ if(c.value.replace(/\D/g,"").length===6) go(); };   // auto-submit when 6 digits in
    setTimeout(()=>{try{c.focus();}catch(_){}} ,120);
  }
  function passwordLogin(retry,finish){
    app.innerHTML=`<div class="login"><div class="login-card">
      <div class="lock">🔒</div><h2>Welcome back</h2>
      <p>Enter your Lucid password to open your notes.</p>
      <div class="login-err" id="lerr"></div>
      <input id="lpw" type="password" placeholder="Password" autocomplete="current-password" />
      <button class="btn primary" id="lbtn">Unlock</button>
    </div></div>`;
    const pw=document.getElementById("lpw"), btn=document.getElementById("lbtn"), err=document.getElementById("lerr");
    const submit=async()=>{ const v=pw.value.trim(); if(!v) return; btn.disabled=true; err.textContent="";
      try{ const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:v})});
        if(!r.ok){ err.textContent=r.status===401?"Incorrect password.":"Couldn’t log in."; btn.disabled=false; return; }
        finish(await r.json());
      }catch(e){ err.textContent="Network error — try again."; btn.disabled=false; } };
    btn.onclick=submit; pw.onkeydown=e=>{ if(e.key==="Enter") submit(); };
    setTimeout(()=>{ try{pw.focus();}catch(_){}} ,120);
  }

  bindWorkBtn();
  route();
  return { go, reanalyze, del, rename, chat, masthead, setSubline, datelineStr };
})();
