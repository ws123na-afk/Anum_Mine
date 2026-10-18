// Email Center phase 6 (docs/email.md#mailbox-settings): the shapes of mailbox settings,
// forwarding rules and the background unread counts, and the checks the page makes before it
// asks for an approval (the API checks again). Pure helpers, unit-tested in test/mailRules.test.ts.
import type { ApiApproval } from './api';

export type SyncStatus = 'pending' | 'ok' | 'auth_failed' | 'offline' | 'error';

export interface MailSync {
  status: SyncStatus;
  inbox_unread: number | null;
  inbox_total: number | null;
  checked_at: string | null;
  next_sync_at: string | null;
  last_error: string | null;
}

export interface MailSettingsValues {
  sent_retention_days: number | null;
  draft_retention_days: number | null;
  label_retention_days: number | null;
  background_sync: boolean;
}

export interface MailSettings extends MailSettingsValues {
  account_id: string;
  access: 'owner' | 'send' | 'read';
  can_change: boolean;
  version: number;
  updated_at: string | null;
  retention_choices: number[];
  max_retention_days: number;
  sync_running: boolean;
  sync_interval_seconds: number;
  sync: MailSync | null;
  approval: ApiApproval | null;
  pending: MailSettingsValues | null;
}

export type RuleStatus = 'pending_approval' | 'active' | 'paused';

export interface MailRule {
  id: string;
  account_id: string;
  name: string;
  from_contains: string;
  subject_contains: string;
  forward_to: string[];
  status: RuleStatus;
  approval: ApiApproval | null;
  approval_note: string | null;
  approved_at: string | null;
  last_run_at: string | null;
  last_error: string | null;
  forwarded_count: number;
  created_at: string;
  updated_at: string;
}

export interface MailRuleInput {
  name: string;
  from_contains: string;
  subject_contains: string;
  forward_to: string[];
}

export interface MailRuleResult { rule: MailRule; approval: ApiApproval | null }
export interface MailWaiting { status: 'waiting_approval'; summary: string; approval: ApiApproval }

export interface MailUnread {
  enabled: boolean;
  sync_running: boolean;
  total: number;
  checked_at: string | null;
  mailboxes: { account_id: string; address: string; access: string; unread: number | null; checked_at: string | null; status: string }[];
}

export const MAX_FORWARD_TO = 5;
export const SETTING_KEYS = ['sent_retention_days', 'draft_retention_days', 'label_retention_days', 'background_sync'] as const;
const ADDRESS = /^[^@\s<>,;"()]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;

export const EMPTY_RULE: MailRuleInput = { name: '', from_contains: '', subject_contains: '', forward_to: [] };

/** The values of a settings answer (what an approval would change). */
export function settingsValues(settings: MailSettingsValues): MailSettingsValues {
  return {
    sent_retention_days: settings.sent_retention_days, draft_retention_days: settings.draft_retention_days,
    label_retention_days: settings.label_retention_days, background_sync: settings.background_sync,
  };
}

export function sameSettings(a: MailSettingsValues, b: MailSettingsValues): boolean {
  return SETTING_KEYS.every((key) => a[key] === b[key]);
}

/** The select's options: "keep" plus the server's choices, and the current value if it is another. */
export function retentionOptions(choices: number[], current: number | null): (number | null)[] {
  const days = [...new Set([...choices, ...(current === null ? [] : [current])])].sort((x, y) => x - y);
  return [null, ...days];
}

/** A select value back to days (empty is "keep"). */
export function parseRetention(value: string): number | null {
  if (!value) return null;
  const days = Number(value);
  return Number.isInteger(days) && days >= 1 && days <= 3650 ? days : null;
}

/** The addresses typed in one field (commas, semicolons, spaces or new lines), each once. */
export function splitAddresses(text: string): string[] {
  const seen: string[] = [];
  for (const piece of text.split(/[\s,;]+/)) {
    const address = piece.trim();
    if (address && !seen.some((s) => s.toLowerCase() === address.toLowerCase())) seen.push(address);
  }
  return seen;
}

export type RuleProblem = 'name' | 'condition' | 'noAddress' | 'tooMany' | 'invalid' | 'self';

/** Why the rule cannot be asked for yet, or null (the API checks the same again). */
export function ruleProblem(input: MailRuleInput, mailbox: string): { problem: RuleProblem; address?: string } | null {
  if (!input.name.trim()) return { problem: 'name' };
  if (!input.from_contains.trim() && !input.subject_contains.trim()) return { problem: 'condition' };
  if (input.forward_to.length === 0) return { problem: 'noAddress' };
  if (input.forward_to.length > MAX_FORWARD_TO) return { problem: 'tooMany' };
  const invalid = input.forward_to.find((a) => !ADDRESS.test(a));
  if (invalid) return { problem: 'invalid', address: invalid };
  if (input.forward_to.some((a) => a.toLowerCase() === mailbox.toLowerCase())) return { problem: 'self' };
  return null;
}

export function ruleInput(rule: MailRule): MailRuleInput {
  return { name: rule.name, from_contains: rule.from_contains, subject_contains: rule.subject_contains, forward_to: [...rule.forward_to] };
}

/** Rules waiting for approval first, then forwarding, then paused; each group oldest first. */
export function sortRules(rules: MailRule[]): MailRule[] {
  const rank: Record<RuleStatus, number> = { pending_approval: 0, active: 1, paused: 2 };
  return [...rules].sort((a, b) => rank[a.status] - rank[b.status] || a.created_at.localeCompare(b.created_at));
}
