import assert from 'node:assert/strict';
import { test } from 'node:test';
import { seeded } from '../src/lib/hqGeometry.ts';
import {
  ALERT_KINDS, KIND_SECONDS, LINGER_MS, MAX_ANIMATED, STATIC_MS, TYRE_TIMELINE, VEHICLE_TIMELINE, WORK_KINDS, WorkPlanner,
  becauseLine, extraHoldOf, flowRecords, makeRecord, newPose, presenceRecords, routeTo, sampleRoad, segmentHitsBox, siteOf, stagesOf,
  tyreDay, tyreInstant, tyrePoseAt, tyreRecords, vehiclePoseAt, vehicleSeconds, whenText, type TyreFeed, type WorkKind, type WorkRecord,
} from '../src/lib/hqWorkMotion.ts';
import { CATALOGS } from '../src/lib/i18n.ts';

const NOW = Date.parse('2026-10-26T14:10:00Z');
const known = new Set(['b1', 'b2', 'b3']);
const iso = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000).toISOString();
const tr = (locale: 'en' | 'ar') => (key: string, vars: Record<string, string> = {}) => {
  const m = (CATALOGS[locale] as Record<string, unknown>)[key];
  assert.equal(typeof m, 'string', key);
  return (m as string).replace(/\{(\w+)\}/g, (_x, k: string) => vars[k] ?? `{${k}}`);
};

test('no record, no motion: feeds that are off, first answers and unchanged answers produce nothing', () => {
  const quiet: TyreFeed = { state: 'connected', alerts: [], latestAlertAt: null, lastInspection: null, lastInspectionAt: null };
  const busy: TyreFeed = { state: 'connected', alerts: [{ id: 'a', kind: 'low_tread', firstSeenAt: iso(5) }], latestAlertAt: iso(5), lastInspection: '2026-10-26', lastInspectionAt: iso(3) };
  for (const state of ['loading', 'not_connected', 'forbidden', 'error'] as const) {
    assert.deepEqual(tyreRecords(quiet, { state }, 'b1', known, NOW), []);
    // Coming back from an off or unreadable feed is a first answer again: the past is not replayed.
    assert.deepEqual(tyreRecords({ state }, busy, 'b1', known, NOW), []);
  }
  assert.deepEqual(tyreRecords(null, busy, 'b1', known, NOW), []); // the first answer replays nothing
  assert.deepEqual(tyreRecords(busy, busy, 'b1', known, NOW), []); // nothing changed
  assert.deepEqual(tyreRecords(quiet, busy, null, known, NOW), []); // no Tyre Pulse building
  assert.deepEqual(tyreRecords(quiet, quiet, 'b1', known, NOW), []);
  const view = [{ key: 'b1', working: 2, verifiedAt: iso(1) }];
  assert.deepEqual(presenceRecords(null, view, known, NOW), []);
  assert.deepEqual(presenceRecords(view, view, known, NOW), []);
  assert.deepEqual(presenceRecords(view, [{ key: 'b1', working: 1, verifiedAt: iso(1) }], known, NOW), []);
  assert.deepEqual(presenceRecords(view, [{ key: 'b1', working: null, verifiedAt: null }], known, NOW), []);
  const items = [{ key: 'task:1', kind: 'task', stage: 'in_office', since: iso(1), buildingId: 'x' }];
  assert.deepEqual(flowRecords(null, items, () => 'b1', known, NOW), []);
  assert.deepEqual(flowRecords(stagesOf(items), items, () => 'b1', known, NOW), []);
  const planner = new WorkPlanner();
  assert.equal(planner.accept([], NOW, { reducedMotion: false, routed: () => true }), false);
  assert.equal(planner.motions.length, 0);
});

