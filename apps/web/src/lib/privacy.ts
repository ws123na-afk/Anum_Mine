// Privacy page helpers (docs/privacy.md): what ANUM stores about the caller, retention,
// "Download my data" and "Delete my data". Pure, so `pnpm --filter @anum/web test:unit`
// exercises it without a browser; the requests live in privacyApi.ts.
import type { PrivacyDeleteResult, PrivacyPrivateCounts, PrivacySharedCounts, PrivacySummary } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

// The wire shapes are the API's own (packages/contracts/src/account.ts).
export type PrivateCounts = PrivacyPrivateCounts;
export type SharedCounts = PrivacySharedCounts;
export type DeleteResult = PrivacyDeleteResult;
export type { PrivacySummary };

export interface CountRow { key: string; label: string; value: number }

const PRIVATE_KEYS: (keyof PrivateCounts)[] = [
  'conversations', 'conversation_turns', 'private_memories', 'retrieval_index_entries',
  'voice_sessions', 'voice_transcript_segments', 'notification_preferences', 'user_preferences',
  'push_devices', 'meetings', 'meeting_transcript_segments', 'meeting_recordings', 'meeting_connections',
  'learning_signals', 'learned_items', 'favorites', 'private_collections', 'private_reports',
  'email_mailboxes', 'email_messages', 'email_drafts',
  'team_notifications', 'team_profile',
];

const SHARED_KEYS: (keyof SharedCounts)[] = [
  'tasks_created', 'approvals_decided', 'shared_memories_created', 'files_uploaded',
  'email_mailboxes_connected', 'email_drafts_sent', 'whatsapp_messages_sent', 'autonomy_actions_requested',
  'work_items_created', 'work_items_assigned', 'tyre_connections_created', 'tyre_reports_created', 'commerce_connectors_created',
];

const rowLabel = (key: string) => t(`privacy.row.${key}` as MessageKey);

/** The rows "Delete my data" removes, in display order. */
export function privateRows(counts: PrivateCounts): CountRow[] {
  return PRIVATE_KEYS.map((key) => ({ key, label: rowLabel(key), value: counts[key] ?? 0 }));
}

/** Workspace records the caller created or decided: kept by a deletion. */
export function sharedRows(counts: SharedCounts): CountRow[] {
  return SHARED_KEYS.map((key) => ({ key, label: rowLabel(key), value: counts[key] ?? 0 }));
}

export function totalOf(counts: PrivateCounts | SharedCounts): number {
  return Object.values(counts).reduce((sum: number, value) => sum + (typeof value === 'number' ? value : 0), 0);
}

const days = (n: number) => t('privacy.days', { count: n });

/** Retention as plain sentences, from the server's configuration. */
export function retentionRows(summary: PrivacySummary): { label: string; value: string }[] {
  const r = summary.retention;
  return [
    {
      label: t('privacy.retention.conversations'),
      value: r.conversation_days > 0
        ? t('privacy.retention.conversationsAfter', { days: days(r.conversation_days) })
        : t('privacy.retention.conversationsKept'),
    },
    {
      label: t('privacy.retention.voice'),
      value: t('privacy.retention.voiceValue', { days: days(r.voice_transcript_days) }),
    },
    // The server words these three itself (they name its configured policy).
    { label: t('privacy.retention.privateMemories'), value: r.private_memories },
    { label: t('privacy.retention.taskMemories'), value: r.task_memories },
    { label: t('privacy.retention.audit'), value: r.audit_records },
  ];
}

/** Where the model provider runs, in words (ANUM_MODEL_PROVIDER or the workspace's choice). */
export function providerLabel(provider: string): string {
  switch (provider.toLowerCase().replace('_', '-')) {
    case 'anthropic': return t('privacy.provider.anthropic');
    case 'openai-compatible': return t('privacy.provider.openai');
    case 'ollama': return t('privacy.provider.ollama');
    case 'mock': return t('privacy.provider.mock');
    default: return provider;
  }
}

