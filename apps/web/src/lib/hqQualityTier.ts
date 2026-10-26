/**
 * Quality tiers of the 3D headquarters (docs/ANUM_MINE_HQ_PHOTOREAL.md). Pure: no three.js, no DOM.
 *
 *   0 basic      software rasteriser (SwiftShader, llvmpipe): simple lit materials, no shadows, no bloom
 *   1 light      shadows off or small, PBR materials, reflection map
 *   2 standard   shadows 1024, bloom at night (4 M pixels and more starts here)
 *   3 high       shadows 2048, bloom: the ceiling before the photographic tier
 *   4 photo      everything in tier 3 plus the effects of `hqPhotoFx.ts`: procedural normal and roughness maps, ambient
 *                occlusion in screen space, a shadow fitted to the camera, warm point lights, light shafts, depth of
 *                field, vignette, film grain and AgX tone mapping
 *
 * Software GL never reaches tier 4 on its own: it is only reached by a pin (`localStorage["anum.hq.quality"] = 4`), by the
 * owner on a real GPU, or by the measured promotion below on a discrete or Apple GPU.
 */
export type Quality = 0 | 1 | 2 | 3 | 4;
export type GpuClass = 'software' | 'discrete' | 'apple' | 'integrated' | 'mobile' | 'unknown';

export const TIER_NAMES: Readonly<Record<Quality, string>> = { 0: 'basic', 1: 'light', 2: 'standard', 3: 'high', 4: 'photo' };
export const PHOTO: Quality = 4;
/** Above this many device pixels the first tier is 2 (a 4K canvas): the shadow and bloom cost scale with pixels. */
export const PIXELS_BEFORE_STEP_DOWN = 4_000_000;
/** Promotion to tier 4 needs a probe of this many frames at or under `PROMOTE_MS` each (GPU time, with a finish after every frame). */
export const PROMOTE_FRAMES = 24;
export const PROMOTE_MS = 7;
/** The tier is lowered one step after this many slow frames in a row; a frame is slow above `slowFrameMs(tier)`. */
export const SLOW_FRAMES_TO_STEP_DOWN = 20;

export function classifyRenderer(name: string): GpuClass {
  const n = name.toLowerCase();
  if (/swiftshader|llvmpipe|softpipe|software|basic render/.test(n)) return 'software';
  if (/apple (m\d|gpu)|apple m\d/.test(n)) return 'apple';
  if (/nvidia|geforce|rtx|gtx|quadro|radeon (rx|pro|r9)|amd radeon rx|\barc a\d|arc\(tm\) a\d/.test(n)) return 'discrete';
  if (/adreno|mali|powervr|xclipse|immortalis/.test(n)) return 'mobile';
  if (/intel|uhd|iris|radeon\(tm\) graphics|radeon graphics|vega|apple/.test(n)) return 'integrated';
  return 'unknown';
}

/** The pinned tier, 0 to 4, from the raw `anum.hq.quality` text; null when unset or not a tier. */
export function parsePin(raw: string | null): Quality | null {
  if (raw === null || raw.trim() === '') return null;
  const n = Number(raw);
  return n === 0 || n === 1 || n === 2 || n === 3 || n === 4 ? (n as Quality) : null;
}

/** Where a session starts: a pin wins, software starts at 0, a very large canvas at 2, everything else at 3. */
export function initialTier(pin: Quality | null, gpu: GpuClass, pixels: number): Quality {
  if (pin !== null) return pin;
  if (gpu === 'software') return 0;
  return pixels > PIXELS_BEFORE_STEP_DOWN ? 2 : 3;
}

/** Only these GPUs are ever promoted without a pin: the others stay at 3 however fast one frame was. */
export const canPromote = (gpu: GpuClass): boolean => gpu === 'discrete' || gpu === 'apple';

export type PromoteInput = Readonly<{ tier: Quality; gpu: GpuClass; pixels: number; pinned: boolean; demoted: boolean; reducedMotion: boolean; probeMs: readonly number[] }>;

/** True when a session at tier 3 may step up to the photographic tier: enough probe frames, all fast, never after a step down. */
export function shouldPromote(i: PromoteInput): boolean {
  if (i.tier !== 3 || i.pinned || i.demoted || i.reducedMotion || !canPromote(i.gpu) || i.pixels > PIXELS_BEFORE_STEP_DOWN) return false;
  if (i.probeMs.length < PROMOTE_FRAMES) return false;
  const sorted = [...i.probeMs].sort((a, b) => a - b);
  const median = sorted[sorted.length >> 1];
  return median <= PROMOTE_MS && sorted[sorted.length - 1] <= PROMOTE_MS * 3;
}

/** The frame time above which a frame counts as slow: the photographic tier steps down earlier (it asks for 45 frames a second). */
export const slowFrameMs = (tier: Quality): number => (tier === 4 ? 22 : 30);

/** Shadow map size per tier; tier 4 fits one 4096 map to the camera instead of the whole campus. */
export const SHADOW_SIZE: Readonly<Record<Quality, number>> = { 0: 0, 1: 1024, 2: 1024, 3: 2048, 4: 4096 };
