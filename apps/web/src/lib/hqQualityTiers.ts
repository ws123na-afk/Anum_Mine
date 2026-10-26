/**
 * The one registry of headquarters quality tiers. The renderer, the frame governor, the quality chip and the Graphics check
 * all read it, so a new tier (add one entry) appears everywhere without another edit. Pure: no three.js and no DOM.
 *
 * A tier fixes what the picture costs: the shadow map size (0 = no shadows) and the night glow pass. The pixel ratio cap and the
 * simple-material path are not tiers: the frame governor lowers the first at run time and a software rasteriser gets the second.
 */
export type TierSpec = Readonly<{
  /** Stored in `localStorage["anum.hq.quality"]` when a person pins the tier. Higher is heavier. */
  id: number;
  /** Message key suffix (`hqGpu.tier.<key>`) and the name in reports. */
  key: string;
  shadowSize: number;
  bloom: boolean;
}>;

export const HQ_TIERS: readonly TierSpec[] = [
  { id: 0, key: 'low', shadowSize: 0, bloom: false },
  { id: 1, key: 'medium', shadowSize: 1024, bloom: false },
  { id: 2, key: 'high', shadowSize: 1024, bloom: true },
  { id: 3, key: 'ultra', shadowSize: 2048, bloom: true },
];

/** The browser storage key a person (or the Graphics check) pins a tier with. */
export const QUALITY_PIN_KEY = 'anum.hq.quality';

export const topTier = (): TierSpec => HQ_TIERS[HQ_TIERS.length - 1];
export const tierById = (id: number): TierSpec => HQ_TIERS.find((t) => t.id === id) ?? topTier();
export const isTierId = (value: unknown): value is number => typeof value === 'number' && HQ_TIERS.some((t) => t.id === value);

/** A stored pin to a tier id, or null for anything that is not a registered tier (so a stale value never breaks the scene). */
export function parsePin(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined || raw.trim() === '') return null;
  const n = Number(raw);
  return Number.isInteger(n) && isTierId(n) ? n : null;
}

/** What one rung of the governor ladder draws with. `dprScale` multiplies the pixel ratio; `lean` swaps in the simple materials. */
export type StepSettings = Readonly<{ dprScale: number; shadowSize: number; bloom: boolean; lean: boolean }>;

/**
 * The rungs the frame governor walks down, in order: pixel ratio first, then the shadow map (smaller, then off), then the night glow,
 * then the glass and reflection effects (the simple materials). Steps that change nothing for a tier are left out.
 */
export function ladderFor(tier: TierSpec): readonly StepSettings[] {
  const base: StepSettings = { dprScale: 1, shadowSize: tier.shadowSize, bloom: tier.bloom, lean: false };
  const steps: StepSettings[] = [base, { ...base, dprScale: 0.85 }, { ...base, dprScale: 0.7 }];
  let cur = steps[2];
  if (cur.shadowSize > 1024) { cur = { ...cur, shadowSize: 1024 }; steps.push(cur); }
  if (cur.shadowSize > 0) { cur = { ...cur, shadowSize: 0 }; steps.push(cur); }
  if (cur.bloom) { cur = { ...cur, bloom: false }; steps.push(cur); }
  steps.push({ ...cur, lean: true });
  return steps;
}

/** Why the renderer runs the tier it runs. */
export type TierReason = 'pinned' | 'software' | 'large-canvas' | 'automatic';

/** The renderer's current quality, as the chip and the Graphics check show it. */
export type QualityState = Readonly<{
  tier: number;
  reason: TierReason;
  /** Rungs the governor has stepped down (0 = full tier). */
  step: number;
  steps: number;
  settings: StepSettings;
  software: boolean;
  /** The governor is allowed to change the picture (false when pinned, on a software rasteriser, or during a benchmark). */
  governed: boolean;
}>;
