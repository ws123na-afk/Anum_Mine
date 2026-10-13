// Pure helpers behind the shared page layout (docs/design/anum360.md#page-layout): the summary
// band's figures, the split view's height, the default selection of a reading pane, and the
// counts each page shows in its band. No browser or Vite APIs, so `pnpm test:unit` runs them.
import type { ChatCard } from './chat.ts';

/** One figure of a summary band. A figure that failed to load says so; it never shows 0. */
export type FigureState = 'loading' | 'ready' | 'error';
export type FigureTone = 'danger' | 'warn' | 'ok' | 'accent' | '';
export interface Figure { id: string; label: string; state: FigureState; value?: string; hint?: string; tone?: FigureTone }

/** One request's state on a page: loading, its value, refused for the person's role, or failed. */
export type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied'; message: string } | { state: 'error'; message: string };

/** A ready figure, or the load's own state while it is loading or failed. */
export function figure(id: string, label: string, load: { state: FigureState | 'denied' }, ready: () => { value: string; hint?: string; tone?: FigureTone }): Figure {
  if (load.state !== 'ready') return { id, label, state: load.state === 'denied' ? 'error' : load.state };
  return { id, label, state: 'ready', ...ready() };
}

/**
 * Height for a pane group that fills the rest of the window: the window height less the group's
 * top edge and a bottom gutter, never below `min` (short windows scroll the page instead).
 */
export function fillHeight(viewport: number, top: number, { min = 480, gutter = 24 }: { min?: number; gutter?: number } = {}): number {
  if (!Number.isFinite(viewport) || !Number.isFinite(top)) return min;
  return Math.max(min, Math.floor(viewport - top - gutter));
}

/** The first unread item, else the first item: what a reading pane opens on by default. */
export function firstUnread<T>(items: readonly T[], unread: (item: T) => boolean): T | null {
  return items.find(unread) ?? items[0] ?? null;
}

// ---- Index state (GET /api/v1/retrieval/status) --------------------------------------------------

export interface IndexSourceLike { source_type: string; status: string }
export interface IndexCounts { indexed: number; failed: number; skipped: number; total: number }

/** Index state per source type ("memory", "file"), counted from the status' source list. */
export function indexCounts(sources: readonly IndexSourceLike[] | null | undefined, sourceType: string): IndexCounts {
  const counts: IndexCounts = { indexed: 0, failed: 0, skipped: 0, total: 0 };
  for (const source of Array.isArray(sources) ? sources : []) {
    if (source.source_type !== sourceType) continue;
    counts.total += 1;
    if (source.status === 'indexed') counts.indexed += 1;
    else if (source.status === 'failed') counts.failed += 1;
    else if (source.status === 'skipped') counts.skipped += 1;
  }
  return counts;
}

// ---- Documents ---------------------------------------------------------------------------------

export type FileTypeFilter = '' | 'text' | 'markdown' | 'data' | 'image' | 'pdf' | 'document' | 'archive' | 'video';
export type FileIndexFilter = '' | 'indexed' | 'pending' | 'failed' | 'skipped' | 'stale';
export const FILE_TYPE_FILTERS: readonly Exclude<FileTypeFilter, ''>[] = ['text', 'markdown', 'data', 'image', 'pdf', 'document', 'archive', 'video'];
export const FILE_INDEX_FILTERS: readonly Exclude<FileIndexFilter, ''>[] = ['indexed', 'pending', 'stale', 'failed', 'skipped'];

interface FileLike { kind?: string | null; index?: { status: string } | null }

/** Type and index-state filters over the files already loaded (the list API filters by name only). */
export function filterFiles<T extends FileLike>(items: readonly T[], filters: { type: FileTypeFilter; index: FileIndexFilter }): T[] {
  return items.filter((item) => (!filters.type || item.kind === filters.type) && (!filters.index || (item.index?.status ?? 'pending') === filters.index));
}

// ---- Knowledge base: memories by where they came from -----------------------------------------------

export type MemoryGroupId = 'chat' | 'task' | 'other';
export const MEMORY_GROUPS: readonly MemoryGroupId[] = ['chat', 'task', 'other'];

