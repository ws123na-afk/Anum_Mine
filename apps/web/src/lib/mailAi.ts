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
