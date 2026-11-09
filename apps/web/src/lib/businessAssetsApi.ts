// The fixed asset register, loan schedules, the period-end revaluation and the group balance sheet over the live API
// (/api/v1/business/books/assets, /loans, /ledger/revaluation, /group/balance-sheet; docs/business-books.md#api). The server
// checks every right; a 403 carries its own sentence naming who may do it.
import type {
  AssetImpairment, BusinessLoan, BusinessLoanDetail, BusinessLoanInput, BusinessLoanList, FixedAssetDetail, FixedAssetList, FixedAssetUpdate,
  GroupBalanceSheet, LoanRepayment, LoanRepaymentInput, RevaluationPreview, RevaluationResult,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { query } from './businessBooksApi';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';

const BASE = '/api/v1/business/books';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown, key?: string) => apiRequest<T>(`${BASE}${path}`, {
  method, body: body === undefined ? undefined : JSON.stringify(body),
  headers: { 'content-type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
});
const newKey = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`);

export type AssetQuery = { from?: string; to?: string; status?: 'active' | 'disposed' | 'all'; q?: string; offset?: number; limit?: number };

export const listAssets = (params: AssetQuery) => get<FixedAssetList>(`/assets${query(params)}`);
export const getAsset = (assetId: string) => get<FixedAssetDetail>(`/assets/${id(assetId)}`);
export const updateAsset = (assetId: string, body: FixedAssetUpdate) => send<FixedAssetDetail>('PATCH', `/assets/${id(assetId)}`, body);
export const disposeAsset = (assetId: string, body: { disposed_on: string; kind: 'sale' | 'write_off'; proceeds: string; version: number }) =>
  send<FixedAssetDetail>('POST', `/assets/${id(assetId)}/dispose`, body);
export const requestImpairment = (assetId: string, body: { entry_date: string; amount: string; reason: string }) =>
  send<AssetImpairment>('POST', `/assets/${id(assetId)}/impairments`, body);
export const decideImpairment = (assetId: string, impairmentId: string, decision: 'approve' | 'reject', version: number) =>
  send<AssetImpairment>('POST', `/assets/${id(assetId)}/impairments/${id(impairmentId)}/${decision}`, { version });

export const listLoans = (status: 'active' | 'void' | 'all', offset: number, limit = 25) => get<BusinessLoanList>(`/loans${query({ status, offset, limit })}`);
export const getLoan = (loanId: string) => get<BusinessLoanDetail>(`/loans/${id(loanId)}`);
export const createLoan = (body: BusinessLoanInput) => send<BusinessLoan>('POST', '/loans', body, newKey());
export const recordRepayment = (loanId: string, body: LoanRepaymentInput) => send<LoanRepayment>('POST', `/loans/${id(loanId)}/repayments`, body, newKey());
export const voidRepayment = (loanId: string, repaymentId: string, reason: string) =>
  send<LoanRepayment>('POST', `/loans/${id(loanId)}/repayments/${id(repaymentId)}/void`, { reason });
export const voidLoan = (loanId: string, reason: string) => send<BusinessLoan>('POST', `/loans/${id(loanId)}/void`, { reason });

export const getRevaluation = (periodEnd: string) => get<RevaluationPreview>(`/ledger/revaluation${query({ period_end: periodEnd })}`);
export const revalue = (periodEnd: string) => send<RevaluationResult>('POST', '/ledger/revaluation', { period_end: periodEnd });

export function getGroupBalanceSheet(params: { as_of?: string; currency?: string; workspaces?: string[] }): Promise<GroupBalanceSheet> {
  const search = new URLSearchParams(query({ as_of: params.as_of, currency: params.currency }).slice(1));
  for (const workspace of params.workspaces ?? []) search.append('workspace', workspace);
  const text = search.toString();
  return get<GroupBalanceSheet>(`/group/balance-sheet${text ? `?${text}` : ''}`);
}

/** The register and its roll-forward as CSV. */
export async function exportAssets(params: { from?: string; to?: string }): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/assets.csv${query(params)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), 'fixed-assets.csv'));
}
