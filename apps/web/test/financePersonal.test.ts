// Lent and borrowed, payslips, sending money home, fixed costs and the Plan view: the pure helpers
// (docs/finance.md#lent-and-borrowed onwards). Every figure here is SYNTHETIC.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { FinanceCommitment, FinancePayoff } from '@anum/contracts';
import { commitmentGroups, fromMinorUnits, loanTone, minor, pages, payoffHeadline, previewNet, previewReceived, verdictTone } from '../src/lib/financePersonal.ts';
import { FINANCE_TABS, sectionFromHash } from '../src/lib/finance.ts';
import { FINANCE_NAV, OWN_HEAD_TABS, navTabs } from '../src/lib/financeNav.ts';

test('minor units round half to even like the server, never through floating point sums', () => {
  assert.equal(minor('1234.50', 'AED'), 123450);
  assert.equal(minor('0.125', 'AED'), 12);
  assert.equal(minor('0.135', 'AED'), 14);
  assert.equal(minor('1.2345', 'KWD'), 1234);
  assert.equal(minor('abc', 'AED'), null);
  assert.equal(fromMinorUnits(-2500, 'AED'), '-25.00');
  assert.equal(fromMinorUnits(5, 'KWD'), '0.005');
});

test('the payslip preview subtracts every deduction from gross', () => {
  assert.equal(previewNet('10000', [{ amount: '975' }, { amount: '200' }, { amount: '500' }, { amount: '' }], 'SAR'), '8325.00');
  assert.equal(previewNet('', [{ amount: '1' }], 'AED'), null);
});

test('the remittance preview applies the rate in the received currency', () => {
  assert.equal(previewReceived('1000', '22.5', 'INR'), '22500.00');
  assert.equal(previewReceived('1000', '', 'INR'), null);
  assert.equal(previewReceived('-5', '2', 'INR'), null);
});

test('tones follow the status and the verdict', () => {
  assert.equal(loanTone('overdue'), 'bad');
  assert.equal(loanTone('due_soon'), 'warn');
  assert.equal(loanTone('repaid'), 'ok');
  assert.equal(verdictTone('yes_from_cash'), 'good');
  assert.equal(verdictTone('monthly_tight'), 'warn');
  assert.equal(verdictTone('too_much'), 'bad');
  assert.equal(pages(0), 1);
  assert.equal(pages(41), 3);
});

test('fixed costs group per kind within one currency, totals in minor units', () => {
  const row = (kind: FinanceCommitment['kind'], monthly: string, currency = 'AED'): FinanceCommitment => ({ kind, name: kind, currency, monthly, source: 'bill', ref_id: kind + monthly, note: null });
  const groups = commitmentGroups([row('rent', '3000.00'), row('subscription', '433.33'), row('subscription', '0.01'), row('rent', '9', 'USD')], 'AED');
  assert.deepEqual(groups.map((g) => [g.kind, g.total, g.items.length]), [['rent', '3000.00', 1], ['subscription', '433.34', 2]]);
});

test('the payoff headline names the first debt of the cheaper order', () => {
  const base = { currency: 'AED', payment: '300.00', planned_payment: null, below_minimums: false, interest_saved: '3.06', first_win_months: 1, missing_rates: [],
    debts: [{ id: 'a', name: 'Card A', balance: '1200.00', apr_percent: '12', minimum: '100.00', avalanche_month: 5, snowball_month: 5 },
      { id: 'b', name: 'Loan B', balance: '200.00', apr_percent: '0', minimum: '50.00', avalanche_month: 4, snowball_month: 1 }],
    avalanche: { strategy: 'avalanche', months: 5, total_interest: '35.97', order: ['a', 'b'] },
    snowball: { strategy: 'snowball', months: 5, total_interest: '39.03', order: ['b', 'a'] } } as const;
  assert.deepEqual(payoffHeadline({ ...base, cheaper: 'avalanche' } as FinancePayoff), { strategy: 'avalanche', first: 'Card A' });
  assert.deepEqual(payoffHeadline({ ...base, cheaper: 'snowball' } as FinancePayoff), { strategy: 'snowball', first: 'Loan B' });
});

test('the personal money sections live in their pages, each page a real tab with its own head', () => {
  for (const [hash, tab, section] of [['#finance-people', 'debts', 'people'], ['#finance-payslips', 'advisor', 'salary'], ['#finance-remittances', 'goals', 'send-home'],
    ['#finance-commitments', 'bills', 'fixed']] as const) {
    assert.deepEqual(sectionFromHash(hash), { tab, section });
    assert.ok(FINANCE_TABS.includes(tab));
    assert.ok(navTabs(FINANCE_NAV).includes(tab));
    assert.ok(OWN_HEAD_TABS.has(tab));
    assert.ok(!(FINANCE_TABS as readonly string[]).includes(hash.slice('#finance-'.length)));
  }
});