test('records come only from a rise in working agents, a task that started or finished, and Tyre Pulse records of the last day', () => {
  const before = [{ key: 'b1', working: 1, verifiedAt: iso(9) }, { key: 'b2', working: 0, verifiedAt: iso(9) }];
  const after = [{ key: 'b1', working: 3, verifiedAt: iso(1) }, { key: 'b2', working: 0, verifiedAt: iso(1) }];
  const p = presenceRecords(before, after, known, NOW);
  assert.deepEqual(p.map((r) => [r.kind, r.buildingKey, r.at]), [['agent_working', 'b1', iso(1)]]);
  const prev = new Map([['task:a', 'arrived'], ['task:b', 'in_office'], ['lead:c', 'arrived']]);
  const items = [
    { key: 'task:a', kind: 'task', stage: 'in_office', since: iso(1), buildingId: 'x' },
    { key: 'task:b', kind: 'task', stage: 'done', since: iso(2), buildingId: 'x' },
    { key: 'lead:c', kind: 'lead', stage: 'in_office', since: iso(1), buildingId: 'x' },
    { key: 'task:d', kind: 'task', stage: 'in_office', since: iso(1), buildingId: null },
    { key: 'task:e', kind: 'task', stage: 'in_office', since: iso(90), buildingId: 'x' },
  ];
  const f = flowRecords(prev, items, (id) => (id === 'x' ? 'b2' : null), known, NOW);
  assert.deepEqual(f.map((r) => [r.kind, r.buildingKey]), [['task_started', 'b2'], ['task_done', 'b2']]);
  const tyreBefore: TyreFeed = { state: 'connected', alerts: [{ id: 'old', kind: 'low_tread', firstSeenAt: iso(60 * 30) }], latestAlertAt: iso(60 * 30), lastInspection: '2026-10-25', lastInspectionAt: '2026-10-25T09:00:00Z' };
  const tyreAfter: TyreFeed = { state: 'connected', alerts: [{ id: 'old', kind: 'low_tread', firstSeenAt: iso(60 * 30) }, { id: 'n1', kind: 'abnormal_wear', firstSeenAt: iso(20) }, { id: 'n2', kind: 'low_tread', firstSeenAt: iso(5) }], latestAlertAt: iso(5), lastInspection: '2026-10-26', lastInspectionAt: iso(3) };
  const t = tyreRecords(tyreBefore, tyreAfter, 'b3', known, NOW);
  // One record per event type: the newest alert, and the new inspection at its stored time (an instant, so the line says 14:07).
  assert.deepEqual(t.map((r) => [r.kind, r.alertKind, r.at, r.day]), [['tyre_alert', 'low_tread', iso(5), false], ['tyre_inspection', null, iso(3), false]]);
  // The newest alert is not in the list the browser read (it holds 50): the event still happens, with the plain line.
  const unseen = tyreRecords(tyreBefore, { ...tyreAfter, latestAlertAt: iso(1) } as TyreFeed, 'b3', known, NOW);
  assert.deepEqual(unseen.map((r) => [r.kind, r.alertKind, r.at]), [['tyre_alert', null, iso(1)], ['tyre_inspection', null, iso(3)]]);
  // Times that did not move forward are not events: an alert resolved (the newest is older now), the same or an older inspection.
  assert.deepEqual(tyreRecords(tyreAfter, tyreBefore, 'b3', known, NOW), []);
  // A change older than the 24 hour window is history: no record.
  const stale: TyreFeed = { ...tyreAfter, latestAlertAt: iso(60 * 25), lastInspection: '2026-10-25', lastInspectionAt: iso(60 * 25) } as TyreFeed;
  assert.deepEqual(tyreRecords({ ...tyreBefore, latestAlertAt: iso(60 * 30), lastInspection: '2026-10-24', lastInspectionAt: null } as TyreFeed, stale, 'b3', known, NOW), []);
  // A source that keeps dates only: a later day is an inspection, shown as a day ("Oct 26"), never given a time.
  const dayOnly = tyreRecords({ ...tyreBefore, lastInspectionAt: null } as TyreFeed, { ...tyreBefore, lastInspection: '2026-10-26', lastInspectionAt: null } as TyreFeed, 'b3', known, NOW);
  assert.deepEqual(dayOnly.map((r) => [r.kind, r.at, r.day]), [['tyre_inspection', '2026-10-26', true]]);
  assert.equal(whenText(dayOnly[0].at, dayOnly[0].day, 'en'), 'Oct 26');
  // Only a day was known tyreBefore and now the same day has a time: it may be the same inspection, so it is not an event.
  assert.deepEqual(tyreRecords({ ...tyreBefore, lastInspection: '2026-10-26', lastInspectionAt: null } as TyreFeed, { ...tyreBefore, lastInspection: '2026-10-26', lastInspectionAt: iso(3) } as TyreFeed, 'b3', known, NOW), []);
  // A later inspection time on the same day is a new inspection.
  const sameDay = tyreRecords({ ...tyreAfter, lastInspectionAt: iso(90) } as TyreFeed, tyreAfter, 'b3', known, NOW);
  assert.deepEqual(sameDay.map((r) => [r.kind, r.at]), [['tyre_inspection', iso(3)]]);
});

