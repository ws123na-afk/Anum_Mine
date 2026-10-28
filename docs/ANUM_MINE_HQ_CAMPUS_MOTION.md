# ANUM MINE headquarters: campus motion

Status: built on 26 October 2026. The owner asked for trucks, tyres and other things on the campus to be seen moving and doing things. Web only; no migration, no new route, no write path.

The campus has two motion systems that never mix. The first claims nothing. The second is started by one stored record at a time and always says which.

| Layer | Question it answers | May it imply work or people? |
|---|---|---|
| 1. Ambient scenery | "Does this place look alive?" | Never. No vehicle, wheel or light means an order, a delivery, a job or a person. |
| 2. Record-backed work motion | "Did something just happen in this building, and what says so?" | Only through one record, and the card names it ("Because: ...") |

Layer 1 is described in [scenery motion](ANUM_MINE_HQ_SCENERY_MOTION.md); this page adds what changed there and defines layer 2. People are a third system with the same rule (one stored record, a "Because" line, bounded, reduced motion, "Not connected"): see [agent walkers](ANUM_MINE_HQ_AGENT_WALKERS.md). Scenery never draws a person, and a walker never drives a vehicle.

## Layer 1: what is new

- More traffic: up to 26 vehicles (was 18) on the two ring lanes, plus a third loop round the roundabout at the centre when the campus has one. The mix now includes a bus and a flatbed carrying stacks of tyres on each ring lane. The flatbed is plain scenery: no lettering, no plate, no owner, and it says nothing about an order or a delivery.
- Wheels turn. Every wheel is an instance of one wheel mesh (a dark tyre with three light spokes on each face); its matrix carries the spin, the distance driven over the wheel radius, so a faster lane spins faster and nothing slips.
- Headlights: the head and tail lights glow as before, and from the blue hour on a pool of warm light lies on the road ahead of each vehicle (one additive trapezoid per vehicle, hidden by day).
- No pedestrians, no indicators, no stopping: the loops are fixed, one speed per lane.
- Cost and gating are unchanged: levels 0, 1 and 2, reduced motion and a hidden tab freeze it, a software rasteriser does not place vehicles unless a person or a test pins `localStorage["anum.hq.ambient"]`, and slow frames step the level down before anything else. The traffic layer is its own lazy chunk now, so a software rasteriser never downloads it.

## Layer 2: the rule

A work motion starts only from a `WorkRecord` (`lib/hqWorkMotion.ts`). A record has exactly these fields and no others: a kind from a fixed list of five, the key of a building the campus really has, an instant that parses and is not in the future, an alert kind from a fixed list of six, and a record id used only to show a record once. `makeRecord` refuses anything else, so a title, a name, a customer, a vehicle or a count cannot reach the screen through it. The words on the card are a message template filled with the time (and, for an alert, one word from the fixed list), nothing more (`becauseLine`). Unit tests state this as a property over hundreds of random inputs, in English and Arabic.

- One vehicle per event type, never a count. The card tooltip says it: "One animation per event type, not a count." If Tyre Pulse reports forty alerts, one hauler drives in, for the newest.
- Bounded: a motion runs its timeline once (13 to 20 seconds), then the vehicle is gone and the scene is at rest. The card line stays six seconds longer, then it goes too. Nothing loops while nothing changes.
- One live motion per building and kind, at most four animated at once; a fifth shows its card line without a vehicle.
- A record is shown once per page load: leaving the campus and coming back does not replay it.
- The first answer after opening replays nothing (presence and flow); only a change between two answers is an event. A record older than 30 minutes (Tyre Pulse: 24 hours) is history, not an event.
- Feed off or unreadable: no records, no motion, and the Tyre Pulse building's card says "Tyre Pulse: Not connected" (the tooltip says why: not connected, no access, could not be read, or open that workspace). No feed ever falls back to a made-up motion.
- Reduced motion: no vehicle is drawn. The same "Because" line is shown as a static marker on the card for 60 seconds.

## The motions

