# Development Standards

ANUM should be built with standards that make future autonomy safe to operate. Code style, tests, contracts, migrations, and reviews are part of the product quality bar.

## General Rules

- Keep implementation aligned with documented boundaries.
- Prefer typed interfaces and generated contracts where practical.
- Avoid hidden global state in agent execution.
- Make tenant context explicit in requests, jobs, events, and tests.
- Treat migrations, policies, and infrastructure as reviewed code.
- Keep examples small and separate from production paths.

## Backend

Python backend code should use FastAPI, typed request and response models, dependency injection for infrastructure boundaries, explicit transactions, and clear domain services. Agent runtime code should be testable without real model providers.

## Frontend

React+TypeScript+Vite code should use shared generated API types, predictable state management, accessible components, and clear task timelines. The web app should be reusable inside Tauri.

Dependencies: every `package.json` names exact versions (no `latest`), and CI installs with `pnpm install --frozen-lockfile`, so a change to any `package.json` must commit the matching `pnpm-lock.yaml` (run `pnpm install` locally; never edit the lockfile by hand). Owner decision of 12 October 2026 ([Full skills audit](audits/2026-10-12-full-skills-audit.md)).

## Testing

The baseline should include unit tests, integration tests with local services, contract tests, RLS tests, authorization tests, workflow tests, and model gateway adapter tests with mocks.

Database tests (`python -m pytest services/api -m database`, from the repository root) rebuild the whole schema of `ANUM_TEST_DATABASE_URL` at the start of the session and truncate every table around each test, so one database serves one run at a time. The session takes a PostgreSQL advisory lock for that: a second run against the same database waits for the first (up to `ANUM_TEST_DATABASE_LOCK_WAIT_SECONDS`, default 1800) and then stops with a message, instead of failing with missing relations, foreign-key violations, deadlocks or hangs. Give parallel runs a database each.

## Reviews

Pull requests should explain behavior changes, data model changes, risk implications, and validation. Security-sensitive changes require extra scrutiny.

## Now

Adopt formatting, linting, type checks, migration review, test conventions, and docs updates for architecture changes.

## Later

Add performance budgets, formal threat-model reviews, evaluation suites, compatibility policies, and release gates.