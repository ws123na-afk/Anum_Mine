import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import {
  FIGURE_TRIANGLE_CAP, INTERIOR_KINDS, INTERIOR_TRIANGLE_CAP, REFERENCE_FRAMES, SCHEMES, STATION_TRIANGLE_CAP,
  buildInteriorMesh, buildInteriorRoom, interiorKindFor, layoutStations, resolveScheme, seatedFigure, standingFigure, workstation,
  type InteriorKind, type SeatedPose, type StandingPose,
} from '../src/hqAssets/interior/index.ts';
import { Local } from '../src/hqAssets/interior/local.ts';
import { MIN_FRAMES } from '../src/hqAssets/interior/index.ts';

const frameOf = (kind: InteriorKind, scaleBy = 1, x = 0, y = 0, z = 0) => ({
  x, y, z, width: REFERENCE_FRAMES[kind].width * scaleBy, depth: REFERENCE_FRAMES[kind].depth * scaleBy,
});

/** Rooms that draw the stations they are given (the others ignore them: their furniture is fixed). */
const STATION_KINDS: readonly InteriorKind[] = ['office', 'customer-care', 'operations', 'executive'];

test('every room kind builds within its measured triangle cap', () => {
  for (const kind of INTERIOR_KINDS) {
    const mesh = buildInteriorMesh(kind, frameOf(kind), { seed: 'cap' });
    assert.ok(mesh.triangles > 300, `${kind} has furniture (${mesh.triangles})`);
    assert.ok(mesh.triangles <= INTERIOR_TRIANGLE_CAP[kind], `${kind}: ${mesh.triangles} > ${INTERIOR_TRIANGLE_CAP[kind]}`);
    assert.equal(mesh.vertices.length, mesh.triangles * 3 * VERTEX_FLOATS);
  }
});

test('the same seed gives the same room and a seed changes the stock rooms', () => {
  for (const kind of INTERIOR_KINDS) {
    const a = buildInteriorMesh(kind, frameOf(kind), { seed: 'one' });
    const b = buildInteriorMesh(kind, frameOf(kind), { seed: 'one' });
    assert.deepEqual(Array.from(a.vertices), Array.from(b.vertices), kind);
  }
  const w1 = buildInteriorMesh('warehouse', frameOf('warehouse'), { seed: 'one' });
  const w2 = buildInteriorMesh('warehouse', frameOf('warehouse'), { seed: 'two' });
  assert.notDeepEqual(Array.from(w1.vertices), Array.from(w2.vertices));
});

test('geometry stays inside the room frame, from a small room to a large one, at any offset', () => {
  const tolerance = 0.03;
  for (const kind of INTERIOR_KINDS) {
    for (const k of [0.5, 0.8, 1, 1.5, 2.2]) {
      const frame = frameOf(kind, k, 12.5, 4.2, -7);
      const mesh = buildInteriorMesh(kind, frame, { seed: 'bounds' });
      const { min, max } = mesh.bounds;
      const label = `${kind} x${k}`;
      assert.ok(min.x >= frame.x - tolerance && max.x <= frame.x + frame.width + tolerance, `${label} x ${min.x}..${max.x}`);
      assert.ok(min.z >= frame.z - tolerance && max.z <= frame.z + frame.depth + tolerance, `${label} z ${min.z}..${max.z}`);
      assert.ok(min.y >= frame.y - 0.001, `${label} below the floor ${min.y}`);
      assert.ok(max.y <= frame.y + 3.0 + 1e-6, `${label} through the ceiling ${max.y - frame.y}`);
    }
  }
});

test('a frame smaller than the minimum is dressed at the minimum and shrunk, not cropped', () => {
  for (const kind of INTERIOR_KINDS) {
    const min = MIN_FRAMES[kind];
    const full = buildInteriorMesh(kind, { x: 0, y: 0, z: 0, ...min }, { seed: 'm' });
    const small = buildInteriorMesh(kind, { x: 0, y: 0, z: 0, width: min.width * 0.6, depth: min.depth * 0.9 }, { seed: 'm' });
    assert.equal(small.triangles, full.triangles, kind);
    assert.ok(small.bounds.max.x <= min.width * 0.6 + 0.03, kind);
    assert.ok(small.bounds.max.y < full.bounds.max.y * 0.7, kind);
  }
});

