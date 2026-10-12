// Setup plan page helpers (docs/setup-plan.md): grouping, filters, the rollout track and the
// result summary. Pure, so `pnpm --filter @anum/web test:unit` covers them without a browser.
import type {
  PilotCheck, PilotCheckStatus, PilotChecks, SetupPlanAction, SetupPlanApplyResult, SetupPlanItem, SetupPlanLink, SetupPlanOutcome, SetupPlanPreview, SetupPlanSection, SetupPlanWeek,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export type {
  PilotCheck, PilotCheckStatus, PilotChecks, SetupPlanAction, SetupPlanApplyResult, SetupPlanItem, SetupPlanOutcome, SetupPlanPhase, SetupPlanPreview, SetupPlanSection, SetupPlanSummary,
} from '@anum/contracts';

/** Sections in the order the plan is applied (dependencies first), then what stays for people. */
export const SECTION_ORDER: readonly SetupPlanSection[] = [
  'workspaces', 'governance', 'knowledge', 'budget', 'skills', 'agents', 'assistants', 'commands', 'automations', 'manual', 'unsupported',
];

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

/** How many items applying the plan would create or update. */
export const changeCount = (preview: Pick<SetupPlanPreview, 'items'>): number => preview.items.filter((item) => CHANGING.has(item.action)).length;

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
