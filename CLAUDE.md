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

The Superpowers plugin is enabled in `.claude/settings.json`; prefer its planning, TDD, systematic-debugging and verification-before-completion skills for non-trivial work.
