# ANUM MINE headquarters: campus motion

Status: built on 26 October 2026. The owner asked for trucks, tyres and other things on the campus to be seen moving and doing things. Web only; no migration, no new route, no write path. On 28 October 2026 the owner asked for Tyre Pulse events to work "the way it should be": the tyre dashboard gained two read-only fields with the stored times of the last inspection and of the newest alert (see [Tyre Pulse event times](#tyre-pulse-event-times)); still no migration and no write path.

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
- The first answer after opening replays nothing (presence, flow and Tyre Pulse); only a change between two answers is an event. A record older than 30 minutes (Tyre Pulse: 24 hours) is history, not an event.
- Feed off or unreadable: no records, no motion, and the Tyre Pulse building's card says "Tyre Pulse: Not connected" (the tooltip says why: not connected, no access, could not be read, or open that workspace). No feed ever falls back to a made-up motion.
- Reduced motion: no vehicle is drawn. The same "Because" line is shown as a static marker on the card for 60 seconds.

## The motions

| # | Layer | Trigger record (and where it is read) | Building | Representative | Duration | Card line (English) | Source |
|---|---|---|---|---|---|---|---|
| 1 | 2 | Presence: a building's `working` count is higher than in the previous answer, `presence_verified_at` set (`GET /api/v1/office/campus`, refreshed by the presence stream) | That campus building | White van, amber roof beacon, parks in the yard, turns, leaves | 18 s (+6 s line) | Because: presence record shows an agent working, 14:05 | `lib/hqCampusLoader.ts` totals; `presenceRecords` |
| 2 | 2 | Flow: a task of the open workspace whose stage became `in_office` (a running run) between two answers (`GET /api/v1/office/flow`, re-read on task, run and approval events) | The campus building of the task's office (`location.building_id`) | Box truck, amber beacon | 18 s (+6 s) | Because: a task run started, 14:05 | `flowRecords` |
| 3 | 2 | Flow: a task whose stage became `done` between two answers | Same | Box truck, green beacon | 18 s (+6 s) | Because: a task finished, 14:05 | `flowRecords` |
| 4 | 2 | Tyre Pulse: a later inspection than in the previous answer: `operations.last_inspection_at` moved forward, or, for a source that keeps dates only, `operations.last_inspection` became a later day (`GET /api/v1/tyre/dashboard`, read every minute while the tab is visible and on coming back to the tab) | The workshop building of the open workspace | One tyre rolls from the workshop door onto the rack (4 s), rests (5 s), rolls back (4 s) | 13 s (+6 s) | Because: Tyre Pulse inspection recorded, 14:05 (a date-only source: Oct 26) | `tyreRecords` |
| 5 | 2 | Tyre Pulse: the dashboard's `latest_alert_at` (when ANUM first saw the newest alert that is not resolved) moved forward since the previous answer; the alert kind comes from the matching record of `GET /api/v1/tyre/alerts?status=active` | Same | Flatbed with a stack of tyres, red beacon | 20 s (+6 s) | Because: Tyre Pulse alert raised (low tread), 14:05 | `tyreRecords` |
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
- `GET /api/v1/tyre/dashboard`: `operations.last_inspection` (the day of the last synced inspection), `operations.last_inspection_at` (its time) and `latest_alert_at` (the newest alert's time); see below.

Not readable today, and so not animated: work orders as events, tyre fitments and removals as events, and any other workspace's feed. The integration reader role (`anum_integration`, `infra/tyrepulse/`) is server side and is not used by the browser.

## Tyre Pulse event times

Both fields are read only, ISO 8601 instants in UTC, taken from stored records and never estimated, or `null` when unknown (contract `packages/contracts/src/tyreEventTimes.ts`, server `tyre/api.py`, `tyre/fleet.py`, `tyre/mapping.py`):

- `operations.last_inspection_at`: inspections are not recorded by ANUM; they are synced from the source's inspection table, where the time lived only in the mapped inspection column and the sync kept the date. The sync now also keeps the instant that column stores (`inspected_time` on each inspection and visit, inside the snapshot ANUM already stores as JSON, so no migration). It counts as a time only when the value carries a time of day and a time zone: a date, a text without an offset (the zone is unknown) or a value at exactly midnight (a date stored as a timestamp) stays date-only. The field is set only when every inspection of the last inspection day has a time, and it is the latest of them. Snapshots synced before this change, and sources that keep dates only, give `null`: the record is shown as a day ("Oct 26"), never with a made-up time. A source that stores the time in a separate column gets it by mapping that column as the inspection date in Tyre setup.
- `latest_alert_at`: the stored `first_seen_at` of the newest alert that is not resolved (ANUM writes it when a sync raises the alert), so no new storage was needed.

In the browser (`lib/hqTyreFeed.ts`) a value that is not a well formed ISO time is dropped, not repaired. `tyreRecords` compares each answer with the previous one: the first answer is the baseline; a later `latest_alert_at` is one alert event; a later `last_inspection_at`, or a later day when the source keeps dates only, is one inspection event. When only the day was known before and the same day now has a time, it may be the same inspection, so it is not an event. Times that move back (an alert resolved) are not events, and a change older than the 24 hour window is history.

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

- A Tyre Pulse source that stores inspection dates only (or a snapshot synced before 28 October 2026) still shows the day, "Oct 26", until its inspection column carries a time with a zone and the next sync runs.
- Tyre Pulse changes are seen when the dashboard is read (every minute, and on coming back to the tab), and a sync must have run: two inspections between two reads are one event.
- Only the open workspace's Tyre Pulse feed can be read.
- Presence rises are seen when the campus is refreshed, so a short burst between two refreshes is one event.
- The vehicle models are low poly (about 100 to 900 triangles); they read as vehicles from the campus camera, not close up.
- Motion on a real GPU for frame cost has not been measured (the CI runner is a software rasteriser); see the [performance budget](ANUM_MINE_HQ_PERFORMANCE_BUDGET.md).
