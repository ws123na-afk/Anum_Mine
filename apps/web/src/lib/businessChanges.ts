// Change orders, the usage and cost ledger and project profitability (docs/business.md#change-orders):
// labels, form checks and money text. Pure, so the unit tests cover it without a browser. The API is
// the authority: it recomputes every delta and total and checks who may approve.
import type { BusinessCurrency, ChangeOrderInput, ChangeOrderLine, ChangeOrderStatus, LineItem, UsageKind } from '@anum/contracts';
import { formatNumber, getLocale, t, type MessageKey } from './i18n.ts';
import type { Check, Tone } from './business.ts';

export const CHANGE_STATUSES: readonly ChangeOrderStatus[] = ['draft', 'pending_approval', 'approved', 'rejected', 'withdrawn'];
export const USAGE_KINDS: readonly UsageKind[] = ['labour', 'ai_usage', 'hosting', 'messaging', 'other'];
/** Kinds a person records by hand; AI usage comes from the model budget import. */
export const MANUAL_USAGE_KINDS: readonly UsageKind[] = ['labour', 'hosting', 'messaging', 'other'];

export const changeStatusLabel = (status: ChangeOrderStatus) => t(`biz.change.status.${status}` as MessageKey);
export const usageKindLabel = (kind: UsageKind) => t(`biz.usage.kind.${kind}` as MessageKey);

export function changeTone(status: ChangeOrderStatus): Tone {
  return status === 'approved' ? 'success' : status === 'rejected' || status === 'withdrawn' ? 'dangerTone' : status === 'pending_approval' ? 'warning' : 'neutral';
}

/** An empty value is "-" (never "0" or "Unknown" for a figure nobody recorded). */
export const DASH = '-';

/** Money with its currency, or "-" when there is no figure. Negative amounts keep their sign. */
export function moneyOrDash(amount: number | null | undefined, currency: BusinessCurrency): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return DASH;
  const locale = getLocale() === 'ar' ? 'ar-u-nu-latn' : 'en';
  return new Intl.NumberFormat(locale, { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
}

/** A delta with an explicit sign ("+SAR 800.00", "-SAR 344.99"), or "-". */
export function signedMoney(amount: number | null | undefined, currency: BusinessCurrency): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return DASH;
  const text = moneyOrDash(Math.abs(amount), currency);
  return amount > 0 ? `+${text}` : amount < 0 ? `-${text}` : text;
}

export const numberOrDash = (value: number | null | undefined) => (value === null || value === undefined ? DASH : formatNumber(value));
export const pctOrDash = (value: number | null | undefined) => (value === null || value === undefined ? DASH : `${formatNumber(value)}%`);

/** "+10 days", "-3 days", "No change". */
export function timelineText(days: number): string {
  if (days === 0) return t('biz.change.noTimeline');
  return t(days > 0 ? 'biz.change.later' : 'biz.change.sooner', { days: Math.abs(days) });
}

const DECIMAL = /^-?\d+(\.\d{1,2})?$/;

function decimal(text: string, { signed = false } = {}): number | null {
  const clean = text.replace(/,/g, '').trim();
  if (!DECIMAL.test(clean)) return null;
  const value = Number(clean);
  if (!Number.isFinite(value) || (!signed && value < 0)) return null;
  return value;
}

export interface ChangeLineDraft { action: ChangeOrderLine['action']; description: string; kind: LineItem['kind']; quantity: string; unit_price: string }
export interface ChangeDraft { title: string; request: string; requestedBy: string; lines: ChangeLineDraft[]; timelineDays: string; costDelta: string }

export const emptyChangeLine = (): ChangeLineDraft => ({ action: 'add', description: '', kind: 'service', quantity: '1', unit_price: '' });
export const emptyChangeDraft = (): ChangeDraft => ({ title: '', request: '', requestedBy: '', lines: [emptyChangeLine()], timelineDays: '0', costDelta: '0' });

export function changeDraftOf(input: Pick<ChangeOrderInput, 'title' | 'request' | 'requested_by' | 'lines' | 'timeline_days' | 'internal_cost_delta'>): ChangeDraft {
  return {
    title: input.title, request: input.request, requestedBy: input.requested_by, timelineDays: String(input.timeline_days), costDelta: String(input.internal_cost_delta),
    lines: input.lines.map((line) => ({ action: line.action, description: line.description, kind: line.kind, quantity: String(line.quantity), unit_price: String(line.unit_price) })),
  };
}

