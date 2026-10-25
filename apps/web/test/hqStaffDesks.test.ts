// Staff my campus client rules (src/hq-staff-desks/client.ts): a preview creates nothing, only a click confirm sends
// one building's hash, and turning agents on needs the hash of exactly that selection.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { StaffBatch, StaffDeskCounts, StaffDesksPreview } from '@anum/contracts';
import { StaffDesksApiError, canConfirm, createStaffDesksClient, flatten, money, pageOf, summarize } from '../src/hq-staff-desks/client.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const scope = { tenantId: 't', workspaceId: 'w' };
const HASH = 'c'.repeat(64);
const counts = (patch: Partial<StaffDeskCounts> = {}): StaffDeskCounts => ({
  plan: 0, resume: 0, occupied: 0, keep_empty: 0, over_limit: 0, over_cap: 0, no_role: 0, excluded: 0, agent_elsewhere: 0, total: 0, ...patch });
const batch: StaffBatch = { building_id: 'campus-01-hq', count: 2, new_agents: 2, resumed: 0, quota: 2, max_monthly_usd: 10, payload_hash: HASH };
const preview = (patch: Partial<StaffDesksPreview> = {}): StaffDesksPreview => ({
  version: 1, catalog_version: 1, workspace_id: 'w', as_of: 'now', lang: 'en',
  limits: { agents: 0, max_agents: 200, free_slots: 200, run_cap: null }, totals: counts({ plan: 2, total: 2 }),
  cost: { per_template: [], max_monthly_usd: 10, agents: 2, workspace_budget: { visible: true, monthly_cost_limit_usd: null }, within_budget: null },
  roles: [], capability_gaps: [],
  buildings: [{ id: 'campus-01-hq', name: 'ANUM HQ', selected: true, counts: counts({ plan: 2, total: 2 }), rooms: [{
    id: 'r1', name: 'Orders', name_ar: null, kind: 'office', team_department: null, floor_id: 'f', floor_level: 0, floor_name: 'G', template_id: 'orders_desk', role_source: 'name',
    counts: counts({ plan: 2, total: 2 }), desks: [1, 2].map((n) => ({ id: `d${n}`, label: `D${n}`, number: n, state: 'plan' as const, reason: null,
      agent: { name: `Orders Desk, Orders ${n}`, template_id: 'orders_desk', monthly_cost_limit_usd: 5, existing_agent_id: null } })) }] }],
  batches: [batch], can_confirm: true,
  rules: { created_paused: true, touches_occupied_desks: false, click_only: true, least_privilege: true }, ...patch });

describe('staff desks client', () => {
  test('the preview is a POST that only reads and carries the choices', async () => {
    const calls: { path: string; init: RequestInit }[] = [];
    const client = createStaffDesksClient(async <T>(path: string, init: RequestInit) => { calls.push({ path, init }); return preview() as T; }, () => scope);
    await client.preview({ buildings: ['campus-01-hq'], exclude_rooms: ['r9'], max_agents: 5, lang: 'ar' });
    assert.equal(calls[0].path, '/api/v1/office/staff-desks/preview');
    assert.deepEqual(JSON.parse(String(calls[0].init.body)), { buildings: ['campus-01-hq'], exclude_rooms: ['r9'], max_agents: 5, lang: 'ar' });
  });
  test('refuses malformed ids before any request, another workspace and a bad hash', async () => {
    let sent = 0;
    const client = createStaffDesksClient(async <T>() => { sent += 1; return preview() as T; }, () => scope);
    await assert.rejects(client.preview({ buildings: ['Bad Id'], exclude_rooms: [], lang: 'en' }), StaffDesksApiError);
    await assert.rejects(client.keepEmpty(['x y'], true), StaffDesksApiError);
    assert.equal(sent, 0);
    const other = createStaffDesksClient(async <T>() => preview({ workspace_id: 'other' }) as T, () => scope);
    await assert.rejects(other.preview({ exclude_rooms: [], lang: 'en' }), /another workspace/);
    const bad = createStaffDesksClient(async <T>() => preview({ batches: [{ ...batch, payload_hash: 'x' }] }) as T, () => scope);
    await assert.rejects(bad.preview({ exclude_rooms: [], lang: 'en' }), /invalid hash/);
  });
  test('confirm sends a click, one building, its hash and its quota', async () => {
    let seen: { path: string; init: RequestInit } | null = null;
    const client = createStaffDesksClient(async <T>(path: string, init: RequestInit) => { seen = { path, init }; return { version: 1, outcome: 'created', results: [] } as T; }, () => scope);
    await client.confirm(batch, ['r9'], 'en');
    assert.equal(seen!.path, '/api/v1/office/staff-desks/confirm');
    assert.equal(seen!.init.method, 'POST');
    assert.deepEqual(JSON.parse(String(seen!.init.body)), { payload_hash: HASH, input_mode: 'click', building_id: 'campus-01-hq', exclude_rooms: ['r9'], quota: 2, lang: 'en' });
  });
  test('turning on sends a click with the hash of that selection and refuses a bad hash', async () => {
    let body: Record<string, unknown> = {};
    const client = createStaffDesksClient(async <T>(_p: string, init: RequestInit) => { body = JSON.parse(String(init.body)); return {} as T; }, () => scope);
    const pv = { version: 1, items: [], not_paused_or_unknown: [], max_monthly_usd: 5, agents_without_cap: [], workspace_budget: { visible: false, monthly_cost_limit_usd: null }, within_budget: null, payload_hash: HASH, can_confirm: true } as const;
    await client.turnOn(['a1'], pv);
    assert.deepEqual(body, { agent_ids: ['a1'], payload_hash: HASH, input_mode: 'click' });
    await assert.rejects(client.turnOn(['a1'], { ...pv, payload_hash: 'bad' }), StaffDesksApiError);
  });
});

