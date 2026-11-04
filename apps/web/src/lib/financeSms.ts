// iPhone SMS banking and the ledger (docs/finance-sms.md): pure view logic, covered by the unit tests.
import type { FinanceSmsCorrection, FinanceSmsMessage, FinanceSmsStatus } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export const SMS_KINDS = [
  'card_purchase', 'salary', 'deposit', 'transfer_in', 'transfer_out', 'own_transfer', 'atm_withdrawal', 'card_repayment',
  'loan_disbursement', 'loan_repayment', 'bank_fee', 'refund', 'reversal',
] as const;
export const SMS_STATUSES: readonly FinanceSmsStatus[] = ['review', 'failed', 'duplicate', 'posted', 'dismissed', 'rejected'];

/** The full ingest address the Shortcut calls. Shown only once the safety check passed. */
export function ingestUrl(apiBase: string, path: string | null): string | null {
  if (!path) return null;
  return `${apiBase.replace(/\/+$/, '')}${path}`;
}

/** Plain words for a review or refusal reason code; an unknown code is shown as it is. */
export function reasonLabel(reason: string | null): string {
  if (!reason) return '-';
  const key = `fsms.reason.${reason}` as MessageKey;
  const text = t(key);
  return text === key ? reason : text;
}

export function kindLabel(kind: string | null | undefined): string {
  if (!kind) return '-';
  const key = `fsms.kind.${kind}` as MessageKey;
  const text = t(key);
  return text === key ? kind : text;
}

/** The ledger account code in plain words: "Expense: groceries", "Card", "In transit". */
export function ledgerAccountLabel(code: string, accountNames: Record<string, string>): string {
  const [type, rest = ''] = code.split(':', 2);
  if (type === 'asset' || type === 'liability') return accountNames[rest] ?? t('fledger.account.unknown');
  if (type === 'suspense') return t('fledger.inTransit');
  if (type === 'equity') return t('fledger.account.opening');
  const key = `fledger.type.${type}` as MessageKey;
  const label = t(key);
  return `${label === key ? type : label}: ${rest.replace(/_/g, ' ')}`;
}

/** The fields the correction form starts from: what ANUM read, as text. */
export function correctionDefaults(message: FinanceSmsMessage): FinanceSmsCorrection {
  const p = message.parsed;
  const kind = (SMS_KINDS as readonly string[]).includes(p.kind ?? '') ? p.kind as FinanceSmsCorrection['kind'] : undefined;
  return {
    kind, amount: p.amount ?? '', currency: p.currency ?? '', converted_amount: p.converted_amount ?? '', merchant: p.merchant ?? '',
    occurred_on: p.occurred_on ?? '', reference: p.reference ?? '', interest: p.interest ?? '', fee: p.fee ?? '', last4: p.last4 ?? '',
  };
}

/** Only the fields the person filled in, so the server keeps what it read for the rest. */
export function correctionBody(form: FinanceSmsCorrection): FinanceSmsCorrection {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(form)) {
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (trimmed) out[key] = key === 'currency' ? trimmed.toUpperCase() : trimmed;
    } else if (value !== undefined && value !== null) {
      out[key] = value;
    }
  }
  return out as FinanceSmsCorrection;
}

/** The step list the settings page shows for the Shortcut (docs/finance-sms.md#the-shortcut). */
export function shortcutSteps(url: string): string[] {
  return [
    t('fsms.step.1'), t('fsms.step.2'), t('fsms.step.3'), t('fsms.step.4', { url }), t('fsms.step.5'), t('fsms.step.6'),
  ];
}
