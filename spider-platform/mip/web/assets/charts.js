/* Minimal dependency-free SVG charts (line, bar, sparkline) with hover tooltips. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const PALETTE = ["#0d9e8a", "#475569", "#d97706", "#2563eb", "#7c3aed", "#e11d48", "#0891b2", "#65a30d"];
  const tip = () => document.getElementById("tip");
  const el = (n, a = {}, p) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  const fmt = (v) => v == null ? "—" : Math.abs(v) >= 1e9 ? (v / 1e9).toFixed(1) + "B" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + "M"
    : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(0) + "k" : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3);
  function showTip(evt, html) { const t = tip(); t.innerHTML = html; t.style.display = "block"; t.style.left = (evt.clientX + 14) + "px"; t.style.top = (evt.clientY + 12) + "px"; }
  function hideTip() { tip().style.display = "none"; }
  function ticks(min, max, n = 4) {
    if (min === max) { min -= 1; max += 1; }
    const step = Math.pow(10, Math.floor(Math.log10((max - min) / n)));
    const err = (max - min) / n / step; const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    const s = step * mult, out = []; for (let v = Math.ceil(min / s) * s; v <= max + 1e-9; v += s) out.push(+v.toFixed(10)); return out;
  }

  /** series: [{name, points:[{x: Date|string, y: number}]}] */
  function line(container, series, opts = {}) {
    container.innerHTML = "";
    const W = container.clientWidth || 640, H = opts.height || 240, m = { t: 12, r: 16, b: 26, l: 48 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": opts.label || "line chart" }, container);
    const all = series.flatMap((s) => s.points.map((p) => ({ ...p, x: new Date(p.x) })));
    if (!all.length) return;
    let xmin = Math.min(...all.map((p) => +p.x)), xmax = Math.max(...all.map((p) => +p.x));
    if (xmin === xmax) { xmin -= 864e5; xmax += 864e5; }
    let ymin = Math.min(...all.map((p) => p.y)), ymax = Math.max(...all.map((p) => p.y));
    if (opts.zero) ymin = Math.min(0, ymin);
    const pad = (ymax - ymin) * 0.08 || Math.abs(ymax) * 0.05 || 1; ymin -= pad; ymax += pad;
    const X = (d) => m.l + ((+d - xmin) / (xmax - xmin)) * (W - m.l - m.r), Y = (v) => H - m.b - ((v - ymin) / (ymax - ymin)) * (H - m.t - m.b);
    const g = el("g", { class: "grid" }, svg);
    ticks(ymin, ymax).forEach((v) => { el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g); const t = el("text", { x: m.l - 6, y: Y(v) + 3, "text-anchor": "end" }, svg); t.textContent = fmt(v); });
    const nx = Math.min(6, Math.max(2, Math.floor(W / 110)));
    const seenLbl = new Set();
    for (let i = 0; i <= nx; i++) { const d = new Date(xmin + (xmax - xmin) * i / nx);
      const lbl = (xmax - xmin) > 400 * 864e5 ? String(d.getFullYear()) : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      if (seenLbl.has(lbl)) continue; seenLbl.add(lbl);
      const t = el("text", { x: X(d), y: H - 8, "text-anchor": i === 0 ? "start" : i === nx ? "end" : "middle" }, svg); t.textContent = lbl; }
    series.forEach((s, i) => {
      const color = s.color || PALETTE[i % PALETTE.length];
      const pts = s.points.map((p) => ({ x: new Date(p.x), y: p.y, raw: p })).sort((a, b) => a.x - b.x);
      el("path", { d: pts.map((p, j) => `${j ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(""), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
      if (pts.length < 40) pts.forEach((p) => el("circle", { cx: X(p.x), cy: Y(p.y), r: 2.6, fill: color }, svg));
    });
    // hover crosshair
    const hl = el("line", { y1: m.t, y2: H - m.b, stroke: "#94a3b8", "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
    const ov = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
    ov.addEventListener("mousemove", (evt) => {
      const r = svg.getBoundingClientRect(); const px = (evt.clientX - r.left) * (W / r.width);
      const t = xmin + ((px - m.l) / (W - m.l - m.r)) * (xmax - xmin);
      let html = "", xs = null;
      series.forEach((s, i) => { let best = null; s.points.forEach((p) => { const d = Math.abs(+new Date(p.x) - t); if (!best || d < best.d) best = { d, p }; });
        if (best) { xs = new Date(best.p.x); html += `<div><span style="color:${s.color || PALETTE[i % PALETTE.length]}">●</span> ${s.name || ""}: <b>${fmt(best.p.y)}</b></div>`; } });
      if (xs) { hl.setAttribute("x1", X(xs)); hl.setAttribute("x2", X(xs)); hl.setAttribute("visibility", "visible"); showTip(evt, `<div style="opacity:.7">${xs.toISOString().slice(0, 10)}</div>${html}`); }
    });
    ov.addEventListener("mouseleave", () => { hl.setAttribute("visibility", "hidden"); hideTip(); });
  }

  /** data: [{label, value, color?, note?}] horizontal bars */
  function hbar(container, data, opts = {}) {
    container.innerHTML = "";
    if (!data.length) return;
    const W = container.clientWidth || 520, rowH = opts.rowH || 26, lw = opts.labelWidth || 150, H = data.length * rowH + 8;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": opts.label || "bar chart" }, container);
    const max = Math.max(...data.map((d) => d.value || 0)) || 1;
    data.forEach((d, i) => {
      const y = i * rowH + 4, w = Math.max(1, ((d.value || 0) / max) * (W - lw - 70));
      const t = el("text", { x: lw - 8, y: y + rowH / 2 + 2, "text-anchor": "end" }, svg); t.textContent = d.label.length > 24 ? d.label.slice(0, 23) + "…" : d.label;
      const r = el("rect", { x: lw, y: y + 4, width: w, height: rowH - 10, rx: 3, fill: d.color || PALETTE[0] }, svg);
      const v = el("text", { x: lw + w + 6, y: y + rowH / 2 + 2 }, svg); v.textContent = (opts.prefix || "") + fmt(d.value) + (opts.suffix || "");
      r.addEventListener("mousemove", (e) => showTip(e, `<b>${d.label}</b><br>${(opts.prefix || "") + fmt(d.value) + (opts.suffix || "")}${d.note ? "<br>" + d.note : ""}`));
      r.addEventListener("mouseleave", hideTip);
    });
  }

  function spark(container, values, color = PALETTE[0]) {
    container.innerHTML = "";
    if (!values || values.length < 2) { container.innerHTML = '<div class="small muted">no history</div>'; return; }
    const W = 160, H = 34, min = Math.min(...values), max = Math.max(...values) || 1, rng = (max - min) || 1;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", preserveAspectRatio: "none" }, container);
    const d = values.map((v, i) => `${i ? "L" : "M"}${(i / (values.length - 1) * W).toFixed(1)},${(H - 3 - ((v - min) / rng) * (H - 6)).toFixed(1)}`).join("");
    el("path", { d: d + `L${W},${H}L0,${H}Z`, fill: color, opacity: 0.08 }, svg);
    el("path", { d, fill: "none", stroke: color, "stroke-width": 1.6 }, svg);
  }

  window.Charts = { line, hbar, spark, fmt, PALETTE, showTip, hideTip };
})();
