// Pure helpers for the Automations page (docs/automation.md): step drafts and their validation,
// overlap policies, schedule presets, time labels, permissions and error wording. No DOM, so
// `pnpm --filter @anum/web test:unit` exercises them directly. Every sentence comes from the
// catalogs (lib/messages, `auto.*`) through t(), so labels follow the current language.
import { getLocale, intlLocale, joinList, t, type MessageKey } from './i18n.ts';

// ---------------------------------------------------------------------------------- API shapes

export type RunStatus = 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
export type OverlapPolicy = 'allow' | 'skip' | 'cancel_previous';
export interface WorkflowStepBody { id: string; name: string; action: string; input: Record<string, unknown>; max_attempts: number; retry_delay_seconds?: number }
export interface AutomationWorkflow {
  id: string; name: string; description: string; status: 'active' | 'disabled'; version: number; overlap_policy: OverlapPolicy;
  steps: WorkflowStepBody[]; created_at: string; updated_at: string; created_by: string | null; updated_by: string | null;
  last_run_at: string | null; last_run_status: RunStatus | null; next_run_at: string | null; active_runs: number; run_count: number; schedule_count: number;
}
/** What a running step waits for (docs/automation.md#execution). */
export type WaitingFor = 'start' | 'task' | 'approval' | 'retry';
export interface AutomationRunStep {
  id: string; name: string; action: string; status: RunStatus; attempt: number; input: Record<string, unknown>; output: Record<string, unknown>; error: string | null;
  max_attempts?: number; waiting_for?: WaitingFor | null; next_attempt_at?: string | null;
}
export interface AutomationRun {
  id: string; workflow_id: string; workflow_name: string | null; workflow_version: number | null; status: RunStatus; trigger: 'manual' | 'schedule' | 'retry' | 'event' | 'webhook';
  idempotency_key: string | null; retry_of: string | null; schedule_id: string | null; current_step: number; steps: AutomationRunStep[];
  created_at: string; updated_at: string; created_by: string | null; error: string | null;
  trigger_id?: string | null; trigger_type?: string | null; waiting_for?: WaitingFor | null; wake_at?: string | null;
}
export interface AutomationSchedule {
  id: string; workflow_id: string; name: string; cron: string; cron_description: string; cron_description_ar?: string; timezone: string; enabled: boolean;
  next_run_at: string | null; last_run_at: string | null; last_outcome: string | null; created_by: string | null; created_at: string; updated_at: string;
}
export interface SchedulePreview { cron: string; timezone: string; description: string; description_ar?: string; next_runs: string[] }

/** The schedule in words in the current language (the API writes English and Arabic). */
export const cronWords = (english: string, arabic: string | undefined): string => getLocale() === 'ar' && arabic ? arabic : english;
export interface WorkflowBody { name: string; description: string; overlap_policy: OverlapPolicy; steps: WorkflowStepBody[] }
export interface Page<T> { items: T[]; total: number }

// ----------------------------------------------------------------------------------- Steps

/** The actions the engine executes (anum_api/automation.py STEP_ACTIONS). */
export const STEP_ACTIONS = [
  { value: 'task.create', get label() { return t('auto.action.taskCreate'); }, get help() { return t('auto.action.taskCreateHelp'); } },
  { value: 'email.send', get label() { return t('auto.action.emailSend'); }, get help() { return t('auto.action.emailSendHelp'); } },
  { value: 'notify', get label() { return t('auto.action.notify'); }, get help() { return t('auto.action.notifyHelp'); } },
  { value: 'pause', get label() { return t('auto.action.pause'); }, get help() { return t('auto.action.pauseHelp'); } },
  { value: 'fail', get label() { return t('auto.action.fail'); }, get help() { return t('auto.action.failHelp'); } },
  { value: 'report.generate', get label() { return t('auto.action.reportGenerate'); }, get help() { return t('auto.action.reportGenerateHelp'); } },
] as const;
export type StepAction = typeof STEP_ACTIONS[number]['value'];
export const TITLE_MAX = 160, PROMPT_MAX = 8000, MESSAGE_MAX = 500, NAME_MAX = 160, DESCRIPTION_MAX = 1000, STEPS_MAX = 100;
export const SUBJECT_MAX = 500, BODY_MAX = 20000, RECIPIENTS_MAX = 20;

