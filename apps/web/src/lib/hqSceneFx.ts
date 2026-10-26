/**
 * Baked vertex ambient occlusion with tall walls cut so the gradient is resolved: costs nothing per frame, so every tier gets it (a
 * lazy chunk of its own, no three.js in it). The maths is in `hqBakedAo.ts` (pure, tested); this file caches the result per vertex
 * array, and by content, so a floor that is rebuilt with identical geometry is not baked twice.
 */
import { bakeScene, contentKey } from './hqBakedAo.ts';

export type Prepared = Readonly<{ vertices: Float32Array; ao: Float32Array }>;

/** A cheap (software) renderer bakes at most this many vertices; the others bake any scene up to the cap in `hqBakedAo.ts`. */
export const CHEAP_AO_VERTICES = 260_000;

const aoCache = new WeakMap<Float32Array, Prepared>();
/** A scene that is rebuilt with identical geometry (live data redraws a floor every few seconds) must not be baked again: look it up by content. */
const byContent = new Map<string, Prepared>();
const MAX_CONTENT_ENTRIES = 12;

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
