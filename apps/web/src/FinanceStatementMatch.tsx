// The statement matching engine in the import preview (docs/finance.md#statement-matching), shared by the CSV preview and
// the PDF review: rows grouped by what the import will do, each with its plain-language reason, FX breakdown, bill or
// transfer, and the ticks the person can change. Plus "Find transfers" over rows already in the ledger. The API runs the
// engine again on import; nothing here moves money.
import { useId, useMemo, useState, type FormEvent } from 'react';
import { ArrowLeftRight, CheckCircle2, CircleAlert, Copy, FilePlus2, Link2, Receipt, Scale } from 'lucide-react';
import type { FinanceReconciliation, FinanceTransferCandidates, FinanceTransferPair } from '@anum/contracts';
import * as api from './lib/financeApi';
import { describeError } from './lib/errors';
import { formatAmount as rawAmount, isolate } from './lib/finance';

const formatAmount = (value: string | null | undefined, currency: string) => isolate(rawAmount(value, currency));
import {
  GROUP_ORDER, GROUP_PAGE, billOn, canImportAnyway, correctOn, fxText, groupRows, outcomeOf, reasonText, reconciliationText, sourceText, tally, transferOn,
  type Choice, type GroupId, type MatchRow,
} from './lib/financeMatch';
import { t, type MessageKey } from './lib/i18n';
import { FormError, Notice } from './FinanceParts';

const GROUP_ICON: Record<GroupId, typeof Copy> = {
  decide: CircleAlert, matched: Link2, bills: Receipt, transfers: ArrowLeftRight, new: FilePlus2, recorded: Copy,
};

export function ReconciliationBanner({ check, currency }: { check: FinanceReconciliation; currency: string }) {
  const { tone, text } = reconciliationText(check, currency);
  return <p className={`notice fmatchRecon ${tone === 'error' ? 'errorNotice' : ''}`} data-tone={tone} role={tone === 'error' ? 'alert' : 'status'}>
    {tone === 'ok' ? <CheckCircle2 aria-hidden="true" /> : <Scale aria-hidden="true" />}<span><strong>{t('fmatch.recon.title')}: </strong>{text}</span>
  </p>;
}

/** The preview's matches, grouped. ``choices`` is the parent's state so the import sends exactly what is shown. */
export function MatchPreview({ rows, currency, choices, onChoose }: {
  rows: readonly MatchRow[]; currency: string; choices: Readonly<Record<string, Choice>>; onChoose: (id: string, patch: Choice) => void;
}) {
  const headId = useId();
  const groups = useMemo(() => groupRows(rows), [rows]);
  const totals = tally(rows, choices);
  return <section className="fmatch" aria-labelledby={headId}>
    <h3 id={headId}>{t('fmatch.title')}</h3>
    <p className="muted">{t('fmatch.intro')}</p>
    <p className="fmatchSummary" aria-live="polite">{t('fmatch.summary', { import: totals.import, attach: totals.attach, duplicate: totals.duplicate, review: totals.review, skip: totals.skip })}
      {(totals.bills > 0 || totals.transfers > 0) && <> {t('fmatch.summaryExtras', { bills: totals.bills, transfers: totals.transfers })}</>}</p>
    {GROUP_ORDER.filter((id) => groups[id].length > 0).map((id) => <MatchGroup key={id} id={id} rows={groups[id]} currency={currency} choices={choices} onChoose={onChoose} />)}
  </section>;
}

function MatchGroup({ id, rows, currency, choices, onChoose }: {
  id: GroupId; rows: readonly MatchRow[]; currency: string; choices: Readonly<Record<string, Choice>>; onChoose: (id: string, patch: Choice) => void;
}) {
  const [shown, setShown] = useState(GROUP_PAGE);
  const Icon = GROUP_ICON[id];
  const visible = rows.slice(0, shown);
  return <details className="fmatchGroup" data-group={id} open={id === 'decide' || id === 'matched' || id === 'transfers' || id === 'bills' || undefined}>
    <summary><Icon aria-hidden="true" /><span>{t(`fmatch.group.${id}` as MessageKey, { count: rows.length })}</span></summary>
    <p className="muted fmatchHelp">{t(`fmatch.group.help.${id}` as MessageKey)}</p>
    <ul className="fmatchRows">{visible.map((row) => <MatchItem key={`${id}:${row.id}`} group={id} row={row} currency={currency} choice={choices[row.id]}
      onChoose={(patch) => onChoose(row.id, patch)} />)}</ul>
    {rows.length > shown && <button type="button" className="secondary" onClick={() => setShown(shown + GROUP_PAGE)}>
      {t('fmatch.showMore', { count: Math.min(GROUP_PAGE, rows.length - shown) })}</button>}
  </details>;
}

