import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { OfficeFlowItem, OfficeFlowSnapshot } from '@anum/contracts';
import { commandWall } from '../src/lib/hqCommandWall.ts';

const task = (id: string, stage: OfficeFlowItem['stage'], status: string, extra: Partial<OfficeFlowItem> = {}): OfficeFlowItem => ({
  key: `task:${id}`, kind: 'task', id, title: id, stage, status, since: '2026-10-26T10:00:00Z', created_at: '2026-10-26T10:00:00Z', location: null, location_reason: null, stuck: null, waiting: null, ...extra,
});
const snap = (items: OfficeFlowItem[], sources: Partial<OfficeFlowSnapshot['sources']> = {}, truncated = false): OfficeFlowSnapshot => ({
  version: 1, workspace_id: 'w', as_of: '2026-10-26T10:00:00Z', cursor: null, thresholds: {},
  sources: { tasks: 'connected', approvals: 'connected', leads: 'not_connected', orders: 'not_connected', ...sources },
  items, rooms: {}, totals: { items: items.length } as never, truncated,
});
const tile = (m: ReturnType<typeof commandWall>, id: string) => m.tiles.find((t) => t.id === id)!;

test('numbers come only from stored task records', () => {
  const items = [
    task('a', 'in_office', 'running'),
    task('b', 'blocked', 'failed'),
    task('c', 'waiting_human', 'waiting_approval', { waiting: { code: 'approval_pending', since: 'x', approval_id: 'ap', action: null, expires_at: null } }),
    task('d', 'in_office', 'running', { stuck: { code: 'run_silent', severity: 'warning', since: 'x', age_seconds: 1, threshold_seconds: 1, facts: {}, action: 'open_task' } }),
    task('e', 'done', 'completed'),
    { ...task('f', 'in_office', 'new'), kind: 'lead' as const, key: 'lead:f' },
  ];
  const m = commandWall({ status: 'ready', snapshot: snap(items) }, [{ agent_id: 'x', state: 'working' }, { agent_id: 'y', state: 'idle' }, { agent_id: null, state: 'unknown' }]);
  assert.equal(tile(m, 'openTasks').value, 4);
  assert.equal(tile(m, 'blocked').value, 1);
  assert.equal(tile(m, 'approvals').value, 1);
  assert.equal(tile(m, 'running').value, 1, 'a silent run is not counted as running');
  assert.deepEqual(m.agents.state === 'ready' && [m.agents.total, m.agents.byState.working, m.agents.byState.idle], [2, 1, 1], 'a hidden desk is not an agent');
});

test('a source that is down shows no number', () => {
  const m = commandWall({ status: 'ready', snapshot: snap([task('a', 'in_office', 'running')], { tasks: 'error', approvals: 'no_access' }) }, null);
  assert.deepEqual(m.tiles.map((t) => [t.id, t.state, t.value]), [['openTasks', 'not_connected', null], ['blocked', 'not_connected', null], ['approvals', 'no_access', null], ['running', 'not_connected', null]]);
  assert.equal(m.agents.state, 'not_connected');
});

test('loading, failed and denied reads show states and never zeros', () => {
  assert.equal(tile(commandWall({ status: 'loading', snapshot: null }, null, true), 'openTasks').state, 'loading');
  assert.equal(commandWall({ status: 'loading', snapshot: null }, null, true).agents.state, 'loading');
  assert.equal(tile(commandWall({ status: 'error', snapshot: null }, null), 'openTasks').state, 'not_connected');
  assert.equal(tile(commandWall({ status: 'denied', snapshot: null }, null), 'openTasks').state, 'no_access');
  for (const t of commandWall({ status: 'error', snapshot: null }, null).tiles) assert.equal(t.value, null);
});

test('a truncated read is a lower bound', () => {
  assert.equal(tile(commandWall({ status: 'ready', snapshot: snap([task('a', 'in_office', 'running')], {}, true) }, []), 'openTasks').atLeast, true);
});
