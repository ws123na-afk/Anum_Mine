// Live eligibility preflight (docs/finance.md#live-eligibility-preflight): the form draft, its checks and
// the request. Pure so `pnpm --filter @anum/web test:unit` covers it. Ticked facts are the person's own
// notes, not evidence: whatever is sent, the server answers that live trading is off and no order was sent.
import type {
  FinanceBrokerAsset, FinanceBrokerKind, FinancePreflight, FinancePreflightDataFact, FinancePreflightFact, FinancePreflightInput,
  FinancePreflightResearchFact, FinancePreflightReviewFact,
} from '@anum/contracts';
import { t } from './i18n.ts';

export type Checked<T> = { ok: true; value: T } | { ok: false; error: string };

/** The same three groups and order as finance/broker_readiness.py. */
export const PREFLIGHT_GROUPS: {
  data: readonly FinancePreflightDataFact[];
  research: readonly FinancePreflightResearchFact[];
  live_review: readonly FinancePreflightReviewFact[];
} = {
  data: [
    'regulated_provider_verified', 'account_in_owner_name', 'broker_country_eligibility_verified', 'asset_permissions_verified',
    'funding_only_at_broker', 'market_data_terms_accepted', 'data_freshness_verified', 'read_only_access_verified',
  ],
  research: ['historical_data_tests_passed', 'paper_testing_passed', 'guardian_regression_passed'],
  live_review: [
    'reconciliation_tests_passed', 'independent_security_review_passed', 'written_live_approval', 'withdrawal_scope_absent',
    'credentials_in_secret_store', 'order_idempotency_verified', 'emergency_stop_verified',
  ],
};
export type PreflightGroup = keyof typeof PREFLIGHT_GROUPS;
export const PREFLIGHT_GROUP_NAMES: readonly PreflightGroup[] = ['data', 'research', 'live_review'];
export const BROKERS: readonly FinanceBrokerKind[] = ['alpaca', 'interactive_brokers', 'other'];
export const ASSETS: readonly FinanceBrokerAsset[] = ['stock', 'crypto'];

export interface PreflightDraft {
  broker: FinanceBrokerKind | '';
  country: string;
  assets: FinanceBrokerAsset[];
  facts: Partial<Record<FinancePreflightFact, boolean>>;
}

export const emptyPreflightDraft = (): PreflightDraft => ({ broker: '', country: '', assets: [], facts: {} });

/** The request body, or the first thing to fix. No broker chosen means no profile: the checklist alone. */
export function preflightRequest(draft: PreflightDraft): Checked<FinancePreflightInput> {
  const facts: Partial<Record<FinancePreflightFact, boolean>> = {};
  for (const group of PREFLIGHT_GROUP_NAMES) for (const fact of PREFLIGHT_GROUPS[group]) if (draft.facts[fact]) facts[fact] = true;
  // The server refuses read-only access before the broker itself is verified; say so before sending.
  if (facts.read_only_access_verified && !facts.regulated_provider_verified) return { ok: false, error: t('finance.preflight.error.order') };
  if (!draft.broker) return { ok: true, value: { facts, profile: null } };
  const country = draft.country.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(country)) return { ok: false, error: t('finance.preflight.error.country') };
  const assets = ASSETS.filter((asset) => draft.assets.includes(asset));
  if (!assets.length) return { ok: false, error: t('finance.preflight.error.assets') };
  return { ok: true, value: { facts, profile: { broker: draft.broker, country_of_residence: country, assets } } };
}

export const missingIn = (result: FinancePreflight, group: PreflightGroup): FinancePreflightFact[] =>
  group === 'data' ? result.missing_data_requirements : group === 'research' ? result.missing_research_requirements : result.missing_live_review_requirements;

export const missingCount = (result: FinancePreflight): number =>
  result.missing_data_requirements.length + result.missing_research_requirements.length + result.missing_live_review_requirements.length;

/** Only an https link from the server is shown as a link. */
export const safeSource = (source: string): string | null => (/^https:\/\/[^\s"<>]+$/.test(source) ? source : null);
