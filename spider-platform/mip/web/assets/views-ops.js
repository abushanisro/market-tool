/* Screens: Overview, Spider Manager, Source Explorer, Settings */
"use strict";

/* =============================== OVERVIEW =============================== */
route("overview", async (view) => {
  const [o, alerts, mf] = await Promise.all([api("/api/overview"), api("/api/alerts").catch(() => ({ events: [] })), api("/api/market/freshness")]);
  const fr = o.sources.freshness, origin = o.records.by_origin;
  const totalObs = o.records.observations || 1;
  const replayOnly = origin.crawled_replayed > 0 && origin.crawled_live === 0;
  view.innerHTML = `
  <div class="page-head"><div><div class="eyebrow">Manufacturing intelligence</div>
    <h1>Data spider <em>overview</em></h1>
    <p>Every figure on this page is computed from the platform database at ${esc(dt(o.generated_at))}. Internal taxonomy data and crawled data are counted separately.</p></div>
    <div class="row"><a class="btn" href="#/spiders">${icon("spider")} Spider Manager</a><a class="btn primary" href="#/sources">${icon("sources")} Explore sources</a></div></div>
  ${replayOnly ? banner(`<b>Crawled records in this database came from recorded responses (replay mode)</b>: real payloads captured 2026-10-27 and replayed offline. They are tagged <span class="tag teal">replayed</span>. Run the worker with network access to collect live data.`, "teal") : ""}
  <div class="grid g-6">
    ${kpi("Sources", o.sources.total, `<b>${o.sources.active}</b> active · ${o.sources.by_verification.verified || 0} verified`)}
    ${kpi("Active spiders", o.spiders.running, `<b>${o.spiders.queued}</b> queued`, o.spiders.running ? '<span class="pill info">live</span>' : "")}
    ${kpi("URLs discovered", o.urls.discovered, `<b>${nf.format(o.urls.fetched)}</b> fetched · ${o.urls.failed} failed`)}
    ${kpi("Records extracted", o.records.observations + o.records.documents, `<b>${nf.format(o.records.by_status.verified || 0)}</b> verified observations`)}
    ${kpi("Data freshness", `${fr.fresh || 0}<span class="muted" style="font-size:16px"> / ${(fr.fresh || 0) + (fr.stale || 0) + (fr.never || 0) + (fr.failing || 0)}</span>`, `<b>${fr.stale || 0}</b> stale · ${fr.never || 0} never run`, "", true)}
    ${kpi("Failed jobs (24 h)", o.spiders.failed_24h, `<b>${o.spiders.failed_total}</b> failed all-time`, o.spiders.failed_24h ? '<span class="pill err">attention</span>' : "")}
  </div>
  <div class="grid g-3" style="margin-top:16px">
    <div class="card span-2"><div class="card-h"><h3>Material–market coverage</h3><span class="sub">materials with ≥1 normalized USD/kg price, by family</span></div>
      <div class="card-b flush">${table([
        { label: "Family", html: (r) => `<b>${esc(r.family.replace(/_/g, " "))}</b>` },
        { label: "Materials", num: true, html: (r) => num(r.materials) },
        { label: "Priced", num: true, html: (r) => num(r.priced) },
        { label: "Coverage", html: (r) => `<div class="row" style="flex-wrap:nowrap"><div class="bar" style="flex:1"><span style="width:${r.materials ? (r.priced / r.materials * 100) : 0}%"></span></div><span class="small muted nowrap">${r.materials ? Math.round(r.priced / r.materials * 100) : 0}%</span></div>` },
      ], o.coverage.slice(0, 12), { onRow: (r) => go(`materials?family=${r.family}`) })}</div></div>
    <div class="stack">
      <div class="card"><div class="card-h"><h3>Record origin</h3><span class="sub">${nf.format(o.records.observations)} observations</span></div>
        <div class="card-b"><div class="stackbar">${[["internal_taxonomy", "#475569"], ["crawled_live", "#0d9e8a"], ["crawled_replayed", "#2dd4bf"]].map(([k, c]) => `<span style="width:${origin[k] / totalObs * 100}%;background:${c}" title="${k}: ${origin[k]}"></span>`).join("")}</div>
        <div class="legend"><span><i style="background:#475569"></i>Internal taxonomy ${nf.format(origin.internal_taxonomy)}</span><span><i style="background:#0d9e8a"></i>Crawled live ${nf.format(origin.crawled_live)}</span><span><i style="background:#2dd4bf"></i>Replayed ${nf.format(origin.crawled_replayed)}</span></div>
        <hr class="sep"><div class="kv"><dt>Verified</dt><dd>${nf.format(o.records.by_status.verified || 0)}</dd><dt>Unverified</dt><dd>${nf.format(o.records.by_status.unverified || 0)}</dd><dt>Quarantined</dt><dd>${nf.format(o.records.by_status.quarantined || 0)} <a class="small" href="#/data?tab=issues">review</a></dd><dt>Documents</dt><dd>${nf.format(o.records.documents)}</dd><dt>Standards</dt><dd>${nf.format(o.records.standards)}</dd></div></div></div>
      <div class="card"><div class="card-h"><h3>Taxonomy foundation</h3><a class="small" href="#/taxonomy">Open</a></div>
        <div class="card-b"><div class="kv"><dt>Categories</dt><dd>${o.taxonomy.categories}</dd><dt>Datasets (files/sheets)</dt><dd>${nf.format(o.taxonomy.datasets)}</dd><dt>Source rows preserved</dt><dd>${nf.format(o.taxonomy.rows)}</dd><dt>Materials</dt><dd>${nf.format(o.taxonomy.materials)}</dd><dt>Machines / work centers</dt><dd>${nf.format(o.taxonomy.machines)}</dd></div></div></div>
    </div>
  </div>
  <div class="grid" style="margin-top:16px;grid-template-columns:minmax(0,3fr) minmax(0,2fr)">
    <div class="card"><div class="card-h"><h3>Recent crawl jobs</h3><a class="small" href="#/spiders">All jobs</a></div><div class="card-b flush">${jobsTable(o.recent_jobs)}</div></div>
    <div class="card"><div class="card-h"><h3>Market source freshness</h3><span class="sub">last successful refresh vs. SLA</span></div><div class="card-b flush">${table([
      { label: "Source", html: (r) => `<a href="#/spiders/${r.source}">${esc(r.name)}</a>` },
      { label: "Freshness", html: (r) => pill(r.freshness) }, { label: "Latest period", html: (r) => esc(r.latest_period || "-") },
      { label: "Last success", html: (r) => `<span class="small">${esc(ago(r.last_success_at))}</span>` }], mf.items.filter((x) => x.freshness !== "n/a"))}</div></div>
  </div>
  <div class="card" style="margin-top:16px"><div class="card-h"><h3>Procurement & price alerts</h3><span class="sub">${alerts.events.length} open</span></div>
    <div class="card-b flush">${alerts.events.length ? table([{ label: "Severity", html: (r) => pill(r.severity) }, { label: "Alert", html: (r) => esc(r.message) }, { label: "Rule", html: (r) => `<span class="small muted">${esc(r.rule)}</span>` }, { label: "When", html: (r) => `<span class="small nowrap">${esc(ago(r.triggered_at))}</span>` }], alerts.events.slice(0, 8))
      : emptyState("No open alerts", "Rules evaluate every 15 minutes in the worker (FX moves, commodity price changes, stale or failing sources).")}</div></div>`;
});

