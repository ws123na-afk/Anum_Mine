// The business books over the live API (/api/v1/business/books, docs/business-books.md#api). The server checks
// business:read / business:write / business:manage on every route; a 403 carries its own sentence naming who may do it.
import type {
  BooksAdjustment, BooksAdjustmentInput, BooksAdjustmentKind, BooksAllocation, BooksAllocationFilter, BooksAllocationTag, BooksBasis, BooksBill,
  BooksBillFilter, BooksBillInput, BooksBillPage, BooksBudget, BooksBudgetInput, BooksCostCentre, BooksCounterparty, BooksIntercompany,
  BooksIntercompanyInput, BooksPage, BooksPaymentInput, BooksProjectPage, BooksRate, BooksRateInput, BooksReport, BooksSettings, BooksSettingsInput,
  BooksSetup, BooksTagSection, BooksVendor, BooksVendorInput, BusinessPage, BusinessProject, BooksGroup, BooksRateFeed, BooksRateOrigin, BooksTemplate,
  BooksTemplateInput, BooksTemplatePage, BooksTemplateStatus,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';

const BASE = '/api/v1/business/books';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown, key?: string) => apiRequest<T>(`${BASE}${path}`, {
  method, body: body === undefined ? undefined : JSON.stringify(body),
  headers: { 'content-type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
});

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '' && value !== false) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

export type Period = { from?: string; to?: string; basis?: BooksBasis };

export const getSetup = () => get<BooksSetup>('/setup');
export const saveSettings = (body: BooksSettingsInput) => send<BooksSettings>('PUT', '/settings', body);
export const getReport = (period: Period) => get<BooksReport>(`/report${query(period)}`);
export const listProjects = (period: Period, offset: number, limit = 10) => get<BooksProjectPage>(`/projects${query({ from: period.from, to: period.to, offset, limit })}`);

export const listCostCentres = (archived: boolean, offset: number, limit = 50) => get<BooksPage<BooksCostCentre>>(`/cost-centres${query({ archived, offset, limit })}`);
export const createCostCentre = (body: { code: string; name: string }) => send<BooksCostCentre>('POST', '/cost-centres', body);
export const updateCostCentre = (centreId: string, body: { name?: string; archived?: boolean; version: number }) => send<BooksCostCentre>('PATCH', `/cost-centres/${id(centreId)}`, body);

export const listVendors = (q: string, archived: boolean, offset: number, limit = 25) => get<BooksPage<BooksVendor>>(`/vendors${query({ q, archived, offset, limit })}`);
export const createVendor = (body: BooksVendorInput) => send<BooksVendor>('POST', '/vendors', body);
export const updateVendor = (vendorId: string, body: BooksVendorInput & { archived: boolean; version: number }) => send<BooksVendor>('PATCH', `/vendors/${id(vendorId)}`, body);

export const listBills = (q: string, status: BooksBillFilter | '', offset: number, limit = 25) => get<BooksBillPage>(`/bills${query({ q, status, offset, limit })}`);
export const getBill = (billId: string) => get<BooksBill>(`/bills/${id(billId)}`);
export const createBill = (body: BooksBillInput, key: string) => send<BooksBill>('POST', '/bills', body, key);
export const updateBill = (billId: string, body: BooksBillInput & { version: number }) => send<BooksBill>('PATCH', `/bills/${id(billId)}`, body);
export const approveBill = (billId: string, version: number) => send<BooksBill>('POST', `/bills/${id(billId)}/approve`, { version });
export const voidBill = (billId: string, reason: string) => send<BooksBill>('POST', `/bills/${id(billId)}/void`, { reason });
export const payBill = (billId: string, body: BooksPaymentInput, key: string) => send<BooksBill>('POST', `/bills/${id(billId)}/payments`, body, key);
export const voidPayment = (billId: string, paymentId: string, reason: string) => send<BooksBill>('POST', `/bills/${id(billId)}/payments/${id(paymentId)}/void`, { reason });

export const listAllocations = (status: BooksAllocationFilter | '', q: string, offset: number, limit = 25, currency = '') =>
  get<BooksPage<BooksAllocation>>(`/allocations${query({ status, q, currency, offset, limit })}`);
export const tagAllocation = (allocationId: string, body: { cost_centre_id: string | null; section: BooksTagSection | null; note: string; version: number | null }) =>
  send<BooksAllocationTag>('PUT', `/allocations/${id(allocationId)}/tag`, body);

