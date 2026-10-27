The `manufacturing` ontology is generated from the `memory/` folder. Each folder becomes a
`ManufacturingCategory`; folder names are preserved in `originalFolder` (including spellings such as
"Multi-Spindle Maching") and mapped to nodes of `digital_factory_tree.csv`.

| memory/ content | Object type | Notes |
|---|---|---|
| Every CSV, JSON, XLSX sheet, MD/PDF | `Dataset` | role inferred from folder & columns; rows preserved verbatim |
| `Raw Material*/`, `materials*.csv` | `Material` + `Observation(kind=price)` | deduplicated by normalized name; prices normalized to USD/kg |
| `Machine*/`, `Countries data/` | `Machine` | labour / overhead USD/h; every original column in `attributes` |
| `Processes/`, `operations*.csv` | `Process` | process and operation names |
| `exchange_rate_table.csv` | `Observation(kind=fx)` | flagged `fx_internal_table` |
| `digital_factory_tree.csv` | `TaxonomyNode` | verbatim |
| `Standards/` | `Standard` | |
| MHR / LHR mentions | `UnresolvedIdentifier` | evidence only, never interpreted |

Use [List Object Types](/docs/ontologies/object-types/list-object-types) or the
[object type reference](/docs/ontologies/object-types-reference/material) for every property.
