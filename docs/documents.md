# Document vault and scanning

The Document vault keeps identity papers, contracts, insurance, statements, receipts and warranties in one private place on top of [Workspace files](files.md): the same object storage, malware scan, type and size checks, versions, quota and signed links, with no second store. It adds document records (category, dates, issuer, country, tags, an encrypted number), a private access class, duplicate detection, a per-document access log, expiry reminders, a trash with retention, an iPhone Shortcut upload and local OCR whose results wait for the person to confirm them.

It is module 2 of the owner's five-module expansion (4 Nov 2026). Receipts never post money: the ANUM Finance ledger stays the only financial source of truth, and a document links to a finance transaction only through the finance links foundation (see [Links](#links)).

Code: `services/api/anum_api/vault/` (`models.py`, `store.py`, `service.py`, `api.py`, `devices.py`, `links.py`, `jobs.py`, `privacy.py`, `ocr_parse.py`, `ocr_client.py`, `ocr_service.py`). Migration `0133_document_vault`. Web: `apps/web/src/VaultView.tsx`, `src/lib/vault.ts`, `src/lib/vaultApi.ts`, `src/vault.css`, `src/lib/messages/vault.{en,ar}.ts`. Contracts: `packages/contracts/src/documents.ts`. Help: [English](help/vault.md), [Arabic](help/vault.ar.md).

## Contents