function MatchItem({ group, row, currency, choice, onChoose }: { group: GroupId; row: MatchRow; currency: string; choice: Choice | undefined; onChoose: (patch: Choice) => void }) {
  const match = row.match;
  const outcome = outcomeOf(match, choice);
  const fx = fxText(row.fx, currency);
  const bill = match.bill;
  const transfer = match.transfer;
  const account = transfer ? (transfer.account_last4 ? t('fmatch.transfer.ending', { name: transfer.account_name, last4: transfer.account_last4 }) : transfer.account_name) : '';
  const tick = (label: string, checked: boolean, change: (value: boolean) => void) => <label className="finCheck fmatchTick">
    <input type="checkbox" checked={checked} onChange={(e) => change(e.target.checked)} /><span>{label}</span></label>;
  return <li className="fmatchRow" data-outcome={outcome}>
    <div className="fmatchHead">
      <span className="fmatchDate"><bdi dir="ltr">{row.posted_on}</bdi></span>
      <strong className="fmatchDesc" dir="auto">{row.description}</strong>
      <span className="fmatchAmount num"><bdi dir="ltr">{formatAmount(row.amount, currency)}</bdi></span>
    </div>
    <p className="fmatchReason"><span className="fmatchOutcome">{t(`fmatch.outcome.${outcome}` as MessageKey)}</span> {reasonText(match, currency)}</p>
    {match.candidates.length > 0 && <ul className="fmatchCandidates">{match.candidates.map((c) => <li key={c.record_id} dir="auto">
      {t('fmatch.candidate', { source: sourceText(c), description: c.description, date: isolate(c.posted_on), amount: formatAmount(c.amount, c.currency) })}</li>)}</ul>}
    {fx.length > 0 && <p className="fmatchFx" dir="auto">{fx.join(' · ')}</p>}
    {bill && group !== 'transfers' && <p className="fmatchNote" dir="auto">{t(bill.strength === 'strong' ? 'fmatch.bill.strong' : 'fmatch.bill.weak',
      { bill: bill.name, date: isolate(bill.due_on), expected: formatAmount(bill.expected, bill.currency) })}</p>}
    {transfer && group !== 'bills' && <p className="fmatchNote" dir="auto">{transfer.strength === 'strong'
      ? t(transfer.direction === 'to' ? 'fmatch.transfer.to' : 'fmatch.transfer.from', { account })
      : t('fmatch.transfer.weak', { account })}</p>}
    <div className="fmatchTicks">
      {match.status === 'new' && tick(t('fmatch.choice.import'), choice?.action !== 'skip', (on) => onChoose({ action: on ? null : 'skip' }))}
      {canImportAnyway(match) && tick(t('fmatch.choice.importAnyway'), choice?.action === 'import', (on) => onChoose({ action: on ? 'import' : null }))}
      {match.status === 'matched_existing' && match.amount_difference !== null && tick(t('fmatch.choice.correct', { amount: formatAmount(row.amount.replace('-', ''), currency) }),
        correctOn(match, choice), (on) => onChoose({ correct: on ? null : false }))}
      {bill && (outcome === 'import' || outcome === 'attach') && tick(
        t(bill.strength === 'strong' ? 'fmatch.choice.bill' : 'fmatch.choice.billReview', { bill: bill.name, date: isolate(bill.due_on) }),
        billOn(match, choice), (on) => onChoose({ bill: on ? null : false }))}
      {transfer && (outcome === 'import' || outcome === 'attach') && tick(t('fmatch.choice.transfer'), transferOn(match, choice),
        (on) => onChoose({ transfer: transfer.strength === 'strong' ? (on ? null : false) : (on ? true : null) }))}
    </div>
  </li>;
}

const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);

