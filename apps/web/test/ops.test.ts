// Unit tests for the operations pages' figures (src/lib/ops.ts): vehicle status and usage, the books' expense breakdown,
// recurring costs, budgets and month close, the Attention Center and attendance, and the catalogs (docs/home.md#operations-overview).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { BooksReport, VehicleDueItem } from '@anum/contracts';
import {
  amountIn, attentionTotal, budgetProgress, closeChecklist, daysChip, daysTone, expenseBreakdown, breakdownTotal, kmIn, kmPerLitre, mainCurrency, newestFirst,
  nextDue, papers, previousMonthName, singleMonth, rankAttention, topRecurring, trendFor, usageRows, vehicleStatus, weekAttendance, type AttentionItem,
} from '../src/lib/ops.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { opsAr } from '../src/lib/messages/ops.ar.ts';
import { opsEn } from '../src/lib/messages/ops.en.ts';

const due = (extra: Partial<VehicleDueItem>): VehicleDueItem => ({
  vehicle_id: 'v', registration: 'ABC 1', kind: 'insurance', source_id: 'v', due_on: '2026-11-16', days_left: 10, due_km: null, km_left: null, ...extra,
});
const group = (lines: [string, string][], total: string) => ({ lines: lines.map(([key, amount]) => ({ key, amount })), total });
function report(extra: Partial<BooksReport> = {}): BooksReport {
  return {
    period: { from: '2026-11-01', to: '2026-11-30', basis: 'accrual' }, base_currency: 'SAR',
    pnl: { currency: 'SAR', revenue: group([['service', '1000.00']], '1000.00'), cost_of_sales: group([['fuel', '300.00']], '300.00'), gross_profit: '700.00', gross_margin_pct: '70.0',
      operating_expenses: group([['office_rent', '500.00'], ['fuel', '100.00'], ['hosting', '50.00'], ['wages', '40.00'], ['phone', '20.00'], ['travel', '10.00']], '720.00'),
      operating_profit: '-20.00', other_income: group([], '0.00'), finance_costs: group([['loan_interest', '5.00']], '5.00'), fx_differences: '0.00', net_profit: '-25.00',
      net_margin_pct: null, not_converted: [], complete: true },
    tax: { label: 'VAT', country: 'SA', by_currency: [], allocations_without_tax_split: 0 },
    cash_flow: { lines: {}, net: [], paid_by_members: {} },
    balances: { as_of: '2026-11-30', aged_on: '2026-11-06', receivables: [], outstanding_invoices: [], outstanding_invoices_count: 0, payables: [],
      unpaid_bills: [{ bill_id: 'b1', vendor_name: 'Landlord', reference: '', due_date: '2026-10-15', days_overdue: 20, currency: 'SAR', balance: '500.00' }],
      unpaid_bills_count: 1, owed_to_members: [], debt: [], intercompany: [] },
    equity: { contributions: [], drawings: [] },
    trend: [{ month: '2026-10', revenue: '800.00', costs: '600.00', profit: '200.00', unconverted: false }, { month: '2026-11', revenue: '1000.00', costs: '1025.00', profit: '-25.00', unconverted: false }],
    customers: [], projects: [], cost_centres: [],
    budgets: [
      { id: 'a', name: 'Rent', period_start: '2026-11-01', period_end: '2026-11-30', currency: 'SAR', budget: '400.00', actual: '500.00', variance: '-100.00', used_pct: '125.0', comparable: true },
      { id: 'b', name: 'Fuel', period_start: '2026-11-01', period_end: '2026-11-30', currency: 'SAR', budget: '500.00', actual: '400.00', variance: '100.00', used_pct: '80.0', comparable: true },
      { id: 'c', name: 'Euro', period_start: '2026-11-01', period_end: '2026-11-30', currency: 'EUR', budget: '100.00', actual: null, variance: null, used_pct: null, comparable: false },
    ],
    recurring: { items: [
      { bill_id: null, template_id: 't1', vendor_id: 'v1', vendor_name: 'Landlord', category: 'office_rent', recurrence: 'monthly', interval: 1, currency: 'SAR', amount: '500.00', per_month: '500.00', last_bill_date: null, next_date: null, status: 'active' },
      { bill_id: null, template_id: 't2', vendor_id: 'v2', vendor_name: 'Host', category: 'hosting', recurrence: 'yearly', interval: 1, currency: 'SAR', amount: '1200.00', per_month: '100.00', last_bill_date: null, next_date: null, status: 'active' },
      { bill_id: null, template_id: 't3', vendor_id: 'v3', vendor_name: 'Abroad', category: 'travel', recurrence: 'monthly', interval: 1, currency: 'EUR', amount: '900.00', per_month: '900.00', last_bill_date: null, next_date: null, status: 'active' },
    ], per_month: [], upcoming: [] },
    allocations_in_review: 2, conversions: [], notes: [], truncated: false, ...extra,
  };
}

