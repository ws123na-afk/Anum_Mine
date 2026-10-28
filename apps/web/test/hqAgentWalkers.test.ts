import assert from 'node:assert/strict';
import { test } from 'node:test';
import { seeded } from '../src/lib/hqGeometry.ts';
import { registryKey } from '../src/lib/campusRegistry.ts';
import { layoutCampus, type CampusBuilding } from '../src/lib/campusModel.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { SPOKE_START } from '../src/lib/campusSpokes.ts';
import {
  LINGER_MS, MAX_WALKERS, PAUSE_S, QUEUE_TTL_MS, STATIC_MS, WALKER_KINDS, WalkerPlanner, becauseLine, buildRoute, eligibleState, endedRecords, makeRecord, movedRecords,
  newWalkPose, restPose, sceneKeyOf, startedRecords, timeText, topologyOf, vanishedMeetings, walkPoseAt,
  type MeetingEntry, type SeatEntry, type SiteGeo, type WalkSnapshot, type WalkRoute, type WalkerRecord,
} from '../src/lib/hqWalkers.ts';
import { CATALOGS } from '../src/lib/i18n.ts';

const NOW = Date.parse('2026-10-27T14:10:00Z');
const iso = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000).toISOString();
const tr = (locale: 'en' | 'ar') => (key: string, vars: Record<string, string> = {}) => {
  const m = (CATALOGS[locale] as Record<string, unknown>)[key];
  assert.equal(typeof m, 'string', key);
  return (m as string).replace(/\{(\w+)\}/g, (_x, k: string) => vars[k] ?? `{${k}}`);
};

const registry = [
  { id: 'hq', floors: [{ rooms: [{ id: 'r-hq-ops', kind: 'office' }, { id: 'r-hq-meet', kind: 'meeting' }], desks: [{ id: 'd1', roomId: 'r-hq-ops', agentId: 'ag1' }, { id: 'd2', roomId: 'r-hq-ops', agentId: 'ag2' }, { id: 'd9', roomId: 'r-hq-ops', agentId: null }] }] },
  { id: 'tp', floors: [{ rooms: [{ id: 'r-tp-meet', kind: 'meeting' }, { id: 'r-tp-work', kind: 'workshop' }], desks: [{ id: 'd3', roomId: 'r-tp-work', agentId: 'ag3' }, { id: 'd4', roomId: 'r-tp-work', agentId: 'ag4' }] }] },
  { id: 'co', floors: [{ rooms: [{ id: 'r-co', kind: 'office' }], desks: [{ id: 'd5', roomId: 'r-co', agentId: 'ag5' }, { id: 'd6', roomId: 'r-co', agentId: 'ag1' }] }] },
];
const topo = topologyOf('w', registry);
const HQ = sceneKeyOf('w', 'hq'), TP = sceneKeyOf('w', 'tp'), CO = sceneKeyOf('w', 'co');
const seat = (deskId: string | null, eligible = true, verifiedAt = iso(1)): SeatEntry => ({ deskId, verifiedAt, eligible });
const seats = (o: Record<string, SeatEntry>) => new Map(Object.entries(o));
const meeting = (roomId: string, agentIds: string[], startedAt: string | null = iso(2)): MeetingEntry => ({ roomId, agentIds, startedAt });

test('the scene key of a registry building is the campus registry key', () => {
  assert.equal(sceneKeyOf('w', 'b'), registryKey('w', 'b'));
});

test('topology: a workspace with fewer than two registry buildings has nobody who can walk; an agent seated in two buildings has no home', () => {
  assert.equal(topologyOf('w', [registry[0]]).known.size, 0);
  assert.equal(topologyOf('w', []).known.size, 0);
  assert.equal(topo.home.get('ag3'), TP);
  assert.equal(topo.home.get('ag1'), undefined, 'ag1 has desks in two buildings: the data cannot say where it sits');
  assert.equal(topo.roomBuilding.get('r-tp-meet'), TP);
  assert.equal(topo.homeRoomKind.get('ag3'), 'workshop');
});

