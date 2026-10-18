// Customer billing (docs/billing.md): the shapes the billing API returns and the pure helpers the
// Billing settings page, the platform console, the pricing page and sign-up share. Money is always an
// integer number of minor units plus an ISO 4217 currency (SAR by default). No browser or Vite APIs,
// so `pnpm --filter @anum/web test:unit` runs it.
import { ApiError } from './errors.ts';
import { getLocale, intlLocale, type Locale, type MessageKey } from './i18n.ts';

// ---- shapes ---------------------------------------------------------------------------------------

export type Interval = 'month' | 'year';
export type SubscriptionStatus = 'trialing' | 'active' | 'past_due' | 'paused' | 'cancelled';
export type InvoiceStatus = 'draft' | 'open' | 'paid' | 'void' | 'uncollectible';
export type PaymentMethod = 'bank_transfer' | 'cash' | 'card' | 'other';
export type MeterKey = 'seats' | 'workspaces' | 'agent_runs' | 'storage' | 'messages';
export type LimitKey = 'seats' | 'workspaces' | 'agent_runs_per_month' | 'storage_gb' | 'messages_per_month';
export type Limits = Partial<Record<LimitKey, number | null>>;

export interface Plan {
  id: string; code: string; name: string; description: string; price_minor: number; currency: string; interval: Interval;
  trial_days: number; per_seat: boolean; limits: Limits; features: string[]; current?: boolean;
}
export interface AdminPlan extends Plan { published: boolean; active: boolean; sort_order: number; created_at?: string; updated_at?: string }

export interface Account {
  display_name: string | null; contact_name: string | null; contact_email: string | null; company_name: string | null;
  address_line1: string | null; address_line2: string | null; city: string | null; postal_code: string | null; country: string | null;
  vat_number: string | null; credit_balance_minor: number; state: 'active' | 'read_only';
}
export type AccountBody = Record<'contact_name' | 'contact_email' | 'company_name' | 'address_line1' | 'address_line2' | 'city' | 'postal_code' | 'country' | 'vat_number', string>;

export interface Subscription {
  id: string; plan_id: string; plan_code: string; plan_name: string; price_minor: number; currency: string; interval: Interval; per_seat: boolean;
  limits: Limits; features: string[]; seats: number; status: SubscriptionStatus; trial_end: string | null; current_period_start: string | null;
  current_period_end: string | null; cancel_at_period_end: boolean; cancelled_at: string | null; pending_plan_id: string | null;
  pending_seats?: number | null; paused_at: string | null; past_due_since: string | null; grace_until: string | null;
}
export interface Meter { key: MeterKey; used: number; limit: number | null; unit: string }
export interface BankDetails { enabled: boolean; bank_name: string; account_name: string; iban: string; swift: string }
export interface Overview {
  account: Account | null; subscription: Subscription | null; usage: { period: string; meters: Meter[] }; state: 'active' | 'read_only';
  open_balance_minor: number; overdue: boolean; currency: string; payment: { bank_transfer: BankDetails; card_enabled: boolean };
  issuer: { name: string; email?: string };
}
export interface Preview { effective: 'now' | 'period_end'; amount_minor: number; tax_minor: number; total_minor: number; credit_minor: number; blocked_by: Blocked[]; currency?: string }
export interface Blocked { key: string; used: number; limit: number }

export interface InvoiceSummary {
  id: string; number: string | null; status: InvoiceStatus; kind: string; currency: string; period_start: string | null; period_end: string | null;
  issued_at: string | null; due_at: string | null; subtotal_minor: number; tax_rate_bp: number; tax_country: string | null; tax_minor: number;
  total_minor: number; amount_paid_minor: number; amount_credited_minor: number; amount_due_minor: number; overdue: boolean;
  reminders_sent?: number; last_email?: { status?: string; approval_id?: string | null; at?: string } | null;
}
export interface InvoiceLine { id: string; description: string; quantity: number; unit_minor: number; amount_minor: number; kind: string; period_start: string | null; period_end: string | null }
export interface Payment { id: string; amount_minor: number; currency: string; method: string; reference: string; received_at: string; has_proof: boolean; proof_name: string | null; provider: string | null; recorded_by: string | null }
export interface CreditNote { id: string; number: string; reason: string; subtotal_minor: number; tax_minor: number; total_minor: number; issued_at: string }
export interface Invoice extends InvoiceSummary {
  bill_to: Record<string, string>; issuer: Record<string, string>; bank_details: BankDetails; lines: InvoiceLine[]; payments: Payment[]; credit_notes: CreditNote[];
}
export interface Page<T> { items: T[]; total: number; limit?: number; offset?: number }