test('the Tyre Pulse reader keeps only well formed times: an instant with a zone, or a calendar day', () => {
  assert.equal(tyreInstant('2026-10-26T14:05:00Z'), '2026-10-26T14:05:00Z');
  assert.equal(tyreInstant('2026-10-26T14:05:00.123456+04:00'), '2026-10-26T14:05:00.123456+04:00');
  for (const bad of [null, undefined, 7, '', '2026-10-26', '2026-10-26T14:05:00', 'Oct 26 14:05', '2026-13-45T10:00:00Z', 'fake-sn-0103']) assert.equal(tyreInstant(bad), null, String(bad));
  assert.equal(tyreDay('2026-10-26'), '2026-10-26');
  assert.equal(tyreDay('2026-10-26T14:05:00Z'), '2026-10-26');
  for (const bad of [null, undefined, 7, '', 'Oct 26', '2026-13-45', 'fake-plate-01']) assert.equal(tyreDay(bad), null, String(bad));
  // A record built from an instant is never a day, and a day-only inspection is never an instant.
  assert.equal(makeRecord({ kind: 'tyre_inspection', buildingKey: 'b1', at: iso(3), recordId: 'i' }, known, NOW)?.day, false);
  assert.equal(makeRecord({ kind: 'tyre_inspection', buildingKey: 'b1', at: '2026-10-26', recordId: 'd' }, known, NOW)?.day, true);
  assert.equal(makeRecord({ kind: 'tyre_alert', buildingKey: 'b1', at: '2026-10-26', recordId: 'a' }, known, NOW)?.day, false);
});

test('property: a record is refused unless every field is from the allowed set', () => {
  const rnd = seeded('records');
  const junk = ['', 'x', '2026-13-45', 'Acme Fleet Co', '<script>', 'owner@example.com', '999', 'NaN'];
  for (let i = 0; i < 400; i++) {
    const kind = rnd() < 0.5 ? WORK_KINDS[Math.floor(rnd() * WORK_KINDS.length)] : junk[Math.floor(rnd() * junk.length)];
    const building = rnd() < 0.6 ? ['b1', 'b2', 'zz'][Math.floor(rnd() * 3)] : junk[Math.floor(rnd() * junk.length)];
    const at = rnd() < 0.5 ? iso(rnd() * 3000 - 60) : junk[Math.floor(rnd() * junk.length)];
    const alertKind = rnd() < 0.5 ? ALERT_KINDS[Math.floor(rnd() * 6)] : junk[Math.floor(rnd() * junk.length)];
    const r = makeRecord({ kind, buildingKey: building, at, recordId: 'id' + i, alertKind }, known, NOW);
    if (!r) continue;
    assert.ok(WORK_KINDS.includes(r.kind));
    assert.ok(known.has(r.buildingKey));
    assert.ok(Number.isFinite(Date.parse(r.at)));
    assert.ok(r.alertKind === null || (ALERT_KINDS as readonly string[]).includes(r.alertKind));
    if (r.kind !== 'tyre_alert') assert.equal(r.alertKind, null);
    assert.deepEqual(Object.keys(r).sort(), ['alertKind', 'at', 'buildingKey', 'day', 'kind', 'recordId']);
  }
});