test('no record, no walker: first answers, unchanged answers, unreadable feeds and ineligible agents produce nothing', () => {
  const s: WalkSnapshot = { meetings: new Map([['m1', meeting('r-tp-meet', ['ag2'])]]), seats: seats({ ag2: seat('d2') }) };
  assert.deepEqual(startedRecords(null, s, topo, NOW), []);
  assert.deepEqual(startedRecords({ meetings: null, seats: s.seats }, s, topo, NOW), []);
  assert.deepEqual(startedRecords(s, s, topo, NOW), []);
  assert.deepEqual(startedRecords({ meetings: new Map(), seats: s.seats }, { meetings: null, seats: s.seats }, topo, NOW), []);
  assert.deepEqual(startedRecords({ meetings: new Map(), seats: null }, { meetings: s.meetings, seats: null }, topo, NOW), []);
  assert.deepEqual(movedRecords(null, s, topo, NOW), []);
  assert.deepEqual(movedRecords(s, s, topo, NOW), []);
  for (const kind of ['paused', 'unknown'] as const) assert.equal(eligibleState({ agentId: 'a', status: kind }), false);
  assert.equal(eligibleState({ agentId: 'desk:x', status: 'working' }), false);
  assert.equal(eligibleState({ agentId: 'a', status: 'working', stale: true }), false);
  assert.equal(eligibleState({ agentId: 'a', status: 'working', reason: 'agent_not_visible' }), false);
  assert.equal(eligibleState({ agentId: 'a', status: 'meeting' }), true);
  // only "working" with no place: nothing can be said about where the agent went
  const working: WalkSnapshot = { meetings: new Map(), seats: seats({ ag2: seat('d2') }) };
  assert.deepEqual(startedRecords(working, { ...working, seats: seats({ ag2: seat('d2') }) }, topo, NOW), []);
});

test('a meeting that started in another building moves its participant from the desk building to the meeting room building', () => {
  const before: WalkSnapshot = { meetings: new Map(), seats: seats({ ag2: seat('d2'), ag3: seat('d3'), ag4: seat('d4', false) }) };
  const after: WalkSnapshot = { meetings: new Map([['m1', meeting('r-tp-meet', ['ag2', 'ag3', 'ag4', 'ag1', 'ghost'])]]), seats: before.seats };
  const rs = startedRecords(before, after, topo, NOW);
  // ag3 already sits in the meeting's building, ag4 is not eligible, ag1 has no single home, ghost has no presence
  assert.deepEqual(rs.map((r) => [r.kind, r.agentId, r.from, r.to, r.placeId]), [['meeting_started', 'ag2', HQ, TP, 'r-tp-meet']]);
  assert.equal(rs[0].at, iso(2));
  // a meeting with no stored start time, or in a room the registry does not hold, or without a room building, is not an event
  assert.deepEqual(startedRecords(before, { ...after, meetings: new Map([['m2', meeting('r-tp-meet', ['ag2'], null)]]) }, topo, NOW), []);
  assert.deepEqual(startedRecords(before, { ...after, meetings: new Map([['m3', meeting('r-nowhere', ['ag2'])]]) }, topo, NOW), []);
  assert.deepEqual(startedRecords(before, { ...after, meetings: new Map([['m4', meeting('r-tp-meet', ['ag2'], iso(120))]]) }, topo, NOW), [], 'older than the live window is history');
  assert.deepEqual(startedRecords(before, { ...after, meetings: new Map([['m5', meeting('r-tp-meet', ['ag2'], new Date(NOW + 3_600_000).toISOString())]]) }, topo, NOW), [], 'a start in the future is refused');
});

test('a meeting that comes back after a pause is not a new start', () => {
  const before: WalkSnapshot = { meetings: new Map(), seats: seats({ ag2: seat('d2') }) };
  const after: WalkSnapshot = { meetings: new Map([['m1', meeting('r-tp-meet', ['ag2'])]]), seats: before.seats };
  assert.equal(startedRecords(before, after, topo, NOW).length, 1);
  assert.equal(startedRecords(before, after, topo, NOW, new Set(['m1'])).length, 0);
  assert.equal(startedRecords(before, after, topo, NOW, new Set(['other'])).length, 1);
});

