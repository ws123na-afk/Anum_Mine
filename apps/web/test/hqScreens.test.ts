import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import { formatNumber, translate, type Locale } from '../src/lib/i18n.ts';
import {
  SCREEN_FIELDS, SCREEN_READS, SCREEN_STALE_MS, deskRecords, duration, monitorFace, monitorTexts, wallFace, wallTexts,
  type MonitorSource, type WallInput,
} from '../src/hq-screens/screenModel.ts';
import { MON_CAP, WALL_CAP, screenLayer, screenLayout } from '../src/hq-screens/screenSites.ts';
import { ScreenAtlas, SCREEN_MIN_MS, type ScreenFace } from '../src/hq-screens/screenDraw.ts';
import { startScreenFeed } from '../src/hq-screens/index.ts';
import { MeshBuilder, VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import { buildInteriorRoom } from '../src/hqAssets/interior/index.ts';
import type { SceneScreen } from '../src/lib/buildingScene.ts';
import type { WallModel } from '../src/lib/hqCommandWall.ts';
import type { StatusKey } from '../src/lib/campusModel.ts';

// The screens in the rooms show only fixed messages and real record fields (docs/ANUM_MINE_HQ_LIVE_SCREENS.md). Every name here is plainly fake.
const NOW = Date.parse('2026-10-27T10:00:00Z');
const FRESH = '2026-10-27T09:59:40Z';
const LEAK = 'ZZLEAK';
const tr = (locale: Locale) => (key: Parameters<typeof translate>[1], vars?: Parameters<typeof translate>[2]) => translate(locale, key, vars);

function track<T extends object>(value: T, path: string, log: Set<string>): T {
  return new Proxy(value, {
    get(target, key, receiver) {
      const out = Reflect.get(target, key, receiver);
      if (typeof key === 'symbol' || key === 'length' || typeof out === 'function') return out;
      const next = Array.isArray(target) ? `${path}[]` : `${path}.${key}`;
      log.add(next);
      return out && typeof out === 'object' ? track(out as object, next, log) : out;
    },
  });
}
/** Reads of the proxy, allowing the `byState.*` family. */
const outside = (log: Set<string>) => [...log].filter((p) => !SCREEN_READS.includes(p) && !(p.startsWith('wall.agents.byState.') && SCREEN_READS.includes('wall.agents.byState.*')));

const item = (key: string, stage: string, desk: string | null, since = FRESH, extra: Record<string, unknown> = {}) => ({
  key, kind: 'task', id: `${LEAK}ID-${key}`, title: `${LEAK}TITLE-${key}`, stage, status: `${LEAK}STATUS`, since, created_at: since,
  location: desk ? { building_id: 'b', floor_id: 'f', room_id: 'r', room_name: `${LEAK}ROOMNAME`, desk_id: desk, agent_id: `${LEAK}A`, agent_name: `${LEAK}AGENT` } : null,
  location_reason: null, stuck: null, waiting: null, ...extra,
});
const snapshot = (items: unknown[], tasks = 'connected'): OfficeFlowSnapshot => ({
  version: 1, workspace_id: 'ws', as_of: FRESH, cursor: null, thresholds: {}, sources: { tasks, approvals: 'connected', leads: 'connected', orders: 'not_connected' },
  items, rooms: {}, totals: {}, truncated: false,
} as unknown as OfficeFlowSnapshot);

const byState = (over: Partial<Record<StatusKey, number>> = {}) => ({ working: 0, meeting: 0, queued: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0, ...over });
const wallModel = (over: Partial<WallModel> = {}): WallModel => ({
  tiles: [
    { id: 'openTasks', state: 'ready', value: 7, atLeast: false }, { id: 'blocked', state: 'ready', value: 1, atLeast: false },
    { id: 'approvals', state: 'ready', value: 2, atLeast: true }, { id: 'running', state: 'ready', value: 3, atLeast: false },
  ],
  agents: { state: 'ready', total: 5, byState: byState({ working: 2, idle: 3 }) }, asOf: FRESH, ...over,
} as WallModel);
const tyre = { vehicles: 1031, fitted: 2806, inStock: 0, belowMinimum: 20, overdueInspections: 938, dueNext30: 1970, criticalAlerts: 4, warningAlerts: 5 };
const wallInput = (over: Partial<WallInput> = {}): WallInput => ({
  wall: wallModel(), flow: { status: 'ready', snapshot: snapshot([item('a', 'in_office', 'd1'), item('b', 'blocked', 'd2', '2026-10-27T09:58:00Z'), item('c', 'done', null, '2026-10-27T09:50:00Z')]) },
  tyre: null, buildingName: 'OKNAME Pulse', live: true, ...over,
});
const source = (visual: MonitorSource['visual'], status: StatusKey | null, over: Partial<MonitorSource> = {}): MonitorSource => ({
  visual,
  presence: status ? { status, stale: false, observedAt: FRESH, ...(({ name: `${LEAK}NAME`, taskTitles: [`${LEAK}TITLE`], agentId: `${LEAK}AGENTID` }) as object) } as MonitorSource['presence'] : null,
  record: { kind: 'task', stage: 'in_office', since: '2026-10-27T09:55:00Z', waitingSince: null, ...(({ title: `${LEAK}TITLE` }) as object) } as MonitorSource['record'], ...over,
});

test('the screens read only the allowed record fields', () => {
  const log = new Set<string>();
  const input = wallInput({ tyre: { state: 'ready', data: tyre } });
  wallFace({ ...input, wall: track(input.wall, 'wall', log), flow: track(input.flow!, 'flow', log), tyre: track(input.tyre!, 'tyre', log) }, NOW, 'en', tr('en'));
  const s = source('working', 'working');
  monitorFace({ visual: s.visual, presence: track(s.presence!, 'source.presence', log), record: track(s.record!, 'source.record', log) }, NOW, 'en', tr('en'));
  deskRecords(track(snapshot([item('a', 'in_office', 'd1', FRESH, { waiting: { since: FRESH, approval_id: `${LEAK}AP` } })]), 'flow.snapshot', log));
  assert.deepEqual(outside(log), [], 'a property outside SCREEN_READS was read');
  assert.ok(log.has('tyre.data.dueNext30') && log.has('flow.snapshot.items[].since') && log.has('source.presence.observedAt') && log.has('wall.tiles[].value'), 'the proxy sees the reads that matter');
});

test('no private text reaches a screen, in English or Arabic', () => {
  for (const locale of ['en', 'ar'] as const) {
    const texts = [
      ...wallTexts(wallFace(wallInput({ tyre: { state: 'ready', data: tyre } }), NOW, locale, tr(locale))),
      ...wallTexts(wallFace(wallInput({ tyre: { state: 'not_connected' } }), NOW, locale, tr(locale))),
      ...(['working', 'queued', 'meeting', 'waiting', 'blocked', 'idle', 'paused', 'unknown'] as const).flatMap((v) => {
        const status = v === 'waiting' ? 'waiting_approval' : v;
        return monitorTexts(monitorFace(source(v, status as StatusKey), NOW, locale, tr(locale)));
      }),
    ];
    const ids = new Set<string>(SCREEN_FIELDS.map((f) => f.id));
    for (const { field, text } of texts) {
      assert.ok(ids.has(field), `unknown field ${field}`);
      assert.ok(!text.includes(LEAK), `${field} leaks: ${text}`);
      assert.ok(!/undefined|NaN|null|\[object/.test(text), `${field} prints a bad value: ${text}`);
    }
  }
});

test('the wall shows the numbers of the Operations wall block, the flow by type and stage, and the tyre figures as the cards label them', () => {
  const face = wallFace(wallInput({ tyre: { state: 'ready', data: tyre } }), NOW, 'en', tr('en'));
  assert.deepEqual(face.tiles.map((x) => [x.label, x.text]), [['Open tasks', '7'], ['Blocked work', '1'], ['Pending approvals', '≥ 2'], ['Running now', '3']]);
  assert.equal(face.agents.text, '5');
  assert.deepEqual(face.agents.parts.map((p) => p.text), ['Working 2', 'Idle 3']);
  assert.equal(face.flowRows[0], 'Task · In an office · 20s');
  assert.equal(face.flowRows.length, 3);
  assert.ok(face.flowRows.every((r) => !r.includes(LEAK)));
  assert.deepEqual(face.tyre!.items.map((x) => [x.label, x.text]), [['Vehicles', '1031'], ['Tyres fitted', '2806'], ['Tyres in stock', '0'], ['Below minimum', '20'], ['Overdue inspections', '938'], ['Due in 30 days', '1970']]);
  assert.equal(face.tyre!.title, 'Tyre fleet');
  assert.equal(face.updated, `Updated ${new Date(FRESH).toLocaleTimeString()}`);
  // No tyre block for a building without the tyre panel; a lower bound for a tyre stock that the page omits is left out, never invented.
  assert.equal(wallFace(wallInput(), NOW, 'en', tr('en')).tyre, null);
  assert.equal(wallFace(wallInput({ tyre: { state: 'ready', data: { ...tyre, inStock: null } } }), NOW, 'en', tr('en')).tyre!.items.length, 5);
});

test('an unavailable source says so and shows no number', () => {
  const off = wallFace(wallInput({ tyre: { state: 'not_connected' } }), NOW, 'en', tr('en'));
  assert.equal(off.tyre!.note, 'OKNAME Pulse: Not connected');
  assert.equal(off.tyre!.items.length, 0);
  assert.equal(wallFace(wallInput({ tyre: { state: 'forbidden' } }), NOW, 'en', tr('en')).tyre!.note, 'OKNAME Pulse: No access');
  const down = wallFace(wallInput({
    wall: wallModel({ tiles: wallModel().tiles.map((x) => ({ ...x, state: 'not_connected', value: null })), agents: { state: 'not_connected' }, asOf: null }),
    flow: { status: 'error', snapshot: null },
  }), NOW, 'en', tr('en'));
  assert.deepEqual(down.tiles.map((x) => x.text), ['Not connected', 'Not connected', 'Not connected', 'Not connected']);
  assert.equal(down.status, 'off');
  assert.equal(down.statusText, 'Not connected');
  assert.equal(down.flowNote, 'Not connected');
  assert.equal(down.updated, null);
});

test('Live shows only while the stream is connected, Stale after 90 seconds', () => {
  assert.equal(wallFace(wallInput({ live: true }), NOW, 'en', tr('en')).statusText, 'Live');
  const quiet = wallFace(wallInput({ live: false }), NOW, 'en', tr('en'));
  assert.equal(quiet.status, 'ok');
  assert.equal(quiet.statusText, null);
  const late = NOW + SCREEN_STALE_MS + 1000;
  const stale = wallFace(wallInput({ live: true }), late, 'en', tr('en'));
  assert.equal(stale.status, 'stale');
  assert.equal(stale.statusText, 'Stale');
});

test('the wall is right to left with Arabic labels and digits as the page', () => {
  const face = wallFace(wallInput({ tyre: { state: 'ready', data: tyre } }), NOW, 'ar', tr('ar'));
  assert.equal(face.rtl, true);
  assert.equal(face.tiles[0].label, 'مهام مفتوحة');
  assert.equal(face.tiles[0].text, formatNumber(7, 'ar'), 'digits follow the page (the Operations wall block uses the same formatter)');
  assert.equal(face.statusText, 'مباشر');
});

test('a monitor shows the record behind the state, its real elapsed time, and a scan only while fresh work runs', () => {
  const working = monitorFace(source('working', 'working'), NOW, 'en', tr('en'));
  assert.deepEqual([working.headline, working.record, working.elapsed, working.scan], ['Working', 'Task run', 'Running 5m', true]);
  assert.match(working.foot!, /^Verified /);
  const noStart = monitorFace(source('working', 'working', { record: null }), NOW, 'en', tr('en'));
  assert.equal(noStart.elapsed, null, 'no record, no invented time');
  const staleWork = monitorFace(source('working', 'working', { presence: { status: 'working', stale: true, observedAt: FRESH } }), NOW, 'en', tr('en'));
  assert.equal(staleWork.scan, false);
  const meeting = monitorFace(source('meeting', 'meeting'), NOW, 'en', tr('en'));
  assert.deepEqual([meeting.record, meeting.elapsed, meeting.scan], ['Meeting', null, true]);
  const approval = monitorFace(source('waiting', 'waiting_approval', { record: { kind: 'task', stage: 'waiting_human', since: FRESH, waitingSince: '2026-10-27T09:30:00Z' } }), NOW, 'en', tr('en'));
  assert.deepEqual([approval.headline, approval.record, approval.elapsed, approval.scan], ['Waiting for approval', 'Approval', 'Waiting 30m', false]);
  for (const [visual, status, text] of [['idle', 'idle', 'Idle'], ['paused', 'paused', 'Paused'], ['blocked', 'blocked', 'Blocked']] as const) {
    const f = monitorFace(source(visual, status), NOW, 'en', tr('en'));
    assert.equal(f.headline, text);
    assert.equal(f.scan, false);
    assert.equal(f.elapsed, null);
  }
  const off = monitorFace(source('offshift', 'working'), NOW, 'en', tr('en'));
  assert.deepEqual([off.headline, off.tone, off.scan, off.foot], ['Off shift', 'off', false, null]);
  assert.equal(monitorFace(source('assigned', null), NOW, 'en', tr('en')).headline, 'Not connected');
  assert.equal(monitorFace(source('unknown', 'working', { presence: { status: 'working', stale: true, observedAt: FRESH } }), NOW, 'en', tr('en')).headline, 'Stale');
});

test('durations count from a real start and refuse a future one', () => {
  const en = tr('en');
  assert.equal(duration(45_000, en, 'en'), '45s');
  assert.equal(duration(12 * 60_000, en, 'en'), '12m');
  assert.equal(duration((3 * 60 + 5) * 60_000, en, 'en'), '3h 05m');
  assert.equal(duration((50 * 60) * 60_000, en, 'en'), '2d 2h');
  assert.equal(duration(-1, en, 'en'), null);
});

test('deskRecords keeps the newest open task per desk and nothing when the tasks source is down', () => {
  const records = deskRecords(snapshot([item('a', 'in_office', 'd1', '2026-10-27T09:00:00Z'), item('b', 'in_office', 'd1', '2026-10-27T09:30:00Z'), item('c', 'done', 'd2'), item('d', 'in_office', null)]));
  assert.deepEqual([...records.keys()], ['d1']);
  assert.equal(records.get('d1')!.since, '2026-10-27T09:30:00Z');
  assert.equal(deskRecords(snapshot([item('a', 'in_office', 'd1')], 'not_connected')).size, 0);
  assert.equal(deskRecords(null).size, 0);
});

test('the atlas stays small: caps on cells and at most 8 MB', () => {
  const big = screenLayout(500, 50);
  assert.ok(big.width * big.height * 4 <= 8 * 1024 * 1024);
  const last = big.cell('monitor', MON_CAP - 1);
  assert.ok(last.y + last.h <= big.height && last.x + last.w <= big.width);
  const wall = big.cell('wall', WALL_CAP - 1);
  assert.ok(wall.y + wall.h <= big.height && wall.y >= Math.ceil(MON_CAP / 8) * 128);
  const small = screenLayout(3, 1);
  assert.ok(small.width * small.height * 4 <= 2048 * 512 * 4);
  for (const c of [small.cell('monitor', 0), small.cell('monitor', 2), small.cell('wall', 0)]) assert.ok(c.u0 >= 0 && c.u1 <= 1 && c.v0 >= 0 && c.v1 <= 1 && c.u0 < c.u1 && c.v0 < c.v1);
});

/** The room kit notes its monitors and the video wall; each face looks into the room whichever wall the door is in. */
for (const doorSide of ['south', 'north', 'east', 'west'] as const) {
  test(`the operations room notes a wall and a monitor facing the room (door ${doorSide})`, () => {
    const b = new MeshBuilder();
    const frame = { x: 10, y: 0, z: 20, width: 9, depth: 7 };
    buildInteriorRoom(b, 'operations', frame, { seed: 'r', doorSide, stations: [{ id: 'desk-1', x: 14, z: 24, rot: 0, state: 'working', occupantSeed: 'x' }] });
    const wall = b.screens.find((s) => s.kind === 'wall');
    const mon = b.screens.find((s) => s.kind === 'monitor');
    assert.ok(wall && mon, 'both are noted');
    assert.equal(mon!.id, 'desk-1');
    const mid = { x: frame.x + frame.width / 2, z: frame.z + frame.depth / 2 };
    for (const s of [wall!, mon!]) {
      const p = s.p;
      const u = [p[3] - p[0], p[4] - p[1], p[5] - p[2]], v = [p[9] - p[0], p[10] - p[1], p[11] - p[2]];
      const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      assert.ok(Math.abs(n[1]) < 1e-6, 'upright');
      assert.ok(p[7] > p[1] && p[10] > p[1], 'top is above the bottom');
    }
    // The wall's face points to the middle of the room (the viewer stands there).
    const p = wall!.p;
    const u = [p[3] - p[0], p[5] - p[2]], cx = (p[0] + p[6]) / 2, cz = (p[2] + p[8]) / 2;
    const nx = -u[1], nz = u[0]; // (u x up) in the plan: points to the viewer side when counter clockwise from the viewer
    const toward = (mid.x - cx) * nx + (mid.z - cz) * nz;
    assert.ok(toward !== 0);
    // Winding bottom left -> bottom right -> top right is counter clockwise seen by the viewer exactly when the normal points at the viewer.
    const e1 = [p[3] - p[0], p[4] - p[1], p[5] - p[2]], e2 = [p[6] - p[3], p[7] - p[4], p[8] - p[5]];
    const n3 = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
    assert.ok((mid.x - cx) * n3[0] + (mid.z - cz) * n3[2] > 0, 'the wall face looks into the room');
    const m = mon!.p;
    const f1 = [m[3] - m[0], m[4] - m[1], m[5] - m[2]], f2 = [m[6] - m[3], m[7] - m[4], m[8] - m[5]];
    const mn = [f1[1] * f2[2] - f1[2] * f2[1], f1[2] * f2[0] - f1[0] * f2[2], f1[0] * f2[1] - f1[1] * f2[0]];
    // The monitor faces the person at the desk: towards -z of its own frame, here the +z side of the desk (rot 0 + 2 turns).
    assert.ok(Math.hypot(mn[0], mn[2]) > 0 && Math.abs(mn[1]) < 1e-9);
  });
}

test('the layer lays one textured quad per screen, a frame for a mounted wall, and keeps to the caps', () => {
  const rec = (kind: 'monitor' | 'wall', id: string, room: string, n: number): SceneScreen => ({ kind, id, roomId: room, floorIndex: 0, p: [n, 1, 0, n + 0.5, 1, 0, n + 0.5, 1.3, 0, n, 1.3, 0] });
  const screens: SceneScreen[] = [rec('wall', '', 'r1', 0), ...Array.from({ length: MON_CAP + 5 }, (_, i) => rec('monitor', `d${i}`, 'r1', i + 1)), { ...rec('monitor', 'other', 'r9', 1), floorIndex: 1 }];
  const layer = screenLayer(screens, 0, [{ id: 'r2', x: 0, z: 0, width: 8, depth: 6, doorSide: 'south' }, { id: 'r1', x: 0, z: 0, width: 8, depth: 6 }], 0.06);
  assert.equal(layer.sites.filter((s) => s.kind === 'monitor').length, MON_CAP);
  assert.equal(layer.sites.filter((s) => s.kind === 'wall').length, 2, 'the video wall of r1 and the mounted wall of r2; r1 has no second one');
  assert.equal(layer.sites.some((s) => s.id === 'other'), false, 'another floor has no screens here');
  const quads = layer.items.find((i) => i.id === 'screens')!;
  assert.equal(quads.vertices.length / VERTEX_FLOATS, layer.sites.length * 6);
  assert.ok(layer.items.some((i) => i.id === 'screen-frames'));
  for (let t = 0; t < quads.vertices.length; t += VERTEX_FLOATS) assert.equal(quads.vertices[t + 10], 16, 'the screen class');
});

// ---- the atlas draws only changed cells, at most once a second each

function fakeCtx() {
  const calls = { fills: 0 };
  const ctx = new Proxy({ fillStyle: '', font: '', textAlign: 'left', textBaseline: 'alphabetic', direction: 'ltr', globalAlpha: 1 } as Record<string, unknown>, {
    get(target, key: string) {
      if (key in target) return target[key];
      if (key === 'measureText') return () => ({ width: 10 });
      if (key === 'fillRect') return () => { calls.fills += 1; };
      return () => undefined;
    },
    set(target, key: string, value) { target[key] = value; return true; },
  });
  return { ctx, calls };
}
const face = (headline: string): ScreenFace => ({ kind: 'monitor', face: { tone: 'work', headline, record: null, elapsed: null, foot: null, scan: false, rtl: false }, near: true, aspect: 1.8, animate: true, phase: 0 });

test('the atlas redraws only what changed and holds a cell back for a second', () => {
  const { ctx, calls } = fakeCtx();
  const atlas = new ScreenAtlas(screenLayout(2, 0), () => ({ canvas: {} as HTMLCanvasElement, ctx: ctx as never }));
  const base = calls.fills;
  assert.equal(atlas.update(new Map([['monitor:0', face('Working')], ['monitor:1', face('Idle')]]), 1000).drawn, 2);
  assert.equal(atlas.version, 1);
  assert.equal(atlas.update(new Map([['monitor:0', face('Working')], ['monitor:1', face('Idle')]]), 1100).drawn, 0, 'unchanged cells are not drawn');
  assert.equal(atlas.version, 1);
  const held = atlas.update(new Map([['monitor:0', face('Blocked')], ['monitor:1', face('Idle')]]), 1500);
  assert.equal(held.drawn, 0);
  assert.equal(held.nextDueMs, SCREEN_MIN_MS - 500);
  assert.equal(atlas.update(new Map([['monitor:0', face('Blocked')], ['monitor:1', face('Idle')]]), 2001).drawn, 1);
  assert.equal(atlas.version, 2);
  assert.ok(calls.fills > base);
  assert.equal(atlas.drawn.get('monitor:0')!.kind, 'monitor');
});

test('reduced motion keeps the content and stops the scan line', () => {
  const a = { ...face('Working'), face: { ...face('Working').kind === 'monitor' ? (face('Working') as Extract<ScreenFace, { kind: 'monitor' }>).face : (null as never), scan: true } } as ScreenFace;
  const { ctx } = fakeCtx();
  const atlas = new ScreenAtlas(screenLayout(1, 0), () => ({ canvas: {} as HTMLCanvasElement, ctx: ctx as never }));
  assert.equal(atlas.update(new Map([['monitor:0', { ...(a as object), animate: false, phase: 0 } as ScreenFace]]), 1000).drawn, 1);
  // With motion off the phase no longer changes the picture, so a new phase is not a new draw.
  assert.equal(atlas.update(new Map([['monitor:0', { ...(a as object), animate: false, phase: 3 } as ScreenFace]]), 3000).drawn, 0);
  assert.equal(atlas.update(new Map([['monitor:0', { ...(a as object), animate: true, phase: 1 } as ScreenFace]]), 5000).drawn, 1);
});

test('every field of the table is documented, and the docs list no other', () => {
  const doc = readFileSync(new URL('../../../docs/ANUM_MINE_HQ_LIVE_SCREENS.md', import.meta.url), 'utf8');
  for (const f of SCREEN_FIELDS) assert.ok(doc.includes(`\`${f.id}\``), `docs miss ${f.id}`);
  const rows = [...doc.matchAll(/^\| `((?:wall|monitor)\.[a-z.]+)` \|/gm)].map((m) => m[1]);
  assert.deepEqual(rows.sort(), SCREEN_FIELDS.map((f) => f.id).sort());
});

test('a flow row with no usable age prints no empty age', () => {
  const face = wallFace(wallInput(), Date.parse(FRESH) - 600_000, 'en', tr('en'));
  assert.ok(face.flowRows.length > 0);
  for (const row of face.flowRows) assert.ok(!row.endsWith('·') && !row.endsWith(' '), row);
  assert.equal(face.flowRows[0], 'Task · In an office');
});

test('the feed keeps drawing when the page clock is pinned: the one second rule uses its own clock', () => {
  const { ctx } = fakeCtx();
  const layer = screenLayer([{ kind: 'monitor', id: 'd1', roomId: 'r', floorIndex: 0, p: [0, 1, 0, 1, 1, 0, 1, 2, 0, 0, 2, 0] }], 0, [], 0);
  let mono = 0;
  let text = 'Working';
  const feed = startScreenFeed({
    layer, tickMs: 1_000_000, now: () => NOW, tickClock: () => mono, make: () => ({ canvas: {} as HTMLCanvasElement, ctx: ctx as never }),
    read: () => ({ monitors: new Map([['d1', { ...source('working', 'working'), visual: text === 'Working' ? 'working' : 'blocked', presence: { status: text === 'Working' ? 'working' : 'blocked', stale: false, observedAt: FRESH } } as MonitorSource]]), wall: null, eye: [0, 1, 0], reducedMotion: true, now: NOW }),
    onDraw: () => undefined,
  });
  const first = feed.atlas.version;
  assert.equal(first, 1);
  text = 'Blocked';
  mono = 400;
  feed.touch();
  assert.equal(feed.atlas.version, first, 'held back inside the second');
  mono = 1500;
  feed.touch();
  assert.equal(feed.atlas.version, first + 1, 'drawn once the monotonic second passed, although Date.now never moved');
  feed.stop();
});
