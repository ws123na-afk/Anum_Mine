// All inboxes (docs/email.md#all-inboxes): one list over the inbox of every mailbox the person may
// read. The shapes of GET /api/v1/mail/all-inboxes and the pure helpers the view uses; no browser APIs
// at import time, so the unit tests run these directly.
import type { MailAccount, MailFolder, MailMessage } from './mail';

export type MailAccess = 'owner' | 'send' | 'read';
export type AllInboxState = 'ok' | 'auth_failed' | 'offline' | 'error' | 'done';

export interface AllInboxMessage extends MailMessage {
  account_id: string; account_address: string; account_name: string | null; access: MailAccess;
}
export interface AllInboxMailbox {
  account_id: string; address: string; display_name: string | null; access: MailAccess; state: AllInboxState; error: string | null; total: number | null;
}
export interface AllInboxPage { items: AllInboxMessage[]; total: number; next_cursor: string | null; mailboxes: AllInboxMailbox[] }

/** Search, the Unread and Starred chips, one mailbox ('' for all) and a received-date range (YYYY-MM-DD, inclusive). */
export interface AllInboxFilters { q: string; unread: boolean; flagged: boolean; accountId: string; since: string; until: string }
export const NO_ALL_FILTERS: AllInboxFilters = { q: '', unread: false, flagged: false, accountId: '', since: '', until: '' };

/** The value the mailbox picker uses for this view. */
export const ALL_INBOXES = '__all__';
export const ALL_PAGE = 25;

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The query string the API takes: only what is set, so an empty filter is never sent. */
export function allInboxesQuery(filters: AllInboxFilters, cursor: string | null = null, limit = ALL_PAGE): string {
  const query = new URLSearchParams({ limit: String(limit) });
  const q = filters.q.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
  if (q) query.set('q', q);
  if (filters.unread) query.set('unread', 'true');
  if (filters.flagged) query.set('flagged', 'true');
  if (filters.accountId) query.set('account_id', filters.accountId);
  if (DAY.test(filters.since)) query.set('since', filters.since);
  if (DAY.test(filters.until)) query.set('until', filters.until);
  if (cursor) query.set('cursor', cursor);
  return query.toString();
}

export function allFiltersActive(filters: AllInboxFilters): boolean {
  return Boolean(filters.q.trim() || filters.unread || filters.flagged || filters.accountId || filters.since || filters.until);
}

/** A date range the API would refuse: the start after the end (checked before anything is asked). */
export function badDateRange(filters: Pick<AllInboxFilters, 'since' | 'until'>): boolean {
  return Boolean(DAY.test(filters.since) && DAY.test(filters.until) && filters.since > filters.until);
}

/** One message across mailboxes: the same UID can exist in two inboxes. */
export const messageKey = (m: Pick<AllInboxMessage, 'account_id' | 'uid'>): string => `${m.account_id}:${m.uid}`;

/** The next page after the current one, without repeating a message already shown. */
export function appendPage(current: AllInboxMessage[], next: AllInboxMessage[]): AllInboxMessage[] {
  const seen = new Set(current.map(messageKey));
  return [...current, ...next.filter((m) => !seen.has(messageKey(m)))];
}

/** The mailbox badge on a row: its name when it has one, else the address. */
export function mailboxBadge(m: Pick<AllInboxMessage, 'account_name' | 'account_address'>): string {
  return m.account_name?.trim() || m.account_address;
}

/** Mailboxes that could not be read for this page (shown with their reason and a retry). */
export function unreadable(page: Pick<AllInboxPage, 'mailboxes'>): AllInboxMailbox[] {
  return page.mailboxes.filter((m) => m.state !== 'ok' && m.state !== 'done');
}

/** The inbox of a mailbox as the reader and composer take it (every IMAP server names it INBOX). */
export function inboxFolder(): MailFolder {
  return { id: 'INBOX', name: 'Inbox', role: 'inbox', total: 0, unread: 0 };
}

/** The mailbox a message belongs to, from the setup (null when it was removed meanwhile). */
export function accountOf(accounts: MailAccount[], m: Pick<AllInboxMessage, 'account_id'>): MailAccount | null {
  return accounts.find((a) => a.id === m.account_id) ?? null;
}

/** Change one message's flag in the list after the API agreed. */
export function setFlag(items: AllInboxMessage[], key: string, flag: 'seen' | 'flagged', value: boolean): AllInboxMessage[] {
  return items.map((m) => messageKey(m) === key ? { ...m, [flag]: value } : m);
}
