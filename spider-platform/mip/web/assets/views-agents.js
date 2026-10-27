/* Agent Intelligence: Emithran Brain overview, specialist agents, flags, forecasts, disagreements and model setup */
"use strict";

const SEV_ORDER = ["critical", "high", "medium", "low", "info"];
const sevRank = (s) => SEV_ORDER.length - SEV_ORDER.indexOf(s);
const agentFilters = { agent: "", sev: "medium+", status: "live", q: "", page: 1, actPage: 1 };   // survive live redraws
const AGENT_ICON = { data_quality: "sources", commodity: "market", fx: "market", labour: "material", news_risk: "data", source_scout: "spider", red_team: "alert" };

/* Chart series for a finding's forecast: history, median, and the 80% band; dates are derived from the series type. */
function forecastSeries(f) {
  const hist = (f.history || []).map((p) => ({ x: p.date, y: p.value }));
  const last = hist[hist.length - 1];
  if (!last || !f.points) return [];
  const base = new Date(last.x), walk = (f.method || "").startsWith("random walk");
  const at = (p) => { if (p.x) return `${p.x}-12-31`; const d = new Date(base); walk ? d.setDate(d.getDate() + p.h) : d.setMonth(d.getMonth() + p.h); return d.toISOString().slice(0, 10); };
  const mk = (key) => [last, ...f.points.map((p) => ({ x: at(p), y: p[key] }))];
  return [{ name: "History", points: hist }, { name: "Forecast (median)", points: mk("p50") }, { name: "80% low", points: mk("lo80") }, { name: "80% high", points: mk("hi80") }];
}

function signals(f) {
  const out = [];
  if (f.corroborated_by?.length) out.push(`<span class="tag teal" title="Also flagged by: ${esc(f.corroborated_by.join(", "))}">corroborated ×${f.corroborated_by.length}</span>`);
  if (f.contested) out.push('<span class="tag" style="color:var(--err)" title="The red-team agent found a substantive weakness">contested</span>');
  if (f.mode === "llm") out.push('<span class="tag" title="A language model reviewed this flag">LLM</span>');
  if (f.has_forecast) out.push('<span class="tag">forecast</span>');
  return out.join(" ");
}

