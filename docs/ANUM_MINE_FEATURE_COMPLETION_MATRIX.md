# ANUM MINE headquarters: feature completion matrix

Date: 25 October 2026. Audited commit: `341aebc` (main after PR 221). Docs only change plus verification. This file does not change any behaviour.

It answers one question per row: is the owner's requirement really built, and what proves it? Claims were checked by reading the code and running the fast checks listed below, not copied from other docs. Where a doc and the code disagree, the code wins and the row says so.

## How to read this file

Status words:

- **Complete**: built, wired into the live headquarters on `main`, with a test or a CI job that covers the main path.
- **Partial**: some of it is built. The gap column says exactly what is missing.
- **Missing**: not built on `main`.
- **Not applicable**: out of scope for this round, or it would break the data-truth rule if built as asked. The row says which.

"In flight" is not a status. It is a note on a Partial or Missing row: the work is open in a draft pull request and is not on `main`. In flight now: PR 245 (realism pass, glTF models), the voice fix, the starter campus, the navigation layout and assign/command slices. PR numbers merged and counted as `main`: 216 to 222 and 240 to 246.

The "How checked" column says how this audit saw the claim:

- **Ran**: this audit ran the check (listed below).
- **Read**: this audit read the code and found the claim true. No test was run for that claim here.
- **CI**: relies on the main CI run for `341aebc` (CI, CodeQL and Deploy staging all success on 25 October 2026). Not re-run here.
- **Not verified**: could not be checked, and the cell says why.

### Checks this audit ran

| Check | Result |
|---|---|
| `tsc -p tsconfig.json --noEmit` in `apps/web` | pass |
| Web unit tests for the headquarters (`headquarters*`, `campusModel`, `officePresence`, `officeFlow`, `executivePa`, `guidedExecutiveTour`, `liveMeeting`, `hqBuilder*`, `officeHqData`, `officeSpaceRegistry`, `officeArchitecturalScene`, `officeRouteIntegration`) | 168 of 168 pass |
| API unit tests: `test_office_history_api`, `test_executive_pa`, `test_live_agent_meeting_room`, `test_office_presence`, `test_office_flow`, `test_office_layout`, `test_office_provisioning` | 138 of 138 pass |
| `pnpm docs:check` | pass (see the PR) |

### What this audit could not run

- **PostgreSQL tests** (`-m database`, including `test_postgres_hq_rls.py`, `test_postgres_office_spaces.py`, `test_postgres_live_agent_meeting_room.py`). The local cluster at 127.0.0.1:55450 accepts connections, but this sandbox has no `ANUM_TEST_DATABASE_URL` credentials, so those tests would skip. They are marked CI only.
- **Browser end-to-end** (Playwright). Not run here; CI runs six shards. The matrix cites the spec names, not a local result.
- **Visual comparison with the five concept renders.** No screenshots were taken or compared in this audit, so every "looks like the render" claim is Not verified.
- **Real GPU, phone, microphone and speaker behaviour.** Not available in the sandbox.

### About the master prompt

The 14 sections are taken from the owner's summary. Section 14 (acceptance criteria) is not enumerated in that summary, so section 14 below derives one acceptance row per promise made in sections 2 to 13. If the owner's original list differs, add rows; do not relabel these.

## Counts

Counts per status are in the last section ("Totals"), computed from the tables in this file.

## Section 1. Read and verify the project

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 1.1 | Project read and verified against the code | Complete | Read | This file; `docs/ANUM_MINE_HQ_BASELINE_AUDIT.md` | none |
| 1.2 | Docs are indexed so the docs check passes | Complete | Ran | README index lines for every `docs/ANUM_MINE_*.md`; `pnpm docs:check` | none |
| 1.3 | The earlier baseline audit is still true | Partial | Read | The baseline audit is dated 22 October at `34dee88`. It says F10 (executive) is not started, lists the data, meeting and executive branches as unmerged, and lists Arabic (H-06) as open | Now merged: PRs 218, 219, 220, 221, 222, 242 to 244, 246. `hq.ar.ts` exists. PR: "docs: refresh the baseline audit statuses to main 341aebc" |
| 1.4 | Main is green | Complete | CI | CI, CodeQL, Deploy staging success on `341aebc`. The run for `5bcd047` shows CI failure and was followed by a green run | Cause of the `5bcd047` failure not investigated here |

## Section 2. Visual source of truth

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 2.1 | The visual spec and data-truth table are written down | Complete | Read | `docs/ANUM_MINE_VISUAL_REFERENCE.md` (PR 216) | none |
| 2.2 | Premium building exteriors and a campus with landscaping, water, roads, vehicles | Partial | Read, Ran, CI | `hqAssets/exterior/*` (executive HQ with atrium vault and A crown, stepped glass office tower, admin block with louvres and balconies, retail with a canopy and a blank signage frame, tyre workshop with rolled up bay doors, warehouse, personal villa with a pool), `facadeKit.ts` (spandrels, balconies, louvres, roof decks, canopies), `hqAssets/environment/*`, `lib/campusScene.ts`; `test/hqAssetsExterior.test.ts`, `e2e/hq-exterior-look.spec.ts`; before and after in `docs/ANUM_MINE_HQ_BUILDING_LOOK.md` | Still low poly and procedural: no photographic glass, no people on paths (by the data rule). Side by side with the concept renders on a real GPU: Not verified. Free CC0 models not added (none passed review for this pass) |
| 2.3 | Room interiors for office, finance, sales, customer care, operations, executive, reception, meeting, lounge, server area, pantry, restroom, warehouse, workshop | Complete | Read | `hqAssets/interior/index.ts` (`interiorKindFor`), `roomsFront.ts`, `roomsWork.ts`, `roomsIndustrial.ts`; glass partitions, door openings per `door_side`, floor finishes and the service core in `hqAssets/interior/shell.ts` and `core.ts` ([interiors](ANUM_MINE_HQ_INTERIORS.md)); `test/hqFloorCompleteness.test.ts` checks all 62 rooms of the starter campus | Which interior a room gets for kind `office` is chosen by words in the room name (a regex), not by a stored department. See 7.3 |
| 2.4 | Reception, executive office, workstations, meeting rooms, operations command centre all present | Complete | Read, Ran | Room kind `command` (API enum and validation, migration 0113, template `command_center`, contract, builder, 3D operations interior with the video wall, module = live flow, starter campus Command Center); `test_postgres_office_builder.py`, `test/hqBuildingModules.test.ts`, `e2e/hq-building-modules.spec.ts`; `docs/ANUM_MINE_HQ_BUILDING_MODULES.md` | Rooms made before this change keep their kind (an office named Command Center is still decorated by name); the owner can change the type in the builder |
| 2.5 | Day, blue hour and night | Complete | Read, CI | `lib/officeSolar.ts`, night prop in `HqStage.tsx`, `hqLighting.ts`; `e2e/hq-campus.spec.ts` "night and day both draw" | none |
| 2.6 | Wall of live dashboards in the command centre | Complete | Ran, CI | `HqCommandWall.tsx` (room inspector of an operations room, and a board over the selected room in the 3D scene from 900 px wide), `lib/hqCommandWall.ts`: open tasks, blocked work, pending approvals and running work from `GET /office/flow`, agents by state from the presence list; `test/hqCommandWall.test.ts`, `e2e/hq-live-scene.spec.ts`; `docs/ANUM_MINE_HQ_LIVE_SCENE.md` | A source that fails shows "Not connected" and no number. The numbers are drawn as a panel over the room; the screens of the 3D video wall stay abstract (no text is drawn onto 3D screens). Commerce counts are not on the wall: the commerce panel below the scene keeps them |
| 2.7 | Cinematic quality (lighting, depth, atmosphere) | Partial | Read | Physically based materials, shadow map, sky environment, fog, bloom at night (`lib/hqThreeRenderer.ts`) | Visual quality not judged by this audit: Not verified. PR 245 (in flight) adds models, glass relief and night halos |
| 2.8 | Characters match the concept's professional avatars | Partial | Read | See 5.3 | See 5.3 |
| 2.9 | Ambient scenery motion (traffic loops, water ripples, tree and flag sway, drifting clouds, window light drift, rooftop beacon) that never implies people or work | Complete | Read, Ran, CI | `hqAssets/environment/scenery.ts`, `lib/hqSceneryLayer.ts`, `lib/hqThreeRenderer.ts`; `docs/ANUM_MINE_HQ_SCENERY_MOTION.md`; `test/hqSceneryMotion.test.ts`, `e2e/hq-exterior-look.spec.ts` | Off by default on a software renderer; a real GPU run for frame cost is missing (3.10). Person and agent movement is a separate, record backed system and is not part of this row |
| 2.10 | Photographic look and quality tiers: procedural PBR materials, baked and screen space occlusion, contact shadows, camera fitted shadow, point lights, light shafts, depth of field, AgX grade, roof and window detail, a tier ladder with a pin for the top tier | Partial | Read, Ran, CI | `lib/hqQualityTier.ts`, `lib/hqPbrTextures.ts`, `lib/hqBakedAo.ts`, `lib/hqDetailPass.ts`, `lib/hqLightRig.ts`, `lib/hqPhotoShaders.ts`, `lib/hqSceneFx.ts`, `lib/hqCampusFx.ts`, `lib/hqPhotoFx.ts`; `docs/ANUM_MINE_HQ_PHOTOREAL.md`; unit tests `test/hqQualityTier.test.ts` and five more, `e2e/hq-photoreal.spec.ts` | Top tier checked on a software rasteriser for single frames only; real GPU frame cost and look are not verified (owner: pin `anum.hq.quality` = 4 and read `__anumHqBench`). No bevels, window reveals or instanced vegetation variety; contact discs not on interior floors |
| 2.10 | What is seen through the glass is that building's real floors and rooms, and the shell follows the floor plans | Partial | Read, Ran | Plan atlas `lib/hqPlanAtlas.ts`, shell from the plans `hqAssets/exterior/planShell.ts`, frame and keys `lib/hqPlanFrame.ts`, interior-mapped shader `lib/hqGlassShader.ts`, wiring `CampusView.tsx`, `campusRegistry.registryPlans`; `test/hqGlassPlan.test.ts`, `e2e/hq-glass-plan.spec.ts`; [glass plan](ANUM_MINE_HQ_GLASS_PLAN.md) | Parallax is on the hardware path (switched on by hand in the sandbox); a software rasteriser reads one texel. Not verified on a real GPU. Room rotation is not in the scene projection yet. No cross-dissolve to the opened building: that is a separate scene |

