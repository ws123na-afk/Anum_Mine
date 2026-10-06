# Infrastructure

ANUM infrastructure should be reproducible, observable, and environment-aware from the first implementation. Docker should support local development and service packaging. OpenTofu should manage cloud infrastructure. GitHub Actions should run checks and deployment workflows.

## Local Development

`infra/docker/compose.yaml` provides PostgreSQL with pgvector, Valkey, NATS JetStream, Temporal, Keycloak, S3-compatible storage (SeaweedFS, `s3` service; MinIO no longer publishes community images) and an OpenTelemetry collector. `docker compose -f infra/docker/compose.yaml up` starts this infrastructure only; developers run the API with `uvicorn --reload` and the web app with Vite as before.

The API and web containers sit behind the `app` profile, so they start only when asked:

```bash
docker compose -f infra/docker/compose.yaml --profile app up --build
```

This serves the API on `http://localhost:8000` and the built web bundle on `http://localhost:4173`, and starts the Temporal `worker` (the API image running `python -m anum_api.worker`). The `api` and `worker` services run with `ANUM_ENVIRONMENT=local` and the compose development credentials; those credentials are refused by the API and worker in every other environment (see [Security](security.md)). The compose `api` keeps `ANUM_RUNTIME_BACKEND=inline` and the in-memory repository; to try durable runs, run the migrations and set `ANUM_REPOSITORY_BACKEND=postgresql` and `ANUM_RUNTIME_BACKEND=temporal` on both `api` and `worker` ([Agent runtime](agent-runtime.md#durable-execution)).

Valkey (`valkey-cli ping`) and S3 storage (`/healthz`) have compose health checks.

The compose `api` and `worker` export OpenTelemetry to the `otel-collector` (`ANUM_OTEL_EXPORTER_OTLP_ENDPOINT`). The `observability` profile adds Prometheus, Tempo, Loki and Grafana with provisioned dashboards and alert rules from `infra/observability`; start it with `ANUM_OTELCOL_OVERLAY=observability` so the collector forwards to them ([Observability](observability.md#local-stack)). Backups and the restore drill use `infra/backup/anum_backup.py` ([Runbooks](runbooks.md#backup-and-restore)).

## Container Images

| Image | Dockerfile | Build context | Port | Runs as |
|---|---|---|---|---|
| API | `services/api/Dockerfile` | `services/api` | 8000 | uid 10001 (`anum`) |
| Temporal worker | same image as the API, command `python -m anum_api.worker` | `services/api` | none | uid 10001 (`anum`) |
| Web | `apps/web/Dockerfile` | repository root | 8080 | uid 101 (`nginx`, unprivileged image) |
| Backup | `infra/backup/Dockerfile` | `infra/backup` | none | uid 10001 (`anum`) |

API image:

- Multi-stage on `python:3.13-slim`. Dependencies come from the exact pins in `services/api/pyproject.toml` into a virtualenv copied into the runtime stage; no compiler, pip cache or pip ships (pip is removed from the virtualenv and the base image, and pending Debian updates are applied, so image scanning stays clean).
- Runs `uvicorn anum_api.main:app --proxy-headers --no-server-header`. uvicorn reads `FORWARDED_ALLOW_IPS` (image default `127.0.0.1`); set it to the load balancer's address range so client IPs (used for rate limiting) come from `X-Forwarded-For` only when a trusted proxy sent it.
- Defaults to `ANUM_ENVIRONMENT=production`, so a container started without real configuration fails fast instead of running with development defaults.
- `HEALTHCHECK` polls `GET /health`.
- Ships `alembic.ini` and `migrations/`, so migrations run as a one-off job from the same image: `python -m alembic upgrade head`.
- `services/api/.dockerignore` keeps tests, local data and `.env` files out of the build context.

Web image:

- Builds the Vite bundle with pnpm (frozen lockfile, lifecycle scripts skipped) on Node 22, then serves `dist/` from `nginxinc/nginx-unprivileged:1.30-alpine` (the stable nginx line) after `apk upgrade`.
- `VITE_ANUM_API_URL` and the sign-in settings `VITE_ANUM_OIDC_ISSUER`, `VITE_ANUM_OIDC_CLIENT_ID` (default `anum-web`) and `VITE_ANUM_WORKSPACE_ID` (build arguments) are compiled into the bundle. Every shared environment must set the issuer: without it the bundle uses local sessions, which the API refuses outside `local`. `ANUM_CSP_CONNECT_SRC` (runtime environment, space-separated origins) is substituted into the Content-Security-Policy when the container starts; it must include the API origin and the Keycloak issuer origin (discovery and token requests). The staging workflow passes `vars.STAGING_OIDC_ISSUER`.
- nginx config (`apps/web/nginx/default.conf.template`) adds the security headers listed in [Security](security.md), serves hashed `/assets/` as immutable, falls back to `index.html` for client routes, and answers `/healthz`.
- `apps/web/Dockerfile.dockerignore` limits the root build context to the workspace manifests, `apps/web` and `packages/contracts`.

Backup image: the official PostgreSQL 17 image (`pg_dump`/`pg_restore` and their libraries) with the Python runtime and pinned `psycopg` copied in from the official Python image of the same Debian release, running `infra/backup/anum_backup.py` as uid 10001. It is used only by the chart's optional backup CronJob ([Deployment](deployment.md)).

`infra/docker/compose.yaml` starts Keycloak with `--import-realm` and mounts `infra/keycloak/anum-realm.json`, so the `anum` realm, its clients, roles, and claim mappers come up from code. `KC_HOSTNAME` pins the token issuer to `http://localhost:8080/realms/anum`; a containerized API should keep that issuer and point `ANUM_OIDC_JWKS_URL` at `http://keycloak:8080/realms/anum/protocol/openid-connect/certs`. The realm's `dev` user and the `admin`/`admin` console account are dev-only placeholders. See [Identity and sign-in](identity.md).

## Environments

Recommended environments are local, preview, staging, and production. Each environment should have separate secrets, databases, object buckets, identity realm settings, and telemetry configuration.

Any `ANUM_ENVIRONMENT` other than `local` must provide, through the deployment secret store: `ANUM_DATABASE_URL` with non-default credentials, `ANUM_CORS_ORIGINS` listing the exact `https` web origins, and real object-storage and provider credentials. The API refuses to start otherwise.

## Kubernetes

Shared environments run on any Kubernetes cluster with the Helm chart in `infra/helm/anum`: API (HPA, PDB, probes on `/health`), Temporal worker, web, ingress, a pre-install/pre-upgrade migration Job, the voice retention CronJob, an optional backup CronJob, default-deny NetworkPolicies and token-less ServiceAccounts. Every secret is referenced by name from Secrets created outside the chart (External Secrets friendly), and the chart refuses to render values the API would refuse at startup. `infra/helm/bootstrap-database.sql` creates the migration, application and relay logins. Overlays exist for staging and production with placeholder hosts. See [Deployment](deployment.md) for values, Secrets, cluster access, migrations, rollback and scaling.

## OpenTofu

OpenTofu should define networks, compute, managed databases where used, object storage, secrets integration, DNS, certificates, queues, and observability wiring. State must be stored remotely with locking for shared environments. Not started: it waits on the owner's cloud provider choice.

## GitHub Actions

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | PRs and pushes to `main` | Web, API, e2e, PostgreSQL, compose (including `--profile app`), **Security scans**, **Docker images**, **Helm deploy (kind)**, desktop, Android, Flutter. |
| `codeql.yml` | PRs, pushes to `main`, weekly | CodeQL `security-extended` for Python and JavaScript/TypeScript. |
| `deploy-staging.yml` | Pushes to `main`, manual | Builds and pushes `ghcr.io/<owner>/anum-api:<sha>`, `anum-web:<sha>` and `anum-backup:<sha>`, then `helm upgrade --install` into the `staging` environment once `STAGING_DEPLOY_TARGET=kubernetes` is set. |
| `deploy-production.yml` | Manual, `production` environment approval | Builds the production web bundle for a commit staging deployed, then `helm upgrade --install` with `values-production.yaml`; or `helm rollback` to a given revision. Skipped while `PRODUCTION_DEPLOY_TARGET` is unset. |

The **Docker images** job builds both images without pushing, scans them with Trivy (fixable high and critical vulnerabilities and baked-in secrets fail the job; see [Security](security.md#scanning-in-ci)), checks that the API image and the worker command (`python -m anum_api.worker`) refuse development defaults in production mode and that the worker refuses the in-memory repository outside `local`, runs the worker against a Temporal dev server until it polls and then stops it with SIGTERM, and smoke-tests the API and web containers (health endpoint, non-root user, CSP header).

The **Helm deploy (kind)** job lints the chart (`helm lint --strict`, `kubeconform -strict` against pinned schemas, twelve refusal checks), then installs it into a kind cluster with in-cluster PostgreSQL, NATS and Temporal and smoke-tests the result, including `helm test`, the CronJobs, worker SIGTERM, upgrade and rollback ([Deployment](deployment.md#checks)).

### Staging and production deploys: owner decision needed

`deploy-staging.yml` never runs on pull requests. Its `deploy` job is skipped while the `STAGING_DEPLOY_TARGET` repository variable is unset, so the workflow passes with only the image push until a cluster exists. `deploy-production.yml` is manual and skipped while `PRODUCTION_DEPLOY_TARGET` is unset. To turn them on:

1. Choose the cloud provider and region (see [Production plan](production-plan.md)) and create the cluster and managed services.
2. Run `infra/helm/bootstrap-database.sql` and create the release Secrets in each namespace ([Deployment](deployment.md#secrets)).
3. Create the GitHub `staging` and `production` environments (production with required reviewers), store `STAGING_KUBECONFIG` / `PRODUCTION_KUBECONFIG` or replace the cluster-access step with the cloud's OIDC login, and set `*_DEPLOY_TARGET=kubernetes`, `*_API_URL`, `*_OIDC_ISSUER`, `*_WEB_URL` and `*_HELM_VALUES` ([Deployment](deployment.md#cluster-access)).

## Now

Container images for the API, web and backups, a compose `app` profile with the Temporal worker, CI image builds with smoke tests, a cloud-neutral Helm chart tested in kind on every PR, and staging and production workflows that deploy it with `helm upgrade --install` once a cluster is configured. The worker runs from the API image with a different command; it serves no HTTP port, so compose disables the image health check and the chart gives it no HTTP probe. Next: OpenTofu for the chosen cloud.

## Later

Add blue/green or canary deploys, preview environments per PR, cross-region design, disaster recovery tests, and cost controls.
