# Emithran Developer Portal (Next.js)

API reference + Developer Console for the **Emithran Manufacturing Intelligence API v2** served by
`spider-platform` (FastAPI). Layout follows the Palantir Foundry API reference (left resource tree,
endpoint pages with parameters, request/response examples, contents rail); styling follows the Emithran
brand (Sora / Inter / JetBrains Mono, teal `#0D9E8A` on ink `#0D1117`, light + dark).

| Area | What it does |
|---|---|
| `/docs/general/*` | Guides: introduction, getting started, authentication, data model, RIDs, search & aggregation, paging, versioning, limits, errors |
| `/docs/<group>/<resource>/*` | One page per operation (42) generated from the OpenAPI spec, with cURL / Python / TypeScript samples, a **real** captured response, errors and a **Try it** panel |
| `/docs/ontologies/object-types-reference/*` | One page per object type (13) with every property, link, an example object and live counts |
| `/console` | Sign in with the service admin token → create / rotate / revoke OAuth clients, create / revoke API keys, usage analytics |
| `/api/spec` | The OpenAPI 3.1 document |

## Run

```bash
npm ci
cp .env.example .env.local        # EMITHRAN_API_URL = the FastAPI service, NEXT_PUBLIC_API_BASE_URL = URL shown in samples
npm run dev                       # http://localhost:3000
npm run build && npm start        # production
```

Docker: `docker build -t emithran-portal . && docker run -p 3000:3000 -e EMITHRAN_API_URL=http://api:8000 emithran-portal`
(or use the `portal` service in `spider-platform/docker-compose.yml`).

## Keeping docs in sync with the API

`content/` holds snapshots generated from the API so pages are static and fast:

```bash
# in spider-platform (needs the imported database)
EMITHRAN_ADMIN_TOKEN=... python scripts/export_api_docs.py ../developer-portal/content   # spec + object types + real examples
# or, spec and error catalogue only, from a running API:
npm run sync-spec
```

## Security notes

* The admin token is stored in an httpOnly, SameSite=Strict cookie and only used server-side.
* Try it calls go through `/api/try`, which forwards only to `/api/v2/*` and never to admin endpoints.
  The bearer token typed into Try it stays in the browser tab's sessionStorage.
* Client secrets and API keys are shown once; the API stores only SHA-256 hashes.
