// Starter campus client rules (src/hq-starter-campus/client.ts): a preview creates nothing, only a click confirm
// sends the hash shown and exactly the buildings it was made for.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { StarterBuilding, StarterCampusPreview, StarterCounts } from '@anum/contracts';
import { StarterCampusApiError, canConfirm, createStarterCampusClient, deskLabels, hasWork, selectable } from '../src/hq-starter-campus/client.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const scope = { tenantId: 't', workspaceId: 'w' };
const HASH = 'b'.repeat(64);
const zero: StarterCounts = { buildings: 0, floors: 0, rooms: 0, desks: 0 };
const building = (key: string, status: StarterBuilding['status']): StarterBuilding => ({
  key, number: 1, id: `campus-01-${key}`, name: { en: key, ar: key }, timezone: 'Asia/Dubai', selected: true, action: 'create', reason: null,
  status, floors: [], counts: { create: status === 'complete' ? zero : { buildings: 1, floors: 1, rooms: 2, desks: 3 }, skip: zero } });
const preview = (patch: Partial<StarterCampusPreview> = {}): StarterCampusPreview => ({
  version: 1, catalog_version: 1, workspace_id: 'w', timezone: 'Asia/Dubai', selected: ['hq', 'tyre-pulse'], payload_hash: HASH, can_confirm: true,
  buildings: [building('hq', 'new'), building('tyre-pulse', 'new'), building('career', 'complete')],
  totals: { create: { buildings: 2, floors: 2, rooms: 4, desks: 6 }, skip: zero },
  rules: { creates_agents: false, seats_agents: false, changes_existing: false }, ...patch });

describe('starter campus client', () => {
  test('the preview is a GET without a body and carries the chosen buildings', async () => {
    const calls: { path: string; init: RequestInit }[] = [];
    const client = createStarterCampusClient(async <T>(path: string, init: RequestInit) => { calls.push({ path, init }); return preview() as T; }, () => scope);
    await client.preview(null);
    await client.preview(['hq', 'tyre-pulse']);
    assert.deepEqual(calls.map((c) => c.init.method), ['GET', 'GET']);
    assert.equal(calls[0].path, '/api/v1/office/starter-campus/preview');
    assert.equal(calls[1].path, '/api/v1/office/starter-campus/preview?buildings=hq%2Ctyre-pulse');
    assert.ok(calls.every((c) => c.init.body === undefined));
  });
  test('refuses an empty or malformed selection before any request', async () => {
    let sent = 0;
    const client = createStarterCampusClient(async <T>() => { sent += 1; return preview() as T; }, () => scope);
    await assert.rejects(client.preview([]), StarterCampusApiError);
    await assert.rejects(client.preview(['Bad Key']), StarterCampusApiError);
    assert.equal(sent, 0);
  });
  test('refuses an answer for another workspace or without a hash', async () => {
    const other = createStarterCampusClient(async <T>() => preview({ workspace_id: 'other' }) as T, () => scope);
    await assert.rejects(other.preview(null), /another workspace/);
    const bad = createStarterCampusClient(async <T>() => preview({ payload_hash: 'x' }) as T, () => scope);
    await assert.rejects(bad.preview(null), /version 1/);
    let now = scope;
    const moved = createStarterCampusClient(async <T>() => { now = { tenantId: 't', workspaceId: 'w2' }; return preview() as T; }, () => now);
    await assert.rejects(moved.preview(null), /another workspace/);
  });
  test('confirm sends a click, the shown hash, the selection and the time zone', async () => {
    let seen: { path: string; init: RequestInit } | null = null;
    const client = createStarterCampusClient(async <T>(path: string, init: RequestInit) => {
      seen = { path, init };
      return { version: 1, outcome: 'created', buildings: [], totals: zero } as T;
    }, () => scope);
    await client.confirm(preview());
    assert.equal(seen!.path, '/api/v1/office/starter-campus/confirm');
    assert.equal(seen!.init.method, 'POST');
    assert.deepEqual(JSON.parse(String(seen!.init.body)), { payload_hash: HASH, input_mode: 'click', buildings: ['hq', 'tyre-pulse'], timezone: 'Asia/Dubai' });
  });
});

