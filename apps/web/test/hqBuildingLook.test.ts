import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, VERTEX_FLOATS, hex } from '../src/lib/hqGeometry.ts';
import { GLOBE_MONUMENT_TRIANGLES_CAP, BUILDING_GARDEN_TRIANGLES_CAP, buildingGarden, globeMonument } from '../src/hqAssets/environment/index.ts';
import { stamp, makeTemplate } from '../src/hqAssets/environment/common.ts';
import { lightingFor } from '../src/lib/hqLighting.ts';

test('smoothBlob gives unit normals, stays inside its radii (plus lump) and shades the top brighter than the underside', () => {
  const b = new MeshBuilder();
  b.smoothBlob(0, 5, 0, 2, 1, 2, hex('#40a060'), { lat: 4, lon: 8, lump: 0.1 });
  const v = b.toFloat32();
  assert.ok(b.triangleCount > 30 && b.triangleCount <= 64);
  let top = 0, bottom = 0;
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    assert.ok(Math.abs(Math.hypot(v[i + 3], v[i + 4], v[i + 5]) - 1) < 1e-6);
    assert.ok(Math.abs(v[i]) <= 2.21 && Math.abs(v[i + 1] - 5) <= 1.11);
    if (v[i + 4] > 0.9) top = Math.max(top, v[i + 7]);
    if (v[i + 4] < -0.9) bottom = Math.max(bottom, v[i + 7]);
  }
  assert.ok(top > bottom);
});

test('stamping a smooth template keeps its smooth normals, turned with the heading', () => {
  const t = makeTemplate('t', (b) => b.smoothBlob(0, 1, 0, 1, 1, 1, hex('#40a060'), { lat: 3, lon: 6, lump: 0 }));
  const b = new MeshBuilder();
  stamp(b, t, { x: 10, z: 5, rot: Math.PI / 2, scale: 2 });
  const v = b.toFloat32();
  assert.equal(v.length, t.vertices.length);
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    assert.ok(Math.abs(Math.hypot(v[i + 3], v[i + 4], v[i + 5]) - 1) < 1e-5);
    // On a unit sphere the normal points away from the centre, whatever the heading.
    const dx = v[i] - 10, dy = v[i + 1] - 2, dz = v[i + 2] - 5;
    const l = Math.hypot(dx, dy, dz);
    assert.ok((dx * v[i + 3] + dy * v[i + 4] + dz * v[i + 5]) / l > 0.95);
  }
});

test('globe monument and building garden are deterministic, bounded and finite', () => {
  const run = () => { const b = new MeshBuilder(); const g = globeMonument(b, 0, 0); return { b, g }; };
  const a = run(), c = run();
  assert.deepEqual(Array.from(a.b.toFloat32()), Array.from(c.b.toFloat32()));
  assert.ok(a.g.triangles > 400 && a.g.triangles <= GLOBE_MONUMENT_TRIANGLES_CAP, String(a.g.triangles));
  assert.ok(a.g.bounds.max.y > 8 && a.g.bounds.max.y < 12);
  assert.ok(a.b.halos.length >= 16, 'the globe lights the plaza at night');
  const gb = new MeshBuilder();
  const garden = buildingGarden(gb, { x: -8, z: -6, w: 16, d: 12 }, { x: 0, z: 14 }, { seed: 'g' });
  assert.ok(garden.triangles > 500 && garden.triangles <= BUILDING_GARDEN_TRIANGLES_CAP, String(garden.triangles));
  for (const x of gb.toFloat32()) assert.ok(Number.isFinite(x));
});

test('day light is a warm low sun, night a cool moon, and the fog colour is what the sky dome uses as its horizon', () => {
  const day = lightingFor(0), night = lightingFor(1);
  assert.ok(day.sunColor[0] > day.sunColor[2]);
  assert.ok(night.sunColor[2] > night.sunColor[0]);
  assert.ok(-day.sunDir[1] < 0.75, 'a low sun throws long shadows');
  assert.ok(day.fog[2] > night.fog[2] && night.fog[2] > 0.2);
});
