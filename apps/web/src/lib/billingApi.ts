// Customer billing over the live API (docs/billing.md#api). Permissions are the server's: billing
// admins (workspace owners or org_admin) for the organisation's own billing, platform_operator for
// the console, anyone for the public plan list, a signed-in account for sign-up.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { getLocale } from './i18n';
import {
  consoleOrgPath, invoicesPath, previewPath, subscriptionsPath,
  type Account, type AccountBody, type AdminPlan, type ConsoleOrganisation, type ConsoleSubscription, type ConsoleSummary, type EmailOutcome, type Invoice,
  type InvoiceFilter, type InvoiceSummary, type IssuerSettings, type Onboarding, type Overview, type Page, type PaymentBody, type Plan, type Preview,
  type PublicCatalog, type RunResult, type SettingsBody, type SignupBody, type SignupResult, type SignupStatus, type Subscription, type SubscriptionFilter,
} from './billing';

const ORG = '/api/v1/billing';
const CONSOLE = '/api/v1/platform/billing';
const json = (body: unknown) => ({ body: JSON.stringify(body) });
const id = (value: string) => encodeURIComponent(value);
const get = <T>(path: string): Promise<T> => apiRequest<T>(path, { method: 'GET' });
const post = <T>(path: string, body?: unknown): Promise<T> => apiRequest<T>(path, { method: 'POST', ...(body === undefined ? {} : json(body)) });

// ---- the organisation's own billing ---------------------------------------------------------------------

export const getOverview = (): Promise<Overview> => get(`${ORG}/overview`);
export const listPlans = (): Promise<Plan[]> => get(`${ORG}/plans`);
export const previewChange = (planId: string): Promise<Preview> => get(previewPath(planId));
export const changePlan = (planId: string): Promise<{ subscription: Subscription; invoice: InvoiceSummary | null }> => post(`${ORG}/subscription/change`, { plan_id: planId });
export const cancelSubscription = (): Promise<{ subscription: Subscription }> => post(`${ORG}/subscription/cancel`);
export const reactivateSubscription = (): Promise<{ subscription: Subscription; invoice: InvoiceSummary | null }> => post(`${ORG}/subscription/reactivate`);
export const saveAccount = (body: AccountBody): Promise<Account> => apiRequest(`${ORG}/account`, { method: 'PUT', ...json(body) });
export const listInvoices = (filter: InvoiceFilter): Promise<Page<InvoiceSummary>> => get(invoicesPath(ORG, filter));
export const getInvoice = (invoiceId: string): Promise<Invoice> => get(`${ORG}/invoices/${id(invoiceId)}`);
export const downloadInvoice = (invoice: Pick<InvoiceSummary, 'id' | 'number'>): Promise<void> => downloadPdf(`${ORG}/invoices/${id(invoice.id)}/pdf`, invoice.number ?? invoice.id);
export const checkout = (invoiceId: string): Promise<{ url: string }> => post(`${ORG}/invoices/${id(invoiceId)}/checkout`);
export const getOnboarding = (): Promise<Onboarding> => get(`${ORG}/onboarding`);
export const dismissOnboarding = (): Promise<{ dismissed: boolean }> => post(`${ORG}/onboarding/dismiss`);

// ---- the platform console --------------------------------------------------------------------------------

