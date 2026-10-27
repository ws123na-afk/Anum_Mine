# ANUM MINE headquarters: page layout and side panels

The owner's request (27 October 2026): make the headquarters page clear, with the side panels and navigation easy to read and honest about what is live. This file is the spec for the page around the 3D scene. The scene itself is in [the extension points](ANUM_MINE_HQ_EXTENSION_POINTS.md), the labels in [labels](ANUM_MINE_HQ_LABELS.md), the flow in [live flow](ANUM_MINE_HQ_LIVE_FLOW.md) and the live link in [live scene](ANUM_MINE_HQ_LIVE_SCENE.md).

## The rule

Nothing but the scene's own controls sits on the scene. Panels live in their own column, under the scene, or in a drawer the person opened. The stage takes the height the viewport leaves after the page header above it and the quick views and status row below it.

```
header: title and tagline, then one row of status chips, view switch, Hide side panels, Office tools
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

## Panel by panel

Every panel has one header (`PanelSection.tsx`): title, one line on what it is for, a live chip, and a fold button (`aria-expanded`, kept per browser under `anum.hq.nav.fold.<panel>`). A folded panel keeps its header and its status.

| Panel | Where | Data | States |
|---|---|---|---|
| Total agents (`OfficeStatus.tsx`) | side column, or a chip row under the scene | the campus read (every 30 s at most) and the presence the server verified; counts the unreported states as `-`; "Presence unavailable" when no record backs a count | reading, refused (who can open it), failed (with Try again), nobody reports (with Add an agent), partly reporting, stale |
| Quick moves | side column, or a tab | `hqCommands.navigateTo`, the same moves as the assistant | none available |
| Floor plan, People at their posts | side column tab inside a building | saved rooms and desks; presence index | empty floor, presence unavailable, paged after 6 |
| Selected building (campus) and room or person (building) | top of the column, else under the scene | the same panels as before, moved out of the scene | closes with its own button |
| Executive assistant | side column, else under the scene | the shell's dock | unchanged |
| Live flow (`HqFlowDock.tsx`) | thin bar under the quick views; the list opens as a sheet that rises from the bar | `/office/flow` | loading, denied, failed, offline with the last answer, empty with a next action |
| Campus map | a button in the camera rail (closed by default, remembered); opens beside the rail | the campus layout and the camera footprint | none |

## What "live" means

`LiveChip.tsx` shows one of Live, Reconnecting, Checking every N s, Connecting, Not connected, and the real time of the last confirmation (`Verified hh:mm:ss`, `Updated hh:mm:ss`, 24 hour digits). It uses the links the page already has: the presence stream (`usePresenceLive`) and the flow poll (`useOfficeFlow`). Nothing opens a second connection.

- A chip never says Live for data older than its window. Windows: presence 5 minutes (the server's own `STALE_AFTER`, `PRESENCE_TIMING.staleAfterMs`), flow 60 s (three missed polls of the 20 s live and 8 s fallback cadence), campus 90 s (three missed 30 s reads). Past the window the chip says Stale and shows the clock struck through.
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

- Unit: `test/hqPanelStatus.test.ts`.
- Browser: `e2e/hq-clear-panels.spec.ts` (five viewports; panels never overlap the stage; Total agents whole; quick views not covered by the flow bar; every building card visible and clickable; live status changes when a feed drops and recovers; fold and Hide side panels; Arabic; axe) and the existing `hq-*.spec.ts`.
- By hand: render at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 in English and Arabic (`e2e/tools/hqPanels.shots.ts`).

## Not done

- Panels that other workstreams own keep their own headers: the Executive assistant dock, the History panel, the Team dialog. They sit in the same places but do not yet carry the shared header or a live chip.
- The Live operations strip and the Back card inside a building are still glass controls on the scene (they are scene controls, not panels); folding them into the status row is a later step.
- The campus totals refresh with the campus read (30 s); they are not driven by the presence stream.