test('a meeting that is over (and only one that is over) sends its participants back', () => {
  const entry = meeting('r-tp-meet', ['ag2', 'ag3']);
  const before: WalkSnapshot = { meetings: new Map([['m1', entry]]), seats: seats({}) };
  const after: WalkSnapshot = { meetings: new Map(), seats: seats({}) };
  assert.deepEqual(vanishedMeetings(before, after), ['m1']);
  assert.deepEqual(vanishedMeetings(null, after), []);
  assert.deepEqual(vanishedMeetings(before, { meetings: null, seats: null }), []);
  const s = seats({ ag2: seat('d2'), ag3: seat('d3') });
  const done = endedRecords('m1', entry, { status: 'finished', ended_at: iso(1) }, s, topo, NOW);
  assert.deepEqual(done.map((r) => [r.kind, r.agentId, r.from, r.to]), [['meeting_ended', 'ag2', TP, HQ]]);
  for (const status of ['paused', 'active', 'scheduled', undefined, 7]) assert.deepEqual(endedRecords('m1', entry, { status, ended_at: iso(1) }, s, topo, NOW), [], String(status));
  assert.deepEqual(endedRecords('m1', entry, { status: 'finished', ended_at: null }, s, topo, NOW), []);
  assert.deepEqual(endedRecords('m1', entry, null, s, topo, NOW), []);
});

test('a desk that moved to another building is an event only when the presence answer says so', () => {
  const before: WalkSnapshot = { meetings: null, seats: seats({ ag2: seat('d2'), ag3: seat('d3'), ag4: seat('d4') }) };
  const after: WalkSnapshot = { meetings: null, seats: seats({ ag2: seat('d3', true, iso(1)), ag3: seat('d4'), ag4: seat('d5', false) }) };
  const rs = movedRecords(before, after, topo, NOW);
  assert.deepEqual(rs.map((r) => [r.kind, r.agentId, r.from, r.to, r.placeId]), [['desk_moved', 'ag2', HQ, TP, TP]]);
  assert.equal(rs[0].at, iso(1));
  // ag1 has desks in two buildings (a day and a night desk): the presence answer may report either, so a change between them is no move
  assert.deepEqual(movedRecords({ meetings: null, seats: seats({ ag1: seat('d1') }) }, { meetings: null, seats: seats({ ag1: seat('d6') }) }, topo, NOW), []);
  assert.deepEqual(movedRecords(before, { meetings: null, seats: seats({ ag2: seat(null) }) }, topo, NOW), []);
  assert.deepEqual(movedRecords(before, { meetings: null, seats: seats({ ag2: seat('unknown-desk') }) }, topo, NOW), []);
});

test('makeRecord refuses anything outside the allowed fields', () => {
  const ok = { kind: 'desk_moved', agentId: 'ag2', from: HQ, to: TP, at: iso(1), placeId: TP, recordKey: 'k' };
  assert.ok(makeRecord(ok, topo, NOW));
  const bad: Array<Partial<typeof ok>> = [
    { kind: 'invented' }, { agentId: '' }, { agentId: 'desk:abc' }, { agentId: 4 as never }, { from: TP }, { from: 'elsewhere' }, { to: 'elsewhere' },
    { at: 'yesterday' }, { at: iso(500) }, { at: undefined }, { placeId: HQ }, { recordKey: '' },
  ];
  for (const b of bad) assert.equal(makeRecord({ ...ok, ...b }, topo, NOW), null, JSON.stringify(b));
  assert.equal(makeRecord({ ...ok, kind: 'meeting_started', placeId: 'r-hq-meet' }, topo, NOW), null, 'the room must be in the destination');
  assert.ok(makeRecord({ ...ok, kind: 'meeting_started', placeId: 'r-tp-meet' }, topo, NOW));
  assert.equal(makeRecord({ ...ok, kind: 'meeting_ended', placeId: 'r-tp-meet' }, topo, NOW), null, 'ended: the room must be in the source building');
});

