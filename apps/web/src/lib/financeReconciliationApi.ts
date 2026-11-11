// Finance, Reconciliation (docs/finance.md#reconciliation-sessions): the API calls. Every record is the caller's own;
// the server checks finance:read and finance:manage. Pure helpers live in financeReconciliation.ts.
import type {
  FinanceReconCorrectionPreview, FinanceReconDetail, FinanceReconHealthPage, FinanceReconInput, FinanceReconPage, FinanceReconQueuePage,
  FinanceReconResolution, FinanceReconResolutionInput,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';
import { query } from './financeApi';
import type { SeverityFilter } from './financeReconciliation';

const BASE = '/api/v1/finance/reconciliations';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body: unknown) => apiRequest<T>(`${BASE}${path}`, {
  method, body: JSON.stringify(body), headers: { 'content-type': 'application/json' },
});

export const listReconciliations = (limit: number, offset: number) => get<FinanceReconPage>(query({ limit, offset }));
export const reconciliationHealth = () => get<FinanceReconHealthPage>('/health');
export const reconciliationExceptions = (limit: number, offset: number, order?: { sort: string; desc: boolean }) =>
  get<FinanceReconQueuePage>(`/exceptions${query({ limit, offset, sort: order?.sort, desc: order?.desc ? 'true' : undefined })}`);

/** Downloads every open exception as a CSV made by the server (formula-safe) and returns the file name. */
export async function exportExceptions(order?: { sort: string; desc: boolean }): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/exceptions/export${query({ sort: order?.sort, desc: order?.desc ? 'true' : undefined })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), 'anum-reconciliation-exceptions.csv'));
}
export const getReconciliation = (recId: string, p: { severity?: SeverityFilter; kind?: string; limit: number; offset: number }) =>
  get<FinanceReconDetail>(`/${id(recId)}${query({ severity: p.severity === 'all' ? undefined : p.severity, kind: p.kind, limit: p.limit, offset: p.offset })}`);
export const createReconciliation = (body: FinanceReconInput) => send<FinanceReconDetail>('POST', '', body);
export const updateReconciliation = (recId: string, body: Partial<FinanceReconInput> & { version: number }) => send<FinanceReconDetail>('PATCH', `/${id(recId)}`, body);
export const deleteReconciliation = (recId: string) => apiRequestVoid(`${BASE}/${id(recId)}`, { method: 'DELETE' });
export const resolve = (recId: string, body: FinanceReconResolutionInput) => send<FinanceReconResolution>('POST', `/${id(recId)}/resolutions`, body);
export const undoResolution = (recId: string, resolutionId: string) => apiRequestVoid(`${BASE}/${id(recId)}/resolutions/${id(resolutionId)}`, { method: 'DELETE' });
export const previewCorrection = (recId: string, statementRef: string) =>
  send<FinanceReconCorrectionPreview>('POST', `/${id(recId)}/corrections/preview`, { statement_ref: statementRef });
export const postCorrection = (recId: string, preview: FinanceReconCorrectionPreview) =>
  send<{ transaction_id: string; resolution_id: string }>('POST', `/${id(recId)}/corrections`, { statement_ref: preview.statement_ref, payload_hash: preview.payload_hash });
export const completeReconciliation = (recId: string, version: number) => send<FinanceReconDetail>('POST', `/${id(recId)}/complete`, { version });
export const reopenReconciliation = (recId: string, version: number, reason: string) => send<FinanceReconDetail>('POST', `/${id(recId)}/reopen`, { version, reason });
