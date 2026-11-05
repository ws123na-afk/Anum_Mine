// Purchases over the live API (docs/purchases.md#api). The server checks purchase:read / purchase:write on every route,
// and finance:manage for linking a payment or a refund; a personal purchase of someone else answers 404.
import type {
  FinanceAccount, PurchaseCashInput, PurchaseDetail, PurchaseEvent, PurchaseFilter, PurchaseInput, PurchaseLinkInput, PurchaseMoveInput,
  PurchaseOverview, PurchasePage, PurchasePatch, PurchasePaymentCandidate, PurchasePaymentResult, PurchasePreferences, PurchaseRefundResult,
  PurchaseReturn, PurchaseReturnInput, PurchaseReturnPatch, PurchaseShipment, PurchaseShipmentInput, Purchase,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';

const BASE = '/api/v1/purchases';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown, key?: string) => apiRequest<T>(`${BASE}${path}`, {
  method, body: body === undefined ? undefined : JSON.stringify(body), headers: key ? { 'Idempotency-Key': key } : undefined,
});
const id = encodeURIComponent;

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const listPurchases = (q: string, status: PurchaseFilter | '', archived: boolean, offset: number, limit = 20) =>
  get<PurchasePage<Purchase>>(query({ q, status, archived: archived || null, offset, limit }));
export const getPurchase = (purchaseId: string) => get<PurchaseDetail>(`/${id(purchaseId)}`);
export const createPurchase = (body: PurchaseInput, key: string) => send<PurchaseDetail & { replayed: boolean }>('POST', '', body, key);
export const updatePurchase = (purchaseId: string, body: PurchasePatch) => send<PurchaseDetail>('PATCH', `/${id(purchaseId)}`, body);
export const archivePurchase = (purchaseId: string, version: number, archived: boolean) => send<PurchaseDetail>('POST', `/${id(purchaseId)}/archive`, { version, archived });
export const deletePurchase = (purchaseId: string) => apiRequestVoid(`${BASE}/${id(purchaseId)}`, { method: 'DELETE' });
export const moveStatus = (purchaseId: string, body: PurchaseMoveInput) => send<PurchaseDetail>('POST', `/${id(purchaseId)}/status`, body);
export const listEvents = (purchaseId: string, offset: number, limit = 20) => get<PurchasePage<PurchaseEvent>>(`/${id(purchaseId)}/events${query({ offset, limit })}`);
export const addNote = (purchaseId: string, note: string) => send<PurchaseEvent>('POST', `/${id(purchaseId)}/notes`, { note });

export const overview = () => get<PurchaseOverview>('/overview');
export const getPreferences = () => get<PurchasePreferences>('/preferences');
export const savePreferences = (body: Omit<PurchasePreferences, 'owner_user_id' | 'updated_at'>) => send<PurchasePreferences>('PUT', '/preferences', body);
export const listReturns = (open: boolean, offset: number, limit = 20) => get<PurchasePage<PurchaseReturn>>(`/returns${query({ open, offset, limit })}`);

export const addShipment = (purchaseId: string, body: PurchaseShipmentInput) => send<PurchaseShipment>('POST', `/${id(purchaseId)}/shipments`, body);
export const updateShipment = (shipmentId: string, body: PurchaseShipmentInput & { version: number }) => send<PurchaseShipment>('PATCH', `/shipments/${id(shipmentId)}`, body);
export const deleteShipment = (shipmentId: string) => apiRequestVoid(`${BASE}/shipments/${id(shipmentId)}`, { method: 'DELETE' });

export const createReturn = (purchaseId: string, body: PurchaseReturnInput, key: string) => send<PurchaseReturn>('POST', `/${id(purchaseId)}/returns`, body, key);
export const updateReturn = (returnId: string, body: PurchaseReturnPatch) => send<PurchaseReturn>('PATCH', `/returns/${id(returnId)}`, body);
export const moveReturn = (returnId: string, body: PurchaseMoveInput) => send<PurchaseReturn>('POST', `/returns/${id(returnId)}/status`, body);
export const deleteReturn = (returnId: string) => apiRequestVoid(`${BASE}/returns/${id(returnId)}`, { method: 'DELETE' });

export const candidates = (kind: 'payment' | 'refund', q: string, around?: string | null, amount?: string | null) =>
  get<{ items: PurchasePaymentCandidate[] }>(`/payments${query({ kind, q, around, amount })}`);
export const linkPayment = (purchaseId: string, body: PurchaseLinkInput, key: string) => send<PurchasePaymentResult>('POST', `/${id(purchaseId)}/payment`, body, key);
export const recordCash = (purchaseId: string, body: PurchaseCashInput, key: string) => send<PurchasePaymentResult>('POST', `/${id(purchaseId)}/payment/cash`, body, key);
export const unlinkPayment = (purchaseId: string) => apiRequestVoid(`${BASE}/${id(purchaseId)}/payment`, { method: 'DELETE' });
export const linkRefund = (returnId: string, body: PurchaseLinkInput, key: string) => send<PurchaseRefundResult>('POST', `/returns/${id(returnId)}/refunds`, body, key);
export const recordCashRefund = (returnId: string, body: PurchaseCashInput, key: string) => send<PurchaseRefundResult>('POST', `/returns/${id(returnId)}/refunds/cash`, body, key);
export const unlinkRefund = (returnId: string, linkId: string) => apiRequestVoid(`${BASE}/returns/${id(returnId)}/refunds/${id(linkId)}`, { method: 'DELETE' });

export const financeAccounts = () => apiRequest<FinanceAccount[]>('/api/v1/finance/accounts', { method: 'GET' });

/** Download the purchases as CSV. */
export async function exportCsv(archived = false): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/export.csv${query({ archived: archived || null })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), 'anum-purchases.csv'));
}
