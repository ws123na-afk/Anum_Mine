# Tyre planning

Tyre planning reads the owner's own fleet tyre data (TyrePulse, or any Supabase or
Firebase project), maps it onto ANUM's tyre model and turns it into a fleet health
dashboard, removal forecasts, a purchase plan, alerts and reports. The assistant answers
tyre questions from the same numbers in chat, voice and tasks.

ANUM only ever **reads** the source. Nothing in tyre planning writes to it.

- API: `services/api/anum_api/tyre/` (`/api/v1/tyre`), migrations `0037_tyre_planning` and
  `0053_tyre_supabase_auth` (the `supabase_auth` connection kind).
- Owner SQL for TyrePulse: `infra/tyrepulse/supabase-anum-reader.sql` (undo:
  `infra/tyrepulse/supabase-anum-reader-rollback.sql`).
- Web: **Tyre planning** in the sidebar (`apps/web/src/TyreView.tsx`).
- Flutter: **Home > Tyre planning** (dashboard, tyres due, alerts) in `apps/mobile/lib/features/tyre/`; connecting, mapping and reports are on the web.

## Who can do what

| Action | Viewer | Member | Owner |
| --- | --- | --- | --- |
| Read dashboard, forecasts, purchase plan, alerts, reports; download CSV/PDF | yes | yes | yes |
| Sync now, generate and delete reports, schedule reports, request delivery, acknowledge alerts | no | yes | yes |
| Connect the source, enter the credential, discover tables, edit the mapping and assumptions, disconnect | no | no | yes |

Permissions: `tyre:read`, `tyre:operate`, `tyre:manage` (`anum_api/authorization.py`).
The server checks them on every route; the UI hides what the role cannot do and says
who can.

## Connecting

Pick one connection kind in **Tyre planning > Connection**. The credential is encrypted
with `ANUM_SECRETS_KEY` (Fernet) before it is stored and is never shown again: the page
shows only a hint (`user@host`, the service account email, or the last four characters
of a key). `python -m anum_api.rotate_secrets` re-encrypts it with the rest of the
stored secrets.

### TyrePulse sign-in (recommended for TyrePulse)

TyrePulse's row level security decides the organisation from the **signed-in user**:
`app_current_org()` is the `profiles.org_id` of `auth.uid()`, and `auth.uid()` comes from the
JWT that Supabase verifies. A plain database login has no JWT, so it sees nothing. The old
advice to give a login a permanent JWT (`alter role anum_reader set request.jwt.claims = ...`)
fails on Supabase with `42501 permission denied` (the dashboard's `postgres` user may not set
that on a role) and would be unsafe anyway: any login can `set_config('request.jwt.claims', ...)`
in its own session and so act as any TyrePulse user inside TyrePulse's SECURITY DEFINER
functions. So ANUM signs in as a **dedicated TyrePulse user** through Supabase Auth and reads
through Supabase's API with that user's token. Supabase signs the token; nobody can choose
another user.

What protects TyrePulse (nothing is disabled, nothing gets BYPASSRLS, no service_role key,
no blanket `grant execute`):

- TyrePulse's own policies still apply to the reader like to any user.
- `infra/tyrepulse/supabase-anum-reader.sql` adds RESTRICTIVE policies that apply only to
  registered reader users (everyone else is unaffected): no insert, update or delete on any
  table, and reads only of the five tyre tables (`vehicle_fleet`, `tyre_records`,
  `tyre_service_events`, `tpms_readings`, `suppliers`), only rows of the reader's own
  organisation, even if its profile is later made an administrator.
- A PostgREST pre-request guard refuses every reader request that is not a `GET` / `HEAD`
  of one of those tables: no `/rpc` functions (SECURITY DEFINER functions skip RLS), no
  GraphQL, no other tables. Supabase runs `GET` requests in a read-only transaction.
- ANUM itself sends only `POST /auth/v1/token` (sign-in and refresh), `GET /auth/v1/user`
  (to confirm the session belongs to an ordinary signed-in user) and `GET` on
  `/rest/v1/<table>`; anything else is refused before it leaves ANUM. A service_role or
  `sb_secret_` key is refused in every Supabase connection kind.
