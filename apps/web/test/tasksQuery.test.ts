// Unit tests for the Tasks view's server query, permissions, bulk, run history and activity helpers.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AgentRun, Task, TaskBulkResult, TaskSummary } from '@anum/contracts';
import {
  activeFilterCount, bulkMessage, dateRangeError, dayBoundary, EMPTY_QUERY, eventLabel, eventTone, isDefaultQuery, isStranded, mergeById,
  pruneSelection, roleAllows, roleFrom, runLabel, selectRun, statusesFor, stepFacts, summaryCounts, taskActions, taskQueryString, taskTitleFrom,
} from '../src/lib/tasks.ts';

const task = (id: string, status: Task['status'], extra: Partial<Task> = {}): Task => ({
  id, title: id, prompt: '', status, tenantId: 't', workspaceId: 'w', createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z', ...extra,
});
const run = (id: string, taskId: string, createdAt: string, extra: Partial<AgentRun> = {}): AgentRun => ({ id, taskId, status: 'completed', steps: [], createdAt, ...extra });

describe('taskQueryString', () => {
  test('the default query only pages', () => {
    assert.equal(taskQueryString(EMPTY_QUERY, { limit: 50 }), 'limit=50');
    assert.equal(isDefaultQuery(EMPTY_QUERY), true);
    assert.equal(activeFilterCount(EMPTY_QUERY), 0);
  });
  test('status groups, search, creator, archived and cursor', () => {
    const qs = new URLSearchParams(taskQueryString({ ...EMPTY_QUERY, status: 'active', q: '  launch  ', creator: 'me', archived: true }, { limit: 20, cursor: 'task_9' }));
    assert.deepEqual(qs.getAll('status'), ['created', 'queued', 'running', 'waiting_approval']);
    assert.equal(qs.get('q'), 'launch');
    assert.equal(qs.get('created_by'), 'me');
    assert.equal(qs.get('archived'), 'true');
    assert.equal(qs.get('cursor'), 'task_9');
    assert.equal(qs.get('limit'), '20');
  });
  test('the summary leaves the status out', () => {
    const qs = new URLSearchParams(taskQueryString({ ...EMPTY_QUERY, status: 'failed', q: 'x' }, { withStatus: false }));
    assert.equal(qs.has('status'), false);
    assert.equal(qs.get('q'), 'x');
  });
  test('a date range covers both days in local time', () => {
    const qs = new URLSearchParams(taskQueryString({ ...EMPTY_QUERY, from: '2026-10-01', to: '2026-10-03' }));
    assert.equal(qs.get('created_after'), new Date(2026, 9, 1).toISOString());
    assert.equal(qs.get('created_before'), new Date(2026, 9, 4).toISOString());
    assert.equal(activeFilterCount({ ...EMPTY_QUERY, from: '2026-10-01', to: '2026-10-03', status: 'failed' }), 2);
  });
  test('date helpers', () => {
    assert.equal(dayBoundary('not-a-date'), null);
    assert.equal(dayBoundary('2026-12-31', true), new Date(2027, 0, 1).toISOString());
    assert.equal(dateRangeError('2026-10-03', '2026-10-01'), 'The "From" date must be on or before the "To" date.');
    assert.equal(dateRangeError('2026-10-01', '2026-10-01'), null);
    assert.deepEqual(statusesFor('all'), []);
    assert.deepEqual(statusesFor('failed'), ['failed']);
  });
});

test('summaryCounts uses the server summary, else the loaded tasks', () => {
  const summary: TaskSummary = { total: 9, byStatus: { created: 1, running: 2, waiting_approval: 1, completed: 3, failed: 1, cancelled: 1 }, archived: 4, creators: [] };
  assert.deepEqual(summaryCounts(summary, []), { active: 4, waiting: 1, completed: 3, failed: 2, total: 9 });
  assert.deepEqual(summaryCounts(null, [task('a', 'waiting_approval'), task('b', 'failed')]), { active: 1, waiting: 1, completed: 0, failed: 1, total: 2 });
});

describe('permissions and actions', () => {
  test('roles allow what the API allows', () => {
    const mine = task('a', 'completed', { createdBy: 'me' });
    assert.equal(roleAllows('viewer', 'run', mine, 'me'), false);
    assert.equal(roleAllows('member', 'run', mine, 'me'), true);
    assert.equal(roleAllows('member', 'cancel', mine, 'me'), false);
    assert.equal(roleAllows('owner', 'cancel', mine, 'me'), true);
    assert.equal(roleAllows('member', 'manage', mine, 'me'), true);
    assert.equal(roleAllows('member', 'manage', mine, 'someone'), false);
    assert.equal(roleAllows('owner', 'manage', mine, 'someone'), true);
    assert.equal(roleAllows(null, 'cancel', mine, null), true);
    assert.equal(roleFrom({ role: 'member', active: true }), 'member');
    assert.equal(roleFrom({ role: 'member', active: false }), null);
    assert.equal(roleFrom(null), null);
  });
  test('state decides which actions exist', () => {
    const created = taskActions(task('a', 'created'), null);
    assert.equal(created.run && created.editInstructions && created.archive && created.cancel, true);
    assert.equal(created.retry, false);
    const failed = taskActions(task('a', 'failed'), run('r', 'a', '2026-10-01', { status: 'failed' }));
    assert.equal(failed.retry && failed.archive && failed.edit, true);
    assert.equal(failed.run || failed.cancel || failed.editInstructions, false);
    const waiting = taskActions(task('a', 'waiting_approval'), run('r', 'a', '2026-10-01', { status: 'waiting_approval' }));
    assert.equal(waiting.cancel, true);
    assert.equal(waiting.archive || waiting.retry, false);
    const archived = taskActions(task('a', 'completed', { archivedAt: '2026-10-02T00:00:00Z' }), null);
    assert.deepEqual(Object.entries(archived).filter(([, on]) => on).map(([name]) => name), ['unarchive']);
  });
  test('a run stranded at tool_ready can be resumed', () => {
    assert.equal(isStranded(run('r', 'a', '', { status: 'running', phase: 'tool_ready' })), true);
    assert.equal(isStranded(run('r', 'a', '', { status: 'running', phase: 'executing' })), false);
    assert.equal(isStranded(null), false);
  });
});

