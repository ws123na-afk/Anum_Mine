# P0 CI remediation (Order 001, step 1)

Status of the P0 items in the [business master roadmap](../ANUM_OS_Business_and_Product_Master_Roadmap.md) (section 1 "Real defects seen in CI logs" and section 7 "P0"). Checked on 18 October 2026 against `main` at `3a10bdb` (merge of PR #104).

Part of Order 001: [gap register](ANUM_BUSINESS_GAP_REGISTER.md), [reuse decisions](REUSE_DECISION_REGISTER.md), [P1 acceptance tests](P1_ACCEPTANCE_TESTS.md).

## Summary

| Item | Status | Where |
|---|---|---|
| 3 commerce test failures (`test_commerce.py`) | **Fixed in this PR.** They were never fixed on `main`: they pass or fail by the time of day. | Test clock only; product code unchanged. |
| Authenticated journey: SSE `403 Active workspace membership required` | **Fixed in this PR.** Intermittent: failed on `main` in runs 38814995240 (15 Oct) and 39113185705 (18 Oct), passed in the latest run 39119380316. | `services/api/anum_api/dependencies.py` |
| Keycloak "Missing parameter: code_challenge_method" | **Not a defect.** The log line is the journey's own negative check: Keycloak must refuse a login without PKCE, and it does. | No change |
| 4 Rust advisories in `apps/desktop/src-tauri/Cargo.lock` | **Fixed** in `bc873a2` and `b53059f` (both on `main`). Latest `cargo audit` on `main`: 0 vulnerabilities, 5 allowed warnings. | `docs/security.md` updated for the 3 new warnings |
| Staging health, migration dry run, restore drill, login and isolation smoke test on the real VM | **Still open.** Needs the owner's Oracle VM; this sandbox cannot reach it. | Owner action |

## 1. Commerce tests: a clock bug in the test, not in the product

**What failed** (run 38814995240, job "API unit tests", started 22:08 UTC on 15 October):

```
FAILED services/api/tests/test_commerce.py::test_asking_for_sales_runs_the_read_tool_without_approval
  'Amazon sales today: no orders yet.'.startswith('Amazon sales today: 2 orders, 3 units, ...')
FAILED services/api/tests/test_commerce.py::test_voice_answers_sales_questions_from_the_synced_data
FAILED services/api/tests/test_commerce.py::test_store_questions_in_chat_answer_with_commerce_cards
= 3 failed, 2225 passed, 9 skipped, 381 deselected
```

**On current `main`:** `python -m pytest services/api/tests/test_commerce.py -m "not database"` gave `57 passed` at about 11:30 UTC. They pass at that hour and would fail again after 21:00 UTC. No commit fixed them.

**Root cause.** The fake Amazon orders (`services/api/tests/commerce_fakes.py`) are 2 and 3 hours old. "Today" is counted in the workspace time zone, `Asia/Riyadh` (UTC+3) by default (`anum_api/commerce/insights.py`, `DEFAULT_TZ`). From 21:00 to 24:00 UTC an order placed 3 hours earlier is still "yesterday" in Riyadh, so "no orders yet" was the correct answer for Riyadh at that hour. The noon summary test had the same problem and was fixed earlier (`2ceecea`, `_midday_zone()`), but the three tests that ask without a `tz` (assistant task, voice, chat card) were not.

**Evidence the time window is the cause:**

| Run | Started (UTC) | Commerce tests |
|---|---|---|
| 39065676062 | 17 Oct 20:11 | passed (1 other failure: dashes in the roadmap doc) |
| 39069614600 | 17 Oct 21:07 | 3 failed (plus the same dashes failure) |
| 38814995240 | 15 Oct 22:08 | 3 failed |

Reproduced locally by moving the default zone to one where it is now 01:10 (as in Riyadh at 22:10 UTC), with a scratch pytest plugin outside the repository:

```
$ python -m pytest services/api/tests/test_commerce.py -m "not database" -p late_clock
FAILED ...test_asking_for_sales_runs_the_read_tool_without_approval
FAILED ...test_voice_answers_sales_questions_from_the_synced_data
FAILED ...test_store_questions_in_chat_answer_with_commerce_cards
```

(The plugin also makes `test_period_names_match_and_cover_local_days` fail, because that test checks the default zone's name itself; that is the plugin's doing, not a bug.)

**Fix (this PR).** A `midday_default_zone` fixture in `services/api/tests/test_commerce.py` sets the default zone, for those three tests only, to a fixed-offset zone where it is about noon now (the same idea as `_midday_zone()`). Every assertion is unchanged; the product still answers in Riyadh time.

**After the fix:** `57 passed` normally, and `56 passed, 1 failed` (only the plugin's own artefact above) with the late-clock plugin.

## 2. Authenticated journey: the SSE 403 after onboarding

**What failed** (run 39113185705, 18 October, same in 38814995240):

```
[journey] Signed in as 'dev' via authorization code + PKCE: ... roles=['owner', 'org_admin']
[journey] Onboarding bootstrapped the tenant, workspace and owner membership
JOURNEY FAILED: SSE stream failed: Open the SSE stream: expected HTTP 200, got 403:
  {"error":{"code":"forbidden","message":"Active workspace membership required", ...}}
API log: PUT /api/v1/onboarding 200 OK, then GET /api/v1/events/stream 403 Forbidden
```

Over the last 60 completed CI runs the job failed 5 times: twice on `main` with this 403, three times on Dependabot branches started together on 14 October. It passed 52 times. The latest `main` run (39119380316, 10:28 UTC 18 October) passed every job. So the failure is intermittent, not fixed.

**Root cause.** A race between the commit and the next request:

1. `PUT /api/v1/onboarding` writes the tenant, workspace and owner membership through `provisioning_repository_context`, which commits after its `yield`.
2. FastAPI 0.142 closes a `yield` dependency with the default scope (`"request"`) only **after the response has been sent** (`fastapi/routing.py`: the request exit stack closes after `await response(scope, receive, send)`).
3. The journey receives the `200` and opens `GET /api/v1/events/stream` at once. Its `tenant_context` looks up the membership on another pooled connection. If the onboarding transaction has not committed yet, there is no membership, and the API correctly answers `403`.

The membership check is right; the commit was late. The same window applied to every write: `repository_context`, `identity_repository_context` and `memory_repository_context` also committed after the response.

**Proof.** `services/api/tests/test_commit_before_response.py` calls the ASGI app directly and records when the response starts and when the unit of work commits. On `main` it fails:

```
AssertionError: assert ['response.start', 'commit'] == ['commit', 'response.start']
```

**Fix (this PR).** In `services/api/anum_api/dependencies.py` each of the four units of work is now a generator (`_repository_unit`, `_provisioning_repository_unit`, `_identity_repository_unit`, `_memory_repository_unit`) resolved with `Depends(..., scope="function")` through a plain wrapper that keeps the old name. FastAPI then commits before it sends the response. All 262 `Depends(repository_context)` call sites stay as they are. Membership checks are unchanged: the same `_require_persisted_membership` and `tenant_context` code runs. Documented in [Realtime: commit order](../realtime.md#commit-order).

Side effects, both intended: committed events reach the event runtime before the response, and a route's background tasks (meeting processing, WhatsApp triage) now run after the commit, so they see the rows the request wrote.

**After the fix:**

- `test_commit_before_response.py`: the in-memory test passes; the PostgreSQL test (`-m database`) checks that onboarding's commit comes before the response and that the next request on a new connection is a member.
- Whole API unit suite: `python -m pytest services/api -m "not database"`: **2398 passed, 14 skipped, 396 deselected** (285 s).
- PostgreSQL suite (`-m database`, scratch PostgreSQL 16 with a password for 127.0.0.1, run from the repository root): **392 passed, 1 skipped**.
- The journey itself needs Docker (Keycloak, NATS, PostgreSQL), which this sandbox does not have: CI's "Authenticated journey" job on this PR is the check.

## 3. Keycloak "Missing parameter: code_challenge_method"

```
keycloak-1 | PKCE enforced Client without code challenge method.
keycloak-1 | type="LOGIN_ERROR", clientId="anum-web", error="invalid_request",
             reason="Missing parameter: code_challenge_method", redirect_uri=".../journey/callback"
```

This appears at 08:54:19.8, the same moment the journey logs `Keycloak refuses an anum-web authorization request without PKCE` (`services/api/scripts/journey.py`, `check_pkce_is_required`). The journey sends that request on purpose and fails if Keycloak accepts it. The real sign-in a moment later uses `code_challenge_method=S256` and succeeds (`Signed in as 'dev' via authorization code + PKCE`). Nothing to fix. This PR adds a note to `check_pkce_is_required` in `journey.py` so the next reader does not chase this line.

## 4. Rust advisories

Run 38814995240 "Security scans" (`cargo audit`, Cargo.lock with 535 crates):

| ID | Crate | Kind | Fixed |
|---|---|---|---|
| RUSTSEC-2026-0346 | rustls 0.23.45 | vulnerability (4.0 medium): `Debug` exposes TLS secrets | `bc873a2`: rustls 0.23.46 |
| RUSTSEC-2026-0347 | rustls 0.23.45 | vulnerability (2.3 low): data sent after ECH rejection | same |
| RUSTSEC-2026-0348 | rustls 0.23.45 | vulnerability (3.7 low): brotli decompression allocation | same |
| RUSTSEC-2026-0349 | rustls 0.23.45 | vulnerability (3.7 low): ServerHello downgrade | same |
| RUSTSEC-2024-0370 | proc-macro-error 1.0.4 | warning: unmaintained | not a failure; Tauri's tree |
| RUSTSEC-2024-0429 | glib 0.18.5 | warning: unsound | not a failure; Tauri's tree |

The roadmap counted `proc-macro-error` among the four; the four that failed the job were the rustls ones. `b53059f` also removed `zip` 4.6.1 (RUSTSEC-2026-0367 to -0369) by turning off the updater's `zip` feature.

`cargo-audit` is not installed in this sandbox (and building it needs about 3 minutes and disk this sandbox lacks), so the evidence is CI's own run on `main` (run 39119380316, job "Security scans", `cargo-audit` 0.22.2, 1338 advisories loaded):

```
Scanning apps/desktop/src-tauri/Cargo.lock for vulnerabilities (535 crate dependencies)
warning: 5 allowed warnings found
  proc-macro-error 1.0.4  unmaintained  RUSTSEC-2024-0370
  zbus_names 4.3.4        unmaintained  RUSTSEC-2026-0371
  zvariant 5.15.0         unmaintained  RUSTSEC-2026-0370
  zvariant_derive 5.15.0  unmaintained  RUSTSEC-2026-0372
  glib 0.18.5             unsound       RUSTSEC-2024-0429
```

The lockfile on this branch has `rustls` 0.23.46. The three `zvariant`/`zbus_names` notices are new (16 October) and come through `zbus` 5.19.0 from `notify-rust` and `tauri-plugin-opener`; this PR adds them to the reviewed warnings row in [Security](../security.md). No critical or high advisory is open.

## Verification

Commands run in this sandbox (Python 3.13, FastAPI 0.142.2; CI uses Python 3.12):

| Command | Result |
|---|---|
| `python -m pytest services/api/tests/test_commerce.py -m "not database"` (before) | 57 passed (at 11:30 UTC) |
| same with the late-clock plugin (before) | 3 failed as in CI, plus the plugin's own artefact |
| same with the late-clock plugin (after) | only the plugin's own artefact fails |
| `python -m pytest services/api/tests/test_commit_before_response.py` (before the fix) | 1 failed: `['response.start', 'commit']` |
| `python -m pytest services/api -m "not database"` (after) | 2398 passed, 14 skipped |
| `python -m pytest services/api -m database` against a scratch PostgreSQL 16 database (after) | 392 passed, 1 skipped (28 min); `test_commit_before_response.py` on its own: 2 passed |
| `pnpm docs:check` | passed |

## Still open for P0 exit

- Staging smoke test, migration dry run and restore drill on the owner's Oracle VM (`sudo ./backup.sh drill`, `infra/oracle/smoke.sh`), recorded in the PR. The sandbox cannot reach the VM.
- Branch protection with all CI checks required (owner action, [Production plan](../production-plan.md)).
- CI on this PR, in particular "Authenticated journey" and "API PostgreSQL persistence tests", is the independent check of the two fixes.
