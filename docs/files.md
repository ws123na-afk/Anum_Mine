# Workspace Files

Files are tenant- and workspace-scoped objects: the bytes live in object storage, the metadata in PostgreSQL under row-level security, and text files are indexed for retrieval into task prompts. Code: `services/api/anum_api/files.py` (stores and routes), `anum_api/file_policy.py` (accepted types, content checks, download names, signed links, the malware-scan hook), `anum_api/db/workspace_settings_repository.py` (PostgreSQL metadata).

## API

All routes are under `/api/v1/files` and take the usual tenant context ([Identity](identity.md)).

| Route | Permission | What it does |
|---|---|---|
| `POST /api/v1/files?name=<name>` | `file:upload` | Upload the raw request body. The name goes in the `name` query parameter (URL-encoded, so any Unicode name works); ASCII-only clients may still send `X-File-Name`. `X-Content-SHA256` (optional) is verified. Returns the file (`201`). |
| `GET /api/v1/files` | `file:read` | One page. `q` (part of the name, case-insensitive; `%` and `_` are literal), `sort` (`created_at`, `name`, `size`), `order` (`asc`/`desc`; default newest or largest first, A to Z for names), `limit` (1 to 200, default 50) and `cursor` (the last file id of the previous page). A full page means there may be more; an unknown cursor answers `400` ("reload the list"). |
| `GET /api/v1/files/usage` | `file:read` | Bytes used and file count, the workspace quota, the per-file limit, the link lifetime, the accepted types (a sentence and the extensions) and whether the caller may upload (`can_upload`) and manage every file (`can_manage_all`). Clients check uploads against it before sending. |
| `GET /api/v1/files/{id}` | `file:read` | Metadata. |
| `PATCH /api/v1/files/{id}` `{"name": ...}` | `file:upload`, and the uploader or `file:manage` | Rename. The extension must stay the same (the type never changes); `updated_by`/`updated_at` record the rename. |
| `DELETE /api/v1/files/{id}` | `file:upload`, and the uploader or `file:manage` | Delete metadata, then bytes, then index chunks. |
| `GET /api/v1/files/{id}/content` | `file:read` | The bytes as an attachment (bearer-authenticated clients). |
| `GET /api/v1/files/{id}/preview` | `file:read` | Text, Markdown and data files as JSON text, at most 256 KiB (`truncated` says when more exists). Other kinds answer `422`. |
| `POST /api/v1/files/{id}/links` `{"disposition": "attachment"\|"inline"}` | `file:read` | A short-lived signed link (`url`, `expires_at`). `inline` only for previewable kinds. |
| `GET /api/v1/files/links/{token}` | the link | The bytes, with no bearer token. Expired or tampered links answer `410`. |
| `POST /api/v1/files/{id}/reindex` | `file:upload` | Index the file for retrieval again (spends model budget for text) and return its state. |

Every file row carries `kind` (`text`, `markdown`, `data`, `image`, `pdf`, `document`, `archive`; null for a type uploaded before the allow-list), `previewable`, `can_manage` (whether the caller may rename and delete it), `created_by`/`created_at`, `updated_by`/`updated_at`, and `index` (below).

### Permissions

| Role | Read, preview, download | Upload, re-index | Rename and delete |
|---|---|---|---|
| viewer | yes | no | no |
| member | yes | yes | their own uploads |
| owner | yes | yes | any file (`file:manage`) |

`file:read`, `file:upload` and `file:manage` are also offered in role templates. Refusals say who can do it ("Only the person who uploaded this file or a workspace owner can rename or delete it."). Before this change members could delete anyone's file; that is now owner-only.

## Limits

