// The Suggestions page's model (docs/finance.md#suggestions): pure helpers, covered by
// test/financeSuggestions.test.ts without a browser.
import type { FinanceRuleContent, FinanceRuleMatch, FinanceSuggestion, FinanceSuggestionQueue } from '@anum/contracts';

export type SuggestionsView = 'queue' | 'rules' | 'merchants' | 'automation';
export const SUGGESTION_VIEWS: readonly SuggestionsView[] = ['queue', 'rules', 'merchants', 'automation'];

/** The view a link opens (`#finance-suggestions?view=automation`, from Finance settings); the queue otherwise. */
export function suggestionsViewFromHash(hash: string): SuggestionsView {
  const query = hash.split('?')[1] ?? '';
  const view = new URLSearchParams(query).get('view');
  return (SUGGESTION_VIEWS as readonly string[]).includes(view ?? '') ? view as SuggestionsView : 'queue';
}

/** What the Accept button does for a suggestion: change rows, only record that it was seen, or nothing here. */
export function acceptMode(item: Pick<FinanceSuggestion, 'action'>): 'apply' | 'acknowledge' | 'none' {
  if (item.action.kind === 'classify' || item.action.kind === 'confirm_refund') return 'apply';
  if (item.action.kind === 'acknowledge') return 'acknowledge';
  return 'none';
}

/** A confidence ("0.85") as a whole percentage, or null. */
export function confidencePercent(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  // A zero is "no rule recognised it", not a confidence worth showing.
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) : null;
}

export function confidenceTone(value: string | null | undefined): 'ok' | 'warn' | 'muted' {
  const pct = confidencePercent(value);
  if (pct === null) return 'muted';
  return pct >= 80 ? 'ok' : 'warn';
}

/** The kind filter chips: "all" first, then each kind that has items, in the server's order. */
export function kindChips(queue: Pick<FinanceSuggestionQueue, 'counts'>): { kind: string; count: number }[] {
  const total = queue.counts.reduce((sum, c) => sum + c.count, 0);
  return [{ kind: 'all', count: total }, ...queue.counts.map((c) => ({ kind: c.kind, count: c.count }))];
}

export interface RuleForm {
  merchant: string;
  words: string;
  reference: string;
  direction: 'any' | 'in' | 'out';
  minAmount: string;
  maxAmount: string;
  currency: string;
  accountId: string;
  category: string;
  subcategoryId: string;
  transactionType: string;
  autoApply: boolean;
  enabled: boolean;
}

export const EMPTY_RULE: RuleForm = {
  merchant: '', words: '', reference: '', direction: 'any', minAmount: '', maxAmount: '', currency: '', accountId: '', category: 'groceries',
  subcategoryId: '', transactionType: '', autoApply: false, enabled: true,
};

/** Comma-separated phrases, trimmed, empty ones dropped, duplicates (any case) once. */
export function splitWords(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(',')) {
    const words = part.split(/\s+/).filter(Boolean).join(' ');
    if (words && !out.some((o) => o.toLowerCase() === words.toLowerCase())) out.push(words);
  }
  return out;
}

/** The form as rule content, or the problem to fix first (a message key). */
export function toContent(form: RuleForm): { content: FinanceRuleContent } | { error: 'needMatch' | 'needCurrency' | 'badRange' } {
  const words = splitWords(form.words);
  const merchant = form.merchant.trim(), reference = form.reference.trim();
  if (!merchant && !words.length && !reference) return { error: 'needMatch' };
  const ranged = form.minAmount.trim() !== '' || form.maxAmount.trim() !== '';
  if (ranged && !/^[A-Z]{3}$/.test(form.currency.trim().toUpperCase())) return { error: 'needCurrency' };
  if (ranged && form.minAmount && form.maxAmount && Number(form.minAmount) > Number(form.maxAmount)) return { error: 'badRange' };
  const match: FinanceRuleMatch = {
    merchant: merchant || null, words, reference: reference || null, direction: form.direction,
    min_amount: form.minAmount.trim() || null, max_amount: form.maxAmount.trim() || null,
    currency: ranged ? form.currency.trim().toUpperCase() : null, account_ids: form.accountId ? [form.accountId] : [], senders: [],
  };
  return { content: {
    match, category: form.category as FinanceRuleContent['category'], subcategory_id: form.subcategoryId || null,
    transaction_type: (form.transactionType || null) as FinanceRuleContent['transaction_type'], auto_apply: form.autoApply, enabled: form.enabled,
  } };
}

/** Rule content back into the form (editing a rule). */
export function fromContent(content: FinanceRuleContent): RuleForm {
  const m = content.match;
  return {
    merchant: m.merchant ?? '', words: (m.words ?? []).join(', '), reference: m.reference ?? '', direction: m.direction ?? 'any',
    minAmount: m.min_amount ?? '', maxAmount: m.max_amount ?? '', currency: m.currency ?? '', accountId: m.account_ids?.[0] ?? '',
    category: content.category, subcategoryId: content.subcategory_id ?? '', transactionType: content.transaction_type ?? '',
    autoApply: Boolean(content.auto_apply), enabled: content.enabled !== false,
  };
}

/** A short plain description of what a rule matches ("zebramart, money out, AED 100 to 200"). */
export function matchSummary(match: FinanceRuleMatch): string[] {
  const parts: string[] = [];
  if (match.merchant) parts.push(match.merchant);
  parts.push(...(match.words ?? []));
  if (match.reference) parts.push(`"${match.reference}"`);
  return parts;
}

export const RULE_CATEGORIES = [
  'groceries', 'dining', 'food', 'room_rent', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'interest', 'cash', 'transfer', 'income', 'cashback', 'refunds', 'other',
] as const;

/** Never automated, in the order the Automation tab lists them (docs/finance.md#automation-policies). */
export const NEVER_AUTOMATED = ['post_adjustment', 'delete_record', 'change_bank_mapping', 'approve_payment', 'change_period'] as const;
