# ANUM MINE headquarters: label cards and scene chrome

Owner feedback: the building must look like the concept renders, where every room carries a floating dark glass card (icon, room name, one line), seated agents carry a small white card with their state, and the screen has a Back to campus card, a floor selector, a camera bar and live strips. This document is the spec for that layer. It changes how things are drawn and placed. It adds no data source and no invented number.

Code: `apps/web/src/lib/hqLabels.ts` (placement), `lib/hqLabelModel.ts` (what the labels say and where they hang), `HqLabelCards.tsx`, `HqSceneStrips.tsx` (the Back to campus card, the floor selector and the strips, a lazy chunk), `hqLabels.css`, wiring in `BuildingView.tsx` and `HqStage.tsx`. Messages: `lib/messages/hqLabels.en.ts` and `hqLabels.ar.ts`.

## Data truth

| On the screen | Where it comes from |
|---|---|
| Room name | The room record |
| Room subtitle | Kind, desk count and department of the room record: `Office · 6 desks · Sales`. A department the message catalogue does not know is left out, never guessed. A live meeting adds "In a meeting" from the stored meeting `room_id` |
| Agent card | Only a desk with a registry assignment or a presence record. Name, role (department) and state come from presence; no record, no card. A private agent shows as "Private agent" |
| State chip | The presence state (Working, In meetings, Idle, Queued, Waiting for approval, Blocked, Paused) with its tone; stale evidence is marked "stale" and drawn dashed |
| Back to campus card facts | Registry counts (floors, rooms), presence total (People, "-" when presence is not reporting), live meetings, Orders only when the commerce module answered |
| Status dot of the building | Grey when presence is not reporting, red when an agent in it is blocked, green otherwise |
| Live operations strip | Presence totals plus "Stuck" from the flow snapshot; "Not connected" when presence has no answer |
| Current flow strip | `GET /office/flow` stage counts (Arrived, In an office, Waiting for a person, Blocked, Just finished); "Not connected" when no flow source is connected, "No work is moving right now" when it is connected and empty |

## Where a label hangs

- A label is anchored to the room centre at its floor's height: `y = floorIndex * FLOOR_PITCH` (`lib/buildingScene.ts`) plus the wall height and half a metre in the single floor view, or `STACKED_LABEL_Y` (1.9 m) inside the floor in the stacked view.
- Single floor view: only the active floor has labels. Every other floor is cut away, so it has none.
- Stacked view (All floors, the section view's default): room and agent cards stay on the open floor only, hung above the rooms like the single floor view, so the stack does not carry every room's card at once. Each floor also gets one tag on the edge of its floor nearest the eye, so no slab can cover it; clicking it shows that floor. The depth test (`isOccluded`, a segment against box test on the camera eye, with the floor slabs as boxes) and the stacked anchors (`STACKED_LABEL_Y`) are kept in `lib/hqLabels.ts` and `lib/hqLabelModel.ts` and unit tested, but the building view does not use them while the cutaway keeps the open floor visible. A card whose anchor leaves the stage is not drawn but stays in the page (invisible, drawn at the edge when it takes focus) for keyboard and screen reader users; agent cards and cards beyond their distance are hidden.
- The floor overview keeps its own room cards (kind, desks, agents).

## Density

Every label can take three densities, richest first: **full** (icon, name, subtitle), **compact** (icon and name) and **dot** (the icon alone, or the status ring of an agent). The layout runs per frame in priority order (selected, rooms, then seated agents; near before far) and gives each label the richest density that is free at its anchor. A room that fits at no density is hidden unless it is selected; a room that is crowded may move up to two steps; a seated agent never moves off its desk (it is shown there or gives way, because its position is the fact it states). Hover, keyboard focus and selection open the full card. The text stays in the accessibility tree at every density.

Cards also keep clear of the controls drawn over the scene (the Back to campus card, the camera tools, the dock, the open room panel): their rectangles are read when they change size, never per frame (`bindKeepOut`), and a card is never put under one. A card that has no free spot at any density is **crowded**: it stays at its anchor, unseen and not clickable, but in the tab order, and a keyboard focus brings it forward (`data-hidden="crowded"`). In a scene narrower than 641 px (a phone) no card is left out: one with no free spot sits on its anchor in its smallest density, even over another. A card hidden by the depth test (`data-occluded="true"`) or out of view is removed from the page's tab order; the room list, the room panel and the floor tags reach those.

## Chrome

- **Back to campus card** (top left of the scene): the back button, the building name, the status dot and a facts row. It replaces the crumb and counters row above the scene; the building and floor controls stay there.
- **Floor selector** (bottom right in a wide scene, above the camera bar in a medium one, left to the control bar above the scene in a narrow one): one button per floor, named with the stored floor name up to the first colon, and a switch for the stacked view. It does not replace the floor stepper, the floors rail or the All floors checkbox.
- **Camera bar**: rotate left and right, floor below and above, Inside, Floor overview, **Pan** (new: dragging moves the scene sideways while it is on), zoom, full screen. Words show only where the scene is wide enough; the icons keep their names and tooltips.
- The **Live operations** and **Current flow** strips are docked widgets in the panels under the scene at every width (they are no longer drawn over the stage); same numbers and words as before ([page layout](ANUM_MINE_HQ_PAGE_LAYOUT.md)).
- Full screen gives the scene the window height.

These steps follow the width of the scene, not the window: `bindKeepOut` writes `data-s641`, `data-s700`, `data-s1340` and `data-s1600` on the scene. (A CSS size container was tried and dropped: it let the scene width stop depending on its content and moved the page columns.)

## Accessibility, language, motion

- Every card is a button with a name that states the room and its subtitle (or the agent, role and state) and what a press does. The pressed state is `aria-pressed`. The cards are in the page's tab order; the list view and the floor rooms list remain the full alternative.
- English and Arabic: logical properties only, names in `dir="auto"`, plural subtitles per language (`hqLabels.desks`), the floor selector and the facts row mirror in a right to left page.
- Light and night: the cards are dark glass in both (AA against any scene colour behind them); the agent card is white.
- Reduced motion: no fade in, no transitions.

## Cost

No React render per frame. The cards are DOM nodes written by `placeLabels` only when a value changes, and a frame whose camera, size and labels did not change writes nothing. Sizes are measured once per label and density. Budgets are unchanged (lazy HQ chunk 140,000 bytes gzip, renderer 160,000).

## Tests

- Unit: `test/hqLabelModel.test.ts` (subtitle facts, anchors and floor height, slab occlusion (library), density with 60 rooms, agents that do not move), `test/hqLabelsPresence.test.ts` (placement).
- Browser: `e2e/hq-labels.spec.ts` (cards, density, stacked view, states: no agents, presence not connected, flow not connected, denied, Arabic, axe). Local render check: `e2e/hq-labels-look.local.spec.ts`.

## Not in this change

- Per department icons (the icon follows the room kind).
- A Team status card with Offline: presence has no offline state.
- The concept's room name subtitles are written by hand; ours are generated from the record, so they read plainer.
