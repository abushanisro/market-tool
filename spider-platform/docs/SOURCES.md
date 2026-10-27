# Sources & stack verification (2026-10-27)

“Verified” means a live request on 2026-10-27 returned the expected format. Items marked otherwise
were confirmed only from documentation or a landing page, or could not be confirmed.

## Data sources

| Source | Endpoint | Format / auth | Frequency | Licence · commercial use | Status |
|---|---|---|---|---|---|
| Frankfurter (ECB rates) | `https://api.frankfurter.dev/v1/latest?base=USD`, time series `/v1/{start}..{end}` (`.app` → 302 to `.dev`) | JSON, no key | daily | free incl. commercial · yes | **verified** (payload captured) |
| World Bank WDI | `https://api.worldbank.org/v2/country/{ISO3;…}/indicator/NV.IND.MANF.ZS?format=json` | JSON, no key, paged | annual | CC BY 4.0 · yes | **verified** (payload captured; `.CD` variant not captured) |
| Eurostat `sts_inpr_m` | `…/eurostat/api/dissemination/statistics/1.0/data/sts_inpr_m?…` | JSON-stat 2.0, no key | monthly | Eurostat reuse policy · yes (attribution) | **verified** (needs dimension filters) |
| UN Comtrade preview | `https://comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=699&…` | JSON, no key (preview) | annual/monthly | UN Comtrade terms · conditional | **verified** single-code query |
| World Bank Pink Sheet | link discovered on `worldbank.org/en/research/commodity-markets` (hash changes monthly) | XLSX | monthly | CC BY 4.0 (catalog default) · conditional | url verified |
| ISO Open Data | `isopublicstorageprod.blob.core.windows.net/opendata/_latest/…` | CSV/JSON/Parquet | daily | ODC-By 1.0 · yes | url verified (exact file name to confirm) |
| OpenAlex | `https://api.openalex.org/works?search=…` | JSON; no key 10 req/s | daily | CC0 · yes | documented |
| Crossref | `https://api.crossref.org/works?query=…&mailto=` | JSON; polite pool | continuous | metadata facts · yes | documented |
| arXiv | `https://export.arxiv.org/api/query` | Atom; ≤1 req / 3 s | daily | metadata CC0 · yes | documented |
| Manufacturing Dive | `https://www.manufacturingdive.com/feeds/news/` | RSS 2.0 | several/day | publisher © — headline/link/date only | **verified** |
| IMF PCPS | dataset page `data.imf.org/en/datasets/IMF.RES:PCPS` | SDMX 2.1/3.0 | monthly | attribution · conditional | planned (API host blocked verification) |
| data.gov.in | `api.data.gov.in` | REST + api-key | varies | GODL (to confirm) | key required |
| OEA India WPI | `eaindustry.nic.in` | HTML/XLS | monthly | to confirm | planned (latest release looked stale) |
| RBI reference rates | `rbi.org.in/Scripts/ReferenceRateArchive.aspx` | ASP.NET + Excel | daily | to confirm | planned |
| MOSPI IIP | `mospi.gov.in` | to confirm | monthly | GODL expected | planned |
| BIS India standards | `services.bis.gov.in` | HTML | — | to confirm | planned |
| USGS MCS 2026 | `doi.org/10.5066/P1WKQ63T` | CSV + PDF | annual | public domain expected | url verified |
| Materials Project | `api.materialsproject.org` | REST + key | — | CC BY 4.0 expected | key required |
| Wikidata | SPARQL | JSON | continuous | CC0 | planned |
| US Census Intl Trade | `api.census.gov/data/timeseries/intltrade/…` | JSON | monthly | — | key required (returned “Missing Key”) |
| FRED | — | — | — | storing in another DB prohibited; commercial needs consent | **blocked by terms** |
| MatWeb | — | — | — | terms forbid mass export | **blocked by terms** |
| NIMS MatNavi | — | — | — | site prohibits scraping | **blocked by terms** |
| LME / MCX / Fastmarkets / BigMint | — | — | — | proprietary licences | **blocked by terms** |

The platform does not assume any site provides live prices, availability or supplier inventory.
Supplier catalogs and public quotations can be added through `jsonld`/`html_css` sources only where
the site's terms permit it; records are flagged `catalog_price`.

## Stack (PyPI, 2026-10-27)

| Package | Version | Licence | Note |
|---|---|---|---|
| scrapy | 2.19.0 | BSD-3 | primary engine |
| scrapy-playwright | 0.0.48 | BSD-3 | only for `render_js` sources; needs asyncio reactor |
| httpx | 0.28.1 | BSD-3 | `llm_extract` HTTP client |
| lxml | 6.1.x | BSD-3 | |
| beautifulsoup4 | 4.15.0 | MIT | available for custom parsers |
| trafilatura | 2.3.1 | Apache-2.0 | GPL up to 1.7 — pinned ≥2.0 |
| fastapi | 0.162.0 | MIT | |
| sqlalchemy | 2.1.6 | MIT | needs Python ≥3.11 |
| psycopg | 3.3.6 | LGPL-3.0 | used unmodified |
| redis | 8.1.0 | MIT | optional |
| apscheduler | 3.11.3 | MIT | 4.x still pre-release |
| openpyxl / pdfplumber | 3.1.5 / 0.11.10 | MIT | |
