// Business invoices (docs/business.md#invoices): labels, tones, the money shown for a figure, form
// drafts and the request bodies. Pure, so `pnpm --filter @anum/web test:unit` covers it without a
// browser. The API is the authority: it recomputes every total and rechecks every rule.
import type {
  Billable, BillingProfile, BusinessCurrency, CreditNoteInput, CreditNoteStatus, InvoiceCreate, InvoiceListItem, InvoicePaymentState, InvoiceStatus, InvoiceSummary,
  InvoiceUpdate, LineKind, ManualInvoiceLine, PaymentInput, PaymentMethod, PaymentNoticeStatus,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';
import { formatMoney, parseAmount, type Tone } from './business.ts';

const label = (key: string) => t(key as MessageKey);

export const INVOICE_STATUSES: readonly InvoiceStatus[] = ['draft', 'pending_approval', 'sent', 'paid', 'void'];
export const PAYMENT_METHODS: readonly PaymentMethod[] = ['bank_transfer', 'cash', 'card', 'cheque', 'other'];
export const LINE_KINDS: readonly LineKind[] = ['service', 'labor', 'external_cost', 'other'];
export const INVOICE_PAGE = 25;
/** The print language choices. */
export const PRINT_LANGUAGES = ['en', 'ar'] as const;
export type PrintLanguage = (typeof PRINT_LANGUAGES)[number];

export const invoiceStatusLabel = (status: InvoiceStatus) => label(`inv.status.${status}`);
export const paymentMethodLabel = (method: PaymentMethod) => label(`inv.method.${method}`);
export const lineKindLabel = (kind: LineKind) => label(`inv.kind.${kind}`);
export const paymentStateLabel = (state: InvoicePaymentState) => label(`inv.payState.${state}`);
export const noticeStatusLabel = (status: PaymentNoticeStatus) => label(`inv.notice.status.${status}`);
export const noticeTone = (status: PaymentNoticeStatus): Tone => (status === 'confirmed' ? 'success' : status === 'rejected' ? 'dangerTone' : 'warning');
export const creditNoteTone = (status: CreditNoteStatus): Tone => (status === 'void' ? 'dangerTone' : 'neutral');

export function invoiceTone(status: InvoiceStatus, overdue = false): Tone {
  if (overdue && status === 'sent') return 'dangerTone';
  if (status === 'paid') return 'success';
  if (status === 'void') return 'dangerTone';
  if (status === 'pending_approval') return 'warning';
  return 'neutral';
}

/** "Overdue by 5 days", or an empty string when the invoice is not late. */
export function overdueText(item: { overdue: boolean; days_overdue: number }): string {
  return item.overdue ? t('inv.overdueDays', { count: item.days_overdue }) : '';
}

/** The hash a detail opens from: `#business-invoices/<id>`. */
export const invoiceHash = (id: string | null) => (id ? `#business-invoices/${encodeURIComponent(id)}` : '#business-invoices');

/** An invoice number, or a plain word for a draft that has none yet. */
export const invoiceTitle = (item: { number: string | null }) => item.number ?? t('inv.draftTitle');

/** Filters of the list, as the query the API reads (`overdue` and `status` exclude each other in the UI). */
export interface InvoiceFilters { status: InvoiceStatus | ''; overdue: boolean; q: string }
export const defaultInvoiceFilters = (): InvoiceFilters => ({ status: '', overdue: false, q: '' });
export const invoiceFiltersActive = (filters: InvoiceFilters) => filters.status !== '' || filters.overdue || filters.q.trim() !== '';
export function invoiceQuery(filters: InvoiceFilters, offset: number): { status?: InvoiceStatus[]; overdue?: boolean; q?: string; limit: number; offset: number } {
  return {
    ...(filters.status && !filters.overdue ? { status: [filters.status] } : {}),
    ...(filters.overdue ? { overdue: true } : {}),
    ...(filters.q.trim() ? { q: filters.q.trim() } : {}),
    limit: INVOICE_PAGE, offset,
  };
}

// ---- summary tiles: one block per currency, never added together ----------------------------

export interface CurrencyTiles { currency: BusinessCurrency; tiles: { id: string; label: string; value: string; hint: string; tone: 'danger' | 'ok' | '' }[] }

export function summaryTiles(summary: InvoiceSummary): CurrencyTiles[] {
  return summary.currencies.map((row) => ({
    currency: row.currency,
    tiles: [
      { id: 'outstanding', label: t('inv.sum.outstanding'), value: formatMoney(row.outstanding, row.currency), hint: t('inv.sum.outstandingHint'), tone: '' },
      { id: 'overdue', label: t('inv.sum.overdue'), value: formatMoney(row.overdue, row.currency), hint: t('inv.sum.overdueHint', { count: row.overdue_count }), tone: row.overdue_count > 0 ? 'danger' : '' },
      { id: 'invoiced', label: t('inv.sum.invoiced'), value: formatMoney(row.invoiced_this_month, row.currency), hint: t('inv.sum.monthHint'), tone: '' },
      { id: 'collected', label: t('inv.sum.collected'), value: formatMoney(row.collected_this_month, row.currency), hint: t('inv.sum.monthHint'), tone: 'ok' },
      { id: 'drafts', label: t('inv.sum.drafts'), value: formatMoney(row.drafts, row.currency), hint: t('inv.sum.draftsHint', { count: summary.counts.draft ?? 0 }), tone: '' },
    ],
  }));
}

// ---- totals shown next to the lines (the API computes the real ones) ---------------------------

/** Net, tax and total of one line the way the server rounds them (2 decimals, half up). */
export function lineTotals(quantity: number, unitPrice: number, taxPct: number): { net: number; tax: number; total: number } {
  const cents = (value: number) => Math.round((value + Number.EPSILON) * 100) / 100;
  const net = cents(quantity * unitPrice);
  const tax = cents(net * taxPct / 100);
  return { net, tax, total: cents(net + tax) };
}

// ---- new invoice and edit forms --------------------------------------------------------------

export interface ManualLineDraft { description: string; kind: LineKind; quantity: string; unitPrice: string; taxPct: string }
export const emptyManualLine = (taxPct = ''): ManualLineDraft => ({ description: '', kind: 'service', quantity: '1', unitPrice: '', taxPct });

export interface FieldsDraft {
  dueDays: string; reference: string; notes: string; terms: string; billName: string; billAddress: string; billEmail: string; billTax: string; taxRate: string;
}
export const emptyFields = (dueDays = '', taxRate = ''): FieldsDraft => ({ dueDays, reference: '', notes: '', terms: '', billName: '', billAddress: '', billEmail: '', billTax: '', taxRate });

export interface NewInvoiceDraft extends FieldsDraft {
  currency: BusinessCurrency; milestoneIds: string[]; contractAmount: string; usageIds: string[]; markup: string; offerId: string; offerQty: string; offerPrice: string; lines: ManualLineDraft[];
}
export const emptyNewInvoice = (): NewInvoiceDraft => ({ ...emptyFields(), currency: 'SAR', milestoneIds: [], contractAmount: '', usageIds: [], markup: '0', offerId: '', offerQty: '1', offerPrice: '', lines: [] });

export type Check<T> = { ok: true; value: T } | { ok: false; error: string };

function percent(text: string): number | null {
  const value = Number(text.trim());
  return text.trim() !== '' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}
function quantity(text: string): number | null {
  const value = Number(text.trim());
  return text.trim() !== '' && Number.isFinite(value) && value > 0 && value <= 1_000_000 ? value : null;
}

export function manualLines(drafts: readonly ManualLineDraft[]): Check<ManualInvoiceLine[]> {
  const lines: ManualInvoiceLine[] = [];
  for (const draft of drafts) {
    if (!draft.description.trim() && !draft.unitPrice.trim()) continue;
    const price = parseAmount(draft.unitPrice);
    const qty = quantity(draft.quantity);
    const tax = draft.taxPct.trim() === '' ? undefined : percent(draft.taxPct);
    if (!draft.description.trim()) return { ok: false, error: t('inv.form.lineDescription') };
    if (price === null) return { ok: false, error: t('inv.form.linePrice') };
    if (qty === null) return { ok: false, error: t('inv.form.lineQuantity') };
    if (tax === null) return { ok: false, error: t('inv.form.taxInvalid') };
    lines.push({ description: draft.description.trim(), kind: draft.kind, quantity: qty, unit_price: price, ...(tax !== undefined ? { tax_rate_pct: tax } : {}) });
  }
  return { ok: true, value: lines };
}

function fieldsBody(draft: FieldsDraft, sendEmpty: boolean): Check<Record<string, unknown>> {
  const due = Number(draft.dueDays.trim());
  if (draft.dueDays.trim() !== '' && (!Number.isInteger(due) || due < 0 || due > 365)) return { ok: false, error: t('inv.form.dueInvalid') };
  const rate = draft.taxRate.trim() === '' ? undefined : percent(draft.taxRate);
  if (rate === null) return { ok: false, error: t('inv.form.taxInvalid') };
  const text = (value: string) => (value.trim() === '' && !sendEmpty ? undefined : value.trim());
  const body: Record<string, unknown> = {
    due_in_days: draft.dueDays.trim() === '' ? undefined : due, customer_reference: text(draft.reference), notes: text(draft.notes), payment_terms: text(draft.terms),
    bill_to_name: text(draft.billName), bill_to_address: text(draft.billAddress), bill_to_email: text(draft.billEmail), bill_to_tax_number: text(draft.billTax),
    tax_rate_pct: rate,
  };
  for (const key of Object.keys(body)) if (body[key] === undefined) delete body[key];
  return { ok: true, value: body };
}

export interface InvoiceScope { accountId: string; projectId: string | null }

/** The body of POST /invoices, or what to fix. What is ticked decides which source fields are sent. */
export function createBody(scope: InvoiceScope, draft: NewInvoiceDraft, billable: Billable): Check<InvoiceCreate> {
  const fields = fieldsBody(draft, false);
  if (!fields.ok) return fields;
  const lines = manualLines(draft.lines);
  if (!lines.ok) return lines;
  const body: InvoiceCreate = { ...(fields.value as InvoiceCreate), ...(scope.projectId ? { project_id: scope.projectId } : { account_id: scope.accountId, currency: draft.currency }) };
  const chosen = draft.milestoneIds.filter((id) => billable.milestones.some((item) => item.id === id && item.billable));
  if (chosen.length) body.milestone_ids = chosen;
  if (billable.contract && draft.contractAmount.trim()) {
    const value = parseAmount(draft.contractAmount);
    if (value === null || value <= 0) return { ok: false, error: t('inv.form.contractInvalid') };
    if (value > billable.contract.remaining + 0.0001) return { ok: false, error: t('inv.form.contractTooMuch', { amount: formatMoney(billable.contract.remaining, billable.currency) }) };
    body.contract_amount = value;
  }
  const usage = draft.usageIds.filter((id) => billable.usage.some((item) => item.id === id && item.billable));
  if (usage.length) {
    const markup = percent(draft.markup || '0');
    if (markup === null) return { ok: false, error: t('inv.form.markupInvalid') };
    body.usage_ids = usage; body.usage_markup_pct = markup;
  }
  if (draft.offerId) {
    const offer = billable.offers.find((item) => item.id === draft.offerId);
    const qty = quantity(draft.offerQty);
    if (qty === null) return { ok: false, error: t('inv.form.lineQuantity') };
    body.offer_id = draft.offerId; body.offer_quantity = qty;
    if (offer && offer.price_type !== 'fixed') {
      const price = parseAmount(draft.offerPrice);
      if (price === null || price <= 0) return { ok: false, error: t('inv.form.offerPrice') };
      body.offer_unit_price = price;
    }
  }
  if (lines.value.length) body.lines = lines.value;
  if (!body.milestone_ids && body.contract_amount === undefined && !body.usage_ids && !body.offer_id && !body.lines) return { ok: false, error: t('inv.form.nothingChosen') };
  return { ok: true, value: body };
}

/** The body of PATCH /invoices/{id}: every text field is sent (an empty one clears it), the removed lines by position. */
export function updateBody(draft: FieldsDraft, newLines: readonly ManualLineDraft[], removed: readonly number[], replaceManual: boolean): Check<InvoiceUpdate> {
  const fields = fieldsBody(draft, true);
  if (!fields.ok) return fields;
  const lines = manualLines(newLines);
  if (!lines.ok) return lines;
  const body: InvoiceUpdate = { ...(fields.value as InvoiceUpdate) };
  if (lines.value.length || replaceManual) body.lines = lines.value;
  if (removed.length) body.remove_lines = [...removed].sort((a, b) => a - b);
  return { ok: true, value: body };
}

// ---- payments ---------------------------------------------------------------------------------

export interface PaymentDraft { amount: string; paidOn: string; method: PaymentMethod; reference: string; note: string }
export const emptyPayment = (balance: number, today: string): PaymentDraft => ({ amount: balance > 0 ? balance.toFixed(2) : '', paidOn: today, method: 'bank_transfer', reference: '', note: '' });

/** The body of POST /invoices/{id}/payments. A payment above the balance is refused here and again by the API. */
export function paymentBody(draft: PaymentDraft, balance: number, currency: BusinessCurrency): Check<PaymentInput> {
  const amount = parseAmount(draft.amount);
  if (amount === null || amount <= 0) return { ok: false, error: t('inv.pay.amountInvalid') };
  if (amount > balance + 0.0001) return { ok: false, error: t('inv.pay.tooMuch', { balance: formatMoney(balance, currency) }) };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.paidOn)) return { ok: false, error: t('inv.pay.dateInvalid') };
  return { ok: true, value: { amount, paid_on: draft.paidOn, method: draft.method, reference: draft.reference.trim(), note: draft.note.trim() } };
}

