# Workspace Files

Files are tenant- and workspace-scoped objects: the bytes live in object storage, the metadata in PostgreSQL under row-level security, and text files and the text of PDF, DOCX, XLSX, PPTX and OpenDocument files are indexed for retrieval into task prompts. Code: `services/api/anum_api/files.py` (stores and routes), `anum_api/file_policy.py` (accepted types, content checks, download names, signed links, the malware-scan hook), `anum_api/file_scanner_clamav.py` (the ClamAV scanner), `anum_api/db/workspace_settings_repository.py` (PostgreSQL metadata).

## API

All routes are under `/api/v1/files` and take the usual tenant context ([Identity](identity.md)).

| Route | Permission | What it does |
|---|---|---|
| `POST /api/v1/files?name=<name>` | `file:upload` | Upload the raw request body. The name goes in the `name` query parameter (URL-encoded, so any Unicode name works); ASCII-only clients may still send `X-File-Name`. `X-Content-SHA256` (optional) is verified. Returns the file (`201`). |
| `GET /api/v1/files` | `file:read` | One page. `q` (part of the name, case-insensitive; `%` and `_` are literal), `sort` (`created_at`, `name`, `size`), `order` (`asc`/`desc`; default newest or largest first, A to Z for names), `limit` (1 to 200, default 50) and `cursor` (the last file id of the previous page). A full page means there may be more; an unknown cursor answers `400` ("reload the list"). |
| `GET /api/v1/files/usage` | `file:read` | Bytes used (current files and older versions; `version_bytes` and `version_count` give the part older versions take) and file count, the workspace quota, the per-file limit, the link lifetime, the accepted types (a sentence and the extensions) and whether the caller may upload (`can_upload`) and manage every file (`can_manage_all`). Clients check uploads against it before sending. |
| `GET /api/v1/files/{id}` | `file:read` | Metadata. |
| `PATCH /api/v1/files/{id}` `{"name": ...}` | `file:upload`, and the uploader or `file:manage` | Rename. The extension must stay the same (the type never changes); `updated_by`/`updated_at` record the rename. |
| `DELETE /api/v1/files/{id}` | `file:upload`, and the uploader or `file:manage` | Delete metadata, then bytes, then index chunks. |
| `GET /api/v1/files/{id}/content` | `file:read` | The bytes as an attachment (bearer-authenticated clients). |
| `GET /api/v1/files/{id}/preview` | `file:read` | Text, Markdown and data files as JSON text, at most 256 KiB (`truncated` says when more exists). Other kinds answer `422`. |
| `POST /api/v1/files/{id}/links` `{"disposition": "attachment"\|"inline"}` | `file:read` | A short-lived signed link (`url`, `expires_at`). `inline` only for previewable kinds. |
| `GET /api/v1/files/links/{token}` | the link | The bytes, with no bearer token. Expired or tampered links answer `410`. |
| `POST /api/v1/files/{id}/reindex` | `file:upload` | Index the file for retrieval again (spends model budget for text) and return its state. |
| `GET /api/v1/files/{id}/versions?limit=&offset=` | `file:read` | The current file and one page (1 to 100, default 20) of its older versions, newest first, with `total`. Each version carries its size, who uploaded it and when, who replaced it and when, and `downloadable` (false when quarantined). |
| `POST /api/v1/files/{id}/versions` | `file:upload`, and the uploader or `file:manage` | Upload new content for the file (raw body, like an upload). The current bytes become an older version and the file's `version` goes up by one; name and type stay. Same checks as an upload: extension and signature, size, malware scan and quota. The same bytes as the current version answer `409`. |
| `GET /api/v1/files/{id}/versions/{version_id}/content` | `file:read` | An older version's bytes as an attachment named `name (version N).ext`. A quarantined version answers `409`. |
| `POST /api/v1/files/{id}/versions/{version_id}/restore` | `file:upload`, and the uploader or `file:manage` | Make an older version current. The current bytes become an older version, so a restore can be undone; the restored bytes are shared, not copied. |
| `DELETE /api/v1/files/{id}/versions/{version_id}` | `file:upload`, and the uploader or `file:manage` | Delete one older version to free its space. The current version is deleted only with the file. |
| `GET /api/v1/files/storage` | `file:read` | The storage screen's data: usage (as `/usage`), the split by kind, and the ten files that take the most space with their older versions. |

