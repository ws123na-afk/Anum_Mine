// Ready to sell over the live API (docs/selling-readiness.md). The console routes need the realm role
// platform_operator; the public legal facts need no sign-in; receipts follow the invoice's own rules.
import { apiRequest } from './api';
import type { Invoice, Refund, RefundBody } from './billing';
import type { DnsResult, LegalBody, LegalProfile, MoyasarBody, MoyasarSettings, Readiness } from './selling';
import { downloadFile } from './billingApi';

const CONSOLE = '/api/v1/platform/billing';
const id = (value: string) => encodeURIComponent(value);
const send = <T>(method: 'POST' | 'PUT', path: string, body: unknown): Promise<T> => apiRequest<T>(path, { method, body: JSON.stringify(body) });

export const getMoyasar = (): Promise<MoyasarSettings> => apiRequest(`${CONSOLE}/providers/moyasar`, { method: 'GET' });
export const saveMoyasar = (body: MoyasarBody): Promise<MoyasarSettings> => send('PUT', `${CONSOLE}/providers/moyasar`, body);
export const refundPayment = (tenantId: string, invoiceId: string, paymentId: string, body: RefundBody): Promise<{ refund: Refund; invoice: Invoice }> =>
  send('POST', `${CONSOLE}/organizations/${id(tenantId)}/invoices/${id(invoiceId)}/payments/${id(paymentId)}/refunds`, body);
export const downloadOrgReceipt = (tenantId: string, invoiceId: string, paymentId: string): Promise<void> =>
  downloadFile(`${CONSOLE}/organizations/${id(tenantId)}/invoices/${id(invoiceId)}/payments/${id(paymentId)}/receipt`, `receipt-${paymentId}`);
export const downloadReceipt = (invoiceId: string, paymentId: string): Promise<void> =>
  downloadFile(`/api/v1/billing/invoices/${id(invoiceId)}/payments/${id(paymentId)}/receipt`, `receipt-${paymentId}`);

export const getLegal = (): Promise<LegalProfile> => apiRequest('/api/v1/platform/legal', { method: 'GET' });
export const saveLegal = (body: LegalBody): Promise<LegalProfile> => send('PUT', '/api/v1/platform/legal', body);
export const getReadiness = (): Promise<Readiness> => apiRequest('/api/v1/platform/readiness', { method: 'GET' });
export const checkDns = (domain: string): Promise<DnsResult> => send('POST', '/api/v1/platform/readiness/dns', { domain: domain.trim() });
