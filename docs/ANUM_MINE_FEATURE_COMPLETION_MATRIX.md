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
| 2.2 | Premium building exteriors and a campus with landscaping, water, roads, vehicles | Partial | Read | `hqAssets/exterior/*` (executive HQ, office tower, admin block, retail, tyre workshop, warehouse), `hqAssets/environment/*` (roads, planting, water, vehicles, skyline); `lib/campusScene.ts` | Built from procedural boxes and blobs, not authored models. glTF models are in PR 245 (in flight). Look not compared with the renders: Not verified. PR: after 245 merges, a side by side review at five viewports using the concept renders |
| 2.3 | Room interiors for office, customer care, operations, executive, reception, meeting, lounge, warehouse, workshop | Complete | Read | `hqAssets/interior/index.ts` (`interiorKindFor`), `roomsFront.ts`, `roomsWork.ts`, `roomsIndustrial.ts`; glass partitions in `lib/buildingScene.ts` | Which interior a room gets for kind `office` is chosen by words in the room name (a regex), not by a stored department. See 7.3 |
| 2.4 | Reception, executive office, workstations, meeting rooms, operations command centre all present | Partial | Read | Interiors in 2.3 | There is no "command centre" room kind; an operations room is decorated by name only and has no data wall (2.6) |
| 2.5 | Day and night | Complete | Read, CI | `lib/officeSolar.ts`, night prop in `HqStage.tsx`, `hqLighting.ts`; `e2e/hq-campus.spec.ts` "night and day both draw" | none |
| 2.6 | Wall of live dashboards in the command centre | Missing | Read | The operations interior has static status lights only (`hqAssets/interior/roomsWork.ts`) | By data-truth this must show real numbers or nothing. PR: draw `GET /office/flow` totals and the commerce counts onto the operations wall screen, with "Not connected" when there is no source |
| 2.7 | Cinematic quality (lighting, depth, atmosphere) | Partial | Read | Physically based materials, shadow map, sky environment, fog, bloom at night (`lib/hqThreeRenderer.ts`) | Visual quality not judged by this audit: Not verified. PR 245 (in flight) adds models, glass relief and night halos |
| 2.8 | Characters match the concept's professional avatars | Partial | Read | See 5.3 | See 5.3 |

## Section 3. 3D engine

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 3.1 | Three.js engine | Complete | Read | `three` 0.184 in `apps/web/package.json`; `lib/hqThreeRenderer.ts`; loaded lazily by `HqStage.tsx` | Needs WebGL 2; without it the list and plan take over (3.14) |
| 3.2 | Orbit, pan, zoom, tilt | Complete | Read, CI | Pointer, wheel and pinch in `HqStage.tsx`; `lib/hqCamera.ts`; `e2e/hq-scene.spec.ts` | none |
| 3.3 | Focus and camera transitions | Complete | Read | Camera tween on control changes (`HqStage.tsx`), `alignYaw`, PA navigation moves the camera (`e2e/hq-campus.spec.ts` "executive PA dock ... moves the camera") | none |
| 3.4 | Top, isometric and interior view modes | Partial | Read | 3D, Top and List in `HqChrome.tsx`; `topView`, `threeDView` in `hqCamera.ts` | No interior (inside the room, low eye height) camera mode. PR: an "Inside" camera preset for a selected room reusing `buildingCamera` |
| 3.5 | Floor transitions and floor isolation | Partial | Read | Active floor in full, floors above cut away, optional stacked view (`buildBuildingScene` `activeIndex`, `showAll`); previous and next floor buttons | Whether the camera animates between floors was not verified. No isolate-one-floor-and-hide-the-rest toggle beyond the cutaway. PR: floor change tween plus an "only this floor" toggle |
| 3.6 | Clickable buildings, rooms, desks, agents | Complete | Read, CI | `pickNearest` in `lib/hqMath.ts`, label cards, room list; `e2e/hq-campus.spec.ts` "inside a building: floors, rooms, desks, agent cards" | none |
| 3.7 | Raycasting | Complete | Read | Ray against boxes of pick targets (`lib/hqMath.ts`) | Picks boxes, not triangles. Fine for rooms and desks |
| 3.8 | Full screen | Complete | Read | `toggleFullscreen` in `HeadquartersLive.tsx`, button in `HqChrome.tsx` | none |
| 3.9 | WebGL context-loss recovery | Complete | Read, CI | `webglcontextlost` and `webglcontextrestored` handlers in `hqThreeRenderer.ts`; `e2e/hq-scene.spec.ts`, `hq-campus.spec.ts` "lost GPU context" | none |
| 3.10 | Performance budgets | Partial | Read, CI | `docs/ANUM_MINE_HQ_PERFORMANCE_BUDGET.md`, `e2e/hq-performance.spec.ts`, adaptive quality (bloom, shadow size, shadows, pixel ratio) | Numbers come from software rendering in the sandbox, a regression guard only. Never measured on a real laptop GPU or a phone. PR: a documented device run (two real devices) added to the budget doc |
| 3.11 | Keyboard control | Complete | Read, CI | `keyToCameraAction` in `hqCamera.ts`; `e2e/hq-scene.spec.ts` "keyboard only", `hq-campus.spec.ts` "reachable from the keyboard" | none |
| 3.12 | Reduced motion | Complete | Read, CI | `setReducedMotion` (no bloom, no tween), `hqFlow.css`, `meetingRoom.css`; `hq-scene.spec.ts` "reduced motion" | none |
| 3.13 | Minimap, compass, zoom, reset | Complete | Read | `CampusView.tsx` minimap, `HqChrome.tsx` compass and zoom, `viewFootprint` in `hqCamera.ts` | none |
| 3.14 | 2D and list fallback | Complete | Read, CI | `HqPlanFallback.tsx`, `CampusListView.tsx`; `hq-scene.spec.ts` "no WebGL", `hq-campus.spec.ts` "without WebGL" | none |
| 3.15 | LOD, instancing, culling | Partial | Read | Frustum culling per mesh (`cull`), a "lite" interior detail level for big buildings (`buildingScene.ts`), adaptive pixel ratio | No GPU instancing (meshes are merged per surface class) and no distance LOD for figures. PR: measure first with the performance spec at 200 desks, then add instancing only if it fails the budget |

