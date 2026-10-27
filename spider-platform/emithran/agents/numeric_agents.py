"""Quantitative specialists: commodity & material cost, FX & currency risk, labour & cost."""
from __future__ import annotations

import datetime as dt
import math

from sqlalchemy import func, select

from ..models import Machine
from . import data as D
from .core import Agent, Ctx, Evidence, Finding
from .forecast import holt_forecast, linear_trend_forecast, stats, volatility_cone


INDIRECT_PROXIES = {"crude-oil-brent", "natural-gas-europe"}   # inputs to plastics, not the plastics' own price


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def _sev(score: float) -> str:
    """score = how many 'alarm thresholds' a move represents."""
    return "critical" if score >= 3 else "high" if score >= 2 else "medium" if score >= 1 else "low" if score >= 0.5 else "info"


def _series_points(points: list[tuple[dt.date, float]]) -> list[dict]:
    return [{"date": d.isoformat(), "value": v} for d, v in points]


class CommodityAgent(Agent):
    id, name, domain = "commodity", "Commodity & Material Cost Analyst", "commodities"
    job = "Tracks benchmark commodity prices, forecasts them with a back-tested model and links each move to the materials you actually use."
    persona = ("You are a sceptical commodity strategist for a manufacturer's cost-engineering team. You care about input-cost "
               "risk to purchased materials, you distrust forecasts that do not beat a naive baseline, and you separate cyclical "
               "moves from structural ones.")

    def collect(self, ctx: Ctx) -> list[Finding]:
        s = ctx.session
        fam = D.family_counts(s)
        out: list[Finding] = []
        for (entity, _country), pts in sorted(D.series(s, "worldbank-pink-sheet", "price").items()):
            if len(pts) < 24:
                continue
            slug = D.slugify(entity)
            vals = [v for _, v in pts]
            unit = _unit(s, entity)
            last_d, last = pts[-1]
            c1, _ = D.pct_change(pts, 30)
            c3, b3 = D.pct_change(pts, 91)
            c12, b12 = D.pct_change(pts, 365)
            st = stats(vals[-60:])
            fc = holt_forecast(vals, 6)
            skill = (fc.get("backtest") or {}).get("skill")
            p6 = fc["points"][-1] if fc else None
            fc_chg = (p6["p50"] - last) / last if p6 and last else None
            score = max(abs(c12 or 0) / 0.25, abs(c3 or 0) / 0.15)
            if fc_chg is not None and skill is not None and skill > 0.1:
                score = max(score, abs(fc_chg) / 0.15)       # a credible forecast move counts as a risk signal too
            ev = [Evidence("e1", f"{entity} latest", round(last, 2), unit, "worldbank-pink-sheet", _iso(last_d)),
                  Evidence("e2", "history length", len(pts), "months", "worldbank-pink-sheet", _iso(pts[0][0]))]
            if c3 is not None:
                ev.append(Evidence("e3", "change over ~3 months", round(c3 * 100, 1), "%", "worldbank-pink-sheet", _iso(last_d)))
            if c12 is not None:
                ev.append(Evidence("e4", "change over ~12 months", round(c12 * 100, 1), "%", "worldbank-pink-sheet", _iso(last_d)))
            if st.get("monthly_vol") is not None:
                ev.append(Evidence("e5", "monthly volatility (60m)", round(st["monthly_vol"] * 100, 1), "%", "worldbank-pink-sheet", _iso(last_d)))
            if st.get("drawdown_from_peak") is not None:
                ev.append(Evidence("e6", "distance from 5-year peak", round(st["drawdown_from_peak"] * 100, 1), "%", "worldbank-pink-sheet", _iso(last_d)))
            if p6:
                ev.append(Evidence("e7", "model +6 months (p50)", round(p6["p50"], 2), unit, "damped Holt", _iso(last_d)))
                ev.append(Evidence("e8", "model +6 months 80% range", f"{p6['lo80']:.2f} to {p6['hi80']:.2f}", unit, "damped Holt", _iso(last_d)))
                ev.append(Evidence("e9", "back-test skill vs naive", None if skill is None else round(skill, 2), "1 = perfect, <=0 = no better than naive", "rolling 12-step", _iso(last_d)))
            age = (ctx.now.date() - last_d).days
            conf = min(0.9, 0.45 + 0.2 * (len(pts) >= 60) + 0.15 * (age <= 60) + 0.15 * (skill is not None and skill > 0.1))
            direction = "rising" if (c12 or 0) > 0 else "falling"
            title = f"{entity}: {direction} {abs(c12) * 100:.0f}% over 12 months" if c12 is not None else f"{entity}: price trend"
            where = [f for f in D.COMMODITY_FAMILIES.get(slug, []) if fam.get(f)]
            n_mat = sum(fam[f] for f in where)
            summ = [f"Latest {last:,.2f} {unit} ({_iso(last_d)})."]
            if c12 is not None:
                summ.append(f"{c12 * 100:+.1f}% over 12 months, {(c3 or 0) * 100:+.1f}% over ~3 months.")
            if p6 and skill is not None and skill > 0.1:
                summ.append(f"Back-tested model puts it at about {p6['p50']:,.2f} in 6 months (80% range {p6['lo80']:,.2f} to {p6['hi80']:,.2f}).")
            else:
                summ.append("No credible forecast: the model does not beat a naive 'no change' guess on this series.")
            if n_mat:
                summ.append(f"Benchmark proxy for {n_mat} materials in the taxonomy ({', '.join(where)}).")
            out.append(Finding(self.id, f"commodity:{slug}", "risk", title, _sev(score), round(conf, 2), " ".join(summ), ev,
                               {"families": where, "materials": n_mat}, {**fc, "history": _series_points(pts[-72:]), "unit": unit} if fc else None,
                               [f"commodity:{slug}"] + [f"family:{f}" for f in where]))
            out.extend(self._internal_drift(s, entity, slug, unit, pts, where, fam))
        return out

    def _internal_drift(self, s, entity, slug, unit, pts, where, fam) -> list[Finding]:
        """The taxonomy's own prices are a snapshot; how far has the benchmark moved since that date?"""
        if not where:
            return []
        snap = D.internal_price_snapshot(s, where)
        if not snap:
            return []
        at = [p for p in pts if p[0] <= snap["date"]]
        if not at or not at[-1][1]:
            return []
        base_d, base_v = at[-1]
        move = (pts[-1][1] - base_v) / base_v
        score = abs(move) / 0.10
        if score < 1:
            return []
        indirect = slug in INDIRECT_PROXIES                          # energy is only an indirect input to plastics: never above medium
        if indirect:
            score = min(score, 1.99)
        n_mat = sum(fam[f] for f in where)
        ev = [Evidence("e1", f"internal USD/kg snapshot ({'+'.join(where)})", round(snap["median_usd_kg"], 3), "USD/kg", "emithran-taxonomy", _iso(snap["date"]), "internal"),
              Evidence("e2", "materials priced in the snapshot", snap["n"], "price observations", "emithran-taxonomy", _iso(snap["date"]), "internal"),
              Evidence("e3", f"{entity} benchmark at snapshot date", round(base_v, 2), unit, "worldbank-pink-sheet", _iso(base_d)),
              Evidence("e4", f"{entity} benchmark latest", round(pts[-1][1], 2), unit, "worldbank-pink-sheet", _iso(pts[-1][0])),
              Evidence("e5", "benchmark move since snapshot", round(move * 100, 1), "%", "worldbank-pink-sheet", _iso(pts[-1][0]))]
        return [Finding(self.id, f"commodity:{slug}:internal-drift", "risk",
                        f"Taxonomy prices for {', '.join(where)} are likely stale: {entity} benchmark {move * 100:+.0f}% since {snap['date']}",
                        _sev(score), 0.35 if indirect else 0.55,
                        f"The taxonomy's {', '.join(where)} prices are a snapshot dated {snap['date']}. The {entity} benchmark has moved "
                        f"{move * 100:+.1f}% since then, so costs for {n_mat} materials may be off. "
                        + ("This is an indirect proxy (an energy input), so the effect on material prices is uncertain." if indirect
                           else "The benchmark is a proxy, not the price of each grade."), ev, {"families": where, "materials": n_mat}, None, [f"commodity:{slug}"] + [f"family:{f}" for f in where])]