// ---- credit notes -----------------------------------------------------------------------------

export interface CreditDraft { amount: string; reason: string }
export const emptyCredit = (balance: number): CreditDraft => ({ amount: balance > 0 ? balance.toFixed(2) : '', reason: '' });

/** The body of POST /invoices/{id}/credit-notes. More than the balance is refused here and again by the API (a credit note never leaves a refund owed). */
export function creditBody(draft: CreditDraft, balance: number, currency: BusinessCurrency): Check<CreditNoteInput> {
  const amount = parseAmount(draft.amount);
  if (amount === null || amount <= 0) return { ok: false, error: t('inv.credit.amountInvalid') };
  if (amount > balance + 0.0001) return { ok: false, error: t('inv.credit.tooMuch', { balance: formatMoney(balance, currency) }) };
  const reason = draft.reason.replace(/\s+/g, ' ').trim();
  if (reason.length < 3) return { ok: false, error: t('inv.credit.reasonInvalid') };
  return { ok: true, value: { amount, reason } };
}

/** How many customer reports the Invoices list can open at once: the pending ones of a page. */
export const noticesWaiting = (items: readonly { notices_pending: number }[]) => items.reduce((sum, item) => sum + item.notices_pending, 0);

// ---- billing profile --------------------------------------------------------------------------