## Section 4. Building and campus structure

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 4.1 | Campus of buildings, one per authorised workspace | Complete | Read, CI | `GET /api/v1/office/campus` (`office_campus_api.py`), `CampusView.tsx`; `e2e/hq-campus.spec.ts` "one building per workspace with real counts" | none |
| 4.2 | Personal office building | Partial | Read | Building kind `personal` draws a villa (`lib/campusBuildings.ts`) | Exists only when the owner has a workspace and sets its kind; nothing creates it by default. Starter campus (in flight) may add it |
| 4.3 | Career building | Missing | Read | Building kinds are only `executive`, `business`, `department`, `personal` (`office_campus_api.py`) | Owner decision O2: is career a personal workspace, or its own kind? |
| 4.4 | ANUM OS HQ, Tyre Pulse, ANUM360 Commerce buildings | Partial | Read, CI | Buildings come from workspaces; building view shows Tyre and Commerce panels (`lib/hqModules.ts`, `e2e/hq-campus.spec.ts` "module panels") | A workspace must exist for each. The starter campus (in flight) is the missing default |
| 4.5 | Departments: Finance, Sales and Marketing, Operations, Customer Experience, Technology, Research, Legal, Security and Governance, Executive Office | Partial | Read | Team departments `sales, support, operations, finance, tyre_engineering, custom` (`OFFICE_TEAM_DEPARTMENTS`, migration 0105); governed provisioning templates (PR 246: orders desk and six more roles) | No Marketing, Technology, Research, Legal or Security and Governance department or template. Owner decision O3: which of these need rooms, and which real module each opens. Do not add rooms with nothing behind them |
| 4.6 | Configurable buildings, floors, rooms, desks | Complete | Read, CI | `office_space_api.py` create, patch, delete for all four kinds, `PUT /floors/{id}/layout`; builder `hqBuilder/*` | none |
| 4.7 | Tenant and workspace isolation | Complete | CI | Forced RLS on all office tables (`docs/ANUM_MINE_HQ_SECURITY_REVIEW.md` S-01, S-08); `tests/test_postgres_hq_rls.py` | PostgreSQL tests not run here: Not verified locally (no test database credentials). `H-10` notes this test was first run by CI |
| 4.8 | Cross-workspace executive view only by explicit grant | Complete | Ran, CI | `executive_api.py` grants (owner approves with payload hash, narrow only, revocable); `executive_pa.py`; `ExecutiveCommandCenter.tsx`; `test_executive_pa.py` pass | Postgres side of grants is CI only |

## Section 5. Digital characters and AI employees

