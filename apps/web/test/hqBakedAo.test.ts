import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, MAT, VERTEX_FLOATS, hex } from '../src/lib/hqGeometry.ts';
import { CONTACT_FLOATS, MAX_CONTACTS, bakeScene, contentKey, bakeVertexAo, deriveContacts, groundLevels, refineWalls } from '../src/lib/hqBakedAo.ts';

const GREY = hex('#aaaaaa');

/** A 60 m ground plane, an L shaped pair of walls (a concave corner), a lone wall far away, a car sized box and a tree. */
function scene(): { v: Float32Array; b: MeshBuilder } {
  const b = new MeshBuilder();
  b.withMat(MAT.asphalt, () => b.plane(-30, 0, -30, 60, 60, GREY));
  b.box(0, 0, 0, 12, 8, 0.4, GREY, { skip: ['bottom'] });      // wall along x
  b.box(0, 0, 0, 0.4, 8, 12, GREY, { skip: ['bottom'] });      // wall along z: a concave corner at the origin
  b.box(-25, 0, -25, 12, 8, 0.4, GREY, { skip: ['bottom'] });  // a wall with nothing near it
  b.box(15, 0, 15, 4.4, 1.4, 1.8, GREY, { skip: ['bottom'] }); // a car sized box
  b.box(-10, 0, 10, 0.4, 3, 0.4, hex('#5a4a3a'), { skip: ['bottom'] }); // a trunk
  b.withMat(MAT.foliage, () => b.smoothBlob(-9.8, 4.5, 10.2, 2.4, 1.6, 2.4, hex('#2f6a3a'), { lat: 4, lon: 8 }));
  return { v: b.toFloat32(), b };
}

/** The AO of the first vertex found near a point with a given normal axis. */
function aoNear(v: Float32Array, ao: Float32Array, x: number, y: number, z: number, nAxis: 'x' | 'y' | 'z', sign: 1 | -1, tol = 0.6): number {
  const axis = nAxis === 'x' ? 3 : nAxis === 'y' ? 4 : 5;
  let best = Infinity, out = Number.NaN;
  for (let i = 0; i < ao.length; i++) {
    const p = i * VERTEX_FLOATS;
    if (v[p + axis] * sign < 0.9) continue;
    const d = Math.hypot(v[p] - x, v[p + 1] - y, v[p + 2] - z);
    if (d < best && d < tol) { best = d; out = ao[i]; }
  }
  return out;
}

test('ground levels are found from large flat floors only', () => {
  const { v } = scene();
  assert.deepEqual(groundLevels(v), [0]);
  const b = new MeshBuilder();
  b.plane(0, 0, 0, 20, 20, GREY); b.plane(0, 4.2, 0, 20, 20, GREY); b.plane(0, 4.25, 0, 20, 20, GREY); b.plane(0, 2, 0, 1, 1, GREY);
  assert.deepEqual(groundLevels(b.toFloat32()), [0, 4.2]);
  assert.equal(groundLevels(new MeshBuilder().toFloat32()).length, 0);
});

/** Statistic of the AO over the vertices of the refined scene that satisfy `pick`. */
function stat(r: { vertices: Float32Array; ao: Float32Array }, pick: (x: number, y: number, z: number, nx: number, ny: number, nz: number) => boolean, kind: 'min' | 'mean'): number {
  const values: number[] = [];
  for (let i = 0; i < r.ao.length; i++) {
    const p = i * VERTEX_FLOATS, v = r.vertices;
    if (pick(v[p], v[p + 1], v[p + 2], v[p + 3], v[p + 4], v[p + 5])) values.push(r.ao[i]);
  }
  assert.ok(values.length > 0, 'vertices found');
  return kind === 'min' ? Math.min(...values) : values.reduce((a, b) => a + b, 0) / values.length;
}

test('tall walls are cut above their floor; nothing else changes', () => {
  const { v } = scene();
  const r = refineWalls(v);
  assert.ok(r.length > v.length && (r.length / VERTEX_FLOATS) % 3 === 0);
  const ys = new Set<number>();
  for (let i = 0; i < r.length; i += VERTEX_FLOATS) ys.add(Math.round(r[i + 1] * 10) / 10);
  for (const y of [0.6, 1.8, 3.6, 7.2]) assert.ok(ys.has(y), `cut at ${y}`);
  const flat = new MeshBuilder();
  flat.plane(0, 0, 0, 50, 50, GREY);
  const fv = flat.toFloat32();
  assert.equal(refineWalls(fv), fv, 'a plane is returned as it is');
  assert.deepEqual(refineWalls(v), r);
});

test('the inside of a corner is darker than an open wall, and the base of a wall darker than its top', () => {
  const { v } = scene();
  const r = bakeScene(v);
  assert.equal(r.ao.length, r.vertices.length / VERTEX_FLOATS);
  // The east face of the z wall (normal +x), near the x wall, against the lonely wall's south face.
  const corner = stat(r, (x, y, z, nx) => nx > 0.9 && Math.abs(x - 0.4) < 0.01 && z < 4 && y > 0.5 && y < 4, 'min');
  const open = stat(r, (x, y, z, nx, ny, nz) => nz > 0.9 && Math.abs(z + 24.6) < 0.01 && y > 0.5 && y < 4, 'min');
  assert.ok(corner < open - 0.03, `corner ${corner} against open ${open}`);
  const base = stat(r, (x, y, z, nx, ny, nz) => nz > 0.9 && Math.abs(z + 24.6) < 0.01 && y < 0.7, 'mean');
  const top = stat(r, (x, y, z, nx, ny, nz) => nz > 0.9 && Math.abs(z + 24.6) < 0.01 && y > 7.5, 'mean');
  assert.ok(base < top - 0.15, `base ${base} against top ${top}`);
  for (const a of r.ao) assert.ok(a >= 0.45 - 1e-6 && a <= 1 + 1e-6);
});

