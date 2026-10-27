import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { CampusBuilding, CampusTotals } from '../src/lib/campusModel.ts';
import { translate, CATALOGS, type Locale, type MessageKey } from '../src/lib/i18n.ts';
import {
  BILLBOARD_FIELDS, BILLBOARD_READS, BILLBOARD_STALE_MS, billboardModel, billboardTexts, workOf, type BillboardFlow, type BillboardInput,
} from '../src/hq-billboards/billboardModel.ts';
import { signAtlasLayout } from '../src/lib/hqTextures.ts';
import { boardMesh, boardSites, boardWidth, signCell } from '../src/hq-billboards/billboardSites.ts';
import { layoutCampus } from '../src/lib/campusModel.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';

// The billboard of a building shows only fixed messages and real record fields (docs/ANUM_MINE_HQ_BILLBOARDS.md). Every name here is plainly fake.
const NOW = Date.parse('2026-10-26T10:00:00Z');
const FRESH = '2026-10-26T09:59:30Z';
const totals = (over: Partial<CampusTotals> = {}): CampusTotals => ({ agents: 0, working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0, ...over });
const LEAK = 'ZZLEAK';

const poisoned = (over: Record<string, unknown> = {}): CampusBuilding => ({
  workspace_id: 'ws~bld_hq', tenant_id: `${LEAK}TENANT`, name: 'OKNAME Tower', nameAr: 'برج OKNAME', kind: 'executive', kindKnown: true, icon: `${LEAK}ICON`, role: `${LEAK}ROLE`,
  rooms: 7, desks: 31, floors: 3, accessible: true, totals: totals({ agents: 6, working: 2, meeting: 1, waiting_approval: 1, idle: 2 }), presence_verified_at: FRESH,
  realWorkspaceId: 'ws', registryBuildingId: 'bld_hq', workspaceName: `${LEAK}WORKSPACE`, people: [`${LEAK}PEOPLE`], departments: [`${LEAK}AGENTROLE`],
  profileRevision: 4, archived: false, ...over,
} as unknown as CampusBuilding);

const room = (label: string, department: string | null) => ({
  id: `${LEAK}ROOMID`, floorId: 'f', buildingId: 'b', label, labelAr: `${label} ع`, kind: 'office', x: 0, z: 0, width: 5, depth: 4, height: 3, capacity: 4, revision: 1,
  deskIds: [`${LEAK}DESK`], teamDepartment: department, evidence: 'registry',
});
const plan = () => ({
  floors: [
    { id: 'f1', name: `${LEAK}FLOOR`, level: 0, rooms: [room('OKROOM Executive Office', 'marketing'), room('OKROOM Boardroom', 'sales')], desks: [{ id: 'd', roomId: 'r', label: 'Desk 1', position: { x: 0, y: 0, z: 0 }, agentId: `${LEAK}AGENTID`, agent: { name: `${LEAK}AGENTNAME` }, assignmentVisible: true }], bounds: { width: 10, depth: 10 } },
    { id: 'f2', name: 'Upper', level: 1, rooms: [room('OKROOM Strategy Room', null), room('OKROOM Command Center', 'marketing'), room('OKROOM Lounge', null), room('OKROOM Library', null)], desks: [], bounds: { width: 10, depth: 10 } },
  ],
  lit: new Set([`${LEAK}LIT`]), seated: new Set([`${LEAK}SEATED`]),
}) as unknown as NonNullable<BillboardInput['plan']>;

