# Infrastructure

ANUM infrastructure should be reproducible, observable, and environment-aware from the first implementation. Docker should support local development and service packaging. OpenTofu should manage cloud infrastructure. GitHub Actions should run checks and deployment workflows.

## Local Development

`infra/docker/compose.yaml` provides PostgreSQL with pgvector, Valkey, NATS JetStream, Temporal, Keycloak, S3-compatible storage (MinIO) and an OpenTelemetry collector. `docker compose -f infra/docker/compose.yaml up` starts this infrastructure only; developers run the API with `uvicorn --reload` and the web app with Vite as before.

The API and web containers sit behind the `app` profile, so they start only when asked:

```bash
docker compose -f infra/docker/compose.yaml --profile app up --build
```

This serves the API on `http://localhost:8000` and the built web bundle on `http://localhost:4173`, and starts the Temporal `worker` (the API image running `python -m anum_api.worker`). The `api` and `worker` services run with `ANUM_ENVIRONMENT=local` and the compose development credentials; those credentials are refused by the API and worker in every other environment (see [Security](security.md)). The compose `api` keeps `ANUM_RUNTIME_BACKEND=inline` and the in-memory repository; to try durable runs, run the migrations and set `ANUM_REPOSITORY_BACKEND=postgresql` and `ANUM_RUNTIME_BACKEND=temporal` on both `api` and `worker` ([Agent runtime](agent-runtime.md#durable-execution)).

Valkey (`valkey-cli ping`) and MinIO (`mc ready local`) have compose health checks.

## Container Images

| Image | Dockerfile | Build context | Port | Runs as |
|---|---|---|---|---|
| API | `services/api/Dockerfile` | `services/api` | 8000 | uid 10001 (`anum`) |
| Temporal worker | same image as the API, command `python -m anum_api.worker` | `services/api` | none | uid 10001 (`anum`) |
| Web | `apps/web/Dockerfile` | repository root | 8080 | uid 101 (`nginx`, unprivileged image) |

API image:

- Multi-stage on `python:3.13-slim`. Dependencies come from the exact pins in `services/api/pyproject.toml` into a virtualenv copied into the runtime stage; no compiler or pip cache ships.
- Runs `uvicorn anum_api.main:app --proxy-headers --no-server-header`. uvicorn reads `FORWARDED_ALLOW_IPS` (image default `127.0.0.1`); set it to the load balancer's address range so client IPs (used for rate limiting) come from `X-Forwarded-For` only when a trusted proxy sent it.
- Defaults to `ANUM_ENVIRONMENT=production`, so a container started without real configuration fails fast instead of running with development defaults.
- `HEALTHCHECK` polls `GET /health`.
- Ships `alembic.ini` and `migrations/`, so migrations run as a one-off job from the same image: `python -m alembic upgrade head`.
- `services/api/.dockerignore` keeps tests, local data and `.env` files out of the build context.

Web image:

- Builds the Vite bundle with pnpm (frozen lockfile, lifecycle scripts skipped) on Node 22, then serves `dist/` from `nginxinc/nginx-unprivileged`.
- `VITE_ANUM_API_URL` (build argument) is compiled into the bundle. `ANUM_CSP_CONNECT_SRC` (runtime environment, space-separated origins) is substituted into the Content-Security-Policy when the container starts; it must include the API origin.
- nginx config (`apps/web/nginx/default.conf.template`) adds the security headers listed in [Security](security.md), serves hashed `/assets/` as immutable, falls back to `index.html` for client routes, and answers `/healthz`.
- `apps/web/Dockerfile.dockerignore` limits the root build context to the workspace manifests, `apps/web` and `packages/contracts`.

`infra/docker/compose.yaml` starts Keycloak with `--import-realm` and mounts `infra/keycloak/anum-realm.json`, so the `anum` realm, its clients, roles, and claim mappers come up from code. `KC_HOSTNAME` pins the token issuer to `http://localhost:8080/realms/anum`; a containerized API should keep that issuer and point `ANUM_OIDC_JWKS_URL` at `http://keycloak:8080/realms/anum/protocol/openid-connect/certs`. The realm's `dev` user and the `admin`/`admin` console account are dev-only placeholders. See [Identity and sign-in](identity.md).

## Environments

Recommended environments are local, preview, staging, and production. Each environment should have separate secrets, databases, object buckets, identity realm settings, and telemetry configuration.

Any `ANUM_ENVIRONMENT` other than `local` must provide, through the deployment secret store: `ANUM_DATABASE_URL` with non-default credentials, `ANUM_CORS_ORIGINS` listing the exact `https` web origins, and real object-storage and provider credentials. The API refuses to start otherwise.

## OpenTofu

OpenTofu should define networks, compute, managed databases where used, object storage, secrets integration, DNS, certificates, queues, and observability wiring. State must be stored remotely with locking for shared environments. Not started: it waits on the owner's cloud provider choice.

## GitHub Actions

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | PRs and pushes to `main` | Web, API, e2e, PostgreSQL, compose (including `--profile app`), **Security scans**, **Docker images**, desktop, Android, Flutter. |
| `codeql.yml` | PRs, pushes to `main`, weekly | CodeQL `security-extended` for Python and JavaScript/TypeScript. |
| `deploy-staging.yml` | Pushes to `main`, manual | Builds and pushes `ghcr.io/<owner>/anum-api:<sha>` and `anum-web:<sha>`, then deploys to the `staging` environment once configured. |

The **Docker images** job builds both images without pushing, checks that the API image refuses development defaults in production mode, and smoke-tests both containers (health endpoint, non-root user, CSP header).

### Staging deploy: owner decision needed

`deploy-staging.yml` never runs on pull requests. Its `deploy` job is skipped while the `STAGING_DEPLOY_TARGET` repository variable is unset, so the workflow passes with only the image push until a cloud provider is chosen. To finish it:

1. Choose the cloud provider and region (see [Production plan](production-plan.md)).
2. Create the GitHub `staging` environment with its secrets (database URL, object storage, provider keys) and set variables `STAGING_DEPLOY_TARGET`, `STAGING_API_URL` and `STAGING_WEB_URL`.
3. Replace the placeholder "Deploy images" step with the provider's steps: run `python -m alembic upgrade head` from the API image as a one-off job, then roll out the API and web images. The placeholder fails loudly if the variable is set before this is done.
4. Add a `deploy-production.yml` (or a production job) behind a `production` environment with required reviewers.

## Now

Container images for the API and web, a compose `app` profile with the Temporal worker, CI image builds with smoke tests, and a staging workflow that pushes images to GHCR. The worker runs from the API image with a different command; its container needs the image health check disabled (it serves no HTTP port), as compose does. Next: OpenTofu and the provider-specific deploy steps.

## Later

Add blue/green or rolling deploys, preview environments per PR, autoscaling, cross-region design, backup drills, disaster recovery tests, and cost controls.
