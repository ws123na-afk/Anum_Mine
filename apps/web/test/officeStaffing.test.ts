// Offices and agents client rules (src/lib/officeStaffing.ts): reads change nothing, apply sends only clicked actions with
// their hashes in groups of 20, answers from another workspace are refused, and both languages carry every string.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { StaffingAction, StaffingOffice, StaffingOfficePage } from '@anum/contracts';
import {
  StaffingApiError, applyAll, chunks, clampOffset, connectView, coverageRatio, createStaffingClient, localText, rolesByUnit, totals,
} from '../src/lib/officeStaffing.ts';
import { officeStaffingEn } from '../src/lib/messages/officeStaffing.en.ts';
import { officeStaffingAr } from '../src/lib/messages/officeStaffing.ar.ts';

const HASH = 'a'.repeat(64);
const page = (patch: Partial<StaffingOfficePage> = {}): StaffingOfficePage => ({
  version: 1, blueprint_version: 1, workspace_id: 'w', as_of: 'now', total: 0, limit: 12, offset: 0, buildings_total: 0, items: [], can_apply: false, ...patch,
});
const action = (i: number): StaffingAction => ({ id: `create:r${i}`, kind: 'create', role: `r${i}`, hash: HASH, content: {} as StaffingAction['content'] });

function recorder(answer: (path: string, init: RequestInit) => unknown) {
  const calls: { path: string; init: RequestInit }[] = [];
  const request = async <T,>(path: string, init: RequestInit): Promise<T> => { calls.push({ path, init }); return answer(path, init) as T; };
  return { calls, request };
}

describe('office staffing client', () => {
  test('list is a GET with search and paging and refuses another workspace', async () => {
    const r = recorder(() => page());
    const client = createStaffingClient(r.request, () => ({ tenantId: 't', workspaceId: 'w' }));
    await client.list(' commerce ', 12);
    assert.equal(r.calls[0].init.method, 'GET');
    assert.match(r.calls[0].path, /\/api\/v1\/office\/staffing\/offices\?limit=12&offset=12&q=commerce$/);
    const other = createStaffingClient(recorder(() => page({ workspace_id: 'x' })).request, () => ({ tenantId: 't', workspaceId: 'w' }));
    await assert.rejects(other.list('', 0), StaffingApiError);
    const bad = createStaffingClient(recorder(() => ({ version: 2 })).request, () => ({ tenantId: 't', workspaceId: 'w' }));
    await assert.rejects(bad.list('', 0), StaffingApiError);
  });

  test('office refuses invalid ids and answers for another office', async () => {
    const client = createStaffingClient(recorder(() => ({ version: 1, roles: [], building_id: 'other' })).request, () => ({ tenantId: 't', workspaceId: 'w' }));
    await assert.rejects(client.office('../x', 'en'), StaffingApiError);
    await assert.rejects(client.office('campus-02-commerce', 'en'), StaffingApiError);
  });

  test('apply always sends click, the language and only id and hash', async () => {
    const r = recorder(() => ({ version: 1, building_id: 'b', created: 1, updated: 0, results: [{ id: 'create:r1', outcome: 'created' }] }));
    const client = createStaffingClient(r.request, () => ({ tenantId: 't', workspaceId: 'w' }));
    await client.apply('b', 'ar', [action(1)]);
    assert.equal(r.calls[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(String(r.calls[0].init.body)), { input_mode: 'click', lang: 'ar', actions: [{ id: 'create:r1', hash: HASH }] });
    await assert.rejects(client.apply('b', 'en', []), StaffingApiError);
    await assert.rejects(client.apply('b', 'en', [{ id: 'create:x', hash: 'nope' }]), StaffingApiError);
  });

  test('applyAll sends groups of at most 20 and reports progress', async () => {
    const r = recorder((_p, init) => {
      const body = JSON.parse(String(init.body)) as { actions: { id: string }[] };
      return { version: 1, building_id: 'b', created: body.actions.length, updated: 0, results: body.actions.map((a) => ({ id: a.id, outcome: 'created' })) };
    });
    const client = createStaffingClient(r.request, () => ({ tenantId: 't', workspaceId: 'w' }));
    const seen: number[] = [];
    const out = await applyAll(client, 'b', 'en', Array.from({ length: 45 }, (_, i) => action(i)), (done) => seen.push(done));
    assert.equal(r.calls.length, 3);
    assert.deepEqual(seen, [20, 40, 45]);
    assert.equal(out.length, 45);
  });
});

describe('office staffing helpers', () => {
  test('chunks, totals, coverage and paging', () => {
    assert.deepEqual(chunks([1, 2, 3], 2), [[1, 2], [3]]);
    assert.deepEqual(totals([{ id: 'a', outcome: 'created' }, { id: 'b', outcome: 'updated' }, { id: 'c', outcome: 'stale' }, { id: 'd', outcome: 'resumed' }]),
      { created: 2, updated: 1, other: 1, stale: 1 });
    assert.equal(coverageRatio(5, 7), 71);
    assert.equal(coverageRatio(0, 0), 0);
    assert.equal(clampOffset(48, 13), 12);
    assert.equal(clampOffset(12, 0), 0);
  });

  test('only a not connected source offers a connect view; a server setting offers none', () => {
    assert.equal(connectView({ state: 'not_connected', view: 'tyre' }), 'tyre');
    assert.equal(connectView({ state: 'connected', view: 'tyre' }), null);
    assert.equal(connectView({ state: 'not_connected', view: null }), null);
    assert.equal(localText({ en: 'Tyre Pulse', ar: null }, 'ar'), 'Tyre Pulse');
    assert.equal(localText({ en: 'Tyre Pulse', ar: 'تاير بلس' }, 'ar'), 'تاير بلس');
  });

  test('roles are grouped by the office departments in order', () => {
    const role = (key: string, unit: string) => ({ key, unit }) as unknown as StaffingOffice['roles'][number];
    const office = { units: [{ id: 'finance', name: { en: 'Finance', ar: 'المالية' } }, { id: 'hr', name: { en: 'HR', ar: 'م' } }, { id: 'none', name: { en: 'x', ar: 'x' } }],
      roles: [role('hr_people', 'hr'), role('finance_clerk', 'finance')] } as unknown as StaffingOffice;
    assert.deepEqual(rolesByUnit(office).map((u) => [u.id, u.roles.map((r) => r.key)]), [['finance', ['finance_clerk']], ['hr', ['hr_people']]]);
  });

  test('every English string has an Arabic one, with Arabic letters and no long dashes', () => {
    assert.deepEqual(Object.keys(officeStaffingAr).sort(), Object.keys(officeStaffingEn).sort());
    for (const [key, value] of Object.entries(officeStaffingAr)) {
      const text = typeof value === 'string' ? value : Object.values(value).join(' ');
      assert.match(text, /[؀-ۿ]/, key);
      assert.doesNotMatch(text, /[–—]/, key);
    }
  });
});
