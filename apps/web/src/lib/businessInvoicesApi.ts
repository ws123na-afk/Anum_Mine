// Invoices and the customer portal's owner routes over the live API (/api/v1/business, docs/business.md#invoices,
// docs/business-portal.md). Every route checks the caller's role on the server; a 403 carries the API's own sentence.
import type {
  Billable, BillingProfile, BusinessInvoice, BusinessPage, CreditNote, CreditNoteInput, InvoiceCreate, InvoiceListItem, InvoiceStatus, InvoiceSummary, InvoiceUpdate, PaymentInput,
  PaymentNotice, PortalAccessCreated, PortalAccessInput, PortalAccessItem, PortalDecisionRow,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';

const BASE = '/api/v1/business';
const json = (method: string, body?: unknown): RequestInit => ({ method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
const id = (value: string) => encodeURIComponent(value);

function query(values: Record<string, string | number | boolean | readonly string[] | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined || value === '' || value === false) continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item));
    else params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

export const getBillingProfile = () => apiRequest<BillingProfile>(`${BASE}/billing-profile`, { method: 'GET' });
export const saveBillingProfile = (body: BillingProfile) => apiRequest<BillingProfile>(`${BASE}/billing-profile`, json('PUT', body));

export const getInvoiceSummary = () => apiRequest<InvoiceSummary>(`${BASE}/invoices/summary`, { method: 'GET' });
export const listInvoices = (q: { status?: readonly InvoiceStatus[]; overdue?: boolean; q?: string; account_id?: string; limit: number; offset: number }) =>
  apiRequest<BusinessPage<InvoiceListItem>>(`${BASE}/invoices${query(q)}`, { method: 'GET' });
export const getBillable = (q: { account_id?: string; project_id?: string }) => apiRequest<Billable>(`${BASE}/invoices/billable${query(q)}`, { method: 'GET' });
export const createInvoice = (body: InvoiceCreate) => apiRequest<BusinessInvoice>(`${BASE}/invoices`, json('POST', body));
export const getInvoice = (invoiceId: string) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}`, { method: 'GET' });
export const updateInvoice = (invoiceId: string, body: InvoiceUpdate) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}`, json('PATCH', body));
export const deleteInvoice = (invoiceId: string) => apiRequestVoid(`${BASE}/invoices/${id(invoiceId)}`, { method: 'DELETE' });
export const requestInvoiceApproval = (invoiceId: string) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/request-approval`, json('POST'));
export const cancelInvoiceRequest = (invoiceId: string) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/cancel-request`, json('POST'));
export const voidInvoice = (invoiceId: string, reason: string) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/void`, json('POST', { reason }));
export const recordPayment = (invoiceId: string, body: PaymentInput) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/payments`, json('POST', body));
export const removePayment = (invoiceId: string, paymentId: string) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/payments/${id(paymentId)}`, { method: 'DELETE' });

/** The printable page (text/html) fetched with the app's own credentials. */
export async function invoicePrintable(invoiceId: string, lang: 'en' | 'ar'): Promise<Blob> {
  const response = await fetch(`${apiBaseUrl}${BASE}/invoices/${id(invoiceId)}/print${query({ lang })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.blob();
}

/** The server-made PDF, saved with the app's own credentials. Returns the file name. */
export async function downloadInvoicePdf(invoiceId: string): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/invoices/${id(invoiceId)}/pdf`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), 'invoice.pdf'));
}

// credit notes (owners issue and void) and customers' payment reports (members and owners decide)
export const listCreditNotes = (invoiceId: string) => apiRequest<CreditNote[]>(`${BASE}/invoices/${id(invoiceId)}/credit-notes`, { method: 'GET' });
export const issueCreditNote = (invoiceId: string, body: CreditNoteInput) => apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/credit-notes`, json('POST', body));
export const voidCreditNote = (invoiceId: string, creditNoteId: string, reason: string) =>
  apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/credit-notes/${id(creditNoteId)}/void`, json('POST', { reason }));
export const listPaymentNotices = (invoiceId: string) => apiRequest<PaymentNotice[]>(`${BASE}/invoices/${id(invoiceId)}/payment-notices`, { method: 'GET' });
export const confirmPaymentNotice = (invoiceId: string, noticeId: string, note: string) =>
  apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/payment-notices/${id(noticeId)}/confirm`, json('POST', { note }));
export const rejectPaymentNotice = (invoiceId: string, noticeId: string, reason: string) =>
  apiRequest<BusinessInvoice>(`${BASE}/invoices/${id(invoiceId)}/payment-notices/${id(noticeId)}/reject`, json('POST', { reason }));

// portal access (owner side)
export const listPortalAccess = (accountId: string) => apiRequest<PortalAccessItem[]>(`${BASE}/portal/access${query({ account_id: accountId })}`, { method: 'GET' });
export const createPortalAccess = (body: PortalAccessInput) => apiRequest<PortalAccessCreated>(`${BASE}/portal/access`, json('POST', body));
export const revokePortalAccess = (accessId: string) => apiRequest<PortalAccessItem>(`${BASE}/portal/access/${id(accessId)}/revoke`, json('POST'));
export const listPortalDecisions = (q: { account_id?: string; subject_id?: string }) => apiRequest<PortalDecisionRow[]>(`${BASE}/portal/decisions${query(q)}`, { method: 'GET' });
