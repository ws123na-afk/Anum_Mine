# ANUM MINE headquarters: agent walkers

Status: built on 27 October 2026. The owner asked for people around the campus and "something more exciting", and chose one rule for them: **real agents only**. A figure appears only for a real agent, and only because a stored record says that agent moved. No record means nobody is drawn. No decorative or invented crowds. Web only: no migration, no new route, no write path.

## The rule

- A walker is the agent's own figure (the same people as at the desks: `hqAssets/interior/figures.ts`, look from the agent id and the owner's appearance choice, neutral by default, never from a name). It walks door to door along the campus's own walkways, pauses at the destination door, walks in and is gone. It exists only while the event lasts.
- It starts from one `WalkerRecord` (`lib/hqWalkers.ts`). A record has exactly these fields and no others: a kind from a list of three, an agent id, two building keys the campus really has, an instant that parses and is not in the future or older than 30 minutes, the id of the place the line names (a room, or a building) and a record id used only to show a record once. `makeRecord` refuses anything else. The words on screen are a message template filled with the time and the NAME of a registry room or building, so a meeting title, a transcript, a prompt, a task title or a customer cannot reach a string. A unit test states this as a property over hundreds of random inputs, in English and Arabic.
- If the data cannot say where an agent went (it is only "working"), nothing is drawn. A paused, unknown, stale or private agent is never drawn. An agent whose desks are in two buildings (a day and a night desk) has no known home, so nothing is drawn for it.
- The first answer after opening replays nothing: only a change between two answers is an event. A meeting that was already live when the page opened does not send anybody. A record older than 30 minutes is history.
- One figure per agent. At most 12 at once; the rest wait in a queue (a waiting entry is dropped after two minutes) and the screen says "N more agents on the move" only when N is the real length of that queue.
- Feed off or unreadable: no records, no walkers, and the open building's card and the status chip say "Agents on the move: Not connected". Nothing is drawn in place of a record.
- Reduced motion: nothing walks. The agent's figure stands at the destination door, with the same "Because" line, for 60 seconds.

## What data exists (the investigation)

Read on 27 October 2026, in code and docs. Everything below is stored and readable by the browser today.

| Feed | What it says | Says where an agent is? |
|---|---|---|
| Presence (`GET /api/v1/office/presence`, `office_presence.py`) | One item per agent: state (`working, queued, meeting, waiting_approval, blocked, idle, paused, unknown`), provenance, `last_verified_at`, `stale`, and `desk_id` | Only through `desk_id`: the desk the agent is assigned to. There is no "walking" state and no location history. |
| Office registry (buildings, floors, rooms, desks) | Which building holds which room and desk, and which agent sits at a desk | Yes for where an agent sits (its home building). It carries no time. |
| Campus (`GET /api/v1/office/campus`) | One building per workspace, with counts by state | No agent ids at all. |
| Live meetings (`GET /api/v1/agent-meetings`, `…/{id}`) | Status, `room_id` (an office room), `started_at`, `ended_at`, participants | Yes: a meeting held in a room is held in that room's building, and its stored participants are agents. |
| Live flow (`GET /api/v1/office/flow`) | Tasks and runs with the office they happen in | Not for an agent: tasks are not people. Used by the work motion, not here. |

**No feed carries inter-building or inter-room location changes today.** There is no record that says "agent A is now in building B". Walkers are therefore derived from two real events that do exist, a meeting held in a room of another building and a desk that is in another building than before, and from nothing else. Nothing was added to the API or the database. If a stored per-agent location with a time is wanted, that is a new read-only field and needs a decision first.

Buildings here are the registry buildings of the open workspace (the starter campus creates eight). Other workspaces' buildings are never expanded and their agents are not readable from this browser, so a walker goes between buildings of the open workspace only.

## The triggers

| # | Kind | Record that starts it | From, to | Duration | Because line (English) | Source |
|---|---|---|---|---|---|---|
| 1 | `meeting_started` | A live meeting appears between two answers, with a room in another building than a participant's desk, `started_at` set and recent (`GET /api/v1/agent-meetings?status=active`, refreshed with presence and flow events and every 10 s) | The participant's desk building to the building of the meeting room | 10.6 to 24.6 s by path length (walk 9 to 23 s, 1.6 s pause at the door) | Because: meeting started 14:05 in Meeting Room A | `meetings.rooms` in `lib/hqPresence.ts`; `startedRecords` |
| 2 | `meeting_ended` | A meeting that was live is gone from the live list and its record (`GET /api/v1/agent-meetings/{id}`) says `finished`, `cancelled` or `failed` with `ended_at`. A meeting that is only paused sends nobody back | The meeting room's building to the participant's desk building | Same | Because: meeting in Meeting Room A ended 14:20 | `vanishedMeetings`, `endedRecords` |
| 3 | `desk_moved` | The presence answer puts an agent's `desk_id` in another building than the answer before (not for an agent seated in two buildings) | The old desk's building to the new desk's building | Same | Because: moved to Tyre Pulse, verified 14:05 | `presence.items[].desk_id`, `last_verified_at`; `movedRecords` |