| # | Layer | Trigger record (and where it is read) | Building | Representative | Duration | Card line (English) | Source |
|---|---|---|---|---|---|---|---|
| 1 | 2 | Presence: a building's `working` count is higher than in the previous answer, `presence_verified_at` set (`GET /api/v1/office/campus`, refreshed by the presence stream) | That campus building | White van, amber roof beacon, parks in the yard, turns, leaves | 18 s (+6 s line) | Because: presence record shows an agent working, 14:05 | `lib/hqCampusLoader.ts` totals; `presenceRecords` |
| 2 | 2 | Flow: a task of the open workspace whose stage became `in_office` (a running run) between two answers (`GET /api/v1/office/flow`, re-read on task, run and approval events) | The campus building of the task's office (`location.building_id`) | Box truck, amber beacon | 18 s (+6 s) | Because: a task run started, 14:05 | `flowRecords` |
| 3 | 2 | Flow: a task whose stage became `done` between two answers | Same | Box truck, green beacon | 18 s (+6 s) | Because: a task finished, 14:05 | `flowRecords` |
| 4 | 2 | Tyre Pulse: the dashboard's `operations.last_inspection` is today or yesterday (`GET /api/v1/tyre/dashboard`, polled every minute while the tab is visible) | The workshop building of the open workspace | One tyre rolls from the workshop door onto the rack (4 s), rests (5 s), rolls back (4 s) | 13 s (+6 s) | Because: Tyre Pulse inspection recorded, Oct 26 | `tyreRecords` |
| 5 | 2 | Tyre Pulse: the newest active alert whose `first_seen_at` is within 24 hours (`GET /api/v1/tyre/alerts?status=active`) | Same | Flatbed with a stack of tyres, red beacon | 20 s (+6 s) | Because: Tyre Pulse alert raised (low tread), 14:05 | `tyreRecords` |
| 6 | 1 | none (scenery) | Roads and roundabout | Cars, SUVs, vans, a bus and a tyre flatbed on fixed loops, wheels turning | loops while motion is allowed | none | `trafficPlan`, `hqSceneryLayer.ts` |
| 7 | 1 | none (scenery) | Road surface | Pools of headlight on the road ahead of each vehicle, from the blue hour | with the light preset | none | `hqSceneryLayer.ts` |

A tyre rack stands in the yard of the Tyre Pulse building while the Tyre Pulse feed is connected. It is a fixed prop and holds no tyres; it is where the tyre of motion 4 is put.

## Which building is the Tyre Pulse building

The Tyre Pulse routes are per workspace: the browser can read the feed of the open workspace only, never another workspace's. So:

1. In the open workspace, the building drawn as a workshop (the starter campus draws Tyre Pulse that way, by its stable id) is the Tyre Pulse building.
2. A workspace with no workshop building but a connected source uses its single building.
3. Any workshop building of a workspace that is not open says "Not connected" with the tooltip "Open this workspace to read its Tyre Pulse feed." It is not animated.

## What the HQ can read from Tyre Pulse today

Readable by the browser, read only, with the `tyre:read` permission and a connected source (`docs/tyre-planning.md`):

- `GET /api/v1/tyre/setup`: whether a source is connected.
- `GET /api/v1/tyre/alerts`: alert records ANUM derives from the synced Tyre Pulse data, each with a kind, a severity, `first_seen_at` and a status. Only the id, kind and time are kept; the subject label, message and values are discarded in `lib/hqTyreFeed.ts` before anything is stored in state.
- `GET /api/v1/tyre/dashboard`: `operations.last_inspection`, the date (not the time) of the last synced inspection.

Not readable today, and so not animated: single inspection events with a time of day (the sync keeps counts and the last date), work orders as events, tyre fitments and removals as events, and any other workspace's feed. The integration reader role (`anum_integration`, `infra/tyrepulse/`) is server side and is not used by the browser. Nothing was added to the API, the database or Tyre Pulse for this page; if per-event inspection timestamps are wanted, that is a new read-only field and needs a decision first.

## Where the code is

| Part | File |
|---|---|
| Records, planner, routes, timelines (pure) | `apps/web/src/lib/hqWorkMotion.ts` |
| Reading the three feeds, the card lines, the plan (lazy chunk, renders nothing) | `apps/web/src/HqCampusWork.tsx`, `lib/hqTyreFeed.ts` |
| Drawing (lazy chunk, its own frame timer while a motion exists) | `apps/web/src/lib/hqWorkLayer.ts`, `hqAssets/environment/workVehicle.ts` |
| Traffic and wheels (lazy chunk) | `lib/hqSceneryLayer.ts`, `hqAssets/environment/scenery.ts`, `sceneryVehicle.ts` |
| Card lines and styles | `CampusView.tsx` (`BuildingCard`), `hqCampusMotion.css`, messages `hqMotion.en.ts` / `hqMotion.ar.ts` |
| Tests | `test/hqCampusMotion.test.ts`, `e2e/hq-campus-motion.spec.ts` |

The renderer only gained `workHooks()`, a few bytes; the work layer is attached from `HqStage` through a dynamic import, only when a plan has something to draw.

## Routes

A vehicle needs a straight run from a road to the yard stop (8.5 m out from the door, 6 m to the side of the walkway) that crosses no building. The nearest such point on the roads is used. If none exists, the card line still shows and nothing is drawn.

## Gaps, said plainly

- Tyre Pulse inspections have a date, not a time, so the card says "Oct 26", not "14:05".
- Only the open workspace's Tyre Pulse feed can be read.
- Presence rises are seen when the campus is refreshed, so a short burst between two refreshes is one event.
- The vehicle models are low poly (about 100 to 900 triangles); they read as vehicles from the campus camera, not close up.
- Motion on a real GPU for frame cost has not been measured (the CI runner is a software rasteriser); see the [performance budget](ANUM_MINE_HQ_PERFORMANCE_BUDGET.md).