test('property: private text can never reach a string, whatever a meeting, task or agent record contains', () => {
  const rnd = seeded('private');
  const secrets = ['Q3 layoffs plan', 'password=hunter2', 'acquire Contoso for 4M', 'CEO transcript: we should', '<img onerror=x>', 'owner@example.com'];
  const nameOf = { room: (id: string) => ({ 'r-tp-meet': 'Meeting Room A' }[id] ?? ''), building: (k: string) => ({ [TP]: 'Tyre Pulse' }[k] ?? '') };
  for (let i = 0; i < 300; i++) {
    const secret = secrets[Math.floor(rnd() * secrets.length)] + String(Math.floor(rnd() * 1e6));
    // titles, prompts and transcripts ride along on the meeting object and on presence records: only ids, a room and times are read
    const m = { roomId: 'r-tp-meet', agentIds: ['ag2'], startedAt: iso(2), title: secret, transcript: secret, prompt: secret, summary: secret } as unknown as MeetingEntry;
    const s = { deskId: 'd2', verifiedAt: iso(1), eligible: true, taskTitle: secret, name: secret } as unknown as SeatEntry;
    const before: WalkSnapshot = { meetings: new Map(), seats: seats({ ag2: s }) };
    const after: WalkSnapshot = { meetings: new Map([['m1', m]]), seats: before.seats };
    const records = [...startedRecords(before, after, topo, NOW), ...movedRecords(before, { meetings: null, seats: seats({ ag2: { ...s, deskId: 'd3' } as SeatEntry }) }, topo, NOW),
      ...endedRecords('m1', m, { status: 'finished', ended_at: iso(1), title: secret } as never, before.seats, topo, NOW)];
    assert.ok(records.length >= 2);
    for (const r of records) {
      assert.deepEqual(Object.keys(r).sort(), ['agentId', 'at', 'from', 'kind', 'placeId', 'recordId', 'to']);
      for (const locale of ['en', 'ar'] as const) {
        const line = becauseLine(r, nameOf, locale, tr(locale));
        assert.ok(!line.includes(secret) && !secrets.some((x) => line.includes(x)), line);
        assert.ok(!/[\u2013\u2014]/.test(line));
      }
      assert.ok(!JSON.stringify(r).includes(secret));
    }
  }
});

test('the Because line names the record kind, the time and a registry place, in English and Arabic', () => {
  const names = { room: (id: string) => (id === 'r-tp-meet' ? 'Meeting Room A' : ''), building: (k: string) => (k === TP ? 'Tyre Pulse' : '') };
  const at = '2026-10-27T10:05:00Z';
  const time = timeText(at, 'en');
  assert.match(time, /^\d\d:\d\d$/);
  assert.equal(becauseLine({ kind: 'meeting_started', at, placeId: 'r-tp-meet' }, names, 'en', tr('en')), `Because: meeting started ${time} in Meeting Room A`);
  assert.equal(becauseLine({ kind: 'meeting_ended', at, placeId: 'r-tp-meet' }, names, 'en', tr('en')), `Because: meeting in Meeting Room A ended ${time}`);
  assert.equal(becauseLine({ kind: 'desk_moved', at, placeId: TP }, names, 'en', tr('en')), `Because: moved to Tyre Pulse, verified ${time}`);
  assert.match(becauseLine({ kind: 'meeting_started', at, placeId: 'gone' }, names, 'en', tr('en')), /in a room$/);
  assert.match(becauseLine({ kind: 'desk_moved', at, placeId: TP }, names, 'ar', tr('ar')), /^السبب: /);
  assert.equal(timeText('nope', 'en'), '');
});

test('Arabic catalogue has the same keys and placeholders as English', () => {
  const keys = Object.keys(CATALOGS.en).filter((k) => k.startsWith('hqWalkers.'));
  assert.ok(keys.length >= 10);
  for (const k of keys) {
    const en = String((CATALOGS.en as Record<string, unknown>)[k]), ar = String((CATALOGS.ar as Record<string, unknown>)[k] ?? '');
    assert.ok(ar, k);
    assert.deepEqual([...en.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort(), [...ar.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort(), k);
  }
});

// ---------------------------------------------------------------- routes on the real campus walkways

function realCampus() {
  const mk = (id: string, name: string, kind: CampusBuilding['kind']): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name, kind, icon: null, floors: 2, accessible: true, totals: null, presence_verified_at: null });
  const buildings = [mk('w~hq', 'ANUM HQ', 'executive'), mk('w~tp', 'Tyre Pulse', 'department'), mk('w~co', 'Commerce', 'business'), mk('w~cs', 'Corporate', 'business'), mk('w~ko', 'Knowledge', 'business'), mk('w~nv', 'Ventures', 'business')];
  const scene = buildCampusScene(layoutCampus(buildings));
  const sites: SiteGeo[] = scene.buildings.map((b) => ({ key: b.workspaceId, door: { x: b.door.x, z: b.door.z }, box: { minX: b.box.min.x, minZ: b.box.min.z, maxX: b.box.max.x, maxZ: b.box.max.z } }));
  const spokes = new Map(scene.plaza.spokes.map((s) => [s.id, s] as const));
  return { sites, spokes };
}