def _unit(s, entity: str) -> str:
    from ..models import Observation

    return s.execute(select(Observation.unit).where(Observation.entity == entity, Observation.kind == "price").limit(1)).scalar_one_or_none() or ""


class FXAgent(Agent):
    id, name, domain = "fx", "FX & Currency Risk Analyst", "fx"
    job = "Measures currency volatility and moves against USD, sizes exposure by the machine rates priced in each region, and audits the internal exchange-rate table."
    persona = ("You are a treasury-minded FX risk analyst. You do not predict currencies: you size risk with volatility and "
               "flag exposures and stale conversion tables. You say plainly when a move is within normal noise.")

    def collect(self, ctx: Ctx) -> list[Finding]:
        s = ctx.session
        mc, cc = D.machines_by_country(s), D.currency_countries(s)
        series = D.series(s, "frankfurter-fx", "fx")
        out: list[Finding] = []
        latest: dict[str, float] = {}
        for (pair, _), pts in sorted(series.items()):
            cur = pair.split("/")[-1]
            if len(pts) < 60:
                continue
            latest[cur] = pts[-1][1]
            vals = [v for _, v in pts]
            c30, _ = D.pct_change(pts, 30)
            c90, _ = D.pct_change(pts, 90)
            c365, _ = D.pct_change(pts, 365)
            cone = volatility_cone(vals[-260:], 30)
            vol = cone.get("daily_vol")
            ann = vol * math.sqrt(252) if vol else None
            score = max(abs(c30 or 0) / 0.03, abs(c90 or 0) / 0.06, abs(c365 or 0) / 0.12)
            expo = sum(mc.get(c, 0) for c in cc.get(cur, []))
            if score < 0.5 and expo == 0:
                continue
            last_d = pts[-1][0]
            ev = [Evidence("e1", f"{pair} latest", round(pts[-1][1], 4), f"{cur} per USD", "frankfurter-fx", _iso(last_d)),
                  Evidence("e2", "daily observations", len(pts), "days", "frankfurter-fx", _iso(pts[0][0]))]
            for i, (lab, v) in enumerate((("30-day move", c30), ("90-day move", c90), ("12-month move", c365)), 3):
                if v is not None:
                    ev.append(Evidence(f"e{i}", lab, round(v * 100, 2), "%", "frankfurter-fx", _iso(last_d)))
            if ann:
                ev.append(Evidence("e6", "annualised volatility", round(ann * 100, 1), "%", "frankfurter-fx", _iso(last_d)))
            if expo:
                ev.append(Evidence("e7", f"machine/work-center rates in {cur} regions", expo, "records", "emithran-taxonomy", None, "internal"))
            move = c90 if c90 is not None else (c30 or 0)
            out.append(Finding(self.id, f"fx:{cur}", "risk", f"USD/{cur}: {move * 100:+.1f}% over 90 days" if c90 is not None else f"USD/{cur} volatility",
                               _sev(score), 0.8 if len(pts) >= 200 else 0.55,
                               f"USD/{cur} at {pts[-1][1]:.4f} ({_iso(last_d)}); {(c30 or 0) * 100:+.1f}% in 30 days, {(c90 or 0) * 100:+.1f}% in 90 days"
                               + (f", annualised volatility {ann * 100:.1f}%." if ann else ".")
                               + " No directional forecast is made for currencies; the band below is a volatility cone."
                               + (f" {expo:,} machine/work-center rates are priced for this region." if expo else ""),
                               ev, {"currency": cur, "countries": cc.get(cur, []), "machines": expo},
                               {**cone, "history": _series_points(pts[-120:])} if cone else None, [f"fx:{cur}"] + [f"country:{c}" for c in cc.get(cur, [])]))
        out.extend(self._internal_table(s, latest))
        return out

    def _internal_table(self, s, latest: dict[str, float]) -> list[Finding]:
        from ..models import Observation

        sid = D.source_id(s, D.INTERNAL_SLUG)
        rows = s.execute(select(Observation).where(Observation.source_id == sid, Observation.kind == "fx")).scalars().all() if sid else []
        diffs = []
        for o in rows:
            cur = (o.entity or "").split("/")[-1]
            if cur in latest and o.value and latest[cur]:
                diffs.append((cur, o.value, latest[cur], (o.value - latest[cur]) / latest[cur], o.period_date))
        if not diffs:
            return []
        diffs.sort(key=lambda d: -abs(d[3]))
        worst = diffs[0]
        oldest = min((d[4] for d in diffs if d[4]), default=None)
        score = abs(worst[3]) / 0.10
        ev = [Evidence(f"e{i}", f"internal vs live USD/{c}", f"{iv} vs {lv:.4f}", f"{d * 100:+.1f}%", "exchange_rate_table.csv vs frankfurter-fx", _iso(dd), "internal")
              for i, (c, iv, lv, d, dd) in enumerate(diffs[:5], 1)]
        return [Finding(self.id, "fx:internal-table", "risk", f"Internal exchange_rate_table.csv differs from live rates by up to {abs(worst[3]) * 100:.0f}%",
                        _sev(score), 0.85,
                        f"{len(diffs)} currencies in exchange_rate_table.csv were compared with live ECB rates. The largest gap is USD/{worst[0]} "
                        f"({worst[3] * 100:+.1f}%). The table's oldest rate is dated {oldest}. Any cost converted with it is off by the same order.",
                        ev, {"currencies": [d[0] for d in diffs]}, None, ["data:fx-table"] + [f"fx:{d[0]}" for d in diffs[:5]])]


