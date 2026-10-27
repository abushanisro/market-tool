# Agent Intelligence (Emithran Brain)

Seven specialist agents review every dataset in the platform; the **Brain** runs them, lets a red-team agent challenge them,
finds where independent agents agree, scores the risk and records every flag with a lifecycle (open, acknowledged, dismissed, resolved).

## Agents (one job each)

| Agent | Only job | Data it reads |
|---|---|---|
| Data Quality Auditor | freshness, completeness, trustworthiness | source registry, quarantined prices, machine-rate coverage, undated records, MHR/LHR |
| Commodity & Material Cost Analyst | benchmark price moves + back-tested forecast, linked to materials you use; flags stale taxonomy prices | World Bank Pink Sheet, taxonomy materials and prices |
| FX & Currency Risk Analyst | currency volatility and exposure (volatility cone, no directional guess); audits `exchange_rate_table.csv` | Frankfurter/ECB rates, machine rates by region |
| Labour & Cost Analyst | labour-cost trend and indicative projection; cross-checks taxonomy labour rates | Eurostat `lc_lci_lev`, machine labour rates |
| News & Supply-Chain Risk Analyst | groups headlines into risk themes and links them to commodities | news documents |
| Source Scout | finds what is missing: history depth, countries, material families, news coverage, sources waiting for keys | everything above |
| Red-Team Skeptic | challenges the others: short history, forecasts that do not beat naive, single source, stale or unverified data | all other findings |

## How it stays trustworthy

* **Numbers come from code, not from a model.** Every finding lists its evidence (id, value, unit, source, date). Forecasts are damped-trend
  smoothing back-tested against a naive "no change" baseline; a forecast that does not beat naive is labelled as such and never raises severity.
* **A model only writes opinions about findings.** An opinion that cites an evidence id that does not exist is discarded; it can move a severity
  by at most one step; "watch" items need cited evidence. The Brain brief must cite real findings or it falls back to the rules summary.
* **Privacy gate.** Findings built from your taxonomy files (origin `internal`) are only sent to **local** models unless you tick
  *Allow hosted providers to see internal taxonomy data* in Model setup. Public-data findings may use any provider.
* **Without any model everything still works** (mode "rules"). Models add opinions, red-team arguments and a written brief.

## Models

`emithran/agents/providers.yaml` lists OpenAI-compatible endpoints: Ollama and LM Studio (local), OpenRouter, Google AI Studio, NVIDIA NIM, Groq, Cerebras,
Mistral, Hugging Face. Keys are read from `EMITHRAN_KEY_*` environment variables (see `.env.example`), never stored. Model ids are not hard-coded: use
**Model setup -> Discover models** to list each provider's own names, then assign per agent (or for all). Several models per agent act as fallbacks.
Use different model families for the red team and the analysts to get genuinely different opinions.

Model landscape (checked from press and aggregator pages, October 2026; verify on each provider before relying on it): large open-weight leaders include
Kimi K3, GLM-5.3, DeepSeek V4 and Nemotron 3 Ultra (550B; too big for a laptop, so use a hosted API); Gemma 4 (Apache 2.0) and Qwen 3.x 27B-class models
run locally on a 24 GB Mac. Free tiers change often and may train on prompts.

## Running

UI: *Agent Intelligence* -> *Run all agents*. API: `POST /api/agents/run`, `GET /api/agents/overview`, `GET /api/agents/findings`.
The worker also runs the agents every `agents_auto_run_minutes` (default 60; 0 = off). The dashboard redraws live when a run finishes.

## Not built yet

* Reddit and Stack Exchange connectors. Reddit is deliberately API-only with your own credentials: its terms restrict scraping, and the platform does
  not collect from sources that forbid it. (Hacker News, GDELT and trade-press RSS are built: see `docs/NEWS.md`.)
* Full historical backfill beyond what is captured (the Source Scout lists each gap and offers a backfill action for FX).
* The LLM path has been tested against a stub provider only, not against a live model.
