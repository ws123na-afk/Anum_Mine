// Finance reports and Safe to spend figures (docs/finance.md#reports).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FinanceSafeToSpend, FinanceSummary } from '@anum/contracts';
import {
  add, categoryRows, dailyPace, fromMilli, guardrails, keyMovements, netWorth, parseImportAmount, percentOf, personalFlows, remainingOf, reportCurrencies, reportQuality,
  runway, spendOutsideBills, subtract, toMilli,
} from '../src/lib/financeStatements.ts';

const summary = (month: string, over: Partial<FinanceSummary> = {}): FinanceSummary => ({
  month, start: `${month}-01`, end: `${month}-30`, today: `${month}-05`, base_currency: 'SAR', timezone: 'Asia/Riyadh', accounts: [], balances: [], spend_by_category: [],
  income: [], spend: [], net: [], transaction_count: 0, upcoming_bills: [], bills_due: [], debts: [], minimum_payments: [], debt_count: 0, goals: [], ...over,
});

describe('decimal money', () => {
  it('adds and subtracts without floating point error', () => {
    assert.equal(add('SAR', '0.10', '0.20'), '0.30');
    assert.equal(subtract('7140.00', '7760.00', 'SAR'), '-620.00');
    assert.equal(add('KWD', '1.005', '2.005'), '3.010');
    assert.equal(fromMilli(toMilli('13.5'), 'JPY'), '14');
    assert.equal(toMilli('abc'), 0n);
  });
  it('reads imported amounts per currency, thousands separators included, without floats', () => {
    assert.equal(parseImportAmount('1,250.00', 'SAR'), '1250.00');
    assert.equal(parseImportAmount('1.250', 'KWD'), '1.250');
    assert.equal(parseImportAmount('1.250', 'SAR'), null);
    assert.equal(parseImportAmount('0.1', 'AED'), '0.10');
    assert.equal(parseImportAmount('0', 'AED'), null);
    assert.equal(parseImportAmount('abc', 'AED'), null);
    assert.equal(remainingOf('1000.00', '999.99', 'AED'), '0.01');
    assert.equal(remainingOf('1.000', '1.250', 'KWD'), '0.000');
  });
  it('rounds half to even like the server', () => {
    assert.equal(fromMilli(toMilli('2.125'), 'SAR'), '2.12');
    assert.equal(fromMilli(toMilli('2.135'), 'SAR'), '2.14');
    assert.equal(fromMilli(toMilli('-2.125'), 'SAR'), '-2.12');
    assert.equal(fromMilli(toMilli('2.5'), 'JPY'), '2');
    // Past three decimals the thousandths round too, never cut.
    assert.equal(toMilli('1.0006'), 1001n);
    assert.equal(toMilli('1.0005'), 1000n);
    assert.equal(fromMilli(toMilli('1.2346'), 'KWD'), '1.235');
  });
  it('gives a percentage to one decimal and none from a zero whole', () => {
    assert.equal(percentOf('20220.00', '31280.00'), 64.6);
    assert.equal(percentOf('5', '0'), null);
    assert.equal(percentOf('-620', '7760'), -8);
  });
});

describe('personal reports', () => {
  const oct = summary('2026-10', { spend: [{ currency: 'SAR', amount: '1000.00' }], spend_by_category: [
    { category: 'fuel', currency: 'SAR', amount: '600.00', count: 3 }, { category: 'dining', currency: 'SAR', amount: '400.00', count: 4 }] });
  const nov = summary('2026-11', {
    income: [{ currency: 'SAR', amount: '5000.00' }], spend: [{ currency: 'SAR', amount: '1200.00' }, { currency: 'USD', amount: '10.00' }], net: [{ currency: 'SAR', amount: '3800.00' }],
    balances: [{ currency: 'SAR', amount: '9000.00' }, { currency: 'USD', amount: '50.00' }], debts: [{ currency: 'SAR', amount: '2500.00' }], transaction_count: 9,
    spend_by_category: [{ category: 'fuel', currency: 'SAR', amount: '540.00', count: 2 }, { category: 'other', currency: 'SAR', amount: '660.00', count: 2 }, { category: 'dining', currency: 'USD', amount: '10.00', count: 1 }],
  });
  it('lists the currencies with the base first', () => {
    assert.deepEqual(reportCurrencies([oct, nov], 'SAR'), ['SAR', 'USD']);
  });
  it('reads a month flow per currency', () => {
    assert.deepEqual(personalFlows([nov], 'SAR'), [{ month: '2026-11', income: '5000.00', expenses: '1200.00', net: '3800.00' }]);
    assert.deepEqual(personalFlows([oct], 'SAR')[0].income, '0');
  });
  it('compares categories month over month, never across currencies', () => {
    const rows = categoryRows(nov, oct, 'SAR');
    assert.deepEqual(rows.map((r) => r.category), ['other', 'fuel', 'dining']);
    const fuel = rows.find((r) => r.category === 'fuel')!;
    assert.equal(fuel.change, '-60.00');
    assert.equal(fuel.percent, -10);
    assert.equal(fuel.share, 45);
    const dining = rows.find((r) => r.category === 'dining')!;
    assert.equal(dining.now, '0.00');
    assert.equal(dining.percent, -100);
    assert.equal(categoryRows(nov, null, 'SAR')[0].percent, null);
  });
  it('picks the largest movements', () => {
    assert.deepEqual(keyMovements(categoryRows(nov, oct, 'SAR'), 2).map((r) => r.category), ['other', 'dining']);
  });
  it('measures how much spending is categorized', () => {
    assert.deepEqual(reportQuality(nov, 'SAR'), { transactions: 9, spendCount: 4, uncategorized: 2, categorizedPercent: 50 });
    assert.equal(reportQuality(oct, 'USD').categorizedPercent, null);
  });
  it('works out net worth per currency', () => {
    assert.deepEqual(netWorth(nov), [
      { currency: 'SAR', assets: '9000.00', debts: '2500.00', net: '6500.00' },
      { currency: 'USD', assets: '50.00', debts: '0.00', net: '50.00' },
    ]);
  });
  it('counts a card kept as an account and as a debt once', () => {
    const account = (id: string, kind: 'bank' | 'card' | 'loan', balance: string) => ({ id, name: id, kind, currency: 'SAR', institution: null, last4: null, balance, archived: false });
    const s = summary('2026-11', {
      accounts: [account('bank', 'bank', '2000.00'), account('card', 'card', '-5000.00'), account('loan', 'loan', '-1000.00')],
      balances: [{ currency: 'SAR', amount: '-4000.00' }], debts: [{ currency: 'SAR', amount: '6300.00' }], unlinked_debts: [{ currency: 'SAR', amount: '300.00' }],
    });
    assert.deepEqual(netWorth(s), [{ currency: 'SAR', assets: '2000.00', debts: '6300.00', net: '-4300.00' }]);
  });
  it('reads the server\'s net worth when it is given: money lent, holdings at cost, borrowed money and installments', () => {
    const s = summary('2026-11', { net_worth_parts: [{ currency: 'KWD', accounts: '100.125', receivables: '20.000', holdings_at_cost: '5.500', debts: '30.000',
      installments: '1.250', net: '94.375' }] });
    assert.deepEqual(netWorth(s), [{ currency: 'KWD', assets: '125.625', debts: '31.250', net: '94.375' }]);
  });
});

