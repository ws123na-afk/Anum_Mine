// The ANUM Command Center (docs/design/anum360.md#web-command-center): pure helpers for the home
// page. No browser APIs at import time, so `pnpm --filter @anum/web test:unit` runs them in Node.
// The API calls live in commandApi.ts; this module only turns their answers into what the page shows.
import type { AgentRun, Approval, Task, TaskStatus } from '@anum/contracts';
import { getLocale, intlLocale, t, type MessageKey } from './i18n.ts';

/** Ways to give a command. Vision is not offered: the API stores images but no model reads them yet. */
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

// ---------------------------------------------------------------- where a shortcut leads

/** Views a shortcut on the page can open (a subset of the shell's views). */
export type CommandTarget = 'inbox' | 'whatsapp' | 'tasks' | 'files' | 'voice' | 'automation' | 'agents' | 'assistant' | 'approvals' | 'memory';

/**
 * What a chip or tile does: open a real view, start a command with a prefilled request the agent can
 * carry out with its tools (the model, web search), switch to Files mode, or open the command palette.
 */
export type Shortcut =
  | { kind: 'view'; view: CommandTarget }
  | { kind: 'prefill'; text: MessageKey; mode?: CommandMode }
  | { kind: 'palette' };

export interface ShortcutItem { id: string; label: MessageKey; icon: string; action: Shortcut }

/** The chips floating around the orb (four on each side on wide screens). */
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

/**
 * The capability tiles under the composer. Meetings and phone calls from the mockup are not here:
 * the API has no calendar or telephony module yet; the voice assistant stands in for "Talk".
 */
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

/** Example requests the agent can carry out with the model and web search alone. */
export const EXAMPLE_COMMANDS: readonly MessageKey[] = [
  'command.example.1', 'command.example.2', 'command.example.3', 'command.example.4', 'command.example.5', 'command.example.6',
];

export interface QuickCommand { id: string; text: string; fromHistory: boolean }

/**
 * The quick command chips: the person's own recent requests first (newest, no repeats), topped up
 * with examples; `rotation` (the refresh button) moves through the examples.
 */
export function quickCommands(tasks: readonly Task[], rotation = 0, count = 5): QuickCommand[] {
  const seen = new Set<string>();
  const out: QuickCommand[] = [];
  for (const task of [...tasks].sort(byNewest)) {
    const text = (task.prompt || task.title).trim().replace(/\s+/g, ' ');
    const key = text.toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    out.push({ id: `task:${task.id}`, text, fromHistory: true });
    if (out.length >= Math.min(3, count)) break;
  }
  for (let index = 0; out.length < count && index < EXAMPLE_COMMANDS.length; index += 1) {
    const key = EXAMPLE_COMMANDS[(index + rotation * 2) % EXAMPLE_COMMANDS.length];
    const text = t(key);
    if (seen.has(text.toLowerCase())) continue;
    seen.add(text.toLowerCase());
    out.push({ id: key, text, fromHistory: false });
  }
  return out;
}

// ---------------------------------------------------------------- task lists

const OPEN: ReadonlySet<TaskStatus> = new Set(['created', 'queued', 'running', 'waiting_approval']);
const ACTIVE: ReadonlySet<TaskStatus> = new Set(['queued', 'running', 'waiting_approval']);

export const isOpenTask = (task: Task) => OPEN.has(task.status) && !task.archivedAt;
export const isActiveTask = (task: Task) => ACTIVE.has(task.status) && !task.archivedAt;
const byNewest = (a: Task, b: Task) => Date.parse(b.createdAt) - Date.parse(a.createdAt);
const byUpdated = (a: Task, b: Task) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt);

/** Current Tasks: open tasks, the most recently changed first. */
export function currentTasks(tasks: readonly Task[], limit = 6): { items: Task[]; total: number } {
  const open = tasks.filter(isOpenTask).sort(byUpdated);
  return { items: open.slice(0, limit), total: open.length };
}

