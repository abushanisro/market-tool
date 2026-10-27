# Emithran MIP — Manufacturing Data Spider & Market Intelligence

An independently deployable service that imports the existing manufacturing taxonomy (`../memory`),
discovers and crawls **permitted** public sources, normalizes everything into one provenance-tracked
database, and serves an 8-screen enterprise dashboard.

```
                ┌──────────── FastAPI (mip.api) ─────────────┐      ┌─ UI (mip/web, no build step)
  memory/  ──►  │ taxonomy importer → ObservationStore ◄──┐  │ ◄──► │ Overview · Spider Manager · Sources
  (CSV/JSON/    │                         ▲               │  │      │ Taxonomy · Materials · Market
   XLSX/MD)     │ source registry (YAML seed + API)       │  │      │ Data Explorer · Settings
                └────────────┬─────────────┬──────────────┼──┘
                             │ jobs table  │              │
                ┌────────────▼──────┐   ┌──▼──────────────┴──┐
                │ worker (APScheduler)│ │ crawl process / job │  python -m mip.crawler.run <id>
                │ schedule · dispatch │─►│ Scrapy RegistrySpider│  (+ scrapy-playwright only if render_js)
                │ recover · alerts    │ │ parsers → pipeline  │
                └─────────────────────┘ └─────────────────────┘
                     PostgreSQL (SQLite for dev/tests) · Redis optional (scheduler lock, job events)
```

## Quick start

**Docker (recommended)** — from this folder, with `memory/` next to it:

```bash
cp .env.example .env            # set MIP_CONTACT_EMAIL and MIP_ADMIN_TOKEN
docker compose up -d --build    # db, redis, api (:8000), worker
docker compose run --rm importer   # import ../memory (≈1–2 min, idempotent by checksum)
open http://localhost:8000
```

**Local (Python 3.11+)**

```bash
pip install -r requirements-dev.txt
export MIP_DATABASE_URL=postgresql+psycopg://mip:mip@localhost/mip   # or omit → SQLite in ./var
export MIP_DATA_ROOT=../memory
alembic upgrade head
python -m mip.cli seed-sources
python -m mip.cli import-taxonomy
python -m mip.cli serve &          # UI + API on :8000 (MIP_EMBEDDED_WORKER=true runs the worker in-process)
python -m mip.cli worker           # scheduler + dispatcher (separate process in production)
python -m mip.cli crawl frankfurter-fx    # run one source synchronously
```

Offline demo / tests: `MIP_REPLAY_DIR=tests/fixtures/replay` serves real responses captured on
2026-10-27 instead of using the network; every resulting record is tagged `replayed`.

## What the importer found in `memory/`

| | |
|---|---|
| Files registered (CSV, JSON, XLSX sheets, MD/PDF/DOCX/PNG) | 1,223 datasets, schemas discovered per file |
| Source rows preserved verbatim (`dataset_rows`) | 113,461 |
| Categories (one per folder, original names kept) | 34, mapped to `digital_factory_tree.csv` nodes |
| Materials (deduplicated, family-classified) | 1,318 |
| Machines / work centers with labour & overhead rates | 65,380 |
| Internal price observations | 8,851 verified · 3,224 quarantined (unit not stated, implausible values) |
| `exchange_rate_table.csv` | imported as FX observations, flagged `fx_internal_table` (most rows dated 2017) |

Roles are inferred from location and columns (`materials`, `machines`, `processes`, `operations`,
`lookup`, `variables`, `wage_grades`, `country_machine_rates`, `standards`, `database`). Folder
spellings such as “Multi-Spindle Maching” or “metirials” are preserved and still recognized.

**MHR / LHR** are stored as *unresolved identifiers* with 118 / 68 occurrences of evidence. The
platform never interprets them; an owner records a confirmed definition in *Taxonomy → MHR / LHR*.
Source columns whose names contain them (e.g. `mhrUsdPerHour`) are kept under their original names.

## Source registry

`mip/registry/sources.yaml` seeds 24 sources. Each has type, scope, licence, commercial-use flag,
verification status and evidence date. Status meanings:

| status | meaning | can run? |
|---|---|---|
| `verified` | live request confirmed URL + format (2026-10-27) | yes |
| `url_verified` / `documented` | URL or official docs confirmed, not fully exercised | yes (manual first run advised) |
| `key_required` | exists, needs `MIP_KEY_*` env var | yes, once key is set |
| `planned` | candidate, terms/URL unconfirmed | **no** |
| `blocked_by_terms` | terms forbid crawling/reuse (FRED, MatWeb, NIMS, LME/MCX/Fastmarkets) | **never** |

Verified & runnable now: Frankfurter FX, World Bank WDI, Eurostat industrial production, UN Comtrade
preview, Manufacturing Dive RSS; URL-verified: World Bank Pink Sheet (monthly metals/rubber/energy),
ISO Open Data; documented: OpenAlex, Crossref, arXiv. Details: `docs/SOURCES.md`.

### Registering a new source (no code)

`POST /api/sources` or *Spider Manager → Register source*. Example — a public HTML price table:

