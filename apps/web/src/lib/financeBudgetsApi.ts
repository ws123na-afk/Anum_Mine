// Personal finance planning over the live API: the category ontology, learned merchants, the owner's budgets and the
// salary allocation, and recurring payments (docs/finance.md#category-ontology, #learned-merchants,
// #budgets-and-planning, #recurring-payments). Every record is the caller's own; the server checks finance:read and
// finance:manage. Figures (spent, coming, forecast, risk) are worked out by the server; the page never adds them up.
import type {
  FinanceAllocationReport, FinanceCategoryMapping, FinanceCategoryMappingPage, FinanceCategoryTree, FinanceKeptBill, FinancePlanBudgetInput,
  FinancePlanBudgetLine, FinancePlanReport, FinanceRecurringConfirmInput, FinanceRecurringKind, FinanceRecurringView, FinanceSubscriptionUsage,
} from '@anum/contracts';
import { apiRequest, apiRequestVoid } from './api';
import { query } from './financeApi';

const BASE = '/api/v1/finance';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, {
  method, ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { 'content-type': 'application/json' } }),
});

export const categoryTree = (month?: string | null) => get<FinanceCategoryTree>(`/categories${query({ month: month ?? undefined })}`);
export const listMappings = (p: { q?: string; limit: number; offset: number }) => get<FinanceCategoryMappingPage>(`/category-mappings${query({ ...p })}`);
export const switchMapping = (m: Pick<FinanceCategoryMapping, 'merchant' | 'direction'>, enabled: boolean) =>
  send<FinanceCategoryMapping>('PATCH', '/category-mappings', { merchant: m.merchant, direction: m.direction, enabled });
export const deleteMapping = (m: Pick<FinanceCategoryMapping, 'merchant' | 'direction'>) =>
  apiRequestVoid(`${BASE}/category-mappings${query({ merchant: m.merchant, direction: m.direction })}`, { method: 'DELETE' });

export const planBudgets = (on?: string | null, includeInactive = false) =>
  get<FinancePlanReport>(`/budgets/plans${query({ on: on ?? undefined, include_inactive: includeInactive ? 'true' : undefined })}`);
export const createPlanBudget = (body: FinancePlanBudgetInput) => send<FinancePlanBudgetLine>('POST', '/budgets/plans', body);
export const updatePlanBudget = (budgetId: string, body: FinancePlanBudgetInput) => send<FinancePlanBudgetLine>('PATCH', `/budgets/plans/${id(budgetId)}`, body);
export const deletePlanBudget = (budgetId: string) => apiRequestVoid(`${BASE}/budgets/plans/${id(budgetId)}`, { method: 'DELETE' });
export const budgetAllocation = () => get<FinanceAllocationReport>('/budgets/allocation');

export const recurring = (q?: string) => get<FinanceRecurringView>(`/recurring${query({ q: q || undefined })}`);
export const confirmRecurring = (body: FinanceRecurringConfirmInput) => send<FinanceKeptBill>('POST', '/recurring/confirm', body);
export const rejectRecurring = (key: string) => send<{ key: string; status: string }>('POST', '/recurring/reject', { key });
export const recurringSettings = (billId: string, body: { auto_link?: boolean; usage?: FinanceSubscriptionUsage | 'unknown'; recurring_kind?: FinanceRecurringKind | null }) =>
  send<FinanceKeptBill>('PATCH', `/recurring/bills/${id(billId)}`, body);
export const linkRecurringNow = () => send<{ linked: number; left: number }>('POST', '/recurring/link-now', {});
