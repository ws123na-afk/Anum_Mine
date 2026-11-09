// Finance reports (docs/finance.md#reports), from the owner's mockup of 5 Nov 2026. Personal figures
// come from the month summaries (GET /finance/summary?month=), business figures from the business books
// report (GET /business/books/report). Nothing is invented: a widget without data says so and offers
// the next step. Read only; the downloads are the API's own CSV exports.
import { Suspense, lazy, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ArrowDownLeft, ArrowUpRight, BarChart3, Briefcase, CircleCheck, Download, ExternalLink, FileSpreadsheet, Info, Landmark, Percent, PieChart, Receipt,
  Scale, ShieldCheck, TrendingUp, TriangleAlert, Wallet,
} from 'lucide-react';
import type { BooksReport, FinanceAccount, FinanceCategory, FinanceSummary } from '@anum/contracts';
import * as api from './lib/financeApi';
import * as books from './lib/businessBooksApi';
import { categoryLabel, formatAmount, isolate, monthLabel, shiftMonth, type FinanceTab } from './lib/finance';
import { delta, monthRange, monthsEnding, type FinanceScope } from './lib/financeNav';
import {
  add, businessExpenseRows, businessFlows, businessTotals, categoryRows, keyMovements, netWorth, percentOf, personalFlows, reportCurrencies, reportQuality,
  type MonthFlow,
} from './lib/financeStatements';
import { lineLabel } from './lib/businessBooks';
import { describeError } from './lib/errors';
import { formatNumber, t, type MessageKey } from './lib/i18n';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import './financePages.css';
import { DeltaChip, FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, PeriodPicker, ScopeSwitch, StatusChip, tabPanel } from './FinanceShell';

const FlowChart = lazy(() => import('./FinanceCharts').then((m) => ({ default: m.FlowChart })));

export type ReportTab = 'pnl' | 'cashflow' | 'spending' | 'networth' | 'tax' | 'custom';
const REPORT_TABS: readonly ReportTab[] = ['pnl', 'cashflow', 'spending', 'networth', 'tax', 'custom'];
const MONTHS = 6;
const label = (key: string) => t(key as MessageKey);
const pct = (value: number | null) => (value === null ? '-' : `${formatNumber(value)}%`);

interface Personal { months: FinanceSummary[]; current: FinanceSummary; previous: FinanceSummary | null }
interface Business { current: BooksReport; previous: BooksReport | null }

/** One shape for a scope's month: what the KPI row, the chart and the movements read. */
interface View { currency: string; flows: MonthFlow[]; now: MonthFlow | null; before: MonthFlow | null }

