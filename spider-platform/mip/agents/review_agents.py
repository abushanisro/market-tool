"""Reviewing specialists: data quality, news & supply-chain risk, source scouting, and the red-team skeptic."""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter, defaultdict

from sqlalchemy import String, cast, func, select

from ..models import Category, Document, Machine, Observation, Source, UnresolvedIdentifier
from ..registry.service import RUNNABLE_VERIFICATION, freshness
from . import data as D
from .core import SEV_RANK, Agent, AgentResult, Ctx, Evidence, Finding, _get, _grounded, slug


def _iso(d) -> str | None:
    return d.isoformat() if d else None


class DataQualityAgent(Agent):
    id, name, domain = "data_quality", "Data Quality Auditor", "data_quality"
    job = "Audits freshness, completeness and trustworthiness of every dataset, and says exactly what is missing or unreliable."
    persona = ("You are a meticulous data-quality auditor. You rank issues by how much they could mislead a decision, you never "
               "call something fine just because it is common, and you always say what a gap prevents the user from doing.")

    def collect(self, ctx: Ctx) -> list[Finding]:
        s, out = ctx.session, []
        # 1) source freshness / failures
        for src in s.execute(select(Source).where(Source.status == "active")).scalars():
            if src.source_type == "internal" or src.verification_status not in RUNNABLE_VERIFICATION:
                continue
            state = freshness(src)
            if state in ("fresh", "n/a"):
                continue
            sev = {"failing": "high", "stale": "medium", "never": "low"}[state]
            ev = [Evidence("e1", "freshness state", state, None, src.slug, _iso(src.last_success_at)),
                  Evidence("e2", "allowed age", src.freshness_sla_hours, "hours", src.slug),
                  Evidence("e3", "consecutive failures", src.consecutive_failures, None, src.slug)]
            if src.last_error:
                ev.append(Evidence("e4", "last error", src.last_error[:200], None, src.slug))
            out.append(Finding(self.id, f"dq:source:{src.slug}:{state}", "data_gap", f"Source {src.name} is {state}", sev, 0.9,
                               f"{src.name} is {state}: last success {_iso(src.last_success_at) or 'never'}, allowed age {src.freshness_sla_hours} h, "
                               f"{src.consecutive_failures} consecutive failures.", ev, {"sources": [src.slug]}, None, [f"source:{src.slug}"]))
        # 2) quarantined internal prices
        flags = Counter()
        for (fl,) in s.execute(select(Observation.quality_flags).where(Observation.status == "quarantined")):
            for f in fl or []:
                if f in ("implausible_price", "missing_value", "unit_unknown", "unit_not_stated"):
                    flags[f] += 1
        q = s.execute(select(func.count()).select_from(Observation).where(Observation.status == "quarantined")).scalar_one()
        if q:
            ev = [Evidence("e1", "quarantined observations", q, None, "emithran-taxonomy", None, "internal")] + \
                 [Evidence(f"e{i}", f"flag: {f}", n, None, "emithran-taxonomy", None, "internal") for i, (f, n) in enumerate(flags.most_common(4), 2)]
            out.append(Finding(self.id, "dq:quarantined", "data_gap", f"{q:,} internal price observations are quarantined and unusable",
                               "high" if q > 2000 else "medium", 0.9,
                               f"{q:,} price observations were set aside during import (mostly: " + ", ".join(f"{f} {n:,}" for f, n in flags.most_common(3)) +
                               "). They are not used in benchmarks, so affected materials have no usable internal price.", ev, {}, None, ["data:internal-prices"]))
        # 3) machine-rate coverage
        tot = s.execute(select(func.count()).select_from(Machine)).scalar_one()
        if tot:
            have = {c: s.execute(select(func.count()).select_from(Machine).where(getattr(Machine, c).is_not(None))).scalar_one()
                    for c in ("labor_rate_usd_hr", "direct_overhead_usd_hr", "indirect_overhead_usd_hr")}
            share = have["labor_rate_usd_hr"] / tot
            worst = s.execute(select(Category.name, func.count(), func.count(Machine.labor_rate_usd_hr)).join(Category, Category.id == Machine.category_id)
                              .group_by(Category.name)).all()
            worst = sorted(((n, t, t - h) for n, t, h in worst if t - h > 0), key=lambda r: -r[2])[:3]
            ev = [Evidence("e1", "machine records", tot, None, "emithran-taxonomy", None, "internal"),
                  Evidence("e2", "with labour rate", have["labor_rate_usd_hr"], f"{share * 100:.0f}%", "emithran-taxonomy", None, "internal"),
                  Evidence("e3", "with direct overhead", have["direct_overhead_usd_hr"], f"{have['direct_overhead_usd_hr'] / tot * 100:.0f}%", "emithran-taxonomy", None, "internal"),
                  Evidence("e4", "with indirect overhead", have["indirect_overhead_usd_hr"], f"{have['indirect_overhead_usd_hr'] / tot * 100:.0f}%", "emithran-taxonomy", None, "internal")]
            if share < 0.8:
                out.append(Finding(self.id, "dq:labour-coverage", "data_gap", f"Only {share * 100:.0f}% of machine records carry a labour rate",
                                   "high" if share < 0.5 else "medium", 0.9,
                                   f"{have['labor_rate_usd_hr']:,} of {tot:,} machine records have a labour rate, versus "
                                   f"{have['direct_overhead_usd_hr'] / tot * 100:.0f}% for direct overhead. Largest gaps: " +
                                   ", ".join(f"{n} ({m:,} missing)" for n, _, m in worst) + ". The country workbooks only contain labour factors, not rates.",
                                   ev, {"categories": [n for n, _, _ in worst]}, None, ["data:labour-rates"]))
        # 4) undated observations
        undated = s.execute(select(func.count()).select_from(Observation).where(Observation.period_date.is_(None), Observation.kind != "property")).scalar_one()
        allobs = s.execute(select(func.count()).select_from(Observation).where(Observation.kind != "property")).scalar_one()
        if undated and allobs and undated / allobs > 0.05:
            out.append(Finding(self.id, "dq:undated", "data_gap", f"{undated / allobs * 100:.0f}% of observations have no date", "medium", 0.9,
                               f"{undated:,} of {allobs:,} observations have no as-of date, so they cannot be placed on a time axis or aged.",
                               [Evidence("e1", "undated observations", undated, None, "all sources", None, "internal"),
                                Evidence("e2", "all observations", allobs, None, "all sources", None, "internal")], {}, None, ["data:dates"]))
        # 5) unresolved MHR / LHR
        ids = s.execute(select(UnresolvedIdentifier).where(UnresolvedIdentifier.status == "unresolved")).scalars().all()
        if ids:
            out.append(Finding(self.id, "dq:unresolved-ids", "data_gap", "MHR / LHR are still undefined: an owner decision is needed", "medium", 0.95,
                               "The identifiers " + ", ".join(f"{i.code} ({i.occurrences} occurrences)" for i in ids) + " appear in the source files but have no confirmed "
                               "definition. The platform deliberately does not guess, so no analysis can use them until the owner confirms what they mean.",
                               [Evidence(f"e{n}", f"{i.code} occurrences", i.occurrences, None, "emithran-taxonomy", None, "internal") for n, i in enumerate(ids, 1)],
                               {}, None, ["data:identifiers"]))
        # 6) recorded-fixture data must never be mistaken for live data
        replay = s.execute(select(func.count()).select_from(Observation).where(cast(Observation.quality_flags, String).like('%"replayed"%'))).scalar_one()
        if replay:
            out.append(Finding(self.id, "dq:replayed", "data_gap", f"{replay:,} records come from recorded fixtures, not live sources", "high", 0.95,
                               "These observations were produced in replay mode and may be old. They are flagged but are present in the same tables as live data.",
                               [Evidence("e1", "replayed observations", replay, None, "all sources")], {}, None, ["data:replay"]))
        return out


