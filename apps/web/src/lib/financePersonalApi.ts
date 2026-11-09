// Lent and borrowed with people, payslips, remittances, fixed commitments and the Plan view over the live API
// (docs/finance.md#lent-and-borrowed, #payslips, #sending-money-home, #fixed-commitments, #planning-and-advice).
// Every record is the caller's own; the server checks finance:read and finance:manage. ANUM only advises.
import type {
  FinanceAfford, FinanceAffordInput, FinanceCommitments, FinanceCompare, FinanceCompareInput, FinanceLinkResult, FinancePaymentCandidate,
  FinancePayoffPage, FinancePayslip, FinancePayslipDetail, FinancePayslipInput, FinancePayslipPage, FinancePersonLoan, FinancePersonLoanDetail,
  FinancePersonLoanInput, FinancePersonLoanPage, FinanceRemittance, FinanceRemittanceDetail, FinanceRemittanceInput, FinanceRemittancePage,
  FinanceResearch, FinanceResearchInput,
} from '@anum/contracts';
import { apiRequest, apiRequestVoid } from './api';
import { query } from './financeApi';

const BASE = '/api/v1/finance';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body: unknown, key?: string) => apiRequest<T>(`${BASE}${path}`, {
  method, body: JSON.stringify(body), headers: { 'content-type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
});
const remove = (path: string) => apiRequestVoid(`${BASE}${path}`, { method: 'DELETE' });
const newKey = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`);

export type PersonalKind = 'person_loan' | 'payslip' | 'remittance';

export interface LoanQuery { q?: string; direction?: string; status?: string; limit: number; offset: number }
export const listLoans = (p: LoanQuery) => get<FinancePersonLoanPage>(`/people-loans${query({ ...p })}`);
export const getLoan = (loanId: string) => get<FinancePersonLoanDetail>(`/people-loans/${id(loanId)}`);
export const createLoan = (body: FinancePersonLoanInput) => send<FinancePersonLoan>('POST', '/people-loans', body);
export const updateLoan = (loanId: string, body: FinancePersonLoanInput) => send<FinancePersonLoan>('PATCH', `/people-loans/${id(loanId)}`, body);
export const deleteLoan = (loanId: string) => remove(`/people-loans/${id(loanId)}`);
export const linkLoan = (loanId: string, canonicalId: string, kind: 'handed' | 'repayment', amount?: string | null) =>
  send<FinanceLinkResult>('POST', `/people-loans/${id(loanId)}/links`, { canonical_id: canonicalId, kind, ...(amount ? { amount } : {}) }, newKey());
export const unlinkLoan = (loanId: string, linkId: string) => remove(`/people-loans/${id(loanId)}/links/${id(linkId)}`);

export const listPayslips = (p: { year?: number | null; q?: string; limit: number; offset: number }) => get<FinancePayslipPage>(`/payslips${query({ ...p })}`);
export const getPayslip = (payslipId: string) => get<FinancePayslipDetail>(`/payslips/${id(payslipId)}`);
export const createPayslip = (body: FinancePayslipInput) => send<FinancePayslip>('POST', '/payslips', body);
export const updatePayslip = (payslipId: string, body: FinancePayslipInput) => send<FinancePayslip>('PATCH', `/payslips/${id(payslipId)}`, body);
export const deletePayslip = (payslipId: string) => remove(`/payslips/${id(payslipId)}`);
export const linkPayslip = (payslipId: string, canonicalId: string) =>
  send<FinanceLinkResult>('POST', `/payslips/${id(payslipId)}/links`, { canonical_id: canonicalId }, newKey());
export const unlinkPayslip = (payslipId: string, linkId: string) => remove(`/payslips/${id(payslipId)}/links/${id(linkId)}`);

export const listRemittances = (p: { q?: string; month?: string; limit: number; offset: number }) => get<FinanceRemittancePage>(`/remittances${query({ ...p })}`);
export const getRemittance = (remitId: string) => get<FinanceRemittanceDetail>(`/remittances/${id(remitId)}`);
export const createRemittance = (body: FinanceRemittanceInput) => send<FinanceRemittance>('POST', '/remittances', body);
export const updateRemittance = (remitId: string, body: FinanceRemittanceInput) => send<FinanceRemittance>('PATCH', `/remittances/${id(remitId)}`, body);
export const deleteRemittance = (remitId: string) => remove(`/remittances/${id(remitId)}`);
export const linkRemittance = (remitId: string, canonicalId: string) =>
  send<FinanceLinkResult>('POST', `/remittances/${id(remitId)}/links`, { canonical_id: canonicalId }, newKey());
export const unlinkRemittance = (remitId: string, linkId: string) => remove(`/remittances/${id(remitId)}/links/${id(linkId)}`);

/** Recorded transactions a record could be linked to (money out, or money in for a payslip or a loan repaid to you). */
export const candidates = (kind: PersonalKind, direction: 'in' | 'out', opts: { around?: string | null; amount?: string | null; currency?: string | null; q?: string | null }) =>
  get<{ items: FinancePaymentCandidate[] }>(`/payment-candidates${query({ for: kind, direction, around: opts.around, amount: opts.amount, currency: opts.currency, q: opts.q })}`);

export const getCommitments = () => get<FinanceCommitments>('/fixed-commitments');
export const getPayoff = (payment?: string | null, currency?: string | null) => get<FinancePayoffPage>(`/planner/payoff${query({ payment, currency })}`);
export const afford = (body: FinanceAffordInput) => send<FinanceAfford>('POST', '/planner/afford', body);
export const compare = (body: FinanceCompareInput) => send<FinanceCompare>('POST', '/planner/compare', body);
export const research = (body: FinanceResearchInput) => send<FinanceResearch>('POST', '/planner/research', body);
