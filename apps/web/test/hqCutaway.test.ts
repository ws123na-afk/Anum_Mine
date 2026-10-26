// The section view: which walls face the camera (with a dead band), the 90 degree snaps, the fit of the whole stack, and the scene pieces.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { FLOOR_PITCH, WALL_HEIGHT, buildBuildingScene } from '../src/lib/buildingScene.ts';
import { HOME_YAW, buildingFitCamera } from '../src/lib/hqCamera.ts';
import { CUT_BAND, CUT_PITCH, cutLifts, facingSides, snapYaw, type Side } from '../src/lib/hqCutaway.ts';
import { cameraBasis, cameraEye, type CameraState } from '../src/lib/headquartersSpatial.ts';
import type { RoomGeometry, SceneFloor } from '../src/lib/headquartersProjection.ts';

const room = (id: string, x: number, z: number, w: number, d: number, kind: RoomGeometry['kind'] = 'office'): RoomGeometry => ({
  id, floorId: 'f', buildingId: 'b', label: id, kind, x, z, width: w, depth: d, height: 3, capacity: 4, revision: 1, deskIds: [], evidence: 'registry',
});
const stack = (n: number): SceneFloor[] => Array.from({ length: n }, (_, i) => ({
  id: 'f' + i, name: 'Floor ' + i, level: i, bounds: { width: 40, depth: 30 },
  rooms: [room(`a${i}`, 0, 0, 14, 8), room(`b${i}`, 15, 0, 8, 8, 'meeting'), room(`c${i}`, 0, 10, 12, 8, 'warehouse')], desks: [],
}));
const pair = (s: readonly Side[]) => [...s].sort().join(' ');

describe('which walls face the camera', () => {
  test('the four diagonal views each cut one wall of each axis', () => {
    assert.equal(pair(facingSides(35)), 'east south');
    assert.equal(pair(facingSides(125)), 'east north');
    assert.equal(pair(facingSides(215)), 'north west');
    assert.equal(pair(facingSides(305)), 'south west');
  });
  test('a camera orbiting through the dead band does not flicker, and flips once it is clear of it', () => {
    let prev: readonly Side[] = facingSides(35);
    const changes: number[] = [];
    // Sweep the azimuth from 35 to 200 and back in 1 degree steps: each wall changes once each way.
    const sweep = [...Array.from({ length: 166 }, (_, i) => 35 + i), ...Array.from({ length: 166 }, (_, i) => 200 - i)];
    for (const yaw of sweep) { const next = facingSides(yaw, prev); if (pair(next) !== pair(prev)) changes.push(yaw); prev = next; }
    assert.equal(changes.length, 4, `two axes, out and back: ${changes.join(',')}`);
    // Going out, the south wall gives way only after 90 plus the band; coming back, only after 90 minus it.
    assert.ok(changes[0] >= 90 + CUT_BAND - 1 && changes[0] <= 90 + CUT_BAND + 2);
    // Wobbling by 3 degrees across the diagonal or the axis never changes the pair.
    for (const centre of [0, 45, 90, 135, 180]) {
      let p = facingSides(centre + 20, []);
      p = facingSides(centre, p);
      for (let w = -3; w <= 3; w++) assert.equal(pair(facingSides(centre + w, p)), pair(p), `${centre}+${w}`);
    }
  });
  test('the open and closed lifts: cut walls and the roof go to 0, kept walls stay', () => {
    const open = cutLifts({ facade: 'cutaway', cutSides: ['south', 'east'], roofOpen: true, floors: 3, activeIndex: 0 });
    assert.deepEqual(open, { 'cut:roof': { to: 0 }, 'cut:north': { to: 1 }, 'cut:south': { to: 0 }, 'cut:east': { to: 0 }, 'cut:west': { to: 1 } });
    const closed = cutLifts({ facade: 'closed', cutSides: [], roofOpen: false, floors: 3, activeIndex: 0 });
    assert.ok(Object.values(closed).every((l) => l.to === 1));
  });
});

describe('rotate snaps', () => {
  test('each step goes to the next diagonal, from a diagonal or from anywhere between', () => {
    assert.equal(snapYaw(HOME_YAW, 1), HOME_YAW + 90);
    assert.equal(snapYaw(HOME_YAW, -1), HOME_YAW - 90);
    assert.equal(snapYaw(60, 1), HOME_YAW + 90);
    assert.equal(snapYaw(60, -1), HOME_YAW);
    assert.equal(snapYaw(305, 1), 395);
    // Four steps come back to the same view.
    let y = HOME_YAW; for (let i = 0; i < 4; i++) y = snapYaw(y, 1);
    assert.equal(y, HOME_YAW + 360);
    // Every snapped view re-picks the walls: four different pairs.
    const seen = new Set<string>(); let yaw = HOME_YAW; for (let i = 0; i < 4; i++) { seen.add(pair(facingSides(yaw))); yaw = snapYaw(yaw, 1); }
    assert.equal(seen.size, 4);
  });
});

