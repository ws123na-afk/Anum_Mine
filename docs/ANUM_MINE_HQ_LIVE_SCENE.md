# ANUM MINE headquarters: live scene

How the headquarters page stays live and what its desks, wall and camera show. Matrix rows 8.1, 8.2, 5.5, 2.6, 3.4, 7.2 and 14.6 (`ANUM_MINE_FEATURE_COMPLETION_MATRIX.md`). Server side: `ANUM_MINE_PRESENCE_AND_CAMPUS.md` and `services/api/anum_api/office_presence_api.py`. Nothing here changes the API.

## Data truth

Every state, number and marker comes from a stored record through an existing endpoint. When a source does not answer, the screen says "Not connected" and shows no number. Nothing is estimated, and no figure moves unless a record moved it.

## The presence link (8.1, 8.2, 14.6)

One `PresenceHub` (`apps/web/src/lib/presenceStream.ts`) serves the whole page. `usePresenceLive` leases it with `acquirePresenceHub`, so however many parts ask for presence there is one stream; the last release stops it. The stream is read with `fetch` and an incremental event-stream parser, because the browser's `EventSource` cannot send the sign-in headers.

| Situation | What the page does | Status shown |
|---|---|---|
| First connect | Takes the snapshot frame, then deltas | Connecting, then **Live** |
| A delta with a repeated or older sequence | Ignored | Live |
| A skipped sequence number, or a delta before a snapshot | Keeps what it has, reconnects at once, takes the new snapshot | Live |
| The server ends the stream (it bounds its lifetime) | Reconnects after 0.3 s with `Last-Event-ID` | Live |
| No bytes for 45 s (the server sends a keep-alive about every 15 s) | Drops the link, reconnects | **Reconnecting** |
| No snapshot within 8 s of a 200 answer, or an answer that is not an event stream (a buffering proxy) | Counts as blocked | Reconnecting |
| Failure | Backoff 1 s, 2 s, 4 s ... up to 30 s, with 20 percent jitter | Reconnecting |
| Three failures in a row, or 404, 405, 501, 503 on the stream | Polls `GET /office/presence` every 10 s and tries the stream again every 60 s | **Checking every 10 s** |
| Polling fails too | Keeps the last answer, greyed out when it ages, keeps trying every 10 s | **Not connected** |
| 401 or 403 | Stops. The tooltip says who can grant access | Not connected |
| Poll answers 404 | The server has no presence: the page reads tasks and meetings itself, as before | Not connected |
| The tab becomes visible, or the network returns | Reconnects now | |

A snapshot of another workspace is never shown, and a snapshot older than the one on screen is ignored. A reconnect's snapshot that repeats the screen does not redraw anything.

**Stale.** The server marks an item stale when its record was last confirmed more than 5 minutes before a frame is built, and does not resend an unchanged item. The browser therefore ages items itself every 20 s, on the server's clock (the frame's `as_of` plus the time since it arrived), so a wrong device clock changes nothing. An item with no record behind it (idle, or a desk whose agent you cannot see) is stale only when the link has been silent for the whole window. A stale item is drawn as unknown (below).

The status chip is in the header (`data-testid="hq-live-link"`), a polite live region, with the reason in its tooltip. The older "Connected / Updating" chip next to it is the office registry's connection and is unchanged.

## What a desk shows for each state (5.5)

`apps/web/src/hqAssets/interior/stateVisual.ts` is the one table. Three channels at once, so colour is never the only signal:

| State | Screen | Bezel light | Seated pose | Marker above the monitor |
|---|---|---|---|---|
| working | teal, text lines | green | typing | none |
| queued | dim blue, text lines | blue | leaning back | three rising bars |
| meeting | violet, dark | violet | talking | speech bubble |
| waiting_approval | amber, text lines | amber | reading | flag on a pole |
| blocked | red, dark | red | hand to the head | exclamation mark |
| idle | blue, dark | dim green | leaning back | none |
| paused | black | grey | leaning back | pause bars |
| unknown, or stale | grey | dark grey | leaning back | empty frame |