THEMES: dict[str, tuple[str, list[str]]] = {
    "trade_policy": ("Tariffs & trade restrictions", ["tariff", "duties", "trade war", "export ban", "import ban", "sanction", "embargo", "anti-dumping", "quota"]),
    "labour": ("Labour disruption", ["strike", "walkout", "union", "labor shortage", "labour shortage", "layoff", "job cuts", "lockout"]),
    "supply": ("Supply shortage & delays", ["shortage", "supply chain", "bottleneck", "backlog", "delay", "out of stock", "allocation"]),
    "closure": ("Plant closures & insolvency", ["plant closure", "shut down", "shutdown", "closing plant", "bankrupt", "chapter 11", "insolven", "idled"]),
    "price": ("Input-cost pressure", ["price increase", "surcharge", "inflation", "cost pressure", "steel price", "aluminum price", "aluminium price", "copper price", "energy price"]),
    "quality": ("Quality, recalls & safety", ["recall", "defect", "osha", "explosion", "contamination", "safety"]),
    "cyber": ("Cyber incidents", ["cyberattack", "ransomware", "data breach"]),
    "energy": ("Energy supply & cost", ["energy crisis", "power outage", "gas price", "electricity price", "blackout"]),
    "logistics": ("Logistics & shipping", ["port ", "shipping", "freight", "container", "red sea", "canal"]),
    "demand": ("Demand slowdown", ["orders fall", "orders drop", "slowdown", "downturn", "recession", "contraction", "pmi"]),
}
KEYWORD_TAGS = {"aluminum": "commodity:aluminum", "aluminium": "commodity:aluminum", "copper": "commodity:copper", "nickel": "commodity:nickel",
                "zinc": "commodity:zinc", "steel": "commodity:iron-ore-cfr-spot", "iron ore": "commodity:iron-ore-cfr-spot", "rubber": "commodity:rubber-rss3",
                "oil": "commodity:crude-oil-brent", "natural gas": "commodity:natural-gas-europe", "tin ": "commodity:tin", "lead ": "commodity:lead"}