export interface StepDraft {
  /** Stable React key; never sent. */
  key: string;
  id: string; name: string; action: string;
  title: string; prompt: string; message: string;
  /** task.create: run the task (default) or only create it. */
  runTask: boolean;
  /** email.send */
  accountId: string; to: string; subject: string; body: string;
  /** notify: also push to the owner's phone. */
  push: boolean;
  maxAttempts: number; retryDelay: number;
  /** Input keys the form does not edit, kept as they were. */
  extra: Record<string, unknown>;
}
export interface WorkflowDraft { name: string; description: string; overlapPolicy: OverlapPolicy; steps: StepDraft[] }

let keySeed = 0;
const nextKey = () => `step-key-${++keySeed}`;

export const actionLabel = (action: string): string => STEP_ACTIONS.find((item) => item.value === action)?.label ?? action;
export const isKnownAction = (action: string): action is StepAction => STEP_ACTIONS.some((item) => item.value === action);

export function newStep(existing: StepDraft[], action: StepAction = 'task.create'): StepDraft {
  const ids = new Set(existing.map((step) => step.id));
  let index = existing.length + 1;
  while (ids.has(`step-${index}`)) index += 1;
  return {
    key: nextKey(), id: `step-${index}`, name: t('auto.stepN', { n: index }, { isolate: false }), action, title: '', prompt: '', message: '',
    runTask: true, accountId: '', to: '', subject: '', body: '', push: false, maxAttempts: 3, retryDelay: 60, extra: {},
  };
}

export function emptyDraft(): WorkflowDraft {
  return { name: '', description: '', overlapPolicy: 'allow', steps: [newStep([])] };
}

export function moveStep<T>(steps: T[], index: number, delta: number): T[] {
  const target = index + delta;
  if (index < 0 || index >= steps.length || target < 0 || target >= steps.length) return steps;
  const next = [...steps];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function removeStep<T>(steps: T[], index: number): T[] { return steps.length <= 1 ? steps : steps.filter((_, i) => i !== index); }

const text = (value: unknown): string => typeof value === 'string' ? value : '';

export function draftFromWorkflow(workflow: Pick<AutomationWorkflow, 'name' | 'description' | 'overlap_policy' | 'steps'>): WorkflowDraft {
  return {
    name: workflow.name,
    description: workflow.description,
    overlapPolicy: workflow.overlap_policy ?? 'allow',
    steps: workflow.steps.map((step) => {
      const { title, prompt, message, run, account_id, to, subject, body, channels, ...extra } = step.input ?? {};
      return {
        key: nextKey(), id: step.id, name: step.name, action: step.action, title: text(title), prompt: text(prompt), message: text(message),
        runTask: run !== false, accountId: text(account_id), to: Array.isArray(to) ? to.filter((x) => typeof x === 'string').join(', ') : text(to),
        subject: text(subject), body: text(body), push: Array.isArray(channels) && channels.includes('push'),
        maxAttempts: step.max_attempts ?? 3, retryDelay: step.retry_delay_seconds ?? 60, extra,
      };
    }),
  };
}

function stepInput(step: StepDraft): Record<string, unknown> {
  if (step.action === 'task.create') return { ...step.extra, title: step.title.trim(), prompt: step.prompt.trim(), ...(step.runTask ? {} : { run: false }) };
  if (step.action === 'email.send') return { ...step.extra, account_id: step.accountId, to: splitRecipients(step.to), subject: step.subject.trim(), body: step.body.trim() };
  if (step.action === 'notify') {
    const channels = step.push ? ['in_app', 'push'] : ['in_app'];
    return step.message.trim() ? { ...step.extra, message: step.message.trim(), channels } : { ...step.extra, channels };
  }
  if (step.action === 'fail') return step.message.trim() ? { ...step.extra, message: step.message.trim() } : { ...step.extra };
  return { ...step.extra };
}

/** "a@x.com, b@y.com" (commas, semicolons or new lines) to a list; templates stay as written. */
export function splitRecipients(value: string): string[] {
  return [...new Set(value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean))];
}

