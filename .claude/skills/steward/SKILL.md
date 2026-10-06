---
name: steward
description: Repo-specific rules for driving an ANUM pull request or main branch from red CI to green and mergeable. Use when CI fails, a review arrives, or a PR has a merge conflict.
---

# Stewarding ANUM PRs

## Order of work
1. Merge conflicts first: merge `main` into the branch (no rebase or force-push on someone else's branch). Regenerate `pnpm-lock.yaml` with `pnpm install`, never by hand.
2. Red CI next. Read the failing job's log and find the first real error, not the last line. Reproduce it locally with the `anum-verify` row for that path.
3. Review comments last. Do small asks directly; propose larger ones in a reply.

## Known CI jobs (`.github/workflows/ci.yml`)
- **Web and contracts**: `pnpm docs:check`, `pnpm check`, `pnpm build`.
- **API unit tests** / **API PostgreSQL persistence tests**: pytest, split by the `database` marker. Migrations run via Alembic first.
- **Browser end-to-end**: Playwright against the built web app.
- **Docker Compose config**: `docker compose config`, with and without `--profile app`.
- **Security scans**: pip-audit, `pnpm audit --prod --audit-level high`, bandit, gitleaks (`.gitleaks.toml`). Fix the finding; any exception needs a narrowly scoped ignore and a row in `docs/security.md`.
- **Docker images**: builds the API and web images, checks the API refuses dev defaults in production mode, smoke-tests both containers.
- **Tauri desktop** (Windows): fails if `apps/desktop/src-tauri/icons/` is missing. Regenerate with `pnpm --filter @anum/desktop exec tauri icon <1024px png>`.
- **Android client**: Gradle unit tests + debug APK.
- **Flutter mobile**: generates native wrappers with `flutter create`, then analyze, test, build APK.

## Never
- Add `continue-on-error`, skip, or delete a test to get green. The Flutter analyze/test steps currently have `continue-on-error`; removing it is a goal, adding more is not.
- Push an empty commit or re-run more than once to "fix a flake". A second failure is real.
- Commit secrets, signing keys, or real `.env` files.
