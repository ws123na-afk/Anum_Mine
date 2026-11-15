// Budgets, categories and recurring helpers (src/lib/financeBudgets.ts, docs/finance.md#budgets-and-planning).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceCategoryTree, FinancePlanBudgetLine } from '@anum/contracts';
import { budgetFormError, budgetInput, emptyBudget, filterTree, meterValue, nodeName, priceChangeText, riskTone, rolloverText, showsRollover } from '../src/lib/financeBudgets.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { safeNote } from '../src/lib/finance.ts';

const line = (over: Partial<FinancePlanBudgetLine>): FinancePlanBudgetLine => ({
  id: 'b1', name: 'Food', kind: 'spending', period: 'monthly', currency: 'AED', amount: '1000.00', period_start: '2026-11-01', period_end: '2026-11-30',
  days_left: 16, categories: ['groceries'], subcategory_ids: [], fixed: false, essential: true, allocation: 'food', alert_percent: 80,
  reserve_in_safe_to_spend: false, active: true, notes: null, goal_id: null, debt_ids: [], spent: '250.00', remaining: '750.00', over: '0.00',
  percent: 25, coming: '0.00', coming_items: [], forecast: '480.00', risk: 'ok', rows: 4, basis: 'spending', goal_saved: null, goal_target: null,
  rollover: 'none', rollover_cap: null, overspend: 'absorb', carried_in: '0.00', available: '1000.00', chain: [], chain_periods: 0, starts_on: null, ends_on: null, ...over,
});

describe('budget form', () => {
  test('a new form is a monthly spending budget and says what is missing, in order', () => {
    setLocale('en');
    const form = emptyBudget('AED');
    assert.equal(form.period, 'monthly');
    assert.equal(budgetFormError(form), 'Enter a name.');
    assert.equal(budgetFormError({ ...form, name: 'Food', amount: '0' }), 'Enter an amount above zero.');
    assert.equal(budgetFormError({ ...form, name: 'Food', amount: '500' }), 'Choose at least one category.');
    assert.equal(budgetFormError({ ...form, name: 'Food', amount: '500', categories: ['groceries'] }), null);
    assert.equal(budgetFormError({ ...form, name: 'Reserve', amount: '500', kind: 'emergency' }), 'Choose the goal.');
    assert.match(budgetFormError({ ...form, name: 'Trip', amount: '9', categories: ['travel'], period: 'custom', starts_on: '2026-12-10', ends_on: '2026-12-01' }) ?? '', /last day/);
  });

  test('editing sends the whole budget back, with a custom period\'s dates and a weekly budget\'s first day', () => {
    assert.equal(budgetInput(line({})).starts_on, null);
    const custom = budgetInput(line({ period: 'custom', period_start: '2026-12-01', period_end: '2026-12-20' }));
    assert.deepEqual([custom.starts_on, custom.ends_on], ['2026-12-01', '2026-12-20']);
    const weekly = budgetInput(line({ period: 'weekly', period_start: '2026-11-09', period_end: '2026-11-15' }));
    assert.deepEqual([weekly.starts_on, weekly.ends_on], ['2026-11-09', null]);
  });

  test('the meter stays between 0 and 100 and each risk has a tone', () => {
    assert.equal(meterValue(line({ percent: 140 })), 100);
    assert.equal(meterValue(line({ percent: null })), 0);
    assert.deepEqual((['ok', 'watch', 'high', 'over', 'behind', 'unknown'] as const).map(riskTone), ['ok', 'warn', 'bad', 'bad', 'warn', 'muted']);
  });
});

