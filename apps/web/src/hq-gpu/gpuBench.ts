/**
 * The Graphics check's measurement run (browser only, its own lazy chunk). It drives the live renderer: the camera orbits the scene
 * and, when the scene has cut-away floors, a floor is lifted and put back, while the time between presented frames is recorded.
 * Frame time here is the gap between animation frames, which is what a person sees; the draw call alone would hide a slow GPU. Before the
 * first tier, every shader variant the tiers use is compiled; after each switch the run waits until the frames settle and times nothing
 * of that warm-up. Where the browser has the GPU timer query, GPU time per frame is recorded too, and every long frame says whether it
 * compiled shaders or allocated buffers, so a report tells a slow GPU from a stall.
 */
import type { Mesh, Texture, WebGLRenderer } from 'three';
import type { CameraState } from '../lib/headquartersSpatial.ts';
import type { HqRenderer } from '../lib/hqThreeRenderer.ts';
import { percentile, summarize } from '../lib/hqFrameStats.ts';
import { tierById } from '../lib/hqQualityTiers.ts';
import type { TierResult } from '../lib/hqGpuReport.ts';

/**
 * Warm-up after each tier switch: at least `minMs` and `minFrames`, then until the last `window` frame gaps hold no outlier (none longer
 * than `ratio` times their median, or `floorMs`), at most `maxMs`. A switch compiles shaders, rebuilds the shadow map and uploads
 * textures; the first real report counted those stalls (a 3.4 s first frame on Low) because the old warm-up was a fixed 0.7 s and the
 * frame that straddled its end was kept. Now the gap that ends the warm-up is never timed.
 */
export const WARM = { minMs: 700, minFrames: 12, maxMs: 6000, window: 8, ratio: 2.5, floorMs: 34 } as const;

/** True once the frames after a tier switch have settled (see `WARM`). `gaps` are the frame gaps seen since the switch, oldest first. */
export function warmedUp(elapsedMs: number, gaps: readonly number[]): boolean {
  if (elapsedMs >= WARM.maxMs) return true;
  if (elapsedMs < WARM.minMs || gaps.length < WARM.minFrames) return false;
  const last = gaps.slice(-WARM.window);
  const sorted = [...last].sort((a, b) => a - b);
  const median = sorted[sorted.length >> 1];
  return sorted[sorted.length - 1] <= Math.max(WARM.floorMs, median * WARM.ratio);
}

/** A frame this long in a timed run is a hitch; the report says what happened in it (new shader programs, new buffers or textures). */
export const HITCH_MS = 50;

