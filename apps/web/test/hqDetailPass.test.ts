import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, MAT, VERTEX_FLOATS, hex } from '../src/lib/hqGeometry.ts';
import { DETAIL_TRIANGLES_CAP, MAX_SILLS, deriveDetail } from '../src/lib/hqDetailPass.ts';
import { buildExterior, EXTERIOR_KINDS } from '../src/hqAssets/exterior/index.ts';

const GREY = hex('#9a9a9a');

/** A 24 x 16 building 20 m high on a ground plane, its roof in two pieces (a seam in the middle), with a pool-sized obstacle on the roof. */
function roofScene(withObstacle: boolean): Float32Array {
  const b = new MeshBuilder();
  b.plane(-60, 0, -60, 120, 120, GREY);
  b.box(-12, 0, -8, 24, 20, 16, GREY, { skip: ['top', 'bottom'] });
  // Two roof pieces that meet at x = 0 (a seam that must get no coping).
  b.plane(-12, 20, -8, 12, 16, GREY); b.plane(0, 20, -8, 12, 16, GREY);
  if (withObstacle) b.box(-6, 20, -3, 10, 0.5, 6, hex('#2a6a9a'), { skip: ['bottom'] });
  return b.toFloat32();
}

test('coping runs along the roof edges and not along the seam between two roof pieces', () => {
  const r = deriveDetail(roofScene(false));
  assert.ok(r.coping >= 4 && r.coping <= 8, `coping ${r.coping}`);
  const v = r.vertices;
  // No cap vertex on the seam line x = 0 inside the roof (its caps would sit at x = 0 with |z| < 7).
  let seam = 0, edge = 0;
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    if (v[i + 1] < 20.1 || v[i + 1] > 20.3) continue;
    if (Math.abs(v[i]) < 0.01 && Math.abs(v[i + 2]) < 7) seam += 1;
    if (Math.abs(Math.abs(v[i]) - 12) < 0.3 || Math.abs(Math.abs(v[i + 2]) - 8) < 0.3) edge += 1;
  }
  assert.equal(seam, 0);
  assert.ok(edge > 20);
});

test('roof plant stays on free roof, clear of the edge and of what is already there', () => {
  const r = deriveDetail(roofScene(true), { seed: 'a' });
  assert.ok(r.units >= 1, `units ${r.units}`);
  const v = r.vertices;
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    const x = v[i], y = v[i + 1], z = v[i + 2];
    if (y < 20.01 || v[i + 10] !== MAT.metal) continue;
    assert.ok(Math.abs(x) <= 12 && Math.abs(z) <= 8, 'on the roof');
    assert.ok(!(x > -6.2 && x < 4.2 && z > -3.2 && z < 3.2), `unit vertex over the obstacle (${x}, ${z})`);
  }
  assert.deepEqual(deriveDetail(roofScene(true), { seed: 'a' }).vertices, v, 'deterministic');
  assert.notDeepEqual(deriveDetail(roofScene(true), { seed: 'b' }).vertices, v, 'the seed changes the layout');
});

test('planters stand along ground floor walls, not next to lit doors, and stay under the triangle cap', () => {
  const b = new MeshBuilder();
  b.plane(-80, 0, -80, 160, 160, GREY);
  b.box(-30, 0, -10, 60, 12, 20, GREY, { skip: ['top', 'bottom'] });
  b.plane(-30, 12, -10, 60, 20, GREY);
  b.box(-1.5, 0, 10.02, 3, 3, 0.1, hex('#ffd9a0'), { emissive: 0.8 });   // a lit entrance on the south wall
  const r = deriveDetail(b.toFloat32());
  assert.ok(r.planters >= 3, `planters ${r.planters}`);
  assert.ok(r.triangles <= DETAIL_TRIANGLES_CAP);
  const v = r.vertices;
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    if (v[i + 1] > 1.2 || v[i + 10] !== MAT.concrete) continue;
    assert.ok(!(Math.abs(v[i]) < 3 && v[i + 2] > 9.5 && v[i + 2] < 13.2), 'nothing in front of the lit entrance');
  }
});

test('window sills sit under panes, out from the wall, and never exceed the cap', () => {
  const b = new MeshBuilder();
  b.plane(-50, 0, -50, 100, 100, GREY);
  b.box(-20, 0, -5, 40, 14, 10, GREY, { skip: ['top', 'bottom'] });
  // A row of 24 panes on the south wall (z = 5): each a vertical quad, 1.6 m wide and 1.8 m high, glass weight 2.5.
  for (let i = 0; i < 24; i++) b.quad([-19 + i * 1.65, 3, 5.04], [-19 + i * 1.65 + 1.6, 3, 5.04], [-19 + i * 1.65 + 1.6, 4.8, 5.04], [-19 + i * 1.65, 4.8, 5.04], hex('#7aa4c4'), 2.5);
  const r = deriveDetail(b.toFloat32());
  assert.ok(r.sills >= 3 && r.sills <= 6, `sills ${r.sills}`);
  const v = r.vertices;
  let minY = Infinity, maxZ = -Infinity;
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) { if (v[i + 10] === MAT.concrete && v[i + 1] < 3.1 && v[i + 1] > 1 && v[i + 2] > 4.9) { minY = Math.min(minY, v[i + 1]); maxZ = Math.max(maxZ, v[i + 2]); } }
  assert.ok(minY < 3 && minY > 2.8, `under the pane ${minY}`);
  assert.ok(maxZ > 5.1 && maxZ < 5.4, `out from the wall ${maxZ}`);
  const many = new MeshBuilder();
  many.plane(-50, 0, -50, 100, 100, GREY);
  for (let row = 0; row < 40; row++) for (let i = 0; i < 40; i++) many.quad([i * 2, 1 + row * 3, 5.04], [i * 2 + 1.5, 1 + row * 3, 5.04], [i * 2 + 1.5, 2.8 + row * 3, 5.04], [i * 2, 2.8 + row * 3, 5.04], hex('#7aa4c4'), 2.5);
  assert.ok(deriveDetail(many.toFloat32()).sills <= MAX_SILLS);
});

test('an empty or roofless scene yields nothing, and every real exterior building stays inside the cap', () => {
  assert.equal(deriveDetail(new Float32Array(0)).triangles, 0);
  const floor = new MeshBuilder(); floor.plane(0, 0, 0, 10, 10, GREY);
  assert.equal(deriveDetail(floor.toFloat32()).triangles, 0);
  const t0 = performance.now();
  for (const kind of EXTERIOR_KINDS) {
    const a = buildExterior(kind, { seed: 'detail', origin: { x: 0, z: 0 } });
    const r = deriveDetail(a.mesh.vertices);
    assert.ok(r.triangles <= DETAIL_TRIANGLES_CAP, `${kind} ${r.triangles}`);
    console.log(`DETAIL ${kind} base=${a.triangles} coping=${r.coping} units=${r.units} planters=${r.planters} extra=${r.triangles}`);
  }
  console.log(`DETAIL_MS ${(performance.now() - t0).toFixed(0)}`);
});