test('all vertices are finite, normals have unit length and colours are in range', () => {
  for (const kind of INTERIOR_KINDS) {
    const v = buildInteriorMesh(kind, frameOf(kind), { seed: 'finite', live: true }).vertices;
    for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
      for (let k = 0; k < VERTEX_FLOATS; k++) assert.ok(Number.isFinite(v[i + k]), `${kind} vertex ${i / VERTEX_FLOATS}`);
      assert.ok(Math.abs(Math.hypot(v[i + 3], v[i + 4], v[i + 5]) - 1) < 1e-4, `${kind} normal`);
      for (let k = 6; k < 9; k++) assert.ok(v[i + k] >= 0 && v[i + k] <= 1.0001, `${kind} colour`);
      // 2 or more is the renderer's window convention: only the meeting room's glass wall uses it.
      assert.ok(v[i + 9] >= 0 && (v[i + 9] < 2 || kind === 'meeting') && v[i + 9] < 2.1, `${kind} emissive ${v[i + 9]}`);
    }
  }
});

test('no desk and no person is invented: stations come only from the caller', () => {
  for (const kind of INTERIOR_KINDS) {
    const none = buildInteriorMesh(kind, frameOf(kind), { seed: 'p' }).triangles;
    const empty = buildInteriorMesh(kind, frameOf(kind), { seed: 'p', stations: [] }).triangles;
    assert.equal(none, empty, kind);
    const frame = frameOf(kind);
    const stations = layoutStations(frame, ['a', 'b', 'c']).map((s, i) => ({ ...s, occupantSeed: `agent-${i}` }));
    const withStations = buildInteriorMesh(kind, frame, { seed: 'p', stations }).triangles;
    if (STATION_KINDS.includes(kind) && kind !== 'executive') assert.ok(withStations > none + 3 * 500, `${kind} draws its stations and occupants`);
    if (!STATION_KINDS.includes(kind)) assert.equal(withStations, none, `${kind} ignores stations`);
  }
});

test('a figure appears only for a station with an occupant seed', () => {
  const frame = frameOf('office');
  const base = layoutStations(frame, ['a', 'b', 'c', 'd']);
  const vacant = buildInteriorMesh('office', frame, { seed: 's', stations: base.map((s) => ({ ...s, state: 'vacant' as const })) }).triangles;
  const seated = buildInteriorMesh('office', frame, { seed: 's', stations: base.map((s, i) => ({ ...s, state: 'working' as const, occupantSeed: i < 2 ? `agent-${i}` : null })) }).triangles;
  const added = seated - vacant;
  assert.ok(added > 2 * 200 && added <= 2 * FIGURE_TRIANGLE_CAP + 8 * 40, `two figures added ${added}`);
  const executiveVacant = buildInteriorMesh('executive', frameOf('executive'), { seed: 's' }).triangles;
  const executiveSeated = buildInteriorMesh('executive', frameOf('executive'), { seed: 's', stations: [{ x: 4, z: 2.5, occupantSeed: 'agent-x' }] }).triangles;
  assert.ok(executiveSeated - executiveVacant >= 200 && executiveSeated - executiveVacant <= FIGURE_TRIANGLE_CAP);
});

test('a station stays under its cap and lite mode is lighter', () => {
  const b = new MeshBuilder();
  workstation(b, 0, { x: 0, z: 0, monitors: 3, headset: true }, resolveScheme(), { floor: [0.9, 0.9, 0.8] });
  assert.ok(b.triangleCount <= STATION_TRIANGLE_CAP, `${b.triangleCount}`);
  const lite = new MeshBuilder();
  workstation(lite, 0, { x: 0, z: 0, monitors: 3, headset: true }, resolveScheme(), { lite: true });
  assert.ok(lite.triangleCount < b.triangleCount * 0.85);
  const full = buildInteriorMesh('warehouse', frameOf('warehouse'), { seed: 'l' }).triangles;
  const light = buildInteriorMesh('warehouse', frameOf('warehouse'), { seed: 'l', detail: 'lite' }).triangles;
  assert.ok(light < full * 0.75, `${light} vs ${full}`);
});

test('station screens glow only for a state that has one', () => {
  const lit = (state: 'working' | 'vacant') => {
    const b = new MeshBuilder();
    workstation(b, 0, { x: 0, z: 0, state }, resolveScheme());
    const v = b.toFloat32();
    let total = 0;
    for (let i = 9; i < v.length; i += VERTEX_FLOATS) total += v[i];
    return total;
  };
  assert.ok(lit('working') > lit('vacant') + 5);
});

