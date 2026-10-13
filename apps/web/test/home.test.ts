// Unit tests for Home's pure rules (src/lib/home.ts). docs/home.md#the-web-home.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { Approval, ModelBudgetOverview, Task } from '@anum/contracts';
import {
  FEATURE_VIEWS, PINNABLE_FEATURES, QUICK_LINKS, budgetUse, dayPart, greetingName, movePin, page, pendingApprovals, sourceState, taskGlance, tasksNeedingAttention,
  todayRange, todaysMeetings, validLink, viewForFeature,
} from '../src/lib/home.ts';
import { VIEWS } from '../src/lib/shell.ts';

const task = (id: string, status: Task['status'], updatedAt = '2026-10-12T09:00:00Z', extra: Partial<Task> = {}): Task => ({
  id, title: id, prompt: id, status, tenantId: 't', workspaceId: 'w', createdAt: updatedAt, updatedAt, ...extra,
});

describe('greeting', () => {
  test('day part follows the local hour', () => {
    assert.equal(dayPart(new Date(2026, 9, 12, 8)), 'morning');
    assert.equal(dayPart(new Date(2026, 9, 12, 12)), 'afternoon');
    assert.equal(dayPart(new Date(2026, 9, 12, 19)), 'evening');
  });
  test('the Home name wins, else the sign-in name, first word only; none greets without a name', () => {
    assert.equal(greetingName('Sara Khan', 'user_local'), 'Sara');
    assert.equal(greetingName('  ', 'Omar Ali'), 'Omar');
    assert.equal(greetingName(null, null), null);
  });
});

describe('what opens where', () => {
  test('every feature key opens a real web view, and pins never offer Home itself', () => {
    for (const view of Object.values(FEATURE_VIEWS)) assert.ok(VIEWS.includes(view), view);
    assert.equal(viewForFeature('email'), 'inbox');
    assert.equal(viewForFeature('calendar'), 'calendar');
    assert.equal(viewForFeature('unknown'), null);
    assert.ok(!PINNABLE_FEATURES.includes('insights'));
  });
  test('quick links are real views and Home does not link to itself', () => {
    for (const view of QUICK_LINKS) assert.ok(VIEWS.includes(view), view);
    assert.ok(!QUICK_LINKS.includes('home'));
    assert.ok(QUICK_LINKS.includes('command'));
  });
});

describe('today', () => {
  test('the calendar range is the local day', () => {
    const { start, end } = todayRange(new Date(2026, 9, 12, 15, 30));
    assert.equal(new Date(start).getTime(), new Date(2026, 9, 12).getTime());
    assert.equal(new Date(end).getTime(), new Date(2026, 9, 13).getTime());
  });
  test('meetings: cancelled left out, upcoming first, ended last', () => {
    const now = new Date('2026-10-12T12:00:00Z');
    const event = (id: string, start: string, end: string, status = 'scheduled') => ({ id, title: id, starts_at: start, ends_at: end, status, kind: 'meeting' });
    const order = todaysMeetings([
      event('ended', '2026-10-12T08:00:00Z', '2026-10-12T09:00:00Z'),
      event('later', '2026-10-12T15:00:00Z', '2026-10-12T16:00:00Z'),
      event('now', '2026-10-12T11:30:00Z', '2026-10-12T12:30:00Z'),
      event('gone', '2026-10-12T13:00:00Z', '2026-10-12T14:00:00Z', 'cancelled'),
    ], now).map((e) => e.id);
    assert.deepEqual(order, ['now', 'later', 'ended']);
  });
  test('tasks at a glance and the ones needing attention, archived left out', () => {
    const tasks = [task('a', 'running'), task('b', 'failed'), task('c', 'waiting_approval'), task('d', 'queued'), task('e', 'created'), task('f', 'completed'), task('g', 'failed', undefined, { archivedAt: '2026-10-12T10:00:00Z' })];
    assert.deepEqual(taskGlance(tasks), { running: 1, waiting: 1, failed: 1, queued: 2 });
    assert.deepEqual(tasksNeedingAttention(tasks).map((t) => t.id), ['b', 'c', 'a', 'd', 'e']);
  });
  test('pending approvals: oldest first, expired and decided left out', () => {
    const base = { taskId: 't', action: 'x', riskLevel: 'high', reason: '' } as const;
    const approvals = [
      { ...base, id: 'new', status: 'pending', createdAt: '2026-10-12T10:00:00Z' },
      { ...base, id: 'old', status: 'pending', createdAt: '2026-10-12T08:00:00Z' },
      { ...base, id: 'lapsed', status: 'pending', createdAt: '2026-10-12T07:00:00Z', expiresAt: '2026-10-12T09:00:00Z' },
      { ...base, id: 'done', status: 'approved', createdAt: '2026-10-12T06:00:00Z' },
    ] as Approval[];
    assert.deepEqual(pendingApprovals(approvals, new Date('2026-10-12T11:00:00Z')).map((a) => a.id), ['old', 'new']);
  });
});

