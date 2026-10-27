/* Screens: Taxonomy, Material Intelligence, Market Intelligence, Data Explorer + boot */
"use strict";

/* =============================== TAXONOMY =============================== */
route("taxonomy", async (view, { sub, params }) => {
  const cats = await api("/api/taxonomy/categories");
  const fams = {};
  cats.items.forEach((c) => (fams[c.family] = fams[c.family] || []).push(c));
  let selected = sub[0] || (params.panel === "identifiers" ? "__ids" : "die-casting");
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Reference</div><h1>Manufacturing <em>Taxonomy</em></h1>
    <p>Built from the existing folder structure. Original folder names are preserved and mapped to normalized categories and to <code>digital_factory_tree.csv</code> nodes.</p></div>
    <div class="row"><button class="btn" id="show-tree">digital_factory_tree.csv</button><button class="btn" id="show-ids">MHR / LHR identifiers</button></div></div>
    <div class="split"><div class="card"><div class="list" id="tax-list">${Object.entries(fams).map(([f, list]) => `
      <div class="nav-group" style="color:var(--muted);padding:12px 16px 4px">${esc(cats.families[f] || f)}</div>
      ${list.map((c) => `<div class="list-item ${c.slug === selected ? "sel" : ""}" data-slug="${c.slug}" style="${c.parent ? "padding-left:30px" : ""}">
        <div class="row between"><span class="t1">${esc(c.name)}</span><span class="small muted">${nf.format(c.rows)}</span></div>
        <div class="t2">folder “${esc(c.original_folder || "—")}” · ${c.datasets} files · ${c.materials} materials</div></div>`).join("")}`).join("")}</div></div>
      <div id="tax-detail"></div></div>`;
  $("#tax-list").addEventListener("click", (e) => { const it = e.target.closest("[data-slug]"); if (!it) return; selected = it.dataset.slug; $$("#tax-list .list-item").forEach((x) => x.classList.toggle("sel", x === it)); history.replaceState(null, "", `#/taxonomy/${selected}`); draw(); });
  $("#show-tree").onclick = async () => {
    const t = await api("/api/taxonomy/tree");
    openDrawer(`<div class="eyebrow">Imported verbatim</div><h2>digital_factory_tree.csv</h2>`, table([{ label: "Level", num: true, html: (r) => r.level },
      { label: "Name", html: (r) => `<span style="padding-left:${r.level * 16}px">${r.node_type === "Folder" ? "📁" : "📄"} ${esc(r.name)}</span>` },
      { label: "Type", html: (r) => esc(r.node_type) }, { label: "Mapped category", html: (r) => r.category ? `<a href="#/taxonomy/${r.category}" onclick="closeDrawer()">${esc(r.category)}</a>` : '<span class="muted">—</span>' }], t.items));
  };
  $("#show-ids").onclick = () => { selected = "__ids"; draw(); };
  const draw = async () => {
    const box = $("#tax-detail");
    box.innerHTML = `<div class="card"><div class="card-b">${skeleton(8)}</div></div>`;
    if (selected === "__ids") return drawIdentifiers(box);
    let c;
    try { c = await api(`/api/taxonomy/categories/${selected}`); } catch (e) { box.innerHTML = `<div class="card">${errorState(e)}</div>`; return; }
    const byRole = {}; c.datasets.forEach((d) => (byRole[d.role] = byRole[d.role] || []).push(d));
    box.innerHTML = `<div class="card"><div class="card-h"><div><h3>${esc(c.name)}</h3><div class="sub">Original folder <code>${esc(c.original_folder || "—")}</code> · tree node <code>${esc(c.tree_node_name || "not in tree")}</code></div></div><span class="tag">${esc(c.family)}</span></div>
      <div class="card-b"><div class="grid g-3">
        <div><div class="small muted">Material families</div>${c.material_families.length ? c.material_families.map((f) => `<div class="row between small"><a href="#/materials?category=${c.slug}&family=${f.family}">${esc(f.label)}</a><span>${f.count}</span></div>`).join("") : '<div class="small">No material list in this folder</div>'}</div>
        <div><div class="small muted">Datasets by role</div>${Object.entries(byRole).map(([r, l]) => `<div class="row between small"><span>${esc(r.replace(/_/g, " "))}</span><span>${l.length}</span></div>`).join("")}</div>
        <div><div class="small muted">Related sources</div>${c.sources.slice(0, 8).map((s) => `<a class="tag" href="#/spiders/${s}">${esc(s)}</a>`).join("") || "—"}</div></div></div></div>
      <div class="tabs" style="margin-top:16px" id="tax-tabs"><button class="on" data-t="files">Files (${c.datasets.length})</button><button data-t="proc">Processes & operations (${c.processes.length})</button><button data-t="mach">Machines (${c.machine_groups.reduce((a, b) => a + b.machines, 0)})</button></div>
      <div class="card"><div class="card-b flush" id="tax-tab"></div></div>`;
    const tabs = {
      files: () => table([{ label: "File (original path)", html: (d) => `<span class="mono small">${esc(d.rel_path)}</span>` }, { label: "Role", html: (d) => `<span class="tag">${esc(d.role)}</span>` },
        { label: "Rows", num: true, html: (d) => num(d.rows) }, { label: "Columns", html: (d) => `<span class="small muted truncate" style="max-width:300px;display:inline-block">${esc((d.columns || []).slice(0, 6).join(", "))}${d.columns.length > 6 ? "…" : ""}</span>` }],
        c.datasets, { onRow: (d) => datasetViewer(d.id) }),
      proc: () => table([{ label: "Name", html: (p) => esc(p.name) }, { label: "Kind", html: (p) => (p.kinds || []).map((k) => `<span class="tag">${esc(k)}</span>`).join("") }, { label: "Default machine", html: (p) => esc(p.default_machine || "—") }], c.processes, { empty: "No process files" }),
      mach: () => table([{ label: "Process / group", html: (m) => esc(m.process) }, { label: "Machines", num: true, html: (m) => num(m.machines) }, { label: "Avg labour USD/h", num: true, html: (m) => m.avg_labor_usd_hr != null ? money(m.avg_labor_usd_hr) : "—" }], c.machine_groups, { empty: "No machine files" }),
    };
    $("#tax-tab").innerHTML = tabs.files();
    $("#tax-tabs").onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; $$("#tax-tabs button").forEach((x) => x.classList.toggle("on", x === b)); $("#tax-tab").innerHTML = tabs[b.dataset.t](); };
  };
  draw();
});

