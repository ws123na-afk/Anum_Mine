// Approvals list: filter, search and paging (ApprovalsView.tsx, docs/approvals-and-risk.md#paging-filters-and-search),
// English. Spread into en.ts; approvalPaging.ar.ts must have exactly these keys and placeholders.
import type { Message } from './types.ts';

export const approvalPagingEn = {
  'approvals.filter.label': 'Show',
  'approvals.filter.all': 'All',
  'approvals.filter.pending': 'Pending',
  'approvals.filter.decided': 'History',
  'approvals.search.label': 'Search approvals',
  'approvals.search.placeholder': 'Tool, reason, task or target',
  'approvals.loading': 'Loading approvals...',
  'approvals.error.title': 'Approvals did not load',
  'approvals.error.text': 'Could not load approvals: {reason}',
  'approvals.denied.title': 'You cannot see approvals',
  'approvals.denied.text': 'Viewing approvals needs approval access in this workspace. Ask an owner or admin to change your role.',
  'approvals.empty.searchTitle': 'No approvals match your search',
  'approvals.empty.searchText': 'Nothing matches "{query}". Try a tool name, part of a reason or a task title.',
  'approvals.empty.decidedTitle': 'No decisions yet',
  'approvals.empty.decidedText': 'Approved, rejected and lapsed requests show here once someone decides.',
  'approvals.loadMore': 'Load more approvals',
  'approvals.loadMoreError': 'Could not load more approvals: {reason}',
  'approvals.shown': '{shown} of {total} shown',
  'approvals.shownUnknown': '{shown} shown',
} satisfies Record<string, Message>;
