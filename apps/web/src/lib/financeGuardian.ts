// Finance, Guardian (docs/finance.md#guardian): pure helpers for the monitoring rules page, so
// `pnpm --filter @anum/web test:unit` covers them. Every figure comes from the API; nothing here moves money.
import type {
  FinanceAmountLimit, FinanceCategory, FinanceGuardianActivity, FinanceGuardianRuleKind, FinanceGuardianRuleStat, FinanceGuardianRules,
} from '@anum/contracts';
import { categoryLabel, formatAmount, isolate, ruleKindLabel, type RulesDraft } from './finance.ts';
import { formatDateTime, t, type MessageKey } from './i18n.ts';

/** The kinds of rule, in the order the evaluator checks them (guardian.py RULE_KINDS). */
export const RULE_KINDS: readonly FinanceGuardianRuleKind[] = [
  'allowed_currencies', 'max_single', 'max_daily_total', 'category_caps', 'blocked_categories', 'blocked_merchants',
  'approval_above', 'quiet_hours', 'cooling_off',
];
/** Kinds that apply to an action asked for, never to money already recorded: they never flag a payment. */
export const ACTION_ONLY_KINDS: readonly FinanceGuardianRuleKind[] = ['approval_above', 'quiet_hours', 'cooling_off'];

export function isSet(rules: FinanceGuardianRules, kind: FinanceGuardianRuleKind): boolean {
  const value = rules[kind];
  return Array.isArray(value) ? value.length > 0 : value !== null && value !== undefined;
}
export const configuredKinds = (rules: FinanceGuardianRules) => RULE_KINDS.filter((kind) => isSet(rules, kind));
export const isPaused = (rules: FinanceGuardianRules, kind: FinanceGuardianRuleKind) => (rules.paused ?? []).includes(kind);
export const activeKinds = (rules: FinanceGuardianRules) => configuredKinds(rules).filter((kind) => !isPaused(rules, kind));

/** Whole-page state: no rule set, at least one applied, or every rule paused. */
export function monitoringState(rules: FinanceGuardianRules): 'none' | 'on' | 'off' {
  const set = configuredKinds(rules);
  if (set.length === 0) return 'none';
  return set.some((kind) => !isPaused(rules, kind)) ? 'on' : 'off';
}

/** The same rules with ``kind`` paused or resumed (the server keeps the fixed order). */
export function withPaused(rules: FinanceGuardianRules, kind: FinanceGuardianRuleKind, paused: boolean): FinanceGuardianRules {
  const next = new Set(rules.paused ?? []);
  if (paused) next.add(kind); else next.delete(kind);
  return { ...rules, paused: RULE_KINDS.filter((k) => next.has(k) && isSet(rules, k)) };
}
/** Pause every rule that is set (Pause monitoring), or resume them all. */
export function withAllPaused(rules: FinanceGuardianRules, paused: boolean): FinanceGuardianRules {
  return { ...rules, paused: paused ? configuredKinds(rules) : [] };
}

const money = (limit: FinanceAmountLimit) => isolate(formatAmount(limit.amount, limit.currency));

/** The rule's settings as short lines ("Above AED 5,000", "Dining: AED 400"). */
export function thresholdLines(rules: FinanceGuardianRules, kind: FinanceGuardianRuleKind): string[] {
  switch (kind) {
    case 'allowed_currencies': return rules.allowed_currencies.length ? [isolate(rules.allowed_currencies.join(', '))] : [];
    case 'max_single': case 'max_daily_total': return rules[kind].map((l) => t('fguard.threshold.above', { amount: money(l) }));
    case 'approval_above': return rules.approval_above.map((l) => t('fguard.threshold.atOrAbove', { amount: money(l) }));
    case 'category_caps': return rules.category_caps.map((c) => t('fguard.threshold.cap', { category: categoryLabel(c.category), amount: money(c) }));
    case 'blocked_categories': return rules.blocked_categories.map(categoryLabel);
    case 'blocked_merchants': return rules.blocked_merchants.map((m) => `⁨${m}⁩`);
    case 'quiet_hours': return rules.quiet_hours ? [t('fguard.threshold.window', { start: isolate(rules.quiet_hours.start), end: isolate(rules.quiet_hours.end) })] : [];
    case 'cooling_off': {
      const cool = rules.cooling_off;
      if (!cool) return [];
      return cool.at_or_above.length ? cool.at_or_above.map((l) => t('fguard.threshold.wait', { count: cool.hours, amount: money(l) })) : [t('fguard.threshold.waitAll', { count: cool.hours })];
    }
    default: return [];
  }
}