class NewsRiskAgent(Agent):
    id, name, domain = "news_risk", "News & Supply-Chain Risk Analyst", "news"
    job = "Reads the manufacturing news and research feed, groups headlines into risk themes and links them to the materials and processes they could hit."
    persona = ("You are a supply-chain risk analyst who reads industrial news for early warning. You weigh how many independent "
               "headlines support a theme, you do not extrapolate from a single story, and you name the likely affected materials or processes.")

    def collect(self, ctx: Ctx) -> list[Finding]:
        s = ctx.session
        since = (ctx.now - dt.timedelta(days=30)).replace(tzinfo=None)
        docs = s.execute(select(Document).where(Document.kind == "news")).scalars().all()
        recent = [d for d in docs if (d.published_at or d.observed_at) is None or (d.published_at or d.observed_at).replace(tzinfo=None) >= since]
        risk: list[tuple[Document, list[str]]] = []
        by: dict[str, list[Document]] = defaultdict(list)
        for d in recent:
            low = f"{d.title} {d.summary or ''}".lower() + " "
            themes = [th for th, (_, words) in THEMES.items() if any(w in low for w in words)]
            for th in themes:
                by[th].append(d)
            if themes:
                risk.append((d, themes))
        out = []

        def evidence(items: list[Document]) -> list[Evidence]:
            newest = sorted(items, key=lambda x: x.published_at or x.observed_at or dt.datetime.min, reverse=True)[:8]
            return [Evidence(f"e{i}", d.title[:140], _iso(d.published_at), None, (d.raw or {}).get("domain") or f"source #{d.source_id}", _iso(d.published_at), "public", d.url)
                    for i, d in enumerate(newest, 1)]

        def spread(items: list[Document]) -> dict:
            tg = lambda pre: Counter(x[len(pre):] for d in items for x in (d.categories or []) if x.startswith(pre))
            return {"categories": [c for c, _ in tg("cat:").most_common(6)], "countries": [c for c, _ in tg("country:").most_common(6)],
                    "locations": [c for c, _ in tg("loc:").most_common(6)], "headlines": len(items)}

        for theme, items in by.items():
            label = THEMES[theme][0]
            n, srcs = len(items), len({d.source_id for d in items})
            sev = "high" if n >= 5 else "medium" if n >= 3 else "low" if n >= 2 else "info"
            tags = {"news:" + theme}
            ent = spread(items)
            tags |= {f"country:{c}" for c in ent["countries"]} | {f"cat:{c}" for c in ent["categories"]}
            for d in items:
                t = f"{d.title} {d.summary or ''}".lower() + " "
                tags |= {tag for kw, tag in KEYWORD_TAGS.items() if kw in t}
            out.append(Finding(self.id, f"news:{theme}", "risk", f"{label}: {n} headline{'s' if n != 1 else ''} in the last 30 days", sev,
                               min(0.85, 0.3 + 0.08 * n + 0.1 * (srcs > 1)),
                               f"{n} manufacturing headline(s) from {srcs} source(s) mention {label.lower()}"
                               + (f", mostly about {', '.join(ent['countries'][:3])}" if ent["countries"] else "") + ". "
                               + ("Few headlines, so treat this as a weak signal. " if n < 3 else "") + "Latest: " + "; ".join(d.title[:90] for d in items[:2]),
                               evidence(items), ent, None, sorted(tags)))
        # hotspots: where in the world / which process the risk headlines concentrate
        for kind, prefix in (("country", "country:"), ("cat", "cat:")):
            groups: dict[str, list[tuple[Document, list[str]]]] = defaultdict(list)
            for d, themes in risk:
                for tag in (d.categories or []):
                    if tag.startswith(prefix):
                        groups[tag[len(prefix):]].append((d, themes))
            for key, lst in groups.items():
                if len(lst) < 3:
                    continue
                items = [d for d, _ in lst]
                th = Counter(THEMES[x][0] for _, themes in lst for x in themes).most_common(3)
                nm = _name(s, kind, key)
                n = len(items)
                out.append(Finding(self.id, f"news:{kind}:{key}", "risk", f"{nm}: {n} risk headlines in the last 30 days", "high" if n >= 8 else "medium" if n >= 5 else "low",
                                   min(0.8, 0.3 + 0.05 * n), f"{n} manufacturing headlines about {nm} carry risk language, mainly " + ", ".join(f"{a.lower()} ({c})" for a, c in th) + ".",
                                   evidence(items), spread(items), None, [f"{kind}:{key}"]))
        return out