export interface OnboardingItem { key: 'invite_team' | 'connect_email' | 'choose_plan' | 'add_payment_details'; done: boolean }
export interface Onboarding { items: OnboardingItem[]; complete: boolean; dismissed: boolean }

export interface PublicCatalog { signup_enabled: boolean; currency: string; tax: { country: string; rate_bp: number }; plans: Plan[] }
export type SignupState = 'verify_email' | 'ready' | 'provisioned';
export interface SignupStatus { email: string | null; email_verified: boolean; signup_enabled?: boolean; state: SignupState; tenant_id?: string; workspace_id?: string | null; identity_linked?: boolean }
export interface SignupBody { organization_name: string; plan_id: string; country: string; accept_terms: boolean }
export interface SignupResult { tenant_id: string; workspace_id: string; trial_end: string | null; identity_linked: boolean; plan_id?: string }

export interface ConsoleSummary {
  currency: string; mrr_minor: number; arr_minor: number; counts: Record<SubscriptionStatus, number>; organisations?: number; read_only: number;
  open_invoices: number; open_minor: number; overdue_minor: number; collected_this_month_minor: number;
}
export interface ConsoleSubscription {
  tenant_id: string; display_name: string; plan_name: string; status: SubscriptionStatus; price_minor: number; currency: string; interval: Interval;
  mrr_minor: number; current_period_end: string | null; open_balance_minor: number; state: 'active' | 'read_only';
}
export interface ConsoleOrganisation { tenant_id: string; account: Account | null; subscription: Subscription | null; usage: { period: string; meters: Meter[] } }
export interface IssuerSettings {
  issuer_name: string; issuer_address: string; issuer_vat_number: string; issuer_email: string; bank_name: string; bank_account_name: string;
  bank_iban: string; bank_swift: string; invoice_prefix: string; credit_note_prefix: string; payment_terms_days: number; tax_rules: Record<string, number>;
  default_tax_bp: number; reminder_days: number[]; grace_days: number; sending_workspace_id: string | null; stripe_enabled: boolean;
  has_stripe_secret_key: boolean; has_stripe_webhook_secret: boolean; stripe_test_mode: boolean | null;
}
export type SettingsBody = Omit<IssuerSettings, 'has_stripe_secret_key' | 'has_stripe_webhook_secret' | 'stripe_test_mode'> & { stripe_secret_key: string | null; stripe_webhook_secret: string | null };
export interface PaymentBody { amount_minor: number; method: PaymentMethod; reference: string; received_at?: string; proof?: { name: string; content_type: ProofType; data_base64: string } }
export type ProofType = 'application/pdf' | 'image/png' | 'image/jpeg';
export interface EmailOutcome { status: string; approval_id: string | null; payload_hash: string | null; task_id?: string | null; message: string }
export interface RunResult { organisations: number; invoices: number; reminders: number; read_only: number; failed: number }

export const SUBSCRIPTION_STATUSES: readonly SubscriptionStatus[] = ['trialing', 'active', 'past_due', 'paused', 'cancelled'];
export const INVOICE_STATUSES: readonly InvoiceStatus[] = ['draft', 'open', 'paid', 'void', 'uncollectible'];
export const PAYMENT_METHODS: readonly PaymentMethod[] = ['bank_transfer', 'cash', 'card', 'other'];
export const LIMIT_KEYS: readonly LimitKey[] = ['seats', 'workspaces', 'agent_runs_per_month', 'storage_gb', 'messages_per_month'];
export const PROOF_TYPES: readonly ProofType[] = ['application/pdf', 'image/png', 'image/jpeg'];
export const PROOF_MAX_BYTES = 5 * 1024 * 1024;
export const PAGE_SIZE = 20;
/** Countries offered on the sign-up and billing contact forms (two-letter codes the API accepts); Saudi Arabia first. */
export const COUNTRIES: readonly string[] = ['SA', 'AE', 'BH', 'KW', 'OM', 'QA', 'EG', 'JO', 'PK', 'IN', 'GB', 'US', 'DE', 'FR'];

