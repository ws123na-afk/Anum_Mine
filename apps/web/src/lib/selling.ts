// Ready to sell (docs/selling-readiness.md): the company details and legal facts, the Moyasar adapter,
// refunds and the platform owner's checklist. Pure types and helpers only, so `pnpm test:unit` runs them.

import type { LegalDocId } from './publicSite';

export type HostingProvider = '' | 'oracle' | 'render' | 'other';
export const COMPANY_FIELDS = [
  'legal_name', 'trading_name', 'registration_number', 'registration_country', 'registration_authority', 'address', 'contact_email',
  'support_contact', 'phone', 'vat_number', 'dpo_name', 'dpo_email', 'security_email', 'server_location', 'hosting_provider', 'governing_law',
] as const;
export type CompanyField = (typeof COMPANY_FIELDS)[number];
export type Company = Record<CompanyField, string>;
export const EMAIL_FIELDS: readonly CompanyField[] = ['contact_email', 'dpo_email', 'security_email'];

export interface LegalTerm { key: string; label: string; help: string; generated: boolean }
export interface LegalProfile {
  company: Company;
  legal_terms: Record<string, string>;
  published_docs: LegalDocId[];
  terms: LegalTerm[];
  values: Record<string, string | null>;
  missing: Record<LegalDocId, string[]>;
  generated: { key: string; value: string }[];
  required_company: CompanyField[];
  updated_by: string | null;
  updated_at: string | null;
  public_issuer: boolean | null;
}
export type LegalBody = Company & { legal_terms: Record<string, string>; published_docs: LegalDocId[]; lawyer_reviewed: boolean };

export interface PublicLegal {
  configured: boolean;
  company: Omit<Company, 'hosting_provider'>;
  values: Record<string, string | null>;
  published: LegalDocId[];
  missing: Record<LegalDocId, string[]>;
}

export interface MoyasarSettings { enabled: boolean; has_secret_key: boolean; has_webhook_secret: boolean; test_mode: boolean | null; webhook_url: string; events: string[] }
export interface MoyasarBody { enabled: boolean; secret_key: string | null; webhook_secret: string | null }

export type ReadinessKey = 'payment_provider' | 'company_details' | 'mail_sending' | 'backups_local' | 'backups_offsite' | 'legal_pages' | 'plans' | 'public_issuer';
export type ReadinessState = 'done' | 'warning' | 'missing' | 'unknown';
export type FixTarget = 'payments' | 'company' | 'mail' | 'backups' | 'legal' | 'plans' | 'env';
export interface ReadinessItem { key: ReadinessKey; state: ReadinessState; fix: FixTarget; detail: Record<string, unknown> }
export interface Readiness { items: ReadinessItem[]; done: number; total: number; ready: boolean; mail_domain: string | null; checked_at: string }

export type SpfStatus = 'ok' | 'missing' | 'multiple' | 'no_senders' | 'too_open';
export interface DnsResult {
  domain: string; checked_at: string;
  spf: { record: string | null; status: SpfStatus; senders: string[]; all: string | null; error: string | null };
  dmarc: { record: string | null; policy: string | null; status: 'ok' | 'monitor' | 'missing'; error: string | null };
}

/** Where a checklist item is fixed: a console tab, or a page of the signed-in app. */
export function fixTarget(fix: FixTarget): { tab?: 'settings' | 'company' | 'plans'; href?: string } {
  switch (fix) {
    case 'payments': return { tab: 'settings' };
    case 'company': case 'legal': return { tab: 'company' };
    case 'plans': return { tab: 'plans' };
    case 'mail': return { href: '#email' };
    default: return {};
  }
}

/** A checklist item counts as finished only when it is done; a warning still needs the owner. */
export const isDone = (item: ReadinessItem): boolean => item.state === 'done';

/** Order: what blocks selling first (missing), then warnings and unknowns, then done. */
export function sortReadiness(items: ReadinessItem[]): ReadinessItem[] {
  const rank: Record<ReadinessState, number> = { missing: 0, warning: 1, unknown: 2, done: 3 };
  return [...items].sort((a, b) => rank[a.state] - rank[b.state]);
}

/** A domain the server will accept for the SPF lookup (letters, digits, hyphens, at least one dot). */
export function validDomain(value: string): boolean {
  const v = value.trim().toLowerCase().replace(/\.$/, '');
  return v.length >= 3 && v.length <= 253 && /^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(v);
}

/** A plain email address check, the same shape the API accepts (one @, a dot in the host, no spaces). */
export function validEmail(value: string): boolean {
  const v = value.trim();
  if (!v || v.length > 254 || (v.match(/@/g) ?? []).length !== 1 || /[\s,;<>]/.test(v)) return false;
  const [local, host] = v.split('@');
  const labels = host.split('.');
  return Boolean(local) && labels.length >= 2 && labels.every(Boolean);
}

/** Company fields the checklist needs that are still empty. */
export function missingCompany(company: Partial<Company>, required: readonly CompanyField[]): CompanyField[] {
  return required.filter((key) => !String(company[key] ?? '').trim());
}

/** A fresh key so a retried refund is never sent twice (the API remembers it per issuer). */
export function newIdempotencyKey(random: () => string = () => crypto.randomUUID()): string {
  return `refund-${random()}`;
}

/** What a payment still allows to refund (the API's own figure when present). */
/** Card payments ANUM refunds through the provider's API (Moyasar or Stripe); anything else is recorded by hand. */
export function refundsThroughProvider(provider: string | null | undefined): provider is 'moyasar' | 'stripe' {
  return provider === 'moyasar' || provider === 'stripe';
}

/** The message key naming how a provider refund goes back to the card. */
export function providerRefundLabel(provider: 'moyasar' | 'stripe'): 'billing.refund.viaMoyasar' | 'billing.refund.viaStripe' {
  return provider === 'stripe' ? 'billing.refund.viaStripe' : 'billing.refund.viaMoyasar';
}

export function refundableOf(payment: { amount_minor: number; refundable_minor?: number }): number {
  return Math.max(0, payment.refundable_minor ?? payment.amount_minor);
}

/** The placeholder keys of a legal document that are still empty. */
export function missingKeys(keys: string[], values: Record<string, string | null> | null | undefined): string[] {
  return keys.filter((key) => !values?.[key]);
}

/** Counts of facts not set yet, per document, for the console. */
export function missingTotal(missing: Partial<Record<LegalDocId, string[]>>): number {
  return Object.values(missing).reduce((sum, keys) => sum + (keys?.length ?? 0), 0);
}
