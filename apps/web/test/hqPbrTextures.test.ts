import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PBR_KINDS, PBR_OF_CLASS, PBR_SIZE, generatePbr, type PbrKind } from '../src/lib/hqPbrTextures.ts';

const N = 64;
const maps = new Map<PbrKind, Uint8Array>();
for (const k of PBR_KINDS) maps.set(k, generatePbr(k, N));
const px = (m: Uint8Array, x: number, y: number, c: number) => m[((y + N) % N * N + ((x + N) % N)) * 4 + c];

test('every kind is deterministic, the right size and different from the others', () => {
  assert.equal(PBR_SIZE, 128);
  for (const k of PBR_KINDS) {
    const a = maps.get(k)!;
    assert.equal(a.length, N * N * 4, k);
    assert.deepEqual(generatePbr(k, N), a, `${k} repeats exactly`);
  }
  for (let i = 0; i < PBR_KINDS.length; i++) for (let j = i + 1; j < PBR_KINDS.length; j++) {
    assert.notDeepEqual(maps.get(PBR_KINDS[i]), maps.get(PBR_KINDS[j]), `${PBR_KINDS[i]} vs ${PBR_KINDS[j]}`);
  }
});

test('normals are bent but never beyond unit length, and the maps carry real variation in every channel', () => {
  for (const k of PBR_KINDS) {
    const m = maps.get(k)!;
    let bent = 0, minR = 255, maxR = 0, minG = 255, maxG = 0, minB = 255, maxB = 0, minA = 255;
    for (let i = 0; i < m.length; i += 4) {
      const nx = m[i] / 255 * 2 - 1, ny = m[i + 1] / 255 * 2 - 1;
      assert.ok(nx * nx + ny * ny <= 1.0001, `${k} normal xy length`);
      if (Math.abs(nx) + Math.abs(ny) > 0.04) bent += 1;
      minR = Math.min(minR, m[i]); maxR = Math.max(maxR, m[i]); minG = Math.min(minG, m[i + 1]); maxG = Math.max(maxG, m[i + 1]);
      minB = Math.min(minB, m[i + 2]); maxB = Math.max(maxB, m[i + 2]); minA = Math.min(minA, m[i + 3]);
    }
    assert.ok(bent > N * N * 0.02, `${k} has relief (${bent})`);
    assert.ok(Math.max(maxR - minR, maxG - minG) > 12, `${k} normal varies`);
    assert.ok(maxB - minB > 12, `${k} roughness varies`);
    assert.ok(minA < 250, `${k} has cavities`);
  }
});

test('the maps tile: roughness and cavity are continuous over the seam (a groove on the seam flips the normal slope, which is correct)', () => {
  for (const k of PBR_KINDS) {
    const m = maps.get(k)!;
    let seam = 0, inner = 0;
    for (let y = 0; y < N; y++) {
      for (const c of [2, 3]) {
        seam += Math.abs(px(m, N - 1, y, c) - px(m, 0, y, c));
        inner += Math.abs(px(m, N / 2, y, c) - px(m, N / 2 + 1, y, c));
      }
    }
    assert.ok(seam <= inner * 2.5 + N * 4, `${k} seam ${seam} against ${inner}`);
  }
});

test('brushed metal streaks along x; wood has seams; asphalt wear makes some patches glossier; leaf thickness varies', () => {
  const steel = maps.get('brushedMetal')!;
  let along = 0, across = 0;
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) { along += Math.abs(px(steel, x, y, 2) - px(steel, x + 1, y, 2)); across += Math.abs(px(steel, x, y, 2) - px(steel, x, y + 1, 2)); }
  assert.ok(across > along * 2, `anisotropy ${across} against ${along}`);
  const wood = maps.get('wood')!;
  let seams = 0;
  for (let y = 0; y < N; y++) if (px(wood, 5, y, 3) < 120) seams += 1;
  assert.ok(seams >= 3, `plank seams ${seams}`);
  const road = maps.get('asphaltWorn')!;
  let lo = 255, hi = 0;
  for (let i = 2; i < road.length; i += 4) { lo = Math.min(lo, road[i]); hi = Math.max(hi, road[i]); }
  assert.ok(hi - lo > 40, 'asphalt wear spreads the roughness');
  const leaf = maps.get('leaf')!;
  let thin = 0;
  for (let i = 3; i < leaf.length; i += 4) if (leaf[i] < 150) thin += 1;
  assert.ok(thin > N * N * 0.1, 'most of a blade is thin');
});

test('the class table points at real kinds and a full 128 map costs well under a second', () => {
  for (const kind of Object.values(PBR_OF_CLASS)) assert.ok(PBR_KINDS.includes(kind), kind);
  const t0 = performance.now();
  for (const k of PBR_KINDS) generatePbr(k);
  const ms = performance.now() - t0;
  console.log(`PBR_GEN_MS total=${ms.toFixed(0)} for ${PBR_KINDS.length} maps at ${PBR_SIZE}`);
  assert.ok(ms < 2500, `generation took ${ms} ms`);
});