describe('budget use', () => {
  const usage = (cost: number) => ({ inputTokens: 0, outputTokens: 0, estimatedCostUsd: cost, calls: 1, unpricedCalls: 0 });
  const overview = (workspace: { cost?: number | null; tokens?: number | null } | null, tenant: { cost?: number | null; tokens?: number | null } | null, exceeded = false): ModelBudgetOverview => ({
    periodStart: '2026-10-01', resetsOn: '2026-11-01', canManageTenantBudget: false,
    workspace: { budget: workspace ? { scope: 'workspace', monthlyCostLimitUsd: workspace.cost ?? null, monthlyTokenLimit: workspace.tokens ?? null, updatedAt: '', updatedBy: '' } : null, usage: usage(1.5), totalTokens: 2000, exceeded },
    tenant: { budget: tenant ? { scope: 'tenant', monthlyCostLimitUsd: tenant.cost ?? null, monthlyTokenLimit: tenant.tokens ?? null, updatedAt: '', updatedBy: '' } : null, usage: usage(1.5), totalTokens: 2000, exceeded: false },
  } as ModelBudgetOverview);
  test('the budget closest to its limit is shown as a share', () => {
    assert.deepEqual(budgetUse(overview({ cost: 2, tokens: 4000 }, { cost: 10 })), { percent: 75, costUsd: 1.5, tokens: 2000, limitLabel: 'cost', exceeded: false, scope: 'workspace' });
    assert.equal(budgetUse(overview({ cost: 10 }, { tokens: 2500 })).scope, 'tenant');
    assert.equal(budgetUse(overview({ cost: 10 }, { tokens: 2500 })).percent, 80);
  });
  test('without a limit the share is null and usage still shows; exceeded is carried', () => {
    const use = budgetUse(overview(null, null, true));
    assert.equal(use.percent, null);
    assert.equal(use.costUsd, 1.5);
    assert.equal(use.exceeded, true);
  });
});

describe('lists and pins', () => {
  test('a page shows the first items and counts the rest', () => {
    assert.deepEqual(page([1, 2, 3, 4], 3), { items: [1, 2, 3], more: 1 });
    assert.deepEqual(page([1], 3), { items: [1], more: 0 });
  });
  test('source states come from the summary', () => {
    assert.equal(sourceState([{ name: 'meetings', status: 'not_installed' }], 'meetings'), 'not_installed');
    assert.equal(sourceState([], 'email'), null);
  });
  test('pins move one place and not past either end', () => {
    assert.deepEqual(movePin(['a', 'b', 'c'], 'b', -1), ['b', 'a', 'c']);
    assert.equal(movePin(['a', 'b'], 'a', -1), null);
    assert.equal(movePin(['a', 'b'], 'b', 1), null);
  });
  test('a link pin must be https without credentials', () => {
    assert.ok(validLink('https://tyrepulse.example.com/app'));
    assert.ok(!validLink('http://example.com'));
    assert.ok(!validLink('https://user:pw@example.com'));
    assert.ok(!validLink('not a link'));
  });
});
