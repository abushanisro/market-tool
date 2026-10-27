/* Core: API client, router, shared UI helpers. No framework, no build step. */
"use strict";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("en-US");
const num = (v, d = 0) => v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
const money = (v, d = 2) => v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now"; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
const dt = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

const ADMIN = { token: (() => { try { return localStorage.getItem("mip_admin_token") || ""; } catch { return ""; } })() };
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (ADMIN.token) headers["X-Admin-Token"] = ADMIN.token;
  const res = await fetch(path, { ...opts, headers, body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return res.json();
}

function toast(msg, isErr = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = "toast"), 3600);
}

/* ---------- icons ---------- */
const I = {
  overview: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"/>',
  spider: '<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/>',
  sources: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  taxonomy: '<path d="M3 3h6v6H3zM15 15h6v6h-6zM15 3h6v6h-6zM6 9v3h12V9M18 12v3"/>',
  material: '<path d="M12 2 3 7v10l9 5 9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/>',
  market: '<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 6-7"/>',
  data: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  empty: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  play: '<path d="m6 4 14 8-14 8z"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>', ext: '<path d="M14 3h7v7M10 14 21 3M21 14v7H3V3h7"/>',
};
const icon = (name, attrs = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ${attrs}>${I[name] || ""}</svg>`;

/* ---------- state components ---------- */
const skeleton = (rows = 4, h = 18) => `<div class="stack" style="gap:10px">${Array.from({ length: rows }, () => `<div class="skeleton" style="height:${h}px"></div>`).join("")}</div>`;
const emptyState = (title, body = "", action = "") => `<div class="empty">${icon("empty")}<h4>${esc(title)}</h4><div class="small">${body}</div>${action ? `<div style="margin-top:12px">${action}</div>` : ""}</div>`;
const errorState = (err, retry = "") => `<div class="error-state">${icon("alert")}<h4>Couldn't load this data</h4><div class="small">${esc(err.message || err)}</div>${retry ? `<div style="margin-top:12px"><button class="btn sm" data-retry="${retry}">${icon("refresh")} Retry</button></div>` : ""}</div>`;
const banner = (html, kind = "") => `<div class="banner ${kind}">${icon(kind === "warn" ? "alert" : "info")}<div>${html}</div></div>`;

const STATUS_CLASS = {
  verified: "ok", succeeded: "ok", fresh: "ok", active: "ok", url_verified: "ok", documented: "info", running: "info", starting: "info",
  queued: "info", unverified: "warn", partial: "warn", stale: "warn", paused: "warn", planned: "plain", key_required: "warn", never: "plain",
  quarantined: "err", failed: "err", failing: "err", blocked_by_terms: "err", disabled: "err", cancelled: "plain", superseded: "plain",
  "n/a": "plain", unresolved: "warn", confirmed: "ok", error: "err", warning: "warn", info: "info",
};
const pill = (v, label) => `<span class="pill ${STATUS_CLASS[v] || ""}">${esc(label ?? String(v).replace(/_/g, " "))}</span>`;
const flagTags = (flags = []) => flags.map((f) => `<span class="tag ${f === "replayed" ? "teal" : ""}" title="${esc(FLAG_HELP[f] || f)}">${esc(f.replace(/_/g, " "))}</span>`).join("");
const FLAG_HELP = {
  replayed: "Produced from a recorded real response (offline replay), not a live request",
  as_of_unknown: "The source does not state the date of this value",
  period_from_release_metadata: "Date taken from the digital factory release (aP2025R1 2026-03)",
  country_from_digital_factory_default: "Country inferred from the aPriori USA digital factory",
  fx_fallback_internal_table: "Converted with the internal exchange_rate_table (may be outdated)",
  fx_internal_table: "Rate from exchange_rate_table.csv", non_mass_unit: "Price is per area/piece, not per kg",
  unit_not_stated_in_source: "Source gives a cost without a unit", large_change: "≥35% change vs previous value",
  conflict: "Another source reports a materially different value", revision: "Replaces an earlier value for the same period",
  implausible_price: "Outside the plausible USD/kg band for this material family", unit_unknown: "Unit could not be normalized",
};

/* ---------- drawer / modal ---------- */
function openDrawer(titleHtml, bodyHtml) {
  $("#drawer-title").innerHTML = titleHtml; $("#drawer-body").innerHTML = bodyHtml;
  $("#drawer").classList.add("open"); $("#drawer-back").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
  return $("#drawer-body");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#drawer-back").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); if (closeDrawer.onClose) { closeDrawer.onClose(); closeDrawer.onClose = null; } }
function openModal(html) { $("#modal-card").innerHTML = html; $("#modal").classList.add("open"); return $("#modal-card"); }
function closeModal() { $("#modal").classList.remove("open"); }

/* ---------- table helper ---------- */
function table(cols, rows, { onRow, empty = "No rows", selected } = {}) {
  if (!rows.length) return emptyState(empty);
  const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((r, i) => `<tr class="${onRow ? "click" : ""} ${selected && selected(r) ? "sel" : ""}" data-i="${i}">${cols.map((c) =>
    `<td class="${c.num ? "num" : ""} ${c.cls || ""}">${c.html ? c.html(r) : esc(r[c.key] ?? "—")}</td>`).join("")}</tr>`).join("");
  const id = "t" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => { const t = document.getElementById(id); if (t && onRow) t.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-i]"); if (tr && !e.target.closest("a,button,input,select,label")) onRow(rows[+tr.dataset.i], tr); }); });
  return `<div class="table-wrap"><table class="t" id="${id}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function pager(total, offset, limit, onPage) {
  const id = "p" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => { const p = document.getElementById(id); if (!p) return;
    p.querySelector("[data-prev]")?.addEventListener("click", () => onPage(Math.max(0, offset - limit)));
    p.querySelector("[data-next]")?.addEventListener("click", () => onPage(offset + limit)); });
  return `<div class="pager" id="${id}"><span>${total ? `${nf.format(offset + 1)}–${nf.format(Math.min(total, offset + limit))} of ${nf.format(total)}` : "0 results"}</span>
    <span class="row"><button class="btn sm" data-prev ${offset <= 0 ? "disabled" : ""}>Previous</button><button class="btn sm" data-next ${offset + limit >= total ? "disabled" : ""}>Next</button></span></div>`;
}

/* ---------- router ---------- */
const ROUTES = [];
const NAV = [
  { group: "Intelligence" },
  { path: "overview", label: "Overview", icon: "overview" },
  { path: "materials", label: "Material Intelligence", icon: "material" },
  { path: "market", label: "Market Intelligence", icon: "market" },
  { group: "Data operations" },
  { path: "spiders", label: "Spider Manager", icon: "spider" },
  { path: "sources", label: "Source Explorer", icon: "sources" },
  { path: "data", label: "Data Explorer", icon: "data" },
  { group: "Reference" },
  { path: "taxonomy", label: "Manufacturing Taxonomy", icon: "taxonomy" },
  { group: "System" },
  { path: "settings", label: "Settings", icon: "settings" },
];
const timers = new Set();
function every(ms, fn) { const id = setInterval(fn, ms); timers.add(id); return id; }
function route(path, render) { ROUTES.push({ path, render }); }
function parseHash() {
  const h = location.hash.replace(/^#\/?/, "") || "overview";
  const [path, qs] = h.split("?");
  return { path: path.split("/")[0], sub: path.split("/").slice(1), params: Object.fromEntries(new URLSearchParams(qs || "")) };
}
function go(path) { location.hash = "#/" + path; }
async function render() {
  timers.forEach(clearInterval); timers.clear(); Charts.hideTip(); closeModal(); closeDrawer();
  const { path, sub, params } = parseHash();
  const r = ROUTES.find((x) => x.path === path) || ROUTES[0];
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.path === r.path));
  $("#sidebar").classList.remove("open");
  const view = $("#view"); view.innerHTML = skeleton(6, 22); window.scrollTo(0, 0);
  try { await r.render(view, { sub, params }); } catch (e) { console.error(e); view.innerHTML = errorState(e); }
}
function buildNav() {
  $("#nav").innerHTML = NAV.map((n) => n.group ? `<div class="nav-group">${n.group}</div>`
    : `<a href="#/${n.path}" data-path="${n.path}">${icon(n.icon)}<span>${n.label}</span></a>`).join("");
}
async function refreshChrome() {
  try {
    const o = await api("/api/overview");
    $("#ticker").innerHTML = `Records <b>${nf.format(o.records.observations + o.records.documents)}</b> · Sources <b>${o.sources.active}/${o.sources.total}</b> · Spiders running <b>${o.spiders.running}</b>`;
    $("#sidebar-foot").innerHTML = `<span class="dot"></span>API online · last refresh ${esc(ago(o.last_successful_refresh))}`;
  } catch (e) { $("#sidebar-foot").innerHTML = `<span class="dot" style="background:#f85149"></span>API unreachable`; }
}
document.addEventListener("click", (e) => {
  const r = e.target.closest("[data-retry]"); if (r) render();
  if (e.target.id === "modal") closeModal();
});
