// Pure helpers for budgets, categories and recurring payments (docs/finance.md#budgets-and-planning,
// #category-ontology, #recurring-payments). No network: the figures themselves are the API's.
import type {
  FinanceBudgetKind, FinanceCategoryTree, FinancePlanBudgetInput, FinancePlanBudgetLine,
} from '@anum/contracts';
import { categoryLabel, formatAmount } from './finance.ts';
import { getLocale, t, type MessageKey } from './i18n.ts';

const SAVING: readonly FinanceBudgetKind[] = ['savings', 'emergency', 'sinking_fund'];

/** A fresh budget form: a monthly spending budget in the person's main currency. */
export function emptyBudget(currency: string): FinancePlanBudgetInput {
  return { name: '', kind: 'spending', period: 'monthly', currency, amount: '', categories: [], subcategory_ids: [], starts_on: null, ends_on: null, fixed: false,
    essential: null, allocation: null, goal_id: null, debt_ids: [], alert_percent: 80, reserve_in_safe_to_spend: false, active: true, notes: null,
    rollover: 'none', rollover_cap: null, overspend: 'absorb' };
}

/** What is missing before the form can be sent, in the person's language (null: ready). The server checks again. */
export function budgetFormError(form: FinancePlanBudgetInput): string | null {
  if (!form.name.trim()) return t('fbud.form.needName');
  const amount = Number(form.amount);
  if (!form.amount.trim() || !Number.isFinite(amount) || amount <= 0) return t('fbud.form.needAmount');
  if (form.kind === 'spending' && form.categories.length === 0 && form.subcategory_ids.length === 0) return t('fbud.form.needCategory');
  if (SAVING.includes(form.kind) && !form.goal_id) return t('fbud.form.needGoal');
  if (form.period === 'custom' && (!form.starts_on || !form.ends_on || form.ends_on < form.starts_on)) return t('fbud.form.needDates');
  if (form.kind === 'spending' && form.rollover === 'capped') {
    const cap = Number(form.rollover_cap ?? '');
    if (!(form.rollover_cap ?? '').trim() || !Number.isFinite(cap) || cap <= 0) return t('fbud.form.needCap');
  }
  return null;
}

/** The input the budget form sends for an existing budget (the server takes the whole budget on PATCH). */
export function budgetInput(line: FinancePlanBudgetLine): FinancePlanBudgetInput {
  return {
    name: line.name, kind: line.kind, period: line.period, currency: line.currency, amount: line.amount, categories: line.categories,
    subcategory_ids: line.subcategory_ids,
    // The budget's own dates: a custom budget that carries money repeats, so the period shown is not its first window.
    starts_on: line.period === 'monthly' ? null : (line.starts_on ?? line.period_start), ends_on: line.period === 'custom' ? (line.ends_on ?? line.period_end) : null,
    fixed: line.fixed, essential: line.essential, allocation: line.allocation, goal_id: line.goal_id, debt_ids: line.debt_ids, alert_percent: line.alert_percent,
    reserve_in_safe_to_spend: line.reserve_in_safe_to_spend, active: line.active, notes: line.notes,
    ...(line.kind === 'spending' ? { rollover: line.rollover ?? 'none', rollover_cap: line.rollover_cap ?? null, overspend: line.overspend ?? 'absorb' } : {}),
  };
}

/** Whether a budget line has rollover to show: a setting other than none and absorb, money carried in or a chain. */
export function showsRollover(line: Pick<FinancePlanBudgetLine, 'rollover' | 'overspend' | 'carried_in' | 'chain_periods'>): boolean {
  return (line.rollover ?? 'none') !== 'none' || (line.overspend ?? 'absorb') !== 'absorb' || Number(line.carried_in ?? 0) !== 0 || (line.chain_periods ?? 0) > 0;
}

/** The words for one rollover setting ("Carries what is left, at most 150.00; an overspend is carried"). */
export function rolloverText(terms: { rollover: FinancePlanBudgetLine['rollover']; cap: string | null; overspend: FinancePlanBudgetLine['overspend'] },
  currency: string): string {
  const surplus = terms.rollover === 'capped' && terms.cap !== null
    ? t('fbud.roll.cappedAt', { amount: formatAmount(terms.cap, currency) }) : t(`fbud.roll.mode.${terms.rollover}` as MessageKey);
  return `${surplus}${t('fbud.roll.join')}${t(`fbud.roll.over.${terms.overspend}` as MessageKey)}`;
}

/** The short words for one period's setting in the history ("Carries up to AED 150.00 · overspend carried"). */
export function shortRollover(terms: { rollover: FinancePlanBudgetLine['rollover']; cap: string | null; overspend: FinancePlanBudgetLine['overspend'] },
  currency: string): string {
  const surplus = terms.rollover === 'capped' && terms.cap !== null
    ? t('fbud.roll.short.capped', { amount: formatAmount(terms.cap, currency) }) : t(`fbud.roll.short.${terms.rollover === 'capped' ? 'surplus' : terms.rollover}` as MessageKey);
  return terms.overspend === 'carry' ? `${surplus} · ${t('fbud.roll.short.deficit')}` : surplus;
}

/** Percent of the budget used, between 0 and 100 for a meter (the label still says the real figure). */
export function meterValue(line: Pick<FinancePlanBudgetLine, 'percent'>): number {
  return Math.max(0, Math.min(100, line.percent ?? 0));
}

/** The tone a risk is shown in. */
export function riskTone(risk: FinancePlanBudgetLine['risk']): 'ok' | 'warn' | 'bad' | 'muted' {
  if (risk === 'over' || risk === 'high') return 'bad';
  if (risk === 'watch' || risk === 'behind') return 'warn';
  if (risk === 'unknown') return 'muted';
  return 'ok';
}

/** The name of a group, a category or a subcategory in the person's language (the owner's own names are kept as typed). */
export const nodeName = (node: { name: string; name_ar: string | null }) => (getLocale() === 'ar' && node.name_ar ? node.name_ar : node.name);

/** Filter the tree by a search: a group stays when its name, a category's or a subcategory's matches. */
export function filterTree(tree: Pick<FinanceCategoryTree, 'groups'>, query: string): FinanceCategoryTree['groups'] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return tree.groups;
  const hit = (text: string | null | undefined) => Boolean(text && text.toLocaleLowerCase().includes(needle));
  return tree.groups.flatMap((g) => {
    if (hit(g.name) || hit(g.name_ar)) return [g];
    const categories = g.categories.filter((c) => hit(c.name) || hit(c.name_ar) || hit(categoryLabel(c.id)) || c.subcategories.some((s) => hit(s.name) || hit(s.name_ar)));
    return categories.length ? [{ ...g, categories }] : [];
  });
}

/** A price change, signed, in its currency ("+5.00" before the currency, or "-3.00"). */
export function priceChangeText(amount: string, currency: string): string {
  return `${Number(amount) > 0 ? '+' : ''}${formatAmount(amount, currency)}`;
}