export type FieldErrors = Record<string, string>;
export type DraftCheck = { ok: true; body: WorkflowBody } | { ok: false; errors: FieldErrors; summary: string };

/** The API's rules, checked before sending; error keys are `name`, `steps` or `<step key>.<field>`. */
export function checkDraft(draft: WorkflowDraft): DraftCheck {
  const errors: FieldErrors = {};
  const name = draft.name.trim();
  if (!name) errors.name = t('auto.err.name');
  else if (name.length > NAME_MAX) errors.name = t('auto.err.tooLong', { max: NAME_MAX });
  if (draft.description.trim().length > DESCRIPTION_MAX) errors.description = t('auto.err.tooLong', { max: DESCRIPTION_MAX });
  if (draft.steps.length < 1 || draft.steps.length > STEPS_MAX) errors.steps = t('auto.err.stepsCount', { max: STEPS_MAX });
  const seen = new Set<string>();
  draft.steps.forEach((step, index) => {
    const n = index + 1;
    const id = step.id.trim();
    if (!id) errors[`${step.key}.id`] = t('auto.err.stepId', { n });
    else if (seen.has(id)) errors[`${step.key}.id`] = t('auto.err.stepIdUsed', { n, id });
    seen.add(id);
    if (!step.name.trim()) errors[`${step.key}.name`] = t('auto.err.stepName', { n });
    if (!isKnownAction(step.action)) errors[`${step.key}.action`] = t('auto.err.stepAction', { n, action: step.action });
    if (step.action === 'task.create') {
      if (!step.title.trim()) errors[`${step.key}.title`] = t('auto.err.title', { n });
      else if (step.title.trim().length > TITLE_MAX) errors[`${step.key}.title`] = t('auto.err.titleLong', { n, max: TITLE_MAX });
      if (!step.prompt.trim()) errors[`${step.key}.prompt`] = t('auto.err.prompt', { n });
      else if (step.prompt.trim().length > PROMPT_MAX) errors[`${step.key}.prompt`] = t('auto.err.promptLong', { n, max: PROMPT_MAX });
    }
    if (step.action === 'email.send') {
      if (!step.accountId) errors[`${step.key}.accountId`] = t('auto.err.mailbox', { n });
      const recipients = splitRecipients(step.to);
      if (!recipients.length) errors[`${step.key}.to`] = t('auto.err.recipients', { n });
      else if (recipients.length > RECIPIENTS_MAX) errors[`${step.key}.to`] = t('auto.err.recipientsMany', { n, max: RECIPIENTS_MAX });
      if (step.subject.trim().length > SUBJECT_MAX) errors[`${step.key}.subject`] = t('auto.err.tooLong', { max: SUBJECT_MAX });
      if (!step.body.trim()) errors[`${step.key}.body`] = t('auto.err.body', { n });
      else if (step.body.trim().length > BODY_MAX) errors[`${step.key}.body`] = t('auto.err.tooLong', { max: BODY_MAX });
    }
    if (!(step.retryDelay >= 5 && step.retryDelay <= 3600)) errors[`${step.key}.retryDelay`] = t('auto.err.retryDelay', { n });
    if ((step.action === 'notify' || step.action === 'fail') && step.message.trim().length > MESSAGE_MAX) {
      errors[`${step.key}.message`] = t('auto.err.messageLong', { n, max: MESSAGE_MAX });
    }
  });
  const problems = Object.values(errors);
  if (problems.length) return { ok: false, errors, summary: t('auto.err.summary', { count: problems.length, first: problems[0] }, { isolate: false }) };
  return {
    ok: true,
    body: {
      name, description: draft.description.trim(), overlap_policy: draft.overlapPolicy,
      steps: draft.steps.map((step) => ({
        id: step.id.trim(), name: step.name.trim(), action: step.action, input: stepInput(step),
        max_attempts: Math.min(10, Math.max(1, Math.round(step.maxAttempts) || 3)), retry_delay_seconds: Math.round(step.retryDelay),
      })),
    },
  };
}

