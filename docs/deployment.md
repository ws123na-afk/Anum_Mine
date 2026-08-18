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
| `rate_limit_enabled` | `ANUM_RATE_LIMIT_ENABLED` | `false` | Turns on the single-process rate limiter (see `anum_api/rate_limit.py`). |
| `rate_limit_requests` | `ANUM_RATE_LIMIT_REQUESTS` | `120` | Max requests per client per window before `429`. |
| `rate_limit_window_seconds` | `ANUM_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length, in seconds. |
| `rate_limit_trust_forwarded_for` | `ANUM_RATE_LIMIT_TRUST_FORWARDED_FOR` | `false` | Key the rate limiter by `X-Forwarded-For` instead of the raw connection IP. Only safe behind a trusted reverse proxy. |
| `security_headers_hsts_enabled` | `ANUM_SECURITY_HEADERS_HSTS_ENABLED` | `false` | Adds `Strict-Transport-Security` to every response. Only enable once served over HTTPS end-to-end. |
| `valkey_url` | `ANUM_VALKEY_URL` | unset | Set to a `redis://...` URL to move idempotency + rate-limit state into Valkey (survives restarts, shared across replicas) instead of single-process memory. See `anum_api/valkey_client.py`. |
| `nats_url` | `ANUM_NATS_URL` | unset | Set to a `nats://...` URL to publish domain events to JetStream and enable the live (non-polling) path of `GET /api/v1/events/stream`. See `anum_api/events_nats.py`, `anum_api/realtime.py`. |
| `nats_stream_name` | `ANUM_NATS_STREAM_NAME` | `ANUM_EVENTS` | JetStream stream name events publish to / the SSE endpoint subscribes to. |
| `temporal_address` | `ANUM_TEMPORAL_ADDRESS` | unset | Set to a `host:port` to make task run/approve/cancel a durable Temporal workflow instead of one synchronous call; the API process itself runs the worker. See `anum_api/workflows/`. |
| `temporal_namespace` | `ANUM_TEMPORAL_NAMESPACE` | `default` | Temporal namespace to connect to. |
| `temporal_task_queue` | `ANUM_TEMPORAL_TASK_QUEUE` | `anum-tasks` | Temporal task queue the in-process worker polls. |
| `object_storage_endpoint_url` | `ANUM_OBJECT_STORAGE_ENDPOINT_URL` | unset | Set to an S3-compatible endpoint (e.g. MinIO) to enable `/api/v1/files`; unset returns `503`. See `anum_api/object_storage.py`. |
| `object_storage_bucket` / `object_storage_region` / `object_storage_access_key` / `object_storage_secret_key` | `ANUM_OBJECT_STORAGE_BUCKET` / `_REGION` / `_ACCESS_KEY` / `_SECRET_KEY` | `anum-files` / `us-east-1` / unset / unset | Bucket + credentials for the object storage endpoint above. |
| `model_provider` | `ANUM_MODEL_PROVIDER` | `mock` | `mock` (default) keeps every task run's model call deterministic and offline. `anthropic` opts into real calls via `AnthropicModelGateway` — requires `anthropic_api_key`. See `anum_api/model_gateway.py`. |
| `anthropic_api_key` | `ANUM_ANTHROPIC_API_KEY` | unset | Required when `model_provider=anthropic`. Never falls back to an ambient `ANTHROPIC_API_KEY` env var. |
| `anthropic_model` | `ANUM_ANTHROPIC_MODEL` | `claude-sonnet-5` | Model id passed to the Anthropic Messages API. |

Additionally, `apps/web/Dockerfile` reads these build-time (not runtime)
variables — Vite inlines them into the static bundle when it builds, so
changing any of them requires a rebuild, not a restart:

| Variable | Where it's set | Purpose |
| --- | --- | --- |
| `VITE_ANUM_API_URL` | Docker build `ARG` | Base URL the web app's JS calls out to. |
| `VITE_ANUM_AUTH_MODE` | Docker build `ARG` | `oidc` opts into real Keycloak login (Authorization Code + PKCE, via `apps/web/src/lib/auth.ts`); unset/anything else keeps the stub-header dev flow the app has always used. Mirrors the API's own `ANUM_AUTH_MODE` — set both together. |
| `VITE_ANUM_KEYCLOAK_URL` / `VITE_ANUM_KEYCLOAK_REALM` / `VITE_ANUM_KEYCLOAK_CLIENT_ID` | Docker build `ARG` | Only read when `VITE_ANUM_AUTH_MODE=oidc`. Point these at the same Keycloak realm the API's `ANUM_KEYCLOAK_ISSUER` validates tokens against (client id defaults to `anum-web`, the client both the dev and production realm configs define — see `infra/docker/keycloak/`). |

[`.env.production.example`](../.env.production.example) (repo root) is a
filled-in-the-blanks template covering exactly these settings for a real
deployment; make sure it includes the Valkey/NATS/Temporal/object-storage/
model-provider rows above before using it as a production checklist - it
may still only reflect the settings that existed when it was last updated.

