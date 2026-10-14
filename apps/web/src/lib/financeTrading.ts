// Finance stages 6 to 8 (docs/finance.md): paper and shadow trading (SIMULATED), the execution
// gateway and limited live (off). Pure so `pnpm --filter @anum/web test:unit` covers it. Prices,
// quantities and amounts arrive as decimal strings; the view formats them and never adds currencies.
import type {
  FinanceAmountLimit, FinanceIntent, FinanceIntentStatus, FinanceLiveLimits, FinancePaperOrderStatus, FinanceTradingAssumptions,
} from '@anum/contracts';
import { getLocale, intlLocale, t, type MessageKey } from './i18n.ts';
import { parseAmount } from './finance.ts';

export type Checked<T> = { ok: true; value: T } | { ok: false; error: string };

const SYMBOL = /^[A-Z0-9][A-Z0-9.-]{0,19}$/;

/** A decimal string without trailing zeros ("187.2500000000" to "187.25"), grouped for reading. */
export function formatDecimal(value: string | null | undefined, maxDigits = 10): string {
  if (value === null || value === undefined || !/^-?\d+(\.\d+)?$/.test(value)) return '-';
  const [whole, fraction = ''] = value.replace('-', '').split('.');
  const trimmed = fraction.slice(0, maxDigits).replace(/0+$/, '');
  const grouped = new Intl.NumberFormat(intlLocale(getLocale()), { useGrouping: true, numberingSystem: 'latn' }).format(BigInt(whole));
  return `${value.startsWith('-') ? '-' : ''}${grouped}${trimmed ? `.${trimmed}` : ''}`;
}

/** Basis points as a percentage: "10" or "10.00" to "0.10%". */
export function bpsLabel(bps: string): string {
  const number = Number(bps);
  if (!Number.isFinite(number)) return '-';
  return `${(number / 100).toFixed(2)}%`;
}

/** The fee and slippage line shown with every simulated figure. */
export function assumptionsText(a: Pick<FinanceTradingAssumptions, 'fee_bps' | 'fee_min' | 'slippage_bps'>): string {
  return t('finance.trading.assumptions', { fee: bpsLabel(a.fee_bps), min: formatDecimal(a.fee_min, 2) || '0', slippage: bpsLabel(a.slippage_bps) });
}

export function checkSymbol(input: string, fx: boolean): Checked<string> {
  const symbol = input.trim().toUpperCase();
  if (fx ? !/^[A-Z]{3}$/.test(symbol) : !SYMBOL.test(symbol)) return { ok: false, error: t(fx ? 'finance.error.currency' : 'finance.trading.error.symbol') };
  return { ok: true, value: symbol };
}

/** A positive quantity with at most 8 decimals (the API's precision), Arabic digits allowed. */
export function checkQuantity(input: string): Checked<string> {
  const text = input.trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660)).replace(/٫/g, '.').replace(/[,٬\s]/g, '');
  if (!/^\d{1,9}(\.\d{1,8})?$/.test(text) || Number(text) <= 0) return { ok: false, error: t('finance.trading.error.quantity') };
  return { ok: true, value: text };
}

/** A positive price with at most 10 decimals. */
export function checkPrice(input: string): Checked<string> {
  const text = input.trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660)).replace(/٫/g, '.').replace(/[,٬\s]/g, '');
  if (!/^\d{1,9}(\.\d{1,10})?$/.test(text) || Number(text) <= 0) return { ok: false, error: t('finance.trading.error.price') };
  return { ok: true, value: text };
}

export function checkBps(input: string): Checked<string> {
  const text = input.trim();
  if (!/^\d{1,4}(\.\d{1,2})?$/.test(text) || Number(text) > 1000) return { ok: false, error: t('finance.trading.error.bps') };
  return { ok: true, value: text };
}

export interface OrderDraft { kind: 'security' | 'fx'; symbol: string; currency: string; side: 'buy' | 'sell'; type: 'market' | 'limit'; quantity: string; limit: string }

/** The order form checked the way the API checks it, so the person sees the reason before sending. */
export function checkOrder(draft: OrderDraft): Checked<{ instrument_kind: 'security' | 'fx'; symbol: string; currency: string; side: 'buy' | 'sell'; order_type: 'market' | 'limit'; quantity: string; limit_price: string | null }> {
  const symbol = checkSymbol(draft.symbol, draft.kind === 'fx');
  if (!symbol.ok) return symbol;
  const currency = draft.currency.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(currency)) return { ok: false, error: t('finance.error.currency') };
  if (draft.kind === 'fx' && symbol.value === currency) return { ok: false, error: t('finance.trading.error.sameCurrency') };
  const quantity = checkQuantity(draft.quantity);
  if (!quantity.ok) return quantity;
  let limit: string | null = null;
  if (draft.type === 'limit') {
    const checked = checkPrice(draft.limit);
    if (!checked.ok) return checked;
    limit = checked.value;
  }
  return { ok: true, value: { instrument_kind: draft.kind, symbol: symbol.value, currency, side: draft.side, order_type: draft.type, quantity: quantity.value, limit_price: limit } };
}

