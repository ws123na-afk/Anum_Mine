// Home, the owner's overview (docs/home.md#the-web-home): the shapes of /api/v1/home and the pure
// rules the page uses (greeting, what opens where, budget use, task counts, paging). Pure, so
// `pnpm --filter @anum/web test:unit` covers it without a browser.
import type { Approval, ModelBudgetOverview, Task } from '@anum/contracts';
import type { View } from './shell.ts';

export type SourceState = 'ok' | 'not_installed' | 'not_permitted' | 'not_connected' | 'unavailable';
export interface HomeTarget { feature: string; id?: string | null }
export interface SourceStatus { name: string; status: SourceState; detail?: string | null }
export interface FocusItem {
  id: string; source: string; kind: string; title: string; detail?: string | null; count?: number | null; progress?: number | null;
  severity: 'high' | 'medium' | 'low'; at?: string | null; target: HomeTarget;
}
export interface ActivityItem { id: string; kind: string; title: string; status: string; at: string; target: HomeTarget }
export interface HomeSummary {
  generated_at: string;
  profile: { display_name: string | null };
  focus: FocusItem[];
  counts: Record<string, number>;
  progress: { completed: number; open: number } | null;
  activity: ActivityItem[];
  sources: SourceStatus[];
}
export interface InsightAction { type: 'open' | 'command'; verb: string; target?: HomeTarget | null; command?: string | null }
export interface Insight {
  key: string; source: string; kind: string; severity: 'high' | 'medium' | 'low'; title: string; finding: string; question: string;
  action: InsightAction; created_at?: string | null; state: 'new' | 'snoozed' | 'dismissed';
}
export interface InsightList { items: Insight[]; hidden: number; sources: SourceStatus[] }
export type PinKind = 'link' | 'workspace' | 'feature';
export interface Pin { id: string; kind: PinKind; label: string; target: string; icon?: string | null; position: number }
export interface CalendarEvent {
  id: string; title: string; starts_at: string; ends_at: string; status: string; kind: string; location?: string | null; conference_url?: string | null;
}

/** Feature keys of docs/home.md#targets and the web view that shows each (missing: the web has no such view). */
export const FEATURE_VIEWS: Readonly<Record<string, View>> = {
  command: 'command', chat: 'assistant', agents: 'agents', voice: 'voice', automations: 'automation', tasks: 'tasks', calendar: 'meetings',
  reports: 'reports', documents: 'files', integrations: 'integrations', knowledge: 'knowledge', approvals: 'approvals', email: 'inbox',
  whatsapp: 'whatsapp', autonomy: 'autonomy', settings: 'settings', insights: 'home', commerce: 'commerce', team: 'team', favorites: 'favorites',
  tyres: 'tyre', marketing: 'marketing',
};
export const viewForFeature = (feature: string): View | null => FEATURE_VIEWS[feature] ?? null;
/** Features a person can pin on the web (each opens a view), in the order the picker lists them. */
export const PINNABLE_FEATURES: readonly string[] = Object.keys(FEATURE_VIEWS).filter((key) => key !== 'insights');

/** Quick links into each area, in the order Home shows them. */
export const QUICK_LINKS: readonly View[] = ['command', 'inbox', 'whatsapp', 'tasks', 'approvals', 'meetings', 'automation', 'reports', 'files', 'agents', 'voice', 'assistant'];

export type DayPart = 'morning' | 'afternoon' | 'evening';
export function dayPart(date: Date): DayPart {
  const hour = date.getHours();
  return hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
}

/** The name Home greets by: the one saved on Home, else the sign-in name; null greets without a name. */
export function greetingName(profileName: string | null | undefined, signInName: string | null | undefined): string | null {
  const name = (profileName ?? '').trim() || (signInName ?? '').trim();
  return name ? name.split(/\s+/)[0] : null;
}

/** Local midnight to the next local midnight, as ISO strings for `GET /calendar/events`. */
export function todayRange(now: Date): { start: string; end: string } {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return { start: start.toISOString(), end: end.toISOString() };
}