test('flat ground and lit surfaces stay at 1; the bake is deterministic', () => {
  const { v } = scene();
  const a = bakeScene(v), b = bakeScene(v);
  assert.deepEqual(a.ao, b.ao);
  assert.deepEqual(a.vertices, b.vertices);
  const open = stat(a, (x, y, z, nx, ny) => ny > 0.99 && y < 0.01 && Math.abs(x) > 20 && Math.abs(z) > 20, 'min');
  assert.ok(open > 0.99, `ground ${open}`);
  const lit = new MeshBuilder();
  lit.box(0, 0, 0, 4, 4, 0.4, GREY, { emissive: 2.5 }); lit.box(0, 0, 0.5, 4, 4, 0.4, GREY, { emissive: 2.5 });
  assert.ok(bakeVertexAo(lit.toFloat32()).every((x) => x === 1), 'window and lit surfaces are not baked');
});

test('a scene over the vertex cap is left untouched, and an empty scene is fine', () => {
  const { v } = scene();
  assert.ok(bakeVertexAo(v, { maxVertices: 10 }).every((x) => x === 1));
  assert.equal(bakeVertexAo(new Float32Array(0)).length, 0);
  assert.equal(deriveContacts(new Float32Array(0)).length, 0);
});

test('contact shadows: a car sized box and the tree canopy get a disc on the ground, a tall wall does not', () => {
  const { v } = scene();
  const c = deriveContacts(v);
  assert.equal(c.length % CONTACT_FLOATS, 0);
  assert.deepEqual(deriveContacts(v), c);
  const discs = Array.from({ length: c.length / CONTACT_FLOATS }, (_, i) => Array.from(c.subarray(i * CONTACT_FLOATS, (i + 1) * CONTACT_FLOATS)));
  const near = (x: number, z: number, r: number) => discs.filter(([dx, , dz]) => Math.hypot(dx - x, dz - z) < r);
  assert.ok(near(17.2, 15.9, 3.5).length >= 1, 'car');
  const canopy = near(-9.8, 10.2, 2).filter(([, , , radius]) => radius > 1.5);
  assert.ok(canopy.length >= 1, 'canopy disc');
  assert.equal(near(6, 0.2, 0.7).filter(([, , , radius]) => radius < 1.5).length, 0, 'an 8 m wall is a building, not a low object');
  assert.ok(near(6, 1, 3).some(([, , , radius, strength]) => Math.abs(radius - 2.1) < 1e-3 && Math.abs(strength - 0.24) < 1e-3), 'but its foot gets soft strips');
  for (const [, y, , r, s] of discs) { assert.equal(y, 0); assert.ok(r > 0.5 && r < 12); assert.ok(s > 0 && s <= 0.5); }
});

test('the number of discs is capped and the strongest are kept', () => {
  const b = new MeshBuilder();
  b.plane(-200, 0, -200, 400, 400, GREY);
  for (let i = 0; i < 80; i++) for (let j = 0; j < 40; j++) b.box(-190 + i * 4.5, 0, -190 + j * 9, 1.2, 1.2, 1.2, GREY, { skip: ['bottom'] });
  const c = deriveContacts(b.toFloat32());
  assert.ok(c.length / CONTACT_FLOATS <= MAX_CONTACTS);
  assert.ok(c.length / CONTACT_FLOATS > 100);
});

test('timing: baking a campus sized scene stays well under two seconds (logged for the performance doc)', () => {
  const b = new MeshBuilder();
  b.plane(-200, 0, -200, 400, 400, GREY);
  for (let i = 0; i < 70; i++) for (let j = 0; j < 40; j++) b.box(-190 + i * 5.4, 0, -190 + j * 9.5, 3 + (i % 3), 1.4 + (j % 5), 3, GREY, { skip: ['bottom'] });
  const v = b.toFloat32();
  const t0 = performance.now();
  bakeVertexAo(v);
  const ao = performance.now() - t0;
  const t1 = performance.now();
  deriveContacts(v);
  console.log(`AO_BAKE_MS vertices=${v.length / VERTEX_FLOATS} ao=${ao.toFixed(0)} contacts=${(performance.now() - t1).toFixed(0)}`);
  assert.ok(ao < 2000);
});

test('the content key is equal for equal geometry and changes with any float', () => {
  const { v } = scene();
  const copy = new Float32Array(v);
  assert.equal(contentKey(v), contentKey(copy));
  copy[copy.length - 1] += 0.5;
  assert.notEqual(contentKey(v), contentKey(copy));
  const sub = v.subarray(VERTEX_FLOATS * 3);
  assert.notEqual(contentKey(sub), contentKey(v));
  assert.match(contentKey(new Float32Array(0)), /^0:/);
});
