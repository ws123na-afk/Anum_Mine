// Bank and finance loans on a debt (docs/finance.md#bank-loans): pure helpers for the loan page and the terms form,
// covered by test/financeLoans.test.ts without a browser. The schedule, what is paid and owed and the payments found
// all come from the API (GET /debts/{id}/loan, GET /loans); nothing here works a loan out again.
import type { FinanceDebt, FinanceLoanRowStatus, FinanceLoanSummary, FinanceLoanTerms, FinanceLoanTermsInput, FinanceSuggestion } from '@anum/contracts';
import { parseAmount } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';

export type LoanTone = 'ok' | 'warn' | 'bad' | 'info' | 'muted';

/** The chip tone of an instalment's status. */
export function rowTone(status: FinanceLoanRowStatus): LoanTone {
  switch (status) {
    case 'paid': return 'ok';
    case 'missed': return 'bad';
    case 'part_paid': case 'ambiguous': return 'warn';
    case 'upcoming': return 'info';
    default: return 'muted';
  }
}

/** "2 months ahead", "on schedule" or "3 months behind": the words key and the count. */
export function aheadBehind(monthsAhead: number): { key: MessageKey; count: number } {
  if (monthsAhead > 0) return { key: 'floan.ahead', count: monthsAhead };
  if (monthsAhead < 0) return { key: 'floan.behind', count: -monthsAhead };
  return { key: 'floan.onSchedule', count: 0 };
}

/**
 * The debts as every figure counts them: a debt with loan terms owes what its schedule and matched payments leave and pays
 * its instalment (the API's figures, `GET /loans`), not the balance typed when it was added. A loan paid off or closed is
 * left out here (History keeps it).
 */
export function debtsWithLoans(debts: readonly FinanceDebt[], loans: readonly FinanceLoanSummary[]): FinanceDebt[] {
  const byId = new Map(loans.map((l) => [l.debt_id, l]));
  const out: FinanceDebt[] = [];
  for (const debt of debts) {
    const loan = byId.get(debt.id);
    if (!loan) { out.push(debt); continue; }
    if (loan.totals.status !== 'open') continue;
    out.push({ ...debt, balance: loan.totals.balance, minimum_payment: loan.totals.installment, apr_percent: loan.totals.effective_rate ?? '0',
      due_day: loan.totals.next_due_on ? Number(loan.totals.next_due_on.slice(8, 10)) : debt.due_day });
  }
  return out;
}

/** Loans paid off or closed: they stay in History with their whole record. */
export function finishedLoans(loans: readonly FinanceLoanSummary[]): FinanceLoanSummary[] {
  return loans.filter((l) => l.totals.status !== 'open');
}

/** The open `loan_payments` suggestion of one loan, if the queue has one. */
export function loanSuggestion(items: readonly FinanceSuggestion[], debtId: string): FinanceSuggestion | null {
  return items.find((s) => s.kind === 'loan_payments' && s.params?.debt_id === debtId) ?? null;
}

export interface LoanForm {
  lender: string;
  startOn: string;
  amount: string;
  rateType: 'reducing' | 'flat';
  rate: string;
  termMonths: string;
  installment: string;
  paymentDay: string;
  repayment: 'bank_debit' | 'salary_deduction' | 'manual';
  accountId: string;
  status: 'open' | 'paid_off' | 'closed';
  closedOn: string;
}

/** The form's starting values: the saved terms, or what the debt already says (its account and due day). */
export function loanForm(terms: FinanceLoanTerms | null, debt: Pick<FinanceDebt, 'account_id' | 'due_day' | 'name'>): LoanForm {
  return {
    lender: terms?.lender ?? '', startOn: terms?.start_on ?? '', amount: terms?.amount ?? '', rateType: terms?.rate_type ?? 'reducing',
    rate: terms?.rate ? String(Number(terms.rate)) : '', termMonths: terms?.term_months ? String(terms.term_months) : '', installment: terms?.installment ?? '',
    paymentDay: terms?.payment_day ? String(terms.payment_day) : debt.due_day ? String(debt.due_day) : '', repayment: terms?.repayment ?? 'bank_debit',
    accountId: terms?.account_id ?? debt.account_id ?? '', status: terms?.status ?? 'open', closedOn: terms?.closed_on ?? '',
  };
}

/** The body for PUT /debts/{id}/loan, or the first problem in words. The API checks the same again. */
export function loanBody(form: LoanForm, currency: string): { ok: true; body: FinanceLoanTermsInput } | { ok: false; error: string } {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.startOn)) return { ok: false, error: t('floan.error.start') };
  const amount = parseAmount(form.amount, currency, { signed: false, allowZero: false });
  if (!amount.ok) return { ok: false, error: amount.error };
  const rate = form.rate.trim();
  if (rate && (!/^\d{1,3}(\.\d{1,4})?$/.test(rate) || Number(rate) <= 0 || Number(rate) > 100)) return { ok: false, error: t('floan.error.rate') };
  const term = form.termMonths.trim();
  if (term && (!/^\d{1,3}$/.test(term) || Number(term) < 1 || Number(term) > 600)) return { ok: false, error: t('floan.error.term') };
  const installment = form.installment.trim() ? parseAmount(form.installment, currency, { signed: false, allowZero: false }) : null;
  if (installment && !installment.ok) return { ok: false, error: installment.error };
  if (!term && !installment) return { ok: false, error: t('floan.error.termOrInstallment') };
  const day = form.paymentDay.trim();
  if (day && !/^([1-9]|[12]\d|3[01])$/.test(day)) return { ok: false, error: t('finance.error.dueDay') };
  if (form.repayment === 'bank_debit' && !form.accountId) return { ok: false, error: t('floan.error.account') };
  if (form.status !== 'open' && !/^\d{4}-\d{2}-\d{2}$/.test(form.closedOn)) return { ok: false, error: t('floan.error.closedOn') };
  if (form.status !== 'open' && form.closedOn < form.startOn) return { ok: false, error: t('floan.error.closedBeforeStart') };
  return {
    ok: true,
    body: {
      lender: form.lender.trim() || null, start_on: form.startOn, amount: amount.value, rate_type: form.rateType, rate: rate || null,
      term_months: term ? Number(term) : null, installment: installment?.ok ? installment.value : null, payment_day: day ? Number(day) : null,
      repayment: form.repayment, account_id: form.repayment === 'bank_debit' ? form.accountId : form.accountId || null, status: form.status,
      closed_on: form.status === 'open' ? null : form.closedOn,
    },
  };
}
