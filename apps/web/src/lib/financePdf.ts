// PDF statement review logic (docs/finance.md#pdf-statements), pure so `pnpm --filter @anum/web test:unit` covers it.
// The API reads the PDF and checks its balances; while the person edits or drops rows, this file
// repeats that check in whole minor units (never floating point) so the warning follows every edit.
// The API checks again when the rows are imported.
import type { FinancePdfCheck, FinancePdfImportInput, FinancePdfRole, FinancePdfRow, FinancePdfStatement, FinanceRowFx, FinanceRowMatch } from '@anum/contracts';
import { PDF_STATEMENT_MAX_BYTES, minorDigits, parseAmount } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';

export const PDF_MAX_BYTES = PDF_STATEMENT_MAX_BYTES;
export const PDF_ROLES: readonly FinancePdfRole[] = ['date', 'description', 'debit', 'credit', 'amount', 'balance', 'reference', 'currency',
  'foreign_amount', 'fx_rate', 'fee', 'vat', 'skip'];
export const LOW_CONFIDENCE = 0.6;
export const PDF_PAGE_SIZE = 25;

export function isPdf(file: { name: string; type?: string }): boolean {
  return /\.pdf$/i.test(file.name) || file.type === 'application/pdf';
}

/** One row as the person reviews it: amounts stay text until they are imported. */
export interface ReviewRow {
  key: string;
  line: number;
  page: number;
  include: boolean;
  posted_on: string;
  description: string;
  amount: string;
  billed_amount: string | null;
  billed_currency: string | null;
  balance: string | null;
  reference: string | null;
  currency: string | null;
  time: string | null;
  kind: string | null;
  counterparty: string | null;
  parent_line: number | null;
  original_amount: string | null;
  original_currency: string | null;
  fx_rate: string | null;
  fee: string | null;
  vat: string | null;
  confidence: number;
  duplicate: boolean;
  flags: string[];
  /** The matching engine's answer when the statement was read (docs/finance.md#statement-matching). */
  match: FinanceRowMatch | null;
  fx: FinanceRowFx | null;
}

/** The person's ticks on a row's match, sent with the row (the API reads them per row). */
export interface PdfChoice { action?: 'import' | 'skip' | null; bill?: boolean | null; transfer?: boolean | null; correct?: boolean | null; split?: string[] | null }

export function reviewRows(view: Pick<FinancePdfStatement, 'rows'>): ReviewRow[] {
  return view.rows.map((row: FinancePdfRow) => ({
    key: `${row.page}:${row.line}`, line: row.line, page: row.page,
    // A row in another currency than the account's cannot be imported into it: left out until the owner decides.
    include: !row.flags.includes('foreign_currency'), posted_on: row.posted_on ?? '', description: row.description,
    amount: row.amount ?? '', billed_amount: null, billed_currency: null, balance: row.balance, reference: row.reference, currency: row.currency, time: row.time, kind: row.kind,
    counterparty: row.counterparty, parent_line: row.parent_line, original_amount: row.original_amount, original_currency: row.original_currency,
    fx_rate: row.fx_rate, fee: row.fee, vat: row.vat, confidence: row.confidence, duplicate: row.duplicate, flags: row.flags,
    match: row.match ?? null, fx: row.fx ?? null,
  }));
}

/** A wire amount ("-1234.50") in whole minor units; null when it is not a number. */
export function toMinor(value: string | null | undefined, currency: string): bigint | null {
  if (value === null || value === undefined) return null;
  const parsed = parseAmount(value, currency);
  if (!parsed.ok) return null;
  const digits = minorDigits(currency);
  const negative = parsed.value.startsWith('-');
  const [whole, fraction = ''] = parsed.value.replace('-', '').split('.');
  const units = BigInt(whole) * 10n ** BigInt(digits) + BigInt((fraction + '0'.repeat(digits)).slice(0, digits) || '0');
  return negative ? -units : units;
}

/** Minor units back to a wire amount: -123450n in AED is "-1234.50". */
export function fromMinor(units: bigint, currency: string): string {
  const digits = minorDigits(currency);
  const negative = units < 0n;
  const text = (negative ? -units : units).toString().padStart(digits + 1, '0');
  const whole = digits ? text.slice(0, -digits) : text;
  const fraction = digits ? `.${text.slice(-digits)}` : '';
  return `${negative ? '-' : ''}${whole}${fraction}`;
}

/** A warning only. Original bank evidence takes priority over a merchant label. */
export function directionHint(row: Pick<ReviewRow, 'amount' | 'description'>): string | null {
  if (!/^[+]?(?:[0-9]|[٠-٩])/.test(row.amount.trim())) return null;
  if (/(?:room[ -]?rent|rent[ -]?room|room[ -]?rental|bed[ -]?space|accommodation[ -]?rent|إيجار\s*غرفة|ايجار\s*غرفة)/i.test(row.description))
    return 'Room rent is shown as incoming. Check the bank statement and change the sign if you paid it.';
  return null;
}

