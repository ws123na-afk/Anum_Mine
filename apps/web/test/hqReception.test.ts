// Reception visitor register and the career building kind (docs/ANUM_MINE_HQ_RECEPTION.md, owner decisions O2 and O4).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import {
  actionsFor, createBody, dayWindow, draftFrom, draftProblems, emptyDraft, mergePage, newVisitId, parseSummary, parseVisitPage,
  receptionCounts, replaceVisit, toLocalInput, updateBody, visitQuery, type Draft,
} from '../src/lib/hqReception.ts';
import { createReceptionClient } from '../src/lib/hqReceptionApi.ts';
import { visitorLine } from '../src/lib/hqLabelModel.ts';
import { archetypeOf } from '../src/lib/campusBuildings.ts';
import { EXTERIOR_OF } from '../src/lib/campusExteriors.ts';
import { registryLook } from '../src/lib/campusRegistry.ts';
import { buildExterior, TRIANGLE_CAPS } from '../src/hqAssets/exterior/index.ts';
import { CATALOGS, t } from '../src/lib/i18n.ts';

const NOW = new Date(2026, 9, 27, 10, 30);
const visit = (over: Record<string, unknown> = {}) => ({
  id: 'visit-a', visitor_name: 'Visitor Example', company: null, host: null, purpose: 'Meeting', status: 'expected', expected_at: '2026-10-27T06:30:00+00:00',
  checked_in_at: null, checked_out_at: null, cancelled_at: null, building_id: null, room_id: null, revision: 1, created_by: 'u', updated_by: 'u',
  created_at: '2026-10-27T06:00:00+00:00', updated_at: '2026-10-27T06:00:00+00:00', ...over,
});

describe('list query and parsing', () => {
  test('today asks for the local day window; all dates sends no window', () => {
    const w = dayWindow(NOW);
    assert.equal(new Date(w.since).getTime(), new Date(2026, 9, 27).getTime());
    assert.equal(new Date(w.until).getTime(), new Date(2026, 9, 28).getTime());
    const today = new URL(`http://x${visitQuery({ q: '  acme ', status: 'checked_in', scope: 'today', cursor: 'abc', now: NOW })}`);
    assert.equal(today.pathname, '/api/v1/office/visitors');
    assert.equal(today.searchParams.get('q'), 'acme');
    assert.equal(today.searchParams.get('status'), 'checked_in');
    assert.equal(today.searchParams.get('since'), w.since);
    assert.equal(today.searchParams.get('cursor'), 'abc');
    const all = new URL(`http://x${visitQuery({ q: '', status: null, scope: 'all', cursor: null, now: NOW })}`);
    assert.equal(all.searchParams.has('since'), false);
    assert.equal(all.searchParams.has('q'), false);
    assert.equal(all.searchParams.get('limit'), '25');
  });
  test('malformed rows are dropped, never patched', () => {
    const page = parseVisitPage({ items: [visit(), { id: 'x' }, visit({ id: 'b', status: 'lost' }), null], next_cursor: 'c', total: 3, counts: { expected: 2, checked_in: -1 }, can_manage: 'yes' });
    assert.deepEqual(page.items.map((v) => v.id), ['visit-a']);
    assert.deepEqual(page.counts, { expected: 2, checked_in: 0, checked_out: 0, cancelled: 0 });
    assert.equal(page.can_manage, false);
    assert.equal(parseVisitPage(null).items.length, 0);
  });
  test('summary needs a number and keeps only positive rows', () => {
    assert.equal(parseSummary({}), null);
    const s = parseSummary({ checked_in: 3, by_room: [{ room_id: 'r1', count: 2 }, { room_id: '', count: 1 }, { room_id: 'r2', count: 0 }], by_building: [{ building_id: 'b1', count: 1 }], unplaced: 0 });
    assert.deepEqual(s?.by_room, [{ room_id: 'r1', count: 2 }]);
    assert.deepEqual(s?.by_building, [{ building_id: 'b1', count: 1 }]);
  });
});