test('property: the Because line is a template filled with a time and a fixed alert word, never with text from outside', () => {
  const rnd = seeded('because');
  for (const locale of ['en', 'ar'] as const) {
    const translate = tr(locale);
    for (const kind of WORK_KINDS) {
      for (let i = 0; i < 40; i++) {
        const secret = `Customer-${Math.floor(rnd() * 1e6)}-Acme`;
        const feedRecord = makeRecord({ kind, buildingKey: 'b1', at: kind === 'tyre_inspection' ? '2026-10-26' : iso(rnd() * 20), recordId: secret, alertKind: rnd() < 0.5 ? ALERT_KINDS[Math.floor(rnd() * 6)] : secret }, known, NOW) as WorkRecord;
        assert.ok(feedRecord, kind);
        // Titles, names and ids can ride along on the source object; they are not fields of a record.
        const line = becauseLine({ ...feedRecord, title: secret, name: secret, count: 7 } as WorkRecord, locale, translate);
        assert.ok(!line.includes(secret), line);
        assert.ok(!/\{\w+\}/.test(line), `placeholder left: ${line}`);
        assert.ok(line.length > 8 && line.length < 120);
        // The only digits are the time (HH:MM or a day number).
        const digits = (line.match(/\d+/g) ?? []).join('');
        const time = whenText(feedRecord.at, feedRecord.day, locale).replace(/\D/g, '');
        assert.equal(digits, time, line);
      }
    }
  }
});

test('the planner shows each record once, one motion per building and kind, capped at four animated, and rests when time is over', () => {
  const planner = new WorkPlanner();
  const rec = (kind: WorkKind, building: string, id: string): WorkRecord => makeRecord({ kind, buildingKey: building, at: iso(1), recordId: id, alertKind: 'low_tread' }, known, NOW)!;
  const routed = () => true;
  assert.equal(planner.accept([rec('task_started', 'b1', 'a')], 1000, { reducedMotion: false, routed }), true);
  assert.equal(planner.accept([rec('task_started', 'b1', 'a')], 2000, { reducedMotion: false, routed }), false); // the same record never replays
  assert.equal(planner.accept([rec('task_started', 'b1', 'b')], 3000, { reducedMotion: false, routed }), false); // already one live for this building and kind
  assert.equal(planner.motions.length, 1);
  planner.accept([rec('task_done', 'b1', 'c'), rec('agent_working', 'b2', 'd'), rec('tyre_alert', 'b3', 'e'), rec('tyre_inspection', 'b3', 'f')], 4000, { reducedMotion: false, routed });
  assert.equal(planner.motions.length, 5);
  assert.equal(planner.motions.filter((m) => m.animate).length, MAX_ANIMATED);
  const first = planner.motions[0];
  assert.ok(planner.animating(first, 1000 + KIND_SECONDS.task_started * 1000 - 1));
  assert.ok(!planner.animating(first, 1000 + KIND_SECONDS.task_started * 1000));
  assert.ok(planner.alive(first, 1000 + KIND_SECONDS.task_started * 1000 + LINGER_MS - 1));
  assert.equal(planner.prune(1000 + 60 * 60_000), true);
  assert.equal(planner.motions.length, 0); // rest state: nothing loops forever
  assert.equal(planner.nextEnd(0), null);
});

test('reduced motion and a missing route keep the marker but draw nothing; the marker is bounded too', () => {
  const planner = new WorkPlanner();
  const r = makeRecord({ kind: 'tyre_alert', buildingKey: 'b1', at: iso(3), recordId: 'z', alertKind: 'low_tread' }, known, NOW)!;
  planner.accept([r], 0, { reducedMotion: true, routed: () => true });
  assert.equal(planner.motions[0].animate, false);
  assert.ok(planner.alive(planner.motions[0], STATIC_MS - 1));
  assert.ok(!planner.alive(planner.motions[0], STATIC_MS));
  const p2 = new WorkPlanner();
  p2.accept([makeRecord({ kind: 'task_done', buildingKey: 'b2', at: iso(1), recordId: 'q' }, known, NOW)!], 0, { reducedMotion: false, routed: () => false });
  assert.equal(p2.motions[0].animate, false);
});