// --------------------------------------------------------------------------- Overlap policies

export const OVERLAP_POLICIES: { value: OverlapPolicy; label: string; help: string }[] = [
  { value: 'allow', get label() { return t('auto.policy.allow'); }, get help() { return t('auto.policy.allowHelp'); } },
  { value: 'skip', get label() { return t('auto.policy.skip'); }, get help() { return t('auto.policy.skipHelp'); } },
  { value: 'cancel_previous', get label() { return t('auto.policy.cancelPrevious'); }, get help() { return t('auto.policy.cancelPreviousHelp'); } },
];
export const overlapLabel = (policy: OverlapPolicy): string => OVERLAP_POLICIES.find((item) => item.value === policy)?.label ?? policy;

// ---------------------------------------------------------------------------------- Schedules

/** The API accepts five-field cron only; intervals and presets expand to cron. */
export const SCHEDULE_PRESETS = [
  { value: '*/15 * * * *', get label() { return t('auto.preset.every15'); } },
  { value: '0 * * * *', get label() { return t('auto.preset.hourly'); } },
  { value: '0 9 * * *', get label() { return t('auto.preset.daily'); } },
  { value: '0 9 * * 1-5', get label() { return t('auto.preset.weekdays'); } },
  { value: '0 9 * * 1', get label() { return t('auto.preset.mondays'); } },
  { value: '0 9 1 * *', get label() { return t('auto.preset.monthly'); } },
] as const;

export function intervalCron(minutes: number): string {
  const value = Math.round(minutes);
  if (!Number.isFinite(value) || value < 1) throw new Error(t('auto.err.intervalMin'));
  if (value < 60) {
    if (60 % value !== 0) throw new Error(t('auto.err.intervalHour', { value }));
    return `*/${value} * * * *`;
  }
  if (value === 60) return '0 * * * *';
  if (value === 1440) return '0 0 * * *';
  if (value % 60 === 0 && 24 % (value / 60) === 0) return `0 */${value / 60} * * *`;
  throw new Error(t('auto.err.intervalDay'));
}

/** A quick local check for the five-field shape; the API preview gives the real verdict. */
export function cronShapeError(expression: string): string | null {
  const fields = expression.trim().split(/\s+/).filter(Boolean);
  if (fields.length !== 5) return t('auto.err.cronShape', { count: fields.length });
  return null;
}

/** IANA zones for the time zone picker; a short list when the runtime cannot list them. */
export function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    const zones = intl.supportedValuesOf?.('timeZone');
    if (zones && zones.length) return zones.includes('UTC') ? zones : ['UTC', ...zones];
  } catch { /* fall through */ }
  return ['UTC', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles', 'Asia/Riyadh', 'Asia/Dubai', 'Asia/Tokyo', 'Australia/Sydney'];
}

export function browserTimeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
}

// ------------------------------------------------------------------------------------ Times

const spanOf = (minutes: number): string => {
  if (minutes < 60) return t('auto.time.minutes', { count: minutes });
  if (minutes < 48 * 60) return t('auto.time.hours', { count: Math.round(minutes / 60) });
  return t('auto.time.days', { count: Math.round(minutes / 1440) });
};

export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  const minutes = Math.round((at.getTime() - now.getTime()) / 60000);
  const size = Math.abs(minutes);
  if (size < 1) return minutes >= 0 ? t('auto.time.now') : t('auto.time.justNow');
  return t(minutes > 0 ? 'auto.time.in' : 'auto.time.ago', { span: spanOf(size) }, { isolate: false });
}