export type ProfileDraft = Record<keyof BillingProfile, string>;
export const profileDraft = (profile: BillingProfile): ProfileDraft => ({
  legal_name: profile.legal_name, address: profile.address, tax_label: profile.tax_label, tax_number: profile.tax_number, email: profile.email, phone: profile.phone,
  bank_details: profile.bank_details, footer_note: profile.footer_note, invoice_prefix: profile.invoice_prefix, default_due_days: String(profile.default_due_days),
  default_tax_rate_pct: String(profile.default_tax_rate_pct),
});

export function profileBody(draft: ProfileDraft): Check<BillingProfile> {
  const due = Number(draft.default_due_days.trim());
  if (!Number.isInteger(due) || due < 0 || due > 365) return { ok: false, error: t('inv.form.dueInvalid') };
  const rate = percent(draft.default_tax_rate_pct);
  if (rate === null) return { ok: false, error: t('inv.form.taxInvalid') };
  if (!/^[A-Za-z0-9]{1,8}$/.test(draft.invoice_prefix.trim())) return { ok: false, error: t('inv.profile.prefixInvalid') };
  return {
    ok: true,
    value: {
      legal_name: draft.legal_name.trim(), address: draft.address.trim(), tax_label: draft.tax_label.trim(), tax_number: draft.tax_number.trim(), email: draft.email.trim(),
      phone: draft.phone.trim(), bank_details: draft.bank_details.trim(), footer_note: draft.footer_note.trim(), invoice_prefix: draft.invoice_prefix.trim(),
      default_due_days: due, default_tax_rate_pct: rate,
    },
  };
}

/** Today as `YYYY-MM-DD` in the browser's calendar (the date a payment arrived defaults to it). */
export function today(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, '0'), day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

/** What the list shows in the "when" column: due date for open invoices, issue date otherwise. */
export const listDate = (item: Pick<InvoiceListItem, 'status' | 'due_date' | 'issue_date'>) => (item.status === 'sent' ? item.due_date : item.issue_date);
