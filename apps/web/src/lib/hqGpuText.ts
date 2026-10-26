/** Words for the quality chip and the Graphics check: tier names and the reason a tier is in force. */
import { t, type MessageKey } from './i18n.ts';
import { tierById, type QualityState } from './hqQualityTiers.ts';

/** The tier's name in the current language; a tier added to the registry without a message falls back to its key. */
export function tierName(id: number): string {
  const tier = tierById(id);
  const key = `hqGpu.tier.${tier.key}` as MessageKey;
  const name = t(key);
  return name === key ? tier.key : name;
}

/** What the governor has taken away so far, in the order it takes things. */
export function reducedParts(state: QualityState): string[] {
  const full = tierById(state.tier);
  const parts: string[] = [];
  if (state.settings.dprScale < 1) parts.push(t('hqGpu.what.dpr'));
  if (state.settings.shadowSize < full.shadowSize) parts.push(t('hqGpu.what.shadows'));
  if (full.bloom && !state.settings.bloom) parts.push(t('hqGpu.what.glow'));
  if (state.settings.lean) parts.push(t('hqGpu.what.glass'));
  return parts;
}

export const isReduced = (state: QualityState): boolean => reducedParts(state).length > 0;

export function chipLabel(state: QualityState): string {
  return t(isReduced(state) ? 'hqGpu.chip.reduced' : 'hqGpu.chip', { tier: tierName(state.tier) });
}

/** The reason in a sentence (the chip's tooltip and the panel). */
export function reasonText(state: QualityState): string {
  const tier = tierName(state.tier);
  const base = state.reason === 'pinned' ? t('hqGpu.hint.pinned', { tier })
    : state.reason === 'software' ? t('hqGpu.hint.software')
      : state.reason === 'large-canvas' ? t('hqGpu.hint.large', { tier })
        : t('hqGpu.hint.automatic', { tier });
  const parts = reducedParts(state);
  return parts.length ? `${base} ${t('hqGpu.hint.reduced', { what: parts.join(', ') })}` : base;
}
