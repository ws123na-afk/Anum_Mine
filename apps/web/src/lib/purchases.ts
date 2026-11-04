// Purchases, deliveries, returns and refunds on the web (docs/purchases.md#web): tabs, labels and small pure helpers.
// Money is never added across currencies, a payment or refund counts only while its Finance link is active, and a
// refund is never income: it lowers what a purchase cost.
import type {
  PurchaseDueItem, PurchaseMoney, PurchaseMoneyLink, PurchaseReturn, PurchaseReturnReason, PurchaseReturnStatus, PurchaseShipmentStatus,
  PurchaseStatus,
} from '@anum/contracts';
import { formatAmount } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';

export const PURCHASE_TABS = ['overview', 'list', 'returns', 'settings'] as const;
export type PurchaseTab = (typeof PURCHASE_TABS)[number];
export const PURCHASE_DETAIL_TABS = ['delivery', 'items', 'returns', 'activity', 'details'] as const;
export type PurchaseDetailTab = (typeof PURCHASE_DETAIL_TABS)[number];
export const PAGE_SIZE = 20;
export const FILTERS = ['', 'awaiting', 'returns', 'open', 'closed'] as const;

export function tabFromHash(hash: string): PurchaseTab {
  const id = hash.replace(/^#/, '').split('?')[0];
  const tab = id.startsWith('purchases-') ? id.slice('purchases-'.length) : 'overview';
  return (PURCHASE_TABS as readonly string[]).includes(tab) ? (tab as PurchaseTab) : 'overview';
}

export function hashForTab(tab: PurchaseTab): string {
  return tab === 'overview' ? '#purchases' : `#purchases-${tab}`;
}

const label = (prefix: string, value: string) => {
  const key = `${prefix}.${value}` as MessageKey;
  const text = t(key);
  return text === key ? value.replace(/_/g, ' ') : text;
};
export const statusLabel = (value: PurchaseStatus | string) => label('pur.status', value);
export const returnStatusLabel = (value: PurchaseReturnStatus | string) => label('pur.rstatus', value);
export const reasonLabel = (value: PurchaseReturnReason | string) => label('pur.reason', value);
export const shipmentLabel = (value: PurchaseShipmentStatus | string) => label('pur.sstatus', value);
export const methodLabel = (value: string | null) => (value ? label('pur.method', value) : '-');
export const dueLabel = (item: PurchaseDueItem) => label('pur.due', item.kind);
export const filterLabel = (value: string) => (value ? label('pur.filter', value) : t('pur.filter.all'));

/** The colour family of a status chip: waiting, moving, done, a problem or closed. */
export function statusTone(status: PurchaseStatus | PurchaseReturnStatus): 'wait' | 'move' | 'done' | 'warn' | 'closed' {
  if (status === 'disputed' || status === 'rejected') return 'warn';
  if (status === 'refunded' || status === 'delivered') return 'done';
  if (status === 'cancelled') return 'closed';
  if (status === 'ordered' || status === 'confirmed' || status === 'requested' || status === 'refund_pending') return 'wait';
  return 'move';
}

/** One line per currency; never a sum across currencies. */
export function moneyList(rows: readonly PurchaseMoney[] | null | undefined): string {
  if (!rows || rows.length === 0) return '-';
  return rows.map((row) => formatAmount(row.amount, row.currency)).join(' · ');
}

export type LinkState = 'none' | 'active' | 'needs_review' | 'void';

export function linkState(link: PurchaseMoneyLink | null | undefined): LinkState {
  return link ? link.status : 'none';
}

/** What a payment or refund cell says: the linked amount, or why there is none. */
export function linkText(link: PurchaseMoneyLink | null | undefined): string {
  const state = linkState(link);
  if (state === 'none') return t('pur.pay.none');
  if (state === 'void') return link?.status_reason === 'transaction_undone' ? t('pur.pay.undone') : t('pur.pay.removed');
  const amount = formatAmount(link!.amount, link!.currency);
  return state === 'needs_review' ? t('pur.pay.review', { amount }) : amount;
}

/** What is still to be refunded on a return, in its currency, or null when the return has no amount. */
export function refundLeft(item: Pick<PurchaseReturn, 'amount' | 'refunded'>): string | null {
  if (item.amount === null) return null;
  const left = Math.round((Number(item.amount) - Number(item.refunded)) * 100) / 100;
  return left > 0 ? left.toFixed(2) : '0.00';
}

/** Days left in words: today, overdue or in N days. */
export function dueText(item: PurchaseDueItem): string {
  if (item.days_left < 0) return t('pur.due.overdue', { count: -item.days_left });
  if (item.days_left === 0) return t('pur.due.today');
  return t('pur.due.in', { count: item.days_left });
}

export function purchaseName(p: { merchant: string; order_ref: string | null }): string {
  return p.order_ref ? `${p.merchant} ${p.order_ref}` : p.merchant;
}

export function today(): string {
  const date = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** A non-negative decimal as typed (dot or Arabic digits and separators), or null. */
export function decimal(value: string, places = 2): string | null {
  const western = value.trim().replace(/[٠-٩]/g, (d) => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d))).replace('٫', '.').replace(/,/g, '');
  if (!/^\d+(\.\d+)?$/.test(western)) return null;
  const [whole, fraction = ''] = western.split('.');
  if (fraction.length > places) return null;
  return fraction ? `${whole}.${fraction}` : whole;
}

/** The sum of priced lines as typed, for the form (a detail: the money is the linked payment). */
export function linesTotal(lines: { quantity: string; unit_price: string }[]): string | null {
  let total = 0;
  let priced = false;
  for (const line of lines) {
    const quantity = decimal(line.quantity, 3);
    const price = decimal(line.unit_price);
    if (quantity && price) { total += Number(quantity) * Number(price); priced = true; }
  }
  return priced ? (Math.round(total * 100) / 100).toFixed(2) : null;
}

/** A new idempotency key for one press of a save button. */
export function newKey(prefix = 'pur'): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}
