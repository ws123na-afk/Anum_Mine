import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  HQ_PENDING_KEY, createPaClient, isCurrent, parseUtterance, publishNavigation, splitOverview, summaryFigures,
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

test('summary figures: an ungranted scope is null, never zero', () => {
  const none = summaryFigures({ approvals: { pending: 2, oldest_pending_at: null } });
  assert.deepEqual([none.tasks, none.blocked, none.approvals, none.costUsd, none.meetings], [null, null, 2, null, null]);
  const some = summaryFigures({ tasks: { by_status: { completed: 5, waiting_approval: 2 }, blocked_agents: 0 }, cost: { month: '2026-10', estimated_cost_usd: '4.50', calls: 3, unpriced_calls: 0, currency: 'USD' } });
  assert.equal(some.tasks, 7);
  assert.equal(some.blocked, 2);
  assert.equal(some.costUsd, '4.50');
  assert.deepEqual(summaryFigures(undefined).counts, null);
});

test('overview items split by access and the current workspace is recognised', () => {
  const split = splitOverview([
    { workspace_id: 'w1', name: 'A', access: 'granted', grant_id: 'g', scopes: ['tasks'], expires_at: null, summary: {}, as_of: 'x' },
    { workspace_id: 'w2', access: 'not_granted', reason: 'no_grant', open_with: 'workspace_switch' },
    { workspace_id: 'w3', access: 'unavailable', reason: 'read_failed_or_audit_failed' },
  ]);
  assert.deepEqual([split.granted.length, split.notGranted.length, split.unavailable.length], [1, 1, 1]);
  assert.equal(isCurrent('w1', { workspaceId: 'w1' }), true);
  assert.equal(isCurrent('w2', { workspaceId: 'w1' }), false);
});

test('navigation is published for the scene and kept for the next mount', () => {
  const store = new Map<string, string>();
  const events: unknown[] = [];
  const host = {
    sessionStorage: { setItem: (k: string, v: string) => void store.set(k, v) } as unknown as Storage,
    dispatchEvent: (e: Event) => { events.push((e as CustomEvent).detail); return true; },
  };
  publishNavigation({ room_id: 'r1', floor_id: 'f1' }, host);
  assert.deepEqual(events, [{ building: undefined, floor: 'f1', room: 'r1', agent: undefined }]);
  assert.equal(store.get(HQ_PENDING_KEY), JSON.stringify({ room_id: 'r1', floor_id: 'f1' }));
});

test('briefing: typed and spoken phrases in English and Arabic ask for the read-only briefing', () => {
  for (const sentence of ['Brief me', 'brief me please', 'give me my daily briefing', 'Morning briefing', "What's on today", 'what is my day like']) {
    assert.deepEqual(parseUtterance(sentence, ctx), { tool: 'daily_briefing', args: { lang: 'en' } }, sentence);
  }
  for (const sentence of ['أعطني الإحاطة اليومية', 'ملخص اليوم', 'إحاطة']) {
    assert.deepEqual(parseUtterance(sentence, { ...ctx, lang: 'ar' }), { tool: 'daily_briefing', args: { lang: 'ar' } }, sentence);
  }
  assert.equal(parseUtterance('give me a summary', ctx)?.tool, 'summarise_workspace');
});

test('briefing client reads sections defensively and never sends a confirmation for a read', async () => {
  const sent: { path: string; init: RequestInit }[] = [];
  const request: Requester = async <T,>(path: string, init: RequestInit) => {
    sent.push({ path, init });
    return (path.includes('/briefing') ? { version: 1, sections: null } : { tool: 'daily_briefing', status: 'ok' }) as T;
  };
  const client = createPaClient(request);
  const briefing = await client.briefing('ar');
  assert.deepEqual(briefing.sections, []);
  assert.equal(sent[0].path, '/api/v1/executive-pa/briefing?lang=ar');
  await client.invoke('daily_briefing', { lang: 'en' }, 'voice', 'a'.repeat(64));
  assert.ok(!('confirm_hash' in JSON.parse(String(sent[1].init.body))));
});
