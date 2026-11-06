// My tasks and Team tasks on the Tasks page (docs/tasks.md#my-tasks-and-team-tasks): work items
// (docs/team-work.md) grouped the way the owner's mockup groups them. Pure, so it is unit tested.
import type { WorkItem, WorkPriority } from '@anum/contracts';
import { dueState } from './team.ts';

export type WorkGroup = 'overdue' | 'today' | 'in_progress' | 'blocked' | 'todo' | 'done_week';
export const WORK_GROUPS: readonly WorkGroup[] = ['overdue', 'today', 'in_progress', 'blocked', 'todo', 'done_week'];
const PRIORITY_RANK: Record<WorkPriority, number> = { urgent: 0, high: 1, normal: 2, low: 3 };

/** Monday 00:00 (local) of the week holding `now`: the start of "completed this week". */
export function weekStart(now: Date): Date {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return new Date(day.getFullYear(), day.getMonth(), day.getDate() - ((day.getDay() + 6) % 7));
}

/** The group an item belongs in: an overdue or due-today item counts there whatever its open status. */
export function workGroup(item: Pick<WorkItem, 'status' | 'due_at' | 'completed_at' | 'updated_at'>, now: Date): WorkGroup | null {
  if (item.status === 'done') return Date.parse(item.completed_at ?? item.updated_at) >= weekStart(now).getTime() ? 'done_week' : null;
  if (item.status === 'cancelled') return null;
  const due = dueState(item, now);
  if (due === 'overdue') return 'overdue';
  if (due === 'today') return 'today';
  return item.status;
}

/** Items in each group: overdue and due today by due time, the rest by priority then due date, done newest first. */
export function groupWork(items: readonly WorkItem[], now: Date, priority: WorkPriority | '' = ''): Record<WorkGroup, WorkItem[]> {
  const groups = Object.fromEntries(WORK_GROUPS.map((g) => [g, [] as WorkItem[]])) as Record<WorkGroup, WorkItem[]>;
  const seen = new Set<string>();
  for (const item of items) {
    if (seen.has(item.id) || (priority && item.priority !== priority)) continue;
    seen.add(item.id);
    const group = workGroup(item, now);
    if (group) groups[group].push(item);
  }
  const due = (a: WorkItem) => (a.due_at ? Date.parse(a.due_at) : Infinity);
  for (const g of ['overdue', 'today'] as const) groups[g].sort((a, b) => due(a) - due(b));
  for (const g of ['in_progress', 'blocked', 'todo'] as const) groups[g].sort((a, b) => PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] || due(a) - due(b));
  groups.done_week.sort((a, b) => (b.completed_at ?? b.updated_at).localeCompare(a.completed_at ?? a.updated_at));
  return groups;
}

/** How long until (or since) the due time, for the SLA column: minutes, hours or days, signed. */
export function dueIn(dueAt: string, now: Date): { unit: 'minutes' | 'hours' | 'days'; count: number; late: boolean } {
  const diff = Date.parse(dueAt) - now.getTime(), late = diff < 0, minutes = Math.max(1, Math.round(Math.abs(diff) / 60_000));
  if (minutes < 60) return { unit: 'minutes', count: minutes, late };
  if (minutes < 48 * 60) return { unit: 'hours', count: Math.round(minutes / 60), late };
  return { unit: 'days', count: Math.round(minutes / 1440), late };
}
