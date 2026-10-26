# ANUM MINE floor overview: the whole floor on one screen

Status: built in this change. Web only; no API, no migration.

Opening a building used to show one isometric cutaway, framed tightly, so a 38 m floor of the [starter campus](ANUM_MINE_STARTER_CAMPUS.md) looked like one room. Two changes fix that.

## Fitted camera

The building's home camera (`buildingFitCamera` in `apps/web/src/lib/hqCamera.ts`) is a pinhole fit: every corner of the footprint, from the active slab to the top of the cutaway walls, must be inside the picture for the isometric angle (yaw 35, pitch 44, field of view 40) and the stage's own aspect ratio (read from the stage by `useAspect`). A narrower stage (the fold control, a phone) puts the camera farther, a wide one closer. A 12 percent margin keeps the corners off the edge and clear of the bottom toolbar. Tests: `apps/web/test/hqFirstRun.test.ts` checks the corners at six aspect ratios and that the old flat factor could not have fitted the 38 m floor.

## Floor overview

A mode of the building view: the camera looks at the whole active floor from high above (pitch 62, north up, `floorOverviewCamera`), the stage takes the height the page can spare (`clamp(440px, 100dvh - 280px, 900px)`) and **every room of the floor** carries a card with its name, kind, desk count and agents seated. A card is a button: it selects the room (the room panel opens) and the camera stays on the whole floor. Under the scene a plain list repeats the same facts, so every room is reachable by keyboard and screen reader even when cards would overlap: "Rooms on {floor}", totals, and a way back.

Ways in and out:

- The **Floor overview** button in the bottom toolbar of the building view, and **Whole floor** at the top of the floor rail (also in the rail's drawer on narrow screens). Both show `aria-pressed` and stay in step (the state lives in `HeadquartersLive`).
- Another floor (rail, the floor control, the previous and next buttons) keeps the mode and shows that floor.
- **Escape**, either button, or leaving the building ends it. Choosing **Inside** a room ends it too (the Inside camera has its own framing).
- The fold control ("Hide side panels") and any resize frame the floor again for the new stage shape. The navigation strip is untouched; its Home stop returns to the overview camera while the mode is on.
- Only the room cards are drawn in this mode (desk and agent cards are left out so rooms stay readable); the room panel still lists the people.

Numbers shown are the saved registry (rooms, desks) and the presence list (agents seated); an unknown value is never invented.

## Budgets

Lazy HQ chunk 130,338 bytes gzip (limit 135,000), renderer 153,706 (limit 160,000), HQ CSS 11,831 (limit 17,000). The first-run panel and the assistant card are separate lazy chunks (2.5 kB and 1.3 kB gzip).
