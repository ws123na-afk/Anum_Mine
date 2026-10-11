// Pure helpers for the Tasks view: filtering, paging, stream state and rendering a model answer
// as plain blocks (never HTML), so untrusted model output can only ever become text nodes.
import type { AgentRun, AgentRunStep, Approval, DomainEvent, Task, TaskBulkResult, TaskStatus, TaskSummary } from '@anum/contracts';
import { CATALOGS, t, type MessageKey } from './i18n.ts';

export const TASK_PAGE_SIZE = 50;
export const RUN_PAGE_SIZE = 20;
export const EVENT_PAGE_SIZE = 50;
export const BULK_MAX = 100;
export const ACTIVE_STATUSES: readonly TaskStatus[] = ['created', 'queued', 'running', 'waiting_approval'];
export const TERMINAL_STATUSES: readonly TaskStatus[] = ['completed', 'failed', 'cancelled'];

export type StatusFilter = 'all' | 'active' | TaskStatus;
export type StreamState = 'connecting' | 'live' | 'offline';

export function isTerminal(status: TaskStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

/** Tasks matching a status filter and a case-insensitive search over title, instructions and id. */
export function filterTasks(tasks: readonly Task[], status: StatusFilter, query: string): Task[] {
  const q = query.trim().toLowerCase();
  return tasks.filter((t) => {
    if (status === 'active' ? !ACTIVE_STATUSES.includes(t.status) : status !== 'all' && t.status !== status) return false;
    return !q || t.title.toLowerCase().includes(q) || t.prompt.toLowerCase().includes(q) || t.id.toLowerCase().includes(q);
  });
}

/** The first page (refreshed on every event) followed by older pages, without duplicates; first-page rows win. */
export function mergeTaskPages(first: readonly Task[], older: readonly Task[]): Task[] {
  const seen = new Set(first.map((t) => t.id));
  return [...first, ...older.filter((t) => !seen.has(t.id) && (seen.add(t.id), true))];
}

/** Whether another page may exist: the API returns a full page when more rows follow. */
export function hasMorePages(lastPageLength: number, pageSize = TASK_PAGE_SIZE): boolean {
  return lastPageLength >= pageSize;
}

export function countByStatus(tasks: readonly Task[]): { active: number; completed: number; failed: number } {
  return {
    active: tasks.filter((t) => ACTIVE_STATUSES.includes(t.status)).length,
    completed: tasks.filter((t) => t.status === 'completed').length,
    failed: tasks.filter((t) => t.status === 'failed' || t.status === 'cancelled').length,
  };
}

export function streamBadge(state: StreamState): { label: string; tone: 'success' | 'warning' | 'dangerTone' } {
  if (state === 'live') return { label: t('tasks.stream.live'), tone: 'success' };
  if (state === 'connecting') return { label: t('tasks.stream.connecting'), tone: 'warning' };
  return { label: t('tasks.stream.offline'), tone: 'dangerTone' };
}

/** Approvals raised by this task, pending first, newest first within each group. */
export function approvalsForTask(approvals: readonly Approval[], taskId: string): Approval[] {
  return approvals
    .filter((a) => a.taskId === taskId)
    .sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending') || b.createdAt.localeCompare(a.createdAt));
}

/** The run to show for a task: a run fetched for it, else the run the page just started for it. */
export function runForTask(taskId: string | undefined, fetched: AgentRun | null, recent: AgentRun | null): AgentRun | null {
  if (!taskId) return null;
  if (fetched?.taskId === taskId) return fetched;
  return recent?.taskId === taskId ? recent : null;
}

/** The answer text: the run's result, else the summary of its `final` step. */
export function finalAnswer(run: AgentRun | null): string | null {
  if (!run) return null;
  const text = run.result?.trim() || [...run.steps].reverse().find((s) => s.type === 'final')?.summary.trim();
  return text || null;
}

export type Inline = { kind: 'text' | 'strong' | 'em' | 'code'; text: string };
export type AnswerBlock =
  | { kind: 'heading'; level: 1 | 2 | 3; inline: Inline[] }
  | { kind: 'paragraph'; inline: Inline[] }
  | { kind: 'list'; ordered: boolean; items: Inline[][] }
  | { kind: 'code'; text: string };

/** Split `**bold**`, `*em*`/`_em_` and `` `code` `` spans; anything else stays literal text. */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\b_[^_\n]+_\b)/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    const index = m.index ?? 0;
    if (index > last) out.push({ kind: 'text', text: text.slice(last, index) });
    const token = m[0];
    if (m[1]) out.push({ kind: 'code', text: token.slice(1, -1) });
    else if (m[2]) out.push({ kind: 'strong', text: token.slice(2, -2) });
    else out.push({ kind: 'em', text: token.slice(1, -1) });
    last = index + token.length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

