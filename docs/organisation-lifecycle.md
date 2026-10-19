# Organisation export and deletion

An organisation (tenant) can take all of its data out of ANUM and can leave ANUM for good. A workspace can be deleted on its own. This is what a customer company needs before ANUM is offered as a hosted service (P4 SaaS, [Business gap register](business/ANUM_BUSINESS_GAP_REGISTER.md)); a person's own data stays under [Privacy](privacy.md).

Implemented in `services/api/anum_api/tenant_lifecycle.py` (routes, jobs, the background runner), `tenant_lifecycle_tables.py` (the registry of every tenant table), `db/tenant_lifecycle_repository.py` (PostgreSQL), migration `0081_tenant_lifecycle`; web `apps/web/src/DataLifecycle.tsx` under Settings > Workspace; Flutter `apps/mobile/lib/features/lifecycle/` under Settings > Data export and deletion (English and Arabic, `lifecycle_strings.dart`; the Flutter Privacy screen is now in both languages too, `privacy/privacy_strings.dart`); the platform console's **Deleted organisations** pane (`apps/web/src/DeletedOrganisationsPane.tsx`); Keycloak account cleanup in `anum_api/keycloak_admin.py`.

## Who may do what

| Action | Who |
|---|---|
| See the page | every member; the controls show only for those allowed (`can_manage_organisation`, `can_delete_workspace`) |
| Export the organisation, schedule or cancel its deletion | an **organisation administrator** (`org_admin`) who is also an **owner** of the current workspace (`organization:lifecycle`) |
| Schedule or cancel deleting the current workspace | an **owner** of that workspace (`workspace:delete`) |

Every check is made by the API; clients only hide what the API would refuse. A refused call answers `403` with a sentence naming the role needed. Another organisation's jobs are never visible: every job row is under forced RLS on `anum.tenant_id`, and every route reads it inside the caller's own tenant.

## API

| Route | What it does |
|---|---|
| `GET /api/v1/organization/lifecycle` | The page: `can_manage_organisation`, `can_delete_workspace`, the texts to type (`organisation_confirmation` is the organisation id, `workspace_confirmation` the workspace name), `cooling_off_hours`, `export_link_ttl_seconds`, `export_retention_hours`, the newest 20 `exports` (empty for people who may not export), the open `organisation_deletion` and the open `workspace_deletion` of this workspace (or `null`). |
| `POST /api/v1/organization/lifecycle/exports` | Queues an export (`202`, a job in `queued`). `409` while another export is queued or running, or while the organisation is being deleted. |
| `POST /api/v1/organization/lifecycle/exports/{id}/link` | A signed download link valid for `ANUM_TENANT_EXPORT_LINK_TTL_SECONDS` (default 900): `{url, expires_at}`. `409` until the export is `ready`, `410` once it has expired. |
| `GET /api/v1/organization/lifecycle/exports/download/{token}` | The archive (`application/zip`, attachment, `Cache-Control: no-store`). The signed token is the credential, so it works in a plain link; it names the tenant, workspace, user and job, and with OIDC the person must still be an active member. `410` when the link expired or the export is gone. |
| `POST /api/v1/organization/lifecycle/deletion` `{confirm}` | Schedules deleting the whole organisation. `confirm` must equal the organisation id exactly (`422` otherwise, and nothing is scheduled). Answers `202` with the job in `scheduled` and `purge_after` = now + the cooling-off period. `409` when one is already scheduled or running. |
| `POST /api/v1/organization/lifecycle/deletion/cancel` | Cancels a scheduled deletion (`200`, status `cancelled`). `409` when nothing is scheduled or the purge has started. |
| `POST /api/v1/workspaces/current/deletion` `{confirm_name}` | Schedules deleting the current workspace; `confirm_name` must equal its current name. Same answers as above. Allowed in an archived workspace. |
| `POST /api/v1/workspaces/current/deletion/cancel` | Cancels it. |