/** The change order body, or what to fix. A removal must name an existing line exactly; the API checks that. */
export function changeBody(draft: ChangeDraft): Check<ChangeOrderInput> {
  const title = draft.title.replace(/\s+/g, ' ').trim();
  if (title.length < 2) return { ok: false, error: t('biz.change.titleRequired') };
  const lines: ChangeOrderLine[] = [];
  for (const [index, line] of draft.lines.entries()) {
    const description = line.description.replace(/\s+/g, ' ').trim();
    if (!description && !line.unit_price.trim()) continue;
    const quantity = decimal(line.quantity || '1'), price = decimal(line.unit_price);
    if (!description || quantity === null || quantity <= 0 || price === null) return { ok: false, error: t('biz.change.lineInvalid', { n: index + 1 }) };
    lines.push({ action: line.action, description, kind: line.kind, quantity, unit_price: price });
  }
  const days = Number(draft.timelineDays.trim() || '0');
  if (!Number.isInteger(days) || Math.abs(days) > 3650) return { ok: false, error: t('biz.change.timelineInvalid') };
  const cost = decimal(draft.costDelta || '0', { signed: true });
  if (cost === null) return { ok: false, error: t('biz.change.costInvalid') };
  if (lines.length === 0 && days === 0 && cost === 0) return { ok: false, error: t('biz.change.nothing') };
  return { ok: true, value: { title, request: draft.request.trim(), requested_by: draft.requestedBy.replace(/\s+/g, ' ').trim(), lines, timeline_days: days, internal_cost_delta: cost } };
}

export interface UsageDraft { kind: UsageKind; description: string; occurredOn: string; quantity: string; unitCost: string; currency: BusinessCurrency; billable: boolean }
export const emptyUsageDraft = (currency: BusinessCurrency, today: string): UsageDraft => ({ kind: 'labour', description: '', occurredOn: today, quantity: '1', unitCost: '', currency, billable: false });

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** A ledger entry body for one project, or what to fix. The amount is quantity times unit cost, as the API computes it. */
export function usageBody(projectId: string, draft: UsageDraft): Check<Record<string, unknown>> {
  const description = draft.description.replace(/\s+/g, ' ').trim();
  if (description.length < 2) return { ok: false, error: t('biz.usage.descriptionRequired') };
  if (!DATE.test(draft.occurredOn)) return { ok: false, error: t('biz.usage.dateRequired') };
  const quantity = decimal(draft.quantity), cost = decimal(draft.unitCost);
  if (quantity === null || quantity <= 0 || quantity > 100000) return { ok: false, error: t(draft.kind === 'labour' ? 'biz.usage.hoursInvalid' : 'biz.usage.quantityInvalid') };
  if (cost === null) return { ok: false, error: t('biz.usage.costInvalid') };
  return { ok: true, value: { project_id: projectId, kind: draft.kind, description, occurred_on: draft.occurredOn, quantity, unit_cost: cost, currency: draft.currency, billable: draft.billable } };
}

/** Quantity times unit cost in integer cents, rounded half up like the API (a preview only). */
export function usageAmount(quantity: number, unitCost: number): number {
  const cents = Math.round(quantity * 100) * Math.round(unitCost * 100) / 100;
  return Math.sign(cents) * Math.round(Math.abs(cents) + Number.EPSILON) / 100;
}

/** The last finished month ("2026-09" on any day of October 2026): the only months the import takes. */
export function lastFinishedMonth(now: Date): string {
  const year = now.getUTCMonth() === 0 ? now.getUTCFullYear() - 1 : now.getUTCFullYear();
  const month = now.getUTCMonth() === 0 ? 12 : now.getUTCMonth();
  return `${year}-${String(month).padStart(2, '0')}`;
}

export function aiImportBody(projectId: string, month: string, share: string, billable: boolean, now: Date): Check<Record<string, unknown>> {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month) || month > lastFinishedMonth(now)) return { ok: false, error: t('biz.usage.monthInvalid') };
  const pct = decimal(share || '100');
  if (pct === null || pct <= 0 || pct > 100) return { ok: false, error: t('biz.usage.shareInvalid') };
  return { ok: true, value: { project_id: projectId, month, share_pct: pct, billable } };
}