const item = (key: string, stage: string, building: string | null, extra: Record<string, unknown> = {}) => ({
  key, kind: 'task', id: key, title: `${LEAK}TITLE-${key}`, stage, status: `${LEAK}STATUS`, since: FRESH, created_at: FRESH,
  location: building ? { building_id: building, floor_id: 'f', room_id: 'r', room_name: `${LEAK}ROOMNAME`, desk_id: 'd', agent_id: `${LEAK}A`, agent_name: `${LEAK}AGENT` } : null,
  location_reason: null, stuck: null, waiting: null, ...extra,
});
const snapshot = (items: unknown[], over: Record<string, unknown> = {}): OfficeFlowSnapshot => ({
  version: 1, workspace_id: 'ws', as_of: FRESH, cursor: null, thresholds: {}, sources: { tasks: 'connected', approvals: 'connected', leads: 'connected', orders: 'not_connected' },
  items, rooms: {}, totals: {}, truncated: false, ...over,
} as unknown as OfficeFlowSnapshot);
const flowOf = (items: unknown[], status: BillboardFlow['status'] = 'ready', over: Record<string, unknown> = {}): BillboardFlow => ({ status, snapshot: snapshot(items, over) });

const input = (over: Partial<BillboardInput> = {}, locale: Locale = 'en'): BillboardInput => ({
  building: poisoned(), plan: plan(), flow: flowOf([item('a', 'in_office', 'bld_hq'), item('b', 'blocked', 'bld_hq'), item('c', 'waiting_human', 'bld_hq', { waiting: { code: 'approval_pending' } }), item('d', 'in_office', 'bld_other'), item('e', 'done', 'bld_hq'), item('f', 'in_office', null)]),
  currentWorkspaceId: 'ws', now: NOW, locale, tr: (key, vars) => translate(locale, key, vars), ...over,
});

// ---- recording proxy: which properties does the model read?

function track<T extends object>(value: T, path: string, log: Set<string>): T {
  return new Proxy(value, {
    get(target, key, receiver) {
      const out = Reflect.get(target, key, receiver);
      if (typeof key === 'symbol' || key === 'length' || typeof out === 'function') return out;
      const part = Array.isArray(target) ? '[]' : `.${key}`;
      const next = Array.isArray(target) ? `${path}[]` : `${path}.${key}`;
      void part;
      log.add(next);
      return out && typeof out === 'object' && !(out instanceof Set) ? track(out as object, next, log) : out;
    },
  });
}

test('the billboard reads only the allowed record fields', () => {
  const log = new Set<string>();
  const base = input();
  const model = billboardModel({ ...base, building: track(base.building, 'building', log), plan: track(base.plan!, 'plan', log), flow: track(base.flow!, 'flow', log) });
  assert.ok(model.rows.length > 5);
  const outside = [...log].filter((path) => !BILLBOARD_READS.includes(path));
  assert.deepEqual(outside, [], 'a property outside BILLBOARD_READS was read');
  assert.ok(log.has('building.totals.working') && log.has('plan.floors[].rooms[].label') && log.has('flow.snapshot.items[].stage'), 'the proxy sees the reads that matter');
});