The agent panel shows the text label and one sentence of what it means (`hq.stateHelp.*`). There is no walking figure: no record places anyone but at their desk. A figure is drawn only where the registry assigns someone to the desk.

## The operations wall (2.6)

`HqCommandWall` shows, for an operations room (a room whose module is the flow dock), five tiles:

| Tile | Source | Rule |
|---|---|---|
| Open tasks | `GET /office/flow`, tasks | stage is not done |
| Blocked work | flow, tasks | stage is blocked |
| Pending approvals | flow, approvals | a task with a pending approval |
| Running now | flow, tasks | status running, not counted when its run went silent or failed |
| Agents by state | the presence list | agents only; a desk whose agent is hidden is not counted |

A tile whose source is `error` or `not_connected` shows "Not connected"; `no_access` shows "No access"; loading shows "Loading". A flow list that hit its page limit shows the number with a "greater than or equal" sign. The panel is in the room inspector; from 900 px wide the same board also floats over the selected room in the 3D scene. A retry button appears when a source failed.

## Operations opens a filtered dock (7.2)

Opening the flow from a room (inspector or list) sets the dock's room filter: "Showing NAME only", with "Show all offices" to clear it. Opening it from Office tools shows everything.

## Inside camera (3.4)

The bottom toolbar has **Inside**, enabled once a room is selected (not in the top view). It puts a low camera (eye height 1.5 m, field of view 62) a metre inside the room's front wall. The orbit pivot sits 5 cm from the eye, so the rotate buttons, the keys Q and E, and dragging turn the view on the spot; the eye cannot leave the room. Enter or Space on the focused button toggles it, Escape leaves, Reset leaves. Selecting another room moves the camera there; changing floor or view leaves. Under reduced motion the camera jumps instead of flying. It uses the existing navigation strip and the fold control unchanged, because it is a camera preset, not a new view.

## Tests

- Unit: `test/presenceStream.test.ts` (parser, hub, backoff, polling, staleness, one hub), `test/hqStateVisual.test.ts`, `test/hqCommandWall.test.ts`, `test/hqCamera.test.ts`.
- Browser: `e2e/hq-live-scene.spec.ts` against a mock that answers the stream like the server does (`stream` option of `mockHq`: `live`, `blocked`, `down`, `denied`, `unsupported`). Set `HQ_LIVE_SHOTS=<dir>` to keep screenshots.
- Test hygiene: `e2e/calendar.spec.ts` and the journey calendar test pin the page clock to the run day's midday (`pinCalendarClock`), so a run that crosses midnight cannot move "today" under the fixture.

## Budgets

Measured on this change (software GL, one worker): lazy headquarters JS 129,259 bytes gzip (limit 135,000), CSS 11,575 (limit 17,000), renderer chunk 153,709 (limit 160,000). The presence code (`lib/presenceBrowser.ts`, 3.1 kB gzip) and the wall (`HqCommandWall`, 1.1 kB) are their own lazy chunks. `e2e/hq-live-scene.spec.ts` runs 15 tests in about 1.7 minutes in each of the desktop and mobile projects. The unit-test and spec hygiene fixes: the guided visit and campus specs wait for the renderer (`sceneReady`) instead of the 5 s default and free the WebGL context between reloads (`reloadReleasingGl`); the visit's overflow check waits for the label cards to settle after a resize (the assertion is unchanged).

## Not finished

- H-09 (rare lost deliveries under load) is closed in memory mode: the cause was the load tool counting frames that arrived before the producer knew the task id, not the server (10,000 of 10,000 delivered at 200 clients). The web still recovers from a lost delta by the sequence gap rule. A load run on the Oracle VM with PostgreSQL has not been done; owner steps are in `docs/ANUM_MINE_HQ_LOAD_TESTING.md`.
- The screens of the 3D video wall and the desk monitors show live faces drawn from real records ([live screens](ANUM_MINE_HQ_LIVE_SCREENS.md)): the wall carries the same numbers as the Operations wall block, a monitor the state, record type and real elapsed time of its agent. The tiles under the face stay abstract.
- The commerce counts are not on the wall.
- Pose is a cue, not a measurement. The marker and the text carry the meaning.
