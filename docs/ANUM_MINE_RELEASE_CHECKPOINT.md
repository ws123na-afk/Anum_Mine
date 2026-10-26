# ANUM MINE completion checkpoint

## Shipped as source changes in this PR

- A workspace-scoped unattended meeting supervisor discovers active/paused
  meetings created by its explicitly configured owner.
- It starts background tasks for those sessions, handles graceful shutdown,
  imposes a concurrency limit and uses the existing durable PostgreSQL
  meeting worker. UI activation is detected by polling the database.
- Contract tests check discovery scope and supervisor limits.

## Deployment

Run a dedicated supervised process for each explicitly enrolled workspace owner:

```bash
python -m anum_api.meeting_supervisor --tenant TENANT_ID \
  --workspace WORKSPACE_ID --owner OWNER_USER_ID \
  --poll 5 --max-meetings 8
```

Keep this under a process supervisor (systemd, Kubernetes, etc.) with restarts,
secret management and scoped network access. Meeting start from the UI works
only after the corresponding supervisor process is provisioned. This does
not automatically deploy supervisors for new tenants or owners; provisioning
remains an operator action.

## Deferred checks (do not mark passed)

- Real provider billing, PostgreSQL concurrency, pause/cancel and crash-recovery.
- Persistent drag-and-drop room geometry with collision/capacity validation.
- Full employee presence events and end-to-end freshness tests.
- Browser, accessibility, API/database integration and security CI.
- Production deployment, smoke tests and end-user visual matching.

Keep incomplete items visible rather than inferring release readiness from merge.
