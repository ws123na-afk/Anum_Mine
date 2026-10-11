// Pure helpers for the Tasks view: filtering, paging, stream state and rendering a model answer
// as plain blocks (never HTML), so untrusted model output can only ever become text nodes.
import type { AgentRun, Approval, Task, TaskStatus } from '@anum/contracts';
import { t } from './i18n.ts';

export const TASK_PAGE_SIZE = 50;
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