class LabourAgent(Agent):
    id, name, domain = "labour", "Labour & Cost Analyst", "labour"
    job = "Tracks hourly labour-cost trends by country, projects them with an interval, and cross-checks the taxonomy's internal labour rates against them."
    persona = ("You are a manufacturing labour-economics analyst. You watch wage inflation as a driver of should-cost, you "
               "never compare unlike measures without saying so, and you flag when a projection rests on very few years.")

    def collect(self, ctx: Ctx) -> list[Finding]:
        s = ctx.session
        out: list[Finding] = []
        usd_per_eur = 1 / latest_eur if (latest_eur := _latest_fx(s, "EUR")) else None
        internal = _internal_labour_by_country(s)
        for (_entity, iso), pts in sorted(D.series(s, "eurostat-labour-cost", "indicator").items(), key=lambda kv: str(kv[0][1])):
            if not iso or len(pts) < 5:
                continue
            years = [p[0].year for p in pts]
            vals = [p[1] for p in pts]
            run = 1                                           # contiguous yearly tail only: irregular gaps would distort a trend
            while run < len(years) and years[-run - 1] == years[-run] - 1:
                run += 1
            tail_y, tail_v = years[-run:], vals[-run:]
            yoy = (vals[-1] / vals[-2] - 1) if years[-1] - years[-2] == 1 and vals[-2] else None
            span = years[-1] - years[0]
            cagr = (vals[-1] / vals[0]) ** (1 / span) - 1 if span > 0 and vals[0] else None
            fc = linear_trend_forecast(tail_y, tail_v, [years[-1] + 1, years[-1] + 2]) if run >= 5 else {}
            score = max(abs(yoy or 0) / 0.06, abs(cagr or 0) / 0.07)      # 6% a year = medium, 12% = high; normal catch-up growth is not flagged
            if score < 1:
                continue
            ev = [Evidence("e1", "hourly labour cost, manufacturing", round(vals[-1], 2), "EUR/h", "eurostat-labour-cost", f"{years[-1]}"),
                  Evidence("e2", "years of history", len(pts), f"{years[0]}-{years[-1]}", "eurostat-labour-cost", f"{years[0]}")]
            if yoy is not None:
                ev.append(Evidence("e3", "latest year-on-year", round(yoy * 100, 1), "%", "eurostat-labour-cost", f"{years[-1]}"))
            if cagr is not None:
                ev.append(Evidence("e4", f"compound growth {years[0]}-{years[-1]}", round(cagr * 100, 1), "% per year", "eurostat-labour-cost", f"{years[-1]}"))
            if fc:
                p2 = fc["points"][-1]
                ev.append(Evidence("e5", f"indicative {p2['x']} (p50)", round(p2["p50"], 2), "EUR/h", "linear trend", f"{years[-1]}"))
                ev.append(Evidence("e6", f"indicative {p2['x']} 80% range", f"{p2['lo80']:.2f} to {p2['hi80']:.2f}", "EUR/h", "linear trend", f"{years[-1]}"))
            mach = internal.get(iso)
            if mach and usd_per_eur:
                ev.append(Evidence("e7", "taxonomy average labour rate", round(mach["labor"], 2), "USD/h", "emithran-taxonomy", None, "internal"))
                ev.append(Evidence("e8", "Eurostat cost converted at live ECB rate", round(vals[-1] * usd_per_eur, 2), "USD/h", "eurostat-labour-cost", f"{years[-1]}"))
            out.append(Finding(self.id, f"labour:{iso}", "risk", f"{iso}: manufacturing labour cost {yoy * 100:+.1f}% year on year" if yoy is not None else f"{iso}: labour-cost trend",
                               _sev(score), 0.7 if len(pts) >= 8 else 0.5,
                               f"{iso} total hourly labour cost in manufacturing is {vals[-1]:.2f} EUR ({years[-1]})"
                               + (f", {yoy * 100:+.1f}% on the year" if yoy is not None else "")
                               + (f" and {cagr * 100:.1f}% a year since {years[0]}" if cagr is not None else "")
                               + (f". An indicative straight-line projection reaches {fc['points'][-1]['p50']:.2f} EUR/h in {fc['points'][-1]['x']} (80% range {fc['points'][-1]['lo80']:.2f} to {fc['points'][-1]['hi80']:.2f})." if fc else ".")
                               + " Eurostat measures total labour cost including non-wage costs, which is a different measure from machine labour rates.",
                               ev, {"countries": [iso]}, {**fc, "history": [{"date": f"{y}-12-31", "value": v} for y, v in zip(years, vals)], "unit": "EUR/h"} if fc else None,
                               [f"labour:{iso}", f"country:{iso}"]))
            if mach and usd_per_eur:
                ratio = mach["labor"] / (vals[-1] * usd_per_eur)
                if ratio < 0.5 or ratio > 1.5:
                    out.append(Finding(self.id, f"labour:{iso}:internal-gap", "risk", f"{iso}: taxonomy labour rate is {ratio * 100:.0f}% of the Eurostat labour cost",
                                       "medium" if (ratio < 0.4 or ratio > 2) else "low", 0.45,
                                       f"The taxonomy averages {mach['labor']:.2f} USD/h across {mach['n']} machine records for {iso}; Eurostat's total labour cost is "
                                       f"{vals[-1] * usd_per_eur:.2f} USD/h at today's rate. The two measure different things (a work-center rate vs total employer cost), so a gap "
                                       "is not an error by itself, but a large one deserves a check of which labour definition the rates use.",
                                       [e for e in ev if e.id in ("e1", "e7", "e8")], {"countries": [iso]}, None, [f"labour:{iso}", "data:labour-rates"]))
        return out


def _latest_fx(s, cur: str) -> float | None:
    pts = D.series(s, "frankfurter-fx", "fx").get((f"USD/{cur}", None)) or []
    return pts[-1][1] if pts else None


def _internal_labour_by_country(s) -> dict[str, dict]:
    rows = s.execute(select(Machine.country_iso3, func.count(), func.avg(func.nullif(Machine.labor_rate_usd_hr, 0)))
                     .where(Machine.country_iso3.is_not(None)).group_by(Machine.country_iso3)).all()
    return {iso: {"n": n, "labor": lab} for iso, n, lab in rows if lab}
