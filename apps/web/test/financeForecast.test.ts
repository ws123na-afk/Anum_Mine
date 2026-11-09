// The forecast and the emergency reserve on the web (src/lib/financeForecast.ts, docs/finance.md#forecast). The figures
// are the API's; these helpers only shape them. Synthetic values only.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FinanceCurrencyForecast, FinanceForecast, FinanceReserve } from '@anum/contracts';
import { FINANCE_TABS, hashForTab, tabFromHash } from '../src/lib/finance.ts';
import { FINANCE_NAV, OWN_HEAD_TABS, groupOf } from '../src/lib/financeNav.ts';
import { changeToMonthEnd, chartPoints, currencyForecast, eventsUntil, firstWarning, horizonEnd, monthsTarget, progressValue, reserveFor, targetDrifted } from '../src/lib/financeForecast.ts';
import { suggestionsViewFromHash } from '../src/lib/financeSuggestions.ts';

const month = (m: string, on: string, expected: string) => ({ month: m, on, expected, low: expected, high: expected, money_in: '0.00', money_out: '0.00', spending: '0.00' });
const row: FinanceCurrencyForecast = {
  currency: 'SAR', actual: '5000.00', accounts_counted: 1, reserve_target: '3000.00',
  spend: { basis: 'months', months: ['2026-08', '2026-09', '2026-10'], monthly: '3000.00', daily: '98.56', daily_low: '80.00', daily_high: '120.00' },
  month_end: month('2026-11', '2026-11-30', '2400.00'), months: [month('2026-11', '2026-11-30', '2400.00'), month('2026-12', '2026-12-31', '-100.00'), month('2027-01', '2027-01-31', '900.00')],
  points: [
    { on: '2026-11-28', expected: '5000.00', low: '4900.00', high: '5000.00' },
    { on: '2026-11-29', expected: '2500.00', low: '2380.00', high: '2520.00' },
    { on: '2026-11-30', expected: '2400.00', low: '2260.00', high: '2440.00' },
    { on: '2026-12-01', expected: '2300.00', low: '2140.00', high: '2360.00' },
  ],
  events: [
    { on: '2026-11-29', kind: 'bill', label: 'Rent', ref_id: 'b1', currency: 'SAR', amount: '-2500.00', status: 'expected', note: 'bill_due', params: {} },
    { on: '2026-12-27', kind: 'salary', label: 'Example Co', ref_id: 'sp', currency: 'SAR', amount: '9050.00', status: 'expected', note: 'salary_expected', params: {} },
  ],
  warnings: [{ kind: 'below_zero', on: '2026-12-20', balance: '-50.00', threshold: '0.00' }, { kind: 'below_reserve', on: '2026-11-29', balance: '2500.00', threshold: '3000.00' }],
  assumptions: [], left_out: [],
};
const forecast: FinanceForecast = { today: '2026-11-28', month_end: '2026-11-30', horizon_end: '2027-01-31', timezone: 'Asia/Riyadh', base_currency: 'SAR', currencies: [row], note: '' };

describe('forecast', () => {
  it('draws the API points with their band and each day\'s movements, up to the horizon chosen', () => {
    const points = chartPoints(row, forecast.month_end);
    assert.deepEqual(points.map((p) => p.day), ['2026-11-28', '2026-11-29', '2026-11-30']);
    assert.deepEqual(points[1], { day: '2026-11-29', balance: '2500.00', low: '2380.00', high: '2520.00', bills: [{ name: 'Rent', amount: '-2500.00' }] });
    assert.equal(chartPoints(row, forecast.horizon_end).length, 4);
    assert.equal(currencyForecast(forecast, 'USD'), null);
    assert.equal(currencyForecast(forecast, 'SAR'), row);
  });
  it('warns below zero first, then below the reserve, within the horizon shown', () => {
    assert.equal(firstWarning(row)?.kind, 'below_zero');
    assert.equal(firstWarning(row, forecast.month_end)?.kind, 'below_reserve');
    assert.equal(firstWarning({ ...row, warnings: [] }), null);
  });
  it('reads the month end for this month or the third month, the change and the events', () => {
    assert.equal(horizonEnd(row, 'month').expected, '2400.00');
    assert.equal(horizonEnd(row, 'three').expected, '900.00');
    assert.equal(changeToMonthEnd(row), '-2600.00');
    assert.deepEqual(eventsUntil(row, forecast.month_end).map((e) => e.label), ['Rent']);
  });
});

describe('emergency reserve', () => {
  const reserve: FinanceReserve = {
    goal_id: 'g1', name: 'Emergency', currency: 'KWD', mode: 'months', months: 3, target: '1500.000', recalculated_target: '1650.000', essential_monthly: '550.000',
    essential_basis: 'essential', saved: '600.000', progress_percent: 40, months_covered: '1.0', held_in: 'goal', account_id: null, account_name: null, paused: false,
    monthly_contribution: null, status: 'building',
  };
  it('works the months target out exactly in the currency\'s digits', () => {
    assert.equal(monthsTarget('550.125', 3, 'KWD'), '1650.375');
    assert.equal(monthsTarget('6000.00', 6, 'SAR'), '36000.00');
    assert.equal(monthsTarget(null, 3, 'SAR'), null);
    assert.equal(monthsTarget('100.00', 0, 'SAR'), null);
  });
  it('offers to update a target that moved more than 5%', () => {
    assert.equal(targetDrifted(reserve), true);
    assert.equal(targetDrifted({ ...reserve, recalculated_target: '1520.000' }), false);
    assert.equal(targetDrifted({ ...reserve, recalculated_target: null }), false);
  });
  it('caps the progress bar and finds the reserve or the suggestion for a currency', () => {
    assert.equal(progressValue({ progress_percent: 140 }), 100);
    assert.equal(progressValue({ progress_percent: null }), 0);
    const found = reserveFor({ reserves: [reserve], suggestions: [{ currency: 'SAR', essential_monthly: '6000.00', essential_basis: 'bare', months: 3, target: '18000.00' }] }, 'SAR');
    assert.equal(found.reserve, null);
    assert.equal(found.suggestion?.target, '18000.00');
    assert.equal(reserveFor({ reserves: [reserve], suggestions: [] }, 'KWD').reserve, reserve);
  });
});

describe('finance settings page', () => {
  it('is one tab under data capture with its own head', () => {
    assert.ok(FINANCE_TABS.includes('settings'));
    assert.equal(tabFromHash(hashForTab('settings')), 'settings');
    assert.equal(groupOf('settings'), 'capture');
    assert.ok(OWN_HEAD_TABS.has('settings'));
    assert.equal(FINANCE_NAV.flatMap((g) => g.entries).filter((e) => e.kind === 'tab' && e.tab === 'settings').length, 1);
  });
  it('links straight to the rules and automation views of Suggestions', () => {
    assert.equal(suggestionsViewFromHash('#finance-suggestions?view=automation'), 'automation');
    assert.equal(suggestionsViewFromHash('#finance-suggestions?view=rules'), 'rules');
    assert.equal(suggestionsViewFromHash('#finance-suggestions?view=nope'), 'queue');
    assert.equal(suggestionsViewFromHash('#finance-suggestions'), 'queue');
  });
});
