// Tamara refunds on the installment statement (docs/finance.md#tamara-refunds): pure helpers for the
// Refunds card. The server works out the evidence and the new schedule; the page only shows and confirms it.
import type {
  FinanceInstallmentStatementEntry, FinanceOrderRefundPlan, FinanceRefundEvidence, FinanceRefundOrderState, FinanceRefundReason,
} from '@anum/contracts';
import type { MessageKey } from './i18n.ts';
import { fromMilli, toMilli } from './financeStatements.ts';

export type RefundFilter = 'all' | 'pending' | 'review' | 'refunded';
export type ChipTone = 'ok' | 'warn' | 'bad' | 'info' | 'muted';

export const stateKey = (state: FinanceRefundOrderState): MessageKey => `frefund.state.${state}` as MessageKey;
export const stateTone = (state: FinanceRefundOrderState): ChipTone =>
  state === 'refunded' ? 'ok' : state === 'partly_refunded' ? 'info' : state === 'needs_review' ? 'warn' : 'muted';
export const evidenceKey = (evidence: FinanceRefundEvidence): MessageKey => `frefund.evidence.${evidence.code}` as MessageKey;
export const reasonKey = (reason: FinanceRefundReason): MessageKey => `frefund.reason.${reason}` as MessageKey;
export const modeKey = (plan: FinanceOrderRefundPlan): MessageKey => `frefund.mode.${plan.mode}` as MessageKey;

/** Orders with any refund signal: the ones the card lists. An order with no evidence at all is left out. */
export function withEvidence(orders: readonly FinanceOrderRefundPlan[]): FinanceOrderRefundPlan[] {
  return orders.filter((o) => o.evidence.length > 0 || o.applied_state !== 'active');
}

export function filterOrders(orders: readonly FinanceOrderRefundPlan[], filter: RefundFilter, needle: string): FinanceOrderRefundPlan[] {
  const q = needle.trim().toLowerCase();
  return orders.filter((o) => {
    if (filter === 'pending' && !o.pending) return false;
    if (filter === 'review' && o.state !== 'needs_review') return false;
    if (filter === 'refunded' && o.state !== 'refunded' && o.state !== 'partly_refunded') return false;
    return !q || `${o.merchant} ${o.order_id}`.toLowerCase().includes(q);
  });
}

/** What "Apply refunds" sends: every order that can be applied, each with the plan the page showed. */
export function applySelection(orders: readonly FinanceOrderRefundPlan[]): { provider: string; order_id: string; plan_hash: string }[] {
  return orders.filter((o) => o.applicable).map((o) => ({ provider: o.provider, order_id: o.order_id, plan_hash: o.plan_hash }));
}

/** Exact sum of decimal strings in minor units (no float drift), back as a fixed-point string. */
export function sumAmounts(values: readonly string[], digits = 2): string {
  const scale = 10n ** BigInt(digits);
  let total = 0n;
  for (const raw of values) {
    const [whole, frac = ''] = raw.replace('-', '').split('.');
    const minor = BigInt(whole || '0') * scale + BigInt((frac + '0'.repeat(digits)).slice(0, digits) || '0');
    total += raw.startsWith('-') ? -minor : minor;
  }
  const negative = total < 0n;
  const abs = negative ? -total : total;
  const text = digits ? `${abs / scale}.${(abs % scale).toString().padStart(digits, '0')}` : `${abs}`;
  return negative ? `-${text}` : text;
}

/** Totals per currency of what the confirmed refunds remove from what is due, and the money back expected. */
export function pendingTotals(orders: readonly FinanceOrderRefundPlan[]): { currency: string; notDue: string; moneyBack: string }[] {
  const by = new Map<string, { notDue: string[]; moneyBack: string[] }>();
  for (const o of orders.filter((x) => x.applicable)) {
    const entry = by.get(o.currency) ?? { notDue: [], moneyBack: [] };
    entry.notDue.push(o.outstanding_before, `-${o.outstanding_after}`);
    entry.moneyBack.push(o.credit_back);
    by.set(o.currency, entry);
  }
  return [...by].map(([currency, v]) => ({ currency, notDue: sumAmounts(v.notDue), moneyBack: sumAmounts(v.moneyBack) }));
}

/** What is still due on one statement row once applied refunds are taken off (0 when paid or refunded). */
export function rowDue(row: FinanceInstallmentStatementEntry): number {
  if (row.source_status === 'paid' || row.source_status === 'refunded') return 0;
  return Math.max(0, Number(row.amount) - Number(row.refunded_amount ?? 0));
}

/** What is still due on a row as the wire string in its currency's digits (exact, for the CSV): 0 once it is paid. */
export function rowDueText(row: FinanceInstallmentStatementEntry, paid: boolean): string {
  if (paid || row.source_status === 'paid' || row.source_status === 'refunded') return fromMilli(0n, row.currency);
  const left = toMilli(row.amount) - toMilli(row.refunded_amount ?? '0');
  return fromMilli(left > 0n ? left : 0n, row.currency);
}

/** One statement row's place in the dashboard: refunded rows are not due and never counted as payable. */
export function rowEffective(row: FinanceInstallmentStatementEntry, verified: boolean): 'paid' | 'scheduled' | 'review' | 'refunded' {
  if (verified || row.source_status === 'paid') return 'paid';
  if (row.source_status === 'refunded' || Number(row.refunded_amount ?? 0) >= Number(row.amount)) return 'refunded';
  return row.source_status === 'review' ? 'review' : 'scheduled';
}

/** The order's refund status from its rows alone: Active, Partly refunded or Refunded. */
export function orderRefundState(rows: readonly FinanceInstallmentStatementEntry[]): 'active' | 'partly_refunded' | 'refunded' {
  const refunded = rows.some((r) => Number(r.refunded_amount ?? 0) > 0 || r.source_status === 'refunded');
  if (!refunded) return 'active';
  const open = rows.filter((r) => r.source_status !== 'paid');
  return open.every((r) => rowDue(r) === 0) ? 'refunded' : 'partly_refunded';
}
