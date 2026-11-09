// Tamara refunds on the installment statement (docs/finance.md#tamara-refunds). The server finds the refund
// evidence and works out the new schedule; this card shows it per order and saves it only when the owner
// confirms "Apply refunds". Estimates are shown, never applied. Nothing here moves money.
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { RotateCcw, Undo2 } from 'lucide-react';
import type { FinanceOrderRefundPlan, FinanceRefundPreview } from '@anum/contracts';
import * as plan from './lib/financePlanningApi';
import { formatAmount, isolate } from './lib/finance';
import { applySelection, evidenceKey, filterOrders, modeKey, pendingTotals, reasonKey, stateKey, stateTone, withEvidence, type RefundFilter } from './lib/financeRefunds';
import { ApiError, describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { Denied, Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, StatusChip } from './FinanceShell';

const PAGE = 25;
const money = (value: string, currency: string) => isolate(formatAmount(value, currency));

export function FinanceInstallmentRefunds({ onChanged, onSyncGmail, refreshKey = 0 }: { onChanged: () => void; onSyncGmail: () => void; refreshKey?: number }) {
  const [load, setLoad] = useState<Load<FinanceRefundPreview>>({ kind: 'loading' });
  const [filter, setFilter] = useState<RefundFilter>('all');
  const [needle, setNeedle] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);

  const refresh = useCallback(async () => {
    try { setLoad({ kind: 'ready', value: await plan.installmentRefunds() }); } catch (e) { setLoad(failed(e)); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh, refreshKey]);

  const preview = load.kind === 'ready' ? load.value : null;
  const listed = useMemo(() => withEvidence(preview?.orders ?? []), [preview]);
  const shown = useMemo(() => filterOrders(listed, filter, needle), [listed, filter, needle]);
  const selection = useMemo(() => applySelection(listed), [listed]);
  const totals = useMemo(() => pendingTotals(listed), [listed]);

  async function applyAll() {
    if (!selection.length || busy) return;
    setBusy('apply'); setNotice(null);
    try {
      const result = await plan.applyInstallmentRefunds(selection);
      setLoad({ kind: 'ready', value: result.preview });
      setNotice({ text: t('frefund.applied', { count: result.applied, installments: result.installments }) });
      onChanged();
    } catch (e) {
      setNotice({ error: true, text: e instanceof ApiError && e.status === 409 ? t('frefund.stale') : describeError(e) });
      void refresh();
    } finally { setBusy(null); setConfirm(false); }
  }

  async function undo(recordId: string, order: string) {
    if (busy) return;
    setBusy(recordId); setNotice(null);
    try {
      setLoad({ kind: 'ready', value: await plan.undoInstallmentRefund(recordId) });
      setNotice({ text: t('frefund.undoDone', { order }) });
      onChanged();
    } catch (e) { setNotice({ error: true, text: describeError(e) }); void refresh(); }
    finally { setBusy(null); }
  }

  const head = { title: t('frefund.title'), icon: <RotateCcw />, id: 'frefund-title' };
  if (load.kind === 'loading') return <FinCard {...head}><Notice busy>{t('frefund.loading')}</Notice></FinCard>;
  if (load.kind === 'denied') return <Denied />;
  if (load.kind === 'error') return <FinCard {...head}><LoadError message={t('frefund.error', { message: load.message })} retry={() => void refresh()} /></FinCard>;
  if (!listed.length) return <FinCard {...head} subtitle={t('frefund.subtitle')}>
    <Empty icon={<RotateCcw aria-hidden="true" />} title={t('frefund.empty')} text={t('frefund.emptyText')}
      action={<button type="button" className="secondary" onClick={onSyncGmail}>{t('frefund.syncGmail')}</button>} />
  </FinCard>;

  const records = preview?.records ?? [];
  return <FinCard {...head} subtitle={t('frefund.subtitle')} className="frefundCard"
    actions={<button type="button" disabled={!selection.length || busy !== null} onClick={() => setConfirm(true)}>
      {busy === 'apply' ? t('frefund.applying') : t('frefund.apply')}</button>}>
    {(preview!.pending > 0 || preview!.needs_review > 0) && <FinBanner tone={preview!.needs_review ? 'warn' : 'info'}
      title={[preview!.pending ? t('frefund.ready', { count: preview!.pending }) : '', preview!.needs_review ? t('frefund.review', { count: preview!.needs_review }) : ''].filter(Boolean).join('. ')}>
      {totals.map((x) => <span key={x.currency} className="frefundTotals">
        {t('frefund.notDue', { amount: money(x.notDue, x.currency) })}{Number(x.moneyBack) > 0 && <>{'. '}{t('frefund.moneyBack', { amount: money(x.moneyBack, x.currency) })}</>}
      </span>)}
    </FinBanner>}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <div className="frefundFilters">
      <label className="field"><span>{t('frefund.search')}</span><input type="search" value={needle} onChange={(e) => { setNeedle(e.target.value); setLimit(PAGE); }} /></label>
      <label className="field"><span>{t('frefund.show')}</span>
        <select value={filter} onChange={(e) => { setFilter(e.target.value as RefundFilter); setLimit(PAGE); }}>
          {(['all', 'pending', 'review', 'refunded'] as const).map((f) => <option key={f} value={f}>{t(`frefund.filter.${f}` as MessageKey)}</option>)}
        </select></label>
    </div>
    {!shown.length ? <p className="muted">{t('frefund.noMatch')}</p> : <div className="finTableWrap"><table className="finGrid frefundTable">
      <thead><tr><th>{t('frefund.col.order')}</th><th>{t('frefund.col.status')}</th><th>{t('frefund.col.refund')}</th><th>{t('frefund.col.before')}</th>
        <th>{t('frefund.col.after')}</th><th>{t('frefund.col.next')}</th><th><span className="visuallyHidden">{t('frefund.details')}</span></th></tr></thead>
      <tbody>{shown.slice(0, limit).map((o) => {
        const key = `${o.provider}/${o.order_id}`;
        return <Fragment key={key}>
          <tr>
            <td className="frefundLead"><strong>{o.merchant}</strong><small className="frefundRef" dir="ltr">{o.order_id}</small></td>
            <td data-label={t('frefund.col.status')}><span className="frefundChips"><StatusChip tone={stateTone(o.state)}>{t(stateKey(o.state))}</StatusChip>
              {o.pending && <StatusChip tone="info">{t('frefund.pending')}</StatusChip>}</span></td>
            <td data-label={t('frefund.col.refund')}><Amount value={o.refund_total} currency={o.currency} /></td>
            <td data-label={t('frefund.col.before')}><Amount value={o.outstanding_before} currency={o.currency} /></td>
            <td data-label={t('frefund.col.after')}><Amount value={o.outstanding_after} currency={o.currency} /></td>
            <td data-label={t('frefund.col.next')}>{o.next_due_after ?? '-'}</td>
            <td className="frefundAction"><button type="button" className="secondary" aria-expanded={open === key} aria-label={t('frefund.detailsFor', { merchant: o.merchant })}
              onClick={() => setOpen(open === key ? null : key)}>{t('frefund.details')}</button></td>
          </tr>
          {open === key && <tr className="frefundDetailRow"><td colSpan={7}><OrderDetail order={o} /></td></tr>}
        </Fragment>;
      })}</tbody>
    </table></div>}
    {shown.length > limit && <div className="frefundMore"><span className="muted">{t('frefund.count', { shown: limit, total: shown.length })}</span>
      <button type="button" className="secondary" onClick={() => setLimit(limit + PAGE)}>{t('frefund.more', { count: Math.min(PAGE, shown.length - limit) })}</button></div>}
    {records.length > 0 && <section className="frefundHistory" aria-labelledby="frefund-history">
      <h3 id="frefund-history">{t('frefund.history')}</h3>
      <p className="muted">{t('frefund.historyText')}</p>
      <ul>{records.map((r) => <li key={r.id}>
        <span>{t('frefund.record', { order: r.order_id, amount: money(r.refund_total, r.currency), count: r.installments, date: formatDateTime(r.applied_at) })}</span>
        {r.undone ? <StatusChip>{t('frefund.undone')}</StatusChip> : r.can_undo && <button type="button" className="secondary" disabled={busy !== null}
          onClick={() => void undo(r.id, r.order_id)}><Undo2 aria-hidden="true" />{busy === r.id ? t('frefund.undoing') : t('frefund.undo')}</button>}
      </li>)}</ul>
    </section>}
    <p className="muted">{t('frefund.noMoney')}</p>
    {confirm && <ConfirmDialog title={t('frefund.applyTitle')} confirmLabel={t('frefund.apply')} busy={busy === 'apply'}
      onConfirm={() => void applyAll()} onCancel={() => setConfirm(false)}>
      <p>{t('frefund.applyText', { count: selection.length })}</p>
      {totals.map((x) => <p key={x.currency}>{t('frefund.notDue', { amount: money(x.notDue, x.currency) })}</p>)}
    </ConfirmDialog>}
  </FinCard>;
}

function Amount({ value, currency }: { value: string; currency: string }) {
  return <span className="finMoney" dir="ltr">{formatAmount(value, currency)}</span>;
}

function OrderDetail({ order: o }: { order: FinanceOrderRefundPlan }) {
  const estimate = o.mode === 'estimate';
  return <div className="frefundDetail">
    <p>{t(modeKey(o))}</p>
    {o.reasons.length > 0 && <ul className="frefundReasons">{o.reasons.map((r) => <li key={r}>{t(reasonKey(r))}</li>)}</ul>}
    {Number(o.credit_back) > 0 && <p>{t('frefund.creditExpected', { amount: money(o.credit_back, o.currency) })}
      {Number(o.credit_received) > 0 && <>{'. '}{t('frefund.creditReceived', { amount: money(o.credit_received, o.currency) })}</>}</p>}
    <h4>{t('frefund.evidenceTitle')}</h4>
    <ul className="frefundEvidence">{o.evidence.map((e) => <li key={e.id}>
      <StatusChip tone={e.strength === 'strong' ? 'ok' : 'warn'}>{t(e.strength === 'strong' ? 'frefund.strong' : 'frefund.weak')}</StatusChip>
      <span>{t(evidenceKey(e), { amount: e.amount ? money(e.amount, e.currency) : '-', installment: e.installment ?? '-' })}</span>
      {e.on && <span className="muted" dir="ltr">{e.on}</span>}
      {e.link?.startsWith('https://mail.google.com/') && <a href={e.link} target="_blank" rel="noreferrer">{t('frefund.openEmail')}</a>}
    </li>)}</ul>
    <div className="finTableWrap"><table className="finGrid frefundInst">
      <thead><tr><th>{t('frefund.col.installment')}</th><th>{t('frefund.col.dueOn')}</th><th>{t('frefund.col.amount')}</th><th>{t('frefund.col.new')}</th>
        {estimate && <th>{t('frefund.col.estimate')}</th>}<th>{t('frefund.col.instState')}</th></tr></thead>
      <tbody>{o.installments.map((i) => <tr key={i.installment}>
        <td className="frefundLead"><span dir="ltr">{i.installment} / {i.total_installments}</span></td><td data-label={t('frefund.col.dueOn')}>{i.due_on}</td>
        <td data-label={t('frefund.col.amount')}><Amount value={i.state_before === 'paid' ? i.amount : i.due_before} currency={o.currency} /></td>
        <td data-label={t('frefund.col.new')}>{i.state_after === 'paid' ? '-' : <Amount value={i.due_after} currency={o.currency} />}</td>
        {estimate && <td data-label={t('frefund.col.estimate')}>{i.estimate_due !== null ? <Amount value={i.estimate_due} currency={o.currency} /> : '-'}</td>}
        <td data-label={t('frefund.col.instState')}><StatusChip tone={i.state_after === 'refunded' ? 'ok' : i.state_after === 'paid' ? 'muted' : i.state_after === 'partly_refunded' ? 'info' : 'muted'}>
          {t(`frefund.inst.${i.state_after}` as MessageKey)}</StatusChip></td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}
