The Emithran Manufacturing Intelligence API is a developer-friendly REST API over everything in the Emithran
manufacturing taxonomy — the `memory/` folder of category data, materials, machines, processes, lookup tables and
country rates — plus market data collected by the platform's spiders.

Requests and responses are JSON over HTTPS, except dataset exports (CSV) and original file downloads.
Endpoints are versioned under `/api/v2`.

| What you can do | Where |
|---|---|
| Discover object types and query any object with one search / aggregate syntax | [Ontologies](/docs/ontologies/ontology-objects/basics) |
| Read any imported file's rows, schema or original bytes | [Datasets](/docs/datasets/datasets/basics) |
| FX, price series, regional USD/kg benchmarks, labour rates, indicators | [Market Intelligence](/docs/market-intelligence/exchange-rates/basics) |
| Compare grades and find process-compatible alternatives | [Material Intelligence](/docs/material-intelligence/materials/basics) |
| Inspect sources and trigger crawls | [Orchestration](/docs/orchestration/builds/basics) |

> **Data honesty.** Every record carries its original value, unit and a pointer to the file row or URL it came
> from, plus a `status` (`verified`, `unverified`, `quarantined`, `superseded`). The API never fills gaps with
> estimates. Internal identifiers **MHR** and **LHR** are exposed as `UnresolvedIdentifier` objects and are not
> interpreted until their definitions are confirmed.

Start with [Getting started](/docs/general/getting-started), then read [Authentication](/docs/general/authentication).