- [Owner decisions (defaults the owner can change)](#owner-decisions-defaults-the-owner-can-change)
- [Who can do what](#who-can-do-what)
- [Storage and isolation](#storage-and-isolation)
- [Identity and account numbers](#identity-and-account-numbers)
- [Adding documents](#adding-documents)
- [Downloads and links](#downloads-and-links)
- [Versions, trash and retention](#versions-trash-and-retention)
- [Expiry reminders, trash and retention](#expiry-reminders-trash-and-retention)
- [Scanning and extraction](#scanning-and-extraction)
- [The OCR service](#the-ocr-service)
- [Scanning from an iPhone](#scanning-from-an-iphone)
- [Links](#links)
- [The assistant](#the-assistant)
- [Privacy](#privacy)
- [API](#api)
- [The web page](#the-web-page)
- [Mobile](#mobile)
- [Tests](#tests)
- [Not in this release](#not-in-this-release)

## Owner decisions (defaults the owner can change)

Made for the owner on 4 Nov 2026 and recorded here; each one is a setting or a small code change, not a migration:

| Decision | Default | How to change it |
|---|---|---|
| Identity and account numbers | Encrypted at rest with `ANUM_SECRETS_KEY` (Fernet, rotated by `rotate_secrets`), lists show the last four only, the full number only to the document's owner on request (logged) | Keep the number field empty to store none |
| The assistant | Vault documents are never indexed for the assistant. The owner marks one workspace document "The assistant may read this document" to index it | Per document, off by default; private documents can never be marked |
| OCR | Local open-source Tesseract (English and Arabic) in a separate container with no ANUM secrets, internal network only, off until the operator enables its compose profile. No paid or hosted document AI | `ANUM_OCR_SERVICE_URL` and `ANUM_OCR_SERVICE_TOKEN`; profile `ocr` |
| Extracted values | Proposed only, pending until the person confirms or corrects them | Not configurable |
| Receipt reading | Off until a workspace owner turns on "Read receipts and invoices when they are added" | Vault settings |
| Identity dates | Off until a workspace owner turns on "Read the dates of IDs, passports and insurance"; numbers are never read | Vault settings |
| New documents | Private | Choose Workspace when adding, or edit later |
| Reminders | 30 days before expiry (workspace), then 7 days, 1 day and on the day | Vault settings, or per document |
| Trash | 30 days before a deleted document is deleted for good | Vault settings |

## Who can do what

| | Viewer | Member | Owner |
|---|---|---|---|
| See workspace documents | No (personal data) | Yes | Yes |
| See a private document | Only if it is theirs or shared with them, whatever the role | Same | Same |
| Add documents, scan from an iPhone | No | Yes | Yes |
| Change, trash, restore a document | No | Their own | Their own, and any workspace document |
| Share a private document, change who can see it, set its number | No | The person who added it | The person who added it |
| Vault settings and categories | No | No | Yes |

Permissions `document:read`, `document:write`, `document:manage` (`authorization.py`). A private document is invisible to a workspace owner who is not its owner: the API answers 404 and the row policy hides the row.

## Storage and isolation

- **One store.** Each document is a row in `vault_documents` pointing at a `workspace_files` row; the bytes are the Files object (key per workspace, file and SHA-256). Versions are Files versions (`workspace_file_versions`), the quota is the workspace quota, the malware rescan job covers vault files too.
- **Hidden from Files.** `workspace_files.vault` is `hidden` for a vault file (`assistant` once its owner allows the assistant). The restrictive row policy `vault_files_hidden` hides `hidden` rows from every unit of work that has not set `anum.vault` to `on`; only the vault's own unit of work (`vault/store.py open_vault_unit`) and the malware rescan set it. So the Files list, Files routes by id, retrieval, attachments of tasks, meetings and marketing, and the previous release (rollback) never see a vault file. The Files routes also refuse any vault row (404) and the Files list and storage report leave them out. The purge and maintenance roles still see them (organisation deletion, rescan).
- **Private class.** The restrictive row policy `vault_documents_private` lets a session see a `private` document only when `anum.user_id` is its owner or a person in `vault_document_access`; the daily job sets `anum.vault_job`. Child rows (access, links, extractions, the access log) are reached through their document. Other workspaces see nothing (forced RLS on tenant and workspace on every table).
- **Access log.** `vault_access_log` records each view, download, link, link opened, number shown, change, share, scan and reminder, with ids and codes only (never titles, numbers or text). Changes also go to the workspace audit log as `document.*`.
- **Duplicates.** The SHA-256 of the bytes is kept on the document; adding the same bytes again answers 409 with the existing document's id (or says it is in the trash) unless `allow_duplicate=true`. Only documents the caller can see are compared, so a duplicate never reveals someone else's private document.
- **Quota note.** File uploads outside the vault cannot see hidden vault rows, so their quota check counts Files bytes only; vault uploads count both. The organisation export (`docs/organisation-lifecycle.md`) exports vault rows the app role can see without a person (workspace documents' metadata, ciphertexts stay encrypted) and no vault file bytes; organisation deletion purges everything.

## Identity and account numbers

`number_ciphertext` holds the normalised number (letters and digits) encrypted with the secrets key, `number_last4` the last four characters for lists and search. `POST /documents/{id}/number` returns the full number to the document's owner only and logs `number_revealed`. `python -m anum_api.rotate_secrets` re-encrypts them (`vault_documents`).

## Adding documents

`POST /api/v1/documents?name=&title=&category=&access_class=` with the raw file as the body, from the web dialog or any client. The Files rules apply: the type from the extension and the content signature, 25 MiB (`ANUM_MAX_UPLOAD_BODY_BYTES`), the ClamAV scan (fail closed), the plan's storage and the workspace quota. Bytes are stored, then the file row and the document commit in one unit of work; any failure deletes the bytes. Categories: passport, national ID, residency, visa, contract, salary certificate, vehicle registration, insurance, bank statement, receipt, invoice, purchase order, agreement, warranty, maintenance invoice, certificate, other, plus the workspace's own (`vault_categories`, owners only; a category still in use cannot be removed).

## Downloads and links

`GET /documents/{id}/content` (bearer) or a short-lived signed link from `POST /documents/{id}/link` served at `GET /documents/links/{token}` (the link is the credential, as for Files; `ANUM_FILES_LINK_TTL_SECONDS`). A vault token names `vault:<document id>`, so it never works on the Files link route and a Files token never works here. When the link is opened, the person it was minted for must still be an active member who can see the document; quarantined files are refused. Inline previews (images, PDFs) are served sandboxed with `Content-Security-Policy: default-src 'none'; sandbox`.

## Versions, trash and retention

A renewed passport is a new version: `POST /documents/{id}/versions` keeps the old bytes as an older version (Files versions), `restore` makes an older one current, and downloads take `?version_id=`. `DELETE /documents/{id}` moves a document to the trash (`purge_after` = now + trash days); `POST /restore` brings it back; `DELETE ?permanent=true` from the trash deletes the rows, the file rows and every version's bytes. `retention_until` on a document moves it to the trash on that date.

## Expiry reminders, trash and retention

The daily retention job (`python -m anum_api.voice_retention`, the Oracle `retention` service and the Helm CronJob) runs `anum_api.vault.jobs` and reports under `documents`: one in-app notification (kind `document_expiry`, never a push or email, since titles can be personal) to the document's owner at the reminder step, at 7 days, at 1 day and on the expiry day, each once per expiry date (`reminders_sent`), reading due documents in pages of 500 ordered by expiry date and leaving already expired ones out before the limit; documents past `retention_until` to the trash; documents past `purge_after` deleted for good. Workspaces are discovered as `anum_maintenance` (scope and date columns only); the work runs as the app role in each workspace with `anum.vault_job` set. Output is counts only.

## Scanning and extraction

- **What is read.** Receipts, invoices, maintenance invoices and purchase orders: merchant, date, total, currency, tax, reference, and line items only when they add up to the total (or subtotal, or total less tax) within 0.02. IDs, passports, residency, visas, insurance, registration, warranties and certificates: issue and expiry dates only, never numbers or names, and only when the owner turned identity dates on.
- **Where the text comes from.** A PDF with its own text layer is read in the API (`doc_extract`); an image goes to [the OCR service](#the-ocr-service). A scanned PDF without text is refused with a hint to scan it as a photo.
- **Arabic and English.** Arabic-Indic digits and separators are read as Western ones; keywords come in both languages (`الإجمالي`, `ضريبة القيمة المضافة`, `التاريخ`, `رقم الفاتورة`, `تاريخ الانتهاء`, ...); day-first dates are the default and an ambiguous date gets a lower confidence; Hijri dates are flagged, not converted; registration numbers (`VAT No`, `TRN`, `الرقم الضريبي`) and percentages are never taken as amounts; 3-decimal currencies keep 3 places.
- **Pending review.** Results are stored as `vault_extractions` with each field's value and confidence (and, for receipts only, the line it came from). Nothing changes on the document until the person confirms (with corrections) in **To review**; then receipt fields go to `vault_documents.receipt` and dates to the document. Rejecting keeps the document unchanged. A result read from an older version (a new version was added since) cannot be confirmed (409); reject it and scan the current version.
- **Prompt-injection protection.** OCR text is untrusted data. It is only matched against fixed patterns, never sent to a model, never interpreted. Values are validated (real dates, decimals, a currency from a fixed list, short text without control, direction or formula characters). A line that reads like instructions to an assistant (English or Arabic) is dropped from every value and the result carries the warning `instructions_in_text`, which the review screen shows. The raw text is not stored.

## The OCR service

`python -m anum_api.vault.ocr_service` runs in its own container (the API image built with `ANUM_OCR=1`, which adds Debian's `tesseract-ocr`, `tesseract-ocr-eng` and `tesseract-ocr-ara`, Apache-2.0). It holds no ANUM secrets (only `ANUM_OCR_SERVICE_TOKEN`), sits only on the internal `ocr` network with no internet route and no database, runs read-only with all capabilities dropped, writes nothing to disk (the image goes to Tesseract on stdin), logs outcome and line count only, decodes and normalises the image itself (40 megapixels at most, 15 MB body), and serves one call at a time.

Turn it on (Oracle VM, `docs/deploy-oracle.md`): set `ANUM_OCR_TOKEN` (32+ characters) in `infra/oracle/.env`, start with `--profile ocr`, and give the API `ANUM_OCR_SERVICE_URL=http://ocr:8091` and `ANUM_OCR_SERVICE_TOKEN`. Without them the vault says "Scanning images is not connected" and reads PDF text layers only. `GET /healthz` answers 503 with the reason when Tesseract or its Arabic or English data is missing.

## Scanning from an iPhone

Vault settings, **Scan from your iPhone**: **Make a token** shows a token once (`anumdoc.` + tenant, workspace, owner, device id and a 256-bit secret; ANUM keeps its SHA-256 and last four). A Shortcut sends a photo, a document scan or a Share Sheet file with `POST /api/v1/documents/device/upload?name=scan.jpg[&category=receipt&title=]` and `Authorization: Bearer <token>`. The token has the `documents` scope only and works on that route only; the owner's membership and role are checked on every upload (a viewer or former member is refused), at most `ANUM_VAULT_DEVICE_UPLOADS_PER_HOUR` (60) uploads per device per hour, revoked tokens answer 401, and every check of a normal upload applies. Documents arrive private to the owner, with `source: device`. Up to 10 active devices per person.

Shared tokens: a token made on Settings, Integrations, iPhone with the **Documents** scope also works on this route once the "Document scans" integration is on there ([iPhone integrations](iphone-integrations.md)); `vault/devices.py` `register_device_authority` is the seam the API plugs at start. The vault's own `anumdoc.` tokens keep working; moving their rows to the shared table is still open.

## Links

`vault_document_links` points a document at a vehicle, person, business or purchase (a reference the person types, resolved when those modules exist), at a finance transaction, or (since migration `0157_finance_evidence`) at a bill, invoice, adjustment or manual journal entry of the business books as its supporting document (`business_bill`, `business_invoice`, `business_adjustment`, `business_journal`; made only from Business, Books, which checks the record exists, [Business books](business-books.md#supporting-documents-and-evidence)). The vault's own link form does not offer those types; its detail pane lists them and removing one there removes the evidence. A finance link goes through `vault/links.py FinanceLinkPort`, which the finance links foundation connects at start (`finance/links.py VaultLinkAdapter`): the target is the payment's `canonical_id`, the caller needs `finance:manage`, and the document becomes an `evidence` link on that payment with no amount; it never posts money ([Finance links](finance-links.md)). An unknown payment answers 404. Removing that link in the vault voids the evidence link on the ledger first (`withdraw`), so both sides agree. The web page does not offer the finance target yet (it needs a payment picker).

## The assistant

Off by default for every document. On a workspace document the person who added it (or an owner) can tick "The assistant may read this document": the file row becomes `vault = 'assistant'` (visible to retrieval) and its text layer is indexed like a file; unticking, trashing or making it private sets it back to `hidden` and forgets the index. A new or restored version is indexed again at once, so the assistant reads the current version only, and the retrieval reindex (`POST /api/v1/retrieval/index`) keeps these documents (it lists them through `list_files(include_assistant_vault=True)`; the Files list still never shows them). Images are not indexed (their OCR text is not stored).

## Privacy

`GET /api/v1/privacy` counts `vault_private_documents`, `vault_devices` (deleted with the person's data) and `vault_workspace_documents` (kept, like workspace files). The export has a `documents` section with the person's documents (masked number, never the ciphertext), who they shared them with, links and devices (no token hashes); files are downloaded from the vault. Deletion purges their private documents with their files and versions, and disconnects their devices. Organisation and workspace deletion purge every vault table (`tenant_lifecycle_tables.py`, domain `documents`).

## API

All under `/api/v1/documents` (contracts in `packages/contracts/src/documents.ts`):

| Route | What |
|---|---|
| `GET /meta` | Counts (`summary.linked`: active documents with at least one link, for "Records linked"), categories, settings, OCR status, upload limits, what the caller may do |
| `GET /` | One page: `q`, `category`, `access_class`, `expiry=soon|expired`, `tag`, `status=active|deleted`, `sort=created|updated|expiry` (newest added, newest change, or the next expiry first: only documents that expire today or later), `cursor` (bound to the sort), `limit`. Each row carries its file (name, size, version), `links_count`, `first_link` and `review_pending` |
| `POST /` | Add (raw body) |
| `GET /review` | Scan results waiting for review |
| `PUT /settings`; `POST /categories`; `DELETE /categories/{id}` | Owners |
| `GET /people` | Members a private document can be shared with |
| `GET/POST /devices`, `DELETE /devices/{id}`; `POST /device/upload` | iPhone Shortcut |
| `GET /{id}`; `PATCH /{id}` (with `row_version`, 409 when stale); `DELETE /{id}[?permanent=true]`; `POST /{id}/restore` | One document |
| `POST /{id}/number`; `GET /{id}/content`; `POST /{id}/link`; `GET /links/{token}` | Number and bytes |
| `GET/POST /{id}/versions`; `POST /{id}/versions/{v}/restore`; `DELETE /{id}/versions/{v}` | Versions |
| `POST /{id}/access`; `DELETE /{id}/access/{user}` | Sharing |
| `POST /{id}/links`; `DELETE /{id}/links/{link}` | Links |
| `POST /{id}/extract`; `POST /extractions/{id}/confirm`; `POST /extractions/{id}/reject` | Scanning |

## The web page

Sidebar **Document vault** on the shared shell ([Finance shell](finance.md#finance-shell)), to the owner's mockup of 5 Nov 2026. Page head: **Scan document** (a photo or scan, camera first on phones, saved private as a receipt by default and read at once; the values wait for review) and **Upload file**. Tabs (`#vault`, `#vault-documents`, `#vault-review`, `#vault-expiring`, `#vault-categories`, `#vault-access`, `#vault-trash`, `#vault-settings`; `#vault-documents?doc=<id>` opens one document):

- **Overview:** four KPI cards from `GET /meta` (documents and how many are private, expiring within the reminder window and how many already expired, scans that need review, records linked as a percentage of active documents); the documents table (newest change first, six rows, View all); **Next expiries** (a month calendar marking expiry days and the next four, from `sort=expiry`); the **scan review queue** (the oldest scan with its suggested values, **Confirm and open** and **Edit**: suggestions are saved only when confirmed, and a receipt never becomes a Finance transaction by itself); **Scan from iPhone** (connected iPhones, set up or manage); **Security and versions** (the access log, private by default with the private count, limited access by role, versions kept, the trash days, and a plain note that two-step sign-in is set in the sign-in service, which ANUM cannot read).
- **Documents:** search, Category, Expiry state, Access (private or workspace) and Sort; a table (title and file with size, a sensitive identity tag, the linked record with how many more, category, updated, expiry, status Valid, Review needed, Expiring soon, Expired or Restricted for a private identity paper, download and versions), keyset paging, and the detail pane (preview, details and edit, number reveal, receipt, scanning, versions, sharing, links and activity).
- **Review queue** (one form per result with confidence, source line and warnings), **Expiring**, **Categories** (built-in ones with what scanning reads and how many documents each has, the workspace's own to add or remove), **Access and devices** (who can do what by role, the security strip, the iPhone tokens), **Trash** (restore, delete for good) and **Settings** (reminders, trash days, scanning switches, OCR status).

Every list has loading, empty, error and permission states; viewers get the explanation of who can see the vault. English and Arabic (RTL), keyboard and screen-reader labels, and layouts from 390 px phones to 1440 px desktops.

## Mobile

Menu, **Document vault** (`/vault`, `apps/mobile/lib/features/vault/`, [Record modules](mobile.md#record-modules)): the summary, search, a category, **Expiring soon**, **Expired** and the trash, with cursor paging; each document shows its masked number and an expiry badge in words. A document shows its details, **Show number** (the owner only, `POST /{id}/number`, logged), its file, **Save a copy** (`GET /{id}/content` with the session, saved with the system dialog), **Upload a new version**, older versions to save or make current, links to a vehicle, person, business, purchase or Finance payment (add and remove), and **Move to trash** or **Restore**. **Add document** picks a file with the system picker (the vault's types and size limit are checked first), streams it with progress, and on the duplicate refusal offers **Add anyway** (`allow_duplicate=true`). From 840 px the list and the document sit side by side. Camera scanning is not on the phone: the app has no camera plugin, so photos come from the files the phone already has or from the iPhone Shortcut. Sharing, scan review, settings and categories stay on the web. Tests: `apps/mobile/test/vault/vault_test.dart`.

## Tests

- API, memory backend: `services/api/tests/test_vault.py` (adding, types, size, malware, duplicates, private and workspace access, viewers and other workspaces, Files never shows vault files, properties, encrypted number, sharing, links, versions, trash, permanent deletion with bytes, signed links and expiry, assistant opt-in, scanning with a stubbed OCR service and review, settings and categories, iPhone tokens, reminders and purge, privacy).
- Parser and OCR service: `tests/test_vault_ocr.py` (English and Arabic receipts, identity dates, injection, control characters, TSV). The `ocr`-marked tests render synthetic English and Arabic receipts and an identity page with Pillow and read them with the real Tesseract; deselected unless `-m ocr` (CI job "Document OCR (Tesseract)", where none may be skipped).
- PostgreSQL: `tests/test_postgres_vault.py` (both row policies as the app role, other workspaces, a forged private row refused, duplicates, versions, trash and permanent deletion, the daily job with notifications, number rotation).
- Web: `apps/web/test/vault.test.ts`; e2e `apps/web/e2e/vault.spec.ts` (desktop and mobile) with the API mocked, plus the UI gate and axe pages.

## Not in this release

- Automatic matching of a fuel receipt to its vehicle and transaction, a receipt to its purchase, or insurance to a vehicle reminder. A purchase or return that names a vault document as its receipt or proof adds a `purchase` link on that document when its owner may edit it ([Purchases](purchases.md#records)).
- The shared iPhone integrations page and generalised device tokens (after #364).
- Native iPhone camera, VisionKit scanner and Share Sheet extension (Shortcuts only), and camera capture in the Flutter app (no camera plugin yet; the phone screens upload files the phone already has). On the phone: sharing, scan review, editing a document's details, settings and categories (on the web).
- OCR of scanned PDFs (send them as images), Hijri date conversion, and the OCR service in the Helm chart (compose profile only, like the browser service).
- Per-category retention rules; retention is per document.
- From the owner's mockup: bulk actions on the documents table (Attach to workflow, Tags, Safe download, Restore previous version for a selection), a Create scan button inside the table card, retention in years per category, and a "2FA enabled" figure (two-step sign-in lives in the sign-in service, which ANUM cannot read). Each document's own actions are in its detail pane.