A job is `{id, kind, status, workspace_id, requested_by, requested_at, purge_after, started_at, finished_at, cancelled_at, cancelled_by, expires_at, archive_bytes, archive_sha256, row_count, file_count, error}`. Kinds: `organisation_export`, `organisation_deletion`, `workspace_deletion`. Export statuses: `queued`, `running`, `ready`, `failed`, `expired`. Deletion statuses: `scheduled`, `cancelled`, `purging`, `completed`, `failed`.

## The export

A background job builds one zip archive (`anum.organisation-export/v1`):

| Path | Content |
|---|---|
| `manifest.json` | Format, organisation id, when it was made, every table with its domain, row count and the columns left out, the file count, and `not_included` sentences. |
| `data/<domain>/<table>.jsonl` | Every row of every tenant table, one JSON object per line. |
| `csv/<domain>/<table>.csv` | The same rows as CSV (nested values as JSON text; cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so a spreadsheet does not run them). |
| `files/<workspace>/<file id>/<name>` | The bytes of every workspace file. |

What it leaves out, and says so in `not_included`:

- **Secrets**: every column holding a ciphertext, token, token hash, password, credential, API key or secret, and embeddings (derived from text that is exported). Integrations have to be connected again after an import.
- **Rows private to one person** (conversations, voice transcripts, private memories, personal finance, private mailboxes, learning, preferences, in-app calls): row policies admit them only with that person's own context, and an organisation administrator is not that person. Each person downloads their own from Settings > Privacy ([Privacy](privacy.md)) before the organisation is deleted.
- The sign-in accounts (Keycloak) and mail kept on the mail server.

How it runs (the event-loop lessons in `CLAUDE.md`): the route only inserts the job and returns. The runner claims a queued job in one short transaction (`queued` to `running` with a lease), then reads each table in batches of `ANUM_TENANT_EXPORT_BATCH_ROWS` (default 500) by primary key, **each batch in its own short read transaction** as the application role inside the workspace's RLS context, writing to a temporary file. No transaction is open while files are copied, the zip is compressed or the archive is uploaded. The finished archive goes to object storage under `tenants/<tenant>/workspaces/<workspace>/lifecycle/<job>/export.zip` and the job becomes `ready` with its size and SHA-256. An archive larger than `ANUM_TENANT_EXPORT_MAX_BYTES` (default 512 MiB) fails the job with a message saying so; it is never truncated. A worker that dies mid-export loses its lease and the next pass starts the job again.

The archive is kept for `ANUM_TENANT_EXPORT_RETENTION_HOURS` (default 168); then the runner deletes it and the job becomes `expired`. Rows are read batch by batch, so the archive is not a point-in-time snapshot of a busy organisation.

Finding the workspaces of an organisation needs one read across workspaces: `anum_maintenance` gets a select policy on `workspaces (tenant_id, id)` that admits only organisations with an export `running` (migration `0081`). Every row read after that runs as the application role inside each workspace.

## Deletion

1. **Request.** The owner types the organisation id (or the workspace name) and confirms. The job is `scheduled` with `purge_after` = now + `ANUM_TENANT_DELETION_COOLING_OFF_HOURS` (default 168, at least 1). Nothing is deleted yet and everyone keeps working; the web and Flutter pages show a banner with the date and a **Cancel deletion** button.
2. **Cooling-off and cancel.** Until `purge_after` any person allowed to request it can cancel; the job becomes `cancelled` and nothing changes.
3. **Purge.** After `purge_after` the runner moves the job to `purging` and deletes, table by table in foreign-key order (children first), in batches of `ANUM_TENANT_PURGE_BATCH_ROWS` (default 1000), each batch its own short transaction. Memberships and invitations go first, so nobody can sign in to it while the rest is removed. File bytes (workspace files, Email Center attachments) and export archives are deleted from object storage before their rows. The runner repeats until a full pass finds nothing left, so rows a background job wrote meanwhile go too.
4. **Receipt.** What stays: the `tenants` row (renamed "Deleted organisation", status `deleted`, so the id is never reused), one workspace row kept as a tombstone (archived, renamed "Deleted workspace") and, in it, one `audit_records` row `organisation.deleted` (or `workspace.deleted`) with the job id, who asked, when, and row and file counts per table. No content, names or emails. The job row stays `completed` with the same counts.

