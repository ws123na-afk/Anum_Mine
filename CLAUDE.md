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