def _name(s, kind: str, key: str) -> str:
    if kind == "country":
        from ..models import Country

        return s.execute(select(Country.name).where(Country.iso3 == key)).scalar_one_or_none() or key
    from ..news.classify import category_names

    return category_names().get(key, key)


# Candidate public sources the scout may suggest. These are suggestions to evaluate, NOT claims that they were verified.
SCOUT_CANDIDATES = {
    "news": ["GDELT (open global news events and tone)", "Hacker News Algolia API (engineering opinion)", "Stack Exchange API (engineering Q&A)",
             "Reddit official API (needs your credentials; check the commercial-use terms)", "more manufacturing trade-press RSS feeds (check each publisher's terms)"],
    "labour": ["ILOSTAT (ILO) earnings and labour cost", "US BLS international labor comparisons", "national statistics offices (India PLFS, China NBS)"],
    "benchmarks": ["national producer price indices for plastics and composites (BLS PPI, Eurostat PRODCOM)", "IMF primary commodity prices"],
}
HISTORY_OFFERED = {  # (what, offered since, how to capture)
    "frankfurter-fx": ("ECB reference rates", dt.date(1999, 1, 4), {"type": "backfill", "source": "frankfurter-fx", "start_date": "1999-01-04"}),
    "worldbank-pink-sheet": ("monthly commodity prices (Pink Sheet workbook)", dt.date(1960, 1, 1), {"type": "config", "source": "worldbank-pink-sheet", "hint": "raise the parser's month limit"}),
    "worldbank-wdi-manufacturing": ("World Bank indicators", dt.date(1960, 1, 1), {"type": "config", "source": "worldbank-wdi-manufacturing", "hint": "raise mrv in start_urls"}),
}


