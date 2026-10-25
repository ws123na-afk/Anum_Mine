# ANUM MINE headquarters: market research and office builder plan

**Date:** 25 October 2026 | **Status:** research only. No feature is built by this document. Branch `claude/hq-market-research`, based on `origin/main` at `d2e629b`.

Scope: (1) what the headquarters (HQ) module already has, (2) what the market asks for, (3) what to add, ranked, (4) what is missing for an owner to build their own office inside the module, with a PR-sized build plan.

Note: `docs/ANUM_MINE_HQ_EXTENSION_POINTS.md` named in the request does not exist on `main` at this commit. Extension points below come from the code and the other HQ docs.

## 1. Current state (verified in code on main)

### 1.1 Data model and API
Registry API, prefix `/api/v1/office-spaces` (`services/api/anum_api/office_space_api.py`, store in `office_space_store.py`). Tables from migration `0097_office_spaces`: `office_buildings`, `office_floors`, `office_rooms`, `office_desks`, all with forced row level security on tenant and workspace. Idempotency claims from `0099_office_create_keys`. Room geometry from revision `0100_office_room_geometry` (file `0099_office_room_geometry.py`).

| Capability | Endpoint | Permission | Notes |
|---|---|---|---|
| List / read buildings, floors, rooms, desks | `GET /{kind}`, `GET /{kind}/{id}` | `task:read` | cursor paged, 1 to 100 |
| Create building / floor / room / desk | `POST /buildings`, `/floors`, `/rooms`, `/desks` | `workspace:manage` | optional `Idempotency-Key`; client supplies the id |
| Rename room | `PATCH /rooms/{id}` | `workspace:manage` | `expected_revision`, unique name per floor |
| Set room position and size | `PATCH /rooms/{id}/geometry` | `workspace:manage` | x, z (+/-10000), width, depth (1 to 1000), overlap check per floor under a floor lock |
| Assign or release an agent on a desk | `PATCH /desks/{id}/assignment` | `workspace:manage` | agent must be visible to the caller; one desk per agent |
| Delete room (only when empty) / desk | `DELETE /rooms/{id}`, `/desks/{id}` | `workspace:manage` | `expected_revision` query |

Room kinds are fixed: `office, meeting, reception, workshop, warehouse, lounge`. Desks are allowed only in `office, warehouse, workshop` rooms and limited by room capacity. Every write appends an audit record in the same transaction. Memory mode answers 503 (no fake success). Strict input models reject client scope fields.

Read-side services that already exist: presence projection `GET /api/v1/office/presence` and `/presence/stream` and per-desk work `GET /api/v1/office/workstations/{desk_id}/work` (`office_presence_api.py`); audited history and replay `GET /api/v1/office-history`, `/at`, `/correlations/{id}` (`office_history_api.py`); live agent meetings and the meeting room API (`live_agent_meeting_*.py`, `/{meeting_id}/room`, events, SSE stream, presence, hand raise).

### 1.2 Web
- `HeadquartersEntrypoint.tsx` (live default, labelled concept preview toggle), `HeadquartersLive.tsx`, `HeadquartersCanvas.tsx` (software 3D canvas, orbit, pan, pick room), `HeadquartersWebGL.tsx`, `OfficeWebglScene.tsx`, `OfficeWorkstations.tsx`, `OfficeTourView.tsx`.
- Pure libraries: `lib/headquartersProjection.ts` (registry to scene; generated layout when a room has no saved geometry), `headquartersSpatial.ts` (camera, ray picking, `overlapArea`, `LayoutConflict` codes `ROOM_OVERLAP` and `OUT_OF_BOUNDS`, `polygonForRoom` with a derived door), `headquartersController.ts`, `headquartersPolicy.ts`, `officeSpaceClient.ts`.
- `OfficeSpaceLiveManager.tsx` (142 lines) is the only editor: forms to create building, floor, room (name, kind, capacity), rename, a four-number geometry form (X, Z, width, depth), add and delete desks, assign agent, delete empty room. Re-reads canonical server state after each write; handles 409 with a reload message.
- Tests: web unit tests `officeSpaceClient`, `headquartersSpatial`, `headquartersProjection`, `officeSpaceRegistry` and others; API tests `test_office_space_api.py`, `test_office_room_geometry.py`, `test_postgres_office_spaces.py`. Only one office e2e spec exists (`apps/web/e2e/office-workstations.spec.ts`); there is no e2e for building an office.

