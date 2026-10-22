import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { createCampusClient, CampusApiError, workstationWorkPath } from '../src/lib/officeCampusClient.ts';
import { applyPresenceDelta, parsePresenceFrames, stateFromSnapshot, PresenceApiError } from '../src/lib/officePresenceClient.ts';
import { createExecutiveClient, ExecutiveApiError } from '../src/lib/executiveClient.ts';

const scope = () => ({ tenantId: 't', workspaceId: 'w' });
const item = (key: string, state = 'idle') => ({
  key, agent_id: key, agent_name: key, desk_id: null, state,
  provenance: { kind: 'none', run_id: null, task_id: null, approval_id: null, meeting_id: null, reason: null },
  task: null, last_verified_at: '2026-10-22T10:00:00+00:00', stale: false,
});
const snapshot = (items: unknown[]) => ({ version: 1, workspace_id: 'w', as_of: 'x', cursor: 'evt_1', totals: {}, items });

describe('campus client', () => {
  it('rejects another tenant, repeated workspaces and a scope change', async () => {
    const ok = { version: 1, tenant_id: 't', current_workspace_id: 'w', generated_at: 'x', truncated: false, buildings: [{ workspace_id: 'w' }] };
    assert.equal((await createCampusClient(async <T,>() => ok as T, scope).loadCampus()).buildings.length, 1);
    await assert.rejects(() => createCampusClient(async <T,>() => ({ ...ok, tenant_id: 'other' }) as T, scope).loadCampus(), CampusApiError);
    await assert.rejects(() => createCampusClient(async <T,>() => ({ ...ok, buildings: [{ workspace_id: 'w' }, { workspace_id: 'w' }] }) as T, scope).loadCampus(), CampusApiError);
    let ws = 'w';
    await assert.rejects(() => createCampusClient(async <T,>() => { ws = 'x'; return ok as T; }, () => ({ tenantId: 't', workspaceId: ws })).loadCampus(), CampusApiError);
  });
  it('checks the desk and workspace of a work panel and encodes the path', async () => {
    assert.equal(workstationWorkPath('d1'), '/api/v1/office/workstations/d1/work');
    assert.throws(() => workstationWorkPath('../x'), CampusApiError);
    const work = { version: 1, workspace_id: 'w', desk: { id: 'd1' }, tasks: [] };
    assert.ok(await createCampusClient(async <T,>() => work as T, scope).loadWork('d1'));
    await assert.rejects(() => createCampusClient(async <T,>() => ({ ...work, workspace_id: 'o' }) as T, scope).loadWork('d1'), CampusApiError);
    await assert.rejects(() => createCampusClient(async <T,>() => work as T, scope).loadWork('d2'), CampusApiError);
  });
});

describe('presence reducer', () => {
  it('builds state from a snapshot and applies ordered deltas only', () => {
    const state = stateFromSnapshot(snapshot([item('a'), item('b')]) as never);
    assert.equal(state.totals.idle, 2);
    const next = applyPresenceDelta(state, { version: 1, sequence: 1, as_of: 'y', totals: {} as never, changed: [item('a', 'working')] as never, removed: ['b'] });
    assert.ok(next);
    assert.equal(next.totals.working, 1);
    assert.equal(next.items.has('b'), false);
    assert.equal(applyPresenceDelta(next, { version: 1, sequence: 1, as_of: 'z', totals: {} as never, changed: [], removed: [] }), null, 'repeat');
    assert.equal(applyPresenceDelta(next, { version: 1, sequence: 3, as_of: 'z', totals: {} as never, changed: [], removed: [] }), null, 'gap');
  });
  it('refuses invalid or repeated items', () => {
    assert.throws(() => stateFromSnapshot(snapshot([item('a'), item('a')]) as never), PresenceApiError);
    assert.throws(() => stateFromSnapshot(snapshot([{ ...item('a'), state: 'busy' }]) as never), PresenceApiError);
  });
  it('parses SSE frames and skips comments and broken data', () => {
    const text = `id: e1\nevent: snapshot\ndata: ${JSON.stringify({ ...snapshot([]), sequence: 0 })}\n\n: keep-alive\n\nevent: delta\ndata: {broken\n\nid: e2\nevent: delta\ndata: ${JSON.stringify({ version: 1, sequence: 1, changed: [], removed: [] })}\n\n`;
    const frames = parsePresenceFrames(text);
    assert.deepEqual(frames.map((f) => [f.event, f.id]), [['snapshot', 'e1'], ['delta', 'e2']]);
  });
});

describe('executive client', () => {
  const overview = (workspaces: unknown[]) => ({ version: 1, tenant_id: 't', home_workspace_id: 'w', generated_at: 'x', truncated: false, workspaces });
  it('accepts a normal overview and refuses a leak for an ungranted workspace', async () => {
    const noop = async () => undefined;
    const good = overview([{ workspace_id: 'a', access: 'granted', summary: {} }, { workspace_id: 'b', access: 'not_granted', reason: 'no_grant' }]);
    assert.ok(await createExecutiveClient(async <T,>() => good as T, noop, scope).overview());
    const leak = overview([{ workspace_id: 'b', access: 'not_granted', reason: 'no_grant', summary: { counts: {} } }]);
    await assert.rejects(() => createExecutiveClient(async <T,>() => leak as T, noop, scope).overview(), ExecutiveApiError);
  });
  it('requires the shown hash to approve and validates ids', async () => {
    const calls: string[] = [];
    const client = createExecutiveClient(async <T,>(p: string) => { calls.push(p); return {} as T; }, async (p) => { calls.push(p); }, scope);
    assert.throws(() => client.approveGrant('g1', { payload_hash: 'nope' }), ExecutiveApiError);
    await client.approveGrant('g1', { payload_hash: 'a'.repeat(64) });
    await client.revokeGrant('g1');
    assert.deepEqual(calls, ['/api/v1/executive/grants/g1/approve', '/api/v1/executive/grants/g1']);
    assert.throws(() => client.revokeGrant('../x'), ExecutiveApiError);
  });
});
