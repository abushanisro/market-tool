[Search Objects](/docs/ontologies/ontology-objects/search-objects) and
[Aggregate Objects](/docs/ontologies/ontology-objects/aggregate-objects) share one filter syntax.

```json
{
  "where": {
    "type": "and",
    "value": [
      { "type": "eq", "field": "kind", "value": "price" },
      { "type": "in", "field": "countryIso3", "value": ["IND", "USA"] },
      { "type": "not", "value": { "type": "contains", "field": "qualityFlags", "value": "as_of_unknown" } }
    ]
  },
  "orderBy": { "fields": [{ "field": "valueUsd", "direction": "desc" }] },
  "select": ["entity", "valueUsd", "countryIso3"],
  "pageSize": 50
}
```

| Type | Applies to | Meaning |
|---|---|---|
| `eq` `gt` `gte` `lt` `lte` | scalar | comparison with `value` |
| `isNull` | any | `value: true` = null, `false` = not null |
| `in` | scalar | equals any value in the list |
| `contains` | array | array contains `value` |
| `startsWith` | string | case-insensitive prefix |
| `containsAnyTerm` / `containsAllTerms` | string | case-insensitive word match |
| `and` / `or` / `not` | — | combine clauses |

`struct` properties (e.g. `technicalProperties`, `attributes`) are returned but cannot be filtered.

## Aggregations

Metrics: `count`, `min`, `max`, `avg`, `sum`, `approximateDistinct`. Group by `exact` values or numeric
`fixedWidth` buckets; `accuracy` becomes `APPROXIMATE` when `maxGroupCount` truncates groups.
