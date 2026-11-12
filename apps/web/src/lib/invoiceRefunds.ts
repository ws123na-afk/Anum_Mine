// Refunds of a Business invoice's card payment asked for from ANUM, and provider refunds ANUM could not match
// (docs/business.md#refunds-from-anum, docs/business.md#refunds-anum-could-not-match). Pure helpers, tested in
// test/invoiceRefunds.test.ts; the screens are BusinessInvoiceRefunds.tsx and BusinessRefundReviews.tsx.
import type { BusinessCurrency, BusinessInvoice, InvoicePayment, RefundAsk, RefundRequest, RefundRequestStatus, RefundReview } from '@anum/contracts';
import { formatMoney, parseAmount, type Tone } from './business.ts';
import { t, type MessageKey } from './i18n.ts';
import type { Check } from './invoices.ts';

export interface RefundDraft { amount: string; reason: string }

/** The form starts at the whole amount the payment can still give back. */
export const emptyRefund = (max: number): RefundDraft => ({ amount: max > 0 ? max.toFixed(2) : '', reason: '' });

/** The body of POST .../refund-requests. More than the payment can give back is refused here and again by the API. */
export function refundBody(draft: RefundDraft, max: number, currency: BusinessCurrency): Check<RefundAsk> {
  const amount = parseAmount(draft.amount);
  if (amount === null || amount <= 0 || amount > max + 0.0001) return { ok: false, error: t('inv.rr.errAmount', { max: formatMoney(max, currency) }) };
  const reason = draft.reason.replace(/\s+/g, ' ').trim();
  if (reason.length < 3) return { ok: false, error: t('inv.rr.errReason') };
  return { ok: true, value: { amount: Math.round(amount * 100) / 100, reason } };
}

/** One key per open refund form: a retried submit is the same request, never a second one. */
export function newRefundKey(random: () => string = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`)): string {
  return `refund-${random()}`.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 80);
}

/** What a card payment can still give back (none for a payment that is not a card payment, or a server without refunds). */
export function refundableOf(invoice: BusinessInvoice, payment: InvoicePayment): number {
  if (!invoice.can.refund || !payment.provider) return 0;
  return invoice.refundable?.[payment.id] ?? 0;
}

export const requestsOf = (invoice: BusinessInvoice, paymentId: string): RefundRequest[] =>
  (invoice.refund_requests ?? []).filter((item) => item.payment_id === paymentId);

const TONES: Record<RefundRequestStatus, Tone> = {
  waiting_approval: 'warning', processing: 'warning', pending_provider: 'warning', succeeded: 'success', failed: 'dangerTone', rejected: 'neutral', cancelled: 'neutral',
};
export const requestTone = (status: RefundRequestStatus): Tone => TONES[status];
export const requestStatusLabel = (status: RefundRequestStatus) => t(`inv.rr.status.${status}` as MessageKey);

export const providerName = (provider: string) => (provider === 'stripe' ? 'Stripe' : 'Moyasar');

/** The review row's money: its own currency (a refund ANUM could not match may be in any currency the provider used). */
export function reviewAmount(review: RefundReview): string {
  return ['SAR', 'AED', 'USD'].includes(review.currency) ? formatMoney(review.amount, review.currency as BusinessCurrency) : `${review.currency} ${review.amount.toFixed(2)}`;
}

/** The route of the review list inside Business, Invoices (#business-invoices/refunds). */
export const REVIEWS_VIEW = 'refunds';