describe('vehicles', () => {
  test('status: overdue by date or kilometres, due soon, or in service', () => {
    assert.equal(vehicleStatus([]).tone, 'ok');
    assert.equal(vehicleStatus([]).label, 'In service');
    assert.equal(vehicleStatus([due({ days_left: 12 })]).label, 'Due in 12 days');
    assert.equal(vehicleStatus([due({ days_left: 0 })]).label, 'Due today');
    assert.equal(vehicleStatus([due({ days_left: 12 }), due({ days_left: -2 })]).tone, 'bad');
    assert.equal(vehicleStatus([due({ kind: 'service', days_left: null, due_on: null, km_left: '-10' })]).tone, 'bad');
    assert.equal(nextDue([due({ days_left: 30 }), due({ days_left: 3, kind: 'registration' })])?.kind, 'registration');
  });

  test('usage rows fill missing months with zero; one month reads its kilometres and money', () => {
    const usage = [{ month: '2026-11', distance_km: '300.0', fuel_litres: '40.000' }];
    assert.deepEqual(usageRows(usage, ['2026-10', '2026-11']), [{ month: '2026-10', litres: '0', km: '0' }, { month: '2026-11', litres: '40.000', km: '300.0' }]);
    assert.equal(kmIn(usage, '2026-10'), '0');
    assert.equal(amountIn([{ month: '2026-11', currency: 'SAR', amount: '9.50' }], '2026-11', 'SAR'), '9.50');
    assert.equal(amountIn([{ month: '2026-11', currency: 'AED', amount: '9.50' }], '2026-11', 'SAR'), '0');
    assert.equal(mainCurrency([{ currency: 'AED' }, { currency: 'SAR' }, { currency: 'SAR' }], 'USD'), 'SAR');
    assert.equal(mainCurrency([], 'USD'), 'USD');
  });

  test('fuel economy as km per litre, papers by expiry, newest records first', () => {
    assert.equal(kmPerLitre('10.00'), '10.0');
    assert.equal(kmPerLitre('8'), '12.5');
    assert.equal(kmPerLitre(null), null);
    assert.equal(kmPerLitre('0'), null);
    assert.deepEqual(papers({ registration_expires_on: '2026-11-18', insurance_expires_on: '2026-11-01', inspection_expires_on: null }, '2026-11-06'),
      [{ kind: 'insurance', on: '2026-11-01', days: -5 }, { kind: 'registration', on: '2026-11-18', days: 12 }]);
    const recs = newestFirst([
      { kind: 'fuel', id: 'a', at: '2026-11-01T10:00:00Z', title: '', detail: '', figure: '' },
      { kind: 'trips', id: 'b', at: '2026-11-05T10:00:00Z', title: '', detail: '', figure: '' },
      { kind: 'service', id: 'c', at: '2026-11-03T12:00:00Z', title: '', detail: '', figure: '' },
    ], 2);
    assert.deepEqual(recs.map((r) => r.id), ['b', 'c']);
  });
});