/** What is wrong with a row before it can be imported, or null. */
export function rowProblem(row: ReviewRow, currency: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(row.posted_on)) return t('fpdf.problem.date');
  if (!row.description.trim()) return t('fpdf.problem.description');
  if (row.currency && row.currency !== currency && (!row.billed_amount || row.billed_currency !== currency))
    return `Enter the bank-billed ${currency} amount for this ${row.currency} transaction.`;
  const units = toMinor(row.currency && row.currency !== currency ? row.billed_amount : row.billed_amount || row.amount, currency);
  if (units === null) return t('fpdf.problem.amount');
  if (row.billed_amount && (toMinor(row.amount, row.currency || currency) ?? 0n) * units < 0n)
    return "The original and billed amounts must both be charges or both be credits.";
  if (units === 0n) return t('fpdf.problem.zero');
  return null;
}

export interface LiveCheck {
  status: 'matched' | 'mismatch' | 'unknown';
  count: number;
  total: string;
  expected: string | null;
  difference: string | null;
  /** Lines after which the printed balance disagrees with the running balance. */
  breaks: number[];
}

/** Opening plus the included rows against the closing balance, as the API checks it. */
export function liveCheck(rows: readonly ReviewRow[], opening: string, closing: string, currency: string): LiveCheck {
  const included = rows.filter((row) => row.include);
  const amounts = included.map((row) => toMinor(row.billed_amount || row.amount, currency));
  const total = amounts.reduce<bigint>((sum, value) => sum + (value ?? 0n), 0n);
  const open = opening.trim() ? toMinor(opening, currency) : null;
  const close = closing.trim() ? toMinor(closing, currency) : null;
  const breaks: number[] = [];
  let running = open;
  included.forEach((row, index) => {
    const amount = amounts[index];
    if (running !== null && amount !== null) running += amount;
    const balance = toMinor(row.balance, currency);
    if (balance !== null) {
      if (running !== null && running !== balance) breaks.push(row.line);
      running = balance;
    }
  });
  const base = { count: included.length, total: fromMinor(total, currency), breaks };
  if (open === null || close === null || included.length === 0) return { ...base, status: 'unknown', expected: null, difference: null };
  const expected = open + total;
  const difference = close - expected;
  const unsure = included.some((row) => row.flags.includes('sign_unsure'));
  const status = difference === 0n && breaks.length === 0 && !amounts.includes(null) ? (unsure ? 'unknown' : 'matched') : 'mismatch';
  return { ...base, status, expected: fromMinor(expected, currency), difference: fromMinor(difference, currency) };
}

/** The import request for the included rows, amounts in the API's wire form. */
export function importBody(view: FinancePdfStatement, accountId: string, rows: readonly ReviewRow[], opening: string, closing: string,
  acceptMismatch: boolean, layout: FinancePdfImportInput['layout'] = null, choices: Readonly<Record<string, PdfChoice>> = {}): FinancePdfImportInput {
  const currency = view.currency;
  const wire = (value: string, inCurrency = currency) => {
    const units = toMinor(value, inCurrency);
    return units === null ? null : fromMinor(units, inCurrency);
  };
  return {
    account_id: accountId, filename: view.filename, file_sha256: view.file_sha256, source: view.source,
    // Unticked rows are sent too (include: false) so identical rows keep their place; the server never imports them.
    rows: rows.filter((row) => row.include || sendable(row, currency)).map((row) => ({
      posted_on: row.posted_on, description: row.description.trim(), amount: wire(row.amount, row.currency || currency) ?? row.amount,
      balance: row.balance, reference: row.reference, currency: row.currency, time: row.time,
      original_amount: row.original_amount || (row.currency && row.currency !== currency ? row.amount : null),
      original_currency: row.original_currency || (row.currency && row.currency !== currency ? row.currency : null),
      billed_amount: row.billed_amount ? (wire(row.billed_amount) ?? row.billed_amount) : null,
      billed_currency: row.billed_amount ? currency : null,
      fx_rate: row.fx_rate, fee: row.fee, vat: row.vat,
      ...(row.include ? choiceFields(choices[row.key]) : { action: 'skip' as const, include: false }),
    })),
    opening_balance: opening.trim() ? wire(opening) : null, closing_balance: closing.trim() ? wire(closing) : null, accept_mismatch: acceptMismatch,
    layout,
    // The statement's printed counts and totals: the server checks the rows against them again.
    summary: view.check.summary.map((item) => ({ name: item.name, stated: item.stated })),
    dropped: (view.dropped_rows ?? []).map((item) => ({ page: item.page, reason: item.reason, text: item.text, posted_on: item.posted_on, amount: item.amount })),
    // The printed period, stored with the import for reconciliation and connection health (docs/finance.md#reconciliation-sessions).
    period_start: view.period_start ?? null, period_end: view.period_end ?? null,
  };
}

/** An unticked row is sent only when its date and amount read, so the server can place it among identical rows. */
function sendable(row: ReviewRow, currency: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(row.posted_on) && toMinor(row.amount, row.currency || currency) !== null && row.description.trim() !== '';
}

