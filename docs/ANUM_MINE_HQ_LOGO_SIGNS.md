# ANUM MINE headquarters: logo and brand signs

Date: 27 October 2026. API (migration `0117`), web and tests. No mobile.

The owner asked for "something more exciting and not that team like real world signature rather than this cover comment type names", and chose: **logo and brand signs that the owner uploads**. Each building gets a sign in a real brand style, built from the owner's own logo and colours, one company at a time. A building without an upload keeps a clean lettered sign with the building's real name. **ANUM MINE never invents a logo or a brand mark for any company.** The only picture on a sign is a file the owner uploaded for that building.

## What the owner gets

| Part | What it does |
|---|---|
| Logo | One picture per building (PNG, JPEG or WebP, up to 1 MB). It is checked, cleaned and resized on the server, then drawn on the building's sign in the 3D campus exactly as given, on a white plate so a dark logo never disappears |
| Brand colour | One colour per building (`#rrggbb`). It is the sign's accent: the bar, the frame, the glow or the whole board, depending on the style. A contrast check tells the owner if the colour is hard to read; the lettering then falls back to a readable colour and the owner's colour stays on the accent |
| Sign style | `standard` (no style chosen: the campus sign, a dark board; your colour becomes its bar), `plain` (light board, colour on the lettering and the bar), `plaque` (dark board, frame in the colour), `illuminated` (glowing lettering on a dark board), `banner` (the colour is the whole board). Only the last four are stored values; clearing the style gives the standard sign |
| Without an upload | The standard campus sign with the building's real name (the Arabic name, right to left, in Arabic mode): a dark board, the small ANUM MINE wordmark and the name. Nothing else is drawn. A colour alone recolours the standard sign's bar and mark; a chosen style without a logo gives that style's lettered sign (the name only, no mark) |

## Owner steps: upload a logo for each building

1. Open the headquarters, then **Office tools**, then **Manage spaces**, then **Open visual builder**. Only the workspace owner (permission `workspace:manage`) can change a sign; everyone else sees how it looks and who can change it.
2. Choose the building in the builder's building list (top bar), then open the **Floor** tab. Scroll to **Building sign** (below the building's names and shift hours).
3. Press **Choose a logo file** (or drop the file on the box). The preview at the top of the section shows the sign with your logo before anything is sent. A file that cannot be used is refused with the reason (SVG, GIF, animated, larger than 1 MB, not a picture, damaged).
4. Press **Upload this logo**. The sign on the campus updates for everyone who has the headquarters open.
5. Type or pick your **brand colour**. The line under it says whether it is readable on the chosen style (contrast number). Pick a **sign style** (Standard keeps the campus sign).
6. Press **Save colour and style**.
7. To undo: **Remove logo** (keeps the colour and style), or **Reset to lettered sign** (removes the logo, the colour and the style after one confirmation).

Repeat for each building. A logo is stored per building, so ANUM HQ, ANUM 360 Commerce, Tyre Pulse and any company you add each get their own.

A building in another workspace is drawn with the standard sign until you open that workspace and set its sign there. Signs belong to the office registry buildings of a workspace; the campus reads no other workspace's registry.

### Buildings whose names are generic

The names below come from the starter campus master list (`office_starter_campus.py`). They describe a function, not a company, so a brand sign for them will read like a department. Renaming is already possible in the builder (Floor tab, Building: **Name** and **Name in Arabic**) and nothing was renamed by this change.

| Building | Reads as |
|---|---|
| Corporate Services | A function (HR, finance, legal, procurement) |
| Knowledge & AI | A function |
| New Ventures | A function |
| Personal Office | A function |
| Career & Employment | A function |

Company names already: ANUM HQ, ANUM 360 Commerce, Tyre Pulse (and Green Concrete when added). The owner's own campus may have renamed any of them; the builder shows the live names.

## How it is built

### Data (migration `0117_building_logo_signs`, expand only)

* `office_buildings.sign_style` (nullable, one of `plain`, `plaque`, `illuminated`, `banner`), `brand_color` (nullable, `#rrggbb` lower case) and `logo_sha256` (nullable, 64 hex). Check constraints validate all three in the database too.
* New table `office_building_logos` (`png bytea`, `sha256`, `width`, `height`, `byte_size`, `uploaded_by`, timestamps), primary key `(tenant_id, workspace_id, building_id)`, composite scope foreign key to the building with `ON DELETE CASCADE`, forced row level security, the tenant purge policy of `0097` and an entry in `tenant_lifecycle_tables.py`. The bytes live in their own table so a building list never carries them.
* The previous release never reads the new columns or the table, so a rollback is safe.

### API (`office_logo_api.py`, `office_logo_image.py`)