describe('staff desks rules', () => {
  test('confirm needs an owner, a batch and no request in flight', () => {
    assert.equal(canConfirm(preview(), false, false), true);
    assert.equal(canConfirm(null, false, false), false);
    assert.equal(canConfirm(preview({ can_confirm: false }), false, false), false);
    assert.equal(canConfirm(preview({ batches: [] }), false, false), false);
    assert.equal(canConfirm(preview(), true, false), false);
    assert.equal(canConfirm(preview(), false, true), false, 'a plan being re-read is not confirmable');
  });
  test('desks are flattened in server order and paged', () => {
    assert.deepEqual(flatten(preview()).map((f) => f.desk.id), ['d1', 'd2']);
    const rows = Array.from({ length: 55 }, (_, i) => i);
    assert.deepEqual(pageOf(rows, 0, 25).items.length, 25);
    assert.equal(pageOf(rows, 2, 25).items.length, 5);
    assert.equal(pageOf(rows, 99, 25).page, 2, 'a page past the end is clamped');
    assert.equal(pageOf([], 0, 25).pages, 1);
  });
  test('results are counted by status and money is shown with the currency code', () => {
    assert.deepEqual(summarize([{ desk_id: 'a', room_id: 'r', status: 'created', agent_id: 'x', agent_name: 'n', reason: null },
      { desk_id: 'b', room_id: 'r', status: 'failed', agent_id: null, agent_name: 'n', reason: 'agent_limit' }]), { created: 1, seated_existing: 0, skipped: 0, failed: 1 });
    assert.equal(money(1000, 'en'), 'USD 1,000');
    assert.equal(money(1000, 'ar'), 'USD 1,000', 'Arabic keeps Western digits');
  });
  test('English and Arabic carry the same keys, and every state and result has words', () => {
    const keys = (m: Record<string, unknown>) => Object.keys(m).filter((k) => k.startsWith('hqSd.')).sort();
    assert.deepEqual(keys(en), keys(ar));
    for (const s of ['plan', 'resume', 'occupied', 'keep_empty', 'over_limit', 'over_cap', 'no_role', 'excluded', 'agent_elsewhere']) assert.ok(`hqSd.state.${s}` in en);
    for (const r of ['agent_limit', 'agent_refused', 'agent_failed', 'seat_conflict', 'cap_failed', 'room_failed', 'kept_empty', 'already_staffed', 'being_built']) assert.ok(`hqSd.done.reason.${r}` in en);
    for (const o of ['created', 'partial', 'failed', 'already_staffed', 'in_progress']) assert.ok(`hqSd.done.outcome.${o}` in en);
  });
});