- **Types.** The extension decides the stored type; the declared `Content-Type` only matters for a name without an extension. Accepted: text (`.txt`, `.log`, `.md`, `.rst`, `.csv`, `.tsv`, `.json`, `.yaml`/`.yml`, `.xml`), images (`.png`, `.jpg`/`.jpeg`, `.gif`, `.webp`), PDF, Office and OpenDocument files (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp`) and `.zip`. Everything else, including HTML, SVG and executables, is refused with `415` and a sentence listing what is accepted. Binary types must start with their signature (a "PNG" that is HTML is refused); text must not contain NUL bytes.
- **Size.** `ANUM_MAX_UPLOAD_BODY_BYTES` (25 MiB) per file, enforced from `Content-Length` and while streaming (`413` with the limit in the message). Empty files are refused (`422`).
- **Quota.** `ANUM_FILES_WORKSPACE_QUOTA_BYTES` (2 GiB) per workspace. An upload that would go over answers `413 payload_too_large` with the used and allowed sizes and a `details[0].type` of `storage_quota`. The check runs before the bytes are stored and again while inserting the metadata under a per-workspace transaction advisory lock, so concurrent uploads cannot overshoot; the loser's bytes are deleted.
- **Names.** At most 255 characters, no `/`, `\` or control characters.

## Downloads and previews

Short-lived links are HMAC-SHA256 signatures over tenant, workspace, user, file id, SHA-256, disposition and expiry (`ANUM_FILES_LINK_TTL_SECONDS`, default 300, 30 to 3600). The key is derived from `ANUM_SECRETS_KEY`: the first key signs, every key verifies, so a key rotation keeps unexpired links valid. Without a secrets key (local and test only) a per-process key is used. Serving a link scopes the metadata read to the link's tenant and workspace under RLS, and with OIDC the minting user must still be an active member. A link names a content digest, so it stops working once the file is deleted. Links are bearer credentials in a URL: they are short-lived, the API sends `Referrer-Policy: no-referrer`, and responses are `Cache-Control: private, no-store`.

Responses never execute anything: downloads are `attachment` with an RFC 6266 file name (ASCII fallback plus `filename*=UTF-8''...`), served as `application/octet-stream`; inline responses exist only for previewable kinds, text kinds are sent as `text/plain`, and every link response carries `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox` plus the API's `nosniff`.

Clients render previews from data, never as documents:

- **Text, data, Markdown**: from `GET /preview`. Markdown is parsed into a tree (headings, paragraphs, lists, quotes, code, rules, emphasis and links) and rendered as React elements; raw HTML stays text, and only `http(s):` and `mailto:` links become links (`apps/web/src/lib/files.ts`).
- **Images**: bytes from an inline link, shown through a `blob:` URL in an `<img>` (allowed by the web CSP's `img-src blob:`).
- **PDF**: pdf.js (`pdfjs-dist`, legacy build, loaded only when a PDF is opened) draws pages onto a canvas from the bytes. pdf.js's core API runs no PDF JavaScript, XFA forms are off, and no plugin or foreign frame is involved. Its worker is a same-origin module (`worker-src 'self'`).
- Office documents and archives have no preview; the panel offers a download.

## Malware scanning

ANUM ships no scanner and does not pretend to scan. The hook point is `anum_api.file_policy.FileScanner`: a `scan(name, content_type, content) -> ScanVerdict(clean, reason)` called with every upload's bytes after the type and checksum checks and before anything is stored. Install one at startup with `set_file_scanner(...)`, for example an adapter that streams the bytes to a ClamAV `clamd` (`INSTREAM`) or a cloud scanning API. A verdict with `clean=False` refuses the upload (`422`, the reason shown to the uploader); an exception fails it with `503` and nothing stored. The scanner runs inside the request, so give it a timeout well under the HTTP timeout. Files uploaded before a scanner was installed are not rescanned; to scan them, list the workspace's files and run the scanner over their bytes.

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

Text files (`text/*`, JSON, YAML, XML, CSV, or a `.txt`/`.md`/`.csv`/`.json`/`.yaml`/`.xml`/`.log`/`.rst` name) up to 2 MiB of UTF-8 are indexed for retrieval into task prompts right after their metadata is committed; other files get a `skipped` index row (`not_text`, `not_utf8`, `too_large`). Indexing is best effort: a failed embedding is recorded on the index row and never fails the upload. Each file row reports `index`: `status` (`indexed`, `stale` when indexed with an earlier embedding model, `failed`, `skipped`, or `pending` when no index row exists), `error`, `chunk_count`, `embedding_model` and `indexed_at` (`index` is null if the index could not be read; the list still answers). `POST /api/v1/files/{id}/reindex` retries one file and `POST /api/v1/retrieval/index` the whole workspace. Deleting a file removes its chunks, and search ignores chunks of a file without metadata in any case. See [Memory](memory.md#retrieval).

## Web Files view

`apps/web/src/FilesView.tsx` (the desktop app renders the same build), helpers in `apps/web/src/lib/files.ts`, PDF pages in `apps/web/src/PdfPreview.tsx`:

- **Storage**: a meter of bytes used against the quota (amber from 80%, red from 95%), the per-file limit and the accepted types.
- **Upload**: several files at once. Each upload shows a progress bar and a Cancel button (XMLHttpRequest, aborted on cancel; nothing is stored). Files the API would refuse (empty, too large, unaccepted type, over quota) are refused before sending with the same sentence; server refusals show the API's message. Viewers see a disabled Upload button and who can upload.
- **List**: name, type, size, uploader ("You" for the caller) and time, and the index state as a badge. Search by name, six sort orders, and "Load more files" with the keyset cursor (a cursor whose file was deleted reloads the list). Empty workspace, no search results, loading, a failed load with Retry, and `403` (who can see files) each have their own state. Delete asks to confirm in the row.
- **Detail panel** (a row's name, or "Sources used" on a run, [Memory](memory.md#at-run-time)): kind, size, upload time and user, the last rename, the index state with its reason, id and SHA-256; Download, Rename (inline form that keeps the extension), Re-index (when the file failed, is stale or was never indexed) and Delete with confirm, shown by role. Then the preview (text, formatted or source Markdown, image, or PDF with page controls). It handles loading, a file deleted since ("This file no longer exists in this workspace"), `403` and other errors with Retry.

Tests: `apps/web/test/files.test.ts` (paths, checks, labels, the Markdown reader), `apps/web/e2e/files.spec.ts` (list, search, sort and paging, uploads with refusals, quota and cancel, Markdown, image and PDF previews, rename, re-index, delete, the viewer role, `403`, errors, and no horizontal overflow from 1440x900 to 390x844), `apps/web/e2e/sources-used.spec.ts`.

## Mobile

The Flutter app's Resources, Files tab (`apps/mobile/lib/features/workspace/files_screen.dart`, state in `files_controller.dart`, API in `files_repository.dart`) has the same behaviour: the storage meter and limits, the system picker for several files, streamed uploads with progress and Cancel (`http.StreamedRequest`; cancelling closes that upload's connection), search, sort and "Load more files", and a detail screen with the index state, a text or image preview, Download (the save dialog, through a short-lived link), Rename (a dialog that keeps the extension), Re-index and Delete with confirm, shown by role. PDFs, Office files and archives are downloaded and opened in another app; the app has no PDF renderer. "Sources used" on a run opens the same detail screen. Tests: `apps/mobile/test/files_test.dart`, including layouts at 320, 390, 768, 1024 and 1440 wide at 1x and 2x text.

## Tests

- `services/api/tests/test_files_management.py`: types and signatures, size, empty and name refusals, the quota (including a concurrent upload), the malware hook, search, sort and keyset paging, index state, rename and delete per role, viewers, previews, links (tampering, expiry, another workspace, deleted files, key rotation), re-index, CORS for the upload headers and `PATCH`.
- `services/api/tests/test_postgres_files.py` (database): paging, search and sort in PostgreSQL, the rename columns and indexes, and the quota under the advisory lock, as the non-owner app role.
- `services/api/tests/test_object_storage.py`: the S3 adapter against moto and, `s3`-marked, against a real endpoint at `ANUM_TEST_S3_ENDPOINT` (default `http://127.0.0.1:9000`, credentials `ANUM_TEST_S3_ACCESS_KEY`/`ANUM_TEST_S3_SECRET_KEY`), for example `docker compose -f infra/docker/compose.yaml up s3`.

## Not done yet

- No scanner is installed by default (see [Malware scanning](#malware-scanning)); deployments that need scanning must add one.
- The quota is one value for every workspace (`ANUM_FILES_WORKSPACE_QUOTA_BYTES`); there is no per-workspace override or owner screen for it.
- Name search is a bounded `ILIKE` scan within one workspace; very large workspaces would want a trigram index.
- Office documents, archives and (on mobile) PDFs have no in-app preview, and only text files are indexed for retrieval.
- Files have no versions: an upload with the same name is a new file; replacing content means delete and upload.