/** What "Test" fills in: an amount just over the limit, the blocked merchant or category. */
export interface TestPrefill { amount: string; currency: string; category: FinanceCategory | ''; merchant: string }
export function testPrefill(rules: FinanceGuardianRules, kind: FinanceGuardianRuleKind, base: string): TestPrefill {
  const over = (l: FinanceAmountLimit | undefined) => (l ? { amount: String(Math.floor(Number(l.amount)) + 1), currency: l.currency } : { amount: '', currency: base });
  const empty: TestPrefill = { amount: '', currency: base, category: '', merchant: '' };
  switch (kind) {
    case 'max_single': case 'max_daily_total': return { ...empty, ...over(rules[kind][0]) };
    case 'approval_above': return rules.approval_above[0] ? { ...empty, amount: rules.approval_above[0].amount, currency: rules.approval_above[0].currency } : empty;
    case 'cooling_off': { const l = rules.cooling_off?.at_or_above[0]; return l ? { ...empty, amount: l.amount, currency: l.currency } : empty; }
    case 'category_caps': { const c = rules.category_caps[0]; return c ? { ...empty, ...over(c), category: c.category } : empty; }
    case 'blocked_categories': return { ...empty, category: rules.blocked_categories[0] ?? '' };
    case 'blocked_merchants': return { ...empty, merchant: rules.blocked_merchants[0] ?? '' };
    default: return empty;
  }
}

/** The editor draft with one empty row for a kind being added, so the person only fills in the limit. */
export function draftAdding(draft: RulesDraft, kind: FinanceGuardianRuleKind, base: string): RulesDraft {
  const row = { currency: base, amount: '' };
  switch (kind) {
    case 'max_single': return draft.maxSingle.length ? draft : { ...draft, maxSingle: [row] };
    case 'max_daily_total': return draft.maxDaily.length ? draft : { ...draft, maxDaily: [row] };
    case 'approval_above': return draft.approvalAbove.length ? draft : { ...draft, approvalAbove: [row] };
    case 'category_caps': return draft.caps.length ? draft : { ...draft, caps: [{ ...row, category: 'dining' }] };
    case 'quiet_hours': return { ...draft, quietOn: true };
    case 'cooling_off': return { ...draft, coolingOn: true, coolingAbove: draft.coolingAbove.length ? draft.coolingAbove : [row] };
    case 'allowed_currencies': return draft.allowedCurrencies.trim() ? draft : { ...draft, allowedCurrencies: base };
    default: return draft;
  }
}

/** "Last triggered" for a rule this month. */
export function lastTriggered(kind: FinanceGuardianRuleKind, stat: FinanceGuardianRuleStat | undefined, month: string): string {
  if (ACTION_ONLY_KINDS.includes(kind)) return t('fguard.rule.lastAction');
  if (!stat || !stat.last) return t('fguard.rule.lastNever', { month });
  return t('fguard.rule.lastCount', { date: formatDateTime(`${stat.last}T00:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' }), count: stat.count });
}

// A reason code recorded in the audit log, as the kind of rule it belongs to (guardian.evaluate).
const CODE_KIND: Record<string, FinanceGuardianRuleKind> = {
  currency_not_allowed: 'allowed_currencies', over_single_limit: 'max_single', over_daily_total: 'max_daily_total',
  over_category_cap: 'category_caps', category_blocked: 'blocked_categories', merchant_blocked: 'blocked_merchants',
  above_approval_amount: 'approval_above', quiet_hours: 'quiet_hours', cooling_off: 'cooling_off',
};

/** One audit record as the table's "Rule" and "Action" cells, in the person's language. */
export function activityCells(item: FinanceGuardianActivity): { rule: string; action: string; loosens: boolean; tone: 'ok' | 'warn' | 'neutral' } {
  const meta = item.metadata as { decision?: string; codes?: string[]; version?: number; loosens?: string[]; days?: number };
  const loosens = Array.isArray(meta.loosens) && meta.loosens.length > 0;
  const kinds = [...new Set((meta.codes ?? []).map((c) => CODE_KIND[c]).filter(Boolean))];
  const ruleList = (list: string[] | undefined) => (list && list.length ? list.map(ruleKindLabel).join(', ') : t('fguard.audit.allRules'));
  switch (item.action) {
    case 'finance.guardian.propose': return { rule: ruleList(meta.loosens), action: t('fguard.audit.action.propose'), loosens, tone: loosens ? 'warn' : 'neutral' };
    case 'finance.guardian.confirm': return { rule: ruleList(meta.loosens), action: t('fguard.audit.action.confirm', { version: meta.version ?? '' }), loosens, tone: loosens ? 'warn' : 'ok' };
    case 'finance.guardian.reject': return { rule: t('fguard.audit.allRules'), action: t('fguard.audit.action.reject'), loosens: false, tone: 'neutral' };
    case 'finance.guardian.review': {
      const rule = kinds.length ? kinds.map(ruleKindLabel).join(', ') : '-';
      const decision = meta.decision ?? '';
      if (decision === 'snoozed') return { rule, action: t('fguard.audit.action.snoozed', { count: meta.days ?? 0 }), loosens: false, tone: 'neutral' };
      if (decision === 'expected' || decision === 'reviewed' || decision === 'reopen') {
        return { rule, action: t(`fguard.audit.action.${decision}` as MessageKey), loosens: false, tone: decision === 'reopen' ? 'warn' : 'ok' };
      }
      return { rule, action: t('fguard.audit.action.other'), loosens: false, tone: 'neutral' };
    }
    default: return { rule: '-', action: t('fguard.audit.action.other'), loosens: false, tone: 'neutral' };
  }
}