Every file row carries `version` (the current version number, 1 until a new version is uploaded), `kind` (`text`, `markdown`, `data`, `image`, `pdf`, `document`, `archive`; null for a type uploaded before the allow-list), `previewable`, `can_manage` (whether the caller may rename and delete it), `created_by`/`created_at`, `updated_by`/`updated_at`, and `index` (below).

### Permissions

| Role | Read, preview, download | Upload, re-index | Rename and delete |
|---|---|---|---|
| viewer | yes | no | no |
| member | yes | yes | their own uploads |
| owner | yes | yes | any file (`file:manage`) |

`file:read`, `file:upload` and `file:manage` are also offered in role templates. Refusals say who can do it ("Only the person who uploaded this file or a workspace owner can rename or delete it."). Before this change members could delete anyone's file; that is now owner-only.

## Limits

- **Types.** The extension decides the stored type; the declared `Content-Type` only matters for a name without an extension. Accepted: text (`.txt`, `.log`, `.md`, `.rst`, `.csv`, `.tsv`, `.json`, `.yaml`/`.yml`, `.xml`), images (`.png`, `.jpg`/`.jpeg`, `.gif`, `.webp`), PDF, Office and OpenDocument files (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp`), `.zip`, and video (`.mp4`/`.m4v`, `.mov`, `.webm`, for [Marketing](marketing.md) media; checked for the ISO media or WebM signature). Everything else, including HTML, SVG and executables, is refused with `415` and a sentence listing what is accepted. Binary types must start with their signature (a "PNG" that is HTML is refused); text must not contain NUL bytes.
- **Size.** `ANUM_MAX_UPLOAD_BODY_BYTES` (25 MiB) per file, enforced from `Content-Length` and while streaming (`413` with the limit in the message). Empty files are refused (`422`).
- **Quota.** `ANUM_FILES_WORKSPACE_QUOTA_BYTES` (2 GiB) per workspace, counting every stored byte: current files and older versions, with identical bytes of one file counted once (a restore costs nothing). An upload or a new version that would go over answers `413 payload_too_large` with the used and allowed sizes, a sentence that names what to free (files or older versions) and a `details[0].type` of `storage_quota`. The check runs before the bytes are stored and again while inserting the metadata under a per-workspace transaction advisory lock, so concurrent uploads cannot overshoot; the loser's bytes are deleted.
- **Names.** At most 255 characters, no `/`, `\` or control characters.

## Versions

A file keeps its id, name and type; its bytes can change. `POST /files/{id}/versions` stores the new bytes under a new object key (`.../files/<file id>/<sha256>`) and moves the metadata of the bytes it replaces into `workspace_file_versions`, in one transaction under the same per-workspace advisory lock as uploads, so concurrent uploads and versions cannot overshoot the quota. `version` on the file is the number of the current bytes; older rows keep their own numbers, so a restore is a new version number whose bytes are shared with the version it restored (one object, two rows). The swap checks the SHA-256 the caller read: a file changed or deleted in the meantime answers `409` ("This file changed or was deleted while you were working on it") and the new bytes are deleted again.

- **Who.** Anyone with `file:read` lists and downloads versions; the uploader of the file or an owner (`file:manage`) uploads, restores and deletes them.
- **Scanning and quarantine.** A new version passes the same type, signature and malware checks as an upload. A version found infected is listed, flagged and refused for download and restore; it can be deleted.
- **Retrieval.** The index follows the current bytes: a new version or a restore re-indexes the file (best effort, like an upload).
- **Deleting.** Deleting a file deletes every version's row (foreign key cascade) and then every object; deleting one older version deletes its object only when no other row of that file shares it.
- **Migration** `0088_file_versions` (expand only, chained after `0080_business_change_orders`; renumbered at merge if others land first): `workspace_files.version` (default 1, so the previous release keeps working), a concurrently built unique index on `(tenant_id, workspace_id, id)` and the table `workspace_file_versions` under forced RLS (`tenant_isolation_workspace_file_versions`) with a scoped foreign key `(tenant_id, workspace_id, file_id)` to `workspace_files` (`ON DELETE CASCADE`). The tenant purge role may delete its rows and read their scope and object key (`lifecycle_purge_workspace_file_versions`), so an organisation or workspace deletion removes every version's bytes ([Organisation lifecycle](organisation-lifecycle.md)); `anum_api/tenant_lifecycle_tables.py` lists the table.

## Storage screen

`GET /files/storage` and the **Storage** button on the web Documents page (and on the phone's Files tab) show the workspace's usage against its limit: a meter (amber from 80%, red from 95%, "Storage is full" at 100%), how much older versions take, the split by kind and the ten largest files with their older versions; picking a file opens it, where versions can be deleted. The limit itself is the operator's (`ANUM_FILES_WORKSPACE_QUOTA_BYTES`); the screen is read-only and says so. Uploads and new versions are checked against it on the server every time (the clients only warn early), and the refusal names what to free.

## Downloads and previews

Short-lived links are HMAC-SHA256 signatures over tenant, workspace, user, file id, SHA-256, disposition and expiry (`ANUM_FILES_LINK_TTL_SECONDS`, default 300, 30 to 3600). The key is derived from `ANUM_SECRETS_KEY`: the first key signs, every key verifies, so a key rotation keeps unexpired links valid. Without a secrets key (local and test only) a per-process key is used. Serving a link scopes the metadata read to the link's tenant and workspace under RLS, and with OIDC the minting user must still be an active member. A link names a content digest, so it stops working once the file is deleted. Links are bearer credentials in a URL: they are short-lived, the API sends `Referrer-Policy: no-referrer`, and responses are `Cache-Control: private, no-store`.

Responses never execute anything: downloads are `attachment` with an RFC 6266 file name (ASCII fallback plus `filename*=UTF-8''...`), served as `application/octet-stream`; inline responses exist only for previewable kinds, text kinds are sent as `text/plain`, and every link response carries `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox` plus the API's `nosniff`.

Clients render previews from data, never as documents:

- **Text, data, Markdown**: from `GET /preview`. Markdown is parsed into a tree (headings, paragraphs, lists, quotes, code, rules, emphasis and links) and rendered as React elements; raw HTML stays text, and only `http(s):` and `mailto:` links become links (`apps/web/src/lib/files.ts`).
- **Images**: bytes from an inline link, shown through a `blob:` URL in an `<img>` (allowed by the web CSP's `img-src blob:`).
- **PDF**: pdf.js (`pdfjs-dist`, legacy build, loaded only when a PDF is opened) draws pages onto a canvas from the bytes. pdf.js's core API runs no PDF JavaScript, XFA forms are off, and no plugin or foreign frame is involved. Its worker is a same-origin module (`worker-src 'self'`).
- Office documents and archives have no preview; the panel offers a download.

## Malware scanning

Every upload passes through `anum_api.file_policy.FileScanner`: a `scan(name, content_type, content) -> ScanVerdict(clean, reason, scanned)` called with the bytes after the type and checksum checks and before anything is stored. It runs in a worker thread, never on the event loop. Email Center attachments (files attached to outgoing mail, including the files a forward carries) pass the same type, signature and scanner checks before they are kept ([Email: attachments](email.md#attachments)).

ANUM ships a ClamAV scanner, `anum_api/file_scanner_clamav.py`. It streams the bytes to a `clamd` over TCP with the `INSTREAM` command (64 KiB length-prefixed chunks, no temporary file, no client library) and reads the verdict line. The settings:

| Setting | Default | Meaning |
|---|---|---|
| `ANUM_FILE_SCANNER` | `off` | `clamav` installs the ClamAV scanner at startup; `off` scans nothing. |
| `ANUM_CLAMAV_HOST` | empty | The clamd host name or IP address (no scheme, no port). Required with `clamav`. |
| `ANUM_CLAMAV_PORT` | `3310` | clamd's TCP port. |
| `ANUM_CLAMAV_TIMEOUT_SECONDS` | `30` | Timeout for connecting and for each send and read (above 0, at most 120). |
| `ANUM_FILE_SCAN_REQUIRED` | `true` | Fail closed: when clamd is unreachable, times out or answers with an error (for example `INSTREAM size limit exceeded`), the upload fails with `503` ("The malware scanner is unavailable, so nothing was stored. Try again later.") and nothing is stored. `false` accepts the file unscanned and logs a warning; the API refuses `false` with `clamav` outside `local` ([Security](security.md#startup-policy)), and so does the Helm chart. |

While clamd has not answered since the API started (clamd opens its port only after it has loaded its signatures, about a minute after a restart), an upload answers `503` with `Retry-After: 60` and "The malware scanner is still starting (it loads its virus signatures after a restart, which takes a few minutes), so nothing was stored. Try again in a few minutes." Once clamd has answered, a later failure is reported as unavailable instead.

A file with a signature is refused with `422` and nothing is stored. The message names the signature and the file, for example: "The malware scanner found Win.Test.EICAR_HDB-1 in invoice.txt, so it was not stored. Delete the file and ask the sender for a clean copy." Uploads are at most 25 MiB (`ANUM_MAX_UPLOAD_BODY_BYTES`), so clamd's `StreamMaxLength` is set to 26M everywhere ANUM runs it; a smaller limit would turn full-size uploads into `503`s.

Where clamd runs: the local compose file (`clamd` service, published on 127.0.0.1:3310 only; the compose `api` uses it), the one-VM deployment (`clamd` service, on by default, [Deploying on one Oracle Cloud VM](deploy-oracle.md#malware-scanning)) and Kubernetes (your own clamd Service, then `config.ANUM_FILE_SCANNER=clamav` and `config.ANUM_CLAMAV_HOST`; [Deployment](deployment.md#values)). Render has no clamd, so scanning stays off there unless you point `ANUM_CLAMAV_HOST` at one you run. The image is `clamav/clamav:1.5.4-debian13-slim` (linux/amd64 and linux/arm64), a third-party image pinned by tag like the other compose-only images; it is not built by ANUM's deploy workflows, so it is not in the PR Trivy scan. It ships a signature database and `freshclam` refreshes it; clamd keeps one copy of the signatures in memory (about 1.2 GB): `ConcurrentDatabaseReload no` reloads them in place and freshclam's `TestDatabases no` skips loading a second copy to test a download (the files are still signature-checked), hence the 2 GB memory limit.

Every stored file records its scan in `scan_status` (`null` until scanned, `clean`, `infected`), `scan_signature` and `scanned_at` (migration `0075_file_scan_status`); uploads scanned on the way in are `clean`, and files the API generates (report exports) and files stored before scanning was on stay `null` until the rescan job looks at them.

### Rescanning older files

`python -m anum_api.file_rescan` (opt-in, run by an operator; `--dry-run` counts, `--limit N` stops after N files, `--batch-size N`) scans every file whose `scan_status` is `null`. It needs `ANUM_FILE_SCANNER=clamav` and refuses `ANUM_FILE_SCAN_REQUIRED=false`. It finds the files as `anum_maintenance`, which may read only `id`, `tenant_id`, `workspace_id` and `scan_status` of unscanned rows (never the name, key or digest), then reads each file and records its verdict as the application role inside that workspace's RLS context (the pattern of the retention jobs, `anum_api/maintenance.py`). No transaction is open while a file is scanned, and the verdict is kept only if the file still has the bytes that were scanned. An infected file is **quarantined**: it stays listed (with `scan_status: "infected"`, `previewable: false` and a **Quarantined** badge in the web app) so it can be deleted, but download, preview, links and re-indexing answer `409` ("`{name}` is quarantined: the malware scanner found `{signature}` in it, so it cannot be opened, previewed, shared or indexed. Delete it, or ask a workspace owner to."), its retrieval passages are dropped and search never returns them. If clamd fails, the job stops, records nothing for the file in hand and exits `1`; a later run continues. Output is counts and file ids only.

Any other scanner (a cloud scanning API, for example) can be installed with `file_policy.set_file_scanner(...)`: return `ScanVerdict(clean=False, reason=<signature>)` to refuse (`422`) and raise to fail the upload (`503`). Files uploaded before scanning was turned on are scanned by the [rescan job](#rescanning-older-files).

## Storage Backends

`ObjectStorage` separates file bytes from metadata. `ANUM_OBJECT_STORAGE_BACKEND` selects the adapter:

| Value | Adapter | Notes |
|---|---|---|
| `local` (default) | `LocalObjectStorage` | Files below `ANUM_OBJECT_STORAGE_LOCAL_PATH` (`.anum-data/objects`); refuses keys that escape the root. |
| `memory` | `InMemoryObjectStorage` | Per process; tests and throwaway runs. |
| `s3` | `S3ObjectStorage` | Any S3-compatible endpoint (SeaweedFS locally, compose service `s3`) through a pinned boto3 client: SigV4, path-style addressing, bounded retries and timeouts. |

S3 settings: `ANUM_S3_ENDPOINT`, `ANUM_S3_REGION` (`us-east-1`), `ANUM_S3_BUCKET`, `ANUM_S3_ACCESS_KEY`, `ANUM_S3_SECRET_KEY`, `ANUM_S3_SERVER_SIDE_ENCRYPTION` (for example `AES256` or `aws:kms`; sent on every write) and `ANUM_S3_CREATE_BUCKET` (create the bucket on first write; local storage only, real environments provision buckets, encryption and lifecycle rules with OpenTofu). Outside `local` the API refuses the compose development secret.

Object keys always start with the tenant and workspace, so bucket policies, lifecycle rules and exports can be scoped by prefix:

```
tenants/<tenant>/workspaces/<workspace>/files/<file id>/<sha256>
```

`workspace_object_key()` builds every key and rejects empty, `.`/`..`, slash-containing or over-long segments. A download whose bytes are missing answers `404`.

## Metadata

With `ANUM_REPOSITORY_BACKEND=postgresql` file metadata lives in the `workspace_files` table (migration `0008_control_plane_stores`) under the tenant and workspace RLS policy: id, name, content type, size, SHA-256, object key, creator and time. Migration `0026_file_management` (expand only) adds `updated_by`/`updated_at` for renames and two concurrently built keyset-paging indexes, `(tenant_id, workspace_id, lower(name) collate "C", id)` and `(tenant_id, workspace_id, size_bytes, id)`; names sort bytewise after lower-casing, the same in every database and in the in-memory store. The bytes never enter the database. An upload writes the object, then the metadata row; if the row cannot be written (for example the workspace is not onboarded, `409`, or the quota is reached) the object is deleted again. A delete removes the row first and the object after the transaction commits, so a listed file always has content. With `memory` the metadata is per process.

## Retrieval

Text files (`text/*`, JSON, YAML, XML, CSV, or a `.txt`/`.md`/`.csv`/`.json`/`.yaml`/`.xml`/`.log`/`.rst` name) up to 2 MiB of UTF-8, and PDF, DOCX, XLSX, PPTX, ODT, ODS and ODP files (see [Document text extraction](#document-text-extraction)), are indexed for retrieval into task prompts right after their metadata is committed; other files (images, video, archives) get a `skipped` index row (`not_text`, `not_utf8`, `too_large`, `empty`, or a document's `encrypted`, `malformed`, `too_complex`, `no_text_layer`). Indexing is best effort: a failed embedding is recorded on the index row and never fails the upload. Each file row reports `index`: `status` (`indexed`, `stale` when indexed with an earlier embedding model, `failed`, `skipped`, or `pending` when no index row exists), `error`, `chunk_count`, `embedding_model` and `indexed_at` (`index` is null if the index could not be read; the list still answers). `POST /api/v1/files/{id}/reindex` retries one file and `POST /api/v1/retrieval/index` the whole workspace. Deleting a file removes its chunks, and search ignores chunks of a file without metadata in any case. See [Memory](memory.md#retrieval).

### Document text extraction

`services/api/anum_api/doc_extract.py` reads the text of documents for the index. The extension decides the format (the content type only for a name without one).

| Format | Read with | Sections | Citation |
|---|---|---|---|
| PDF | pypdf | one per page | `page n` |
| DOCX | `zipfile` and defusedxml | split where Word last broke pages (`w:lastRenderedPageBreak`) or at manual page breaks | `page n`, or the file name alone when the file has no page marks |
| PPTX | `zipfile` and defusedxml | one per slide, in presentation order | `slide n` |
| XLSX | `zipfile` and defusedxml | one per sheet, a row per line, cells separated by tabs | `sheet n (name)` |
| ODT | `zipfile` and defusedxml (`content.xml`) | split where LibreOffice last broke pages (`text:soft-page-break`) | `page n`, or the file name alone without page marks |
| ODS | `zipfile` and defusedxml | one per sheet (`table:table`), a row per line; a repeated cell or row with content is expanded at most 100 times and an empty repeat never | `sheet n (name)` |
| ODP | `zipfile` and defusedxml | one per slide (`draw:page`), speaker notes left out | `slide n` |

Each section is chunked on its own, so a chunk never spans two pages, and every chunk starts with a location line such as `[manual.pdf, page 3]`. Prompts therefore show the model the file and page, and the knowledge base returns `page`, `page_unit` and `page_label` on each citation ([Knowledge base](knowledge-base.md#search-and-ask-the-knowledge-base)). Only the unit and number of that line are read back; the name in it is never trusted. A run's `retrieval` step records the page of each document chunk it used.

Uploads are hostile input, so extraction has hard limits (`ExtractionLimits`):

| Limit | Value | Over it |
|---|---|---|
| Input size | 25 MiB | refused, `too_large` |
| PDF pages, PPTX or ODP slides read | 300 | the rest is not read |
| Sheets read | 50 | the rest is not read |
| Rows read per sheet | 20,000 | the rest is not read |
| Characters kept | 500,000 | the rest is not read (indexing also stops at 200 chunks) |
| Wall time | 20 seconds, checked between pages, slides, rows and paragraphs | `failed`, `timeout`, so a re-index tries again |
| Zip entries | 2,000 | refused, `too_complex` |
| Uncompressed size, all entries | 128 MiB | refused, `too_complex` |
| Compression ratio of an entry over 1 MiB | 200 to 1 | refused as a zip bomb, `too_complex` |
| One XML part | 32 MiB, read with a bounded read | refused, `too_complex` |

- **Encrypted files** are refused with `encrypted`: an Office file saved with a password is an OLE container rather than a zip, a zip entry with the encryption flag is refused, an OpenDocument file whose manifest lists `encryption-data` is refused, and a PDF that does not open with the empty password is refused. A PDF that only carries permission flags opens with the empty password and is read.
- **Damaged files** (a broken zip, broken XML, a PDF pypdf cannot parse, nesting deep enough to hit Python's recursion limit) are refused with `malformed`. An XML part that contains a `DOCTYPE` or `ENTITY` declaration is refused too, both by a check before parsing and by defusedxml with DTDs, entities and external references forbidden, so no entity can be expanded (OOXML never declares one). pypdf also caps every stream it decompresses (75 MB per stream by default).
- **Scanned PDFs.** A PDF with pages but no text layer on any of them (usually a scan) is recorded as `skipped` with `no_text_layer`, not as a silent `empty`. ANUM has no OCR: the Files view says "Not indexed: no extractable text, OCR not available.", the knowledge base badge says "No extractable text, OCR not available" (web and Flutter), and the file is still found by name, title and tags. Adding OCR (for example Tesseract) is a separate decision and is not part of this change.
- **Off the event loop.** Extraction runs on its own two-thread pool (`anum-doc-extract`), not on the event loop and not in the default executor that database calls share, and the caller stops waiting after three times the wall time. A running Python thread cannot be killed, so the cooperative deadline inside the extractor is what ends a slow file and the pool size bounds how many run at once.
- **Reuse.** A document whose bytes and embedding model have not changed is not extracted or embedded again on re-index.
- **Licences.** pypdf 6.21.1 (BSD-3-Clause) and defusedxml 0.7.1 (Python Software Foundation License) are pure Python with no required dependencies on Python 3.11+. python-docx, openpyxl and python-pptx were not used: python-docx and python-pptx need lxml (native code) and python-pptx also Pillow, and reading the few OOXML parts directly keeps every zip read under the limits above.

Tests: `services/api/tests/test_doc_extract.py` builds every fixture in memory (tiny PDFs, a scanned PDF, DOCX, XLSX, PPTX, ODT, ODS and ODP zips) and covers each format, OpenDocument repeats, encryption and limits, the scanned PDF's `no_text_layer`, the page, sheet, row and character limits, a zip bomb, an entity declaration, encrypted PDF and Office files, damaged files, oversized input, the deadline, the extraction pool, the location lines, the index rows and knowledge base citations with file name and page.

## Web Files view

The **Documents** page, `apps/web/src/FilesView.tsx` (the desktop app renders the same build), helpers in `apps/web/src/lib/files.ts`, PDF pages in `apps/web/src/PdfPreview.tsx`. It follows the shared [page layout](design/anum360.md#page-layout): one header row with **Upload**, a summary band, and the file list next to the preview pane (30/70 from 1024px wide; the preview opens on the first file).

- **Summary band**: files and storage from `GET /files/usage`, indexed and failed files from `GET /retrieval/status` (sources of type `file`). A figure whose request fails says Unavailable. Under the figures, a meter of bytes used against the quota (amber from 80%, red from 95%), the per-file limit and the accepted types.
- **Filters**: by type and by index state, applied to the files already loaded (the list API filters by name only; the toolbar says how many of the loaded files are shown).
- **Share link** in the preview pane: `POST /files/{id}/links` (`attachment`) gives a download link that needs no sign-in until it expires (the workspace's link lifetime), with Copy.
- **Upload**: several files at once. Each upload shows a progress bar and a Cancel button (XMLHttpRequest, aborted on cancel; nothing is stored). Files the API would refuse (empty, too large, unaccepted type, over quota) are refused before sending with the same sentence; server refusals show the API's message. Viewers see a disabled Upload button and who can upload.
- **List**: name, type, size, uploader ("You" for the caller) and time, and the index state as a badge. Search by name, six sort orders, and "Load more files" with the keyset cursor (a cursor whose file was deleted reloads the list). Empty workspace, no search results, loading, a failed load with Retry, and `403` (who can see files) each have their own state. Delete asks to confirm in the row.
- **Versions** in the detail panel (below the details): `Current: version N`, **Upload new version** (a file with the same extension; refused before sending when it does not fit the bytes left), and the older versions newest first with size, uploader, upload and replacement times, **Download**, **Restore** and **Delete** (confirm in the row), ten at a time with **Show more versions**. Empty, loading, failed (with Retry), denied, quarantined and viewer (read-only, with who can change them) states each have their own text. Components: `FileVersions.tsx`, `FileStorage.tsx`; strings in `filesVersions.en.ts` and `.ar.ts`.
- **Storage** button next to Upload opens the storage screen above the list (`FileStorage.tsx`); it refreshes after uploads, deletes and version changes.
- **Detail panel** (a row's name, or "Sources used" on a run, [Memory](memory.md#at-run-time)): kind, size, upload time and user, the last rename, the index state with its reason, id and SHA-256; Download, Rename (inline form that keeps the extension), Re-index (when the file failed, is stale or was never indexed) and Delete with confirm, shown by role. Then the preview (text, formatted or source Markdown, image, or PDF with page controls). It handles loading, a file deleted since ("This file no longer exists in this workspace"), `403` and other errors with Retry.

Tests: `apps/web/test/files.test.ts` (paths, checks, labels, the Markdown reader), `apps/web/e2e/files.spec.ts` (list, search, sort and paging, uploads with refusals, quota and cancel, Markdown, image and PDF previews, rename, re-index, delete, the viewer role, `403`, errors, and no horizontal overflow from 1440x900 to 390x844), `apps/web/e2e/sources-used.spec.ts`.

Every string is in the English and Arabic catalogs (`files.*` in `apps/web/src/lib/messages/`); file names and previewed text keep their own direction (`dir="auto"`), sizes and ids are isolated, and the pager chevrons mirror in Arabic ([Web localization](web-localization.md)). `e2e/localization.spec.ts` reads the view in Arabic and `e2e/accessibility.spec.ts` runs axe over it in both languages.

## Mobile

The Flutter app's Resources, Files tab (`apps/mobile/lib/features/workspace/files_screen.dart`, state in `files_controller.dart`, API in `files_repository.dart`) has the same behaviour: the storage meter and limits, the system picker for several files, streamed uploads with progress and Cancel (`http.StreamedRequest`; cancelling closes that upload's connection), search, sort and "Load more files", and a detail screen with the index state, a text or image preview, Download (the save dialog, through a short-lived link), Rename (a dialog that keeps the extension), Re-index and Delete with confirm, shown by role. PDFs, Office files and archives are downloaded and opened in another app; the app has no PDF renderer. "Sources used" on a run opens the same detail screen. Tests: `apps/mobile/test/files_test.dart`, including layouts at 320, 390, 768, 1024 and 1440 wide at 1x and 2x text.

Versions and storage on the phone: `apps/mobile/lib/features/files/` (`file_versions_section.dart` on the file detail screen, `storage_screen.dart` behind the Files tab's **Storage** button, `file_versions_controller.dart`, `file_versions_repository.dart`, and `file_strings.dart` with the English and Arabic text). The upload streams with progress and cancel like a file upload; an older version is saved with the system dialog as `name (version N).ext`. Tests: `apps/mobile/test/file_versions_test.dart` (parsing and checks, the API calls, the controller, every state, 320 to 1440 wide at 1x and 2x text, Arabic).

## Tests

- `services/api/tests/test_files_management.py`: types and signatures, size, empty and name refusals, the quota (including a concurrent upload), the malware hook, search, sort and keyset paging, index state, rename and delete per role, viewers, previews, links (tampering, expiry, another workspace, deleted files, key rotation), re-index, CORS for the upload headers and `PATCH`.
- `services/api/tests/test_file_versions.py`: a new version keeps the old bytes, listing and paging, download names, restore (shared bytes, undo), identical content and type mismatch refused, the quota counting every version and freeing on delete, per-role permissions, workspace scoping, deleting a file with its versions, quarantined versions, the storage report.
- `services/api/tests/test_postgres_file_versions.py` (database): versions, restore and the quota as the non-owner app role, forced RLS, the cascade, and a stale replace losing to a concurrent change; `test_migrations.py` checks `0088_file_versions` is expand only.
- `apps/web/e2e/files-versions.spec.ts`, `apps/web/test/files.test.ts`: versions and the storage screen in the browser, every state, no overflow from 1440x900 to 390x844, Arabic.
- `services/api/tests/test_postgres_files.py` (database): paging, search and sort in PostgreSQL, the rename columns and indexes, and the quota under the advisory lock, as the non-owner app role.
- `services/api/tests/test_file_scanner_clamav.py`: the ClamAV scanner against an in-process fake clamd: clean and infected verdicts, chunking, error replies, timeouts and an unreachable clamd failing closed, optional scanning, the `422` and `503` answers through the upload route (including "still starting" before clamd first answers), scan state on uploads, the rescan job (dry run, quarantine, stop on failure) and quarantined files refused for download, preview and links, the settings, the startup refusal and the Helm chart's mirror of it.
- `services/api/tests/test_file_scanner_clamav_integration.py` (`clamav`): a real clamd at `ANUM_TEST_CLAMD_HOST`/`ANUM_TEST_CLAMD_PORT` (default `127.0.0.1:3310`, `docker compose -f infra/docker/compose.yaml up clamd`): `PING`, the EICAR test file refused and clean bytes accepted, and an EICAR upload answering `422`. CI's "API integration" job runs clamd and fails on any skip.
- `services/api/tests/test_postgres_file_rescan.py` (database): the rescan job across tenants as the non-owner app role, and what `anum_maintenance` may read (ids, scope and scan state of unscanned rows only, no writes).
- `services/api/tests/test_object_storage.py`: the S3 adapter against moto and, `s3`-marked, against a real endpoint at `ANUM_TEST_S3_ENDPOINT` (default `http://127.0.0.1:9000`, credentials `ANUM_TEST_S3_ACCESS_KEY`/`ANUM_TEST_S3_SECRET_KEY`), for example `docker compose -f infra/docker/compose.yaml up s3`.

## Not done yet

- Malware scanning is off by default outside the compose files (`ANUM_FILE_SCANNER=off`); Kubernetes and Render deployments must run their own clamd to turn it on ([Malware scanning](#malware-scanning)). Files stored before scanning was turned on are scanned only when an operator runs the [rescan job](#rescanning-older-files); there is no schedule for it. The phone app shows no quarantine badge yet (a quarantined file's download answers `409` with the reason).
- The quota is one value for every workspace (`ANUM_FILES_WORKSPACE_QUOTA_BYTES`); the storage screen shows it but there is no per-workspace override. Versions have no automatic retention (the owner deletes them by hand) and no cap on how many a file keeps.
- Name search is a bounded `ILIKE` scan within one workspace; very large workspaces would want a trigram index.
- Office documents, archives and (on mobile) PDFs have no in-app preview. Images and archives are not indexed for retrieval, and there is no OCR: a scanned PDF is flagged `no_text_layer` ("no extractable text, OCR not available"). DOCX and ODT headers, footers, footnotes and comments, PPTX and ODP speaker notes and XLSX formulas (only their cached values) are not extracted.
- An upload with the same name as an existing file is still a new file; replacing content is **Upload new version** on the file.