5. **Sign-in accounts.** Right after an organisation's purge, ANUM removes its Keycloak accounts (`anum_api/keycloak_admin.py`): a person whose `tenant_id` / `tenant_ids` name only this organisation is deleted; a person who also belongs to other organisations keeps the account and only loses this organisation (their home `tenant_id` moves to another organisation they belong to when it pointed here). It is idempotent: a deleted user is gone (`404` counts as done) and a detached one no longer carries the organisation, so a second run changes nothing. Each run writes an `organisation.identities_removed` audit record in the receipt workspace (counts, or the error type), and the outcome goes on the console receipt (`removed`, `pending`, `failed`, `not_configured`). A failure is retried by the next passes (found through `anum_maintenance`) and marked `failed` after 5 attempts, which the console shows so the operator finishes it in Keycloak.
   ANUM reaches Keycloak with a confidential service-account client (client credentials, no user password) that needs only the `realm-management` roles `view-users` and `manage-users`. Without `ANUM_KEYCLOAK_ADMIN_URL`, `ANUM_KEYCLOAK_ADMIN_CLIENT_ID` and `ANUM_KEYCLOAK_ADMIN_CLIENT_SECRET` the cleanup is off, the receipt says `not_configured` and the operator removes the accounts. To set it up in the admin console: realm **anum** › Clients › Create client (`anum-lifecycle`, client authentication on, only "Service accounts roles" enabled) › Service accounts roles › Assign role › filter by clients › `realm-management` `view-users` and `manage-users`; copy the secret from Credentials into the API's secret store.
6. **Platform console.** The operator who onboarded the organisation sees it under **Deleted organisations** in the console (`GET /api/v1/platform/organizations/deleted`, read only): name, id, who asked and when, when the purge started and finished, rows and files removed per table, and the sign-in accounts outcome. It is read from `deleted_organizations` (below) through `anum_platform_reader`, which sees only the operator's own organisations and only these id, date and count columns.

Deleting a workspace removes every row with that `workspace_id` and its files; organisation-wide rows (branding, policies, the suppression list) stay.

`deleted_organizations` (migration `0081`) is the console receipt: written by the application role in the tenant's context when the purge starts (copying the operator, name and plan from `platform_organizations` before that row is purged) and completed when it ends. It is never exported or purged. Forced RLS on `anum.tenant_id`; `anum_platform_reader` reads it where `operator_user_id = anum.user_id`; `anum_maintenance` sees only the scope and status columns of receipts whose Keycloak cleanup is pending.

### How the purge stays inside RLS

The purge never uses `BYPASSRLS` or a `SECURITY DEFINER` function. It follows the [maintenance role](multi-tenancy.md#maintenance-role) pattern with one more narrow role, `anum_tenant_purger` (migration `0081`):

- `NOLOGIN`, no `BYPASSRLS`. Privileges: `DELETE`, and `SELECT` only on the scope columns (`tenant_id`, `ctid`, `workspace_id`, and `storage_key` of the two file tables), on every tenant table. PostgreSQL also checks privileges on tables that other row policies and triggers look into, so it may read a few lookup columns: `agent_profiles (id, tenant_id, workspace_id)`, `mail_account_grants (account_id, grantee_user_id, access)` and `retrieval_sources (source_type, source_id)` (`POLICY_LOOKUPS` in the migration; the PostgreSQL test fails when a new policy looks into another table).
- One policy per table, `lifecycle_purge_<table>`, `for all to anum_tenant_purger`, that admits a row only when its tenant is the session's `anum.tenant_id` **and** that tenant has a lifecycle job in status `purging` that covers the row (an organisation deletion, or a workspace deletion of the row's `workspace_id`). Without a purging job it admits nothing.
- Every restrictive policy on those tables (owner-only rows such as private memories, personal finance or mail) also admits `current_user = 'anum_tenant_purger'`, as migration `0057` did for `anum_maintenance`; the permissive purge policy still limits it to the purging tenant.
- The API switches to it with `SET LOCAL ROLE anum_tenant_purger` inside a transaction that already carries the tenant context, deletes one batch and commits. Granted to the API login **`WITH INHERIT FALSE, SET TRUE`** (`infra/helm/bootstrap-database.sql`).
- The job row itself (`tenant_lifecycle_jobs`) is changed only by the application role.