- The password, key and session never leave the API: the login is stored encrypted with
  `ANUM_SECRETS_KEY` (the only setting it needs, already required outside local development),
  the session is kept in memory and refreshed, and the page shows only
  `<e-mail> · key …<last 4>`.

Owner steps (about ten minutes):

1. **Run the SQL.** In the Supabase dashboard of the TyrePulse project open **SQL Editor >
   New query**, paste the whole of `infra/tyrepulse/supabase-anum-reader.sql` and press
   **Run**. It runs as the dashboard's `postgres` user, needs no superuser, and also removes
   the half-made `anum_reader` login from the old instructions. Run it again whenever
   TyrePulse adds tables (it is safe to repeat).
2. **Create the reader user in TyrePulse.** Through TyrePulse's own user management, add a
   user such as `anum-reader@<your company domain>` with a strong password, in the
   organisation ANUM should read, with a read-only role (for example **Reporter**), all the
   countries and sites ANUM should see, approved and not locked. Do not use an administrator
   (the next step refuses one) or your own account. Confirm its e-mail if Supabase asks
   (**Authentication > Users**).
3. **Register it.** In the SQL Editor run (with the e-mail from step 2):

   ```sql
   select * from anum_integration.register_reader('anum-reader@your-company.com');
   select * from anum_integration.verify_reader('anum-reader@your-company.com');
   ```

   Every row of `verify_reader` should say `pass`. It tries reads, other organisations'
   rows, a table outside the list, insert / update / delete and API calls as the reader,
   and rolls every attempt back.
4. **Copy the project details.** **Project Settings > API**: the project URL
   (`https://<project-ref>.supabase.co`) and the **publishable** key (`sb_publishable_...`,
   or the legacy `anon` key). Never the secret / service_role key: ANUM refuses it.
5. **Connect in ANUM.** **Tyre planning > Connection**, choose **TyrePulse sign-in
   (Supabase)**, enter the project URL, the publishable key, the reader's e-mail and its
   password, **Save connection**, then **Test connection**. It answers
   `Signed in as <e-mail> (read-only). Rows visible in the first page: ...`. Then
   **Discover tables**, **Use the TyrePulse preset** and **Sync now**.

