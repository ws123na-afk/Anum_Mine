// Unified inbox (docs/email.md): the API's shapes and the pure helpers the Inbox view uses.
// No browser APIs at import time, so `pnpm --filter @anum/web test:unit` runs these directly.
import { t, type MessageKey } from './i18n.ts';

export type EmailProvider = 'gmail' | 'outlook' | 'zoho';
export type Category = 'order' | 'customer_question' | 'supplier' | 'invoice' | 'spam_marketing' | 'personal' | 'other';
export type Priority = 'high' | 'normal' | 'low';
export type AccountStatus = 'connected' | 'error' | 'reauth_required';
export type DraftStatus = 'draft' | 'pending_approval' | 'sent' | 'failed' | 'discarded';

export const PROVIDERS: EmailProvider[] = ['gmail', 'outlook', 'zoho'];
export const CATEGORIES: Category[] = ['customer_question', 'order', 'supplier', 'invoice', 'personal', 'spam_marketing', 'other'];
export const PRIORITIES: Priority[] = ['high', 'normal', 'low'];
export const PAGE_SIZE = 25;
export const MAX_DRAFT_CHARS = 20_000;
export const MAX_INSTRUCTION_CHARS = 1_000;
/** sessionStorage key holding a connection the provider just sent the browser back with. */
export const CONNECT_STORAGE_KEY = 'anum.email.connect';

export interface ProviderSetup { provider: EmailProvider; name: string; configured: boolean; enabled_here: boolean; missing: string[]; scopes: string[] }
export interface EmailSetup { providers: ProviderSetup[]; redirect_uri: string; can_connect: boolean; can_act: boolean }
export interface EmailAccount {
  id: string; provider: EmailProvider; provider_name: string; address: string; display_name: string | null; connected_by: string;
  visibility: 'workspace' | 'private'; status: AccountStatus; scopes: string[]; last_sync_at: string | null; last_sync_status: 'ok' | 'error' | null;
  last_error: string | null; syncing: boolean; message_count: number; unread_count: number; created_at: string; can_manage: boolean;
}
export interface EmailMessage {
  id: string; account_id: string; account_address: string; provider: EmailProvider; thread_id: string; subject: string;
  from_address: string; from_name: string | null; to: string[]; cc: string[]; snippet: string; labels: string[]; received_at: string;
  is_read: boolean; is_archived: boolean; has_attachments: boolean; category: Category | null; priority: Priority | null;
  summary: string | null; triage_source: 'rules' | 'model' | null; triage_model: string | null;
}
export interface MessagePage { items: EmailMessage[]; next_cursor: string | null; accounts: number }
/** `GET /api/v1/email/summary`: unread counts across the mailboxes the caller can see. */
export interface InboxSummary { accounts: number; unread: number; high_priority_unread: number; by_category: Partial<Record<Category, number>>; needs_attention: EmailMessage[] }
export interface ApiApprovalLike { id: string; payload_hash?: string | null; status: string; expires_at?: string | null; arguments?: Record<string, unknown>; [key: string]: unknown }
export interface EmailDraft {
  id: string; account_id: string; message_id: string | null; to: string[]; cc: string[]; subject: string; body: string; status: DraftStatus;
  source: 'model' | 'person'; model_provider: string | null; model_name: string | null; saved_in_provider: boolean; error: string | null;
  approval: ApiApprovalLike | null; approval_note: string | null; created_by: string; created_at: string; updated_at: string; sent_at: string | null;
}
export interface Attachment { id: string; filename: string; content_type: string; size: number }
export interface MessageDetail { message: EmailMessage; thread: EmailMessage[]; body: string | null; body_truncated: boolean; body_error: string | null; attachments: Attachment[]; drafts: EmailDraft[] }
export interface ActionResult { status: 'done' | 'waiting_approval' | 'failed'; summary: string; approval: ApiApprovalLike | null; message: EmailMessage | null }
export interface DraftCreateResult { status: 'done' | 'waiting_approval' | 'failed'; summary: string; draft: EmailDraft | null; approval: ApiApprovalLike | null; warning: string | null }
export interface AnalyzeResult { message: EmailMessage; suggested_reply: string; model: string }

export interface Filters { accountId: string; category: Category | ''; priority: Priority | ''; unread: boolean; archived: boolean; query: string }
export const EMPTY_FILTERS: Filters = { accountId: '', category: '', priority: '', unread: false, archived: false, query: '' };

export function hasFilters(filters: Filters): boolean {
  return Boolean(filters.accountId || filters.category || filters.priority || filters.unread || filters.archived || filters.query.trim());
}

