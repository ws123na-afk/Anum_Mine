/**
 * Surface classes for meshes built without them (the exterior asset builders draw plain colours).
 * `classifySurfaces` gives every triangle that is still `flat` a class from what it is: ground-level
 * flat surfaces become grass, tile or asphalt by colour, big walls and roofs become concrete.
 * Windows (emissive weight 2 or more), signs and anything a builder already classified are left alone.
 * Pure: takes and returns the interleaved float array of `hqGeometry.ts`.
 */
import { MAT, VERTEX_FLOATS } from './hqGeometry.ts';

export function classifySurfaces(source: Float32Array): Float32Array {
  const v = new Float32Array(source);
  const triangleCount = Math.floor(v.length / VERTEX_FLOATS / 3);
  for (let t = 0; t < triangleCount; t++) {
    const a = t * 3 * VERTEX_FLOATS;
    if (v[a + 10] !== MAT.flat || v[a + 9] >= 0.8) continue;
    const ny = v[a + 4];
    let r = 0, g = 0, b = 0, y = 0;
    for (let k = 0; k < 3; k++) {
      const o = a + k * VERTEX_FLOATS;
      r += v[o + 6]; g += v[o + 7]; b += v[o + 8]; y += v[o + 1];
    }
    r /= 3; g /= 3; b /= 3; y /= 3;
    const lum = 0.3 * r + 0.59 * g + 0.11 * b;
    let cls: number = MAT.flat;
    if (ny > 0.9 && y < 0.2) {
      if (g > r * 1.12 && g > b * 1.12) cls = MAT.grass;
      else if (lum < 0.3) cls = MAT.asphalt;
      else if (lum > 0.5) cls = MAT.tile;
      else cls = MAT.concrete;
    } else if (Math.abs(ny) < 0.3 && lum > 0.55) {
      // A wall: area decides, so thin fins and window frames keep their painted colour.
      const area = triangleArea(v, a);
      if (area > 1.5) cls = MAT.concrete;
    } else if (ny > 0.9 && y > 2 && lum > 0.4) {
      cls = MAT.concrete;
    }
    if (cls === MAT.flat) continue;
    for (let k = 0; k < 3; k++) v[a + k * VERTEX_FLOATS + 10] = cls;
  }
  return v;
}

function triangleArea(v: Float32Array, a: number): number {
  const p = (k: number) => [v[a + k * VERTEX_FLOATS], v[a + k * VERTEX_FLOATS + 1], v[a + k * VERTEX_FLOATS + 2]];
  const [p0, p1, p2] = [p(0), p(1), p(2)];
  const ux = p1[0] - p0[0], uy = p1[1] - p0[1], uz = p1[2] - p0[2];
  const wx = p2[0] - p0[0], wy = p2[1] - p0[1], wz = p2[2] - p0[2];
  return 0.5 * Math.hypot(uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx);
}