export function FinanceReportsPage({ onOpen }: { onOpen: (tab: FinanceTab) => void }) {
  const [scope, setScope] = useState<FinanceScope>('personal');
  const [tab, setTab] = useState<ReportTab>('pnl');
  const [month, setMonth] = useState<string | null>(null);
  const [today, setToday] = useState<string | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [personal, setPersonal] = useState<Load<Personal>>({ kind: 'loading' });
  const [business, setBusiness] = useState<Load<Business>>({ kind: 'loading' });

  // Each load carries a generation number: an answer for a month (or a retry) that is no longer the
  // latest request is dropped, so a slow older month never replaces the month on screen.
  const personalRun = useRef(0), businessRun = useRef(0);
  const loadPersonal = useCallback(async () => {
    const run = ++personalRun.current;
    const current = () => run === personalRun.current;
    setPersonal({ kind: 'loading' });
    try {
      const first = await api.getSummary(month ?? undefined);
      if (!current()) return;
      if (!month) { setMonth(first.month); setToday(first.today); return; }
      const others = await Promise.all(monthsEnding(month, MONTHS).slice(0, -1).map((m) => api.getSummary(m)));
      if (!current()) return;
      setToday(first.today);
      setPersonal({ kind: 'ready', value: { months: [...others, first], current: first, previous: others[others.length - 1] ?? null } });
    } catch (e) { if (current()) setPersonal(failed(e)); }
  }, [month]);
  const loadBusiness = useCallback(async () => {
    if (!month) return;
    const run = ++businessRun.current;
    setBusiness({ kind: 'loading' });
    try {
      const [current, previous] = await Promise.all([books.getReport(monthRange(month)), books.getReport(monthRange(shiftMonth(month, -1))).catch(() => null)]);
      if (run === businessRun.current) setBusiness({ kind: 'ready', value: { current, previous } });
    } catch (e) { if (run === businessRun.current) setBusiness(failed(e)); }
  }, [month]);
  useEffect(() => { void loadPersonal(); }, [loadPersonal]);
  useEffect(() => { void loadBusiness(); }, [loadBusiness]);

  const p = personal.kind === 'ready' ? personal.value : null;
  const b = business.kind === 'ready' ? business.value : null;
  const currencies = p ? reportCurrencies(p.months, p.current.base_currency) : [];
  const shownCurrency = currency && currencies.includes(currency) ? currency : currencies[0] ?? p?.current.base_currency ?? 'SAR';

  const view: View | null = useMemo(() => {
    if (scope === 'business') {
      if (!b) return null;
      const flows = businessFlows(b.current, MONTHS);
      const totals = businessTotals(b.current), before = b.previous ? businessTotals(b.previous) : null;
      return { currency: b.current.base_currency, flows, now: { month: month ?? '', income: totals.income, expenses: totals.expenses, net: totals.net },
        before: before ? { month: shiftMonth(month ?? '', -1), income: before.income, expenses: before.expenses, net: before.net } : null };
    }
    if (!p) return null;
    const flows = personalFlows(p.months, shownCurrency);
    return { currency: shownCurrency, flows, now: flows[flows.length - 1] ?? null, before: flows[flows.length - 2] ?? null };
  }, [scope, p, b, shownCurrency, month]);

  const unavailable = business.kind === 'denied' ? { business: t('frep.scope.businessDenied') } : undefined;
  const head = <FinancePageHead title={t('frep.title')} subtitle={t('frep.subtitle')}
    actions={<DownloadButton scope={scope} month={month} />}
    controls={<>
      <ScopeSwitch value={scope} onChange={setScope} unavailable={unavailable} />
      {month && <PeriodPicker month={month} onChange={setMonth} max={today?.slice(0, 7)} />}
      {scope !== 'business' && currencies.length > 1 && <label className="finControl"><span className="finControlLabel">{t('frep.currency')}</span>
        <select value={shownCurrency} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
    </>}
    tabs={<FinanceTabs label={t('frep.tabs')} idPrefix="frep" value={tab} onChange={setTab} items={REPORT_TABS.map((id) => ({ id, label: t(`frep.tab.${id}` as MessageKey) }))} />} />;

  const sourceState = scope === 'business' ? business : personal;
  let body: ReactNode;
  if (personal.kind === 'denied') body = <Denied />;
  else if (sourceState.kind === 'loading' || (scope !== 'business' && !month)) body = <Notice busy>{t('frep.loading')}</Notice>;
  else if (sourceState.kind === 'denied') body = <BusinessDenied />;
  else if (sourceState.kind === 'error') body = <LoadError message={t('frep.error', { message: sourceState.message })} retry={() => void (scope === 'business' ? loadBusiness() : loadPersonal())} />;
  else if (view && month) {
    const ctx = { scope, month, view, personal: p, business: business, onOpen, onScope: setScope, retryBusiness: () => void loadBusiness() };
    body = tab === 'pnl' ? <ProfitAndLoss {...ctx} /> : tab === 'cashflow' ? <CashFlow {...ctx} /> : tab === 'spending' ? <Spending {...ctx} />
      : tab === 'networth' ? <NetWorth {...ctx} /> : tab === 'tax' ? <Tax {...ctx} /> : scope === 'business' ? <CustomBusiness onPersonal={() => setScope('personal')} /> : <Custom month={month} onOpen={onOpen} />;
  }
  return <div className="finPage finReportsPage">
    {head}
    <div {...tabPanel('frep', tab)}>{body}</div>
  </div>;
}

interface Ctx {
  scope: FinanceScope; month: string; view: View; personal: Personal | null; business: Load<Business>; onOpen: (tab: FinanceTab) => void;
  onScope: (scope: FinanceScope) => void; retryBusiness: () => void;
}

function BusinessDenied() {
  return <FinCard title={t('frep.business.deniedTitle')} icon={<Briefcase />}><p className="muted">{t('frep.business.deniedText')}</p></FinCard>;
}

function DownloadButton({ scope, month }: { scope: FinanceScope; month: string | null }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!month) return null;
  const run = async () => {
    setBusy(true); setError(null);
    try {
      if (scope === 'business') await books.exportReport(monthRange(month));
      else await api.downloadTransactions(monthRange(month));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <>
    <button type="button" disabled={busy} onClick={() => void run()}><Download aria-hidden="true" />{busy ? t('frep.downloading') : scope === 'business' ? t('frep.downloadPnl') : t('frep.downloadCsv')}</button>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
  </>;
}

// ---- Profit and loss --------------------------------------------------------------------------------

function ProfitAndLoss(ctx: Ctx) {
  const { view, scope, month, personal, business } = ctx;
  const now = view.now, before = view.before;
  const vs = before ? t('frep.vsMonth', { month: monthLabel(before.month) }) : undefined;
  const margin = scope === 'business' && business.kind === 'ready' ? businessTotals(business.value.current).margin : now ? percentOf(now.net, now.income) : null;
  const marginBefore = scope === 'business' && business.kind === 'ready' && business.value.previous ? businessTotals(business.value.previous).margin : before ? percentOf(before.net, before.income) : null;
  const empty = !now || (Number(now.income) === 0 && Number(now.expenses) === 0);
  return <>
    <KpiRow label={t('frep.kpis', { month: monthLabel(month) })}>
      <KpiCard icon={<ArrowDownLeft />} label={t('frep.kpi.income')} value={<Money value={now?.income ?? '0'} currency={view.currency} />} delta={delta(now?.income, before?.income, true)} hint={vs} />
      <KpiCard icon={<ArrowUpRight />} label={t('frep.kpi.expenses')} value={<Money value={now?.expenses ?? '0'} currency={view.currency} />} delta={delta(now?.expenses, before?.expenses, false)} hint={vs} />
      <KpiCard icon={<BarChart3 />} label={t('frep.kpi.net')} value={<Money value={now?.net ?? '0'} currency={view.currency} signed />} delta={delta(now?.net, before?.net, true)} hint={vs} />
      <KpiCard icon={<Percent />} label={scope === 'business' ? t('frep.kpi.margin') : t('frep.kpi.savings')} value={pct(margin)}
        delta={margin !== null && marginBefore !== null ? delta(margin, marginBefore, true) : null} hint={margin === null ? t('frep.kpi.noIncome') : vs} />
    </KpiRow>
    {empty && <FinBanner icon={<Info />} title={t('frep.empty.title', { month: monthLabel(month) })}
      action={scope === 'business' ? <a className="finLinkBtn secondary" href="#business-books">{t('frep.openBooks')}</a> : <button type="button" className="secondary" onClick={() => ctx.onOpen('import')}>{t('frep.empty.import')}</button>}>
      {scope === 'business' ? t('frep.empty.business') : t('frep.empty.personal')}</FinBanner>}
    <div className="finReportGrid finReportGridTop">
      <FinCard title={t('frep.chart.title')} icon={<TrendingUp />} subtitle={t('frep.chart.subtitle', { count: view.flows.length })}>
        {view.flows.length === 0 ? <p className="muted">{t('frep.chart.none')}</p>
          : <Suspense fallback={<Notice busy>{t('frep.loading')}</Notice>}><FlowChart rows={view.flows} currency={view.currency} highlight={month} title={t('frep.chart.title')} /></Suspense>}
      </FinCard>
      <Movements ctx={ctx} />
    </div>
    <div className="finReportGrid finReportGridThree">
      <Expenses ctx={ctx} />
      <BusinessCard business={business} month={month} retry={ctx.retryBusiness} />
      <Quality personal={personal} business={business} scope={scope} currency={view.currency} onOpen={ctx.onOpen} />
    </div>
    <div className="finReportGrid finReportGridBottom">
      <Compare personal={personal} business={business} month={month} currency={view.currency} />
      <FinBanner icon={<Info />} title={t('frep.about.title')}>{t('frep.about.text')}</FinBanner>
    </div>
  </>;
}

interface DisplayRow { key: string; label: string; now: string; before: string; change: string; percent: number | null; share: number | null }

function rowsFor(ctx: Ctx): DisplayRow[] {
  if (ctx.scope === 'business') {
    if (ctx.business.kind !== 'ready') return [];
    return businessExpenseRows(ctx.business.value.current, ctx.business.value.previous).map((r) => ({ ...r, label: lineLabel(r.key) }));
  }
  if (!ctx.personal) return [];
  return categoryRows(ctx.personal.current, ctx.personal.previous, ctx.view.currency).map((r) => ({ key: r.category, label: categoryLabel(r.category as FinanceCategory), now: r.now, before: r.before, change: r.change, percent: r.percent, share: r.share }));
}

function changeText(row: { change: string; before: string; now: string }, currency: string) {
  return t('frep.move.detail', { now: isolate(formatAmount(row.now, currency)), before: isolate(formatAmount(row.before, currency)) });
}

function Movements({ ctx }: { ctx: Ctx }) {
  const rows = rowsFor(ctx);
  const moves = keyMovements(rows.map((r) => ({ ...r, category: r.key as FinanceCategory, count: 0 })), 3).map((m) => rows.find((r) => r.key === m.category)!);
  const before = shiftMonth(ctx.month, -1);
  return <FinCard title={t('frep.move.title')} icon={<TrendingUp />} className="finMovements">
    {moves.length === 0 ? <p className="muted">{t('frep.move.none')}</p> : <ul className="finMoveList">{moves.map((m) => {
      const d = delta(m.now, m.before, false);
      return <li key={m.key}><div><strong>{m.label}</strong><small>{changeText(m, ctx.view.currency)}</small></div>{d && <DeltaChip delta={d} />}</li>;
    })}</ul>}
    <p className="muted finFoot">{t('frep.move.against', { month: monthLabel(before) })}</p>
  </FinCard>;
}

function Expenses({ ctx }: { ctx: Ctx }) {
  const rows = rowsFor(ctx);
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 8);
  const c = ctx.view.currency;
  const total = { now: ctx.view.now?.expenses ?? '0', before: ctx.view.before?.expenses ?? '0' };
  const totalDelta = delta(total.now, total.before, false);
  return <FinCard title={t('frep.exp.title')} subtitle={monthLabel(ctx.month)} icon={<Receipt />} className="finExpenses">
    {rows.length === 0 ? <Empty icon={<Receipt aria-hidden="true" />} title={t('frep.exp.none')} text={t('frep.exp.noneText')}
      action={<button type="button" className="secondary" onClick={() => ctx.onOpen('transactions')}>{t('frep.openTransactions')}</button>} /> : <>
      <div className="finTableWrap" tabIndex={0}><table className="finGrid">
        <caption className="visuallyHidden">{t('frep.exp.title')}</caption>
        <thead><tr><th scope="col">{t('frep.col.category')}</th><th scope="col" className="num">{monthLabel(ctx.month)}</th><th scope="col" className="num">{monthLabel(shiftMonth(ctx.month, -1))}</th>
          <th scope="col" className="num">{t('frep.col.change')}</th><th scope="col" className="num">{t('frep.col.share')}</th></tr></thead>
        <tbody>{shown.map((r) => { const d = delta(r.now, r.before, false); return <tr key={r.key}><th scope="row">{r.label}</th>
          <td className="num"><Money value={r.now} currency={c} /></td><td className="num"><Money value={r.before} currency={c} /></td>
          <td className="num">{d ? <DeltaChip delta={d} compact /> : '-'}</td><td className="num">{pct(r.share)}</td></tr>; })}</tbody>
        <tfoot><tr><th scope="row">{t('frep.exp.total')}</th><td className="num"><Money value={total.now} currency={c} /></td><td className="num"><Money value={total.before} currency={c} /></td>
          <td className="num">{totalDelta ? <DeltaChip delta={totalDelta} compact /> : '-'}</td><td className="num">{rows.length ? '100%' : '-'}</td></tr></tfoot>
      </table></div>
      {rows.length > 8 && <button type="button" className="cmLinkButton" onClick={() => setAll(!all)}>{all ? t('frep.exp.fewer') : t('frep.exp.all', { count: rows.length })}</button>}
    </>}
  </FinCard>;
}

function BusinessCard({ business, month, retry }: { business: Load<Business>; month: string; retry: () => void }) {
  const body = business.kind === 'loading' ? <Notice busy>{t('frep.loading')}</Notice>
    : business.kind === 'denied' ? <p className="muted">{t('frep.business.deniedText')}</p>
    : business.kind === 'error' ? <LoadError message={t('frep.error', { message: business.message })} retry={retry} />
    : (() => {
      const x = businessTotals(business.value.current);
      return <>
        <dl className="finStatement">
          <div><dt>{t('frep.kpi.income')}</dt><dd><Money value={x.income} currency={x.currency} /></dd></div>
          <div><dt>{t('frep.kpi.expenses')}</dt><dd><Money value={x.expenses} currency={x.currency} /></dd></div>
          <div className="finStatementTotal"><dt>{t('frep.business.gross')}</dt><dd><Money value={x.gross} currency={x.currency} signed /></dd></div>
          <div className="finStatementTotal"><dt>{t('frep.kpi.net')}</dt><dd><Money value={x.net} currency={x.currency} signed /></dd></div>
        </dl>
        {!x.complete && <p className="muted finFoot">{t('frep.business.incomplete')}</p>}
      </>;
    })();
  return <FinCard title={t('frep.business.title')} subtitle={monthLabel(month)} icon={<Briefcase />} className="finBusinessCard">
    {body}
    <a className="finCardLink" href="#business-books"><ExternalLink aria-hidden="true" />{t('frep.openBooks')}</a>
  </FinCard>;
}

function Quality({ personal, business, scope, currency, onOpen }: { personal: Personal | null; business: Load<Business>; scope: FinanceScope; currency: string; onOpen: (tab: FinanceTab) => void }) {
  const items: ReactNode[] = [];
  if (scope !== 'business' && personal) {
    const q = reportQuality(personal.current, currency);
    items.push(<li key="cat" className={q.uncategorized ? 'warn' : 'ok'}>{q.uncategorized ? <TriangleAlert aria-hidden="true" /> : <CircleCheck aria-hidden="true" />}<div>
      <strong>{q.categorizedPercent === null ? t('frep.quality.noSpend') : t('frep.quality.categorized', { percent: formatNumber(q.categorizedPercent) })}</strong>
      <small>{t('frep.quality.categorizedText', { categorized: q.spendCount - q.uncategorized, total: q.spendCount })}</small></div>
      {q.uncategorized > 0 && <button type="button" className="cmLinkButton" onClick={() => onOpen('transactions')}>{t('frep.quality.review')}</button>}</li>);
    items.push(<li key="count" className="ok"><CircleCheck aria-hidden="true" /><div><strong>{t('frep.quality.transactions', { count: q.transactions })}</strong><small>{t('frep.quality.transactionsText')}</small></div></li>);
  }
  if (scope !== 'personal' && business.kind === 'ready') {
    const r = business.value.current;
    items.push(<li key="review" className={r.allocations_in_review ? 'warn' : 'ok'}>{r.allocations_in_review ? <TriangleAlert aria-hidden="true" /> : <CircleCheck aria-hidden="true" />}<div>
      <strong>{t('frep.quality.inReview', { count: r.allocations_in_review })}</strong><small>{t('frep.quality.inReviewText')}</small></div></li>);
    if (r.tax.allocations_without_tax_split) items.push(<li key="tax" className="warn"><TriangleAlert aria-hidden="true" /><div><strong>{t('frep.quality.noTaxSplit', { count: r.tax.allocations_without_tax_split })}</strong><small>{t('frep.quality.noTaxSplitText')}</small></div></li>);
    if (!r.pnl.complete) items.push(<li key="fx" className="warn"><TriangleAlert aria-hidden="true" /><div><strong>{t('frep.quality.unconverted')}</strong><small>{t('frep.business.incomplete')}</small></div></li>);
  }
  return <FinCard title={t('frep.quality.title')} icon={<ShieldCheck />} className="finQuality">
    {items.length === 0 ? <p className="muted">{t('frep.quality.none')}</p> : <ul className="finQualityList">{items}</ul>}
  </FinCard>;
}

function Compare({ personal, business, month, currency }: { personal: Personal | null; business: Load<Business>; month: string; currency: string }) {
  const rows: { key: string; icon: ReactNode; label: string; currency: string; income: string; expenses: string; net: string; margin: number | null }[] = [];
  if (business.kind === 'ready') {
    const x = businessTotals(business.value.current);
    rows.push({ key: 'b', icon: <Briefcase aria-hidden="true" />, label: t('frep.compare.business'), currency: x.currency, income: x.income, expenses: x.expenses, net: x.net, margin: x.margin });
  }
  if (personal) {
    const flows = personalFlows([personal.current], currency)[0];
    rows.push({ key: 'p', icon: <Wallet aria-hidden="true" />, label: t('frep.compare.personal'), currency, income: flows.income, expenses: flows.expenses, net: flows.net, margin: percentOf(flows.net, flows.income) });
  }
  return <FinCard title={t('frep.compare.title')} subtitle={monthLabel(month)} icon={<Scale />} className="finCompare">
    {rows.length === 0 ? <p className="muted">{t('frep.compare.none')}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
      <caption className="visuallyHidden">{t('frep.compare.title')}</caption>
      <thead><tr><th scope="col">{t('fshell.scope')}</th><th scope="col" className="num">{t('frep.kpi.income')}</th><th scope="col" className="num">{t('frep.kpi.expenses')}</th>
        <th scope="col" className="num">{t('frep.kpi.net')}</th><th scope="col" className="num">{t('frep.compare.margin')}</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.key}><th scope="row"><span className="finRowLabel">{r.icon}{r.label}</span></th>
        <td className="num"><Money value={r.income} currency={r.currency} /></td><td className="num"><Money value={r.expenses} currency={r.currency} /></td>
        <td className="num"><Money value={r.net} currency={r.currency} signed /></td><td className="num">{pct(r.margin)}</td></tr>)}</tbody>
    </table></div>}
    {business.kind === 'denied' && <p className="muted finFoot">{t('frep.compare.noBusiness')}</p>}
  </FinCard>;
}

