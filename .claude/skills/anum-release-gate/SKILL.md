---
name: anum-release-gate
description: Checklist of evidence an ANUM release needs before it can be called production-ready (CI, infra smoke tests, credentials, signed artifacts). Use when preparing a release, a staging deploy, or answering "are we ready to ship?".
---

# ANUM release gate

Source of truth: `docs/production-readiness.md` and `docs/production-plan.md`. A gate that was not run stays open and must be listed as open — never report "ready" with open gates.

## Collect, for the exact commit being released
1. CI run URL on that commit with every job green, and no `continue-on-error` left on test or analyze steps.
2. Alembic revision applied (`python -m alembic current` in `services/api`).
3. Web build hash, Playwright report.
4. Android APK/AAB SHA-256, Flutter APK/AAB and iOS build SHA-256, signed desktop installer SHA-256.
5. Staging smoke test: OIDC login (`ANUM_AUTH_MODE=oidc`), create task, approval round-trip, memory write/read, file round-trip through object storage, event published and consumed, workflow resumed after worker restart.
6. Environment where each check ran (local / CI / staging).

## Hard blockers
- `ANUM_AUTH_MODE=headers` in any non-local environment.
- `ANUM_MODEL_PROVIDER=mock` in production.
- Default credentials from `infra/docker/compose.yaml` (anum/anum, admin/admin, `anum-local-secret`) anywhere outside local.
- Any secret in git history or in a client bundle.

Output a table: gate, evidence link or value, status (pass / open / fail).
