import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import { projectHeadquarters, type Registry, type SceneDesk } from '../src/lib/headquartersProjection.ts';
import { activeShiftKey, applyShifts, clockOf, deskShiftLine, hoursOf, inWindow, minutesInZone, offShiftKeys, roomWindows, shiftAt, shiftAtMinutes, windowOf, WORK_STATUSES } from '../src/lib/hqShifts.ts';
import { postOccupants } from '../src/lib/hqPosts.ts';
import { totalsOf, type HqAgentPresence } from '../src/lib/hqPresence.ts';
import { commandWall } from '../src/lib/hqCommandWall.ts';
import { addTotals, buildingStatus, donutArcs, EMPTY_TOTALS, type CampusBuilding } from '../src/lib/campusModel.ts';
import { registryBuildingFacts } from '../src/lib/campusRegistry.ts';
import { rotOfFacing, tableJoins } from '../src/lib/hqSeatTables.ts';
import { STATE_LOOK, lookSignature } from '../src/hqAssets/interior/stateVisual.ts';
import { deskLabel } from '../src/lib/registryNames.ts';
import { setLocale } from '../src/lib/i18n.ts';

type FxPerson = { key: string; name: string; role: string; seat_kind: string; work_start: string; work_end: string; desk_id: string; agent_id: string };
type Fx = { building: Record<string, unknown>; floor: Record<string, unknown>; room: Record<string, unknown>; desks: Record<string, unknown>[]; people: FxPerson[] };
const fx = JSON.parse(readFileSync(new URL('./fixtures/greenConcrete.json', import.meta.url), 'utf8')) as Fx;
const rev = { revision: 1 };
const registry = (building: Record<string, unknown> = {}, desks: Record<string, unknown>[] = fx.desks): Registry => ({
  buildings: [{ ...fx.building, ...rev, ...building }], floors: [{ ...fx.floor, ...rev }], rooms: [{ ...fx.room, ...rev }],
  desks: desks.map((d) => ({ ...d, ...rev })),
} as unknown as Registry);
const scope = { tenantId: 't', workspaceId: 'w' };
const project = (building?: Record<string, unknown>, desks?: Record<string, unknown>[]) => projectHeadquarters(scope, registry(building, desks), []);
// Dubai is UTC+4 all year: a local hour h is UTC h - 4.
const dubai = (h: number, m = 0) => Date.UTC(2026, 9, 26, h - 4, m);
const DAY = dubai(12);
const nameOf = new Map(fx.people.map((p) => [p.desk_id, p.name]));
const onAt = (ms: number) => applyShifts(project().buildings, ms)[0].floors[0].desks.filter((d) => d.shiftInfo?.onShift).map((d) => nameOf.get(d.id)).sort();
const sorted = (names: string[]) => [...names].sort();
// The shape a build made before the working hours has: day and night seats, the night people at the places of seats 1 to 3.
const older = fx.desks.map((d, i) => ({ ...d, work_start: null, work_end: null,
  shift: d.seat_kind === 'manager' ? null : i >= 6 ? 'night' : 'day', ...(i < 6 ? { label: `Day seat ${i + 1}` } : {}),
  ...(i >= 6 && i < 9 ? { pos_x: fx.desks[i - 6].pos_x, pos_z: fx.desks[i - 6].pos_z, label: `Night seat ${i - 5}` } : {}) }));

test('working hours: inside the window, over midnight, in the building time zone', () => {
  assert.ok(inWindow(8 * 60, '08:00', '17:00') && !inWindow(17 * 60, '08:00', '17:00') && !inWindow(7 * 60 + 59, '08:00', '17:00'));
  assert.ok(inWindow(23 * 60, '22:00', '06:00') && inWindow(2 * 60, '22:00', '06:00') && !inWindow(6 * 60, '22:00', '06:00') && !inWindow(12 * 60, '22:00', '06:00'));
  assert.equal(minutesInZone(DAY, 'Asia/Dubai'), 12 * 60);
  assert.equal(minutesInZone(DAY, 'Not/AZone'), 8 * 60, 'an unknown zone reads UTC and never throws');
  assert.equal(clockOf(18 * 60 + 30), '18:30');
});