describe('the section camera', () => {
  const box = (floors: number) => ({ x0: -2, x1: 38, z0: -2, z1: 24, y0: -0.4, y1: (floors - 1) * FLOOR_PITCH + WALL_HEIGHT + 0.4 });
  function inside(camera: CameraState, aspect: number, corner: { x: number; y: number; z: number }): boolean {
    const eye = cameraEye(camera), { right, up, forward } = cameraBasis(camera);
    const rel = { x: corner.x - eye.x, y: corner.y - eye.y, z: corner.z - eye.z };
    const depth = rel.x * forward.x + rel.y * forward.y + rel.z * forward.z;
    if (depth <= 0) return false;
    const tv = Math.tan((camera.fov * Math.PI) / 360);
    const sx = (rel.x * right.x + rel.y * right.y + rel.z * right.z) / (depth * tv * aspect);
    const sy = (rel.x * up.x + rel.y * up.y + rel.z * up.z) / (depth * tv);
    return Math.abs(sx) <= 1.0001 && Math.abs(sy) <= 1.0001;
  }
  test('every corner of the whole stack is in the picture at six stage shapes and the four diagonal views', () => {
    for (const floors of [1, 3, 8]) {
      const b = box(floors);
      for (const aspect of [0.46, 0.75, 1, 1.33, 1.78, 2.9]) {
        for (let k = 0; k < 4; k++) {
          const cam = buildingFitCamera(b, aspect, CUT_PITCH, HOME_YAW + k * 90);
          assert.ok(cam.distance < 300, `${floors} floors, aspect ${aspect}: the fit is not clamped (${cam.distance})`);
          for (const x of [b.x0, b.x1]) for (const z of [b.z0, b.z1]) for (const y of [b.y0, b.y1]) assert.ok(inside(cam, aspect, { x, y, z }), `${floors} floors, aspect ${aspect}, view ${k}, corner ${x},${y},${z}`);
        }
      }
    }
  });
  test('the section angle is the mocks: a third-quarter turn, 28 to 35 degrees up', () => {
    assert.ok(CUT_PITCH >= 28 && CUT_PITCH <= 35);
    const cam = buildingFitCamera(box(3), 1.6, CUT_PITCH);
    assert.equal(cam.yaw, HOME_YAW);
    assert.equal(cam.pitch, CUT_PITCH);
  });
});

describe('the section scene', () => {
  const floors = stack(3);
  test('every dressed floor gets four outer wall pieces at its own level, and the top one the roof', () => {
    const s = buildBuildingScene(floors, { activeIndex: 1, showAll: true, cutaway: true });
    assert.equal(s.walls.filter((w) => w.side !== 'roof').length, 12);
    for (const side of ['north', 'south', 'east', 'west'] as const) assert.deepEqual(s.walls.filter((w) => w.side === side).map((w) => w.y), [0, 1, 2].map((i) => i * FLOOR_PITCH));
    const roof = s.walls.filter((w) => w.side === 'roof');
    assert.equal(roof.length, 1);
    assert.equal(roof[0].floorIndex, 2);
    assert.equal(roof[0].y, 3 * FLOOR_PITCH - 0.3);
    assert.ok(s.bounds.max.y >= 3 * FLOOR_PITCH, 'the roof is inside the bounds');
  });
  test('every floor is dressed, its rooms are pickable handles at the right height, and only the open floor is at full colour', () => {
    const all = buildBuildingScene(floors, { activeIndex: 1, showAll: true, cutaway: true });
    assert.deepEqual([...new Set(all.rooms.map((r) => r.floorIndex))].sort(), [0, 1, 2]);
    for (const r of all.rooms) assert.equal(r.box.min.y, r.floorIndex * FLOOR_PITCH);
    const lum = (v: Float32Array) => { let n = 0; for (let i = 6; i < v.length; i += 13) n += v[i] + v[i + 1] + v[i + 2]; return n; };
    const bright = (fi: number) => { const w = all.walls.find((x) => x.side === 'north' && x.floorIndex === fi)!; return lum(w.opaque.vertices) / w.opaque.triangles; };
    assert.ok(bright(0) < bright(1) * 0.9 && bright(2) < bright(1) * 0.9, 'the floors other than the open one are dimmed');
  });
  test('the single floor has its own four walls and its roof', () => {
    const one = buildBuildingScene(floors, { activeIndex: 1, cutaway: true });
    assert.equal(one.walls.filter((w) => w.side === 'roof')[0].floorIndex, 1);
    assert.equal(one.walls.filter((w) => w.side === 'north').length, 1);
  });
  test('lite detail still dresses every floor: kits, desks and lit rooms stay on the floors that are not open', () => {
    const f = stack(8);
    const all = buildBuildingScene(f, { activeIndex: 7, showAll: true, cutaway: true });
    const single = buildBuildingScene(f, { activeIndex: 7, cutaway: true });
    assert.ok(all.triangles > single.triangles * 1.8, 'seven more furnished floors are drawn');
    assert.ok(all.halos.length > single.halos.length * 4, 'the lit rooms of the other floors glow');
  });
  test('an 8 floor tower and an 18 floor campus stay inside the geometry budget', () => {
    for (const [n, limit] of [[8, 180_000], [18, 340_000]] as const) {
      const s = buildBuildingScene(stack(n), { activeIndex: 3, showAll: true, cutaway: true });
      assert.ok(s.triangles < limit, `${n} floors: ${s.triangles} triangles`);
    }
  });
});
