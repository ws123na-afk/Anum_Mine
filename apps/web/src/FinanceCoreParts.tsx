// Personal Finance, the dashboard contract on the web (docs/finance.md#dashboard, docs/finance.md#transaction-detail):
// cash against card credit, what the month left out of income and spending, money in by kind with the salary month,
// what needs review, coming bills and installments, each account's freshness, and in a transaction's drawer the
// records it is linked to (with the evidence behind each), related refunds and fees, and its note. Every figure is
// the server's (GET /finance/summary, GET /finance/register/{id}); nothing here adds or converts amounts.
import { useEffect, useId, useState } from 'react';
import { ArrowRight, CalendarClock, ClipboardCheck, Link2, Repeat, StickyNote, TrendingUp, Wallet } from 'lucide-react';
import type { FinanceAccountFreshness, FinanceExcludedKind, FinanceRegisterDetail, FinanceSummary } from '@anum/contracts';
import * as api from './lib/financeApi';
import { categoryLabel, dueLabel, formatAmount, isolate, type FinanceTab } from './lib/finance';
import { dashboardTotals, daysSince, lastCapture, upcomingPayments } from './lib/financeDashboard';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Money, Notice } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import './financeCore.css';

const EXCLUDED_ORDER: FinanceExcludedKind[] = ['own_transfer', 'card_payment', 'loan_principal', 'lending', 'other_transfer'];
const shortDate = (value: string) => formatDateTime(value, { day: 'numeric', month: 'short' });
const day = (value: string) => formatDateTime(`${value.slice(0, 10)}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' });

/** Bank and cash against what the cards owe, and the month's transfers, card repayments and loan principal left out. */
export function CashCardsCard({ summary, currency }: { summary: FinanceSummary; currency: string }) {
  const totals = dashboardTotals(summary, currency);
  const position = summary.positions?.find((p) => p.currency === currency) ?? null;
  const excluded = EXCLUDED_ORDER.filter((k) => totals.excluded[k]);
  return <FinCard title={t('fcore.cash.title')} icon={<Wallet />} subtitle={t('fcore.cash.sub', { currency })} className="fcoreTop">
    {!position ? <p className="muted">{t('fcore.cash.none', { currency })}</p> : <dl className="fcoreFigures">
      <div><dt>{t('fcore.cash.cash')}</dt><dd><Money value={totals.cash} currency={currency} signed /></dd></div>
      <div><dt>{t('fcore.cash.cardOwed')}</dt><dd><bdi>{formatAmount(totals.cardOwed, currency)}</bdi></dd></div>
      {Number(totals.cardOwed) > 0 && <div><dt>{t('fcore.cash.available')}</dt>
        <dd>{totals.cardAvailable !== null ? <bdi>{formatAmount(totals.cardAvailable, currency)}</bdi> : <small className="muted">{t('fcore.cash.availableUnknown')}</small>}</dd></div>}
      {Number(position.loans_owed) > 0 && <div><dt>{t('fcore.cash.loans')}</dt><dd><bdi>{formatAmount(position.loans_owed, currency)}</bdi></dd></div>}
      {Number(position.investments) !== 0 && <div><dt>{t('fcore.cash.investments')}</dt><dd><bdi>{formatAmount(position.investments, currency)}</bdi></dd></div>}
    </dl>}
    <h3 className="fcoreSubhead"><Repeat aria-hidden="true" />{t('fcore.excluded.title')}</h3>
    {excluded.length === 0 ? <p className="muted">{t('fcore.excluded.none')}</p> : <ul className="cmList">{excluded.map((k) => {
      const row = totals.excluded[k]!;
      return <li key={k}><div><strong>{t(`fcore.excluded.${k}` as MessageKey)}</strong>
        <small>{t('fcore.excluded.row', { in: isolate(formatAmount(row.in, currency)), out: isolate(formatAmount(row.out, currency)) })}</small></div></li>;
    })}</ul>}
  </FinCard>;
}

/** Money in by kind, and the shown month in the salary check (expected against received). */
export function IncomeCard({ summary, currency }: { summary: FinanceSummary; currency: string }) {
  const lines = (summary.income_breakdown ?? []).filter((l) => l.currency === currency);
  const salary = summary.salary;
  return <FinCard title={t('fcore.income.title')} icon={<TrendingUp />} subtitle={t('fcore.income.sub', { currency })}
    actions={<a className="cmLinkButton" href="#finance-advisor-salary">{t('fcore.salary.open')}</a>}>
    {lines.length === 0 ? <p className="muted">{t('fcore.income.none')}</p> : <ul className="cmList">{lines.map((l) => <li key={l.kind}>
      <div><strong>{t(`fcore.income.${l.kind}` as MessageKey)}</strong><small>{t('fcore.income.count', { count: l.count })}</small></div>
      <Money value={l.amount} currency={l.currency} /></li>)}</ul>}
    {salary ? <div className="fcoreSalary">
      <p><StatusChip tone={salary.status === 'received' ? 'ok' : salary.status === 'upcoming' ? 'info' : 'warn'}>{t(`fcore.salary.status.${salary.status}` as MessageKey)}</StatusChip>{' '}
        {t('fcore.salary.expected', { expected: isolate(formatAmount(salary.expected_net, salary.currency)), received: isolate(formatAmount(salary.received, salary.currency)) })}</p>
      <small className="muted">{t('fcore.salary.payOn', { date: day(salary.pay_on) })}</small>
      {salary.waiting > 0 && <p className="muted">{t('fcore.salary.waiting', { count: salary.waiting })}</p>}
    </div> : summary.detail !== 'figures' && <p className="muted">{t('fcore.salary.noProfile')}</p>}
  </FinCard>;
}

/** What waits for the person, each opening the page where it is done. */
export function ReviewCard({ summary, onOpen }: { summary: FinanceSummary; onOpen: (tab: FinanceTab) => void }) {
  const r = summary.review;
  const rows: { key: MessageKey; count: number; tab: FinanceTab }[] = r ? [
    { key: 'fcore.review.uncategorized', count: r.uncategorized, tab: 'transactions' },
    { key: 'fcore.review.sms', count: r.sms_review, tab: 'sms' },
    { key: 'fcore.review.links', count: r.links_need_review, tab: 'transactions' },
    { key: 'fcore.review.installments', count: r.installments_review, tab: 'bills' },
  ].filter((row) => row.count > 0) as { key: MessageKey; count: number; tab: FinanceTab }[] : [];
  return <FinCard title={t('fcore.review.title')} icon={<ClipboardCheck />}>
    {rows.length === 0 ? <p className="muted">{t('fcore.review.none')}</p> : <ul className="fovLinks fcoreReview">{rows.map((row) =>
      <li key={row.key}><button type="button" className="fovLink" onClick={() => onOpen(row.tab)}><span>{t(row.key, { count: row.count })}</span>
        <ArrowRight className="cmFlip" aria-hidden="true" /></button></li>)}</ul>}
  </FinCard>;
}

/** Bills and installments due in 30 days (or overdue this month) with no matched payment, with their total. */
export function UpcomingRows({ summary }: { summary: FinanceSummary }) {
  const rows = upcomingPayments(summary);
  const total = (summary.payments_due ?? summary.bills_due).map((r) => formatAmount(r.amount, r.currency)).join(' · ');
  if (rows.length === 0) return <p className="muted">{t('fcore.upcoming.none')}</p>;
  return <>
    <p className="muted"><CalendarClock aria-hidden="true" className="fcoreInline" />{t('fcore.upcoming.total', { total: isolate(total) })}</p>
    <ul className="cmList">{rows.slice(0, 6).map((row) =>
      <li key={`${row.kind}-${row.id}-${row.due_on}`}>
        <div><strong dir="auto">{row.name}</strong>
          <small>{t(`fcore.upcoming.${row.kind}` as MessageKey)} · <bdi dir="ltr">{row.due_on}</bdi> · {row.overdue ? t('fcore.upcoming.overdue') : dueLabel(row.days_until)}
            {' · '}{row.status === 'needs_review' ? t('fcore.upcoming.review') : t('fcore.upcoming.pending')}</small></div>
        <Money value={row.amount} currency={row.currency} /></li>)}</ul>
  </>;
}

/** One account's freshness: verified or estimated balance, the newest capture and, when stale, how long. */
export function FreshnessLine({ freshness, currency, health = true }: { freshness: FinanceAccountFreshness | null | undefined; currency: string; health?: boolean }) {
  if (!freshness) return null;
  const capture = lastCapture(freshness);
  const parts: string[] = [];
  if (freshness.balance_kind === 'verified' && freshness.verified_at) parts.push(t('fcore.fresh.verified', { date: shortDate(freshness.verified_at) }));
  else parts.push(t('fcore.fresh.estimated'));
  if (freshness.verified_by === 'sms' && freshness.difference && Number(freshness.difference) !== 0 && freshness.verified_balance && freshness.verified_at) {
    parts.push(t('fcore.fresh.statedDiffers', { amount: isolate(formatAmount(freshness.verified_balance, currency)), date: shortDate(freshness.verified_at) }));
  } else if (freshness.verified_by === 'statement' && freshness.verified_balance && freshness.verified_at) {
    parts.push(t('fcore.fresh.statement', { amount: isolate(formatAmount(freshness.verified_balance, currency)), date: shortDate(freshness.verified_at) }));
  }
  if (freshness.last_sms_at) parts.push(t('fcore.fresh.lastSms', { date: shortDate(freshness.last_sms_at) }));
  if (freshness.last_apple_pay_at) parts.push(t('fcore.fresh.lastApplePay', { date: shortDate(freshness.last_apple_pay_at) }));
  if (freshness.last_import_at) parts.push(t('fcore.fresh.lastStatement', { date: shortDate(freshness.last_import_at) }));
  if (freshness.state === 'manual') parts.push(t('fcore.fresh.manual'));
  const stale = freshness.state === 'stale' ? (capture ? t('fcore.fresh.stale', { count: daysSince(capture.at) }) : t('fcore.fresh.staleNever')) : null;
  return <small className={`fcoreFresh ${freshness.state}`} dir="auto">
    {stale && <StatusChip tone="warn">{stale}</StatusChip>} {parts.join(' · ')}
    {health && freshness.state !== 'manual' && <> · <a href="#finance-sms">{t('fcore.fresh.health')}</a></>}
  </small>;
}

/** In a transaction's drawer: who set the category, the linked records with their evidence, and related rows. */
export function TxnRecords({ detail, onOpen }: { detail: FinanceRegisterDetail; onOpen: (id: string) => void }) {
  const links = detail.links ?? [];
  const related = detail.related ?? [];
  return <section className="fcoreTxn" aria-label={t('fcore.tx.links')}>
    <p><span className="muted">{t('fcore.tx.categoryBy')}:</span> {t(`fcore.tx.by.${detail.category_by ?? BY_SOURCE[detail.row.category_source] ?? 'none'}` as MessageKey)} · {categoryLabel(detail.row.category)}</p>
    <h3><Link2 aria-hidden="true" />{t('fcore.tx.links')}</h3>
    {detail.split_count ? <p className="muted">{t('fcore.tx.split', { count: detail.split_count })}</p> : null}
    {links.length === 0 ? <p className="muted">{t('fcore.tx.linksNone')}</p> : <><ul className="cmList">{links.map((l) =>
      <li key={l.id}><div><strong dir="auto">{l.target_name ?? t(`fcore.tx.dim.${dimKey(l.dimension)}` as MessageKey)}</strong>
        <small>{t(`fcore.tx.dim.${dimKey(l.dimension)}` as MessageKey)}{l.amount && l.currency ? <> · <bdi>{formatAmount(l.amount, l.currency)}</bdi></> : null}</small></div>
        <StatusChip tone={l.evidence === 'verified' ? 'ok' : l.evidence === 'pending' ? 'warn' : 'info'}>{t(`fcore.tx.evidence.${l.evidence}` as MessageKey)}</StatusChip></li>)}</ul>
      <small className="muted">{t('fcore.tx.evidenceHint')}</small></>}
    {related.length > 0 && <><h3>{t('fcore.tx.related')}</h3><ul className="cmList">{related.map((r) => <li key={`${r.relation}-${r.id}`}>
      <button type="button" className="cmLinkButton" onClick={() => onOpen(r.id)} aria-label={t('fcore.tx.open', { description: r.description })}>
        <span>{t(`fcore.tx.rel.${r.relation}` as MessageKey)}: <span dir="auto">{r.description}</span> · <bdi dir="ltr">{r.posted_on}</bdi></span></button>
      <Money value={r.amount} currency={r.currency} signed /></li>)}</ul></>}
  </section>;
}

// An older server without `category_by`: the row's own category source says the same.
const BY_SOURCE: Record<string, string> = { manual: 'owner', owner_rule: 'learned', rule: 'rule', match: 'match', system: 'system', none: 'none' };
const DIMS = new Set(['bill', 'debt', 'person_loan', 'payslip', 'remittance', 'transfer', 'vehicle', 'purchase', 'business', 'trip', 'project']);
const dimKey = (dimension: string) => (DIMS.has(dimension) ? dimension : 'other');

/** The transaction's private note: saved on its own, never changing the amount or the category. */
export function TxnNote({ transactionId, notes, onSaved }: { transactionId: string; notes: string | null; onSaved: () => void }) {
  const [text, setText] = useState(notes ?? '');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ error?: boolean; text: string } | null>(null);
  const id = useId();
  useEffect(() => { setText(notes ?? ''); setMessage(null); }, [transactionId, notes]);
  async function save() {
    setBusy(true); setMessage(null);
    try { await api.updateTransaction(transactionId, { notes: text.trim() || null }); setMessage({ text: t('fcore.tx.noteSaved') }); onSaved(); }
    catch (e) { setMessage({ error: true, text: describeError(e) }); } finally { setBusy(false); }
  }
  return <div className="fcoreNote">
    <label htmlFor={id}><StickyNote aria-hidden="true" />{t('fcore.tx.note')}</label>
    <textarea id={id} value={text} maxLength={1000} rows={2} onChange={(e) => setText(e.target.value)} aria-describedby={`${id}-hint`} />
    <small id={`${id}-hint`} className="muted">{t('fcore.tx.noteHint')}</small>
    <button type="button" className="secondary" disabled={busy || text.trim() === (notes ?? '').trim()} onClick={() => void save()}>{busy ? t('finance.saving') : t('fcore.tx.noteSave')}</button>
    {message && <Notice error={message.error}>{message.text}</Notice>}
  </div>;
}
