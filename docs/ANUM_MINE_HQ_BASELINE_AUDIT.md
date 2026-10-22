# ANUM MINE headquarters: baseline audit (F01)

Date: 22 October 2026. Audited commit: `34dee88` (main after PR 214), plus this branch's fixes.
Status words: **not started**, **partial** (some of it exists and is tested), **tested** (code and a
passing test for the stated scope), **deployed** (running on a real environment; nothing here is,
see "What is deployed"). A plan is not implementation.

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
| F03 true presence projection | partial | browser-side: `apps/web/src/lib/officePresence.ts`, `officeWorkstations.ts`, `HeadquartersLive.tsx` (agents loader) | `apps/web/test/officePresence.test.ts`, `officeWorkstations.test.ts`; e2e `office-workstations.spec.ts`, `hq-live-data.spec.ts` (this PR) | No server presence API on main (branch `claude/hq-data` is working on it); no meeting state in presence; no provenance object or stale flag from the server; no snapshot plus delta with `Last-Event-ID` in the office (it refreshes on events and every 10 s) |
| F04 3D renderer | partial | `apps/web/src/HeadquartersWebGL.tsx` (raw WebGL), `HeadquartersCanvas.tsx` (2D fallback), `lib/headquartersSpatial.ts`, `headquartersProjection.ts` | unit `headquartersSpatial.test.ts`, `headquartersProjection.test.ts`, `headquartersArchitecture.test.ts`; e2e `hq-scene.spec.ts` (WebGL, fallback, context loss, reduced motion, keyboard), `hq-performance.spec.ts` (this PR) | Not React Three Fiber (see ADR 0003); flat colour quads sorted by depth, no lighting, no depth buffer, no glTF; no viewport culling; orbit and zoom work but there is no keyboard pan or zoom on the canvas |
| F05 floor navigation, department and desk details | partial | `HeadquartersLive.tsx` (building, floor, room list, search, filters, room details, desk rows) | `headquartersController.test.ts`; `hq-scene.spec.ts` keyboard journey | No minimap, no deep links to a room or desk, desk shows only the status word (no checkpoints, errors, documents), no link to the agent profile, room inspector hidden below 768 px (H-04) |
| F06 live meeting data model | tested (server) | migration `0098_agent_live_meetings`, `live_agent_meeting_domain.py`, `live_agent_meeting_store.py`, `live_agent_meeting_api.py` | `test_live_agent_meeting_domain.py`, `test_live_agent_meeting_api.py`, `test_postgres_live_agent_meeting_api.py` | No decisions or action items tables; no transcript export or delete; retention only through tenant lifecycle purge |
| F07 governed meeting runner | partial | `live_agent_meeting_runtime.py`, `live_agent_meeting_coordinator.py`; `POST /agent-meetings/{id}/execute-turn` and `/run` (at most 3 turns per call, synchronous) | `test_live_agent_meeting_runtime.py`, `test_live_agent_meeting_coordinator.py` | No unattended worker, no crash resume, no owner interrupt, no disagreement summary on main (branch `claude/hq-meetings`) |
| F08 interactive meeting rooms | partial | `apps/web/src/AgentMeetingSessions.tsx` (96 lines, list and run buttons), `MeetingsView.tsx` (imported meetings, unchanged) | `apps/web/e2e/meetings.spec.ts` (imported meetings only) | No live transcript, raised hands, room occupancy in the scene, escaped Markdown, replay; branch `claude/hq-meetings` |
| F09 work history and replay | partial (API only) | `services/api/anum_api/office_history_api.py` (`/api/v1/office-history`, `/at`, `/correlations/{id}`) | `services/api/tests/test_office_history_api.py` (12 tests) | No web UI, no export provenance |
| F10 executive office and switcher | not started on main | branch `claude/hq-executive` (`executive_pa.py`, `ExecutiveCommandCenter.tsx`) | on that branch only | The in-app organisation switcher exists in the shell (`api.switchOrganisation`); there is no cross-workspace summary and no grant model on main |
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
| H-04 | Medium | open (visual) | Below 768 px the room inspector is hidden and the directory is clipped, so a phone user cannot pick a room except by tapping a label in the scene (screenshot at 390x844). The "Authenticated office / Concept preview" switch overlaps the footer |
| H-05 | Medium | fixed | In the light app theme the global `strong, h1-h3` colour made office text near-black on dark panels (contrast 1.3 to 1); the active room's second line was 4.2 to 1; icon-only header buttons had no name on phones; room rows were `button role=listitem` (lost button semantics). Covered by `e2e/hq-a11y.spec.ts` |
| H-06 | Medium | open (visual) | The office has no Arabic: its text is hard-coded English and not in the message catalog (`lib/messages`). The axe scan in Arabic passes only because the page language switch does not translate these strings |
| H-07 | Medium | open (visual) | The scene is flat-shaded quads sorted by depth. With synthetic 6 by 6 rooms it renders as large white blocks. No lighting, depth buffer or real assets |
| H-08 | Low | open | The office route is not keyed by tenant and workspace, and has no switcher inside it. The controller is safe (tested), but a switch made in another way will only be noticed on the next refresh or focus |
| H-09 | Medium | open | SSE fan-out at 200 clients lost 0.3 to 0.8 percent of deliveries in memory mode (not at 21 clients). Cause not isolated; re-run on Oracle with PostgreSQL (see `docs/ANUM_MINE_HQ_LOAD_TESTING.md`) |
| H-10 | Low | open | `services/api/tests/test_postgres_hq_rls.py` was written without a reachable test database in this sandbox (no credentials), so it is compile-checked only. CI's database job is its first run |
| H-11 | Medium | open | The master plan's `agent_meeting_sessions` allow a `max_cost_usd` up to 1000 per meeting and there is no workspace-wide cap (security finding S-02) |
| H-13 | Low | open (visual) | In the office overlay the panel close button ("Close office panel") is covered by the dialog header, so a mouse click on it is intercepted (keyboard works, and the dialog has its own Close button). Found by `hq-csp.spec.ts` while clicking it |
| H-12 | Low | open | Two migration files are numbered 0099 (`0099_office_create_keys.py`, `0099_office_room_geometry.py`); the revision ids chain correctly (`0100_office_room_geometry` follows `0099_office_create_keys`). Parallel HQ branches add 0101 to 0103: renumber at merge |

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
- Branches fetched for this audit: `claude/hq-meetings` (one commit, F06 to F08 room, migration 0102),
  `claude/hq-executive` (two commits, executive PA). `claude/hq-data` and `claude/hq-campus-visual` had
  no pushed commits when the audit was written beyond a first push of `claude/hq-data`; re-run the matrix
  when they land.
