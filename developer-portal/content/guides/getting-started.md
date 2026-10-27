## 1. Get credentials

Ask an administrator to create an **OAuth client** (server-to-server) or an **API key** (scripts, notebooks) in the
[Developer Console](/console). Secrets are shown once.

## 2. Exchange client credentials for a token

```bash
curl -X POST "$BASE/api/v2/oauth2/token" \
  -d grant_type=client_credentials \
  -d client_id=$CLIENT_ID -d client_secret=$CLIENT_SECRET \
  -d scope="api:ontologies-read api:market-read"
```

API keys skip this step — use the key directly as the bearer token.

## 3. Make your first call

```bash
curl "$BASE/api/v2/ontologies/manufacturing/objectTypes" \
  -H "Authorization: Bearer $EMITHRAN_TOKEN"
```

## 4. Search objects

```bash
curl -X POST "$BASE/api/v2/ontologies/manufacturing/objects/Material/search" \
  -H "Authorization: Bearer $EMITHRAN_TOKEN" -H "Content-Type: application/json" \
  -d '{"where": {"type": "eq", "field": "family", "value": "aluminium"}, "pageSize": 10}'
```

## 5. Read the underlying file

Every object links back to a `Dataset`. Use [Read Table Dataset](/docs/datasets/datasets/read-table-dataset) for rows or
[Get File Content](/docs/datasets/files/get-file-content) for the original file.
