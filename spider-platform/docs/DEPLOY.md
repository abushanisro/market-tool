# Deploying Emithran: Auth0 + Vercel + Railway

```
browser --Auth0 login--> Vercel (Next.js)  --Bearer access token, server to server-->  Railway: API + worker + Go binary --> Postgres
                          /dashboard   (signed-in users)                                 verifies the token against Auth0
                          /docs        (public)       /console (admins only)
```

The browser only talks to Vercel. The backend checks every request's Auth0 access token; the Go worker's control plane (`/api/fetcher`) uses a
separate machine secret and is never reachable with a user token. Reddit (Devvit) is a later phase and not covered here.

## 1. Auth0 (once, about 10 minutes)

Dashboard: https://manage.auth0.com

1. **API** (Applications > APIs > Create). Identifier: `https://api.emithran.com` (this exact string is the *audience*, it is not a real URL). Signing algorithm RS256.
   In its settings turn on **Enable RBAC**, **Add Permissions in the Access Token** and **Allow Offline Access**.
2. **Permissions** on that API: `read:data`, `write:data` and `admin:platform`.
3. **Roles** (User Management > Roles, then Permissions tab > Add Permissions):

   | Role | Permissions | Can do |
   |---|---|---|
   | `viewer` | `read:data` | read everything |
   | `editor` | `read:data` + `write:data` | plus: run and cancel crawls, register/edit sources, run agents, acknowledge/dismiss flags, confirm identifiers, manage alert rules |
   | `admin` | all three | plus: model setup, crawl-limit settings, taxonomy re-import, Developer Console (API clients and keys) |
4. **Application** (Applications > Create, type *Regular Web Application*). Allowed Callback URLs `https://<your-vercel-domain>/auth/callback`
   (and `http://localhost:3100/auth/callback` for local work); Allowed Logout URLs `https://<your-vercel-domain>` (and `http://localhost:3100`).
   Note its *Domain*, *Client ID* and *Client Secret*.
5. **Invite-only**: Authentication > Database > Username-Password-Authentication > turn on **Disable Sign Ups**. Add users under User Management > Users and
   give each one role (`viewer`, `editor` or `admin`). Everyone you do not add cannot sign in.

## 2. Backend on Railway

New project from the GitHub repo; service root directory `spider-platform` (it builds the `Dockerfile`: API, embedded worker and the Go binary in one container).
Add the **Postgres** plugin. Variables:

| Variable | Value |
|---|---|
| `EMITHRAN_ENV` | `production` (the app refuses to start without the next three) |
| `EMITHRAN_AUTH0_DOMAIN` | your tenant domain, e.g. `emithran.us.auth0.com` |
| `EMITHRAN_AUTH0_AUDIENCE` | `https://api.emithran.com` |
| `EMITHRAN_ADMIN_TOKEN` | a long random string (machine secret for the Go worker; never a login) |
| `EMITHRAN_DATABASE_URL` | the Postgres plugin's `DATABASE_URL` (any `postgres://` form is accepted) |
| `EMITHRAN_EMBEDDED_WORKER` | `true` |
| `PORT` | `8000` |
| `EMITHRAN_GO_FETCHER_API` | `http://127.0.0.1:8000` |
| `EMITHRAN_CONTACT_EMAIL` | an address for polite-pool APIs |

Health check path `/api/health`. The container runs `alembic upgrade head`, seeds the source registry, then starts. Generate a public domain; that URL is `EMITHRAN_API_URL` below.
To scale later, split the worker into its own service (`python -m emithran.cli worker`) and set `EMITHRAN_EMBEDDED_WORKER=false`.

## 3. Load the memory/ data (once, from this machine; never committed)

```bash
cd spider-platform
EMITHRAN_DATABASE_URL='<Railway Postgres PUBLIC url>' EMITHRAN_DATA_ROOT=../memory .venv/bin/python -m emithran.cli import-taxonomy
```

## 4. Front door on Vercel

Import the repo. **Root Directory `developer-portal`** and keep *Include source files outside of the Root Directory in the Build Step* on (the build
copies the dashboard from `spider-platform/emithran/web`). Environment variables:

| Variable | Value |
|---|---|
| `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET` | from the Auth0 application |
| `AUTH0_AUDIENCE` | `https://api.emithran.com` (same as the backend) |
| `AUTH0_SECRET` | `openssl rand -hex 32` (encrypts the session cookie) |
| `APP_BASE_URL` | `https://<your-vercel-domain>` |
| `EMITHRAN_API_URL` | the Railway public URL, no trailing slash |

After the first deploy, put the Vercel URL into the Auth0 callback and logout lists (step 1.4).

## Local development

Backend: leave `EMITHRAN_AUTH0_*` unset and the API is open (a warning is logged); `python -m emithran.cli serve` and open http://localhost:8000.
To try sign-in locally, set the Auth0 values in `spider-platform/.env` and `developer-portal/.env.local` (see their `.env.example`), run `npm run dev` in
`developer-portal` (port 3100) and open http://localhost:3100.

## Checks after deploying

* `https://<vercel>/docs/...` loads without signing in; `https://<vercel>/dashboard/` sends you to Auth0.
* Signed in as a `viewer`: the dashboard loads; any change (Run all agents, acknowledge a flag) says "Editing access required".
* Signed in as an `editor`: running agents and acknowledging flags work; Model setup and Settings say "Admin access required".
* Signed in as an `admin`: everything works and `/console` lists clients.
* `curl https://<railway>/api/overview` returns 401 without a token; `curl https://<railway>/api/fetcher/jobs/1/plan` returns 401.
