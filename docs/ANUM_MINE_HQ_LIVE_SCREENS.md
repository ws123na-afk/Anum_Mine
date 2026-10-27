# ANUM MINE HQ live screens

The screens inside the rooms of an open building show what is running, drawn onto the 3D meshes as canvas textures. The Operations wall (the video wall of a command centre or operations room, and a mounted panel in any room whose module is the live flow, such as Logistics) shows the same real numbers as the Operations wall block. A desk monitor shows the state, the record type and the elapsed time of the record behind its agent. Nothing is invented: a value that is not available says "Not connected".

## How it is drawn

- One shared canvas atlas per open floor: 256 x 128 cells for monitors (at most 48) and 512 x 256 cells for walls (at most 4). The largest atlas is 2048 x 1024 (8 MB); a floor with a few desks uses 2048 x 256 or less.
- The furnishing notes each monitor and video wall (`MeshBuilder.screens`, world corners). The lazy chunk `hq-screens/` lays one textured quad (material class `screen`, `MAT.screen`) a few millimetres in front of each; the room geometry never changes when a face does. The renderer only gains the class and `setScreens(canvas)`.
- The chunk loads when a building scene with screens opens (`lib/hqScreens.ts` is the only screens code in the headquarters chunk). Only the open floor has screens.
- Only changed cells are drawn, each at most once a second (`ScreenAtlas.update`); a one second tick keeps elapsed times and Stale current. The canvas is uploaded again, nothing is rebuilt. The tick stops while the tab is hidden.
- Distance gate: a monitor shows text only within 7 m of the camera and a wall its full face within 45 m (15 percent hysteresis); beyond that it is a plain face of the state colour, or the four big numbers for a wall.
- Reduced motion: the content and its updates are the same; the scan line and the cursor stand still.
- Arabic: labels come from the catalogue, text is shaped right to left, digits follow the page's locale.
- The photographic tier needs nothing: the screens are unlit (emissive) faces.

## What a screen shows

Operations wall: the four numbers (open tasks, blocked work, pending approvals, running now) and agents by state, the newest flow items as type, stage and age (never a title), "Updated hh:mm:ss", and a mark: **Live** only while the presence stream is connected, **Stale** after 90 seconds, **Not connected** when there is no read. In a Tyre Pulse building the wall also shows the figures of the Tyre fleet cards with the same labels (Vehicles, Tyres fitted, Tyres in stock, Below minimum, Overdue inspections, Due in 30 days); with the feed off it says "<building name>: Not connected" (or "No access").

Desk monitor: only for a desk with a person. Working shows "Working", "Task run" and "Running 5m" counted from the real start of the task the flow places at the desk, a scan line while the presence is fresh; in a meeting "Meeting"; waiting for approval "Approval" and "Waiting 30m" from the approval's start; queued, blocked, idle, paused and unknown are dim; a shift desk off shift says "Off shift" and is dark whatever work waits. There is no progress bar: no record carries step counts.

## Every field drawn

| Field | Source |
| --- | --- |
| `wall.tiles` | WallModel.tiles: open tasks, blocked work, pending approvals, running now (the live flow read), the same numbers and labels as the Operations wall block |
| `wall.agents` | WallModel.agents: agent total and by state (the presence read), labels hq.status.* |
| `wall.flow` | OfficeFlowSnapshot.items: kind and stage of the newest items by `since`, and their age; never the title |
| `wall.time` | WallModel.asOf (the flow read time); Live only while the presence stream is connected; Stale after 90 seconds |
| `wall.tyre` | TyreLive (the tyre dashboard the Tyre fleet cards read): vehicles, tyres fitted, in stock, below minimum, overdue inspections, due in 30 days, labels hq.tyre.* |
| `wall.building` | the open building name (registry), only in the "Not connected" line of the tyre block |
| `monitor.state` | the desk visual state (registry shift plus the presence status), label hq.status.* or Off shift |
| `monitor.record` | a fixed word for the record type behind the state: task run, task queued, meeting, approval |
| `monitor.elapsed` | OfficeFlowItem.since (task start) or OfficeFlowItem.waiting.since (approval start) of the item the flow places at the desk |
| `monitor.verified` | the presence record last_verified_at (Verified hh:mm:ss) and its stale flag |

`SCREEN_READS` lists the properties the model may read; `test/hqScreens.test.ts` reads every function through a recording proxy and fails on any other property, and feeds poisoned inputs (names, titles, ids) that must never reach a string.

## Limits

- Budget: the renderer gains the `screen` class and `setScreens` only; the drawing is its own lazy chunk.
- The wall of a building other than Tyre Pulse shows no module figures (commerce figures are not drawn on the wall).
- A floor with more than 48 monitors keeps the plain look on the rest.
- The Operations wall HTML block over the stage is left to the page panels work.