const BULLET = /^\s*[-*+•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{1,6})\s+(.*)$/;

/** A Markdown-ish reading of model output: headings, paragraphs, bullet/numbered lists and code fences. */
export function parseAnswer(text: string): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: 'paragraph', inline: parseInline(paragraph.join(' ')) });
    if (list) blocks.push({ kind: 'list', ordered: list.ordered, items: list.items.map(parseInline) });
    paragraph = [];
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trimStart().startsWith('```')) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].trimStart().startsWith('```'); i++) code.push(lines[i]);
      blocks.push({ kind: 'code', text: code.join('\n') });
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: 'heading', level: Math.min(heading[1].length, 3) as 1 | 2 | 3, inline: parseInline(heading[2].trim()) });
      continue;
    }
    const bullet = BULLET.exec(line), numbered = bullet ? null : NUMBERED.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      const ordered = Boolean(numbered);
      if (paragraph.length || (list && list.ordered !== ordered)) flush();
      list ??= { ordered, items: [] };
      list.items.push(item[1].trim());
      continue;
    }
    if (list && /^\s+\S/.test(line)) { list.items[list.items.length - 1] += ` ${line.trim()}`; continue; }
    if (list) flush();
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

/** A task title from its instructions: the first line, at most 80 characters (the API allows 160). */
export function taskTitleFrom(prompt: string): string {
  const line = prompt.trim().split(/\r?\n/, 1)[0]?.trim() ?? '';
  if (!line) return 'Web task';
  return line.length > 80 ? `${line.slice(0, 79).trimEnd()}…` : line;
}

// ---- Server-side list query (GET /api/v1/tasks, docs/tasks.md) ------------------------------

/** The list's filters. `creator` is '' (anyone), 'me' or a user id; dates are `YYYY-MM-DD` (local), both inclusive. */
export interface TaskQuery { status: StatusFilter; q: string; creator: string; from: string; to: string; archived: boolean }
export const EMPTY_QUERY: TaskQuery = { status: 'all', q: '', creator: '', from: '', to: '', archived: false };

export function isDefaultQuery(query: TaskQuery): boolean {
  return query.status === 'all' && !query.q.trim() && !query.creator && !query.from && !query.to && !query.archived;
}

/** How many filters differ from the default (for the "Clear filters" label). */
export function activeFilterCount(query: TaskQuery): number {
  return [query.status !== 'all', Boolean(query.q.trim()), Boolean(query.creator), Boolean(query.from || query.to), query.archived].filter(Boolean).length;
}

export function statusesFor(filter: StatusFilter): TaskStatus[] {
  if (filter === 'all') return [];
  return filter === 'active' ? [...ACTIVE_STATUSES] : [filter];
}

const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Local midnight of a `YYYY-MM-DD` day as an ISO instant; `nextDay` gives the exclusive end of that day. */
export function dayBoundary(day: string, nextDay = false): string | null {
  const match = DAY.exec(day);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + (nextDay ? 1 : 0));
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** Why a date range cannot be searched, or null. */
export function dateRangeError(from: string, to: string): string | null {
  if (from && to && from > to) return t('tasks.filter.dateOrder');
  return null;
}

/** Query string for `GET /api/v1/tasks` and `GET /api/v1/tasks/summary` (no leading `?`). */
export function taskQueryString(query: TaskQuery, page: { cursor?: string | null; limit?: number; withStatus?: boolean } = {}): string {
  const params = new URLSearchParams();
  if (page.limit) params.set('limit', String(page.limit));
  if (page.cursor) params.set('cursor', page.cursor);
  if (page.withStatus !== false) for (const status of statusesFor(query.status)) params.append('status', status);
  const q = query.q.trim();
  if (q) params.set('q', q.slice(0, 200));
  if (query.creator) params.set('created_by', query.creator);
  const after = query.from ? dayBoundary(query.from) : null;
  const before = query.to ? dayBoundary(query.to, true) : null;
  if (after) params.set('created_after', after);
  if (before) params.set('created_before', before);
  if (query.archived) params.set('archived', 'true');
  return params.toString();
}

