/* Lucid v2 — calm personal conversation-memory journal. Vanilla JS. */
const App = (() => {
  const app = document.getElementById("app");
  const toastEl = document.getElementById("toast");

  // token from deep link (?k=) kept for backwards-compat; harmless if unused
  const u0 = new URL(location.href);
  if (u0.searchParams.get("k")) { localStorage.setItem("lucid_token", u0.searchParams.get("k"));
    u0.searchParams.delete("k"); history.replaceState({}, "", u0.pathname + u0.hash); }
  let token = localStorage.getItem("lucid_token") || "";

  // theme
  const applyTheme = () => { const t = localStorage.getItem("lucid_theme");
    if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme"); };
  applyTheme();
  document.getElementById("themeBtn").onclick = () => {
    const cur = localStorage.getItem("lucid_theme");
    const next = cur === "dark" ? "light" : cur === "light" ? "" : "dark";
    next ? localStorage.setItem("lucid_theme", next) : localStorage.removeItem("lucid_theme"); applyTheme();
  };

  // helpers
  const h = (s) => (s == null ? "" : String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"));
  const fmt = (sec) => { sec = Math.max(0, Math.floor(sec||0));
    const m=Math.floor(sec/60), s=sec%60, hh=Math.floor(m/60), mm=m%60;
    return hh ? `${hh}:${String(mm).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${mm}:${String(s).padStart(2,"0")}`; };
  const rel = (iso) => { if(!iso) return ""; const d=new Date(iso), diff=(Date.now()-d)/1000;
    if (diff<60) return "just now"; if (diff<3600) return `${Math.floor(diff/60)}m ago`;
    if (diff<86400) return `${Math.floor(diff/3600)}h ago`;
    return d.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"}); };
  const dayBucket = (iso) => { const d=new Date(iso); const now=new Date();
    const sd=(x)=>new Date(x.getFullYear(),x.getMonth(),x.getDate()).getTime();
    const days=Math.round((sd(now)-sd(d))/86400000);
    if (days<=0) return "Today"; if (days===1) return "Yesterday"; if (days<7) return "This week";
    if (days<30) return "This month"; return "Earlier"; };
  const toast = (m) => { toastEl.textContent=m; toastEl.classList.add("show");
    clearTimeout(toast._t); toast._t=setTimeout(()=>toastEl.classList.remove("show"),2200); };
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

  // routing
  let cache=[], pollTimer=null;
  let homeFilter="all", homeSort="newest";
  const clearPoll=()=>{ if(pollTimer){clearTimeout(pollTimer); pollTimer=null;} };
  const setTab=(n)=>document.querySelectorAll(".tabbar button").forEach(b=>b.classList.toggle("active",b.dataset.tab===n));
  const go=(p)=>{ history.pushState({},"",p); route(); };
  window.onpopstate=route;
  document.querySelectorAll(".tabbar button").forEach(b=>b.onclick=()=>go(b.dataset.tab==="home"?"/":"/"+b.dataset.tab));

  function route(){ clearPoll(); window.scrollTo(0,0); const p=location.pathname;
    const m=p.match(/^\/r\/([\w-]+)/);
    if (m){ setTab("home"); return showDetail(m[1]); }
    const pm=p.match(/^\/people\/(.+)$/);
    if (pm){ setTab("people"); return showPerson(decodeURIComponent(pm[1])); }
    if (p==="/people"){ setTab("people"); return showPeople(); }
    if (p==="/directory"){ setTab("people"); return showDirectory(); }
    const vm=p.match(/^\/ventures\/(.+)$/);
    if (vm){ setTab("ventures"); return showVenture(decodeURIComponent(vm[1])); }
    if (p==="/ventures"){ setTab("ventures"); return showVentures(); }
    if (p==="/search"){ setTab("search"); return showSearch(); }
    if (p==="/settings"){ setTab("settings"); return showSettings(); }
    setTab("home"); return showHome(); }

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

  async function showHome(){
    if (!cache.length) app.innerHTML=`<div class="view"><div class="hero"><h1>…</h1></div>${skeletons()}</div>`;
    let recs; try { recs=await api("/api/recordings"); } catch(e){ return authOrError(e,showHome); }
    cache=recs; paintHome();
    if (recs.some(r=>!["done","error"].includes(r.status))) pollTimer=setTimeout(showHome,4000);
  }

  function paintHome(){
    const recs=cache;
    const done=recs.filter(r=>r.status==="done");
    const mins=Math.round(done.reduce((a,r)=>a+(r.duration||0),0)/60);
    const tense=done.filter(r=>mood(r).k==="tense").length;
    const hr=new Date().getHours();
    const greet=hr<12?"Good morning":hr<18?"Good afternoon":"Good evening";
    document.getElementById("subline").textContent=recs.length?`${recs.length} note${recs.length>1?"s":""}`:"notes";

    const ft=FILTERS.find(f=>f.k===homeFilter)||FILTERS[0];
    let list=recs.filter(ft.test);
    if (homeSort==="oldest") list=[...list].reverse();
    else if (homeSort==="longest") list=[...list].sort((a,b)=>(b.duration||0)-(a.duration||0));

    let body;
    if (!recs.length){
      body=`<div class="empty"><div class="big">◐</div>No notes yet.
        <div class="hint">Record on your Plaud — your notes appear here automatically, sorted and ready.</div></div>`;
    } else if (!list.length){
      body=`<div class="empty"><div class="big">◌</div>Nothing matches this filter.
        <div class="hint">Try “All”.</div></div>`;
    } else if (homeSort==="newest"){
      const groups={}; list.forEach(r=>{ const b=dayBucket(r.created_at)||"Earlier"; (groups[b]=groups[b]||[]).push(r); });
      const order=["Today","Yesterday","This week","This month","Earlier"];
      body=order.filter(k=>groups[k]).map(k=>`<div class="daygroup">
        <div class="daylabel">${k}</div><div class="feed">${groups[k].map(cardHTML).join("")}</div></div>`).join("");
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

    app.innerHTML=`<div class="view">
      <div class="hero"><div class="greeting">${greet}</div>
        <h1>Your notes <span class="count">${done.length} sorted</span></h1>
        <div class="stats">
          ${mins?`<span><b>${mins}</b> min captured</span>`:""}
          ${tense?`<span><span class="stat-dot"></span><b>${tense}</b> tense</span>`:""}
        </div>
      </div>${filterbar}${body}</div>`;
    bindCards();
    app.querySelectorAll(".fchip").forEach(b=>b.onclick=()=>{ homeFilter=b.dataset.f; paintHome(); });
    const ss=document.getElementById("sortSel"); if(ss) ss.onchange=()=>{ homeSort=ss.value; paintHome(); };
  }

  function cardHTML(r){
    const m=mood(r);
    const title=r.headline||(r.status==="done"?"Untitled":"New recording");
    const topics=(r.topics||[]).slice(0,2).map(t=>`<span class="chip">${h(t)}</span>`).join("");
    const proc=["done","error"].includes(r.status)?"":`<span class="proc"><span class="spin"></span>${h(r.status)}…</span>`;
    return `<div class="rcard" data-id="${r.id}" style="--mc:${m.c}">
      <div class="tile ${m.k}"></div>
      <div class="rbody"><h3>${h(title)}</h3>
        ${r.summary?`<div class="snip">${h(r.summary)}</div>`:""}
        <div class="rmeta">
          ${r.status==="done"?`<span class="chip mood">${m.w}</span>`:proc}
          ${r.duration?`<span class="chip">${fmt(r.duration)}</span>`:""}${topics}
          <span class="time">${h(rel(r.created_at))}</span>
        </div></div></div>`;
  }
  const bindCards=()=>app.querySelectorAll(".rcard").forEach(c=>c.onclick=()=>go("/r/"+c.dataset.id));

  // ===== SEARCH =====
  async function showSearch(){
    if (!cache.length){ try { cache=await api("/api/recordings"); } catch(e){ return authOrError(e,showSearch); } }
    app.innerHTML=`<div class="view">
      <div class="searchwrap"><span class="mag">⌕</span>
        <input id="q" placeholder="Search conversations, topics, ideas…" autofocus></div>
      <div id="results" class="feed"></div><div id="hint" class="empty" style="padding:36px"></div></div>`;
    const q=document.getElementById("q"), results=document.getElementById("results"), hint=document.getElementById("hint");
    const run=()=>{ const term=q.value.trim().toLowerCase();
      if (!term){ results.innerHTML=""; hint.textContent="Type to search your memories."; return; }
      const hits=cache.filter(r=>JSON.stringify([r.headline,r.summary,(r.topics||[]).join(" ")]).toLowerCase().includes(term));
      hint.textContent=hits.length?"":"No matches.";
      results.innerHTML=hits.map(cardHTML).join("");
      results.querySelectorAll(".rcard").forEach(c=>c.onclick=()=>go("/r/"+c.dataset.id)); };
    q.oninput=run; run();
  }

  // ===== PEOPLE (relationships over time) =====
  const toneClass = (t)=> t==="warm"?"pos":t==="strained"?"ten":"neu";
  const toneWord  = (t)=> t==="warm"?"warm":t==="strained"?"strained":t==="mixed"?"mixed":"neutral";
  const trendWord = (t)=> t==="warming"?"↗ warming":t==="cooling"?"↘ cooling":"→ steady";
  const dateShort = (iso)=>{ if(!iso) return ""; const d=new Date(iso);
    return d.toLocaleDateString(undefined,{month:"short",day:"numeric"}); };
  function valBar(p){ const tot=(p.positive||0)+(p.negative||0)+(p.neutral||0);
    if(!tot) return `<div class="vbar empty"></div>`;
    const w=(n)=>(100*n/tot).toFixed(1)+"%";
    return `<div class="vbar">
      <span style="width:${w(p.positive)};background:var(--pos)"></span>
      <span style="width:${w(p.neutral)};background:var(--neu)"></span>
      <span style="width:${w(p.negative)};background:var(--ten)"></span></div>`; }

  let pplCache=[], dirCache=[], peopleMode="rel", selMode=false, sel=new Set(), suggestions=null;
  const peopleSeg=()=>`<div class="segtoggle" id="pseg">
      <button data-pm="rel" class="${peopleMode==="rel"?"on":""}">Relationships</button>
      <button data-pm="dir" class="${peopleMode==="dir"?"on":""}">Directory</button></div>`;
  function bindSeg(){ const s=document.getElementById("pseg"); if(!s) return;
    s.querySelectorAll("[data-pm]").forEach(b=>b.onclick=()=>{ if(b.dataset.pm==="dir"){go("/directory");} else {go("/people");} }); }

  async function showPeople(){
    peopleMode="rel";
    app.innerHTML=`<div class="view"><div class="hero"><h1>People</h1></div>${peopleSeg()}${skeletons(3)}</div>`;
    bindSeg();
    let ppl; try { ppl=await api("/api/people"); } catch(e){ return authOrError(e,showPeople); }
    pplCache=ppl; renderPeople();
  }

  async function showDirectory(){
    peopleMode="dir"; selMode=false; sel.clear();
    app.innerHTML=`<div class="view"><div class="hero"><h1>People</h1></div>${peopleSeg()}${skeletons(3)}</div>`;
    bindSeg();
    let dir; try { dir=await api("/api/directory"); } catch(e){ return authOrError(e,showDirectory); }
    dirCache=dir; renderDirectory();
  }

  function renderDirectory(){
    const dir=dirCache;
    document.getElementById("subline").textContent = dir.length?`${dir.length} learned`:"directory";
    const known=dir.filter(e=>e.recognition!=="new").length;
    const intro=`<div class="hero"><h1>People</h1>
      <div class="stats"><span><b>${dir.length}</b> known</span>
        <span><b>${known}</b> recognized</span></div></div>`;
    let body;
    if(!dir.length){
      body=`<div class="empty"><div class="big">&#9737;</div>Nothing learned yet.
        <div class="hint">As recordings come in, Lucid learns each person's voice and way
        of speaking — and remembers every name you set.</div></div>`;
    } else {
      body=dir.map(e=>{
        const words=(e.top_words||[]).slice(0,8).map(w=>`<span class="word">${h(w)}</span>`).join("");
        const phr=(e.phrases||[]).map(p=>`<div class="dirphrase">&ldquo;${h(p)}&rdquo;</div>`).join("");
        const al=(e.aliases||[]).length?`<div class="aliasrow">${e.aliases.map(a=>`<span class="aliaschip">aka ${h(a)}</span>`).join("")}</div>`:"";
        const voice=e.has_voice?`<span>&#127908; voice &times;${e.voice_samples}</span>`:"";
        return `<div class="dircard" data-id="${attr(e.id)}">
          <div class="dirtop"><div class="nm">${h(e.name)}</div>
            <span class="recbadge ${e.recognition}">${e.recognition}</span></div>
          ${e.role?`<div class="snip" style="color:var(--muted);font-size:14px;margin-top:3px">${h(e.role)}</div>`:""}
          ${al}
          <div class="dirmeta"><span><b>${e.seen_count}</b> conversation${e.seen_count===1?"":"s"}</span>
            ${e.corrections?`<span><b>${e.corrections}</b> correction${e.corrections===1?"":"s"}</span>`:""}
            ${voice}</div>
          ${words?`<div class="dirsec"><div class="lbl">How they speak</div><div class="wordcloud">${words}</div></div>`:""}
          ${phr?`<div class="dirsec"><div class="lbl">Things they've said</div>${phr}</div>`:""}
          <button class="dirforget" data-forget="${attr(e.id)}">Forget this person</button>
        </div>`;
      }).join("");
    }
    app.innerHTML=`<div class="view">${intro}${peopleSeg()}${body}</div>`;
    bindSeg();
    app.querySelectorAll("[data-forget]").forEach(b=>b.onclick=async()=>{
      if(!confirm("Forget everything learned about this person? (their recordings stay)")) return;
      try{ await api("/api/directory/"+encodeURIComponent(b.dataset.forget),{method:"DELETE"}); toast("Forgotten"); showDirectory(); }
      catch(e){ toast("Failed"); }
    });
  }

  function pcardHTML(p){
    const nat=(p.natures||[]).slice(0,3).map(n=>`<span class="chip">${h(n)}</span>`).join("");
    const checked=sel.has(p.key);
    const box=selMode?`<span class="pcheck${checked?" on":""}">${checked?"&#10003;":""}</span>`:"";
    return `<div class="pcard${selMode?" selmode":""}${checked?" picked":""}" data-key="${attr(p.key)}" style="--mc:var(--${toneClass(p.tone)})">
      ${box}${ringHTML(`var(--${toneClass(p.tone)})`,72,"&#9737;")}
      <div class="rbody">
        <h3>${h(p.name)} <span class="pcount">&times;${p.interactions}</span></h3>
        ${p.role?`<div class="snip">${h(p.role)}</div>`:""}
        ${valBar(p)}
        <div class="rmeta">
          <span class="chip mood">${toneWord(p.tone)}</span>
          <span class="chip">${trendWord(p.trend)}</span>
          ${nat}
          <span class="time">${h(rel(p.last_seen))}</span>
        </div></div></div>`;
  }

  function renderPeople(){
    const ppl=pplCache;
    document.getElementById("subline").textContent = ppl.length?`${ppl.length} relationship${ppl.length>1?"s":""}`:"relationships";
    const nameOf=(k)=>(ppl.find(p=>p.key===k)||{}).name||k;

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

    let body;
    if(!ppl.length){
      body=`<div class="empty"><div class="big">&#9737;</div>No people yet.
        <div class="hint">As you record conversations, the people in them — and how
        your relationships evolve — gather here.</div></div>`;
    } else {
      body=`<div class="ptools">
          <button class="btn ghost" id="findDup">&#10022; Find duplicates</button>
          <button class="btn ghost" id="selBtn">${selMode?"Done":"Select"}</button>
        </div>${sugHTML}
        <div class="feed">${ppl.map(pcardHTML).join("")}</div>`;
    }
    app.innerHTML=`<div class="view"><div class="hero"><h1>People</h1>
      <div class="stats"><span><b>${ppl.length}</b> ${ppl.length===1?"person":"people"} across your recordings</span></div>
      </div>${peopleSeg()}${body}
      ${selMode&&sel.size?`<div class="selbar"><span>${sel.size} selected</span>
        <button class="btn" id="combineBtn" ${sel.size<2?"disabled":""}>Combine</button>
        <button class="btn ghost" id="deleteBtn">Delete</button></div>`:""}</div>`;
    bindSeg();
    app.querySelectorAll(".pcard").forEach(c=>c.onclick=()=>{
      const k=c.dataset.key;
      if(selMode){ sel.has(k)?sel.delete(k):sel.add(k); renderPeople(); }
      else go("/people/"+encodeURIComponent(k));
    });
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
    app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/people')">&larr; People</span>${skeletons(2)}</div>`;
    let p; try { p=await api("/api/people/"+encodeURIComponent(key)); } catch(e){ return authOrError(e,()=>showPerson(key)); }
    const span = p.first_seen===p.last_seen ? dateShort(p.first_seen)
      : `${dateShort(p.first_seen)} – ${dateShort(p.last_seen)}`;
    const natures=(p.natures||[]).map(n=>`<span class="chip">${h(n)}</span>`).join("");
    const arc = p.trend==="warming" ? "This relationship has been warming over time."
      : p.trend==="cooling" ? "This relationship has been cooling over time."
      : p.tone==="warm" ? "Consistently warm across your conversations."
      : p.tone==="strained" ? "Has carried recurring tension."
      : "Steady across your conversations.";

    const tl = (p.timeline||[]).map(i=>{
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
        <div class="tdate">${dateShort(i.date)}</div>
        <div class="tcard">
          <h3>${h(i.headline)} <span class="chev">&rsaquo;</span></h3>
          ${i.role?`<div class="prole">${h(i.role)}</div>`:""}
          ${rels}${psy}${qs}${pc}
          ${i.sentiment?`<div class="arc">&#9709; ${h(i.sentiment)}</div>`:""}
        </div></div>`;}).join("");

    app.innerHTML=`<div class="view" style="--mc:var(--${toneClass(p.tone)})">
      <span class="backlink" onclick="App.go('/people')">&larr; People</span>
      <div class="dhero">${ringHTML(`var(--${toneClass(p.tone)})`,72,"&#9737;")}
        <div><h1>${h(p.name)}</h1>
          <div class="dmeta"><span class="mc">${toneWord(p.tone)}</span>
            <span>&middot; ${trendWord(p.trend)}</span>
            <span>&middot; ${p.interactions} conversation${p.interactions>1?"s":""}</span>
            <span>&middot; ${span}</span></div></div></div>
      <div class="panel"><h2>The relationship</h2>
        <p class="lead">${arc}</p>
        ${valBar(p)}
        <div class="vkey"><span><i style="background:var(--pos)"></i>${p.positive} positive</span>
          <span><i style="background:var(--ten)"></i>${p.negative} concerning</span>
          <span><i style="background:var(--neu)"></i>${p.neutral} neutral</span></div>
        ${p.roles&&p.roles.length?`<div class="prole">${h(p.roles[0])}</div>`:""}
        ${natures?`<div class="chips" style="margin-top:10px">${natures}</div>`:""}</div>
      <div class="panel"><h2>Over time &middot; ${p.interactions} interaction${p.interactions>1?"s":""}</h2>
        <div class="timeline-people">${tl}</div></div></div>`;
    app.querySelectorAll(".tinter").forEach(c=>c.onclick=()=>go("/r/"+c.dataset.rid));
  }

  // ===== VENTURES (business ideas → build specs) =====
  async function showVentures(){
    app.innerHTML=`<div class="view"><div class="hero"><h1>Ventures</h1></div>${skeletons(3)}</div>`;
    let vs; try{ vs=await api("/api/ventures"); }catch(e){ return authOrError(e,showVentures); }
    document.getElementById("subline").textContent = vs.length?`${vs.length} idea${vs.length>1?"s":""}`:"ventures";
    let body;
    if(!vs.length){
      body=`<div class="empty"><div class="big">&#9650;</div>No business ideas yet.
        <div class="hint">When you and the people around you brainstorm in a recording,
        the ventures gather here — each with a full build plan.</div></div>`;
    } else {
      body=`<div class="feed">${vs.map(v=>`<div class="vcard" data-id="${attr(v.id)}">
        <div class="vbody">
          <div class="vtop"><h3>${h(v.title)}</h3>${v.has_spec?`<span class="vspec">plan ✓</span>`:""}</div>
          ${v.summary?`<div class="snip">${h(v.summary)}</div>`:""}
          <div class="rmeta">
            ${v.status?`<span class="chip">${h(v.status)}</span>`:""}
            ${(v.people||[]).slice(0,3).map(p=>`<span class="chip">${h(p)}</span>`).join("")}
            ${v.mentions>1?`<span class="chip">${v.mentions}× discussed</span>`:""}
          </div></div></div>`).join("")}</div>`;
    }
    app.innerHTML=`<div class="view"><div class="hero"><div class="greeting">Your ideas, made buildable</div>
      <h1>Ventures <span class="count">(${vs.length})</span></h1></div>${body}</div>`;
    app.querySelectorAll(".vcard").forEach(c=>c.onclick=()=>go("/ventures/"+encodeURIComponent(c.dataset.id)));
  }

  async function showVenture(id){
    app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/ventures')">← Ventures</span>${skeletons(2)}</div>`;
    let v; try{ v=await api("/api/ventures/"+encodeURIComponent(id)); }catch(e){ return authOrError(e,()=>showVenture(id)); }
    renderVenture(v);
  }

  function renderVenture(v){
    const persp=(v.perspectives||[]).map(p=>{const ss=(p.stance||"").replace(/\s+/g,"-"); return `<div class="persp">
      <span class="persp-n">${h(p.person)}</span>${p.stance?`<span class="persp-s s-${h(ss)}">${h(p.stance)}</span>`:""}
      <div class="persp-v">${h(p.view)}</div></div>`;}).join("");
    const srcs=(v.sources||[]).map(s=>`<span class="vsrc" data-rid="${s.rec_id}">${h(s.headline||"recording")}</span>`).join("");
    const spec=v.spec;
    const specBlock = spec ? renderSpec(v, spec) :
      `<div class="panel vbuild"><h2>Build plan</h2>
        <p class="muted" style="font-size:14px;margin-top:0">Generate a complete, buildable spec —
        stack, data model, features, roadmap, and first steps — predicted from your discussion and
        ready to hand to Claude Code.</p>
        <button class="btn" id="genBtn">✦ Generate build plan</button></div>`;

    app.innerHTML=`<div class="view">
      <span class="backlink" onclick="App.go('/ventures')">← Ventures</span>
      <div class="dhero"><div class="ring"><span class="glyph">▲</span></div>
        <div><h1>${h(v.title)}</h1>
          <div class="dmeta">${v.proposed_by?`<span class="mc">${h(v.proposed_by)}</span>`:""}
            ${v.status?`<span>· ${h(v.status)}</span>`:""}<span>· ${(v.sources||[]).length} mention${(v.sources||[]).length>1?"s":""}</span></div></div></div>
      <div class="panel"><h2>The idea</h2>
        ${v.summary?`<p class="lead">${h(v.summary)}</p>`:""}
        ${v.details?`<div class="idea-d">${h(v.details)}</div>`:""}
        ${persp?`<div class="idea-p">${persp}</div>`:""}
        ${srcs?`<div class="vsrcs">From: ${srcs}</div>`:""}</div>
      ${specBlock}</div>`;

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
      <div class="panel vplan"><h2>Build plan</h2>
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
    app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/')">← Home</span>${skeletons(1)}
      <div style="height:12px"></div>${skeletons(2)}</div>`;
    let rec; try { rec=await api("/api/recordings/"+id); } catch(e){ return authOrError(e,()=>showDetail(id)); }
    current=rec; activeTab="overview"; showOriginal=false; chatHist=[];

    if (!["done","error"].includes(rec.status)){
      app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/')">← Home</span>
        <div class="empty"><span class="spin-lg"></span>${h(rec.status)}…
        <div class="hint">transcribe → translate → analyze</div></div></div>`;
      pollTimer=setTimeout(()=>showDetail(id),3500); return;
    }
    if (rec.status==="error"){
      app.innerHTML=`<div class="view"><span class="backlink" onclick="App.go('/')">← Home</span>
        <div class="panel"><h2>Error</h2><p style="color:var(--ten);white-space:pre-wrap;font-size:13px">${h(rec.error)}</p>
        <button class="btn" onclick="App.reanalyze('${id}')">Retry</button></div></div>`; return;
    }

    const a=rec.analysis||{}; const m=mood(a);
    app.innerHTML=`<div class="view" style="--mc:${m.c}">
      <span class="backlink" onclick="App.go('/')">← Home</span>
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
    sheet.querySelectorAll("[data-proof]").forEach(el=>el.onclick=(e)=>{ e.stopPropagation(); proof(parseFloat(el.dataset.proof), el.dataset.qt); });
    requestAnimationFrame(()=>sheet.classList.add("open"));
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
    app.innerHTML=`<div class="view"><div class="hero"><h1>Settings</h1></div>${skeletons(2)}</div>`;
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
      <div class="hero"><h1>Settings</h1></div>
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
        <p style="color:var(--muted);font-size:14px;margin:0;line-height:1.5">Lucid turns your Plaud recordings into clean, sorted notes — summaries, people, ideas, and action items — transcribed and analyzed on your own machine. Version 1.0.</p></div>
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
  async function del(id){ if(!confirm("Delete this recording?"))return;
    try{ await api(`/api/recordings/${id}`,{method:"DELETE"}); cache=[]; go("/"); }catch(e){toast("Failed");} }

  function authOrError(e,retry){
    if (String(e.message)==="auth"){ return showLogin(retry); }
    app.innerHTML=`<div class="view"><div class="empty">⚠ ${h(e.message)}<br><br>
      <button class="btn" id="rt">Retry</button></div></div>`; document.getElementById("rt").onclick=retry;
  }

  function showLogin(retry){
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
        const d=await r.json(); token=d.token||""; localStorage.setItem("lucid_token",token);
        (retry||route)();
      }catch(e){ err.textContent="Network error — try again."; btn.disabled=false; } };
    btn.onclick=submit; pw.onkeydown=e=>{ if(e.key==="Enter") submit(); };
    setTimeout(()=>{ try{pw.focus();}catch(_){}} ,120);
  }

  route();
  return { go, reanalyze, del, rename, chat };
})();
