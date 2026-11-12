# Contacts directory

Before this module a person could exist in a workspace up to four times: as a Phone calls contact ([Phone calls](calls.md)), as a Business contact under an account ([Business](business.md)), as a WhatsApp contact behind a conversation ([WhatsApp](whatsapp.md)) and as someone's Email Center address book entry ([Email](email.md#email-center)), each list with its own consent flags and do-not-contact marks. The Contacts directory puts one **person** record over them: who they are, every number and address they are known by, which source records they came from, what each module says about contacting them on each channel, and one workspace-wide **do not contact** mark per channel that every sending path obeys.

The directory is additive. The four lists stay as they are and keep working; the directory links to their records and never changes them. Code: `services/api/anum_api/contacts/` (`models.py`, `store.py`, `sources.py`, `service.py`, `api.py`, `jobs.py`, `importer.py`, `import_files.py`), `db/contacts_repository.py`, migration `0169_contacts_directory`; web `apps/web/src/ContactsView.tsx`; Flutter: see [Clients](#clients).

## Data model

Migration `0169_contacts_directory` (expand only, chained after `0168_files_folders_tags`) adds five tables, every one scoped to tenant and workspace with forced row level security, the `lifecycle_purge_<table>` policy and grants of [organisation deletion](organisation-lifecycle.md), and listed in `anum_api/tenant_lifecycle_tables.py` (export folder `contacts`):

| Table | Holds |
|---|---|
| `contacts` | one person: name, organisation, notes, where the record came from (`manual`, `calls`, `business`, `whatsapp`, `merge`), who added it and when |
| `contact_identifiers` | the person's phone numbers (E.164: `+` and digits) and e-mail addresses (lower case), with the value as it was typed as the label; one row each. A check constraint refuses an unnormalised value. The same value may belong to two people while a merge proposal is open |
| `contact_links` | the source record the person was built from: `call_contact`, `business_contact` or `whatsapp_contact` and its id; one link per source record (unique) |
| `contact_do_not_contact` | the workspace-wide block per channel (`phone`, `email`, `whatsapp`, `all`) with the reason, who set it and when; one row per person and channel |
| `contact_merge_proposals` | two people who share a phone number or an address under different names (`same_phone`, `same_email`, the shared value), `open` until a person merges or dismisses them |

Nothing from a source record is copied except its names, numbers, addresses and organisation: no notes, call logs or message text. The directory build also never stores anything about Email Center contacts (see [Privacy](#privacy)).

Rollback: `alembic downgrade 0168_files_folders_tags` drops the five tables and the maintenance grants; the four lists are untouched because the directory never wrote to them.

## Sources

`contacts/sources.py` reads the three linked lists as `SourcePerson` rows (name, phones, e-mails, organisation and a few module facts), in the caller's scope: with PostgreSQL as plain selects in the same RLS session, in the memory backend from the module stores filtered by scope.

| Source | Record | Name and identifiers | Organisation | Facts shown on the person page |
|---|---|---|---|---|
| Phone calls | `call_contacts` | name, the number, the e-mail | company | AI call consent recorded or not |
| Business | `business_contacts` with its `business_accounts` row | name, phone, e-mail | the account's name | consent basis, the contact's and the account's do-not-contact mark, role |
| WhatsApp | `whatsapp_contacts` with its conversation | profile name, the number from the WhatsApp id | - | whether the customer wrote first (`last_inbound_at`), the conversation to open |

**Email Center contacts are not a stored source.** They are personal (`mail_contacts` is owner only under RLS, [Email Center](email.md#email-center)), so copying them into a workspace directory would show one person's private address book to the whole workspace. Instead the person page reads, in the caller's own user scope, whether one of the person's addresses is in the caller's own mailbox contacts and links to it; nobody else sees it and nothing is written.

## Matching and the directory build

Matching is on normalised identifiers only: a phone number as `+` and digits (`00` and UAE `05x` numbers read as `+971...`, Arabic digits converted, through `suppression.normalize`) and an e-mail address in lower case. Names are never a reason to merge.

The build (`service.build_directory`) takes, per source, the records that have no link yet (at most `limit` per source and run, 500 by default) and for each one:

1. finds the people who already hold one of its identifiers;
2. if one of them has the same name (folded: case and spacing ignored) or no name to contradict it, the record **joins** that person: its identifiers are added, a missing organisation is filled in;
3. otherwise a **new person** is created from the record, and for every other person holding one of its identifiers an **open merge proposal** is written (one per pair, kept once dismissed);
4. the link is written. A record already linked is skipped, so the build is idempotent: running it again changes nothing until a source gains records.

Creating or editing a person by hand follows the same rule: an identifier another person already holds opens a proposal.

The build runs in two places:

- **Daily**: `("contacts", ".contacts.jobs", "run", True)` in the daily retention job (`python -m anum_api.voice_retention`, reported under `contacts`), or alone with `python -m anum_api.contacts.jobs [--dry-run] [--batch-size N]`. It discovers the workspaces that have contacts as `anum_maintenance` ([Multi-tenancy](multi-tenancy.md#maintenance-role)): migration `0166` grants that role `select (tenant_id, workspace_id)` on `call_contacts`, `business_contacts` and `whatsapp_contacts` and a select policy, so it sees scopes and nothing else (no name, number or address; `tests/test_postgres_contacts.py` checks it). It then builds each workspace as the application role inside that workspace's RLS context, with `batch_size` records per source and run, and reports counts only (`workspaces`, `scanned`, `linked`, `people_created`, `proposals_created`, `incomplete_workspaces`). People it creates carry the actor `system:contacts_directory`.
- **Rebuild now** on the page (`POST /directory/rebuild`): the same build for the caller's workspace, at once; the answer says what it linked and whether anything remains (`complete`).

## Merging and proposals

A proposal is decided by a person: **Keep A** or **Keep B** merges the other into the one kept; **Different people** dismisses it (a dismissed pair is never proposed again). Merging (`POST /{id}/merge {"other_id"}`) locks both rows in id order for its short transaction (no outside call happens in it), so two merges of the same pair run one after the other and the second answers `404`. It moves the other person's identifiers (duplicates dropped), links and do-not-contact marks (the kept person's mark wins per channel) onto the kept person, appends their notes and fills a missing organisation, closes the pair's proposal, re-points the other person's open proposals at the kept one, and deletes the other record. The merge is one audit record (`contacts.merged`: the kept and removed ids and the counts moved). The source records are untouched: a merge changes which person they are linked to, nothing in their modules.

## Do not contact

`PUT /{id}/do-not-contact {"channel", "reason"}` marks a person for `phone`, `email`, `whatsapp` or `all` (`201` new, `200` renewed with the new reason); `DELETE /{id}/do-not-contact/{channel} {"reason"}` clears one (owners only, the reason goes to the audit log). The mark is the directory's own, workspace-wide record; the modules' own flags (the Business contact's and account's `do_not_contact`, the Phone calls AI opt-out) and the tenant's [contact suppression list](privacy.md#contact-suppression) keep working as before and are shown next to it.

### Enforcement

`contacts.service.do_not_contact_block(context, channel, value)` normalises the number or address, finds the people holding it in the caller's workspace and answers the refusal sentence when any of them is marked for that channel or for all ("Sara Ahmed asked not to be contacted by phone (Asked us to stop calling, recorded 2026-11-10), so ANUM did not send this. Owners can review it under Contacts."). Each refusal writes `contacts.send_refused` (the person id and channel, never the number). A failure to read the directory propagates: nothing is sent on a guess. The sending paths that call it:

| Module | Where | Channel | Answer |
|---|---|---|---|
| Phone calls | `POST /calls/provider/calls` (a browser call) | `phone` | `409` with the sentence |
| Phone calls | `service.check_ai_call`: when an AI call is asked for and again when the approved call is placed | `phone` | `409` when asked; the placed call is refused and its request marked failed with the sentence |
| Email Center | `POST /mail/drafts/{id}/send`: every To, Cc and Bcc address | `email` | `409`, the draft stays a draft |
| WhatsApp | `POST /whatsapp/conversations/{id}/send` (text and templates), checked in the worker-thread unit before anything is queued | `whatsapp` | `409` |
| Business outreach | `outreach.gate`, run when a draft is queued and again when the approved message is sent | the draft's channel | `422` with the issue when queued; the send is held like any other gate failure |

Tests: `tests/test_contacts.py` (one test per path) and `tests/test_postgres_contacts.py` (browser call and WhatsApp under RLS).

## Import from a CSV file

Owners bring people in from a spreadsheet or another address book (`contacts/importer.py`). Two calls, both owner only (`business:manage`): a file brings many people's personal data into the workspace at once, so it is held to the same rule as the export.

1. **Preview** (`POST /import/preview {"csv", "file_name", "mapping"?}`, or with `"format": "xlsx"` and `"file_base64"`, or `"format": "vcf"` and the text in `"csv"`: see [Excel and vCard files](#excel-and-vcard-files)) reads the file (CSV: UTF-8 text, a byte order mark is dropped, comma, semicolon or tab separated, the first row names the columns; at most 2,000 people, 60 columns and 2 MB; `413` past the row limit, `422` with the reason for a file that is empty, binary, has no rows or repeats a column name). Without a mapping it suggests one from the column names in English and Arabic (name, or first and last name joined; organisation; notes; up to four phone and four address columns; a do-not-contact column); a file whose names say nothing gets no rows and the owner picks the columns. Every row is checked like the person form (lengths, control characters, numbers with their country code or UAE `05x`, addresses; a cell may hold several values separated by `;` or `|`) and compared with the directory by its numbers and addresses only (never by name):

| Status | When | Suggested | Other choices |
|---|---|---|---|
| `new` | No one in the directory holds the row's numbers or addresses | Add as a new person | Skip |
| `match` | Exactly one person holds them, under the same name (or no name to contradict it) | Add to that person | Add as new, skip |
| `possible_duplicate` | Someone with another name holds one, or several people do | Add as a new person (with a merge proposal, as typing it by hand would) | Add to one of the people found, skip |
| `duplicate_in_file` | An earlier row of the file has the same number or address (`duplicate_of`) | Skip | Add as a new person |
| `invalid` | A value is not a number or an address, the row has no name, number or address, or a do-not-contact value is not `phone`, `email`, `whatsapp`, `all` or yes/true/1 (`problems` says which in English, and `issues` gives the same as codes with the value, `not_phone`, `not_email`, `bad_dnc`, `empty`, `control`, `too_many`, `name_long`, `organisation_long`, `notes_long`, which the web words in English or Arabic) | Skip | None |

   Each row lists the people it matched, what they share and their do-not-contact marks. The answer carries the counts per status and a `fingerprint` of the file and mapping. Nothing is written and nothing is audited (`tests/audit_probe.py` lists it as reading only).
2. **Apply** (`POST /import/apply {"csv", "file_name", "mapping", "fingerprint", "decisions"}`, with `format` and `file_base64` as in the preview) takes the same file, format and mapping (another file, format or mapping is `409`, preview again) and a decision per row (`row`, `action`, `target_id` for an update; the web sends every row as the owner left it; a row the call leaves out takes its suggestion). It classifies every row again against the directory as it is now and checks every decision before writing anything: a row that is not skipped must be valid and its action must be one of its choices, and a person to update must still exist and still hold the row's number or address. One failure refuses the whole import with `409` naming the rows ("Nothing was imported. Row 6: ...") and nothing is written. Then it writes all of it in the same unit of work (one PostgreSQL transaction): new people as `manual` people created by the owner, updates that add the numbers and addresses the person lacks (up to ten each), fill a missing organisation and append the row's notes, without changing the name. Merge proposals open as for any person typed by hand.
3. **Do not contact is preserved.** An import never clears or replaces a mark: a person updated from the file keeps every mark and its reason, and a row that marks a channel adds that mark only where the person has none, with the reason "Marked do not contact in the imported file" and the owner as who set it. Imported marks stop the sending paths at once ([Enforcement](#enforcement)).

The answer counts what was added, updated and skipped, the marks added and the proposals opened, and one audit record `contacts.imported` holds the same counts (never a name, number, address or the file name). The file itself is never stored. Imported people are `manual` people, because the previous release's code reads `source` as one of the five known values; a new value would be a contract change.

### Excel and vCard files

The same preview and apply read two more formats (`contacts/import_files.py`); after reading, the rows go through exactly the CSV path (mapping, checks, statuses, decisions, all or nothing, do-not-contact kept, counts-only audit). The format is named in the body (`format`: `csv`, the default, `xlsx` or `vcf`), each file in its own field (a file in the wrong field is `422`), and the `fingerprint` covers the format, so a file previewed as one format cannot be applied as another.

- **Excel (.xlsx)**, sent as base64 in `file_base64` (at most 2 MB, `413` above). The workbook is hostile input and is read with the checked OOXML reader of [document text extraction](files.md#document-text-extraction): entry count, total uncompressed size (48 MB) and compression ratio checked before reading, encrypted entries refused, bounded part reads, XML through defusedxml with any `DOCTYPE` refused (no entity can be declared or expanded). Only the **first sheet** is read; its first non-empty row names the columns and the rows keep the sheet's numbers. **Formulas are never evaluated**: a formula cell gives the result Excel last saved, as text; an error cell is empty. A workbook with macros (`xl/vbaProject.bin`, an .xlsm renamed) is refused unopened, and an old binary workbook (.xls) or a password-protected one is refused with how to save it as .xlsx or CSV. Cells are placed by their reference, so the empty cells Excel leaves out never shift a row, and a whole number stored as a number (a phone number typed without `+`) is written out in full, never as `9.7E+11`. At most 60 columns and 2,000 people, as for CSV.
- **vCard (.vcf)**, sent as text in `csv` (versions 2.1, 3.0 and 4.0; at most 2 MB of text). Folded lines are joined, each `BEGIN:VCARD` to `END:VCARD` is one person, and only `FN` (or `N` as prefix, given, middle, family and suffix), the first part of `ORG`, `NOTE`, every `TEL` and every `EMAIL` are read, with escapes undone, `tel:` and `mailto:` dropped and version 2.1 quoted-printable values decoded; photos, keys, URLs and every other field are ignored. The table has the columns Name, Organisation, Phone, Email and Notes, so the suggested mapping always fits; several numbers or addresses are joined with `|`, which the CSV path splits again (at most ten each, as for a person). Row 2 is the first card. A card without `END:VCARD`, more than 400 fields in a card, a line over 20,000 characters, a file with no card or more than 2,000 are refused with what to do.

## Consent per channel

`GET /contacts/{id}` answers `channels`: for `phone`, `email` and `whatsapp`, the person's identifiers on it, the directory's mark, `can_contact` (false when the directory marks the channel or the tenant's opt-out list blocks every message to it) and `facts`, each read live from its source at request time and never stored:

| Fact source | States | Read from |
|---|---|---|
| `directory` | `blocked` (the reason) | the person's do-not-contact mark |
| `suppression` | `blocked` (`purpose:masked`) | the tenant's [contact suppression list](privacy.md#contact-suppression), by hash |
| `consent_records` | `granted` (the lawful basis), `withdrawn` | the tenant's [consent records](privacy.md#consent-records-for-campaigns), by hash (e-mail and WhatsApp) |
| `calls` | `granted` or `none` (consent to AI calls), `blocked` (an AI call opt-out) | the linked Phone calls contacts and the workspace's opt-out list (phone) |
| `business` | `granted` (the consent basis), `unknown`, `blocked` (the contact's or account's mark) | the linked Business contacts (e-mail and WhatsApp) |
| `whatsapp` | `customer_wrote` (when), `no_message_yet` | the linked WhatsApp conversation |

## API

All routes are under `/api/v1/contacts` and take the usual tenant context. Every state-changing route writes an audit record with ids and counts only (`contacts.created`, `.updated`, `.deleted`, `.merged`, `.dnc_set`, `.dnc_cleared`, `.proposal_dismissed`, `.directory_built`, `.exported`, `.imported`, `.send_refused`); `tests/test_contacts.py` walks them through `audit_probe.AuditProbe` and checks no name, number or address appears.

| Route | Permission | What it does |
|---|---|---|
| `GET /` `?q=&source=&dnc=&sort=&offset=&limit=` | read | Paged people (25, at most 100), searched, filtered, sorted and paged in one SQL statement (a search's `%` and `_` match literally). `q` searches name, organisation, numbers and addresses; `source` is `manual`, `calls`, `business`, `whatsapp` or `merge`; `dnc` is `any`, `phone`, `email`, `whatsapp` or `none`; `sort` is `name`, `updated` or `created`. Answers `counts` (people, people marked, open proposals, people by source) and what the caller may do |
| `GET /export` | manage | CSV of every person (name, organisation, numbers, addresses, sources, marked channels, dates); cells guarded against spreadsheet formulas, so a number starting with `+` is prefixed with `'` |
| `POST /` | write | Create a person: name, organisation, notes, up to 10 numbers and 10 addresses; `422` names a value that is not a number or an address |
| `GET /{id}` | read | The person with their linked records read live (a deleted source says so), the channel summary, their open proposals and the caller's own mailbox contacts for their addresses |
| `PATCH /{id}` | write | Change name, organisation, notes; `phones` or `emails` replace that list |
| `DELETE /{id}` | manage | Remove the person, their identifiers, links and marks; the source records stay and come back at the next build |
| `POST /{id}/merge {"other_id"}` | write | Merge the other person into this one (above) |
| `PUT /{id}/do-not-contact`, `DELETE /{id}/do-not-contact/{channel}` | write; clear: manage | The mark per channel (above) |
| `GET /proposals?state=open` | read | Paged proposals with both people |
| `POST /proposals/{id}/dismiss` | write | Different people; `409` once decided |
| `POST /directory/rebuild` | write | The bounded build for this workspace (above) |
| `POST /import/preview` | manage | What importing a CSV, Excel or vCard file would do, row by row ([Import from a CSV file](#import-from-a-csv-file), [Excel and vCard files](#excel-and-vcard-files)); writes nothing |
| `POST /import/apply` | manage | Imports the previewed file with a decision per row, all of it or nothing; audited as `contacts.imported` |

**Permissions, and why.** *Read* is any of `call:read`, `business:read`, `whatsapp:read` or `email:read`: whoever may read one of the four lists may see the people built from them. Members and owners hold all four; viewers hold none, so a viewer gets `403` with "Contacts hold personal data from Phone calls, Business, WhatsApp and the Email Center, so viewers cannot see them. Ask a workspace owner to make you a member." *Write* (create, edit, merge, mark do not contact, decide proposals, rebuild) is `business:write`, the permission that already lets members change Business contacts and mark them do not contact. *Manage* (clear a mark, delete a person, export, import) is `business:manage`, as Business's own do-not-contact clearing is owner only and the opt-out list's CSV export is owner only; importing is held to the export's rule because a file brings many people's personal data in at once (a member who tries is told "Only workspace owners import into the directory ... Ask a workspace owner to import it."). The web and phone only hide what the server would refuse; every check is on the server.

## Clients

- **Web** (`apps/web/src/ContactsView.tsx`, `lib/contacts.ts`, `lib/contactsApi.ts`, `contacts.css`, messages `lib/messages/contacts.{en,ar}.ts`): **Contacts** in the sidebar's main group after Phone calls (`#contacts`; `#contacts-duplicates`; a person at `#contacts?person=<id>`), on the shared page head with Add person, Rebuild now and, for owners, Export CSV; four figures (people, marked do not contact, possible duplicates, linked sources); tabs **People** (search, source, do-not-contact and sort filters, a table with numbers and addresses, sources and status, Show more paging), **Possible duplicates** (each pair with Keep A, Keep B and Different people) and, for owners, **Import people** (`#contacts-import`: choose a CSV, Excel (.xlsx) or vCard (.vcf) file, checked for type and size before it is read, an Excel file sent as base64; the suggested columns with a choice per field (name, last name, organisation, notes, two phone and two address columns, do not contact) and **Check again**; the counts per status; the rows 50 at a time with a status filter and a search, each with what ANUM found (matches, what they share, their marks, repeats, problems) and **What to do** (and which person to add it to when several match); **Import** asks for confirmation with the totals, then says what was added, updated, skipped and marked, with links to the people and the possible duplicates; a member who opens the link is told who imports). The person page: linked records with a link into Phone calls (`#calls-contacts?contact=`), Business (`#business-accounts?account=&contact=`) and WhatsApp (`#whatsapp/<conversation>`), the caller's own mailbox contact (`#email/server`), one card per channel with the facts above and the do-not-contact controls (mark with a channel and reason; clear with a reason, owners), the open proposals, Edit, Merge with (a search picker) and, for owners, Delete. The command palette searches people too (kind `contact`). Loading, empty with the next action, no match, error with retry, permission denied and paging on every list; English and Arabic (`ct.*` keys), right to left. Tests: `test/contacts.test.ts`, `e2e/contacts.spec.ts` (the import flow, its refusals and axe on the preview in both languages, with axe on the other tabs too) and the UI gate's `#contacts`, `#contacts?person=`, `#contacts-duplicates` and `#contacts-import` entries.
- **Flutter** (`apps/mobile/lib/features/contacts/`): see [Mobile](#mobile) below.

## Mobile

The phone has a **Contacts** screen (Workspace menu, route `/contacts`): search over name, organisation, number and address, the list 25 at a time with Show more, the do-not-contact chips, and a person page with their numbers and addresses, sources, the per-channel facts and the do-not-contact controls (mark with a channel and reason; clear with a reason, owners). **Add person** (members and owners) opens a form checked like the API (name, organisation, notes, numbers and addresses one per line); **Possible duplicates (N)** lists the open pairs 25 at a time with Keep one (merge) and Different people; the person page has **Edit**, **Merge with...** (a search picker, then a confirmation) and, for owners, **Delete person**; owners **Export CSV** through the system save dialog ([File downloads](mobile.md#file-downloads)). English and Arabic (`contacts_strings.dart`). Code: `contacts_manage.dart` with the screen and controllers. Owners also have **Import people** (`contacts_import.dart`): pick a CSV, Excel (.xlsx) or vCard (.vcf) file with the system picker (checked for type, emptiness, size and UTF-8 text before anything is sent; the picker's temporary copy is removed), see the preview as a summary (the rows per status, what applying would add, update and skip, the columns ANUM chose and the first five rows that cannot be imported with their reason), confirm, and import with ANUM's suggestion for every row (the same `/import/preview` and `/import/apply`, an empty `decisions` list, so each row takes its suggestion). The phone does not choose other columns or another action for one row: reviewing up to 2,000 rows one by one is a desk task, and the screen says to import on the web for that; a file whose columns ANUM cannot tell gets no Import button and the same pointer. Loading, a refused file (in the phone's words), the API's refusals (a member, a changed directory) and the result are each said; the list loads again after an import. Tests: `apps/mobile/test/contacts/contacts_test.dart`.

## Privacy

- **What is read from where, and who sees it.** The directory stores names, numbers, addresses and organisations of Phone calls, Business and WhatsApp contacts, and links to those records: data every member can already read in those modules (viewers can read none of them, and cannot read the directory). The consent facts are read live from those modules, from the tenant's opt-out list and consent records (by hash, masked), and from the workspace's AI call opt-outs. Email Center contacts are private and are never stored or shown to anyone but their owner (above). Notes typed on a person are the workspace's, like Business notes.
- **Do-not-contact marks** are a stronger, workspace-wide version of the modules' own flags: they stop every sending path for that channel. Clearing one is owner only and audited with the reason.
- **Audit.** Every change is audited with ids and counts, never a name, number or address; a refused send is `contacts.send_refused`.
- **Download and delete my data.** People are workspace records, like the source contacts they link: they are not in a person's export and stay when a person deletes their data. Organisation and workspace deletion purge all five tables.
- **Retention.** The directory keeps a person until merged or deleted; it does not follow the sources' retention. When a source record is deleted (a WhatsApp contact purged by its retention, a Phone calls contact removed), the link reports `found: false` and the person keeps the identifiers that record gave them.

## Not done yet

- The directory does not merge or delete people on its own when a source record is deleted; the link shows the record as gone until a person edits or merges.
- No export of the consent facts (the CSV carries the directory's own columns). An import reads one Excel sheet (the first) and no OpenDocument (.ods) or old Excel (.xls) file; a file over 2,000 people must be split. The phone imports with ANUM's suggestions only (choosing columns and a decision per row is on the web).