test('routes follow the existing walkways: they start at the facade, pass exactly through both doors and end at the facade', () => {
  const { sites, spokes } = realCampus();
  assert.equal(sites.length, 6);
  let checked = 0;
  for (const a of sites) for (const b of sites) {
    if (a.key === b.key) { assert.equal(buildRoute(a, b, spokes), null); continue; }
    const r = buildRoute(a, b, spokes)!;
    assert.ok(r, `${a.key} to ${b.key}`);
    const at = (d: number): [number, number] => {
      let i = 0; while (i < r.cum.length - 1 && r.cum[i] < d - 1e-4) i++;
      return [r.pts[i * 2], r.pts[i * 2 + 1]];
    };
    assert.deepEqual(at(r.doorA), [Math.fround(a.door.x), Math.fround(a.door.z)], 'passes exactly through door A');
    assert.deepEqual(at(r.arriveAt), [Math.fround(b.door.x), Math.fround(b.door.z)], 'pauses exactly at door B');
    const first = [r.pts[0], r.pts[1]], last = [r.pts[r.pts.length - 2], r.pts[r.pts.length - 1]];
    for (const [p, s] of [[first, a], [last, b]] as const) {
      assert.ok(p[0] >= s.box.minX - 1e-3 && p[0] <= s.box.maxX + 1e-3 && p[1] >= s.box.minZ - 1e-3 && p[1] <= s.box.maxZ + 1e-3, 'facade point is on the building');
    }
    // the middle of the route stays on the pavement ring or on a walkway: never inside the plaza
    for (let i = 0; i < r.cum.length; i++) assert.ok(Math.hypot(r.pts[i * 2], r.pts[i * 2 + 1]) >= SPOKE_START - 0.01);
    assert.ok(r.totalSec >= 10 && r.totalSec <= 25, `duration ${r.totalSec}`);
    assert.ok(r.arriveAt > r.doorA && r.length > r.arriveAt);
    checked++;
  }
  assert.equal(checked, 30);
  assert.equal(buildRoute(sites[0], { ...sites[1], key: 'nowalkway' }, spokes), null);
});

test('timeline: appears at the facade, walks at constant speed, pauses at the destination door, walks in and is gone', () => {
  const { sites, spokes } = realCampus();
  const r = buildRoute(sites[0], sites[1], spokes) as WalkRoute;
  const p = newWalkPose();
  walkPoseAt(r, 0, p);
  assert.ok(p.visible && p.moving);
  assert.deepEqual([p.x, p.z], [r.pts[0], r.pts[1]]);
  const tArrive = r.arriveAt / r.speed;
  walkPoseAt(r, tArrive + PAUSE_S / 2, p);
  assert.equal(p.moving, false, 'pauses at the door');
  assert.ok(Math.abs(p.x - sites[1].door.x) < 1e-3 && Math.abs(p.z - sites[1].door.z) < 1e-3);
  const faceX = Math.sin(p.heading), faceZ = Math.cos(p.heading);
  const toBuilding = [(sites[1].box.minX + sites[1].box.maxX) / 2 - p.x, (sites[1].box.minZ + sites[1].box.maxZ) / 2 - p.z];
  assert.ok(faceX * toBuilding[0] + faceZ * toBuilding[1] > 0, 'faces the building while paused');
  walkPoseAt(r, r.totalSec + 0.1, p);
  assert.equal(p.visible, false);
  // the stride follows the distance: equal steps in time while moving advance the phase equally
  const a = walkPoseAt(r, 2, newWalkPose()).phase, b = walkPoseAt(r, 3, newWalkPose()).phase, c = walkPoseAt(r, 4, newWalkPose()).phase;
  assert.ok(Math.abs((b - a) - (c - b)) < 1e-6);
  restPose(r, p);
  assert.ok(p.visible && !p.moving);
});

// ---------------------------------------------------------------- planner: cap, queue, one per agent, reduced motion

