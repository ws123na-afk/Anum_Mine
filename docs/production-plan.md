# Production Plan

This is the ordered path from the current `main` to a first production release. It turns the open items in the [Roadmap](roadmap.md) and the gates in [Production readiness gates](production-readiness.md) into stages with exit criteria. Each stage ends with working software and green CI; do not start a later stage's production work while an earlier stage's exit criteria are open.

## Where Things Stand (October 2026)

Status on `main` after PR #12 (Stages 2 to 5, code side). Every CI job is green with no tolerated failures.

| Area | State |
|---|---|
| CI | 15 checks: web, contracts, API unit, PostgreSQL/RLS, API integration (Valkey, S3, Temporal, NATS, none may skip), Authenticated journey (Keycloak + PostgreSQL + NATS end to end), Docker images (build, Trivy scan, smoke test, startup refusals, worker smoke test), Security scans (pip-audit, pnpm audit, bandit, gitleaks, OSV-Scanner, cargo audit), CodeQL (Python, JS/TS), Browser end-to-end, Flutter, Android, Tauri desktop, compose config. Branch protection is not yet required on `main` (owner action). |
| Identity | Keycloak realm as code; `oidc` mode with JWKS rotation and persisted membership roles; PKCE sign-in in web, desktop and Flutter; invitations and member management with last-owner protection. Header and local sessions are refused outside `local`/`test`. |
| API | Request limits, rate limiting, security headers, fail-fast startup checks. Model gateway with Ollama/OpenAI-compatible, retries, cost accounting, redacted logs and per-workspace configs in PostgreSQL (Fernet-encrypted keys). |
| Events and runtime | NATS JetStream with a restart-durable PostgreSQL outbox and narrow relay role; tenant-filtered SSE. Valkey run locks and shared rate limits; S3-compatible file storage (SeaweedFS locally); Temporal worker for durable runs. All adapters are off by default and exercised in CI. |
| Clients | Flutter (real data, depth design, wake-by-name voice) is the shipping Android and iOS app; web/desktop (voice, WebGL orb); the Kotlin Android client is frozen. None signed or device-verified yet. |
| Deployment | API and web images, compose `app` profile with a worker, `deploy-staging.yml` pushing to GHCR. No cloud, OpenTofu or staging environment yet: waits on the owner's cloud choice. |
| Operations | OpenTelemetry in API and worker with a local Prometheus/Tempo/Loki/Grafana profile, dashboards and tested alert rules; backup and restore drill tooling; threat model and runbooks. Production telemetry backend, paging and scheduled encrypted backups wait on the cloud choice. |
| Still in memory | Control-plane stores are in PostgreSQL with RLS when `ANUM_REPOSITORY_BACKEND=postgresql` (migration `0008_control_plane_stores`). Still process-local: voice sessions, local-only auth state (local sessions, OTP and password-reset challenges, which are refused outside `local`/`test`), and the automation engine's local SQLite file. |

## Stage 1: Green and Honest CI

Goal: every CI job passes without tolerated failures.

- Add the Tauri icon set (`tauri icon` from a 1024px source) so the Windows desktop job compiles. Done.
- Fix the six `AnumOperationalCard` status semantics failures and the Arabic RTL localization test in `apps/mobile/test`. Done.
- Clear the Flutter analyzer issues (unused imports, deprecated `Radio`/form APIs, relative `lib` imports in tests). Done.
- Remove `continue-on-error` from the Flutter analyze and test steps. Done.
- Upgrade GitHub Actions that still target Node 20. Done.
- Track plugin warnings for `file_picker`, `flutter_tts` and `speech_to_text` (Kotlin Gradle Plugin migration).

Exit: a CI run on `main` with all jobs green and no tolerated failures. Branch protection requires CI on `main`.

## Stage 2: Finish the Phase 1 Vertical Slice

Goal: the documented thin slice works against real services, not stubs.

