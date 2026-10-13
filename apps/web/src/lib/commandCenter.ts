// Small helpers for the ANUM Command Center and Home (docs/command.md#the-web-command-center): the
// speaking language for push-to-talk, the orb chips and capability tiles, the step timeline, live
// agent activity, "Suggestions for you", relative times and file sizes. No browser APIs at import
// time, so `pnpm --filter @anum/web test:unit` runs them in Node. The command rules are in commands.ts.
import type { CommandStatus, CommandSummary } from '@anum/contracts';
import { getLocale, intlLocale, t, type MessageKey } from './i18n.ts';

/** Ways to give a command: speak to the orb, type, or pick documents to attach. */
export type CommandMode = 'voice' | 'text' | 'files';
export const COMMAND_MODES: readonly CommandMode[] = ['voice', 'text', 'files'];

/**
 * Speaking languages for voice commands. Only languages the interface has a catalog for are offered
 * (English and Arabic); Urdu returns when the web catalogs gain it (docs/web-localization.md).
 */
export type SpeechLanguage = 'en' | 'ar';
export const SPEECH_LANGUAGES: readonly SpeechLanguage[] = ['en', 'ar'];
/** The recognizer locale for each speaking language. */
export const SPEECH_LOCALES: Record<SpeechLanguage, string> = { en: 'en-US', ar: 'ar-SA' };
export const SPEECH_LANGUAGE_KEY = 'anum.command.language';

export function isSpeechLanguage(value: unknown): value is SpeechLanguage {
  return typeof value === 'string' && (SPEECH_LANGUAGES as readonly string[]).includes(value);
}

/** The saved speaking language, else the interface language. */
export function initialSpeechLanguage(saved: string | null | undefined, interfaceLocale: string): SpeechLanguage {
  if (isSpeechLanguage(saved)) return saved;
  return interfaceLocale === 'ar' ? 'ar' : 'en';
}

// ---------------------------------------------------------------- shortcuts around the orb

/** Views a chip, tile or suggestion can open (a subset of the shell's views). */
export type CommandTarget = 'inbox' | 'whatsapp' | 'tasks' | 'files' | 'voice' | 'automation' | 'agents' | 'assistant' | 'approvals';

/**
 * What a chip or tile does: open a real view, put a request into the composer for the person to
 * finish (it is planned only when they ask, nothing runs before Start), or open the search palette.
 */
export type Shortcut =
  | { kind: 'view'; view: CommandTarget }
  | { kind: 'prefill'; text: MessageKey; mode?: CommandMode }
  | { kind: 'palette' };

export interface ShortcutItem { id: string; label: MessageKey; icon: string; action: Shortcut }

/** The chips around the orb: four on each side on wide screens. */
export const ORB_CHIPS: readonly ShortcutItem[] = [
  { id: 'emails', label: 'command.chip.emails', icon: 'mail', action: { kind: 'view', view: 'inbox' } },
  { id: 'report', label: 'command.chip.report', icon: 'chart', action: { kind: 'prefill', text: 'command.prefill.report' } },
  { id: 'research', label: 'command.chip.research', icon: 'search', action: { kind: 'prefill', text: 'command.prefill.research' } },
  { id: 'task', label: 'command.chip.task', icon: 'check', action: { kind: 'view', view: 'tasks' } },
  { id: 'analyze', label: 'command.chip.analyze', icon: 'file', action: { kind: 'prefill', text: 'command.prefill.analyze', mode: 'files' } },
  { id: 'whatsapp', label: 'command.chip.whatsapp', icon: 'message', action: { kind: 'view', view: 'whatsapp' } },
  { id: 'automate', label: 'command.chip.automate', icon: 'workflow', action: { kind: 'view', view: 'automation' } },
  { id: 'open', label: 'command.chip.open', icon: 'grid', action: { kind: 'palette' } },
];

/** The row of capability tiles under the composer. */
export const CAPABILITY_TILES: readonly ShortcutItem[] = [
  { id: 'email', label: 'command.tile.email', icon: 'mail', action: { kind: 'view', view: 'inbox' } },
  { id: 'analysis', label: 'command.tile.analysis', icon: 'chart', action: { kind: 'prefill', text: 'command.prefill.analyze', mode: 'files' } },
  { id: 'reports', label: 'command.tile.reports', icon: 'report', action: { kind: 'prefill', text: 'command.prefill.report' } },
  { id: 'voice', label: 'command.tile.voice', icon: 'mic', action: { kind: 'view', view: 'voice' } },
  { id: 'whatsapp', label: 'command.tile.whatsapp', icon: 'message', action: { kind: 'view', view: 'whatsapp' } },
  { id: 'tasks', label: 'command.tile.tasks', icon: 'check', action: { kind: 'view', view: 'tasks' } },
  { id: 'research', label: 'command.tile.research', icon: 'globe', action: { kind: 'prefill', text: 'command.prefill.research' } },
  { id: 'automations', label: 'command.tile.automations', icon: 'workflow', action: { kind: 'view', view: 'automation' } },
];

/** Example requests for the line under the composer (and the chips when the API offers none). */
export const EXAMPLE_COMMANDS: readonly MessageKey[] = [
  'command.example.1', 'command.example.2', 'command.example.3', 'command.example.4', 'command.example.5', 'command.example.6',
];