Finding due jobs across organisations uses `anum_maintenance` with column grants on `tenant_lifecycle_jobs (id, tenant_id, workspace_id, kind, status, purge_after, lease_until, expires_at)` and a policy admitting only jobs that need work.

## Every table is covered

`anum_api/tenant_lifecycle_tables.py` lists every table with a `tenant_id` column with its domain and how export and purge treat it. Two tests fail when a table is added without being listed:

- `tests/test_tenant_lifecycle.py::test_every_tenant_table_is_listed_for_export_and_purge` replays every migration with a recording stand-in for Alembic's `op` and compares the tables with a `tenant_id` column against the registry.
- `tests/test_postgres_tenant_lifecycle.py` compares the registry with `information_schema` of the migrated database, checks that every listed table has its purge policy and every restrictive policy admits the purger, and runs an export and both purges as the application role: tenant B keeps every row, and the purged tenant keeps only the tombstones and the receipt.

A new tenant table therefore needs a registry entry and, in its migration, the purge policy and grants (copy `_purge_policy` from migration `0081`).

## Audit

`organisation.export.requested`, `organisation.export.ready` (or `.failed`), `organisation.export.link_issued`, `organisation.export.downloaded`, `organisation.deletion.scheduled`, `organisation.deletion.cancelled`, `workspace.deletion.scheduled`, `workspace.deletion.cancelled` are written in the workspace where the person acted, with ids and counts only. The receipts (`organisation.deleted`, `workspace.deleted`) and the Keycloak cleanup (`organisation.identities_removed`, outcome `success` or `failure`) are described above.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANUM_TENANT_LIFECYCLE_ENABLED` | `true` | Runs the background runner in the API process. |
| `ANUM_TENANT_LIFECYCLE_POLL_SECONDS` | `30` | How often the runner looks for work. |
| `ANUM_TENANT_DELETION_COOLING_OFF_HOURS` | `168` | Time between the request and the purge (1 to 2160). |
| `ANUM_TENANT_EXPORT_LINK_TTL_SECONDS` | `900` | How long a download link works (60 to 86400). |
| `ANUM_TENANT_EXPORT_RETENTION_HOURS` | `168` | How long a finished archive is kept. |
| `ANUM_TENANT_EXPORT_MAX_BYTES` | `536870912` | Largest archive. |
| `ANUM_TENANT_EXPORT_BATCH_ROWS` | `500` | Rows per read transaction. |
| `ANUM_TENANT_PURGE_BATCH_ROWS` | `1000` | Rows per delete transaction. |
| `ANUM_KEYCLOAK_ADMIN_URL` | empty | Keycloak's base URL for the account cleanup (for example `http://keycloak:8080`). Empty: off. |
| `ANUM_KEYCLOAK_ADMIN_REALM` | `anum` | The realm whose users are removed. |
| `ANUM_KEYCLOAK_ADMIN_CLIENT_ID` | empty | The service-account client. |
| `ANUM_KEYCLOAK_ADMIN_CLIENT_SECRET` | empty | Its secret, from the secret store. |

With `ANUM_REPOSITORY_BACKEND=memory` (local runs and tests) the export and purge cover the in-process stores (tenants, workspaces, memberships, invitations, tasks, runs, approvals, events, audit records and workspace files); the full table registry applies with PostgreSQL.

## Out of scope: import

Importing an export into another ANUM (or back into this one) is deliberately out of scope. The archive is a portable copy for the organisation's own records and for moving to another system; secrets are not in it, rows private to one person are not in it, and ids would collide with live data, so an import would need its own design (id remapping, re-connecting integrations, consent of each person for their private data). Nothing in the API reads an archive back.

## Not done yet

- The archive is uploaded to object storage in one piece (the storage interface takes bytes), so the API process needs memory for one archive up to `ANUM_TENANT_EXPORT_MAX_BYTES`.
- Rows of a tenant table with a null `workspace_id` (possible only in `memories`) are not exported; an organisation deletion still removes them.
- With the memory backend only the core stores are covered (above).