The tooltip (hover, focus or tap on the figure) shows the agent's display name only when the same viewer already sees that name in the team and presence panels (the presence record's name, never for a private agent), and the "Because" line. Where no name is visible it says "An agent". The same sentence goes to a polite live region ("WhatsApp Concierge is on the way to ANUM HQ. Because: meeting started 14:05 in Meeting Room A."), and the destination building's card shows the line.

Time is the record's own: `started_at`, `ended_at`, or `last_verified_at`. Nothing is the time the screen noticed.

## What it looks like

- The figure is cut into body, legs and arms (`hqAssets/interior/walkerFigure.ts`, about 300 triangles) from the same head, torso and colours as the desk figure, so the same agent looks the same. Legs and arms swing in opposite turns, one step cycle per 5 metres of path (the stride follows the distance walked, so feet do not slide), with a small bob. Walkers stand and face the building during the pause at the door.
- It is drawn 3.5 times life size: the campus is drawn small, and a life size person is a speck from the campus camera. A soft ring of light and a thin light column over the figure keep it findable; a small dark patch grounds it. They claim nothing: the pin is the figure's own marker.
- Night, headlights and the rest of the scenery are untouched.
- Hover, focus or tap the 28 px target over a figure for its reason (a real button with an accessible name). The status chip in the corner of the stage says "1 on the move", plus "N more agents on the move" when agents wait.

## Routes

The route is made from the campus's own walkways (`campusSpokes.routeSpokes`, the walkway from each door to the roundabout's pavement): out of building A's facade to its door, along its walkway to the pavement circle, round the circle the short way, along building B's walkway to its door, and in to B's facade. It leaves no existing path, never enters the plaza, and a unit test checks on the real campus scene, for every pair of buildings, that it passes exactly through both doors, starts and ends on the facades, and stays outside the pavement circle. A building without a walkway makes no walker.

## Where the code is

| Part | File |
|---|---|
| Records, topology, routes, poses, planner (pure) | `apps/web/src/lib/hqWalkers.ts` |
| Reading the registry, presence and meetings, the status chip, targets and live region (lazy chunk) | `apps/web/src/HqCampusWalkers.tsx`, `hqWalkers.css` |
| Drawing (lazy chunk, its own frame timer while a walker walks) | `apps/web/src/lib/hqWalkerLayer.ts`, `hqAssets/interior/walkerFigure.ts` |
| Messages | `lib/messages/hqWalkers.en.ts`, `hqWalkers.ar.ts` |
| Tests | `test/hqAgentWalkers.test.ts`, `e2e/hq-agent-walkers.spec.ts` |

The renderer only gained `walkerHooks()`, a few bytes; the layer is attached from `HqStage` through a dynamic import, only when a plan has a figure to draw. The detector chunk (`HqCampusWalkers`, pure logic and the overlay, about 6 KB gzip with the pure module) loads when the open workspace has two or more registry buildings, because it is what notices a record; the three.js layer and the figure geometry (under 2 KB gzip) load only when a record has produced a walker.

## Gaps, said plainly

- Only movement between buildings is drawn. A meeting in another room of the same building, or a desk moved within one building, produces no figure: there is no outside to walk through, and the interior has no moving figures.
- A walker is a depiction of a stored change, not a measurement of anything physical. An agent in a meeting is a participant of a stored meeting; it does not travel.
- Records are seen when the headquarters refreshes (on events and every 10 seconds), so a meeting that starts and ends between two reads, or two desk moves between two answers, are seen as one change or not at all.
- Only one live meeting per room is read (the first one the loader keeps).
- `meeting_ended` needs one meeting record read per vanished meeting (at most six per answer).
- `desk_moved` trusts the desk id the presence endpoint reports. If a deployment reports the desk of the shift on duty, a shift handover between two buildings would draw a walk with the line "moved to ...". Not seen in the code today (`office_presence.py` reports one desk per agent), not tested against a shift campus on a real server.
- Presence is read per workspace, so other workspaces' agents never walk.
- The figure's role family comes from the registry kind of its desk's room. The desk figure derives it from the furnished room kind, which can differ for a few room names, so an outfit can differ in rare cases.
- The hover target (28 px) sits over the canvas: drag, wheel and pinch that begin on it are not passed to the camera.
- A real GPU frame cost with twelve walkers is not measured (the CI runner is a software rasteriser).
