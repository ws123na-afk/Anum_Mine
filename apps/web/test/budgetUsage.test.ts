import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale, t } from '../src/lib/i18n.ts';
import {
  barLengths, csvFileName, formatMeasure, isTimeSeries, measureOf, rangeFor, rowLabel, shareOf, tickLabel, ticks, usagePath, validRange, type UsageRow,
} from '../src/lib/budgetUsage.ts';

afterEach(() => setLocale('en'));

// Where the spend goes (docs/model-gateway.md#spend-breakdown): ranges, the request path, labels and the chart scale.
const row = (over: Partial<UsageRow> = {}): UsageRow => ({ key: '2026-10-03', label: null, input_tokens: 10, output_tokens: 5, total_tokens: 15, estimated_cost_usd: 1.5, calls: 2, unpriced_calls: 0, ...over });
const today = new Date('2026-11-10T13:00:00Z');

describe('ranges', () => {
  test('presets are UTC days, inclusive', () => {
    assert.deepEqual(rangeFor('month', today), { from: '2026-11-01', to: '2026-11-10' });
    assert.deepEqual(rangeFor('30d', today), { from: '2026-10-12', to: '2026-11-10' });
    assert.deepEqual(rangeFor('3m', today), { from: '2026-09-01', to: '2026-11-10' });
    assert.deepEqual(rangeFor('12m', today), { from: '2025-12-01', to: '2026-11-10' });
    assert.deepEqual(rangeFor('custom', today, { from: '2026-01-01', to: '2026-01-31' }), { from: '2026-01-01', to: '2026-01-31' });
  });
  test('a range is valid when to is not before from and it spans at most 400 days', () => {
    assert.equal(validRange({ from: '2026-10-01', to: '2026-10-01' }), true);
    assert.equal(validRange({ from: '2026-10-02', to: '2026-10-01' }), false);
    assert.equal(validRange({ from: '2025-01-01', to: '2026-10-01' }), false);
    assert.equal(validRange({ from: '', to: '2026-10-01' }), false);
  });
  test('the path names the group, scope, range and page, or asks for CSV of the whole range', () => {
    const range = { from: '2026-10-01', to: '2026-10-31' };
    assert.equal(usagePath({ group: 'day', scope: 'workspace', range }), '/api/v1/model-budgets/usage?group_by=day&scope=workspace&from=2026-10-01&to=2026-10-31&limit=50&offset=0');
    assert.equal(usagePath({ group: 'agent', scope: 'tenant', range, offset: 50, limit: 25 }), '/api/v1/model-budgets/usage?group_by=agent&scope=tenant&from=2026-10-01&to=2026-10-31&limit=25&offset=50');
    assert.equal(usagePath({ group: 'feature', scope: 'tenant', range, format: 'csv' }), '/api/v1/model-budgets/usage?group_by=feature&scope=tenant&from=2026-10-01&to=2026-10-31&format=csv');
    assert.equal(csvFileName('tenant', 'feature', range), 'model-usage-tenant-feature-2026-10-01-2026-10-31.csv');
  });
});

describe('labels', () => {
  test('days, months, people, agents and features read as words', () => {
    assert.equal(rowLabel('day', row()), 'Oct 3, 2026');
    assert.equal(rowLabel('month', row({ key: '2026-10-01' })), 'October 2026');
    assert.equal(rowLabel('user', row({ key: 'user_a' }), 'user_a'), 'user_a (you)');
    assert.equal(rowLabel('user', row({ key: 'user_b' }), 'user_a'), 'user_b');
    assert.equal(rowLabel('agent', row({ key: 'agent_1', label: 'Outreach' })), 'Outreach');
    assert.equal(rowLabel('agent', row({ key: 'agent_1' })), 'agent_1');
    assert.equal(rowLabel('feature', row({ key: 'tasks' })), 'Tasks and agent runs');
    assert.equal(rowLabel('feature', row({ key: 'custom_thing' })), 'custom_thing');
    assert.equal(tickLabel('day', '2026-10-03'), '3');
    assert.equal(tickLabel('month', '2026-10-01'), 'Oct');
    assert.equal(tickLabel('feature', 'voice'), 'voice');
    // A key that is not a date never fails the page.
    assert.equal(rowLabel('month', row({ key: 'user_local' })), 'user_local');
    assert.equal(tickLabel('day', 'user_local'), 'user_local');
  });
  test('Arabic feature names and the you marker', () => {
    setLocale('ar');
    assert.equal(rowLabel('feature', row({ key: 'voice' })), 'الصوت');
    assert.match(rowLabel('user', row({ key: 'user_a' }), 'user_a'), /user_a.*\(أنت\)/);
  });
});

describe('measures and the chart scale', () => {
  test('cost or tokens, shares of the total, bar lengths of the largest', () => {
    const rows = [row({ key: 'a', estimated_cost_usd: 4, input_tokens: 100, output_tokens: 0 }), row({ key: 'b', estimated_cost_usd: 1, input_tokens: 20, output_tokens: 30 })];
    const total = { input_tokens: 120, output_tokens: 30, estimated_cost_usd: 5, calls: 4, unpriced_calls: 0 };
    assert.equal(measureOf(rows[0], 'cost'), 4);
    assert.equal(measureOf(rows[1], 'tokens'), 50);
    assert.deepEqual(rows.map((r) => shareOf(r, total, 'cost')), [80, 20]);
    assert.deepEqual(rows.map((r) => shareOf(r, total, 'tokens')), [67, 33]);
    assert.deepEqual(barLengths(rows, 'cost'), [100, 25]);
    assert.deepEqual(barLengths(rows, 'tokens'), [100, 50]);
    assert.deepEqual(barLengths([row({ estimated_cost_usd: 0 })], 'cost'), [0]);
    assert.equal(formatMeasure(1.5, 'cost'), '$1.50');
    assert.equal(formatMeasure(1500, 'tokens'), '1,500');
  });
  test('ticks are round steps that cover the maximum', () => {
    assert.deepEqual(ticks(0), [0, 1]);
    assert.deepEqual(ticks(7), [0, 2, 4, 6, 8]);
    assert.deepEqual(ticks(0.37), [0, 0.1, 0.2, 0.3, 0.4]);
    assert.deepEqual(ticks(1000), [0, 250, 500, 750, 1000]);
    assert.equal(isTimeSeries('day'), true);
    assert.equal(isTimeSeries('agent'), false);
  });
});

describe('page counts', () => {
  test('the shown-of-total lines pick the plural from the total, in both languages', () => {
    assert.equal(t('memory.showing', { shown: '2', count: 2 }), '2 of 2 memories shown.');
    assert.equal(t('budgets.usage.showing', { shown: '1', count: 3 }), 'Showing 1 of 3 groups.');
    setLocale('ar');
    assert.equal(t('memory.showing', { shown: '2', count: 2 }), 'تُعرض 2 من ذكريتين.');
    assert.equal(t('memory.showing', { shown: '50', count: 120 }), 'تُعرض 50 من 120 ذكرى.');
  });
});
