# ANUM: guidance for Claude Code

ANUM is a monorepo: FastAPI API (`services/api`), React/Vite web (`apps/web`), Tauri desktop (`apps/desktop`), Kotlin Android (`apps/android`), Flutter mobile (`apps/mobile`), shared TS contracts (`packages/contracts`), local infra (`infra/docker`).

## Rules
- Docs are the spec. Read the matching file in `docs/` before changing a boundary, and update it in the same change.
- Every new doc in `docs/` must be linked from the README index or `pnpm docs:check` fails.
- Tenant context is explicit everywhere: requests, jobs, events, tests. Never bypass RLS or the runtime's tool mediation.
- Never commit secrets. `.env.example` files hold placeholders only.
- Never mark a check `continue-on-error`, skip, or delete a test to get CI green.
- `docs/production-plan.md` is the ordered path to production; check it before picking up new work.
- No em or en dashes in UI text, docs or AI replies; use a colon, comma, 'to' or a new sentence. An empty value is `-`. Guards: `services/api/tests/test_no_long_dashes.py` (web, API, Flutter, Android, desktop sources, docs) and `apps/web/test/plainPunctuation.test.ts`; model replies pass through `anum_api/punctuation.py` ([Punctuation](docs/web-localization.md#punctuation)).

## Responsive UI quality gate
For every web or Flutter UI change, a passing build is not proof the UI is right:
- Render the affected pages in a real browser (`vite preview` + Playwright with `executablePath: '/opt/pw-browsers/chromium'`, API mocked at `http://localhost:8000/**` as the e2e specs do) at least at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844; check width and height.
- No horizontal overflow, no clipped controls; check the shared sidebar/header after page-specific changes.
- Content-driven heights: no fixed heights or full-viewport min-heights for normal sections, no stacked padding from parent and child; spacing from the shared tokens, not one-off magic numbers.
- Desktop must use vertical space efficiently; compare before/after screenshots for significant visual changes.

## Definition of done: production ready, never thin
The owner wants finished features, not placeholders. Before calling any page or feature done:
- It does a real job end to end against the real API: create, read, update, delete where it applies, with server-side permission checks. No mock data, "coming soon", dead buttons or lists with nothing to act on.
- Every state is handled: loading, empty (with a next action), error (says what failed and how to fix it), permission denied (explains who can do it), and long or large content (paging, search).
- It passes the Responsive UI quality gate above, keyboard and screen-reader basics, and has unit plus e2e coverage of its main path.
- Docs describe it. If something cannot be finished in the change, say so plainly and list it; never present a partial page as complete.

## Working with the owner
- Keep working until every module is enterprise-grade and complete (definition of done above, in depth); do not stop to ask whether to continue.
- Messages to the owner are short: only what the owner must do (with exact steps), blockers, and decisions only the owner can make. Do not narrate progress, agent reports or test counts unless asked.
- Merge PRs once CI is green; keep the next batch of work going.

## Owner's Oracle setup (as of 19 Oct 2026)
- One VM, `anum-staging` (Riyadh, `ME-RIYADH-1-AD-1`), A1 4 OCPU / 24 GB, 100 GB boot volume, IP 84.13.132.198, hosts `app.anum360os.com`, `api.anum360os.com` and `auth.anum360os.com` (the owner's own domain; the old DuckDNS names are retired, so never use them in docs, builds or messages). DNS for `anum360os.com` (checked 19 Oct 2026): app, api, auth and mail point at the VM; MX is `mail.anum360os.com`; Brevo DKIM (`brevo1`/`brevo2._domainkey`) and DMARC `p=none` exist; the root `anum360os.com` and `www` have no record yet, and SPF is `v=spf1 ~all`, which authorises no sender, so the owner must add the VM IP and Brevo to it before any mail is sent. Owner logs in as `ubuntu` with the `anum_oracle2` key (passphrase set); the old Helper instance and its boot volume are gone.
- Account: Free Tier in its 30-day Free Trial (€250 credit, started 11 Oct 2026, ends about 10 Nov 2026), no payment method. Oracle reportedly cut Always Free A1 to 2 OCPU / 12 GB in June 2026; if Billing → Subscriptions shows credit being used, ANUM at 4/24 is over the free amount and must be resized to 2/12 (or the account upgraded) before the trial ends. Never upgrade the account, create paid resources or change DNS without the owner's explicit go-ahead.
- Owner's choice: everything stays free on this one VM. Other apps (portfolio first, then TyrePulse, VoiceQuote, Anum360 Store, one at a time) run as their own compose stacks behind ANUM's Caddy via the `anum_sites` network ([deploy-oracle.md](docs/deploy-oracle.md)), each with its own database, memory limits and backups, built to move to a home lab later. ANUM's own services are never touched for them.
- Backblaze B2 (`us-east-005`): buckets `anum-staging-files` and `anum-staging-backups`. Off-site backups fail with `SignatureDoesNotMatch` until the owner enters a new backup key (`read -s`, never in chat); local backups work.
- Claude's auto mode refuses to merge PRs: when CI is green, tell the owner to press Ready for review → Merge.
- Portfolio (`ws123na-afk/Portfolio-`, live at www.shahzebrahman.com): frontend on Vercel; the Render backend `Portfolio-` has crash-looped since its database `Portpolio` was suspended (about 4 Sep 2026) and its last good build (July) lacks a 2FA fix, so suspend that service before ever resuming the database.

## Skills in this repo
- `anum-verify`: run the right local checks for what you changed.
- `steward`: how to drive a red PR or `main` back to green.
- `anum-release-gate`: the evidence a release needs before it ships.
- `mockup-to-flutter`: turn a mockup image into an ANUM Flutter screen with our tokens, catalog, responsive rules and tests.
- `flutter-add-widget-test`, `flutter-build-responsive-layout`, `flutter-fix-layout-issues`, `dart-run-static-analysis`: official Flutter/Dart team skills, vendored and pinned; ANUM rules win where they differ. Sources, licenses and rejected skills: `.claude/skills/SOURCES.md`.

Plugins enabled in `.claude/settings.json`: Superpowers, `frontend-design`, `code-review` and Anthropic's `design`. Prefer Superpowers' planning, TDD, systematic-debugging and verification-before-completion skills for non-trivial work.

Design and QA skills (owner, 12 Oct: load them, every helper too; sources and licences in `.claude/skills/THIRD_PARTY_NOTICES.md`):
- Project (`.claude/skills/`): `frontend-design`, `frontend-visual-qa` (audit the rendered page), taste skills `design-taste-frontend` / `high-end-visual-design` / `redesign-existing-projects`, `ui-ux-pro-max` (`python3 .claude/skills/ui-ux-pro-max/scripts/search.py "<query>" --design-system`), `design-accessibility-review`, `design-critique`, `design-ux-copy`, `eng-code-review`, `eng-testing-strategy`, `eng-tech-debt`, `eng-deploy-checklist`.
- Global (`~/.claude/skills/`, installed at pinned commits by `scripts/install-agent-skills.sh` from the SessionStart hook): `full-review`, `owasp-security`, `webapp-testing`; `accessibility-audit`, `performance-audit`, `responsive-design-hub`, `design-system`, `react-component`, `form-builder`, `state-management`; mobile security (`masvs-checklist`, `secure-storage-audit`, `network-security-check`, `platform-interaction-review`, `auth-assessment`, `privacy-audit`, `crypto-review`, `code-quality-scan`, `resilience-assessment`, `mobile-threat-model`, `secure-mobile-dev-guide`, `mobile-pentest-plan`).
- Which to use: any web UI change = `frontend-design` + a taste skill for direction, then `frontend-visual-qa` + `accessibility-audit` + `performance-audit` on the rendered pages (plus the Responsive UI quality gate below); "review security" = `owasp-security` + `full-review`; Flutter / Android = the mobile security skills. Taste-skill defaults never override ANUM's tokens or these rules (no new UI libraries without a reason).

## Things learned the hard way
- `local` and `test` are the only development environments. Outside them the API refuses header auth, `anum_local_*` sessions, a missing `ANUM_SECRETS_KEY`, compose credentials and unsafe CORS. Tests that build non-local `Settings` must pass a Fernet `secrets_key`.
- Streaming responses: never read through the request's repository session inside an SSE body (use `list_events_for_stream`, in a thread). A streaming route takes the repository with `Depends(repository_context, scope="function")`; the default scope holds a pooled connection for the whole stream, and a dozen open tabs froze the whole API (docs/realtime.md).
- One event loop serves every user: never open a store, repository or session inside an `async def` route or SSE body on the loop, and never hold one across a model call. Run the database work with `asyncio.to_thread` in its own short unit of work (`voice.prepare_ask_in_own_session`); one chat question doing it on the loop stalled every tab (docs/realtime.md#event-loop, checked by `tests/test_ask_off_event_loop.py`).
- Never keep a transaction or a `FOR UPDATE` row lock open across a model or tool call: another request that locks the same row on the event loop then waits for a lock whose holder can never resume, and the whole API hangs (Cancel during an inline run did). Claim in one short transaction, work with none open, re-check the claim when committing (`inline_runs.py`).
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
- Managed PostgreSQL admins (Render, RDS) are not superusers: on PostgreSQL 16+ they must be granted membership in a role before `ALTER DATABASE ... OWNER TO` it or `ALTER DEFAULT PRIVILEGES FOR ROLE` it. `bootstrap-database.sql` does that for `anum_migrator`; test bootstrap changes as such an admin, not as `postgres`.
- PyYAML (YAML 1.1) reads unquoted `off`/`on`/`yes`/`no` as booleans; quote such values in `render.yaml` and other YAML the tests load (`generation: "off"`).
- A Kubernetes admission controller's pod can be Ready before its webhook serves; scripts that create policies right after installing it must wait for a server-side dry run to pass (`infra/helm/ci/kind-smoke.sh`).
- Base images can ship unused helpers that fail image scans (`postgres` images carry `gosu`, a Go binary with stdlib CVEs); delete them in the Dockerfile. Every image a deploy workflow builds must also be Trivy-scanned in PR CI (the `images` job), or the finding first shows up as a red `deploy-staging.yml` after merge, and Render (`autoDeployTrigger: checksPass`) then skips that commit.
- `kubectl rollout status` returns while old pods are still terminating and kube-proxy may still route to them; a pod that stops listening on SIGTERM then times out requests (the kind `helm test` after `helm rollback`). Serving pods keep a short `preStop` sleep (`exec sleep`, since the chart supports Kubernetes 1.29) instead of the test waiting longer.
- Oracle Cloud's Ubuntu images end the INPUT chain with a REJECT rule: opening a port in the VCN security list is not enough. `infra/oracle/install.sh` inserts ACCEPT rules before it in `/etc/iptables/rules.v4`; never use ufw or `netfilter-persistent save` there (the latter would persist Docker's chains).
- The Oracle compose stack trusts forwarded headers only from Caddy's fixed address (172.30.10.10) and Caddy replaces any client `X-Forwarded-For`; keep that pairing when changing the proxy or the network.
- On Oracle's Ubuntu image `sudo -n true` works but `sudo -v` asks for a password (sudoers' `verifypw=all` also sees the `%sudo` rule) and `ubuntu` has none: scripts check `sudo -n true` before falling back to `sudo -v`.
- Parallel agent branches collide on migration numbers and on shared files (`apps/web/src/main.tsx`, the web message catalogs, `test_migrations.py`): land them one at a time, renumber each migration onto the newest, and resolve catalogs by keeping both sides' keys.
- Governance audit records share the in-memory audit log with every other audit record; tests that count audit rows clear `memory_repository.store.audit_records` too.
- The web e2e suite needs its fixed ports (4173 app, 4174 OIDC build, 4175 error-reporting build); a run on other ports reports the OIDC and error-reporting specs as failures that are not real.
- `dart format` must run after `flutter pub get`, or it formats with the newest language version and rewrites the whole tree; CI's Flutter job checks it.
- Local checks before a push are the fast ones for what changed (API unit tests, lint, the changed specs); the full browser and database suites run in CI. The owner asked not to spend time on repeated full local runs.
- Several open PRs that each add a migration must be merged one at a time: after each merge, merge `main` into the next branch, renumber its migration after the new head (file name, `revision`, `down_revision`, every doc and test reference) and wait for green. `docs/pending-work.md` lists the owner steps and open work left by the last session.
- When two features land under similar names (phone `calls` vs in-app `app-calls`), check routes, Flutter routes, push kinds, i18n keys and privacy helper names for silent clashes after the merge; git can merge two identical-looking lines into one.
- A test that recomputes an HMAC-SHA1 over a variable named like an id trips CodeQL's weak-hash rule, and a hard-coded expected digest trips gitleaks: compute the expected value in the test from a constant label instead.