test('older day and night seats still read the building hours (defaults until the owner saves them)', () => {
  const hours = hoursOf(null, null);
  assert.deepEqual(hours, { dayStart: '07:00', nightStart: '19:00', isDefault: true });
  assert.equal(shiftAt(DAY, 'Asia/Dubai', hours), 'day');
  assert.equal(shiftAt(dubai(23), 'Asia/Dubai', hours), 'night');
  assert.equal(shiftAt(Date.UTC(2026, 9, 26, 4, 0), 'UTC', hours), 'night', 'the same instant is another shift in another zone');
  assert.equal(shiftAtMinutes(6 * 60 + 59, hours), 'night');
  assert.equal(shiftAtMinutes(7 * 60, hours), 'day');
  const edited = hoursOf('06:30', '18:30');
  assert.equal(edited.isDefault, false);
  assert.deepEqual(windowOf('night', edited), { start: '18:30', end: '06:30' });
  assert.equal(hoursOf('bad', null).isDefault, true, 'a bad hour is the default, not a guess');
});

test('the projection keeps the saved seat positions, the facing and the working hours of each seat', () => {
  const desks = project().buildings[0].floors[0].desks;
  assert.equal(desks.length, 10);
  const raed = desks.find((d) => d.id === fx.people[0].desk_id) as SceneDesk;
  const room = project().buildings[0].floors[0].rooms[0];
  assert.deepEqual([raed.position.x, raed.position.z], [room.x + 4.8, room.z + 4.2]);
  assert.equal(raed.shift, null);
  assert.equal(raed.facing, 'north');
  assert.deepEqual([raed.workStart, raed.workEnd], ['08:00', '17:00']);
  const amjad = desks.find((d) => d.id === fx.people.find((p) => p.key === 'amjad')!.desk_id) as SceneDesk;
  assert.deepEqual([amjad.workStart, amjad.workEnd, amjad.facing], ['13:00', '22:00', 'west']);
  const manager = desks[desks.length - 1];
  assert.equal(manager.seatKind, 'manager');
  assert.equal(new Set(desks.map((d) => `${d.position.x}:${d.position.z}`)).size, 10, 'nobody shares a place');
});

test('who is on shift at 07:00, 12:00, 16:00, 18:30 and 21:00 in Dubai, from each person\'s own hours', () => {
  const morning = ['Ijaz', 'Ali Zeehan', 'Rana'];
  const office = ['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Badea', 'Ahmed Elbendari'];
  assert.deepEqual(onAt(dubai(7)), sorted(morning));
  assert.deepEqual(onAt(dubai(12)), sorted([...morning, ...office]));
  assert.deepEqual(onAt(dubai(16)), sorted([...office, 'Amjad']));
  assert.deepEqual(onAt(dubai(18, 30)), ['Amjad']);
  assert.deepEqual(onAt(dubai(21)), ['Amjad']);
  assert.deepEqual(onAt(dubai(23)), [], 'nobody works at night: there is no night shift');
  for (const ms of [dubai(7), dubai(23)]) assert.equal(applyShifts(project().buildings, ms)[0].floors[0].desks.length, 10, 'everyone keeps their desk on and off shift');
  // Hours over midnight work for an owner who adds them later.
  const late = fx.desks.map((d, i) => (i === 7 ? { ...d, work_start: '22:00', work_end: '06:00' } : d));
  assert.ok(applyShifts(project({}, late).buildings, dubai(23))[0].floors[0].desks[7].shiftInfo?.onShift);
  assert.ok(!applyShifts(project({}, late).buildings, dubai(12))[0].floors[0].desks[7].shiftInfo?.onShift);
});

