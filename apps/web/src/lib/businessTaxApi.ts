// Business tax over the live API (/api/v1/business/books/tax, docs/business-tax.md). Reading needs business:read; the
// settings, rates and e-invoice generation need an owner or an accountant (a 403 carries its own sentence). Nothing here
// files a return or calls ZATCA.
import type {
  BooksPage, TaxOverview, TaxRate, TaxRateInput, TaxReturn, TaxReturnDocument, TaxSettings, TaxSettingsInput, ZatcaDocument, ZatcaPanel,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { query } from './businessBooksApi';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';

const BASE = '/api/v1/business/books/tax';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, {
  method, body: body === undefined ? undefined : JSON.stringify(body), headers: { 'content-type': 'application/json' },
});

export type TaxPeriodQuery = { from: string; to: string };

export const getOverview = () => get<TaxOverview>('');
export const saveSettings = (body: TaxSettingsInput) => send<{ settings: TaxSettings; rates: TaxRate[] }>('PUT', '/settings', body);
export const addRate = (body: TaxRateInput) => send<TaxRate>('POST', '/rates', body);
export const removeRate = (rateId: string) => apiRequestVoid(`${BASE}/rates/${id(rateId)}`, { method: 'DELETE' });
export const getReturn = (period: TaxPeriodQuery) => get<TaxReturn>(`/return${query(period)}`);
export const returnDocuments = (period: TaxPeriodQuery, box: string, offset: number, limit = 25) =>
  get<BooksPage<TaxReturnDocument>>(`/return/documents${query({ ...period, box, offset, limit })}`);
export const getZatca = (offset: number, limit = 25) => get<ZatcaPanel>(`/zatca${query({ offset, limit })}`);
export const generateZatca = (sourceId: string, sourceKind: 'invoice' | 'credit_note' = 'invoice') =>
  send<ZatcaDocument>('POST', '/zatca/generate', { source_kind: sourceKind, source_id: sourceId });
export const submitZatca = (documentId: string) => send<unknown>('POST', `/zatca/documents/${id(documentId)}/submit`);

async function download(path: string, fallback: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}${path}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), fallback));
}

/** The return with its boxes, warnings, notes and documents as CSV (a working draft: nothing is filed). */
export const exportReturn = (period: TaxPeriodQuery) => download(`/return.csv${query(period)}`, 'vat-return.csv');
/** The generated UBL XML of one document. */
export const downloadXml = (documentId: string) => download(`/zatca/documents/${id(documentId)}/xml`, 'e-invoice.xml');