/** "Mon 13 Oct, 09:00" in the given zone (the viewer's by default) and the current language. */
export function formatTime(iso: string | null | undefined, timeZone?: string, locale?: string): string {
  if (!iso) return '';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  try {
    return new Intl.DateTimeFormat(locale ?? intlLocale(getLocale()), { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone }).format(at);
  } catch {
    return at.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
  }
}

export function nextRunLabel(nextRunAt: string | null | undefined, enabled: boolean, now: Date = new Date(), timeZone?: string): string {
  if (!enabled) return t('auto.time.paused');
  if (!nextRunAt) return t('auto.time.notScheduled');
  const at = new Date(nextRunAt);
  if (Number.isNaN(at.getTime())) return t('auto.time.notScheduled');
  if (at.getTime() <= now.getTime()) return t('auto.time.dueNow', { when: formatTime(nextRunAt, timeZone) }, { isolate: false });
  return t('auto.time.withRelative', { when: formatTime(nextRunAt, timeZone), relative: relativeTime(nextRunAt, now) }, { isolate: false });
}

export function runDuration(run: Pick<AutomationRun, 'created_at' | 'updated_at' | 'status'>): string {
  if (run.status === 'queued' || run.status === 'running' || run.status === 'paused') return '';
  const ms = new Date(run.updated_at).getTime() - new Date(run.created_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '';
  if (ms < 1000) return t('auto.time.underSecond');
  const seconds = Math.round(ms / 1000);
  if (seconds < 120) return t('auto.time.seconds', { count: seconds });
  const minutes = Math.round(seconds / 60);
  return minutes < 120 ? t('auto.time.minutes', { count: minutes }) : t('auto.time.hours', { count: Math.round(minutes / 60) });
}

// ---------------------------------------------------------------------------- Status words

export type Tone = 'success' | 'warning' | 'dangerTone' | 'neutral';
export function statusTone(status: string | null | undefined): Tone {
  if (status === 'completed' || status === 'active') return 'success';
  if (status === 'failed') return 'dangerTone';
  if (status === 'cancelled' || status === 'disabled' || !status) return 'neutral';
  return 'warning';
}
const STATUS_KEYS = ['active', 'disabled', 'queued', 'running', 'paused', 'completed', 'failed', 'cancelled'];
export const statusLabel = (status: string): string =>
  STATUS_KEYS.includes(status) ? t(`auto.status.${status}` as MessageKey) : status.replaceAll('_', ' ');
const TRIGGER_KEYS = ['manual', 'schedule', 'retry', 'event', 'webhook'];
export const triggerLabel = (trigger: string): string => TRIGGER_KEYS.includes(trigger) ? t(`auto.trigger.${trigger}` as MessageKey) : trigger;
const OUTCOME_KEYS = ['fired', 'skipped_overlap', 'workflow_paused', 'owner_inactive', 'workspace_archived'];
export const outcomeLabel = (outcome: string | null): string | null =>
  outcome ? (OUTCOME_KEYS.includes(outcome) ? t(`auto.outcome.${outcome}` as MessageKey) : outcome.replaceAll('_', ' ')) : null;

export function runProgress(run: Pick<AutomationRun, 'steps' | 'current_step' | 'status'>): string {
  const total = run.steps.length;
  const done = run.status === 'completed' ? total : run.steps.filter((step) => step.status === 'completed').length;
  return t('auto.runs.progress', { done, total });
}

// ---------------------------------------------------------------------------- Permissions

export interface Viewer { userId: string | null; role: string | null }
/** Owners and members manage automations; viewers only read (anum_api/authorization.py). */
export const canManage = (viewer: Viewer): boolean => viewer.role === null || viewer.role === 'owner' || viewer.role === 'member';
/** Members change their own automations and schedules; owners change any. */
export const canEdit = (viewer: Viewer, createdBy: string | null): boolean =>
  canManage(viewer) && (viewer.role === 'owner' || viewer.role === null || !createdBy || createdBy === viewer.userId);
export const manageHint = (): string => t('auto.manageHint');
export const editHint = (createdBy: string | null): string => t('auto.editHint', { user: createdBy ?? t('auto.itsCreator') });

// -------------------------------------------------------------------------------- Errors and paging

interface ErrorLike { status?: number; detail?: string | null; details?: { location: string | null; message: string }[]; message?: string }

/** A sentence for a failed automation request: the API's own wording, field problems first. */
export function automationError(error: unknown, fallback = t('auto.err.fallback')): string {
  const e = (typeof error === 'object' && error !== null ? error : {}) as ErrorLike;
  if (e.status === 422 && e.details && e.details.length) {
    return e.details.map((item) => item.message.replace(/^Value error, /, '')).filter((m, i, all) => all.indexOf(m) === i).join(' ');
  }
  if (e.status === 401) return t('common.signInExpired');
  if (e.status === 0 || (e.message && /Failed to fetch|NetworkError/i.test(e.message))) return t('auto.err.unreachable');
  if (e.detail) return e.detail;
  return e.message || fallback;
}

export function pageLabel(offset: number, count: number, total: number): string {
  if (total === 0 || count === 0) return t('auto.page.none');
  return t('auto.page.range', { from: offset + 1, to: offset + count, total });
}

/** Where a task created by a run's step lives, for a link to it. */
export function createdTaskId(step: Pick<AutomationRunStep, 'action' | 'output'>): string | null {
  const id = step.output?.task_id;
  return (step.action === 'task.create' || step.action === 'email.send') && typeof id === 'string' ? id : null;
}

/** Next fire times for the preview line, in the schedule's zone. */
export const nextTimes = (runs: string[], timeZone: string): string => joinList(runs.map((run) => formatTime(run, timeZone)));

// ------------------------------------------------------------------------------ Notifications

export interface AutomationAlert { id: string; kind: 'failed' | 'notice'; title: string; message: string; workflowId: string; runId: string }

/**
 * The in-app alert for an event, when it is addressed to this user: `automation_run.failed`
 * (the API only lists people whose "automation failed" notification preference is on) and the
 * `notify` step's `automation.notification`.
 */
export function automationAlert(event: { id: string; type: string; payload?: Record<string, unknown> }, userId: string | null): AutomationAlert | null {
  if (event.type !== 'automation_run.failed' && event.type !== 'automation.notification') return null;
  const payload = event.payload ?? {};
  const recipients = Array.isArray(payload.notify_user_ids) ? payload.notify_user_ids : [];
  if (!userId || !recipients.includes(userId)) return null;
  const workflowId = text(payload.workflow_id), runId = text(payload.run_id);
  if (!workflowId || !runId) return null;
  const name = text(payload.workflow_name) || t('auto.alert.anAutomation');
  if (event.type === 'automation_run.failed') {
    const step = text(payload.failed_step), error = text(payload.error) || t('auto.alert.stepFailed');
    return { id: event.id, kind: 'failed', title: t('auto.alert.failedTitle', { name }), message: step ? t('auto.alert.stepError', { step, error }) : error, workflowId, runId };
  }
  return { id: event.id, kind: 'notice', title: name, message: text(payload.message) || t('auto.alert.noticeDefault'), workflowId, runId };
}

// -------------------------------------------------------------------------------- Waiting steps

/** What a running step is waiting for, in words (null when it is not waiting). */
export function waitingLabel(step: Pick<AutomationRunStep, 'status' | 'waiting_for' | 'next_attempt_at' | 'attempt' | 'max_attempts'>): string | null {
  if (step.status !== 'running' || !step.waiting_for) return null;
  if (step.waiting_for === 'retry') {
    return t('auto.wait.retry', { when: step.next_attempt_at ? formatTime(step.next_attempt_at) : t('auto.time.notScheduled'), attempt: step.attempt + 1, max: step.max_attempts ?? step.attempt + 1 }, { isolate: false });
  }
  return t(`auto.wait.${step.waiting_for}` as MessageKey);
}

/** The run's status word, with "waiting for approval" etc. while a running step waits. */
export const runStatusLabel = (run: Pick<AutomationRun, 'status' | 'waiting_for'>): string =>
  run.status === 'running' && run.waiting_for ? t(`auto.runWait.${run.waiting_for}` as MessageKey) : statusLabel(run.status);

/** Where an approval a step waits for can be decided. */
export function waitingApprovalId(step: Pick<AutomationRunStep, 'waiting_for' | 'output'>): string | null {
  const id = step.output?.approval_id;
  return step.waiting_for === 'approval' && typeof id === 'string' ? id : null;
}

// ------------------------------------------------------------------------------------ Triggers

export type TriggerKind = 'event' | 'webhook';
export type ConditionOperator = 'equals' | 'not_equals' | 'contains' | 'starts_with' | 'exists';
export interface TriggerCondition { field: string; operator: ConditionOperator; value: string | null }
export interface TriggerEventInfo { type: string; label: string; description: string; fields: string[] }
export interface AutomationTrigger {
  id: string; workflow_id: string; name: string; kind: TriggerKind; event_type: string | null; conditions: TriggerCondition[];
  enabled: boolean; rate_limit_per_minute: number; replay_window_seconds: number; secret_hint: string | null; secret_rotated_at: string | null;
  last_fired_at: string | null; last_outcome: string | null; fire_count: number; created_by: string | null; created_at: string; updated_at: string;
  webhook_path: string | null;
}
export interface TriggerWithSecret { trigger: AutomationTrigger; secret: string | null }
export interface TriggerBody {
  workflow_id?: string; name: string; kind?: TriggerKind; event_type?: string | null; conditions: TriggerCondition[];
  rate_limit_per_minute: number; replay_window_seconds: number;
}

export const CONDITION_OPERATORS: { value: ConditionOperator; label: string }[] = [
  { value: 'equals', get label() { return t('auto.trig.op.equals'); } },
  { value: 'not_equals', get label() { return t('auto.trig.op.not_equals'); } },
  { value: 'contains', get label() { return t('auto.trig.op.contains'); } },
  { value: 'starts_with', get label() { return t('auto.trig.op.starts_with'); } },
  { value: 'exists', get label() { return t('auto.trig.op.exists'); } },
];
export const MAX_CONDITIONS = 10;

/** The event's name in the current language (the API's English label as a fallback). */
const EVENT_KEYS = ['agent_run.completed', 'agent_run.failed', 'approval.approved', 'approval.rejected', 'approval.expired', 'email.received', 'whatsapp.message_received', 'file.uploaded'];
export const eventLabel = (type: string | null, fallback?: string): string =>
  type && EVENT_KEYS.includes(type) ? t(`auto.event.${type.replace(/\./g, '_')}` as MessageKey) : fallback ?? type ?? '';

const TRIGGER_OUTCOME_KEYS = ['fired', 'rate_limited', 'workflow_paused', 'skipped_overlap', 'owner_inactive', 'workspace_archived', 'loop_skipped', 'disabled'];
export const triggerOutcomeLabel = (outcome: string | null): string | null =>
  outcome ? (TRIGGER_OUTCOME_KEYS.includes(outcome) ? t(`auto.trig.outcome.${outcome}` as MessageKey) : outcome.replaceAll('_', ' ')) : null;

export function conditionText(condition: TriggerCondition): string {
  const operator = CONDITION_OPERATORS.find((item) => item.value === condition.operator)?.label ?? condition.operator;
  return condition.operator === 'exists' ? t('auto.trig.condExists', { field: condition.field }) : t('auto.trig.cond', { field: condition.field, operator, value: condition.value ?? '' });
}

export interface ConditionDraft { key: string; field: string; operator: ConditionOperator; value: string }
export interface TriggerDraft { name: string; kind: TriggerKind; eventType: string; conditions: ConditionDraft[]; rateLimit: number; replayWindow: number }

export const newCondition = (field = ''): ConditionDraft => ({ key: nextKey(), field, operator: 'equals', value: '' });
export const emptyTriggerDraft = (kind: TriggerKind = 'event'): TriggerDraft =>
  ({ name: '', kind, eventType: 'email.received', conditions: [], rateLimit: 30, replayWindow: 300 });
export const triggerDraftFrom = (trigger: AutomationTrigger): TriggerDraft => ({
  name: trigger.name, kind: trigger.kind, eventType: trigger.event_type ?? 'email.received', rateLimit: trigger.rate_limit_per_minute, replayWindow: trigger.replay_window_seconds,
  conditions: trigger.conditions.map((c) => ({ key: nextKey(), field: c.field, operator: c.operator, value: c.value ?? '' })),
});

const FIELD_PATH = /^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){0,5}$/;
export type TriggerCheck = { ok: true; body: TriggerBody } | { ok: false; errors: FieldErrors; summary: string };