test('the hours are facts, not defaults; the scene is rebuilt only when somebody comes on or goes off shift', () => {
  const desks = applyShifts(project().buildings, DAY)[0].floors[0].desks;
  assert.ok(desks.every((d) => d.shiftInfo?.own === true && d.shiftInfo.hoursAreDefault === false));
  assert.equal(activeShiftKey(project().buildings, DAY), activeShiftKey(project().buildings, DAY + 60_000));
  assert.notEqual(activeShiftKey(project().buildings, dubai(12)), activeShiftKey(project().buildings, dubai(16)));
  assert.notEqual(activeShiftKey(project().buildings, dubai(13, 59)), activeShiftKey(project().buildings, dubai(14)), 'the morning shift ends at 14:00');
});

test('an earlier build with day and night seats keeps working, and never draws two people at one place', () => {
  const noon = applyShifts(project({}, older).buildings, DAY)[0];
  const desks = noon.floors[0].desks;
  assert.deepEqual(desks.map((d) => d.label).sort(), ['Day seat 1', 'Day seat 2', 'Day seat 3', 'Day seat 4', 'Day seat 5', 'Day seat 6', 'Manager desk'].sort());
  assert.equal(new Set(desks.map((d) => `${d.position.x}:${d.position.z}`)).size, desks.length, 'one person per place');
  assert.ok(desks.filter((d) => d.shift === 'day').every((d) => d.shiftInfo?.onShift && d.shiftInfo.hoursAreDefault && !d.shiftInfo.own));
  assert.equal(desks.filter((d) => d.shift === 'night').length, 0, 'the night seats at the day places wait for their shift');
  const night = applyShifts(project({}, older).buildings, dubai(23))[0].floors[0].desks;
  assert.deepEqual(night.filter((d) => d.shift === 'night').map((d) => d.label), ['Night seat 1', 'Night seat 2', 'Night seat 3']);
  assert.ok(night.filter((d) => d.shift === 'night').every((d) => d.shiftInfo?.onShift));
  assert.ok(night.filter((d) => d.shift === 'day').every((d) => !d.shiftInfo?.onShift), 'day seats nobody shares are shown off shift');
  assert.equal(applyShifts(project({ shift_day_start: '13:00', shift_night_start: '23:00' }, older).buildings, DAY)[0].floors[0].desks.filter((d) => d.shift === 'night').length, 3, 'saved hours move the change');
});

test('a building without seats that have hours is returned as it is', () => {
  const plain = projectHeadquarters(scope, { ...registry(), desks: [] }, []);
  assert.equal(applyShifts(plain.buildings, DAY), plain.buildings);
});

test('what a desk says about its hours: off shift is never absent, and waiting work is told honestly', () => {
  const at7 = applyShifts(project().buildings, dubai(7))[0].floors[0].desks;
  const raed = at7.find((d) => d.id === fx.people[0].desk_id) as SceneDesk;
  assert.equal(deskShiftLine(raed.shiftInfo, false), 'off');
  assert.equal(deskShiftLine(raed.shiftInfo, true), 'off_waiting');
  const manager = at7.find((d) => d.seatKind === 'manager') as SceneDesk;
  assert.equal(deskShiftLine(manager.shiftInfo, false), 'off', 'the manager has hours of his own now');
  assert.equal(deskShiftLine(applyShifts(project().buildings, DAY)[0].floors[0].desks.find((d) => d.seatKind === 'manager')!.shiftInfo, true), 'on');
  const noHours = fx.desks.map((d) => (d.seat_kind === 'manager' ? { ...d, work_start: null, work_end: null } : d));
  const bare = applyShifts(project({}, noHours).buildings, dubai(7))[0].floors[0].desks.find((d) => d.seatKind === 'manager') as SceneDesk;
  assert.equal(deskShiftLine(bare.shiftInfo, true), 'manager', 'a manager seat without hours is always on');
  assert.equal(deskShiftLine(undefined, true), null);
  assert.ok(WORK_STATUSES.has('queued') && WORK_STATUSES.has('waiting_approval') && !WORK_STATUSES.has('idle') && !WORK_STATUSES.has('paused'));
});

