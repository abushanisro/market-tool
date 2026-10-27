All endpoints use bearer tokens: `Authorization: Bearer <token>`. Two kinds are accepted.

| | OAuth 2.0 client credentials | API key |
|---|---|---|
| Format | `emt_…` (signed, short-lived) | `emk_<prefix>_<secret>` |
| Lifetime | client TTL, default 3600 s | until expiry (default 90 days) or revocation |
| Best for | services and integrations | scripts, notebooks, testing |
| Obtain | [Get Access Token](/docs/general/authentication/get-access-token) | Developer Console |

## Scopes

| Scope | Grants |
|---|---|
| `api:ontologies-read` | Ontology metadata and objects |
| `api:datasets-read` | Dataset metadata, schema, rows and original files |
| `api:market-read` | Market and material intelligence |
| `api:orchestration-read` | Sources and builds |
| `api:orchestration-write` | Create and cancel builds |
| `api:admin` | Clients, API keys and usage |

A token carries the scopes requested at issuance (a subset of the client's scopes). A call without a required scope
fails with `403 PERMISSION_DENIED / MissingScope` and lists `missingScopes`.

## Revocation and rotation

Revoking a client or key takes effect on the next request. Rotating a client secret invalidates every token issued
before the rotation. Secrets and keys are stored only as SHA-256 hashes.
