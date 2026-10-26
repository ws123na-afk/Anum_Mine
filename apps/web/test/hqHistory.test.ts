// Unit tests for the office history panel rules (src/lib/hqHistory.ts, src/lib/hqRoomHistory.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { OfficeHistoryEvidence, OfficeHistoryPage } from '@anum/contracts';
import {
  EMPTY_FILTERS, EMPTY_STATE, OFFICE_SCOPE, dateWindow, exportFilters, groupByDay, historyQuery, loadMore, parseHistoryPage,
  relatesTo, sourceRefs, type HistoryScope,
} from '../src/lib/hqHistory.ts';
import { buildRoomTimeline, changeFor, desksAt, stateAt } from '../src/lib/hqRoomHistory.ts';

const T0 = Date.parse('2026-10-20T10:00:00Z');
let n = 0;
function rec(action: string, target: string, minutes: number, metadata: Record<string, unknown> = {}, actor = 'owner_1'): OfficeHistoryEvidence {
  n += 1;
  return {
    id: `audit_${String(n).padStart(3, '0')}`, timestamp: new Date(T0 + minutes * 60_000).toISOString(), actor, action, target,
    category: action.startsWith('office.') ? 'office' : 'task', outcome: 'success', correlation_id: `corr_${n}`, metadata, source: 'workspace_audit', verified: true,
  };
}
const room = (name: string, extra: Record<string, unknown> = {}) => ({ id: 'room_a', revision: 1, floor_id: 'floor_1', name, kind: 'office', capacity: 4, scene_x: 1, scene_z: 2, scene_width: 6, scene_depth: 6, ...extra });
const desk = (agent: string | null, roomId = 'room_a') => ({ id: 'desk_1', revision: 1, room_id: roomId, label: 'Desk 1', agent_id: agent, pos_x: 0, pos_z: 0 });

describe('query building', () => {
  test('filters become server parameters and the date window is inclusive of the last day', () => {
    const result = historyQuery({ ...EMPTY_FILTERS, q: ' rename ', actor: 'owner_1', category: 'office', from: '2026-10-01', to: '2026-10-02' }, OFFICE_SCOPE, 'abc', 30);
    assert.ok(result.ok);
    const url = new URL(`http://x${result.path}`);
    assert.equal(url.pathname, '/api/v1/office-history');
    assert.equal(url.searchParams.get('q'), 'rename');
    assert.equal(url.searchParams.get('actor'), 'owner_1');
    assert.equal(url.searchParams.get('category'), 'office');
    assert.equal(url.searchParams.get('cursor'), 'abc');
    assert.equal(url.searchParams.get('limit'), '30');
    const since = Date.parse(url.searchParams.get('since') as string);
    const until = Date.parse(url.searchParams.get('until') as string);
    assert.equal(until - since, 2 * 24 * 3600_000 - (new Date(2026, 9, 3).getTimezoneOffset() - new Date(2026, 9, 1).getTimezoneOffset()) * 60_000);
  });
  test('all and empty values add nothing; bad or reversed dates are refused before any request', () => {
    const plain = historyQuery(EMPTY_FILTERS, OFFICE_SCOPE, null);
    assert.ok(plain.ok && !plain.path.includes('category') && !plain.path.includes('q='));
    assert.deepEqual(dateWindow({ from: '2026-13-40', to: '' }), { ok: false, error: 'badFrom' });
    assert.deepEqual(dateWindow({ from: '', to: 'nope' }), { ok: false, error: 'badTo' });
    assert.deepEqual(dateWindow({ from: '2026-10-05', to: '2026-10-01' }), { ok: false, error: 'reversed' });
    assert.equal(historyQuery({ ...EMPTY_FILTERS, from: '2026-10-05', to: '2026-10-01' }, OFFICE_SCOPE, null).ok, false);
  });
  test('a correlation scope uses the dedicated route and encodes the id', () => {
    const r = historyQuery(EMPTY_FILTERS, { kind: 'correlation', correlationId: 'a/b c' }, null);
    assert.ok(r.ok && r.path.startsWith('/api/v1/office-history/correlations/a%2Fb%20c?'));
  });
  test('the CSV export filters cover every view; room, agent and Other are narrowed afterwards', () => {
    assert.deepEqual(exportFilters({ ...EMPTY_FILTERS, category: 'office', q: 'x' }, OFFICE_SCOPE), { action: 'office.*', q: 'x' });
    assert.deepEqual(exportFilters({ ...EMPTY_FILTERS, category: 'other' }, OFFICE_SCOPE), {});
    assert.deepEqual(exportFilters(EMPTY_FILTERS, { kind: 'agent', agentId: 'a', name: 'A' }), {});
    assert.deepEqual(exportFilters(EMPTY_FILTERS, { kind: 'room', roomId: 'r', label: 'R', deskIds: [] }), {});
    assert.equal(exportFilters({ ...EMPTY_FILTERS, from: 'nope' }, OFFICE_SCOPE), null);
    assert.deepEqual(exportFilters(EMPTY_FILTERS, { kind: 'correlation', correlationId: 'c1' }), { correlationId: 'c1' });
  });
});