test('the room groups the hours of its people, and says which window is on now', () => {
  const desks = applyShifts(project().buildings, dubai(16))[0].floors[0].desks;
  assert.deepEqual(roomWindows(desks), [
    { start: '06:00', end: '14:00', people: 3, on: false, own: true },
    { start: '08:00', end: '17:00', people: 6, on: true, own: true },
    { start: '13:00', end: '22:00', people: 1, on: true, own: true },
  ]);
});

const team = () => fx.desks.filter((d) => d.seat_kind === 'team').map((d) => ({ x: d.pos_x as number, z: d.pos_z as number, facing: d.facing as 'north' | 'east' | 'west', group: d.table_group as string }));

test('the table: the long leg, a leg at each end, a corner piece at each corner, facing gives the turn', () => {
  const joins = tableJoins(team());
  assert.equal(joins.length, 2, 'the right leg and the left leg are both joined to the long leg');
  assert.deepEqual(joins.map((j) => j.outer).sort(), [-1, 1]);
  assert.ok(joins.every((j) => j.width > 0.5 && j.depth > 1.5), 'each corner covers the run from the long leg to the first seat of the leg');
  assert.equal(tableJoins(team().slice(0, 4)).length, 0, 'no short leg, no corner');
  assert.deepEqual(['north', 'east', 'south', 'west'].map((f) => rotOfFacing(f as 'north')), [0, 1, 2, 3]);
});

test('the scene draws a seated figure only for a person on shift, and the off shift look is its own', () => {
  const at = (ms: number) => applyShifts(project().buildings, ms)[0];
  const noon = at(DAY);
  const occupants = postOccupants(noon.floors, { presence: null });
  assert.deepEqual([...occupants.keys()].map((id) => nameOf.get(id)).sort(), onAt(DAY), 'a figure for each person inside their hours');
  const evening = at(dubai(18, 30));
  assert.deepEqual([...postOccupants(evening.floors, { presence: null }).keys()].map((id) => nameOf.get(id)), ['Amjad']);
  const live = buildBuildingScene(noon.floors, { activeIndex: 0, people: occupants, deskState: new Map() });
  assert.equal(live.desks.length, 10, 'one handle per desk, on or off shift');
  const quiet = buildBuildingScene(evening.floors, { activeIndex: 0, people: postOccupants(evening.floors, { presence: null }), deskState: new Map(evening.floors[0].desks.map((d) => [d.id, d.shiftInfo?.onShift ? 'assigned' as const : 'offshift' as const])) });
  assert.ok(quiet.triangles < live.triangles, 'fewer figures in the evening');
  assert.notEqual(lookSignature('offshift'), lookSignature('vacant'));
  assert.equal(STATE_LOOK.offshift.screen[0] < 0.2, true, 'the off shift screen is dark');
  assert.equal(STATE_LOOK.offshift.content, false);
});

test('seat labels read in Arabic and stay as typed in English', () => {
  setLocale('ar');
  assert.notEqual(deskLabel('Seat 3'), 'Seat 3');
  assert.notEqual(deskLabel('Day seat 3'), 'Day seat 3', 'older seat labels too');
  assert.notEqual(deskLabel('Manager desk'), 'Manager desk');
  assert.equal(deskLabel('My own label'), 'My own label');
  setLocale('en');
  assert.equal(deskLabel('Seat 2'), 'Seat 2');
});

