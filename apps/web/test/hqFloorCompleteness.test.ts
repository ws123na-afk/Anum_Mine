import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import { projectHeadquarters, type Registry } from '../src/lib/headquartersProjection.ts';

// The eight starter-campus buildings (docs/ANUM_MINE_STARTER_CAMPUS.md): 18 floors, 62 rooms, 220 desks. The fixture is the
// planner's own output for an empty workspace (services/api office_starter_campus.plan_campus), so these are the real positions.
type Fixture = { buildings: { id: string; name: string }[]; floors: { id: string; building_id: string; level: number; name: string }[]; rooms: Record<string, unknown>[]; desks: Record<string, unknown>[] };
const fx = JSON.parse(readFileSync(new URL('./fixtures/starterCampus.json', import.meta.url), 'utf8')) as Fixture;
const rev = { revision: 1 };
const registry = {
  buildings: fx.buildings.map((b) => ({ ...b, ...rev, timezone: 'Asia/Dubai' })),
  floors: fx.floors.map((f) => ({ ...f, ...rev })),
  rooms: fx.rooms.map((r) => ({ ...r, ...rev })),
  desks: fx.desks.map((d) => ({ ...d, ...rev })),
} as unknown as Registry;
const projection = projectHeadquarters({ tenantId: 't', workspaceId: 'w' }, registry, []);

test('the starter campus fixture is the 8 building, 18 floor, 62 room, 220 desk campus', () => {
  assert.equal(projection.buildings.length, 8);
  assert.equal(projection.buildings.reduce((n, b) => n + b.floors.length, 0), 18);
  assert.equal(projection.buildings.reduce((n, b) => n + b.floors.reduce((m, f) => m + f.rooms.length, 0), 0), 62);
  assert.equal(projection.buildings.reduce((n, b) => n + b.floors.reduce((m, f) => m + f.desks.length, 0), 0), 220);
  assert.deepEqual(projection.issues, []);
});

test('every floor of every building draws all its rooms and all its desks, none overlapping or outside its room', () => {
  for (const b of projection.buildings) {
    b.floors.forEach((floor, fi) => {
      const scene = buildBuildingScene(b.floors, { activeIndex: fi });
      const label = `${b.name} / ${floor.name}`;
      assert.deepEqual(scene.rooms.filter((r) => r.floorIndex === fi).map((r) => r.id).sort(), floor.rooms.map((r) => r.id).sort(), `${label}: rooms`);
      assert.deepEqual(scene.desks.filter((d) => d.floorIndex === fi).map((d) => d.id).sort(), floor.desks.map((d) => d.id).sort(), `${label}: desks`);
      for (const r of floor.rooms) {
        for (const o of floor.rooms) {
          if (r.id >= o.id) continue;
          const apart = r.x + r.width <= o.x + 1e-6 || o.x + o.width <= r.x + 1e-6 || r.z + r.depth <= o.z + 1e-6 || o.z + o.depth <= r.z + 1e-6;
          assert.ok(apart, `${label}: ${r.label} overlaps ${o.label}`);
        }
        const handle = scene.rooms.find((h) => h.id === r.id)!;
        assert.equal(handle.box.min.x, r.x); assert.equal(handle.box.max.x, r.x + r.width);
        assert.equal(handle.box.min.z, r.z); assert.equal(handle.box.max.z, r.z + r.depth);
        for (const d of floor.desks.filter((dk) => dk.roomId === r.id)) {
          assert.ok(d.position.x > r.x && d.position.x < r.x + r.width && d.position.z > r.z && d.position.z < r.z + r.depth, `${label}: desk ${d.label} outside ${r.label}`);
        }
      }
      // The scene's bounds contain every room of every floor (nothing is clipped from the stack).
      for (const r of b.floors.flatMap((f) => f.rooms)) assert.ok(scene.bounds.min.x <= r.x && scene.bounds.max.x >= r.x + r.width && scene.bounds.min.z <= r.z && scene.bounds.max.z >= r.z + r.depth, `${label}: bounds`);
    });
  }
});