/** Counts for the metrics: from the server summary when loaded, else from the loaded tasks. */
export function summaryCounts(summary: TaskSummary | null, loaded: readonly Task[]): { active: number; waiting: number; completed: number; failed: number; total: number } {
  if (!summary) {
    const c = countByStatus(loaded);
    return { active: c.active, waiting: loaded.filter((t) => t.status === 'waiting_approval').length, completed: c.completed, failed: c.failed, total: loaded.length };
  }
  const n = (status: TaskStatus) => summary.byStatus[status] ?? 0;
  return {
    active: ACTIVE_STATUSES.reduce((sum, status) => sum + n(status), 0),
    waiting: n('waiting_approval'),
    completed: n('completed'),
    failed: n('failed') + n('cancelled'),
    total: summary.total,
  };
}

// ---- Permissions and actions ----------------------------------------------------------------

/** The caller's workspace role; null while unknown (the API still decides and explains a 403). */
export type WorkspaceRole = 'owner' | 'member' | 'viewer' | null;

export function roleFrom(membership: { role: string; active?: boolean } | null | undefined): WorkspaceRole {
  if (!membership || membership.active === false) return null;
  return membership.role === 'owner' || membership.role === 'member' || membership.role === 'viewer' ? membership.role : null;
}

/** Who may do an action the caller's role does not allow (shown next to the disabled control). */
export function permissionHint(need: 'run' | 'cancel' | 'manage'): string {
  return t(need === 'run' ? 'tasks.hint.run' : need === 'cancel' ? 'tasks.hint.cancel' : 'tasks.hint.manage');
}

/** A run stranded at an executable checkpoint (a worker stopped mid-run): the API can resume it. */
export function isStranded(run: AgentRun | null): boolean {
  return Boolean(run && (run.status === 'running' || run.status === 'queued') && run.phase === 'tool_ready');
}

export interface TaskActions {
  run: boolean; retry: boolean; resume: boolean; cancel: boolean;
  edit: boolean; editInstructions: boolean; archive: boolean; unarchive: boolean;
}

/** Which actions the task's state allows (the role decides whether they are enabled). */
export function taskActions(task: Task, latestRun: AgentRun | null): TaskActions {
  const archived = Boolean(task.archivedAt);
  const terminal = isTerminal(task.status);
  return {
    run: !archived && task.status === 'created' && !latestRun,
    retry: !archived && (task.status === 'failed' || task.status === 'cancelled'),
    resume: !archived && isStranded(latestRun),
    cancel: !archived && !terminal,
    edit: !archived,
    editInstructions: !archived && task.status === 'created' && !latestRun,
    archive: !archived && (terminal || task.status === 'created'),
    unarchive: archived,
  };
}

/** Whether the role allows an action; an unknown role allows it (the API refuses with a reason). */
export function roleAllows(role: WorkspaceRole, need: 'run' | 'cancel' | 'manage', task: Task | null, userId: string | null): boolean {
  if (role === null) return true;
  if (need === 'cancel') return role === 'owner';
  if (need === 'run') return role === 'owner' || role === 'member';
  return role === 'owner' || (role === 'member' && Boolean(task?.createdBy) && task?.createdBy === userId);
}

// ---- Bulk actions ---------------------------------------------------------------------------

const BULK_DONE: Record<TaskBulkResult['action'], MessageKey> = { archive: 'tasks.bulk.done.archive', unarchive: 'tasks.bulk.done.unarchive', cancel: 'tasks.bulk.done.cancel' };
const BULK_FAILED: Record<TaskBulkResult['action'], MessageKey> = { archive: 'tasks.bulk.failed.archive', unarchive: 'tasks.bulk.failed.unarchive', cancel: 'tasks.bulk.failed.cancel' };

/** One sentence for a bulk result, naming why the first failure failed. */
export function bulkMessage(result: TaskBulkResult): string {
  const done = t(BULK_DONE[result.action], { count: result.succeeded });
  if (!result.failed) return t('tasks.bulk.allDone', { done }, { isolate: false });
  const reason = result.results.find((r) => !r.ok)?.message ?? t('tasks.bulk.refused');
  const failed = t(BULK_FAILED[result.action], { count: result.failed, reason });
  return result.succeeded ? t('tasks.bulk.mixed', { done, failed }, { isolate: false }) : failed;
}

