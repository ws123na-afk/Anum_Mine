# ANUM MINE headquarters: baseline audit (F01)

Date: 22 October 2026. Audited commit: `34dee88` (main after PR 214), plus this branch's fixes.
Statuses refreshed on 29 October 2026 against main `40f05e8` (see "Status on main, 29 October 2026" below);
the sections after it keep the 22 October findings as they were written, with the issue register updated.
Status words: **not started**, **partial** (some of it exists and is tested), **tested** (code and a
passing test for the stated scope), **deployed** (running on a real environment; nothing here is,
see "What is deployed"). A plan is not implementation.

## Status on main, 29 October 2026

Checked on main `40f05e8` by reading the files named here and the [feature completion matrix](ANUM_MINE_FEATURE_COMPLETION_MATRIX.md),
which is the current audit and carries the evidence per requirement. The branches this audit listed as unmerged
(`claude/hq-data`, `claude/hq-meetings`, `claude/hq-executive`) have landed: PRs 218 to 222, 242 to 244 and 246, and
later HQ PRs up to 317. Migrations now run in one chain to `0120_record_retention`, and every file number is unique
(H-12 fixed by renaming the file `0099_office_room_geometry.py` to `0100_office_room_geometry.py`; revision ids unchanged).

| Feature | 22 Oct | 29 Oct | Evidence on main | Still open (matrix row) |
| --- | --- | --- | --- | --- |
| F01 baseline and ADR | tested | tested | this file, ADR 0003, `pnpm docs:check` | none |
| F02 office data contract and floor-plan editor | partial | tested | `office_space_api.py`, `office_layout.py`; contracts `officeBuilder.ts`, `officeLayoutHistory.ts`; builder `apps/web/src/hqBuilder/*` with save, reload, undo, revisions, templates; `e2e/hq-builder.spec.ts`, `hq-layout-history.spec.ts` | editing in the 3D scene is not built (11.9, optional) |
| F03 true presence projection | partial | tested | `office_presence_api.py`, `office_presence.py`, `office_campus_api.py`, contract `officePresence.ts`; snapshot then stream with `Last-Event-ID` (`lib/presenceStream.ts`); `test_office_presence.py`, `e2e/hq-live-scene.spec.ts` | long run on the Oracle VM (8.2, 14.6) |
| F04 3D renderer | partial | partial | three.js 0.184 (`lib/hqThreeRenderer.ts`), loaded lazily; lighting, shadows, glTF and fallbacks (section 3 of the matrix) | premium exteriors and characters against the concept renders (2.2, 2.8, 5.3) |
| F05 floor navigation, department and desk details | partial | tested | floors, rooms, desk work record with checkpoints, approvals, errors and documents (`HqWorkstationWork.tsx`), agent profile link, five-viewport gates (12.2) | none in this list |
| F06 live meeting data model | tested (server) | tested | migrations 0098, 0104, 0107, 0111; outputs, decisions, links, transcript retention 90 days (O5) | none in this list |
| F07 governed meeting runner | partial | partial | `unattended_meeting_worker.py`, leases, owner interrupt, budget and turn rate | proof that a worker runs anywhere (9.4) |
| F08 interactive meeting rooms | partial | tested | `MeetingRoomPage.tsx`, `MeetingRoomPanel.tsx`; `e2e/meeting-room.spec.ts`, `meeting-summary.spec.ts` | none in this list |
| F09 work history and replay | partial (API only) | partial | History panel (`HqHistoryPanel.tsx`), CSV and JSON export with the server `mention` filter, "Show history" from task, meeting and approval screens; `e2e/hq-history*.spec.ts` | a true replay needs stored snapshots (O8, 10.5) |
| F10 executive office and switcher | not started on main | tested | `executive_api.py` (migration `0101_executive_grants`), `executive_pa.py`, `ExecutiveCommandCenter.tsx`; `test_executive_pa.py`, `e2e/executive.spec.ts` | a PA persona in the scene (O9, 6.8) |
| F11 behaviours and polish | not started | partial | agents walk only when a stored record says they moved (O1), campus motion, reduced motion | see matrix sections 2 and 5 |
| F12 Flutter control surface | not started | not started | `apps/mobile/lib/features` has no office, presence or meeting-room feature | whole feature |
| F13 live voice room | not started | not started | none | whole feature (optional per plan) |
| F14 readiness, load, deployment | partial | partial | budgets in `e2e/hq-performance.spec.ts`; `test_postgres_hq_rls.py` and `test_postgres_office_spaces.py` pass against a local password-auth PostgreSQL (29 October 2026) | load test and rollback rehearsal on the Oracle VM (O13, 12.9, 12.10) |

## What is deployed

