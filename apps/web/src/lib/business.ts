// Business, the internal sales desk (docs/business.md): labels, tones, score and money formatting,
// form validation and the proposal totals preview. Pure, so `pnpm --filter @anum/web test:unit`
// covers it without a browser. The API is the authority: it recomputes and rechecks everything.
import type {
  BusinessCurrency, BusinessLead, ConsentBasis, Criterion, DraftChannel, LeadState, MilestoneStatus, OpportunityStage, PriceType, ProductKind,
  ProductMaturity, ProjectStatus, ProposalContent, ProposalStatus,
} from '@anum/contracts';
import { formatNumber, getLocale, t, type MessageKey } from './i18n.ts';

export type BusinessTab = 'overview' | 'leads' | 'prospecting' | 'outreach' | 'pipeline' | 'proposals' | 'projects' | 'invoices' | 'support' | 'catalog' | 'settings';
export const TABS: readonly BusinessTab[] = ['overview', 'leads', 'prospecting', 'outreach', 'pipeline', 'proposals', 'projects', 'invoices', 'support', 'catalog', 'settings'];
export const PAGE_SIZE = 25;

export const LEAD_STATES: readonly LeadState[] = ['discovered', 'researched', 'qualified', 'contacted', 'replied', 'meeting', 'offered', 'won', 'lost', 'suppressed'];
export const STAGES: readonly OpportunityStage[] = ['discovery', 'qualified', 'demo', 'quote', 'negotiation', 'won', 'lost'];
export const CRITERIA: readonly Criterion[] = ['industry_fit', 'problem_evidence', 'reachable_channel', 'project_scale', 'urgency_confirmed', 'authorized_buyer'];
export const CONSENT_BASES: readonly ConsentBasis[] = ['unknown', 'consent_given', 'inbound_inquiry', 'existing_relationship', 'referral', 'public_business_channel'];
export const CHANNELS: readonly DraftChannel[] = ['email', 'whatsapp', 'linkedin', 'call_script'];
export const CURRENCIES: readonly BusinessCurrency[] = ['SAR', 'AED', 'USD'];
export const PRICE_TYPES: readonly PriceType[] = ['fixed', 'from', 'range', 'hourly', 'quote'];
export const MATURITIES: readonly ProductMaturity[] = ['idea', 'prototype', 'pilot', 'live', 'retired'];
export const KINDS: readonly ProductKind[] = ['service_package', 'product'];

/** Catalog list filters (docs/business.md#catalog): name search plus kind and maturity, paged by offset. */
export interface CatalogFilters { q: string; kind: ProductKind | ''; maturity: ProductMaturity | '' }
export const NO_CATALOG_FILTERS: CatalogFilters = { q: '', kind: '', maturity: '' };
export function catalogQuery(filters: CatalogFilters, offset: number, limit = PAGE_SIZE): { q?: string; kind?: string; maturity?: string; limit: number; offset: number } {
  const q = filters.q.trim();
  return { ...(q ? { q } : {}), ...(filters.kind ? { kind: filters.kind } : {}), ...(filters.maturity ? { maturity: filters.maturity } : {}), limit, offset: Math.max(0, offset) };
}
export const catalogFiltered = (filters: CatalogFilters) => Boolean(filters.q.trim() || filters.kind || filters.maturity);
export const PROJECT_STATUSES: readonly ProjectStatus[] = ['planned', 'active', 'on_hold', 'delivered', 'closed'];
export const MILESTONE_STATUSES: readonly MilestoneStatus[] = ['planned', 'in_progress', 'submitted', 'accepted', 'blocked'];

