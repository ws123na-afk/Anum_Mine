import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import { buildBuildingScene, floorBoxOf, frameBuilding } from '../src/lib/buildingScene.ts';
import type { RoomGeometry, SceneFloor } from '../src/lib/headquartersProjection.ts';
import {
  INTERIOR_KINDS, REFERENCE_FRAMES, ambientTag, buildInteriorMesh, interiorKindFor, layoutStations, parseAmbientTag, poseAt, turnOffset, unturnOffset,
  type DoorSide, type InteriorKind,
} from '../src/hqAssets/interior/index.ts';
import { doorWall, doorWidthFor, drawRoomShell } from '../src/hqAssets/interior/shell.ts';
import { CORE_DEPTH, CORE_WIDTH, drawCore } from '../src/hqAssets/interior/core.ts';

const SIDES: readonly DoorSide[] = ['south', 'north', 'east', 'west'];

test('turning a room and turning it back is the identity, and a south door changes nothing', () => {
  for (const side of SIDES) {
    const [x, z] = turnOffset(side, 3.25, -1.5);
    const [bx, bz] = unturnOffset(side, x, z);
    assert.ok(Math.abs(bx - 3.25) < 1e-9 && Math.abs(bz + 1.5) < 1e-9, side);
  }
  assert.deepEqual(turnOffset('south', 2, 3), [2, 3]);
  // The kit front (+z) faces the door side after the turn.
  assert.deepEqual(turnOffset('north', 0, 1), [-0, -1]);
  assert.deepEqual(turnOffset('east', 0, 1), [1, -0]);
  assert.deepEqual(turnOffset('west', 0, 1), [-1, 0]);
});

test('every kit stays inside its frame whichever wall the door is in, and a turned room keeps its count', () => {
  for (const kind of INTERIOR_KINDS) {
    const f = REFERENCE_FRAMES[kind];
    const frame = { x: 20, y: 4.2, z: -9, width: f.width * 1.1, depth: f.depth * 1.3 };
    const base = buildInteriorMesh(kind, frame, { seed: 'door' }).triangles;
    // East and west dress the room across its other axis, so they share one count; north is south turned half way.
    const across = buildInteriorMesh(kind, frame, { seed: 'door', doorSide: 'east' }).triangles;
    for (const side of SIDES) {
      const mesh = buildInteriorMesh(kind, frame, { seed: 'door', doorSide: side });
      assert.equal(mesh.triangles, side === 'east' || side === 'west' ? across : base, `${kind} ${side}`);
      const { min, max } = mesh.bounds;
      assert.ok(min.x >= frame.x - 0.03 && max.x <= frame.x + frame.width + 0.03 && min.z >= frame.z - 0.03 && max.z <= frame.z + frame.depth + 0.03, `${kind} ${side} stays in the frame`);
    }
  }
});

test('stations keep their registry positions in a turned room', () => {
  const frame = { x: 0, y: 0, z: 0, width: 10, depth: 7 };
  const stations = layoutStations(frame, ['a', 'b', 'c']).map((s, i) => ({ ...s, occupantSeed: `agent-${i}` }));
  for (const side of SIDES) {
    const mesh = buildInteriorMesh('office', frame, { seed: 's', stations, doorSide: side });
    const plain = buildInteriorMesh('office', frame, { seed: 's', stations: [], doorSide: side });
    // Three desks with three people add the same geometry on every side.
    assert.equal(mesh.triangles - plain.triangles, buildInteriorMesh('office', frame, { seed: 's', stations }).triangles - buildInteriorMesh('office', frame, { seed: 's', stations: [] }).triangles, side);
  }
});

test('room names pick the right kit and every registry kind has one', () => {
  const cases: Array<[string, string, InteriorKind]> = [
    ['office', 'Finance Office', 'finance'], ['office', 'Sales Office', 'sales'], ['office', 'Command Center', 'operations'],
    ['office', 'AI Labs', 'server'], ['office', 'Customer Service', 'customer-care'], ['office', 'Executive Office', 'executive'],
    ['office', 'Staff Pantry', 'pantry'], ['office', 'Restrooms', 'restroom'], ['office', 'Facilities Office', 'office'],
    ['workshop', 'Workshop', 'workshop'], ['warehouse', 'Warehouse', 'warehouse'], ['lounge', 'Lounge', 'lounge'], ['reception', 'Reception', 'reception'], ['meeting', 'Meeting Room A', 'meeting'],
  ];
  for (const [kind, name, want] of cases) assert.equal(interiorKindFor(kind, name), want, name);
});