async function drawIdentifiers(box) {
  const d = await api("/api/identifiers");
  box.innerHTML = `${banner("<b>MHR and LHR are kept as unresolved internal identifiers.</b> The platform does not interpret them in any calculation, label or recommendation until an owner records a confirmed definition here. Evidence below shows where they appear in the existing files.", "warn")}
  ${d.items.map((u) => `<div class="card" style="margin-bottom:16px"><div class="card-h"><div><h3><code>${esc(u.code)}</code></h3><div class="sub">${u.occurrences} occurrences across the taxonomy</div></div>${pill(u.status)}</div>
    <div class="card-b">${u.status === "confirmed" ? `<dl class="kv"><dt>Definition</dt><dd>${esc(u.confirmed_definition)}</dd><dt>Confirmed by</dt><dd>${esc(u.confirmed_by)} · ${dt(u.confirmed_at)}</dd></dl>`
      : `<form class="row" data-code="${u.code}"><input class="input" name="definition" placeholder="Confirmed definition" style="flex:2" required><input class="input" name="by" placeholder="Confirmed by" style="flex:1" required><button class="btn primary">Record confirmation</button></form>`}
      <h4 style="margin:16px 0 8px;font-size:13px">Evidence (first ${u.evidence.length})</h4>
      ${table([{ label: "File", html: (e) => `<span class="mono small">${esc(e.file)}</span>` }, { label: "Context", html: (e) => `<span class="small">…${esc(e.context).replace(new RegExp(u.code, "gi"), (m) => `<mark>${m}</mark>`)}…</span>` }], u.evidence)}</div></div>`).join("")}`;
  $$("form[data-code]", box).forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault(); const fd = new FormData(f);
    try { await api(`/api/identifiers/${f.dataset.code}/confirm`, { method: "POST", body: { definition: fd.get("definition"), confirmed_by: fd.get("by") } }); toast("Definition recorded"); drawIdentifiers(box); } catch (err) { toast(err.message, true); }
  }));
}

async function datasetViewer(id, offset = 0, q = "") {
  const d = await api(`/api/datasets/${id}?limit=50&offset=${offset}&q=${encodeURIComponent(q)}`);
  const cols = (d.dataset.columns || []).slice(0, 14);
  const m = openModal(`<div class="drawer-h"><div><div class="eyebrow">${esc(d.dataset.role)} · ${esc(d.dataset.category || "root")}</div><h2 class="mono" style="font-size:15px">${esc(d.dataset.rel_path)}</h2>
    <div class="small muted">${nf.format(d.dataset.rows)} rows · ${d.dataset.columns.length} columns · sha256 ${esc(d.dataset.checksum.slice(0, 12))}… · imported ${dt(d.dataset.imported_at)}</div></div><button class="btn ghost" data-close>✕</button></div>
    <div class="drawer-b" style="padding:0"><div class="filters" style="padding:12px 16px 0"><input class="input" id="ds-q" placeholder="Search rows…" value="${esc(q)}" style="flex:1"></div>
    ${d.items.length ? table(cols.map((c) => ({ label: c, html: (r) => `<span class="small">${esc(r.data[c] ?? "")}</span>` })), d.items) : emptyState("No matching rows")}
    ${pager(d.total, offset, 50, (o) => datasetViewer(id, o, q))}${d.dataset.notes ? `<div style="padding:12px 16px"><pre class="json">${esc(d.dataset.notes)}</pre></div>` : ""}</div>`);
  m.querySelector("[data-close]").onclick = closeModal;
  m.querySelector("#ds-q").addEventListener("change", (e) => datasetViewer(id, 0, e.target.value));
}