## Production checklist

Copy `.env.production.example` (repo root) and fill in every placeholder —
it lists the exact settings below with the real `ANUM_*` env var names. This
stack should not be pointed at real user data or exposed on the public
internet until every item here is actually done, not just available:

- **Set `ANUM_AUTH_MODE=oidc`.** It defaults to `stub_headers`: the API
  accepts whatever `x-tenant-id`/`x-workspace-id`/`x-user-id`/`x-user-roles`
  values a caller sends, with zero verification — anyone who can reach the
  API can claim to be any tenant, workspace, user, or role. Fine for local
  development (see the README's "Tenant Headers for Phase 1" section); the
  default must never change without an explicit decision, because it is
  not fine for anything reachable outside a trusted developer's own
  machine.

  A **local dev/test realm** now exists and genuinely works end-to-end:
  `infra/docker/compose.yaml`'s `keycloak` service auto-imports
  `infra/docker/keycloak/anum-realm.json`, a realm whose claim mappers
  produce exactly the `tenant_id`/`workspace_id`/`roles` shape
  `oidc_auth.py` expects (see `infra/docker/keycloak/README.md`, and
  `services/api/tests/test_oidc_realm_config.py`, which confirms the
  mapping without needing a live Keycloak). **That realm is dev/test
  only** — seeded users have plaintext, well-known passwords and
  `sslRequired: none`.

  For production, use `infra/docker/keycloak/anum-realm.production-template.json`
  (see `infra/docker/keycloak/README.md#production`): same claim-mapper
  shape, zero seeded users. It still needs, from you: a real Keycloak (or
  other OIDC IdP) instance and users provisioned through it. The
  browser login flow itself is now built — `apps/web` implements real
  Authorization Code + PKCE login via `keycloak-js` (see
  `apps/web/src/lib/auth.ts`), opt-in at build time through
  `VITE_ANUM_AUTH_MODE=oidc` (see the build-time variables table above).
  Set that alongside `ANUM_AUTH_MODE=oidc` on the API — the frontend and
  backend auth modes are independent build/runtime switches and need to
  be turned on together for a coherent deployment.

- **Set `ANUM_REPOSITORY_BACKEND=postgresql` and a real `ANUM_DATABASE_URL`.**
  It defaults to `memory` — an in-process store with no persistence.
  Every restart, redeploy, or crash silently discards all tasks, runtime
  state, approvals, events, and memory records until this is set.

- **Set `ANUM_CORS_ORIGINS` to your real web app origin(s).** It defaults
  to `["http://localhost:5173"]`. Any deployment where the web app is
  served from a different origin (the normal case) needs this set
  explicitly, as a JSON array, or browser requests from the web app to
  the API are blocked. (CORS is now also configured to allow the
  `Authorization` and `Idempotency-Key` request headers and the `DELETE`
  method — both were missing before and would have silently broken
  cross-origin OIDC bearer auth, idempotent retries, and memory deletion
  from a browser.)

- **Set `ANUM_RATE_LIMIT_ENABLED=true`.** Off by default. When on, the API
  applies a single-process, fixed-window rate limit per client IP (see
  `services/api/anum_api/rate_limit.py`), returning `429` with the
  standard error envelope and a `Retry-After` header once
  `ANUM_RATE_LIMIT_REQUESTS` is exceeded within
  `ANUM_RATE_LIMIT_WINDOW_SECONDS`. This does **not** coordinate across
  multiple API replicas — each instance enforces its own independent
  count. A multi-instance deployment needs a shared store (the `valkey`
  compose service is unused by the app today and is the natural fit) for
  the limit to actually hold across instances; that isn't built. Only set
  `ANUM_RATE_LIMIT_TRUST_FORWARDED_FOR=true` if a trusted reverse proxy
  sits in front of the API and sets `X-Forwarded-For` itself — otherwise
  that header is spoofable by any client and defeats the limit entirely.

- **Set `ANUM_SECURITY_HEADERS_HSTS_ENABLED=true` once served over HTTPS.**
  Every response now carries `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and `Permissions-Policy` unconditionally (see
  `services/api/anum_api/security_headers.py`). `Strict-Transport-Security`
  is opt-in because enabling it over plain HTTP (e.g. local dev) tells
  browsers to require HTTPS for that host going forward, breaking
  `http://localhost` access — only turn it on once TLS is actually
  terminated end-to-end for this deployment.

- **TLS termination is not this repo's job.** Neither Dockerfile/image
  serves HTTPS directly (the API serves plain HTTP on `8000`, the web
  image serves plain HTTP on `80` via nginx). A real deployment needs a
  load balancer, reverse proxy, or platform feature (e.g. a managed
  ingress/CDN) terminating TLS in front of both — pick one as part of
  choosing where to host this (see "Two independent deployables" above).