Current characters are stylised, generic and static. They appear only at desks that have a real assignment.

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 5.1 | AI employees are real records linked to desks | Complete | Read, CI | `PATCH /office-spaces/desks/{id}/assignment` (visible agents only, audited), `agent_profiles` joined to desks in `office_presence.py` | none |
| 5.2 | Missing roles can be added through governance | Complete | Ran | `office_provisioning_api.py` (gaps, templates, proposals, confirm); `test_office_provisioning.py` pass; `e2e/hq-agent-provisioning.spec.ts` | none |
| 5.3 | Character look | Partial | Read | `person()` in `lib/buildingScene.ts` and `hqAssets/interior/figures.ts` (seated poses typing, reading, phone, talking, leaning; standing poses incl. walking). Generic, seeded by the agent id, no likeness | Box figures, about 380 triangles. Static: nothing moves. PR 245 (in flight) may improve models. Owner decision O1 on any decorative motion |
| 5.4 | Truthful states | Partial | Ran | Server states `working, queued, meeting, waiting_approval, blocked, idle, paused, unknown` with provenance and `last_verified_at` (`office_presence.py`, `test_office_presence.py`) | "Walking to meeting" has no stored record, so showing it would be invented. Owner decision O1. A queued task reads as Queued, never Working |
| 5.5 | State shown on the character or desk in the scene | Partial | Read | Desk visual states `working, waiting, idle, unknown, vacant, assigned` (`STATION_OF` in `buildingScene.ts`) | `meeting`, `blocked` and `paused` fold into other desk states or "unknown"; the figure does not change pose with state. PR: map all eight server states to a desk and figure visual |
| 5.6 | Agent card: name, role, status, last verified | Complete | Read, CI | `AgentPanel` and `StatusPill` in `HqPanels.tsx` (stale flag shown) | none |
| 5.7 | Private agents stay private | Complete | Read, CI | `agent_not_visible`, `desk:<id>` keys; `e2e/hq-campus.spec.ts` "a private agent shows no name and no work" | none |
| 5.8 | Characters only for real assignments, never invented people | Complete | Read | `occupantSeed` only where a desk has an agent (`buildingScene.ts`, `BuildingView.tsx`) | The concept preview (`OfficeTourView.tsx`) still draws illustrative staff; it is labelled and separate (13.7) |
| 5.9 | Meeting participants appear in the meeting room | Missing | Read | No room link exists on meeting sessions (docs: "No persisted room to meeting link exists yet") | See 7.4 |

## Section 6. Personal Assistant (PA)

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 6.1 | PA acts through a governed tool interface | Complete | Ran | `executive_pa.py` (`GET /tools`, `POST /invoke`, `GET /tour`); `test_executive_pa.py` pass; `docs/ANUM_MINE_EXECUTIVE_PA.md` | none |
| 6.2 | PA never bypasses approvals | Complete | Ran | No approve or reject tool exists (unknown tool refused 422 and audited); writes need a payload hash and `input_mode: click`; voice and typed can never confirm; `test_executive_pa.py` | none |
| 6.3 | Typed commands | Complete | Ran, CI | `parseUtterance` English and Arabic (`lib/executivePa.ts`, `test/executivePa.test.ts` pass); `ExecutivePanel.tsx` | Parser only proposes; unknown sentences are refused, not guessed |
| 6.4 | Voice commands | Partial | Read | Browser speech recognition via `createPushToTalk` (`lib/voice.ts`), language `ar-AE` or `en-US` | Not verified with a real microphone. Browser recognition availability varies; a voice fix is in flight |
| 6.5 | Guided tour built from real buildings and rooms | Partial | Read | Two tours exist. (a) `GET /executive-pa/tour` stops from the person's accessible buildings, floors and rooms, used by the dock (`ExecutivePanel.tsx`). (b) `GuidedExecutiveTour.tsx` in the Tools menu | (b) uses the open floor's rooms only, with each stop's description set to the room kind word, no staff, a disclaimer "Illustrative PA and employees", narration fixed to `en-US`, strings hard-coded English, visit history kept only in memory. PR: remove (b) or drive it from (a), localise narration, and say what the room really contains from the registry and presence |
| 6.6 | PA daily briefing | Missing | Read | No briefing tool, endpoint or screen (searched `executive_pa.py`, web `lib/executivePa.ts`) | Closest: `summarise_workspace`, `list_blocked_work`, `list_pending_approvals` as separate quick actions. PR: a read-only `daily_briefing` tool that composes those results plus today's meetings, each line naming its source and time, and "Not connected" where a source is missing. No invented schedule |
| 6.7 | PA moves the scene | Complete | Read, CI | `hqCommands.navigateTo`, `anum:hq-navigate` event (`lib/hqExtensions.ts`); `e2e/hq-campus.spec.ts` "the assistant can move the view" | none |
| 6.8 | PA as a visible character in the office | Missing | Read | The PA is a dock panel with a glyph (`ExecutivePanel.tsx`); no 3D guide | Owner decision O9: does the owner want a PA persona in the scene; its name and look must not be invented |
| 6.9 | Writes (start task, delegate, schedule meeting) with visual confirmation | Complete | Ran | Tools in `executive_pa.py`; hash bound to tenant, workspace, user, tool, arguments; `test_executive_pa.py` | `schedule_meeting` creates a live session; the meeting API has no start time, so nothing is scheduled for a time |
| 6.10 | Every PA call audited | Complete | Ran | Audit record `executive_pa.invoke`, refusals included | none |
| 6.11 | Arabic voice and narration | Partial | Read | Arabic words in the parser; recognition language follows the page | Tour narration is English only (6.5) |

