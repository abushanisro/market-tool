/* Manufacturing News: live terminal-style feed, tagged to the memory/ taxonomy categories, countries and locations */
"use strict";

const NEWS_SRC_CLASS = { GDELT: "s-gdelt", HN: "s-hn", "MFG DIVE": "s-mfg", "SC DIVE": "s-scd", NIST: "s-nist" };
const prettyFamily = (f) => String(f || "other").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

route("news", async (view, { params }) => {
  const st = { cat: params.cat || "", country: params.country || "", loc: params.loc || "", source: params.source || "", q: params.q || "", page: 1, size: 50, paused: false, maxId: 0, pending: 0 };
  const fx = await api("/api/feed/facets?days=365");
  const famOrder = [...new Set(fx.categories.map((c) => c.family))].filter((f) => f !== "reference");
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Intelligence</div><h1>Manufacturing <em>News</em></h1>
    <p>Headlines collected continuously and tagged to your <code>memory/</code> categories, countries and manufacturing hubs. Only manufacturing news is kept; everything stays in pages.</p></div>
    <div class="row"><span class="tag teal" id="nw-stat"></span></div></div>
    <div class="split"><div class="card"><div class="filters" style="padding:var(--pad-sm) var(--pad) 8px;margin:0"><input class="input" id="nw-q" placeholder="Search headlines" value="${esc(st.q)}" style="width:100%"></div>
      <div class="list" id="nw-rail"></div></div>
      <div><div class="card"><div class="card-h"><div class="row" style="gap:10px"><span class="live on" id="nw-live"><i></i>Live</span><h3 id="nw-count">News</h3></div>
        <div class="row" style="gap:8px"><button class="btn sm" id="nw-pause">Pause</button><select class="input" id="nw-size" style="width:auto;height:30px"><option>25</option><option selected>50</option><option>100</option></select></div></div>
        <div class="row" id="nw-active" style="padding:8px var(--pad) 0;min-height:8px"></div>
        <div id="nw-newbar"></div><div class="term" id="nw-term"></div><div id="nw-pager"></div></div></div></div>`;

  const label = (c) => (fx.categories.find((x) => x.slug === c) || { name: c }).name;
  const cname = (i) => (fx.countries.find((x) => x.iso3 === i) || { name: i }).name;
  const drawRail = () => {
    const sec = (title) => `<div class="list-group">${esc(title)}</div>`;
    const row = (kind, key, text, n) => `<div class="list-item compact ${st[kind] === key ? "sel" : ""}" data-k="${kind}" data-v="${esc(key)}"><div class="row between"><span class="t1" style="font-weight:500">${esc(text)}</span><span class="count">${n}</span></div></div>`;
    $("#nw-rail").innerHTML = `<div class="list-item compact ${!st.cat && !st.country && !st.loc && !st.source ? "sel" : ""}" data-k="all"><div class="row between"><span class="t1">All manufacturing news</span><span class="count">${nf.format(fx.headlines)}</span></div></div>
      ${sec("Categories (from memory/)")}${famOrder.map((f) => `<div class="small muted" style="padding:6px var(--pad) 0">${esc(prettyFamily(f))}</div>${fx.categories.filter((c) => c.family === f).map((c) => row("cat", c.slug, c.name, c.count)).join("")}`).join("")}
      ${sec("Countries")}${fx.countries.map((c) => row("country", c.iso3, c.name, c.count)).join("")}
      ${fx.locations.length ? sec("Locations") + fx.locations.map((l) => row("loc", l.name, l.name, l.count)).join("") : ""}
      ${sec("Sources")}${fx.sources.map((s) => row("source", s.slug, s.label, s.count)).join("")}`;
  };
  const chips = () => {
    const c = [["cat", st.cat && label(st.cat)], ["country", st.country && cname(st.country)], ["loc", st.loc], ["source", st.source], ["q", st.q && `"${st.q}"`]].filter(([, v]) => v);
    $("#nw-active").innerHTML = c.map(([k, v]) => `<span class="tag teal">${esc(v)} <a href="#" data-clear="${k}" title="Remove filter">×</a></span>`).join("");
  };
  const lineHtml = (it, flash) => {
    const t = new Date(it.published_at);
    const tags = [...it.categories.map((c) => `<span class="t-tag" data-k="cat" data-v="${esc(c)}">${esc(label(c))}</span>`),
      ...it.countries.map((c) => `<span class="t-tag c" data-k="country" data-v="${esc(c)}">${esc(c)}</span>`), ...it.locations.map((l) => `<span class="t-tag l" data-k="loc" data-v="${esc(l)}">${esc(l)}</span>`)].join("");
    const meta = [it.domain, it.points != null ? `▲${it.points}` : "", it.comments != null ? `${it.comments} comments` : ""].filter(Boolean).join(" · ");
    return `<div class="tl ${flash ? "flash" : ""}" data-id="${it.id}"><span class="t-time">${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      <span class="t-src ${NEWS_SRC_CLASS[it.source_label] || ""}">${esc(it.source_label)}</span>
      <div><a class="t-title" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>${it.discussion ? ` <a class="t-meta" href="${esc(it.discussion)}" target="_blank" rel="noopener noreferrer">discuss</a>` : ""}
        <div class="t-meta">${esc(meta)}${meta && tags ? " · " : ""}${tags}</div></div></div>`;
  };
  const group = (items) => { let day = "", out = ""; items.forEach((it) => { const d = new Date(it.published_at).toDateString();
    if (d !== day) { day = d; out += `<div class="day">${new Date(it.published_at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" })}</div>`; } out += lineHtml(it); }); return out; };
  const qs = (extra = {}) => new URLSearchParams({ ...(st.cat ? { cat: st.cat } : {}), ...(st.country ? { country: st.country } : {}), ...(st.loc ? { loc: st.loc } : {}), ...(st.source ? { source: st.source } : {}), ...(st.q ? { q: st.q } : {}), ...extra });
  const load = async () => {
    const d = await api(`/api/feed?${qs({ page: st.page, size: st.size })}`);
    st.maxId = Math.max(st.maxId, d.max_id); st.pending = 0; st.total = d.total;
    $("#nw-newbar").innerHTML = "";
    $("#nw-count").textContent = `${nf.format(d.total)} headline${d.total === 1 ? "" : "s"}`;
    $("#nw-term").innerHTML = d.items.length ? group(d.items) : `<div style="padding:28px 16px;color:#8b949e">No headlines match yet. New ones appear here as they are collected.</div>`;
    $("#nw-pager").innerHTML = d.pages > 1 ? pager(d.total, (st.page - 1) * st.size, st.size, (off) => { st.page = Math.floor(off / st.size) + 1; load(); window.scrollTo(0, 0); }) : "";
    chips(); drawRail();
  };
  $("#nw-stat").textContent = `${nf.format(fx.headlines)} stored · ${nf.format(fx.last_hour)} in the last hour`;
  const pick = (k, v) => { if (k === "all") { st.cat = st.country = st.loc = st.source = ""; } else { st[k] = st[k] === v ? "" : v; } st.page = 1; load(); };
  $("#nw-rail").onclick = (e) => { const r = e.target.closest("[data-k]"); if (r) pick(r.dataset.k, r.dataset.v); };
  $("#nw-term").onclick = (e) => { const t = e.target.closest(".t-tag"); if (t) { e.preventDefault(); pick(t.dataset.k, t.dataset.v); } };
  $("#nw-active").onclick = (e) => { const c = e.target.closest("[data-clear]"); if (c) { e.preventDefault(); st[c.dataset.clear] = ""; if (c.dataset.clear === "q") $("#nw-q").value = ""; st.page = 1; load(); } };
  let timer; $("#nw-q").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { st.q = e.target.value.trim(); st.page = 1; load(); }, 250); };
  $("#nw-size").onchange = (e) => { st.size = +e.target.value; st.page = 1; load(); };
  $("#nw-pause").onclick = () => { st.paused = !st.paused; $("#nw-pause").textContent = st.paused ? "Resume" : "Pause"; $("#nw-live").classList.toggle("on", !st.paused); $("#nw-live").lastChild.textContent = st.paused ? "Paused" : "Live"; if (!st.paused) onNew(); };

  /* live: the server announces new documents; fetch only what is newer than the top of the list and prepend it */
  const onNew = async () => {
    if (st.paused || !st.maxId) return;
    const d = await api(`/api/feed?${qs({ since_id: st.maxId })}`);
    if (!d.items.length) return;
    st.maxId = Math.max(st.maxId, d.max_id, ...d.items.map((i) => i.id)); st.total += d.items.length;
    $("#nw-count").textContent = `${nf.format(st.total)} headlines`;
    if (st.page === 1 && window.scrollY < 400) {
      const term = $("#nw-term"); const first = term.querySelector(".day");
      const html = d.items.sort((a, b) => new Date(b.published_at) - new Date(a.published_at)).map((i) => lineHtml(i, true)).join("");
      first ? first.insertAdjacentHTML("afterend", html) : (term.innerHTML = html + term.innerHTML);
    } else {
      st.pending += d.items.length;
      $("#nw-newbar").innerHTML = `<button class="btn sm primary" id="nw-load" style="margin:8px var(--pad)">${st.pending} new headline${st.pending === 1 ? "" : "s"}: show</button>`;
      $("#nw-load").onclick = () => { st.page = 1; load(); window.scrollTo(0, 0); };
    }
  };
  liveHooks.add(onNew);
  await load();
});
