# ANUM MINE Arabic names of buildings, floors and rooms

Owner request: building and floor names had no Arabic version, so Arabic mode showed English names. Every registry record that has a name now has an optional Arabic name, and Arabic mode shows it everywhere.

## Data

- `office_buildings.name_ar`, `office_floors.name_ar` (migration `0114_building_floor_name_ar`, nullable `varchar(100)`, expand only, no backfill) and `office_rooms.name_ar` (since 0105). The English `name` stays the stable, searchable one: ids, sort order, module detection (a room named Command Center) and the starter campus matching all use it.
- API: create bodies take an optional `name_ar`, `PATCH` sets it or clears it with `null`, reads return it (`null` when none). The value is one line, 1 to 100 characters, no control characters; blank is refused. An absent `name_ar` on create stores `NULL`, never an empty string, and an idempotency key sent before this change still replays.
- Templates: a floor template captures the floor's `name_ar` and every room's; making a floor from it keeps them. If the person gives the new floor a new English name, the old Arabic name is dropped (it would be wrong) unless a new `name_ar` is sent. A building template carries its floors' Arabic names; the new building takes the optional `name_ar` of the request. Copying a room keeps its Arabic name.
- Layout history: a room's `name_ar` is part of the snapshot and of its hash, so a restore brings it back and a change of it is a revision. The floor's own name and Arabic name, like its English name, are not part of the snapshot.
- Audit and events: the audit record of an edit keeps the before and after values like every other field; the `office.layout_changed` event carries ids only, never text. The Add Arabic names audit record carries ids and counts only.
- The campus endpoint (`GET /api/v1/office/campus`) returns `name_ar` on each floor.
- Starter campus: all 8 buildings, 18 floors and 62 rooms have professional Arabic names ([starter campus](ANUM_MINE_STARTER_CAMPUS.md)). Campuses made before 0114 get them with the owner's Add Arabic names click (same document).

## Web

- One helper, `apps/web/src/lib/registryNames.ts`: `pickName(en, ar, locale)` returns the Arabic name in Arabic mode when there is one, else the English name, so a name is never blank. English mode always shows the English name. `arabicMissing()` is used only by the builder.
- Used by: campus cards, signs and details, the campus list and its search, the building title, floor selector, floor rail, floor overview cards, room cards and the room panel, the navigation strip quick views and plan, the history panel, the flow and team panels (by room id), the first-run and starter campus lists (already bilingual), and the command navigation (a floor, room or building is found by its Arabic or its English name).
- Builder: the Floor tab has Arabic name fields for the floor and the building, the room details have one (all right to left, `lang="ar"`, mixed text isolated), and a short note says that Arabic mode shows the English name until an Arabic one is added. The Rooms list marks rooms without an Arabic name in Arabic mode. This hint is shown only in the builder.
- 3D signs: the sign atlas (`lib/hqTextures.ts`) draws a sign that holds Arabic right to left: the canvas shapes the letters (`direction = "rtl"`), the text is right aligned beside the mark, which moves to the right edge, and letter spacing is off (it breaks joined letters). The face is the self-hosted Noto Sans Arabic of `public/fonts/fonts.css`; a canvas does not wait for a web font, so the atlas is drawn once, the font is loaded, and it is drawn again. Latin signs are unchanged. The wording of the plates (RECEPTION, LEVEL n) comes from `hq.sign.*` messages in both languages.
- Not covered: the legacy Manage spaces page (`OfficeSpaceLiveManager.tsx`) is English only and has no Arabic fields; workspace and agent names are not registry records and show as typed.

## Add Arabic names

See [starter campus, Add Arabic names](ANUM_MINE_STARTER_CAMPUS.md#add-arabic-names). Steps for an existing campus: open the headquarters, Office tools, Set up my campus, scroll to Add Arabic names, read the list, click the button. It fills only empty Arabic names whose English name is a starter name, never replaces a name, and a second click does nothing.

## Tests

API unit `tests/test_office_starter_campus.py` (names, web fixtures kept equal to the planner); PostgreSQL as the app role `tests/test_postgres_office_arabic_names.py` (columns and forced RLS, create, edit and clear, validation, templates, layout restore, starter campus names, the action); web unit `test/registryNames.test.ts` (the helper, fallbacks, navigation model, command matching, signs), `test/hqStarterCampus.test.ts`; browser `e2e/hq-arabic-names.spec.ts` (Arabic mode on the campus, building and floor overview and the builder at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844, and the action).
