# ANUM Sales CRM API and integration release

This change exposes the previously developed Sales CRM domain and PostgreSQL storage through authenticated, workspace-scoped REST endpoints. It reuses ANUM's existing session identity, role permissions, transactional database session and row-level security rather than adding a separate user system.

## Endpoints

| Method | Path | Behavior |
| --- | --- | --- |
| GET | /api/v1/sales/leads | List up to 100 workspace leads, filter by status |
| GET | /api/v1/sales/leads/{id} | Read one workspace lead |
| POST | /api/v1/sales/leads/inbound | Authenticated lead import with incoming-message idempotency |
| PATCH | /api/v1/sales/leads/{id} | Edit details, budgets and assignment with expected revision |
| POST | /api/v1/sales/leads/{id}/transition | Validate status transitions and record event |
| POST | /api/v1/sales/leads/{id}/quotes | Save quoted item prices as an approval-required draft |
| POST | /api/v1/sales/leads/{id}/followups | Propose, but never silently execute, a future follow-up |
| GET | /api/v1/sales/overview | Bounded funnel summary for latest 100 leads |

## Security and implementation boundary

- Every request requires ANUM workspace identity. Reads require WhatsApp read permission; edits require both Marketing write and WhatsApp reply permissions.
- Queries explicitly scope to tenant and workspace and also operate under transaction-local PostgreSQL RLS.
- Quotes and follow-ups are proposals, not sent messages or scheduled tasks.
- Campaign marker attribution is unverified customer input; inbound chat does not grant marketing consent.
- Manual authenticated import is supported; it is not yet a webhook worker wired to Meta.
- The dashboard summary covers up to 100 latest records and must not be interpreted as a complete organization-wide analytics export.
- Cross-worker duplicate message writes are protected by a PostgreSQL uniqueness constraint. Concurrent duplicate imports return a conflict instead of creating two leads.
- No Meta access token, phone number credential or personal account secrets are stored in Git.

## Still required for a production-complete marketing product

1. Wire verified Meta inbound messages into a durable ingestion worker and transactional CRM import.
2. Add persistent approved campaign ID links and conversion analytics beyond self-reported markers.
3. Build sales web and Flutter screens with Arabic RTL support, searchable customer history and agent assignment.
4. Add owner-configurable low-risk auto-response execution through existing ToolPolicy and suppression checks.
5. Add approval-based quotation delivery and follow-up execution, budget caps, consent ledger and kill switch.
6. Verify Alembic migrations, database/HTTP integration tests, CI, CodeQL and mobile/web end-to-end tests in staging, then production.
7. Activate Meta Business coexistence/signup, webhook registration and messaging numbers through the owner's provider account.

This release is a working API implementation in source control, not proof of a live deployed or connected service.