## Section 3. 3D engine

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 3.1 | Three.js engine | Complete | Read | `three` 0.184 in `apps/web/package.json`; `lib/hqThreeRenderer.ts`; loaded lazily by `HqStage.tsx` | Needs WebGL 2; without it the list and plan take over (3.14) |
| 3.2 | Orbit, pan, zoom, tilt | Complete | Read, CI | Pointer, wheel and pinch in `HqStage.tsx`; `lib/hqCamera.ts`; `e2e/hq-scene.spec.ts` | none |
| 3.3 | Focus and camera transitions | Complete | Read | Camera tween on control changes (`HqStage.tsx`), `alignYaw`, PA navigation moves the camera (`e2e/hq-campus.spec.ts` "executive PA dock ... moves the camera") | none |
| 3.4 | Top, isometric and interior view modes | Complete | Ran, CI | 3D, Top and List in `HqChrome.tsx`; "Inside" in the bottom toolbar: `insideCamera` in `hqCamera.ts` (eye height 1.5 m, pivot 5 cm from the eye so turning turns on the spot), button or Enter, Escape leaves, instant under reduced motion; `test/hqCamera.test.ts`, `e2e/hq-live-scene.spec.ts` "Inside" | Drag, rotate and the arrow keys stay inside the room (the eye is clamped to it). Looking up is not possible: the camera pitch range starts at a slight downward angle |
| 3.5 | Floor transitions and floor isolation | Complete | Read, Ran | Cut-away scene with cached ghost floors eased in and out by the renderer (360 ms, reduced motion jumps), active floor outlined, camera glides to the new floor, previous and next buttons, floor menu, Page Up and Page Down, live region, "N / M"; `test/hqFloorCutaway.test.ts`, `e2e/hq-building-modules.spec.ts`; `docs/ANUM_MINE_HQ_BUILDING_MODULES.md` | Frame time measured on software GL only (3.10); floors collapse flat rather than slide; "All floors" is the only other isolation mode |
| 3.6 | Clickable buildings, rooms, desks, agents | Complete | Read, CI | `pickNearest` in `lib/hqMath.ts`, label cards, room list; `e2e/hq-campus.spec.ts` "inside a building: floors, rooms, desks, agent cards" | none |
| 3.7 | Raycasting | Complete | Read | Ray against boxes of pick targets (`lib/hqMath.ts`) | Picks boxes, not triangles. Fine for rooms and desks |
| 3.8 | Full screen | Complete | Read | `toggleFullscreen` in `HeadquartersLive.tsx`, button in `HqChrome.tsx` | none |
| 3.9 | WebGL context-loss recovery | Complete | Read, Ran, CI | `webglcontextlost` and `webglcontextrestored` handlers in `hqThreeRenderer.ts` (reflection maps, glow pass and shadow map rebuilt on restore, scene rebuilt by `HqStage.tsx`, non-blocking banner, 8 s then the list and plan with a "Try 3D again" button); `e2e/hq-scene.spec.ts`, `hq-campus.spec.ts` "lost GPU context", `hq-gpu-check.spec.ts` | Simulated with `WEBGL_lose_context` on software GL; a real driver reset is not verified |
| 3.10 | Performance budgets | Partial | Read, CI | `docs/ANUM_MINE_HQ_PERFORMANCE_BUDGET.md`, `e2e/hq-performance.spec.ts`, the frame governor (3.16) | Numbers come from software rendering in the sandbox, a regression guard only. Never measured on a real laptop GPU or a phone. The owner can now produce that run in one click (3.17); a documented device run (two real devices) is still to be added to the budget doc |
| 3.11 | Keyboard control | Complete | Read, CI | `keyToCameraAction` in `hqCamera.ts`; `e2e/hq-scene.spec.ts` "keyboard only", `hq-campus.spec.ts` "reachable from the keyboard" | none |
| 3.12 | Reduced motion | Complete | Read, CI | `setReducedMotion` (no bloom, no tween), `hqFlow.css`, `meetingRoom.css`; `hq-scene.spec.ts` "reduced motion" | none |
| 3.13 | Minimap, compass, zoom, reset | Complete | Read | `CampusView.tsx` minimap, `HqChrome.tsx` compass and zoom, `viewFootprint` in `hqCamera.ts` | none |
| 3.14 | 2D and list fallback | Complete | Read, CI | `HqPlanFallback.tsx`, `CampusListView.tsx`; `hq-scene.spec.ts` "no WebGL", `hq-campus.spec.ts` "without WebGL" | none |
| 3.15 | LOD, instancing, culling | Partial | Read | Frustum culling per mesh (`cull`), a "lite" interior detail level for big buildings (`buildingScene.ts`), adaptive pixel ratio | No GPU instancing (meshes are merged per surface class) and no distance LOD for figures. PR: measure first with the performance spec at 200 desks, then add instancing only if it fails the budget |
| 3.16 | Runtime frame governor and quality tiers | Partial | Read, Ran, CI | `lib/hqQualityTiers.ts` (one tier registry), `lib/hqGovernor.ts` (rolling median, hysteresis, no flip-flop), `lib/hqThreeRenderer.ts` (pixel ratio, then shadows, then glow, then simple materials; never when pinned, on software or hidden), the header chip in `HqChrome.tsx`; `test/hqGpu.test.ts` with simulated frame times | Thresholds are reasoned defaults, never tuned on a real GPU. GPU time is not measured directly, frame time stands in for it |
| 3.17 | Graphics check panel and owner GPU script | Partial | Read, Ran, CI | Office tools, Graphics check (`hq-gpu/HqGpuCheckPanel.tsx`, lazy): device facts, per tier benchmark with a scripted orbit and floor change, verdict, pin, anonymous JSON report; `scripts/hq-gpu-check.mjs`; `docs/ANUM_MINE_HQ_GPU_CHECK.md`; `e2e/hq-gpu-check.spec.ts`, `hq-gpu-script.spec.ts`, axe, English and Arabic, five viewport sizes | Logic verified on software GL only (the panel reports the software rasteriser honestly). No real GPU result exists yet: the owner runs the script on a Windows PC with a GPU and sends the report |
| 3.18 | Cutaway section of the building (front facade and roof removed, all floors, Cutaway and Closed) | Partial | Read, Ran | `lib/hqCutaway.ts`, `lib/buildingScene.ts` (`walls`, `dimmed`), `BuildingView.tsx`; `test/hqCutaway.test.ts`, `e2e/hq-cutaway.spec.ts`; [cutaway](ANUM_MINE_HQ_CUTAWAY.md) | Lower floors show only their front 6 to 7 m under the slab above; side by side with renders 22 and 24 on a real GPU: Not verified; dimmed floors are rebuilt on every floor change; clicking a room on another floor does not switch floors |
| 3.19 | Campus centre: big fountain, roundabout and buildings standing round it | Complete | Ran | `hqAssets/environment/plaza.ts` (`centerPlaza`, `roundabout`, `avenue`), `lib/campusModel.ts` (`PLAZA`, `layoutCampus`: lots, slots, executive due north), `lib/campusSpokes.ts` (walkway from the roundabout to every door); `test/hqCenterPlaza.test.ts`, `e2e/hq-center-plaza.spec.ts`; `docs/ANUM_MINE_HQ_CENTER_PLAZA.md` | Buildings are not rotated, so only those north of the plaza face it; the campus is about twice the radius of the old one for the starter campus |
| 3.20 | Building billboards: a board in front of each building with its name, and a card with what the building does and its real work details | Partial | Read, Ran | `hq-billboards/` (lazy chunk: `billboardModel.ts`, `billboardSites.ts`, `BillboardCard.tsx`), `CampusView.tsx`; `test/hqBillboard.test.ts` (property and whitelist tests, Not connected), `e2e/hq-billboards.spec.ts` (five buildings, five sizes, English and Arabic, axe); [billboards](ANUM_MINE_HQ_BILLBOARDS.md) | Looked at on software GL only, not a real GPU; the 3D board carries the name only (details are the card); open work and room details exist for the open workspace's registry buildings only, every other building says Not connected |

