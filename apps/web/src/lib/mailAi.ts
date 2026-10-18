// AI on the Email Center (docs/email.md#ai-on-email): types and pure helpers for MailAssist.tsx.
// Summaries and labels are read only; a drafted reply lands in the person's drafts and is sent only
// through the normal send, which waits for an owner's approval of the exact message.
import type { ApiApproval } from './api';
import { ApiError, describeError } from './errors.ts';
import { t, type MessageKey } from './i18n.ts';
import type { MailDraft } from './mail';

export const MAIL_CATEGORIES = ['customer', 'order', 'invoice', 'supplier', 'internal', 'newsletter', 'notification', 'personal', 'spam', 'other'] as const;
export type MailCategory = (typeof MAIL_CATEGORIES)[number];
export type MailPriority = 'high' | 'normal' | 'low';

export interface MailAiStatus { available: boolean; reason: string | null; model: string | null; can_read: boolean; can_act: boolean }
export interface MailSummary {
  summary: string; key_points: string[]; action_items: string[]; messages: number; thread: boolean; injection_suspected: boolean; model: string;
}
export interface MailLabel { category: MailCategory; priority: MailPriority; reason: string; source: 'model' | 'rules'; injection_suspected: boolean; model: string }
export interface MailAiDraft { status: 'done' | 'waiting_approval'; draft: MailDraft | null; approval: ApiApproval | null; injection_suspected: boolean; model: string | null }
export interface MailMessageRef { account_id: string; folder: string; uid: number }

/** What a failed AI call means for the panel: the person lacks access, AI is off, or something failed. */
export type AssistFailure = { kind: 'denied' | 'unavailable' | 'error'; text: string };

export function assistFailure(error: unknown): AssistFailure {
  if (error instanceof ApiError) {
    if (error.status === 403) return { kind: 'denied', text: error.detail || t('mailai.denied') };
    if (error.status === 409 && /AI model/i.test(error.detail ?? '')) return { kind: 'unavailable', text: error.detail ?? t('mailai.unavailable') };
    if (error.status === 503) return { kind: 'error', text: error.detail || t('mailai.failed') };
  }
  return { kind: 'error', text: describeError(error, t('mailai.failed')) };
}

export const categoryLabel = (category: MailCategory): string => t(`mailai.category.${category}` as MessageKey);
export const priorityLabel = (priority: MailPriority): string => t(`mailai.priority.${priority}` as MessageKey);
/** The badge tone used across the app (styles.css statusBadge). */
export function priorityTone(priority: MailPriority): 'dangerTone' | 'warning' | 'neutral' {
  return priority === 'high' ? 'dangerTone' : priority === 'normal' ? 'warning' : 'neutral';
}

/** Trim and bound the person's own instructions for a drafted reply (the API allows 1000 characters). */
export function cleanInstructions(text: string): string | undefined {
  const value = text.trim().slice(0, 1000);
  return value || undefined;
}

/** A stored label for one message of the list (GET /mail/ai/labels). */
export interface MailRowLabel { uid: number; category: MailCategory; priority: MailPriority; reason: string; source: 'model' | 'rules' }
export interface MailPageLabels { labels: MailRowLabel[]; labelled: number; skipped: number; failed: number; summary: string }
export interface MailWaiting { status: 'waiting_approval'; summary: string; approval: ApiApproval }

/** At most this many messages are labelled in one "Label all" (the API's cap). */
export const LABEL_PAGE_MAX = 25;
/** Export and bulk archive caps (the API's). */
export const EXPORT_MAX = 100;
export const ARCHIVE_MAX = 200;
/** Categories a tidy-up offers to select for archiving. */
export const TIDY_CATEGORIES: ReadonlySet<MailCategory> = new Set(['newsletter', 'notification', 'spam']);

/** The messages "Label all" sends: the shown page without a label yet, newest first, capped. */
export function unlabelled(uids: number[], labels: ReadonlyMap<number, MailRowLabel>, max = LABEL_PAGE_MAX): number[] {
  return uids.filter((uid) => !labels.has(uid)).slice(0, max);
}

/** Shown messages labelled as newsletters, notifications or spam with low or normal priority. */
export function tidyCandidates(uids: number[], labels: ReadonlyMap<number, MailRowLabel>): number[] {
  return uids.filter((uid) => { const label = labels.get(uid); return Boolean(label && TIDY_CATEGORIES.has(label.category) && label.priority !== 'high'); });
}

/** Merge fresh labels into what the list already shows. */
export function mergeLabels(current: ReadonlyMap<number, MailRowLabel>, fresh: MailRowLabel[]): Map<number, MailRowLabel> {
  const next = new Map(current);
  for (const label of fresh) next.set(label.uid, label);
  return next;
}

/** Words for a row's labels, for its accessible name and the chips. */
export function rowLabelText(label: MailRowLabel): string[] {
  return label.priority === 'normal' ? [categoryLabel(label.category)] : [categoryLabel(label.category), priorityLabel(label.priority)];
}

/** A selection split into what one request may carry (export 100, archive 200). */
export function capSelection(uids: number[], max: number): { uids: number[]; dropped: number } {
  const sorted = [...new Set(uids)].sort((a, b) => b - a);
  return { uids: sorted.slice(0, max), dropped: Math.max(0, sorted.length - max) };
}

export function exportFileName(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `mail-export-${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}-${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}.mbox`;
}

/** What a failed export download means: not approved yet, expired, or another failure. */
export function exportFailure(error: unknown): { kind: 'pending' | 'expired' | 'error'; text: string } {
  if (error instanceof ApiError && error.status === 410) return { kind: 'expired', text: error.detail || t('mailai.export.expired') };
  if (error instanceof ApiError && error.status === 409) return { kind: 'pending', text: error.detail || t('mailai.export.notReady') };
  return { kind: 'error', text: describeError(error, t('mailai.failed')) };
}
