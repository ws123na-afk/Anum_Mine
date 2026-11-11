// Purchases, deliveries, returns and refunds on the web (docs/purchases.md#web): tabs, labels and small pure helpers.
// Money is never added across currencies, a payment or refund counts only while its Finance link is active, and a
// refund is never income: it lowers what a purchase cost.
import type {
  Purchase, PurchaseDetail, PurchaseDueItem, PurchaseMoney, PurchaseMoneyLink, PurchaseReturn, PurchaseReturnReason, PurchaseReturnStatus,
  PurchaseShipmentStatus, PurchaseStatus,
} from '@anum/contracts';
import { formatAmount } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';

export const PURCHASE_TABS = ['overview', 'list', 'deliveries', 'returns', 'warranty', 'settings'] as const;
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

/** The purchase a link asks to open (`#purchases?open=<id>`, from Save as purchase in the Email Center), or null. */
export function openFromHash(hash: string): string | null {
  const query = hash.replace(/^#/, '').split('?')[1] ?? '';
  const value = new URLSearchParams(query).get('open');
  return value && /^[A-Za-z0-9_.:-]{1,80}$/.test(value) ? value : null;
}

export interface MailAttachmentChoice { index: number; filename: string; size: number; downloadable: boolean }

/** A message's attachments worth offering as a purchase's receipt: ones ANUM can download, PDFs then images first. */
export function receiptChoices(attachments: readonly MailAttachmentChoice[]): MailAttachmentChoice[] {
  const rank = (name: string) => (/\.pdf$/i.test(name) ? 0 : /\.(png|jpe?g|webp|heic)$/i.test(name) ? 1 : 2);
  return attachments.filter((item) => item.downloadable).sort((a, b) => rank(a.filename) - rank(b.filename) || a.index - b.index);
}

/** The address of one purchase, opened in the Purchases drawer. */
export const purchaseHref = (purchaseId: string) => `#purchases?open=${encodeURIComponent(purchaseId)}`;

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
export function refundLeft(item: Pick<PurchaseReturn, 'amount' | 'refunded' | 'currency'> & { refunded_all?: PurchaseReturn['refunded_all'] }): string | null {
  if (item.amount === null) return null;
  // A refund in another currency cannot be subtracted from the return's amount: currencies are never converted here.
  if ((item.refunded_all ?? []).some((row) => row.currency !== item.currency)) return null;
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

// ---- The tracker (docs/purchases.md#web) ---------------------------------------------------------------

export const DATE_RANGES = ['', 'month', '30', '90', 'year'] as const;
export type DateRange = (typeof DATE_RANGES)[number];
export const PAYMENT_FILTERS = ['', 'linked', 'unlinked', 'card', 'apple_pay', 'cash', 'bank_transfer', 'cash_on_delivery', 'instalments', 'gift_card', 'other'] as const;
export const SOURCE_FILTERS = ['', 'web', 'iphone'] as const;

export interface TrackerFilters {
  q: string;
  merchant: string;
  status: string;
  range: DateRange;
  payment: string;
  source: string;
}
export const NO_FILTERS: TrackerFilters = { q: '', merchant: '', status: '', range: '', payment: '', source: '' };

const iso = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

/** The first order day a date range covers (the end is today), or null for any date. */
export function rangeStart(range: DateRange, now = new Date()): string | null {
  if (!range) return null;
  if (range === 'month') return iso(new Date(now.getFullYear(), now.getMonth(), 1));
  if (range === 'year') return iso(new Date(now.getFullYear(), 0, 1));
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - Number(range) + 1);
  return iso(start);
}

export function filtersActive(filters: TrackerFilters): boolean {
  return Object.values(filters).some(Boolean);
}

/** A tracking number with only its last four characters shown, for screens others may see over a shoulder. */
export function maskTracking(code: string | null | undefined): string {
  if (!code) return '-';
  const clean = code.trim();
  if (clean.length <= 4) return clean;
  return `${'•'.repeat(Math.min(6, clean.length - 4))}${clean.slice(-4)}`;
}

/** The carrier's own public tracking page, for the carriers whose address is known; ANUM reads nothing from it. */
const CARRIER_PAGES: Record<string, (code: string) => string> = {
  dhl: (code) => `https://www.dhl.com/global-en/home/tracking/tracking-express.html?submit=1&tracking-id=${code}`,
  aramex: (code) => `https://www.aramex.com/us/en/track/results?ShipmentNumber=${code}`,
  fedex: (code) => `https://www.fedex.com/fedextrack/?trknbr=${code}`,
  ups: (code) => `https://www.ups.com/track?tracknum=${code}`,
};

export function carrierKey(carrier: string | null | undefined): string | null {
  const name = (carrier ?? '').trim().toLowerCase();
  if (!name) return null;
  return Object.keys(CARRIER_PAGES).find((key) => name === key || name.startsWith(`${key} `)) ?? null;
}

export function carrierUrl(carrier: string | null | undefined, code: string | null | undefined): string | null {
  const key = carrierKey(carrier);
  const number = (code ?? '').trim();
  if (!key || !number || !/^[A-Za-z0-9-]{4,40}$/.test(number)) return null;
  return CARRIER_PAGES[key](encodeURIComponent(number));
}

/** The task queue's kinds and the one action each offers. */
export type TaskAction = 'open' | 'proof' | 'return' | 'refund' | 'shipment';
export function taskAction(kind: PurchaseDueItem['kind']): TaskAction {
  if (kind === 'delivery_late' || kind === 'delivery_expected') return 'shipment';
  if (kind === 'proof_needed') return 'proof';
  if (kind === 'return_window' || kind === 'pickup_due') return 'return';
  if (kind === 'refund_overdue') return 'refund';
  return 'open';
}

/** Due in words for the queue: "Due: Today", "Due: 6 Nov" or overdue. */
export function dueWhen(item: Pick<PurchaseDueItem, 'days_left' | 'due_on'>, format: (day: string) => string): string {
  if (item.days_left === 0) return t('pur.task.dueToday');
  if (item.days_left < 0) return t('pur.task.overdue', { day: format(item.due_on) });
  return t('pur.task.due', { day: format(item.due_on) });
}

/** The newest return of a purchase that is still open (else the newest), for the drawer. */
export function currentReturn(purchase: Pick<PurchaseDetail, 'returns'>): PurchaseReturn | null {
  if (!purchase.returns.length) return null;
  const sorted = [...purchase.returns].sort((a, b) => (a.requested_on < b.requested_on ? 1 : a.requested_on > b.requested_on ? -1 : 0));
  const open = sorted.find((r) => !['refunded', 'rejected', 'cancelled'].includes(r.status));
  return open ?? sorted[0];
}

export type StepState = 'done' | 'current' | 'todo';
export interface TrackerStep { id: 'ordered' | 'shipped' | 'delivered' | 'refund'; state: StepState; day: string | null; label: string }

const AFTER_SHIPPED = new Set(['shipped', 'out_for_delivery', 'partially_delivered', 'delivered', 'return_requested', 'return_approved', 'pickup_scheduled',
  'returned', 'refund_pending', 'partially_refunded', 'refunded', 'disputed']);
const AFTER_DELIVERED = new Set(['delivered', 'return_requested', 'return_approved', 'pickup_scheduled', 'returned', 'refund_pending', 'partially_refunded',
  'refunded']);

/** Ordered, shipped, delivered and (when a return exists) the refund, from the purchase's own status and dates. */
export function trackerSteps(purchase: Pick<PurchaseDetail, 'status' | 'ordered_on' | 'delivered_on' | 'shipments' | 'returns'>): TrackerStep[] {
  const shippedOn = purchase.shipments.map((s) => s.shipped_on).filter((d): d is string => Boolean(d)).sort()[0] ?? null;
  const shipped = AFTER_SHIPPED.has(purchase.status) || purchase.shipments.some((s) => s.status !== 'pending');
  const delivered = AFTER_DELIVERED.has(purchase.status);
  const steps: TrackerStep[] = [
    { id: 'ordered', state: 'done', day: purchase.ordered_on, label: statusLabel('ordered') },
    { id: 'shipped', state: shipped ? 'done' : purchase.status === 'cancelled' ? 'todo' : 'current', day: shippedOn, label: statusLabel('shipped') },
    { id: 'delivered', state: delivered ? 'done' : shipped ? 'current' : 'todo', day: purchase.delivered_on, label: statusLabel('delivered') },
  ];
  const ret = currentReturn(purchase);
  if (ret) {
    const done = ret.status === 'refunded';
    steps.push({ id: 'refund', state: done ? 'done' : 'current', day: ret.refunded_on, label: done ? returnStatusLabel('refunded') : t('pur.step.refund', { status: returnStatusLabel(ret.status) }) });
  }
  return steps;
}

/** How a refund matches its return: matched in Finance, waiting for review, or not received yet. */
export function refundMatch(ret: Pick<PurchaseReturn, 'refunds' | 'status'>): 'matched' | 'review' | 'waiting' | 'none' {
  if (ret.refunds.some((r) => r.status === 'active')) return 'matched';
  if (ret.refunds.some((r) => r.status === 'needs_review')) return 'review';
  if (['returned', 'refund_pending', 'partially_refunded', 'approved', 'pickup_scheduled', 'requested'].includes(ret.status)) return 'waiting';
  return 'none';
}

/** The chip tone of a purchase status in the shell's palette. */
export function chipTone(status: PurchaseStatus | PurchaseReturnStatus): 'ok' | 'warn' | 'bad' | 'info' | 'muted' {
  const tone = statusTone(status);
  return tone === 'done' ? 'ok' : tone === 'warn' ? 'bad' : tone === 'closed' ? 'muted' : tone === 'wait' ? 'warn' : 'info';
}

/** The item and shop of a tracker row: the first item's description, else the shop. */
export function rowTitle(p: Pick<Purchase, 'merchant'> & { first_item?: string | null; item_count?: number }): string {
  if (!p.first_item) return p.merchant;
  return p.item_count && p.item_count > 1 ? t('pur.row.more', { item: p.first_item, count: p.item_count - 1 }) : p.first_item;
}