## Section 4. Building and campus structure

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 4.1 | Campus of buildings, one per authorised workspace | Complete | Read, CI | `GET /api/v1/office/campus` (`office_campus_api.py`), `CampusView.tsx`; `e2e/hq-campus.spec.ts` "one building per workspace with real counts" | none |
| 4.2 | Personal office building | Complete | Read, Ran | The starter campus creates Personal Office through the click-confirmed flow (ticked by default); a registry building with the starter id `personal` has kind `personal` and is drawn as the villa (`campusRegistry.registryLook`, `archetypeOf`); `test/hqBuildingModules.test.ts`, `e2e/hq-building-modules.spec.ts`; `docs/ANUM_MINE_HQ_BUILDING_MODULES.md` | Nothing creates it without the owner's click, by design (O10); a registry building shows no building type label |
| 4.3 | Career building | Missing | Read | Building kinds are only `executive`, `business`, `department`, `personal` (`office_campus_api.py`) | Owner decision O2: is career a personal workspace, or its own kind? |
| 4.4 | ANUM OS HQ, Tyre Pulse, ANUM360 Commerce buildings | Complete | Read, Ran | The starter campus creates all three; each building shows its own panels by its stable id or the departments of its rooms (`lib/hqBuildingModules.ts`): ANUM OS wall with tasks and agents, Commerce orders and low stock, Tyre fleet, fitted and in stock tyres; Not connected and No access states; HQ is drawn as the executive tower, Tyre Pulse as the workshop, Commerce as the shop pavilion; `e2e/hq-building-modules.spec.ts`; `docs/ANUM_MINE_HQ_BUILDING_MODULES.md` | Numbers are per workspace, not per building (all starter buildings share one workspace) |
| 4.5 | Departments: Finance, Sales and Marketing, Operations, Customer Experience, Technology, Research, Legal, Security and Governance, Executive Office | Complete | Read, Ran | Storable departments now include marketing, technology, research, legal, compliance, security, hr, procurement (enum, `office_layout`, contract, migration 0108 check constraint, builder and team pickers in English and Arabic); each opens a real module or says none is connected (`docs/ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md`); starter campus rooms carry them and a re-run fills an empty or custom department only; provisioning roles `marketing_specialist`, `technology_coordinator`, `research_analyst`, `legal_reviewer` and `compliance_reviewer` linked to their rooms; `tests/test_office_provisioning.py`, `tests/test_office_starter_campus.py`, `tests/test_postgres_departments_meeting_rooms.py`, `e2e/hq-departments-meeting-rooms.spec.ts` | Security has a room and a module (Audit log) but no agent role, because no security tool exists to back one. HR opens Members and roles, which is access management, not a recruiting or payroll module; Procurement has no module. Roles are Limited where tools are missing (listed in the review dialog) |
| 4.6 | Configurable buildings, floors, rooms, desks | Complete | Read, CI | `office_space_api.py` create, patch, delete for all four kinds, `PUT /floors/{id}/layout`; builder `hqBuilder/*` | none |
| 4.7 | Tenant and workspace isolation | Complete | CI | Forced RLS on all office tables (`docs/ANUM_MINE_HQ_SECURITY_REVIEW.md` S-01, S-08); `tests/test_postgres_hq_rls.py` | PostgreSQL tests not run here: Not verified locally (no test database credentials). `H-10` notes this test was first run by CI |
| 4.8 | Cross-workspace executive view only by explicit grant | Complete | Ran, CI | `executive_api.py` grants (owner approves with payload hash, narrow only, revocable); `executive_pa.py`; `ExecutiveCommandCenter.tsx`; `test_executive_pa.py` pass | Postgres side of grants is CI only |
| 4.9 | First run: an empty workspace offers the campus inside the scene | Complete | Ran, CI | `hq-first-run/FirstRunPanel.tsx` shows the real plan numbers, the list, the plan fingerprint and one button; confirm sends the shown hash with `input_mode: "click"`; the campus, floors and rooms appear without a reload (own click or another browser's `office.layout_changed`); loading, error, denied, stale 409, refused confirm, partial retry; the assistant card (`CampusProposalCard`) needs the same click; `test/hqFirstRun.test.ts`, `e2e/hq-first-run.spec.ts`; `docs/ANUM_MINE_STARTER_CAMPUS.md#first-run` | The master list is still code, not editable in the app. Building names are stored in one language |
| 4.10 | Whole floor on one screen, every room labelled and clickable | Complete | Ran, CI | Floor overview mode and the fitted building camera (`lib/hqCamera.ts` `fitDistance`, `BuildingView.tsx`, floor rail "Whole floor"); cause of "only one room": a flat camera factor that cropped 38 m floors; `test/hqFirstRun.test.ts`, `e2e/hq-first-run.spec.ts`; `docs/ANUM_MINE_FLOOR_OVERVIEW.md` | Cards hide in the overview only desk and agent figures; people are listed in the room panel |
| 4.11 | Room and agent label cards like the concept renders: dark glass room cards, white agent cards with the real state, Back to campus card, floor selector, live strips | Complete | Ran, CI | `lib/hqLabels.ts` (density full, compact, dot; slab depth test; no overlap), `lib/hqLabelModel.ts` (anchors at room centres at the floor height, subtitles from the registry), `HqLabelCards.tsx`, `HqSceneStrips.tsx`, `hqLabels.css`; `test/hqLabelModel.test.ts`, `e2e/hq-labels.spec.ts`; spec `docs/ANUM_MINE_HQ_LABELS.md` | Subtitles are generated from kind, desks and department, so they read plainer than the hand-written concept lines. No Offline state in a team card (presence has none). Pan toggle added to the camera bar
| 4.12 | Green Concrete: the owner's company with a command center, L tables, a day and night shift and a manager at the back, created by a previewed owner click | Complete | Read, Ran | `office_green_concrete.py`, `office_green_concrete_api.py`, migration `0115_desk_seats_shifts`, `hq-green-concrete/`, `lib/hqShifts.ts`; people become paused agents with no tool; idempotent, never overwrites, audit without names; `test_office_green_concrete.py`, `test_postgres_office_green_concrete.py`, `test/hqGreenConcrete.test.ts`, `e2e/hq-green-concrete.spec.ts`; `docs/ANUM_MINE_GREEN_CONCRETE.md` | The shift hours are unconfirmed defaults, the night layout and the seat order are readings to confirm with the owner, layout history does not capture seat fields. PostgreSQL and browser tests run in CI |
| 4.13 | Work given to an agent shows at its desk, with the real record, and an off shift person is never lit | Complete | Read, Ran | Presence stream and work records unchanged; `lib/hqDeskState.ts`, desk lines and **Off shift, work waiting** in the room panel and cards; `e2e/hq-green-concrete.spec.ts` (work to Raed, work to a night person by day, the clock moving to night) | Presence is per agent and not shift aware, so the campus card counts every agent; only room and building label cards count working people by shift |

## Section 5. Digital characters and AI employees

Current characters are stylised, generic and static. They appear only at desks that have a real assignment.

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 5.1 | AI employees are real records linked to desks | Complete | Read, CI | `PATCH /office-spaces/desks/{id}/assignment` (visible agents only, audited), `agent_profiles` joined to desks in `office_presence.py` | none |
| 5.2 | Missing roles can be added through governance | Complete | Ran | `office_provisioning_api.py` (gaps, templates, proposals, confirm); `test_office_provisioning.py` pass; `e2e/hq-agent-provisioning.spec.ts` | none |
| 5.3 | Character look | Partial | Read | `person()` in `lib/buildingScene.ts` and `hqAssets/interior/figures.ts` (seated poses typing, reading, phone, talking, leaning; standing poses incl. walking). Generic, seeded by the agent id, no likeness | Box figures, about 380 triangles. Static: nothing moves. PR 245 (in flight) may improve models. Owner decision O1 on any decorative motion |
| 5.4 | Truthful states | Partial | Ran | Server states `working, queued, meeting, waiting_approval, blocked, idle, paused, unknown` with provenance and `last_verified_at` (`office_presence.py`, `test_office_presence.py`) | "Walking to meeting" has no stored record, so showing it would be invented. Owner decision O1. A queued task reads as Queued, never Working |
| 5.5 | State shown on the character or desk in the scene | Complete | Ran, CI | `hqAssets/interior/stateVisual.ts`: all eight server states have their own screen colour, bezel light, seated pose and a marker shape above the monitor (queue bars, speech bubble, flag, exclamation mark, pause bars, empty frame); a stale record is drawn as unknown; the panel shows the label and a one line explanation (`hq.stateHelp.*`); `test/hqStateVisual.test.ts`, `e2e/hq-live-scene.spec.ts` "the eight server states" | No walking figure: no record places an agent anywhere but at the desk (owner decision O1 stays open). The pose is a cue, not a measurement; the marker and the text carry the meaning |
| 5.6 | Agent card: name, role, status, last verified | Complete | Read, CI | `AgentPanel` and `StatusPill` in `HqPanels.tsx` (stale flag shown) | none |
| 5.7 | Private agents stay private | Complete | Read, CI | `agent_not_visible`, `desk:<id>` keys; `e2e/hq-campus.spec.ts` "a private agent shows no name and no work" | none |
| 5.8 | Characters only for real assignments, never invented people | Complete | Read | `occupantSeed` only where a desk has an agent (`buildingScene.ts`, `BuildingView.tsx`) | The concept preview (`OfficeTourView.tsx`) still draws illustrative staff; it is labelled and separate (13.7) |
| 5.9 | Meeting participants appear in the meeting room | Complete | Read, Ran | `agent_meeting_sessions.room_id` (migration 0108); the room inspector lists the agents stored as participants of the room's active meeting (names only from presence the viewer may see, otherwise "Private agent", people as a count); those agents already show the meeting status at their desks; `test/hqLabelsPresence.test.ts`, `e2e/hq-departments-meeting-rooms.spec.ts` | Participants are shown in the room panel, not as figures placed inside the room; a paused or scheduled meeting does not mark the room |

## Section 6. Personal Assistant (PA)

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 6.1 | PA acts through a governed tool interface | Complete | Ran | `executive_pa.py` (`GET /tools`, `POST /invoke`, `GET /tour`); `test_executive_pa.py` pass; `docs/ANUM_MINE_EXECUTIVE_PA.md` | none |
| 6.2 | PA never bypasses approvals | Complete | Ran | No approve or reject tool exists (unknown tool refused 422 and audited); writes need a payload hash and `input_mode: click`; voice and typed can never confirm; `test_executive_pa.py` | none |
| 6.3 | Typed commands | Complete | Ran, CI | `parseUtterance` English and Arabic (`lib/executivePa.ts`, `test/executivePa.test.ts` pass); `ExecutivePanel.tsx` | Parser only proposes; unknown sentences are refused, not guessed |
| 6.4 | Voice commands | Partial | Read | Browser speech recognition via `createPushToTalk` (`lib/voice.ts`), language `ar-AE` or `en-US` | Not verified with a real microphone. Browser recognition availability varies; a voice fix is in flight |
| 6.5 | Guided tour built from real buildings and rooms | Complete | Ran | One tour: the dock and the headquarters' Guided visit both read `GET /executive-pa/tour` (accessible buildings, floors, rooms only); stops show registry facts only (kind, desks, capacity), no staff, no demo disclaimer; moves through `hqCommands.navigateTo`; loading, failed, empty and not-connected states; the concept preview has no tour. `test/guidedExecutiveTour.test.ts`, `e2e/hq-guided-visit.spec.ts` | Visit history is kept in memory only |
| 6.6 | PA daily briefing | Complete | Ran | `GET /executive-pa/briefing` and the read-only `daily_briefing` tool (`pa_briefing.py`): approvals, blocked work, tasks due or overdue, running agent runs, meetings today, agents needing attention, cost against budget; real records only, each line links to its source; "Nothing to report", "Not connected", "Not available for your role"; template text with a figure guard, no model; typed and voice "brief me" in English and Arabic; `test_pa_briefing.py`, `e2e/pa-briefing.spec.ts` | Days are UTC days; only task lines open the exact record (other screens take no record id); cost is owner only |
| 6.7 | PA moves the scene | Complete | Read, CI | `hqCommands.navigateTo`, `anum:hq-navigate` event (`lib/hqExtensions.ts`); `e2e/hq-campus.spec.ts` "the assistant can move the view" | none |
| 6.8 | PA as a visible character in the office | Missing | Read | The PA is a dock panel with a glyph (`ExecutivePanel.tsx`); no 3D guide | Owner decision O9: does the owner want a PA persona in the scene; its name and look must not be invented |
| 6.9 | Writes (start task, delegate, schedule meeting) with visual confirmation | Complete | Ran | Tools in `executive_pa.py`; hash bound to tenant, workspace, user, tool, arguments; `test_executive_pa.py` | `schedule_meeting` creates a live session; the meeting API has no start time, so nothing is scheduled for a time |
| 6.10 | Every PA call audited | Complete | Ran | Audit record `executive_pa.invoke`, refusals included | none |
| 6.11 | Arabic voice and narration | Complete | Ran | Arabic words in the parser ("brief me" included); recognition language follows the page; tour narration and the briefing are written in Arabic and spoken with `ar-SA`, right to left, no overflow at five sizes (`e2e/hq-guided-visit.spec.ts`, `e2e/pa-briefing.spec.ts`) | Spoken quality depends on the browser's Arabic voice; recognition with a real microphone is 6.4 |

## Section 7. Every room opens a useful real module

Today a room panel shows the room, its desks and presence, then one link chosen by room kind (`MODULE_ROUTES` in `HqPanels.tsx`): meeting to Meetings, reception to Inbox, office to Tasks, workshop to Tyre, warehouse to Commerce, lounge to Calendar. A finance, sales, executive or command room is kind `office`, so it opens Tasks.

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 7.1 | Each room kind opens a module | Complete | Read | `lib/hqRoomModule.ts` (department to module table, now covering every storable department), `HqRoomModule.tsx` in the inspector and the room list; `test/hqRoomModule.test.ts`, `e2e/hq-room-modules.spec.ts`, `e2e/hq-departments-meeting-rooms.spec.ts` | Executive stays resolved through the executive building kind (not a storable department). Procurement and custom say no module is connected |
| 7.2 | Operations command centre | Complete | Ran, CI | Operations department opens the live flow dock (`HqFlowDock`) from the room inspector and list, now filtered to that room's work (`openFlowFor` in `HeadquartersLive.tsx`, "Showing X only", one button clears it); the wall (2.6) sits in the same inspector; `e2e/hq-live-scene.spec.ts` "filtered to that room" | The filter is the work located in that room (the agents seated there); work with no office is shown again with "Show all offices"; the room kind `command` now opens the same live flow and wall (`docs/ANUM_MINE_HQ_BUILDING_MODULES.md`) |
| 7.3 | Finance room opens Finance | Complete | Read | `lib/hqRoomModule.ts` (department to module table), `HqRoomModule.tsx` in the inspector and the room list; `test/hqRoomModule.test.ts`, `e2e/hq-room-modules.spec.ts` | none |
| 7.4 | Meeting room opens the live meeting | Complete | Read, Ran | A meeting stores its room (`room_id`, migration 0108, composite foreign key, validated kind and workspace); a room with an active meeting opens `#office/meeting/<id>` from its module link (and from a room of another department); "Start a meeting here" and a room list in the meeting create form set the link; `test_postgres_departments_meeting_rooms.py`, `e2e/hq-departments-meeting-rooms.spec.ts` | The live room opens as the full page, not inline in the side panel; only active meetings mark a room; one meeting is shown per room |
| 7.5 | Tyre engineering opens Tyre | Complete | Read, CI | Workshop kind to `#tyre`; Tyre panel in the building (`hqModules.ts`, `e2e/hq-campus.spec.ts`) | none |
| 7.6 | Sales room opens Sales | Complete | Read | Sales department opens the Sales and CRM view (`#whatsapp`); `lib/hqRoomModule.ts` (department to module table), `HqRoomModule.tsx` in the inspector and the room list; `test/hqRoomModule.test.ts`, `e2e/hq-room-modules.spec.ts` | none |
| 7.7 | Executive office opens the executive desk | Complete | Read, Ran | `executive` is a storable room department (migration `0116_room_dept_executive`, expand only, no backfill; contract, API literals, builder label in English and Arabic). A room whose stored department is executive opens `#executive` whatever its kind and building (`lib/hqRoomModule.ts`); the starter campus Executive Office carries it; `test/hqRoomModule.test.ts`, `test/hqBuildingModules.test.ts`, `test_office_starter_campus.py`, `test_postgres_departments_meeting_rooms.py`, `e2e/hq-room-modules.spec.ts` (English and Arabic) | An existing campus's Executive Office adopts the department only through the owner's reviewed starter campus confirm click (`department_set`) or the builder's department field; nothing is rewritten silently. The building kind rule stays as a fallback |
| 7.8 | Workstation opens the desk's real work | Complete | Read, CI | `HqWorkstationWork.tsx`, `GET /office/workstations/{desk}/work` (tasks, run phase, checkpoint, approvals, errors, documents); `e2e/office-workstations.spec.ts` | none |
| 7.9 | Reception opens a real visitor workflow | Partial | Read | The Reception panel and its tool entry are removed from the live office; a reception room shows its desks and 'no visitor register'. The local reception simulation with its example hosts is deleted from the Concept preview too (security review S-11); `test/conceptOfficeData.test.ts` fails if it returns; `docs/ANUM_MINE_RECEPTION_MODULE.md` | Visitor registry not built: owner decision O4 is open |
| 7.10 | Module panels never show numbers without a source | Complete | Read, CI | `ModuleState` loading, not_connected, forbidden, error, ready (`lib/hqModules.ts`) | none |

## Section 8. Real-time backend

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 8.1 | Snapshot, then authorised events, for presence | Complete | Ran, CI | Server: `GET /office/presence` and `/presence/stream`, `test_office_presence.py`. Web: `lib/presenceStream.ts` (`PresenceHub`, one shared hub per workspace for the whole page, `acquirePresenceHub`), `usePresenceLive.ts`, `lib/hqPresenceApi.ts`; the shell's 10 s polling of presence is gone; `test/presenceStream.test.ts`, `e2e/hq-live-scene.spec.ts` | The stream is read with `fetch` (EventSource cannot send the sign-in headers) |
| 8.2 | Disconnect, duplicate and stale handling | Partial | Ran, Read, CI | Web: reconnect with backoff (1 s doubling to 30 s, jitter) and `Last-Event-ID`, a repeated or older delta ignored, a gap in the sequence takes a new snapshot, a silent link (45 s without a keep-alive) is dropped, a stream that sends nothing in 8 s counts as blocked and the page polls every 10 s, items age to stale on the server's clock (5 minutes); `test/presenceStream.test.ts`. Server de-duplicates by event id and sends keep-alives (`office_presence_api.py`) | H-09 cause found and fixed: the 0.3 to 0.8 percent "loss" was the load tool dropping frames that arrived before the producer knew the task id; the server delivered 10,000 of 10,000 at 200 clients in memory mode (3 runs). Not run on the Oracle VM or in PostgreSQL mode: owner steps in `docs/ANUM_MINE_HQ_LOAD_TESTING.md` |
| 8.3 | Persisted layouts with optimistic concurrency | Complete | Ran, CI | `expected_revision` on every patch and delete, 409 on conflict; `PUT /floors/{id}/layout` is atomic; `test_office_layout.py` pass; PG tests CI | none |
| 8.4 | Layout audit | Complete | Read | `_audit` with before and after on every write (`office_space_api.py`) | none |
| 8.5 | Work moving through offices and stuck work | Complete | Ran | `office_flow.py`, `GET /office/flow`, item trails, `test_office_flow.py` pass; `e2e/hq-live-flow.spec.ts` | Orders have no local history: trail says `not_recorded` |
| 8.6 | Layout revision history and restore | Complete | Read, CI | Append-only `office_floor_revisions` (migration 0112, forced RLS, purge policy, lifecycle list, cap per floor) written in the same transaction as every layout change; `GET /office-layout/floors/{id}/revisions[/{n}]`, restore `POST .../restore` (`workspace:manage`, click only, payload hash, head check 409, exact unassign list, idempotent, new revision, audited). `test_office_layout_history.py`, `test_postgres_layout_history.py` (CI only), `docs/ANUM_MINE_LAYOUT_HISTORY.md` | Floors only (not building rename); revisions start at the first tracked change after the migration |
| 8.7 | Events are authorised | Complete | Read | `task:read` and `event:read` required on the stream; events carry ids, not payload text | none |

## Section 9. Live agent meetings

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 9.1 | Lifecycle: create, add agents, activate, pause, resume, end or cancel | Complete | Ran, CI | `live_agent_meeting_api.py`; `test_live_agent_meeting_*`; migrations 0098, 0104; `e2e/meeting-room.spec.ts` | none |
| 9.2 | Governed model runtime with limits | Complete | Read, CI | Durable turn leases, per-meeting max turns, time, tokens, cost, owner pause, emergency stop, a mock model cannot run a turn; secrets redacted before storage (`live_agent_meeting_runtime.py`, `live_agent_meeting_redaction.py`) | none |
| 9.3 | Workspace-wide meeting budget | Complete | Read, CI | S-02 closed: USD 50 per workspace per UTC month (owner changes it), gate under a workspace lock before each turn, 402 and pause when reached; `docs/ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md`, `test_postgres_live_agent_meeting_security.py` | Owner decision O6 settled (USD 50, monthly) |
| 9.4 | Unattended running and crash resume | Partial | Read | `unattended_meeting_worker.py`, lease expiry and retry, terminal-state guards (`test_unattended_meeting_terminal.py`) | The worker is a managed service; nothing here proves one runs anywhere. Not verified. Do not run a long meeting through an HTTP request |
| 9.5 | Live room: transcript, speakers, hands, owner interrupt | Complete | Read, CI | `MeetingRoomPanel.tsx`; `e2e/meeting-room.spec.ts` (join, turn streams, interrupt, reconnect replay, end) | none |
| 9.6 | Meeting summary | Complete | Read, CI | Owner-triggered, ended meeting only: one metered model call (counts against the USD 50 monthly budget, 402 at the cap) stored as a draft output of kind `summary` with `source_sequences`; statements without a real citation are dropped; confirmed by the owner with a click and the shown hash; audited with cost and month used before and after; transcripts stay owner only. `docs/ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md`, `test_postgres_meeting_summary_links.py`, `e2e/meeting-summary.spec.ts` | Whether a cited message truly supports a line is judged by the owner when reviewing, not machine-checked. No automatic summary at meeting end |
| 9.7 | Decisions and action items | Complete | Read, CI | People draft them, or the same summary call drafts them from the transcript (`origin: model`, never auto-confirmed, click and hash to confirm); each links to source messages. `docs/ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md` | Model drafts depend on the model following the citation rule; uncited ones are dropped by the server |
| 9.8 | Action item becomes a task | Complete | Read, CI | `POST /{id}/outputs/{output_id}/task` (needs `task:create`, status `created`, `task_id` stored on the output); e2e "create a task" | Running the task still follows approval rules |
| 9.9 | Decision linked to work | Complete | Read, CI | A confirmed decision links to a task, approval or run of the same workspace (`agent_meeting_output_links`, migration 0111, forced RLS, purge policy, lifecycle list); click only, idempotent, audited; shown on the decision, in "Work from this meeting" and in the export; reverse lookup `GET /agent-meetings/work-links` (owner). `test_postgres_meeting_summary_links.py` | The Tasks and Approvals pages do not yet show the reverse lookup |
| 9.10 | Export | Complete | Read, CI | Markdown and JSON in the web record card; JSON has summary, decisions with their linked work, links and confirmed outputs; owner only. `docs/ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md`, `e2e/meeting-summary.spec.ts` | No PDF (not required) |
| 9.11 | Delete and retention | Partial | Read | Owner can delete an ended meeting's transcript (`DELETE /transcript`); counts, confirmed outputs and tasks remain | No automatic retention window. Owner decision O5 |
| 9.12 | Turn execution audited | Complete | Read, CI | S-04 closed: `agent_meeting.turn` per turn (success, failure, blocked, denied) with ids, usage, cost, correlation id and no text; `docs/ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md` | None |
| 9.13 | Transcript visibility | Complete | Read, CI | S-03 closed: turns, events, stream, export are workspace owner only (403 otherwise, audited); room bundle carries no text for others; `docs/ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md` | Owner decision O7 settled: owner only |
| 9.14 | Meetings visible in the office scene | Complete | Read, Ran | Rooms with an active meeting show "In a meeting" on the 3D label card, the scene mark, the list and the inspector, from the stored `room_id`; it disappears when the meeting ends (`HeadquartersLive` derives `liveMeetingRoomIds` from the meeting evidence); `e2e/hq-departments-meeting-rooms.spec.ts` | The 3D look of the mark on a real GPU is not verified |
| 9.15 | Stored meeting replay | Complete | Read, CI | Finished meeting opens as a stored replay with playback (`e2e/meeting-room.spec.ts` "finished meeting opens as a stored replay") | Replays stored events only |

## Section 10. Work history and replay

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 10.1 | Evidence history API | Complete | Ran | `GET /api/v1/office-history`, `/at`, `/correlations/{id}` (`office_history_api.py`); `test_office_history_api.py` pass; `docs/ANUM_MINE_F09_AUDITED_HISTORY_API.md` | none |
| 10.2 | Searchable timeline in the product | Complete | Ran, CI | `HqHistoryPanel.tsx` (Office tools, History; room inspector, agent card and Team card entry points), event type, actor, room, agent and date filters, infinite scroll and "Load older records" while `next_cursor` is set; `test/hqHistory.test.ts`, `e2e/hq-history.spec.ts`; `docs/ANUM_MINE_HQ_HISTORY_PANEL.md` | none. Department and project filters are not offered: audit records carry neither |
| 10.3 | Free-text search | Complete | Ran | `GET /office-history` takes `q` (actor, action, target), `since`, `until`; `test_office_history_http.py`, `test_office_history_api.py` | Search does not look inside recorded metadata |
| 10.4 | Correlation ids | Partial | Ran, CI | The record side panel shows the correlation id and lists the records of the same request (`GET /correlations/{id}`, paged with a count, an honest message for a record without an id), a button that opens the whole request as the timeline (which can be exported), and the task, run, approval or meeting it points at, with a summary read from the existing routes and a "Show records that name this task" action; `lib/hqHistory.ts`, `e2e/hq-history.spec.ts`, `e2e/hq-history-export.spec.ts` | No link in the other direction: a task, meeting or approval screen does not open its correlation view (those screens are outside the lazy History chunk). Full transcript text and past snapshots stay out (owner decisions O5, O8: not decided here)
| 10.5 | Replay of the office at a past time | Partial | Ran, CI | Room history slider over the room's stored audit records: before and after values per step, "Cannot be reconstructed for this time" for gaps, times before the oldest loaded record and the future; `lib/hqRoomHistory.ts`, `test/hqHistory.test.ts`, `e2e/hq-history.spec.ts` | Layout changes of one room only. Past presence, occupancy and whole-office replay need time-indexed snapshots (new tables, retention, owner decision O8); nothing is stored by this change and none is drawn |
| 10.6 | Replay only where verified | Complete | Read | `EVIDENCE_NOTICE`, no invented status, `unknown` fields | none |
| 10.7 | Item trail (task, lead, order) | Complete | Ran | `GET /office/flow/items/{kind}/{id}/trail`, allow-listed fields; `test_office_flow.py` | Orders: `not_recorded` |
| 10.8 | Access limited | Complete | Ran | Owners and authorised organisation admins only (`HISTORY_ACCESS`) | none |
| 10.9 | History export | Partial | Ran, CI | "Export these records (CSV)" is offered for every view. The office, a named type and one request use the audited audit export (`GET /audit/export`, permission `audit:export`) with the same filters. Room views, agent views and the "Other" type read the same audited export as JSON, keep only the rows the view shows and save them as CSV on the device (`lib/hqHistoryExport.ts`, `lib/hqHistoryExportApi.ts`), so nothing beyond what the permission already gives; keys that could hold secrets or conversation text are dropped; a viewer without the permission sees it disabled with who can grant it; `test/hqHistoryExport.test.ts`, `e2e/hq-history-export.spec.ts` | No JSON button in the panel. A narrowed export reads the whole matching audit export before narrowing (large logs take longer); a server-side room or agent filter would avoid that. The audit record of the export names the broad filters, not the room or agent

## Section 11. Office management

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 11.1 | Create buildings and floors | Complete | Read, CI | `OfficeSpaceLiveManager.tsx` forms over `POST /office-spaces/buildings` and `/floors` | none |
| 11.2 | Create, edit, move, rotate, resize rooms; doors; equipment | Complete | Ran, CI | `hqBuilder/*` (`PlanCanvas`, `RoomDetails`, `EquipmentEditor`), `test/hqBuilder*.test.ts` pass; `e2e/hq-builder.spec.ts`, `hq-builder-responsive.spec.ts` | none |
| 11.3 | Arrange desks and assign agents | Complete | Read, CI | `DeskPanel.tsx`, `PATCH /desks/{id}/assignment` | none |
| 11.4 | Save and reload | Complete | Read, CI | Atomic floor save, draft model, rebase on conflict (`hqBuilder/draftModel.ts`) | none |
| 11.5 | Undo and redo | Complete | Ran | `hqBuilder/draftModel.ts` history; `hqBuilderDraft.test.ts` pass | Draft level only, before saving |
| 11.6 | Revisions (browse, restore) | Complete | Read, CI | History tab with list, diff preview, restore dialog (`hqBuilder/HistoryTab.tsx`, `RestoreDialog.tsx`, `MiniPlan.tsx`); all states, English and Arabic, five viewports; `hqLayoutHistory.test.ts`, `e2e/hq-layout-history.spec.ts` | none |
| 11.7 | Audit | Complete | Read | See 8.4 | none |
| 11.8 | Templates | Complete | Read, CI | Fixed room templates and room copy (`office_layout.py`), plus owner-named floor and building templates (`office_layout_templates`, `POST /office-layout/templates/from-floor`, `/from-building`, `/floors/from-template`, `/buildings/from-template`; desks empty, no agent copied); Templates tab | Whole-office JSON export and import not built (optional); a template makes a new floor or building, not an edit of an existing floor |
| 11.9 | Edit directly in the 3D scene | Missing | Read | Editing is on the 2D plan canvas only | Optional. A drag handle in 3D is large work; the plan is enough unless the owner asks |
| 11.10 | Delete with safeguards | Complete | Read | Versioned deletes (`expected_revision` query), restricted-state 409 | none |
| 11.11 | English and Arabic builder | Complete | Read, CI | `lib/messages/builder.en.ts`, `builder.ar.ts`, `name_ar` on rooms; `hq-builder-responsive.spec.ts` | none |

## Section 12. Quality and visual verification

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 12.1 | Playwright for the main paths | Complete | CI | `e2e/hq-scene`, `hq-campus`, `hq-live-data`, `hq-live-flow`, `hq-builder`, `hq-agent-provisioning`, `hq-a11y`, `hq-csp`, `hq-performance`, `executive`, `meeting-room`, `office-workstations` | Not run here |
| 12.2 | Five-viewport responsive gate (1440x900, 1366x768, 1024x768, 768x1024, 390x844) | Complete | Read, CI | `@gate` tests in `hq-campus.spec.ts`, `hq-live-flow.spec.ts`, `hq-builder-responsive.spec.ts`, `executive.spec.ts`, `meeting-room.spec.ts`, `hq-agent-provisioning.spec.ts` | `hq-scene.spec.ts` and `hq-live-data.spec.ts` run at one size |
| 12.3 | Screenshots of every state, kept for review | Partial | Read | Some specs write screenshots to the test output (`hq-builder-responsive.spec.ts`) | Not stored in the repo, no visual-regression baseline, none taken by this audit: Not verified. PR: a screenshot sweep job that uploads the campus, building, room, meeting, PA and builder states as a CI artifact |
| 12.4 | Web unit tests and typecheck | Complete | Ran | 168 of 168 and `tsc` pass | none |
| 12.5 | Backend unit tests | Complete | Ran | 138 of 138 pass | none |
| 12.6 | PostgreSQL and RLS tests | Complete | CI | `test_postgres_hq_rls.py`, `test_postgres_office_*`, `test_postgres_live_agent_meeting_*` | Not verified locally: no test database credentials here |
| 12.7 | Security tests and review | Partial | Read | `docs/ANUM_MINE_HQ_SECURITY_REVIEW.md`, `e2e/hq-csp.spec.ts`, RLS test | S-02, S-03, S-04 and S-05 are closed (see 9.3, 9.12, 9.13; S-05 is a per person and per workspace turn rate on `execute-turn` and `run`, `tests/test_live_agent_meeting_turn_rate.py`, per process memory so N replicas allow N times the rate). S-11 is closed (the reception simulation is deleted and the concept preview's example data is isolated and test-guarded). Still open: S-10 (stream per scope, confirm on Oracle). Row stays Partial; totals unchanged |
| 12.8 | Accessibility | Complete | Read, CI | `e2e/hq-a11y.spec.ts`: English and Arabic, light and dark | Open H-13 (close button covered by header in the overlay) was found; status not re-checked |
| 12.9 | Load and streaming | Partial | Read | `scripts/hq-load/hq_load.py`, `docs/ANUM_MINE_HQ_LOAD_TESTING.md` | Memory mode: 0 percent loss at 200 clients (H-09 was a tool counting bug, fixed); never run on the Oracle VM with PostgreSQL |
| 12.10 | Rollback | Complete | Read | `VITE_ANUM_HQ=off` (`lib/headquartersFlag.ts`), expand-only migrations, `docs/ANUM_MINE_HQ_DEPLOYMENT_AND_ROLLBACK.md` | No rehearsal recorded |
| 12.11 | Flutter parity | Not applicable | Read | Owner excluded the Flutter companion (F12) from this round | none |

## Section 13. Delivery rules

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 13.1 | One slice per pull request, draft first, never self-merged | Complete | Read | PR history 216 to 246 | none |
| 13.2 | Docs updated and indexed in the same change | Complete | Ran | README lists every HQ doc; `pnpm docs:check` | none |
| 13.3 | Migrations expand only, ids at most 32 characters, one chain | Partial | Read | 0097 to 0106 are additive (`0105_office_builder.py` header states it) | Two files are numbered 0099 (`0099_office_create_keys.py`, `0099_office_room_geometry.py`); ids chain correctly (finding H-12). PR: rename one file to remove the duplicate number |
| 13.4 | Real data only, no invented people or activity in the live view | Partial | Read | Presence, work, flow, meetings and modules are record-backed with honest empty states; the invented Reception panel is gone from the live view (PR claude/hq-room-modules) | The second guided tour with invented descriptions is gone (6.5). The concept preview toggle stays visibly separate; owner decision O12 |
| 13.5 | English and Arabic | Partial | Read | `lib/messages/hq.en.ts` and `hq.ar.ts` (the unit test `test/i18n.test.ts` fails when a key exists in one language only), `hqFlow.*`, `builder.*`; `hq-campus.spec.ts` "Arabic ... right to left"; `hq-a11y.spec.ts`; registry names are covered by 13.7 | `GuidedExecutiveTour.tsx` and `ReceptionOperations.tsx` are English only. The legacy `OfficeSpaceLiveManager.tsx` (Manage spaces) is English only and has no Arabic name fields (the builder has them). PR: translate those panels |
| 13.6 | Every state handled (loading, empty with next action, error, denied, large) | Complete | Read, CI | `e2e/hq-campus.spec.ts` "refused campus read explains who can fix it", "no saved layout says what to do next", list search; paged history API | none |
| 13.7 | Building, floor and room names have an Arabic form, shown in Arabic mode with an English fallback | Complete | Read, Ran | Migration `0114_building_floor_name_ar` (nullable `name_ar` on buildings and floors; rooms had it since 0105), create, edit, templates and the starter campus carry it, `GET|POST /api/v1/office/starter-campus/arabic-names/*` (owner click, hash, fills empty names of master records only), one web helper `lib/registryNames.ts`, builder fields (right to left), Arabic sign atlas (`hqTextures.ts`); `test_postgres_office_arabic_names.py`, `test_office_starter_campus.py`, `test/registryNames.test.ts`, `e2e/hq-arabic-names.spec.ts`; `docs/ANUM_MINE_ARABIC_NAMES.md` | PostgreSQL and browser tests run in CI. Workspace and agent names are not registry records and stay as typed. Campuses made before 0114 show English names until the owner clicks Add Arabic names (Office tools, Set up my campus) |
| 13.7 | No em or en dashes, no secrets, no model names | Complete | Ran | This file checked with a search; gitleaks and CodeQL green on main | Repo-wide dash check not run |

## Section 14. Acceptance criteria (derived)

| ID | Criterion | Status | How checked | Evidence and reason |
|---|---|---|---|---|
| 14.1 | The office opens as a premium 3D office of the person's real buildings | Partial | Read, CI | Real buildings and honest empty states are built (4.1); look is Not verified against the renders; glTF models in flight (2.2) |
| 14.2 | Campus, building, floor, room, desk and agent can be reached by mouse, touch and keyboard | Complete | Read, CI | 3.2, 3.6, 3.11 |
| 14.3 | Every room opens a useful real module | Partial | Read, Ran | All 62 starter campus rooms are audited by `test/hqBuildingModules.test.ts`: each opens a shell view, the live flow or a meeting, except Reception and Procurement, which say plainly why (the Executive Office opens the executive desk by its stored department); the Career Office no longer opens Inbox and support by a name match; `docs/ANUM_MINE_HQ_BUILDING_MODULES.md` | Twelve starter rooms have no department and open a generic module until the owner sets one: each is a judgment call (table in `docs/ANUM_MINE_HQ_BUILDING_MODULES.md`); the Executive Office now carries `executive`. Procurement is wired to its department but no procurement module exists; Reception waits for the visitor register (O4) |
| 14.4 | Everything shown about agents is real and truthful | Partial | Read | Presence and desks are real (5.1 to 5.8); the Reception panel and its invented hosts are deleted (7.9, S-11); the second tour was removed (6.5); "walking" cannot be truthful (5.4) |
| 14.5 | The PA tours, takes typed and voice commands, briefs, and never bypasses approvals | Partial | Ran | Tools, tour (one real tour), commands, daily briefing, approvals gate are done (6.1 to 6.3, 6.5, 6.6, 6.7, 6.9); the briefing is read-only and cannot decide anything | Voice with a real microphone not verified (6.4) |
| 14.6 | The office updates live and recovers from disconnects | Partial | Ran, Read | Web uses the presence stream with the visible Live, Reconnecting, Checking every 10 s and Not connected status (8.1, 8.2); `e2e/hq-live-scene.spec.ts` | No long session run against the Oracle VM (H-09 itself closed in memory mode) |
| 14.7 | Layouts persist with concurrency control and audit | Complete | Ran, CI | 8.3, 8.4 |
| 14.8 | Live agent meetings run within limits and end with summary, decisions, tasks and export | Complete | Read, CI | Limits, summary, model-drafted and typed decisions, decision to work links, action item to task and export (9.1 to 9.10) | None for this row; see the gaps in 9.4 and 9.11 |
| 14.9 | Work history is searchable and replay is shown only where verified | Partial | Ran, CI | History panel (10.2 to 10.5, 10.9); the "only where verified" rule is kept (10.6) | No real replay of presence or occupancy (owner decision O8) |
| 14.10 | The owner can create, edit, arrange, assign, save, restore and template an office | Complete | Read, CI | Create, edit, arrange, assign, save (11.1 to 11.5), restore (8.6, 11.6), floor and building templates (11.8) | JSON export and import of a template is the one optional piece left |
| 14.11 | Tenants and workspaces stay isolated | Complete | CI | 4.7, 4.8 (PG tests CI only) |
| 14.12 | Fallbacks, reduced motion and performance budgets hold | Complete | Read, CI | 3.9, 3.12, 3.14; budget run on a real device is missing (3.10) |
| 14.13 | Tests and screenshots cover every state | Partial | Read, CI | Tests broad (12.1, 12.2); screenshots not kept (12.3) |
| 14.14 | Docs and delivery rules followed | Complete | Ran | 13.1, 13.2 |

## Ranked next actions (top 10)

1. **Done in PR claude/hq-room-modules: removed the invented Reception panel from the live office (7.9, 13.4).** It shows made-up host and room names with no saved data. Smallest honest fix: hide it in the live view. Then decide O4.
2. **Done in PR claude/hq-room-modules: rooms open the right module (7.1 to 7.3, 7.6, 7.7).** Use the room's stored department so Finance opens Finance, Sales opens Sales, Executive opens the executive desk, Operations opens the flow dock.
3. **Done in PR claude/hq-departments-meeting-rooms: link meetings to rooms (7.4, 5.9, 9.14) and departments with real modules (4.5).** One nullable room id on meeting sessions; the room lights up "In meeting" and opens that live meeting. Marketing, Technology, Research, Legal, Compliance, Security, HR and Procurement are storable departments with a module or an honest "none", and four new governed agent roles.
4. **Build the History panel (10.2, 10.4, 14.9).** The API is done and tested; no one can use it yet. Add filters, paging and correlation view.
5. **PA daily briefing (6.6).** Done in this change: a read-only tool and endpoint that compose approvals, blocked work, tasks due, running runs, meetings, agents and cost, naming each source.
6. **Fix the second guided tour (6.5, 6.11).** Done in this change: one tour from the real tour endpoint, Arabic narration, no invented stops.
7. **Done in PR claude/hq-live-scene: the web uses the presence stream (8.1, 8.2, 14.6), shows all eight states (5.5), the operations wall (2.6), the filtered flow dock (7.2) and the Inside camera (3.4).** Still to do: run the load test on the Oracle VM with PostgreSQL (H-09 root cause is fixed; the VM run is confirmation).
8. **Meeting summary and decision to work links (9.6, 9.9, 9.7).** Owner-confirmed summary, decisions linked to tasks, linked items in the export.
9. **Done in PR claude/hq-layout-history: layout history and restore (8.6, 11.6) and floor and building templates (11.8).** Stored snapshots written in the save's own transaction, a click only governed restore, and a History and Templates tab.
10. **Close the open security findings (9.3, 9.12, 9.13, 12.7).** Workspace meeting cap, audit each turn, decide transcript visibility. Done in the meeting budget PR (S-02, S-03, S-04) and the turn rate change (S-05); owner decision O5 (retention) stays open.

Not in the top ten but next: the realism review after PR 245 (2.2, 2.7), a screenshot sweep artifact (12.3), the migration number clash (13.3), the `hq.en` and `hq.ar` key parity test (13.5).

## Owner decisions still open

| Id | Decision | Why it matters |
|---|---|---|
| O1 | May characters move (walk to a meeting) as a labelled decoration between recorded states, or must every pixel of movement be backed by a record? | "Walking to meeting" has no stored fact (5.4) |
| O2 | Is "career" a personal workspace, or its own building kind? | 4.3 |
| O3 | Resolved by the owner (25 October 2026): Marketing, Technology, Research and Legal rooms must be complete and their agents must get the right skills. Built: see `docs/ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md`. Still open inside it: whether HR and Procurement need real modules | 4.5 |
| O4 | Reception: remove from the live office, or build a real visitor registry (who, host, badge, time)? | 7.9 |
| O5 | Retention windows for meeting transcripts and for history snapshots | 9.11, 10.5 |
| O6 | Workspace-wide monthly cost cap for agent meetings: decided, USD 50 per workspace per month, owner configurable | 9.3 (S-02), closed |
| O7 | Who may read meeting transcripts: decided, the workspace owner only | 9.13 (S-03), closed |
| O8 | Approve storing time-indexed office snapshots so a true replay is possible, with their retention and cost | 10.5 |
| O9 | A PA persona in the scene: name, look, voice. None may be invented by the build | 6.8 |
| O10 | What the starter campus creates by default for a new owner | 4.2, 4.4 |
| O11 | Whether the PA daily briefing may also read email (it now reads approvals, tasks, calendar, live meetings, agents and the model budget) | 6.6 |
| O12 | Keep or remove the "Concept preview" toggle once the live look is approved | 13.4 |
| O13 | Authorise a load test and a rollback rehearsal on the Oracle VM | 12.9, 12.10 |
| O14 | Licence and source of glTF models (PR 245) | 2.2 |

## Totals

Rows counted: 152 (sections 1 to 14).

| Status | Rows |
|---|---|
| Complete | 114 |
| Partial | 34 |
| Missing | 3 |
| Not applicable | 1 |
| Total | 152 |

Section 14 rows are derived acceptance criteria and repeat findings from sections 2 to 13, so they double count. Without section 14: Complete 107, Partial 27, Missing 3, Not applicable 1 (total 138). Recounted from the tables on 26 October 2026 (rows 3.16 and 3.17 added by the graphics check work, row 3.18 by the cutaway section, row 3.20 by the billboards, row 4.11 by the label work, rows 4.12 and 4.13 by Green Concrete).

Not verified items are called out in the cells: PostgreSQL tests (CI only), browser end-to-end (CI only), visual look against the concept renders, real GPU or microphone behaviour, the unattended meeting worker running anywhere.