/** Today's events that still matter: not cancelled, earliest first, the ones still ahead or in progress before finished ones. */
export function todaysMeetings(events: readonly CalendarEvent[], now: Date): CalendarEvent[] {
  const live = events.filter((event) => event.status !== 'cancelled');
  const ahead = live.filter((event) => new Date(event.ends_at) > now).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  const past = live.filter((event) => new Date(event.ends_at) <= now).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  return [...ahead, ...past];
}

export interface TaskGlance { running: number; waiting: number; failed: number; queued: number }
/** Unarchived tasks by what they need: running, waiting for approval, failed (not retried), queued or not started. */
export function taskGlance(tasks: readonly Task[]): TaskGlance {
  const live = tasks.filter((task) => !task.archivedAt);
  const count = (statuses: readonly string[]) => live.filter((task) => statuses.includes(task.status)).length;
  return { running: count(['running']), waiting: count(['waiting_approval']), failed: count(['failed']), queued: count(['queued', 'created']) };
}

/** Tasks that need a look today: failed first, then waiting, running, queued; newest change first within each. */
export function tasksNeedingAttention(tasks: readonly Task[]): Task[] {
  const rank: Record<string, number> = { failed: 0, waiting_approval: 1, running: 2, queued: 3, created: 4 };
  return tasks
    .filter((task) => !task.archivedAt && task.status in rank)
    .sort((a, b) => rank[a.status] - rank[b.status] || b.updatedAt.localeCompare(a.updatedAt));
}

export const pendingApprovals = (approvals: readonly Approval[], now = new Date()): Approval[] =>
  approvals.filter((a) => a.status === 'pending' && (!a.expiresAt || new Date(a.expiresAt) > now)).sort((a, b) => a.createdAt.localeCompare(b.createdAt));

export interface BudgetUse { percent: number | null; costUsd: number; tokens: number; limitLabel: 'cost' | 'tokens' | null; exceeded: boolean; scope: 'workspace' | 'tenant' }
/**
 * The budget closest to its limit this month (the workspace's or the organization's), as a share of
 * the limit; `percent` is null when no limit is set (usage is still shown).
 */
export function budgetUse(overview: ModelBudgetOverview): BudgetUse {
  const candidates = (['workspace', 'tenant'] as const).map((scope) => {
    const view = overview[scope];
    const cost = view.budget?.monthlyCostLimitUsd ?? null, tokens = view.budget?.monthlyTokenLimit ?? null;
    const shares: { share: number; label: 'cost' | 'tokens' }[] = [];
    if (cost !== null && cost > 0) shares.push({ share: view.usage.estimatedCostUsd / cost, label: 'cost' });
    if (tokens !== null && tokens > 0) shares.push({ share: view.totalTokens / tokens, label: 'tokens' });
    const top = shares.sort((a, b) => b.share - a.share)[0] ?? null;
    return { scope, view, top };
  });
  const limited = candidates.filter((c) => c.top !== null).sort((a, b) => (b.top!.share) - (a.top!.share))[0];
  const pick = limited ?? candidates[0];
  return {
    percent: pick.top ? Math.round(pick.top.share * 100) : null,
    costUsd: pick.view.usage.estimatedCostUsd,
    tokens: pick.view.totalTokens,
    limitLabel: pick.top?.label ?? null,
    exceeded: overview.workspace.exceeded || overview.tenant.exceeded,
    scope: pick.scope,
  };
}

/** A page of a list shown `size` at a time: the visible items and whether more remain. */
export function page<T>(items: readonly T[], shown: number): { items: T[]; more: number } {
  return { items: items.slice(0, shown), more: Math.max(0, items.length - shown) };
}

/** The status a Home part reports, so the page can say why it is empty (docs/home.md#sources). */
export const sourceState = (sources: readonly SourceStatus[], name: string): SourceState | null => sources.find((s) => s.name === name)?.status ?? null;

/** Moves a pin one place; returns the new id order, or null at an end. */
export function movePin(ids: readonly string[], id: string, by: -1 | 1): string[] | null {
  const from = ids.indexOf(id), to = from + by;
  if (from < 0 || to < 0 || to >= ids.length) return null;
  const next = [...ids];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

/** A link pin must be https without credentials (the API refuses anything else). */
export function validLink(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return url.protocol === 'https:' && Boolean(url.hostname) && !url.username && !url.password;
  } catch {
    return false;
  }
}
