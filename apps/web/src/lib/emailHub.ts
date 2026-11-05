// One Email module (docs/email.md#one-email-module): the pure model of the Email page that sits on
// both mail back ends. The provider inbox (Gmail, Outlook, Zoho; /api/v1/email) and the Email Center
// on the workspace's own mail server (/api/v1/mail) stay as they are; the page reads one mailbox list
// and one "All inboxes" from /api/v1/email/unified and routes every action to the back end that owns
// the mailbox. Pure, so the unit tests cover it without a browser.
import type { AllInboxMessage } from './mailAllInboxes.ts';

export type MailSource = 'gmail' | 'outlook' | 'zoho' | 'mailserver';
export type MailBackend = 'provider' | 'server';
export type BackendState = 'ok' | 'none' | 'off' | 'not_configured' | 'not_allowed' | 'error';

export interface UnifiedMailbox {
  ref: string; id: string; backend: MailBackend; source: MailSource; address: string; display_name: string | null;
  status: string; access: string; unread: number | null; can_send: boolean;
}
export interface BackendInfo { state: BackendState; detail: string | null }
export interface UnifiedMailboxes { items: UnifiedMailbox[]; provider: BackendInfo; server: BackendInfo; can_act: boolean; can_manage: boolean }
export interface UnifiedMessage {
  ref: string; backend: MailBackend; source: MailSource; mailbox_id: string; mailbox_address: string;
  message_id: string | null; folder: string | null; uid: number | null; from_name: string | null; from_address: string | null;
  subject: string; snippet: string; received_at: string; unread: boolean; flagged: boolean; has_attachments: boolean; read_only: boolean;
  server_message: AllInboxMessage | null;
}
export interface UnreadableMailbox { ref: string; address: string; state: string; reason: string | null }
export interface UnifiedPage { items: UnifiedMessage[]; next_cursor: string | null; provider: BackendInfo; server: BackendInfo; unreadable: UnreadableMailbox[] }

/**
 * What the Email page shows: All inboxes, one mailbox (routed to its back end), the provider inbox as
 * a whole (the old Inbox page, `#inbox`), the own server's mailboxes as a whole (the old Email Center),
 * or Connections (every connection, one settings page).
 */
export type HubPane =
  | { kind: 'all' }
  | { kind: 'mailbox'; ref: string }
  | { kind: 'provider' }
  | { kind: 'server' }
  | { kind: 'connections' };

const REF = /^(provider|server):[\w.@+-]{1,80}$/;

/** The pane a link opens; null means "pick the landing pane once the mailboxes are known". */
export function paneFromHash(hash: string): HubPane | null {
  const id = hash.replace(/^#/, '');
  if (id === 'inbox') return { kind: 'provider' };
  if (id === 'email/all') return { kind: 'all' };
  if (id === 'email/connections') return { kind: 'connections' };
  if (id === 'email/server') return { kind: 'server' };
  if (id === 'email/provider') return { kind: 'provider' };
  if (id.startsWith('email/mailbox/')) {
    let ref = '';
    try { ref = decodeURIComponent(id.slice('email/mailbox/'.length)); } catch { return null; }
    return REF.test(ref) ? { kind: 'mailbox', ref } : null;
  }
  return null;
}

export function paneHash(pane: HubPane): string {
  switch (pane.kind) {
    case 'all': return '#email/all';
    case 'connections': return '#email/connections';
    case 'server': return '#email/server';
    case 'provider': return '#email/provider';
    case 'mailbox': return `#email/mailbox/${encodeURIComponent(pane.ref)}`;
  }
}

/**
 * Where `#email` lands: All inboxes once there are two mailboxes or more (over either back end); the
 * one mailbox when there is exactly one; with none, the Email Center's own connect form when the own
 * server is on and the person may connect there, else Connections (how to connect either). When the
 * list could not be read, the own-server view (it explains its own state) keeps the page usable.
 */
export function landingPane(found: UnifiedMailboxes | null): HubPane {
  if (!found) return { kind: 'server' };
  if (found.items.length >= 2) return { kind: 'all' };
  if (found.items.length === 1) return { kind: 'mailbox', ref: found.items[0]!.ref };
  if (found.server.state === 'ok' && found.can_act) return { kind: 'server' };
  return { kind: 'connections' };
}

export function findMailbox(found: UnifiedMailboxes | null, ref: string): UnifiedMailbox | null {
  return found?.items.find((item) => item.ref === ref) ?? null;
}

/** Addresses a person may write from, provider mailboxes first. */
export function senders(found: UnifiedMailboxes | null): UnifiedMailbox[] {
  return (found?.items ?? []).filter((item) => item.can_send);
}

export function totalUnread(found: UnifiedMailboxes | null): number {
  return (found?.items ?? []).reduce((sum, item) => sum + (item.unread ?? 0), 0);
}

/** Load more appends a page; a message already shown (by ref) is never listed twice. */
export function appendUnique(current: UnifiedMessage[], more: UnifiedMessage[]): UnifiedMessage[] {
  const seen = new Set(current.map((item) => item.ref));
  return [...current, ...more.filter((item) => !seen.has(item.ref))];
}

export function unifiedQuery(opts: { q?: string; unread?: boolean; cursor?: string | null; limit?: number }): string {
  const params = new URLSearchParams();
  if (opts.q?.trim()) params.set('q', opts.q.trim());
  if (opts.unread) params.set('unread', 'true');
  if (opts.cursor) params.set('cursor', opts.cursor);
  params.set('limit', String(opts.limit ?? 20));
  return params.toString();
}

/** The message key of the source badge (Gmail, Outlook, Zoho Mail, Own server). */
export function sourceKey(source: MailSource): 'emailHub.source.gmail' | 'emailHub.source.outlook' | 'emailHub.source.zoho' | 'emailHub.source.mailserver' {
  return `emailHub.source.${source}` as const;
}

/** A mailbox needs its owner when its provider grant or saved password stopped working. */
export function needsAttention(mailbox: UnifiedMailbox): boolean {
  return !['connected', 'ok'].includes(mailbox.status);
}

/** Both back ends say why they show nothing; the page names each so the owner knows what to set up. */
export function backendNotes(found: UnifiedMailboxes | null): { backend: MailBackend; info: BackendInfo }[] {
  if (!found) return [];
  return ([['provider', found.provider], ['server', found.server]] as const)
    .filter(([, info]) => info.state !== 'ok' && info.state !== 'none')
    .map(([backend, info]) => ({ backend, info }));
}
