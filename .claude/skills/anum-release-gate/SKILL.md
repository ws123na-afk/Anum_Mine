---
name: anum-release-gate
description: Checklist of evidence an ANUM release needs before it can be called production-ready (CI, infra smoke tests, credentials, signed artifacts). Use when preparing a release, a staging deploy, or answering "are we ready to ship?".
---

# ANUM release gate

Source of truth: `docs/production-readiness.md` and `docs/production-plan.md`. A gate that was not run stays open and must be listed as open — never report "ready" with open gates.

## Collect, for the exact commit being released
1. CI run URL on that commit with every job green, and no `continue-on-error` left on test or analyze steps.
2. Alembic revision applied: the migration hook's log (`kubectl -n <ns> logs job/anum-migrate`) and `select version_num from alembic_version`, or `python -m alembic current` in `services/api`.
3. Web build hash, Playwright report.
4. Android APK/AAB SHA-256, Flutter APK/AAB and iOS build SHA-256, signed desktop installer SHA-256.
5. Client release run: the `Release clients` workflow run URL for the `vX.Y.Z` tag on that commit, with:
   - no "skipped" notices in its summary (each one means an unsigned or missing artifact; list it as open);
   - the AAB and IPA SHA-256 from the summary, matching the `anum-android-release` and `anum-ios-release` artifacts, and `keytool -printcert -jarfile` showing the upload key, not `CN=Android Debug`;
   - the Play internal-track version code and the TestFlight build number that this run uploaded;
   - per desktop platform: installer SHA-256, Authenticode signature (`Get-AuthenticodeSignature` reports `Valid`) and macOS notarization (`spctl -a -vv` reports `Notarized Developer ID`);
   - the draft GitHub release with `latest.json` listing every shipped platform, and an update installed from the previous published version on Windows and macOS through "Check for updates".
6. Real-device checklist in `docs/mobile.md` filled in for the build numbers above, and the desktop sign-in round trip on a packaged build.
7. Staging smoke test: OIDC login (`ANUM_AUTH_MODE=oidc`), create task, approval round-trip, memory write/read, file round-trip through object storage, event published and consumed, workflow resumed after worker restart.
8. Environment where each check ran (local / CI / staging).
9. Deployment: the green "Helm deploy (kind)" job on the release commit (chart lint, kubeconform, install, `helm test`, CronJobs, upgrade and rollback); the staging `deploy-staging.yml` run with `helm test` output; the production `deploy-production.yml` run with its approval, the Helm revision before and after (`helm history anum`), `helm test` output, and the revision to roll back to (`docs/deployment.md`).
10. Image digests deployed (API, web, backup) and the values used (`values-production.yaml` plus `PRODUCTION_HELM_VALUES`).

## Hard blockers
- `ANUM_AUTH_MODE=headers` in any non-local environment.
- `ANUM_MODEL_PROVIDER=mock` in production.
- Default credentials from `infra/docker/compose.yaml` (anum/anum, admin/admin, `anum-local-secret`) anywhere outside local.
- Any secret in git history or in a client bundle.
- A store or release artifact that is debug-signed, unsigned, or built without `ANUM_PRODUCTION_API_URL` and `ANUM_PRODUCTION_OIDC_ISSUER`.
- A Secret rendered by the chart, or a credential in a values file or repository variable (Secrets are referenced by name only).
- A migration in the release that the previous release cannot run against: `helm rollback` does not reverse migrations, so they must be expand/contract.
- `ANUM_OBJECT_STORAGE_BACKEND` other than `s3` in production.

Output a table: gate, evidence link or value, status (pass / open / fail).
