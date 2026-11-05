// Bills and budgets figures (src/lib/financeBills.ts, docs/finance.md#bills-and-budgets).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceBillOccurrence, FinanceBudgetLine } from '@anum/contracts';
import { billKpis, budgetAtRisk, budgetKpis, budgetTone, calendarWeeks, dayStrip, isOpen, pickTotal, statusTone, uniqueBills } from '../src/lib/financeBills.ts';

const row = (over: Partial<FinanceBillOccurrence>): FinanceBillOccurrence => ({
  bill_id: 'b1', name: 'Rent', category: 'housing', cadence: 'monthly', account_id: null, due_on: '2026-11-15', days_until: 10, expected: '2500.00',
  currency: 'SAR', status: 'planned', paid_amount: '0.00', link_ids: [], canonical_ids: [], ...over,
});
const line = (over: Partial<FinanceBudgetLine>): FinanceBudgetLine => ({
  category: 'fuel', currency: 'SAR', cap: '1000.00', spent: '500.00', remaining: '500.00', over: '0.00', percent: 50, state: 'ok', alert_percent: 80,
  alert_near: true, alert_over: true, projected: null, projected_over: null, ...over,
});

describe('bill figures', () => {
  test('the month, the next 7 days and overdue are per currency; paid dates leave the open totals', () => {
    const rows = [row({}), row({ bill_id: 'b2', expected: '290.00', days_until: 3, status: 'due_soon' }), row({ bill_id: 'b3', expected: '100.00', days_until: -2, status: 'overdue' }),
      row({ bill_id: 'b4', expected: '50.00', days_until: 1, status: 'paid', paid_amount: '45.00' }), row({ bill_id: 'b5', currency: 'AED', expected: '10.00' })];
    const k = billKpis(rows);
    assert.deepEqual(k.month, [{ currency: 'AED', amount: '10.00', count: 1 }, { currency: 'SAR', amount: '2940.00', count: 4 }]);
    assert.deepEqual(k.week, [{ currency: 'SAR', amount: '290.00', count: 1 }]);
    assert.deepEqual(k.overdue, [{ currency: 'SAR', amount: '100.00', count: 1 }]);
    assert.deepEqual(k.paid, [{ currency: 'SAR', amount: '45.00', count: 1 }]);
    assert.equal(pickTotal(k.month, 'SAR')?.amount, '2940.00');
    assert.equal(pickTotal(k.week, 'USD')?.currency, 'SAR');
    assert.equal(pickTotal([], 'SAR'), null);
    assert.equal(isOpen({ status: 'review' }), true);
  });

  test('the day strip counts due dates per day and marks today', () => {
    const strip = dayStrip('2026-11-01', '2026-11-03', [row({ due_on: '2026-11-02' }), row({ due_on: '2026-11-02' })], '2026-11-03');
    assert.deepEqual(strip.map((d) => [d.date, d.weekday, d.dueCount, d.today]), [[1, 0, 0, false], [2, 1, 2, false], [3, 2, 0, true]]);
  });

  test('the calendar pads the first week (November 2026 starts on a Sunday)', () => {
    const sunday = calendarWeeks('2026-11', 0);
    assert.equal(sunday[0][0], '2026-11-01');
    assert.equal(sunday.length, 5);
    const monday = calendarWeeks('2026-11', 1);
    assert.deepEqual(monday[0].slice(5), [null, '2026-11-01']);
    assert.ok(monday.every((w) => w.length === 7));
  });

  test('status tones and one row per bill', () => {
    assert.deepEqual((['paid', 'overdue', 'due_soon', 'review', 'planned'] as const).map(statusTone), ['ok', 'bad', 'warn', 'warn', 'muted']);
    assert.deepEqual(uniqueBills([row({}), row({ due_on: '2026-12-15' }), row({ bill_id: 'b2' })]).map((r) => r.bill_id), ['b1', 'b2']);
  });
});

describe('budget figures', () => {
  test('active budgets and what is left, per currency', () => {
    const k = budgetKpis({ budgets: [line({}), line({ category: 'dining', remaining: '120.50', cap: '300.00' }), line({ currency: 'AED' })] });
    assert.equal(k.active, 3);
    assert.deepEqual(k.remaining, [{ currency: 'AED', amount: '500.00', count: 1 }, { currency: 'SAR', amount: '620.50', count: 2 }]);
    assert.deepEqual(k.budget.find((b) => b.currency === 'SAR')?.amount, '1300.00');
  });

  test('the budget at risk: over first, then the pace that ends furthest past its cap', () => {
    assert.equal(budgetAtRisk({ budgets: [line({})] }), null);
    const pace = line({ category: 'dining', projected: '1640.00', projected_over: '640.00' });
    assert.equal(budgetAtRisk({ budgets: [line({}), pace, line({ category: 'travel', projected_over: '20.00' })] })?.category, 'dining');
    assert.equal(budgetAtRisk({ budgets: [pace, line({ category: 'fees', state: 'over', over: '1.00' })] })?.category, 'fees');
    assert.deepEqual([budgetTone({ state: 'ok' }), budgetTone({ state: 'near' }), budgetTone({ state: 'over' })], ['ok', 'warn', 'bad']);
  });
});