## Section 7. Every room opens a useful real module

Today a room panel shows the room, its desks and presence, then one link chosen by room kind (`MODULE_ROUTES` in `HqPanels.tsx`): meeting to Meetings, reception to Inbox, office to Tasks, workshop to Tyre, warehouse to Commerce, lounge to Calendar. A finance, sales, executive or command room is kind `office`, so it opens Tasks.

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 7.1 | Each room kind opens a module | Complete | Read | `lib/hqRoomModule.ts` (department to module table), `HqRoomModule.tsx` in the inspector and the room list; `test/hqRoomModule.test.ts`, `e2e/hq-room-modules.spec.ts` | Compliance and Executive are not storable departments yet (contract has six); they resolve through the executive building kind and a forward-compatible table entry. Owner decision O3 |
| 7.2 | Operations command centre | Partial | Read | Operations department opens the live flow dock (`HqFlowDock`) from the room inspector and list; `e2e/hq-room-modules.spec.ts | The dock is not yet filtered to the room that opened it |
| 7.3 | Finance room opens Finance | Complete | Read | `lib/hqRoomModule.ts` (department to module table), `HqRoomModule.tsx` in the inspector and the room list; `test/hqRoomModule.test.ts`, `e2e/hq-room-modules.spec.ts` | none |
| 7.4 | Meeting room opens the live meeting | Partial | Read, CI | Meeting kind opens `#meetings`, which lists live meetings with "Open room" (`MeetingRoomLauncher.tsx`); full room `MeetingRoomPanel` | No stored link between an office room and a meeting. `liveMeetingRoomIds` is never passed from `main.tsx`, so no room lights up. PR (expand-only migration): nullable `office_room_id` on meeting sessions, pass `liveMeetingRoomIds`, open `MeetingRoomPanel` from the room through `renderRoomPanel` |
| 7.5 | Tyre engineering opens Tyre | Complete | Read, CI | Workshop kind to `#tyre`; Tyre panel in the building (`hqModules.ts`, `e2e/hq-campus.spec.ts`) | none |
| 7.6 | Sales room opens Sales | Complete | Read | Sales department opens the Sales and CRM view (`#whatsapp`); `lib/hqRoomModule.ts` (department to module table), `HqRoomModule.tsx` in the inspector and the room list; `test/hqRoomModule.test.ts`, `e2e/hq-room-modules.spec.ts` | none |
| 7.7 | Executive office opens the executive desk | Partial | Read | Rooms of an executive-kind building, or a stored `executive` department, open `#executive`; `lib/hqRoomModule.ts` (department to module table), `HqRoomModule.tsx` in the inspector and the room list; `test/hqRoomModule.test.ts`, `e2e/hq-room-modules.spec.ts` | There is no stored `executive` department or room kind in the contract; relies on the building kind |
| 7.8 | Workstation opens the desk's real work | Complete | Read, CI | `HqWorkstationWork.tsx`, `GET /office/workstations/{desk}/work` (tasks, run phase, checkpoint, approvals, errors, documents); `e2e/office-workstations.spec.ts` | none |
| 7.9 | Reception opens a real visitor workflow | Partial | Read | The Reception panel and its tool entry are removed from the live office; a reception room shows its desks and 'no visitor register'; unit and e2e guards fail if invented names return. The simulation stays only under 'Concept preview' | Visitor registry not built: owner decision O4 is open |
| 7.10 | Module panels never show numbers without a source | Complete | Read, CI | `ModuleState` loading, not_connected, forbidden, error, ready (`lib/hqModules.ts`) | none |