/** The API's trigger rules; error keys are `name`, `eventType`, `rateLimit`, `replayWindow` or `<condition key>.<field|value>`. */
export function checkTriggerDraft(draft: TriggerDraft, events: string[]): TriggerCheck {
  const errors: FieldErrors = {};
  if (!draft.name.trim()) errors.name = t('auto.trig.err.name');
  else if (draft.name.trim().length > NAME_MAX) errors.name = t('auto.err.tooLong', { max: NAME_MAX });
  if (draft.kind === 'event' && !events.includes(draft.eventType)) errors.eventType = t('auto.trig.err.event');
  if (!(draft.rateLimit >= 1 && draft.rateLimit <= 600)) errors.rateLimit = t('auto.trig.err.rate');
  if (draft.kind === 'webhook' && !(draft.replayWindow >= 30 && draft.replayWindow <= 3600)) errors.replayWindow = t('auto.trig.err.replay');
  if (draft.conditions.length > MAX_CONDITIONS) errors.conditions = t('auto.trig.err.tooMany', { max: MAX_CONDITIONS });
  draft.conditions.forEach((condition, index) => {
    if (!FIELD_PATH.test(condition.field.trim())) errors[`${condition.key}.field`] = t('auto.trig.err.field', { n: index + 1 });
    if (condition.operator !== 'exists' && !condition.value.trim()) errors[`${condition.key}.value`] = t('auto.trig.err.value', { n: index + 1 });
  });
  const problems = Object.values(errors);
  if (problems.length) return { ok: false, errors, summary: t('auto.err.summary', { count: problems.length, first: problems[0] }, { isolate: false }) };
  return {
    ok: true,
    body: {
      name: draft.name.trim(), kind: draft.kind, event_type: draft.kind === 'event' ? draft.eventType : null,
      conditions: draft.conditions.map((c) => ({ field: c.field.trim(), operator: c.operator, value: c.operator === 'exists' ? null : c.value.trim() })),
      rate_limit_per_minute: Math.round(draft.rateLimit), replay_window_seconds: Math.round(draft.replayWindow),
    },
  };
}

/** The address a sender POSTs to, on this API host. */
export const webhookUrl = (apiBase: string, path: string | null): string => path ? `${apiBase.replace(/\/$/, '')}${path}` : '';

/** A copy-paste example of a signed delivery (HMAC-SHA256 over "<timestamp>.<body>"). */
export const signingExample = (url: string): string => [
  'TS=$(date +%s); BODY=\'{"order":"A-1001"}\'',
  'SIG=$(printf "%s.%s" "$TS" "$BODY" | openssl dgst -sha256 -hmac "$ANUM_WEBHOOK_SECRET" -hex | sed \'s/^.* //\')',
  `curl -X POST '${url}' -H 'Content-Type: application/json' -H "X-Anum-Signature: t=$TS,v1=$SIG" -H "X-Anum-Delivery: order-A-1001" -d "$BODY"`,
].join('\n');

/** Owners take over another member's schedule or trigger (it then runs as them). */
export const canTakeOver = (viewer: Viewer, createdBy: string | null): boolean =>
  viewer.role === 'owner' && Boolean(createdBy) && createdBy !== viewer.userId;
