// Email Center phase 3 part A (docs/email.md#email-center): several mailboxes, shares, sender
// identities and contacts. The API's shapes and the pure helpers the Email page uses; no browser APIs
// at import time, so the unit tests run these directly.
import type { MailAccount } from './mail';

export type MailAccess = 'owner' | 'send' | 'read';
export type MailGrantAccess = 'read' | 'send';

export interface MailGrant { id: string; account_id: string; grantee_user_id: string; grantee_email: string | null; access: MailGrantAccess; created_at: string; updated_at: string }
export interface MailShareCandidate { user_id: string; email: string | null; role: string; grant_id: string | null; access: MailGrantAccess | null }
export interface MailIdentity { id: string; account_id: string; address: string; display_name: string | null; signature: string; is_default: boolean; created_at: string; updated_at: string }
export interface MailContact { id: string; name: string; email: string; company: string; phone: string; notes: string; created_at: string; updated_at: string }
export interface MailContactPage { items: MailContact[]; total: number; next_cursor: string | null }
export interface MailContactInput { name: string; email: string; company: string; phone: string; notes: string }

/** What the caller may do with a mailbox (older API answers without the field are the caller's own). */
export function accessOf(account: Pick<MailAccount, 'access'>): MailAccess {
  return account.access ?? 'owner';
}
export const ownsMailbox = (account: Pick<MailAccount, 'access'>): boolean => accessOf(account) === 'owner';
export const canSendFrom = (account: Pick<MailAccount, 'access'>): boolean => accessOf(account) !== 'read';

/** The mailbox the page opens: the remembered one if it is still there, else the first (own mailboxes come first). */
export function pickAccount<T extends { id: string }>(accounts: T[], remembered: string | null): T | null {
  return accounts.find((a) => a.id === remembered) ?? accounts[0] ?? null;
}

/** "Name <address>", or the address alone. */
export function senderLabel(name: string | null | undefined, address: string): string {
  const clean = (name ?? '').trim();
  return clean ? `${clean} <${address}>` : address;
}

export function contactLabel(contact: Pick<MailContact, 'name' | 'email'>): string {
  return senderLabel(contact.name, contact.email);
}

/** The recipient being typed at `caret` in a field like "a@x.com, Sar": its range and its text. */
export function recipientToken(text: string, caret: number): { start: number; end: number; query: string } {
  const at = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, at);
  const start = Math.max(before.lastIndexOf(','), before.lastIndexOf(';'), before.lastIndexOf('\n')) + 1;
  const rest = text.slice(at);
  const next = rest.search(/[,;\n]/);
  const end = next === -1 ? text.length : at + next;
  return { start, end, query: text.slice(start, end).trim() };
}

/** Put `address` in place of the token being typed and leave the caret after ", ". */
export function insertRecipient(text: string, token: { start: number; end: number }, address: string): { text: string; caret: number } {
  const head = text.slice(0, token.start).replace(/\s+$/, '');
  const tail = text.slice(token.end).replace(/^[\s,;]+/, '');
  const lead = head ? `${head} ` : '';
  const value = `${lead}${address}, `;
  return { text: value + tail, caret: value.length };
}

/** Addresses already typed in a recipients field (lower case), to leave them out of suggestions. */
export function typedAddresses(text: string): Set<string> {
  const found = new Set<string>();
  for (const raw of text.split(/[,;\n]/)) {
    const part = raw.trim();
    const inner = /<([^<>]+)>\s*$/.exec(part)?.[1]?.trim() ?? part;
    if (inner.includes('@')) found.add(inner.toLowerCase());
  }
  return found;
}

/** Suggestions to show: contacts not already in the field, at most `limit`. */
export function suggestionsFor(contacts: MailContact[], typed: Set<string>, limit = 6): MailContact[] {
  return contacts.filter((c) => !typed.has(c.email.toLowerCase())).slice(0, limit);
}

/** Only search the address book once a person has typed at least this much. */
export const SUGGEST_MIN_CHARS = 2;

/** Check a contact form before it goes to the API; the API checks again. */
export function contactProblem(input: MailContactInput): 'email' | 'phone' | null {
  if (!/^[^@\s<>,;"()]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/.test(input.email.trim())) return 'email';
  if (!/^[0-9+()./ -]{0,60}$/.test(input.phone.trim())) return 'phone';
  return null;
}

export const EMPTY_CONTACT: MailContactInput = { name: '', email: '', company: '', phone: '', notes: '' };

export function contactInput(contact: MailContact): MailContactInput {
  return { name: contact.name, email: contact.email, company: contact.company, phone: contact.phone, notes: contact.notes };
}

/** Merge a next page into a list, never showing a contact twice. */
export function appendContacts(current: MailContact[], next: MailContact[]): MailContact[] {
  const seen = new Set(current.map((c) => c.id));
  return [...current, ...next.filter((c) => !seen.has(c.id))];
}

/** Who a share or candidate is, for people: their email if known, else their user id. */
export function personName(entry: { email?: string | null; grantee_email?: string | null; grantee_user_id?: string; user_id?: string }): string {
  return entry.email || entry.grantee_email || entry.grantee_user_id || entry.user_id || '';
}