describe('paging', () => {
  const page = (items: OfficeHistoryEvidence[], next: string | null): OfficeHistoryPage => ({ items, next_cursor: next, workspace_id: 'ws', category: 'all', evidence_notice: '', historical_coverage_complete: false });
  test('keeps following the cursor over empty pages and stops at the end', async () => {
    const calls: (string | null)[] = [];
    const pages: Record<string, OfficeHistoryPage> = { start: page([], 'c1'), c1: page([], 'c2'), c2: page([rec('task.created', 't1', 1)], null) };
    const state = await loadMore(async (cursor) => { calls.push(cursor); return pages[cursor ?? 'start']; }, EMPTY_STATE, OFFICE_SCOPE, { first: true });
    assert.deepEqual(calls, [null, 'c1', 'c2']);
    assert.equal(state.items.length, 1);
    assert.equal(state.cursor, null);
    assert.equal(state.paused, false);
  });
  test('pauses on the page limit with the cursor kept, and a later call continues without repeats', async () => {
    const all = Array.from({ length: 10 }, (_, i) => rec('task.created', `t${i}`, i));
    const fetcher = async (cursor: string | null) => { const i = cursor ? Number(cursor) : 0; return page([all[i]], i + 1 < all.length ? String(i + 1) : null); };
    let state = await loadMore(fetcher, EMPTY_STATE, OFFICE_SCOPE, { first: true, want: 99, maxPages: 4 });
    assert.equal(state.items.length, 4);
    assert.equal(state.paused, true);
    state = await loadMore(fetcher, state, OFFICE_SCOPE, { want: 99, maxPages: 20 });
    assert.deepEqual(state.items.map((i) => i.target), all.map((i) => i.target));
    assert.equal(state.cursor, null);
  });
  test('an answer for a stale request is dropped', async () => {
    const state = await loadMore(async () => page([rec('task.created', 'x', 1)], null), EMPTY_STATE, OFFICE_SCOPE, { first: true, isCurrent: () => false });
    assert.equal(state.items.length, 0);
  });
  test('a scope keeps only records that name the room, its desks or the agent', async () => {
    const scope: HistoryScope = { kind: 'room', roomId: 'room_a', label: 'A', deskIds: ['desk_1'] };
    const mine = rec('office.room.rename', 'room_a', 1);
    const desks = rec('office.desk.assignment', 'desk_1', 2);
    const other = rec('office.room.rename', 'room_b', 3);
    const nested = rec('office.floor.layout', 'floor_1', 4, { after: { rooms: [room('x')] } });
    const state = await loadMore(async () => page([mine, desks, other, nested], null), EMPTY_STATE, scope, { first: true });
    assert.deepEqual(state.items.map((i) => i.id), [mine.id, desks.id, nested.id]);
    assert.equal(relatesTo(rec('task.created', 't', 1, { agent_id: 'agent_x' }), { kind: 'agent', agentId: 'agent_x', name: 'X' }), true);
    assert.equal(relatesTo(rec('task.created', 't', 1, { agent_id: 'agent_y' }), { kind: 'agent', agentId: 'agent_x', name: 'X' }), false);
  });
  test('a malformed or foreign-workspace answer is refused', () => {
    assert.throws(() => parseHistoryPage({ items: [], next_cursor: null, workspace_id: 'other' } as unknown as OfficeHistoryPage, 'ws'));
    assert.throws(() => parseHistoryPage({ items: [{ id: 1 }], next_cursor: null, workspace_id: 'ws' } as unknown as OfficeHistoryPage, 'ws'));
  });
  test('records group by local day', () => {
    const groups = groupByDay([rec('task.created', 'a', 0), rec('task.created', 'b', 1), rec('task.created', 'c', -60 * 48)], 'en');
    assert.equal(groups.length, 2);
    assert.equal(groups[0].items.length, 2);
  });
});