describe('the 3D reception rooms show only stored counts', () => {
  const rooms = [
    { id: 'rec1', buildingId: 'b1', kind: 'reception' }, { id: 'ops', buildingId: 'b1', kind: 'office' },
    { id: 'rec2', buildingId: 'b2', kind: 'reception' }, { id: 'rec3', buildingId: 'b2', kind: 'reception' },
  ];
  test('no answer: no room gets a number', () => {
    assert.equal(receptionCounts(null, rooms).size, 0);
  });
  test('own room counts; a building visit counts only where the building has one reception; unplaced only with one in all', () => {
    const counts = receptionCounts({ version: 1, checked_in: 6, by_room: [{ room_id: 'rec2', count: 2 }, { room_id: 'ops', count: 1 }], by_building: [{ building_id: 'b1', count: 1 }, { building_id: 'b2', count: 1 }], unplaced: 1, as_of: '' }, rooms);
    assert.deepEqual([...counts], [['rec1', 1], ['rec2', 2], ['rec3', 0]]);
    const single = receptionCounts({ version: 1, checked_in: 2, by_room: [], by_building: [], unplaced: 2, as_of: '' }, rooms.slice(0, 2));
    assert.deepEqual([...single], [['rec1', 2]]);
  });
  test('the label line says the real count, "No visitors checked in" at zero, and nothing without a count', () => {
    assert.equal(visitorLine('reception', 0, 'Reception'), `${t('hqRec.room.none')} · Reception`);
    assert.equal(visitorLine('reception', 3, 'Reception'), `${t('hqRec.room.count', { count: 3 })} · Reception`);
    assert.match(visitorLine('reception', 3, 'x'), /^3 visitors checked in/);
    assert.equal(visitorLine('reception', undefined, 'Reception'), 'Reception');
    assert.equal(visitorLine('office', 4, 'Office'), 'Office');
  });
});

describe('form rules and bodies', () => {
  const draft = (over: Partial<Draft> = {}): Draft => ({ ...emptyDraft(NOW, 'rec1'), name: ' Visitor  Example ', purpose: 'Interview', company: ' ', host: 'agent:ag1', ...over });
  test('problems before sending', () => {
    assert.deepEqual(draftProblems(draft(), NOW), {});
    assert.deepEqual(draftProblems(draft({ name: ' ', purpose: '', expected: '' }), NOW), { name: 'required', purpose: 'required', expected: 'invalidTime' });
    assert.equal(draftProblems(draft({ name: 'x'.repeat(121) }), NOW).name, 'tooLong');
    assert.equal(draftProblems(draft({ company: 'x'.repeat(121) }), NOW).company, 'tooLong');
    assert.equal(draftProblems(draft({ expected: toLocalInput(new Date(2028, 0, 1)) }), NOW).expected, 'outOfRange');
  });
  test('create and update bodies are clicks with tidy text and a zoned time', () => {
    const body = createBody(draft({ arrived: true }), 'visit-1');
    assert.equal(body.visitor_name, 'Visitor Example');
    assert.equal(body.company, null);
    assert.deepEqual([body.host_kind, body.host_id, body.room_id, body.arrived, body.input_mode], ['agent', 'ag1', 'rec1', true, 'click']);
    assert.match(body.expected_at, /Z$/);
    assert.equal(new Date(body.expected_at).getTime(), new Date(draft().expected).getTime());
    const up = updateBody(draft({ host: '', room: '' }), 4);
    assert.deepEqual([up.host_kind, up.host_id, up.room_id, up.expected_revision, up.input_mode], [null, null, null, 4, 'click']);
  });
  test('an edit starts from the stored visit', () => {
    const d = draftFrom(parseVisitPage({ items: [visit({ host: { kind: 'user', id: 'owner_a' }, room_id: 'rec1', company: 'Acme' })] }).items[0]);
    assert.deepEqual([d.host, d.room, d.company, d.arrived], ['user:owner_a', 'rec1', 'Acme', false]);
  });
  test('client ids are plain and stable per attempt', () => {
    assert.equal(newVisitId(() => 'abc-123'), 'visit-abc123');
    assert.match(newVisitId(), /^visit-[A-Za-z0-9]{1,32}$/);
  });
});