test('the meeting room and the operations wall light up only when live', () => {
  const glow = (kind: InteriorKind, live: boolean) => {
    const v = buildInteriorMesh(kind, frameOf(kind), { seed: 'g', live }).vertices;
    let total = 0;
    for (let i = 9; i < v.length; i += VERTEX_FLOATS) total += v[i];
    return total;
  };
  assert.ok(glow('meeting', true) > glow('meeting', false) + 10);
  assert.ok(glow('operations', true) > glow('operations', false) + 10);
  assert.equal(glow('warehouse', true), glow('warehouse', false));
});

test('colour schemes change colours, not geometry', () => {
  const names = Object.keys(SCHEMES) as Array<keyof typeof SCHEMES>;
  for (const kind of INTERIOR_KINDS) {
    const counts = names.map((n) => buildInteriorMesh(kind, frameOf(kind), { seed: 's', scheme: n }).triangles);
    assert.ok(counts.every((c) => c === counts[0]), kind);
  }
  const a = buildInteriorMesh('reception', frameOf('reception'), { seed: 's', scheme: 'walnut' }).vertices;
  const b = buildInteriorMesh('reception', frameOf('reception'), { seed: 's', scheme: 'slate' }).vertices;
  assert.notDeepEqual(Array.from(a), Array.from(b));
  assert.equal(resolveScheme().name, 'walnut');
  assert.equal(resolveScheme({ wood: [0, 0, 0] }).wood[0], 0);
});

test('seated and standing figures: caps, poses, scale and rotation', () => {
  const seated: SeatedPose[] = ['typing', 'reading', 'phone', 'talking', 'leaning'];
  const standing: StandingPose[] = ['neutral', 'presenting', 'arms-crossed', 'phone', 'walking'];
  const sizes: number[] = [];
  for (const pose of seated) {
    const b = new MeshBuilder();
    seatedFigure(b, 3, 1, 2, { seed: 'agent-1', pose, headset: pose === 'phone' });
    assert.ok(b.triangleCount > 150 && b.triangleCount <= FIGURE_TRIANGLE_CAP, `seated ${pose} ${b.triangleCount}`);
    const { min, max } = b.bounds();
    assert.ok(min.y >= 1 - 1e-6 && max.y - 1 > 1.0 && max.y - 1 < 1.6, `seated height ${max.y - 1}`);
    sizes.push(b.triangleCount);
  }
  for (const pose of standing) {
    const b = new MeshBuilder();
    standingFigure(b, 3, 1, 2, { seed: 'agent-1', pose });
    assert.ok(b.triangleCount > 150 && b.triangleCount <= FIGURE_TRIANGLE_CAP, `standing ${pose} ${b.triangleCount}`);
    const { min, max } = b.bounds();
    assert.ok(min.y >= 1 - 1e-6 && max.y - 1 > 1.6 && max.y - 1 < 2.0, `standing height ${max.y - 1}`);
  }
  // Size scales the figure, the seed fixes the look, a quarter turn swaps its footprint.
  const small = new MeshBuilder(), big = new MeshBuilder();
  standingFigure(small, 0, 0, 0, { seed: 'k', size: 0.8 });
  standingFigure(big, 0, 0, 0, { seed: 'k', size: 1.1 });
  assert.ok(big.bounds().max.y > small.bounds().max.y * 1.3);
  const a = new MeshBuilder(), a2 = new MeshBuilder(), c = new MeshBuilder();
  standingFigure(a, 0, 0, 0, { seed: 'same', pose: 'presenting' });
  standingFigure(a2, 0, 0, 0, { seed: 'same', pose: 'presenting' });
  standingFigure(c, 0, 0, 0, { seed: 'other', pose: 'presenting' });
  assert.deepEqual(Array.from(a.toFloat32()), Array.from(a2.toFloat32()));
  assert.notDeepEqual(Array.from(a.toFloat32()), Array.from(c.toFloat32()));
  const r0 = new MeshBuilder(), r1 = new MeshBuilder();
  standingFigure(r0, 0, 0, 0, { seed: 'turn', pose: 'presenting', rot: 0 });
  standingFigure(r1, 0, 0, 0, { seed: 'turn', pose: 'presenting', rot: 1 });
  const b0 = r0.bounds(), b1 = r1.bounds();
  assert.ok(Math.abs((b0.max.z - b0.min.z) - (b1.max.x - b1.min.x)) < 0.05);
});

