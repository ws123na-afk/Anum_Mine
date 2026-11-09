// Debts and goals planning (src/lib/financeDebtPlan.ts, docs/finance.md#debts-and-goals): reading the API's
// payoff answer, the what-if, the outlook and goal amounts. The payoff engine itself is tested where it lives,
// in services/api/tests/test_finance_payoff.py. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceDebt, FinancePayoff } from '@anum/contracts';
import {
  addMonths, daysBetween, debtProgress, fromMinor, goalMonthly, goalPercent, monthsLeft, nextDueOn, orderedDebts, outlook, payoffFor, payoffMonth,
  planCurrencies, planDebts, toMinor, totalProgress, whatIf,
} from '../src/lib/financeDebtPlan.ts';

// An answer shaped like GET /planner/payoff: A 1,200 at 12% (min 100), B 200 at 0% (min 50), paying 300 a month.
const view = (over: Partial<FinancePayoff> = {}): FinancePayoff => ({
  currency: 'AED', payment: '300.00', planned_payment: '300.00', minimums_only: false, below_minimums: false,
  debts: [{ id: 'a', name: 'Card A', balance: '1200.00', apr_percent: '12', minimum: '100.00', avalanche_month: 5, snowball_month: 5 },
    { id: 'b', name: 'Loan B', balance: '200.00', apr_percent: '0', minimum: '50.00', avalanche_month: 4, snowball_month: 1 }],
  avalanche: { strategy: 'avalanche', months: 5, total_interest: '35.97', order: ['a', 'b'], paid_off_in: { a: 5, b: 4 }, balances: ['1112.00', '821.62', '528.84', '233.63', '0.00'] },
  snowball: { strategy: 'snowball', months: 5, total_interest: '39.03', order: ['b', 'a'], paid_off_in: { b: 1, a: 5 }, balances: ['1112.00', '823.12', '531.35', '236.66', '0.00'] },
  cheaper: 'avalanche', interest_saved: '3.06', first_win_months: 1, missing_rates: ['Loan B'], ...over,
});
const record = (over: Partial<FinanceDebt>): FinanceDebt => ({
  id: 'd', name: 'Card', kind: 'card', balance: '1000.00', currency: 'SAR', apr_percent: '18', minimum_payment: '50.00', due_day: 21,
  account_id: null, original_balance: null, notes: null, created_at: '', updated_at: '', ...over,
});
const goal = (over: Record<string, unknown>) => ({ target_amount: '400.00', saved_amount: '100.00', currency: 'SAR', monthly_contribution: '100.00', target_on: null, paused: false, ...over });

describe('money in minor units', () => {
  test('amounts are whole fils or halalas, with each currency\'s digits', () => {
    assert.equal(toMinor('1234.56', 'SAR'), 123456);
    assert.equal(toMinor('1.250', 'KWD'), 1250);
    assert.equal(toMinor(null, 'SAR'), 0);
    assert.equal(toMinor('abc', 'SAR'), 0);
    assert.equal(fromMinor(123456, 'SAR'), '1234.56');
  });

  test('planDebts reads one currency and leaves settled debts out', () => {
    const rows = planDebts([record({ id: 'a' }), record({ id: 'b', currency: 'AED' }), record({ id: 'c', balance: '0.00' })], 'SAR');
    assert.deepEqual(rows, [{ id: 'a', name: 'Card', balance: 100000, aprPercent: 18, minimum: 5000 }]);
  });
});

describe('the API payoff answer', () => {
  test('one strategy read into minor units', () => {
    const plan = payoffFor(view(), 'snowball');
    assert.deepEqual([plan.months, plan.interest, plan.payment, plan.belowMinimums], [5, 3903, 30000, false]);
    assert.deepEqual(plan.paidOffIn, { b: 1, a: 5 });
    assert.deepEqual(plan.order, ['b', 'a']);
    assert.deepEqual(plan.balances, [111200, 82312, 53135, 23666, 0]);
  });

  test('the plan table lists the debts in the order extra money goes', () => {
    assert.deepEqual(orderedDebts(view(), 'avalanche').map((d) => [d.id, d.balance, d.aprPercent, d.minimum]), [['a', 120000, 12, 10000], ['b', 20000, 0, 5000]]);
    assert.deepEqual(orderedDebts(view(), 'snowball').map((d) => d.id), ['b', 'a']);
  });

  test('a planned payment below the minimums is flagged as the API says', () => {
    assert.equal(payoffFor(view({ below_minimums: true, payment: '150.00' }), 'avalanche').belowMinimums, true);
  });

  test('payoff month counts the first payment in the start month', () => {
    assert.equal(addMonths('2026-11', 2), '2027-01');
    assert.equal(addMonths('2026-01', -1), '2025-12');
    assert.equal(payoffMonth('2026-11', 6), '2027-04');
    assert.equal(payoffMonth('2026-11', null), null);
  });
});