test('bulkMessage names the first refusal', () => {
  const ok: TaskBulkResult = { action: 'archive', succeeded: 2, failed: 0, results: [] };
  assert.equal(bulkMessage(ok), 'Archived 2 tasks.');
  const mixed: TaskBulkResult = { action: 'cancel', succeeded: 1, failed: 1, results: [
    { taskId: 'a', ok: true, statusCode: 200, message: null, task: null },
    { taskId: 'b', ok: false, statusCode: 409, message: 'Task cannot be cancelled', task: null },
  ] };
  assert.equal(bulkMessage(mixed), 'Cancelled 1 task; could not cancel 1 task: Task cannot be cancelled');
  assert.equal(bulkMessage({ ...mixed, action: 'unarchive', succeeded: 0 }), 'could not restore 1 task: Task cannot be cancelled');
  assert.deepEqual([...pruneSelection(new Set(['a', 'gone']), [task('a', 'created')])], ['a']);
});

describe('run history', () => {
  const runs = [run('r3', 't', '2026-10-03'), run('r2', 't', '2026-10-02'), run('r1', 't', '2026-10-01')];
  test('the chosen run, else the newest', () => {
    assert.equal(selectRun(runs, 'r2', 't', null)?.id, 'r2');
    assert.equal(selectRun(runs, null, 't', null)?.id, 'r3');
    assert.equal(selectRun(runs, 'missing', 't', null)?.id, 'r3');
    assert.equal(selectRun(runs, null, undefined, null), null);
  });
  test('a run the page just started shows before the list has it', () => {
    assert.equal(selectRun(runs, null, 't', run('r4', 't', '2026-10-04'))?.id, 'r4');
    assert.equal(selectRun([], null, 't', run('r4', 't', '2026-10-04'))?.id, 'r4');
    assert.equal(selectRun(runs, null, 't', run('r3', 't', '2026-10-03', { status: 'running' }))?.status, 'completed');
    assert.equal(selectRun(runs, null, 't', run('rx', 'other', '2026-10-09'))?.id, 'r3');
  });
  test('labels number runs from the oldest', () => {
    assert.equal(runLabel(0, 3), 'Run 3 (latest)');
    assert.equal(runLabel(2, 3), 'Run 1');
    assert.deepEqual(mergeById([{ id: 'a', v: 2 }], [{ id: 'a', v: 1 }, { id: 'b', v: 1 }]), [{ id: 'a', v: 2 }, { id: 'b', v: 1 }]);
  });
  test('step facts are readable text only', () => {
    const facts = stepFacts({ id: 's', type: 'tool_proposal', summary: '', createdAt: '', metadata: {
      tool: 'external.action', risk_level: 'high', policy_outcome: 'require_approval', approval_id: 'approval_1',
      usage: { input_tokens: 12, output_tokens: 3 }, governance_rules: ['rule_a'], nested: { secret: 'x' },
    } });
    assert.deepEqual(facts, [
      { label: 'Tool', value: 'external.action' }, { label: 'Risk', value: 'high' }, { label: 'Policy', value: 'require approval' },
      { label: 'Approval', value: 'approval_1' }, { label: 'Tokens', value: '12 in / 3 out' }, { label: 'Rules', value: 'rule_a' },
    ]);
    assert.deepEqual(stepFacts({ id: 's', type: 'final', summary: '', createdAt: '' }), []);
  });
});

test('event labels and tones', () => {
  assert.equal(eventLabel({ type: 'task.retried' }), 'Retry requested');
  assert.equal(eventLabel({ type: 'custom.thing_done' }), 'custom: thing done');
  assert.equal(eventTone({ type: 'agent_run.completed' }), 'success');
  assert.equal(eventTone({ type: 'approval.rejected' }), 'dangerTone');
  assert.equal(eventTone({ type: 'approval.requested' }), 'warning');
  assert.equal(eventTone({ type: 'task.created' }), 'neutral');
});

test('taskTitleFrom takes the first line, shortened', () => {
  assert.equal(taskTitleFrom('  Summarise the launch notes\nwith detail'), 'Summarise the launch notes');
  assert.equal(taskTitleFrom('   '), 'Web task');
  const long = taskTitleFrom('x'.repeat(200));
  assert.equal(long.length, 80);
  assert.ok(long.endsWith('…'));
});
