// The personal debt overview, credit cards, promises to pay, financial alerts and the Money Advisor's answers over the
// live API (docs/finance.md#debt-overview, #credit-cards, #financial-alerts, #advisor-answers). Every figure is the API's,
// worked out from the person's own records; the page only shows it. Nothing here moves money.
import { apiRequest, apiRequestVoid } from './api';
import { query } from './financeApi';
import type { AdvisorAnswer, AlertPreviewItem, AlertSettingsView, AnswerParams, CardView, DebtOverview, DebtProfileInput, PromiseView, Question } from './financeDebts';

export * from './financeDebts';

const BASE = '/api/v1/finance';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body: unknown) => apiRequest<T>(`${BASE}${path}`, {
  method, body: JSON.stringify(body), headers: { 'content-type': 'application/json' },
});

export const debtOverview = () => get<DebtOverview>('/debt-overview');
export const saveDebtProfile = (debtId: string, body: DebtProfileInput) => send<DebtProfileInput & { debt_id: string }>('PUT', `/debts/${id(debtId)}/profile`, body);
export const cards = () => get<{ today: string; items: CardView[]; note: string }>('/cards');
export const addPromise = (loanId: string, body: { amount: string; due_by: string; made_on?: string | null; note?: string | null }) =>
  send<PromiseView>('POST', `/people-loans/${id(loanId)}/promises`, body);
export const removePromise = (loanId: string, promiseId: string) => apiRequestVoid(`${BASE}/people-loans/${id(loanId)}/promises/${id(promiseId)}`, { method: 'DELETE' });
export const saveAgreedBalance = (loanId: string, balance: string | null, on: string | null) =>
  send<{ verified_balance: string | null; verified_on: string | null }>('PUT', `/people-loans/${id(loanId)}/agreed-balance`, { balance, on });
export const alertSettings = () => get<AlertSettingsView>('/alerts/settings');
/** `body` is `alertSettingsBody(kinds)`: every kind's channels and the in-app switch. */
export const saveAlertSettings = (body: { kinds: Record<string, boolean>; channels?: Record<string, Record<string, boolean>> }) =>
  send<AlertSettingsView>('PUT', '/alerts/settings', body);
export const alertPreview = () => get<{ today: string; items: AlertPreviewItem[] }>('/alerts/preview');
export const advisorAnswer = (question: Question, params: AnswerParams = {}) =>
  get<AdvisorAnswer>(`/advisor/answer${query({ question, ...params })}`);

