// Paged approvals: list state helpers (docs/approvals-and-risk.md#paging-filters-and-search).
// Pure functions, so `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
import type { Approval } from '@anum/contracts';
import type { ApprovalFilter, ApprovalSummary } from './api.ts';
import { effectiveStatus } from './approvals.ts';

export const APPROVAL_FILTERS: readonly ApprovalFilter[] = ['all', 'pending', 'decided'];
export const APPROVAL_PAGE_SIZE = 25;
export const APPROVAL_SEARCH_DELAY_MS = 300;
export const APPROVAL_SEARCH_MAX_CHARS = 200;

/** Append a page to what is loaded, keeping the first copy of an id (a refresh can overlap a page). */
export function mergeApprovals(current: readonly Approval[], next: readonly Approval[]): Approval[] {
  const seen = new Set(current.map((approval) => approval.id));
  return [...current, ...next.filter((approval) => !seen.has(approval.id))];
}

/** Split loaded approvals into the ones waiting for a decision now and the decided history, in the order the server sent. */
export function splitApprovals(items: readonly Approval[], now: Date = new Date()): { pending: Approval[]; history: Approval[] } {
  const pending: Approval[] = [];
  const history: Approval[] = [];
  for (const approval of items) (effectiveStatus(approval, now) === 'pending' ? pending : history).push(approval);
  return { pending, history };
}

/** Which empty state to show for the loaded result. */
export type ApprovalEmpty = 'search' | 'pending' | 'decided' | 'none';

export function emptyKind(filter: ApprovalFilter, query: string): ApprovalEmpty {
  if (query.trim()) return 'search';
  if (filter === 'pending') return 'pending';
  if (filter === 'decided') return 'decided';
  return 'none';
}

/** The search text as sent: trimmed and capped at what the API accepts. */
export function searchText(value: string): string {
  return value.trim().slice(0, APPROVAL_SEARCH_MAX_CHARS);
}

/** Total approvals the summary counts, for "N loaded of M". */
export function summaryTotal(summary: ApprovalSummary | null, filter: ApprovalFilter): number | null {
  if (!summary) return null;
  const decided = summary.approved + summary.rejected + summary.expired;
  return filter === 'pending' ? summary.pending : filter === 'decided' ? decided : summary.pending + decided;
}

/**
 * A fingerprint of the shell's pending approvals that changes whenever a card would look or act
 * differently: status, who has approved, the chain's step, and what this person may do. The
 * Approvals page reloads its first page when it changes.
 */
export function approvalSignal(items: readonly Approval[]): string {
  return items
    .map((a) => [
      a.id,
      a.status,
      a.requiredApprovals,
      (a.approvers ?? []).map((approver) => approver.userId).join('+'),
      a.routing?.currentStep ?? '',
      a.routing?.canApprove ?? '',
      a.routing?.canReject ?? '',
      a.routing?.refusal ?? '',
      a.expiresAt ?? '',
    ].join(':'))
    .join(',');
}

/** Read every page of a cursor-paged list, up to `maxPages` (a safety stop). */
export async function collectPages<T>(
  fetchPage: (cursor: string | null) => Promise<{ items: T[]; nextCursor: string | null }>,
  maxPages = 20,
): Promise<{ items: T[]; complete: boolean }> {
  const items: T[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < maxPages; page += 1) {
    const result = await fetchPage(cursor);
    items.push(...result.items);
    cursor = result.nextCursor;
    if (!cursor) return { items, complete: true };
  }
  return { items, complete: false };
}