const room = (id: string, x: number, z: number, w: number, d: number, side?: DoorSide, label = id, kind: RoomGeometry['kind'] = 'office'): RoomGeometry => ({
  id, floorId: 'f', buildingId: 'b', label, kind, x, z, width: w, depth: d, height: 3.2, capacity: 4, revision: 1, deskIds: [], evidence: 'registry', ...(side ? { doorSide: side } : {}),
});

test('a door opening is cut into the wall the room names, and no other wall has one', () => {
  for (const side of SIDES) {
    const r = room('r', 2, 3, 8, 6, side);
    const opaque = new MeshBuilder(), glass = new MeshBuilder(), amb = new MeshBuilder();
    const res = drawRoomShell(opaque, glass, amb, { x: r.x, z: r.z, width: r.width, depth: r.depth, y: 0, kind: 'office', registryKind: 'office', accent: [1, 1, 1], selected: false, ghost: false, doorSide: side, plate: null });
    assert.equal(res.doorWidth, doorWidthFor(side === 'north' || side === 'south' ? r.width : r.depth));
    const dw = doorWall(r.doorSide ? { x: r.x, z: r.z, width: r.width, depth: r.depth, doorSide: side } : { x: r.x, z: r.z, width: r.width, depth: r.depth, doorSide: 'south' });
    // The wall centre line sits half a wall thickness inside the outer face.
    const wallX = dw.cx - dw.nx * 0.05, wallZ = dw.cz - dw.nz * 0.05;
    const v = opaque.toFloat32();
    let blocked = 0;
    for (let i = 0; i < v.length; i += VERTEX_FLOATS * 3) {
      const xs = [0, 1, 2].map((k) => v[i + k * VERTEX_FLOATS]), ys = [0, 1, 2].map((k) => v[i + k * VERTEX_FLOATS + 1]), zs = [0, 1, 2].map((k) => v[i + k * VERTEX_FLOATS + 2]);
      if (Math.min(...ys) > 2.0 || Math.max(...ys) < 0.3) continue;
      const alongLo = dw.ax === 1 ? Math.min(...xs) : Math.min(...zs), alongHi = dw.ax === 1 ? Math.max(...xs) : Math.max(...zs);
      const centre = dw.ax === 1 ? dw.cx : dw.cz;
      const across = dw.ax === 1 ? zs : xs;
      const plane = dw.ax === 1 ? wallZ : wallX;
      // Inside the wall's thickness (sparing the door leaf parked 0.12 m in) and across the door centre.
      if (across.every((a) => Math.abs(a - plane) < 0.06) && alongLo < centre - 0.05 && alongHi > centre + 0.05) blocked++;
    }
    assert.equal(blocked, 0, `${side} door is open`);
  }
});

test('floors by kind differ and a ghost floor has no ceiling lights or glints', () => {
  const draw = (kind: InteriorKind | null, ghost: boolean) => {
    const opaque = new MeshBuilder(), glass = new MeshBuilder(), amb = new MeshBuilder();
    const res = drawRoomShell(opaque, glass, amb, { x: 0, z: 0, width: 8, depth: 6, y: 0, kind, registryKind: 'office', accent: [0.3, 0.8, 0.7], selected: false, ghost, doorSide: 'south', plate: null });
    return { res, tris: opaque.triangleCount, amb: amb.triangleCount, halos: opaque.halos.length / 8 };
  };
  const tones = new Set(INTERIOR_KINDS.map((k) => draw(k, false).res.floor.map((c) => c.toFixed(2)).join(',')));
  assert.ok(tones.size >= 8, `distinct floor finishes: ${tones.size}`);
  const lit = draw('office', false), ghost = draw('office', true);
  assert.ok(lit.amb > 0 && lit.halos > 0 && lit.halos <= 4, 'lights, a glint and at most four halos');
  assert.equal(ghost.amb, 0);
  assert.equal(ghost.halos, 0);
  assert.ok(ghost.tris < lit.tris);
});

test('the service core has lifts with moving indicators and a stair, within its footprint', () => {
  const opaque = new MeshBuilder(), glass = new MeshBuilder(), amb = new MeshBuilder();
  drawCore(opaque, glass, amb, { x: 30, z: 5, y: 4.2, plate: null, ghost: false });
  const b = opaque.bounds(), g = glass.bounds();
  assert.ok(b.min.x >= 30 - 0.2 && b.max.x <= 30 + CORE_WIDTH + 0.2 && b.min.z >= 5 - 0.2 && b.max.z <= 5 + CORE_DEPTH + 0.2);
  assert.ok(g.max.z <= 5 + CORE_DEPTH + 0.2);
  const lifts = new Set(amb.triangleTags().filter((t) => parseAmbientTag(t)?.kind === 'lift'));
  assert.equal(lifts.size, 2, 'one indicator per lift');
});