export function intentTone(status: FinanceIntentStatus): string {
  if (status === 'executed') return 'success';
  if (status === 'pending_approval' || status === 'executing') return 'neutral';
  return 'warning';
}

export function orderTone(status: FinancePaperOrderStatus): string {
  return status === 'filled' ? 'success' : status === 'waiting' ? 'neutral' : 'warning';
}

/** "Buy 10 EXMPL", "Sell 0.5 USD": what a request asks for, in the person's language. */
export function intentLine(intent: Pick<FinanceIntent, 'side' | 'quantity' | 'symbol' | 'order_type' | 'limit_price' | 'currency' | 'kind'>): string {
  if (intent.kind === 'live_profile') return t('finance.live.changeLine');
  const base = t(intent.side === 'sell' ? 'finance.trading.sellLine' : 'finance.trading.buyLine', { quantity: formatDecimal(intent.quantity, 8), symbol: intent.symbol ?? '' });
  return intent.order_type === 'limit' && intent.limit_price
    ? t('finance.trading.limitLine', { order: base, limit: formatDecimal(intent.limit_price), currency: intent.currency ?? '' })
    : base;
}

/** A gateway or simulation reason code in the person's language (the server's text otherwise). */
export function codeText(code: string | null | undefined, fallback: string | null | undefined): string {
  if (!code) return fallback ?? '';
  const key = `finance.trading.code.${code}` as MessageKey;
  const text = t(key);
  return text === key ? fallback ?? code : text;
}

/** Live limits rows to the API shape, checking amounts per currency once each. */
export interface LiveDraft { perAction: FinanceAmountLimit[]; perDay: FinanceAmountLimit[]; perMonth: FinanceAmountLimit[]; currencies: string; instruments: string; accounts: string; coolingHours: string }

export function draftFromLimits(limits: FinanceLiveLimits): LiveDraft {
  return {
    perAction: limits.max_per_action.map((l) => ({ ...l })), perDay: limits.max_per_day.map((l) => ({ ...l })), perMonth: limits.max_per_month.map((l) => ({ ...l })),
    currencies: limits.currencies.join(', '), instruments: limits.instruments.join(', '), accounts: limits.accounts.join(', '), coolingHours: String(limits.cooling_off_hours),
  };
}

function rows(items: FinanceAmountLimit[]): Checked<FinanceAmountLimit[]> {
  const out: FinanceAmountLimit[] = [];
  for (const row of items) {
    if (!row.currency.trim() && !row.amount.trim()) continue;
    const currency = row.currency.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(currency)) return { ok: false, error: t('finance.error.currency') };
    if (out.some((r) => r.currency === currency)) return { ok: false, error: t('finance.guardian.error.twice', { currency }) };
    const amount = parseAmount(row.amount, currency, { signed: false, allowZero: false });
    if (!amount.ok) return amount;
    out.push({ currency, amount: amount.value });
  }
  return { ok: true, value: out };
}

const list = (text: string) => [...new Set(text.split(/[,\n،]/).map((s) => s.trim()).filter(Boolean))];

export function limitsFromDraft(draft: LiveDraft): Checked<FinanceLiveLimits> {
  const perAction = rows(draft.perAction);
  if (!perAction.ok) return perAction;
  const perDay = rows(draft.perDay);
  if (!perDay.ok) return perDay;
  const perMonth = rows(draft.perMonth);
  if (!perMonth.ok) return perMonth;
  const currencies = list(draft.currencies.toUpperCase());
  if (currencies.some((c) => !/^[A-Z]{3}$/.test(c))) return { ok: false, error: t('finance.error.currency') };
  const instruments = list(draft.instruments.toUpperCase());
  if (instruments.some((s) => !SYMBOL.test(s))) return { ok: false, error: t('finance.trading.error.symbol') };
  const hours = Number(draft.coolingHours);
  if (!Number.isInteger(hours) || hours < 1 || hours > 168) return { ok: false, error: t('finance.live.error.hours') };
  return { ok: true, value: { max_per_action: perAction.value, max_per_day: perDay.value, max_per_month: perMonth.value, currencies, instruments, accounts: list(draft.accounts), cooling_off_hours: hours } };
}

/** A fresh idempotency key for one form submission (a retry of the same submission reuses it). */
export function newIdempotencyKey(random: () => number = Math.random): string {
  return `web-${Date.now().toString(36)}-${Math.floor(random() * 2 ** 32).toString(36)}`;
}