## Section 8. Real-time backend

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 8.1 | Snapshot, then authorised events, for presence | Partial | Ran, Read | Server: `GET /office/presence` and `GET /office/presence/stream` (snapshot then deltas, `Last-Event-ID`, keep-alive), `test_office_presence.py` pass | The web never opens `/presence/stream` (`PRESENCE_STREAM_PATH` is defined and unused). It re-reads on general event-stream signals and every 10 s (`HeadquartersLive.tsx`). PR: connect `hqPresence` to the delta stream with the poll as fallback |
| 8.2 | Disconnect, duplicate and stale handling | Partial | Read, CI | Server de-duplicates by event id and sends keep-alives (`office_presence_api.py`); stale flag per agent; meeting stream replays exactly the missed events (`live_agent_meeting_room_api.py`); web coalesces events in 300 ms | Open finding H-09: 0.3 to 0.8 percent lost deliveries at 200 clients in memory mode, cause not isolated, no run on the Oracle VM with PostgreSQL |
| 8.3 | Persisted layouts with optimistic concurrency | Complete | Ran, CI | `expected_revision` on every patch and delete, 409 on conflict; `PUT /floors/{id}/layout` is atomic; `test_office_layout.py` pass; PG tests CI | none |
| 8.4 | Layout audit | Complete | Read | `_audit` with before and after on every write (`office_space_api.py`) | none |
| 8.5 | Work moving through offices and stuck work | Complete | Ran | `office_flow.py`, `GET /office/flow`, item trails, `test_office_flow.py` pass; `e2e/hq-live-flow.spec.ts` | Orders have no local history: trail says `not_recorded` |
| 8.6 | Layout revision history and restore | Missing | Read | Revisions are a counter per row; no route lists past versions or restores one | PR: read-only "layout history" of a floor built from the `office.*` audit records, and "restore this version" as a normal atomic save |
| 8.7 | Events are authorised | Complete | Read | `task:read` and `event:read` required on the stream; events carry ids, not payload text | none |

## Section 9. Live agent meetings

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 9.1 | Lifecycle: create, add agents, activate, pause, resume, end or cancel | Complete | Ran, CI | `live_agent_meeting_api.py`; `test_live_agent_meeting_*`; migrations 0098, 0104; `e2e/meeting-room.spec.ts` | none |
| 9.2 | Governed model runtime with limits | Complete | Read, CI | Durable turn leases, per-meeting max turns, time, tokens, cost, owner pause, emergency stop, a mock model cannot run a turn; secrets redacted before storage (`live_agent_meeting_runtime.py`, `live_agent_meeting_redaction.py`) | none |
| 9.3 | Workspace-wide meeting budget | Missing | Read | Security finding S-02 (open): limits are per meeting only | Owner decision O6 on the cap. PR: a workspace daily cost cap checked before each turn |
| 9.4 | Unattended running and crash resume | Partial | Read | `unattended_meeting_worker.py`, lease expiry and retry, terminal-state guards (`test_unattended_meeting_terminal.py`) | The worker is a managed service; nothing here proves one runs anywhere. Not verified. Do not run a long meeting through an HTTP request |
| 9.5 | Live room: transcript, speakers, hands, owner interrupt | Complete | Read, CI | `MeetingRoomPanel.tsx`; `e2e/meeting-room.spec.ts` (join, turn streams, interrupt, reconnect replay, end) | none |
| 9.6 | Meeting summary | Missing | Read | Live meetings have no stored summary. `meeting_analysis.py` serves imported meetings only | PR: owner-triggered governed summary stored as a draft output of kind `summary` that the owner confirms, like decisions |
| 9.7 | Decisions and action items | Partial | Read, CI | Owner or participants draft them, owner confirms (`output_create`, `output_decide`); each links to source messages (`source_sequences`) | Model-drafted extraction is not built (stated in `docs/ANUM_MINE_LIVE_MEETING_ROOM_UI.md`); a human must type each one |
| 9.8 | Action item becomes a task | Complete | Read, CI | `POST /{id}/outputs/{output_id}/task` (needs `task:create`, status `created`, `task_id` stored on the output); e2e "create a task" | Running the task still follows approval rules |
| 9.9 | Decision linked to work | Missing | Read | Only `action_item` outputs can become tasks (`output_for_task` refuses other kinds). A decision links to transcript messages, not to a task, approval or run | PR: let a decision link to existing task ids and show the links in the room and in the export |
| 9.10 | Export | Partial | Read | `GET /{id}/export?format=markdown or json`, owner only, already redacted; the web "Export" button downloads Markdown (`MeetingRoomPanel.tsx`) | The web has no JSON export; no PDF; export omits decision to work links (9.9). Small PR: JSON option in the card |
| 9.11 | Delete and retention | Partial | Read | Owner can delete an ended meeting's transcript (`DELETE /transcript`); counts, confirmed outputs and tasks remain | No automatic retention window. Owner decision O5 |
| 9.12 | Turn execution audited | Partial | Read | Security finding S-04: `create`, `join_self`, `state_change`, `agent_add`, `output_task` are audited (grep of `live_agent_meeting_api.py` and the room API); turn execution is not | PR: audit record per executed turn (ids and token counts only) |
| 9.13 | Transcript visibility | Partial | Read | S-03: listing meetings and reading turns needs `task:read` | Owner decision O7 on who may read transcripts |
| 9.14 | Meetings visible in the office scene | Missing | Read | See 7.4 | See 7.4 |
| 9.15 | Stored meeting replay | Complete | Read, CI | Finished meeting opens as a stored replay with playback (`e2e/meeting-room.spec.ts` "finished meeting opens as a stored replay") | Replays stored events only |