describe('safe to spend', () => {
  const data: FinanceSafeToSpend = {
    month: '2026-11', today: '2026-11-27', month_end: '2026-11-30', days_left: 4, base_currency: 'SAR', timezone: 'Asia/Riyadh', excluded: [], combine_currencies: false,
    combined_status: 'off', combined: null, missing_rates: [], fx_max_age_days: 7, accounts_counted: 2, note: '',
    currencies: [{ currency: 'SAR', available: '10000.00', bills: '500.00', minimum_payments: '0.00', goals: '1000.00', buffer: '500.00', safe_to_spend: '8000.00', per_day: '2000.00', short: false, lines: [] }],
  };
  it('draws the runway from today to the month end with bills on their day', () => {
    const points = runway(data, 'SAR', [{ id: 'b', name: 'Internet', amount: '500.00', currency: 'SAR', cadence: 'monthly', due_on: '2026-11-29', days_until: 2, category: 'telecom' }], '100.00');
    assert.deepEqual(points.map((p) => p.day), ['2026-11-27', '2026-11-28', '2026-11-29', '2026-11-30']);
    assert.deepEqual(points.map((p) => p.balance), ['8500.00', '8400.00', '7800.00', '7700.00']);
    assert.deepEqual(points[2].bills, [{ name: 'Internet', amount: '500.00' }]);
    assert.deepEqual(runway(data, 'USD', [], '1'), []);
  });
  it('takes an overdue bill off today, and leaves out a past one that was paid', () => {
    const late = { id: 'w', name: 'Water', amount: '80.00', currency: 'SAR', cadence: 'monthly' as const, due_on: '2026-11-20', days_until: -7, category: 'utilities' as const, overdue: true };
    const paid = { ...late, id: 'p', overdue: false };
    assert.deepEqual(runway(data, 'SAR', [late, paid], '0').map((p) => p.balance), ['8420.00', '8420.00', '8420.00', '8420.00']);
  });
  it('paces spending without the categories bills already cover', () => {
    const s = summary('2026-11', { spend: [{ currency: 'SAR', amount: '6000.00' }], spend_by_category: [
      { category: 'housing', currency: 'SAR', amount: '5000.00', count: 1 }, { category: 'dining', currency: 'SAR', amount: '1000.00', count: 9 }] });
    const rent = { category: 'housing' as const, currency: 'SAR' };
    assert.equal(spendOutsideBills(s, 'SAR', [rent]), '1000.00');
    assert.equal(spendOutsideBills(s, 'SAR', []), '6000.00');
    assert.equal(spendOutsideBills(s, 'SAR', [{ category: 'housing', currency: 'USD' }]), '6000.00');
  });
  it('averages the daily pace over the last 30 days', () => {
    assert.equal(dailyPace('500.00', 5, '3000.00', 30, 'SAR'), '100.00');
    assert.equal(dailyPace('3100.00', 31, '0', 30, 'SAR'), '103.33');
  });
  it('compares the Guardian category caps with this month', () => {
    const rules = { allowed_currencies: [], max_single: [], max_daily_total: [], blocked_categories: [], blocked_merchants: [], approval_above: [], quiet_hours: null, cooling_off: null,
      category_caps: [{ category: 'fuel' as const, currency: 'SAR', amount: '600.00' }, { category: 'dining' as const, currency: 'SAR', amount: '100.00' }, { category: 'travel' as const, currency: 'USD', amount: '5.00' }] };
    const nov = summary('2026-11', { spend_by_category: [{ category: 'fuel', currency: 'SAR', amount: '540.00', count: 2 }, { category: 'dining', currency: 'SAR', amount: '120.00', count: 1 }] });
    assert.deepEqual(guardrails(rules, nov, 'SAR'), [
      { category: 'dining', spent: '120.00', limit: '100.00', percent: 120, tone: 'bad' },
      { category: 'fuel', spent: '540.00', limit: '600.00', percent: 90, tone: 'warn' },
    ]);
  });
});
