import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  HQ_PENDING_KEY, createDeskClient, createPaClient, groupByKind, needsSwitch, parseUtterance, publishNavigation, readOverview,
  type Requester, type TourStop,
} from '../src/lib/executivePa.ts';

const stop = (room_id: string, room_name: string, building_name = 'HQ'): TourStop => ({
  building_id: 'hq', building_name, floor_id: 'f1', floor_name: 'First', level: 1, room_id, room_name, room_kind: 'office', capacity: 4, desks: 0,
});
const stops = [stop('r_office', 'Office'), stop('r_exec', 'Executive office'), stop('r_fin', 'Finance')];
const ctx = { stops, agents: [{ id: 'agent_sales', name: 'Sales Agent' }] };

test('rooms: the longest name wins and only known rooms are targeted', () => {
  assert.deepEqual(parseUtterance('Take me to the executive office', ctx), { tool: 'navigate', args: { target_kind: 'room', target_id: 'r_exec' } });
  assert.deepEqual(parseUtterance('go to finance please', ctx), { tool: 'navigate', args: { target_kind: 'room', target_id: 'r_fin' } });
  assert.equal(parseUtterance('take me to the vault', ctx), null);
  assert.equal(parseUtterance('   ', ctx), null);
});

test('words that would approve or reject are never understood', () => {
  for (const sentence of ['approve everything', 'approve the pending refund', 'reject approval 12', 'yes confirm it', 'I confirm']) {
    assert.equal(parseUtterance(sentence, ctx), null, sentence);
  }
});

test('reads and writes map to the right tools, writes only as proposals', () => {
  assert.equal(parseUtterance('list blocked work', ctx)?.tool, 'list_blocked_work');
  assert.equal(parseUtterance('give me a summary', ctx)?.tool, 'summarise_workspace');
  assert.deepEqual(parseUtterance('open approvals', ctx), { tool: 'open_panel', args: { panel: 'approvals' } });
  assert.deepEqual(parseUtterance('start a task to draft the weekly report', ctx), { tool: 'start_task', args: { title: 'draft the weekly report', prompt: 'draft the weekly report' } });
  assert.equal(parseUtterance('ask Sales Agent to call the supplier', ctx)?.tool, 'delegate');
  assert.equal(parseUtterance('ask Nobody to call the supplier', ctx), null);
  assert.deepEqual(parseUtterance('schedule a meeting called Board sync', ctx), { tool: 'schedule_meeting', args: { title: 'Board sync' } });
});

test('the PA client sends a confirmation only for a click', async () => {
  const bodies: Record<string, unknown>[] = [];
  const request: Requester = async (_path, init) => { bodies.push(JSON.parse(String(init.body))); return { tool: 'start_task', status: 'ok' } as never; };
  const client = createPaClient(request);
  await client.invoke('start_task', { title: 'a', prompt: 'b' }, 'voice', 'h'.repeat(64));
  await client.invoke('start_task', { title: 'a', prompt: 'b' }, 'typed', 'h'.repeat(64));
  await client.invoke('start_task', { title: 'a', prompt: 'b' }, 'click', 'h'.repeat(64));
  assert.equal('confirm_hash' in bodies[0], false);
  assert.equal('confirm_hash' in bodies[1], false);
  assert.equal(bodies[2].confirm_hash, 'h'.repeat(64));
});

test('the tour client tolerates a malformed answer', async () => {
  const client = createPaClient((async () => ({ status: 'ready', stops: null })) as Requester);
  assert.deepEqual(await client.tour(), { status: 'ready', stops: [] });
});

test('overview: summaries only for own or granted workspaces, duplicates dropped', () => {
  const { workspaces } = readOverview({ workspaces: [
    { workspace_id: 'w1', tenant_id: 't1', name: 'Mine', kind: 'anum_os', grant_state: 'own', summary: { tasks_total: 2 } },
    { workspace_id: 'w2', tenant_id: 't2', name: 'Employer', kind: 'employment', grant_state: 'not_granted', summary: { tasks_total: 99 } },
    { workspace_id: 'w3', tenant_id: 't3', name: 'Shop', kind: 'weird', grant_state: 'granted', grant_id: 'g3', summary: { tasks_total: 5 } },
    { workspace_id: 'w3', tenant_id: 't3', name: 'Shop again', kind: 'business', grant_state: 'granted' },
    { name: 'broken' },
  ] });
  assert.equal(workspaces.length, 3);
  assert.equal(workspaces[1].summary, null);
  assert.equal(workspaces[2].kind, 'business');
  assert.deepEqual(readOverview(null), { workspaces: [] });
  assert.deepEqual(groupByKind(workspaces).map((g) => g.kind), ['employment', 'anum_os', 'business'].sort((a, b) => ['personal', 'employment', 'anum_os', 'business'].indexOf(a) - ['personal', 'employment', 'anum_os', 'business'].indexOf(b)));
});

test('a switch is needed unless both tenant and workspace match', () => {
  const scope = { tenantId: 't1', workspaceId: 'w1' };
  assert.equal(needsSwitch({ tenant_id: 't1', workspace_id: 'w1' }, scope), false);
  assert.equal(needsSwitch({ tenant_id: 't1', workspace_id: 'w2' }, scope), true);
  assert.equal(needsSwitch({ tenant_id: 't2', workspace_id: 'w1' }, scope), true);
});

test('grant client: pending approval is reported, not treated as applied', async () => {
  const calls: string[] = [];
  const request: Requester = async (path, init) => {
    calls.push(`${init.method} ${path}`);
    return (init.method === 'POST' ? { status: 'pending_approval', approval_id: 'ap1' } : { id: 'g1', workspace_id: 'w2', scopes: ['tasks'], status: 'active' }) as never;
  };
  const desk = createDeskClient(request);
  assert.deepEqual(await desk.grant('w2', ['tasks']), { kind: 'pending_approval', approvalId: 'ap1' });
  assert.equal((await desk.widen('g/1', ['tasks', 'costs'])).kind, 'applied');
  await desk.revoke('g/1');
  assert.deepEqual(calls, ['POST /api/v1/executive/grants', 'PATCH /api/v1/executive/grants/g%2F1', 'DELETE /api/v1/executive/grants/g%2F1']);
});

test('navigation is published for the scene and kept for the next mount', () => {
  const store = new Map<string, string>();
  const events: unknown[] = [];
  const host = {
    sessionStorage: { setItem: (k: string, v: string) => void store.set(k, v) } as unknown as Storage,
    dispatchEvent: (e: Event) => { events.push((e as CustomEvent).detail); return true; },
  };
  publishNavigation({ room_id: 'r1', floor_id: 'f1' }, host);
  assert.deepEqual(events, [{ room_id: 'r1', floor_id: 'f1' }]);
  assert.equal(store.get(HQ_PENDING_KEY), JSON.stringify({ room_id: 'r1', floor_id: 'f1' }));
});