Nothing in this audit is evidenced as deployed. `Deploy staging` is green on main, but no test or log in
this repository proves the office route is reachable or useful on staging. Treat every status below as
"in the code and the test suite", not "in production".

## Main's checks, honestly

Source: `gh api repos/ws123na-afk/Anum_Mine/actions/runs?branch=main` on 22 October 2026.

| Main commit | CI | Failed jobs |
| --- | --- | --- |
| `34dee88` (PR 214 merge) | red | Browser end-to-end shards 2 and 6 |
| `3ae7e41` | red | Browser end-to-end shards 2 and 6 |
| `2c39f28` | red | shards 2 and 6, and "Web and contracts" (`pnpm check`) |
| `230b1f3` | red | Python SAST (bandit), API unit tests, `pnpm docs:check` |
| `49ba30e` | red | `pnpm check`, API unit tests, bandit |
| others in the last 30 runs | cancelled by a newer push | none |

CodeQL and Deploy staging were green on every one of these commits.

Root cause of the current red (34dee88): PR 214 made `#office-preview` render `HeadquartersEntrypoint`
(live office by default, concept view behind a toggle). Three specs still assumed the old concept view:
`e2e/office-workstations.spec.ts` (2 tests) and `e2e/oidc-sign-in.spec.ts` ("office preview stays behind
sign-in"), on desktop and mobile. Fixed in PR 217 (`claude/hq-fix-main-e2e`) without weakening a test:
the specs drive the live office with a mocked registry, and the OIDC spec still proves that nothing is
requested and no office is shown before sign-in. Reproduced and verified locally on desktop and mobile
(10 of 10 passing). The earlier reds (`230b1f3`, `49ba30e`) were fixed by later commits; their logs were
not re-read here.

## Code to feature coverage

Paths are relative to the repository root. "Test" names the file that proves the claim.

| Feature | Status | Code | Tests | Gap |
| --- | --- | --- | --- | --- |
| F01 baseline and ADR | tested (this PR) | this file, `docs/decisions/ADR-0003-office-projection-and-live-meetings.md` | `pnpm docs:check` | none |
| F02 office data contract and floor-plan editor | partial | API `services/api/anum_api/office_space_api.py`, store `office_space_store.py`, migrations `0097_office_spaces`, `0099_office_create_keys`, `0100_office_room_geometry`; web `apps/web/src/OfficeSpaceLiveManager.tsx`, `OfficeSpaceRegistry.tsx`, `lib/officeSpaceClient.ts` | `services/api/tests/test_office_space_api.py`, `test_postgres_office_spaces.py`, `test_office_room_geometry.py`; web `apps/web/test/officeSpaceClient.test.ts`, `officeSpaceRegistry.test.ts` | No shared DTOs in `packages/contracts` (the web redeclares the types in `officeSpaceClient.ts`); no e2e of save and reload of a layout; no routes (circulation paths) or avatars in the model; the unassigned roster is the workstation list, not a registry view |
| F03 true presence projection | partial | browser-side: `apps/web/src/lib/officePresence.ts`, `officeWorkstations.ts`, `HeadquartersLive.tsx` (agents loader) | `apps/web/test/officePresence.test.ts`, `officeWorkstations.test.ts`; e2e `office-workstations.spec.ts`, `hq-live-data.spec.ts` (this PR) | No server presence API on main. Branch `claude/hq-data` (commit `f9e0f8e`, not merged) adds `office_presence_api.py`, `office_presence.py`, `office_campus_api.py` and the contracts `packages/contracts/src/officePresence.ts`; re-check after it merges; no meeting state in presence; no provenance object or stale flag from the server; no snapshot plus delta with `Last-Event-ID` in the office (it refreshes on events and every 10 s) |
| F04 3D renderer | partial | `apps/web/src/HeadquartersWebGL.tsx` (raw WebGL), `HeadquartersCanvas.tsx` (2D fallback), `lib/headquartersSpatial.ts`, `headquartersProjection.ts` | unit `headquartersSpatial.test.ts`, `headquartersProjection.test.ts`, `headquartersArchitecture.test.ts`; e2e `hq-scene.spec.ts` (WebGL, fallback, context loss, reduced motion, keyboard), `hq-performance.spec.ts` (this PR) | Not React Three Fiber (see ADR 0003); flat colour quads sorted by depth, no lighting, no depth buffer, no glTF; no viewport culling; orbit and zoom work but there is no keyboard pan or zoom on the canvas |
| F05 floor navigation, department and desk details | partial | `HeadquartersLive.tsx` (building, floor, room list, search, filters, room details, desk rows) | `headquartersController.test.ts`; `hq-scene.spec.ts` keyboard journey | No minimap, no deep links to a room or desk, desk shows only the status word (no checkpoints, errors, documents), no link to the agent profile, room inspector hidden below 768 px (H-04) |
| F06 live meeting data model | tested (server) | migration `0098_agent_live_meetings`, `live_agent_meeting_domain.py`, `live_agent_meeting_store.py`, `live_agent_meeting_api.py` | `test_live_agent_meeting_domain.py`, `test_live_agent_meeting_api.py`, `test_postgres_live_agent_meeting_api.py` | No decisions or action items tables; no transcript export or delete; retention only through tenant lifecycle purge |
| F07 governed meeting runner | partial | `live_agent_meeting_runtime.py`, `live_agent_meeting_coordinator.py`; `POST /agent-meetings/{id}/execute-turn` and `/run` (at most 3 turns per call, synchronous) | `test_live_agent_meeting_runtime.py`, `test_live_agent_meeting_coordinator.py` | No unattended worker, no crash resume, no owner interrupt, no disagreement summary on main (branch `claude/hq-meetings`) |
| F08 interactive meeting rooms | partial | `apps/web/src/AgentMeetingSessions.tsx` (96 lines, list and run buttons), `MeetingsView.tsx` (imported meetings, unchanged) | `apps/web/e2e/meetings.spec.ts` (imported meetings only) | No live transcript, raised hands, room occupancy in the scene, escaped Markdown, replay; branch `claude/hq-meetings` |
| F09 work history and replay | partial (API only) | `services/api/anum_api/office_history_api.py` (`/api/v1/office-history`, `/at`, `/correlations/{id}`) | `services/api/tests/test_office_history_api.py` (12 tests) | No web UI, no export provenance |
| F10 executive office and switcher | not started on main | branches `claude/hq-data` (`executive_api.py`, migration `0101_executive_grants`: owner-approved, revocable grants, `anum_executive_reader` NOLOGIN role) and `claude/hq-executive` (`executive_pa.py`, `ExecutiveCommandCenter.tsx`) | on those branches only (`test_office_presence.py`, `test_postgres_office_hq.py`, `test_executive_pa.py`) | The in-app organisation switcher exists in the shell (`api.switchOrganisation`); there is no cross-workspace summary and no grant model on main |
| F11 behaviours and polish | not started | none (the reception module and guided tour are separate, `ReceptionOperations.tsx`, `GuidedExecutiveTour.tsx`) | `receptionOperations.test.ts` | No animation state machine, no sound |
| F12 Flutter control surface | not started | `apps/mobile/lib` has no office, presence or meeting feature | none | whole feature |
| F13 live voice room | not started | none | none | whole feature (optional per plan) |
| F14 readiness, load, deployment | partial (this PR) | `scripts/hq-load/hq_load.py`, `apps/web/e2e/hq-*.spec.ts`, `services/api/tests/test_postgres_hq_rls.py`, `apps/web/src/lib/headquartersFlag.ts`, the four `docs/ANUM_MINE_HQ_*.md` files | as listed | No run on the Oracle VM yet; no rollback rehearsal; no failure injection |

Side modules that exist but are not in the F01 to F14 list: reception and visitor operations
(`ReceptionOperations.tsx`), guided executive tour, workstation directory (`OfficeWorkstations.tsx`),
concept view (`OfficeTourView.tsx`, `OfficeWebglScene.tsx`). The concept view is labelled
"CONCEPT VIEW" and is a separate mode of `HeadquartersEntrypoint`; the live view shows no invented
people. The reception module still uses example host and room names (`EXAMPLE_HOSTS`, `DEMO_ROOMS` in
`ReceptionOperations.tsx`); the office shows them only inside that panel.

## Issue register

Severity: High blocks release, Medium needs a fix before a wide rollout, Low is polish.
State: fixed (in this branch or PR 217), open (owner of the fix in brackets).

| Id | Severity | State | Finding |
| --- | --- | --- | --- |
| H-01 | High | fixed (PR 217) | Main CI red on three office specs after PR 214 (see above) |
| H-02 | Medium | fixed (PR 217) | Workstation evidence read "Stale" for up to 10 s after every good refresh: the panel clock lagged the new snapshot (`OfficeWorkstations.tsx`) |
| H-03 | High | fixed | The office never refreshed from events: the event type pattern in `HeadquartersLive.tsx` had a doubled backslash and matched nothing. Only the 10 s poll worked. Covered by `e2e/hq-live-data.spec.ts` |
| H-04 | Medium | fixed (29 Oct: the five-viewport gates in `hq-campus.spec.ts` and the panels layout, matrix 3.21 and 12.2) | Below 768 px the room inspector is hidden and the directory is clipped, so a phone user cannot pick a room except by tapping a label in the scene (screenshot at 390x844). The "Authenticated office / Concept preview" switch overlaps the footer |
| H-05 | Medium | fixed | In the light app theme the global `strong, h1-h3` colour made office text near-black on dark panels (contrast 1.3 to 1); the active room's second line was 4.2 to 1; icon-only header buttons had no name on phones; room rows were `button role=listitem` (lost button semantics). Covered by `e2e/hq-a11y.spec.ts` |
| H-06 | Medium | fixed (29 Oct: `lib/messages/hq.ar.ts` and the other HQ catalogs, key parity in `test/i18n.test.ts`, matrix 13.5) | The office had no Arabic: its text is hard-coded English and not in the message catalog (`lib/messages`). The axe scan in Arabic passes only because the page language switch does not translate these strings |
| H-07 | Medium | fixed (29 Oct: three.js renderer `lib/hqThreeRenderer.ts` with lighting and a depth buffer, matrix 3.1; the premium look itself is matrix 2.2) | The scene was flat-shaded quads sorted by depth. With synthetic 6 by 6 rooms it renders as large white blocks. No lighting, depth buffer or real assets |
| H-08 | Low | open (not re-checked on 29 Oct) | The office route is not keyed by tenant and workspace, and has no switcher inside it. The controller is safe (tested), but a switch made in another way will only be noticed on the next refresh or focus |
| H-09 | Medium | fixed (29 Oct: a counting bug in the load tool; 0 percent loss at 200 clients in memory mode, matrix 12.9; the Oracle run is still open) | SSE fan-out at 200 clients lost 0.3 to 0.8 percent of deliveries in memory mode (not at 21 clients). Cause not isolated; re-run on Oracle with PostgreSQL (see `docs/ANUM_MINE_HQ_LOAD_TESTING.md`) |
| H-10 | Low | fixed (29 Oct: passes in CI and against a local password-auth PostgreSQL) | `services/api/tests/test_postgres_hq_rls.py` was written without a reachable test database in this sandbox (no credentials), so it is compile-checked only. CI's database job is its first run |
| H-11 | Medium | fixed (29 Oct: S-02 closed, USD 50 per workspace per month, matrix 9.3) | The master plan's `agent_meeting_sessions` allow a `max_cost_usd` up to 1000 per meeting and there is no workspace-wide cap (security finding S-02) |
| H-13 | Low | open (visual, not re-checked on 29 Oct) | In the office overlay the panel close button ("Close office panel") is covered by the dialog header, so a mouse click on it is intercepted (keyboard works, and the dialog has its own Close button). Found by `hq-csp.spec.ts` while clicking it |
| H-12 | Low | fixed (29 Oct: the file is now `0100_office_room_geometry.py`, ids unchanged, `test_migrations.py` checks unique numbers and one chain) | Two migration files are numbered 0099 (`0099_office_create_keys.py`, `0099_office_room_geometry.py`); the revision ids chain correctly (`0100_office_room_geometry` follows `0099_office_create_keys`). Parallel HQ branches add 0101 to 0103: renumber at merge |

## Rollback approach

Web: set `VITE_ANUM_HQ=off` and rebuild the web image: the sidebar link and the `#office-preview` route
disappear, nothing else changes. API: all HQ migrations are expand-only; rolling the image back leaves
extra tables unused. Details and rehearsal steps: `docs/ANUM_MINE_HQ_DEPLOYMENT_AND_ROLLBACK.md`.

## How this audit was checked

- Browser: Chromium at `/opt/pw-browsers/chromium` with the sandbox's software GL. WebGL works there
  with no flags (renderer "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)))"); the flags
  `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist` in
  `apps/web/playwright.hq.config.ts` are kept for stability. Software rendering is much slower than a
  laptop GPU, so frame numbers are a regression guard only.
- Specs added: `apps/web/e2e/hq-scene.spec.ts`, `hq-a11y.spec.ts`, `hq-live-data.spec.ts`,
  `hq-performance.spec.ts`, support `e2e/support/hqApi.ts`; unit `apps/web/test/headquartersTenantRace.test.ts`,
  `headquartersFlag.test.ts`.
- Branches fetched for this audit (none merged): `claude/hq-data` (one commit `f9e0f8e`: campus,
  presence, workstation work, executive grants, migration 0101), `claude/hq-meetings` (one commit
  `74539f0`: durable events, interrupts, draft outputs, meeting room page, migration 0102),
  `claude/hq-executive` (two commits: executive PA tools and desk). `claude/hq-campus-visual` had not
  been pushed. Their status in the matrix is "on a branch", never "tested on main". Re-run the matrix
  when they land.
