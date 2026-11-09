// The business journal over the live API (/api/v1/business/books/ledger, /close, /reopen, /accountants;
// docs/business-books.md#api). The server checks every right; a 403 carries its own sentence naming who may do it.
import type {
  LedgerAccount, LedgerAccountInput, LedgerAccountList, LedgerAccountant, LedgerClose, LedgerCloseStatus, LedgerEntry, LedgerGeneral, LedgerIssue,
  LedgerJournalPage, LedgerManualInput, LedgerPostResult, LedgerSourceKind, LedgerStatements, LedgerStatus, BooksPage, BooksReview, BooksReviewKind,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';
import { query } from './businessBooksApi';

const BASE = '/api/v1/business/books';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown, key?: string) => apiRequest<T>(`${BASE}${path}`, {
  method, body: body === undefined ? undefined : JSON.stringify(body),
  headers: { 'content-type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
});

export type Span = { from?: string; to?: string };

export const listAccounts = (includeArchived = false) => get<LedgerAccountList>(`/ledger/accounts${query({ include_archived: includeArchived })}`);
export const createAccount = (body: LedgerAccountInput) => send<LedgerAccount>('POST', '/ledger/accounts', body);
export const updateAccount = (accountId: string, body: { code: string; name: string; category: string | null; archived: boolean; version: number }) =>
  send<LedgerAccount>('PATCH', `/ledger/accounts/${id(accountId)}`, body);

export const getStatus = () => get<LedgerStatus>('/ledger/status');
export const postNow = () => send<LedgerPostResult>('POST', '/ledger/post');
export const listIssues = (offset: number, limit = 25) => get<BooksPage<LedgerIssue>>(`/ledger/issues${query({ offset, limit })}`);

export const listJournal = (span: Span, sourceKind: LedgerSourceKind | '', offset: number, limit = 25) =>
  get<LedgerJournalPage>(`/ledger/journal${query({ from: span.from, to: span.to, source_kind: sourceKind, offset, limit })}`);
export const createManual = (body: LedgerManualInput, key: string) => send<LedgerEntry>('POST', '/ledger/journal', body, key);
export const reverseEntry = (entryId: string, reason: string) => send<LedgerEntry>('POST', `/ledger/journal/${id(entryId)}/reverse`, { reason });

export const accountLines = (accountId: string, span: Span, offset: number, limit = 50) =>
  get<LedgerGeneral>(`/ledger/accounts/${id(accountId)}/lines${query({ from: span.from, to: span.to, offset, limit })}`);
export const getStatements = (span: Span, compare = false) => get<LedgerStatements>(`/ledger/statements${query({ from: span.from, to: span.to, compare })}`);

export const getClose = () => get<LedgerCloseStatus>('/close');
// Accountant review (docs/business-books.md#accountant-review): owners and accountants only; members get a 403 naming who may.
export const getReview = (kind: BooksReviewKind | '' = '', offset = 0, limit = 25) => get<BooksReview>(`/review${query({ kind: kind || undefined, offset, limit })}`);
export const closeThrough = (through: string) => send<{ closed_through: string; previous: string | null; closed: LedgerClose[] }>('POST', '/close', { through });
export const reopen = (month: string, reason: string) => send<{ closed_through: string | null; previous: string; reopened: LedgerClose[] }>('POST', '/reopen', { month, reason });

export const listAccountants = () => get<{ items: LedgerAccountant[]; can_manage: boolean; me: boolean }>('/accountants');
export const addAccountant = (userId: string) => send<LedgerAccountant>('POST', '/accountants', { user_id: userId });
export const removeAccountant = (userId: string) => apiRequestVoid(`${BASE}/accountants/${id(userId)}`, { method: 'DELETE' });

/** Download the statements (trial balance, balance sheet, P&L, equity, cash flow) as CSV, with whether they are complete. */
export async function exportStatements(span: Span): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/ledger/statements.csv${query({ from: span.from, to: span.to })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), 'business-statements.csv'));
}

/** The active owners and members of this business, for naming accountants (owners only can read the member list). */
export const activeMembers = () =>
  apiRequest<{ user_id: string; role: string; active: boolean; email?: string | null }[]>(`/api/v1/workspace-members${query({ status: 'active', limit: 200 })}`, { method: 'GET' });
