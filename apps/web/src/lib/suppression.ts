// The contact suppression (opt-out) list: wire types, the add form's checks and plain words
// (docs/privacy.md#contact-suppression). Pure, so `pnpm --filter @anum/web test:unit` covers it.
import { t, type MessageKey } from './i18n.ts';

export type SuppressionChannel = 'all' | 'whatsapp' | 'email' | 'sms';
export type SuppressionPurpose = 'all' | 'marketing';
export type SuppressionSource = 'manual' | 'whatsapp_keyword' | 'email_unsubscribe';

export const CHANNELS: readonly SuppressionChannel[] = ['all', 'whatsapp', 'email', 'sms'];
export const PURPOSES: readonly SuppressionPurpose[] = ['all', 'marketing'];
export const PAGE_SIZE = 50;
export const SYSTEM_ACTOR = 'system:inbound';

export interface SuppressionEntry {
  id: string;
  masked: string;
  kind: 'phone' | 'email';
  channel: SuppressionChannel;
  purpose: SuppressionPurpose;
  source: SuppressionSource;
  reason: string;
  created_by: string;
  created_at: string;
}

export interface SuppressionPage { items: SuppressionEntry[]; total: number; can_manage: boolean; can_export: boolean }

export interface SuppressionDraft { recipient: string; channel: SuppressionChannel; purpose: SuppressionPurpose; reason: string }

export const emptySuppressionDraft = (): SuppressionDraft => ({ recipient: '', channel: 'all', purpose: 'all', reason: '' });

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

/** What kind of contact the person typed, by the same rules as the API (or null when it is neither). */
export function contactKind(value: string): 'phone' | 'email' | null {
  const text = value.trim().replace(/^<|>$/g, '');
  if (text.includes('@')) return /^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$/.test(text) ? 'email' : null;
  const digits = text.replace(/\D/g, '');
  return digits.length >= 8 && digits.length <= 15 ? 'phone' : null;
}

/** The `POST /contact-suppressions` body, or the first problem in plain words. */
export function suppressionBody(draft: SuppressionDraft): Result<SuppressionDraft> {
  const recipient = draft.recipient.trim();
  const kind = contactKind(recipient);
  if (!kind) return { ok: false, error: t('supp.err.contact') };
  if ((kind === 'email' && (draft.channel === 'whatsapp' || draft.channel === 'sms')) || (kind === 'phone' && draft.channel === 'email')) {
    return { ok: false, error: t('supp.err.channel') };
  }
  const reason = draft.reason.trim();
  if (reason.length < 3 || reason.length > 300) return { ok: false, error: t('supp.err.reason') };
  return { ok: true, value: { recipient, channel: draft.channel, purpose: draft.purpose, reason } };
}

/** A removal needs a reason (kept in the audit log). */
export function removalReason(reason: string): Result<string> {
  const text = reason.trim();
  return text.length >= 3 && text.length <= 300 ? { ok: true, value: text } : { ok: false, error: t('supp.err.reason') };
}

export const channelLabel = (channel: string): string => t(`supp.channel.${CHANNELS.includes(channel as SuppressionChannel) ? channel : 'all'}` as MessageKey);
export const purposeLabel = (purpose: string): string => t(purpose === 'marketing' ? 'supp.purpose.marketing' : 'supp.purpose.all');
export const sourceLabel = (source: string): string =>
  ['manual', 'whatsapp_keyword', 'email_unsubscribe'].includes(source) ? t(`supp.source.${source}` as MessageKey) : source;
/** Who added an entry, in words: a person's id, or "Added automatically" for a STOP message. */
export const addedBy = (entry: Pick<SuppressionEntry, 'created_by'>): string =>
  entry.created_by === SYSTEM_ACTOR ? t('supp.bySystem') : t('supp.by', { who: entry.created_by });

/** The filename from Content-Disposition, or a safe default. */
export function exportName(disposition: string | null): string {
  const name = disposition?.match(/filename="?([^";]+)"?/i)?.[1]?.trim();
  return name && /^[\w.@-]+$/.test(name) ? name : 'contact-suppressions.csv';
}
