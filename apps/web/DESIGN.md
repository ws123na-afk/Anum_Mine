# ANUM web — design system

Ops-console dashboard: calm, high-density, trustworthy. Built with plain CSS
custom properties (no UI framework), React + TypeScript + Vite.

## Tokens

All tokens live in `src/styles.css` under `:root` (light) and
`@media (prefers-color-scheme: dark)` (dark, desaturated — not inverted).
Components must reference tokens (`var(--color-text-muted)`), never raw hex.

- **Color**: `--color-bg`, `--color-surface`, `--color-surface-sunken`,
  `--color-border`, `--color-border-strong`, `--color-text`,
  `--color-text-muted`, `--color-text-subtle`, `--color-accent-500/600/100`.
- **Semantic status** (pair color with text/icon, never color alone):
  `--color-success-bg/text`, `--color-warning-bg/text`,
  `--color-danger-bg/text`, `--color-info-bg/text`.
- **Risk levels** (`low`/`medium`/`high`/`blocked`):
  `--color-risk-{level}-bg/text`.
- **Elevation**: `--shadow-sm/md/lg`. **Radius**: `--radius-sm/md/lg/pill`.
- **Spacing** (4/8px rhythm): `--space-1` (4px) through `--space-10` (40px).
- **Type**: `--text-xs` (12) `--text-sm` (13) `--text-base` (16)
  `--text-lg` (18) `--text-xl` (20) `--text-2xl` (24) `--text-3xl` (34).
- **Focus**: `--focus-ring` — applied globally via `:focus-visible`, don't
  remove it.

## Reusable classes (already in `styles.css` — do not duplicate)

- Layout: `.shell`, `.sidebar`, `.workspace`, `.topbar`, `.viewHeader`
- Surfaces: `.card`, `.panel`, `.panelHeader`
- Status: `.badge`, `.pill--success/warning/danger/info`,
  `.riskPill--low/medium/high/blocked`
- Lists: `.list`, `.listRow`, `.emptyState`, `.skeleton` (loading), `.filterBar`
- Forms: `.field`, `textarea`/`input[type=text|search]`/`select`, `.taskComposer`
- Feedback: `.notice`, `.errorNotice`
- Actions: `button`, `button.secondary`, `button.danger`, `.actions`
- Text: `.eyebrow`

Icons: `lucide-react` only (already a dependency) — stroke-based, 18–20px,
no emoji as structural icons.

## Component contract

Each dashboard view is a default-exported component:

```tsx
export default function XView({ tenantContext }: { tenantContext: TenantContext }) {
  ...
}
```

- File lives at `src/views/<Name>View.tsx`. **Do not edit `styles.css`,
  `main.tsx`, or another view's file** — each view owns exactly one new
  file so parallel work never conflicts. If you need a one-off visual
  treatment not covered by the shared classes, use an inline
  `style={{ color: 'var(--color-text-muted)' }}` referencing a token rather
  than adding a new global class.
- Fetch data with `request<T>(path, init?)` from `../lib/api` — it already
  attaches tenant headers and `content-type`. It throws `ApiError` (also
  exported from `../lib/api`) with `.message`, `.status`, `.code`,
  `.correlationId` on non-2xx responses; catch it and render `.errorNotice`.
  Backend base URL defaults to `http://localhost:8000` (`VITE_ANUM_API_URL`).
- Loading state: render 2-3 `.skeleton` blocks (respect
  `prefers-reduced-motion`, already handled centrally). Empty state: use
  `.emptyState` with a short explanation + one action if applicable, not a
  blank panel. Error state: `.errorNotice` with the message + a retry button.
- All interactive elements need visible focus states (inherited from the
  global `:focus-visible` rule — don't override `outline`/`box-shadow`
  yourself) and `aria-label` on icon-only buttons.
- Component and hook code goes directly in the view file; no new shared
  utility files.

## Shared types

From `@anum/contracts`: `TenantContext`, `Task`, `TaskStatus`, `AgentRun`,
`AgentRunStep`, `Approval`, `ApprovalStatus`, `RiskLevel`, `DomainEvent`.

`../lib/api` already wraps every backend endpoint you need — read it before
writing your own `fetch`/`request` calls:

- Core: `request`, `ApiError`, `defaultTenantContext`
- Tasks: `createAndRunTask(prompt)`, `getTask(id)`, `cancelTask(id)`
- Approvals: `listApprovals()`, `decideApproval(id, 'approve' | 'reject')`
- Agent runs: `getAgentRun(id)`
- Events: `listEvents()` (returns camelCase `DomainEvent[]`; `event.subject`
  is the related entity's id, e.g. a task id for `task.*` events)
- Memory (no `@anum/contracts` type yet — `MemoryNote` etc. are defined
  directly in `lib/api.ts`): `listMemories(filters)`, `createMemory(input)`,
  `deleteMemory(id)`, plus the `MemoryNote`/`MemoryProvenance`/
  `RetentionPolicy` types
- Raw snake_case wire types + mappers (`ApiTask`/`mapTask`,
  `ApiAgentRun`/`mapRun`, `ApiApproval`/`mapApproval`,
  `ApiDomainEvent`/`mapEvent`, `ApiMemoryNote`/`mapMemory`) are exported too,
  in case you need an endpoint combination the wrappers above don't cover.

From `@anum/contracts`: `TenantContext`, `Task`, `TaskStatus`, `AgentRun`,
`AgentRunStep`, `Approval`, `ApprovalStatus`, `RiskLevel`, `DomainEvent`.

There is **no "list all tasks" endpoint** on the backend. To show a task
list, derive task ids from `listEvents()` (filter `type === 'task.created'`,
`event.subject` is the task id, `event.payload.title` is the task title),
then optionally hydrate current status via `getTask(id)`.