// ---- Cash flow, spending, net worth, tax, custom -------------------------------------------------

function CashFlow(ctx: Ctx) {
  const { view, scope, business } = ctx;
  const net = add(view.currency, ...view.flows.map((f) => f.net));
  return <>
    {/* Business: only the books' cash movements. Their trend is accrual profit, never shown as money in and out. */}
    {scope !== 'business' && <FinCard title={t('frep.cash.title')} subtitle={t('frep.chart.subtitle', { count: view.flows.length })} icon={<Landmark />}>
      {view.flows.length === 0 ? <p className="muted">{t('frep.chart.none')}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
        <caption className="visuallyHidden">{t('frep.cash.title')}</caption>
        <thead><tr><th scope="col">{t('frep.col.month')}</th><th scope="col" className="num">{t('frep.cash.in')}</th><th scope="col" className="num">{t('frep.cash.out')}</th><th scope="col" className="num">{t('frep.kpi.net')}</th></tr></thead>
        <tbody>{view.flows.map((f) => <tr key={f.month}><th scope="row">{monthLabel(f.month)}</th><td className="num"><Money value={f.income} currency={view.currency} /></td>
          <td className="num"><Money value={f.expenses} currency={view.currency} /></td><td className="num"><Money value={f.net} currency={view.currency} signed /></td></tr>)}</tbody>
        <tfoot><tr><th scope="row" colSpan={3}>{t('frep.cash.total', { count: view.flows.length })}</th><td className="num"><Money value={net} currency={view.currency} signed /></td></tr></tfoot>
      </table></div>}
      <p className="muted finFoot">{t('frep.cash.personalNote')}</p>
    </FinCard>}
    {scope === 'business' && business.kind === 'ready' && <FinCard title={t('books.cash.title')} subtitle={monthLabel(ctx.month)} icon={<Briefcase />}>
      <p className="muted">{t('books.cash.help')}</p>
      {Object.keys(business.value.current.cash_flow.lines).length === 0 ? <p className="muted">{t('books.cash.none')}</p> : <dl className="finStatement">
        {Object.entries(business.value.current.cash_flow.lines).map(([key, values]) => <div key={key}><dt>{label(`books.cash.${key}`)}</dt>
          <dd>{values.map((v) => <Money key={v.currency} value={v.amount} currency={v.currency} signed />)}</dd></div>)}
        <div className="finStatementTotal"><dt>{t('books.cash.net')}</dt><dd>{business.value.current.cash_flow.net.map((v) => <Money key={v.currency} value={v.amount} currency={v.currency} signed />)}</dd></div>
      </dl>}
    </FinCard>}
  </>;
}