const fakeRoute = (): WalkRoute => ({ pts: new Float32Array([0, 0, 10, 0]), cum: new Float32Array([0, 10]), length: 10, doorA: 0, arriveAt: 10, walkSec: 10, totalSec: 11.6, speed: 1 });
const rec = (agentId: string, n = 0, extra: Partial<WalkerRecord> = {}): WalkerRecord => ({ kind: 'desk_moved', agentId, from: HQ, to: TP, at: iso(1), placeId: TP, recordId: `desk_moved:${agentId}:${n}`, ...extra });
const opts = { reducedMotion: false, route: () => fakeRoute() };

test('cap and queue: twelve at once, the rest wait, and the waiting number is the real queue length', () => {
  const p = new WalkerPlanner();
  const many = Array.from({ length: 20 }, (_x, i) => rec(`a${i}`));
  assert.equal(p.accept(many, 0, opts), true);
  assert.equal(p.walkers.length, MAX_WALKERS);
  assert.equal(p.waiting, 8);
  assert.equal(new Set(p.walkers.map((w) => w.record.agentId)).size, MAX_WALKERS, 'one figure per agent');
  // nobody waits when nobody has to
  const q = new WalkerPlanner();
  q.accept([rec('a'), rec('b')], 0, opts);
  assert.equal(q.waiting, 0);
  // a walk ends, a waiting agent takes the place
  p.prune(12_000);
  p.prune(12_000 + LINGER_MS + 1);
  assert.equal(p.walkers.length, 8, 'the queue only held 8, and none expired yet');
  assert.equal(p.waiting, 0);
});

test('the queue drops events that waited too long and never shows a walker twice for the same record', () => {
  const p = new WalkerPlanner();
  p.accept(Array.from({ length: 13 }, (_x, i) => rec(`a${i}`)), 0, opts);
  assert.equal(p.waiting, 1);
  p.prune(QUEUE_TTL_MS + 1);
  assert.equal(p.waiting, 0, 'the walkers are over by then; the waiting one expired at the same time or was promoted');
  const again = new WalkerPlanner();
  assert.equal(again.accept([rec('x', 1)], 0, opts), true);
  assert.equal(again.accept([rec('x', 1)], 1, opts), false, 'the same record is shown once');
});

test('one figure per agent: a second record for a walking agent waits and the newest waiting one replaces an older one', () => {
  const p = new WalkerPlanner();
  p.accept([rec('a', 1)], 0, opts);
  p.accept([rec('a', 2)], 1, opts);
  p.accept([rec('a', 3)], 2, opts);
  assert.equal(p.walkers.length, 1);
  assert.equal(p.waiting, 1);
  p.prune(20_000); // the walk and its line are over: the waiting record starts
  assert.equal(p.walkers.length, 1);
  assert.equal(p.walkers[0].record.recordId, 'desk_moved:a:3');
});

test('no route, no walker; reduced motion draws a static marker for 60 seconds', () => {
  const p = new WalkerPlanner();
  assert.equal(p.accept([rec('a')], 0, { reducedMotion: false, route: () => null }), false);
  assert.equal(p.walkers.length, 0);
  const s = new WalkerPlanner();
  s.accept([rec('a')], 1000, { reducedMotion: true, route: () => fakeRoute() });
  const w = s.walkers[0];
  assert.equal(w.animate, false);
  assert.equal(w.endMs, 1000 + STATIC_MS);
  assert.equal(s.drawn(1000 + STATIC_MS - 1).length, 1);
  s.prune(1000 + STATIC_MS + 1);
  assert.equal(s.walkers.length, 0);
});

test('an animated walker is drawn for its walk and its line lingers six seconds longer', () => {
  const p = new WalkerPlanner();
  p.accept([rec('a')], 0, opts);
  const w = p.walkers[0];
  assert.equal(w.endMs, 11_600);
  assert.equal(w.lineUntilMs, 11_600 + LINGER_MS);
  assert.equal(p.drawn(11_599).length, 1);
  assert.equal(p.drawn(11_600).length, 0);
  assert.equal(p.walkers.length, 1);
  p.prune(11_600 + LINGER_MS);
  assert.equal(p.walkers.length, 0);
  assert.equal(p.nextEnd(0), null);
});

test('the walker kinds are the three documented triggers', () => {
  assert.deepEqual([...WALKER_KINDS], ['meeting_started', 'meeting_ended', 'desk_moved']);
});
