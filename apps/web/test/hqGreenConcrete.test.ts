import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import { projectHeadquarters, type Registry, type SceneDesk } from '../src/lib/headquartersProjection.ts';
import { activeShiftKey, applyShiftView, deskShiftLine, hoursOf, minutesInZone, shiftAt, shiftAtMinutes, windowOf, WORK_STATUSES } from '../src/lib/hqShifts.ts';
import { rotOfFacing, tableJoins } from '../src/lib/hqSeatTables.ts';
import { STATE_LOOK, lookSignature } from '../src/hqAssets/interior/stateVisual.ts';
import { deskLabel } from '../src/lib/registryNames.ts';
import { setLocale } from '../src/lib/i18n.ts';

type Fx = { building: Record<string, unknown>; floor: Record<string, unknown>; room: Record<string, unknown>; desks: Record<string, unknown>[]; people: { key: string; name: string; role: string; shift: string | null; desk_id: string; agent_id: string }[] };
const fx = JSON.parse(readFileSync(new URL('./fixtures/greenConcrete.json', import.meta.url), 'utf8')) as Fx;
const rev = { revision: 1 };
const registry = (building: Record<string, unknown> = {}): Registry => ({
  buildings: [{ ...fx.building, ...rev, ...building }], floors: [{ ...fx.floor, ...rev }], rooms: [{ ...fx.room, ...rev }],
  desks: fx.desks.map((d) => ({ ...d, ...rev })),
} as unknown as Registry);
const scope = { tenantId: 't', workspaceId: 'w' };
const project = (building?: Record<string, unknown>) => projectHeadquarters(scope, registry(building), []);
// 12:00 in Dubai on a normal day is 08:00 UTC; 23:00 in Dubai is 19:00 UTC; 03:00 in Dubai is 23:00 UTC the day before.
const DAY = Date.UTC(2026, 9, 26, 8, 0);
const NIGHT = Date.UTC(2026, 9, 26, 19, 0);
const SMALL_HOURS = Date.UTC(2026, 9, 25, 23, 0);

test('shift windows follow the default hours, wrap over midnight and read the building time zone', () => {
  const hours = hoursOf(null, null);
  assert.deepEqual(hours, { dayStart: '07:00', nightStart: '19:00', isDefault: true });
  assert.equal(minutesInZone(DAY, 'Asia/Dubai'), 12 * 60);
  assert.equal(shiftAt(DAY, 'Asia/Dubai', hours), 'day');
  assert.equal(shiftAt(NIGHT, 'Asia/Dubai', hours), 'night');
  assert.equal(shiftAt(SMALL_HOURS, 'Asia/Dubai', hours), 'night');
  assert.equal(shiftAt(Date.UTC(2026, 9, 26, 4, 0), 'Asia/Dubai', hours), 'day');
  assert.equal(shiftAt(Date.UTC(2026, 9, 26, 4, 0), 'UTC', hours), 'night', 'the same instant is another shift in another zone');
  assert.equal(shiftAtMinutes(6 * 60 + 59, hours), 'night');
  assert.equal(shiftAtMinutes(7 * 60, hours), 'day');
  assert.equal(shiftAtMinutes(19 * 60, hours), 'night');
  const edited = hoursOf('06:30', '18:30');
  assert.equal(edited.isDefault, false);
  assert.deepEqual(windowOf('night', edited), { start: '18:30', end: '06:30' });
  assert.equal(hoursOf('bad', null).isDefault, true, 'a bad hour is the default, not a guess');
  assert.equal(shiftAt(DAY, 'Not/AZone', hours), 'day', 'an unknown zone reads UTC and never throws');
});

test('the projection keeps the saved seat positions, the facing and the shift of a seat desk', () => {
  const desks = project().buildings[0].floors[0].desks;
  assert.equal(desks.length, 10);
  const raed = desks.find((d) => d.id === fx.people[0].desk_id) as SceneDesk;
  const room = project().buildings[0].floors[0].rooms[0];
  assert.deepEqual([raed.position.x, raed.position.z], [room.x + 4.8, room.z + 4.2]);
  assert.equal(raed.shift, 'day');
  assert.equal(raed.facing, 'north');
  const manager = desks[9];
  assert.equal(manager.seatKind, 'manager');
  assert.equal(manager.shift, null);
});

