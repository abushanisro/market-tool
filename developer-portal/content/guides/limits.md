| Limit | Default |
|---|---|
| Requests per minute per client or key | 600 (configurable per client / key) |
| Page size | 1–1000 |
| Search filter nesting | 12 levels |
| Aggregation metrics per request | 10 |
| Aggregation groups (`maxGroupCount`) | 10 000 |
| CSV export (`readTable?format=CSV`) | `rowLimit` ≤ 100 000 |
| Token lifetime | 300 s – 24 h (client setting) |

Every authenticated response includes `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`
(Unix seconds). Exceeding the budget returns `429` with `errorName: RateLimitExceeded` and `Retry-After`.