class SourceScoutAgent(Agent):
    id, name, domain = "source_scout", "Source Scout", "data_gaps"
    job = "Finds what the platform does not yet know: missing history, uncovered countries and materials, thin news coverage, sources waiting for a key."
    persona = ("You are a data-acquisition lead. You propose concrete public sources to close a gap, you distinguish a verified source "
               "from a candidate to evaluate, and you never suggest a source whose terms forbid automated collection.")

    def collect(self, ctx: Ctx) -> list[Finding]:
        s, out = ctx.session, []
        # 1) history depth vs what the publisher offers
        for slug_, (what, since, action) in HISTORY_OFFERED.items():
            sid = D.source_id(s, slug_)
            if sid is None:
                continue
            first = s.execute(select(func.min(Observation.period_date)).where(Observation.source_id == sid)).scalar_one()
            if first and (first - since).days > 365 * 5:
                out.append(Finding(self.id, f"scout:history:{slug_}", "data_gap", f"{what}: history captured only from {first.year}, publisher offers {since.year}", "medium", 0.8,
                                   f"The platform holds {what} from {first} onward. The publisher's documentation offers data from {since}. Capturing the full history "
                                   "gives forecasts and risk models more cycles to learn from.",
                                   [Evidence("e1", "earliest stored date", _iso(first), None, slug_, _iso(first)),
                                    Evidence("e2", "offered since (publisher documentation, not re-checked today)", _iso(since), None, slug_)],
                                   {"sources": [slug_]}, None, [f"source:{slug_}"]))
                out[-1].analysis["action"] = action
        # 2) news / opinion coverage
        cutoff = ctx.now - dt.timedelta(days=30)
        n = s.execute(select(func.count()).select_from(Document).where(Document.kind == "news", Document.published_at >= cutoff.replace(tzinfo=None))).scalar_one()
        feeds = s.execute(select(func.count(func.distinct(Document.source_id))).where(Document.kind == "news")).scalar_one()
        if n < 100:
            out.append(Finding(self.id, "scout:news-coverage", "data_gap", f"News coverage is thin: {n} headlines from {feeds} feed(s) in 30 days",
                               "high" if n < 30 else "medium", 0.9,
                               "Risk detection from news is only as good as its coverage. Candidates to evaluate: " + "; ".join(SCOUT_CANDIDATES["news"]) + ".",
                               [Evidence("e1", "headlines, last 30 days", n, None, "documents"), Evidence("e2", "distinct news feeds", feeds, None, "documents")],
                               {"candidates": SCOUT_CANDIDATES["news"]}, None, ["data:news"]))
            out[-1].analysis["action"] = {"type": "build", "connectors": ["gdelt", "hackernews", "stackexchange", "reddit-official-api", "more-rss"]}
        # 3) countries with machine rates but no external labour history
        have = {iso for (_, iso) in D.series(s, "eurostat-labour-cost", "indicator")}
        gaps = sorted(((iso, n) for iso, n in D.machines_by_country(s).items() if iso not in have and not iso.startswith("X") and iso != "WLD"), key=lambda r: -r[1])[:8]
        if gaps:
            out.append(Finding(self.id, "scout:labour-history", "data_gap", f"No labour-cost history for {len(gaps)} countries that have machine rates", "medium", 0.8,
                               "Machine rates exist for " + ", ".join(f"{i} ({n:,})" for i, n in gaps) + ", but no external hourly labour-cost history is connected. "
                               "Candidates to evaluate: " + "; ".join(SCOUT_CANDIDATES["labour"]) + ".",
                               [Evidence(f"e{k}", f"machine records in {i}", n, None, "emithran-taxonomy", None, "internal") for k, (i, n) in enumerate(gaps[:5], 1)],
                               {"countries": [i for i, _ in gaps], "candidates": SCOUT_CANDIDATES["labour"]}, None, ["data:labour-history"]))
        # 4) material families with no price benchmark
        covered = {f for fs in D.COMMODITY_FAMILIES.values() for f in fs}
        unc = sorted(((f, c) for f, c in D.family_counts(s).items() if f not in covered and c >= 20), key=lambda r: -r[1])
        if unc:
            out.append(Finding(self.id, "scout:benchmarks", "data_gap", f"{len(unc)} material families have no market price benchmark", "medium", 0.75,
                               "No benchmark series covers " + ", ".join(f"{f} ({c} materials)" for f, c in unc[:5]) + ". Candidates to evaluate: " + "; ".join(SCOUT_CANDIDATES["benchmarks"]) + ".",
                               [Evidence(f"e{k}", f"materials in {f}", c, None, "emithran-taxonomy", None, "internal") for k, (f, c) in enumerate(unc[:5], 1)],
                               {"families": [f for f, _ in unc], "candidates": SCOUT_CANDIDATES["benchmarks"]}, None, ["data:benchmarks"]))
        # 5) registered sources that cannot run yet
        waiting = [(x.slug, x.verification_status, x.auth_env_var) for x in s.execute(select(Source).where(Source.status != "disabled")).scalars()
                   if x.verification_status in ("planned", "key_required") and x.source_type != "internal"]
        if waiting:
            keyless = [w for w in waiting if w[1] == "key_required"]
            out.append(Finding(self.id, "scout:waiting-sources", "data_gap", f"{len(waiting)} registered sources are not running ({len(keyless)} need an API key)", "low", 0.9,
                               "Not running: " + ", ".join(f"{a} ({b}{', set ' + c if c else ''})" for a, b, c in waiting[:10]) + ".",
                               [Evidence("e1", "sources waiting", len(waiting), None, "registry"), Evidence("e2", "waiting for an API key", len(keyless), None, "registry")],
                               {"sources": [w[0] for w in waiting]}, None, ["data:sources"]))
        return out


