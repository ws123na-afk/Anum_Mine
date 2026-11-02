// Consent records for campaigns (docs/privacy.md#consent-records-for-campaigns): pure helpers for the
// Consent records panel on Contact opt-outs. The API checks the same rules.
import { t, type MessageKey } from './i18n.ts';

export type ConsentChannel = 'email' | 'whatsapp' | 'sms' | 'all';
export type LawfulBasis = 'consent' | 'contract' | 'legitimate_interest' | 'existing_customer';

export interface ConsentRecord {
  id: string;
  masked: string;
  kind: 'phone' | 'email';
  channel: ConsentChannel;
  lawful_basis: LawfulBasis;
  source: string;
  consented_at: string;
  note: string | null;
  status: 'active' | 'withdrawn';
  withdrawn_at: string | null;
  withdrawn_reason: string | null;
  withdrawn_by: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ConsentSettings { require_for_marketing: boolean; updated_by: string | null; updated_at: string | null }

export interface ConsentPage { items: ConsentRecord[]; total: number; active: number; withdrawn: number; settings: ConsentSettings; can_manage: boolean }

export const CONSENT_CHANNELS: readonly ConsentChannel[] = ['whatsapp', 'email', 'sms', 'all'];
export const LAWFUL_BASES: readonly LawfulBasis[] = ['consent', 'contract', 'legitimate_interest', 'existing_customer'];
export const CONSENT_PAGE = 20;

export interface ConsentDraft { recipient: string; channel: ConsentChannel; lawful_basis: LawfulBasis; source: string; consented_on: string; note: string }

export const emptyConsentDraft = (today: string): ConsentDraft => ({ recipient: '', channel: 'whatsapp', lawful_basis: 'consent', source: '', consented_on: today, note: '' });

export const basisLabel = (basis: LawfulBasis): string => t(`consent.basis.${basis}` as MessageKey);
export const consentChannelLabel = (channel: ConsentChannel): string => t(`consent.channel.${channel}` as MessageKey);

/** The query string of one page (search, state and paging). */
export function consentsPath(q: string, state: 'active' | 'withdrawn' | '', offset: number, limit = CONSENT_PAGE): string {
  const params = new URLSearchParams({ limit: String(limit), offset: String(Math.max(0, offset)) });
  if (q.trim()) params.set('q', q.trim().slice(0, 320));
  if (state) params.set('state', state);
  return `/api/v1/contact-consents?${params.toString()}`;
}

/** What is wrong with a draft before it is sent, or the request body. ``today`` is YYYY-MM-DD. */
export function consentBody(draft: ConsentDraft, today: string): { ok: true; body: Record<string, unknown> } | { ok: false; error: MessageKey } {
  const recipient = draft.recipient.trim();
  if (recipient.length < 3) return { ok: false, error: 'consent.err.contact' };
  const email = recipient.includes('@');
  if ((email && (draft.channel === 'whatsapp' || draft.channel === 'sms')) || (!email && draft.channel === 'email')) return { ok: false, error: 'consent.err.channel' };
  const source = draft.source.split(/\s+/).filter(Boolean).join(' ');
  if (source.length < 3 || source.length > 200) return { ok: false, error: 'consent.err.source' };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.consented_on) || draft.consented_on > today) return { ok: false, error: 'consent.err.date' };
  const note = draft.note.trim();
  if (note.length > 300) return { ok: false, error: 'consent.err.note' };
  return { ok: true, body: { recipient, channel: draft.channel, lawful_basis: draft.lawful_basis, source, consented_at: `${draft.consented_on}T12:00:00Z`, note: note || null } };
}

export function withdrawReason(text: string): string | null {
  const value = text.split(/\s+/).filter(Boolean).join(' ');
  return value.length >= 3 && value.length <= 300 ? value : null;
}
