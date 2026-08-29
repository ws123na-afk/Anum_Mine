# Workspace Files

Phase 2 files are tenant- and workspace-scoped objects with metadata, SHA-256 integrity, bounded uploads, and explicit authorization. The API accepts the file as the raw request body at `POST /api/v1/files`; `X-File-Name` is required and `X-Content-SHA256` is optional. Metadata, download, listing, and deletion use `/api/v1/files/{id}` and `/api/v1/files/{id}/content`.

The default local adapter writes content-addressed objects below `.anum-data/objects`. `ObjectStorage` separates metadata operations from bytes, and `S3ObjectStorage` accepts a boto3-compatible client for MinIO or S3. Production wiring must create the bucket, configure server-side encryption and lifecycle policy, inject the S3 client, and persist file metadata in PostgreSQL.

Uploads are limited to 25 MiB, reject path traversal, and verify an optional client checksum. Downloads include an ETag containing the SHA-256 digest. API authorization uses the existing memory read/create/delete permissions until dedicated file permissions are introduced.