### 1.3 What the module does well (keep)
Tenant isolation with RLS, optimistic revisions, audit in the same transaction, idempotent create, truthful presence with provenance, replayable history, real live meetings, and a data-truth rule that no label appears without a record. These are the moat. The market tools below mostly lack them.

## 2. Market demand (cited)

Source quality is mixed; many pages are vendor or competitor blogs. Figures are directional. Items I could not confirm are marked UNCERTAIN.

### 2.1 Virtual offices and spatial collaboration
- **Gather** removed its permanent free tier in September 2025 and now sells one Essential plan at USD 15 per user per month, USD 12 billed annually (as reported by competitor SoWork, July 2026, UNCERTAIN because the source sells a rival): https://www.sowork.com/blog/sowork-vs-gather-pricing-2026-virtual-office-comparison
- **Gather Mapmaker** is the reference for user built offices: add or remove objects, tile effects (impassable, spawn, portal, private area, spotlight), walls, floors and extra rooms; role gated to admins and builders; office templates sized by headcount (12 to 18, 18 to 25, 6 to 12 people); and Gather itself warns that two people saving at once overwrite each other. Source (Gather help centre, undated): https://support.gather.town/articles/9657827678-mapmaker-overview . Lesson: templates by team size, private areas and a safe multi-editor story are what buyers meet first; ANUM already has revisions, which fixes the overwrite problem.
- **Teamflow** is positioned on embedding work apps (Salesforce, Figma, Google Docs) in the virtual floor, aimed at sales and design teams; price reports conflict (USD 12 or 15 per member per month) so UNCERTAIN: https://www.sowork.com/blog/teamflow-vs-sowork-vs-gather-virtual-workspace-comparison-2026
- **Microsoft Mesh** was retired as a standalone platform on 1 December 2025; the 3D "immersive spaces" view in Teams was removed and replaced by avatar based 3D events (reported capacity 16 in standard sessions). Sources: https://www.theregister.com/2025/12/02/microsoft_mesh_axed/ (2 Dec 2025), https://www.computerworld.com/article/4100319/microsoft-retires-mesh-app-launches-immersive-spaces-for-teams.html . Lesson: a big vendor walked away from a generic avatar metaverse. A 3D room is not the product; reliable work inside it is.
- **Slack** huddles can get AI generated transcript, notes with citations and action items in a canvas, and Agentforce agents live inside Slack (announced September 2024; no 2026 coverage found): https://slack.com/intl/en-gb/blog/news/ai-innovations-in-slack . Lesson: meeting output (notes, action items) is the valued part, not the avatars.

### 2.2 Agent workforce command centres
- 2026 coverage describes an "agent control plane" as a buying category: governance and observability weigh more than the builder, and a dashboard that only shows history differs from a control plane that can intervene mid run. IBM added Agentic Control Plane dashboards to watsonx Orchestrate (August 2026). Sources: https://www.agno.com/articles/agent-control-plane-vs-agent-dashboard , https://community.ibm.com/community/user/blogs/watsonx-watsonx-orchestrate-blog-team/2026/08/13/whats-new-in-watsonx-orchestrate-august-2026-mid , https://www.voiceflow.com/blog/best-agent-management-platforms.md , https://galileo.ai/blog/ai-observability-trends-2026 (notes OpenTelemetry GenAI conventions still in "Development", so cross tool telemetry is not standard). All vendor or blog sources; no analyst report (Gartner or similar) was found, so analyst claims are UNCERTAIN.
- Decentralised multi agent systems are harder to audit, so vendors layer a central control plane on top (Lyzr, vendor blog): https://lyzr.ai/blog/agent-orchestration/

