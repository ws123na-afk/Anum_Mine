import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { changeGuidedTour, createGuidedTour, GuidedTourError, stopsFromRegistry, tourProgress } from '../src/lib/guidedExecutiveTour.ts';
import { CATALOGS } from '../src/lib/i18n.ts';
import type { TourStop as RegistryStop } from '../src/lib/executivePa.ts';

const registry: RegistryStop[] = [
  { building_id: 'hq', building_name: 'Headquarters', floor_id: 'f0', floor_name: 'Ground', level: 0, room_id: 'r_rec', room_name: 'Reception', room_kind: 'reception', capacity: 6, desks: 0 },
  { building_id: 'hq', building_name: 'Headquarters', floor_id: 'f1', floor_name: 'First floor', level: 1, room_id: 'r_sales', room_name: 'Sales office', room_kind: 'office', capacity: 8, desks: 4 },
  { building_id: 'hq', building_name: 'Headquarters', floor_id: 'f1', floor_name: 'First floor', level: 1, room_id: 'r_exec', room_name: 'Executive office', room_kind: 'office', capacity: 4, desks: 1 },
];
const stops = stopsFromRegistry(registry);
const stamp = (s: number) => new Date(Date.UTC(2026, 9, 21, 9, 0, s)).toISOString();

describe('guided visit built from the registry', () => {
  it('maps only registry facts into stops, with no staff or example text', () => {
    assert.deepEqual(stops[1], { id: 'r_sales', name: 'Sales office', floor: 1, floorId: 'f1', floorName: 'First floor', buildingId: 'hq', buildingName: 'Headquarters', kind: 'office', capacity: 8, desks: 4 });
    assert.deepEqual(Object.keys(stops[0]).sort(), ['buildingId', 'buildingName', 'capacity', 'desks', 'floor', 'floorId', 'floorName', 'id', 'kind', 'name']);
  });

  it('tours every stop in order, supports pause and ends with coded events', () => {
    let t = createGuidedTour(stops, stamp(0));
    assert.equal(t.phase, 'ready'); assert.equal(tourProgress(t), 0);
    t = changeGuidedTour(t, { type: 'begin', at: stamp(1) });
    assert.equal(t.currentRoomId, 'r_rec');
    t = changeGuidedTour(t, { type: 'next', at: stamp(2) });
    assert.equal(t.currentRoomId, 'r_sales'); assert.equal(tourProgress(t), 67);
    t = changeGuidedTour(t, { type: 'pause', at: stamp(3) });
    assert.throws(() => changeGuidedTour(t, { type: 'next', at: stamp(4) }), (e) => e instanceof GuidedTourError && e.code === 'resumeFirst');
    t = changeGuidedTour(t, { type: 'resume', at: stamp(5) });
    t = changeGuidedTour(t, { type: 'next', at: stamp(6) });
    t = changeGuidedTour(t, { type: 'next', at: stamp(7) });
    assert.equal(t.phase, 'finished'); assert.equal(tourProgress(t), 100);
    assert.deepEqual(t.visited, ['r_rec', 'r_sales', 'r_exec']);
    assert.deepEqual(t.events.map((e) => e.kind), ['arrival', 'visit', 'visit', 'pause', 'resume', 'visit', 'finish']);
    assert.ok(t.events.every((e) => Object.keys(e).sort().join() === 'at,kind,roomId'));
  });

  it('redirects only to stops that exist and refuses a redirect after the end', () => {
    let t = createGuidedTour(stops, stamp(0));
    t = changeGuidedTour(t, { type: 'redirect', roomId: 'r_exec', at: stamp(1) });
    assert.equal(t.currentRoomId, 'r_exec'); assert.equal(t.events[1].kind, 'redirect');
    assert.throws(() => changeGuidedTour(t, { type: 'redirect', roomId: 'invented-room', at: stamp(2) }), (e) => e instanceof GuidedTourError && e.code === 'unknownRoom');
    const done = changeGuidedTour(changeGuidedTour(createGuidedTour(stops.slice(0, 1), stamp(0)), { type: 'begin', at: stamp(1) }), { type: 'next', at: stamp(2) });
    assert.throws(() => changeGuidedTour(done, { type: 'redirect', roomId: 'r_rec', at: stamp(3) }), (e) => e instanceof GuidedTourError && e.code === 'finished');
  });

  it('an empty registry finishes at once and rejects duplicates, bad times and old events', () => {
    const empty = changeGuidedTour(createGuidedTour([], stamp(0)), { type: 'begin', at: stamp(1) });
    assert.equal(empty.phase, 'finished'); assert.equal(tourProgress(empty), 100);
    assert.throws(() => createGuidedTour([stops[0], stops[0]], stamp(0)), GuidedTourError);
    assert.throws(() => createGuidedTour(stops, 'invalid'), GuidedTourError);
    const t = createGuidedTour(stops, stamp(2));
    assert.throws(() => changeGuidedTour(t, { type: 'begin', at: stamp(1) }), GuidedTourError);
  });

  it('reset clears the history', () => {
    let t = changeGuidedTour(createGuidedTour(stops, stamp(0)), { type: 'begin', at: stamp(1) });
    t = changeGuidedTour(t, { type: 'reset', stops: stops.slice(0, 1), at: stamp(2) });
    assert.deepEqual(t.visited, []); assert.equal(t.events.length, 1);
  });

  it('every error code and tour phase has an English and an Arabic message', () => {
    for (const code of ['invalid', 'started', 'notRunning', 'notPaused', 'resumeFirst', 'unknownRoom', 'finished']) {
      for (const locale of ['en', 'ar'] as const) assert.ok((CATALOGS[locale] as Record<string, unknown>)[`gt.err.${code}`], `${locale} gt.err.${code}`);
    }
    for (const phase of ['ready', 'touring', 'paused', 'finished']) assert.ok((CATALOGS.ar as Record<string, unknown>)[`gt.phase.${phase}`]);
  });
});