/** The sentence about where messages go when the assistant answers. */
export function providerSentence(summary: PrivacySummary): string {
  const provider = summary.model_provider.workspace ?? summary.model_provider.deployment;
  return t('privacy.providerSentence', { provider: providerLabel(provider) });
}

export interface MemorySentence {
  text: string;
  /** "Remember things from my chats" is off by the caller's own choice: offer the switch. */
  offerSwitch: boolean;
}

/**
 * What happens to the caller's chats after a reply, for the caller's effective state
 * (`model_provider.conversation_memory`, the same value as GET /api/v1/me/preferences):
 * the post-reply extraction call runs only when it is `on`; recall stops only when the
 * caller or the server turned memory off.
 */
export function memorySentence(summary: PrivacySummary): MemorySentence {
  const recall = () => t('privacy.memory.recallStill');
  switch (summary.model_provider.conversation_memory) {
    case 'on': return { offerSwitch: false, text: t('privacy.memory.on') };
    case 'off_by_user': return { offerSwitch: true, text: t('privacy.memory.offByUser') };
    case 'off_on_server': return { offerSwitch: false, text: t('privacy.memory.offServer') };
    case 'off_for_organization': return { offerSwitch: false, text: t('privacy.memory.offOrg', { recall: recall() }) };
    case 'not_for_role': return { offerSwitch: false, text: t('privacy.memory.notForRole', { recall: recall() }) };
    default: return { offerSwitch: false, text: t('privacy.memory.unknown') };
  }
}

export function confirmationMatches(typed: string, phrase: string): boolean {
  return typed.trim() === phrase;
}

/** The file name from a Content-Disposition header, or a dated fallback. */
export function exportFileName(contentDisposition: string | null, now = new Date()): string {
  const match = contentDisposition?.match(/filename="?([^";]+)"?/i);
  if (match?.[1]) return match[1];
  return `anum-my-data-${now.toISOString().slice(0, 10)}.json`;
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/** Who can use the Privacy page, in the current locale. */
export function permissionMessage(): string {
  return t('privacy.permission');
}
/** English text of {@link permissionMessage}, kept for callers and tests that compare against it. */
export const PERMISSION_MESSAGE = 'Only active members of this workspace can see or change their data here. Ask a workspace owner to restore your membership, or switch to a workspace you belong to.';

interface StatusError { status: number; detail?: string | null; message?: string }

// Duck-typed (ApiError from errors.ts has these fields) so this module imports types only.
function asStatusError(error: unknown): StatusError | null {
  return typeof error === 'object' && error !== null && typeof (error as StatusError).status === 'number' ? error as StatusError : null;
}

function reason(error: unknown): string {
  const failure = asStatusError(error);
  if (failure?.detail) return failure.detail;
  return error instanceof Error && error.message ? error.message : t('common.requestFailed');
}

/** What failed and how to fix it, for a privacy action. */
export function privacyErrorMessage(error: unknown, action: 'load' | 'export' | 'delete'): string {
  const failure = asStatusError(error);
  if (failure?.status === 403) return permissionMessage();
  if (failure?.status === 401) return t('privacy.signInExpired');
  if (failure?.status === 413) return failure.detail ?? t('privacy.tooLarge');
  if (failure?.status === 422 && action === 'delete') return failure.detail ?? t('privacy.confirmMismatch');
  return t(`privacy.failed.${action}`, { reason: reason(error) });
}

/** One sentence after a deletion: what went, what stayed. */
export function deletedSentence(result: DeleteResult): string {
  const removed = totalOf(result.deleted);
  const kept = totalOf(result.kept);
  const head = t('privacy.deleted', { count: removed });
  // The server's own sentences, e.g. a mailbox provider where ANUM could not withdraw its access.
  const parts = [head, kept ? t('privacy.keptAfter', { count: kept }) : '', ...(result.notes ?? [])];
  return parts.filter(Boolean).join(' ');
}