function kpi(label, value, foot, badge = "", raw = false) {
  return `<div class="card kpi"><div class="label">${esc(label)}${badge}</div><div class="value">${raw ? value : nf.format(value ?? 0)}</div><div class="foot">${foot}</div></div>`;
}
function jobsTable(jobs, onRow) {
  return table([
    { label: "Job", html: (j) => `<span class="mono">#${j.id}</span>` },
    { label: "Source", html: (j) => `<b>${esc(j.source)}</b><div class="sub">${esc(j.trigger)}</div>` },
    { label: "Status", html: (j) => pill(j.status) + (j.cancel_requested && j.status === "running" ? ' <span class="small muted">cancelling…</span>' : "") },
    { label: "Fetched", num: true, html: (j) => num(j.stats.pages_fetched) },
    { label: "Stored", num: true, html: (j) => num((j.stats.items_stored || 0) + (j.stats.items_revised || 0)) },
    { label: "Failed", num: true, html: (j) => num((j.stats.requests_failed || 0) + (j.stats.processing_errors || 0)) },
    { label: "Started", html: (j) => `<span class="small nowrap">${esc(ago(j.started_at || j.created_at))}</span>` },
  ], jobs, { onRow: onRow || ((j) => showJob(j.id)), empty: "No jobs yet" });
}

/* =============================== SPIDER MANAGER =============================== */
async function showJob(id) {
  let lastLog = 0;
  const body = openDrawer(`<div class="eyebrow">Crawl job</div><h2>#${id}</h2>`, skeleton(5));
  const draw = async () => {
    const j = await api(`/api/jobs/${id}`);
    const logs = await api(`/api/jobs/${id}/logs?after=${lastLog}`);
    if (!body.querySelector("#job-log")) {
      body.innerHTML = `<div id="job-head"></div><h3 style="font-size:14px;margin:18px 0 8px">Log</h3><pre class="log" id="job-log"></pre>`;
    }
    const s = j.stats || {};
    body.querySelector("#job-head").innerHTML = `
      <div class="row between"><div class="row">${pill(j.status)} <span class="small muted">${esc(j.source_name)} · ${esc(j.trigger)}</span></div>
      ${["running", "queued", "starting"].includes(j.status) ? `<button class="btn sm danger" id="job-cancel">${icon("stop")} Stop job</button>` : ""}</div>
      ${["running", "starting"].includes(j.status) ? '<div class="progress" style="margin-top:12px"><span></span></div>' : ""}
      <div class="grid g-3" style="margin-top:14px">${[["URLs discovered", s.urls_discovered], ["Queued", s.urls_queued], ["Pages fetched", s.pages_fetched], ["Unchanged / 304", (s.pages_unchanged || 0) + (s.not_modified || 0)],
        ["Items extracted", s.items_extracted], ["Stored", (s.items_stored || 0) + (s.items_revised || 0)], ["Duplicates skipped", s.items_duplicate], ["Quarantined / invalid", (s.items_quarantined || 0) + (s.items_invalid || 0)],
        ["Failed requests", s.requests_failed], ["Processing errors", s.processing_errors], ["Retries", s.retries], ["Out of scope", s.urls_out_of_scope]].map(([k, v]) =>
        `<div class="card kpi" style="padding:10px 12px"><div class="label">${k}</div><div class="value" style="font-size:20px">${num(v || 0)}</div></div>`).join("")}</div>
      <dl class="kv" style="margin-top:14px"><dt>Created</dt><dd>${dt(j.created_at)}</dd><dt>Started</dt><dd>${dt(j.started_at)}</dd><dt>Finished</dt><dd>${dt(j.finished_at)}</dd>
      <dt>Duration</dt><dd>${j.duration_s != null ? j.duration_s + " s" : "-"}</dd><dt>Params</dt><dd><code>${esc(JSON.stringify(j.params))}</code></dd>${j.error ? `<dt>Error</dt><dd style="color:var(--err)">${esc(j.error)}</dd>` : ""}${s.note ? `<dt>Note</dt><dd>${esc(s.note)}</dd>` : ""}</dl>`;
    const pre = body.querySelector("#job-log");
    if (logs.items.length && pre.dataset.empty) { pre.textContent = ""; delete pre.dataset.empty; }
    logs.items.forEach((l) => { lastLog = l.id; pre.insertAdjacentHTML("beforeend", `<span class="${l.level}">${esc(l.ts.slice(11, 19))} ${esc(l.level.toUpperCase().padEnd(7))} ${esc(l.message)}</span>\n`); });
    if (!pre.textContent) { pre.textContent = "No log lines yet."; pre.dataset.empty = "1"; }
    pre.scrollTop = pre.scrollHeight;
    body.querySelector("#job-cancel")?.addEventListener("click", async () => {
      try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); toast("Cancellation requested"); draw(); } catch (e) { toast(e.message, true); }
    });
    return j;
  };
  const j = await draw();
  if (["running", "queued", "starting"].includes(j.status)) {
    const t = setInterval(async () => { const jj = await draw().catch(() => null); if (!jj || !["running", "queued", "starting"].includes(jj.status)) clearInterval(t); }, 2000);
    closeDrawer.onClose = () => clearInterval(t);
  }
}

