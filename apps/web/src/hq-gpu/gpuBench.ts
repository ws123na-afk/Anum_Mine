/**
 * The Graphics check's measurement run (browser only, its own lazy chunk). It drives the live renderer: the camera orbits the scene
 * and, when the scene has cut-away floors, a floor is lifted and put back, while the time between presented frames is recorded.
 * Frame time here is the gap between animation frames, which is what a person sees; the draw call alone would hide a slow GPU.
 */
import type { Mesh, Texture, WebGLRenderer } from 'three';
import type { CameraState } from '../lib/headquartersSpatial.ts';
import type { HqRenderer } from '../lib/hqThreeRenderer.ts';
import { summarize } from '../lib/hqFrameStats.ts';
import { tierById } from '../lib/hqQualityTiers.ts';
import type { TierResult } from '../lib/hqGpuReport.ts';

const WARMUP_MS = 700;

/** The renderer's own fields the check reads, reached through a cast (see the note in hqThreeRenderer.ts); the e2e spec covers them. */
export type Inner = {
  renderer: WebGLRenderer; meshes: readonly Mesh[]; textures: readonly Texture[]; composer: unknown; cameraState: CameraState | null;
  liftValue: Map<string, number>; startLifts(lifts: Record<string, { to: number }>): void; requestFrame(): void;
};
export const innerOf = (r: HqRenderer): Inner => r as unknown as Inner;
export const liftSnapshot = (r: HqRenderer): Record<string, number> => Object.fromEntries(innerOf(r).liftValue);
export function setLiftValues(r: HqRenderer, values: Record<string, number>): void {
  innerOf(r).startLifts(Object.fromEntries(Object.entries(values).map(([g, to]) => [g, { to }])));
  innerOf(r).requestFrame();
}
/** Buffers in use, for the memory estimate. */
export function partsOf(r: HqRenderer): { meshes: readonly Mesh[]; textures: readonly Texture[]; pixels: number; composer: boolean } {
  const i = innerOf(r);
  const canvas = i.renderer.domElement;
  return { meshes: i.meshes, textures: i.textures, pixels: canvas.width * canvas.height, composer: i.composer !== null };
}

const nextFrame = (): Promise<number> => new Promise((resolve) => requestAnimationFrame(resolve));

export type BenchProgress = (info: { tier: number; index: number; total: number }) => void;

/** Time one tier for `ms` milliseconds. Returns null when `signal` aborted. */
async function runTier(r: HqRenderer, id: number, ms: number, signal: AbortSignal): Promise<TierResult | null> {
  const base = innerOf(r).cameraState;
  if (!base) return null;
  r.setPin(id, true);
  const stand = liftSnapshot(r);
  const floor = Object.keys(stand).find((g) => stand[g] > 0.5);
  const floors = floor !== undefined;
  const frames: number[] = [];
  const started = performance.now();
  let last = 0;
  let liftedDown = false, liftedUp = false;
  for (;;) {
    const now = await nextFrame();
    if (signal.aborted) return null;
    const t = now - started;
    if (t >= WARMUP_MS + ms) break;
    // A scripted orbit: a full turn every ten seconds, with a gentle nod in pitch.
    r.setCamera({ ...base, yaw: base.yaw + (t / 10_000) * Math.PI * 2, pitch: base.pitch + Math.sin(t / 1500) * 0.06 });
    if (floors && t > WARMUP_MS + ms * 0.4 && !liftedDown) { liftedDown = true; setLiftValues(r, { [floor as string]: 0 }); }
    if (floors && t > WARMUP_MS + ms * 0.7 && !liftedUp) { liftedUp = true; setLiftValues(r, { [floor as string]: stand[floor as string] }); }
    if (t > WARMUP_MS && last > 0) frames.push(now - last);
    last = now;
  }
  const s = r.stats;
  const p = partsOf(r);
  const tier = tierById(id);
  let geometry = 0, textures = 0;
  for (const m of p.meshes) for (const a of Object.values(m.geometry.attributes)) geometry += a.array.byteLength;
  for (const tex of p.textures) { const img = tex.image as { width?: number; height?: number } | undefined; textures += (img?.width ?? 0) * (img?.height ?? 0) * 4 * 1.34; }
  const px = p.pixels;
  const memory = geometry + textures + px * 4 * 2 + s.shadowSize * s.shadowSize * 4 + (p.composer ? px * 40 : 0);
  return {
    tier: id, key: tier.key, frames: summarize(frames), drawCalls: s.drawn, triangles: s.triangles, dpr: s.dpr, shadowSize: s.shadowSize, bloom: s.bloom,
    memoryEstimateMb: memory / 1048576, floorChange: floors,
  };
}

/** Measure each tier id in `ids`, in order, then restore the camera, the floors and the tier in force. */
export async function runBenchmark(r: HqRenderer, ids: readonly number[], ms: number, signal: AbortSignal, onProgress: BenchProgress): Promise<TierResult[]> {
  const camera = innerOf(r).cameraState;
  const stand = liftSnapshot(r);
  const out: TierResult[] = [];
  try {
    for (let i = 0; i < ids.length; i++) {
      onProgress({ tier: ids[i], index: i, total: ids.length });
      const result = await runTier(r, ids[i], ms, signal);
      if (!result) break;
      out.push(result);
    }
  } finally {
    r.setPin(null, true);
    if (Object.keys(stand).length > 0) setLiftValues(r, stand);
    if (camera) r.setCamera(camera);
  }
  return out;
}
