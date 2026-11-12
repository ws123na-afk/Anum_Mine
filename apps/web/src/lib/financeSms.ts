// iPhone SMS banking and the ledger (docs/finance-sms.md): pure view logic, covered by the unit tests.
import type { FinanceCategory, FinanceSmsCorrection, FinanceSmsFix, FinanceSmsLearningNotice, FinanceSmsMessage, FinanceSmsStatus } from '@anum/contracts';
import { categoryLabel } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';

export const SMS_KINDS = [
  'card_purchase', 'salary', 'deposit', 'transfer_in', 'transfer_out', 'own_transfer', 'atm_withdrawal', 'card_repayment',
  'loan_disbursement', 'loan_repayment', 'bank_fee', 'refund', 'reversal',
] as const;
export const SMS_STATUSES: readonly FinanceSmsStatus[] = ['review', 'failed', 'duplicate', 'attached', 'posted', 'dismissed', 'rejected'];

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

/** The Apple Wallet automation's steps (docs/finance-sms.md#apple-wallet). */
export function walletSteps(url: string): string[] {
  return [t('fsms.wstep.1'), t('fsms.wstep.2', { url }), t('fsms.wstep.3'), t('fsms.wstep.4')];
}

/** The ingest addresses: messages and Apple Wallet share the device token. */
export function walletUrl(smsUrl: string | null): string | null {
  return smsUrl ? smsUrl.replace(/\/sms\/ingest$/, '/wallet/ingest') : null;
}

/** The step list the settings page shows for the Shortcut (docs/finance-sms.md#the-shortcut). */
export function shortcutSteps(url: string): string[] {
  return [
    t('fsms.step.1'), t('fsms.step.2'), t('fsms.step.3'), t('fsms.step.4', { url }), t('fsms.step.5'), t('fsms.step.6'),
  ];
}

/** Kinds ANUM remembers for money from or to one party (docs/finance-sms.md#learning). */
export const REMEMBERED_KINDS: readonly string[] = ['salary', 'own_transfer', 'refund'];

/** Fixes the person can apply in one tap; the others are said in words (decide, record) or open Settings. */
export const ONE_TAP_FIXES: readonly string[] = ['trust_sender', 'map_card', 'auto_post_on', 'clear_hold', 'remember_kind', 'undo_learning', 'undo_recheck'];

/** The one action that makes such a message automatic next time, in plain words. */
export function fixText(fix: FinanceSmsFix): string {
  const sender = fix.sender ?? '-';
  switch (fix.action) {
    case 'trust_sender': return t('fsms.fix.trust_sender', { sender });
    case 'map_card': return fix.account_name ? t('fsms.fix.map_card', { last4: fix.last4 ?? '-', account: fix.account_name }) : t('fsms.fix.map_card_pick', { last4: fix.last4 ?? '-' });
    case 'auto_post_on': return t('fsms.fix.auto_post_on');
    case 'clear_hold': return t('fsms.fix.clear_hold', { sender });
    case 'remember_kind': return t('fsms.fix.remember_kind', { party: fix.party ?? '-', kind: kindLabel(fix.kind) });
    case 'undo_learning': return t('fsms.fix.undo_learning', { party: fix.party ?? '-', value: learnedValueLabel(fix.kind) });
    case 'undo_recheck': return t('fsms.fix.undo_recheck');
    case 'open_settings': return t('fsms.fix.open_settings');
    case 'decide': return t('fsms.fix.decide');
    default: return t('fsms.fix.record');
  }
}

/** A remembered value: a kind ("Salary") or a category ("Health"). */
export function learnedValueLabel(value: string | null | undefined): string {
  if (!value) return '-';
  const kind = kindLabel(value);
  return kind !== value ? kind : categoryLabel(value as FinanceCategory);
}

/** What ANUM learned, as a sentence. */
export function noticeText(notice: FinanceSmsLearningNotice): string {
  switch (notice.kind) {
    case 'sender_trusted': return t('fsms.learn.sender_trusted', { sender: notice.sender ?? '-' });
    case 'kind_remembered': return t('fsms.learn.kind_remembered', { party: notice.party ?? '-', kind: kindLabel(notice.value) });
    case 'category_remembered': return t('fsms.learn.category_remembered', { party: notice.party ?? '-', category: learnedValueLabel(notice.value) });
    case 'rechecked':
      if (notice.sender) return t('fsms.learn.rechecked_sender', { count: notice.value ?? '0', sender: notice.sender });
      if (notice.last4) return t('fsms.learn.rechecked_card', { count: notice.value ?? '0', last4: notice.last4 });
      return t('fsms.learn.rechecked', { count: notice.value ?? '0' });
    default: return t('fsms.learn.card_proposed', { last4: notice.last4 ?? '-', account: notice.account_name ?? '-' });
  }
}
