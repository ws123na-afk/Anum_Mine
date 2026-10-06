# Production Plan

This is the ordered path from the current `main` to a first production release. It turns the open items in the [Roadmap](roadmap.md) and the gates in [Production readiness gates](production-readiness.md) into stages with exit criteria. Each stage ends with working software and green CI; do not start a later stage's production work while an earlier stage's exit criteria are open.

## Where `main` Stands (October 2026)

| Area | State |
|---|---|
| CI | Red since late August. 7 of 8 jobs pass. Tauri desktop fails on Windows because `apps/desktop/src-tauri/icons/icon.ico` is missing. |
| Flutter | APK builds, but 7 of 27 tests fail and the analyzer reports 29 issues. Both steps run with `continue-on-error`, so CI hides it. |
| API | Unit and PostgreSQL/RLS suites pass. Auth still defaults to development headers (`ANUM_AUTH_MODE=headers`); an OIDC validator exists but is not exercised end to end. |
| Model gateway | Mock and OpenAI-compatible adapters. Default provider is `mock`. |
| Infra adapters | Valkey, NATS, Temporal and object storage appear only as settings and health probes. No client code, workers, or durable event consumers. |
| Deployment | No Dockerfiles, no OpenTofu, no deploy workflow, no staging environment. |
| Clients | Web, desktop, Android and Flutter sources exist; none are signed or device-verified. |

## Stage 1: Green and Honest CI

Goal: every CI job passes without tolerated failures.

- Add the Tauri icon set (`tauri icon` from a 1024px source) so the Windows desktop job compiles.
- Fix the six `AnumOperationalCard` status semantics failures and the Arabic RTL localization test in `apps/mobile/test`.
- Clear the Flutter analyzer issues (unused imports, deprecated `Radio`/form APIs, relative `lib` imports in tests).
- Remove `continue-on-error` from the Flutter analyze and test steps.
- Upgrade GitHub Actions that still target Node 20.
- Track plugin warnings for `file_picker`, `flutter_tts` and `speech_to_text` (Kotlin Gradle Plugin migration).

Exit: a CI run on `main` with all jobs green and no tolerated failures. Branch protection requires CI on `main`.

## Stage 2: Finish the Phase 1 Vertical Slice

Goal: the documented thin slice works against real services, not stubs.

- Keycloak realm, clients (web, desktop, Android, Flutter) and roles as code, imported by the local compose stack.
- `ANUM_AUTH_MODE=oidc` end to end: token validation, membership lookup, tenant and workspace resolution. Header mode is rejected outside `local`.
- NATS JetStream publisher for canonical events plus a consumer that drives the web realtime status stream ([Events](events.md), [Realtime](realtime.md)).
- One real model provider behind the gateway with timeouts, retries, cost accounting and redacted logging ([Model gateway](model-gateway.md)).
- CI job that starts Keycloak, Postgres and NATS and runs an authenticated task journey.

Exit: a user signs in through Keycloak, creates a task, sees live status, approves the risky sample action, and the run is persisted, all in CI.

## Stage 3: Durable Runtime and Storage

Goal: agents are resumable, cancellable and auditable across restarts ([Agent runtime](agent-runtime.md), [Automation](automation.md)).

- Temporal worker for long-running runs; a test kills the worker mid-run and asserts resume.
- Valkey for locks, rate limits and ephemeral coordination, with a distributed-lock test.
- S3-compatible object storage for workspace files with a round-trip test against MinIO ([Workspace files](files.md)).
- Move remaining in-memory control-plane stores (skills, governance, integrations) to PostgreSQL with RLS and migrations.

Exit: the infrastructure gates in [Production readiness gates](production-readiness.md) pass in CI.

## Stage 4: Packaging and Environments

Goal: the system can be deployed reproducibly ([Infrastructure](infrastructure.md)).

- Dockerfiles for the API and Temporal worker; static web bundle served from a CDN or container.
- OpenTofu for network, compute, managed PostgreSQL with pgvector, object storage, secrets, DNS and TLS, with remote locked state.
- Environments: preview (per PR, optional), staging, production, each with separate secrets, databases, buckets and Keycloak realm.
- Deploy workflow: build, migrate, deploy to staging automatically; production behind GitHub environment approval.
- Secrets only from the deployment secret store. Rotate every default credential from `infra/docker/compose.yaml`.

Exit: a push to `main` deploys to staging and passes a smoke test (login, task, approval, memory, file, event, workflow resume).

## Stage 5: Security and Operations Hardening

Goal: safe to hold real user data ([Security](security.md), [Observability](observability.md)).

- Threat model for agent tool use and prompt injection; review approval and risk policies ([Approvals and risk](approvals-and-risk.md)).
- Dependency, container and secret scanning in CI; SAST for Python, TypeScript, Dart and Rust.
- OpenTelemetry traces, metrics and logs exported from the collector to a real backend, with dashboards and alerts for errors, latency, queue depth and model cost.
- Backups with a restore drill; documented incident and on-call runbooks.
- Rate limiting, request size limits, CORS and CSP locked to production origins.
- External penetration test before general availability.

Exit: restore drill and pen-test findings closed or accepted in writing.

## Stage 6: Client Release

Goal: shippable, signed clients on every surface ([Desktop](desktop.md), [Android](android.md), [Flutter mobile](mobile.md)).

- Decide whether the Kotlin Android client or the Flutter client is the shipping Android app; retire or freeze the other.
- Commit generated Flutter platform folders instead of running `flutter create` in CI.
- Android release AAB signed with Play App Signing; iOS build with signing, provisioning and TestFlight; desktop installers signed (Windows Authenticode, macOS notarization) with Tauri updater keys.
- Real-device test matrix including Arabic RTL, 200 percent text scale and voice permissions.
- Store listings, privacy policy, data-safety forms, and production API URLs passed via `--dart-define` or build flavours.

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
| Apple Developer and Google Play accounts | Stage 6 |
| Code-signing certificates for Windows and macOS | Stage 6 |
| Notification provider credentials (FCM, APNs) | Stage 6 |
| Privacy policy and terms of service | Stage 6 |
