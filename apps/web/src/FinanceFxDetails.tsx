// FX details in the transaction drawer (docs/finance.md#card-fx-and-fees): the original and billed amounts, the rate with its
// date and source (never guessed: "rate not stated" when only the billed amount exists), the bank's FX fee or markup, and for an
// own transfer in two currencies the realised rate and FX difference. Separating a fee is the owner's step, with the figures shown.
import { useCallback, useEffect, useId, useState } from 'react';
import { ArrowLeftRight, Scissors } from 'lucide-react';
import type { FinanceTransactionFx } from '@anum/contracts';
import { apiRequest } from './lib/api';
import { describeError } from './lib/errors';
import { formatAmount, isolate } from './lib/finance';
import { differenceTone, plainRate, rateKey, separateAction, showsFx } from './lib/financeFx';
import { t, type MessageKey } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { Notice, failed, type Load } from './FinanceParts';
import { StatusChip } from './FinanceShell';

const BASE = '/api/v1/finance/transactions';
export const getFx = (transactionId: string) => apiRequest<FinanceTransactionFx>(`${BASE}/${encodeURIComponent(transactionId)}/fx`, { method: 'GET' });
export const separateFee = (transactionId: string, planHash: string) => apiRequest<{ mode: string; fee_transaction_id: string }>(
  `${BASE}/${encodeURIComponent(transactionId)}/fx/separate`,
  { method: 'POST', body: JSON.stringify({ plan_hash: planHash }), headers: { 'content-type': 'application/json' } },
);

const money = (amount: string | null, currency: string | null) => (amount && currency ? isolate(formatAmount(amount, currency)) : '-');

export function FxDetails({ transactionId, onChanged }: { transactionId: string; onChanged: () => void }) {
  const [state, setState] = useState<Load<FinanceTransactionFx>>({ kind: 'loading' });
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const headingId = useId();
  const load = useCallback(() => {
    getFx(transactionId).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [transactionId]);
  useEffect(() => { load(); }, [load]);
  if (state.kind === 'loading') return null;  // the drawer already shows its own loading state
  if (state.kind === 'denied') return null;
  if (state.kind === 'error') return <p className="muted ffxError" role="status">{t('ffx.error')} <button type="button" className="cmLinkButton" onClick={load}>{t('ffx.retry')}</button></p>;
  const v = state.value;
  if (!showsFx(v)) return null;
  const action = separateAction(v);
  const run = async () => {
    setBusy(true); setNotice(null);
    try {
      const done = await separateFee(v.transaction_id, v.plan_hash);
      setNotice({ text: t(done.mode === 'split' ? 'ffx.split.done' : 'ffx.link.done') }); setAsking(false); load(); onChanged();
    } catch (e) { setNotice({ text: describeError(e), error: true }); setAsking(false); load(); } finally { setBusy(false); }
  };
  const tone = differenceTone(v.transfer?.difference ?? null);
  return <section className="ffxPanel" aria-labelledby={headingId}>
    <h3 id={headingId}><ArrowLeftRight aria-hidden="true" />{t(v.transfer ? 'ffx.transferTitle' : 'ffx.title')}</h3>
    {v.foreign && <dl className="ffxFacts">
      <div><dt>{t('ffx.original')}</dt><dd dir="ltr">{money(v.original_amount, v.original_currency)}</dd></div>
      <div><dt>{t('ffx.billed')}</dt><dd dir="ltr">{money(v.billed_amount, v.billed_currency)}</dd></div>
      <div><dt>{t('ffx.rate')}</dt><dd>{v.rate !== null && <bdi dir="ltr">{plainRate(v.rate)} </bdi>}<small className="muted">{t(rateKey(v) as MessageKey)}</small></dd></div>
      {v.rate !== null && <div><dt>{t('ffx.rateDate')}</dt><dd><bdi dir="ltr">{v.rate_date ?? '-'}</bdi> <small className="muted">{v.rate_source ? t(`ffx.source.${v.rate_source}` as MessageKey) : ''}</small></dd></div>}
      {v.fee && <div><dt>{t('ffx.fee')}</dt><dd><bdi dir="ltr">{money(v.fee.amount, v.fee.currency)}</bdi>{v.fee.markup_percent && <bdi dir="ltr"> ({v.fee.markup_percent}%)</bdi>}
        {' '}<small className="muted">{t(`ffx.feeSource.${v.fee.source}` as MessageKey)}</small></dd></div>}
      {v.fee && v.fee.included_in_billed && <div><dt>{t('ffx.purchase')}</dt><dd dir="ltr">{money(v.purchase_amount, v.billed_currency)}</dd></div>}
    </dl>}
    {!v.foreign && v.fee && <p className="muted">{t('ffx.feeOnly', { amount: money(v.fee.amount, v.fee.currency) })}</p>}
    {v.notes.includes('fee_not_provably_included') && <p className="muted">{t('ffx.notIncluded')}</p>}
    {v.split === 'done' && <StatusChip tone="ok">{t('ffx.separated')}</StatusChip>}
    {v.transfer && <dl className="ffxFacts">
      <div><dt>{t('ffx.transfer.sent')}</dt><dd dir="ltr">{money(v.transfer.out_amount, v.transfer.out_currency)}</dd></div>
      <div><dt>{t('ffx.transfer.received')}</dt><dd dir="ltr">{money(v.transfer.in_amount, v.transfer.in_currency)}</dd></div>
      <div><dt>{t('ffx.transfer.realised')}</dt><dd><bdi dir="ltr">1 {v.transfer.out_currency} = {plainRate(v.transfer.realised_rate)} {v.transfer.in_currency}</bdi></dd></div>
      {v.transfer.valuation_rate
        ? <>
          <div><dt>{t('ffx.transfer.valuation')}</dt><dd><bdi dir="ltr">{plainRate(v.transfer.valuation_rate)}</bdi> <small className="muted"><bdi dir="ltr">{v.transfer.valuation_date}</bdi> {v.transfer.valuation_source}</small></dd></div>
          <div><dt>{t('ffx.transfer.difference')}</dt><dd className={`ffxDiff ${tone}`}><bdi dir="ltr">{money(v.transfer.difference, v.transfer.in_currency)}</bdi>
            {' '}<small className="muted">{t(`ffx.transfer.${tone}` as MessageKey)}</small></dd></div>
        </>
        : <div><dt>{t('ffx.transfer.difference')}</dt><dd className="muted">{t('ffx.transfer.noValuation')}</dd></div>}
    </dl>}
    {v.transfer && <p className="muted ffxPolicy">{t('ffx.transfer.policy')}</p>}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {action && <button type="button" className="secondary" disabled={busy} onClick={() => setAsking(true)}><Scissors aria-hidden="true" />{t(`ffx.${action}.button` as MessageKey)}</button>}
    {v.fee && !v.can_correct && v.split !== 'done' && <p className="muted">{t('ffx.smsCorrect')}</p>}
    {asking && action && v.fee && <ConfirmDialog title={t(`ffx.${action}.title` as MessageKey)} confirmLabel={t(`ffx.${action}.button` as MessageKey)} busy={busy}
      onConfirm={() => void run()} onCancel={() => setAsking(false)}>
      <p>{t(`ffx.${action}.text` as MessageKey, { fee: money(v.fee.amount, v.fee.currency), purchase: money(v.purchase_amount, v.billed_currency),
        billed: money(v.billed_amount, v.billed_currency) })}</p>
    </ConfirmDialog>}
  </section>;
}