describe('categories', () => {
  const tree: Pick<FinanceCategoryTree, 'groups'> = { groups: [
    { id: 'food_drinks', name: 'Food and drinks', name_ar: 'الطعام والمشروبات', debt_types: [], categories: [
      { id: 'groceries', name: 'Groceries', name_ar: 'البقالة', spending: true, essential: true, subcategories: [
        { id: 'sys:vegetables', name: 'Vegetables', name_ar: 'خضار', built_in: true, archived: false, essential: true }] }] },
    { id: 'charity', name: 'Religious and charitable', name_ar: 'الديني والخيري', debt_types: [], categories: [
      { id: 'charity', name: 'Religious and charitable', name_ar: 'الديني والخيري', spending: true, essential: false, subcategories: [
        { id: 'sys:zakat', name: 'Zakat', name_ar: 'الزكاة', built_in: true, archived: false, essential: true }] }] },
  ] };

  test('a search keeps a group by its own name, a category or a subcategory, in English or Arabic', () => {
    assert.deepEqual(filterTree(tree, '').map((g) => g.id), ['food_drinks', 'charity']);
    assert.deepEqual(filterTree(tree, 'zakat').map((g) => g.id), ['charity']);
    assert.deepEqual(filterTree(tree, 'خضار').map((g) => g.id), ['food_drinks']);
    assert.deepEqual(filterTree(tree, 'nothing like it'), []);
  });

  test('names follow the language; the owner\'s own names stay as typed', () => {
    setLocale('ar');
    assert.equal(nodeName({ name: 'Zakat', name_ar: 'الزكاة' }), 'الزكاة');
    assert.equal(nodeName({ name: 'Mosque fund', name_ar: null }), 'Mosque fund');
    setLocale('en');
    assert.equal(nodeName({ name: 'Zakat', name_ar: 'الزكاة' }), 'Zakat');
  });
});

describe('recurring and safe to spend', () => {
  test('a price change carries its sign', () => {
    setLocale('en');
    assert.match(priceChangeText('5.00', 'AED'), /^\+/);
    assert.doesNotMatch(priceChangeText('-3.00', 'AED'), /^\+/);
  });

  test('the new Safe to spend lines are worded with their figures', () => {
    setLocale('en');
    const text = safeNote({ note: 'installment_due', params: { number: '2', of: '4', date: '2026-11-20' }, currency: 'AED' });
    assert.match(text, /Installment/);
    assert.match(text, /2026-11-20/);
    assert.match(safeNote({ note: 'budget_reserve', params: { spent: '100', amount: '2000' }, currency: 'AED' }), /kept back/);
  });
});

describe('budget rollover', () => {
  test('a capped rollover needs its cap and an edit sends the setting and the budget\'s own dates back', () => {
    setLocale('en');
    const form = { ...emptyBudget('AED'), name: 'Food', amount: '500', categories: ['groceries' as const] };
    assert.equal(form.rollover, 'none');
    assert.equal(budgetFormError({ ...form, rollover: 'capped', rollover_cap: '' }), 'Enter the most that may carry into the next period.');
    assert.equal(budgetFormError({ ...form, rollover: 'capped', rollover_cap: '150' }), null);
    const edit = budgetInput(line({ rollover: 'capped', rollover_cap: '150.00', overspend: 'carry' }));
    assert.deepEqual([edit.rollover, edit.rollover_cap, edit.overspend], ['capped', '150.00', 'carry']);
    // A custom budget that carries repeats: the shown window is not its first one, so its own dates go back.
    const custom = budgetInput(line({ period: 'custom', period_start: '2026-11-10', period_end: '2026-11-19', starts_on: '2026-10-01', ends_on: '2026-10-10' }));
    assert.deepEqual([custom.starts_on, custom.ends_on], ['2026-10-01', '2026-10-10']);
    assert.equal(budgetInput(line({ kind: 'remittance' })).rollover, undefined);
  });

  test('rollover shows only when it does something, in words in both languages', () => {
    assert.equal(showsRollover(line({})), false);
    assert.equal(showsRollover(line({ overspend: 'carry' })), true);
    assert.equal(showsRollover(line({ carried_in: '-50.00' })), true);
    setLocale('en');
    assert.match(rolloverText({ rollover: 'capped', cap: '150.00', overspend: 'carry' }, 'AED'), /at most/);
    assert.match(rolloverText({ rollover: 'surplus', cap: null, overspend: 'absorb' }, 'AED'), /^What is left carries over; an overspend is not carried$/);
    setLocale('ar');
    assert.match(rolloverText({ rollover: 'none', cap: null, overspend: 'carry' }, 'AED'), /^لا يُرحَّل شيء؛ تُخصم/);
    setLocale('en');
  });
});
