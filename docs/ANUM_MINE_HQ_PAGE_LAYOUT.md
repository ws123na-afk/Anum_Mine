# ANUM MINE headquarters: page layout and side panels

The owner's request (27 October 2026): make the headquarters page clear, with the side panels and navigation easy to read and honest about what is live. This file is the spec for the page around the 3D scene. The scene itself is in [the extension points](ANUM_MINE_HQ_EXTENSION_POINTS.md), the labels in [labels](ANUM_MINE_HQ_LABELS.md), the flow in [live flow](ANUM_MINE_HQ_LIVE_FLOW.md) and the live link in [live scene](ANUM_MINE_HQ_LIVE_SCENE.md).

## The rule

Nothing but the scene's own controls sits on the scene. Panels live in their own column, under the scene, or in a drawer the person opened. The stage takes the height the viewport leaves after the page header above it and the quick views and status row below it.

```
header: title and tagline, then one tool row: status chips | refresh, view switch, Hide side panels, Office tools
+--------------------------+----------------+
| stage (3D)               | side column    |   side column: selected room or building, Total agents,
| camera rail on the right |  (scrolls)     |   Quick moves, Executive assistant
+--------------------------+----------------+   inside a building: floor plan and people, Quick moves, assistant (tabs)
quick views (thumbnails)
status row: Total agents chips (when the side column is not open) and the Live flow bar
```

| Width | Side panels | Total agents | Selected room or building |
|---|---|---|---|
| 1280 and up | docked column (320 px); Hide side panels folds it away | panel in the column | top of the column |
| 900 to 1279 | drawer from the header button | chips in the status row | under the scene |
| under 900 | drawer (bottom sheet under 760 px) | chips in the status row | under the scene, stage first |

Heights come from three shared variables on `.hqNav` (`--hq-above`, `--hq-below`, `--hq-stage-h`), never a fixed height. The header buttons for the panels (`Hide side panels`, `Floors`, `Quick moves`) are portalled into the page header's tool row, so they cost the scene no row of their own.

The tool row is two groups that wrap as wholes: the status chips (presentation switch, Live, Connected, Graphics) and the actions (refresh, view switch, the panel buttons, Office tools). Every control in it uses the small type token (`--text-sm`) and the shared spacing tokens, so at 1440x900 the row stays one line even with a 17 px classic scrollbar (1423 px); narrower, the actions move under the chips together instead of leaving Office tools on a line of its own.

## Inside a building