test('the day view shows the day people and the manager; the night shares their tables', () => {
  const day = applyShiftView(project().buildings, 'active', DAY)[0];
  const desks = day.floors[0].desks;
  assert.deepEqual(desks.map((d) => d.label), ['Day seat 1', 'Day seat 2', 'Day seat 3', 'Day seat 4', 'Day seat 5', 'Day seat 6', 'Manager desk']);
  assert.ok(desks.every((d) => d.shiftInfo?.onShift === true), 'everyone shown is on shift now');
  assert.deepEqual(day.floors[0].rooms[0].emptySeats, [], 'no table is left over in the day view');
  assert.equal(day.shifts?.active, 'day');
  const other = applyShiftView(project().buildings, 'other', DAY)[0];
  assert.deepEqual(other.floors[0].desks.map((d) => d.label), ['Night seat 1', 'Night seat 2', 'Night seat 3', 'Manager desk']);
  const night = other.floors[0].desks.filter((d) => d.shift === 'night');
  assert.ok(night.every((d) => d.shiftInfo?.onShift === false), 'the other shift is off shift');
  assert.equal(other.floors[0].desks.find((d) => d.seatKind === 'manager')?.shiftInfo?.onShift, true, 'the manager seat is always on');
  assert.equal(other.floors[0].rooms[0].emptySeats?.length, 3, 'seats 4 to 6 stay as empty tables');
});

test('at night the night people are on shift and the day people are off shift', () => {
  const night = applyShiftView(project().buildings, 'active', NIGHT)[0];
  assert.deepEqual(night.floors[0].desks.map((d) => d.label), ['Night seat 1', 'Night seat 2', 'Night seat 3', 'Manager desk']);
  assert.ok(night.floors[0].desks.every((d) => d.shiftInfo?.onShift));
  const day = applyShiftView(project().buildings, 'other', NIGHT)[0];
  assert.ok(day.floors[0].desks.filter((d) => d.shift === 'day').every((d) => d.shiftInfo?.onShift === false));
  assert.equal(activeShiftKey(project().buildings, DAY) === activeShiftKey(project().buildings, NIGHT), false, 'the scene is rebuilt only when the shift changes');
  assert.equal(activeShiftKey(project().buildings, DAY), activeShiftKey(project().buildings, DAY + 60_000));
});

test('edited hours move the shift change; the default flag says whether the owner confirmed them', () => {
  const hours = { shift_day_start: '06:00', shift_night_start: '14:00' };
  const noon = applyShiftView(project(hours).buildings, 'active', DAY)[0];
  assert.equal(noon.shifts?.active, 'day');
  assert.equal(noon.shifts?.hours.isDefault, false);
  const three = applyShiftView(project(hours).buildings, 'active', Date.UTC(2026, 9, 26, 11, 0))[0];
  assert.equal(three.shifts?.active, 'night', '15:00 in Dubai is night when the day shift ends at 14:00');
  assert.equal(applyShiftView(project().buildings, 'active', DAY)[0].shifts?.hours.isDefault, true);
});

test('a building without shift desks is returned as it is', () => {
  const plain = projectHeadquarters(scope, { ...registry(), desks: [] }, []);
  assert.equal(applyShiftView(plain.buildings, 'active', DAY), plain.buildings);
});

test('what a desk says about its shift: off shift is never absent, and waiting work is told honestly', () => {
  const night = applyShiftView(project().buildings, 'other', DAY)[0].floors[0].desks.find((d) => d.shift === 'night') as SceneDesk;
  assert.equal(deskShiftLine(night.shiftInfo, false), 'off');
  assert.equal(deskShiftLine(night.shiftInfo, true), 'off_waiting');
  const manager = applyShiftView(project().buildings, 'active', DAY)[0].floors[0].desks.find((d) => d.seatKind === 'manager') as SceneDesk;
  assert.equal(deskShiftLine(manager.shiftInfo, true), 'manager');
  assert.equal(deskShiftLine(undefined, true), null);
  assert.ok(WORK_STATUSES.has('queued') && WORK_STATUSES.has('waiting_approval') && !WORK_STATUSES.has('idle') && !WORK_STATUSES.has('paused'));
});