describe('source references', () => {
  test('actions and recorded ids point at the task, approval, meeting, run and office records', () => {
    assert.deepEqual(sourceRefs(rec('task.updated', 'task_1', 1)).map((r) => `${r.kind}:${r.id}:${r.readable}`), ['task:task_1:true']);
    const approval = sourceRefs(rec('approval.approved', 'approval_9', 1, { task_id: 'task_2' })).map((r) => `${r.kind}:${r.id}`);
    assert.deepEqual(approval, ['approval:approval_9', 'task:task_2']);
    assert.deepEqual(sourceRefs(rec('agent_meeting.create', 'meeting_3', 1)).map((r) => r.kind), ['meeting']);
    assert.deepEqual(sourceRefs(rec('office.room.rename', 'room_a', 1, { resource_kind: 'rooms' })).map((r) => `${r.kind}:${r.readable}`), ['room:false']);
  });
  test('unsafe ids are never turned into references', () => {
    assert.deepEqual(sourceRefs(rec('task.updated', '../etc/passwd', 1)), []);
  });
});

describe('room history scrubber', () => {
  const create = rec('office.room.create', 'room_a', 0, { after: room('Reception') });
  const rename = rec('office.room.rename', 'room_a', 10, { before: room('Reception'), after: room('Front desk') });
  const move = rec('office.room.geometry', 'room_a', 20, { before: room('Front desk'), after: room('Front desk', { scene_x: 9 }) });
  const assign = rec('office.desk.assignment', 'desk_1', 25, { before: desk(null), after: desk('agent_x') });

  test('a change reports only the fields that differ', () => {
    const change = changeFor(rename, 'room_a');
    assert.deepEqual(change?.fields, [{ field: 'name', before: 'Reception', after: 'Front desk' }]);
    assert.equal(changeFor(rename, 'room_b'), null);
    assert.equal(changeFor(rec('task.created', 'room_a', 1), 'room_a'), null);
  });
  test('steps run oldest first from the creating record, with the values of each record', () => {
    const tl = buildRoomTimeline([assign, move, rename, create], 'room_a', false);
    assert.deepEqual(tl.steps.map((s) => s.kind), ['baseline', 'change', 'change', 'change', 'change']);
    assert.equal(tl.steps[0].kind === 'baseline' && tl.steps[0].created, true);
    const last = tl.steps[3];
    assert.equal(last.kind === 'change' && last.state?.scene_x, 9);
    assert.equal(tl.createdAt, Date.parse(create.timestamp));
  });
  test('a break between one record\'s after and the next record\'s before is a gap, never filled in', () => {
    const unexplained = rec('office.room.rename', 'room_a', 40, { before: room('Lobby'), after: room('Atrium') });
    const tl = buildRoomTimeline([create, rename, unexplained], 'room_a', false);
    const gap = tl.steps.find((s) => s.kind === 'gap');
    assert.ok(gap && gap.kind === 'gap');
    assert.deepEqual(gap.fields, ['name']);
    const inside = stateAt(tl, T0 + 30 * 60_000, T0 + 99 * 60_000);
    assert.deepEqual(inside, { status: 'unknown', reason: 'gap', stepIndex: gap.index });
  });
  test('stateAt: known between records, not created before creation, unknown before the oldest loaded record, unknown in the future', () => {
    const tl = buildRoomTimeline([create, rename, move], 'room_a', false);
    const now = T0 + 100 * 60_000;
    const mid = stateAt(tl, T0 + 15 * 60_000, now);
    assert.equal(mid.status, 'known');
    assert.equal(mid.status === 'known' && tl.steps[mid.stepIndex].kind === 'change' && (tl.steps[mid.stepIndex] as { state: { name: string } }).state.name, 'Front desk');
    assert.equal(stateAt(tl, T0 - 60_000, now).status, 'not_created');
    assert.deepEqual(stateAt(tl, T0 + 200 * 60_000, now), { status: 'unknown', reason: 'future', stepIndex: null });
    assert.equal(stateAt(tl, 'not a date', now).status, 'unknown');
    const partial = buildRoomTimeline([rename, move], 'room_a', true);
    assert.deepEqual(stateAt(partial, T0 + 5 * 60_000, now), { status: 'unknown', reason: 'older', stepIndex: 0 });
    const base = partial.steps[0];
    assert.equal(base.kind === 'baseline' && base.olderUnloaded, true);
    assert.equal(base.kind === 'baseline' && (base.state as { name: string }).name, 'Reception');
  });
  test('an empty history has no steps and no state', () => {
    const tl = buildRoomTimeline([rec('task.created', 't', 1)], 'room_a', false);
    assert.equal(tl.steps.length, 0);
    assert.equal(stateAt(tl, T0, T0 + 1).status, 'unknown');
  });
  test('desks appear only with a recorded state, and leave when they move to another room', () => {
    const away = rec('office.desk.update', 'desk_1', 30, { before: desk('agent_x'), after: desk('agent_x', 'room_b') });
    const tl = buildRoomTimeline([create, assign, away], 'room_a', false);
    const idxAssign = tl.steps.findIndex((s) => s.kind === 'change' && s.change.record.id === assign.id);
    assert.deepEqual(desksAt(tl, idxAssign).map((d) => d.state.agent_id), ['agent_x']);
    assert.deepEqual(desksAt(tl, 0).map((d) => d.state.agent_id), [null]);
    assert.deepEqual(desksAt(tl, tl.steps.length - 1), []);
  });
  test('a floor layout record counts only when this room or its desks changed', () => {
    const layout = rec('office.floor.layout', 'floor_1', 50, {
      before: { rooms: [room('A'), { ...room('B'), id: 'room_b' }], desks: [] },
      after: { rooms: [room('A', { scene_x: 3 }), { ...room('B'), id: 'room_b' }], desks: [] },
    });
    assert.equal(changeFor(layout, 'room_a')?.fields[0].field, 'scene_x');
    assert.equal(changeFor(layout, 'room_b'), null);
  });
});

describe('source summaries', () => {
  test('only named fields are read, never prompts, results, arguments or turns', async () => {
    const { summarizeSource } = await import('../src/lib/hqHistoryApi.ts');
    const task = summarizeSource('task', { id: 't1', title: 'Order tyres', prompt: 'secret prompt', status: 'running', created_at: '2026-10-20T10:00:00Z' });
    assert.equal(task.title, 'Order tyres');
    assert.ok(!JSON.stringify(task).includes('secret prompt'));
    const meeting = summarizeSource('meeting', { id: 'm1', title: 'Plan', turns: [{ body: 'private words' }], participants: [{}, {}] });
    assert.ok(!JSON.stringify(meeting).includes('private words'));
    assert.deepEqual(meeting.fields.find((f) => f.label.endsWith('participants'))?.value, '2');
    const approval = summarizeSource('approval', { id: 'a1', action: 'send_email', arguments: { to: 'recipient-marker' }, status: 'pending' });
    assert.deepEqual(approval.fields.map((f) => f.label), ['hqHist.source.field.status']);
    assert.equal(approval.title, 'send_email');
  });
});