describe('business books dashboard', () => {
  test('the breakdown joins a category across groups, sorts it and folds the tail into Other', () => {
    const rows = expenseBreakdown(report());
    assert.deepEqual(rows.map((r) => [r.key, r.amount]), [['office_rent', '500.00'], ['fuel', '400.00'], ['hosting', '50.00'], ['wages', '40.00'], ['__other', '35.00']]);
    assert.equal(rows[4].label, 'Other');
    assert.equal(breakdownTotal(rows, 'SAR'), '1025.00');
    assert.equal(expenseBreakdown(report(), 10).length, 7);
  });

  test('recurring costs only in the base currency, with their share; budgets most used first; the month close', () => {
    const recurring = topRecurring(report(), '1025.00');
    assert.deepEqual(recurring.map((r) => [r.name, r.share]), [['Landlord', 48.7], ['Host', 9.7]]);
    assert.deepEqual(budgetProgress(report()).map((b) => [b.name, b.tone]), [['Rent', 'bad'], ['Fuel', 'ok']]);
    const close = closeChecklist(report());
    assert.deepEqual(close.map((c) => [c.key, c.count, c.done]), [['allocations', 2, false], ['overdueBills', 1, false], ['overdueInvoices', 0, true], ['converted', 0, true]]);
  });

  test('trend rows follow the months asked for; a period is a single month only when it covers one calendar month', () => {
    assert.deepEqual(trendFor(report(), ['2026-09', '2026-10', '2026-11']).map((r) => [r.month, r.income]), [['2026-09', '0'], ['2026-10', '800.00'], ['2026-11', '1000.00']]);
    assert.equal(singleMonth({ from: '2026-11-01', to: '2026-11-30' }), '2026-11');
    assert.equal(singleMonth({ from: '2026-02-01', to: '2026-02-28' }), '2026-02');
    assert.equal(singleMonth({ from: '2026-01-01', to: '2026-12-31' }), null);
    assert.equal(previousMonthName('2026-01'), 'December');
  });
});

describe('attention center and attendance', () => {
  const item = (key: string, rank: number, count = 1): AttentionItem => ({ key, kind: 'vehicle', title: key, detail: '', chip: null, tone: 'warn', href: '#', rank, count });
  test('most urgent first, counted once per thing', () => {
    const items = [item('b', 10), item('a', -1, 3), item('c', 2)];
    assert.deepEqual(rankAttention(items, 2).map((i) => i.key), ['a', 'c']);
    assert.equal(attentionTotal(items), 5);
    assert.equal(daysChip(12), '12 days');
    assert.equal(daysChip(0), 'Today');
    assert.equal(daysChip(-3), '3 days late');
    assert.deepEqual([daysTone(-1), daysTone(5), daysTone(40)], ['bad', 'warn', 'info']);
  });

  test('a week counts scheduled days attended, or the days worked without a schedule', () => {
    const day = (scheduled: number, worked: number) => ({ scheduled_seconds: scheduled, worked_seconds: worked });
    assert.deepEqual(weekAttendance([day(28800, 30000), day(28800, 0), day(0, 600)]), { attended: 1, scheduled: 2 });
    assert.deepEqual(weekAttendance([day(0, 600), day(0, 0)]), { attended: 1, scheduled: null });
  });

  test('English and Arabic catalogs have the same keys and placeholders, and Arabic reads in Arabic', () => {
    assert.deepEqual(Object.keys(opsAr).sort(), Object.keys(opsEn).sort());
    for (const [key, value] of Object.entries(opsEn)) {
      const names = (text: unknown) => [...JSON.stringify(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).filter((n) => n !== 'count').sort();
      assert.deepEqual([...new Set(names((opsAr as Record<string, unknown>)[key]))], [...new Set(names(value))], key);
    }
    setLocale('ar');
    assert.equal(vehicleStatus([]).label, 'في الخدمة');
    setLocale('en');
  });
});