test('vehicle poses: drive in, wait, turn, drive out, then gone; wheels only turn when it moves', () => {
  const route = { ax: 0, az: 0, bx: 0, bz: 40, length: 40, heading: 0 };
  const pose = newPose();
  const total = vehicleSeconds('task_started');
  assert.equal(total, VEHICLE_TIMELINE.approach + VEHICLE_TIMELINE.hold + VEHICLE_TIMELINE.turn + VEHICLE_TIMELINE.depart);
  assert.equal(total, KIND_SECONDS.task_started);
  assert.equal(vehicleSeconds('tyre_alert'), KIND_SECONDS.tyre_alert);
  assert.equal(vehiclePoseAt(route, -0.1, 0.4, pose).visible, false);
  assert.equal(vehiclePoseAt(route, total + 0.01, 0.4, pose).visible, false);
  vehiclePoseAt(route, 0.0, 0.4, pose);
  assert.ok(pose.visible && Math.abs(pose.z) < 1e-6);
  let lastSpin = 0, lastZ = -1;
  for (let t = 0; t < VEHICLE_TIMELINE.approach; t += 0.25) {
    vehiclePoseAt(route, t, 0.4, pose);
    assert.ok(pose.z >= lastZ - 1e-9 && pose.spin >= lastSpin - 1e-9, `t=${t}`);
    lastZ = pose.z; lastSpin = pose.spin;
  }
  vehiclePoseAt(route, VEHICLE_TIMELINE.approach + 1, 0.4, pose);
  const parked = { z: pose.z, spin: pose.spin };
  vehiclePoseAt(route, VEHICLE_TIMELINE.approach + 3, 0.4, pose);
  assert.deepEqual({ z: pose.z, spin: pose.spin }, parked);
  assert.ok(pose.beacon >= 0 && pose.beacon <= 1);
  assert.ok(Math.abs(pose.z - 40) < 1e-6);
  const late = VEHICLE_TIMELINE.approach + VEHICLE_TIMELINE.hold + VEHICLE_TIMELINE.turn + VEHICLE_TIMELINE.depart - 0.01;
  vehiclePoseAt(route, late, 0.4, pose);
  assert.ok(pose.z < 2 && Math.abs(Math.abs(pose.heading) - Math.PI) < 1e-6); // leaves facing back down the road
  assert.equal(extraHoldOf('tyre_alert'), 2);
});

test('tyre pose: rolls out to the rack, rests, rolls back in, and is gone after its time', () => {
  const site = siteOf({ key: 'b1', door: { x: 0, z: 10 }, box: { minX: -10, minZ: -10, maxX: 10, maxZ: 10 } });
  const pose = newPose();
  const total = TYRE_TIMELINE.out + TYRE_TIMELINE.rest + TYRE_TIMELINE.back;
  assert.equal(total, KIND_SECONDS.tyre_inspection);
  tyrePoseAt(site, 0, pose);
  assert.ok(Math.hypot(pose.x - site.door.x, pose.z - site.door.z) < 1e-6 && pose.grow === 0);
  tyrePoseAt(site, TYRE_TIMELINE.out + 1, pose);
  assert.ok(Math.hypot(pose.x - site.rack.x, pose.z - site.rack.z) < 1e-6);
  tyrePoseAt(site, total - 0.001, pose);
  assert.ok(Math.hypot(pose.x - site.door.x, pose.z - site.door.z) < 0.05);
  assert.equal(tyrePoseAt(site, total + 0.1, pose).visible, false);
});

