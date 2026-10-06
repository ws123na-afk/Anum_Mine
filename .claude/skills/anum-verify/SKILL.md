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
| `apps/desktop/**` | `pnpm build && pnpm check:desktop` (needs Rust; Windows also needs `src-tauri/icons/icon.ico`) |
| `apps/android/**` | `pnpm check:android && pnpm build:android` (needs JDK 17 + Android SDK) |
| `apps/mobile/**` | `cd apps/mobile && flutter pub get && flutter analyze && flutter test` |
| `infra/**` | `docker compose -f infra/docker/compose.yaml config` |
| `.github/workflows/**` | Re-read the job end to end; CI is the only place some jobs run |

## Rules
- A toolchain that is missing locally (Flutter, Android SDK, Windows) is not a pass. Say the check is left to CI.
- Flutter analyzer and test failures count even though CI currently tolerates them.
- Never weaken a test or assertion to get a pass. Fix the code or explain why the test is wrong.