```json
{"slug": "state-steel-prices", "name": "…", "base_url": "https://example.gov/prices",
 "source_type": "html", "start_urls": ["https://example.gov/prices"], "max_depth": 1,
 "allow_patterns": ["/prices/\\d{4}"], "verification_status": "planned",
 "extraction": {"parser": "html_table", "table_selector": "table.rates",
                "columns": {"entity": "Grade", "value": "Price", "period_date": "Date"},
                "defaults": {"unit": "INR/t", "country": "IND"}},
 "validation": {"required": ["value"], "min": 0}}
```

Generic parsers: `html_table`, `html_css` (CSS/XPath schema), `jsonld`, `csv_table`, `xlsx_table`,
`pdf_tables`, `rss`, `article` (trafilatura). Source-specific: `frankfurter`, `worldbank_indicator`,
`worldbank_pinksheet`, `eurostat_jsonstat`, `comtrade`, `openalex`, `crossref`, `iso_deliverables`.
`llm_extract` is available only with `MIP_LLM_API_KEY`; its records are always `ai_extracted` and never auto-verified.
New sources start `paused`; a source can only be activated once its verification status allows it.

## Crawling behaviour

* Scrapy is the only crawl engine; `scrapy-playwright` is switched on per source (`render_js`).
* One OS process per job → failure isolation; DB-backed queue (`SKIP LOCKED` on Postgres).
* Scope: allowed domains, allow/deny regexes, depth, per-source page budget + global cap, URL
  canonicalization (tracking params stripped), duplicate URL suppression.
* Incremental: per-URL checkpoints (ETag / Last-Modified → conditional GET, content SHA-256 →
  unchanged pages skipped), sitemap `lastmod`, fingerprint-based record dedupe, revisions supersede.
* Resilience: exponential backoff + jitter honouring `Retry-After`, bounded retries, AutoThrottle,
  heartbeat + stale-job recovery, job timeout, auto-pause after N consecutive failures, `JOBDIR` resume.
* Respect: robots.txt obeyed by default; no CAPTCHA solving, login, paywall or anti-bot circumvention.
* Scheduling: interval or cron (UTC) per source, historical backfill windows (e.g. Frankfurter).

## Data quality

Every record keeps original value, unit, currency and source reference (URL or file + row index).
Normalization: material name/family/grade, units → USD/kg (or USD/<unit> for non-mass prices), currencies via
crawled FX (fallback to the internal table, flagged), ISO3 countries/regions, dates (`2026M09`, annual).
Checks: missing values, non-positive values, unknown units, implausible USD/kg per family, ≥35%
change vs history, cross-source conflicts, revisions. Status: `verified` · `unverified` · `quarantined` · `superseded`.

## Material alternatives

Candidates must be listed by the taxonomy for the **same process**, share the material class (and by
default the family), and carry the reference's process capability flags (e.g. `Can Die Cast`).
Score = weighted cost + technical similarity on properties both materials report. Availability and
lead time have no verified source yet, so their weights are redistributed and shown as “n/a”.

## Public API v2 (API as a service)

A Foundry-style REST API over every imported dataset and derived object, documented by the Next.js
developer portal in `../developer-portal`.

* **Auth:** OAuth 2.0 client credentials (`POST /api/v2/oauth2/token` → `emt_…`) or API keys (`emk_…`), six scopes,
  per-client rate limits with `X-RateLimit-*` headers, request log with `errorInstanceId`.
* **Ontology:** `/api/v2/ontologies/manufacturing/…` — 13 object types (ManufacturingCategory, TaxonomyNode, Dataset,
  Material, Machine, Process, Observation, Country, Source, Standard, Document, UnresolvedIdentifier, QualityIssue)
  with List / Get / Search (filter tree) / Aggregate / Linked objects.
* **Datasets:** metadata, inferred schema, rows (JSON paged or CSV) and the original file for each of the 1,223 datasets.
* **Market & material intelligence, orchestration (sources, builds), admin (clients, keys, usage).**
* Errors use `{errorCode, errorName, errorInstanceId, parameters}`; paging uses `pageSize` / `pageToken` / `nextPageToken`;
  resources have RIDs `ri.emithran.main.<type>.<key>`; preview endpoints need `preview=true`.
* OpenAPI 3.1: `GET /api/v2/openapi.json`.

```bash
export MIP_ADMIN_TOKEN=change-me MIP_SECRET_KEY=$(openssl rand -hex 32)
python -m mip.cli create-api-key "my notebook"          # prints an emk_… key once
curl -H "Authorization: Bearer emk_…" http://localhost:8000/api/v2/ontologies/manufacturing/objectTypes
```

## Tests

```bash
pytest -q        # 21 tests: public API v2 (auth, scopes, rate limit, paging, search DSL, aggregation, datasets), normalization, parsers on real captured payloads, importer, API guards,
                 # end-to-end Scrapy runs in replay mode (incremental, backfill, partial failure)
```

## Known limits (as delivered)

* The build environment had no general internet egress, so live crawls were exercised only through
  replayed real responses. Run the worker on a host with network access to collect live data.
* World Bank Pink Sheet and ISO Open Data parsers are built to the documented layouts but have not
  been run against the live files yet — do a manual first run before scheduling.
* No user accounts; write operations are protected by a shared `MIP_ADMIN_TOKEN`.