test('ambient motion: frozen is the rest pose, blink groups alternate, nothing implies work', () => {
  const kinds = ['spin', 'sway', 'slide', 'lift', 'blink'] as const;
  for (const kind of kinds) {
    const tag = ambientTag(kind, 4, 0.5, 'x', 0);
    const m = parseAmbientTag(tag)!;
    assert.equal(m.kind, kind);
    const frozen = poseAt(m, 123.4, true);
    assert.deepEqual({ ...frozen, visible: true }, { spin: 0, dx: 0, dy: 0, dz: 0, visible: true }, `${kind} frozen`);
    // Deterministic and periodic.
    assert.deepEqual(poseAt(m, 1.3, false), poseAt(m, 1.3 + 4, false), kind);
  }
  const a = parseAmbientTag(ambientTag('blink', 2, 0, 'y', 0))!, b = parseAmbientTag(ambientTag('blink', 2, 0, 'y', 1))!;
  for (const t of [0.1, 0.6, 1.1, 1.6]) assert.notEqual(poseAt(a, t, false).visible, poseAt(b, t, false).visible);
  assert.equal(parseAmbientTag('figure'), null);
  assert.equal(parseAmbientTag(ambientTag('spin', 0, 0)), null);
  const lift = parseAmbientTag(ambientTag('lift', 10, 0.24, 'y', 0))!;
  const heights = new Set([0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5].map((t) => poseAt(lift, t, false).dy.toFixed(3)));
  assert.equal(heights.size, 4, 'four stops');
  assert.ok(Math.max(...[...heights].map(Number)) <= 0.24 + 1e-9);
});

test('a floor builds ambient parts that do not depend on desks, people or states', () => {
  const rooms = [room('a', 0, 0, 10, 8, undefined, 'Server Area'), room('b', 12, 0, 8, 7, 'west', 'Finance'), room('c', 0, 10, 8, 6, 'north', 'Pantry', 'lounge')];
  const floor: SceneFloor = {
    id: 'f', name: 'Ground', level: 0, rooms, bounds: { width: 40, depth: 30 },
    desks: [{ id: 'd1', roomId: 'b', label: 'Desk 1', position: { x: 15, y: 0.78, z: 3 }, agentId: 'ag', agent: null, assignmentVisible: true }],
  };
  const empty = buildBuildingScene([floor], { activeIndex: 0 });
  const busy = buildBuildingScene([floor], { activeIndex: 0, people: new Map([['d1', 'ag']]), deskState: new Map([['d1', 'working']]) });
  const count = (s: typeof empty) => s.ambient.reduce((n, p) => n + p.vertices.length / (VERTEX_FLOATS * 3), 0);
  assert.ok(empty.ambient.length >= 4, `parts: ${empty.ambient.length}`);
  assert.equal(count(empty), count(busy));
  const kinds = new Set(empty.ambient.map((p) => p.motion.kind));
  for (const k of ['slide', 'lift', 'blink', 'spin', 'sway']) assert.ok(kinds.has(k as never), `has ${k}`);
  for (const part of empty.ambient) {
    assert.ok(part.vertices.every(Number.isFinite));
    assert.ok(part.bounds.max.x >= part.bounds.min.x);
  }
  // Signs: entrance, three rooms, the floor.
  assert.equal(empty.signCount, 5);
  assert.deepEqual(empty.signs.map((s) => s.subtitle), ['Server Area', 'Finance', 'Pantry', 'Ground']);
});

test('the camera frames the active floor, its rooms and its core', () => {
  const rooms = [room('a', 0, 0, 10, 8), room('b', 12, 0, 8, 7)];
  const f: SceneFloor = { id: 'f', name: 'G', level: 0, rooms, desks: [], bounds: { width: 30, depth: 20 } };
  const box = floorBoxOf(f);
  assert.ok(box.core && box.x1 >= 20 + CORE_WIDTH && box.z1 - box.z0 >= CORE_DEPTH);
  const scene = buildBuildingScene([f], { activeIndex: 0 });
  const frame = frameBuilding(scene, 0);
  assert.equal(frame.target.x, (box.x0 + box.x1) / 2);
  assert.equal(frame.target.z, (box.z0 + box.z1) / 2);
  assert.equal(floorBoxOf({ ...f, rooms: [rooms[0]] }).core, null);
});
