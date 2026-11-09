// Bills and budgets, debts and goals over the live API (docs/finance.md#bills-and-budgets,
// #debts-and-goals). A bill is paid only when a recorded payment is matched to its due date: the
// server works the status out from the match on every read; the page never sets it.
import type {
  FinanceBillSchedule, FinanceBudgetAlert, FinanceBudgetReport, FinanceDebtPaymentPage, FinanceDebtPlan, FinanceDebtPlanView, FinanceLinkResult,
  FinanceMatchInput, FinancePaymentCandidate, FinanceInstallmentStatementEntry, FinanceInstallmentStatementView, FinanceInstallmentStatementSyncResult,
  FinanceInstallmentRefundNotice, FinanceRefundApplyResult, FinanceRefundPreview,
} from '@anum/contracts';
import { apiRequest, apiRequestVoid } from './api';
import { query } from './financeApi';

const BASE = '/api/v1/finance';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body: unknown, key?: string) => apiRequest<T>(`${BASE}${path}`, {
  method, body: JSON.stringify(body), headers: { 'content-type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
});

export interface FinanceGmailMailbox { id: string; address: string; selected: boolean }
export interface TamaraGmailPreview {
  account_id: string; address: string; messages_checked: number; messages_matched: number;
  rows: FinanceInstallmentStatementEntry[]; refunds?: FinanceInstallmentRefundNotice[]; warnings: string[];
}
export const installmentGmailMailboxes = () => get<FinanceGmailMailbox[]>('/installment-email/mailboxes');
export const previewTamaraGmail = (accountId: string, limit = 500) =>
  get<TamaraGmailPreview>(`/installment-email/preview${query({ account_id: accountId, limit })}`);

export const installmentStatement = () => get<FinanceInstallmentStatementView>('/installment-statement');
export const syncInstallmentStatement = (entries: FinanceInstallmentStatementEntry[], refundNotices: FinanceInstallmentRefundNotice[] = []) =>
  send<FinanceInstallmentStatementSyncResult>('POST', '/installment-statement/sync', { entries, refund_notices: refundNotices });

// Tamara refunds (docs/finance.md#tamara-refunds): evidence, the new schedule, apply on confirmation, undo.
export const installmentRefunds = () => get<FinanceRefundPreview>('/installment-refunds');
export const applyInstallmentRefunds = (orders: { provider: string; order_id: string; plan_hash: string }[]) =>
  send<FinanceRefundApplyResult>('POST', '/installment-refunds/apply', { orders });
export const undoInstallmentRefund = (recordId: string) =>
  send<FinanceRefundPreview>('POST', `/installment-refunds/${id(recordId)}/undo`, {});

export const billSchedule = (from: string, to: string) => get<FinanceBillSchedule>(`/bill-schedule${query({ from, to })}`);
export const paymentCandidates = (kind: 'bill' | 'debt', opts: { around?: string | null; amount?: string | null; currency?: string | null; q?: string | null }) =>
  get<{ items: FinancePaymentCandidate[] }>(`/payment-candidates${query({ for: kind, around: opts.around, amount: opts.amount, currency: opts.currency, q: opts.q })}`);
export const matchBill = (billId: string, body: FinanceMatchInput, key: string) => send<FinanceLinkResult>('POST', `/bills/${id(billId)}/matches`, body, key);
export const unmatchBill = (billId: string, linkId: string) => apiRequestVoid(`${BASE}/bills/${id(billId)}/matches/${id(linkId)}`, { method: 'DELETE' });

export const debtPayments = (limit: number, offset: number, debtId?: string) => get<FinanceDebtPaymentPage>(`/debt-payments${query({ limit, offset, debt_id: debtId })}`);
export const matchDebtPayment = (debtId: string, body: FinanceMatchInput, key: string) => send<FinanceLinkResult>('POST', `/debts/${id(debtId)}/payments`, body, key);
export const unmatchDebtPayment = (debtId: string, linkId: string) => apiRequestVoid(`${BASE}/debts/${id(debtId)}/payments/${id(linkId)}`, { method: 'DELETE' });

export const getDebtPlan = () => get<FinanceDebtPlanView>('/debt-plan');
export const saveDebtPlan = (plans: FinanceDebtPlan[]) => send<FinanceDebtPlanView>('PUT', '/debt-plan', { plans });

export const budgets = (month?: string) => get<FinanceBudgetReport>(`/budgets${query({ month })}`);
export const saveBudgetAlerts = (alerts: FinanceBudgetAlert[]) => send<{ alerts: FinanceBudgetAlert[] }>('PUT', '/budgets/alerts', { alerts });

/** A fresh idempotency key for one attempt (a retry of the same attempt reuses it). */
export const attemptKey = (): string => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(36).slice(2)}`);