| Call | Rules |
|---|---|
| `PUT /api/v1/office-spaces/buildings/{id}/logo?expected_revision=N&input_mode=click` | Raw image bytes as the body. `workspace:manage`; only `input_mode=click` (a spoken or typed answer is refused with 403); the building's current revision (409 when stale). Answers the updated building |
| `GET /api/v1/office-spaces/buildings/{id}/logo` | The stored PNG, `task:read`. `Cache-Control: private, no-cache`, `ETag` (the SHA-256), `304` on a matching `If-None-Match`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, `Cross-Origin-Resource-Policy: same-origin`. There is no public URL |
| `DELETE /api/v1/office-spaces/buildings/{id}/logo?expected_revision=N&input_mode=click` | Back to the lettered sign; 404 when the building has no logo |
| `PATCH /api/v1/office-spaces/buildings/{id}` | `sign_style` and `brand_color` (a hex colour or `null`), like any other building field. `logo_sha256` cannot be set through it |

Upload pipeline: the 1 MB body cap, the real type from the leading bytes (never the header or the file name; SVG and GIF are named and refused), the workspace malware scanner when one is installed (fails closed when it is required and down, like every upload), the declared canvas read from the header before any pixel is decoded (at most 4096 pixels a side and 12 million pixels), animation refused, then a decode and a **re-encode to a fresh PNG of at most 512 by 512 pixels built from the decoded pixels only**. EXIF and GPS, colour profiles, text chunks, trailing bytes and anything hidden in the original are gone. Corrupt, truncated or hostile files get one fixed sentence; the decoder's own text never reaches the response. Sentences are localized to Arabic like every other route (`localization.py`).

Every change writes one audit record (`office.building.logo_set`, `office.building.logo_clear`, `office.building.update`) with before and after of the style, colour, logo hash and revision, the `input_mode` and the picture's size: ids and hashes only, never the file bytes. The `office.layout_changed` event goes out after the commit, so other open browsers refresh.

### Web

* **Builder**: `hq-logo-signs/BuildingSignSection.tsx`, a lazy chunk of the Floor tab, with the preview (`SignPreview.tsx`, the same painter as the campus), the file check (`logoFile.ts`: first bytes, size), the colour contrast (`signModel.ts`: WCAG contrast, 4.5 to 1 for lettering and 3 to 1 for a frame), the style radios, remove and reset. States: loading, empty (with the next step), error (with retry), denied (who can do it), refused file (the reason), stale (reload). Keyboard and screen reader: real buttons and radios, one live region for the contrast, `role="alert"` for problems, the drop box is a named group.
* **Campus**: `hq-logo-signs/campusSigns.ts` is a lazy chunk fetched only when a building has a sign set up. It loads the logos of the nearest buildings first (at most 16, three at a time), fetching each with the person's own credentials, decodes each to a bitmap of at most 256 pixels on its long edge (about 4 MB at the very most) and draws the sign into the **existing** sign atlas cell with `signPainter.ts`. No new texture is made. Any failure (404, bad picture, offline) leaves that building on the lettered sign. The page reports `data-sign-brands` and `data-sign-logos` on the campus stage for QA.
* Colour and style change only the sign's own accents. The logo is never recoloured.

### Dependency

The API gained one direct dependency, **Pillow 13.0.0** (HPND licence, ships wheels), pinned exactly in `services/api/pyproject.toml` like every direct dependency. It is the only decoder used on an upload, with the formats limited to the sniffed one (`formats=[...]`) and the pixel limits applied before decoding. `docs/files.md` still holds for file text extraction (pypdf and defusedxml, no Pillow).

## Security review

Rows are in [the security review](ANUM_MINE_HQ_SECURITY_REVIEW.md), "Logo upload": content sniffing, decompression bombs, no SSRF (the server never fetches a URL), storage isolation and cache headers.

## Tests

`test_office_logo_image.py` (re-encoding, sniffing, bombs, animation, metadata), `test_postgres_office_logo_signs.py` (as the app role: RLS isolation between tenants, validation, re-encode, size limit, scanner hook, click only, revision, audit without bytes, cascade), `test_tenant_lifecycle.py` (the table is registered), `test_localization.py`, web `test/hqLogoSigns.test.ts`, `e2e/hq-logo-signs.spec.ts` (builder: upload with a fixture PNG, preview, bad files, denied, Arabic, five viewports, axe; campus: the 3D sign switches from lettered to logo).

## What is not done

* Another workspace's building has no registry building in this workspace, so it keeps the lettered sign.
* The logo is drawn into the building's sign cell of the sign atlas. The facade sign and, today, the board in front of the building (see [billboards](ANUM_MINE_HQ_BILLBOARDS.md)) are textured with that cell, so both show it. When the quiet campus change gives the boards their own live atlas, the logo stays on the facade sign and the board's header can take the brand colour; that is a follow-up in that change's board painter, not done here.
* Looked at on software GL, not on a real GPU.
* A logo is shown at most 256 pixels on its long edge in the sign (a cell is 512 by 128), whatever the stored size.
* There is no crop or background removal; the owner uploads the picture as it should appear.
