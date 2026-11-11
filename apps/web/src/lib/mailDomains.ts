// Mail domains and aliases on the Mailcow server (docs/email.md#mail-domains): types of
// /api/v1/platform/mail/domains and the pure helpers the page uses.
import type { ApiApproval } from './api';

export type DnsStatus = 'ok' | 'warning' | 'danger' | 'missing' | 'error';
export type DomainOperation = 'add_domain' | 'remove_domain' | 'add_alias' | 'remove_alias' | 'add_mailbox';

export interface MailAlias { id: string; address: string; goto: string[]; active: boolean }
export interface MailMailbox { address: string; name: string; active: boolean; quota_mb: number | null; used_percent: number | null }

export interface MailDomain {
  domain: string;
  in_mailcow: boolean;
  active: boolean;
  allowed: boolean;
  mailboxes: number;
  max_mailboxes: number | null;
  quota_used_percent: number | null;
  aliases: MailAlias[];
  mailbox_list: MailMailbox[];
}

export interface PendingDomainChange {
  approval: ApiApproval;
  operation: DomainOperation;
  domain: string;
  address: string | null;
  goto: string[];
  name?: string | null;
  quota_mb?: number | null;
}

export interface MailDomainsView {
  mailcow_state: 'off' | 'misconfigured' | 'unreachable' | 'refused' | 'ok';
  message: string | null;
  can_change: boolean;
  change_note: string | null;
  mail_host: string | null;
  allowed_domains: string[];
  domains: MailDomain[];
  pending: PendingDomainChange[];
  // False when Mailcow's mailbox list could not be read: no mailbox can be added then.
  mailboxes_read: boolean;
}

export interface DnsCheck { status: DnsStatus; code: string; params: Record<string, string>; found: string[]; message: string }
export interface ExpectedRecord { kind: 'mx' | 'spf' | 'dkim' | 'dmarc'; type: 'MX' | 'TXT'; name: string; value: string; note: string | null; note_code: string | null }

export interface DnsReport {
  domain: string;
  checked_at: string;
  mail_host: string | null;
  mx: DnsCheck;
  spf: DnsCheck;
  dkim: DnsCheck;
  dmarc: DnsCheck;
  records: ExpectedRecord[];
}

export interface DomainChangeBody {
  operation: DomainOperation; domain?: string; address?: string; goto?: string[]; alias_id?: string;
  // add_mailbox: the display name, the first password (sent once, never kept by the page) and the quota in MiB.
  name?: string; password?: string; quota_mb?: number;
}

export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 128;
export const QUOTA_MIN_MB = 256;
export const QUOTA_MAX_MB = 102400;
export const QUOTA_DEFAULT_MB = 3072;

export const DNS_KINDS = ['mx', 'spf', 'dkim', 'dmarc'] as const;

const DOMAIN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const ADDRESS = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const LOCAL = /^[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?$/;

/** The shared status pill tone of a DNS check. */
export function dnsTone(status: DnsStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'ok') return 'success';
  if (status === 'danger' || status === 'missing') return 'dangerTone';
  if (status === 'warning') return 'warning';
  return 'neutral';
}

/** The worst status of a report, for the domain's one-line summary. */
export function worstStatus(report: DnsReport): DnsStatus {
  const rank: Record<DnsStatus, number> = { danger: 0, missing: 1, error: 2, warning: 3, ok: 4 };
  return DNS_KINDS.map((k) => report[k].status).sort((a, b) => rank[a] - rank[b])[0];
}

/** A domain name in lower case, or null when it is not one. */
export function cleanDomain(value: string): string | null {
  const domain = value.trim().toLowerCase().replace(/\.$/, '');
  return DOMAIN.test(domain) ? domain : null;
}

/** An alias as name@domain inside ``domain``, or null. */
export function cleanAlias(local: string, domain: string): string | null {
  const name = local.trim().toLowerCase();
  return LOCAL.test(name) ? `${name}@${domain}` : null;
}

/** Destinations from a comma, space or line separated list, without repeats; null when one is not an address. */
export function parseGoto(text: string): string[] | null {
  const out: string[] = [];
  for (const part of text.split(/[\s,;]+/)) {
    const address = part.trim().toLowerCase();
    if (!address) continue;
    if (!ADDRESS.test(address)) return null;
    if (!out.includes(address)) out.push(address);
  }
  return out;
}

/** Domains whose name or an alias contains the search text (case-insensitive). */
export function domainMatches(domain: MailDomain, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || domain.domain.includes(q) || domain.aliases.some((a) => a.address.includes(q) || a.goto.some((g) => g.includes(q)))
    || (domain.mailbox_list ?? []).some((m) => m.address.includes(q) || m.name.toLowerCase().includes(q));
}

/** Why a first mailbox password cannot be used (the same rules as the API), as a problem code, or null. */
export function mailboxPasswordProblem(password: string, localPart: string): 'length' | 'spaces' | 'mix' | 'name' | null {
  if (password.length < PASSWORD_MIN || password.length > PASSWORD_MAX) return 'length';
  if (password !== password.trim() || [...password].some((ch) => ch.charCodeAt(0) < 32)) return 'spaces';
  if (!/\p{L}/u.test(password) || !/\d/.test(password)) return 'mix';
  if (localPart && password.toLowerCase().includes(localPart.toLowerCase())) return 'name';
  return null;
}

/** A quota in MiB from the form, or null when it is outside what the API accepts. */
export function parseQuota(text: string): number | null {
  if (!/^\d{1,6}$/.test(text.trim())) return null;
  const value = Number(text.trim());
  return value >= QUOTA_MIN_MB && value <= QUOTA_MAX_MB ? value : null;
}

/** The change waiting for a domain or alias, if any (one at a time per target). */
export function pendingFor(pending: PendingDomainChange[], domain: string, address?: string): PendingDomainChange | undefined {
  return pending.find((p) => p.domain === domain && (address === undefined ? !p.address : p.address === address));
}

/** The catalog key of a DNS judgement or record note code (`maildom.code.<code>`), or null for a code this client does not know. */
export function dnsCodeKey(code: string | null | undefined, known: (key: string) => boolean): string | null {
  if (!code || !/^[a-z_]{2,40}$/.test(code)) return null;
  const key = `maildom.code.${code}`;
  return known(key) ? key : null;
}

/** One record as a line to paste into a DNS host's zone editor. */
export const recordLine = (r: ExpectedRecord): string => `${r.name}. ${r.type} ${r.type === 'TXT' ? `"${r.value}"` : r.value}`;