test('limbs have outward facing sides and closed ends', () => {
  const b = new MeshBuilder();
  const L = new Local(b, 0, 0, 0, 0);
  L.limb([0, 0, 0], [0, 1, 0], 0.2, 0.2, 0.1, 0.1, [1, 1, 1], { cap: true });
  assert.equal(b.triangleCount, 10);
  const v = b.toFloat32();
  let up = 0;
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    const px = v[i], py = v[i + 1], pz = v[i + 2], nx = v[i + 3], ny = v[i + 4], nz = v[i + 5];
    if (ny > 0.9) { up++; assert.ok(py > 0.99); continue; }
    assert.ok(px * nx + pz * nz > 0, 'side normal points away from the axis');
  }
  assert.equal(up, 6);
  const tilted = new MeshBuilder();
  new Local(tilted, 0, 0, 0, 0).limb([0, 0, 0], [0.5, 0.2, 0.6], 0.1, 0.1, 0.1, 0.1, [1, 1, 1], { cap: true });
  const t = tilted.toFloat32();
  for (let i = 0; i < t.length; i += VERTEX_FLOATS) {
    const d = [0.5, 0.2, 0.6], len = Math.hypot(...d);
    const along = (t[i] * d[0] + t[i + 1] * d[1] + t[i + 2] * d[2]) / len;
    const rad = [t[i] - (along * d[0]) / len, t[i + 1] - (along * d[1]) / len, t[i + 2] - (along * d[2]) / len];
    const side = Math.abs(t[i + 3] * d[0] + t[i + 4] * d[1] + t[i + 5] * d[2]) / len < 0.2;
    if (side) assert.ok(rad[0] * t[i + 3] + rad[1] * t[i + 4] + rad[2] * t[i + 5] > 0, 'tilted side normal points outward');
  }
});

test('registry room kinds map to an interior', () => {
  assert.equal(interiorKindFor('reception'), 'reception');
  assert.equal(interiorKindFor('meeting'), 'meeting');
  assert.equal(interiorKindFor('lounge'), 'lounge');
  assert.equal(interiorKindFor('warehouse'), 'warehouse');
  assert.equal(interiorKindFor('workshop'), 'workshop');
  assert.equal(interiorKindFor('office'), 'office');
  assert.equal(interiorKindFor('office', 'Customer Care'), 'customer-care');
  assert.equal(interiorKindFor('office', 'Operations desk'), 'operations');
  assert.equal(interiorKindFor('office', 'Executive suite'), 'executive');
  assert.equal(interiorKindFor('hangar'), null);
});

test('layoutStations places at most one station per id, inside the frame', () => {
  for (const kind of STATION_KINDS) {
    const frame = frameOf(kind, 1, 5, 0, 3);
    const ids = Array.from({ length: 30 }, (_, i) => `d${i}`);
    const out = layoutStations(frame, ids);
    assert.ok(out.length > 0 && out.length <= ids.length);
    assert.equal(new Set(out.map((s) => s.id)).size, out.length);
    for (const s of out) {
      assert.ok(s.x > frame.x + 0.7 && s.x < frame.x + frame.width - 0.7, `${kind} x`);
      assert.ok(s.z > frame.z + 1.5 && s.z < frame.z + frame.depth - 0.9, `${kind} z`);
    }
  }
  assert.equal(layoutStations(frameOf('office'), []).length, 0);
});

test('buildInteriorRoom appends to a shared builder and reports what it added', () => {
  const b = new MeshBuilder();
  const first = buildInteriorRoom(b, 'meeting', frameOf('meeting'), { seed: 'a' });
  const second = buildInteriorRoom(b, 'lounge', frameOf('lounge', 1, 20, 0, 0), { seed: 'a' });
  assert.equal(b.triangleCount, first.triangles + second.triangles);
  assert.equal(first.kind, 'meeting');
});

test('source files use no long dashes', async () => {
  const { readdir, readFile } = await import('node:fs/promises');
  const dir = new URL('../src/hqAssets/interior/', import.meta.url);
  for (const name of await readdir(dir)) {
    const text = await readFile(new URL(name, dir), 'utf8');
    assert.ok(!/[–—]/.test(text), `${name} contains an en or em dash`);
  }
});