async function findingDrawer(id, refresh) {
  const body = openDrawer('<div class="eyebrow">Flag</div><h2>Loading…</h2>', skeleton(8));
  let f;
  try { f = await api(`/api/agents/findings/${id}`); } catch (e) { body.innerHTML = errorState(e); return; }
  $("#drawer-title").innerHTML = `<div class="eyebrow">${esc(f.agent_name)} · ${esc(f.kind.replace(/_/g, " "))}</div><h2>${esc(f.title)}</h2>
    <div class="row" style="margin-top:6px">${pill(f.severity)} ${pill(f.status)} <span class="small muted">confidence ${Math.round(f.confidence * 100)}%</span> ${signals(f)}</div>`;
  const an = f.analysis || {};
  const chall = an.challenges || [];
  const fc = f.forecast;
  const bt = fc?.backtest?.skill;
  body.innerHTML = `
    <p style="margin:0 0 14px">${esc(f.summary)}</p>
    <div class="row" style="margin-bottom:16px">
      <button class="btn sm" data-st="ack">Acknowledge</button><button class="btn sm" data-st="dismissed">Dismiss</button><button class="btn sm" data-st="open">Reopen</button>
    </div>
    ${an.opinion ? `<div class="banner">${icon("info")}<div><b>${esc(an.model || "Model")} says (${esc(an.stance || "agree")}):</b> ${esc(an.opinion)}
      <div class="small muted" style="margin-top:4px">Cites evidence ${esc((an.cites || []).join(", "))}. Rules-based severity was ${esc(an.severity_rules || f.severity)}.</div></div></div>` : ""}
    ${chall.length ? `<h4 class="panel-h" style="margin-top:16px">Challenges and caveats</h4>${chall.map((c) => `<div class="small" style="margin:0 0 6px"><span class="tag" style="${c.weight === "major" ? "color:var(--err)" : ""}">${esc(c.weight)}</span> ${esc(c.text)} <span class="muted">(${esc(c.mode === "llm" ? c.model || "LLM" : "rules")})</span></div>`).join("")}` : ""}
    ${fc ? `<h4 class="panel-h" style="margin-top:16px">${fc.indicative ? "Indicative projection" : "Forecast"}</h4>
      <div id="fd-chart"></div><div class="legend" id="fd-legend"></div>
      <div class="small muted" style="margin-top:6px">${esc(fc.method || "")}. ${bt == null ? (fc.indicative ? "No back-test is possible on so few points." : "No back-test available.") : `Back-test skill vs a naive guess: <b>${bt.toFixed(2)}</b> ${bt > 0 ? "(better than naive)" : "(no better than naive: do not rely on it)"}.`}</div>` : ""}
    <h4 class="panel-h" style="margin-top:16px">Evidence (${f.evidence.length})</h4>
    ${table([{ label: "#", html: (e) => `<code>${esc(e.id)}</code>` }, { label: "Fact", html: (e) => e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.label)}</a>` : esc(e.label) },
      { label: "Value", num: true, html: (e) => `${esc(e.value ?? "-")} <span class="sub">${esc(e.unit || "")}</span>` },
      { label: "Source", html: (e) => `<span class="small">${esc(e.source || "-")}</span>${e.as_of ? `<div class="sub">${esc(e.as_of)}</div>` : ""}` },
      { label: "Origin", html: (e) => e.origin === "internal" ? '<span class="tag" title="From your taxonomy files; sent only to local models unless you allow hosted ones">internal</span>' : '<span class="tag teal">public</span>' }], f.evidence)}
    ${Object.keys(f.entities || {}).length ? `<h4 class="panel-h" style="margin-top:16px">What it touches</h4><dl class="kv">${Object.entries(f.entities).filter(([k]) => k !== "target").map(([k, v]) =>
      `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${Array.isArray(v) ? v.map((x) => `<span class="tag">${esc(typeof x === "string" ? x : JSON.stringify(x))}</span>`).join(" ") : esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("")}</dl>` : ""}
    ${an.action ? `<div class="banner" style="margin-top:16px">${icon("info")}<div><b>Suggested action:</b> ${an.action.type === "backfill" ? `Backfill <code>${esc(an.action.source)}</code> from ${esc(an.action.start_date)}. <button class="btn sm primary" id="fd-act">Run backfill</button>` : esc(an.action.hint || JSON.stringify(an.action))}</div></div>` : ""}
    <div class="small muted" style="margin-top:16px">First seen ${esc(ago(f.first_seen))} · last confirmed ${esc(ago(f.last_seen))} · mode ${esc(an.mode || "rules")}</div>`;
  if (fc) {
    const ser = forecastSeries(fc);
    Charts.line($("#fd-chart"), ser.map((s) => ({ name: s.name, points: s.points })), { height: 220 });
    $("#fd-legend").innerHTML = ser.map((s, i) => `<span><i style="background:${Charts.PALETTE[i % 8]}"></i>${esc(s.name)}</span>`).join("");
  }
  body.querySelectorAll("[data-st]").forEach((b) => { b.onclick = async () => {
    try { await api(`/api/agents/findings/${id}/status`, { method: "POST", body: { status: b.dataset.st } }); toast(`Marked ${b.dataset.st}`); closeDrawer(); refresh?.(); } catch (e) { toast(e.message, true); } }; });
  $("#fd-act")?.addEventListener("click", async () => {
    try { const j = await api("/api/jobs", { method: "POST", body: { source: an.action.source, params: { backfill: true, start_date: an.action.start_date } } }); toast(`Backfill job #${j.id} queued`); }
    catch (e) { toast(e.message, true); } });
}

