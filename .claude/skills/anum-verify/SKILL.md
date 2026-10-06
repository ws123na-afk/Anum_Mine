---
name: anum-verify
description: Run ANUM's local checks for the parts of the monorepo you changed (docs, contracts, web, API, Postgres, desktop, Android, Flutter) before committing or pushing. Use before every push and whenever asked "does it pass?".
---

# Verify ANUM changes

Pick the rows that match the changed paths (`git diff --name-only origin/main...`). Run them from the repo root. Report each command and its result; a skipped row must be reported as skipped, with the reason.

| Changed path | Commands |
|---|---|
| `README.md`, `docs/**` | `pnpm docs:check` |
| `packages/contracts/**`, `apps/web/**` | `pnpm install && pnpm check && pnpm build` |
| `apps/web/**` (UI behaviour) | `pnpm test:e2e` (Playwright; Chromium is preinstalled in cloud sessions) |
| `services/api/**` | `python -m pip install -e "services/api[test]" && python -m pytest services/api -m "not database"` |
| `services/api/migrations/**`, `services/api/anum_api/db/**` | Start Postgres (`docker compose -f infra/docker/compose.yaml up -d postgres`), then in `services/api`: `python -m alembic upgrade head`, then `ANUM_TEST_DATABASE_URL=postgresql+psycopg://anum:anum@localhost:5432/anum python -m pytest services/api -m database` |
| `services/api/anum_api/**` (auth, tasks, approvals, events, realtime, persistence), `services/api/scripts/journey.py`, `services/api/scripts/run_journey.sh`, `infra/keycloak/**`, `infra/docker/compose.yaml` | Authenticated journey (the CI job of that name): `bash services/api/scripts/run_journey.sh` with `services/api[test]` installed (`PYTHON=<interpreter>` if it is not `python`). It starts `postgres`, `nats` and `keycloak` from the compose file, migrates, creates the `anum_app` role, runs the API (`oidc`, `postgresql`, `nats`) on port 8000 and runs the journey; it needs ports 5432, 4222, 8222, 8080 and 8000 free. Pass when the last line is `[journey] PASSED: ...`. `JOURNEY_DOWN=1` removes the containers and volumes afterwards; otherwise run `docker compose -f infra/docker/compose.yaml down -v` before the next fresh run. The API log is at `$JOURNEY_API_LOG` (default `/tmp/anum-journey-api.log`). If Docker Hub rate-limits pulls, pull `mirror.gcr.io/library/nats:2.10-alpine` and `mirror.gcr.io/pgvector/pgvector:pg16` and `docker tag` them to the compose image names; this is for local runs only, CI pulls the compose images directly |
| `services/api/anum_api/valkey.py`, `files.py`, `durable_runs.py`, `temporal_workflow.py`, `worker.py`, `event_bus.py`, `outbox_relay.py` | API integration (the CI job of that name): `docker compose -f infra/docker/compose.yaml up -d valkey s3 nats`, then in `services/api` run `ANUM_TEST_VALKEY_URL=redis://127.0.0.1:6379/15 ANUM_TEST_S3_ENDPOINT=http://127.0.0.1:9000 ANUM_TEST_NATS_URL=nats://127.0.0.1:4222 python -m pytest -m "(valkey or s3 or temporal or nats) and not database" -rs`. Temporal starts a dev server through the SDK (or set `ANUM_TEST_TEMPORAL_TARGET`, or `ANUM_TEST_TEMPORAL_CLI` to a local `temporal` binary). Pass only with no `SKIPPED` lines: CI fails the job if any integration test skips |
| `apps/desktop/**` | `pnpm build && pnpm check:desktop` (needs Rust; Windows also needs `src-tauri/icons/icon.ico`) |
| `apps/android/**` | `pnpm check:android && pnpm build:android` (needs JDK 17 + Android SDK) |
| `apps/mobile/**` | `cd apps/mobile && flutter pub get && flutter analyze --fatal-infos --fatal-warnings && flutter test` |
| `infra/**` | `docker compose -f infra/docker/compose.yaml config` and `docker compose -f infra/docker/compose.yaml --profile app config` |
| `services/api/Dockerfile`, `apps/web/Dockerfile`, `apps/web/nginx/**` | `docker build -t anum-api services/api` and `docker build -f apps/web/Dockerfile -t anum-web .` |
| `services/api/**`, `package.json`, `pnpm-lock.yaml` (security) | `pip-audit --strict services/api`, `bandit -r services/api/anum_api services/api/migrations`, `pnpm audit --prod --audit-level high`, and OSV-Scanner as in the "Dependency vulnerability scan (OSV-Scanner)" step of `ci.yml` (`go install github.com/google/osv-scanner/v2/cmd/osv-scanner@v2.6.0`; needs Go 1.27). If `api.osv.dev` is blocked, add `--offline-vulnerabilities --download-offline-databases` |
| `services/api/Dockerfile`, `apps/web/Dockerfile` (scan) | After building as `anum-api:ci` / `anum-web:ci`: `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.75.0 image --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 <image>` (use the digest from `ci.yml`). Fix findings in the Dockerfile; exceptions need `.trivyignore` and a `docs/security.md` row |
| `services/api/Dockerfile`, `services/api/anum_api/worker.py`, `settings.py`, `hardening.py`, `identity.py` | Worker image smoke test: run the "Worker refuses ..." and "Worker polls Temporal ..." steps of the `images` job in `ci.yml` (copy the `run:` blocks; set `TEMPORAL_IMAGE`). If Docker Hub rate-limits pulls, use the same tag from `mirror.gcr.io` |
| `apps/desktop/src-tauri/**` | `cargo clippy --manifest-path apps/desktop/src-tauri/Cargo.toml --locked --all-targets -- -D warnings` (Linux needs the WebKitGTK 4.1 dev packages and `apps/web/dist`) and `cargo install cargo-audit --locked --version 0.22.2 && cargo audit --file apps/desktop/src-tauri/Cargo.lock` |
| `apps/mobile/pubspec.*` | After `flutter pub get`: `osv-scanner scan source --config osv-scanner.toml --lockfile apps/mobile/pubspec.lock` |
| `.github/workflows/**`, `.github/dependabot.yml` | `actionlint` over the workflows, then re-read the job end to end; CI is the only place some jobs run. Pin any new non-`actions/*` action to a full commit SHA with a `# vX.Y.Z` comment |

## Rules
- A toolchain that is missing locally (Flutter, Android SDK, Windows) is not a pass. Say the check is left to CI.
- Flutter analyzer and test failures count even though CI currently tolerates them.
- Never weaken a test or assertion to get a pass. Fix the code or explain why the test is wrong.