## Section 10. Work history and replay

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 10.1 | Evidence history API | Complete | Ran | `GET /api/v1/office-history`, `/at`, `/correlations/{id}` (`office_history_api.py`); `test_office_history_api.py` pass; `docs/ANUM_MINE_F09_AUDITED_HISTORY_API.md` | none |
| 10.2 | Searchable timeline in the product | Complete | Ran, CI | `HqHistoryPanel.tsx` (Office tools, History; room inspector, agent card and Team card entry points), event type, actor, room, agent and date filters, infinite scroll and "Load older records" while `next_cursor` is set; `test/hqHistory.test.ts`, `e2e/hq-history.spec.ts`; `docs/ANUM_MINE_HQ_HISTORY_PANEL.md` | none. Department and project filters are not offered: audit records carry neither |
| 10.3 | Free-text search | Complete | Ran | `GET /office-history` takes `q` (actor, action, target), `since`, `until`; `test_office_history_http.py`, `test_office_history_api.py` | Search does not look inside recorded metadata |
| 10.4 | Correlation ids | Partial | Ran, CI | The record side panel shows the correlation id and lists the records of the same request (`GET /correlations/{id}`) and the task, run, approval or meeting it points at, with a summary read from the existing routes; `e2e/hq-history.spec.ts` | No link in the other direction: a task, meeting or approval screen does not open its correlation view |
| 10.5 | Replay of the office at a past time | Partial | Ran, CI | Room history slider over the room's stored audit records: before and after values per step, "Cannot be reconstructed for this time" for gaps, times before the oldest loaded record and the future; `lib/hqRoomHistory.ts`, `test/hqHistory.test.ts`, `e2e/hq-history.spec.ts` | Layout changes of one room only. Past presence, occupancy and whole-office replay need time-indexed snapshots (new tables, retention, owner decision O8); nothing is stored by this change and none is drawn |
| 10.6 | Replay only where verified | Complete | Read | `EVIDENCE_NOTICE`, no invented status, `unknown` fields | none |
| 10.7 | Item trail (task, lead, order) | Complete | Ran | `GET /office/flow/items/{kind}/{id}/trail`, allow-listed fields; `test_office_flow.py` | Orders: `not_recorded` |
| 10.8 | Access limited | Complete | Ran | Owners and authorised organisation admins only (`HISTORY_ACCESS`) | none |
| 10.9 | History export | Partial | Ran, CI | "Export these records (CSV)" calls the existing audited audit export (`GET /audit/export`, permission `audit:export`) with the same filters; `e2e/hq-history.spec.ts` | Not offered for room or agent views or the "Other" type, because the audit export cannot express them. No JSON button in the panel |

## Section 11. Office management

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 11.1 | Create buildings and floors | Complete | Read, CI | `OfficeSpaceLiveManager.tsx` forms over `POST /office-spaces/buildings` and `/floors` | none |
| 11.2 | Create, edit, move, rotate, resize rooms; doors; equipment | Complete | Ran, CI | `hqBuilder/*` (`PlanCanvas`, `RoomDetails`, `EquipmentEditor`), `test/hqBuilder*.test.ts` pass; `e2e/hq-builder.spec.ts`, `hq-builder-responsive.spec.ts` | none |
| 11.3 | Arrange desks and assign agents | Complete | Read, CI | `DeskPanel.tsx`, `PATCH /desks/{id}/assignment` | none |
| 11.4 | Save and reload | Complete | Read, CI | Atomic floor save, draft model, rebase on conflict (`hqBuilder/draftModel.ts`) | none |
| 11.5 | Undo and redo | Complete | Ran | `hqBuilder/draftModel.ts` history; `hqBuilderDraft.test.ts` pass | Draft level only, before saving |
| 11.6 | Revisions (browse, restore) | Missing | Read | See 8.6 | See 8.6 |
| 11.7 | Audit | Complete | Read | See 8.4 | none |
| 11.8 | Templates | Partial | Read, CI | Fixed room templates and room copy (`GET /office-spaces/templates`, `/rooms/from-template`, `/rooms/{id}/copy`, `office_layout.py`, `test_office_layout.py` pass) | No floor, building or whole-office template. Starter campus is in flight |
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
| 12.7 | Security tests and review | Partial | Read | `docs/ANUM_MINE_HQ_SECURITY_REVIEW.md`, `e2e/hq-csp.spec.ts`, RLS test | Open findings S-02 (no workspace cap), S-03 (transcript read scope), S-04 (turns not audited), S-05 (turn rate). See 9.3, 9.12, 9.13 |
| 12.8 | Accessibility | Complete | Read, CI | `e2e/hq-a11y.spec.ts`: English and Arabic, light and dark | Open H-13 (close button covered by header in the overlay) was found; status not re-checked |
| 12.9 | Load and streaming | Partial | Read | `scripts/hq-load/hq_load.py`, `docs/ANUM_MINE_HQ_LOAD_TESTING.md` | Never run on the Oracle VM with PostgreSQL; H-09 open |
| 12.10 | Rollback | Complete | Read | `VITE_ANUM_HQ=off` (`lib/headquartersFlag.ts`), expand-only migrations, `docs/ANUM_MINE_HQ_DEPLOYMENT_AND_ROLLBACK.md` | No rehearsal recorded |
| 12.11 | Flutter parity | Not applicable | Read | Owner excluded the Flutter companion (F12) from this round | none |