The stage holds only the 3D room, its room and floor labels, the Back card (Back to campus, the building's name and its presence dot), the camera tools, the camera bar and the floor selector. No HTML block with numbers is drawn over it: the operations wall is in the selected room's panel, docked at the top of the side column (under the scene on narrow screens), and in the building panels below; the building's facts (type, people, floors, rooms, live meetings, orders today) are the first row of the building panels under the scene. The Live operations and Current flow strips are one docked row under the quick views, next to Team status; the in-progress and stuck counts are also in the Live flow bar. The room's own 3D wall screen (a texture, not HTML) still shows the wall's numbers.

## Panel by panel

Every panel has one header (`PanelSection.tsx`): title, one line on what it is for, a live chip, and a fold button (`aria-expanded`, kept per browser under `anum.hq.nav.fold.<panel>`). A folded panel keeps its header and its status.

| Panel | Where | Data | States |
|---|---|---|---|
| Total agents (`OfficeStatus.tsx`) | side column, or a chip row under the scene | the open workspace follows the presence stream while it is live (`data-source="stream"`); otherwise, and for every other workspace, the campus read (every 30 s at most); counts the unreported states as `-`; "Presence unavailable" when no record backs a count | reading, refused (who can open it), failed (with Try again), nobody reports (with Add an agent), partly reporting, stale |
| Quick moves | side column, or a tab | `hqCommands.navigateTo`, the same moves as the assistant | none available |
| Floor plan, People at their posts | side column tab inside a building | saved rooms and desks; presence index | empty floor, presence unavailable, paged after 6 |
| Selected building (campus) and room or person (building) | top of the column, else under the scene | the same panels as before, moved out of the scene | closes with its own button |
| Executive assistant (`AssistantPanel.tsx` around the shell's dock) | side column, else under the scene | the assistant's tools read, then the shell's workspace event stream (results arrive on it) | tools loading, tools failed (the panel opens Tools with the reason and Try again), event link closed |
| Team (`HqTeamPanel.tsx`) | the Team dialog (Office tools), and under a selected room | `/office/team`, read again whenever the live flow changes | loading, refused, failed (with the roster kept when one was read), empty (with Manage spaces) |
| History (`HqHistoryPanel.tsx`) | a drawer (bottom sheet under 760 px) | `/office-history`, read when opened, then the first page checked every 30 s; newer records are offered ("2 newer records", Show them), never pushed into the list | loading, refused, failed (with Try again), empty, paged, check failed (list kept) |
| Live flow (`HqFlowDock.tsx`) | thin bar under the quick views; the list opens as a sheet that rises from the bar | `/office/flow` | loading, denied, failed, offline with the last answer, empty with a next action |
| Campus map | a button in the camera rail (closed by default, remembered); opens beside the rail | the campus layout and the camera footprint | none |

## What "live" means

`LiveChip.tsx` shows one of Live, Reconnecting, Checking every N s, Connecting, Not connected, and the real time of the last confirmation (`Verified hh:mm:ss`, `Updated hh:mm:ss`, 24 hour digits). It uses the links the page already has: the presence stream (`usePresenceLive`) and the flow poll (`useOfficeFlow`). Nothing opens a second connection.

- A chip never says Live for data older than its window. Windows: presence 5 minutes (the server's own `STALE_AFTER`, `PRESENCE_TIMING.staleAfterMs`), flow 60 s (three missed polls of the 20 s live and 8 s fallback cadence), campus 90 s (three missed 30 s reads), history 90 s (three missed 30 s checks). Past the window the chip says Stale and shows the clock struck through.
- Where each chip's words come from (`lib/hqPanelStatus.ts`):
  - Total agents (`officeLive`, `totalsSource`): Live while the presence stream is live and has answered (the numbers follow it, `Verified` is the stream's own time); otherwise Checking every 30 s on the campus read, Reconnecting when a campus read failed after one succeeded, Not connected when none ever did, Connecting before the first. Stale after the presence window.
  - People at their posts: the presence stream's own link.
  - Live flow: the flow poll (`flowLive`).
  - Team (`teamLive`): Connecting until the roster answers, then the flow's words (it re-reads with the flow), Reconnecting when a re-read failed and the last roster is kept. `Updated` is the roster's own `as_of`; stale after the flow window.
  - History (`historyLive`): Connecting, then Checking every 30 s; never Live, because nothing streams into it. `Updated` is the time of the last successful read or check. On the Room history tab the timeline is not read, so the check itself (one read at once, then every 30 s) is the link.
  - Executive assistant (`assistantLive`): Connecting while its tools load, Not connected when they failed, then the shell's workspace event stream: Live while it is open, Not connected once it closed (the hint says to reload). It has no clock: no single record backs it.
- No time is invented: with no confirmation yet the chip has no clock.
- Updates arrive without a reload and without layout jumps: panels reserve their height while reading, the clock uses tabular digits, and no number animates.
- Reduced motion: nothing animates (the page already stops every transition under `prefers-reduced-motion`).
- Screen readers: one polite status region (`data-testid="hq-panels-live"`) says "The office is live", "Live flow is not connected" and so on, only when a link changes, never per update. The camera keeps its own region.

The pure rules (link to words, windows, stale, the announcement rule) are in `lib/hqPanelStatus.ts`, tested by `test/hqPanelStatus.test.ts`.

## Keyboard, landmarks, focus

- The side column is a labelled `aside` ("Floor plan, people and quick moves"); each panel is a `region` named by its title; the flow bar is a labelled `section`.
- Tab order follows the page: header, stage, side column (or the panels under the scene), quick views, status row.
- A drawer takes focus on its close button when it opens, closes on Escape and gives focus back to the button that opened it.

## Words and Arabic

Short English; the Arabic strings (`hqPanels.ar.ts`) have the same keys and placeholders (`test/i18n.test.ts`). Logical properties only, so the column, the camera rail and the map flip in right to left.

## Checks

- Unit: `test/hqPanelStatus.test.ts` (every chip rule above), `test/hqHistory.test.ts` (`newerRecords`).
- Browser: `e2e/hq-clear-panels.spec.ts` (five viewports; panels never overlap the stage; Total agents whole; quick views not covered by the flow bar; every building card visible and clickable; live status changes when a feed drops and recovers; Total agents follows the stream and falls back to the campus read; the Executive assistant, Team and History headers and chips; one header tool row at 1440 and 1423 px; fold and Hide side panels; Arabic; axe), `e2e/hq-history.spec.ts` (the 30 s check offers newer records and survives a failed check), `e2e/hq-labels.spec.ts` (the building facts are docked, not on the Back card) and the existing `hq-*.spec.ts`.
- By hand: render at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 in English and Arabic (`e2e/tools/hqPanels.shots.ts`).

## Not done

- The Back card, the floor selector and the camera bar inside a building stay glass controls on the scene (they are scene controls, not panels).
- Only the open workspace follows the presence stream: other workspaces' buildings on the campus keep the 30 s campus read (the page holds no stream for them).
- The History panel offers newer records on a 30 s check; it does not stream them.
