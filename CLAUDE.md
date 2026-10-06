# ANUM — guidance for Claude Code

ANUM is a monorepo: FastAPI API (`services/api`), React/Vite web (`apps/web`), Tauri desktop (`apps/desktop`), Kotlin Android (`apps/android`), Flutter mobile (`apps/mobile`), shared TS contracts (`packages/contracts`), local infra (`infra/docker`).

## Rules
- Docs are the spec. Read the matching file in `docs/` before changing a boundary, and update it in the same change.
- Every new doc in `docs/` must be linked from the README index or `pnpm docs:check` fails.
- Tenant context is explicit everywhere: requests, jobs, events, tests. Never bypass RLS or the runtime's tool mediation.
- Never commit secrets. `.env.example` files hold placeholders only.
- Never mark a check `continue-on-error`, skip, or delete a test to get CI green.
- `docs/production-plan.md` is the ordered path to production; check it before picking up new work.

## Skills in this repo
- `anum-verify` — run the right local checks for what you changed.
- `steward` — how to drive a red PR or `main` back to green.
- `anum-release-gate` — the evidence a release needs before it ships.

Plugins enabled in `.claude/settings.json`: Superpowers, `frontend-design` and `code-review`. Prefer Superpowers' planning, TDD, systematic-debugging and verification-before-completion skills for non-trivial work.

## Things learned the hard way
- `local` and `test` are the only development environments. Outside them the API refuses header auth, `anum_local_*` sessions, a missing `ANUM_SECRETS_KEY`, compose credentials and unsafe CORS. Tests that build non-local `Settings` must pass a Fernet `secrets_key`.
- Streaming responses outlive request dependencies: never read through the request's repository session inside an SSE body (use `list_events_for_stream`).
- In PostgreSQL + NATS mode, events publish through the `anum_outbox_relay` role; give the relay its own login via `ANUM_OUTBOX_DATABASE_URL`.
- MinIO no longer publishes community images; the compose `s3` service is SeaweedFS.
- New CI scanners: bandit flags variables named `token`/`password` compared to literals and bare `assert`; gitleaks scans the full history, so a fake key committed once needs a narrowly scoped `.gitleaks.toml` entry.
- Integration-marked tests (`valkey`, `s3`, `temporal`, `nats`) must not skip in CI; the "API integration" job fails on any skip.
- A log-record attribute with the same name as an `extra=` key makes the logging call raise `KeyError`; telemetry fields are prefixed `anum_` for that reason.
- The Temporal worker exits with `os._exit(0)` after a clean shutdown: interpreter finalization can hang collecting the SDK's native objects. CI asserts exit code 0.
- Run database tests the way CI does: from the repository root (`python -m pytest services/api -m database`) against a PostgreSQL that requires a password for 127.0.0.1. A local `trust` cluster run from `services/api` hid a CI-only hang.
- Background tasks that hold a database transaction (the outbox relay) must finish their pass on shutdown, not be cancelled mid-commit; run session calls through `outbox_relay._in_thread` so cancellation never races a commit.
- Parallel branches that each add a migration will collide on the number: renumber at merge into one chain (and fix every reference). Alembic revision ids must fit in 32 characters.
- Cross-tenant jobs (retention purge, scheduler, key rotation, outbox) find work through a narrow NOLOGIN role (`anum_maintenance`, `anum_outbox_relay`) and then act as the app role inside each tenant's context. Never BYPASSRLS, never SECURITY DEFINER over forced-RLS tables.
- A row policy `TO some_role` also applies to every login that *inherits* that role, so granting a narrow NOLOGIN role whose policies widen visibility (`anum_membership_reader`, `anum_maintenance`) with the default INHERIT widens the app role's own queries. Grant such roles `WITH INHERIT FALSE, SET TRUE` and reach them only with `SET LOCAL ROLE`.
- Approvals require the payload hash the client displayed (`POST /approve {"payload_hash": ...}`); tests and scripts that approve must send it.
- gitleaks' `generic-api-key` rule also fires on realistic-looking fake tokens in TypeScript and Dart tests (a mixed-case value with digits and dashes). Use plainly fake, low-entropy fixtures (a lowercase word such as "example" after the prefix); if one was committed, squash it out before pushing, because CI scans the history.
- The Helm chart mirrors the API's startup refusals (`infra/helm/ci/lint.sh`). A new refusal in `config.py` needs a matching chart check, and a chart value the API would refuse must fail `helm lint`, not the pod.
- `helm rollback` never reverses migrations: every migration must be expand/contract, so the previous release still runs against the new schema.
- Parallel branches all edit the status table in `docs/production-plan.md`. Resolve that conflict row by row, keeping both sides' facts, and recount the CI checks.
- FastAPI 0.142+ keeps included routers nested in `app.routes` (`_IncludedRouter` has no `.path`). Read routes from `app.openapi()["paths"]` (no route is excluded from the schema) or from the router itself.
- A Playwright bump needs a browser this sandbox cannot download (`playwright install` is off-limits here): run unit tests and the build locally and let CI's "Browser end-to-end" run the new version.
- If the shared branch is rewritten while agents work from it, bring their work over with `git cherry-pick <agent commit>`, not a merge, so the rewritten commit does not come back.
- Flutter plugins that rely on AGP 9 built-in Kotlin (for example `file_picker` 11) compile nothing while `android.builtInKotlin=false`; the analyzer and tests still pass and only CI's APK build fails (`cannot find symbol <Plugin>`). Check a plugin's `android/build.gradle` before a major bump.
- The local PostgreSQL test cluster in this sandbox stops when the container is recycled; check `pg_isready -h 127.0.0.1 -p 55450` before database tests and restart it (as the `postgres` user) rather than reporting the tests as passed or skipped.
- A Kubernetes admission controller's pod can be Ready before its webhook serves; scripts that create policies right after installing it must wait for a server-side dry run to pass (`infra/helm/ci/kind-smoke.sh`).
