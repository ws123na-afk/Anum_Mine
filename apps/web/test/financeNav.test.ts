// The Finance shell model (docs/finance.md#finance-shell).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { FINANCE_TABS } from '../src/lib/finance.ts';
import { FINANCE_NAV, OWN_HEAD_TABS, delta, groupOf, monthRange, monthsEnding, navTabs } from '../src/lib/financeNav.ts';
import { NAV } from '../src/lib/shell.ts';

describe('finance section nav', () => {
  it('offers every Finance tab exactly once and nothing that is not a page', () => {
    const tabs = navTabs();
    assert.deepEqual([...tabs].sort(), [...FINANCE_TABS].sort());
    assert.equal(new Set(tabs).size, tabs.length);
  });
  it('keeps the mockup order of the Finance group, with the personal money sections after Goals', () => {
    const finance = FINANCE_NAV[0].entries.map((e) => (e.kind === 'tab' ? e.tab : e.id));
    const order = ['overview', 'transactions', 'accounts', 'bills', 'budgets', 'debts', 'goals', 'commitments', 'people', 'payslips', 'remittances', 'reports', 'safe', 'advisor', 'guardian'].filter((id) => finance.includes(id as never));
    assert.deepEqual(finance, order);
  });
  it('ends with Business Expenses and P&L, a link to the business books', () => {
    const last = FINANCE_NAV[FINANCE_NAV.length - 1].entries[0];
    assert.equal(last.kind, 'link');
    assert.equal(last.kind === 'link' && last.href, '#business-books');
  });
  it('finds the group of a tab', () => {
    assert.equal(groupOf('ibkr'), 'trading');
    assert.equal(groupOf('sms'), 'capture');
    assert.equal(groupOf('overview'), 'finance');
  });
  it('lists the sections that draw their own head', () => {
    assert.ok(OWN_HEAD_TABS.has('safe') && OWN_HEAD_TABS.has('advisor'));
    assert.ok(OWN_HEAD_TABS.has('transactions') && OWN_HEAD_TABS.has('accounts'));
    assert.ok(!OWN_HEAD_TABS.has('overview'));
  });
  it('puts the life modules above Finance in the app sidebar', () => {
    const main = NAV[0].views;
    for (const view of ['vehicles', 'vault', 'attendance', 'purchases'] as const) assert.ok(main.indexOf(view) < main.indexOf('finance'), view);
  });
});

describe('periods', () => {
  it('gives a month its first and last day', () => {
    assert.deepEqual(monthRange('2026-02'), { from: '2026-02-01', to: '2026-02-28' });
    assert.deepEqual(monthRange('2024-02'), { from: '2024-02-01', to: '2024-02-29' });
    assert.deepEqual(monthRange('2026-12'), { from: '2026-12-01', to: '2026-12-31' });
  });
  it('lists the months that end with a month, across a year end', () => {
    assert.deepEqual(monthsEnding('2027-02', 4), ['2026-11', '2026-12', '2027-01', '2027-02']);
  });
});

describe('delta', () => {
  it('rounds to one decimal and colours by what is better', () => {
    assert.deepEqual(delta('7140', '7760', false), { direction: 'down', percent: -8, tone: 'good' });
    assert.deepEqual(delta('5680', '5070', false), { direction: 'up', percent: 12, tone: 'bad' });
    assert.deepEqual(delta('110', '100'), { direction: 'up', percent: 10, tone: 'good' });
  });
  it('reads tiny changes as flat', () => {
    assert.equal(delta('1000', '1003')?.tone, 'flat');
  });
  it('never invents a percentage from a zero base', () => {
    assert.deepEqual(delta('50', '0', false), { direction: 'up', percent: null, tone: 'bad' });
    assert.deepEqual(delta('0', '0'), { direction: 'flat', percent: 0, tone: 'flat' });
  });
  it('has no delta without both figures', () => {
    assert.equal(delta(null, '10'), null);
    assert.equal(delta('10', undefined), null);
    assert.equal(delta('abc', '10'), null);
  });
});