test('presence truth: a screen is lit only for fresh work of a person who is on shift; off shift and unknown stay dim', async () => {
  const { deskVisual } = await import('../src/lib/hqDeskState.ts');
  const noon = applyShifts(project().buildings, DAY)[0].floors[0].desks;
  const raed = noon.find((d) => d.id === fx.people[0].desk_id)!;
  const amjad = noon.find((d) => d.id === fx.people.find((p) => p.key === 'amjad')!.desk_id)!;
  const presence = (status: string, stale = false) => ({ agentId: 'a', name: 'A', role: null, status, taskTitles: ['t'], observedAt: '2026-10-26T08:00:00Z', stale }) as never;
  assert.equal(deskVisual(raed, presence('working')), 'working');
  assert.equal(deskVisual(raed, presence('queued')), 'queued');
  assert.equal(deskVisual(raed, presence('working', true)), 'unknown', 'a stale record proves nothing');
  assert.equal(deskVisual(raed, undefined), 'assigned', 'assigned with no record is not working');
  for (const status of ['working', 'queued', 'waiting_approval', 'blocked', 'meeting', 'idle', 'paused']) {
    assert.equal(deskVisual(amjad, presence(status)), 'offshift', `a person outside their hours is never lit (${status})`);
  }
  assert.equal(deskVisual({ agentId: null, shiftInfo: undefined }, undefined), 'vacant', 'nobody is drawn at a desk with no one');
  const manager = noon.find((d) => d.seatKind === 'manager')!;
  assert.equal(deskVisual(manager, presence('working')), 'working', 'the manager is on in his hours');
});

test('role titles are translated only for the roles the action sets', async () => {
  const { roleText } = await import('../src/lib/hqRoles.ts');
  setLocale('ar');
  assert.notEqual(roleText('PMV manager'), 'PMV manager');
  assert.equal(roleText('Sales and outreach'), 'Sales and outreach', 'any other role stays as stored');
  setLocale('en');
  assert.equal(roleText('PMV manager'), 'PMV manager');
});

// The same table mirrored about the room's centre line: the legs swap ends and their people look the other way.
const mirrored = (width: number) => fx.desks.map((d) => (d.table_group === 'l-table'
  ? { ...d, pos_x: Math.round((width - (d.pos_x as number)) * 100) / 100, facing: d.facing === 'east' ? 'west' : d.facing === 'west' ? 'east' : d.facing } : d));

test('the L corner is built for a right and a left L, towards the door or the wall, and a corner seat needs none', () => {
  const right = team().slice(0, 6).map((s) => ({ ...s, group: 'l' }));
  const [r] = tableJoins(right);
  assert.equal(tableJoins(right).length, 1);
  assert.equal(r.outer, 1);
  const left = right.map((s) => ({ ...s, x: 16 - s.x, facing: s.facing === 'east' ? 'west' as const : s.facing }));
  const joins = tableJoins(left);
  assert.equal(joins.length, 1, 'a left L gets its corner');
  const [l] = joins;
  assert.equal(l.outer, -1);
  assert.ok(Math.abs(l.x + l.width - (16 - r.x)) < 1e-9 && Math.abs(l.z - r.z) < 1e-9 && Math.abs(l.depth - r.depth) < 1e-9, 'the left corner is the mirror of the right one');
  const towardsWall = right.map((s) => (s.facing === 'east' ? { ...s, z: 2 * 4.2 - s.z } : s));
  const [w] = tableJoins(towardsWall);
  assert.ok(w && w.z + w.depth > 4.2 && w.z < 4.2, 'a leg running towards the wall is joined on the other side of the row');
  const corner = [...right.slice(0, 4), { x: 10.44, z: 4.5, facing: 'east' as const, group: 'l' }];
  assert.equal(tableJoins(corner).length, 0, 'a corner seat whose table covers the corner needs no extra piece');
});