describe('starter campus rules', () => {
  test('only buildings with something to create can be ticked', () => {
    assert.deepEqual(selectable(preview().buildings), ['hq', 'tyre-pulse']);
    assert.equal(hasWork(zero), false);
  });
  test('confirm needs an owner, a plan for exactly the ticked buildings, work to do and no request in flight', () => {
    const ticked = new Set(['hq', 'tyre-pulse']);
    assert.equal(canConfirm(preview(), ticked, false, false), true);
    assert.equal(canConfirm(null, ticked, false, false), false);
    assert.equal(canConfirm(preview({ can_confirm: false }), ticked, false, false), false);
    assert.equal(canConfirm(preview(), ticked, true, false), false);
    assert.equal(canConfirm(preview(), ticked, false, true), false, 'a plan being re-read is not confirmable');
    assert.equal(canConfirm(preview(), new Set(['hq']), false, false), false, 'the plan no longer matches the ticks');
    assert.equal(canConfirm(preview(), new Set(), false, false), false);
    assert.equal(canConfirm(preview({ totals: { create: zero, skip: zero } }), ticked, false, false), false);
  });
  test('desk labels are listed in plan order', () => {
    assert.equal(deskLabels({ desks: [{ label: 'Desk 1' }, { label: 'Desk 2' }] }), 'Desk 1, Desk 2');
  });
  test('English and Arabic carry the same keys', () => {
    const keys = (m: Record<string, unknown>) => Object.keys(m).filter((k) => k.startsWith('hqSc.')).sort();
    assert.deepEqual(keys(en), keys(ar));
    assert.ok(keys(en).length > 60);
  });
});

describe('add Arabic names client', () => {
  const arPreview = (patch: Record<string, unknown> = {}) => ({
    version: 1, catalog_version: 1, workspace_id: 'w', payload_hash: HASH, can_confirm: true, total: 1,
    items: [{ kind: 'buildings', id: 'campus-01-hq', name: 'ANUM HQ', name_ar: 'المقر الرئيسي لأنوم' }],
    counts: { buildings: 1, floors: 0, rooms: 0 },
    rules: { overwrites: false, fills_empty_only: true, matches_master_names_only: true, changes_other_fields: false }, ...patch });
  test('the list is a GET without a body; the confirm sends the shown hash and a click, nothing else', async () => {
    const calls: { path: string; init: RequestInit }[] = [];
    const client = createStarterCampusClient(async <T>(path: string, init: RequestInit) => {
      calls.push({ path, init });
      return (init.method === 'GET' ? arPreview() : { version: 1, outcome: 'filled', payload_hash: HASH, filled: 1, counts: { buildings: 1, floors: 0, rooms: 0 }, skipped: 0 }) as T;
    }, () => scope);
    const shown = await client.arabicPreview();
    const done = await client.arabicConfirm(shown);
    assert.equal(done.filled, 1);
    assert.equal(calls[0].path, '/api/v1/office/starter-campus/arabic-names/preview');
    assert.equal(calls[0].init.body, undefined);
    assert.equal(calls[1].path, '/api/v1/office/starter-campus/arabic-names/confirm');
    assert.deepEqual(JSON.parse(String(calls[1].init.body)), { payload_hash: HASH, input_mode: 'click' });
  });
  test('refuses an answer for another workspace, without a hash, or a result that is not version 1', async () => {
    await assert.rejects(createStarterCampusClient(async <T>() => arPreview({ workspace_id: 'other' }) as T, () => scope).arabicPreview(), /another workspace/);
    await assert.rejects(createStarterCampusClient(async <T>() => arPreview({ payload_hash: 'x' }) as T, () => scope).arabicPreview(), /version 1/);
    const bad = createStarterCampusClient(async <T>() => ({ version: 2 }) as T, () => scope);
    await assert.rejects(bad.arabicConfirm(arPreview() as never), /version 1/);
  });
  test('every message of the section exists in English and Arabic', () => {
    const keys = Object.keys(en).filter((k) => k.startsWith('hqSc.ar.'));
    assert.ok(keys.length >= 10);
    for (const k of keys) assert.ok(k in ar, k);
  });
});