/** Recent Commands: the newest tasks, whatever their status. */
export function recentCommands(tasks: readonly Task[], limit = 5): Task[] {
  return tasks.filter((task) => !task.archivedAt).sort(byNewest).slice(0, limit);
}

/** Live Agent Activity: the runs in flight (queued, running or waiting for approval). */
export function activeTasks(tasks: readonly Task[], limit = 5): Task[] {
  return tasks.filter(isActiveTask).sort(byUpdated).slice(0, limit);
}

/** Status pill tone, shared with the rest of the app's status badges. */
export function statusTone(status: string): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'completed') return 'success';
  if (status === 'failed' || status === 'cancelled' || status === 'rejected' || status === 'expired') return 'dangerTone';
  if (status === 'created') return 'neutral';
  return 'warning';
}

// ---------------------------------------------------------------- "ANUM is working..."

export type PhaseKey = 'understand' | 'plan' | 'act' | 'review' | 'present';
export const PHASES: readonly PhaseKey[] = ['understand', 'plan', 'act', 'review', 'present'];
export type PhaseState = 'done' | 'active' | 'waiting' | 'failed' | 'todo';
export interface Phase { key: PhaseKey; state: PhaseState; seconds: number | null }

/** Which phase a run step belongs to. */
function phaseOfStep(type: AgentRun['steps'][number]['type']): number {
  switch (type) {
    case 'queued': return 0;
    case 'retrieval': case 'model_call': return 1;
    case 'tool_proposal': case 'approval_wait': case 'tool_result': return 2;
    case 'final': return 3;
    default: return 1;
  }
}

/**
 * The five steps of the tracker from the task and its run: done up to the run's furthest step,
 * the current one active (or waiting for approval, or failed), the rest to do. Durations come from
 * the run's step timestamps; a phase without its own steps shows no time.
 */
export function trackerPhases(task: Pick<Task, 'status' | 'createdAt'> | null, run: Pick<AgentRun, 'status' | 'steps'> | null, submitting = false): Phase[] {
  const status = run?.status ?? task?.status ?? (submitting ? 'created' : null);
  const steps = run?.steps ?? [];
  let current = 0;
  for (const step of steps) current = Math.max(current, phaseOfStep(step.type));
  if (status === 'running' && current === 0 && !submitting) current = 1;
  if (status === 'waiting_approval') current = Math.max(current, 2);
  const complete = status === 'completed';
  const failed = status === 'failed' || status === 'cancelled';
  const seconds = phaseDurations(task?.createdAt ?? null, steps);
  return PHASES.map((key, index) => {
    let state: PhaseState;
    if (status === null) state = 'todo';
    else if (complete) state = 'done';
    else if (index < current) state = 'done';
    else if (index === current) state = failed ? 'failed' : status === 'waiting_approval' ? 'waiting' : 'active';
    else state = 'todo';
    return { key, state, seconds: state === 'done' || complete ? seconds[index] : null };
  });
}

function phaseDurations(start: string | null, steps: readonly { type: AgentRun['steps'][number]['type']; createdAt: string }[]): (number | null)[] {
  const out: (number | null)[] = PHASES.map(() => null);
  let previous = start ? Date.parse(start) : NaN;
  for (const step of steps) {
    const at = Date.parse(step.createdAt);
    if (Number.isNaN(at)) continue;
    const phase = phaseOfStep(step.type);
    if (!Number.isNaN(previous) && at >= previous) out[phase] = (out[phase] ?? 0) + Math.round((at - previous) / 1000);
    previous = at;
  }
  return out;
}

/** Progress through the five phases, 0 to 100, for the activity bars. */
export function phaseProgress(phases: readonly Phase[]): number {
  const done = phases.filter((phase) => phase.state === 'done').length;
  const partial = phases.some((phase) => phase.state === 'active' || phase.state === 'waiting') ? 0.5 : 0;
  return Math.round(((done + partial) / PHASES.length) * 100);
}

