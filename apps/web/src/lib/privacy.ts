// Privacy page helpers (docs/privacy.md): what ANUM stores about the caller, retention,
// "Download my data" and "Delete my data". Pure, so `pnpm --filter @anum/web test:unit`
// exercises it without a browser; the requests live in privacyApi.ts.

export interface PrivateCounts {
  conversations: number;
  conversation_turns: number;
  private_memories: number;
  retrieval_index_entries: number;
  voice_sessions: number;
  voice_transcript_segments: number;
  notification_preferences: number;
}

export interface SharedCounts {
  tasks_created: number;
  approvals_decided: number;
  shared_memories_created: number;
  files_uploaded: number;
}

export interface PrivacySummary {
  tenant_id: string;
  workspace_id: string;
  user_id: string;
  role: string | null;
  private: PrivateCounts;
  shared: SharedCounts;
  retention: {
    conversation_days: number;
    voice_transcript_days: number;
    voice_transcript_choices: string[];
    private_memories: string;
    task_memories: string;
    audit_records: string;
  };
  model_provider: { deployment: string; workspace: string | null; conversation_memory_enabled: boolean };
  privacy_policy_url: string | null;
  delete_confirmation: string;
  export_max_bytes: number;
  account_note: string;
  shared_note: string;
}

export interface DeleteResult {
  deleted: PrivateCounts;
  kept: SharedCounts;
  account_note: string;
  shared_note: string;
}

export interface CountRow { key: string; label: string; value: number }

const PRIVATE_LABELS: [keyof PrivateCounts, string][] = [
  ['conversations', 'Assistant conversations'],
  ['conversation_turns', 'Messages in those conversations'],
  ['private_memories', 'Private memories (from your chats)'],
  ['retrieval_index_entries', 'Search index entries for those memories'],
  ['voice_sessions', 'Voice sessions'],
  ['voice_transcript_segments', 'Voice transcript lines'],
  ['notification_preferences', 'Notification preferences'],
];

const SHARED_LABELS: [keyof SharedCounts, string][] = [
  ['tasks_created', 'Tasks you created (with their runs)'],
  ['approvals_decided', 'Approvals you decided'],
  ['shared_memories_created', 'Shared memories you added'],
  ['files_uploaded', 'Files you uploaded'],
];

/** The rows "Delete my data" removes, in display order. */
export function privateRows(counts: PrivateCounts): CountRow[] {
  return PRIVATE_LABELS.map(([key, label]) => ({ key, label, value: counts[key] ?? 0 }));
}

/** Workspace records the caller created or decided: kept by a deletion. */
export function sharedRows(counts: SharedCounts): CountRow[] {
  return SHARED_LABELS.map(([key, label]) => ({ key, label, value: counts[key] ?? 0 }));
}

export function totalOf(counts: PrivateCounts | SharedCounts): number {
  return Object.values(counts).reduce((sum: number, value) => sum + (typeof value === 'number' ? value : 0), 0);
}

const days = (n: number) => `${n} ${n === 1 ? 'day' : 'days'}`;

/** Retention as plain sentences, from the server's configuration. */
export function retentionRows(summary: PrivacySummary): { label: string; value: string }[] {
  const r = summary.retention;
  return [
    {
      label: 'Assistant conversations',
      value: r.conversation_days > 0
        ? `Deleted after ${days(r.conversation_days)} without activity, or when you delete them.`
        : 'Kept until you delete them.',
    },
    {
      label: 'Voice transcripts',
      value: `You choose per session: removed when the session ends, after ${days(r.voice_transcript_days)}, or kept until you delete them.`,
    },
    { label: 'Private memories', value: r.private_memories },
    { label: 'Task memories', value: r.task_memories },
    { label: 'Audit log', value: r.audit_records },
  ];
}

/** Where the model provider runs, in words (ANUM_MODEL_PROVIDER or the workspace's choice). */
export function providerLabel(provider: string): string {
  switch (provider.toLowerCase().replace('_', '-')) {
    case 'anthropic': return 'Anthropic (Claude), an external service';
    case 'openai-compatible': return 'an OpenAI-compatible service, external unless your administrator runs it';
    case 'ollama': return 'Ollama, a model running on your organization’s own server';
    case 'mock': return 'no model provider (local development mock: nothing leaves the server)';
    default: return provider;
  }
}

/** The sentence about where messages go when the assistant answers. */
export function providerSentence(summary: PrivacySummary): string {
  const provider = summary.model_provider.workspace ?? summary.model_provider.deployment;
  const memory = summary.model_provider.conversation_memory_enabled
    ? ' After a reply, a short model call also picks out facts you stated to remember as private memories.'
    : '';
  return `To produce answers, your messages, voice questions and task prompts are sent to ${providerLabel(provider)}.${memory}`;
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

export const PERMISSION_MESSAGE = 'Only active members of this workspace can see or change their data here. Ask a workspace owner to restore your membership, or switch to a workspace you belong to.';

interface StatusError { status: number; detail?: string | null; message?: string }

// Duck-typed (ApiError from errors.ts has these fields) so this module stays import-free.
function asStatusError(error: unknown): StatusError | null {
  return typeof error === 'object' && error !== null && typeof (error as StatusError).status === 'number' ? error as StatusError : null;
}

function reason(error: unknown): string {
  const failure = asStatusError(error);
  if (failure?.detail) return failure.detail;
  return error instanceof Error && error.message ? error.message : 'Request failed.';
}

/** What failed and how to fix it, for a privacy action. */
export function privacyErrorMessage(error: unknown, action: 'load' | 'export' | 'delete'): string {
  const failure = asStatusError(error);
  if (failure?.status === 403) return PERMISSION_MESSAGE;
  if (failure?.status === 401) return 'Your sign-in expired. Sign in again, then retry.';
  if (failure?.status === 413) return failure.detail ?? 'Your data is larger than this server’s export limit. Delete conversations you no longer need, or ask your administrator to raise it.';
  if (failure?.status === 422 && action === 'delete') return failure.detail ?? 'Type the confirmation phrase exactly. Nothing was deleted.';
  const what = action === 'load' ? 'Could not load your privacy summary' : action === 'export' ? 'Could not prepare your download' : 'Could not delete your data';
  const fix = action === 'delete' ? 'Reload to see what is still stored, then try again.' : 'Check your connection and try again.';
  return `${what}: ${reason(error)} ${fix}`;
}

/** One sentence after a deletion: what went, what stayed. */
export function deletedSentence(result: DeleteResult): string {
  const removed = totalOf(result.deleted);
  const kept = totalOf(result.kept);
  const head = removed ? `Deleted ${removed} private ${removed === 1 ? 'item' : 'items'}.` : 'There was nothing private to delete.';
  return kept ? `${head} ${kept} shared workspace ${kept === 1 ? 'record stays' : 'records stay'}.` : head;
}
