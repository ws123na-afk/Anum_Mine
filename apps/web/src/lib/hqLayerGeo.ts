/** Turns a MeshBuilder vertex buffer (13 floats a vertex, sRGB colours) into a three.js geometry with linear vertex colours. Shared by the scenery and work layers (lazy chunks). */
import { BufferGeometry, Float32BufferAttribute } from 'three';
import { VERTEX_FLOATS } from './hqGeometry.ts';

const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

export function geometryOf(v: Float32Array): BufferGeometry {
  const n = v.length / VERTEX_FLOATS;
  const position = new Float32Array(n * 3), normal = new Float32Array(n * 3), color = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const s = i * VERTEX_FLOATS;
    position.set([v[s], v[s + 1], v[s + 2]], i * 3);
    normal.set([v[s + 3], v[s + 4], v[s + 5]], i * 3);
    color.set([srgbToLinear(v[s + 6]), srgbToLinear(v[s + 7]), srgbToLinear(v[s + 8])], i * 3);
  }
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute(position, 3));
  g.setAttribute('normal', new Float32BufferAttribute(normal, 3));
  g.setAttribute('color', new Float32BufferAttribute(color, 3));
  g.computeBoundingSphere();
  return g;
}