class RedTeamAgent(Agent):
    id, name, domain = "red_team", "Red-Team Skeptic", "oversight"
    job = "Challenges every other agent's findings: weak evidence, short history, forecasts that do not beat a guess, single sources, stale or unverified data."
    persona = ("You are an adversarial reviewer. Your only job is to find what is weak about other analysts' conclusions: thin or old "
               "evidence, single sources, overfitted forecasts, confusing correlation with cause. You are not trying to be agreeable.")

    def collect(self, ctx: Ctx) -> list[Finding]:
        s = ctx.session
        verification = {x.slug: x.verification_status for x in s.execute(select(Source)).scalars()}
        out = []
        for f in ctx.prior:
            if f.agent == self.id or f.kind in ("challenge", "opinion"):
                continue
            issues: list[tuple[str, str]] = []                 # (text, weight 'major'|'minor')
            fc = f.forecast or {}
            if fc:
                skill = (fc.get("backtest") or {}).get("skill")
                if fc.get("indicative"):
                    issues.append((f"Straight-line projection from {fc.get('n')} annual points with no back-test, so it is indicative only.", "minor"))
                elif skill is None or skill <= 0:
                    issues.append((f"The forecast does not beat a naive 'no change' guess in back-testing (skill {'n/a' if skill is None else format(skill, '.2f')}); it is not used to set severity.", "minor"))
            hist = next((e for e in f.evidence if e.label == "history length"), None)
            if hist and isinstance(hist.value, int) and hist.value < 36 and SEV_RANK[f.severity] >= 2:
                issues.append((f"The claim rests on only {hist.value} months of history.", "major"))
            pub = {e.source for e in f.evidence if e.origin == "public" and e.source}
            if SEV_RANK[f.severity] >= 3 and len(pub) <= 1:
                issues.append(("High-severity claim rests on a single source.", "major" if f.severity == "critical" else "minor"))
            if f.evidence and all(e.origin == "internal" for e in f.evidence) and SEV_RANK[f.severity] >= 3:
                issues.append(("Rests only on internal data with no external corroboration.", "minor"))
            for e in f.evidence:
                if e.source in verification and verification[e.source] not in ("verified",) and e.origin == "public":
                    issues.append((f"Source {e.source} is only '{verification[e.source]}' in the registry, so its values are flagged unverified.", "minor"))
                    break
            ages = []
            for e in f.evidence:
                try:
                    if e.origin == "public" and e.as_of and f.kind == "risk" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.as_of):
                        ages.append((ctx.now.date() - dt.date.fromisoformat(e.as_of)).days)
                except ValueError:
                    pass
            if ages and min(ages) > 120 and f.agent in ("commodity", "fx"):
                issues.append((f"The most recent observation is {min(ages)} days old.", "major"))
            if not issues:
                continue
            f.analysis.setdefault("challenges", [])
            f.analysis["challenges"] += [{"by": self.id, "text": t, "weight": w, "mode": "rules"} for t, w in issues]
            major = sum(1 for _, w in issues if w == "major")
            if not major:
                continue                                       # caveats stay on the finding; only real weaknesses raise a challenge
            out.append(Finding(self.id, f"challenge:{f.agent}:{f.key}", "challenge", f"Challenge: {f.title}"[:300], "low", 0.7,
                               " ".join(t for t, _ in issues), [e for e in f.evidence[:3]], {"target": {"agent": f.agent, "key": f.key}}, None, list(f.tags),
                               {"mode": "rules", "target_severity": f.severity}))
        return out

    def enrich(self, ctx: Ctx, res: AgentResult) -> None:
        """LLM red team: write counter-arguments against the strongest claims, grounded in their own evidence."""
        targets = sorted([f for f in ctx.prior if f.agent != self.id and f.kind not in ("challenge", "opinion") and SEV_RANK[f.severity] >= 2],
                         key=lambda f: -SEV_RANK[f.severity])[:10]
        if not targets:
            return
        internal = any(f.internal() for f in targets)
        if not ctx.gateway.available(self.id, internal=internal):
            res.notes += ctx.gateway.candidates(self.id, internal=internal)[1] or ["no model assigned"]
            return
        payload = [{"finding": i, "agent": f.agent, "title": f.title, "severity": f.severity, "summary": f.summary,
                    "evidence": [{"id": e.id, "label": e.label, "value": e.value, "unit": e.unit, "as_of": e.as_of} for e in f.evidence]} for i, f in enumerate(targets)]
        obj, meta = ctx.gateway.chat_json(self.id, self.persona + "\nUse ONLY the evidence given; cite evidence ids.",
                                          'Argue against the weakest of these conclusions. Return JSON {"challenges":[{"finding":<index>,"argument":"<=60 words","cites":["<evidence id>"]}]}\n'
                                          + json.dumps(payload, default=str), internal=internal)
        if obj is None:
            res.notes.append("red-team model returned no usable JSON")
            return
        res.mode, res.model = "llm", f"{meta['provider']}/{meta['model']}"
        for c in obj.get("challenges") or []:
            f = _get(targets, c.get("finding"))
            if f is None or not _grounded(f, c.get("cites")) or not c.get("argument"):
                continue
            f.analysis.setdefault("challenges", []).append({"by": self.id, "text": str(c["argument"])[:500], "weight": "major", "mode": "llm", "model": res.model})
