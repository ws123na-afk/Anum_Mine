import assert from 'node:assert/strict';
import { test } from 'node:test';
import { layoutLabels } from '../src/lib/hqLabels.ts';
import { viewProjection } from '../src/lib/hqMath.ts';
import { campusCamera } from '../src/lib/hqCamera.ts';
import { agentPresence, indexPresence, loadMeetingEvidence, presenceForDesk, presenceFromItems, totalsOf, unreportedStatuses } from '../src/lib/hqPresence.ts';
import { commerceFlow, commerceLive, moduleErrorState, tyreLive } from '../src/lib/hqModules.ts';

const vp = viewProjection(campusCamera(60), 1.6);
const size = () => ({ w: 160, h: 44 });

test('labels that would overlap are pushed apart and keep their order', () => {
  const same = { x: 0, y: 10, z: 0 };
  const out = layoutLabels([{ id: 'a', anchor: same }, { id: 'b', anchor: same }, { id: 'c', anchor: same }], vp, 1200, 700, size);
  assert.ok(out.every((p) => p.shown));
  assert.equal(out[0].y > out[1].y, true, 'the second label takes the spot above the first');
  const boxes = out.map((p) => ({ x0: p.x - 80, x1: p.x + 80, y0: p.y - 44, y1: p.y }));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const overlap = boxes[i].x0 < boxes[j].x1 && boxes[i].x1 > boxes[j].x0 && boxes[i].y0 < boxes[j].y1 - 2 && boxes[i].y1 > boxes[j].y0 + 2;
    assert.ok(!overlap);
  }
});

test('a bottom inset keeps every card clear of the toolbar along the bottom edge', () => {
  const low = { x: 40, y: 0, z: 90 };
  const free = layoutLabels([{ id: 'a', anchor: low }], vp, 1200, 700, size);
  const inset = layoutLabels([{ id: 'a', anchor: low }], vp, 1200, 700, size, 64);
  if (free[0].shown) assert.ok(inset[0].y <= 700 - 64 || !inset[0].shown, 'with an inset the card stays above the bottom 64 px');
  const many = layoutLabels(Array.from({ length: 6 }, (_, i) => ({ id: 'l' + i, anchor: { x: i * 6 - 15, y: 0, z: 60 + i * 5 } })), vp, 1200, 700, size, 64);
  for (const p of many) if (p.shown) assert.ok(p.y <= 700 - 64 + 0.001, 'card ' + p.id + ' at ' + p.y);
});

test('priority decides who keeps the spot, and far or off-screen labels are hidden', () => {
  const at = { x: 0, y: 10, z: 0 };
  const out = layoutLabels([{ id: 'low', anchor: at, priority: 2 }, { id: 'top', anchor: at, priority: 0 }], vp, 1200, 700, size);
  assert.ok(out[1].y > out[0].y, 'the top-priority label stays at the anchor');
  const far = layoutLabels([{ id: 'far', anchor: at, maxDistance: 1 }, { id: 'gone', anchor: { x: 9000, y: 0, z: 9000 } }], vp, 1200, 700, size);
  assert.deepEqual(far.map((p) => p.shown), [false, false]);
});

const rec = (agentId: string, status: 'working' | 'queued' | 'waiting_approval' | 'blocked' | 'paused' | 'idle' | 'unknown', department: string | null = null) =>
  ({ agentId, name: agentId, department, suggestedDepartment: department ?? 'Unassigned', status, taskTitles: [], observedAt: '2026-10-22T10:00:00Z' });

test('agent presence maps task evidence and meeting participation without inventing states', () => {
  const meetings = { liveMeetings: 1, agentIds: new Set(['b', 'c']) };
  const out = agentPresence([rec('a', 'working', 'Fleet'), rec('b', 'idle'), rec('c', 'paused'), rec('d', 'queued'), rec('e', 'unknown'), rec('f', 'blocked')], meetings);
  assert.deepEqual(out.map((p) => p.status), ['working', 'meeting', 'paused', 'queued', 'unknown', 'blocked']);
  assert.equal(out[0].role, 'Fleet');
  assert.equal(out[1].role, null);
  const totals = totalsOf(out);
  assert.equal(totals.agents, 6);
  assert.equal(totals.meeting, 1);
  assert.deepEqual(unreportedStatuses(meetings), []);
  assert.deepEqual(unreportedStatuses(null), ['meeting']);
  assert.equal(agentPresence([rec('b', 'idle')], null)[0].status, 'idle');
});