route("spiders", async (view, { sub }) => {
  const data = await api("/api/sources");
  const crawlable = data.items.filter((s) => s.source_type !== "internal");
  let selected = sub[0] || (crawlable.find((s) => s.status === "active") || crawlable[0])?.slug;
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Data operations</div><h1>Spider <em>Manager</em></h1>
    <p>Run, schedule and tune crawls. Jobs run in isolated worker processes; progress, logs and URL checkpoints update live.</p></div>
    <div class="row"><button class="btn primary" id="add-source">${icon("plus")} Register source</button></div></div>
    <div class="split"><div class="card"><div class="card-h"><h3>Sources</h3><span class="sub">${crawlable.length}</span></div>
      <div class="filters" style="padding:10px 12px 0;margin:0"><input class="input" id="sp-filter" placeholder="Filter…" style="flex:1"></div>
      <div class="list" id="sp-list"></div></div>
    <div id="sp-detail"></div></div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>All crawl jobs</h3><span class="sub">auto-refreshes every 3 s</span></div><div class="card-b flush" id="all-jobs">${skeleton(4)}</div></div>`;
  const drawList = () => {
    const f = $("#sp-filter").value.toLowerCase();
    $("#sp-list").innerHTML = crawlable.filter((s) => !f || (s.name + s.slug).toLowerCase().includes(f)).map((s) => `
      <div class="list-item ${s.slug === selected ? "sel" : ""}" data-slug="${s.slug}"><div class="row between"><div class="t1">${esc(s.name)}</div>${pill(s.status)}</div>
      <div class="t2 row">${pill(s.verification_status)} ${pill(s.freshness)} <span>${esc(s.source_type.toUpperCase())}</span> · <span>${nf.format(s.records)} records</span></div></div>`).join("");
  };
  drawList();
  $("#sp-filter").addEventListener("input", drawList);
  $("#sp-list").addEventListener("click", (e) => { const it = e.target.closest("[data-slug]"); if (!it) return; selected = it.dataset.slug; history.replaceState(null, "", `#/spiders/${selected}`); drawList(); drawDetail(); });
  $("#add-source").addEventListener("click", () => sourceForm());
  const drawDetail = async () => {
    const box = $("#sp-detail");
    if (!selected) { box.innerHTML = `<div class="card">${emptyState("No sources registered", "Seed the registry or register a source.")}</div>`; return; }
    box.innerHTML = `<div class="card"><div class="card-b">${skeleton(6)}</div></div>`;
    let s;
    try { s = await api(`/api/sources/${selected}`); } catch (e) { box.innerHTML = `<div class="card">${errorState(e)}</div>`; return; }
    const runnable = ["verified", "url_verified", "documented", "key_required"].includes(s.verification_status) && s.status !== "disabled" && s.extraction?.parser && s.extraction.parser !== "none";
    const active = s.jobs.find((j) => ["queued", "running", "starting"].includes(j.status));
    box.innerHTML = `
      <div class="card"><div class="card-h"><div><h3>${esc(s.name)}</h3><div class="sub"><a href="${esc(s.base_url)}" target="_blank" rel="noopener">${esc(s.base_url)}</a></div></div>
        <div class="row">${pill(s.status)} ${pill(s.verification_status)} ${pill(s.freshness)}</div></div>
        <div class="card-b">
          ${!runnable ? banner(s.verification_status === "blocked_by_terms" ? `<b>Crawling is blocked.</b> ${esc(s.license || "")}` : s.verification_status === "planned" ? "<b>Planned integration.</b> URL, format and terms must be verified before this source can run." : "This source has no runnable parser configured.", "warn") : ""}
          ${s.credential_present === false ? banner(`Credential <code>${esc(s.auth_env_var)}</code> is not set in the service environment.`, "warn") : ""}
          <div class="row" style="margin-bottom:14px">
            <button class="btn primary" id="run-now" ${!runnable || active ? "disabled" : ""}>${icon("play")} Run now</button>
            <button class="btn" id="run-force" ${!runnable || active ? "disabled" : ""}>${icon("refresh")} Force refresh</button>
            ${s.backfill?.url_template ? `<button class="btn" id="run-backfill" ${!runnable || active ? "disabled" : ""}>Historical backfill…</button>` : ""}
            ${active ? `<button class="btn danger" id="stop-job">${icon("stop")} Stop #${active.id}</button>` : ""}
            <span class="spacer" style="flex:1"></span>
            ${s.status === "active" ? `<button class="btn" id="pause">Pause schedule</button>` : s.status === "paused" && runnable ? `<button class="btn" id="activate">Activate schedule</button>` : ""}
            <button class="btn" id="edit">Edit crawl rules</button></div>
          ${active ? `<div class="banner teal">${icon("info")}<div>Job <a href="#" data-job="${active.id}">#${active.id}</a> is ${esc(active.status)}: ${num(active.stats.pages_fetched || 0)} pages, ${num(active.stats.items_extracted || 0)} items so far.</div></div>` : ""}
          <div class="grid g-2">
            <dl class="kv"><dt>Type</dt><dd>${esc(s.source_type)} · parser <code>${esc(s.extraction?.parser || "none")}</code></dd>
              <dt>Schedule</dt><dd>${s.schedule_cron ? `cron <code>${esc(s.schedule_cron)}</code> (UTC)` : s.schedule_interval_minutes ? `every ${esc(intervalLabel(s.schedule_interval_minutes))}` : "manual only"}</dd>
              <dt>Concurrency / delay</dt><dd>${s.concurrency} · ${s.download_delay}s (AutoThrottle on)</dd>
              <dt>Budget / depth</dt><dd>${s.max_pages} pages · depth ${s.max_depth}</dd>
              <dt>robots.txt</dt><dd>${s.respect_robots ? "obeyed" : "not applied (documented API)"}</dd>
              <dt>Browser rendering</dt><dd>${s.render_js ? "scrapy-playwright (Chromium)" : "off: plain HTTP"}</dd>
              <dt>Allowed domains</dt><dd>${(s.allowed_domains || []).map((d) => `<span class="tag">${esc(d)}</span>`).join("") || "-"}</dd>
              <dt>Allow / deny</dt><dd>${(s.allow_patterns || []).map((d) => `<code class="tag">${esc(d)}</code>`).join("") || "-"} ${(s.deny_patterns || []).map((d) => `<code class="tag" style="color:var(--err)">${esc(d)}</code>`).join("")}</dd></dl>
            <dl class="kv"><dt>Last run</dt><dd>${dt(s.last_run_at)}</dd><dt>Last success</dt><dd>${dt(s.last_success_at)}</dd>
              <dt>Records (last / total)</dt><dd>${num(s.last_records)} / ${num(s.records)}</dd>
              <dt>Failures</dt><dd>${s.failure_count} total · ${s.consecutive_failures} consecutive</dd>
              ${s.last_error ? `<dt>Last error</dt><dd style="color:var(--err)">${esc(s.last_error)}</dd>` : ""}
              <dt>URL checkpoints</dt><dd>${Object.entries(s.url_status || {}).map(([k, v]) => `${pill(k === "fetched" || k === "unchanged" ? "fresh" : k === "failed" ? "failed" : "plain", `${k} ${v}`)}`).join(" ") || "-"}</dd>
              <dt>Start URLs</dt><dd>${(s.start_urls || []).map((u) => `<div class="truncate mono small" title="${esc(u)}">${esc(u)}</div>`).join("") || "-"}</dd></dl></div></div></div>
      <div class="card" style="margin-top:16px"><div class="card-h"><h3>Jobs</h3></div><div class="card-b flush">${jobsTable(s.jobs)}</div></div>
      <div class="card" style="margin-top:16px"><div class="card-h"><h3>Discovered URLs</h3><span class="sub">checkpoints used for incremental crawling (ETag / Last-Modified / content hash)</span></div><div class="card-b flush" id="urls">${skeleton(3)}</div></div>`;
    const startJob = async (params) => { try { const j = await api("/api/jobs", { method: "POST", body: { source: s.slug, params } }); toast(`Job #${j.id} queued`); showJob(j.id); drawDetail(); } catch (e) { toast(e.message, true); } };
    $("#run-now")?.addEventListener("click", () => startJob({}));
    $("#run-force")?.addEventListener("click", () => startJob({ force_refresh: true }));
    $("#run-backfill")?.addEventListener("click", () => {
      const m = openModal(`<div class="drawer-h"><div><div class="eyebrow">Historical backfill</div><h2>${esc(s.name)}</h2></div><button class="btn ghost" data-close>✕</button></div>
        <div class="drawer-b"><p class="small muted">Windows of ${s.backfill.chunk_days || 365} days are requested with <code>${esc(s.backfill.url_template)}</code>.</p>
        <div class="form-grid"><div class="field"><label>Start date</label><input class="input" type="date" id="bf-start" value="${new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10)}"></div>
        <div class="field"><label>End date</label><input class="input" type="date" id="bf-end" value="${new Date().toISOString().slice(0, 10)}"></div></div>
        <div class="row" style="margin-top:16px"><button class="btn primary" id="bf-go">Queue backfill</button></div></div>`);
      m.querySelector("[data-close]").onclick = closeModal;
      m.querySelector("#bf-go").onclick = () => { closeModal(); startJob({ backfill: true, start_date: $("#bf-start").value, end_date: $("#bf-end").value }); };
    });
    $("#stop-job")?.addEventListener("click", async () => { try { await api(`/api/jobs/${active.id}/cancel`, { method: "POST" }); toast("Cancellation requested"); drawDetail(); } catch (e) { toast(e.message, true); } });
    const setStatus = async (st) => { try { await api(`/api/sources/${s.slug}`, { method: "PATCH", body: { status: st } }); toast(`Source ${st}`); drawDetail(); } catch (e) { toast(e.message, true); } };
    $("#pause")?.addEventListener("click", () => setStatus("paused"));
    $("#activate")?.addEventListener("click", () => setStatus("active"));
    $("#edit").addEventListener("click", () => sourceForm(s, drawDetail));
    box.querySelector("[data-job]")?.addEventListener("click", (e) => { e.preventDefault(); showJob(+e.target.dataset.job); });
    const urls = await api(`/api/sources/${s.slug}/urls?limit=100`);
    $("#urls").innerHTML = table([
      { label: "URL", html: (u) => `<div class="truncate mono small" title="${esc(u.url)}">${esc(u.url)}</div>${u.error ? `<div class="small" style="color:var(--err)">${esc(u.error)}</div>` : ""}` },
      { label: "Status", html: (u) => pill(u.status === "fetched" || u.status === "unchanged" ? "succeeded" : u.status === "failed" ? "failed" : "plain", u.status) },
      { label: "HTTP", num: true, html: (u) => u.http_status ?? "-" }, { label: "Items", num: true, html: (u) => num(u.items_extracted) },
      { label: "Fetches", num: true, html: (u) => num(u.fetch_count) }, { label: "Hash", html: (u) => `<code class="small">${esc(u.content_hash || "-")}</code>` },
      { label: "Last fetched", html: (u) => `<span class="small nowrap">${esc(ago(u.last_fetched))}</span>` }], urls.items, { empty: "No URLs discovered yet: run the source." });
  };
  await drawDetail();
  const drawJobs = async () => { try { const j = await api("/api/jobs?limit=30"); $("#all-jobs").innerHTML = jobsTable(j.items); } catch (e) { $("#all-jobs").innerHTML = errorState(e); } };
  drawJobs();
  every(3000, drawJobs);
});
const intervalLabel = (m) => m % 1440 === 0 ? `${m / 1440} day(s)` : m % 60 === 0 ? `${m / 60} h` : `${m} min`;

