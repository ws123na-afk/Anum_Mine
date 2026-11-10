// Bank and finance loans on a debt (docs/finance.md#bank-loans): the terms form, the debts as the figures count them,
// the found-payments suggestion and the catalog keys. Every value is fake and low entropy.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FinanceDebt, FinanceLoanSummary, FinanceLoanTotals, FinanceSuggestion } from '@anum/contracts';
import { aheadBehind, debtsWithLoans, finishedLoans, loanBody, loanForm, loanSuggestion, rowTone } from '../src/lib/financeLoans.ts';
import { acceptMode, NEVER_AUTOMATED } from '../src/lib/financeSuggestions.ts';
import { financeLoansEn } from '../src/lib/messages/financeLoans.en.ts';
import { financeLoansAr } from '../src/lib/messages/financeLoans.ar.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const debt = (over: Partial<FinanceDebt> = {}): FinanceDebt => ({
  id: 'fin_debt_a', name: 'Example car loan', kind: 'loan', balance: '99999.00', currency: 'SAR', apr_percent: '0', minimum_payment: '0', due_day: null,
  account_id: null, notes: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', ...over,
});
const totals = (over: Partial<FinanceLoanTotals> = {}): FinanceLoanTotals => ({
  status: 'open', basis: 'reducing', installment: '1063.70', installment_from: 'computed', split_known: true, never_ends: false, effective_rate: '6.0000',
  total_payable: '25528.65', total_interest: '1528.65', paid: '0', interest_paid: '0', principal_paid: '0', balance: '12000.00', payoff_on: '2026-10-27',
  due_count: 12, paid_count: 12, months_ahead: 0, next_due_on: '2026-11-27', next_amount: '1063.70', from_salary: false, found: 0, missing: 0, ambiguous: 0, ...over,
});
const summary = (over: Partial<FinanceLoanSummary> = {}): FinanceLoanSummary => ({
  debt_id: 'fin_debt_a', name: 'Example car loan', lender: 'Example Bank', currency: 'SAR', repayment: 'bank_debit', totals: totals(), ...over,
});

describe('bank loans', () => {
  it('counts a loan by its schedule, not the typed balance, and moves a finished one to History', () => {
    const [counted] = debtsWithLoans([debt()], [summary()]);
    assert.deepEqual([counted.balance, counted.minimum_payment, counted.apr_percent, counted.due_day], ['12000.00', '1063.70', '6.0000', 27]);
    assert.equal(debtsWithLoans([debt({ id: 'other' })], [summary()])[0].balance, '99999.00');
    const done = summary({ totals: totals({ status: 'paid_off', balance: '0.00' }) });
    assert.deepEqual(debtsWithLoans([debt()], [done]), []);
    assert.deepEqual(finishedLoans([summary(), done]).map((l) => l.totals.status), ['paid_off']);
  });

  it('words the pace and the status of each instalment', () => {
    assert.deepEqual(aheadBehind(2), { key: 'floan.ahead', count: 2 });
    assert.deepEqual(aheadBehind(-3), { key: 'floan.behind', count: 3 });
    assert.deepEqual(aheadBehind(0), { key: 'floan.onSchedule', count: 0 });
    assert.equal(rowTone('paid'), 'ok');
    assert.equal(rowTone('missed'), 'bad');
    assert.equal(rowTone('ambiguous'), 'warn');
    assert.equal(rowTone('settled'), 'muted');
  });

  it('builds the terms body and refuses what the API would', () => {
    const form = { ...loanForm(null, debt({ due_day: 27 })), startOn: '2024-10-15', amount: '24000', termMonths: '24', accountId: 'fin_acct_a' };
    assert.equal(form.paymentDay, '27');
    const made = loanBody(form, 'SAR');
    assert.ok(made.ok);
    if (made.ok) assert.deepEqual([made.body.amount, made.body.term_months, made.body.rate, made.body.payment_day, made.body.closed_on], ['24000', 24, null, 27, null]);
    assert.equal(loanBody({ ...form, termMonths: '', installment: '' }, 'SAR').ok, false);
    assert.equal(loanBody({ ...form, rate: '0' }, 'SAR').ok, false);
    assert.equal(loanBody({ ...form, accountId: '' }, 'SAR').ok, false);
    assert.equal(loanBody({ ...form, status: 'paid_off', closedOn: '' }, 'SAR').ok, false);
    assert.equal(loanBody({ ...form, status: 'paid_off', closedOn: '2024-01-01' }, 'SAR').ok, false);
    assert.equal(loanBody({ ...form, repayment: 'salary_deduction', accountId: '', installment: '1000', termMonths: '' }, 'SAR').ok, true);
  });

  it('accepts the found payments at once, never automatically', () => {
    const action = { kind: 'link_batch' as const, transaction_ids: [], category: null, subcategory_id: null, transaction_type: null, debt_id: 'fin_debt_a',
      links: [], loan_months: [{ number: 1, due_on: '2024-11-27', state: 'found' as const, expected: '1000', currency: 'SAR', amount: '1000', canonical_id: null,
        source: 'payslip' as const, source_id: 'fin_slip_a', candidates: 0 }] };
    assert.equal(acceptMode({ action }), 'batch');
    assert.equal(acceptMode({ action: { ...action, loan_months: [] } }), 'none');
    assert.ok((NEVER_AUTOMATED as readonly string[]).includes('link_loan_payments'));
    const item = { kind: 'loan_payments', params: { debt_id: 'fin_debt_a' } } as unknown as FinanceSuggestion;
    assert.equal(loanSuggestion([item], 'fin_debt_a'), item);
    assert.equal(loanSuggestion([item], 'fin_debt_b'), null);
  });

  it('has every key in both languages', () => {
    assert.deepEqual(Object.keys(financeLoansAr).sort(), Object.keys(financeLoansEn).sort());
    for (const key of ['fsug.kind.loan_payments', 'fsug.head.loan_payments', 'fsug.why.loan_payments', 'fsug.never.link_loan_payments', 'fplan.debts.tab.loans']) {
      assert.ok(key in en && key in ar, key);
    }
    for (const status of ['paid', 'part_paid', 'missed', 'upcoming', 'ambiguous', 'settled']) assert.ok(`floan.row.${status}` in en && `floan.row.${status}` in ar, status);
  });
});