export const listBudgets = (offset: number, limit = 25) => get<BooksPage<BooksBudget>>(`/budgets${query({ offset, limit })}`);
export const createBudget = (body: BooksBudgetInput) => send<BooksBudget>('POST', '/budgets', body);
export const updateBudget = (budgetId: string, body: BooksBudgetInput & { version: number }) => send<BooksBudget>('PATCH', `/budgets/${id(budgetId)}`, body);
export const deleteBudget = (budgetId: string) => apiRequestVoid(`${BASE}/budgets/${id(budgetId)}`, { method: 'DELETE' });

export const listRates = (offset: number, limit = 25, origin: BooksRateOrigin | '' = '') => get<BooksPage<BooksRate>>(`/rates${query({ offset, limit, origin })}`);
export const createRate = (body: BooksRateInput) => send<BooksRate>('POST', '/rates', body);
export const deleteRate = (rateId: string) => apiRequestVoid(`${BASE}/rates/${id(rateId)}`, { method: 'DELETE' });

export const listAdjustments = (kind: BooksAdjustmentKind | '', includeVoid: boolean, offset: number, limit = 25) =>
  get<BooksPage<BooksAdjustment>>(`/adjustments${query({ kind, include_void: includeVoid, offset, limit })}`);
export const createAdjustment = (body: BooksAdjustmentInput, key: string) => send<BooksAdjustment>('POST', '/adjustments', body, key);
export const voidAdjustment = (adjustmentId: string, reason: string) => send<BooksAdjustment>('POST', `/adjustments/${id(adjustmentId)}/void`, { reason });

export const listCounterparties = () => get<{ items: BooksCounterparty[] }>('/intercompany/counterparties');
export const listIntercompany = (includeVoid: boolean, offset: number, limit = 25) => get<BooksPage<BooksIntercompany>>(`/intercompany${query({ include_void: includeVoid, offset, limit })}`);
export const createIntercompany = (body: BooksIntercompanyInput) => send<BooksIntercompany>('POST', '/intercompany', body);
export const voidIntercompany = (entryId: string, reason: string) => send<BooksIntercompany>('POST', `/intercompany/${id(entryId)}/void`, { reason });

export type GroupQuery = Period & { currency?: string; workspaces?: string[] };
function groupQuery(params: GroupQuery): string {
  const search = new URLSearchParams(query({ from: params.from, to: params.to, basis: params.basis, currency: params.currency }).slice(1));
  for (const workspace of params.workspaces ?? []) search.append('workspace', workspace);
  const text = search.toString();
  return text ? `?${text}` : '';
}
/** The consolidated books of the businesses the caller owns (owners only). */
export const getGroup = (params: GroupQuery) => get<BooksGroup>(`/group${groupQuery(params)}`);

export const listTemplates = (status: BooksTemplateStatus | '', q: string, offset: number, limit = 25) =>
  get<BooksTemplatePage>(`/recurring${query({ status, q, offset, limit })}`);
export const getTemplate = (templateId: string) => get<BooksTemplate>(`/recurring/${id(templateId)}`);
export const createTemplate = (body: BooksTemplateInput) => send<BooksTemplate>('POST', '/recurring', body);
export const updateTemplate = (templateId: string, body: BooksTemplateInput & { version: number }) => send<BooksTemplate>('PATCH', `/recurring/${id(templateId)}`, body);
export const templateAction = (templateId: string, action: 'pause' | 'resume' | 'end', version: number) =>
  send<BooksTemplate>('POST', `/recurring/${id(templateId)}/${action}`, { version });

export const getRateFeed = () => get<BooksRateFeed>('/rates/auto');
export const saveRateFeed = (body: { enabled: boolean; currencies: string[]; version: number | null }) => send<BooksRateFeed>('PUT', '/rates/auto', body);
export const refreshRateFeed = () => send<BooksRateFeed>('POST', '/rates/auto/refresh');

/** The business's projects for the project pickers (the Business projects list). */
export const businessProjects = () => apiRequest<BusinessPage<BusinessProject>>(`/api/v1/business/projects${query({ limit: 100, offset: 0 })}`, { method: 'GET' });

async function download(path: string, fallback: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}${path}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), fallback));
}

/** Download the P&L, tax, cash flow and the rates used as CSV. */
export const exportReport = (period: Period) => download(`/report.csv${query(period)}`, 'business-pnl.csv');
/** Download the consolidated P&L, balances and cash flow as CSV (a column per business, eliminations and the total). */
export const exportGroup = (params: GroupQuery) => download(`/group.csv${groupQuery(params)}`, 'group-pnl.csv');
/** Download every bill as CSV. */
export const exportBills = () => download('/bills.csv', 'business-bills.csv');
