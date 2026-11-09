// Finance overview (docs/finance.md#finance-overview): personal and business money on one page, with the scope switch
// (All finance, Personal, Business). Net worth, the business month (income, expenses, the net result against the month
// before), accounts, bills due and what waits for review, income against outflow over six months with the month's P&L,
// where the money went, recent activity, safe to spend and the coming bills, balances and goals. Personal figures are
// the person's own ledger (/api/v1/finance); business figures are the workspace books (/api/v1/business/books), shown
// only to people who may read them. Read only: nothing here pays, transfers or trades.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, Briefcase, CalendarClock, ClipboardCheck, ExternalLink, FileUp, Landmark, Layers, Lock, PiggyBank, Plus, Settings2, TrendingUp, User, Wallet } from 'lucide-react';
import type { BooksBill, BooksReport, FinanceAccount, FinanceSummary, FinanceTransaction } from '@anum/contracts';
import * as api from './lib/financeApi';
import * as books from './lib/businessBooksApi';
import { categoryLabel, dueLabel, formatAmount, isolate, monthLabel, shiftMonth, spendCurrencies, type FinanceTab } from './lib/finance';
import { delta, monthRange, monthsEnding, type Delta, type FinanceScope } from './lib/financeNav';
import { businessTotals, netWorth, personalFlows, percentOf, type MonthFlow } from './lib/financeStatements';
import { expenseBreakdown, breakdownTotal, previousMonthName, trendFor } from './lib/ops';
import { lineLabel, paymentStateLabel } from './lib/businessBooks';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { SafeToSpendCard } from './FinancePlan';
import { FinBanner, FinCard, FinancePageHead, FinanceSectionNav, KpiCard, KpiRow, PeriodPicker, ScopeSwitch, StatusChip } from './FinanceShell';
import { ShareBars, TrendLines } from './OpsParts';

