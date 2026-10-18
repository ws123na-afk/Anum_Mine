// Brevo email campaigns (docs/brevo.md): types and pure helpers for the Email campaigns tab of
// Marketing. No browser or Vite APIs, so `pnpm test:unit` exercises them directly. The server is the
// authority on every rule (sender, lists, approval); these helpers only shape what the page shows.
import { t, type MessageKey } from './i18n.ts';

export type ConnectionStatus = 'connected' | 'needs_key' | 'error';
export type DraftStatus = 'draft' | 'pending_approval' | 'sending' | 'scheduled' | 'sent' | 'failed' | 'unknown';
export const DRAFT_STATUSES: DraftStatus[] = ['draft', 'pending_approval', 'sending', 'scheduled', 'sent', 'failed', 'unknown'];
export const CAMPAIGN_STATUSES = ['draft', 'scheduled', 'queued', 'in_process', 'sent', 'suspended', 'archive'] as const;
export type BrevoCampaignStatus = (typeof CAMPAIGN_STATUSES)[number] | 'unknown';

export interface Connection {
  status: ConnectionStatus; key_last4: string; account_email: string; account_name: string; company: string; plan: string;
  last_checked_at: string | null; last_error: string | null; connected_by: string; updated_at: string;
}
export interface Status { connected: boolean; connection: Connection | null; can_read: boolean; can_write: boolean; can_manage: boolean; host: string }
export interface ContactList { id: number; name: string; subscribers: number; blacklisted: number; folder_id: number | null }
export interface Sender { id: number; name: string; email: string; active: boolean }
export interface Stats {
  sent: number; delivered: number; unique_views: number; unique_clicks: number; unsubscriptions: number;
  hard_bounces: number; soft_bounces: number; complaints: number; open_rate: number | null; click_rate: number | null;
}
export interface Campaign {
  id: number; name: string; subject: string; status: BrevoCampaignStatus; sender_name: string; sender_email: string; list_ids: number[];
  scheduled_at: string | null; sent_at: string | null; created_at: string | null; tag: string; stats: Stats;
  list_stats: (Partial<Stats> & { list_id: number })[];
}
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface SendError { code: string; message: string; fix: string }
export interface Draft {
  id: string; name: string; subject: string; preview_text: string; sender_id: number; sender_name: string; sender_email: string;
  list_ids: number[]; list_names: string[]; status: DraftStatus; scheduled_at: string | null; content_hash: string; approved_hash: string | null;
  brevo_campaign_id: number | null; last_error: SendError | null; test_sent_at: string | null; sent_at: string | null; source: 'person' | 'assistant';
  created_by: string; created_at: string; updated_at: string; html_chars: number;
  approval: Record<string, unknown> | null; approval_state: string; html: string | null; preview_html: string | null; remote_images: number;
}
export interface DraftBody { name: string; subject: string; preview_text: string; sender_id: number; html: string; list_ids: number[] }

export const MAX_HTML_CHARS = 512 * 1024;
export const MAX_LISTS = 10;
/** A Brevo v3 API key starts with "xkeysib-"; an SMTP key ("xsmtpsib-") does not work for the API. */
export function keyProblem(value: string): MessageKey | null {
  const key = value.trim();
  if (!key) return 'brevo.key.empty';
  if (key.startsWith('xsmtpsib-')) return 'brevo.key.smtp';
  if (!/^xkeysib-[A-Za-z0-9-]{16,200}$/.test(key)) return 'brevo.key.shape';
  return null;
}

/** What is wrong with a draft before it goes to the server (the server checks again). */
export function draftProblems(body: DraftBody): MessageKey[] {
  const problems: MessageKey[] = [];
  if (!body.name.trim()) problems.push('brevo.problem.name');
  if (!body.subject.trim()) problems.push('brevo.problem.subject');
  if (!body.sender_id) problems.push('brevo.problem.sender');
  if (body.list_ids.length === 0) problems.push('brevo.problem.lists');
  if (body.list_ids.length > MAX_LISTS) problems.push('brevo.problem.tooManyLists');
  if (!body.html.trim()) problems.push('brevo.problem.html');
  if (body.html.length > MAX_HTML_CHARS) problems.push('brevo.problem.htmlTooLong');
  return problems;
}

export function statusTone(status: DraftStatus | BrevoCampaignStatus | ConnectionStatus): string {
  switch (status) {
    case 'sent': case 'connected': return 'success';
    case 'failed': case 'needs_key': case 'error': case 'unknown': case 'suspended': return 'danger';
    case 'pending_approval': case 'sending': case 'scheduled': case 'queued': case 'in_process': return 'warning';
    default: return 'neutral';
  }
}
export const draftStatusName = (status: DraftStatus): string => t(`brevo.status.${status}` as MessageKey);
export function campaignStatusName(status: BrevoCampaignStatus): string {
  return (CAMPAIGN_STATUSES as readonly string[]).includes(status) ? t(`brevo.campaignStatus.${status}` as MessageKey) : t('brevo.campaignStatus.unknown');
}

export interface DraftActions { edit: boolean; test: boolean; ask: boolean; withdraw: boolean; remove: boolean }
/** Which buttons a draft offers; the server refuses anything else anyway. */
export function draftActions(draft: Pick<Draft, 'status'>, perms: { can_write: boolean; can_manage: boolean }, connected: boolean): DraftActions {
  const open = draft.status === 'draft' || draft.status === 'failed';
  return {
    edit: perms.can_write && (open || draft.status === 'pending_approval'),
    test: perms.can_manage && connected && (open || draft.status === 'pending_approval'),
    ask: perms.can_write && connected && open,
    withdraw: perms.can_write && draft.status === 'pending_approval',
    remove: perms.can_write && draft.status !== 'sending' && draft.status !== 'scheduled' && draft.status !== 'unknown',
  };
}

/** A rate as "48.5%" or "-" when nothing was delivered yet. */
export const percent = (value: number | null | undefined): string => (value === null || value === undefined ? '-' : `${Number(value.toFixed(1))}%`);

/** The value for <input type="datetime-local"> from an ISO time, in the browser's zone. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
/** A datetime-local value as an ISO time in UTC, or null when empty or invalid. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** "21 to 40 of 75" paging facts for offset pages. */
export function pageRange(page: Pick<Page<unknown>, 'total' | 'limit' | 'offset'>, shown: number): { from: number; to: number; hasPrev: boolean; hasNext: boolean } {
  const from = shown === 0 ? 0 : page.offset + 1;
  const to = page.offset + shown;
  return { from, to, hasPrev: page.offset > 0, hasNext: to < page.total };
}

export function draftsPath(filter: { query?: string; status?: DraftStatus | ''; limit?: number; offset?: number }): string {
  const params = new URLSearchParams({ limit: String(filter.limit ?? 20), offset: String(filter.offset ?? 0) });
  if (filter.query?.trim()) params.set('q', filter.query.trim());
  if (filter.status) params.set('status', filter.status);
  return `/api/v1/brevo/drafts?${params.toString()}`;
}
