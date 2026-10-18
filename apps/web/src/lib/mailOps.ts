// The platform operator's mail health (docs/email.md#operator-view): types of
// GET /api/v1/platform/mail/health and /server, and the pure helpers the pane uses.
// Counts, states and timestamps only: no message, address or password ever arrives here.

export interface MailHealthCounts {
  sent: number;
  send_failed: number;
  send_unknown: number;
  send_rate_limited: number;
  connect_rate_limited: number;
  auth_failed: number;
  server_offline: number;
}

export type MailHealth = 'quiet' | 'ok' | 'attention';

export interface OrganisationMailHealth {
  tenant_id: string;
  display_name: string;
  mailboxes: number;
  connected: number;
  auth_failed: number;
  offline: number;
  error: number;
  stale: number;
  last_sync_at: string | null;
  oldest_sync_at: string | null;
  counts: MailHealthCounts;
  health: MailHealth;
}

export interface MailOperatorView {
  enabled: boolean;
  days: number;
  since: string;
  mailboxes: number;
  totals: MailHealthCounts;
  organisations: OrganisationMailHealth[];
}

export interface MailcowDomain { domain: string; active: boolean; mailboxes: number; max_mailboxes: number | null; quota_used_percent: number | null }

export type MailcowState = 'off' | 'misconfigured' | 'unreachable' | 'refused' | 'ok';

export interface MailServerView {
  server: { enabled: boolean; configured: boolean; imap_host: string | null; smtp_host: string | null; allowed_tenants: number; send_per_hour: number; connect_attempts_per_hour: number };
  mailcow: {
    state: MailcowState;
    message: string | null;
    version: string | null;
    containers_total: number | null;
    containers_not_running: string[];
    queue_messages: number | null;
    domains: MailcowDomain[];
  };
}

export const MAIL_HEALTH_DAYS = [1, 7, 30, 90] as const;

/** Rate-limit refusals of sends and of connection checks together. */
export const rateLimited = (c: MailHealthCounts): number => c.send_rate_limited + c.connect_rate_limited;

/** Mailboxes that are not connected right now (password refused, offline, error). */
export const notConnected = (row: OrganisationMailHealth): number => row.auth_failed + row.offline + row.error;

/** The tone of a health state for the shared status pill. */
export function mailHealthTone(health: MailHealth): 'success' | 'dangerTone' | 'neutral' {
  return health === 'attention' ? 'dangerTone' : health === 'ok' ? 'success' : 'neutral';
}

/** Organisations needing a look first, then by name. */
export function sortMailHealth(rows: OrganisationMailHealth[]): OrganisationMailHealth[] {
  const rank: Record<MailHealth, number> = { attention: 0, ok: 1, quiet: 2 };
  return [...rows].sort((a, b) => rank[a.health] - rank[b.health] || a.display_name.localeCompare(b.display_name));
}

/** Organisations whose name or id contains the search text (case-insensitive). */
export function mailHealthMatches(row: OrganisationMailHealth, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || row.display_name.toLowerCase().includes(q) || row.tenant_id.toLowerCase().includes(q);
}
