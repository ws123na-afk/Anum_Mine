// Mock of the paged approval routes for e2e specs (docs/approvals-and-risk.md#paging-filters-and-search):
// `GET /api/v1/approvals/page` and `GET /api/v1/approvals/summary`, computed from a plain list of
// API approvals the way the API does (pending first, then newest first; the cursor is an offset).
type ApiApproval = Record<string, unknown>;

const isPending = (approval: ApiApproval) =>
  approval.status === 'pending' && (!approval.expires_at || Date.parse(String(approval.expires_at)) > Date.now());

export function approvalPagesResponse(url: URL, list: readonly ApiApproval[]): unknown | undefined {
  if (url.pathname === '/api/v1/approvals/summary') {
    const count = (status: string) => list.filter((a) => (status === 'pending' ? isPending(a) : !isPending(a) && a.status === status)).length;
    const lapsed = list.filter((a) => a.status === 'pending' && !isPending(a)).length;
    return { pending: count('pending'), approved: count('approved'), rejected: count('rejected'), expired: count('expired') + lapsed };
  }
  if (url.pathname !== '/api/v1/approvals/page') return undefined;
  const status = url.searchParams.get('status') ?? 'all';
  const query = (url.searchParams.get('query') ?? '').toLowerCase();
  const taskId = url.searchParams.get('task_id');
  const limit = Number(url.searchParams.get('limit') ?? 25);
  const offset = Number(url.searchParams.get('cursor') ?? 0);
  const rows = list
    .filter((a) => (status === 'pending' ? isPending(a) : status === 'decided' ? !isPending(a) : true))
    .filter((a) => (taskId ? a.task_id === taskId : true))
    .filter((a) => (query ? [a.id, a.action, a.reason, a.target].some((v) => String(v ?? '').toLowerCase().includes(query)) : true))
    .sort((a, b) => Number(isPending(b)) - Number(isPending(a)) || String(b.created_at).localeCompare(String(a.created_at)));
  const items = rows.slice(offset, offset + limit);
  return { items, next_cursor: offset + limit < rows.length ? String(offset + limit) : null };
}