test('no string comes from a field outside the allowed list, in English or Arabic', () => {
  for (const locale of ['en', 'ar'] as const) {
    const model = billboardModel(input({}, locale));
    const texts = billboardTexts(model);
    const ids = new Set<string>(BILLBOARD_FIELDS.map((f) => f.id));
    for (const { field, text } of texts) {
      assert.ok(ids.has(field), `unknown field ${field}`);
      assert.ok(!text.includes(LEAK), `${field} leaks: ${text}`);
      assert.ok(!/undefined|NaN|null|\[object/.test(text), `${field} prints a bad value: ${text}`);
    }
    const all = texts.map((x) => x.text).join(' | ');
    assert.match(all, locale === 'en' ? /OKNAME Tower/ : /برج OKNAME/);
    assert.match(all, locale === 'en' ? /OKROOM Executive Office/ : /OKROOM Executive Office ع/);
  }
});

test('every fixed text is a catalogue message, every other text is a record field value', () => {
  const fixedEn = new Set<string>();
  for (const [key, message] of Object.entries(CATALOGS.en)) {
    if (!key.startsWith('hqBillboard.') && !key.startsWith('hq.status.') && !key.startsWith('hq.kind.') && !key.startsWith('hq.building.') && !key.startsWith('hqNav.card.')) continue;
    for (const form of typeof message === 'string' ? [message] : Object.values(message)) if (typeof form === 'string') fixedEn.add(form);
  }
  const model = billboardModel(input());
  const fields = new Set(['name', 'roomNames', 'presenceTime', 'workTime']);
  for (const { field, text } of billboardTexts(model)) {
    if (fields.has(field)) continue;
    const stripped = [...fixedEn].some((form) => new RegExp(`^${form.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\{\w+\\\}/g, '.*')}$`).test(text));
    const numberParts = /^[A-Za-z ]+ \d+\+?$/.test(text);
    const departments = field === 'departments';
    const sizeLine = field === 'floors' && / · /.test(text);
    assert.ok(stripped || numberParts || departments || sizeLine || /^\d+\+?$/.test(text), `${field} text is neither fixed nor a plain count: ${text}`);
  }
});

test('property check: random availability never prints data that is not there', () => {
  let seed = 7;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  for (let i = 0; i < 300; i++) {
    const hasTotals = rnd() < 0.5, hasPlan = rnd() < 0.5, hasFlow = rnd() < 0.5, known = rnd() < 0.5;
    const model = billboardModel(input({
      building: poisoned({
        totals: hasTotals ? totals({ agents: Math.floor(rnd() * 9), working: 1 }) : null, kindKnown: known, rooms: rnd() < 0.5 ? null : 4, floors: rnd() < 0.5 ? null : 2,
        accessible: rnd() < 0.8, presence_verified_at: rnd() < 0.3 ? null : FRESH,
      }),
      plan: hasPlan ? plan() : null, flow: hasFlow ? flowOf([item('a', 'in_office', 'bld_hq')], rnd() < 0.5 ? 'ready' : 'error', { sources: { tasks: rnd() < 0.5 ? 'connected' : 'not_connected' } }) : null,
    }));
    const text = billboardTexts(model);
    assert.ok(text.every((x) => !x.text.includes(LEAK)));
    const hasPresence = hasTotals && model.accessible;
    const presence = model.rows.find((r) => r.field === 'presence')!;
    assert.equal(presence.connected, hasPresence, 'presence is connected only with a totals record');
    if (!presence.connected) assert.equal(presence.value, 'Not connected');
    assert.equal(model.rows.some((r) => r.field === 'presenceTime'), hasPresence, 'no verified time without presence');
    const rooms = model.rows.find((r) => r.field === 'roomNames')!;
    assert.equal(rooms.connected, hasPlan);
    if (!hasPlan) assert.equal(rooms.value, 'Not connected');
  }
});

test('an unavailable feed shows Not connected, never a zero', () => {
  const m = billboardModel(input({ building: poisoned({ totals: null, rooms: null, floors: null }), plan: null, flow: null }));
  const byField = (f: string) => m.rows.find((r) => r.field === f)!;
  for (const f of ['presence', 'work', 'roomNames']) { assert.equal(byField(f).value, 'Not connected', f); assert.equal(byField(f).connected, false); assert.equal(byField(f).parts, undefined); }
  assert.equal(m.summary, 'Not connected');
  assert.ok(!m.rows.some((r) => r.field === 'presenceTime' || r.field === 'workTime'));
  const ar = billboardModel(input({ building: poisoned({ totals: null }), flow: null }, 'ar'));
  assert.equal(ar.rows.find((r) => r.field === 'presence')!.value, 'غير متصل');
  // Another workspace's building has no flow of its own; a denied or failed flow is not a count either.
  assert.equal(workOf(poisoned({ realWorkspaceId: 'other' }), flowOf([]), 'ws').state, 'not_connected');
  assert.equal(workOf(poisoned(), { status: 'denied', snapshot: null }, 'ws').state, 'no_access');
  assert.equal(workOf(poisoned(), { status: 'error', snapshot: null }, 'ws').state, 'not_connected');
  assert.equal(workOf(poisoned(), flowOf([], 'ready', { sources: { tasks: 'not_connected' } }), 'ws').state, 'not_connected');
});

test('open work counts only tasks a record placed in this building, and says when the read failed', () => {
  const w = workOf(poisoned(), input().flow, 'ws');
  assert.deepEqual(w.state === 'ok' ? [w.open, w.waiting, w.blocked, w.stale, w.atLeast] : null, [3, 1, 1, false, false]);
  const stale = workOf(poisoned(), { ...input().flow!, status: 'error' }, 'ws');
  assert.equal(stale.state === 'ok' && stale.stale, true);
  const cut = workOf(poisoned(), flowOf([item('a', 'in_office', 'bld_hq')], 'ready', { truncated: true }), 'ws');
  assert.equal(cut.state === 'ok' && cut.atLeast, true);
  const row = billboardModel(input({ flow: flowOf([item('a', 'in_office', 'bld_hq')], 'ready', { truncated: true }) })).rows.find((r) => r.field === 'work')!;
  assert.equal(row.value, '1+');
  // The workspace's only building (no registry id) takes every located task.
  const own = workOf(poisoned({ registryBuildingId: undefined }), input().flow, 'ws');
  assert.equal(own.state === 'ok' ? own.open : -1, 4);
});

test('presence shows the campus panel counts with a verified time, and calls an old record stale', () => {
  const fresh = billboardModel(input());
  const p = fresh.rows.find((r) => r.field === 'presence')!;
  assert.deepEqual(p.parts?.map((x) => `${x.key}:${x.value}`), ['working:2', 'meeting:1', 'waiting_approval:1', 'idle:2']);
  assert.match(fresh.rows.find((r) => r.field === 'presenceTime')!.value, /^Verified /);
  const old = new Date(NOW - BILLBOARD_STALE_MS - 1000).toISOString();
  const stale = billboardModel(input({ building: poisoned({ presence_verified_at: old }) })).rows.find((r) => r.field === 'presenceTime')!;
  assert.equal(stale.stale, true);
  assert.match(stale.value, /^Stale: last verified /);
  const none = billboardModel(input({ building: poisoned({ presence_verified_at: null }) })).rows.find((r) => r.field === 'presenceTime')!;
  assert.equal(none.value, 'Verification time not reported');
  const empty = billboardModel(input({ building: poisoned({ totals: totals() }) })).rows.find((r) => r.field === 'presence')!;
  assert.equal(empty.value, 'No agents');
});

test('what it does is a fixed sentence per kind, only for a kind the owner set, and rooms and departments are the registry ones', () => {
  const about = (kind: string, known: boolean) => billboardModel(input({ building: poisoned({ kind, kindKnown: known }) })).rows.find((r) => r.field === 'purpose')!.value;
  const sentences = ['executive', 'business', 'department', 'personal'].map((k) => about(k, true));
  assert.equal(new Set(sentences).size, 4);
  for (const s of sentences) assert.ok(!/\d/.test(s) && !/working|task|agent/i.test(s), `a kind sentence must not claim activity: ${s}`);
  assert.match(about('executive', false), /not set yet/);
  const m = billboardModel(input());
  assert.equal(m.kindText, 'Executive');
  assert.equal(billboardModel(input({ building: poisoned({ kindKnown: false }) })).kindText, null);
  assert.equal(m.rows.find((r) => r.field === 'departments')!.value, 'Marketing, Sales');
  assert.equal(m.rows.find((r) => r.field === 'roomNames')!.value, 'OKROOM Executive Office, OKROOM Boardroom, OKROOM Strategy Room, OKROOM Command Center, OKROOM Lounge, +1 more');
  assert.equal(m.rows.find((r) => r.field === 'floors')!.value, '3 floors · 7 rooms');
});

test('the field table of the docs lists every field and its source', () => {
  const doc = readFileSync(new URL('../../../docs/ANUM_MINE_HQ_BILLBOARDS.md', import.meta.url), 'utf8');
  for (const f of BILLBOARD_FIELDS) assert.ok(doc.includes(`\`${f.id}\``), `docs must list the field ${f.id}`);
  const keys = (Object.keys(CATALOGS.en) as MessageKey[]).filter((k) => k.startsWith('hqBillboard.'));
  assert.deepEqual(keys.sort(), (Object.keys(CATALOGS.ar) as MessageKey[]).filter((k) => k.startsWith('hqBillboard.')).sort());
});

// ---- the boards in the scene

const mk = (id: string, name: string, kind: CampusBuilding['kind'], floors: number): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name, kind, icon: null, floors, accessible: true, totals: null, presence_verified_at: null });
const fixture = () => ['a', 'b', 'c', 'd', 'e'].map((id, i) => mk(id, `Fake ${id}`, (['executive', 'business', 'department', 'personal', 'business'] as const)[i], 2));

