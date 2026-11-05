// Debts and goals planning (src/lib/financeDebtPlan.ts, docs/finance.md#debts-and-goals): payoff strategy,
// what-if, outlook and goal amounts, all from the records given. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceDebt } from '@anum/contracts';
import {
  addMonths, daysBetween, debtProgress, fromMinor, goalMonthly, goalPercent, monthsLeft, nextDueOn, outlook, payoffMonth, payoffOrder, planCurrencies,
  planDebts, simulatePayoff, toMinor, totalProgress, whatIf, type PlanDebt,
} from '../src/lib/financeDebtPlan.ts';

const debt = (id: string, balance: number, aprPercent: number, minimum: number): PlanDebt => ({ id, name: id, balance, aprPercent, minimum });
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

describe('payoff strategy', () => {
  test('avalanche takes the highest rate first, snowball the smallest balance first', () => {
    const big = debt('big', 300000, 24, 1000), small = debt('small', 50000, 6, 1000);
    assert.deepEqual(payoffOrder([small, big], 'avalanche').map((d) => d.id), ['big', 'small']);
    assert.deepEqual(payoffOrder([big, small], 'snowball').map((d) => d.id), ['small', 'big']);
  });

  test('without interest the months are the balance over the payment', () => {
    assert.equal(simulatePayoff([debt('a', 100000, 0, 10000)], 'avalanche', null).months, 10);
    assert.equal(simulatePayoff([debt('a', 100000, 0, 10000)], 'avalanche', 25000).months, 4);
  });

  test('interest is a twelfth of the yearly rate on the month\'s balance, rounded once', () => {
    const plan = simulatePayoff([debt('a', 120000, 12, 0)], 'avalanche', 121200);
    assert.equal(plan.months, 1);
    assert.equal(plan.interest, 1200);
    assert.deepEqual(plan.balances, [0]);
  });

  test('a paid-off debt frees its minimum for the next one (the payment stays the same)', () => {
    const plan = simulatePayoff([debt('a', 10000, 0, 5000), debt('b', 30000, 0, 5000)], 'snowball', 10000);
    assert.equal(plan.months, 4);
    assert.deepEqual(plan.balances, [30000, 20000, 10000, 0]);
    assert.deepEqual(plan.paidOffIn, { a: 2, b: 4 });
  });

  test('avalanche costs less interest than snowball when the large debt has the high rate', () => {
    const debts = [debt('card', 900000, 30, 20000), debt('car', 1550000, 4, 183000)];
    const avalanche = simulatePayoff(debts, 'avalanche', 300000), snowball = simulatePayoff(debts, 'snowball', 300000);
    assert.ok(avalanche.months !== null && snowball.months !== null);
    assert.ok(avalanche.interest < snowball.interest);
  });

  test('payments that never catch up with the interest never end', () => {
    const plan = simulatePayoff([debt('a', 100000, 24, 1000)], 'avalanche', null);
    assert.equal(plan.months, null);
  });

  test('a planned payment below the minimums is not used: the minimums are', () => {
    const plan = simulatePayoff([debt('a', 100000, 0, 10000), debt('b', 100000, 0, 10000)], 'avalanche', 5000);
    assert.equal(plan.belowMinimums, true);
    assert.equal(plan.payment, 20000);
    assert.equal(plan.months, 10);
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
    const plain = whatIf([debt('a', 120000, 0, 10000)], 'avalanche', null, 10000);
    assert.deepEqual([plain.baseMonths, plain.withMonths, plain.monthsSaved, plain.interestSaved], [12, 6, 6, 0]);
    const charged = whatIf([debt('a', 500000, 18, 15000)], 'avalanche', 20000, 5000);
    assert.ok(charged.monthsSaved !== null && charged.monthsSaved > 0);
    assert.ok(charged.interestSaved > 0);
  });

  test('no saving can be named when the plan never ends', () => {
    assert.equal(whatIf([debt('a', 100000, 24, 1000)], 'avalanche', null, 0).monthsSaved, null);
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

  test('the outlook: debt under the plan and goals growing to their targets', () => {
    const points = outlook([debt('a', 40000, 0, 10000)], 'avalanche', null, [goal({}), goal({ currency: 'AED' }), goal({ paused: true })], 'SAR', '2026-11-05', 4);
    assert.deepEqual(points.map((p) => p.month), ['2026-11', '2026-12', '2027-01', '2027-02', '2027-03']);
    assert.deepEqual(points.map((p) => p.debt), [40000, 30000, 20000, 10000, 0]);
    // The active goal grows 100 a month to its 400 target; the paused one stays at 100.
    assert.deepEqual(points.map((p) => p.savings), [20000, 30000, 40000, 50000, 50000]);
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