function Spending(ctx: Ctx) {
  const rows = rowsFor(ctx);
  const c = ctx.view.currency;
  return <FinCard title={t('frep.spend.title')} subtitle={monthLabel(ctx.month)} icon={<PieChart />}>
    {rows.length === 0 ? <Empty icon={<PieChart aria-hidden="true" />} title={t('frep.exp.none')} text={t('frep.exp.noneText')}
      action={<button type="button" className="secondary" onClick={() => ctx.onOpen('transactions')}>{t('frep.openTransactions')}</button>} />
      : <ul className="finShareList">{rows.map((r) => <li key={r.key}>
        <div className="finShareHead"><strong>{r.label}</strong><span><Money value={r.now} currency={c} /> <small className="muted">{pct(r.share)}</small></span></div>
        <span className="finTrack" aria-hidden="true"><span className="finFill" style={{ width: `${Math.max(0, Math.min(100, r.share ?? 0))}%` }} /></span>
      </li>)}</ul>}
  </FinCard>;
}

function NetWorth(ctx: Ctx) {
  if (ctx.scope === 'business') {
    if (ctx.business.kind !== 'ready') return null;
    const bal = ctx.business.value.current.balances;
    const list = (rows: { currency: string; total?: string; amount?: string }[]) => rows.length === 0 ? '-' : rows.map((r) => <Money key={r.currency} value={r.total ?? r.amount ?? '0'} currency={r.currency} />);
    return <FinCard title={t('frep.worth.businessTitle')} subtitle={t('frep.worth.asOf', { date: isolate(bal.as_of) })} icon={<Briefcase />}>
      <dl className="finStatement">
        <div><dt>{t('frep.worth.receivables')}</dt><dd>{list(bal.receivables)}</dd></div>
        <div><dt>{t('frep.worth.payables')}</dt><dd>{list(bal.payables)}</dd></div>
        <div><dt>{t('frep.worth.debt')}</dt><dd>{list(bal.debt)}</dd></div>
        <div><dt>{t('frep.worth.owedMembers')}</dt><dd>{list(bal.owed_to_members)}</dd></div>
      </dl>
      <p className="muted finFoot">{t('frep.worth.businessNote')}</p>
    </FinCard>;
  }
  if (!ctx.personal) return null;
  const s = ctx.personal.months[ctx.personal.months.length - 1];
  const rows = netWorth(s);
  return <>
    <FinCard title={t('frep.worth.title')} subtitle={t('frep.worth.today')} icon={<Scale />}>
      {rows.length === 0 ? <Empty icon={<Wallet aria-hidden="true" />} title={t('frep.worth.none')} text={t('frep.worth.noneText')}
        action={<button type="button" className="secondary" onClick={() => ctx.onOpen('accounts')}>{t('finance.accounts.add')}</button>} />
        : <div className="finTableWrap" tabIndex={0}><table className="finGrid"><caption className="visuallyHidden">{t('frep.worth.title')}</caption>
          <thead><tr><th scope="col">{t('finance.form.currency')}</th><th scope="col" className="num">{t('frep.worth.accounts')}</th><th scope="col" className="num">{t('frep.worth.debts')}</th><th scope="col" className="num">{t('frep.worth.net')}</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.currency}><th scope="row">{r.currency}</th><td className="num"><Money value={r.assets} currency={r.currency} signed /></td>
            <td className="num"><Money value={r.debts} currency={r.currency} /></td><td className="num"><strong><Money value={r.net} currency={r.currency} signed /></strong></td></tr>)}</tbody>
        </table></div>}
      <p className="muted finFoot">{t('frep.worth.note')}</p>
      {(s.net_worth_excluded ?? []).length > 0 && <p className="muted finFoot" role="note">{t('frep.worth.excluded', { list: (s.net_worth_excluded ?? [])
        .map((e) => t(`frep.worth.excluded.${e.reason}` as MessageKey, { name: e.name, currency: e.currency })).join(', ') })}</p>}
    </FinCard>
    {s.accounts.length > 0 && <FinCard title={t('finance.overview.accounts')} icon={<Landmark />}>
      <ul className="cmList">{s.accounts.filter((a) => !a.archived).map((a) => <li key={a.id}><div><strong dir="auto">{a.name}</strong><small>{t(`finance.kind.${a.kind}` as MessageKey)}</small></div>
        <Money value={a.balance} currency={a.currency} signed /></li>)}</ul>
    </FinCard>}
  </>;
}