test('a board stands in front of each building, clear of the buildings, walkways and plaza, and is the same every time', () => {
  const placements = layoutCampus(fixture());
  const scene = buildCampusScene(placements);
  const ctx = { placements, buildings: scene.buildings.map((b) => ({ workspaceId: b.workspaceId, box: b.box, door: b.door, signIndex: b.signIndex })), monument: scene.monument, extentRadius: scene.extent.radius, canalRadius: scene.canalRadius, signCount: scene.signCount, plaza: scene.plaza };
  const sites = boardSites(ctx);
  assert.equal(sites.length, 5);
  assert.deepEqual(sites, boardSites(ctx));
  const width = boardWidth(scene.extent.radius);
  sites.forEach((s, i) => {
    const p = placements[i];
    // In front: on the side of the building its door is on.
    const toDoor = [scene.buildings[i].door.x - p.x, scene.buildings[i].door.z - p.z];
    assert.ok((s.x - p.x) * toDoor[0] + (s.z - p.z) * toDoor[1] > 0, `board ${i} is not in front of its building`);
    for (const other of scene.buildings) {
      const inside = s.x > other.box.min.x && s.x < other.box.max.x && s.z > other.box.min.z && s.z < other.box.max.z;
      assert.ok(!inside, `board ${i} stands inside a building`);
    }
    assert.ok(Math.hypot(s.x - scene.plaza.center.x, s.z - scene.plaza.center.z) > scene.plaza.outer, `board ${i} stands on the plaza`);
    for (const a of scene.plaza.avenues) { const dx = a.to[0] - a.from[0], dz = a.to[1] - a.from[1], l2 = dx * dx + dz * dz; const k = Math.max(0, Math.min(1, ((s.x - a.from[0]) * dx + (s.z - a.from[1]) * dz) / l2)); assert.ok(Math.hypot(s.x - a.from[0] - k * dx, s.z - a.from[1] - k * dz) > 3, `board ${i} stands on an avenue`); }
    assert.ok(Math.hypot(s.nx, s.nz) > 0.99);
    assert.equal(s.width, width);
  });
  const mesh = boardMesh(sites, ctx.buildings, scene.signCount);
  assert.ok(mesh.triangles > 0 && mesh.triangles < 1000, `cheap: ${mesh.triangles} triangles`);
  assert.equal(mesh.vertices.length / 13, mesh.triangles * 3);
  assert.ok(mesh.halos.length === sites.length * 8);
  // Wider campuses get wider boards, within limits.
  assert.ok(boardWidth(200) > boardWidth(30) && boardWidth(200) <= 13 && boardWidth(1) >= 9);
});

test('the sign cell copy equals the sign atlas layout', () => {
  for (const count of [1, 2, 5, 9, 24, 60]) for (let i = 0; i < count; i++) assert.deepEqual(signCell(count, i), signAtlasLayout(count).cell(i));
});

test('the billboards are a separate pass: only the focused building has a card unless the camera is near, and the main labels never see them', () => {
  const src = readFileSync(new URL('../src/CampusView.tsx', import.meta.url), 'utf8');
  const labelsMemo = src.slice(src.indexOf('const labels: StageLabel[]'), src.indexOf('// The billboards are a separate pass'));
  assert.ok(labelsMemo.length > 200 && !/billboard|boards/i.test(labelsMemo), 'the main label list must not contain billboards');
  assert.ok(/extraLabels=\{extraLabels\}/.test(src) && (src.match(/labelKeepOut="([^"]*)"/g) ?? []).join() === 'labelKeepOut="[data-keepout]"', 'billboards go to the extra pass; the campus keeps its own keep-out selector');
});