/** Memories in their source groups, in a fixed order, leaving out empty groups. */
export function groupBy<T>(items: readonly T[], groupOf: (item: T) => MemoryGroupId): { group: MemoryGroupId; items: T[] }[] {
  return MEMORY_GROUPS.map((group) => ({ group, items: items.filter((item) => groupOf(item) === group) })).filter((entry) => entry.items.length > 0);
}

// ---- Calls & Meetings: action items across meetings -----------------------------------------------

interface ActionLike { done: boolean; due_date?: string | null }
export interface ActionCounts { open: number; overdue: number; done: number }

/** The calendar day of `date` in the browser's time zone, as YYYY-MM-DD. */
export function localDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Open (not done), overdue (open with a due date before `today`) and done action items. */
export function actionItemCounts(meetings: readonly { action_items: readonly ActionLike[] }[], today: string): ActionCounts {
  const counts: ActionCounts = { open: 0, overdue: 0, done: 0 };
  for (const meeting of meetings) {
    for (const item of meeting.action_items) {
      if (item.done) { counts.done += 1; continue; }
      counts.open += 1;
      if (item.due_date && item.due_date < today) counts.overdue += 1;
    }
  }
  return counts;
}

// ---- Inbox: reply drafts waiting for someone ---------------------------------------------------

const WAITING = new Set(['draft', 'pending_approval', 'failed']);

/** Drafts still waiting for a person: not yet sent, approval pending, or failed to send. */
export function waitingDrafts<T extends { status: string }>(drafts: readonly T[]): T[] {
  return drafts.filter((draft) => WAITING.has(draft.status));
}

// ---- AI Chat: what this conversation used and produced --------------------------------------------

interface TurnLike { id: string; role: 'user' | 'assistant'; card?: ChatCard | null }
interface MemoryLike { id: string; content: string; created_at: string; provenance: { source_type: string; source_id: string | null } }

export interface ContextSource { kind: string; id: string | null; title: string; subtitle: string | null; url: string | null }
export interface ContextTask { taskId: string; summary: string; state: ChatCard['state'] }
export interface ContextApproval { id: string; action: string; status: string; risk: string; summary: string }
export interface ChatContext { questions: number; answers: number; sources: ContextSource[]; tasks: ContextTask[]; approvals: ContextApproval[] }

/**
 * The context rail of one conversation: the records its answers used (task card items, deduplicated
 * by kind and id), the tasks its questions ran as, and the approvals those tasks asked for.
 */
export function chatContext(turns: readonly TurnLike[]): ChatContext {
  const sources = new Map<string, ContextSource>();
  const tasks = new Map<string, ContextTask>();
  const approvals = new Map<string, ContextApproval>();
  let questions = 0, answers = 0;
  for (const turn of turns) {
    if (turn.role === 'user') questions += 1; else answers += 1;
    const card = turn.card;
    if (!card) continue;
    for (const item of card.items) {
      const key = `${item.kind}:${item.id ?? item.url ?? item.title}`;
      if (!sources.has(key)) sources.set(key, { kind: item.kind, id: item.id ?? null, title: item.title, subtitle: item.subtitle ?? null, url: item.url ?? null });
    }
    if (card.task_id) tasks.set(card.task_id, { taskId: card.task_id, summary: card.summary, state: card.state });
    if (card.approval) approvals.set(card.approval.id, { id: card.approval.id, action: card.approval.action, status: card.approval.status, risk: card.approval.risk_level, summary: card.summary });
  }
  return { questions, answers, sources: [...sources.values()], tasks: [...tasks.values()], approvals: [...approvals.values()] };
}

/** Memories the Assistant remembered from this conversation (source_type "conversation"), newest first. */
export function memoriesFromChat<T extends MemoryLike>(memories: readonly T[], conversationId: string | null): T[] {
  if (!conversationId) return [];
  return memories
    .filter((memory) => memory.provenance.source_type === 'conversation' && memory.provenance.source_id === conversationId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}
