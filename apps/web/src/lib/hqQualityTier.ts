/**
 * What the photographic extension adds on top of the registered tiers (`hqQualityTiers.ts`), as pure decisions: GPU classes, the measured
 * promotion and the effect names. Pure: no three.js, no DOM. docs/ANUM_MINE_HQ_PHOTOREAL.md describes the whole ladder.
 *
 * The extension is not a registry tier (the Graphics check measures registered tiers only, and a photographic frame takes minutes to compile on
 * a software rasteriser): it is the ultra tier plus procedural normal and roughness maps, ambient occlusion in screen space, a shadow fitted to
 * the camera, warm point lights, light shafts, depth of field, vignette, film grain and AgX tone mapping. It runs on the full rung of the
 * governor's ladder only; the first step down ends it for good. It is reached by `anum.hq.quality` = 4, or by the measured promotion below.
 */
export type GpuClass = 'software' | 'discrete' | 'apple' | 'integrated' | 'mobile' | 'unknown';

/** Above this many device pixels the extension is never promoted (a 4K canvas): the post stack cost scales with pixels. */
export const PIXELS_BEFORE_STEP_DOWN = 4_000_000;
/** Promotion needs a probe of this many frames at or under `PROMOTE_MS` each (GPU time, a one pixel read after every frame). */
export const PROMOTE_FRAMES = 24;
export const PROMOTE_MS = 7;

export function classifyRenderer(name: string): GpuClass {
  const n = name.toLowerCase();
  if (/swiftshader|llvmpipe|softpipe|software|basic render/.test(n)) return 'software';
  if (/apple (m\d|gpu)|apple m\d/.test(n)) return 'apple';
  if (/nvidia|geforce|rtx|gtx|quadro|radeon (rx|pro|r9)|amd radeon rx|\barc a\d|arc\(tm\) a\d/.test(n)) return 'discrete';
  if (/adreno|mali|powervr|xclipse|immortalis/.test(n)) return 'mobile';
  if (/intel|uhd|iris|radeon\(tm\) graphics|radeon graphics|vega|apple/.test(n)) return 'integrated';
  return 'unknown';
}

/**
 * Older integrated graphics, which start one tier lower than the integrated rule: Intel HD Graphics with a four digit model or none
 * (2012 to 2015, 7th and 8th generation), Intel's 12 to 18 unit chips (HD or UHD 500, 505, 510, 600, 605, 610: half the units of the
 * UHD 620 measured below) and AMD's R-series APUs (2014 to 2016).
 */
const OLDER_INTEGRATED = /\bhd graphics(?! ?[5-9]\d\d\b)|\b(u?hd) graphics (50[05]|510|60[05]|610)\b|radeon(\(tm\))? r[2-7] graphics/;

/**
 * The tier a session starts on before anything is measured (docs/ANUM_MINE_HQ_GPU_CHECK.md, "The automatic choice"). The rule rests on the
 * renderer string and on the first real report: an integrated Intel UHD Graphics 620 (Windows, Chrome) held 60 frames a second at High and
 * Medium (95th percentile 16.8 ms) but not at Ultra (95th percentile 66.7 ms, 14 dropped frames in 201), so integrated graphics start at
 * High and the older ones above at Medium. Discrete cards and Apple M chips start at Ultra; phone GPUs at Medium; a masked or unknown name
 * at High (the frame governor still lowers any tier that is too heavy). A very large canvas never starts above High.
 */
export function startTierFor(name: string, pixels: number): number {
  const gpu = classifyRenderer(name);
  const n = name.toLowerCase();
  const tier = gpu === 'software' ? 0
    : gpu === 'discrete' || (gpu === 'apple' && /apple m\d/.test(n)) ? 3
      : gpu === 'mobile' || ((gpu === 'integrated' || gpu === 'unknown') && OLDER_INTEGRATED.test(n)) ? 1
        : 2;
  return pixels > PIXELS_BEFORE_STEP_DOWN ? Math.min(tier, 2) : tier;
}

/** Only these GPUs are ever promoted without a pin: the others stay on the ultra tier however fast one frame was. */
export const canPromote = (gpu: GpuClass): boolean => gpu === 'discrete' || gpu === 'apple';

export type PromoteInput = Readonly<{ tier: number; gpu: GpuClass; pixels: number; pinned: boolean; demoted: boolean; reducedMotion: boolean; probeMs: readonly number[] }>;

/** True when a session on the ultra tier (3) may switch the extension on: enough probe frames, all fast, never after a pin or a step down. */
export function shouldPromote(i: PromoteInput): boolean {
  if (i.tier !== 3 || i.pinned || i.demoted || i.reducedMotion || !canPromote(i.gpu) || i.pixels > PIXELS_BEFORE_STEP_DOWN) return false;
  if (i.probeMs.length < PROMOTE_FRAMES) return false;
  const sorted = [...i.probeMs].sort((a, b) => a - b);
  const median = sorted[sorted.length >> 1];
  return median <= PROMOTE_MS && sorted[sorted.length - 1] <= PROMOTE_MS * 3;
}
/**
 * Effects a person (or a test, or the cost measurements in the performance doc) can switch off one by one with
 * `localStorage["anum.hq.fxoff"]`, a comma list: baked ambient occlusion, roof and wall detail, contact discs, PBR detail maps,
 * screen space occlusion, depth of field, light shafts, grain and vignette, bloom, warm point lights and the camera fitted shadow.
 */
export const FX_NAMES = ['ao', 'detail', 'contact', 'pbr', 'gtao', 'dof', 'shafts', 'grain', 'bloom', 'lights', 'cascade'] as const;
export type FxName = (typeof FX_NAMES)[number];

export function parseFxOff(raw: string | null): ReadonlySet<FxName> {
  const out = new Set<FxName>();
  for (const part of (raw ?? '').split(',')) {
    const name = part.trim().toLowerCase();
    if ((FX_NAMES as readonly string[]).includes(name)) out.add(name as FxName);
  }
  return out;
}
