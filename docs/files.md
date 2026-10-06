# Workspace Files

Phase 2 files are tenant- and workspace-scoped objects with metadata, SHA-256 integrity, bounded uploads, and explicit authorization. The API accepts the file as the raw request body at `POST /api/v1/files`; `X-File-Name` is required and `X-Content-SHA256` is optional. Metadata, download, listing, and deletion use `/api/v1/files/{id}` and `/api/v1/files/{id}/content`.

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

With `ANUM_REPOSITORY_BACKEND=postgresql` file metadata lives in the `workspace_files` table (migration `0008_control_plane_stores`) under the tenant and workspace RLS policy: id, name, content type, size, SHA-256, object key, creator and time. The bytes never enter the database. An upload writes the object, then the metadata row; if the row cannot be written (for example the workspace is not onboarded, `409`) the object is deleted again. A delete removes the row first and the object after the transaction commits, so a listed file always has content. With `memory` the metadata is per process.

Tests: `tests/test_object_storage.py` round-trips through the S3 adapter and the files API against moto's in-process S3, and its `s3`-marked test round-trips against a real endpoint at `ANUM_TEST_S3_ENDPOINT` (default `http://127.0.0.1:9000`, credentials `ANUM_TEST_S3_ACCESS_KEY`/`ANUM_TEST_S3_SECRET_KEY`), for example `docker compose -f infra/docker/compose.yaml up s3`.

Uploads are limited to 25 MiB, reject path traversal, and verify an optional client checksum. Downloads include an ETag containing the SHA-256 digest. API authorization uses the existing memory read/create/delete permissions until dedicated file permissions are introduced.