async function sourceForm(existing, after) {
  const parsers = (await api("/api/parsers")).parsers;
  const s = existing || { slug: "", name: "", base_url: "https://", website_category: "government", source_type: "api", data_kinds: ["price"], categories: ["*"], countries: [],
    access_method: "HTTPS GET", license: "", commercial_use: "unknown", verification_status: "planned", status: "paused", concurrency: 1, download_delay: 1.0, max_pages: 50, max_depth: 1,
    respect_robots: true, render_js: false, allowed_domains: [], allow_patterns: [], deny_patterns: [], start_urls: [], sitemap_urls: [], pagination: {}, extraction: { parser: "html_table" }, validation: {}, freshness_sla_hours: 72 };
  const L = (a) => (a || []).join("\n");
  const m = openModal(`<div class="drawer-h"><div><div class="eyebrow">${existing ? "Edit crawl rules" : "Register a new source"}</div><h2>${esc(s.name || "New source")}</h2></div><button class="btn ghost" data-close>✕</button></div>
  <div class="drawer-b"><form id="src-form" class="stack">
    ${!existing ? `<div class="form-grid"><div class="field"><label>Slug</label><input class="input" name="slug" required pattern="[a-z0-9][a-z0-9\\-]{1,78}" value="${esc(s.slug)}"><span class="hint">lowercase-with-dashes</span></div>
      <div class="field" style="grid-column:span 2"><label>Name</label><input class="input" name="name" required value="${esc(s.name)}"></div>
      <div class="field" style="grid-column:span 2"><label>Base URL</label><input class="input" name="base_url" required value="${esc(s.base_url)}"></div>
      <div class="field"><label>Source type</label><select class="input" name="source_type">${["api", "html", "sitemap", "rss", "csv", "xlsx", "pdf"].map((t) => `<option ${t === s.source_type ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Website category</label><input class="input" name="website_category" value="${esc(s.website_category)}"></div>
      <div class="field"><label>Verification</label><select class="input" name="verification_status">${["planned", "documented", "url_verified", "verified", "key_required", "blocked_by_terms"].map((t) => `<option ${t === s.verification_status ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Licence</label><input class="input" name="license" value="${esc(s.license || "")}"></div>
      <div class="field"><label>Commercial use</label><select class="input" name="commercial_use">${["unknown", "yes", "conditional", "no"].map((t) => `<option ${t === s.commercial_use ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Access method</label><input class="input" name="access_method" value="${esc(s.access_method)}"></div>
      <div class="field"><label>Data kinds (comma)</label><input class="input" name="data_kinds" value="${esc((s.data_kinds || []).join(", "))}"></div>
      <div class="field"><label>Categories (comma, * = all)</label><input class="input" name="categories" value="${esc((s.categories || []).join(", "))}"></div>
      <div class="field"><label>Countries (ISO3, comma)</label><input class="input" name="countries" value="${esc((s.countries || []).join(", "))}"></div></div><hr class="sep">` : ""}
    <div class="form-grid">
      <div class="field"><label>Schedule interval (min)</label><input class="input" type="number" min="15" name="schedule_interval_minutes" value="${s.schedule_interval_minutes ?? ""}"></div>
      <div class="field"><label>…or cron (UTC)</label><input class="input" name="schedule_cron" placeholder="20 15 * * 1-5" value="${esc(s.schedule_cron || "")}"></div>
      <div class="field"><label>Concurrency</label><input class="input" type="number" min="1" max="16" name="concurrency" value="${s.concurrency}"></div>
      <div class="field"><label>Download delay (s)</label><input class="input" type="number" step="0.1" min="0" name="download_delay" value="${s.download_delay}"></div>
      <div class="field"><label>Max pages / job</label><input class="input" type="number" min="1" name="max_pages" value="${s.max_pages}"></div>
      <div class="field"><label>Max depth</label><input class="input" type="number" min="0" max="10" name="max_depth" value="${s.max_depth}"></div>
      <div class="field"><label>Freshness SLA (h)</label><input class="input" type="number" min="1" name="freshness_sla_hours" value="${s.freshness_sla_hours}"></div>
      <div class="field"><label>Parser</label><select class="input" name="parser">${["none", ...parsers].map((p) => `<option ${p === (s.extraction?.parser || "none") ? "selected" : ""}>${p}</option>`).join("")}</select></div>
      <div class="field"><label><input type="checkbox" name="respect_robots" ${s.respect_robots ? "checked" : ""}> Obey robots.txt</label><label><input type="checkbox" name="render_js" ${s.render_js ? "checked" : ""}> Needs JS rendering</label><label><input type="checkbox" name="resumable" ${s.resumable ? "checked" : ""}> Resumable (JOBDIR)</label></div></div>
    <div class="grid g-2"><div class="field"><label>Start URLs (one per line)</label><textarea class="input" name="start_urls">${esc(L(s.start_urls))}</textarea></div>
      <div class="field"><label>Sitemap URLs</label><textarea class="input" name="sitemap_urls">${esc(L(s.sitemap_urls))}</textarea></div>
      <div class="field"><label>Allowed domains</label><textarea class="input" name="allowed_domains">${esc(L(s.allowed_domains))}</textarea><span class="hint">Empty = derived from URLs</span></div>
      <div class="field"><label>Allow patterns (regex)</label><textarea class="input" name="allow_patterns">${esc(L(s.allow_patterns))}</textarea></div>
      <div class="field"><label>Deny patterns (regex)</label><textarea class="input" name="deny_patterns">${esc(L(s.deny_patterns))}</textarea></div>
      <div class="field"><label>Pagination (JSON)</label><textarea class="input" name="pagination">${esc(JSON.stringify(s.pagination || {}, null, 1))}</textarea><span class="hint">{"type":"page","param":"page","pages_path":"0.pages","max_pages":5}</span></div>
      <div class="field"><label>Extraction options (JSON, besides parser)</label><textarea class="input" name="extraction">${esc(JSON.stringify(Object.fromEntries(Object.entries(s.extraction || {}).filter(([k]) => k !== "parser")), null, 1))}</textarea></div>
      <div class="field"><label>Validation rules (JSON)</label><textarea class="input" name="validation">${esc(JSON.stringify(s.validation || {}, null, 1))}</textarea><span class="hint">{"required":["value"],"min":0,"max":100000,"allowed_units":["USD/mt"]}</span></div></div>
    <div id="form-err"></div>
    <div class="row"><button class="btn primary" type="submit">${existing ? "Save changes" : "Register source"}</button><button class="btn" type="button" data-close>Cancel</button></div></form></div>`);
  $$("[data-close]", m).forEach((b) => (b.onclick = closeModal));
  m.querySelector("#src-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), lines = (k) => (f.get(k) || "").split("\n").map((x) => x.trim()).filter(Boolean), csv = (k) => (f.get(k) || "").split(",").map((x) => x.trim()).filter(Boolean);
    let body;
    try {
      body = { schedule_interval_minutes: f.get("schedule_interval_minutes") ? +f.get("schedule_interval_minutes") : null, schedule_cron: f.get("schedule_cron") || null,
        concurrency: +f.get("concurrency"), download_delay: +f.get("download_delay"), max_pages: +f.get("max_pages"), max_depth: +f.get("max_depth"),
        freshness_sla_hours: +f.get("freshness_sla_hours"), respect_robots: !!f.get("respect_robots"), render_js: !!f.get("render_js"), resumable: !!f.get("resumable"),
        start_urls: lines("start_urls"), sitemap_urls: lines("sitemap_urls"), allowed_domains: lines("allowed_domains"), allow_patterns: lines("allow_patterns"), deny_patterns: lines("deny_patterns"),
        pagination: JSON.parse(f.get("pagination") || "{}"), extraction: { parser: f.get("parser"), ...JSON.parse(f.get("extraction") || "{}") }, validation: JSON.parse(f.get("validation") || "{}") };
      if (!existing) Object.assign(body, { slug: f.get("slug"), name: f.get("name"), base_url: f.get("base_url"), source_type: f.get("source_type"), website_category: f.get("website_category"),
        verification_status: f.get("verification_status"), license: f.get("license") || null, commercial_use: f.get("commercial_use"), access_method: f.get("access_method"),
        data_kinds: csv("data_kinds"), categories: csv("categories"), countries: csv("countries"), status: "paused" });
    } catch (err) { $("#form-err").innerHTML = banner("Invalid JSON: " + esc(err.message), "warn"); return; }
    try {
      const res = existing ? await api(`/api/sources/${existing.slug}`, { method: "PATCH", body }) : await api("/api/sources", { method: "POST", body });
      closeModal(); toast(existing ? "Crawl rules saved" : `Source ${res.slug} registered (paused)`);
      if (after) after(); else go(`spiders/${res.slug}`);
    } catch (err) { $("#form-err").innerHTML = banner(esc(err.message), "warn"); }
  });
}

/* =============================== SOURCE EXPLORER =============================== */
route("sources", async (view, { params }) => {
  const [cats] = await Promise.all([api("/api/taxonomy/categories")]);
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Registry</div><h1>Source <em>Explorer</em></h1>
    <p>Verified sources are distinguished from planned integrations and sources whose terms forbid crawling. Licence and commercial-use terms are recorded per source.</p></div></div>
    <div class="filters">
      <input class="input" id="f-q" placeholder="Search name, URL, notes…" value="${esc(params.q || "")}" style="min-width:220px;flex:1">
      <select class="input" id="f-cat"><option value="">All categories</option>${cats.items.map((c) => `<option value="${c.slug}">${esc(c.name)}</option>`).join("")}</select>
      <select class="input" id="f-type"><option value="">All types</option>${["api", "html", "sitemap", "rss", "csv", "xlsx", "pdf", "internal"].map((t) => `<option>${t}</option>`).join("")}</select>
      <select class="input" id="f-kind"><option value="">All data kinds</option>${["price", "fx", "indicator", "trade", "property", "standard", "publication", "news"].map((t) => `<option>${t}</option>`).join("")}</select>
      <select class="input" id="f-ver"><option value="">Any verification</option>${["verified", "url_verified", "documented", "key_required", "planned", "blocked_by_terms"].map((t) => `<option>${t}</option>`).join("")}</select>
      <input class="input" id="f-country" placeholder="Country ISO3" style="width:120px">
      <input class="input" id="f-mat" placeholder="Material" style="width:130px"></div>
    <div class="card"><div class="card-b flush" id="src-table">${skeleton(8)}</div></div>`;
  const load = async () => {
    const q = new URLSearchParams({ q: $("#f-q").value, category: $("#f-cat").value, source_type: $("#f-type").value, kind: $("#f-kind").value, verification: $("#f-ver").value, country: $("#f-country").value.toUpperCase(), material: $("#f-mat").value });
    [...q.keys()].forEach((k) => !q.get(k) && q.delete(k));
    try {
      const d = await api("/api/sources?" + q);
      $("#src-table").innerHTML = table([
        { label: "Source", html: (s) => `<b>${esc(s.name)}</b><div class="sub truncate">${esc(s.base_url)}</div>` },
        { label: "Type", html: (s) => `<span class="tag">${esc(s.source_type)}</span>` },
        { label: "Data", html: (s) => (s.data_kinds || []).map((k) => `<span class="tag">${esc(k)}</span>`).join("") },
        { label: "Verification", html: (s) => pill(s.verification_status) },
        { label: "Licence", html: (s) => `<div class="small truncate" style="max-width:220px" title="${esc(s.license || "")}">${esc(s.license || "-")}</div><div class="sub">commercial: ${esc(s.commercial_use)}</div>` },
        { label: "Reliability", html: (s) => `<div class="row" style="flex-wrap:nowrap;min-width:90px"><div class="bar" style="flex:1"><span style="width:${s.reliability * 100}%"></span></div><span class="small">${Math.round(s.reliability * 100)}</span></div>` },
        { label: "Freshness", html: (s) => pill(s.freshness) },
        { label: "Records", num: true, html: (s) => num(s.records) },
      ], d.items, { onRow: (s) => sourceDrawer(s), empty: "No sources match these filters" });
    } catch (e) { $("#src-table").innerHTML = errorState(e); }
  };
  $$(".filters .input", view).forEach((i) => i.addEventListener(i.tagName === "SELECT" ? "change" : "input", () => { clearTimeout(load.t); load.t = setTimeout(load, 200); }));
  load();
});
function sourceDrawer(s) {
  const list = (a) => (a && a.length ? a.map((x) => `<span class="tag">${esc(x)}</span>`).join("") : "-");
  openDrawer(`<div class="eyebrow">${esc(s.website_category.replace(/_/g, " "))}</div><h2>${esc(s.name)}</h2><div class="row" style="margin-top:6px">${pill(s.verification_status)} ${pill(s.status)} ${pill(s.freshness)}</div>`,
    `<dl class="kv"><dt>Base URL</dt><dd><a href="${esc(s.base_url)}" target="_blank" rel="noopener">${esc(s.base_url)}</a></dd>
    <dt>Source type</dt><dd>${esc(s.source_type)}</dd><dt>Access method</dt><dd>${esc(s.access_method)}</dd>
    <dt>Credential</dt><dd>${s.auth_env_var ? `<code>${esc(s.auth_env_var)}</code> ${s.credential_present ? pill("verified", "set") : pill("key_required", "not set")}` : "none required"}</dd>
    <dt>Licence</dt><dd>${esc(s.license || "-")} ${s.license_url ? `<a href="${esc(s.license_url)}" target="_blank" rel="noopener">terms</a>` : ""}</dd>
    <dt>Commercial use</dt><dd>${esc(s.commercial_use)}</dd><dt>Attribution</dt><dd>${esc(s.attribution || "-")}</dd>
    <dt>Verified on</dt><dd>${esc(s.verified_at || "-")}</dd><dt>Update frequency</dt><dd>${esc(s.update_frequency || "-")}</dd>
    <dt>Reliability</dt><dd>${Math.round(s.reliability * 100)} / 100</dd>
    <dt>Data kinds</dt><dd>${list(s.data_kinds)}</dd><dt>Categories</dt><dd>${list(s.categories)}</dd><dt>Materials</dt><dd>${list(s.materials)}</dd>
    <dt>Countries</dt><dd>${list(s.countries)}</dd><dt>Schedule</dt><dd>${s.schedule_cron ? `cron ${esc(s.schedule_cron)} UTC` : s.schedule_interval_minutes ? "every " + intervalLabel(s.schedule_interval_minutes) : "manual"}</dd>
    <dt>Crawl scope</dt><dd>${list(s.allowed_domains)} · ${s.max_pages} pages · depth ${s.max_depth}</dd>
    <dt>Extraction</dt><dd><pre class="json">${esc(JSON.stringify(s.extraction, null, 2))}</pre></dd>
    <dt>Last success</dt><dd>${dt(s.last_success_at)}</dd><dt>Records</dt><dd>${num(s.records)} · ${num(s.urls)} URLs</dd>
    <dt>Failures</dt><dd>${s.failure_count} (${s.consecutive_failures} consecutive)</dd>
    <dt>Notes</dt><dd>${esc(s.notes || "-")}</dd></dl>
    ${s.source_type !== "internal" ? `<div class="row" style="margin-top:16px"><a class="btn primary" href="#/spiders/${s.slug}">Open in Spider Manager</a><a class="btn" href="#/data?source=${s.slug}">View records</a></div>` : ""}`);
}

/* =============================== SETTINGS =============================== */
const shortPath = (p) => { const parts = String(p).split("/").filter(Boolean); return parts.length > 2 ? "…/" + parts.slice(-2).join("/") : p; };
route("settings", async (view) => {
  const [st, al, imp, ids] = await Promise.all([api("/api/settings"), api("/api/alerts?include_ack=true"), api("/api/admin/import-taxonomy"), api("/api/identifiers")]);
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">System</div><h1><em>Settings</em></h1><p>Service configuration, crawl limits, source policies, credentials and alert rules.</p></div></div>
  <div class="grid g-2">
    <div class="card"><div class="card-h"><h3>Service</h3></div><div class="card-b"><dl class="kv">
      <dt>Database</dt><dd><code>${esc(st.service.database)}</code></dd><dt>Redis</dt><dd>${st.service.redis_url ? (st.service.redis_connected ? pill("verified", "connected") : pill("failed", "unreachable")) : pill("plain", "not configured (optional)")}</dd>
      <dt>Taxonomy folder</dt><dd><code title="${esc(st.service.data_root)}">${esc(shortPath(st.service.data_root))}</code> ${st.service.data_root_exists ? "" : pill("failed", "missing")}</dd>
      <dt>Replay mode</dt><dd>${st.service.replay_mode ? pill("key_required", "on: recorded responses") : pill("verified", "off: live network")}</dd>
      <dt>Worker</dt><dd>${st.service.embedded_worker ? "embedded in API" : "separate process"} · last heartbeat ${esc(ago(st.service.last_job_heartbeat))}</dd>
      <dt>User-Agent</dt><dd><code class="small">${esc(st.service.user_agent)}</code></dd><dt>Contact e-mail</dt><dd>${esc(st.service.contact_email || "- (set MIP_CONTACT_EMAIL for polite API pools)")}</dd>
      <dt>Admin token</dt><dd>${st.service.admin_token_required ? "required for changes" : "not required (set MIP_ADMIN_TOKEN in production)"} <input class="input" id="admin-token" type="password" placeholder="Token for this browser" value="${esc(ADMIN.token)}" style="margin-top:6px;width:100%"></dd></dl></div></div>
    <div class="card"><div class="card-h"><h3>Crawl limits</h3><span class="sub">stored in the database; applied to the next job</span></div><div class="card-b">
      <form id="limits" class="form-grid">${Object.entries(st.crawl_policy).map(([k, v]) => `<div class="field"><label>${esc(k.replace(/_/g, " "))}</label><input class="input" name="${k}" type="number" step="any" min="0" value="${esc(v)}"></div>`).join("")}
      <div class="field" style="justify-content:flex-end"><button class="btn primary" type="submit">Save limits</button></div></form>
      <hr class="sep"><h3 style="font-size:13.5px;margin-bottom:8px">Source policies</h3><ul class="small" style="margin:0;padding-left:18px">${st.policies.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div></div>
    <div class="card"><div class="card-h"><h3>Credentials</h3><span class="sub">environment variables: values are never shown or stored</span></div><div class="card-b flush">${table([{ label: "Variable", html: (c) => `<code>${esc(c.env_var)}</code>` }, { label: "Status", html: (c) => c.present ? pill("verified", "set") : pill("plain", "not set") }], st.credentials)}</div></div>
    <div class="card"><div class="card-h"><h3>Taxonomy import</h3><span class="sub">re-reads the memory/ folder; unchanged files are skipped by checksum</span></div><div class="card-b">
      <div class="row"><button class="btn primary" id="reimport" ${imp.running ? "disabled" : ""}>${icon("refresh")} Re-import changed files</button><button class="btn" id="reimport-force" ${imp.running ? "disabled" : ""}>Force full re-import</button></div>
      <div id="imp-status" style="margin-top:12px">${importStatus(imp)}</div>
      <hr class="sep"><h3 style="font-size:13.5px;margin-bottom:8px">Unresolved identifiers</h3>${ids.items.map((u) => `<div class="row between"><span><code>${esc(u.code)}</code> · ${u.occurrences} occurrences</span>${pill(u.status)}</div>`).join("")}<a class="small" href="#/taxonomy?panel=identifiers">Review evidence →</a></div></div>
  </div>
  <div class="card" style="margin-top:16px"><div class="card-h"><h3>Alert rules</h3><button class="btn sm" id="eval">Evaluate now</button></div><div class="card-b flush">${table([
    { label: "Rule", html: (r) => esc(r.name) }, { label: "Kind", html: (r) => `<span class="tag">${esc(r.kind)}</span>` }, { label: "Series", html: (r) => esc(r.series_key || (r.material_id ? "material #" + r.material_id : "all")) },
    { label: "Threshold", num: true, html: (r) => r.threshold_pct != null ? r.threshold_pct + "%" : "-" }, { label: "Window", num: true, html: (r) => r.window_days + " d" }, { label: "Active", html: (r) => r.active ? pill("verified", "on") : pill("plain", "off") }], al.rules)}</div></div>`;
  $("#admin-token").addEventListener("change", (e) => { ADMIN.token = e.target.value; try { localStorage.setItem("mip_admin_token", ADMIN.token); } catch { /* private mode */ } toast("Token saved for this browser"); });
  $("#limits").addEventListener("submit", async (e) => { e.preventDefault(); const body = Object.fromEntries(new FormData(e.target)); try { await api("/api/settings", { method: "PUT", body }); toast("Limits saved"); } catch (err) { toast(err.message, true); } });
  const runImport = async (force) => { try { await api(`/api/admin/import-taxonomy?force=${force}`, { method: "POST" }); toast("Import started"); poll(); } catch (e) { toast(e.message, true); } };
  const poll = () => { const t = every(1500, async () => { const s = await api("/api/admin/import-taxonomy"); $("#imp-status").innerHTML = importStatus(s); if (!s.running) { clearInterval(t); timers.delete(t); } }); };
  $("#reimport").onclick = () => runImport(false); $("#reimport-force").onclick = () => runImport(true);
  $("#eval").onclick = async () => { try { const r = await api("/api/alerts/evaluate", { method: "POST" }); toast(`${r.fired} new alert(s)`); } catch (e) { toast(e.message, true); } };
});
const importStatus = (s) => s.running ? '<div class="progress"><span></span></div><div class="small muted" style="margin-top:6px">Importing…</div>'
  : s.last ? (s.last.ok ? `<div class="small">${pill("succeeded")} last run ${esc(ago(s.last.started))}: ${esc(Object.entries(s.last.stats).map(([k, v]) => `${k}: ${v}`).join(", "))}</div>` : `<div class="small">${pill("failed")} ${esc(s.last.error)}</div>`)
  : '<div class="small muted">No import has run from the UI in this API process (CLI imports are not tracked here).</div>';
