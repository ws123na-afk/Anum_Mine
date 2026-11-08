// The Expenses and P&L dashboard (docs/business-books.md#overview-dashboard): the top of the books overview. Income,
// expenses, net profit with its margin, cash flow and what waits for review; income against expenses over six months;
// where the money went; the newest bills with search and a status filter; what is left before the month can be closed;
// the largest recurring costs and budget progress. Every figure is the books report's (GET /business/books/report) or the
// bills list's; nothing here posts money, and amounts in other currencies than the base are never added in.
import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, Coins, Repeat, Search, ShoppingCart, Target, TrendingUp, Wallet } from 'lucide-react';
import type { BooksBill, BooksBillFilter, BooksReport, BooksSetup } from '@anum/contracts';
import { BOOKS_BILL_FILTERS } from '@anum/contracts';
import * as api from './lib/businessBooksApi';
import { billFilterLabel, lineLabel, money, paymentStateLabel, type BooksTab } from './lib/businessBooks';
import { businessTotals, percentOf } from './lib/financeStatements';
import { delta, monthsEnding, type Delta } from './lib/financeNav';
import { shiftMonth } from './lib/finance';
import { breakdownTotal, budgetProgress, previousMonthName, singleMonth, trendFor, closeChecklist, expenseBreakdown, netIn, topRecurring } from './lib/ops';
import { describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';
import { Meter, ShareBars, TrendLines } from './OpsParts';
import { BillDrawer } from './BusinessBooksForms';

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export { singleMonth };

export function BooksDashboard({ report, setup, single, onSection }: { report: BooksReport; setup: BooksSetup; single: string | null; onSection: (tab: BooksTab) => void }) {
  const base = report.base_currency;
  const totals = businessTotals(report);
  const flows = trendFor(report, monthsEnding(single ?? report.period.to.slice(0, 7), 6));
  // The month before, from the report's own trend (only when the period is one month).
  const before = single ? report.trend.find((row) => row.month.slice(0, 7) === shiftMonth(single, -1)) ?? null : null;
  const vs = single ? t('ops.vsMonth', { month: previousMonthName(single) }) : '';
  const words = (d: Delta | null) => (d && single ? `${d.percent === null ? t(`fshell.delta.${d.direction}` as MessageKey) : t(`fshell.delta.pct.${d.direction}` as MessageKey, { percent: Math.abs(d.percent) })} ${vs}` : undefined);
  const dIncome = before ? delta(totals.income, before.revenue) : null;
  const dExpenses = before ? delta(totals.expenses, before.costs, false) : null;
  const dNet = before ? delta(totals.net, before.profit) : null;
  const cash = netIn(report.cash_flow.net, base);
  const otherCash = report.cash_flow.net.filter((r) => r.currency !== base);
  const breakdown = expenseBreakdown(report);
  const breakdownSum = breakdownTotal(breakdown, base);
  const recurring = topRecurring(report, totals.expenses);
  const budgets = budgetProgress(report).slice(0, 4);
  const checks = closeChecklist(report);
  const open = checks.filter((c) => !c.done).length;
  const margin = percentOf(totals.net, totals.income);
  const ICON: Record<string, ReactNode> = { fuel: <Coins />, rent: <Wallet />, payroll: <Wallet /> };
  return <div className="bkDash">
    <KpiRow label={t('books.dash.kpis', { currency: base })} className="opsKpis5">
      <KpiCard icon={<Coins />} label={t('books.dash.income')} value={<bdi>{money(totals.income, base)}</bdi>} delta={dIncome} deltaText={words(dIncome)} />
      <KpiCard icon={<ShoppingCart />} label={t('books.dash.expenses')} value={<bdi>{money(totals.expenses, base)}</bdi>} delta={dExpenses} deltaText={words(dExpenses)}
        hint={t('books.fig.billsOpen', { count: report.balances.unpaid_bills_count })} />
      <KpiCard icon={<TrendingUp />} label={t('books.dash.net')} value={<bdi>{money(totals.net, base)}</bdi>} tone={totals.net.startsWith('-') ? 'bad' : ''} delta={dNet} deltaText={words(dNet)}
        hint={margin === null ? undefined : t('books.dash.margin', { percent: formatNumber(margin) })} />
      <KpiCard icon={<Repeat />} label={t('books.dash.cash')} value={<bdi>{money(cash, base)}</bdi>} hint={otherCash.length ? otherCash.map((r) => money(r.amount, r.currency)).join(' · ') : t('books.fig.cashHint')} />
      <KpiCard icon={<AlertTriangle />} label={t('books.dash.review')} value={formatNumber(report.allocations_in_review)} tone={report.allocations_in_review ? 'warn' : ''}
        hint={report.allocations_in_review ? t('books.dash.reviewHint') : t('books.dash.reviewNone')} />
    </KpiRow>
    <div className="bkDashRow">
      <FinCard title={t('books.dash.trend')} icon={<TrendingUp />} subtitle={t('books.dash.trendSub', { currency: base })} className="bkDashWide">
        {flows.length === 0 ? <p className="muted">{t('ops.home.trendEmpty')}</p>
          : <TrendLines rows={flows} currency={base} title={t('books.dash.trend')} incomeLabel={t('books.dash.income')} expenseLabel={t('books.dash.expenses')} />}
        {report.trend.some((row) => row.unconverted) && <p className="muted bzHint">{t('books.trend.partial')}</p>}
      </FinCard>
      <FinCard title={t('books.dash.breakdown')} subtitle={breakdown.length ? t('books.dash.total', { amount: money(breakdownSum, base) }) : undefined}>
        {breakdown.length === 0 ? <p className="muted">{t('books.dash.breakdownNone')}</p>
          : <ShareBars rows={breakdown} currency={base} label={t('books.dash.breakdown')} />}
      </FinCard>
    </div>
    <div className="bkDashRow">
      <RecentBills setup={setup} className="bkDashWide" onAll={() => onSection('bills')} />
      <FinCard title={t('books.dash.close')} icon={<CheckCircle2 />}>
        <ul className="opsChecklist">{checks.map((c) => <li key={c.key} className={c.done ? 'done' : 'open'}>
          {c.done ? <CheckCircle2 aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <span>{t(`books.dash.check.${c.key}${c.done ? 'Done' : ''}` as MessageKey, { count: c.count })}</span></li>)}</ul>
        {report.allocations_in_review > 0
          ? <button type="button" onClick={() => onSection('allocations')}>{t('books.dash.reviewButton', { count: report.allocations_in_review })}</button>
          : open > 0 ? <button type="button" className="secondary" onClick={() => onSection('bills')}>{t('books.dash.openBills')}</button>
          : <p className="muted bzHint">{t('books.dash.closeReady')}</p>}
      </FinCard>
    </div>
    <div className="bkDashRow">
      <FinCard title={t('books.dash.recurring')} icon={<Repeat />} className="bkDashHalf"
        actions={<button type="button" className="cmLinkButton" onClick={() => onSection('recurring')}>{t('ops.viewAll')}</button>}>
        {recurring.length === 0 ? <p className="muted">{t('books.recurring.none')}</p>
          : <ul className="opsTiles">{recurring.map((r) => <li key={r.key}><span className="opsTileIcon" aria-hidden="true">{ICON[r.category] ?? <Repeat />}</span>
            <span><strong dir="auto">{r.name}</strong><small>{lineLabel(r.category)}</small><b><bdi>{money(r.perMonth, base)}</bdi></b>
              {r.share !== null && <small>{t('books.dash.ofExpenses', { percent: formatNumber(r.share) })}</small>}</span></li>)}</ul>}
      </FinCard>
      <FinCard title={t('books.dash.budgets')} icon={<Target />} className="bkDashHalf"
        actions={<button type="button" className="cmLinkButton" onClick={() => onSection('budgets')}>{t('ops.viewAll')}</button>}>
        {budgets.length === 0 ? <p className="muted">{t('books.budgets.none')}</p>
          : <ul className="opsBudgets">{budgets.map((b) => <li key={b.id}><strong dir="auto">{b.name}</strong>
            <Meter percent={b.percent} tone={b.tone === 'bad' ? 'bad' : b.tone === 'warn' ? 'warn' : 'ok'} label={t('books.dash.budgetLabel', { name: b.name, percent: Math.round(b.percent) })} />
            <span><bdi>{money(b.actual, b.currency)}</bdi> / <bdi>{money(b.budget, b.currency)}</bdi></span>
            <StatusChip tone={b.tone === 'bad' ? 'bad' : b.tone === 'warn' ? 'warn' : 'ok'}>{t('ops.share', { percent: Math.round(b.percent) })}</StatusChip></li>)}</ul>}
      </FinCard>
    </div>
  </div>;
}

type BillsLoad = { kind: 'loading' } | { kind: 'ready'; items: BooksBill[]; total: number } | { kind: 'error'; message: string };

/** The newest bills with a search and a status filter (the server filters); a row opens the bill. */
function RecentBills({ setup, className, onAll }: { setup: BooksSetup; className?: string; onAll: () => void }) {
  const [q, setQ] = useState(''), [search, setSearch] = useState(''), [status, setStatus] = useState<BooksBillFilter | ''>('');
  const [bills, setBills] = useState<BillsLoad>({ kind: 'loading' });
  const [openId, setOpenId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const statusId = useId();
  const load = useCallback(async () => {
    setBills({ kind: 'loading' });
    try { const page = await api.listBills(search, status, 0, 6); setBills({ kind: 'ready', items: page.items, total: page.total }); }
    catch (error) { setBills({ kind: 'error', message: describeError(error) }); }
  }, [search, status, tick]);
  useEffect(() => { void load(); }, [load]);
  return <FinCard title={t('books.dash.bills')} icon={<ShoppingCart />} className={className}
    actions={<button type="button" className="cmLinkButton" onClick={onAll}>{t('ops.viewAll')}</button>}>
    <form className="opsFilters" role="search" onSubmit={(e) => { e.preventDefault(); setSearch(q.trim()); }}>
      <label className="opsSearch"><span className="visuallyHidden">{t('books.bills.search')}</span><Search aria-hidden="true" />
        <input type="search" dir="auto" value={q} maxLength={80} placeholder={t('books.bills.search')} onChange={(e) => { setQ(e.target.value); if (!e.target.value) setSearch(''); }} /></label>
      <button type="submit" className="secondary" disabled={q.trim() === search}>{t('books.dash.find')}</button>
      <label className="opsSelect" htmlFor={statusId}><span className="visuallyHidden">{t('books.bills.filter')}</span>
        <select id={statusId} value={status} onChange={(e) => setStatus(e.target.value as BooksBillFilter | '')}>
          <option value="">{t('books.bills.all')}</option>{BOOKS_BILL_FILTERS.map((item) => <option key={item} value={item}>{billFilterLabel(item)}</option>)}</select></label>
    </form>
    {bills.kind === 'loading' && <p className="muted" role="status">{t('books.reading')}</p>}
    {bills.kind === 'error' && <div className="opsInlineError" role="alert"><span>{bills.message}</span><button type="button" className="secondary" onClick={() => void load()}>{t('ops.retry')}</button></div>}
    {bills.kind === 'ready' && bills.items.length === 0 && <p className="muted">{search || status ? t('books.dash.billsNoMatch') : t('books.bills.emptyTitle')}</p>}
    {bills.kind === 'ready' && bills.items.length > 0 && <div className="finTableWrap" tabIndex={0}>
      <table className="finGrid bkDashBills"><caption className="visuallyHidden">{t('books.dash.bills')}</caption>
        <thead><tr><th scope="col">{t('books.dash.col.date')}</th><th scope="col">{t('books.dash.col.description')}</th><th scope="col">{t('books.dash.col.category')}</th>
          <th scope="col">{t('books.dash.col.status')}</th><th scope="col" className="num">{t('books.dash.col.amount')}</th></tr></thead>
        <tbody>{bills.items.map((bill) => <tr key={bill.id}>
          <td className="nowrap"><bdi>{day(bill.bill_date)}</bdi></td>
          <th scope="row"><button type="button" className="linkButton" onClick={() => setOpenId(bill.id)}><span dir="auto">{bill.vendor_name}</span></button>
            <small dir="auto">{bill.description}</small></th>
          <td>{bill.category ? lineLabel(bill.category) : '-'}</td>
          <td><StatusChip tone={bill.payment_state === 'paid' ? 'ok' : bill.overdue ? 'bad' : bill.payment_state === 'void' || bill.payment_state === 'draft' ? 'muted' : 'warn'}>
            {bill.overdue ? t('books.daysLate', { days: bill.days_overdue }) : paymentStateLabel(bill.payment_state)}</StatusChip></td>
          <td className="num"><bdi>{money(bill.total, bill.currency)}</bdi></td>
        </tr>)}</tbody>
      </table>
    </div>}
    {bills.kind === 'ready' && bills.total > bills.items.length && <p className="muted bzHint">{t('books.dash.billsMore', { shown: bills.items.length, total: bills.total })}</p>}
    {openId && <BillDrawer setup={setup} billId={openId} onClose={() => setOpenId(null)} onChanged={() => setTick((n) => n + 1)} />}
  </FinCard>;
}