export const getSummary = (): Promise<ConsoleSummary> => get(`${CONSOLE}/summary`);
export const listSubscriptions = (filter: SubscriptionFilter): Promise<Page<ConsoleSubscription>> => get(subscriptionsPath(filter));
export const listCatalog = (): Promise<AdminPlan[]> => get(`${CONSOLE}/plans`);
export const createPlan = (body: Omit<AdminPlan, 'id' | 'created_at' | 'updated_at' | 'current'>): Promise<AdminPlan> => post(`${CONSOLE}/plans`, body);
export const updatePlan = (planId: string, body: Partial<AdminPlan>): Promise<AdminPlan> => apiRequest(`${CONSOLE}/plans/${id(planId)}`, { method: 'PATCH', ...json(body) });
export const getSettings = (): Promise<IssuerSettings> => get(`${CONSOLE}/settings`);
export const saveSettings = (body: SettingsBody): Promise<IssuerSettings> => apiRequest(`${CONSOLE}/settings`, { method: 'PUT', ...json(body) });
export const runBilling = (): Promise<RunResult> => post(`${CONSOLE}/run`);
export const getOrganisation = (tenantId: string): Promise<ConsoleOrganisation> => get(consoleOrgPath(tenantId));
export const listOrgInvoices = (tenantId: string, filter: InvoiceFilter): Promise<Page<InvoiceSummary>> => get(invoicesPath(consoleOrgPath(tenantId), filter));
export const getOrgInvoice = (tenantId: string, invoiceId: string): Promise<Invoice> => get(`${consoleOrgPath(tenantId)}/invoices/${id(invoiceId)}`);
export const downloadOrgInvoice = (tenantId: string, invoice: Pick<InvoiceSummary, 'id' | 'number'>): Promise<void> => downloadPdf(`${consoleOrgPath(tenantId)}/invoices/${id(invoice.id)}/pdf`, invoice.number ?? invoice.id);
export const recordPayment = (tenantId: string, invoiceId: string, body: PaymentBody): Promise<{ payment_id: string; invoice: Invoice }> => post(`${consoleOrgPath(tenantId)}/invoices/${id(invoiceId)}/payments`, body);
export const issueCreditNote = (tenantId: string, invoiceId: string, body: { total_minor: number; reason: string }): Promise<{ invoice: Invoice }> => post(`${consoleOrgPath(tenantId)}/invoices/${id(invoiceId)}/credit-notes`, body);
export const voidInvoice = (tenantId: string, invoiceId: string): Promise<Invoice> => post(`${consoleOrgPath(tenantId)}/invoices/${id(invoiceId)}/void`);
export const emailInvoice = (tenantId: string, invoiceId: string, to: string): Promise<EmailOutcome> => post(`${consoleOrgPath(tenantId)}/invoices/${id(invoiceId)}/email`, to.trim() ? { to: to.trim() } : {});
export const startSubscription = (tenantId: string, body: { plan_id: string; trial_days?: number; seats?: number }): Promise<{ subscription: Subscription }> => post(`${consoleOrgPath(tenantId)}/subscription`, body);
export type SubscriptionAction = 'change' | 'pause' | 'resume' | 'cancel' | 'reactivate';
export const subscriptionAction = (tenantId: string, action: SubscriptionAction, body?: { plan_id: string }): Promise<{ subscription: Subscription }> => post(`${consoleOrgPath(tenantId)}/subscription/${action}`, body ?? {});

// ---- public plans and sign-up ------------------------------------------------------------------------------

/** The pricing page's plans: no sign-in and no auth headers (only the page language). */
export async function getPublicPlans(): Promise<PublicCatalog> {
  const response = await fetch(`${apiBaseUrl}/api/v1/public/billing/plans`, { headers: { 'accept-language': getLocale() } });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.json() as Promise<PublicCatalog>;
}
export const getSignupStatus = (): Promise<SignupStatus> => get('/api/v1/signup/status');
export const createOrganisation = (body: SignupBody): Promise<SignupResult> => post('/api/v1/signup/organization', body);

// ---- files -------------------------------------------------------------------------------------------------

/** An invoice PDF through the signed-in request (bearer token), saved from a same-origin blob URL. */
async function downloadPdf(path: string, name: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const disposition = response.headers.get('content-disposition') ?? '';
  const named = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = named || `${name.replace(/[^A-Za-z0-9_-]+/g, '-')}.pdf`;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A picked file's bytes as base64 (the proof of a payment). */
export function fileToBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => { const text = String(reader.result ?? ''); resolve(text.slice(text.indexOf(',') + 1)); };
    reader.onerror = () => reject(reader.error ?? new Error('The file could not be read.'));
    reader.readAsDataURL(file);
  });
}