### 2.3 Workplace digital twins and space planning
- A 2026 market blog sizes space planning software at USD 5.2 billion in 2026 and USD 11 billion by 2033 (CAGR 11.2 percent); single source, UNCERTAIN: https://www.htfmarketintelligence.com/blog/space-planning-software-market-ai-digital-twins-and-hybrid-work-reshape-the-future-of-workplace-optimization
- Kadence launched SpaceOps in April 2026, using its own live occupancy data for space decisions without extra sensors (vendor claim): https://app.dealroom.co/news/feed/kadence-launches-spaceops-first-workplace-platform-using-real-occupancy-data-for-space-planning
- Interactive floor plans with drag and drop are a standard feature in space planning products (example: https://www.teem.com/our-platform/space-planning-software/ ). Reports that desk utilisation is far below assumptions are industry commentary without a primary source (UNCERTAIN).

### 2.4 Operations control towers
- Control tower demand is driven by end to end visibility and a shift from passive tracking to exception based management with predictive alerts. Market sizes disagree widely (USD 10.2 billion in 2024 to 38.1 billion in 2033 per IMARC; USD 15.51 billion in 2025 to 109.32 billion in 2035 per Spherical Insights), so quote none as fact. One report warns months of data cleanup may precede trustworthy alerts. Sources: https://imarcgroup.com/control-towers-market , https://www.sphericalinsights.com/blogs/top-30-companies-in-global-control-towers-market-2026-2035-expert-view-by-spherical-insights , https://www.giiresearch.com/report/moi2117598-supply-chain-control-tower-market-share-analysis.html

### 2.5 What the evidence adds up to
1. Buyers value outcomes (notes, action items, exceptions) and governance over avatars. Mesh's retirement is the warning.
2. Users expect to build their own space with a real editor, templates and private areas.
3. Agent control planes sell on observability, intervention and audit. ANUM has audit, replay and approvals already.
4. Exception first views ("what needs me now") beat passive status walls.

## 3. Opportunity list, ranked

Value and effort are my estimates, not measured. Data truth fit: yes means every shown value has a persisted record.

| Rank | Opportunity | Value | Effort | Risk | Data truth fit |
|---|---|---|---|---|---|
| 1 | Owner Office Builder (this document, section 4) | Very high: the owner's first request, gates every other HQ feature | Large, 8 small PRs | Medium: overlap and concurrency | Yes: layout is a record |
| 2 | Needs-attention rail: blocked runs, pending approvals, failed meeting actions, per room, from existing presence and approvals | High | Small | Low | Yes: only existing records |
| 3 | Meeting output in the room: decisions and action items turned into tasks from the live meeting room | High | Medium | Medium: must go through approvals | Yes: meeting records |
| 4 | Team to room mapping ("this room belongs to team X"), with roster counts from real membership | High | Medium | Low | Yes if team ids are references |
| 5 | Replay scrubber over office-history for a room (what changed, who, when) | Medium to high | Small to medium | Low | Yes: history API exists |
| 6 | Layout templates by team size (copy of a saved layout, owner approved, created through the same API) | Medium | Medium | Low | Yes: templates are layouts, not data |
| 7 | Desk and room utilisation report from presence and assignment history | Medium | Medium | Medium: must say "not enough data" early | Yes if sourced from presence records |
| 8 | Mobile read-only office list and room status (Flutter, already planned F12) | Medium | Medium | Low | Yes |
| 9 | Private or restricted areas (rooms only visible to named roles) | Medium | Large | High: touches RLS | Yes |
| 10 | Intervene mid run from the room (pause, reroute) through existing agent controls | Medium | Medium | High: governance | Yes |
| Not now | Avatar chat, spatial audio, VR, generic metaverse | Low per evidence in 2.1 | Very large | High | Weak |

## 4. Office Builder gap analysis

Goal: the owner builds their own office inside the module (offices and rooms, size and position, name, type, teams and agents, desks, equipment, notes, layout, save) and it works live end to end.

### 4.1 What the owner can do today
Create building, floor, room; rename a room; type numbers for room x, z, width, depth; add and delete desks; assign and release agents; delete an empty room. All server backed, audited, revision checked.

### 4.2 What is missing, exactly

**API and data**
1. No update of a building or floor (name, level, time zone, coordinates): no PATCH, no delete. A typo in a floor name or a wrong building cannot be fixed; deletion of a building or floor is impossible.
2. No update of a room's `kind` or `capacity` after creation (only rename and geometry). Changing a meeting room to an office needs delete and recreate, which fails if desks exist.
3. No desk update (rename label) and no desk position inside a room. Desks are laid out by generator only.
4. No team link. There is no table or column tying a room or desk to a team or department; the owner cannot "assign teams". Only agents to desks exist.
5. No place for details: no equipment list, no notes, no description, no colour or icon on rooms or desks. Nothing to store "whiteboard, 2 screens" or notes.
6. No rotation, doors, walls or windows. The door is derived from the rectangle in `polygonForRoom`. Only axis aligned rectangles.
7. Geometry validation is overlap only. There is no floor bounds model: `OUT_OF_BOUNDS` exists in web code but the API accepts any x, z within +/-10000. Desk capacity versus room area is not checked. Rooms without geometry are not part of the overlap check, so a saved room can later collide with a generated one in the view.
8. No bulk or atomic layout save: geometry is one PATCH per room with its own revision. Moving two rooms that swap places cannot be done without passing through an overlap.
9. Permission is a single level: `workspace:manage` for every write. No "can edit layout" separate from "can assign agents"; no editor role below owner. UNCERTAIN whether that is wanted (owner decision, section 6).
10. Audit records exist but store only action and target; no before and after values, so history cannot show "moved room from here to there". Check `_audit` before building on it.
11. No outbox event on office changes, so other open browsers learn of a layout change only by refresh (gap 3 in the registry doc "Remaining gates" is still open).
12. Idempotency exists only for create. Client generated random ids mean no human readable stable ids or slugs.
13. Delete is refused for non-empty rooms with a generic 409; no "move desks out" helper. Building and floor delete rules are undefined.

**Editor UX (web)**
14. No visual editor. Geometry is four number fields; there is no drag, resize, or draw on the plan. The 3D canvas can pick a room but cannot move one.
15. No snap or grid, no alignment guides, no rulers, no units shown (metres assumed, UNCERTAIN; the code never says).
16. No undo or redo, no draft mode, no "discard changes", no unsaved-changes guard. Every field save is immediate.
17. No client side live validation: overlap and bounds errors only appear after the server rejects, and the message is generic.
18. No room templates or presets, no copy room, no copy floor.
19. No desk placement tool, no equipment or notes panel, no team assignment control.
20. Delete uses `window.confirm`; no accessible confirm dialog.

**Quality bar from CLAUDE.md**
21. English only: `OfficeSpaceLiveManager.tsx` has hard coded English strings and does not use `useLocale`. No Arabic, no RTL layout check.
22. Mobile: the manager is a form stack; there is no tested behaviour at 390x844 for a drag editor. Touch needs a separate model (pan with two fingers, select with tap, move via handles or number steppers).
23. Keyboard and screen reader: no keyboard move or resize of a room (arrow keys with step), no live region for results, no focus management after create or delete.
24. Tests: no e2e spec for the builder; no Playwright screenshots at the five viewports; no concurrency test of two editors in the web; no property test for overlap and snapping.
25. States: loading, empty (with next action), error, permission denied and large workspace (hundreds of rooms) are only partly handled in the manager; no virtualised list.
26. Docs: the registry doc lists room geometry in "Remaining gates" item 6 as separate feature work; it must be updated with every PR below.

### 4.3 Build plan (small PR sized sections)

Rules for all PRs: expand only migrations (revision id at most 32 characters, chain on the current head and renumber at merge); every PR updates `docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md`; tests, EN and AR strings, five viewport screenshots for UI PRs; no mock data.

| PR | Slice | Contents | Covers gaps |
|---|---|---|---|
| B1 | Edit and delete for buildings, floors, desks; room kind and capacity edit | `PATCH /buildings/{id}`, `/floors/{id}`, `/desks/{id}`, `PATCH /rooms/{id}` extended (kind, capacity; refuse shrink below desk count, refuse kind change that forbids existing desks); `DELETE` building and floor only when empty; audit with before and after | 1, 2, 3 (label), 10, 13 |
| B2 | Details: notes and equipment | Migration adds `notes` (text, max length), `color` (enum of design tokens) on rooms; new `office_room_equipment` (room, kind from a fixed list, quantity, note) with RLS and revisions; endpoints and audit | 5 |
| B3 | Teams on rooms | Migration adds nullable team reference on rooms (reference to the existing team or department concept, owner decision needed); API returns real member and agent counts from existing records; permission check that the team is visible | 4 |
| B4 | Layout model and atomic layout save | Floor bounds (width, depth) on floors; `PUT /floors/{id}/layout` with a list of rooms, desks positions and one floor revision; server validates overlap, bounds, desk inside room and desk count versus capacity in one transaction; optional rotation in 90 degree steps; office change events through the outbox | 6, 7, 8, 11 |
| B5 | Visual editor (web, desktop first) | Top view plan with drag, resize handles, snap to grid with a visible unit, alignment guides, client side validation using the same pure functions as the server (shared test vectors), draft mode with save and discard, undo and redo stack, unsaved-changes guard, accessible confirm dialog | 14 to 17, 20 |
| B6 | Editor content tools | Templates by team size (stored layouts applied through B4), copy room, desk placement, equipment and notes panels, team picker, rename and kind edit in the side panel | 18, 19 |
| B7 | Access, EN/AR, keyboard, mobile | Split permission (layout edit versus assignment) if the owner decides yes; Arabic strings and RTL review; keyboard move and resize with arrow keys and a live region; touch behaviour at 390x844 and 768x1024; virtualised room list; all states | 9, 21, 22, 23, 25 |
| B8 | End to end and docs | Playwright journey: build an office from empty, save, reload, edit in a second session and see the 409 flow, delete; five viewport screenshots; load test with hundreds of rooms; docs updated and linked | 24, 26 |

Suggested order: B1, B2, B4, B5 (the owner gets a usable visual builder), then B3, B6, B7, B8. B3 waits for owner decisions.

### 4.4 Status
The API slices (B1 to B4, with the team link B3 decided as a department link) are merged. The web builder (B5, B6, B7 and the browser part of B8) is in `apps/web/src/hqBuilder/` and covers gaps 14 to 20 (plan, drag, resize, rotate, snap with a visible unit, live checks, draft with undo, redo and discard, templates, copy room, desk placement, equipment and notes, team link, inline confirms), 21 to 25 (English and Arabic with RTL, touch at 390x844 and 768x1024, keyboard move and resize with a live region, e2e and five viewport screenshots, 500 room check). Still open: alignment guides and rulers, a virtualised room list (the list is searchable and paged instead), copy floor, live notice of another editor's change (stale revision is caught on save), and the separate layout editor permission (gap 9, owner decision). See the "Web builder" section of the registry doc.

## 5. Recommendations (top 5)
1. Build the Office Builder (B1, B2, B4, B5 first). It is the owner's request and the base for everything else.
2. Add the needs-attention rail from existing presence and approvals; high value, small, fully data true.
3. Turn live meeting decisions and action items into approved tasks; the market values meeting output over avatars.
4. Link rooms to teams with real counts (after the owner picks the team model).
5. Add a history scrubber for a room using the existing history API, a governance feature that agent control plane buyers ask for.

## 6. Open owner decisions
1. Units and scale: are plan units metres? Default floor size and maximum floor size?
2. Rotation: only 90 degree steps, or free angles? Rectangles only, or L shaped and custom polygons?
3. Team model: link rooms to the existing departments, to teams in `team_models.py`, or to a new "office team" list? Can one room belong to many teams?
4. Who may edit layout: only owners (`workspace:manage`) or also a separate "layout editor" role? Who may assign agents?
5. Equipment: a fixed list of kinds (screen, whiteboard, printer, rack) or free text? Are equipment counts only notes, or should they feed procurement or assets?
6. Templates: ship built-in templates by team size, or only let owners save their own?
7. Deleting a building or floor: allowed only when empty (proposed), or with a guarded cascade?
8. Is a draft and publish model wanted (edit privately, publish to all viewers), or is every save live (proposed first)?
9. Mobile: read only and number step editing on phones (proposed) or full touch drawing?
10. Arabic: should room names support separate English and Arabic names, or one free text name?

## 7. What this document does not claim
No test or build was run for features here because none were built. Market figures are from the cited pages on the dates shown and are not independently verified. No Gartner or Forrester source was found.
