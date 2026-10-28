# ANUM MINE headquarters: the people (character kit, three looks, choosing the look)

Status: built in this change (web, one API change for Green Concrete, tests, docs). Web and API only; no mobile work.

The owner asked for the detail of the male and the female, and for characters that are better and cleaner. Until now a person in an office was a box figure, or (when the glTF models had loaded) a stylised 750 triangle chibi from a third party pack. This change replaces both with a
procedural **character kit**: a low poly humanoid with real proportions, a face you can read at close range, hair, hands, clothing and a seated or standing pose,
in three builds that follow the look the owner chose for that person. The style source is the owner-approved visual reference; only the style of the concept renders
is matched, never their panels or data.

## The rules this follows (the owner's standing data rules)

- **The build (masculine, feminine, neutral) comes only from the look the owner chose** for that person (the avatar style, migration 0110). Nothing reads a name, a description, a role title or anything else
  to decide it. A person without a chosen look is drawn from a hash of the record id, as before (see "Still simplified").
- Everything else (hair length, a head covering or cap, the garment set, glasses, a beard on the masculine build, body width, shoe colour) is a **deterministic draw from the record id**, purely cosmetic. It implies nothing about the person.
- **No invented people.** A figure is drawn only at a desk with an assigned agent (or a standing post a record backs). No crowds, no walkers, no figures at empty desks.
- A hi-vis vest appears only in a workshop or a warehouse room (the `field` role family: `roleFamily` reads the room kind) and only with the uniform outfit.
- Tests: a property test (`test/hqCharacters.test.ts`) shows that changing only the name never changes the look, a source scan fails if any file of the kit has a parameter or field called name, gender, sex, pronoun, ethnicity or race, and the variation rules are checked over thousands of ids.

## What was built

| Part | File |
|---|---|
| Triangle recorder, lofts, ellipsoids, boxes (pure, no three.js) | `apps/web/src/hqAssets/characters/mesh.ts` |
| Shape code, cosmetic variation from the id (pure) | `apps/web/src/hqAssets/characters/spec.ts` |
| The figure: body, clothing, head and face, hair and head coverings, hands, poses (pure) | `apps/web/src/hqAssets/characters/figure.ts` |
| The three.js bridge (its own lazy chunk, `bake-*.js`) | `apps/web/src/hqAssets/characters/bake.ts` |
| The shape code written by the entry chunk, the placement carries it | `apps/web/src/lib/hqModels.ts` (`figureShape`, `specOf`, `ModelPlacement.shape`) |
| A tiny hook in the model layer: shaped placements, level of detail, the lazy import | `apps/web/src/lib/hqModelLayer.ts` |
| All or nothing readiness for kit figures | `apps/web/src/lib/hqModelCache.ts` |
| Standing figures also note a slot now | `apps/web/src/hqAssets/interior/figures.ts` (`standingFigure`) |
| The Look control with a small preview in the Team panel | `apps/web/src/HqTeamPanel.tsx`, messages in `lib/messages/avatar.*.ts` |
| The optional look per person in "Add Green Concrete" | `apps/web/src/hq-green-concrete/`, `office_green_concrete.py`, `office_green_concrete_api.py`, `packages/contracts/src/officeGreenConcrete.ts` |

### The figure

About seven and a half heads tall (1.75 m standing, a hip joint 0.5 m above the floor when seated, the same frame as the interior figures so every chair, desk and keyboard still lines up).

- **Head**: one skull and jaw surface (squarer jaw for the masculine build, finer for the feminine, between for the neutral), ears, a nose, eyes (white and a dark iris, an eye colour from the id), brows (heavier on the masculine build), a mouth (fuller, in a rose colour, on the feminine build), optional glasses and optional freckles.
- **Hair** follows the avatar hair shape the owner can pick: spiky (short, cropped with spikes), parted (short, side parted), bun (tied), long (to the shoulder blades with a fall each side), swept (a medium bob with a fringe). **Head coverings** are variations from the id: a scarf in the accent colour (feminine and neutral builds, the face stays open, with a wrap under the chin and a drape to the shoulders) and a cap (masculine and neutral builds). A beard exists only on the masculine build.
- **Body**: shoulders, chest, waist and hips differ by build (the shoulder line is widest for masculine, narrowest for feminine), a neck, tapered arms with elbows, hands with a palm, fingers and a thumb, tapered legs with knees, shoes with a sole.
- **Clothing sets** (from the outfit the owner chose and the id): shirt, polo (short sleeves, bare forearms), jacket with lapels and a tie or scarf, knitted vest, blouse, skirt, and a long dress in the abaya style (feminine build). The uniform outfit in a workshop or a warehouse adds a hi-vis vest with reflective bands. Reception and operations roles keep their headset, security and finance their lanyard badge.
- **Poses**: seated at the desk: typing, reading (a sheet on the desk, head down), phone (in the hand at the ear), talking (a hand gesture), leaning (relaxed back). Standing: neutral, presenting, arms crossed, phone, walking. The elbow of each arm is solved from the wrist target (a two bone solution), so the hands meet the keyboard, the sheet or the ear.
- **Colours** are tinted per person with the same six roles as before (skin, hair, top, bottom, trim, accent): the owner's skin tone, hair colour, outfit and accent choices work unchanged. Parts that keep their own colour (eyes, shoes, a vest, a headset, a phone) do not tint.

### Triangle budget and level of detail

