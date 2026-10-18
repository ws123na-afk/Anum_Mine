// Business prospecting and outreach (docs/business.md#prospecting-pipeline, #outreach): labels, tones and
// small pure rules for the Prospecting and Outreach tabs. The API is the authority; these only shape the UI.
import type { BusinessSignal, OutboxStatus, PipelineRun, ProductFit, RunEstimate, RunStatus, TargetProfileInput } from '@anum/contracts';
import { formatNumber, t, type MessageKey } from './i18n.ts';
import type { Tone } from './business.ts';

const label = (key: string) => t(key as MessageKey);
export const runStatusLabel = (status: RunStatus) => label(`biz.run.status.${status}`);
export const outboxStatusLabel = (status: OutboxStatus) => label(`biz.outbox.status.${status}`);
export const OUTBOX_STATUSES: readonly OutboxStatus[] = ['pending_approval', 'held', 'scheduled', 'sending', 'sent', 'needs_review', 'failed', 'cancelled', 'rejected'];
export const SIZES = ['micro', 'small', 'medium', 'large', 'enterprise'] as const;

export function runTone(status: RunStatus): Tone {
  if (status === 'succeeded') return 'success';
  if (status === 'failed' || status === 'interrupted') return 'dangerTone';
  if (status === 'partial' || status === 'stopped' || status === 'running') return 'warning';
  return 'neutral';
}

export function outboxTone(status: OutboxStatus): Tone {
  if (status === 'sent') return 'success';
  if (status === 'failed' || status === 'rejected' || status === 'cancelled' || status === 'needs_review') return 'dangerTone';
  if (status === 'pending_approval' || status === 'held' || status === 'sending') return 'warning';
  return 'neutral';
}

/** A run still working (poll it) or waiting for a person's confirmation. */
export const isActive = (run: Pick<PipelineRun, 'status'>) => run.status === 'running';

/** Comma or new-line separated words, trimmed, without duplicates (case-insensitive), at most `limit`. */
export function wordsOf(text: string, limit = 20): string[] {
  const out: string[] = [];
  for (const part of text.split(/[\n,]/)) {
    const value = part.replace(/\s+/g, ' ').trim();
    if (value && !out.some((item) => item.toLocaleLowerCase() === value.toLocaleLowerCase())) out.push(value);
    if (out.length >= limit) break;
  }
  return out;
}

/** Signals grouped for the confirmation list, in the catalog's order. */
export function signalGroups(signals: readonly BusinessSignal[]): { category: BusinessSignal['category']; items: BusinessSignal[] }[] {
  const order: BusinessSignal['category'][] = ['discovery', 'profile', 'channel'];
  return order.map((category) => ({ category, items: signals.filter((item) => item.category === category) })).filter((group) => group.items.length > 0);
}

/** One line of what a run will use at most, for the estimate panel. */
export function estimateLine(estimate: RunEstimate): string {
  const cost = estimate.estimated_cost_usd === null ? t('biz.run.costUnknown') : `USD ${estimate.estimated_cost_usd.toFixed(4)}`;
  return t('biz.run.estimateLine', {
    searches: formatNumber(estimate.searches), pages: formatNumber(estimate.page_fetches), calls: formatNumber(estimate.model_calls), cost,
  });
}

/** "4/5" for a product fit; unknown without a target profile. */
export function fitText(fit: Pick<ProductFit, 'score' | 'disqualified'>): string {
  if (fit.disqualified) return t('biz.fit.disqualified');
  return fit.score === null ? t('biz.fit.noProfile') : t('biz.fit.score', { score: fit.score });
}

export interface TargetDraft { industries: string; sizes: string[]; regions: string; roles: string; keywords: string; disqualifiers: string; pitch: string; pitchAr: string }

export const targetDraft = (profile: TargetProfileInput): TargetDraft => ({
  industries: profile.industries.join(', '), sizes: [...profile.company_sizes], regions: profile.regions.join(', '), roles: profile.roles.join(', '),
  keywords: profile.keywords.join(', '), disqualifiers: profile.disqualifiers.join(', '), pitch: profile.value_proposition, pitchAr: profile.value_proposition_ar,
});

export function targetBody(draft: TargetDraft): TargetProfileInput {
  return {
    industries: wordsOf(draft.industries), company_sizes: draft.sizes.filter((size): size is (typeof SIZES)[number] => (SIZES as readonly string[]).includes(size)) as TargetProfileInput['company_sizes'],
    regions: wordsOf(draft.regions), roles: wordsOf(draft.roles), keywords: wordsOf(draft.keywords, 30), disqualifiers: wordsOf(draft.disqualifiers, 30),
    value_proposition: draft.pitch.trim(), value_proposition_ar: draft.pitchAr.trim(),
  };
}

/** A rate with its denominator ("2 of 40, 5%"); unknown when nothing was sent. */
export function rateText(rate: { count: number; of: number; rate_pct: number | null }): string {
  return t('biz.outreach.rate', { count: rate.count, of: rate.of, pct: rate.rate_pct === null ? '-' : `${formatNumber(rate.rate_pct)}%` });
}