/** Query string for GET /api/v1/email/messages. */
export function messagesQuery(filters: Filters, before?: string | null, limit = PAGE_SIZE): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (filters.accountId) params.set('account_id', filters.accountId);
  if (filters.category) params.set('category', filters.category);
  if (filters.priority) params.set('priority', filters.priority);
  if (filters.unread) params.set('unread', 'true');
  if (filters.archived) params.set('archived', 'true');
  const q = filters.query.trim().replace(/\s+/g, ' ');
  if (q) params.set('q', q.slice(0, 200));
  if (before) params.set('before', before);
  return params.toString();
}

export function categoryLabel(category: Category | null): string {
  return t(`email.category.${category ?? 'other'}` as MessageKey);
}
export function priorityLabel(priority: Priority | null): string {
  return t(`email.priority.${priority ?? 'normal'}` as MessageKey);
}
export function priorityTone(priority: Priority | null): 'dangerTone' | 'neutral' {
  return priority === 'high' ? 'dangerTone' : 'neutral';
}

export function accountStatus(account: EmailAccount): { tone: 'success' | 'warning' | 'dangerTone'; label: string; detail: string | null } {
  if (account.status === 'reauth_required') return { tone: 'dangerTone', label: t('email.account.status.reauth'), detail: account.last_error ?? t('email.account.reauthHelp') };
  if (account.status === 'error' || account.last_sync_status === 'error') return { tone: 'warning', label: t('email.account.status.error'), detail: account.last_error };
  return { tone: 'success', label: t('email.account.status.connected'), detail: null };
}

/** The person the message is from, as shown in lists. */
export function senderName(message: Pick<EmailMessage, 'from_name' | 'from_address'>): string {
  return message.from_name?.trim() || message.from_address || t('email.unknownSender');
}

/** Split a typed recipient list ("a@x.com, b@y.com; c@z.com") and report anything that is not an address. */
export function parseRecipients(text: string): { ok: true; value: string[] } | { ok: false; error: string } {
  const parts = text.split(/[,;\s]+/).map((part) => part.trim().replace(/^<|>$/g, '')).filter(Boolean);
  const out: string[] = [];
  for (const part of parts) {
    if (!/^[^@\s<>,;"]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/.test(part)) return { ok: false, error: t('email.draft.badAddress', { value: part.slice(0, 80) }) };
    const lower = part.toLowerCase();
    if (!out.includes(lower)) out.push(lower);
  }
  if (out.length > 50) return { ok: false, error: t('email.draft.tooManyRecipients') };
  return { ok: true, value: out };
}

/** Who wrote the draft, for the provenance label next to it. */
export function draftProvenance(draft: Pick<EmailDraft, 'source' | 'model_provider' | 'model_name'>): string {
  const model = [draft.model_provider, draft.model_name].filter(Boolean).join('/');
  if (draft.source === 'model') return t('email.draft.byModel', { model: model || t('email.draft.unknownModel') });
  return model ? t('email.draft.editedModel', { model }) : t('email.draft.byPerson');
}

export function draftStatusLabel(status: DraftStatus): string {
  return t(`email.draft.status.${status}` as MessageKey);
}

/** A human size: 512 B, 12 KB, 3.4 MB. */
export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The provider sends the browser back through the API to `/?email_connect=<id>#inbox` (or
 * `email_connect_error=<code>`). Read those parameters and the URL to show without them.
 */
export function readConnectReturn(href: string): { connection: string | null; error: string | null; cleanUrl: string } | null {
  const url = new URL(href);
  const connection = url.searchParams.get('email_connect');
  const error = url.searchParams.get('email_connect_error');
  if (!connection && !error) return null;
  url.searchParams.delete('email_connect');
  url.searchParams.delete('email_connect_error');
  const search = url.searchParams.toString();
  return { connection, error, cleanUrl: `${url.pathname}${search ? `?${search}` : ''}${url.hash || '#inbox'}` };
}

export function connectErrorText(code: string): string {
  return code === 'invalid_state' ? t('email.connect.errorState') : code === 'unknown_request' ? t('email.connect.errorUsed') : t('email.connect.errorGeneric', { code: code.slice(0, 60) });
}

/** "Synced 5 min ago" / "Never synced". */
export function syncedLabel(lastSyncAt: string | null, now: Date = new Date()): string {
  if (!lastSyncAt) return t('email.account.neverSynced');
  const minutes = Math.max(0, Math.round((now.getTime() - Date.parse(lastSyncAt)) / 60_000));
  if (minutes < 1) return t('email.account.syncedNow');
  if (minutes < 60) return t('email.account.syncedMinutes', { count: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 48) return t('email.account.syncedHours', { count: hours });
  return t('email.account.syncedDays', { count: Math.round(hours / 24) });
}

/** An account is worth syncing when the page opens if it has not synced in the last two minutes. */
export function isStale(account: EmailAccount, now: Date = new Date()): boolean {
  if (account.status === 'reauth_required' || account.syncing) return false;
  return !account.last_sync_at || now.getTime() - Date.parse(account.last_sync_at) > 2 * 60_000;
}