| Level | Used when | Measured maximum | Cap (unit test) |
|---|---|---|---|
| 0, close | up to 40 people on screen (18 on a software rasteriser) | 1,120 (average 967) | 1,200 |
| 1, busy | more than that | 485 | 500 |
| 2, crowded | more than 120 people (60 on a software rasteriser) | 345 | 360 |

The measured numbers are the maximum over every build, hair, pose, outfit and flag in the unit test sweep. A figure is built once per distinct shape and drawn with one instanced mesh per shape (one colour set per person), so a floor of twenty desks is about twenty thousand triangles at level 0 and never more than a bounded number of draw calls. Reduced motion changes nothing (the poses are static).

If the kit cannot load (a failed lazy chunk), the interior's own stand-in figures (about 380 triangles each) stay on screen, exactly as the glTF people did before. Kit figures are all or nothing: a room never mixes the kit with stand-ins.

## Choosing the look

- **Team panel** (Office tools, Team): every person card has a **Look** row with a small preview of the figure and three choices, Masculine, Feminine and Neutral. A click saves it (the same `PATCH /agents/{id}` as the avatar style dialog, only `avatar_style`; a hair shape the new look does not offer is dropped) and the figure in the room redraws. A person with no chosen look shows "Automatic: drawn from the record id" and nothing is checked. A failure says what failed. The full avatar style dialog (skin tone, hair, outfit, accent) is unchanged; its first group is now named **Look**.
- **Add Green Concrete** (Office tools): the preview has a **Look** column. Each person who will be created has a dropdown (Neutral, Masculine, Feminine), **Neutral by default**, with a small preview. The named Green Concrete people stay neutral until the owner chooses: nothing is guessed from a name. A choice makes the plan again (the hash covers it) and the confirm carries it.

### The API change for Green Concrete

No migration: the look is stored in the agent's existing `avatar` column (migration 0110), at creation.

- `GET /api/v1/office/green-concrete/preview` accepts `default_look` (`masculine`, `feminine` or `neutral`, for everyone not listed) and a repeated `look=<person key>:<style>`. Each person of the plan gains `appearance`: the look a NEW agent is created with, or null (not sent, or the agent already exists: an existing agent is never changed).
- `POST /confirm` accepts the same as `default_look` and `appearance` (a map of person key to style). The `payload_hash` covers the effective look of every person, so a confirm made for other looks than the ones shown answers 409 and creates nothing. An unknown person key or style answers 422; nothing is guessed.
- Without any look sent, nothing is stored (the previous behaviour). The web dialog always sends `default_look: neutral`.
- Audit: the `office.green_concrete.add` record gains `looks_chosen` (a count); it still holds ids and counts only, never a name or a look.

## Tests

Unit: `test/hqCharacters.test.ts` (shape code round trip, the build is exactly the chosen style, the property test that a name never changes the look, determinism, triangle caps over every build/hair/pose/outfit/flag, three different bodies, the face has eyes and tinted roles, variation rules, hi-vis only for field rooms, slots only with a seed, all or nothing readiness). API: `tests/test_office_green_concrete.py` and, on PostgreSQL as the app role, `tests/test_postgres_office_green_concrete.py` (the looks are part of the hash, stored for new agents only, audited without names). Browser: `e2e/hq-characters.spec.ts` (three looks render as three figures by the kit, the Team panel choice changes the figure and the canvas, automatic state, save failure, Arabic, five viewports with axe, the Green Concrete defaults, a per person choice, the hash binding, existing people, a viewer, Arabic, five viewports with axe) next to the existing `hq-people`, `hq-interior`, `hq-green-concrete` and `hq-performance` specs. Review screenshots come from `e2e/tools/hqCharacters.shots.ts` (not part of CI).

## Still simplified (said plainly)

- **The poses are static.** Nothing animates; a seated person does not type, turn or breathe. Any decorative motion is owner decision O1.
- **The camera mostly sees the backs of seated people** (the 3D view looks from the door side while they face their screens), so the face is readable only when the view is turned or zoomed close. At the campus and building distances a figure is a few pixels tall and the build, the hair and the clothing colours are what show.
- The faces are simple: no eyelids, lashes, teeth, wrinkles or skin texture. Hands have a palm, fingers as one block and a thumb, not separate fingers. Cloth does not fold or move; the abaya style dress and the scarf are shaped surfaces.
- The cosmetic variety is a bounded set (five hair shapes, three head coverings, seven garment sets, six shoe colours, five eye colours); two people can look alike in a large office.
- **An agent with no chosen look is still drawn with a build drawn from its id** (a mix of masculine, feminine and neutral). The existing test of six assigned agents drawn with different styles depends on that. The owner's rule that the default is neutral is applied where a record is created (Green Concrete stores an explicit neutral look); making every unchosen agent neutral is a decision for the owner, because it changes that test and the look of every existing office.
- Standing figures: the kit draws them and `standingFigure` now notes a slot, but no room kit places one (people appear only seated at desks), so no standing person is on screen yet.
- A seated person in a meeting room appears only when the person has a desk there; participants of a live meeting are not drawn as figures (see feature matrix row 5.9).
- The five Kenney person files (`public/hq/models/person_*.glb`) are no longer drawn. They stay in the catalogue and in the repository (their unit test and licence record are unchanged) until the owner decides to remove them.
- Level of detail is by the number of people on screen, not by the distance of each one from the camera.