## Section 13. Delivery rules

| ID | Requirement | Status | How checked | Evidence | Gap and recommended small PR |
|---|---|---|---|---|---|
| 13.1 | One slice per pull request, draft first, never self-merged | Complete | Read | PR history 216 to 246 | none |
| 13.2 | Docs updated and indexed in the same change | Complete | Ran | README lists every HQ doc; `pnpm docs:check` | none |
| 13.3 | Migrations expand only, ids at most 32 characters, one chain | Partial | Read | 0097 to 0106 are additive (`0105_office_builder.py` header states it) | Two files are numbered 0099 (`0099_office_create_keys.py`, `0099_office_room_geometry.py`); ids chain correctly (finding H-12). PR: rename one file to remove the duplicate number |
| 13.4 | Real data only, no invented people or activity in the live view | Partial | Read | Presence, work, flow, meetings and modules are record-backed with honest empty states; the invented Reception panel is gone from the live view (PR claude/hq-room-modules) | Remaining: tour (b) room descriptions are kind words with an 'Illustrative' disclaimer (6.5). The concept preview toggle stays visibly separate; owner decision O12 |
| 13.5 | English and Arabic | Partial | Read | `lib/messages/hq.en.ts` (210 lines) and `hq.ar.ts` (209), `hqFlow.*`, `builder.*`; `hq-campus.spec.ts` "Arabic ... right to left"; `hq-a11y.spec.ts` | Key parity of `hq.en` and `hq.ar` not checked key by key. `GuidedExecutiveTour.tsx` and `ReceptionOperations.tsx` are English only. PR: a unit test that fails when a key exists in one language only, and translate those two panels |
| 13.6 | Every state handled (loading, empty with next action, error, denied, large) | Complete | Read, CI | `e2e/hq-campus.spec.ts` "refused campus read explains who can fix it", "no saved layout says what to do next", list search; paged history API | none |
| 13.7 | No em or en dashes, no secrets, no model names | Complete | Ran | This file checked with a search; gitleaks and CodeQL green on main | Repo-wide dash check not run |

## Section 14. Acceptance criteria (derived)

| ID | Criterion | Status | How checked | Evidence and reason |
|---|---|---|---|---|
| 14.1 | The office opens as a premium 3D office of the person's real buildings | Partial | Read, CI | Real buildings and honest empty states are built (4.1); look is Not verified against the renders; glTF models in flight (2.2) |
| 14.2 | Campus, building, floor, room, desk and agent can be reached by mouse, touch and keyboard | Complete | Read, CI | 3.2, 3.6, 3.11 |
| 14.3 | Every room opens a useful real module | Partial | Read | Only by room kind; finance, sales, executive, command rooms open Tasks; reception is fake (7.1 to 7.9) |
| 14.4 | Everything shown about agents is real and truthful | Partial | Read | Presence and desks are real (5.1 to 5.8); Reception panel and tour (b) break it (7.9, 6.5); "walking" cannot be truthful (5.4) |
| 14.5 | The PA tours, takes typed and voice commands, briefs, and never bypasses approvals | Partial | Ran, Read | Tools, tour, commands, approvals gate are done (6.1 to 6.3, 6.7, 6.9); briefing missing (6.6); voice not verified (6.4) |
| 14.6 | The office updates live and recovers from disconnects | Partial | Ran, Read | Server stream done; web does not use the presence stream (8.1); H-09 open |
| 14.7 | Layouts persist with concurrency control and audit | Complete | Ran, CI | 8.3, 8.4 |
| 14.8 | Live agent meetings run within limits and end with summary, decisions, tasks and export | Partial | Read, CI | Limits, decisions, action item to task, export done (9.1, 9.2, 9.8, 9.10); no summary (9.6); decision to work link missing (9.9) |
| 14.9 | Work history is searchable and replay is shown only where verified | Partial | Ran, CI | History panel (10.2 to 10.5, 10.9); the "only where verified" rule is kept (10.6) | No real replay of presence or occupancy (owner decision O8) |
| 14.10 | The owner can create, edit, arrange, assign, save, restore and template an office | Partial | Read, CI | All but restore and whole-office templates (8.6, 11.8) |
| 14.11 | Tenants and workspaces stay isolated | Complete | CI | 4.7, 4.8 (PG tests CI only) |
| 14.12 | Fallbacks, reduced motion and performance budgets hold | Complete | Read, CI | 3.9, 3.12, 3.14; budget run on a real device is missing (3.10) |
| 14.13 | Tests and screenshots cover every state | Partial | Read, CI | Tests broad (12.1, 12.2); screenshots not kept (12.3) |
| 14.14 | Docs and delivery rules followed | Complete | Ran | 13.1, 13.2 |