describe('what a visit can do next', () => {
  test('owners move visits forward only; others only read', () => {
    assert.deepEqual(actionsFor({ status: 'expected' }, true), { checkIn: true, checkOut: false, cancel: true, edit: true, remove: true });
    assert.deepEqual(actionsFor({ status: 'checked_in' }, true), { checkIn: false, checkOut: true, cancel: false, edit: true, remove: true });
    assert.deepEqual(actionsFor({ status: 'checked_out' }, true), { checkIn: false, checkOut: false, cancel: false, edit: false, remove: true });
    assert.deepEqual(Object.values(actionsFor({ status: 'expected' }, false)), [false, false, false, false, false]);
  });
  test('paging merges without duplicates and a write replaces one row', () => {
    const [a, b] = parseVisitPage({ items: [visit(), visit({ id: 'b' })] }).items;
    const merged = mergePage([a], [{ ...a, revision: 2 }, b]);
    assert.deepEqual(merged.map((v) => [v.id, v.revision]), [['visit-a', 2], ['b', 1]]);
    assert.deepEqual(replaceVisit(merged, 'b', null).map((v) => v.id), ['visit-a']);
  });
});

describe('the client sends clicks and revisions', () => {
  test('paths and bodies', async () => {
    const calls: Array<[string, RequestInit]> = [];
    const c = createReceptionClient(async <T,>(path: string, init: RequestInit) => { calls.push([path, init]); return visit() as T; }, async (path, init) => { calls.push([path, init]); });
    await c.move('a/b', 'check-out', 3);
    await c.remove('v1', 2);
    await c.update('v1', { expected_revision: 1, input_mode: 'click', purpose: 'x' });
    assert.equal(calls[0][0], '/api/v1/office/visitors/a%2Fb/check-out');
    assert.deepEqual(JSON.parse(String(calls[0][1].body)), { expected_revision: 3, input_mode: 'click' });
    assert.equal(calls[1][0], '/api/v1/office/visitors/v1?expected_revision=2&input_mode=click');
    assert.equal(calls[1][1].method, 'DELETE');
    assert.equal(calls[2][1].method, 'PATCH');
  });
});

describe('career is its own building kind (O2)', () => {
  test('the career kind always draws the career centre, with its own exterior asset', () => {
    assert.equal(archetypeOf({ kind: 'career', icon: 'truck', workspaceId: 'w' }), 'career');
    assert.equal(archetypeOf({ kind: 'career', icon: null, workspaceId: 'w' }), 'career');
    assert.equal(EXTERIOR_OF.career, 'careerCentre');
    const asset = buildExterior('careerCentre', { seed: 'w', width: 30, depth: 15, levels: 2 });
    assert.ok(asset.triangles > 200 && asset.triangles <= TRIANGLE_CAPS.careerCentre, String(asset.triangles));
    assert.ok(asset.sign.width > 4 && asset.sign.z > 0, 'a front sign board on the pylon');
  });
  test('the Career & Employment starter building maps to the career kind', () => {
    assert.deepEqual(registryLook('campus-08-career'), { kind: 'career', icon: null });
    assert.equal(registryLook('campus-07-personal').kind, 'personal');
    assert.equal(registryLook('other-building').kind, 'business');
  });
  test('the kind has English and Arabic names', () => {
    assert.equal(CATALOGS.en['hq.kind.career'], 'Career');
    assert.ok(typeof CATALOGS.ar['hq.kind.career'] === 'string' && CATALOGS.ar['hq.kind.career'] !== 'Career');
  });
});

describe('no simulated visitors come back', () => {
  test('the panel and the summary hook hold no example people and no local visitor list', () => {
    for (const f of ['HqReceptionPanel.tsx', 'useVisitorSummary.ts', 'lib/hqReception.ts']) {
      const src = readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8');
      for (const name of ['Reception coordinator', 'Finance Controller', 'Chief of Staff', 'Sample Customer', 'ReceptionOperations']) assert.ok(!src.includes(name), `${f}: ${name}`);
      assert.ok(!/Math\.random\(\)\s*\*\s*\d/.test(src), `${f} invents no figures`);
    }
  });
});