/** "Find transfers": pairs already in the ledger, previewed first and paired only when ticked. */
export function TransferFinder({ onPaired }: { onPaired: () => void }) {
  const [start, setStart] = useState(daysAgo(90)), [end, setEnd] = useState(today());
  const [found, setFound] = useState<FinanceTransferCandidates | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const headId = useId(), errorId = useId();
  const key = (p: FinanceTransferPair) => `${p.out.record_id}|${p.into.record_id}`;
  async function find(event?: FormEvent) {
    event?.preventDefault();
    if (!start || !end || start > end) { setError(t('ftransfer.error.range')); return; }
    setBusy(true); setError(null); setNotice(null);
    try {
      const result = await api.findTransfers(start, end);
      setFound(result);
      setPicked(new Set(result.pairs.filter((p) => p.strength === 'strong').map(key)));
    } catch (e) { setFound(null); setError(describeError(e)); } finally { setBusy(false); }
  }
  async function pair() {
    if (!found) return;
    setBusy(true); setError(null);
    try {
      const pairs = found.pairs.filter((p) => picked.has(key(p))).map((p) => ({ out_record_id: p.out.record_id, into_record_id: p.into.record_id }));
      const result = await api.pairTransfers({ start: found.start, end: found.end, pairs });
      setNotice(t('ftransfer.paired', { paired: result.paired }) + (result.failed.length ? ` ${t('ftransfer.failed', { count: result.failed.length })}` : ''));
      setFound(null); setPicked(new Set()); onPaired();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const toggle = (id: string, on: boolean) => setPicked((current) => { const next = new Set(current); if (on) next.add(id); else next.delete(id); return next; });
  return <section className="surface cmPanel ftransferFind" aria-labelledby={headId}>
    <h2 id={headId}><ArrowLeftRight aria-hidden="true" className="cmInlineIcon" />{t('ftransfer.title')}</h2>
    <p className="muted">{t('ftransfer.intro')}</p>
    <form className="finForm" onSubmit={(e) => void find(e)} aria-describedby={error ? errorId : undefined}>
      <div className="cmFilters">
        <label className="cmInline"><span>{t('ftransfer.start')}</span><input type="date" value={start} max={end || undefined} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="cmInline"><span>{t('ftransfer.end')}</span><input type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} /></label>
        <button type="submit" className="secondary" disabled={busy}>{busy && !found ? t('ftransfer.finding') : t('ftransfer.find')}</button>
      </div>
      <FormError id={errorId} text={error} />
    </form>
    {notice && <Notice>{notice}</Notice>}
    {found && (found.pairs.length === 0 ? <p className="muted">{t('ftransfer.none')}</p> : <>
      <p aria-live="polite">{t('ftransfer.found', { strong: found.strong, weak: found.weak })}</p>
      <ul className="fmatchRows">{found.pairs.map((p) => {
        const into = p.into_last4 ? t('fmatch.transfer.ending', { name: p.into_account_name, last4: p.into_last4 }) : p.into_account_name;
        const amount = formatAmount(p.out.amount.replace('-', ''), p.out.currency);
        return <li key={key(p)} className="fmatchRow" data-outcome={p.strength === 'strong' ? 'import' : 'review'}>
          <label className="finCheck fmatchTick"><input type="checkbox" checked={picked.has(key(p))} aria-label={t('ftransfer.select', { out: p.out_account_name, into, amount })}
            onChange={(e) => toggle(key(p), e.target.checked)} />
            <span><strong dir="auto">{t('ftransfer.row', { out: p.out_account_name, into })}</strong> <bdi dir="ltr">{amount}</bdi></span></label>
          <p className="fmatchReason"><span className="fmatchOutcome">{t(p.strength === 'strong' ? 'ftransfer.strong' : 'ftransfer.weak')}</span>{' '}
            <bdi dir="ltr">{p.out.posted_on}</bdi> · {p.lag_days ? t('ftransfer.days', { count: p.lag_days }) : t('ftransfer.sameDay')} · {p.reasons.map((r) => t(`ftransfer.reason.${r}` as MessageKey)).join(', ')}</p>
          <p className="fmatchFx" dir="auto">{p.out.description} · {p.into.description}</p>
        </li>;
      })}</ul>
      <div className="actions"><button type="button" disabled={busy || picked.size === 0} onClick={() => void pair()}>{busy ? t('ftransfer.pairing') : t('ftransfer.pair', { count: picked.size })}</button></div>
    </>)}
  </section>;
}