test('the L table: six seats, a corner piece joins the long and the short leg, facing gives the turn', () => {
  const seats = fx.desks.filter((d) => d.shift === 'day').map((d) => ({ x: d.pos_x as number, z: d.pos_z as number, facing: d.facing as 'north' | 'east', group: d.table_group as string }));
  const joins = tableJoins(seats);
  assert.equal(joins.length, 1);
  const [j] = joins;
  assert.ok(j.width > 0.5 && j.depth > 1.5, 'the corner covers the run between the end of the long leg and the first seat of the short leg');
  assert.equal(tableJoins(seats.slice(0, 4)).length, 0, 'no short leg, no corner');
  assert.deepEqual(['north', 'east', 'south', 'west'].map((f) => rotOfFacing(f as 'north')), [0, 1, 2, 3]);
});

test('the scene draws a seated figure for every assigned seat of the shown shift, and the off shift look is its own', () => {
  const building = applyShiftView(project().buildings, 'active', DAY)[0];
  const people = new Map(building.floors[0].desks.map((d) => [d.id, d.agentId as string]));
  const live = buildBuildingScene(building.floors, { activeIndex: 0, people, deskState: new Map() });
  const off = buildBuildingScene(building.floors, { activeIndex: 0, people, deskState: new Map(building.floors[0].desks.map((d) => [d.id, 'offshift' as const])) });
  assert.equal(live.desks.length, 7, 'one handle per desk of the shown shift, none for an empty table');
  assert.ok(live.triangles > 0 && off.triangles > 0);
  const nobody = buildBuildingScene(building.floors, { activeIndex: 0, people: new Map(), deskState: new Map() });
  assert.ok(nobody.triangles < live.triangles, 'a figure is drawn only for an assigned desk');
  const other = applyShiftView(project().buildings, 'other', DAY)[0];
  const sceneOther = buildBuildingScene(other.floors, { activeIndex: 0, people: new Map(other.floors[0].desks.map((d) => [d.id, d.agentId as string])) });
  assert.equal(sceneOther.desks.length, 4, 'desks of the other shift view: three night seats and the manager; empty tables have no handle');
  assert.notEqual(lookSignature('offshift'), lookSignature('vacant'));
  assert.equal(STATE_LOOK.offshift.screen[0] < 0.2, true, 'the off shift screen is dark');
  assert.equal(STATE_LOOK.offshift.content, false);
});

test('seat labels read in Arabic and stay as typed in English', () => {
  setLocale('ar');
  assert.notEqual(deskLabel('Day seat 3'), 'Day seat 3');
  assert.notEqual(deskLabel('Manager desk'), 'Manager desk');
  assert.equal(deskLabel('My own label'), 'My own label');
  setLocale('en');
  assert.equal(deskLabel('Night seat 2'), 'Night seat 2');
});

test('presence truth: a screen is lit only for fresh work of a person who is on shift; off shift and unknown stay dim', async () => {
  const { deskVisual } = await import('../src/lib/hqDeskState.ts');
  const day = applyShiftView(project().buildings, 'active', DAY)[0].floors[0].desks;
  const night = applyShiftView(project().buildings, 'other', DAY)[0].floors[0].desks.filter((d) => d.shift === 'night');
  const presence = (status: string, stale = false) => ({ agentId: 'a', name: 'A', role: null, status, taskTitles: ['t'], observedAt: '2026-10-26T08:00:00Z', stale }) as never;
  assert.equal(deskVisual(day[0], presence('working')), 'working');
  assert.equal(deskVisual(day[0], presence('queued')), 'queued');
  assert.equal(deskVisual(day[0], presence('working', true)), 'unknown', 'a stale record proves nothing');
  assert.equal(deskVisual(day[0], undefined), 'assigned', 'assigned with no record is not working');
  for (const status of ['working', 'queued', 'waiting_approval', 'blocked', 'meeting', 'idle', 'paused']) {
    assert.equal(deskVisual(night[0], presence(status)), 'offshift', `an off shift person is never lit (${status})`);
  }
  assert.equal(deskVisual({ agentId: null, shiftInfo: undefined }, undefined), 'vacant', 'nobody is drawn at a desk with no one');
  const manager = day.find((d) => d.seatKind === 'manager')!;
  assert.equal(deskVisual(manager, presence('working')), 'working', 'the manager seat is always on');
  assert.equal(WORK_STATUSES.has('working'), true);
});

test('role titles are translated only for the roles the action sets', async () => {
  const { roleText } = await import('../src/lib/hqRoles.ts');
  setLocale('ar');
  assert.notEqual(roleText('PMV manager'), 'PMV manager');
  assert.equal(roleText('Sales and outreach'), 'Sales and outreach', 'any other role stays as stored');
  setLocale('en');
  assert.equal(roleText('PMV manager'), 'PMV manager');
});