function choiceFields(choice: PdfChoice | undefined): Partial<Pick<FinancePdfImportInput['rows'][number], 'action' | 'bill' | 'transfer' | 'correct' | 'split'>> {
  if (!choice) return {};
  const out: Partial<Pick<FinancePdfImportInput['rows'][number], 'action' | 'bill' | 'transfer' | 'correct' | 'split'>> = {};
  if (choice.action) out.action = choice.action;
  if (choice.bill === false) out.bill = false;
  if (typeof choice.transfer === 'boolean') out.transfer = choice.transfer;
  if (choice.correct === false) out.correct = false;
  if (choice.split?.length && choice.action !== 'skip') out.split = [...choice.split];
  return out;
}

/** Rows whose description or reference contains the search text. */
export function filterRows(rows: readonly ReviewRow[], query: string): ReviewRow[] {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return [...rows];
  return rows.filter((row) => row.description.toLowerCase().includes(wanted) || (row.reference ?? '').toLowerCase().includes(wanted));
}

export function roleLabel(role: FinancePdfRole): string {
  return t(`fpdf.role.${role}` as MessageKey);
}

export function flagLabel(flag: string): string | null {
  const known = ['sign_unsure', 'sign_from_balance', 'date_unreadable', 'amount_missing', 'amount_zero', 'hijri_date', 'balance_break',
    'foreign_currency', 'columns_swapped', 'low_confidence'];
  return known.includes(flag) ? t(`fpdf.flag.${flag}` as MessageKey) : null;
}

/** The error code the API gave a PDF it could not read (`pdf_password_required` and friends), or null. */
export function pdfErrorCode(error: unknown): string | null {
  const details = (error as { details?: { type?: string | null }[] } | null)?.details;
  const found = Array.isArray(details) ? details.find((detail) => typeof detail.type === 'string' && detail.type.startsWith('pdf_')) : undefined;
  return found?.type ? found.type.slice(4) : null;
}

export type SummaryLine = FinancePdfCheck['summary'][number];

/** The statement's own counts and totals against the included rows, as the owner edits them. */
export function liveSummary(stated: readonly SummaryLine[], rows: readonly ReviewRow[], opening: string, currency: string): SummaryLine[] {
  const amounts = rows.filter((row) => row.include).map((row) => toMinor(row.billed_amount || row.amount, currency)).filter((v): v is bigint => v !== null);
  const credits = amounts.filter((v) => v > 0n), debits = amounts.filter((v) => v < 0n);
  const sum = (values: bigint[]) => values.reduce((a, b) => a + b, 0n);
  return stated.map((item) => {
    let found: string;
    if (item.name === 'deposits_count') found = String(credits.length);
    else if (item.name === 'withdrawals_count') found = String(debits.length);
    else if (item.name === 'deposits_total') found = fromMinor(sum(credits), currency);
    else if (item.name === 'withdrawals_total') found = fromMinor(-sum(debits), currency);
    else found = fromMinor(-(toMinor(opening, currency) ?? 0n) - sum(amounts), currency); // card: previous balance plus charges
    const ok = item.name.endsWith('_count') ? found === String(Number(item.stated)) : toMinor(found, currency) === toMinor(item.stated, currency);
    return { ...item, found, ok };
  });
}

/** "USD 200.00 at 3.75, fee 15.00, VAT 2.25" from a card row's printed details, or null. */
export function foreignDetail(row: Pick<ReviewRow, 'original_amount' | 'original_currency' | 'fx_rate' | 'fee' | 'vat'>): string | null {
  const parts: string[] = [];
  if (row.original_amount) parts.push(t('fpdf.detail.original', { amount: `${row.original_currency ?? ''} ${row.original_amount}`.trim() }));
  if (row.fx_rate) parts.push(t('fpdf.detail.rate', { rate: row.fx_rate }));
  if (row.fee) parts.push(t('fpdf.detail.fee', { fee: row.fee }));
  if (row.vat) parts.push(t('fpdf.detail.vat', { vat: row.vat }));
  return parts.length ? parts.join(', ') : null;
}

/** Roles for a re-read: exactly one date and at least one money column, or a message saying what to fix. */
export function checkRoles(roles: readonly FinancePdfRole[]): string | null {
  if (roles.filter((role) => role === 'date').length !== 1) return t('fpdf.mapping.oneDate');
  if (!roles.some((role) => role === 'debit' || role === 'credit' || role === 'amount' || role === 'balance')) return t('fpdf.mapping.money');
  if (roles.includes('amount') && (roles.includes('debit') || roles.includes('credit'))) return t('fpdf.mapping.either');
  for (const role of ['description', 'balance', 'reference', 'amount', 'debit', 'credit', 'currency', 'foreign_amount', 'fx_rate', 'fee', 'vat'] as const) {
    if (roles.filter((r) => r === role).length > 1) return t('fpdf.mapping.once');
  }
  return null;
}