// ---------------------------------------------------------------- the step timeline

export type StepState = 'done' | 'active' | 'waiting' | 'failed' | 'todo';

/** Where a command step stands on the timeline, from its task's status (or `missing`). */
export function stepState(status: string): StepState {
  if (status === 'completed') return 'done';
  if (status === 'running' || status === 'queued') return 'active';
  if (status === 'waiting_approval') return 'waiting';
  if (status === 'created') return 'todo';
  return 'failed';
}

/** The tracker's heading for the command it follows. */
export function trackerTitle(status: CommandStatus | null): MessageKey {
  if (status === null) return 'command.tracker.ready';
  if (status === 'waiting_approval') return 'command.tracker.waiting';
  if (status === 'running' || status === 'ready') return 'command.tracker.working';
  return 'command.tracker.last';
}

/** Live agent activity: the commands still running or waiting for approval, newest first. */
export function liveCommands(history: readonly CommandSummary[], limit = 5): CommandSummary[] {
  return history.filter((c) => c.status === 'running' || c.status === 'ready' || c.status === 'waiting_approval').slice(0, limit);
}

/** Merge a fresh first page of history into what is shown, keeping older pages already loaded. */
export function mergeHistory(fresh: readonly CommandSummary[], shown: readonly CommandSummary[]): CommandSummary[] {
  const ids = new Set(fresh.map((c) => c.id));
  return [...fresh, ...shown.filter((c) => !ids.has(c.id))];
}

// ---------------------------------------------------------------- suggestions

export type SuggestionAction =
  | { kind: 'view'; view: CommandTarget }
  | { kind: 'replan'; text: string }
  | { kind: 'focus' };

export interface Suggestion { id: string; title: string; detail: string; action: SuggestionAction; tone: 'info' | 'warning' | 'danger' }

export interface SuggestionInput {
  /** Pending approvals in the workspace; null when they could not be read. */
  pendingApprovals: number | null;
  /** Commands shown in history (null while loading or when they could not be read). */
  commands: readonly Pick<CommandSummary, 'id' | 'text' | 'status'>[] | null;
  /** Unread messages across connected mailboxes; null when the inbox could not be read. */
  unreadEmails: number | null;
  /** Connected mailboxes; null when the inbox could not be read. */
  mailboxes: number | null;
  /** Documents in the workspace; null when unknown. */
  files: number | null;
}

/**
 * "Suggestions for you": what in this workspace needs the person, most urgent first, each with the
 * one action that deals with it. A failed command is planned again (shown before anything runs).
 */
export function buildSuggestions(input: SuggestionInput, limit = 5): Suggestion[] {
  const out: Suggestion[] = [];
  if (input.pendingApprovals) out.push({ id: 'approvals', title: t('command.suggest.approvals', { count: input.pendingApprovals }), detail: t('command.suggest.approvalsDetail'), action: { kind: 'view', view: 'approvals' }, tone: 'warning' });
  for (const command of (input.commands ?? []).filter((c) => c.status === 'failed' || c.status === 'partial').slice(0, 2)) {
    out.push({ id: `failed:${command.id}`, title: t('command.suggest.failed', { title: command.text }), detail: t('command.suggest.failedDetail'), action: { kind: 'replan', text: command.text }, tone: 'danger' });
  }
  if (input.unreadEmails) out.push({ id: 'unread', title: t('command.suggest.unread', { count: input.unreadEmails }), detail: t('command.suggest.unreadDetail'), action: { kind: 'view', view: 'inbox' }, tone: 'info' });
  if (input.mailboxes === 0) out.push({ id: 'mailbox', title: t('command.suggest.mailbox'), detail: t('command.suggest.mailboxDetail'), action: { kind: 'view', view: 'inbox' }, tone: 'info' });
  if (input.files === 0) out.push({ id: 'files', title: t('command.suggest.files'), detail: t('command.suggest.filesDetail'), action: { kind: 'view', view: 'files' }, tone: 'info' });
  if (input.commands && input.commands.length === 0) out.push({ id: 'first', title: t('command.suggest.first'), detail: t('command.suggest.firstDetail'), action: { kind: 'focus' }, tone: 'info' });
  return out.slice(0, limit);
}

// ---------------------------------------------------------------- time

/** "2 hours ago" in the interface language (Intl.RelativeTimeFormat). */
export function relativeTime(iso: string, now = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return '';
  const seconds = Math.round((at - now) / 1000);
  const format = new Intl.RelativeTimeFormat(intlLocale(getLocale()), { numeric: 'auto' });
  const abs = Math.abs(seconds);
  if (abs < 45) return t('command.time.justNow');
  if (abs < 3600) return format.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return format.format(Math.round(seconds / 3600), 'hour');
  if (abs < 86400 * 30) return format.format(Math.round(seconds / 86400), 'day');
  return format.format(Math.round(seconds / (86400 * 30)), 'month');
}

/** A file size in the units the Documents view uses. */
export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return t('command.size.bytes', { size: bytes });
  if (bytes < 1024 * 1024) return t('command.size.kb', { size: Math.round(bytes / 102.4) / 10 });
  return t('command.size.mb', { size: Math.round(bytes / (1024 * 102.4)) / 10 });
}
