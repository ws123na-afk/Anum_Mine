// Flow rules in src/lib/officeFlow.ts: movements come only from two consecutive server answers.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { OfficeFlowItem, OfficeFlowSnapshot, OfficeFlowStuck } from '@anum/contracts';
import { setLocale } from '../src/lib/i18n.ts';
import {
  FlowApiError, actionHash, ageText, createFlowClient, diffFlow, isFlowEvent, parseFlowSnapshot, reasonText,
} from '../src/lib/officeFlow.ts';
import { projectToStage, roomBadgePoint } from '../src/lib/hqFlowProject.ts';
import { initialCamera } from '../src/lib/headquartersSpatial.ts';

afterEach(() => setLocale('en'));

const loc = (room: string) => ({ building_id: 'b', floor_id: 'f', room_id: room, room_name: `Room ${room}`, desk_id: 'd', agent_id: 'a', agent_name: 'Ada' });
function item(key: string, patch: Partial<OfficeFlowItem> = {}): OfficeFlowItem {
  return { key, kind: 'task', id: key.split(':')[1] ?? key, title: `Title ${key}`, stage: 'in_office', status: 'running', since: '2026-10-25T10:00:00Z',
    created_at: '2026-10-25T09:00:00Z', location: loc('r1'), location_reason: null, stuck: null, waiting: null, ...patch };
}
function stuck(code: OfficeFlowStuck['code'], extra: Partial<OfficeFlowStuck> = {}): OfficeFlowStuck {
  return { code, severity: 'warning', since: '2026-10-25T09:00:00Z', age_seconds: 5400, threshold_seconds: 1800, facts: {}, action: 'open_task', ...extra };
}
function snap(items: OfficeFlowItem[]): OfficeFlowSnapshot {
  return { version: 1, workspace_id: 'w', as_of: '2026-10-25T12:00:00Z', cursor: null, thresholds: {},
    sources: { tasks: 'connected', approvals: 'connected', leads: 'connected', orders: 'connected' },
    items, rooms: {}, truncated: false,
    totals: { items: items.length, by_stage: { arrived: 0, in_office: 0, waiting_human: 0, blocked: 0, done: 0 }, by_kind: {}, waiting_human: 0, stuck: 0, stuck_by_code: {}, critical: 0, unlocated: 0 } };
}

describe('diffFlow', () => {
  test('the first answer produces no movement: nothing is replayed from the past', () => {
    assert.deepEqual(diffFlow(null, snap([item('task:t1')])), []);
  });
  test('an identical answer produces no movement, however often it is read', () => {
    const a = snap([item('task:t1'), item('lead:l1', { kind: 'lead', location: null })]);
    assert.deepEqual(diffFlow(a, snap(a.items)), []);
  });
  test('a new item is an arrival at the office its record names, or nowhere', () => {
    const changes = diffFlow(snap([]), snap([item('task:t1'), item('lead:l1', { kind: 'lead', location: null, stage: 'arrived' })]));
    assert.deepEqual(changes.map((c) => [c.kind, c.key, c.toRoomId]), [['arrived', 'task:t1', 'r1'], ['arrived', 'lead:l1', null]]);
  });
  test('an item that is already finished when first seen is not an arrival', () => {
    assert.deepEqual(diffFlow(snap([]), snap([item('task:t1', { stage: 'done' })])), []);
  });
  test('a changed room is a move from the old office to the new one', () => {
    const [change] = diffFlow(snap([item('task:t1')]), snap([item('task:t1', { location: loc('r2') })]));
    assert.equal(change.kind, 'moved');
    assert.equal(change.fromRoomId, 'r1');
    assert.equal(change.toRoomId, 'r2');
  });
  test('an unlocated lead that gets an owner moves from nowhere into an office', () => {
    const [change] = diffFlow(snap([item('lead:l1', { kind: 'lead', location: null })]), snap([item('lead:l1', { kind: 'lead' })]));
    assert.deepEqual([change.kind, change.fromRoomId, change.toRoomId], ['moved', null, 'r1']);
  });
  test('becoming stuck is reported once, and a changed reason is reported again', () => {
    const before = snap([item('task:t1')]);
    const stuckNow = snap([item('task:t1', { stuck: stuck('queued_too_long') })]);
    assert.deepEqual(diffFlow(before, stuckNow).map((c) => c.kind), ['stuck']);
    assert.deepEqual(diffFlow(stuckNow, snap([item('task:t1', { stuck: stuck('queued_too_long', { age_seconds: 9000 }) })])), []);
    assert.deepEqual(diffFlow(stuckNow, snap([item('task:t1', { stuck: stuck('agent_paused') })])).map((c) => c.kind), ['stuck']);
  });
  test('finishing is reported once', () => {
    const [change] = diffFlow(snap([item('task:t1')]), snap([item('task:t1', { stage: 'done' })]));
    assert.equal(change.kind, 'done');
  });
});

