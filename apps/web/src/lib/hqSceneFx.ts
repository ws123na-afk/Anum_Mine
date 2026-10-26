/**
 * Scene effects that cost nothing per frame (loaded as their own lazy chunk by the renderer, in every tier except none): baked
 * vertex ambient occlusion with tall walls cut so the gradient is resolved, geometry detail on campus roofs and walls, and the
 * soft contact shadow discs under cars, planters, trees and the foot of walls. The maths is in `hqBakedAo.ts` and `hqDetailPass.ts`
 * (pure, tested); this file caches their results per vertex array and builds the one transparent mesh for the discs.
 */
import { BufferGeometry, DataTexture, Float32BufferAttribute, LinearFilter, Uint32BufferAttribute, Mesh, MeshBasicMaterial, RGBAFormat, UnsignedByteType, type Texture } from 'three';
import { bakeScene, contentKey, deriveContacts, CONTACT_FLOATS } from './hqBakedAo.ts';
import { deriveDetail } from './hqDetailPass.ts';

export type Prepared = Readonly<{ vertices: Float32Array; ao: Float32Array }>;

/** A cheap (software) renderer bakes at most this many vertices; the others bake any scene up to the cap in `hqBakedAo.ts`. */
export const CHEAP_AO_VERTICES = 260_000;

const aoCache = new WeakMap<Float32Array, Prepared>();
/** A scene that is rebuilt with identical geometry (live data redraws a floor every few seconds) must not be baked again: look it up by content. */
const byContent = new Map<string, Prepared>();
const MAX_CONTENT_ENTRIES = 12;
const detailCache = new WeakMap<Float32Array, Float32Array | null>();
const contactCache = new WeakMap<Float32Array, Float32Array>();

export function prepare(v: Float32Array, cheap: boolean): Prepared {
  let hit = aoCache.get(v);
  if (hit) return hit;
  const key = `${cheap ? 'c' : 'f'}${contentKey(v)}`;
  hit = byContent.get(key);
  if (!hit) {
    hit = bakeScene(v, cheap ? { maxVertices: CHEAP_AO_VERTICES } : {});
    byContent.set(key, hit);
    if (byContent.size > MAX_CONTENT_ENTRIES) byContent.delete(byContent.keys().next().value as string);
  }
  aoCache.set(v, hit);
  return hit;
}

/** Roof and wall detail for one building sized item; null when it has no roof worth detailing. */
export function detailOf(v: Float32Array, seed: string): Float32Array | null {
  if (detailCache.has(v)) return detailCache.get(v)!;
  const r = deriveDetail(v, { seed });
  const out = r.triangles > 0 ? r.vertices : null;
  detailCache.set(v, out);
  return out;
}

export function contactsOf(v: Float32Array): Float32Array {
  let hit = contactCache.get(v);
  if (!hit) { hit = deriveContacts(v); contactCache.set(v, hit); }
  return hit;
}

let radial: Texture | null = null;
/** A soft round falloff, 64 px, in the alpha channel. */
function radialTexture(): Texture {
  if (radial) return radial;
  const n = 64, data = new Uint8Array(n * n * 4);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const d = Math.hypot((x + 0.5) / n - 0.5, (y + 0.5) / n - 0.5) * 2;
    const a = Math.max(0, 1 - d);
    data[(y * n + x) * 4 + 3] = Math.round(255 * a * a * (3 - 2 * a));
  }
  const tex = new DataTexture(data, n, n, RGBAFormat, UnsignedByteType);
  tex.magFilter = tex.minFilter = LinearFilter;
  tex.needsUpdate = true;
  radial = tex;
  return tex;
}

/** One flat mesh with a quad per contact disc (x, y, z, radius, strength). Strength is the alpha; `opacity` scales all of them. */
export function contactMesh(contacts: Float32Array): Mesh {
  const n = contacts.length / CONTACT_FLOATS;
  const pos = new Float32Array(n * 12), uv = new Float32Array(n * 8), col = new Float32Array(n * 16), idx = new Uint32Array(n * 6);
  for (let i = 0; i < n; i++) {
    const [x, y, z, r, s] = [contacts[i * 5], contacts[i * 5 + 1], contacts[i * 5 + 2], contacts[i * 5 + 3], contacts[i * 5 + 4]];
    const yy = y + 0.07;
    pos.set([x - r, yy, z - r, x + r, yy, z - r, x + r, yy, z + r, x - r, yy, z + r], i * 12);
    uv.set([0, 0, 1, 0, 1, 1, 0, 1], i * 8);
    for (let k = 0; k < 4; k++) col.set([0, 0, 0, s], i * 16 + k * 4);
    idx.set([i * 4, i * 4 + 2, i * 4 + 1, i * 4, i * 4 + 3, i * 4 + 2], i * 6);
  }
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new Float32BufferAttribute(uv, 2));
  g.setAttribute('color', new Float32BufferAttribute(col, 4));
  g.setIndex(new Uint32BufferAttribute(idx, 1));
  g.computeBoundingSphere();
  const m = new MeshBasicMaterial({ map: radialTexture(), vertexColors: true, transparent: true, depthWrite: false, fog: false, toneMapped: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4 });
  const mesh = new Mesh(g, m);
  mesh.renderOrder = 2;
  mesh.frustumCulled = false;
  mesh.userData.contacts = n;
  return mesh;
}