/** The renderer's own fields the check reads, reached through a cast (see the note in hqThreeRenderer.ts); the e2e spec covers them. */
export type Inner = {
  prewarm?(ids?: readonly number[]): Promise<unknown>;
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

type GL = WebGL2RenderingContext;
type TimerExt = { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number };

/**
 * GPU time per frame with EXT_disjoint_timer_query_webgl2 where the browser offers it (null otherwise): a query brackets the commands
 * issued between two animation frames, which in a run is the renderer's draw. Read back a few frames later, never waited for.
 */
function gpuTimer(gl: GL | null): { tick(): void; poll(out: number[]): void; stop(): void } | null {
  const ext = gl?.getExtension?.('EXT_disjoint_timer_query_webgl2') as TimerExt | null | undefined;
  if (!gl || !ext) return null;
  let active: WebGLQuery | null = null;
  const pending: WebGLQuery[] = [];
  const end = () => { if (active) { gl.endQuery(ext.TIME_ELAPSED_EXT); pending.push(active); active = null; } };
  return {
    tick() { end(); active = gl.createQuery(); if (active) gl.beginQuery(ext.TIME_ELAPSED_EXT, active); },
    poll(out) {
      while (pending.length > 0 && gl.getQueryParameter(pending[0], gl.QUERY_RESULT_AVAILABLE)) {
        const q = pending.shift()!;
        const ns = Number(gl.getQueryParameter(q, gl.QUERY_RESULT));
        if (!gl.getParameter(ext.GPU_DISJOINT_EXT) && ns > 0) out.push(ns / 1e6);
        gl.deleteQuery(q);
      }
    },
    stop() { end(); for (const q of pending) gl.deleteQuery(q); pending.length = 0; },
  };
}

/** Programs, geometries and textures three.js holds now: a frame that adds to them compiled shaders or uploaded buffers. */
function counts(r: HqRenderer): { programs: number; buffers: number } {
  const info = innerOf(r).renderer.info as { programs?: unknown[] | null; memory?: { geometries: number; textures: number } } | undefined;
  return { programs: info?.programs?.length ?? 0, buffers: (info?.memory?.geometries ?? 0) + (info?.memory?.textures ?? 0) };
}

/** Time one tier for `ms` milliseconds after it has warmed up. Returns null when `signal` aborted. */
async function runTier(r: HqRenderer, id: number, ms: number, signal: AbortSignal): Promise<TierResult | null> {
  const base = innerOf(r).cameraState;
  if (!base) return null;
  performance.mark(`hq:bench:${id}:switch`);
  r.setPin(id, true);
  const stand = liftSnapshot(r);
  const floor = Object.keys(stand).find((g) => stand[g] > 0.5);
  const floors = floor !== undefined;
  const orbit = (t: number) => r.setCamera({ ...base, yaw: base.yaw + (t / 10_000) * Math.PI * 2, pitch: base.pitch + Math.sin(t / 1500) * 0.06 });
  // Warm-up: the camera already orbits (so the frames are the same kind as the timed ones), nothing of it is counted.
  const switched = performance.now();
  const warmGaps: number[] = [];
  let last = 0;
  for (;;) {
    const now = await nextFrame();
    if (signal.aborted) return null;
    if (last > 0) warmGaps.push(now - last);
    last = now;
    orbit(now - switched);
    if (warmedUp(now - switched, warmGaps)) break;
  }
  const warmupMs = performance.now() - switched;
  performance.mark(`hq:bench:${id}:timed`);
  const gl = (innerOf(r).renderer.getContext?.() ?? null) as GL | null;
  const timer = gpuTimer(gl);
  const gpu: number[] = [];
  const frames: number[] = [];
  let hitches = 0, compile = 0, buffers = 0;
  let before = counts(r);
  let changed = { programs: false, buffers: false };
  const started = last;
  let liftedDown = false, liftedUp = false;
  timer?.tick();
  for (;;) {
    const now = await nextFrame();
    if (signal.aborted) { timer?.stop(); return null; }
    timer?.tick();
    timer?.poll(gpu);
    const t = now - started;
    const gap = now - last;
    last = now;
    if (t > ms) break;
    frames.push(gap);
    // The long gap ending now was spent in the previous frame's draw: what that draw changed was counted at the previous tick.
    if (gap > HITCH_MS) { hitches += 1; if (changed.programs) compile += 1; if (changed.buffers) buffers += 1; }
    const after = counts(r);
    changed = { programs: after.programs > before.programs, buffers: after.buffers > before.buffers };
    before = after;
    orbit(now - switched);
    if (floors && t > ms * 0.4 && !liftedDown) { liftedDown = true; setLiftValues(r, { [floor as string]: 0 }); }
    if (floors && t > ms * 0.7 && !liftedUp) { liftedUp = true; setLiftValues(r, { [floor as string]: stand[floor as string] }); }
  }
  for (let i = 0; i < 4 && timer; i++) { await nextFrame(); timer.poll(gpu); }
  timer?.stop();
  const s = r.stats;
  const p = partsOf(r);
  const tier = tierById(id);
  let geometry = 0, textures = 0;
  for (const m of p.meshes) for (const a of Object.values(m.geometry.attributes)) geometry += a.array.byteLength;
  for (const tex of p.textures) { const img = tex.image as { width?: number; height?: number } | undefined; textures += (img?.width ?? 0) * (img?.height ?? 0) * 4 * 1.34; }
  const px = p.pixels;
  const memory = geometry + textures + px * 4 * 2 + s.shadowSize * s.shadowSize * 4 + (p.composer ? px * 40 : 0);
  const gpuSorted = [...gpu].sort((a, b) => a - b);
  return {
    tier: id, key: tier.key, frames: summarize(frames), drawCalls: s.drawn, triangles: s.triangles, dpr: s.dpr, shadowSize: s.shadowSize, bloom: s.bloom,
    memoryEstimateMb: memory / 1048576, floorChange: floors, warmupMs,
    gpu: gpuSorted.length >= 10 ? { medianMs: percentile(gpuSorted, 0.5), p95Ms: percentile(gpuSorted, 0.95), samples: gpuSorted.length } : null,
    hitches: { count: hitches, compile, buffers },
  };
}

/** Measure each tier id in `ids`, in order, then restore the camera, the floors and the tier in force. */
export async function runBenchmark(r: HqRenderer, ids: readonly number[], ms: number, signal: AbortSignal, onProgress: BenchProgress): Promise<TierResult[]> {
  const camera = innerOf(r).cameraState;
  const stand = liftSnapshot(r);
  const out: TierResult[] = [];
  try {
    // Every shader variant the measured tiers use is compiled before the first one is timed (hqWarmup.ts), so no tier pays for the next one's programs.
    await innerOf(r).prewarm?.(ids);
    if (signal.aborted) return out;
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
