// Setup plan page helpers (docs/setup-plan.md): grouping, filters, the rollout track and the
// result summary. Pure, so `pnpm --filter @anum/web test:unit` covers them without a browser.
import type {
  PilotCheck, PilotCheckStatus, PilotChecks, SetupChecklist, SetupChecklistItem, SetupChecklistState, SetupPlanAction, SetupPlanApplyRequest, SetupPlanApplyResult, SetupPlanItem, SetupPlanLink, SetupPlanOptions, SetupPlanOutcome, SetupPlanPhase, SetupPlanPreview, SetupPlanSection, SetupPlanWeek,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export type {
  PilotCheck, PilotCheckStatus, PilotChecks, SetupChecklist, SetupChecklistItem, SetupChecklistState, SetupPlanAction, SetupPlanApplyResult, SetupPlanItem, SetupPlanOptions, SetupPlanOutcome, SetupPlanPhase, SetupPlanPreview, SetupPlanSection, SetupPlanSummary,
} from '@anum/contracts';

/** Sections in the order the plan is applied (dependencies first), then what stays for people. */
export const SECTION_ORDER: readonly SetupPlanSection[] = [
  'workspaces', 'governance', 'knowledge', 'budget', 'skills', 'agents', 'controls', 'offices', 'business_agents', 'assistants', 'commands', 'automations', 'home',
  'manual', 'unsupported',
];

/** The page's defaults: offices on (a seat only where an office exists), the two wider choices off. */
export const DEFAULT_OPTIONS: SetupPlanOptions = { business_workspaces: false, run_automations_as_agents: false, seat_in_offices: true };

const sameOptions = (a: SetupPlanOptions, b: SetupPlanOptions) =>
  a.business_workspaces === b.business_workspaces && a.run_automations_as_agents === b.run_automations_as_agents && a.seat_in_offices === b.seat_in_offices;

/** Actions that change something when the plan is applied. */
export const CHANGING: ReadonlySet<SetupPlanAction> = new Set(['create', 'update']);

export type Tone = 'success' | 'warning' | 'neutral' | 'dangerTone';

export function actionTone(action: SetupPlanAction): Tone {
  if (action === 'create' || action === 'update') return 'success';
  if (action === 'skipped' || action === 'kept' || action === 'manual') return 'warning';
  if (action === 'unsupported') return 'dangerTone';
  return 'neutral';
}

export function outcomeTone(outcome: SetupPlanOutcome): Tone {
  if (outcome === 'created' || outcome === 'updated') return 'success';
  if (outcome === 'failed') return 'dangerTone';
  if (outcome === 'skipped' || outcome === 'kept' || outcome === 'busy') return 'warning';
  return 'neutral';
}

export interface ItemFilter { section: SetupPlanSection | 'all'; query: string; changesOnly: boolean }

const fold = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();

export function filterItems(items: readonly SetupPlanItem[], filter: ItemFilter): SetupPlanItem[] {
  const words = fold(filter.query).split(/\s+/).filter(Boolean);
  return items.filter((item) =>
    (filter.section === 'all' || item.section === filter.section)
    && (!filter.changesOnly || CHANGING.has(item.action) || item.outcome === 'failed')
    && words.every((word) => fold(`${item.name} ${item.reason} ${item.facts.join(' ')}`).includes(word)));
}

export function groupBySection(items: readonly SetupPlanItem[]): { section: SetupPlanSection; items: SetupPlanItem[] }[] {
  return SECTION_ORDER.map((section) => ({ section, items: items.filter((item) => item.section === section) })).filter((group) => group.items.length > 0);
}

/** Whether the shown preview was built with the choices on screen (phase, "Replace my changes" and the options). */
export const previewMatches = (
  preview: Pick<SetupPlanPreview, 'phase' | 'overwrite_edited'> & { options?: SetupPlanOptions },
  phase: SetupPlanPhase, overwrite: boolean, options: SetupPlanOptions = DEFAULT_OPTIONS,
): boolean => preview.phase === phase && preview.overwrite_edited === overwrite && sameOptions(preview.options ?? DEFAULT_OPTIONS, options);

/** The apply request for a preview: the same phase, overwrite and options the owner reviewed, and its plan version. */
export const applyRequestFor = (
  preview: Pick<SetupPlanPreview, 'phase' | 'overwrite_edited'> & { options?: SetupPlanOptions; plan: { version: number } },
): SetupPlanApplyRequest => ({
  phase: preview.phase, overwrite_edited: preview.overwrite_edited, plan_version: preview.plan.version, ...(preview.options ?? DEFAULT_OPTIONS),
});

/** The preview query for the choices on screen. */
export function previewQuery(phase: SetupPlanPhase, overwrite: boolean, options: SetupPlanOptions): string {
  const query = new URLSearchParams({
    phase, overwrite_edited: String(overwrite), business_workspaces: String(options.business_workspaces),
    run_automations_as_agents: String(options.run_automations_as_agents), seat_in_offices: String(options.seat_in_offices),
  });
  return query.toString();
}

/** How many items applying the plan would create, update, or link (an existing object with the plan's name is recorded, not changed). */
export const changeCount = (preview: Pick<SetupPlanPreview, 'items'>): number =>
  preview.items.filter((item) => CHANGING.has(item.action) || item.reason_code === 'found_by_name' || item.reason_code === 'resume_stopped_apply').length;

export interface RolloutWeek { week: number; title: string; pilot: boolean; names: string[] }

/** The plan's weeks, each with the agents and assistants that start in it. Week 1 is the pilot. */
export function rolloutWeeks(weeks: readonly SetupPlanWeek[], items: readonly SetupPlanItem[]): RolloutWeek[] {
  return [...weeks].sort((a, b) => a.week - b.week).map((week) => ({
    week: week.week,
    title: week.title,
    pilot: week.week === 1,
    names: items.filter((item) => (item.section === 'agents' || item.section === 'assistants') && item.week === week.week).map((item) => item.name),
  }));
}

/** Outcome counts of an apply, in a fixed order, without zeros. */
export function outcomeSummary(result: Pick<SetupPlanApplyResult, 'outcomes'>): { outcome: SetupPlanOutcome; count: number }[] {
  const order: SetupPlanOutcome[] = ['created', 'updated', 'present', 'kept', 'skipped', 'busy', 'failed', 'not_applied'];
  return order.map((outcome) => ({ outcome, count: result.outcomes[outcome] ?? 0 })).filter((row) => row.count > 0);
}

export const failedItems = (result: Pick<SetupPlanApplyResult, 'items'>): SetupPlanItem[] => result.items.filter((item) => item.outcome === 'failed');

/** Reasons whose wording is fixed are shown in the reader's language; the others are the API's own sentence. */
const LOCALIZED_REASONS = new Set([
  'needs_org_admin', 'needs_owner', 'not_in_phase', 'edited_by_owner', 'removed_by_owner', 'found_by_name', 'already_applied', 'busy', 'recreate', 'set_by_someone',
  'resume_stopped_apply', 'already_set', 'missing_dependency', 'no_offices', 'will_run_as_agent', 'workspace_missing',
]);

export function reasonText(item: Pick<SetupPlanItem, 'reason_code' | 'reason'>): string {
  return LOCALIZED_REASONS.has(item.reason_code) ? t(`plan.reason.${item.reason_code}` as MessageKey) : item.reason;
}

/** The app address of an item's page (a hash route of the shell). */
export function itemHref(link: SetupPlanLink | null | undefined): string | null {
  if (!link) return null;
  return link.view === 'knowledge' && link.id ? `#knowledge/${encodeURIComponent(link.id)}` : `#${link.view}`;
}

export const sectionLabel = (section: SetupPlanSection): string => t(`plan.section.${section}` as MessageKey);
export const actionLabel = (action: SetupPlanAction): string => t(`plan.action.${action}` as MessageKey);
export const outcomeLabel = (outcome: SetupPlanOutcome): string => t(`plan.outcome.${outcome}` as MessageKey);

// ---------------------------------------------------------------- pilot checks


export const CHECK_GROUPS = ['customer_care', 'cod', 'owner'] as const;

export const checkStatus = (check: Pick<PilotCheck, 'result'>): PilotCheckStatus => check.result?.status ?? 'not_run';

export function checkTone(status: PilotCheckStatus): Tone {
  return status === 'pass' ? 'success' : status === 'fail' ? 'dangerTone' : 'neutral';
}

/** A check's draft is worth saving only when it differs from what is recorded. */
export function checkChanged(check: Pick<PilotCheck, 'result'>, draft: { status: PilotCheckStatus; note: string; evidence: string }): boolean {
  const saved = { status: checkStatus(check), note: check.result?.note ?? '', evidence: check.result?.evidence ?? '' };
  return saved.status !== draft.status || saved.note !== draft.note.trim() || saved.evidence !== draft.evidence.trim();
}

/** What is wrong with an evidence reference before it is sent (the API checks the same). */
export function evidenceProblem(evidence: string): MessageKey | null {
  const value = evidence.trim();
  if (value.length > 300) return 'plan.checks.evidenceLong';
  if (value.includes('://') && !value.toLowerCase().startsWith('https://')) return 'plan.checks.evidenceHttps';
  return null;
}

/** "x of 18 passed" with the failures, and whether the Week 1 gate is open. */
export const checksSummary = (checks: Pick<PilotChecks, 'passed' | 'failed' | 'total' | 'done'>) =>
  ({ passed: checks.passed, failed: checks.failed, total: checks.total, done: checks.done });

// ---------------------------------------------------------------- live checklist

export function checklistTone(state: SetupChecklistState): Tone {
  if (state === 'done') return 'success';
  if (state === 'partial') return 'warning';
  if (state === 'todo') return 'dangerTone';
  return 'neutral';
}

export const checklistStateLabel = (state: SetupChecklistState): string => t(`plan.list.state.${state}` as MessageKey);

/** Steps still to do first (todo, then partial), then those ANUM cannot read, then done ones; the plan's order within each. */
export function orderChecklist(items: readonly SetupChecklistItem[]): SetupChecklistItem[] {
  const rank: Record<SetupChecklistState, number> = { todo: 0, partial: 1, no_access: 2, cannot_check: 3, done: 4 };
  return items.map((item, index) => ({ item, index })).sort((a, b) => rank[a.item.state] - rank[b.item.state] || a.index - b.index).map((row) => row.item);
}

export const checklistSummary = (list: Pick<SetupChecklist, 'done' | 'total'>) => ({ done: list.done, total: list.total, complete: list.total > 0 && list.done === list.total });