describe('words', () => {
  test('reasons are built from the rule facts in English and Arabic', () => {
    const failed = item('task:t1', { stuck: stuck('run_failed', { severity: 'critical', facts: { error: 'boom' } }) });
    assert.equal(reasonText(failed), 'The last run failed. boom');
    const queued = item('task:t2', { stuck: stuck('queued_too_long') });
    assert.equal(reasonText(queued), 'Queued for 90 min and not started. The limit is 30 min.');
    setLocale('ar');
    assert.match(reasonText(queued), /90/);
    assert.notEqual(reasonText(queued), 'Queued for 90 min and not started. The limit is 30 min.');
  });
  test('an item that is not stuck has no reason', () => assert.equal(reasonText(item('task:t1')), ''));
  test('ages read in minutes, hours and days', () => {
    assert.equal(ageText(20), 'under a minute');
    assert.equal(ageText(600), '10 min');
    assert.equal(ageText(3 * 3600 + 5), '3 h');
    assert.equal(ageText(5 * 86400), '5 d');
  });
  test('every next step opens an existing view', () => {
    for (const a of ['decide_approval', 'retry_task', 'open_task', 'resume_agent', 'assign_owner', 'open_lead', 'open_order'] as const) {
      assert.match(actionHash(a), /^#[a-z]+$/);
    }
  });
});

describe('validation and client', () => {
  test('the stream only hints at a re-read for flow related events', () => {
    assert.ok(isFlowEvent('task.queued') && isFlowEvent('approval.requested') && isFlowEvent('office.desk_assigned'));
    assert.ok(!isFlowEvent('notification.created') && !isFlowEvent('email.received'));
  });
  test('a response that is not version 1, repeats a key or has a bad stage is refused', () => {
    assert.throws(() => parseFlowSnapshot({ ...snap([]), version: 2 } as never), FlowApiError);
    assert.throws(() => parseFlowSnapshot(snap([item('task:t1'), item('task:t1')])), FlowApiError);
    assert.throws(() => parseFlowSnapshot(snap([item('task:t1', { stage: 'flying' as never })])), FlowApiError);
  });
  test('an answer for another workspace is refused', async () => {
    const client = createFlowClient(async () => ({ ...snap([]), workspace_id: 'other' }) as never, () => ({ tenantId: 't', workspaceId: 'w' }));
    await assert.rejects(client.load(), /another workspace/);
  });
  test('team and trail reject malformed ids before any request', async () => {
    let calls = 0;
    const client = createFlowClient((async () => { calls++; return {}; }) as never, () => ({ tenantId: 't', workspaceId: 'w' }));
    await assert.rejects(client.team({ roomId: '../x' }), FlowApiError);
    await assert.rejects(client.trail('task', 'a/b'), FlowApiError);
    assert.equal(calls, 0);
  });
});

describe('stage projection', () => {
  const room = { id: 'r1', floorId: 'f', buildingId: 'b', label: 'R', kind: 'office' as const, x: 0, z: 0, width: 6, depth: 6, height: 3.2, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' as const };
  const camera = initialCamera({ id: 'f', name: 'F', level: 0, rooms: [room], desks: [], bounds: { width: 20, depth: 16 } });
  test('a room in front of the camera lands inside the stage box, behind it returns nothing', () => {
    const p = projectToStage(camera, roomBadgePoint(room), 1.6);
    assert.ok(p && p.left > 0 && p.left < 100 && p.top > 0 && p.top < 100);
    assert.equal(projectToStage(camera, { x: 0, y: 0, z: 100000 }, 1.6), null);
  });
});