async function modelSetup(refresh) {
  const st = await api("/api/agents/llm");
  const asg = JSON.parse(JSON.stringify(st.assignments || {}));
  const models = {};
  let allow = st.allow_hosted_internal;
  const draw = () => {
    const box = openModal(`<div class="card-h"><h3>Model setup</h3><button class="btn sm" id="ms-close">Close</button></div><div class="card-b" style="max-height:75vh;overflow:auto">
      <p class="small muted" style="margin-top:0">Agents work without any model (rules only). Add models to let them write grounded opinions, challenge each other and brief you. Keys are read from environment variables listed below (set them in <code>.env</code> and restart); they are never stored or shown here.</p>
      <h4 class="panel-h">Providers</h4>
      ${table([{ label: "Provider", html: (p) => `<b>${esc(p.label)}</b><div class="sub">${esc(p.base_url)}</div>` },
        { label: "Where", html: (p) => p.local ? '<span class="tag teal">local</span>' : '<span class="tag">hosted</span>' },
        { label: "Key", html: (p) => p.local ? '<span class="muted">none needed</span>' : `<code class="small">${esc(p.key_env)}</code> ${p.key_present ? pill("verified", "set") : pill("key_required", "not set")}` },
        { label: "Reachable / models", html: (p) => p.reachable === undefined ? '<span class="muted">-</span>' : p.reachable ? `${pill("verified", "yes")} <span class="small">${(models[p.name] || []).length} models</span>` : `${pill("failed", "no")} <span class="small">${esc(p.error || "")}</span>` }], st.providers)}
      <div class="row" style="margin:10px 0 18px"><button class="btn sm" id="ms-probe">Discover models</button><span class="small muted">Lists each configured provider's own model names (they change often, so none are hard-coded).</span></div>
      <h4 class="panel-h">Assignments (first working model wins; the rest are fallbacks)</h4>
      <div id="ms-list">${Object.entries(asg).map(([k, list]) => `<div class="row between" style="margin:4px 0"><span><b>${esc(k === "*" ? "All agents" : k)}</b>: ${list.map((a, i) => `<span class="tag">${esc(a.provider)}/${esc(a.model)} <a href="#" data-rm="${esc(k)}|${i}" title="Remove">×</a></span>`).join(" ")}</span></div>`).join("") || '<div class="small muted">Nothing assigned: agents run in rules-only mode.</div>'}</div>
      <div class="row" style="margin-top:10px;gap:8px">
        <select class="input" id="ms-agent" style="width:auto"><option value="*">All agents</option><option value="brain">Brain (brief)</option>${st.agent_ids.filter((a) => a !== "brain").map((a) => `<option>${esc(a)}</option>`).join("")}</select>
        <select class="input" id="ms-prov" style="width:auto">${st.providers.filter((p) => p.configured).map((p) => `<option value="${esc(p.name)}">${esc(p.label)}</option>`).join("")}</select>
        <input class="input" id="ms-model" list="ms-models" placeholder="model id exactly as the provider names it" style="flex:1;min-width:220px"><datalist id="ms-models"></datalist>
        <button class="btn sm" id="ms-add">Add</button></div>
      <label class="row" style="margin-top:16px;align-items:flex-start;gap:8px"><input type="checkbox" id="ms-allow" ${allow ? "checked" : ""} style="margin-top:3px">
        <span class="small"><b>Allow hosted providers to see internal taxonomy data.</b> Off by default: findings built from your taxonomy files go only to local models. Public-data findings may use any provider. Free hosted tiers may use prompts to improve their products.</span></label>
      <div class="row" style="margin-top:18px;justify-content:flex-end"><button class="btn primary" id="ms-save">Save</button></div></div>`);
    const fill = () => { $("#ms-models").innerHTML = (models[$("#ms-prov").value] || []).map((m) => `<option value="${esc(m)}">`).join(""); };
    $("#ms-prov")?.addEventListener("change", fill); fill();
    $("#ms-close").onclick = closeModal;
    $("#ms-probe").onclick = async () => { $("#ms-probe").textContent = "Probing…"; try { const p = await api("/api/agents/llm?probe=true"); st.providers = p.providers; p.providers.forEach((x) => { if (x.models) models[x.name] = x.models; }); } catch (e) { toast(e.message, true); } draw(); };
    $("#ms-add").onclick = () => { const m = $("#ms-model").value.trim(); if (!m) return toast("Enter a model id", true); const k = $("#ms-agent").value; (asg[k] = asg[k] || []).push({ provider: $("#ms-prov").value, model: m }); allow = $("#ms-allow").checked; draw(); };
    box.querySelectorAll("[data-rm]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); const [k, i] = a.dataset.rm.split("|"); asg[k].splice(+i, 1); if (!asg[k].length) delete asg[k]; allow = $("#ms-allow").checked; draw(); }; });
    $("#ms-save").onclick = async () => { try { await api("/api/agents/llm/assign", { method: "POST", body: { assignments: asg, allow_hosted_internal: $("#ms-allow").checked } }); toast("Model setup saved"); closeModal(); refresh?.(); } catch (e) { toast(e.message, true); } };
  };
  draw();
}

route("agents", async (view, { params }) => {
  const [ov, ag] = await Promise.all([api("/api/agents/overview"), api("/api/agents")]);
  const fl = agentFilters;
  const risk = ov.risk, last = ov.last_run, brief = ov.brief || {};
  const llmMode = last?.mode || "rules";
  const kpi = (label, value, foot, extra = "") => `<div class="card kpi"><div class="label"><span>${label}</span></div><div class="value">${value}</div><div class="foot">${foot}</div>${extra}</div>`;
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Intelligence</div><h1>Agent <em>Intelligence</em></h1>
    <p>Seven specialists, each with one job, review every dataset. The Emithran Brain reconciles them, scores the risk and shows where they disagree. Every number is computed from your stored data and cited as evidence.</p></div>
    <div class="row"><button class="btn" id="ag-setup">${icon("settings")} Model setup</button><button class="btn primary" id="ag-run">${icon("spider")} ${ov.running ? "Running…" : "Run all agents"}</button></div></div>
    <div class="grid g-6" style="margin-bottom:var(--gap)">
      ${kpi("Overall risk", `${Math.round(risk.overall.score)}<span style="font-size:14px;color:var(--muted)"> / 100</span>`, `${pill(risk.overall.level)} across ${risk.counts.open} live flags`)}
      ${kpi("Needs attention", risk.counts.critical + risk.counts.high, `${risk.counts.critical} critical · ${risk.counts.high} high · ${risk.counts.medium} medium`)}
      ${kpi("Contested", risk.counts.contested, "flags the red team challenged")}
      ${kpi("Agents", `${ag.agents.length}`, `${ag.agents.filter((a) => a.last?.status === "ok").length} ran OK last time`)}
      ${kpi("Reasoning", llmMode === "rules" ? "Rules" : llmMode === "llm" ? "LLM" : "Mixed", ov.llm.assigned_agents.length ? `models assigned to ${ov.llm.assigned_agents.length} role(s)` : "no model assigned yet")}
      ${kpi("Last run", last ? esc(ago(last.finished_at || last.started_at)) : "never", last ? `${pill(last.status)} #${last.id}` : "press Run all agents")}
    </div>
    <div class="grid g-2 fillrow" style="margin-bottom:var(--gap)">
      <div class="card fill"><div class="card-h"><h3>Brain brief</h3><span class="sub">${brief.mode === "llm" ? esc(brief.model || "LLM") + " · grounded in the flags below" : "rules summary · add a model for a written brief"}</span></div>
        <div class="card-b">${brief.headline ? `<p style="margin:0 0 12px;font-weight:600;font-size:15px">${esc(brief.headline)}</p>
          ${(brief.bullets || []).map((b) => `<div style="margin:0 0 8px">• ${esc(b.text)} ${(b.items || []).map((i) => `<a href="#" data-f="${i.id}" class="small">[${esc(i.severity)}]</a>`).join(" ")}</div>`).join("")}
          ${(brief.watch_next || []).length ? `<div class="panel-h" style="margin-top:14px">Watch next</div>${brief.watch_next.map((w) => `<div class="small">• ${esc(w)}</div>`).join("")}` : ""}`
          : emptyState("No run yet", "Press Run all agents to generate the first review.")}</div></div>
      <div class="card fill"><div class="card-h"><h3>Risk by domain</h3><span class="sub">0 to 100, from live flags weighted by severity and confidence</span></div>
        <div class="card-b"><div id="ag-domains"></div></div></div>
    </div>
    <div class="panel-h" style="margin:0 0 8px">Specialist agents</div>
    <div class="agent-grid" id="ag-cards" style="margin-bottom:var(--gap)">${ag.agents.map((a) => {
      const c = a.counts, hot = c.critical + c.high, l = a.last;
      return `<div class="card agent-card"><div class="card-b"><div class="row between" style="align-items:flex-start"><div class="row" style="gap:8px;flex-wrap:nowrap">${icon(AGENT_ICON[a.id] || "spider")}<b>${esc(a.name)}</b></div>
        <button class="btn sm" data-run="${esc(a.id)}">Run</button></div>
        <p class="small muted" style="margin:8px 0 12px">${esc(a.job)}</p>
        <div class="row" style="gap:6px">${hot ? `<span class="tag" style="color:var(--err)">${hot} high+</span>` : ""}${c.medium ? `<span class="tag" style="color:var(--warn)">${c.medium} medium</span>` : ""}${c.low ? `<span class="tag">${c.low} low</span>` : ""}${a.open === 0 ? '<span class="tag teal">nothing flagged</span>' : ""}</div>
        <div class="small muted" style="margin-top:10px">${l ? (l.status === "error" ? `<span style="color:var(--err)">error: ${esc(l.error)}</span>` : `${esc(l.mode === "llm" ? l.model : "rules only")} · ${l.findings} findings · ${l.seconds}s`) : "not run yet"}${l?.notes?.length ? `<div title="${esc(l.notes.join("; "))}">${esc(l.notes[0])}</div>` : ""}</div></div></div>`; }).join("")}</div>
    <div class="card" style="margin-bottom:var(--gap)"><div class="card-h"><h3>Flags</h3><span class="sub" id="fl-count"></span></div>
      <div class="filters" style="padding:var(--pad-sm) var(--pad) 0;margin:0">
        <select class="input" id="fl-agent" style="width:auto"><option value="">All agents</option>${ag.agents.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("")}</select>
        <select class="input" id="fl-sev" style="width:auto"><option value="medium+">Medium and up</option><option value="high+">High and up</option><option value="">All severities</option></select>
        <select class="input" id="fl-status" style="width:auto"><option value="live">Open and acknowledged</option><option value="ack">Acknowledged</option><option value="dismissed">Dismissed</option><option value="resolved">Resolved</option></select>
        <input class="input" id="fl-q" placeholder="Search flags" style="flex:1;min-width:180px"></div>
      <div id="fl-table"></div></div>
    <div class="card" style="margin-bottom:var(--gap)"><div class="card-h"><h3>Activity</h3><span class="sub">history of every flag: new, worse, better, resolved, back. Nothing is overwritten.</span></div><div class="card-b flush" id="ag-act"></div></div>
    <div class="grid g-2 fillrow"><div class="card fill"><div class="card-h"><h3>Forecast outlook</h3><span class="sub">only forecasts that beat a naive guess in back-testing</span></div><div class="card-b flush" id="ag-fc"></div></div>
      <div class="card fill"><div class="card-h"><h3>Where agents disagree</h3><span class="sub">flags the red team contests</span></div><div class="card-b flush" id="ag-dis"></div></div></div>`;

  $("#fl-agent").value = fl.agent; $("#fl-sev").value = fl.sev; $("#fl-status").value = fl.status; $("#fl-q").value = fl.q;
  Charts.hbar($("#ag-domains"), Object.values(risk.domains).sort((a, b) => b.score - a.score).map((d) => ({ label: d.label, value: d.score, note: d.level })), { labelWidth: 160, suffix: "" });
  view.querySelectorAll("[data-f]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); findingDrawer(+a.dataset.f, () => render()); }; });
  $("#ag-setup").onclick = () => modelSetup(() => render());
  const poll = async (rid) => { for (let i = 0; i < 400; i++) { await new Promise((r) => setTimeout(r, 1500)); const s = await api(`/api/agents/runs/${rid}`); if (s.status !== "running") return s; } };
  const start = async (agents) => {
    try { const r = await api("/api/agents/run", { method: "POST", body: agents ? { agents } : {} }); toast(`Run #${r.run_id} started`); $("#ag-run").textContent = "Running…"; $("#ag-run").disabled = true;
      const s = await poll(r.run_id); toast(`Run #${r.run_id} ${s.status}`, s.status === "failed"); render(); }
    catch (e) { toast(e.message, true); } };
  $("#ag-run").onclick = () => start(null);
  $("#ag-cards").onclick = (e) => { const b = e.target.closest("[data-run]"); if (b) start([b.dataset.run, "red_team"].filter((v, i, a) => a.indexOf(v) === i)); };

  const PAGE = 25;
  const loadFlags = async () => {
    const minSev = fl.sev === "high+" ? "high" : fl.sev === "medium+" ? "medium" : "";
    const qs = new URLSearchParams({ status: fl.status, limit: PAGE, offset: (fl.page - 1) * PAGE, ...(minSev ? { min_severity: minSev } : {}), ...(fl.agent ? { agent: fl.agent } : {}), ...(fl.q ? { q: fl.q } : {}) });
    const d = await api(`/api/agents/findings?${qs}`);
    $("#fl-count").textContent = `${nf.format(d.total)} flag${d.total === 1 ? "" : "s"}`;
    $("#fl-table").innerHTML = table([
      { label: "Severity", html: (f) => pill(f.severity) },
      { label: "Flag", html: (f) => `<b>${esc(f.title)}</b><div class="sub">${esc(f.summary.slice(0, 150))}${f.summary.length > 150 ? "…" : ""}</div>` },
      { label: "Agent", html: (f) => `<span class="small">${esc(f.agent_name)}</span>` },
      { label: "Conf.", num: true, html: (f) => `${Math.round(f.confidence * 100)}%` },
      { label: "Signals", html: (f) => signals(f) || '<span class="muted">-</span>' },
      { label: "Seen", html: (f) => `<span class="small">${esc(ago(f.last_seen))}</span>` }], d.items, { onRow: (f) => findingDrawer(f.id, loadFlags), empty: "No flags match" })
      + (d.total > PAGE ? pager(d.total, (fl.page - 1) * PAGE, PAGE, (off) => { fl.page = Math.floor(off / PAGE) + 1; loadFlags(); }) : "");
  };
  const EVENT_LABEL = { new: ["new", "info"], escalated: ["worse", "err"], deescalated: ["better", "ok"], resolved: ["resolved", "ok"], reopened: ["back", "warn"] };
  const loadActivity = async () => {
    const d = await api(`/api/agents/activity?page=${fl.actPage}&size=15`);
    $("#ag-act").innerHTML = d.items.length ? table([
      { label: "When", html: (e) => `<span class="small" title="${esc(e.ts)}">${esc(ago(e.ts))}</span>` },
      { label: "Event", html: (e) => `<span class="pill ${EVENT_LABEL[e.event]?.[1] || ""}">${esc(EVENT_LABEL[e.event]?.[0] || e.event)}</span>` },
      { label: "Flag", html: (e) => `<a href="#" data-f="${e.finding_id}"><b>${esc(e.title)}</b></a><div class="sub">${esc(e.agent_name)}${e.prev_severity ? ` · ${esc(e.prev_severity)} to ${esc(e.severity)}` : ""}</div>` },
      { label: "Severity", html: (e) => pill(e.severity) },
      { label: "Run", num: true, html: (e) => `#${e.run_id ?? "-"}` }], d.items)
      + (d.pages > 1 ? pager(d.total, (fl.actPage - 1) * 15, 15, (off) => { fl.actPage = Math.floor(off / 15) + 1; loadActivity(); }) : "")
      : emptyState("No activity yet", "Every new, worsening, improving and resolved flag is recorded here once the agents have run.");
    $("#ag-act").querySelectorAll("[data-f]").forEach((x) => { x.onclick = (ev) => { ev.preventDefault(); findingDrawer(+x.dataset.f, () => render()); }; });
  };
  let timer; const reload = () => { clearTimeout(timer); timer = setTimeout(loadFlags, 200); };
  $("#fl-agent").onchange = (e) => { fl.agent = e.target.value; fl.page = 1; loadFlags(); };
  $("#fl-sev").onchange = (e) => { fl.sev = e.target.value; fl.page = 1; loadFlags(); };
  $("#fl-status").onchange = (e) => { fl.status = e.target.value; fl.page = 1; loadFlags(); };
  $("#fl-q").oninput = (e) => { fl.q = e.target.value; fl.page = 1; reload(); };
  loadFlags(); loadActivity();

  $("#ag-fc").innerHTML = ov.forecasts.length ? table([
    { label: "Series", html: (f) => `<a href="#" data-f="${f.id}">${esc(f.title.split(":")[0])}</a>` },
    { label: "Latest", num: true, html: (f) => `${num(f.last, 2)} <span class="sub">${esc(f.unit || "")}</span>` },
    { label: "+6 months", num: true, html: (f) => `${num(f.p50, 2)}<div class="sub">${num(f.lo80, 2)} to ${num(f.hi80, 2)}</div>` },
    { label: "Change", num: true, html: (f) => `<b style="color:${(f.change_pct || 0) > 0 ? "var(--warn)" : "var(--teal-700)"}">${f.change_pct > 0 ? "+" : ""}${f.change_pct}%</b>` },
    { label: "Skill", num: true, html: (f) => f.skill == null ? "-" : `<span title="1 - model error / naive error; above 0 beats a naive guess" style="color:${f.skill > 0.1 ? "var(--teal-700)" : "var(--muted)"}">${f.skill.toFixed(2)}</span>` }], ov.forecasts)
    : emptyState(ov.forecast_stats.evaluated ? "No forecast beats a naive guess" : "No forecasts yet",
        ov.forecast_stats.evaluated ? `${ov.forecast_stats.evaluated} price series were forecast and back-tested; none beat a simple "no change" baseline (skill above 0.1), so none is shown as a forecast. Open a flag to see its chart and its skill score.` : "Forecasts appear after a run, for series with enough history.");
  $("#ag-dis").innerHTML = ov.contested.length ? table([{ label: "Flag", html: (f) => `<a href="#" data-f="${f.id}"><b>${esc(f.title)}</b></a><div class="sub">${esc(f.agent_name)}</div>` }, { label: "Severity", html: (f) => pill(f.severity) }], ov.contested)
    : emptyState("No contested flags", "The red-team agent has not found a substantive weakness in any live flag.");
  view.querySelectorAll("#ag-fc [data-f], #ag-dis [data-f]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); findingDrawer(+a.dataset.f, () => render()); }; });
  if (params.flag) findingDrawer(+params.flag, () => render());   // #/agents?flag=<id> opens a flag directly
});