- Keycloak realm, clients (web, desktop, Android, Flutter) and roles as code, imported by the local compose stack.
- `ANUM_AUTH_MODE=oidc` end to end: token validation, membership lookup, tenant and workspace resolution. Header mode is rejected outside `local`. Workspace invitations and owner-managed memberships (audited, last-owner protected) replace the bootstrap path for multi-user workspaces.
- Client sign-in through Keycloak (authorization code + PKCE, refresh, sign-out) in web, desktop and Flutter, with the local session kept for development. Done; the Kotlin Android client and a CI journey against a real Keycloak remain.
- NATS JetStream publisher for canonical events plus a consumer that drives the web realtime status stream ([Events](events.md), [Realtime](realtime.md)). A restart-durable PostgreSQL outbox relay (multi-instance safe, narrow relay role) is in.
- One real model provider behind the gateway with timeouts, retries, cost accounting and redacted logging ([Model gateway](model-gateway.md)). Ollama and OpenAI-compatible adapters with timeouts, bounded retries with backoff, token and estimated-cost accounting, redacted call logging, and per-workspace model configs persisted in PostgreSQL (RLS, Fernet-encrypted keys via `ANUM_SECRETS_KEY`) are in; a real hosted-provider run in CI remains.
- CI job that starts Keycloak, Postgres and NATS and runs an authenticated task journey. Done: the "Authenticated journey" job runs `services/api/scripts/run_journey.sh` (see [Identity and sign-in](identity.md#local-use)).

Exit: a user signs in through Keycloak, creates a task, sees live status, approves the risky sample action, and the run is persisted, all in CI.

Exit status: met in CI once the Authenticated journey job is green on `main`. The job signs in as the realm's `dev` user through `anum-web` with authorization code + PKCE (login form posted by a script, no browser), bootstraps onboarding, opens the SSE stream, creates a task whose prompt triggers the high-risk `external.action` tool, runs it, approves it, and checks that `task.created`, `approval.requested`, `approval.approved` and `agent_run.completed` arrive over SSE in order, are on the `ANUM_EVENTS` JetStream stream, and that the task, run, steps, approval and events are rows in PostgreSQL visible to the non-superuser `anum_app` role only inside the tenant's RLS scope. The journey found and fixed one bug: SSE streams never ended after their client disconnected (see [Realtime](realtime.md#implementation)). Still open in Stage 2: a real hosted-provider run in CI (the journey uses the mock model provider), and the web, desktop and mobile clients' own Keycloak sign-in flows (the journey signs in as the web client would, without the web UI).

## Stage 3: Durable Runtime and Storage

Goal: agents are resumable, cancellable and auditable across restarts ([Agent runtime](agent-runtime.md), [Automation](automation.md)).

- Temporal worker for long-running runs; a test kills the worker mid-run and asserts resume. Done in code: `ANUM_RUNTIME_BACKEND=temporal`, `AgentRunWorkflow` with one checkpoint-driven activity, the worker entrypoint and a compose `worker` service ([Agent runtime](agent-runtime.md#durable-execution)). The worker-restart test (`tests/test_temporal_worker.py`, marker `temporal`) runs in the CI "API integration" job against a dev server the SDK starts; `tests/test_postgres_durable_runs.py` covers the same crash/resume path on PostgreSQL with RLS. The CI "Docker images" job smoke-tests the worker command in the API image (see Stage 4).
- Valkey for locks, rate limits and ephemeral coordination, with a distributed-lock test. Done: `ANUM_RUN_LOCK_BACKEND=valkey` and `ANUM_RATE_LIMIT_BACKEND=valkey`; `tests/test_valkey_integration.py` (marker `valkey`) includes a contention test and runs in the CI "API integration" job.
- S3-compatible object storage for workspace files with a round-trip test against an S3-compatible server ([Workspace files](files.md)). Done in code: `ANUM_OBJECT_STORAGE_BACKEND=s3`, tenant/workspace key prefixes, moto-backed tests, and an `s3`-marked round trip that runs in the CI "API integration" job against SeaweedFS (MinIO no longer publishes community images).
- Move remaining in-memory control-plane stores (skills, governance, integrations) to PostgreSQL with RLS and migrations. File metadata belongs here too. Done: migration `0008_control_plane_stores` adds twelve RLS-forced tables for skill versions and installations, policy packs, role templates, approval rules, memory governance, the marketplace catalog and installs, routing targets, integration configurations, workspace file metadata (bytes stay in object storage) and notification preferences; governance changes are audited into the append-only `audit_records` table in the same transaction. Each store keeps its in-memory implementation for `ANUM_REPOSITORY_BACKEND=memory` ([Multi-tenancy](multi-tenancy.md#control-plane-stores)). `tests/test_postgres_control_plane.py` covers restart persistence and cross-tenant and cross-workspace isolation under the non-owner app role. Open: the automation engine still keeps workflows, schedules and runs in a local SQLite file ([Automation](automation.md)).

Exit: the infrastructure gates in [Production readiness gates](production-readiness.md) pass in CI.

## Stage 4: Packaging and Environments

Goal: the system can be deployed reproducibly ([Infrastructure](infrastructure.md)).

- Dockerfiles for the API and Temporal worker; static web bundle served from a CDN or container. API and web images done (non-root, health checks, built and smoke-tested in CI); the worker runs from the API image with `python -m anum_api.worker` (no separate Dockerfile). CI checks that the worker refuses development defaults and the in-memory repository outside `local`, fails with a clear error when Temporal is unreachable, and polls a Temporal dev server until SIGTERM. After SIGTERM the worker shuts down cleanly and exits 0: it skips interpreter finalization (`os._exit(0)` after a clean shutdown), which used to hang in 3 of 14 runs while the Temporal SDK's native objects were collected; CI asserts exit code 0.
- OpenTofu for network, compute, managed PostgreSQL with pgvector, object storage, secrets, DNS and TLS, with remote locked state.
- Environments: preview (per PR, optional), staging, production, each with separate secrets, databases, buckets and Keycloak realm.
- Deploy workflow: build, migrate, deploy to staging automatically; production behind GitHub environment approval. Partly done: `deploy-staging.yml` builds and pushes images to GHCR on every push to `main`; the migrate and deploy steps are placeholders gated on the `staging` environment and `STAGING_DEPLOY_TARGET` until the cloud is chosen. No production workflow yet.
- Secrets only from the deployment secret store. Rotate every default credential from `infra/docker/compose.yaml`. The API now refuses to start outside `local` with the compose database credentials, the compose S3 default secret, or localhost/wildcard CORS origins; Keycloak's `admin/admin` is not yet checked.

Exit: a push to `main` deploys to staging and passes a smoke test (login, task, approval, memory, file, event, workflow resume).

## Stage 5: Security and Operations Hardening

Goal: safe to hold real user data ([Security](security.md), [Observability](observability.md)).

- Threat model for agent tool use and prompt injection; review approval and risk policies ([Approvals and risk](approvals-and-risk.md)). Done: [Threat model](threat-model.md) with assets, trust boundaries, STRIDE threats with code references, and the approval policy review. Open from it: SSRF guard on workspace model `base_url` (G1), approvals that show and bind the exact tool arguments and expire (A1 to A3), per-tenant model budgets (G4).
- Dependency, container and secret scanning in CI; SAST for Python, TypeScript, Dart and Rust. Done: pip-audit, `pnpm audit --prod` (high and above), OSV-Scanner (pnpm lockfile, resolved API dependencies, Flutter `pubspec.lock`), `cargo audit`, gitleaks, Trivy on the API and web images (fixable high and critical, plus secrets), bandit and CodeQL (Python, JavaScript/TypeScript), `flutter analyze --fatal-infos --fatal-warnings` (Dart), `cargo clippy -D warnings` (Rust); third-party actions pinned to commit SHAs and Dependabot for every ecosystem ([Security](security.md#scanning-in-ci)). Open: stricter Dart analyzer modes (`strict-casts`, `strict-inference`, `strict-raw-types`) once their findings are fixed, and scanning the images `deploy-staging.yml` pushes.
- OpenTelemetry traces, metrics and logs exported from the collector to a real backend, with dashboards and alerts for errors, latency, queue depth and model cost. Done in code ([Observability](observability.md#implementation)): API and worker export OTLP when `ANUM_OTEL_EXPORTER_OTLP_ENDPOINT` is set (traces across requests, model calls, outbound HTTP, SQL, NATS publish and Temporal; request, model cost/tokens, outbox backlog, rate-limit, run-lock and activity metrics; logs with trace and correlation ids; redaction at export, tested); the compose `observability` profile runs Prometheus, Tempo, Loki and Grafana with three provisioned dashboards and twelve `promtool`-tested alert rules. Open: a production backend and Alertmanager routing to on-call, which wait on the cloud choice.
- Backups with a restore drill; documented incident and on-call runbooks. Done in code: `infra/backup/anum_backup.py` (consistent `pg_dump` with a manifest, restore into a fresh `anum_restore_*` database, verification of counts per tenant and of RLS isolation), run in the PostgreSQL CI job by `tests/test_backup_restore.py` and drilled locally on PostgreSQL 16; [Runbooks](runbooks.md) for incidents, on-call, each alert, restore, and `ANUM_SECRETS_KEY` and Keycloak key rotation; RPO/RTO proposals. Open: scheduled backups to encrypted storage, point-in-time recovery and a drill on production-sized data, which need the cloud account; a re-encryption command for `ANUM_SECRETS_KEY` rotation.
- Rate limiting, request size limits, CORS and CSP locked to production origins. Done in code: limits and headers in the API, CSP and security headers in the web container, startup checks that reject wildcard, localhost and non-https CORS origins outside `local`. Open: Valkey-backed rate limits shared across replicas, and setting the real origins once domains exist.
- External penetration test before general availability.

Exit: restore drill and pen-test findings closed or accepted in writing.

## Stage 6: Client Release

Goal: shippable, signed clients on every surface ([Desktop](desktop.md), [Android](android.md), [Flutter mobile](mobile.md)).

- Decide whether the Kotlin Android client or the Flutter client is the shipping Android app; retire or freeze the other. Done: Flutter (`apps/mobile`, `com.anum.app`) ships on Android and iOS; the Kotlin client is frozen ([Android](android.md#status-frozen)).
- Commit generated Flutter platform folders instead of running `flutter create` in CI. Done: `android/` and `ios/` are committed and configured by `tool/configure_native.dart`; a test fails if they drift ([Flutter mobile](mobile.md#native-projects)).
- Android release AAB signed with Play App Signing; iOS build with signing, provisioning and TestFlight; desktop installers signed (Windows Authenticode, macOS notarization) with Tauri updater keys. Code side done: the Android `release` signing config reads the upload key from the environment or a gitignored `key.properties` and CI builds a (debug-signed) release bundle ([Flutter mobile](mobile.md#release-builds)); the desktop release overlay takes the updater public key, endpoint and Windows certificate thumbprint from the environment ([Desktop](desktop.md#release-builds)). Open, needs the owner's accounts: the upload keystore, Play App Signing enrolment, Apple team, provisioning profiles and TestFlight, the Authenticode certificate, Apple notarization credentials, the updater key pair and endpoint, and registering the `tauri-plugin-updater` runtime check.
- Desktop sign-in in the system browser with an RFC 8252 loopback redirect instead of the webview. Done in code ([Desktop](desktop.md#sign-in)); a round trip against a running Keycloak is on the device checklist.
- Real-device test matrix including Arabic RTL, 200 percent text scale and voice permissions. The checklist is written ([Flutter mobile](mobile.md#real-device-test-checklist)); running it needs devices and is open.
- Store listings, privacy policy, data-safety forms, and production API URLs passed via `--dart-define` or build flavours. Production defines are documented (`--dart-define-from-file`, [Flutter mobile](mobile.md#release-builds)); listings, policy and forms are open.

Exit: internal testing tracks (Play internal, TestFlight, desktop beta channel) running against staging.

## Stage 7: Launch

- Run the release gate: every item in [Production readiness gates](production-readiness.md) recorded with evidence for the release commit.
- Production deploy with rollback plan, then staged client rollout.
- Post-launch: error budget review after one week, then resume Phase 4 and 5 work ([Governance and scale](governance-and-scale.md), [Scaling](scaling.md)).

## Credentials and Decisions Needed From the Owner

These cannot be produced from the repository and block the stages noted.

| Item | Needed by |
|---|---|
| Cloud provider and region choice | Stage 4 |
| Domain names and DNS access | Stage 4 |
| Model provider account and API key | Stage 2 |
| Production Keycloak hosting decision (self-hosted or managed) | Stage 2 |
| Telemetry backend (managed or self-hosted) and a paging/on-call tool | Stage 5 |
| Confirmed RPO/RTO targets, backup retention and backup storage location ([Runbooks](runbooks.md#recovery-objectives)) | Stage 5 |
| Model spend budget (replaces the placeholder `AnumModelHourlySpendHigh` threshold) | Stage 5 |
| Apple Developer and Google Play accounts | Stage 6 |
| Code-signing certificates for Windows and macOS | Stage 6 |
| Notification provider credentials (FCM, APNs) | Stage 6 |
| Privacy policy and terms of service | Stage 6 |