test('a mirrored Green Concrete room draws the same table in the scene, with both corners and every seat', () => {
  const reg = { ...registry(), desks: mirrored(16).map((d) => ({ ...d, ...rev })) } as unknown as Registry;
  const building = applyShifts(projectHeadquarters(scope, reg, []).buildings, DAY)[0];
  const seats = building.floors[0].desks.filter((d) => d.tableGroup === 'l-table');
  assert.ok(seats.filter((d) => d.facing === 'west').length === 2 && seats.filter((d) => d.facing === 'east').length === 3 && seats.filter((d) => d.facing === 'north').length === 4);
  assert.deepEqual(tableJoins(seats.map((d) => ({ x: d.position.x, z: d.position.z, facing: d.facing ?? null, group: d.tableGroup ?? null }))).map((j) => j.outer).sort(), [-1, 1]);
  const people = new Map(building.floors[0].desks.map((d) => [d.id, d.agentId as string]));
  const scene = buildBuildingScene(building.floors, { activeIndex: 0, people, deskState: new Map() });
  assert.equal(scene.desks.length, 10, 'every seat and the manager');
  const straight = applyShifts(project().buildings, DAY)[0];
  const original = buildBuildingScene(straight.floors, { activeIndex: 0, people: new Map(straight.floors[0].desks.map((d) => [d.id, d.agentId as string])), deskState: new Map() });
  assert.equal(scene.triangles, original.triangles, 'the mirrored table draws the same furniture, corner pieces included');
});

test('people outside their hours count as Off shift everywhere a count is made, never idle or missing', () => {
  const raw = project().buildings;
  const at = (ms: number) => offShiftKeys(raw, ms);
  const ids = (keys: ReadonlySet<string>) => [...keys].filter((k) => !k.startsWith('desk:')).sort();
  const agent = (key: string) => fx.people.find((p) => p.key === key)!.agent_id;
  assert.deepEqual(ids(at(DAY)), [agent('amjad')]);
  assert.deepEqual(ids(at(dubai(7))), fx.people.filter((p) => !['ijaz', 'ali-zeehan', 'rana'].includes(p.key)).map((p) => p.agent_id).sort());
  assert.deepEqual(ids(at(dubai(21))), fx.people.filter((p) => p.key !== 'amjad').map((p) => p.agent_id).sort());
  // Older seats: the day and night split from the building's hours.
  const olderRaw = project({}, older).buildings;
  assert.equal(ids(offShiftKeys(olderRaw, DAY)).length, 3, 'the three night seats at noon');
  assert.ok(ids(offShiftKeys(project({ shift_day_start: '13:00', shift_night_start: '23:00' }, older).buildings, DAY)).includes(agent('raed')), 'saved hours move the edge');
  const presence: HqAgentPresence[] = fx.people.map((p) => ({ agentId: p.agent_id, name: p.name, role: null, status: 'idle', taskTitles: [], observedAt: '2026-10-26T08:00:00Z', deskId: p.desk_id }));
  const totals = totalsOf(presence, at(DAY));
  assert.equal(totals.agents, 10);
  assert.equal(totals.off_shift, 1);
  assert.equal(totals.idle, 9, 'only the people inside their hours are idle');
  assert.equal(donutArcs(totals).reduce((n, a) => n + a.value, 0), 10, 'the donut still adds up to every agent');
  const wall = commandWall({ status: 'ready', snapshot: null }, presence.map((p) => ({ agent_id: p.agentId, state: at(DAY).has(p.agentId) ? 'off_shift' : p.status })));
  assert.ok(wall.agents.state === 'ready' && wall.agents.total === 10 && wall.agents.byState.off_shift === 1 && wall.agents.byState.idle === 9);
  const index = { byAgent: new Map(presence.map((p) => [p.agentId, p])), byDesk: new Map() };
  const facts = registryBuildingFacts(raw[0], index, at(dubai(7)));
  assert.equal(facts.totals?.off_shift, 7);
  assert.equal(addTotals(totals, EMPTY_TOTALS).off_shift, 1);
  const allOff = { totals: { ...EMPTY_TOTALS, agents: 3, off_shift: 3 } } as unknown as CampusBuilding;
  assert.equal(buildingStatus(allOff), 'off_shift');
  // Without the set the old count stands: nothing changes for a building with no hours.
  assert.equal(totalsOf(presence).off_shift, 0);
  assert.equal(offShiftKeys(project({}, fx.desks.map((d) => ({ ...d, shift: null, work_start: null, work_end: null }))).buildings, DAY).size, 0);
});
