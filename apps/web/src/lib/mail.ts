// Email Center (docs/email.md#email-center): the API's shapes under /api/v1/mail and the pure helpers
// the Email page uses. No browser APIs at import time, so the unit tests run these directly.
import type { ApiApproval } from './api';

export type MailAccountStatus = 'connected' | 'auth_failed' | 'offline' | 'error';
export type MailFolderRole = 'inbox' | 'drafts' | 'sent' | 'archive' | 'junk' | 'trash';
export type MailDraftKind = 'new' | 'reply' | 'reply_all' | 'forward';
export type MailDraftStatus = 'draft' | 'pending_approval' | 'sending' | 'sent' | 'failed' | 'unknown';

export interface MailAccount { id: string; address: string; display_name: string | null; status: MailAccountStatus; last_error: string | null; last_checked_at: string | null; created_at: string }
export interface MailSetup {
  enabled: boolean; configured: boolean; tenant_allowed: boolean; imap_host: string | null; imap_port: number; smtp_host: string | null; smtp_port: number;
  smtp_security: string; allowed_domains: string[]; can_read: boolean; can_act: boolean; max_recipients: number; send_per_hour: number; accounts: MailAccount[];
}
export interface MailFolder { id: string; name: string; role: MailFolderRole | null; total: number; unread: number }
export interface MailPerson { name: string; address: string }
export interface MailMessage {
  uid: number; folder: string; subject: string; sender: MailPerson | null; to: MailPerson[]; cc: MailPerson[]; date: string; message_id: string | null;
  seen: boolean; flagged: boolean; answered: boolean; size: number; maybe_attachments: boolean;
}
export interface MailPage { items: MailMessage[]; total: number; next_before: number | null }
export interface MailAttachment { index: number; filename: string; content_type: string; size: number; downloadable: boolean }
export interface MailDetail {
  message: MailMessage; reply_to: MailPerson[]; text: string; text_truncated: boolean; html: string | null;
  remote_images_blocked: number; inline_images_hidden: number; attachments: MailAttachment[];
}
export interface MailDraft {
  id: string; account_id: string; kind: MailDraftKind; to: string[]; cc: string[]; bcc: string[]; subject: string; body: string; in_reply_to: string | null;
  message_id: string; status: MailDraftStatus; error: string | null; approval: ApiApproval | null; approval_note: string | null; saved_to_sent: boolean | null;
  sent_at: string | null; created_at: string; updated_at: string;
}
export interface MailSendResult { draft: MailDraft; approval: ApiApproval | null }

/** Special folders first, in the order people expect; other folders by name. */
const ROLE_ORDER: Record<MailFolderRole, number> = { inbox: 0, drafts: 1, sent: 2, archive: 3, junk: 4, trash: 5 };
export function sortFolders(folders: MailFolder[]): MailFolder[] {
  return [...folders].sort((a, b) => (a.role ? ROLE_ORDER[a.role] : 9) - (b.role ? ROLE_ORDER[b.role] : 9) || a.name.localeCompare(b.name));
}

/** The folder the page opens first: the inbox, else the first folder. */
export function defaultFolder(folders: MailFolder[]): MailFolder | null {
  return folders.find((f) => f.role === 'inbox') ?? folders[0] ?? null;
}

/** Who a list row names: the sender, or (in Sent and Drafts) the first recipient. */
export function listPerson(message: MailMessage, role: MailFolderRole | null): MailPerson | null {
  if ((role === 'sent' || role === 'drafts') && message.to.length) return message.to[0];
  return message.sender;
}

export function personLabel(person: MailPerson | null): string {
  if (!person) return '';
  return person.name || person.address;
}

const EMAIL = /^[^@\s<>,;"()]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;
export const MAX_RECIPIENTS_FIELD = 100;

/** Recipients typed as "a@x.com, Name <b@y.com>; c@z.com": the addresses, or the first bad entry. */
export function parseRecipients(text: string): { ok: true; value: string[] } | { ok: false; bad: string } {
  const value: string[] = [];
  for (const raw of text.split(/[,;\n]/)) {
    const part = raw.trim();
    if (!part) continue;
    const inner = /<([^<>]+)>\s*$/.exec(part)?.[1]?.trim() ?? part;
    if (!EMAIL.test(inner)) return { ok: false, bad: part };
    if (!value.some((v) => v.toLowerCase() === inner.toLowerCase())) value.push(inner);
  }
  return { ok: true, value };
}

export function joinRecipients(values: string[]): string { return values.join(', '); }

/** Drafts the person can still change (a pending approval is withdrawn by an edit). */
export function isEditable(draft: MailDraft): boolean {
  return draft.status === 'draft' || draft.status === 'failed' || draft.status === 'pending_approval';
}

/** A fresh idempotency key for one send request (letters, digits and dashes, 8 to 120 characters). */
export function newSendKey(random: () => string = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`)): string {
  return `send-${random()}`.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 120);
}

/**
 * The document the reader's sandboxed iframe shows: the server's sanitized HTML under a policy that
 * loads nothing (no scripts, no remote images, no fonts), links opening in a new tab.
 */
export function readerDocument(html: string, dir: 'ltr' | 'rtl' = 'ltr'): string {
  const csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'";
  return `<!doctype html><html dir="auto"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><base target="_blank"><style>body{margin:0;padding:4px 2px;font:15px/1.5 system-ui,sans-serif;color:#1d1d1f;background:#fff;overflow-wrap:anywhere}img{max-width:100%;height:auto}table{max-width:100%}pre{white-space:pre-wrap}</style></head><body data-dir="${dir}">${html}</body></html>`;
}

/** Human size: 900 B, 12 KB, 3.4 MB. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