If the test fails: "refused the reader's e-mail or password" (check them, or reset the
password in TyrePulse), "not confirmed" (confirm the user under **Authentication > Users**),
`HTTP 403` (the user is not registered, or the guard refused the request: run
`verify_reader`), rows `0` (the user's organisation, countries or sites in TyrePulse).

The SQL file also tells you if it could not install the pre-request guard (another
function already uses PostgREST's pre-request hook): add
`perform anum_integration.pre_request();` at the start of that function and run
`verify_reader` again. To undo everything run
`infra/tyrepulse/supabase-anum-reader-rollback.sql` the same way and delete or ban the
reader user.

### Supabase PostgreSQL

A read-only database login, for a Supabase or PostgreSQL database whose row level security
does **not** depend on a signed-in user. It does not work for TyrePulse (see above); a
TyrePulse login sees no rows.

1. In the Supabase dashboard open **Project Settings > Database > Connection string**
   and note the host and port (`5432` direct, or `6543` for the pooler).
2. In **SQL Editor** create a login that can only read the tables ANUM needs (choose your
   own name and a strong password; `nobypassrls`, `default_transaction_read_only = on`,
   `grant usage on schema` and `grant select on` those tables only).
3. In ANUM choose **Supabase PostgreSQL**, paste
   `postgresql://<user>:<password>@<host>:<port>/postgres?sslmode=require`, save,
   then **Test connection**.

ANUM opens every session with `default_transaction_read_only=on` and a statement
timeout, so even a login that could write cannot. Discovery lists any table the login
could write to and the page warns about it. Outside local development the host must
resolve to public addresses only, the port must be 5432 or 6543 and TLS is required;
the connection goes to the address that was checked. An operator can allow a private
host with `ANUM_TYRE_SOURCE_ALLOWED_HOSTS` (same `host` / `host:port` format as
`ANUM_MODEL_ALLOWED_HOSTS`).

### Supabase REST

Choose **Supabase REST**, enter the project URL (`https://<project-ref>.supabase.co`,
from **Project Settings > API**) and an API key whose role can read the tables. ANUM
sends only `GET` requests to tables under `/rest/v1` (never `/rpc`). The `anon` key
respects your RLS and usually sees nothing; a service_role / `sb_secret_` key bypasses RLS,
so ANUM refuses it. For TyrePulse use **TyrePulse sign-in** above. Calls go through the
same egress guard as model endpoints (public HTTPS only, pinned address, no redirects).

### Firebase (Firestore)

1. In the Google Cloud console for the Firebase project open **IAM & Admin > Service
   accounts > Create service account**, name it for example `anum-tyre-reader`.
2. Grant it only the **Cloud Datastore Viewer** role (`roles/datastore.viewer`), which
   can read Firestore documents and nothing else.
3. Open the account, **Keys > Add key > Create new key > JSON**, and download the file.
4. In ANUM choose **Firebase (Firestore)**, paste the whole JSON file into the
   credential box and, for a named database, its id (otherwise `(default)`).

ANUM signs a short-lived JWT with the key and exchanges it at Google's fixed token
endpoint (a key naming any other endpoint is refused); only `firestore.googleapis.com`
is called. Discovery lists top-level collections and infers fields from up to 25
documents each; nested maps become dotted fields (`specs.size`) and the document id is
`__id__`. Sub-collections are not read.

## Mapping

After **Discover tables**, the mapping editor shows ANUM's entities and fields and, for
each, a picker of your tables and columns:

| Entity | Required | What it holds |
| --- | --- | --- |
| Tyres | `id` | serial, brand, pattern, size, DOT, vehicle, position, status, fitment and removal dates, km and hours, removal reason, total km/hours, purchase cost, supplier, new tread |
| Vehicles | `id` | registration, fleet number, type, site, odometer, hours, status |
| Inspections | date, and `tyre_id` or `tyre_serial` | tread depth, pressure, target pressure, odometer, hours |
| Pressure readings | date, pressure, and `tyre_id` or `tyre_serial` | target pressure (TPMS) |
| Removals | date, and `tyre_id` or `tyre_serial` | reason, km, hours, tread at removal (optional: removal columns on Tyres also count) |
| Costs | date, amount | tyre, vehicle, category, supplier |
| Suppliers | `id` | name |

**TyrePulse preset.** When discovery finds TyrePulse's `tyre_records` and
`vehicle_fleet`, the suggestion is the ready-made TyrePulse mapping (fitted to the
columns that exist): `vehicle_fleet` (asset_no, registration_no, current_km...),
`tyre_records` as tyres and removals (serial_no, brand, size, asset_no, tyre_position,
status, issue_date, km_at_fitment, removal_date, removal_reason, total_km,
cost_per_tyre...), `tyre_service_events` as inspections (tread_depth, pressure,
event_date) and costs (cost, event_type), `tpms_readings` as pressure readings with
targets, and `suppliers`. Status values `Active`, `Removed` and `Scrapped` map to fitted,
removed and scrapped. Every field stays editable, and **Use the TyrePulse preset**
restores it. For other schemas the suggestion is a best guess by table and column names.

Units are set once for the mapping: tread in mm or 32nds of an inch, pressure in psi,
bar or kPa, distance in km or miles, and the currency code. ANUM stores mm, psi and km.

**Preview** applies the draft mapping to the first 20 rows of each table without saving
and shows what was kept, what was skipped and why (counts only, never row contents).
Rows attach to a tyre by id, or by serial and date when the source links by serial (as
TyrePulse does): the fitment record covering the reading's date wins.

## Sync

**Sync now** reads each mapped table once (only the mapped columns), applies the
mapping, stores the result as the workspace's snapshot, and re-evaluates alerts. Each
table is read up to `ANUM_TYRE_MAX_ROWS_PER_ENTITY` rows (default 50,000); beyond that
the page says the table was cut. Calls time out after `ANUM_TYRE_SOURCE_TIMEOUT_SECONDS`
(default 20). Scheduled reports sync first. Everything on the dashboard, in reports and
in assistant answers says when the data was synced.

## Method

All figures are computed in `anum_api/tyre/analytics.py` from the snapshot, the
assumptions and today's date, so the dashboard, a report and an assistant answer agree.

**Wear rate.** For each fitted tyre, a Theil-Sen line through its tread readings: the
median of the slopes between every pair of readings. One mis-typed reading cannot drag
it (a 25 mm reading among 14, 13 and 11 mm leaves the rate at 1 mm per 30 days). Distance
is the x axis when readings carry odometer values and the fitment odometer is known
(mm per 1000 km); otherwise calendar days (mm per 30 days). With one reading, the wear
since fitment from the new-tread depth is used. With none, the median rate of the same
brand and size (else size, else fleet, at least three tyres) is used and marked low
confidence.

**Projected removal.** Remaining tread = current tread (the fitted line at the latest
reading) minus the minimum tread. Days to removal = remaining / wear per day (distance
rates are turned into days with the vehicle's km per day, itself a Theil-Sen fit of its
odometer readings over time). The likely range uses Sen's 95% confidence interval of
the slope (Kendall's S variance); without one it is +/-50%.

**Confidence.** High: five or more readings and an interval within +/-25% of the
rate. Medium: three or more readings within +/-50%. Low: anything else, single
readings and group rates.

**Purchase plan.** Each projected removal inside the horizon falls in its month
(overdue ones in the current month) by size and brand (or the preferred brand for the
size). Quantity = due + safety stock (rounded up), minus matching tyres in stock (when
enabled). Unit price: the assumption for `size|brand`, else for the size, else the median
purchase cost of that brand and size in the data, else of the size; rows with no price
say so and are not added to the total.

**Assumptions** (owner-editable, with defaults): minimum tread 3 mm, warning band 2 mm,
pressure tolerance 10%, default target pressure (none), inspection interval 30 days,
abnormal wear 1.5x the brand and size median, cost anomaly 3x the category median, new
tread 16 mm, horizon 12 months, safety stock 10%, include low-confidence forecasts,
subtract stock, unit prices and preferred brands.

## Alerts

Evaluated after every sync and whenever the assumptions change:

| Kind | Raised when | Severity |
| --- | --- | --- |
| `low_tread` | latest tread at or below the minimum / within the warning band | critical / warning |
| `under_inflation`, `over_inflation` | latest pressure in the inspection interval outside the tolerance of its target | critical at twice the tolerance, else warning |
| `overdue_inspection` | fitted tyre not inspected for longer than the interval | warning past twice the interval, else info |
| `abnormal_wear` | wear rate above the factor times its brand and size median (five or more tyres) | warning |
| `cost_anomaly` | a cost in the last 90 days, or a recent purchase, above the factor times its median | warning |

One alert stays active per subject and kind. Members acknowledge (and reopen) alerts;
when the condition clears on a later sync the alert is resolved, and resolved alerts are
deleted after 90 days. An acknowledged alert that gets worse is reopened.

Each user's **Tyre alerts** switch in **Settings > Notifications**
(`notification_preferences.tyre_alerts`, default on) controls the notifications:

- **Phone push.** A sync (or an assumptions change) that raises new critical or warning
  alerts records one `tyre_alerts.raised` event with counts only; phone push
  ([Notifications](notifications.md)) sends "Tyre alerts" to every active member with
  push and this switch on, replacing the previous tyre push for the workspace. Tapping it
  opens the Flutter **Tyres > Alerts** screen.
- **Desktop.** With **Desktop delivery** and this switch on, and the browser's permission
  given (**Alerts > Desktop notifications**), the web app shows new critical and warning
  alerts as desktop notifications when the Alerts tab loads them.

## Reports

Kinds: fleet tyre health, tread wear rates, pressure compliance, cost per km and per
hour (by brand, size and vehicle), scrap and removal reasons, brand performance, tyres
due for removal, and the purchase plan. A report is generated on demand (**Reports >
Generate**) for a period, or on a schedule, and its content is frozen when generated.
Each downloads as CSV (formulas neutralised) or PDF. The PDF uses the built-in PDF
fonts, which cover Latin text only: Arabic text in the data prints as `?` there; the CSV
keeps it.

**Schedules.** **Reports > Schedule** creates an automation workflow with one
`tyre.report` step and a daily, weekly or monthly schedule in a time zone, visible in the
Workbench like any other. When it fires, the step runs as the person who created it (if
they are still an active member who may operate tyre planning), syncs, generates the
report once per run, and, when a channel and recipient were given, requests delivery.

**Delivery.** Sending a report outside ANUM goes through the generic delivery path
(`anum_api/delivery.py`, docs/tools-and-integrations.md#delivery): the connected email or
WhatsApp integration's tool is planned as a task run and decided by tool policy. Sending
externally is high risk, so it waits in **Approvals** for an approver, bound to the
payload hash; the report is attached by reference with its SHA-256, so what is sent is
exactly what was approved. Without such an integration the page says so and the report
stays downloadable.

## Assistant

Four read-only, low-risk tools (`anum_api/tyre/tools.py`): `tyre_fleet_summary`,
`tyres_due(month)`, `purchase_plan(months | month)` and `tyre_alerts`. Their summaries
carry the numbers, units and sync time.

- **Tasks:** the `anum.tyre-planning` skill triggers on tyre words; the planner picks
  the tool and arguments from the user's own prompt.
- **Chat:** a tyre question runs as a task with the same skill and planner and comes back
  as a `tyre` card listing tyres due, alerts or purchase-plan lines
  ([Conversations: tools in chat](conversations.md#tools-in-chat)).
- **Voice (and chat questions the skill does not pick up):** a tyre question (English or Arabic) runs the matching tool under
  `ToolPolicy` (an organization policy can deny it), audits it as `tool.call`, and gives
  the result to the model as a labeled, untrusted data block with an instruction to show
  the numbers. With the mock provider the tool's own summary is the answer.

## API

All under `/api/v1/tyre`; errors use the standard envelope.

| Method and path | Role | Purpose |
| --- | --- | --- |
| `GET /setup` | read | connection (hint only), mapping and problems, assumptions, last discovery and sync, field catalogue, presets, caller's rights, delivery channels |
| `PUT /connection`, `DELETE /connection`, `POST /connection/test` | manage | save (secret write-only, omit to keep), forget (snapshot and alerts too; reports stay), test |
| `POST /discover`, `GET /presets/{name}` | manage / read | tables and columns with a suggested mapping; a preset fitted to the schema |
| `PUT /mapping`, `POST /mapping/preview` | manage | save; dry-run on a sample |
| `PUT /assumptions` | manage | save and re-evaluate alerts |
| `POST /sync` | operate | read the source now |
| `GET /dashboard` | read | summary, alert counts, top alerts, soonest removals, removals per month |
| `GET /forecasts`, `GET /forecasts.csv` | read | per tyre; `month`, `due_by`, `state`, `confidence`, `q`, `limit`, `offset` |
| `GET /purchase-plan`, `GET /purchase-plan.csv` | read | `months` 1-36 |
| `GET /alerts`, `POST /alerts/{id}/acknowledge`, `POST /alerts/{id}/reopen` | read / operate | `status`, `kind`, `q`, paging |
| `POST /reports`, `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/export`, `DELETE /reports/{id}` | operate / read | generate, list (paged), read, CSV or PDF, delete |
| `POST /reports/{id}/deliver` | operate | email or WhatsApp through approval |
| `GET /report-schedules`, `POST /report-schedules`, `DELETE /report-schedules/{id}` | read / operate | scheduled reports |
| `GET /delivery-channels` | read | connected delivery channels |

## Data and privacy

Tables (all under forced tenant and workspace RLS): `tyre_connections` (one per
workspace, credential as ciphertext), `tyre_snapshots` (the mapped dataset),
`tyre_alerts`, `tyre_reports`. The snapshot is a copy of the owner's fleet data inside
their workspace; **Disconnect** deletes the connection, the credential, the snapshot and
the alerts (reports stay until deleted). The fixture used by the tests
(`services/api/tests/tyre_fixtures.py`) is synthetic and shaped like TyrePulse's tables.

## Not done yet

- Email and WhatsApp delivery depend on those integrations registering a delivery tool.
  The inbox's reply sender and the WhatsApp template sender do not fit (no attachments,
  bound to an existing draft or conversation; docs/tools-and-integrations.md#delivery), so
  until a new-message email send and a WhatsApp document send exist, reports are
  downloaded and sent by hand.
- Firestore sub-collections and collection-group queries are not read.
- The PDF export prints Latin text only.
- Scheduled reports run only with the automation scheduler on
  (`ANUM_AUTOMATION_SCHEDULER_ENABLED=true`); a missed fire runs once, as for every
  automation.
