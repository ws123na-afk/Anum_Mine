# ANUM MINE branch reconciliation, October 22 2026

Branch counts are compared against `main` after PRs 205-209 were merged.
Ahead commits are **not proof of missing functionality** when an earlier change
was cherry-picked or reimplemented on main. Do not merge legacy office branches
wholesale because that would overwrite newer integrated components.

| Branch | Ahead | Behind | Decision |
|---|---:|---:|---|
| workstream/office-quality | 0 | 169 | Safe deletion candidate after confirming no active Codex writer |
| workstream/office-production-backend | 0 | 101 | Safe deletion candidate after confirming no active writer |
| feature/anum-mine-autonomous-meeting-runtime-20261022 | 0 | 27 | Safe deletion candidate, included via PR 209 |
| workstream/office-live-data | 13 | 169 | Preserve, review changed UI and presence logic; main includes related work through PR 207 |
| workstream/office-space-registry | 9 | 169 | Preserve, review old room registry; main includes newer server-backed registry |
| workstream/office-3d-scene | 3 | 169 | Preserve until solar behavior and tests reconciled |
| feature/hq-agent-meeting-sessions-20261022 | 2 | 44 | PR 208 closed as superseded; compare content against PR 209 before deleting |

## Current branch cleanup state

No remote branch has been deleted by this change. Branch deletion requires
an authorized GitHub ref-delete action and confirmation that four concurrently
working Codex agents are not targeting those refs. Existing issue tickets also
contain acceptance requirements that should not be silently closed.

## P3/P4 delivery in this branch

- Migration 0099 adds validated scene coordinates and dimensions to scoped
  `office_rooms`, retaining generated placement for records without geometry.
- Permissioned optimistic PATCH `/api/v1/office-spaces/rooms/{id}/geometry`
  persists room placement and serializes floor edits, with collision rejection.
- Server-backed office editor exposes X, Z, width and depth and re-fetches
  canonical records after save.
- Live headquarters renders persisted geometry and refreshes authenticated
  task/agent evidence every ten seconds and on window focus/visibility.
- This is evidence refresh, not proof that an employee is physically online.
- Visual parity requires a signed-off photographic reference; GPU screenshots
  and release checks remain required.

## Required verification

Apply migration 0099 in staging and test concurrent room edits, overlap
errors, cross-tenant isolation, room revisions, JSON serialization, and mobile
layout; run TypeScript, browser, API database and security suites before
production. Compare all branch files before authorized deletions.