const day = (value: string) => formatDateTime(`${value.slice(0, 10)}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

interface Business { current: BooksReport; bills: BooksBill[] }

export function FinanceOverviewPage({ summary, accounts, month, onMonth, onOpen, onSettings, onImport, onAddAccount, onRetry, accountsError }: {
  summary: Load<FinanceSummary>; accounts: Load<FinanceAccount[]>; month: string | null; onMonth: (month: string) => void; onOpen: (tab: FinanceTab) => void;
  onSettings: () => void; onImport: () => void; onAddAccount: () => void; onRetry: () => void; accountsError: ReactNode;
}) {
  const [scope, setScope] = useState<FinanceScope>('all');
  const [currency, setCurrency] = useState<string | null>(null);
  const [business, setBusiness] = useState<Load<Business>>({ kind: 'loading' });
  const [previous, setPrevious] = useState<FinanceSummary | null>(null);
  const [history, setHistory] = useState<Load<FinanceSummary[]>>({ kind: 'loading' });
  const [recent, setRecent] = useState<Load<FinanceTransaction[]>>({ kind: 'loading' });
  const [review, setReview] = useState<Load<{ awaiting: number; first: string | null }>>({ kind: 'loading' });
  const s = summary.kind === 'ready' ? summary.value : null;
  const shownMonth = month ?? s?.month ?? null;
  const run = useRef(0);

  const loadBusiness = useCallback(async () => {
    if (!shownMonth) return;
    const mine = ++run.current;
    setBusiness({ kind: 'loading' });
    try {
      const [current, bills] = await Promise.all([books.getReport(monthRange(shownMonth)), books.listBills('', '', 0, 5).then((p) => p.items).catch(() => [])]);
      if (mine === run.current) setBusiness({ kind: 'ready', value: { current, bills } });
    } catch (e) { if (mine === run.current) setBusiness(failed(e)); }
  }, [shownMonth]);
  const loadPersonal = useCallback(async () => {
    if (!shownMonth) return;
    setRecent({ kind: 'loading' });
    setReview({ kind: 'loading' });
    const range = monthRange(shownMonth);
    const [before, tx, queue] = await Promise.allSettled([api.getSummary(shiftMonth(shownMonth, -1)), api.listTransactions({ from: range.from, to: range.to, limit: 5, offset: 0 }),
      api.guardianReview({ month: shownMonth, state: 'awaiting', limit: 1 })]);
    setPrevious(before.status === 'fulfilled' ? before.value : null);
    setRecent(tx.status === 'fulfilled' ? { kind: 'ready', value: tx.value.items } : failed(tx.reason));
    setReview(queue.status === 'fulfilled' ? { kind: 'ready', value: { awaiting: queue.value.counts.awaiting, first: queue.value.items[0]?.description ?? null } } : failed(queue.reason));
  }, [shownMonth]);
  useEffect(() => { void loadBusiness(); }, [loadBusiness]);
  useEffect(() => { void loadPersonal(); }, [loadPersonal]);
  const businessDenied = business.kind === 'denied';
  // Without access to the books, All finance and Business have nothing business to show: the page reads Personal.
  const shownScope: FinanceScope = businessDenied ? 'personal' : scope;
  // Six months of the person's own ledger, read only when the Personal chart is shown: chosen,
  // or because the books are refused (the effective scope, so a 403 never leaves the chart loading).
  useEffect(() => {
    if (shownScope !== 'personal' || !shownMonth || !s) return;
    let live = true;
    setHistory({ kind: 'loading' });
    Promise.all(monthsEnding(shownMonth, 6).slice(0, -1).map((m) => api.getSummary(m)))
      .then((rows) => { if (live) setHistory({ kind: 'ready', value: [...rows, s] }); })
      .catch((e) => { if (live) setHistory(failed(e)); });
    return () => { live = false; };
  }, [shownScope, shownMonth, s]);

  const currencies = s ? spendCurrencies(s, s.base_currency) : [];
  const cur = currency && currencies.includes(currency) ? currency : currencies[0] ?? s?.base_currency ?? 'SAR';
  const head = <FinancePageHead title={t('fov.title')} subtitle={t('fov.subtitle')}
    actions={s ? <>
      <button type="button" className="secondary" onClick={onSettings}><Settings2 aria-hidden="true" />{t('finance.settings.open')}</button>
      <button type="button" onClick={onImport}><FileUp aria-hidden="true" />{t('finance.import.open')}</button>
    </> : undefined}
    controls={s && shownMonth ? <>
      <ScopeSwitch value={shownScope} onChange={setScope} options={['all', 'personal', 'business']} unavailable={businessDenied ? { all: t('fov.businessDenied'), business: t('fov.businessDenied') } : undefined} />
      <PeriodPicker month={shownMonth} onChange={onMonth} max={s.today.slice(0, 7)} />
      {shownScope !== 'business' && currencies.length > 1 && <label className="finControl"><span className="finControlLabel">{t('finance.overview.currency')}</span>
        <select value={cur} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
    </> : undefined} />;

  const nav = <FinanceSectionNav tab="overview" onPick={onOpen} />;
  if (summary.kind === 'loading') return <div className="cmLayout finLayout finOwnHead opsPage">{head}{nav}<Notice busy>{t('finance.loading')}</Notice></div>;
  if (summary.kind === 'denied' || accounts.kind === 'denied') return <div className="cmLayout finLayout finOwnHead opsPage">{head}{nav}<FinDenied /></div>;
  if (summary.kind === 'error') return <div className="cmLayout finLayout finOwnHead opsPage">{head}{nav}
    <LoadError message={t('finance.error.load', { message: summary.message })} retry={onRetry} /></div>;
  if (!s || !shownMonth) return null;
  const accountList = accounts.kind === 'ready' ? accounts.value.filter((a) => !a.archived) : [];
  const noAccounts = accounts.kind === 'ready' && accountList.length === 0;
  const b = business.kind === 'ready' ? business.value : null;
  const totals = b ? businessTotals(b.current) : null;
  const trendBefore = b ? b.current.trend.find((row) => row.month.slice(0, 7) === shiftMonth(shownMonth, -1)) ?? null : null;
  const vs = t('ops.vsMonth', { month: previousMonthName(shownMonth) });
  const words = (d: Delta | null) => (d ? `${d.percent === null ? t(`fshell.delta.${d.direction}` as MessageKey) : t(`fshell.delta.pct.${d.direction}` as MessageKey, { percent: Math.abs(d.percent) })} ${vs}` : undefined);
  const worth = netWorth(s).find((r) => r.currency === cur) ?? netWorth(s)[0] ?? null;
  const amountOf = (rows: readonly { currency: string; amount: string }[] | undefined, c: string) => rows?.find((r) => r.currency === c)?.amount ?? '0';
  const businessUnavailable = business.kind === 'loading' ? t('fov.loadingBusiness') : business.kind === 'error' ? t('fov.businessError') : t('fov.businessDenied');
  const bizCard = (key: 'income' | 'expenses' | 'net', icon: ReactNode) => {
    if (!totals || !b) return <KpiCard key={key} icon={icon} label={t(`fov.kpi.${key}` as MessageKey)} value="-" hint={businessUnavailable} />;
    const value = key === 'income' ? totals.income : key === 'expenses' ? totals.expenses : totals.net;
    const before = trendBefore ? (key === 'income' ? trendBefore.revenue : key === 'expenses' ? trendBefore.costs : trendBefore.profit) : null;
    const d = before === null ? null : delta(value, before, key !== 'expenses');
    return <KpiCard key={key} icon={icon} label={t(`fov.kpi.${key}` as MessageKey)} value={<bdi>{formatAmount(value, totals.currency)}</bdi>} delta={d} deltaText={words(d)}
      tone={key === 'net' && value.startsWith('-') ? 'bad' : ''} hint={t('fov.kpi.businessHint', { month: monthLabel(shownMonth) })} />;
  };
  const income = amountOf(s.income, cur), spent = amountOf(s.spend, cur), net = amountOf(s.net, cur);
  const cashback = amountOf(s.cashback ?? [], cur), refunds = amountOf(s.refunds ?? [], cur);
  const dIncome = previous ? delta(income, amountOf(previous.income, cur)) : null;
  const dSpent = previous ? delta(spent, amountOf(previous.spend, cur), false) : null;
  const dNet = previous ? delta(net, amountOf(previous.net, cur)) : null;
  const worthCard = <KpiCard key="worth" icon={<User />} label={t('fov.kpi.worth')} value={worth ? <bdi>{formatAmount(worth.net, worth.currency)}</bdi> : '-'} hint={t('fov.kpi.worthHint')} />;
  const kpis = shownScope === 'personal' ? [worthCard,
    <KpiCard key="in" icon={<TrendingUp />} label={t('finance.figure.income')} value={<bdi>{formatAmount(income, cur)}</bdi>} delta={dIncome} deltaText={words(dIncome)} />,
    <KpiCard key="out" icon={<Wallet />} label={t('finance.figure.spent')} value={<bdi>{formatAmount(spent, cur)}</bdi>} delta={dSpent} deltaText={words(dSpent)} />,
    <KpiCard key="net" icon={<Layers />} label={t('fov.kpi.personalNet')} value={<bdi>{formatAmount(net, cur)}</bdi>} delta={dNet} deltaText={words(dNet)} tone={net.startsWith('-') ? 'bad' : ''} />,
    <KpiCard key="cashback" icon={<PiggyBank />} label={t('finance.category.cashback')} value={<bdi>{formatAmount(cashback, cur)}</bdi>} hint={t('fov.kpi.cashbackHint')} />,
    <KpiCard key="refunds" icon={<Wallet />} label={t('finance.category.refunds')} value={<bdi>{formatAmount(refunds, cur)}</bdi>} hint={t('fov.kpi.refundsHint')} />]
    : shownScope === 'business' ? [bizCard('income', <Briefcase />), bizCard('expenses', <Layers />), bizCard('net', <TrendingUp />),
      <KpiCard key="cash" icon={<Landmark />} label={t('books.dash.cash')} value={b ? <bdi>{formatAmount(amountOf(b.current.cash_flow.net, b.current.base_currency), b.current.base_currency)}</bdi> : '-'}
        hint={b ? t('books.fig.cashHint') : businessUnavailable} />]
    : [worthCard, bizCard('income', <Briefcase />), bizCard('expenses', <Layers />), bizCard('net', <TrendingUp />)];
  const reviewCount = (review.kind === 'ready' ? review.value.awaiting : 0) + (b ? b.current.allocations_in_review : 0);
  const nextBill = s.upcoming_bills[0] ?? null;
  return <div className="cmLayout finLayout finOwnHead opsPage">
    {head}
    {nav}
    {noAccounts && <section className="surface cmPanel cmWelcome" aria-labelledby="fin-welcome">
      <Wallet aria-hidden="true" />
      <div><h2 id="fin-welcome">{t('finance.welcome.title')}</h2><p className="muted">{t('finance.welcome.text')}</p>
        <div className="actions"><button type="button" onClick={onAddAccount}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button></div></div>
    </section>}
    {accountsError}
    <KpiRow label={t('fov.kpis')}>{kpis}</KpiRow>
    <ul className="fovLinks" aria-label={t('fov.links')}>
      <li><button type="button" className="fovLink" onClick={() => onOpen('accounts')}><span className="fovLinkIcon" aria-hidden="true"><Landmark /></span>
        <span><strong>{t('fov.link.accounts', { count: accountList.length })}</strong><small dir="auto">{accountList.length ? accountList.slice(0, 3).map((a) => a.name).join(', ') : t('finance.accounts.none')}</small></span>
        <ArrowRight className="cmFlip" aria-hidden="true" /></button></li>
      <li><button type="button" className="fovLink" onClick={() => onOpen('bills')}><span className="fovLinkIcon" aria-hidden="true"><CalendarClock /></span>
        <span><strong>{t('fov.link.bills', { count: s.upcoming_bills.length })}</strong>
          <small>{nextBill ? t('fov.link.billsNext', { total: s.bills_due.map((r) => formatAmount(r.amount, r.currency)).join(' · '), date: day(nextBill.due_on) }) : t('finance.bills.noneDue')}</small></span>
        <ArrowRight className="cmFlip" aria-hidden="true" /></button></li>
      <li><button type="button" className="fovLink" onClick={() => onOpen('guardian')}><span className="fovLinkIcon" aria-hidden="true"><ClipboardCheck /></span>
        <span><strong>{review.kind === 'loading' ? t('fov.link.reviewLoading') : t('fov.link.review', { count: reviewCount })}</strong>
          <small>{review.kind === 'denied' || review.kind === 'error' ? t('fov.link.reviewUnavailable') : t('fov.link.reviewHint')}</small></span>
        <ArrowRight className="cmFlip" aria-hidden="true" /></button></li>
    </ul>
    <div className="fovRow">
      <FlowCard scope={shownScope} business={business} history={history} summary={s} currency={cur} month={shownMonth} onRetry={() => void loadBusiness()} />
      <SpendingCard scope={shownScope} business={business} summary={s} currency={cur} onOpen={onOpen} onRetry={() => void loadBusiness()} />
    </div>
    <div className="fovRow">
      <ActivityCard scope={shownScope} recent={recent} business={business} accounts={accountList} onOpen={onOpen} onRetry={() => void loadPersonal()} />
      <div className="fovColumn">
        <SafeToSpendCard base={s.base_currency} onOpen={onOpen} />
        <FinCard title={t('finance.overview.upcoming')} icon={<CalendarClock />} actions={<button type="button" className="cmLinkButton" onClick={() => onOpen('bills')}>{t('finance.overview.allBills')}</button>}>
          {s.upcoming_bills.length === 0 ? <p className="muted">{t('finance.bills.noneDue')}</p>
            : <ul className="cmList">{s.upcoming_bills.slice(0, 5).map((row) => <li key={`${row.id}-${row.due_on}`}>
              <div><strong dir="auto">{row.name}</strong><small><bdi dir="ltr">{row.due_on}</bdi> · {dueLabel(row.days_until)} · {categoryLabel(row.category)}</small></div>
              <Money value={row.amount} currency={row.currency} /></li>)}</ul>}
          {review.kind === 'ready' && review.value.awaiting > 0 && <FinBanner tone="warn" title={t('fov.review.title', { count: review.value.awaiting })}
            action={<button type="button" className="secondary" onClick={() => onOpen('guardian')}>{t('fov.review.open')}</button>}>{review.value.first ?? ''}</FinBanner>}
          <p className="muted fovNote"><Lock aria-hidden="true" />{t('finance.readOnly')}</p>
        </FinCard>
      </div>
    </div>
    <div className="fovRow">
      <FinCard title={t('finance.overview.accounts')} icon={<Landmark />} actions={<button type="button" className="cmLinkButton" onClick={() => onOpen('accounts')}>{t('finance.overview.manage')}</button>}>
        {s.accounts.length === 0 ? <p className="muted">{t('finance.accounts.none')}</p> : <ul className="cmList">{s.accounts.filter((a) => !a.archived).map((a) => <li key={a.id}>
          <div><strong dir="auto">{a.name}</strong><small>{t(`finance.kind.${a.kind}` as MessageKey)}{a.last4 ? ` · ${t('finance.accounts.endingIn', { last4: a.last4 })}` : ''}</small></div>
          <Money value={a.balance} currency={a.currency} signed /></li>)}</ul>}
      </FinCard>
      {s.goals.length > 0 ? <FinCard title={t('finance.tab.goals')} icon={<PiggyBank />} actions={<button type="button" className="cmLinkButton" onClick={() => onOpen('goals')}>{t('finance.overview.manage')}</button>}>
        <ul className="cmList">{s.goals.map((g) => <li key={g.id}><div><strong dir="auto">{g.name}</strong><small>{t('finance.goals.progress', { saved: isolate(formatAmount(g.saved_amount, g.currency)), target: isolate(formatAmount(g.target_amount, g.currency)) })}</small></div>
          <span className="finProgress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={g.percent} aria-label={t('finance.goals.progressLabel', { name: g.name, percent: g.percent })}>
            <span className="finTrack"><span className="finFill" style={{ width: `${g.percent}%` }} /></span><small aria-hidden="true">{g.percent}%</small></span></li>)}</ul>
      </FinCard> : <FinCard title={t('finance.tab.goals')} icon={<PiggyBank />}><p className="muted">{t('fov.goalsNone')}</p>
        <button type="button" className="secondary" onClick={() => onOpen('goals')}><Plus aria-hidden="true" />{t('fov.goalsAdd')}</button></FinCard>}
    </div>
  </div>;
}

function FinDenied() {
  return <section className="surface cmPanel cmDenied" aria-labelledby="fin-denied"><Lock aria-hidden="true" /><div><h2 id="fin-denied">{t('finance.denied.title')}</h2><p className="muted">{t('finance.denied.text')}</p></div></section>;
}

/** Income against outflow over six months: the business books (All finance, Business) or the person's ledger (Personal), with the month's P&L beside it. */
function FlowCard({ scope, business, history, summary, currency, month, onRetry }: {
  scope: FinanceScope; business: Load<Business>; history: Load<FinanceSummary[]>; summary: FinanceSummary; currency: string; month: string; onRetry: () => void;
}) {
  const personal = scope === 'personal';
  const flows: MonthFlow[] | null = useMemo(() => (personal ? (history.kind === 'ready' ? personalFlows(history.value, currency) : null)
    : business.kind === 'ready' ? trendFor(business.value.current, monthsEnding(month, 6)) : null), [personal, history, business, currency, month]);
  const unit = personal ? currency : business.kind === 'ready' ? business.value.current.base_currency : currency;
  const source = personal ? history : business;
  const now = personal ? { income: summary.income.find((r) => r.currency === currency)?.amount ?? '0', expenses: summary.spend.find((r) => r.currency === currency)?.amount ?? '0',
    net: summary.net.find((r) => r.currency === currency)?.amount ?? '0' } : business.kind === 'ready' ? (() => { const x = businessTotals(business.value.current); return { income: x.income, expenses: x.expenses, net: x.net }; })() : null;
  return <FinCard title={t('fov.flow.title')} icon={<TrendingUp />} subtitle={personal ? t('fov.flow.personalSub') : t('fov.flow.businessSub')} className="fovWide">
    {source.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
      : source.kind === 'denied' || source.kind === 'error' ? <LoadError message={t('fov.flow.error', { message: source.message })} retry={onRetry} />
      : !flows || flows.every((f) => Number(f.income) === 0 && Number(f.expenses) === 0) ? <p className="muted">{t('finance.overview.noFlow')}</p>
      : <div className="fovFlow">
        <TrendLines rows={flows} currency={unit} title={t('fov.flow.title')} incomeLabel={t('fov.flow.income')} expenseLabel={t('fov.flow.outflow')} />
        {now && <div className="fovPnl"><p className="fovPnlHead">{t('fov.flow.pnl', { month: monthLabel(month) })}</p><dl>
          <div><dt>{t('fov.flow.income')}</dt><dd className="in"><bdi>{formatAmount(now.income, unit)}</bdi></dd></div>
          <div><dt>{t('fov.flow.outflow')}</dt><dd className="out"><bdi>{formatAmount(now.expenses, unit)}</bdi></dd></div>
          <div className="total"><dt>{t('fov.flow.net')}</dt><dd><bdi>{formatAmount(now.net, unit)}</bdi></dd></div>
          {percentOf(now.net, now.income) !== null && <div><dt>{t('fov.flow.margin')}</dt><dd>{formatNumber(percentOf(now.net, now.income)!)}%</dd></div>}
        </dl></div>}
      </div>}
  </FinCard>;
}

/** Where the money went this month: the books' expense lines (All finance, Business) or the person's spending by category. */
function SpendingCard({ scope, business, summary, currency, onOpen, onRetry }: {
  scope: FinanceScope; business: Load<Business>; summary: FinanceSummary; currency: string; onOpen: (tab: FinanceTab) => void; onRetry: () => void;
}) {
  if (scope === 'personal') {
    const rows = summary.spend_by_category.filter((r) => r.currency === currency).sort((a, b) => Number(b.amount) - Number(a.amount))
      .map((r) => ({ key: r.category, label: categoryLabel(r.category), amount: r.amount }));
    const total = summary.spend.find((r) => r.currency === currency)?.amount ?? '0';
    return <FinCard title={t('fov.spend.personal')} icon={<Wallet />} subtitle={t('fov.spend.personalSub', { month: monthLabel(summary.month), currency })}>
      {rows.length === 0 ? <Empty icon={<PiggyBank aria-hidden="true" />} title={t('finance.chart.empty')} text={t('finance.chart.emptyText')}
        action={<button type="button" className="secondary" onClick={() => onOpen('transactions')}>{t('finance.chart.openTransactions')}</button>} />
        : <><ShareBars rows={rows.slice(0, 6)} currency={currency} label={t('fov.spend.personal')} total={Number(total)} />
          <p className="fovTotal"><span>{t('fov.spend.total')}</span><strong><bdi>{formatAmount(total, currency)}</bdi></strong></p></>}
    </FinCard>;
  }
  const report = business.kind === 'ready' ? business.value.current : null;
  const rows = report ? expenseBreakdown(report) : [];
  return <FinCard title={t('fov.spend.business')} icon={<Layers />} subtitle={t('fov.spend.businessSub')}>
    {business.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
      : business.kind === 'denied' || business.kind === 'error' ? <LoadError message={t('fov.flow.error', { message: business.message })} retry={onRetry} />
      : rows.length === 0 ? <p className="muted">{t('books.dash.breakdownNone')}</p>
      : <><ShareBars rows={rows} currency={report!.base_currency} label={t('fov.spend.business')} />
        <p className="fovTotal"><span>{t('fov.spend.total')}</span><strong><bdi>{formatAmount(breakdownTotal(rows, report!.base_currency), report!.base_currency)}</bdi></strong></p></>}
    <a className="finLinkBtn fovOpenBooks" href="#business-books"><ExternalLink aria-hidden="true" />{t('fov.spend.openBooks')}</a>
  </FinCard>;
}

/** The newest movements: the person's transactions (All finance, Personal) or the business bills (Business). */
function ActivityCard({ scope, recent, business, accounts, onOpen, onRetry }: {
  scope: FinanceScope; recent: Load<FinanceTransaction[]>; business: Load<Business>; accounts: FinanceAccount[]; onOpen: (tab: FinanceTab) => void; onRetry: () => void;
}) {
  const accountName = (id: string) => accounts.find((a) => a.id === id)?.name ?? '-';
  if (scope === 'business') {
    const bills = business.kind === 'ready' ? business.value.bills : [];
    return <FinCard title={t('fov.activity.business')} icon={<Briefcase />} className="fovWide"
      actions={<a className="cmLinkButton" href="#business-books/bills">{t('fov.activity.allBills')}</a>}>
      {business.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
        : bills.length === 0 ? <p className="muted">{t('books.bills.emptyTitle')}</p>
        : <div className="finTableWrap" tabIndex={0}><table className="finGrid"><caption className="visuallyHidden">{t('fov.activity.business')}</caption>
          <thead><tr><th scope="col">{t('books.dash.col.date')}</th><th scope="col">{t('books.dash.col.description')}</th><th scope="col">{t('books.dash.col.category')}</th>
            <th scope="col" className="num">{t('books.dash.col.amount')}</th><th scope="col">{t('books.dash.col.status')}</th></tr></thead>
          <tbody>{bills.map((bill) => <tr key={bill.id}><td className="nowrap"><bdi>{day(bill.bill_date)}</bdi></td><th scope="row"><span dir="auto">{bill.vendor_name}</span><small dir="auto">{bill.description}</small></th>
            <td>{bill.category ? lineLabel(bill.category) : '-'}</td><td className="num"><bdi>{formatAmount(bill.total, bill.currency)}</bdi></td>
            <td><StatusChip tone={bill.payment_state === 'paid' ? 'ok' : bill.overdue ? 'bad' : 'warn'}>{paymentStateLabel(bill.payment_state)}</StatusChip></td></tr>)}</tbody></table></div>}
    </FinCard>;
  }
  return <FinCard title={t('fov.activity.title')} icon={<Layers />} subtitle={t('fov.activity.sub')} className="fovWide"
    actions={<button type="button" className="secondary" onClick={() => onOpen('transactions')}>{t('fov.activity.all')}<ArrowRight className="cmFlip" aria-hidden="true" /></button>}>
    {recent.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
      : recent.kind === 'denied' || recent.kind === 'error' ? <LoadError message={t('fov.activity.error', { message: recent.message })} retry={onRetry} />
      : recent.value.length === 0 ? <p className="muted">{t('fov.activity.none')}</p>
      : <div className="finTableWrap" tabIndex={0}><table className="finGrid"><caption className="visuallyHidden">{t('fov.activity.title')}</caption>
        <thead><tr><th scope="col">{t('books.dash.col.date')}</th><th scope="col">{t('books.dash.col.description')}</th><th scope="col">{t('books.dash.col.category')}</th>
          <th scope="col">{t('fov.activity.account')}</th><th scope="col" className="num">{t('books.dash.col.amount')}</th></tr></thead>
        <tbody>{recent.value.map((tx) => <tr key={tx.id}><td className="nowrap"><bdi>{day(tx.posted_on)}</bdi></td><th scope="row"><span dir="auto">{tx.description || '-'}</span></th>
          <td>{categoryLabel(tx.category)}</td><td dir="auto">{accountName(tx.account_id)}</td><td className="num"><Money value={tx.amount} currency={tx.currency} signed /></td></tr>)}</tbody></table></div>}
  </FinCard>;
}