## Ranked next actions (top 10)

1. **Done in PR claude/hq-room-modules: removed the invented Reception panel from the live office (7.9, 13.4).** It shows made-up host and room names with no saved data. Smallest honest fix: hide it in the live view. Then decide O4.
2. **Done in PR claude/hq-room-modules: rooms open the right module (7.1 to 7.3, 7.6, 7.7).** Use the room's stored department so Finance opens Finance, Sales opens Sales, Executive opens the executive desk, Operations opens the flow dock.
3. **Link meetings to rooms (7.4, 5.9, 9.14).** One nullable room id on meeting sessions, then the room lights up "In meeting" and opens the live meeting room.
4. **Build the History panel (10.2, 10.4, 14.9).** The API is done and tested; no one can use it yet. Add filters, paging and correlation view.
5. **PA daily briefing (6.6).** A read-only tool that composes blocked work, approvals, meetings and workspace summary, naming each source.
6. **Fix the second guided tour (6.5, 6.11).** Drive it from the real tour endpoint, translate the narration, remove the "illustrative" wording once it is real.
7. **Use the presence stream in the web (8.1, 8.2).** Cut polling and close the gap with the server design; then re-run the load test on the Oracle VM (H-09).
8. **Meeting summary and decision to work links (9.6, 9.9, 9.7).** Owner-confirmed summary, decisions linked to tasks, linked items in the export.
9. **Layout history and restore (8.6, 11.6).** From the existing audit records, restore as a normal save.
10. **Close the open security findings (9.3, 9.12, 9.13, 12.7).** Workspace meeting cap, audit each turn, decide transcript visibility.

Not in the top ten but next: the realism review after PR 245 (2.2, 2.7), a screenshot sweep artifact (12.3), the migration number clash (13.3), the `hq.en` and `hq.ar` key parity test (13.5), desk and figure visuals for all eight states (5.5).

## Owner decisions still open

| Id | Decision | Why it matters |
|---|---|---|
| O1 | May characters move (walk to a meeting) as a labelled decoration between recorded states, or must every pixel of movement be backed by a record? | "Walking to meeting" has no stored fact (5.4) |
| O2 | Is "career" a personal workspace, or its own building kind? | 4.3 |
| O3 | Which departments get rooms and templates: Marketing, Technology, Research, Legal, Security and Governance, and what real module does each open? | 4.5. A room with no module would be a dead button |
| O4 | Reception: remove from the live office, or build a real visitor registry (who, host, badge, time)? | 7.9 |
| O5 | Retention windows for meeting transcripts and for history snapshots | 9.11, 10.5 |
| O6 | Workspace-wide daily cost cap for agent meetings | 9.3 (S-02) |
| O7 | Who may read meeting transcripts besides the owner (any member with `task:read` today) | 9.13 (S-03) |
| O8 | Approve storing time-indexed office snapshots so a true replay is possible, with their retention and cost | 10.5 |
| O9 | A PA persona in the scene: name, look, voice. None may be invented by the build | 6.8 |
| O10 | What the starter campus creates by default for a new owner | 4.2, 4.4 |
| O11 | Which sources the PA daily briefing may read (tasks, approvals, meetings, calendar, email) | 6.6 |
| O12 | Keep or remove the "Concept preview" toggle once the live look is approved | 13.4 |
| O13 | Authorise a load test and a rollback rehearsal on the Oracle VM | 12.9, 12.10 |
| O14 | Licence and source of glTF models (PR 245) | 2.2 |

## Totals

Rows counted: 139 (sections 1 to 14).

| Status | Rows |
|---|---|
| Complete | 74 |
| Partial | 47 |
| Missing | 17 |
| Not applicable | 1 |
| Total | 139 |

Section 14 rows are derived acceptance criteria and repeat findings from sections 2 to 13, so they double count. Without section 14: Complete 69, Partial 38, Missing 17, Not applicable 1 (total 125).

Not verified items are called out in the cells: PostgreSQL tests (CI only), browser end-to-end (CI only), visual look against the concept renders, real GPU or microphone behaviour, the unattended meeting worker running anywhere.
