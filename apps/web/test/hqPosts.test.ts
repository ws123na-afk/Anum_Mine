import assert from 'node:assert/strict';
import { test } from 'node:test';
import { roomMove } from '../src/hq-nav/navModel.ts';
import { MAX_MEETING_CHAIRS, deskOffShift, meetingAttendees, meetingChairs, postLook, postOccupants, postureFor, seatedInMeetings } from '../src/lib/hqPosts.ts';
import { figureLook } from '../src/lib/hqAvatar.ts';
import { indexPresence, type HqAgentPresence, type RoomMeeting } from '../src/lib/hqPresence.ts';
import type { RoomGeometry, SceneDesk, SceneFloor } from '../src/lib/headquartersProjection.ts';
import type { DeskShiftInfo } from '../src/lib/hqShifts.ts';
import { buildInteriorRoom } from '../src/hqAssets/interior/index.ts';
import { MeshBuilder } from '../src/lib/hqGeometry.ts';

const room = (id: string, kind: RoomGeometry['kind'] = 'office'): RoomGeometry => ({ id, floorId: 'f', buildingId: 'b', label: id, kind, x: 0, z: 0, width: 8, depth: 6, height: 3, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' });
const desk = (id: string, roomId: string, agentId: string | null, shiftInfo?: DeskShiftInfo): SceneDesk => ({ id, roomId, label: id, position: { x: 1, y: 0, z: 1 }, agentId, agent: null, assignmentVisible: true, ...(shiftInfo ? { shiftInfo } : {}) });
const floor = (id: string, level: number, rooms: RoomGeometry[], desks: SceneDesk[] = []): SceneFloor => ({ id, name: `Floor ${level}`, level, rooms, desks, bounds: { width: 10, depth: 10 } });
const shift = (onShift: boolean): DeskShiftInfo => ({ shift: 'day', manager: false, active: onShift ? 'day' : 'night', viewed: 'day', onShift, start: '07:00', end: '19:00', hoursAreDefault: true });
const presence = (agentId: string, deskId?: string): HqAgentPresence => ({ agentId, name: '', role: null, status: 'working', taskTitles: [], observedAt: '', source: 'presence-api', ...(deskId ? { deskId } : {}) } as HqAgentPresence);

// ---------------------------------------------------------------- floor switch decision

test('a room on another floor switches to that floor first; the same floor does not switch', () => {
  const b = { id: 'b1', floors: [floor('f0', 0, [room('lobby', 'reception')]), floor('f1', 1, [room('board', 'meeting')])] };
  assert.deepEqual(roomMove([b], { buildingId: 'b1', floorId: 'f0' }, 'board'), { buildingId: 'b1', floorId: 'f1', roomId: 'board', switchBuilding: false, switchFloor: true });
  assert.deepEqual(roomMove([b], { buildingId: 'b1', floorId: 'f1' }, 'board'), { buildingId: 'b1', floorId: 'f1', roomId: 'board', switchBuilding: false, switchFloor: false });
});

test('the saved layout decides the floor: a stale floor hint never opens the wrong floor', () => {
  const b = { id: 'b1', floors: [floor('f0', 0, [room('lobby')]), floor('f1', 1, [room('board')])] };
  assert.equal(roomMove([b], { buildingId: 'b1', floorId: 'f0' }, 'board', 'f0')?.floorId, 'f1');
  assert.equal(roomMove([b], { buildingId: 'b1', floorId: 'f0' }, 'board', 'nope')?.floorId, 'f1');
});

test('a room of another registry building opens that building and its floor', () => {
  const a = { id: 'a', floors: [floor('fa', 0, [room('ra')])] };
  const b = { id: 'b', floors: [floor('fb0', 0, [room('rb0')]), floor('fb1', 1, [room('rb1')])] };
  assert.deepEqual(roomMove([a, b], { buildingId: 'a', floorId: 'fa' }, 'rb1'), { buildingId: 'b', floorId: 'fb1', roomId: 'rb1', switchBuilding: true, switchFloor: true });
});

test('a room no saved floor holds changes nothing', () => {
  assert.equal(roomMove([{ id: 'b', floors: [floor('f0', 0, [room('r')])] }], { buildingId: 'b', floorId: 'f0' }, 'gone'), null);
  assert.equal(roomMove([], { buildingId: null, floorId: null }, 'r'), null);
});

// ---------------------------------------------------------------- who stands where

test('a person is drawn at a post only with an assignment or a presence record', () => {
  const f = floor('f0', 0, [room('o1')], [desk('d1', 'o1', 'a1'), desk('d2', 'o1', null), desk('d3', 'o1', null)]);
  const index = indexPresence([presence('desk:d3', 'd3')]);
  const out = postOccupants([f], { presence: index });
  assert.deepEqual([...out.entries()].sort(), [['d1', 'a1'], ['d3', 'desk:d3']]);
  assert.equal(out.has('d2'), false, 'an empty desk shows nobody');
});

test('a person off shift is not drawn at the post, whether the seat or the off-shift index says so', () => {
  const f = floor('f0', 0, [room('o1')], [desk('d1', 'o1', 'a1', shift(false)), desk('d2', 'o1', 'a2', shift(true)), desk('d3', 'o1', 'a3')]);
  const out = postOccupants([f], { presence: null, offShift: new Set(['a3']) });
  assert.deepEqual([...out.keys()], ['d2']);
  assert.equal(deskOffShift(desk('d9', 'o1', null), new Set(['desk:d9'])), true);
  assert.equal(deskOffShift(desk('d9', 'o1', 'a9')), false);
});

test('a person seated in a live meeting is not drawn a second time at the desk', () => {
  const f = floor('f0', 0, [room('o1'), room('m1', 'meeting')], [desk('d1', 'o1', 'a1'), desk('d2', 'o1', 'a2')]);
  const meetings = new Map<string, RoomMeeting>([['m1', { meetingId: 'mt', title: '', agentIds: ['a1'], people: 0 }]]);
  const inMeeting = seatedInMeetings(['o1', 'm1'], meetings);
  assert.deepEqual([...postOccupants([f], { presence: null, inMeeting }).keys()], ['d2']);
  // A meeting in a room of another building does not empty a desk here.
  assert.equal(seatedInMeetings(['o1'], meetings).size, 0);
});

test('reception and other rooms without workstations stand their people; office kits seat them', () => {
  assert.equal(postureFor('reception'), 'standing');
  assert.equal(postureFor('meeting'), 'standing');
  assert.equal(postureFor('lounge'), 'standing');
  assert.equal(postureFor('office'), 'seated');
  assert.equal(postureFor('customer-care'), 'seated');
  assert.equal(postureFor(null), 'seated');
});

test('a post look is the owner\'s choice, and neutral when none is set; it never depends on a name', () => {
  assert.equal(postLook('agent_1', null, { roomKind: 'reception' }).avatar.avatar_style, 'neutral');
  assert.equal(postLook('agent_1', { skin_tone: 'tone_4' }, { roomKind: 'reception' }).avatar.avatar_style, 'neutral');
  assert.equal(postLook('agent_1', { avatar_style: 'feminine' }, { roomKind: 'reception' }).avatar.avatar_style, 'feminine');
  for (let i = 0; i < 200; i++) assert.equal(postLook(`id_${i}`, null, { roomKind: 'office' }).avatar.avatar_style, 'neutral');
});

// ---------------------------------------------------------------- meeting attendees

test('the meeting room seats exactly the stored participants, agents then people, once each', () => {
  const meeting: RoomMeeting = { meetingId: 'm', title: 'Weekly', agentIds: ['a1', 'a2', 'a1'], humanIds: ['u1'], people: 1 };
  const styles = new Map([['a2', { avatar: { avatar_style: 'masculine' as const }, department: null }]]);
  const seated = meetingAttendees(meeting, styles, 14);
  assert.deepEqual(seated.map((a) => [a.seed, a.kind]), [['a1', 'agent'], ['a2', 'agent'], ['u1', 'human']]);
  assert.equal(seated[0].look.avatar.avatar_style, 'neutral');
  assert.equal(seated[1].look.avatar.avatar_style, 'masculine');
  assert.equal(seated[2].look.avatar.avatar_style, 'neutral', 'a person has no stored look: neutral');
});

test('no meeting, or a meeting nobody is listed in, seats nobody; more people than chairs fill the chairs', () => {
  assert.deepEqual(meetingAttendees(null, undefined, 14), []);
  assert.deepEqual(meetingAttendees({ meetingId: 'm', title: '', agentIds: [], people: 0 }, undefined, 14), []);
  const many: RoomMeeting = { meetingId: 'm', title: '', agentIds: Array.from({ length: 20 }, (_, i) => `a${i}`), people: 0 };
  assert.equal(meetingAttendees(many, undefined, 6).length, 6);
  assert.equal(meetingChairs(1.8), 6);
  assert.equal(MAX_MEETING_CHAIRS, meetingChairs(5.2));
});

const personSlots = (b: MeshBuilder) => b.slots.filter((s) => s.model === 'person');

test('the meeting kit draws one seated person per attendee and none without a meeting', () => {
  const frame = { x: 0, y: 0, z: 0, width: 8, depth: 6 };
  const empty = new MeshBuilder();
  buildInteriorRoom(empty, 'meeting', frame, { seed: 'm1', live: true, attendees: [] });
  assert.equal(personSlots(empty).length, 0);
  const full = new MeshBuilder();
  const attendees = ['a1', 'a2', 'u1'].map((seed) => ({ seed, look: figureLook(seed, { avatar_style: 'neutral' }) }));
  buildInteriorRoom(full, 'meeting', frame, { seed: 'm1', live: true, attendees, doorSide: 'east' });
  const slots = personSlots(full);
  assert.deepEqual(slots.map((s) => s.seed), ['a1', 'a2', 'u1']);
  assert.ok(slots.every((s) => !s.standing), 'attendees sit');
  // Every seat is inside the room.
  for (const s of slots) assert.ok(s.x > 0 && s.x < 8 && s.z > 0 && s.z < 6, `seat ${s.x},${s.z} in the room`);
});

test('the reception kit stands an assigned person behind the counter, and nobody at an empty desk', () => {
  const frame = { x: 0, y: 0, z: 0, width: 8, depth: 6 };
  const vacant = new MeshBuilder();
  buildInteriorRoom(vacant, 'reception', frame, { seed: 'r', stations: [{ x: 4, z: 3, occupantSeed: null }] });
  assert.equal(personSlots(vacant).length, 0);
  const staffed = new MeshBuilder();
  buildInteriorRoom(staffed, 'reception', frame, { seed: 'r', stations: [{ id: 'd1', x: 4, z: 3, occupantSeed: 'a1' }] });
  const slots = personSlots(staffed);
  assert.equal(slots.length, 1);
  assert.equal(slots[0].standing, true);
  assert.equal(slots[0].seed, 'a1');
});

test('a person at a post in a room with no workstations stands at the stored post', () => {
  const frame = { x: 0, y: 0, z: 0, width: 8, depth: 6 };
  const b = new MeshBuilder();
  buildInteriorRoom(b, 'lounge', frame, { seed: 'l', stations: [{ x: 2, z: 2, occupantSeed: 'a1' }, { x: 5, z: 2, occupantSeed: null }] });
  const slots = personSlots(b);
  assert.equal(slots.length, 1);
  assert.equal(slots[0].standing, true);
  assert.deepEqual([slots[0].x, slots[0].z], [2, 2]);
});