test('meeting evidence reads active meetings only, and any failure means unknown', async () => {
  const read = async <T>(path: string): Promise<T> => {
    if (path.startsWith('/api/v1/agent-meetings?')) return { items: [{ id: 'm1', status: 'active' }, { id: 'm2', status: 'finished' }], next_cursor: null } as T;
    if (path.endsWith('/m1')) return { participants: [{ actor_id: 'agent_a', kind: 'agent' }, { actor_id: 'user_1', kind: 'human' }] } as T;
    throw new Error('unexpected ' + path);
  };
  const evidence = await loadMeetingEvidence(read);
  assert.equal(evidence?.liveMeetings, 1);
  assert.deepEqual([...evidence!.agentIds], ['agent_a']);
  assert.equal(await loadMeetingEvidence(async () => { throw new Error('down'); }), null);
  assert.equal(await loadMeetingEvidence(async <T>() => ({ items: [], next_cursor: 'more' }) as T), null);
});

test('module panels map real dashboard fields and refuse to invent a state', () => {
  const live = commerceLive({ by_channel: [{ orders: 12 }, { orders: 5 }], pending_total: 7, late_total: 2, returns: { count: 3 }, open_alerts: 1, currency: 'AED' });
  assert.deepEqual(commerceFlow(live).map((s) => [s.key, s.count]), [['received', 17], ['waiting', 7], ['late', 2], ['returned', 3]]);
  const tyre = tyreLive({ summary: { vehicles: 42, tyres_fitted: 250, below_minimum: 3, overdue_inspections: 6, due_next_30_days: 11 }, alert_counts: { critical: 1 } });
  assert.deepEqual([tyre.vehicles, tyre.criticalAlerts, tyre.warningAlerts], [42, 1, 0]);
  assert.equal(moduleErrorState(403, 'x').state, 'forbidden');
  assert.equal(moduleErrorState(500, 'boom').state, 'error');
});

test('presence from the API keeps stale, reason and private desks, and finds a desk either way', () => {
  const at = '2026-10-22T10:00:00Z';
  const list = presenceFromItems([
    { key: 'agent_a', agent_id: 'agent_a', agent_name: 'Alpha', desk_id: 'desk_1', state: 'working', provenance: { reason: null }, task: { id: 't1', title: 'Plan' }, last_verified_at: at, stale: false },
    { key: 'agent_b', agent_id: 'agent_b', agent_name: 'Bravo', desk_id: null, state: 'idle', provenance: { reason: null }, task: null, last_verified_at: at, stale: true },
    { key: 'desk:desk_2', agent_id: null, agent_name: null, desk_id: 'desk_2', state: 'unknown', provenance: { reason: 'agent_not_visible' }, task: null, last_verified_at: at, stale: false },
  ], (id) => (id === 'agent_a' ? 'Fleet' : null));
  assert.deepEqual(list.map((p) => [p.agentId, p.status, p.role, p.stale, p.source]), [['agent_a', 'working', 'Fleet', false, 'presence-api'], ['agent_b', 'idle', null, true, 'presence-api'], ['desk:desk_2', 'unknown', null, false, 'presence-api']]);
  assert.deepEqual(list[0].taskTitles, ['Plan']);
  const index = indexPresence(list);
  assert.equal(presenceForDesk(index, { id: 'desk_1', agentId: 'agent_a' })?.name, 'Alpha');
  assert.equal(presenceForDesk(index, { id: 'desk_2', agentId: null })?.reason, 'agent_not_visible');
  assert.equal(presenceForDesk(index, { id: 'desk_9', agentId: null }), undefined);
  assert.equal(presenceForDesk(null, { id: 'desk_1', agentId: 'agent_a' }), undefined);
});