function Tax(ctx: Ctx) {
  if (ctx.scope !== 'business') {
    return <FinCard title={t('frep.tax.title')} icon={<FileSpreadsheet />}>
      <Empty icon={<FileSpreadsheet aria-hidden="true" />} title={t('frep.tax.personalTitle')} text={t('frep.tax.personalText')}
        action={ctx.business.kind === 'ready' ? <button type="button" className="secondary" onClick={() => ctx.onScope('business')}>{t('frep.tax.showBusiness')}</button> : undefined} />
    </FinCard>;
  }
  if (ctx.business.kind !== 'ready') return null;
  const tax = ctx.business.value.current.tax;
  return <FinCard title={tax.label ? t('books.tax.titleNamed', { label: tax.label }) : t('books.tax.title')} subtitle={monthLabel(ctx.month)} icon={<FileSpreadsheet />}>
    {tax.by_currency.length === 0 ? <p className="muted">{t('books.tax.none')}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
      <caption className="visuallyHidden">{t('books.tax.title')}</caption>
      <thead><tr><th scope="col">{t('finance.form.currency')}</th><th scope="col" className="num">{t('books.tax.output')}</th><th scope="col" className="num">{t('books.tax.input')}</th><th scope="col" className="num">{t('books.tax.net')}</th></tr></thead>
      <tbody>{tax.by_currency.map((r) => <tr key={r.currency}><th scope="row">{r.currency}</th><td className="num"><Money value={r.output} currency={r.currency} /></td>
        <td className="num"><Money value={r.input} currency={r.currency} /></td><td className="num"><strong><Money value={r.net} currency={r.currency} signed /></strong></td></tr>)}</tbody>
    </table></div>}
    {tax.allocations_without_tax_split > 0 && <p className="muted">{t('books.tax.untracked', { count: tax.allocations_without_tax_split })}</p>}
    <FinBanner icon={<Info />}>{t('books.tax.disclaimer')}</FinBanner>
  </FinCard>;
}

