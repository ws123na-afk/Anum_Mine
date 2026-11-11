// Recurring invoices on the web (docs/business.md#recurring-invoices): pure helpers, covered by test/invoiceSchedules.test.ts.
// Every figure, date and status is the API's; the web only turns the person's form into the request body and labels.
import type {
  BusinessCurrency, InvoiceAutomationSummary, InvoiceSchedule, InvoiceScheduleFields, ScheduleFrequency, ScheduleIssueMode, ScheduleLine, ScheduleStatus,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';
import type { Tone } from './business.ts';
import { manualLines, type Check, type ManualLineDraft } from './invoices.ts';

export const SCHEDULE_FREQUENCIES: readonly ScheduleFrequency[] = ['monthly', 'quarterly', 'yearly', 'weeks', 'days', 'months'];
export const SCHEDULE_STATUSES: readonly ScheduleStatus[] = ['active', 'paused', 'ended'];
export type EndKind = 'never' | 'date' | 'count';

export interface ScheduleDraft {
  title: string; accountId: string; currency: BusinessCurrency; frequency: ScheduleFrequency; interval: string; startDate: string;
  end: EndKind; endDate: string; maxCount: string; dueDays: string; issueMode: ScheduleIssueMode; reference: string; notes: string; terms: string;
  lines: ManualLineDraft[];
}

export function emptySchedule(startDate: string, taxPct = ''): ScheduleDraft {
  return { title: '', accountId: '', currency: 'SAR', frequency: 'monthly', interval: '1', startDate, end: 'never', endDate: '', maxCount: '', dueDays: '',
    issueMode: 'draft', reference: '', notes: '', terms: '', lines: [{ description: '', kind: 'service', quantity: '1', unitPrice: '', taxPct }] };
}

export function draftOf(schedule: InvoiceSchedule): ScheduleDraft {
  return {
    title: schedule.title, accountId: schedule.account_id, currency: schedule.currency, frequency: schedule.frequency, interval: String(schedule.interval),
    startDate: schedule.start_date, end: schedule.end_date ? 'date' : schedule.max_count ? 'count' : 'never', endDate: schedule.end_date ?? '',
    maxCount: schedule.max_count ? String(schedule.max_count) : '', dueDays: String(schedule.due_in_days), issueMode: schedule.issue_mode,
    reference: schedule.customer_reference, notes: schedule.notes, terms: schedule.payment_terms,
    lines: schedule.lines.map((line) => ({ description: line.description, kind: line.kind ?? 'service', quantity: String(line.quantity), unitPrice: String(line.unit_price),
      taxPct: String(line.tax_rate_pct), taxCode: line.tax_code ?? '' })),
  };
}

/** Whether the interval box applies (monthly, quarterly and yearly repeat every one period). */
export const hasInterval = (frequency: ScheduleFrequency) => frequency === 'days' || frequency === 'weeks' || frequency === 'months';
const INTERVAL_MAX: Record<string, number> = { days: 366, weeks: 52, months: 24 };
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The request body, or the first problem in the person's words. */
export function scheduleBody(draft: ScheduleDraft): Check<InvoiceScheduleFields> {
  if (!draft.accountId) return { ok: false, error: t('rinv.err.account') };
  if (!ISO_DAY.test(draft.startDate)) return { ok: false, error: t('rinv.err.start') };
  const lines = manualLines(draft.lines);
  if (!lines.ok) return lines;
  if (lines.value.length === 0) return { ok: false, error: t('rinv.err.lines') };
  const body: InvoiceScheduleFields = {
    title: draft.title.trim() || undefined, account_id: draft.accountId, currency: draft.currency, frequency: draft.frequency, start_date: draft.startDate,
    issue_mode: draft.issueMode, customer_reference: draft.reference.trim(), notes: draft.notes.trim(), payment_terms: draft.terms.trim(),
    lines: lines.value.map((line): ScheduleLine => ({ description: line.description, kind: line.kind, quantity: line.quantity ?? 1, unit_price: line.unit_price,
      tax_rate_pct: line.tax_rate_pct ?? 0, tax_code: line.tax_code ?? null })),
    end_date: null, max_count: null,
  };
  if (hasInterval(draft.frequency)) {
    const every = Number(draft.interval);
    if (!Number.isInteger(every) || every < 1 || every > INTERVAL_MAX[draft.frequency]) return { ok: false, error: t('rinv.err.interval', { max: INTERVAL_MAX[draft.frequency] }) };
    body.interval = every;
  }
  if (draft.end === 'date') {
    if (!ISO_DAY.test(draft.endDate) || draft.endDate < draft.startDate) return { ok: false, error: t('rinv.err.endDate') };
    body.end_date = draft.endDate;
  } else if (draft.end === 'count') {
    const count = Number(draft.maxCount);
    if (!Number.isInteger(count) || count < 1 || count > 1000) return { ok: false, error: t('rinv.err.count') };
    body.max_count = count;
  }
  if (draft.dueDays.trim() !== '') {
    const due = Number(draft.dueDays);
    if (!Number.isInteger(due) || due < 0 || due > 365) return { ok: false, error: t('rinv.err.due') };
    body.due_in_days = due;
  }
  return { ok: true, value: body };
}

/** "Every month", "Every 2 weeks", in the person's language. */
export function frequencyText(schedule: Pick<InvoiceSchedule, 'frequency' | 'interval'>): string {
  if (!hasInterval(schedule.frequency) || schedule.interval === 1) return t(`rinv.freq.every.${schedule.frequency}` as MessageKey);
  return t(`rinv.freq.everyN.${schedule.frequency}` as MessageKey, { count: schedule.interval });
}

export const statusLabel = (status: ScheduleStatus) => t(`rinv.status.${status}` as MessageKey);
export const statusTone = (status: ScheduleStatus): Tone => (status === 'active' ? 'success' : status === 'paused' ? 'warning' : 'neutral');

/** What the schedule does next, in one line. */
export function nextText(schedule: Pick<InvoiceSchedule, 'status' | 'next_date' | 'end_reason'>): string {
  if (schedule.status === 'paused') return t('rinv.next.paused');
  if (schedule.status === 'ended') return t(`rinv.next.ended.${schedule.end_reason || 'owner'}` as MessageKey);
  return schedule.next_date ? t('rinv.next.on', { date: schedule.next_date }) : t('rinv.next.none');
}

/** The last daily pass in one sentence; null before it ever ran. */
export function summaryText(summary: InvoiceAutomationSummary | null): string | null {
  if (!summary) return null;
  const parts = [t('rinv.auto.planned', { reminders: summary.reminders_planned, emails: summary.emails_planned })];
  if (summary.skipped_do_not_contact) parts.push(t('rinv.auto.skipped', { count: summary.skipped_do_not_contact }));
  if (summary.failed) parts.push(t('rinv.auto.failed', { count: summary.failed }));
  return parts.join(' ');
}