/** The latest step a run recorded, for the one-line "what it is doing now". */
export function latestStep(run: Pick<AgentRun, 'steps'> | null): AgentRun['steps'][number] | null {
  return run?.steps.length ? run.steps[run.steps.length - 1] : null;
}

/** A task's title from a typed or spoken request: the first line, at most 80 characters. */
export function commandTitle(text: string): string {
  const line = text.trim().split(/\r?\n/)[0].replace(/\s+/g, ' ');
  return line.length > 80 ? `${line.slice(0, 77).trimEnd()}...` : line;
}

/** The request the agent receives: the person's words, plus the documents they attached by name and id. */
export function commandPrompt(text: string, attachments: readonly { id: string; name: string }[] = []): string {
  const body = text.trim();
  if (!attachments.length) return body;
  const list = attachments.map((file) => `- ${file.name} (file id ${file.id})`).join('\n');
  return `${body}\n\nAttached documents (in Documents):\n${list}`;
}

// ---------------------------------------------------------------- suggestions

export type SuggestionAction =
  | { kind: 'view'; view: CommandTarget }
  | { kind: 'retry'; taskId: string }
  | { kind: 'focus' };

export interface Suggestion { id: string; title: string; detail: string; action: SuggestionAction; tone: 'info' | 'warning' | 'danger' }

export interface SuggestionInput {
  approvals: readonly Approval[];
  tasks: readonly Task[];
  /** Unread messages across connected mailboxes; null when the inbox could not be read. */
  unreadEmails: number | null;
  /** Connected mailboxes; null when the inbox could not be read. */
  mailboxes: number | null;
  /** Documents in the workspace; null when unknown. */
  files: number | null;
}

/**
 * "Suggestions for you": things in this workspace that need the person, most urgent first, each
 * with the one action that deals with it. Built only from the workspace's own data.
 */
export function buildSuggestions(input: SuggestionInput, limit = 5): Suggestion[] {
  const out: Suggestion[] = [];
  const pending = input.approvals.filter((approval) => approval.status === 'pending');
  if (pending.length) out.push({ id: 'approvals', title: t('command.suggest.approvals', { count: pending.length }), detail: t('command.suggest.approvalsDetail'), action: { kind: 'view', view: 'approvals' }, tone: 'warning' });
  for (const task of input.tasks.filter((x) => x.status === 'failed' && !x.archivedAt).sort(byUpdated).slice(0, 2)) {
    out.push({ id: `failed:${task.id}`, title: t('command.suggest.failed', { title: task.title }), detail: t('command.suggest.failedDetail'), action: { kind: 'retry', taskId: task.id }, tone: 'danger' });
  }
  if (input.unreadEmails !== null && input.unreadEmails > 0) out.push({ id: 'unread', title: t('command.suggest.unread', { count: input.unreadEmails }), detail: t('command.suggest.unreadDetail'), action: { kind: 'view', view: 'inbox' }, tone: 'info' });
  if (input.mailboxes === 0) out.push({ id: 'mailbox', title: t('command.suggest.mailbox'), detail: t('command.suggest.mailboxDetail'), action: { kind: 'view', view: 'inbox' }, tone: 'info' });
  const waiting = input.tasks.filter((x) => x.status === 'created' && !x.archivedAt).length;
  if (waiting) out.push({ id: 'created', title: t('command.suggest.notStarted', { count: waiting }), detail: t('command.suggest.notStartedDetail'), action: { kind: 'view', view: 'tasks' }, tone: 'info' });
  if (input.files === 0) out.push({ id: 'files', title: t('command.suggest.files'), detail: t('command.suggest.filesDetail'), action: { kind: 'view', view: 'files' }, tone: 'info' });
  if (!input.tasks.length) out.push({ id: 'first', title: t('command.suggest.first'), detail: t('command.suggest.firstDetail'), action: { kind: 'focus' }, tone: 'info' });
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
