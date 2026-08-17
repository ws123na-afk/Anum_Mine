# Deployment

This document describes how to run ANUM's API and web app outside of a single
developer's laptop: locally via Docker Compose, and via the images published
by CI. It reflects what exists in this repository today; it does not describe
aspirational infrastructure (see [Infrastructure](infrastructure.md) and the
root [README](../README.md) "Current Status" section for that distinction).

## Two independent deployables

ANUM ships as two separate container images that do not need to be
co-located, scaled together, or deployed by the same process:

- **API** (`services/api/Dockerfile`, built with context `services/api` so
  it has no dependency on the rest of the monorepo). It is a stateless ASGI
  process (FastAPI, served by uvicorn) that listens on port `8000`. It needs:
  - A reachable PostgreSQL database, via `ANUM_DATABASE_URL`, with
    `ANUM_REPOSITORY_BACKEND=postgresql` (see
    [Environment variables reference](#environment-variables-reference)
    below). On container start, when `ANUM_REPOSITORY_BACKEND=postgresql`
    the image runs `alembic upgrade head` automatically before serving.
  - If `ANUM_AUTH_MODE=oidc`, a reachable Keycloak (or other OIDC-compatible)
    issuer matching `ANUM_KEYCLOAK_ISSUER` / `ANUM_OIDC_JWKS_URL`.

  Any host that can run a container (or an ASGI process directly) works —
  the API does not need to live on the same machine, cluster, or region as
  the web app.

- **Web** (`apps/web/Dockerfile`, built with context `.`, i.e. the repo
  root — required because the app has a pnpm workspace dependency on
  `packages/contracts`). It builds the Vite app and serves the static
  output via nginx on port `80`. It needs exactly one thing at build time:
  the `VITE_ANUM_API_URL` build `ARG`, pointing at wherever the API will be
  reachable from end-user browsers.

  Because Vite inlines `VITE_*` environment variables into the built
  JS/CSS bundle at build time, **`VITE_ANUM_API_URL` is baked into the
  image** — it cannot be changed by restarting or reconfiguring a running
  container. Changing which API a web deployment talks to requires
  rebuilding the image with a new `VITE_ANUM_API_URL` value.

  The resulting image is a plain static site behind nginx, so it can be
  deployed anywhere that can serve that image (or, equivalently, anywhere
  that can host static files if the build output were copied out) — again,
  with no requirement to sit next to the API.

## Local full-stack via Docker Compose

`infra/docker/compose.yaml` is the local development/infra composition. It
now includes `api` and `web` services alongside the existing infra services
(`postgres`, `valkey`, `nats`, `temporal`, `temporal-ui`, `keycloak`,
`minio`, `otel-collector` — note that of these, only `postgres` and
`keycloak` are actually wired up to application code today; the rest are
started for future use but nothing in `services/api` or `apps/web` talks to
them yet).

To bring up just the pieces needed for a working full-stack app:

```bash
docker compose -f infra/docker/compose.yaml up api web postgres
```

Or to start everything (including the not-yet-wired-up infra services):

```bash
docker compose -f infra/docker/compose.yaml up
```

Ports:

| Service | Host port | Notes |
| --- | --- | --- |
| `web` (nginx) | `8081` | **Not** `5173` — `5173` is reserved for the separate `pnpm dev` Vite dev server, so the two never collide or get confused with each other. |
| `api` | `8000` | Same port whether run via compose or via `uvicorn --reload` directly. |
| `postgres` | `5432` | `anum` / `anum` / db `anum`. |
| `keycloak` | `8080` | Auto-imports the `anum` dev/test realm from `infra/docker/keycloak/anum-realm.json` — see `infra/docker/keycloak/README.md` to fetch a test token. Local dev/test only, not production (see [What's NOT production-ready yet](#whats-not-production-ready-yet-and-why)). |

- Reach the web UI at `http://localhost:8081`.
- Reach the API directly at `http://localhost:8000` (e.g.
  `http://localhost:8000/docs` for the FastAPI-generated OpenAPI UI, or for
  hitting endpoints directly with `curl`/Postman using the stub tenant
  headers described in the README's "Tenant Headers for Phase 1" section).

The compose `web` service is built with `VITE_ANUM_API_URL=http://localhost:8000`
baked in, so the compose-built web UI talks to the compose-built API at
`localhost:8000` out of the box.

The `api` service in this compose file defaults to
`ANUM_AUTH_MODE=stub_headers` for easy local testing (no token machinery
required — just send the stub headers). See the comment inline in
`infra/docker/compose.yaml` for how to switch it to `ANUM_AUTH_MODE=oidc`,
and note the caveat: switching that one variable is not sufficient on its
own — see below.

## Building/publishing images via CI

`.github/workflows/docker-publish.yml` builds and publishes both images on
push to `main`:

- The API image is published to `ghcr.io/<owner>/<repo>-api`.
- The web image is published to `ghcr.io/<owner>/<repo>-web`.

Because `VITE_ANUM_API_URL` is baked into the web image at build time (see
above), the web image CI publishes on a push to `main` is only meaningful
for a real deployment if it was built with the right API URL for that
deployment. Before relying on the published web image:

- A repo maintainer should set the `PRODUCTION_API_URL` repository variable
  so the workflow bakes in the correct `VITE_ANUM_API_URL` for the intended
  production API endpoint, **or**
- Rebuild the web image manually (e.g. `docker build --build-arg
  VITE_ANUM_API_URL=https://api.example.com -f apps/web/Dockerfile .` from
  the repo root) with the correct URL for wherever the API is actually
  reachable.

Until that variable is set (or a manual rebuild is done with the right
value), the GHCR web image should be treated as a CI-verification artifact,
not something to deploy as-is.

## Environment variables reference

These are the real settings read by `services/api/anum_api/settings.py`
today (via `pydantic-settings`, prefix `ANUM_`, optionally from a
`.env` file). Do not assume settings beyond this list exist.

| Setting | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `app_name` | `ANUM_APP_NAME` | `ANUM API` | Display name for the service. |
| `environment` | `ANUM_ENVIRONMENT` | `local` | Free-text environment label (e.g. `local`, `staging`, `production`). |
| `database_url` | `ANUM_DATABASE_URL` | `postgresql+psycopg://anum:anum@localhost:5432/anum` | SQLAlchemy connection string used when `repository_backend=postgresql`. |
| `repository_backend` | `ANUM_REPOSITORY_BACKEND` | `memory` | Selects the storage backend: `memory` (in-process, non-persistent) or `postgresql` (durable, tenant-isolated via RLS). |
| `keycloak_issuer` | `ANUM_KEYCLOAK_ISSUER` | `http://localhost:8080/realms/anum` | Expected OIDC issuer (`iss` claim) for the Keycloak realm; also used to derive the JWKS endpoint when `oidc_jwks_url` is unset. |
| `cors_origins` | `ANUM_CORS_ORIGINS` | `["http://localhost:5173"]` | Allowed CORS origins for browser clients calling the API. |
| `auth_mode` | `ANUM_AUTH_MODE` | `stub_headers` | Selects the request-auth path: `stub_headers` trusts `x-tenant-id`/`x-workspace-id`/`x-user-id`/`x-user-roles` headers as-is with zero verification; `oidc` validates a Bearer token against the configured Keycloak JWKS instead. |
| `oidc_audience` | `ANUM_OIDC_AUDIENCE` | `anum-api` | Expected `aud` claim when validating OIDC bearer tokens (only used when `auth_mode=oidc`). |
| `oidc_jwks_url` | `ANUM_OIDC_JWKS_URL` | `""` (empty — derived from `keycloak_issuer`) | Explicit JWKS endpoint override; when unset, derived as `{keycloak_issuer}/protocol/openid-connect/certs`. |
| `oidc_jwks_cache_seconds` | `ANUM_OIDC_JWKS_CACHE_SECONDS` | `300` | How long fetched JWKS keys are cached before being re-fetched. |

Additionally, `apps/web/Dockerfile` reads one build-time (not runtime)
variable:

| Variable | Where it's set | Purpose |
| --- | --- | --- |
| `VITE_ANUM_API_URL` | Docker build `ARG` | Base URL the web app's JS calls out to. Baked into the static bundle at build time; changing it requires a rebuild. |

For reference, the root [`.env.example`](../.env.example) also lists a few
env vars for infra pieces (`ANUM_VALKEY_URL`, `ANUM_NATS_URL`,
`ANUM_TEMPORAL_TARGET`, `ANUM_S3_*`) that are aspirational placeholders for
future integrations — they are not currently read by `settings.py` and have
no effect on the API today.

## What's NOT production-ready yet, and why

This stack should not be pointed at real user data or exposed on the public
internet without addressing all of the following:

- **Auth defaults to trusting arbitrary headers.** `auth_mode` defaults to
  `stub_headers`: the API accepts whatever `x-tenant-id`, `x-workspace-id`,
  `x-user-id`, and `x-user-roles` values a caller sends, with no
  verification whatsoever. Anyone who can reach the API can claim to be any
  tenant, any workspace, any user, with any role. This is fine for local
  development (see the README's "Tenant Headers for Phase 1" section) and
  is exactly why the default must never change without an explicit
  decision — it is not fine for anything reachable outside a trusted
  developer's own machine.

- **OIDC has a working dev/test realm now, but not a production one.**
  `infra/docker/compose.yaml`'s `keycloak` service auto-imports
  `infra/docker/keycloak/anum-realm.json` on startup: a realm with a
  client and claim mappers that populate `tenant_id`, `workspace_id`, and
  `roles` on issued tokens in exactly the shape the API expects (see
  `infra/docker/keycloak/README.md` for how to fetch a token from it with
  `curl`, and `services/api/tests/test_oidc_realm_config.py`, which
  confirms the mapper shape against the actual code without needing a
  live Keycloak). Setting `ANUM_AUTH_MODE=oidc` against that local realm
  now genuinely works end-to-end.

  That realm is explicitly **local dev/test only** — seeded users have
  plaintext, well-known passwords and `sslRequired: none`. A production
  deployment needs its own realm/client/mappers provisioned separately
  (by hand, via the Keycloak admin console/API, or a future OpenTofu
  module), with `ANUM_OIDC_AUDIENCE` / `ANUM_KEYCLOAK_ISSUER` /
  `ANUM_OIDC_JWKS_URL` pointed at it. See
  [Security → Identity](security.md#identity) for the target design.

- **No rate limiting.** There is no request throttling, brute-force
  protection, or abuse mitigation anywhere in the API. Any deployment
  reachable from untrusted clients needs this in front of (or inside) the
  service before going live.

- **The in-memory repository backend loses all data on restart.**
  `repository_backend` defaults to `memory` — an in-process store with no
  persistence. Any real deployment must explicitly set
  `ANUM_REPOSITORY_BACKEND=postgresql` (and a real `ANUM_DATABASE_URL`),
  or every restart, redeploy, or crash silently discards all tasks,
  runtime state, approvals, events, and memory records.

- **CORS defaults to the local dev server only.** `cors_origins` defaults
  to `["http://localhost:5173"]`. Any deployment where the web app is
  served from a different origin (which is the normal case — e.g. the
  compose `web` service on `localhost:8081`, or any real hostname) needs
  `ANUM_CORS_ORIGINS` set explicitly to the actual origin(s) the web app
  will be served from, or browser requests from the web app to the API
  will be blocked.