/** Selected ids that are still listed (a refresh can drop rows). */
export function pruneSelection(selected: ReadonlySet<string>, listed: readonly Task[]): Set<string> {
  const ids = new Set(listed.map((t) => t.id));
  return new Set([...selected].filter((id) => ids.has(id)));
}

// ---- Run history, steps and events ----------------------------------------------------------

/** Rows of `first` (the refreshed newest page) followed by older rows not already present. */
export function mergeById<T extends { id: string }>(first: readonly T[], older: readonly T[]): T[] {
  const seen = new Set(first.map((x) => x.id));
  return [...first, ...older.filter((x) => !seen.has(x.id) && (seen.add(x.id), true))];
}

/**
 * The run to show: the chosen one when listed; else the run the page just started when the list
 * does not have it yet (or could not load); else the newest listed run.
 */
export function selectRun(runs: readonly AgentRun[], chosenId: string | null, taskId: string | undefined, recent: AgentRun | null): AgentRun | null {
  if (!taskId) return null;
  const listed = runs.filter((r) => r.taskId === taskId);
  const chosen = chosenId ? listed.find((r) => r.id === chosenId) : undefined;
  if (chosen) return chosen;
  const fresh = recent?.taskId === taskId ? recent : null;
  if (fresh && !listed.some((r) => r.id === fresh.id) && (!listed.length || (fresh.createdAt ?? '') >= (listed[0].createdAt ?? ''))) return fresh;
  return listed[0] ?? null;
}

/** "Run 3 (latest)" style label: runs are listed newest first and numbered from the oldest. */
export function runLabel(index: number, total: number): string {
  const number = total - index;
  return t(index === 0 ? 'tasks.runs.latest' : 'tasks.runs.label', { number });
}

const STEP_FACT_LABELS: Record<string, MessageKey> = {
  tool: 'tasks.fact.tool', risk_level: 'tasks.fact.risk', policy_outcome: 'tasks.fact.policy', approval_id: 'tasks.fact.approval',
  status: 'tasks.fact.outcome', workflow_id: 'tasks.fact.workflow', truncated: 'tasks.fact.shortened',
};

/** Readable facts from a step's metadata (tool, risk, policy outcome, approval, token use); never raw objects. */
export function stepFacts(step: AgentRunStep): { label: string; value: string }[] {
  const meta = step.metadata ?? {};
  const facts: { label: string; value: string }[] = [];
  for (const [key, labelKey] of Object.entries(STEP_FACT_LABELS)) {
    const value = meta[key], label = t(labelKey);
    if (typeof value === 'string' && value) facts.push({ label, value: key.endsWith('_id') || key === 'tool' ? value : value.replaceAll('_', ' ') });
    else if (value === true) facts.push({ label, value: t('tasks.fact.yes') });
  }
  const usage = meta.usage;
  if (usage && typeof usage === 'object') {
    const u = usage as Record<string, unknown>;
    const input = Number(u.input_tokens ?? u.prompt_tokens), output = Number(u.output_tokens ?? u.completion_tokens);
    if (Number.isFinite(input) && Number.isFinite(output)) facts.push({ label: t('tasks.fact.tokens'), value: t('tasks.fact.tokensValue', { input, output }) });
  }
  const rules = meta.governance_rules;
  if (Array.isArray(rules) && rules.length) facts.push({ label: t('tasks.fact.rules'), value: rules.map(String).join(', ') });
  return facts;
}

/** A readable event name (`tasks.event.<type>` in the catalogs); an unknown type still reads. */
export function eventLabel(event: Pick<DomainEvent, 'type'>): string {
  const key = `tasks.event.${event.type}`;
  return key in CATALOGS.en ? t(key as MessageKey) : event.type.replaceAll('_', ' ').replaceAll('.', ': ');
}

export function eventTone(event: Pick<DomainEvent, 'type'>): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (/(completed|approved)$/.test(event.type)) return 'success';
  if (/(failed|rejected|expired|cancelled)$/.test(event.type)) return 'dangerTone';
  if (/(requested|waiting_approval|partially_approved|retried)$/.test(event.type)) return 'warning';
  return 'neutral';
}