/* =============================== MATERIAL INTELLIGENCE =============================== */
const compareSet = new Set();
route("materials", async (view, { sub, params }) => {
  const [fams, cats] = await Promise.all([api("/api/materials/families"), api("/api/taxonomy/categories")]);
  const state = { q: params.q || "", family: params.family || "", category: params.category || "", offset: 0 };
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Intelligence</div><h1>Material <em>Intelligence</em></h1>
    <p>Search raw materials by process, compare grades and designations, inspect specifications and price observations. Prices show their origin, date basis and verification status.</p></div>
    <div class="row"><button class="btn primary" id="cmp-btn" disabled>Compare (0)</button></div></div>
    <div class="filters"><input class="input" id="m-q" placeholder="Name, grade or designation (e.g. 6061, AISI 304, PA66)…" value="${esc(state.q)}" style="flex:1;min-width:240px">
      <select class="input" id="m-cat"><option value="">All processes</option>${cats.items.filter((c) => c.materials).map((c) => `<option value="${c.slug}" ${c.slug === state.category ? "selected" : ""}>${esc(c.name)} (${c.materials})</option>`).join("")}</select></div>
    <div class="chip-row" id="m-fams" style="margin-bottom:14px"><button class="chip ${!state.family ? "on" : ""}" data-f="">All</button>${fams.items.map((f) => `<button class="chip ${f.family === state.family ? "on" : ""}" data-f="${f.family}">${esc(f.label)} · ${f.count}</button>`).join("")}</div>
    <div class="card"><div class="card-b flush" id="m-table">${skeleton(8)}</div></div>`;
  const updateCmp = () => { const b = $("#cmp-btn"); b.textContent = `Compare (${compareSet.size})`; b.disabled = compareSet.size < 2; };
  updateCmp();
  $("#cmp-btn").onclick = () => compareView([...compareSet]);
  const load = async () => {
    const qs = new URLSearchParams({ q: state.q, family: state.family, category: state.category, limit: 50, offset: state.offset });
    [...qs.keys()].forEach((k) => qs.get(k) === "" && qs.delete(k));
    try {
      const d = await api("/api/materials?" + qs);
      $("#m-table").innerHTML = table([
        { label: "", html: (m) => `<input type="checkbox" data-cmp="${m.id}" ${compareSet.has(m.id) ? "checked" : ""} aria-label="compare">` },
        { label: "Material", html: (m) => `<b>${esc(m.name)}</b><div class="sub">${esc(m.material_type || "")}${m.grade ? " · grade " + esc(m.grade) : ""}</div>` },
        { label: "Family", html: (m) => `<span class="tag">${esc(m.family_label)}</span>` },
        { label: "Designations", html: (m) => Object.entries(m.designations).filter(([k]) => ["usa", "din", "en", "jis", "gb", "european"].includes(k)).slice(0, 3).map(([k, v]) => `<span class="tag" title="${esc(k)}">${esc(k.toUpperCase())}: ${esc(v)}</span>`).join("") || "—" },
        { label: "Processes", html: (m) => m.categories.slice(0, 3).map((c) => `<span class="tag teal">${esc(c.name)}</span>`).join("") + (m.categories.length > 3 ? `<span class="small muted"> +${m.categories.length - 3}</span>` : "") },
        { label: "Density", num: true, html: (m) => m.density_kg_m3 ? num(m.density_kg_m3) : "—" },
        { label: "Latest USD/kg", num: true, html: (m) => m.latest_price ? `${money(m.latest_price.value_usd)}<div class="sub">${esc(m.latest_price.country)} · ${esc(m.latest_price.period_date || "date n/a")}</div>` : '<span class="muted">no price</span>' },
      ], d.items, { onRow: (m) => materialDrawer(m.id, state.category), empty: "No materials match" }) + pager(d.total, state.offset, 50, (o) => { state.offset = o; load(); });
      $$("[data-cmp]").forEach((cb) => cb.addEventListener("change", () => { const id = +cb.dataset.cmp; cb.checked ? compareSet.size < 6 ? compareSet.add(id) : (cb.checked = false, toast("Compare up to 6 materials")) : compareSet.delete(id); updateCmp(); }));
    } catch (e) { $("#m-table").innerHTML = errorState(e); }
  };
  $("#m-q").addEventListener("input", (e) => { state.q = e.target.value; state.offset = 0; clearTimeout(load.t); load.t = setTimeout(load, 250); });
  $("#m-cat").addEventListener("change", (e) => { state.category = e.target.value; state.offset = 0; load(); });
  $("#m-fams").addEventListener("click", (e) => { const b = e.target.closest("[data-f]"); if (!b) return; state.family = b.dataset.f; state.offset = 0; $$("#m-fams .chip").forEach((x) => x.classList.toggle("on", x === b)); load(); });
  await load();
  if (sub[0]) materialDrawer(+sub[0], state.category);
});

async function materialDrawer(id, preferCategory) {
  const body = openDrawer(`<div class="eyebrow">Material</div><h2>Loading…</h2>`, skeleton(8));
  const m = await api(`/api/materials/${id}`);
  $("#drawer-title").innerHTML = `<div class="eyebrow">${esc(m.family_label)}</div><h2>${esc(m.name)}</h2><div class="small muted">${esc(m.material_type || "")} ${m.grade ? "· grade " + esc(m.grade) : ""} ${m.cut_code ? "· cut code " + esc(m.cut_code) : ""}</div>`;
  const props = Object.entries(m.properties || {});
  const countries = Object.entries(m.price_by_country || {});
  const cat = preferCategory && m.processes.some((p) => p.category === preferCategory) ? preferCategory : m.processes[0]?.category;
  body.innerHTML = `
    <div class="tabs" id="md-tabs"><button class="on" data-t="spec">Specification</button><button data-t="price">Prices (${m.prices.length})</button><button data-t="alt">Alternatives</button></div>
    <div id="md-spec"><h4 style="margin:0 0 8px;font-size:13px">Designations</h4><div>${Object.entries(m.designations).map(([k, v]) => `<span class="tag">${esc(k)}: ${esc(v)}</span>`).join("") || "—"}</div>
      <h4 style="margin:16px 0 8px;font-size:13px">Properties (as reported by the taxonomy files)</h4>
      ${props.length ? table([{ label: "Property", html: ([k]) => esc(k.replace(/_/g, " ")) }, { label: "Value", num: true, html: ([, v]) => esc(typeof v.value === "number" ? num(v.value, 4) : v.value) }, { label: "Unit", html: ([, v]) => esc(v.unit) }, { label: "Original header", html: ([, v]) => `<span class="small muted">${esc(v.original_header)}</span>` }], props) : emptyState("No properties recorded")}
      <h4 style="margin:16px 0 8px;font-size:13px">Manufacturing processes</h4>${m.processes.map((p) => `<span class="tag teal">${esc(p.name)}</span>${Object.entries(p.capabilities || {}).filter(([, v]) => String(v).toLowerCase() === "true").map(([k]) => `<span class="tag">${esc(k)}</span>`).join("")}`).join(" ") || "—"}</div>
    <div id="md-price" hidden>
      ${countries.length ? `<h4 style="margin:0 0 8px;font-size:13px">Latest USD/kg by country</h4><div id="md-cbar"></div>` : ""}
      ${m.history_points < 2 ? banner("Not enough dated observations for a trend line. Internal taxonomy prices are snapshots; historical trends appear once a time-series source (e.g. the World Bank Pink Sheet) covers this material.") : '<div id="md-hist" style="margin:12px 0"></div>'}
      ${table([{ label: "Value", num: true, html: (o) => `${esc(o.value)} <span class="sub">${esc(o.unit_original || "")}</span>` }, { label: "USD/kg", num: true, html: (o) => o.value_usd != null ? money(o.value_usd, 3) : "—" },
        { label: "Country", html: (o) => esc(o.country || "—") }, { label: "Period", html: (o) => esc(o.period_date || "unknown") }, { label: "Metric", html: (o) => `<span class="small">${esc(o.metric)}</span>` },
        { label: "Status", html: (o) => pill(o.status) + `<div>${flagTags(o.flags)}</div>` }], m.prices, { onRow: (o) => go(`data?obs=${o.id}`), empty: "No price observations" })}</div>
    <div id="md-alt" hidden></div>`;
  $("#md-tabs").onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; $$("#md-tabs button").forEach((x) => x.classList.toggle("on", x === b)); ["spec", "price", "alt"].forEach((t) => ($("#md-" + t).hidden = t !== b.dataset.t));
    if (b.dataset.t === "price" && $("#md-cbar") && !$("#md-cbar").innerHTML) Charts.hbar($("#md-cbar"), countries.map(([c, o]) => ({ label: c, value: o.value_usd, note: `${o.status} · ${o.period_date || "date n/a"}`, color: o.status === "verified" ? "#0d9e8a" : "#d97706" })), { prefix: "$" });
    if (b.dataset.t === "price" && $("#md-hist") && !$("#md-hist").innerHTML) { const byC = {}; m.prices.filter((o) => o.period_date && o.value_usd).forEach((o) => (byC[o.country] = byC[o.country] || []).push({ x: o.period_date, y: o.value_usd })); Charts.line($("#md-hist"), Object.entries(byC).map(([k, p]) => ({ name: k, points: p })), { height: 200 }); }
    if (b.dataset.t === "alt" && !$("#md-alt").innerHTML) alternativesPanel($("#md-alt"), m, cat); };
}

function alternativesPanel(box, m, cat) {
  if (!m.processes.length) { box.innerHTML = emptyState("No process context", "This material is not linked to any manufacturing category, so compatible alternatives cannot be determined."); return; }
  box.innerHTML = `<div class="form-grid"><div class="field"><label>Process</label><select class="input" id="alt-cat">${m.processes.map((p) => `<option value="${p.category}" ${p.category === cat ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>
    <div class="field"><label>Country for prices</label><select class="input" id="alt-country">${["USA", "IND", "CHN", "MEX", "DEU", "XWE", "XEE"].map((c) => `<option>${c}</option>`).join("")}</select></div>
    <div class="field"><label>Cost weight <span id="wc-v">0.4</span></label><input type="range" min="0" max="1" step="0.1" value="0.4" id="w-cost"></div>
    <div class="field"><label>Technical weight <span id="wt-v">0.4</span></label><input type="range" min="0" max="1" step="0.1" value="0.4" id="w-tech"></div>
    <div class="field"><label><input type="checkbox" id="alt-same" checked> Same material family only</label></div></div>
    <div id="alt-res" style="margin-top:12px">${skeleton(4)}</div>`;
  const run = async () => {
    $("#wc-v").textContent = $("#w-cost").value; $("#wt-v").textContent = $("#w-tech").value;
    const qs = new URLSearchParams({ category: $("#alt-cat").value, country: $("#alt-country").value, w_cost: $("#w-cost").value, w_technical: $("#w-tech").value, same_family: $("#alt-same").checked });
    try {
      const a = await api(`/api/materials/${m.id}/alternatives?${qs}`);
      $("#alt-res").innerHTML = a.notes.map((n) => `<div class="small muted">• ${esc(n)}</div>`).join("") +
        (a.reference.required_capabilities?.length ? `<div class="small" style="margin-top:6px">Required capabilities: ${a.reference.required_capabilities.map((c) => `<span class="tag">${esc(c)}</span>`).join("")}</div>` : "") +
        (Object.keys(a.excluded || {}).length ? `<div class="small muted" style="margin-top:4px">Excluded: ${Object.entries(a.excluded).map(([k, v]) => `${esc(k.replace(/_/g, " "))} ${v}`).join(" · ")}</div>` : "") +
        `<div style="margin-top:10px">${table([{ label: "Candidate", html: (x) => `<b>${esc(x.name)}</b><div class="sub">${esc(x.family_label)}</div>` },
          { label: "Score", num: true, html: (x) => `<b>${(x.score * 100).toFixed(0)}</b>` },
          { label: "Cost", num: true, html: (x) => x.factors.cost == null ? '<span class="muted">n/a</span>' : (x.factors.cost * 100).toFixed(0) },
          { label: "Technical", num: true, html: (x) => x.factors.technical == null ? '<span class="muted">n/a</span>' : (x.factors.technical * 100).toFixed(0) },
          { label: "USD/kg", num: true, html: (x) => x.price ? money(x.price.value_usd) : "—" },
          { label: "Compared on", html: (x) => `<span class="small muted">${esc(x.compared_properties.join(", ") || "price only")}</span>` }], a.items, { onRow: (x) => materialDrawer(x.id, $("#alt-cat").value), empty: "No compatible alternatives with comparable data" })}</div>`;
    } catch (e) { $("#alt-res").innerHTML = errorState(e); }
  };
  ["alt-cat", "alt-country", "w-cost", "w-tech", "alt-same"].forEach((i) => $("#" + i).addEventListener("change", run));
  run();
}

async function compareView(ids) {
  const c = await api(`/api/materials/compare?ids=${ids.join(",")}`);
  const mats = c.materials;
  const m = openModal(`<div class="drawer-h"><div><div class="eyebrow">Grade & specification comparison</div><h2>${mats.length} materials</h2></div><div class="row"><button class="btn sm" id="cmp-clear">Clear selection</button><button class="btn ghost" data-close>✕</button></div></div>
    <div class="drawer-b" style="padding:0"><div class="table-wrap"><table class="t"><thead><tr><th>Attribute</th>${mats.map((x) => `<th>${esc(x.name)}</th>`).join("")}</tr></thead><tbody>
    <tr><td class="muted">Family</td>${mats.map((x) => `<td>${esc(x.family_label)}</td>`).join("")}</tr>
    <tr><td class="muted">Grade</td>${mats.map((x) => `<td>${esc(x.grade || "—")}</td>`).join("")}</tr>
    <tr><td class="muted">Processes</td>${mats.map((x) => `<td>${x.processes.map((p) => `<span class="tag teal">${esc(p.name)}</span>`).join("")}</td>`).join("")}</tr>
    <tr><td class="muted">USD/kg (USA)</td>${mats.map((x) => `<td class="num">${x.price_by_country?.USA ? money(x.price_by_country.USA.value_usd) : "—"}</td>`).join("")}</tr>
    <tr><td class="muted">USD/kg (IND)</td>${mats.map((x) => `<td class="num">${x.price_by_country?.IND ? money(x.price_by_country.IND.value_usd) : "—"}</td>`).join("")}</tr>
    ${c.designations.map((d) => `<tr><td class="muted">${esc(d.standard)}</td>${d.values.map((v) => `<td>${esc(v || "—")}</td>`).join("")}</tr>`).join("")}
    ${c.properties.map((p) => { const nums = p.values.filter((v) => typeof v === "number"); const best = nums.length > 1 ? Math.max(...nums) : null;
      return `<tr><td class="muted">${esc(p.property.replace(/_/g, " "))} <span class="sub">${esc(p.unit || "")}</span></td>${p.values.map((v) => `<td class="num" style="${v === best ? "font-weight:700" : ""}">${v == null ? "—" : esc(typeof v === "number" ? num(v, 3) : v)}</td>`).join("")}</tr>`; }).join("")}
    </tbody></table></div></div>`);
  m.querySelector("[data-close]").onclick = closeModal;
  m.querySelector("#cmp-clear").onclick = () => { compareSet.clear(); closeModal(); render(); };
}

/* =============================== MARKET INTELLIGENCE =============================== */
route("market", async (view) => {
  const [fx, ind, mx, lab, fr, bm] = await Promise.all([api("/api/market/fx"), api("/api/market/indicators"), api("/api/market/matrix"), api("/api/market/labor"), api("/api/market/freshness"), api("/api/market/benchmarks")]);
  const majors = ["INR", "EUR", "CNY", "JPY", "MXN", "KRW", "GBP", "CZK", "HUF", "MYR", "SGD", "CAD"];
  const pairs = fx.pairs.filter((p) => majors.includes(p.currency) && p.latest != null).sort((a, b) => majors.indexOf(a.currency) - majors.indexOf(b.currency));
  const anyReplay = pairs.some((p) => p.replayed);
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Intelligence</div><h1>Market <em>Intelligence</em></h1>
    <p>Currency-adjusted benchmarks, regional comparisons and indicator trends. Every value links back to its source; stale values are marked.</p></div></div>
    ${anyReplay ? banner("FX and indicator values shown here were produced by replaying real responses captured on 2026-10-27 (tagged <b>replayed</b>). Live refresh requires the worker with network access.", "teal") : ""}
    <div class="card"><div class="card-h"><h3>Exchange rates vs USD</h3><span class="sub">Frankfurter (ECB reference rates) · compared with the internal exchange_rate_table.csv</span></div>
      <div class="card-b">${pairs.length ? `<div class="grid g-6" id="fx-cards">${pairs.map((p) => `
        <div class="card kpi click" data-pair="${p.pair}" style="cursor:pointer;padding:12px 14px"><div class="label"><span>${esc(p.pair)}</span>${p.stale ? pill("stale") : ""}</div>
          <div class="value" style="font-size:20px">${num(p.latest, 4)}</div><div class="spark-box" data-pair="${p.pair}"></div>
          <div class="foot">30d <b style="color:${(p.change_30d_pct || 0) > 0 ? "var(--warn)" : "var(--teal-700)"}">${pct(p.change_30d_pct)}</b> · ${esc(p.latest_date)}</div>
          ${p.internal_table_rate != null && Math.abs(p.internal_vs_latest_pct) > 5 ? `<div class="small" style="color:var(--warn);margin-top:4px">Internal table ${num(p.internal_table_rate, 4)} (${esc(p.internal_table_date)}) differs ${pct(p.internal_vs_latest_pct)}</div>` : ""}</div>`).join("")}</div>
        <div id="fx-chart" style="margin-top:16px"></div>` : emptyState("No crawled FX data yet", "Run the frankfurter-fx source (and its backfill) from the Spider Manager.", '<a class="btn primary" href="#/spiders/frankfurter-fx">Open source</a>')}</div></div>
    <div class="grid g-2" style="margin-top:16px">
      <div class="card"><div class="card-h"><h3>Manufacturing value added (% of GDP)</h3><span class="sub">World Bank WDI · latest year per country</span></div><div class="card-b" id="ind-chart">${ind.series.length ? "" : emptyState("No indicator data yet", "Run worldbank-wdi-manufacturing.")}</div></div>
      <div class="card"><div class="card-h"><h3>Labour & overhead by country</h3><span class="sub">average USD/h across imported machine/work-center records</span></div><div class="card-b" id="lab-chart"></div></div></div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>Regional material benchmark</h3><span class="sub">median normalized USD/kg by material family × country (all verified & unverified observations; hover for count)</span></div><div class="card-b flush" id="matrix"></div></div>
    <div class="grid g-2" style="margin-top:16px">
      <div class="card"><div class="card-h"><h3>Commodity benchmarks (crawled)</h3></div><div class="card-b flush">${bm.items.length ? table([{ label: "Series", html: (r) => esc(r.entity) }, { label: "Points", num: true, html: (r) => num(r.points) }, { label: "Latest", html: (r) => esc(r.latest || "—") }], bm.items)
        : emptyState("No crawled commodity price series yet", "The World Bank Pink Sheet source (monthly metals, rubber, energy) is configured; run it on a host with network access.", '<a class="btn" href="#/spiders/worldbank-pink-sheet">Open source</a>')}</div></div>
      <div class="card"><div class="card-h"><h3>Data freshness</h3></div><div class="card-b flush">${table([{ label: "Source", html: (r) => `${esc(r.name)}<div class="sub">${(r.kinds || []).join(", ")}</div>` }, { label: "Status", html: (r) => pill(r.freshness) }, { label: "Latest period", html: (r) => esc(r.latest_period || "—") }, { label: "Verification", html: (r) => pill(r.verification_status) }], fr.items.filter((r) => r.freshness !== "n/a"))}</div></div></div>`;
  // FX sparklines + main chart
  pairs.forEach((p) => { const box = $(`.spark-box[data-pair="${p.pair}"]`); if (box) Charts.spark(box, p.points.map((x) => x.value)); });
  const drawFx = (pair) => { const p = pairs.find((x) => x.pair === pair); if (!p) return; $$("#fx-cards .kpi").forEach((k) => (k.style.outline = k.dataset.pair === pair ? "2px solid var(--teal)" : "none"));
    $("#fx-chart").innerHTML = `<div class="small muted" style="margin-bottom:6px">${esc(pair)} — ${p.points.length} daily observations</div><div id="fx-line"></div>`; Charts.line($("#fx-line"), [{ name: pair, points: p.points.map((x) => ({ x: x.date, y: x.value })) }], { height: 220 }); };
  if (pairs.length) { drawFx((pairs.find((p) => p.currency === "INR") || pairs[0]).pair); $("#fx-cards").addEventListener("click", (e) => { const k = e.target.closest("[data-pair]"); if (k) drawFx(k.dataset.pair); }); }
  // indicators
  if (ind.series.length) {
    const latest = ind.series.filter((s) => s.metric === "NV.IND.MANF.ZS").map((s) => ({ label: s.country, value: s.latest.value, note: `${s.latest.date.slice(0, 4)} · ${s.latest.flags.join(", ")}` })).sort((a, b) => b.value - a.value);
    $("#ind-chart").innerHTML = '<div id="ind-bars"></div><div id="ind-lines" style="margin-top:12px"></div>';
    Charts.hbar($("#ind-bars"), latest, { suffix: "%", labelWidth: 60 });
    const ls = ind.series.filter((s) => s.metric === "NV.IND.MANF.ZS");
    Charts.line($("#ind-lines"), ls.map((s) => ({ name: s.country, points: s.points.map((p) => ({ x: p.date, y: p.value })) })), { height: 200 });
    $("#ind-lines").insertAdjacentHTML("beforeend", `<div class="legend">${ls.map((s, i) => `<span><i style="background:${Charts.PALETTE[i % 8]}"></i>${esc(s.country)}</span>`).join("")}</div>`);
  }
  Charts.hbar($("#lab-chart"), lab.items.filter((r) => r.avg_labor_usd_hr).map((r) => ({ label: r.country_name, value: r.avg_labor_usd_hr, note: `${nf.format(r.machines)} records · overhead ${money(r.avg_direct_overhead_usd_hr)}/h direct` })), { prefix: "$", suffix: "/h" });
  // matrix
  const cells = {}; mx.cells.forEach((c) => (cells[c.family + "|" + c.country] = c));
  const cs = mx.countries.slice(0, 9), fs = mx.families.slice(0, 16);
  const vals = mx.cells.map((c) => Math.log(c.median + 1e-9)); const lo = Math.min(...vals), hi = Math.max(...vals);
  $("#matrix").innerHTML = mx.cells.length ? `<div class="table-wrap"><table class="t heat"><thead><tr><th>Family</th>${cs.map((c) => `<th class="num">${esc(c)}</th>`).join("")}</tr></thead><tbody>${fs.map((f) => `<tr><td><b>${esc(f.replace(/_/g, " "))}</b></td>${cs.map((c) => {
    const v = cells[f + "|" + c]; if (!v) return '<td class="cell muted">·</td>'; const t = (Math.log(v.median + 1e-9) - lo) / ((hi - lo) || 1);
    return `<td class="cell" title="n=${v.n}" style="background:rgba(13,158,138,${(0.06 + t * 0.4).toFixed(2)})">${money(v.median)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>`
    : emptyState("No normalized prices");
});

/* =============================== DATA EXPLORER =============================== */
route("data", async (view, { params }) => {
  const tab = params.tab || "obs";
  const st = { kind: params.kind || "", status: params.status || "", origin: params.origin || "", country: "", flag: params.flag || "", q: "", source: params.source || "", offset: 0 };
  view.innerHTML = `<div class="page-head"><div><div class="eyebrow">Data operations</div><h1>Data <em>Explorer</em></h1>
    <p>Inspect normalized records next to their original values, source references, extraction timestamps and validation results.</p></div></div>
    <div class="tabs" id="dx-tabs">${[["obs", "Observations"], ["issues", "Validation issues"], ["docs", "Documents & news"], ["std", "Standards"]].map(([k, l]) => `<button data-t="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div>
    <div id="dx-body"></div>`;
  $("#dx-tabs").onclick = (e) => { const b = e.target.closest("[data-t]"); if (b) go(`data?tab=${b.dataset.t}`); };
  const body = $("#dx-body");
  if (tab === "obs") {
    body.innerHTML = `<div class="filters"><input class="input" id="o-q" placeholder="Entity or metric…" style="flex:1;min-width:200px">
      <select class="input" id="o-kind"><option value="">All kinds</option>${["price", "fx", "indicator", "trade", "labor_rate", "machine_rate", "property"].map((k) => `<option ${k === st.kind ? "selected" : ""}>${k}</option>`).join("")}</select>
      <select class="input" id="o-status"><option value="">All statuses</option>${["verified", "unverified", "quarantined", "superseded"].map((k) => `<option ${k === st.status ? "selected" : ""}>${k}</option>`).join("")}</select>
      <select class="input" id="o-origin"><option value="">All origins</option><option value="crawled" ${st.origin === "crawled" ? "selected" : ""}>Crawled</option><option value="internal" ${st.origin === "internal" ? "selected" : ""}>Internal taxonomy</option></select>
      <input class="input" id="o-country" placeholder="ISO3" style="width:90px"><input class="input" id="o-flag" placeholder="Flag (e.g. replayed)" style="width:150px" value="${esc(st.flag)}">
      ${st.source ? `<span class="tag teal">source: ${esc(st.source)}</span>` : ""}</div>
      <div class="card"><div class="card-b flush" id="o-table">${skeleton(10)}</div></div>`;
    const load = async () => {
      const qs = new URLSearchParams({ ...st, limit: 50 }); [...qs.keys()].forEach((k) => qs.get(k) === "" && qs.delete(k));
      try {
        const d = await api("/api/data/observations?" + qs);
        $("#o-table").innerHTML = table([
          { label: "Kind", html: (o) => `<span class="tag">${esc(o.kind)}</span>` },
          { label: "Entity / metric", html: (o) => `<b>${esc(o.entity)}</b><div class="sub">${esc(o.metric)}</div>` },
          { label: "Original", num: true, html: (o) => `${o.value != null ? num(o.value, 6) : esc(o.value_text ?? "—")}<div class="sub">${esc(o.unit || "")}</div>` },
          { label: "Normalized", num: true, html: (o) => o.value_usd != null ? `${num(o.value_usd, 4)}<div class="sub">${esc(o.unit_normalized || "")}</div>` : "—" },
          { label: "Country", html: (o) => esc(o.country || "—") }, { label: "Period", html: (o) => `<span class="nowrap">${esc(o.period_date || "—")}</span>` },
          { label: "Status", html: (o) => `${pill(o.status)}<div class="sub">conf. ${Math.round(o.confidence * 100)}%</div>` },
          { label: "Origin", html: (o) => o.source ? `<span class="tag teal">${esc(o.source)}</span>` : `<span class="mono small truncate" style="max-width:200px;display:inline-block" title="${esc(o.dataset)}">${esc(o.dataset || "—")}</span>` },
          { label: "Flags", html: (o) => flagTags(o.flags) },
          { label: "Extracted", html: (o) => `<span class="small nowrap">${esc(ago(o.observed_at))}</span>` }], d.items, { onRow: (o) => obsDrawer(o.id), empty: "No observations match" })
          + pager(d.total, st.offset, 50, (o) => { st.offset = o; load(); });
      } catch (e) { $("#o-table").innerHTML = errorState(e); }
    };
    [["o-q", "q"], ["o-kind", "kind"], ["o-status", "status"], ["o-origin", "origin"], ["o-country", "country"], ["o-flag", "flag"]].forEach(([id, k]) =>
      $("#" + id).addEventListener($("#" + id).tagName === "SELECT" ? "change" : "input", (e) => { st[k] = id === "o-country" ? e.target.value.toUpperCase() : e.target.value; st.offset = 0; clearTimeout(load.t); load.t = setTimeout(load, 250); }));
    await load();
    if (params.obs) obsDrawer(+params.obs);
  } else if (tab === "issues") {
    const s2 = { severity: "", code: "", offset: 0 };
    body.innerHTML = `<div class="filters"><select class="input" id="i-sev"><option value="">All severities</option><option>error</option><option>warning</option><option>info</option></select><div class="chip-row" id="i-codes"></div></div><div class="card"><div class="card-b flush" id="i-table">${skeleton(8)}</div></div>`;
    const load = async () => {
      const qs = new URLSearchParams({ ...s2, limit: 50 }); [...qs.keys()].forEach((k) => qs.get(k) === "" && qs.delete(k));
      const d = await api("/api/data/issues?" + qs);
      $("#i-codes").innerHTML = `<button class="chip ${!s2.code ? "on" : ""}" data-c="">All codes</button>` + Object.entries(d.codes).map(([c, n]) => `<button class="chip ${c === s2.code ? "on" : ""}" data-c="${c}">${esc(c)} · ${n}</button>`).join("");
      $("#i-table").innerHTML = table([{ label: "Severity", html: (i) => pill(i.severity) }, { label: "Code", html: (i) => `<code>${esc(i.code)}</code>` }, { label: "Message", html: (i) => esc(i.message) },
        { label: "Where", html: (i) => i.dataset ? `<span class="mono small">${esc(i.dataset)}</span>` : i.job_id ? `job #${i.job_id}` : "—" }, { label: "Context", html: (i) => `<span class="small muted truncate" style="max-width:260px;display:inline-block">${esc(JSON.stringify(i.context))}</span>` },
        { label: "When", html: (i) => `<span class="small nowrap">${esc(ago(i.created_at))}</span>` }], d.items, { onRow: (i) => i.observation_id && obsDrawer(i.observation_id), empty: "No validation issues" }) + pager(d.total, s2.offset, 50, (o) => { s2.offset = o; load(); });
    };
    $("#i-sev").onchange = (e) => { s2.severity = e.target.value; s2.offset = 0; load(); };
    $("#i-codes").onclick = (e) => { const b = e.target.closest("[data-c]"); if (b) { s2.code = b.dataset.c; s2.offset = 0; load(); } };
    await load();
  } else if (tab === "docs") {
    const d = await api("/api/data/documents?limit=100");
    body.innerHTML = `<div class="card"><div class="card-b flush">${table([{ label: "Kind", html: (x) => `<span class="tag">${esc(x.kind)}</span>` }, { label: "Title", html: (x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a><div class="sub">${esc((x.authors || []).slice(0, 3).join(", "))}</div>` },
      { label: "Published", html: (x) => `<span class="nowrap">${esc((x.published_at || "").slice(0, 10) || "—")}</span>` }, { label: "Source", html: (x) => `<span class="tag teal">${esc(x.source || "—")}</span>` }, { label: "Flags", html: (x) => flagTags(x.flags) }, { label: "Extracted", html: (x) => `<span class="small">${esc(ago(x.observed_at))}</span>` }],
      d.items, { empty: "No documents yet — run a research or news source" })}</div></div>`;
  } else {
    const d = await api("/api/data/standards");
    body.innerHTML = `<div class="card"><div class="card-b flush">${table([{ label: "Body", html: (x) => `<span class="tag">${esc(x.body)}</span>` }, { label: "Code", html: (x) => `<b>${esc(x.code)}</b>` }, { label: "Title", html: (x) => esc(x.title || "—") }, { label: "Status", html: (x) => esc(x.status || "—") }, { label: "Origin", html: (x) => x.source_id ? "crawled" : "taxonomy file" }], d.items, { empty: "No standards metadata yet" })}</div></div>`;
  }
});

async function obsDrawer(id) {
  const body = openDrawer(`<div class="eyebrow">Observation #${id}</div><h2>Loading…</h2>`, skeleton(8));
  const o = await api(`/api/data/observations/${id}`);
  $("#drawer-title").innerHTML = `<div class="eyebrow">${esc(o.kind)} · observation #${o.id}</div><h2>${esc(o.entity)}</h2><div class="row" style="margin-top:6px">${pill(o.status)} <span class="small muted">confidence ${Math.round(o.confidence * 100)}%</span> ${flagTags(o.flags)}</div>`;
  body.innerHTML = `<div class="grid g-2"><div class="card kpi"><div class="label">Original value</div><div class="value" style="font-size:22px">${esc(o.value ?? o.value_text)}</div><div class="foot">${esc(o.unit || "no unit")} ${o.currency ? "· " + esc(o.currency) : ""}</div></div>
    <div class="card kpi"><div class="label">Normalized</div><div class="value" style="font-size:22px">${o.value_usd != null ? num(o.value_usd, 4) : "—"}</div><div class="foot">${esc(o.unit_normalized || "not normalized")}${o.original?.conversion?.fx_rate ? ` · FX ${o.original.conversion.fx_rate} (${esc(o.original.conversion.fx_as_of)})` : ""}</div></div></div>
    <dl class="kv" style="margin-top:16px"><dt>Metric</dt><dd>${esc(o.metric)}</dd><dt>Country</dt><dd>${esc(o.country || "—")}</dd><dt>Period</dt><dd>${esc(o.period_date || "not stated by source")}</dd>
      <dt>Extracted at</dt><dd>${dt(o.observed_at)}</dd><dt>Series key</dt><dd><code class="small">${esc(o.series_key)}</code></dd>
      ${o.source ? `<dt>Source</dt><dd><a href="#/spiders/${o.source.slug}" onclick="closeDrawer()">${esc(o.source.name)}</a> ${pill(o.source.verification_status)}<div class="small muted">${esc(o.source.license || "")}${o.source.attribution ? " · " + esc(o.source.attribution) : ""}</div></dd>` : ""}
      ${o.source_url ? `<dt>Source URL</dt><dd><a class="mono small" href="${esc(o.source_url)}" target="_blank" rel="noopener">${esc(o.source_url)}</a></dd>` : ""}
      ${o.dataset ? `<dt>Dataset</dt><dd><a href="#" id="open-ds" class="mono small">${esc(o.dataset.rel_path)}</a> (${esc(o.dataset.role)})</dd>` : ""}
      ${o.material ? `<dt>Material</dt><dd><a href="#/materials/${o.material.id}" onclick="closeDrawer()">${esc(o.material.name)}</a> · ${esc(o.material.family_label)}</dd>` : ""}
      ${o.job_id ? `<dt>Crawl job</dt><dd>#${o.job_id}</dd>` : ""}</dl>
    ${o.issues.length ? `<h4 style="margin:16px 0 8px;font-size:13px">Validation</h4>${o.issues.map((i) => `<div class="row">${pill(i.severity)} <code>${esc(i.code)}</code> <span class="small">${esc(i.message)}</span></div>`).join("")}` : ""}
    <h4 style="margin:16px 0 8px;font-size:13px">Original record</h4><pre class="json">${esc(JSON.stringify(o.source_row || o.original, null, 2))}</pre>
    ${o.history.length > 1 ? `<h4 style="margin:16px 0 8px;font-size:13px">Series history (${o.history.length})</h4>${table([{ label: "Period", html: (h) => esc(h.period_date || "—") }, { label: "Value", num: true, html: (h) => esc(h.value) }, { label: "Status", html: (h) => pill(h.status) }, { label: "Extracted", html: (h) => `<span class="small">${esc(ago(h.observed_at))}</span>` }], o.history.slice(0, 20))}` : ""}`;
  $("#open-ds")?.addEventListener("click", (e) => { e.preventDefault(); datasetViewer(o.dataset.id); });
}

/* =============================== BOOT =============================== */
buildNav();
$("#drawer-close").onclick = closeDrawer; $("#drawer-back").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); closeModal(); } });
$("#menu-btn").onclick = () => $("#sidebar").classList.toggle("open");
$("#global-search").addEventListener("keydown", (e) => { if (e.key === "Enter") go(`materials?q=${encodeURIComponent(e.target.value)}`); });
window.addEventListener("hashchange", render);
render(); refreshChrome(); setInterval(refreshChrome, 15000);