/** Custom exports read the personal ledger only; in Business scope the page says so instead of exporting personal records. */
function CustomBusiness({ onPersonal }: { onPersonal: () => void }) {
  return <FinCard title={t('frep.custom.title')} icon={<FileSpreadsheet />}>
    <Empty icon={<FileSpreadsheet aria-hidden="true" />} title={t('frep.custom.personalOnly')} text={t('frep.custom.personalOnlyText')}
      action={<div className="actions"><button type="button" onClick={onPersonal}>{t('frep.custom.toPersonal')}</button>
        <a className="finLinkBtn secondary" href="#business-books">{t('frep.openBooks')}</a></div>} />
  </FinCard>;
}

const CUSTOM_CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'food', 'room_rent', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'interest', 'cash', 'transfer', 'income', 'cashback', 'refunds', 'other',
];

/** A custom period and filter: how many records match, and the CSV of exactly those records. */
function Custom({ month, onOpen }: { month: string; onOpen: (tab: FinanceTab) => void }) {
  const range = monthRange(month);
  const [from, setFrom] = useState(range.from), [to, setTo] = useState(range.to);
  const [account, setAccount] = useState(''), [category, setCategory] = useState('');
  const [accounts, setAccounts] = useState<FinanceAccount[]>([]);
  const [count, setCount] = useState<Load<number>>({ kind: 'loading' });
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const formId = useId();
  useEffect(() => { void api.listAccounts().then(setAccounts).catch(() => setAccounts([])); }, []);
  const invalid = !from || !to || from > to;
  useEffect(() => {
    if (invalid) return;
    setCount({ kind: 'loading' });
    void api.listTransactions({ from, to, account_id: account || undefined, category: category || undefined, limit: 1, offset: 0 })
      .then((page) => setCount({ kind: 'ready', value: page.total })).catch((e) => setCount(failed(e)));
  }, [from, to, account, category, invalid]);
  const download = async () => {
    setBusy(true); setError(null);
    try { await api.downloadTransactions({ from, to, account_id: account || undefined, category: category || undefined }); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <FinCard title={t('frep.custom.title')} icon={<FileSpreadsheet />} id={formId}>
    <p className="muted">{t('frep.custom.help')}</p>
    <div className="cmFilters finFilters">
      <label className="cmInline"><span>{t('frep.custom.from')}</span><input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} dir="ltr" /></label>
      <label className="cmInline"><span>{t('frep.custom.to')}</span><input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} dir="ltr" /></label>
      <label className="cmInline"><span>{t('frep.custom.account')}</span><select value={account} onChange={(e) => setAccount(e.target.value)}>
        <option value="">{t('frep.custom.allAccounts')}</option>{accounts.filter((a) => !a.archived).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="cmInline"><span>{t('frep.col.category')}</span><select value={category} onChange={(e) => setCategory(e.target.value)}>
        <option value="">{t('frep.custom.allCategories')}</option>{CUSTOM_CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
    </div>
    {invalid ? <p className="cmFieldError" role="alert">{t('frep.custom.invalid')}</p>
      : count.kind === 'loading' ? <Notice busy>{t('frep.loading')}</Notice>
      : count.kind === 'ready' ? <p className="finCustomCount" role="status"><strong>{t('frep.custom.count', { count: count.value })}</strong></p>
      : <Notice error>{t('frep.error', { message: count.message })}</Notice>}
    <div className="actions">
      <button type="button" disabled={invalid || busy || (count.kind === 'ready' && count.value === 0)} onClick={() => void download()}><Download aria-hidden="true" />{busy ? t('frep.downloading') : t('frep.downloadCsv')}</button>
      <button type="button" className="secondary" onClick={() => onOpen('transactions')}>{t('frep.openTransactions')}</button>
    </div>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
    {count.kind === 'ready' && count.value === 0 && <StatusChip tone="muted">{t('frep.custom.noneMatch')}</StatusChip>}
  </FinCard>;
}