test('sites sit outside the building on the door side and routes never cross a building', () => {
  const box = { minX: -10, minZ: -8, maxX: 10, maxZ: 8 };
  const south = siteOf({ key: 'b', door: { x: 0, z: 8 }, box });
  assert.deepEqual([south.nx, south.nz], [0, 1]);
  assert.ok(south.stop.z > 8 && south.rack.z > 8);
  const east = siteOf({ key: 'b', door: { x: 10, z: 0 }, box });
  assert.deepEqual([east.nx, east.nz], [1, 0]);
  const other = { minX: -3, minZ: 24, maxX: 12, maxZ: 34 };
  const roads: number[] = [];
  sampleRoad(new Float32Array([-60, -60, 60, -60, 60, 60, -60, 60]), 5, roads);
  assert.ok(roads.length > 40);
  const route = routeTo(south, new Float32Array(roads), [box, other]);
  assert.ok(route);
  assert.equal(segmentHitsBox(route!.ax, route!.az, route!.bx, route!.bz, box, 1.6), false);
  assert.equal(segmentHitsBox(route!.ax, route!.az, route!.bx, route!.bz, other, 1.6), false);
  assert.equal(routeTo(south, new Float32Array([]), [box]), null);
  assert.equal(segmentHitsBox(-20, 0, 20, 0, box, 0), true);
  assert.equal(segmentHitsBox(-20, 30, 20, 30, box, 0), false);
});

test('the catalogue has the same keys and placeholders in English and Arabic, with no long dashes', () => {
  const en = CATALOGS.en as Record<string, unknown>, ar = CATALOGS.ar as Record<string, unknown>;
  const keys = Object.keys(en).filter((k) => k.startsWith('hqMotion.'));
  assert.ok(keys.length >= 18);
  for (const k of keys) {
    assert.equal(typeof ar[k], 'string', k);
    const names = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join();
    assert.equal(names(en[k] as string), names(ar[k] as string), k);
    assert.ok(!/[\u2013\u2014]/.test(en[k] as string + (ar[k] as string)), k);
  }
});

test('scenery vehicles: wheels face outwards and are separate parts, the hauler and beam are finite and bounded', async () => {
  const { sceneryWheel, sceneryBeam, haulerParts, WHEEL_SPOTS, WHEEL_RADIUS, SCENERY_WHEEL_TRIANGLES_CAP, HAULER_TRIANGLES_CAP } = await import('../src/hqAssets/environment/sceneryVehicle.ts');
  const { VERTEX_FLOATS } = await import('../src/lib/hqGeometry.ts');
  const w = sceneryWheel();
  assert.ok(w.length / VERTEX_FLOATS / 3 <= SCENERY_WHEEL_TRIANGLES_CAP);
  assert.ok(w.every(Number.isFinite));
  for (let i = 0; i < w.length; i += VERTEX_FLOATS * 3) {
    const nx = w[i + 3], ny = w[i + 4], nz = w[i + 5];
    const cx = (w[i] + w[i + VERTEX_FLOATS] + w[i + 2 * VERTEX_FLOATS]) / 3, cy = (w[i + 1] + w[i + VERTEX_FLOATS + 1] + w[i + 2 * VERTEX_FLOATS + 1]) / 3, cz = (w[i + 2] + w[i + VERTEX_FLOATS + 2] + w[i + 2 * VERTEX_FLOATS + 2]) / 3;
    if (Math.abs(Math.abs(cx) - 0.35) < 0.02 || Math.abs(Math.abs(cx) - 0.362) < 0.02) assert.ok(Math.sign(cx) * nx > 0.9, 'cap or spoke faces outwards');
    else assert.ok(nx * 0 + ny * cy + nz * cz > 0, 'tread faces outwards');
  }
  const h = haulerParts();
  assert.ok(h.triangles > 100 && h.triangles <= HAULER_TRIANGLES_CAP, String(h.triangles));
  assert.ok([h.body, h.trim, h.lights].every((p) => p.every(Number.isFinite)));
  assert.ok(sceneryBeam().every(Number.isFinite));
  for (const k of [0, 1, 2, 3, 4] as const) { assert.ok(WHEEL_SPOTS[k].length === (k === 4 ? 6 : 4)); assert.ok(WHEEL_RADIUS[k] > 0.3 && WHEEL_RADIUS[k] < 0.6); }
});