/** The tab a URL hash opens (`#business-leads`), or null when it is not a Business hash. */
export function tabFromHash(hash: string): BusinessTab | null {
  const id = hash.replace(/^#/, '');
  if (id === 'business') return 'overview';
  if (!id.startsWith('business-')) return null;
  const tab = id.slice('business-'.length).split('/')[0] as BusinessTab;
  return TABS.includes(tab) ? tab : null;
}

const label = (key: string) => t(key as MessageKey);
export const stateLabel = (state: LeadState) => label(`biz.state.${state}`);
export const stageLabel = (stage: OpportunityStage) => label(`biz.stage.${stage}`);
export const criterionLabel = (criterion: Criterion) => label(`biz.criterion.${criterion}`);
export const consentLabel = (basis: ConsentBasis) => label(`biz.consent.${basis}`);
export const channelLabel = (channel: DraftChannel) => label(`biz.channel.${channel}`);
export const priceTypeLabel = (type: PriceType) => label(`biz.priceType.${type}`);
export const maturityLabel = (value: ProductMaturity) => label(`biz.maturity.${value}`);
export const kindLabel = (value: ProductKind) => label(`biz.kind.${value}`);
export const proposalStatusLabel = (value: ProposalStatus) => label(`biz.proposalStatus.${value}`);
export const projectStatusLabel = (value: ProjectStatus) => label(`biz.projectStatus.${value}`);
export const milestoneStatusLabel = (value: MilestoneStatus) => label(`biz.milestoneStatus.${value}`);

export type Tone = 'neutral' | 'success' | 'warning' | 'dangerTone';

export function stateTone(state: LeadState): Tone {
  if (state === 'won') return 'success';
  if (state === 'lost' || state === 'suppressed') return 'dangerTone';
  if (state === 'qualified' || state === 'offered' || state === 'meeting' || state === 'replied') return 'warning';
  return 'neutral';
}

export function stageTone(stage: OpportunityStage): Tone {
  return stage === 'won' ? 'success' : stage === 'lost' ? 'dangerTone' : stage === 'negotiation' || stage === 'quote' ? 'warning' : 'neutral';
}

export function proposalTone(status: ProposalStatus): Tone {
  return status === 'accepted' ? 'success' : status === 'withdrawn' ? 'dangerTone' : status === 'pending_approval' ? 'warning' : 'neutral';
}

/** "7/30" and how many of the six criteria are known (unknown criteria are not zeros). */
export function scoreText(lead: Pick<BusinessLead, 'score_total' | 'score_known'>): { total: string; known: string } {
  return { total: t('biz.score.total', { total: lead.score_total }), known: t('biz.score.known', { known: lead.score_known }) };
}

/** Transitions that need a reason (lost, suppressed) and the one ANUM does itself (won). */
export const needsReason = (to: LeadState) => to === 'lost' || to === 'suppressed';
export const manualTransitions = (allowed: readonly LeadState[]) => allowed.filter((state) => state !== 'won');

export function formatMoney(amount: number | null | undefined, currency: BusinessCurrency): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return t('biz.unknown');
  const locale = getLocale() === 'ar' ? 'ar-u-nu-latn' : 'en';
  return new Intl.NumberFormat(locale, { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
}

export const formatPct = (value: number | null | undefined) => (value === null || value === undefined ? t('biz.unknown') : `${formatNumber(value)}%`);

/** One entry per non-empty line (deliverables, exclusions, criteria). */
export function linesOf(text: string): string[] {
  const seen: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const value = line.replace(/\s+/g, ' ').trim();
    if (value && !seen.includes(value)) seen.push(value);
  }
  return seen;
}

const cents = (value: number) => Math.round(value * 100);
const fromCents = (value: number) => value / 100;
/** Half-up rounding to cents, the way the API rounds with Decimal. */
const roundCents = (value: number) => Math.sign(value) * Math.round(Math.abs(value) + Number.EPSILON);

/** The totals the API will compute for a version (a preview while editing; integer cents, no float drift). */
export function proposalTotals(content: Pick<ProposalContent, 'line_items' | 'milestones' | 'tax_rate_pct' | 'internal_cost'>) {
  const subtotal = content.line_items.reduce((sum, item) => sum + roundCents(cents(item.quantity) * cents(item.unit_price) / 100), 0);
  const tax = roundCents(subtotal * content.tax_rate_pct / 100);
  const total = subtotal + tax;
  const milestones = content.milestones.reduce((sum, item) => sum + cents(item.amount), 0);
  return {
    subtotal: fromCents(subtotal), tax: fromCents(tax), total: fromCents(total), milestoneTotal: fromCents(milestones),
    margin: fromCents(subtotal - cents(content.internal_cost)), milestonesMatch: content.milestones.length === 0 || milestones === total,
  };
}

export type Check<T> = { ok: true; value: T } | { ok: false; error: string };

const URLISH = /^(https?:\/\/)?[a-z0-9.-]+\.[a-z]{2,}(\/\S*)?$/i;
const EMAILISH = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/;

export interface LeadDraft { title: string; company: string; website: string; industry: string; city: string; country: string; contactName: string; email: string; phone: string; consent: ConsentBasis; productId: string }
export const emptyLeadDraft = (): LeadDraft => ({ title: '', company: '', website: '', industry: '', city: '', country: '', contactName: '', email: '', phone: '', consent: 'unknown', productId: '' });

/** The POST /leads body for a lead at a new company, or what to fix. */
export function leadBody(draft: LeadDraft): Check<Record<string, unknown>> {
  const company = draft.company.trim();
  if (company.length < 2) return { ok: false, error: t('biz.form.companyRequired') };
  const website = draft.website.trim();
  if (website && !URLISH.test(website)) return { ok: false, error: t('biz.form.websiteInvalid') };
  const email = draft.email.trim();
  if (email && !EMAILISH.test(email)) return { ok: false, error: t('biz.form.emailInvalid') };
  const phone = draft.phone.trim();
  if (phone && phone.replace(/\D/g, '').length < 8) return { ok: false, error: t('biz.form.phoneInvalid') };
  const hasContact = Boolean(draft.contactName.trim() || email || phone);
  const title = draft.title.trim() || `${company}`;
  return {
    ok: true,
    value: {
      title: title.length >= 2 ? title : `${company} lead`,
      account: { name: company, website: website || null, industry: draft.industry.trim(), city: draft.city.trim(), country: draft.country.trim() },
      contact: hasContact ? { name: draft.contactName.trim() || company, email: email || null, phone: phone || null, consent_basis: draft.consent } : null,
      product_id: draft.productId || null,
    },
  };
}

export interface OfferDraft { currency: BusinessCurrency; priceType: PriceType; priceMin: string; priceMax: string; deliverables: string; limitations: string; laborHours: string; hourlyCost: string; externalCosts: string; contingency: string; notes: string }
export const emptyOfferDraft = (): OfferDraft => ({ currency: 'SAR', priceType: 'fixed', priceMin: '', priceMax: '', deliverables: '', limitations: '', laborHours: '0', hourlyCost: '0', externalCosts: '0', contingency: '0', notes: '' });

function amount(text: string): number | null {
  const value = Number(text.replace(/,/g, '').trim());
  return text.trim() !== '' && Number.isFinite(value) && value >= 0 && Math.abs(Math.round(value * 100) - value * 100) < 1e-6 ? value : null;
}

/** The body of a priced version, or what to fix (prices are the owner's, never suggested). */
export function offerBody(draft: OfferDraft): Check<Record<string, unknown>> {
  const min = amount(draft.priceMin), max = amount(draft.priceMax);
  if (draft.priceType !== 'quote' && min === null) return { ok: false, error: t('biz.form.priceRequired') };
  if (draft.priceType === 'range' && (max === null || (min !== null && max < min))) return { ok: false, error: t('biz.form.rangeInvalid') };
  const numbers = [draft.laborHours, draft.hourlyCost, draft.externalCosts, draft.contingency].map((value) => amount(value || '0'));
  if (numbers.some((value) => value === null) || (numbers[3] ?? 0) > 100) return { ok: false, error: t('biz.form.costInvalid') };
  return {
    ok: true,
    value: {
      currency: draft.currency, price_type: draft.priceType, price_min: draft.priceType === 'quote' ? null : min, price_max: draft.priceType === 'range' ? max : null,
      deliverables: linesOf(draft.deliverables), limitations: linesOf(draft.limitations), notes: draft.notes.trim(),
      cost: { labor_hours: numbers[0], hourly_cost: numbers[1], external_costs: numbers[2], contingency_pct: numbers[3] },
    },
  };
}

export { amount as parseAmount };