describe('what if', () => {
  test('paying more each month says how many months sooner and the interest saved', () => {
    const base = payoffFor(view(), 'avalanche');
    const more = payoffFor(view({ avalanche: { ...view().avalanche, months: 3, total_interest: '20.00' } }), 'avalanche');
    assert.deepEqual(whatIf(base, more, 10000), { extra: 10000, baseMonths: 5, withMonths: 3, monthsSaved: 2, interestSaved: 1597 });
  });

  test('no saving can be named when the plan never ends', () => {
    const never = payoffFor(view({ avalanche: { ...view().avalanche, months: null } }), 'avalanche');
    assert.equal(whatIf(never, never, 0).monthsSaved, null);
  });
});

describe('progress and goals', () => {
  test('payoff progress needs a starting balance', () => {
    assert.equal(debtProgress(record({ balance: '600.00', original_balance: '1000.00' })), 40);
    assert.equal(debtProgress(record({ original_balance: null })), null);
    assert.deepEqual(totalProgress([record({ balance: '600.00', original_balance: '1000.00' }), record({ balance: '50.00' })]), { percent: 40, start: 100000, now: 60000 });
    assert.equal(totalProgress([record({})]), null);
  });

  test('a goal\'s monthly amount: the one set, else what is left over the months left, never for a paused goal', () => {
    assert.equal(goalMonthly(goal({}), '2026-11-05'), 10000);
    assert.equal(goalMonthly(goal({ monthly_contribution: null, target_amount: '1100.00', target_on: '2027-04-30' }), '2026-11-05'), 16667);
    assert.equal(goalMonthly(goal({ monthly_contribution: null }), '2026-11-05'), null);
    assert.equal(goalMonthly(goal({ paused: true }), '2026-11-05'), 0);
    assert.equal(goalMonthly(goal({ saved_amount: '400.00' }), '2026-11-05'), 0);
    assert.equal(monthsLeft('2026-11-05', '2026-11-30'), 1);
    assert.equal(goalPercent(goal({})), 25);
  });

  test('the outlook: debt from the API\'s month-end balances, goals growing to their targets', () => {
    const points = outlook(40000, [30000, 20000, 10000, 0], true, [goal({}), goal({ currency: 'AED' }), goal({ paused: true })], 'SAR', '2026-11-05', 5);
    assert.deepEqual(points.map((p) => p.month), ['2026-11', '2026-12', '2027-01', '2027-02', '2027-03', '2027-04']);
    assert.deepEqual(points.map((p) => p.debt), [40000, 30000, 20000, 10000, 0, 0]);
    // The active goal grows 100 a month to its 400 target; the paused one stays at 100.
    assert.deepEqual(points.map((p) => p.savings), [20000, 30000, 40000, 50000, 50000, 50000]);
    // A plan that never ends keeps the last balance the API gave.
    assert.deepEqual(outlook(40000, [39000], false, [], 'SAR', '2026-11-05', 2).map((p) => p.debt), [40000, 39000, 39000]);
  });
});

describe('dates', () => {
  test('a debt\'s next due date is its due day this month, else next month, in short months the last day', () => {
    assert.equal(nextDueOn(31, '2026-11-05'), '2026-11-30');
    assert.equal(nextDueOn(3, '2026-11-05'), '2026-12-03');
    assert.equal(nextDueOn(3, '2026-12-20'), '2027-01-03');
    assert.equal(nextDueOn(null, '2026-12-20'), null);
    assert.equal(daysBetween('2026-11-05', '2026-11-21'), 16);
  });

  test('currencies with debts or goals, the base first', () => {
    assert.deepEqual(planCurrencies([{ currency: 'USD' }, { currency: 'SAR' }], [{ currency: 'AED' }], 'SAR'), ['SAR', 'AED', 'USD']);
    assert.deepEqual(planCurrencies([], [], 'SAR'), []);
  });
});