// ---- money -----------------------------------------------------------------------------------------

/** How many minor units a currency has (2 for SAR and USD, 0 for JPY, 3 for KWD). */
export function minorDigits(currency: string): number {
  try { return new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2; } catch { return 2; }
}

/** An integer amount of minor units as money in the locale ("SAR 1,500.00", Arabic with Western digits). */
export function formatMoney(minor: number | null | undefined, currency = 'SAR', locale: Locale = getLocale()): string {
  if (minor === null || minor === undefined || !Number.isFinite(minor)) return '-';
  const code = /^[A-Z]{3}$/.test(currency) ? currency : 'SAR';
  const digits = minorDigits(code);
  try {
    return new Intl.NumberFormat(intlLocale(locale), { style: 'currency', currency: code, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(minor / 10 ** digits);
  } catch {
    return `${code} ${(minor / 10 ** digits).toFixed(digits)}`;
  }
}

/** A typed amount ("1500", "1,500.5") as minor units; null when it is not a positive amount. */
export function parseMoney(text: string, currency = 'SAR'): number | null {
  const clean = text.replace(/[\s,]/g, '');
  if (!/^\d+(\.\d+)?$/.test(clean)) return null;
  const digits = minorDigits(currency);
  const [whole, fraction = ''] = clean.split('.');
  if (fraction.length > digits) return null;
  const value = Number(whole) * 10 ** digits + Number((fraction + '0'.repeat(digits)).slice(0, digits) || '0');
  return Number.isSafeInteger(value) ? value : null;
}

/** Minor units as the plain number a person edits ("1500.00"). */
export function minorToInput(minor: number, currency = 'SAR'): string {
  const digits = minorDigits(currency);
  return (minor / 10 ** digits).toFixed(digits);
}

/** Basis points as a percent ("15%", "7.5%"). */
export function formatRate(bp: number, locale: Locale = getLocale()): string {
  return new Intl.NumberFormat(intlLocale(locale), { style: 'percent', maximumFractionDigits: 2 }).format((bp || 0) / 10000);
}
/** A typed percent ("15", "7.5") as basis points; null when outside 0 to 100. */
export function percentToBp(text: string): number | null {
  const value = Number(text.trim().replace(',', '.'));
  if (!text.trim() || !Number.isFinite(value) || value < 0 || value > 100) return null;
  return Math.round(value * 100);
}
export const bpToPercent = (bp: number): string => String(Math.round(bp) / 100);

// ---- usage -------------------------------------------------------------------------------------------

/** How full a meter is, 0 to 100 (rounded down); null for an unlimited meter. */
export function usagePercent(used: number, limit: number | null | undefined): number | null {
  if (limit === null || limit === undefined) return null;
  if (limit <= 0) return used > 0 ? 100 : 0;
  return Math.min(100, Math.max(0, Math.floor((used / limit) * 100)));
}
/** The bar's tone: over 90% red, over 75% amber. */
export function usageTone(percent: number | null): 'ok' | 'warn' | 'danger' {
  if (percent === null) return 'ok';
  return percent >= 90 ? 'danger' : percent >= 75 ? 'warn' : 'ok';
}

/** Bytes as "1.5 GB" (storage meters count bytes; the storage limit is in GB). */
export function formatBytes(bytes: number, locale: Locale = getLocale()): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Math.max(0, bytes), unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${new Intl.NumberFormat(intlLocale(locale), { maximumFractionDigits: unit ? 1 : 0 }).format(value)} ${units[unit]}`;
}

/** A meter's used and limit values as text (storage in bytes, the rest as counts). */
export function meterValues(meter: Pick<Meter, 'key' | 'used' | 'limit'>, locale: Locale = getLocale()): { used: string; limit: string | null } {
  const count = (n: number) => new Intl.NumberFormat(intlLocale(locale)).format(n);
  if (meter.key === 'storage') return { used: formatBytes(meter.used, locale), limit: meter.limit === null ? null : formatBytes(meter.limit, locale) };
  return { used: count(meter.used), limit: meter.limit === null ? null : count(meter.limit) };
}

/** The message key naming a meter or a plan limit. */
export function meterKey(key: string): MessageKey {
  const known: Record<string, MessageKey> = {
    seats: 'billing.meter.seats', workspaces: 'billing.meter.workspaces', agent_runs: 'billing.meter.agent_runs', agent_runs_per_month: 'billing.meter.agent_runs',
    storage: 'billing.meter.storage', storage_gb: 'billing.meter.storage', messages: 'billing.meter.messages', messages_per_month: 'billing.meter.messages',
  };
  return known[key] ?? 'billing.meter.other';
}

/** A plan limit as text for plan cards ("10 seats" style values are composed by the caller). */
export function limitValue(key: LimitKey, value: number | null | undefined, locale: Locale = getLocale()): string | null {
  if (value === null || value === undefined) return null;
  const n = new Intl.NumberFormat(intlLocale(locale)).format(value);
  return key === 'storage_gb' ? `${n} GB` : n;
}

// ---- refusals ------------------------------------------------------------------------------------------

/**
 * The limits a change is blocked by: the preview's `blocked_by`, or a 409 refusal's `error.details`
 * entries of type `blocked_by` (`location` is the key, `message` reads "used of limit").
 */
export function parseBlockedBy(source: unknown): Blocked[] {
  if (Array.isArray(source)) {
    return source.filter(isRecord).filter((item) => typeof item.key === 'string')
      .map((item) => ({ key: item.key as string, used: Number(item.used) || 0, limit: Number(item.limit) || 0 }));
  }
  const details = isRecord(source) && isRecord(source.error) && Array.isArray(source.error.details) ? source.error.details : null;
  if (!details) return [];
  return details.filter(isRecord).filter((d) => d.type === 'blocked_by' && typeof d.location === 'string').map((d) => {
    const match = typeof d.message === 'string' ? /(\d+)\D+(\d+)/.exec(d.message) : null;
    return { key: d.location as string, used: match ? Number(match[1]) : 0, limit: match ? Number(match[2]) : 0 };
  });
}

/** The limits a refused change (409) names, from the ApiError the shared request threw. */
export function blockedFromError(error: unknown): Blocked[] {
  if (!(error instanceof ApiError) || error.status !== 409) return [];
  return error.details.filter((d) => d.location && /^\s*\d+\s+of\s+\d+\s*$/.test(d.message)).map((d) => {
    const [used, limit] = d.message.trim().split(/\s+of\s+/).map(Number);
    return { key: d.location as string, used, limit };
  });
}

/** The two billing refusals (HTTP 402) that point to Settings > Billing rather than model budgets. */
export const BILLING_CODES = ['plan_limit_reached', 'billing_read_only'] as const;
export function isBillingRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && (BILLING_CODES as readonly string[]).includes(String(error.code));
}

// ---- labels and tones ------------------------------------------------------------------------------------

export function subscriptionTone(status: SubscriptionStatus | string): string {
  return status === 'active' ? 'success' : status === 'trialing' ? 'neutral' : status === 'past_due' ? 'dangerTone' : 'warning';
}
export function invoiceTone(invoice: Pick<InvoiceSummary, 'status' | 'overdue'>): string {
  if (invoice.status === 'paid') return 'success';
  if (invoice.status === 'open') return invoice.overdue ? 'dangerTone' : 'warning';
  return 'neutral';
}
export const intervalKey = (interval: Interval | string): MessageKey => interval === 'year' ? 'billing.perYear' : 'billing.perMonth';

// ---- paths ------------------------------------------------------------------------------------------------

export interface InvoiceFilter { q?: string; status?: InvoiceStatus | ''; limit?: number; offset?: number }
/** `.../invoices?q=&status=&limit=&offset=` under a base (the organisation's, or the console's for one organisation). */
export function invoicesPath(base: string, filter: InvoiceFilter = {}): string {
  const params = new URLSearchParams({ limit: String(filter.limit ?? PAGE_SIZE), offset: String(filter.offset ?? 0) });
  if (filter.q?.trim()) params.set('q', filter.q.trim());
  if (filter.status) params.set('status', filter.status);
  return `${base}/invoices?${params.toString()}`;
}
export interface SubscriptionFilter { q?: string; status?: string; limit?: number; offset?: number }
export function subscriptionsPath(filter: SubscriptionFilter = {}): string {
  const params = new URLSearchParams({ limit: String(filter.limit ?? PAGE_SIZE), offset: String(filter.offset ?? 0) });
  if (filter.q?.trim()) params.set('q', filter.q.trim());
  if (filter.status) params.set('status', filter.status);
  return `/api/v1/platform/billing/subscriptions?${params.toString()}`;
}
export const previewPath = (planId: string): string => `/api/v1/billing/subscription/preview?plan_id=${encodeURIComponent(planId)}`;
export const consoleOrgPath = (tenantId: string): string => `/api/v1/platform/billing/organizations/${encodeURIComponent(tenantId)}`;

/** "1 to 20 of 45" for a page. */
export function pageRange(total: number, offset: number, shown: number): { from: number; to: number; hasPrev: boolean; hasNext: boolean } {
  return { from: shown ? offset + 1 : 0, to: offset + shown, hasPrev: offset > 0, hasNext: offset + shown < total };
}

/** A country's name in the page language ("Saudi Arabia"), the code when the runtime has no name. */
export function countryName(code: string, locale: Locale = getLocale()): string {
  try { return new Intl.DisplayNames([locale], { type: 'region' }).of(code) ?? code; } catch { return code; }
}

// ---- sign-up -------------------------------------------------------------------------------------------------

/** The plan named in `#signup?plan=<id>` (or `#pricing?plan=`), else null. */
export function planFromHash(hash: string): string | null {
  const query = hash.split('?')[1] ?? '';
  const plan = new URLSearchParams(query).get('plan');
  return plan && /^[A-Za-z0-9_-]{1,80}$/.test(plan) ? plan : null;
}
/** Which public screen a hash opens outside the signed-in app. */
export function publicRoute(hash: string): 'pricing' | 'signup' | null {
  const id = hash.replace(/^#/, '').split('?')[0];
  return id === 'pricing' ? 'pricing' : id === 'signup' ? 'signup' : null;
}
/** The sign-up form's own checks, mirroring the API (2 to 160 characters, no links or angle brackets). */
export function signupProblems(body: SignupBody): MessageKey[] {
  const problems: MessageKey[] = [];
  const name = body.organization_name.trim().replace(/\s+/g, ' ');
  if (name.length < 2 || name.length > 160 || !/[\p{L}\p{N}]/u.test(name)) problems.push('signup.problem.name');
  else if (/https?:\/\/|www\.|<|>/i.test(name)) problems.push('signup.problem.link');
  if (!body.plan_id) problems.push('signup.problem.plan');
  if (!/^[A-Za-z]{2}$/.test(body.country)) problems.push('signup.problem.country');
  if (!body.accept_terms) problems.push('signup.problem.terms');
  return problems;
}

/** The checklist item's message key and the place it links to. */
export const ONBOARDING_LINKS: Record<OnboardingItem['key'], { label: MessageKey; hash: string }> = {
  invite_team: { label: 'billing.onboarding.invite_team', hash: '#members' },
  connect_email: { label: 'billing.onboarding.connect_email', hash: '#email' },
  choose_plan: { label: 'billing.onboarding.choose_plan', hash: '#billing-plans' },
  add_payment_details: { label: 'billing.onboarding.add_payment_details', hash: '#billing-contact' },
};

/** The proof file's own checks before it is read: PDF, PNG or JPEG, 1 byte to 5 MB. */
export function proofProblem(file: { size: number; type: string }): MessageKey | null {
  if (!(PROOF_TYPES as readonly string[]).includes(file.type)) return 'billing.console.proofType';
  if (file.size <= 0 || file.size > PROOF_MAX_BYTES) return 'billing.console.proofSize';
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
