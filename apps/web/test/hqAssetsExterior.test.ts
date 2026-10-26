import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import { VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import { EXTERIOR_KINDS, TRIANGLE_CAPS, WINDOW_RULES, buildExterior, type ExteriorKind } from '../src/hqAssets/exterior/index.ts';

const DASH = new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`);
const eachKind = (fn: (kind: ExteriorKind) => void) => { for (const kind of EXTERIOR_KINDS) fn(kind); };

test('every builder returns finite geometry within its triangle cap, resting on the ground', () => {
  const counts: Record<string, number> = {};
  eachKind((kind) => {
    const a = buildExterior(kind, { seed: 'ws-1' });
    const v = a.mesh.vertices;
    assert.equal(v.length % (VERTEX_FLOATS * 3), 0, `${kind}: whole triangles`);
    assert.equal(a.triangles, v.length / VERTEX_FLOATS / 3);
    assert.equal(a.mesh.triangles, a.triangles);
    counts[kind] = a.triangles;
    assert.ok(a.triangles > 400, `${kind}: has real detail (${a.triangles})`);
    assert.ok(a.triangles <= TRIANGLE_CAPS[kind], `${kind}: ${a.triangles} over cap ${TRIANGLE_CAPS[kind]}`);
    let minY = Infinity;
    for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
      for (let k = 0; k < VERTEX_FLOATS; k++) assert.ok(Number.isFinite(v[i + k]), `${kind}: finite`);
      minY = Math.min(minY, v[i + 1]);
      assert.ok(Math.abs(Math.hypot(v[i + 3], v[i + 4], v[i + 5]) - 1) < 1e-4, `${kind}: unit normal`);
    }
    assert.ok(minY >= -0.01 && minY < 0.1, `${kind}: stands on the ground (min y ${minY})`);
    assert.ok(a.mesh.bounds.max.y >= a.height - 0.01);
  });
  console.log('triangle counts', JSON.stringify(counts));
});

test('same seed gives the same building, another seed gives another night and finish', () => {
  eachKind((kind) => {
    const a = buildExterior(kind, { seed: 'one' }), b = buildExterior(kind, { seed: 'one' }), c = buildExterior(kind, { seed: 'two', scheme: 'graphite' });
    assert.deepEqual(Array.from(a.mesh.vertices), Array.from(b.mesh.vertices));
    assert.notDeepEqual(Array.from(a.mesh.vertices), Array.from(c.mesh.vertices));
    assert.equal(c.scheme, 'graphite');
  });
});

test('window rule: panes carry emissive 2 (dark) or 2.45 to 2.95 (lit); the lit share follows litWindows', () => {
  eachKind((kind) => {
    const none = buildExterior(kind, { seed: 'n', litWindows: 0 }), all = buildExterior(kind, { seed: 'n', litWindows: 1 });
    assert.ok(none.windows.total >= 20, `${kind}: has windows`);
    assert.equal(none.windows.lit <= none.windows.total * 0.35, true, `${kind}: few lit with litWindows 0`);
    assert.ok(all.windows.lit >= all.windows.total * 0.9, `${kind}: nearly all lit with litWindows 1`);
    const v = all.mesh.vertices;
    let lit = 0;
    for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
      const e = v[i + 9];
      if (e >= 1.5) { assert.ok(e >= 2 - 1e-6 && e <= 3.0, `${kind}: window emissive ${e}`); if (e > 2.2) lit += 1; }
    }
    assert.ok(lit > 0, `${kind}: lit panes exist`);
    const def = buildExterior(kind, { seed: 'd' });
    const share = def.windows.lit / def.windows.total;
    assert.ok(Math.abs(share - WINDOW_RULES[kind]) < 0.3, `${kind}: default share ${share.toFixed(2)} near ${WINDOW_RULES[kind]}`);
  });
});

test('the sign anchor sits on the front, faces +z, and the door is out in front of it', () => {
  eachKind((kind) => {
    const a = buildExterior(kind, { seed: 's' });
    assert.equal(a.sign.nx, 0);
    assert.equal(a.sign.nz, 1);
    assert.ok(a.sign.width > 4 && a.sign.height >= 1 && a.sign.markWidth > 0 && a.sign.markWidth < a.sign.width);
    assert.ok(a.sign.y > 2 && a.sign.y < a.height);
    assert.ok(a.sign.z > 0, `${kind}: front is +z`);
    assert.ok(a.sign.z <= a.mesh.bounds.max.z + 0.01);
    assert.ok(a.door.z > a.sign.z - 0.5 && a.door.y === 0);
    assert.ok(a.anchor.y > a.height - 1, `${kind}: label above the roof`);
    assert.ok(a.box.min.x <= a.mesh.bounds.min.x + 0.5 && a.box.max.x >= a.mesh.bounds.max.x - 0.5, `${kind}: ground box covers the mesh`);
  });
});

test('rotation and origin move the mesh, the sign and the door together', () => {
  eachKind((kind) => {
    const base = buildExterior(kind, { seed: 'r' });
    const moved = buildExterior(kind, { seed: 'r', origin: { x: 100, z: -40 } });
    assert.ok(Math.abs(moved.sign.x - base.sign.x - 100) < 1e-6 && Math.abs(moved.sign.z - base.sign.z + 40) < 1e-6);
    assert.ok(Math.abs(moved.mesh.bounds.min.x - base.mesh.bounds.min.x - 100) < 1e-3);
    for (const rot of [1, 2, 3] as const) {
      const r = buildExterior(kind, { seed: 'r', rotation: rot });
      assert.equal(r.triangles, base.triangles);
      const expected = [[0, 1], [-1, 0], [0, -1], [1, 0]][rot];
      assert.equal(Math.round(r.sign.nx) + 0, expected[0]);
      assert.equal(Math.round(r.sign.nz) + 0, expected[1]);
      // The sign anchor is in front of the footprint centre along its normal.
      assert.ok(r.sign.x * r.sign.nx + r.sign.z * r.sign.nz > 0);
      const sx = Math.abs(r.mesh.bounds.max.x - r.mesh.bounds.min.x), sz = Math.abs(r.mesh.bounds.max.z - r.mesh.bounds.min.z);
      const bx = base.mesh.bounds.max.x - base.mesh.bounds.min.x, bz = base.mesh.bounds.max.z - base.mesh.bounds.min.z;
      if (rot % 2) { assert.ok(Math.abs(sx - bz) < 1e-3 && Math.abs(sz - bx) < 1e-3); } else { assert.ok(Math.abs(sx - bx) < 1e-3); }
      const v = r.mesh.vertices;
      for (let i = 0; i < v.length; i += VERTEX_FLOATS) assert.ok(Math.abs(Math.hypot(v[i + 3], v[i + 4], v[i + 5]) - 1) < 1e-4);
    }
  });
});

test('size parameters are honoured and clamped, and lean detail is lighter', () => {
  eachKind((kind) => {
    const small = buildExterior(kind, { width: 1, depth: 1, levels: 1 }), big = buildExterior(kind, { width: 500, depth: 500, levels: 500 });
    assert.ok(small.footprint.width > 10 && small.footprint.depth > 10, `${kind}: min clamp`);
    assert.ok(big.footprint.width < 120 && big.footprint.depth < 120, `${kind}: max clamp`);
    assert.ok(Number.isFinite(big.triangles) && big.triangles > small.triangles - 1);
    const full = buildExterior(kind, { seed: 'l' }), lean = buildExterior(kind, { seed: 'l', detail: 'lean' });
    assert.ok(lean.triangles < full.triangles, `${kind}: lean ${lean.triangles} < full ${full.triangles}`);
  });
  const tall = buildExterior('officeTower', { levels: 30 }), low = buildExterior('officeTower', { levels: 10 });
  assert.ok(tall.height > low.height + 40);
});

test('the sources carry no em or en dashes', () => {
  const dir = new URL('../src/hqAssets/exterior/', import.meta.url);
  for (const f of readdirSync(dir)) {
    const text = readFileSync(new URL(f, dir), 'utf8');
    assert.ok(!DASH.test(text), `${f} has a dash`);
  }
  assert.ok(!DASH.test(readFileSync(new URL('./hqAssetsExterior.test.ts', import.meta.url), 'utf8')));
});
