// Safe to spend (docs/finance.md#safe-to-spend), from the owner's mockup of 5 Nov 2026. The figure and
// its build come from GET /finance/safe-to-spend; the runway from that figure, your bills
// (GET /finance/bills/upcoming) and your average daily spending (two month summaries); the guardrails
// from your Guardian category limits (GET /finance/guardian) against this month's spending. Business
// budgets come from the business books. An estimate from your own records: nothing here moves money.
import { Suspense, lazy, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  CalendarClock, CircleCheck, Gauge, Info, Landmark, ListChecks, Pencil, RefreshCw, Scale, Settings2, ShieldCheck, Target, TriangleAlert, Wallet,
} from 'lucide-react';
import type { BooksReport, FinanceGuardian, FinanceGuardianAlerts, FinanceSafeToSpend, FinanceSummary, FinanceTransaction, FinanceUpcomingBill } from '@anum/contracts';
import * as api from './lib/financeApi';
import * as books from './lib/businessBooksApi';
import { categoryLabel, describeRules, formatAmount, isolate, monthLabel, shiftMonth, type FinanceTab } from './lib/finance';
import { monthRange, type FinanceScope } from './lib/financeNav';
import { add, dailyPace, guardrails, runway } from './lib/financeStatements';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, ScopeSwitch, StatusChip, tabPanel } from './FinanceShell';
import { PlanDialog, SafeBody } from './FinancePlan';
import './financePages.css';

const RunwayChart = lazy(() => import('./FinanceCharts').then((m) => ({ default: m.RunwayChart })));

type SafeTab = 'now' | 'rules';
/** Records older than this many days make the estimate "may be stale". */
export const STALE_DAYS = 3;

function useLoad<T>(load: (() => Promise<T>) | null): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(() => {
    if (!load) return;
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    load().then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [load]);
  useEffect(() => { run(); }, [run]);
  return [state, run];
}

const daysBetween = (a: string, b: string) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);

export function FinanceSafePage({ onOpen, onChanged }: { onOpen: (tab: FinanceTab) => void; onChanged: () => void }) {
  const [scope, setScope] = useState<FinanceScope>('personal');
  const [tab, setTab] = useState<SafeTab>('now');
  const [settings, setSettings] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [safe, reloadSafe] = useLoad(api.getSafeToSpend);
  const month = safe.kind === 'ready' ? safe.value.month : null;
  const loadSummaries = useMemo(() => (month ? () => Promise.all([api.getSummary(month), api.getSummary(shiftMonth(month, -1))]) : null), [month]);
  const [summaries, reloadSummaries] = useLoad(loadSummaries);
  const loadBills = useCallback(() => api.upcomingBills(45), []);
  const [bills, reloadBills] = useLoad(loadBills);
  const [guardian, reloadGuardian] = useLoad(api.getGuardian);
  const loadAlerts = useCallback(() => api.guardianAlerts(), []);
  const [alerts] = useLoad(loadAlerts);
  const loadLatest = useCallback(() => api.listTransactions({ limit: 1, offset: 0 }), []);
  const [latest, reloadLatest] = useLoad(loadLatest);
  const loadBooks = useMemo(() => (month && scope !== 'personal' ? () => books.getReport(monthRange(month)) : null), [month, scope]);
  const [report, reloadReport] = useLoad(loadBooks);
  const refresh = () => { reloadSafe(); reloadSummaries(); reloadBills(); reloadGuardian(); reloadLatest(); reloadReport(); onChanged(); };

  const data = safe.kind === 'ready' ? safe.value : null;
  const currencies = data?.currencies.map((c) => c.currency) ?? [];
  const shown = currency && currencies.includes(currency) ? currency : (data && currencies.includes(data.base_currency) ? data.base_currency : currencies[0]) ?? null;

  const head = <FinancePageHead title={t('finance.tab.safe')} subtitle={t('fsafe.subtitle')}
    actions={<>
      <button type="button" className="secondary" onClick={refresh}><RefreshCw aria-hidden="true" />{t('fsafe.refresh')}</button>
      <button type="button" onClick={() => setSettings(true)}><Settings2 aria-hidden="true" />{t('finance.safe.settings')}</button>
    </>}
    controls={<>
      <ScopeSwitch value={scope} onChange={setScope} />
      {data && <div className="finControl"><span className="finControlLabel">{t('fshell.period')}</span>
        <strong className="finStaticPeriod">{t('fsafe.period', { month: monthLabel(data.month), end: isolate(formatDateTime(`${data.month_end}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' })) })}</strong></div>}
      {currencies.length > 1 && <label className="finControl"><span className="finControlLabel">{t('frep.currency')}</span>
        <select value={shown ?? ''} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
    </>}
    tabs={<FinanceTabs label={t('fsafe.tabs')} idPrefix="fsafe" value={tab} onChange={setTab} items={[{ id: 'now', label: t('fsafe.tab.now') }, { id: 'rules', label: t('fsafe.tab.rules') }]} />} />;

  let body: ReactNode;
  if (safe.kind === 'loading') body = <Notice busy>{t('finance.safe.loading')}</Notice>;
  else if (safe.kind === 'denied') body = <Denied />;
  else if (safe.kind === 'error') body = <LoadError message={t('finance.safe.error', { message: safe.message })} retry={reloadSafe} />;
  else if (data && tab === 'rules') body = <RulesTab guardian={guardian} alerts={alerts} onOpen={onOpen} retry={reloadGuardian} />;
  else if (data && scope === 'business') body = <BusinessScope report={report} retry={reloadReport} />;
  else if (data && (data.currencies.length === 0 || !shown)) body = <FinCard title={t('finance.tab.safe')} icon={<Landmark />}>
    <Empty icon={<Landmark aria-hidden="true" />} title={t('finance.safe.empty')} text={t('finance.safe.emptyText')}
      action={<button type="button" onClick={() => onOpen('accounts')}>{t('finance.accounts.add')}</button>} />
  </FinCard>;
  else if (data && shown) body = <AvailableNow data={data} currency={shown} summaries={summaries} bills={bills} guardian={guardian} latest={latest} report={scope === 'all' ? report : null}
    onOpen={onOpen} onEdit={() => setSettings(true)} onRefresh={refresh} />;
  return <div className="finPage finSafePage">
    {head}
    <FinBanner tone="warn" icon={<Info />} title={t('fsafe.estimate.title')}>{t('fsafe.estimate.text')}</FinBanner>
    {notice && <Notice>{notice}</Notice>}
    <div {...tabPanel('fsafe', tab)}>{body}</div>
    <p className="muted finFoot finReadOnlyLine"><ShieldCheck aria-hidden="true" />{t('fsafe.footer')}</p>
    {settings && <PlanDialog onClose={(saved) => { setSettings(false); if (saved) { setNotice(t('finance.safe.saved')); refresh(); } }} />}
  </div>;
}

function AvailableNow({ data, currency, summaries, bills, guardian, latest, report, onOpen, onEdit, onRefresh }: {
  data: FinanceSafeToSpend; currency: string; summaries: Load<[FinanceSummary, FinanceSummary]>; bills: Load<FinanceUpcomingBill[]>; guardian: Load<FinanceGuardian>;
  latest: Load<{ items: FinanceTransaction[] }>; report: Load<BooksReport> | null; onOpen: (tab: FinanceTab) => void; onEdit: () => void; onRefresh: () => void;
}) {
  const row = data.currencies.find((c) => c.currency === currency)!;
  const committed = add(currency, row.bills, row.minimum_payments);
  const setAside = add(currency, row.goals, row.buffer);
  const dueBills = bills.kind === 'ready' ? bills.value.filter((b) => b.currency === currency && b.due_on <= data.month_end) : [];
  const pace = summaries.kind === 'ready' ? dailyPace(summaries.value[0].spend.find((s) => s.currency === currency)?.amount ?? '0', Number(data.today.slice(8, 10)),
    summaries.value[1].spend.find((s) => s.currency === currency)?.amount ?? '0', Number(monthRange(summaries.value[1].month).to.slice(8, 10)), currency) : null;
  const points = pace !== null ? runway(data, currency, dueBills, pace) : [];
  const newest = latest.kind === 'ready' ? latest.value.items[0]?.posted_on ?? null : null;
  const age = newest ? daysBetween(newest, data.today) : null;
  return <>
    <KpiRow label={t('fsafe.kpis')}>
      <KpiCard icon={<Wallet />} label={t('fsafe.kpi.balance')} value={<Money value={row.available} currency={currency} signed />} hint={t('fsafe.kpi.accounts', { count: data.accounts_counted })} />
      <KpiCard icon={<CalendarClock />} label={t('fsafe.kpi.bills')} value={<Money value={committed} currency={currency} />} hint={t('fsafe.kpi.billsHint')} tone={Number(committed) > 0 ? 'warn' : ''} />
      <KpiCard icon={<Target />} label={t('fsafe.kpi.goals')} value={<Money value={setAside} currency={currency} />} hint={Number(row.buffer) > 0 ? t('fsafe.kpi.withBuffer', { amount: isolate(formatAmount(row.buffer, currency)) }) : t('fsafe.kpi.goalsHint')} />
      <KpiCard icon={<ShieldCheck />} label={t('fsafe.kpi.safe')} value={<Money value={row.safe_to_spend} currency={currency} signed />} tone={row.short ? 'bad' : 'good'}
        hint={row.short ? t('finance.safe.shortHint') : t('finance.safe.perDay', { amount: isolate(formatAmount(row.per_day, currency)), count: data.days_left })} />
    </KpiRow>
    <section className="finCalc" aria-label={t('fsafe.calc.title')}>
      <strong className="finCalcTitle">{t('fsafe.calc.title')}</strong>
      <ol className="finCalcSteps">
        <li><span>{t('fsafe.calc.balance')}</span><Money value={row.available} currency={currency} signed /></li>
        <li className="op" aria-hidden="true">-</li>
        <li><span>{t('fsafe.calc.bills')}</span><Money value={committed} currency={currency} /></li>
        <li className="op" aria-hidden="true">-</li>
        <li><span>{t('fsafe.calc.goals')}</span><Money value={setAside} currency={currency} /></li>
        <li className="op" aria-hidden="true">=</li>
        <li className={`result ${row.short ? 'short' : ''}`}><span>{t('fsafe.calc.result')}</span><Money value={row.safe_to_spend} currency={currency} signed /></li>
      </ol>
      <p className="visuallyHidden">{t('fsafe.calc.sentence', { balance: formatAmount(row.available, currency), bills: formatAmount(committed, currency), goals: formatAmount(setAside, currency), result: formatAmount(row.safe_to_spend, currency) })}</p>
      <span className="finCalcMeta">{t('fsafe.kpi.accounts', { count: data.accounts_counted })}{data.excluded.length > 0 && ` · ${t('fsafe.review.excluded', { count: data.excluded.length })}`}</span>
    </section>
    <div className="finReportGrid finSafeGridTop">
      <FinCard title={t('fsafe.runway.title', { month: monthLabel(data.month) })} subtitle={t('fsafe.runway.subtitle')} icon={<Gauge />} className="finRunway">
        {summaries.kind === 'loading' || bills.kind === 'loading' ? <Notice busy>{t('finance.safe.loading')}</Notice>
          : summaries.kind !== 'ready' ? <LoadError message={t('fsafe.runway.error', { message: summaries.kind === 'error' || summaries.kind === 'denied' ? summaries.message : '' })} retry={onRefresh} />
          // Without the bills the projection would overstate the month end: say what failed instead of drawing it.
          : bills.kind !== 'ready' ? <LoadError message={t('fsafe.runway.billsError', { message: bills.kind === 'error' || bills.kind === 'denied' ? bills.message : '' })} retry={onRefresh} />
          : points.length < 2 ? <p className="muted">{t('fsafe.runway.lastDay')}</p>
          : <Suspense fallback={<Notice busy>{t('finance.safe.loading')}</Notice>}><RunwayChart points={points} currency={currency} perDay={pace ?? '0'} title={t('fsafe.runway.chartTitle', { currency })} /></Suspense>}
        {pace !== null && bills.kind === 'ready' && <div className="finAssumptions"><strong>{t('fsafe.assume.title')}</strong><ul>
          <li>{t('fsafe.assume.pace', { amount: isolate(formatAmount(pace, currency)) })}</li>
          <li>{t('fsafe.assume.band', { percent: 10 })}</li>
          <li>{t('fsafe.assume.bills', { count: dueBills.length })}</li>
          {points.length > 0 && <li>{t('fsafe.assume.end', { amount: isolate(formatAmount(points[points.length - 1].balance, currency)) })}</li>}
        </ul></div>}
      </FinCard>
      <FinCard title={t('fsafe.review.title')} icon={<ListChecks />} className="finReview">
        <ul className="finQualityList">
          <li className="ok"><CircleCheck aria-hidden="true" /><div><strong>{t('fsafe.kpi.accounts', { count: data.accounts_counted })}</strong></div></li>
          <li className="ok"><CircleCheck aria-hidden="true" /><div><strong>{t('fsafe.review.bills', { count: row.lines.filter((l) => l.kind === 'bill').length })}</strong></div></li>
          <li className="ok"><CircleCheck aria-hidden="true" /><div><strong>{t('fsafe.review.goals', { count: row.lines.filter((l) => l.kind === 'goal').length })}</strong></div></li>
          <li className={data.excluded.length ? 'warn' : 'ok'}>{data.excluded.length ? <TriangleAlert aria-hidden="true" /> : <CircleCheck aria-hidden="true" />}<div><strong>{t('fsafe.review.excluded', { count: data.excluded.length })}</strong></div></li>
          <li className="ok"><CircleCheck aria-hidden="true" /><div><strong>{newest ? t('fsafe.review.latest', { date: isolate(newest) }) : t('fsafe.review.noRecords')}</strong></div></li>
        </ul>
        <div className="finCardButtons">
          <button type="button" className="secondary" onClick={onEdit}><Pencil aria-hidden="true" />{t('fsafe.review.edit')}</button>
          <button type="button" className="secondary" onClick={onRefresh}><RefreshCw aria-hidden="true" />{t('fsafe.refresh')}</button>
        </div>
      </FinCard>
      <FinCard title={t('fsafe.upcoming.title')} icon={<CalendarClock />} className="finUpcoming"
        actions={<button type="button" className="cmLinkButton" onClick={() => onOpen('bills')}>{t('finance.overview.allBills')}</button>}>
        {bills.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
          : bills.kind !== 'ready' ? <LoadError message={bills.message} retry={onRefresh} />
          : dueBills.length === 0 ? <Empty icon={<CalendarClock aria-hidden="true" />} title={t('fsafe.upcoming.none')} text={t('fsafe.upcoming.noneText')}
            action={<button type="button" className="secondary" onClick={() => onOpen('bills')}>{t('fsafe.upcoming.add')}</button>} />
          : <div className="finTableWrap" tabIndex={0}><table className="finGrid"><caption className="visuallyHidden">{t('fsafe.upcoming.title')}</caption>
            <thead><tr><th scope="col">{t('fsafe.col.date')}</th><th scope="col">{t('fsafe.col.commitment')}</th><th scope="col" className="num">{t('fsafe.col.amount')}</th></tr></thead>
            <tbody>{dueBills.map((b) => <tr key={`${b.id}-${b.due_on}`}><td className="finNoWrap"><bdi>{formatDateTime(`${b.due_on}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' })}</bdi></td>
              <th scope="row" dir="auto">{b.name}<small className="muted finSub">{categoryLabel(b.category)}</small></th><td className="num"><Money value={b.amount} currency={b.currency} /></td></tr>)}</tbody>
          </table></div>}
      </FinCard>
    </div>
    <div className="finReportGrid finSafeGridBottom">
      <Guardrails guardian={guardian} summaries={summaries} currency={currency} month={data.month} onOpen={onOpen} />
      <Freshness age={age} newest={newest} onOpen={onOpen} onRefresh={onRefresh} />
      <RulesPreview guardian={guardian} onOpen={onOpen} />
    </div>
    {report && <BusinessScope report={report} retry={onRefresh} />}
    <FinCard title={t('finance.safe.howBuilt')} icon={<Scale />}><SafeBody data={data} onOpen={onOpen} /></FinCard>
  </>;
}

function Guardrails({ guardian, summaries, currency, month, onOpen }: {
  guardian: Load<FinanceGuardian>; summaries: Load<[FinanceSummary, FinanceSummary]>; currency: string; month: string; onOpen: (tab: FinanceTab) => void;
}) {
  const rows = guardian.kind === 'ready' && summaries.kind === 'ready' ? guardrails(guardian.value.rules, summaries.value[0], currency) : [];
  return <FinCard title={t('fsafe.guard.title')} subtitle={t('fsafe.guard.subtitle', { month: monthLabel(month) })} icon={<Target />} className="finGuardrails">
    {guardian.kind === 'loading' || summaries.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
      : guardian.kind === 'denied' ? <p className="muted">{t('finance.denied.text')}</p>
      : guardian.kind === 'error' ? <p className="muted">{t('finance.guardian.error', { message: guardian.message })}</p>
      : rows.length === 0 ? <Empty icon={<Target aria-hidden="true" />} title={t('fsafe.guard.none')} text={t('fsafe.guard.noneText')}
        action={<button type="button" className="secondary" onClick={() => onOpen('guardian')}>{t('fsafe.guard.set')}</button>} />
      : <div className="finTableWrap" tabIndex={0}><table className="finGrid"><caption className="visuallyHidden">{t('fsafe.guard.title')}</caption>
        <thead><tr><th scope="col">{t('frep.col.category')}</th><th scope="col" className="num">{t('fsafe.guard.spent')}</th><th scope="col" className="num">{t('fsafe.guard.limit')}</th><th scope="col">{t('fsafe.guard.usage')}</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.category}><th scope="row">{categoryLabel(r.category)}</th><td className="num"><Money value={r.spent} currency={currency} /></td>
          <td className="num"><Money value={r.limit} currency={currency} /></td>
          <td><span className={`finUsage ${r.tone}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.round(r.percent))} aria-label={t('fsafe.guard.usageLabel', { category: categoryLabel(r.category), percent: formatNumber(r.percent) })}>
            <span className="finTrack"><span className="finFill" style={{ width: `${Math.min(100, r.percent)}%` }} /></span><small>{formatNumber(Math.round(r.percent))}%</small></span></td></tr>)}</tbody>
      </table></div>}
  </FinCard>;
}

function Freshness({ age, newest, onOpen, onRefresh }: { age: number | null; newest: string | null; onOpen: (tab: FinanceTab) => void; onRefresh: () => void }) {
  const stale = age === null || age > STALE_DAYS;
  return <FinBanner tone={stale ? 'warn' : 'ok'} icon={stale ? <TriangleAlert /> : <CircleCheck />}
    title={stale ? t('fsafe.stale.title') : t('fsafe.fresh.title')}
    action={<>{stale && <button type="button" className="secondary" onClick={() => onOpen('import')}>{t('fsafe.stale.import')}</button>}
      <button type="button" className="secondary" onClick={onRefresh}><RefreshCw aria-hidden="true" />{t('fsafe.refresh')}</button></>}>
    {newest ? t(stale ? 'fsafe.stale.text' : 'fsafe.fresh.text', { date: isolate(newest), count: age ?? 0 }) : t('fsafe.stale.none')}
  </FinBanner>;
}

function RulesPreview({ guardian, onOpen }: { guardian: Load<FinanceGuardian>; onOpen: (tab: FinanceTab) => void }) {
  const lines = guardian.kind === 'ready' ? describeRules(guardian.value.rules) : [];
  return <FinCard title={t('fsafe.rules.title')} subtitle={t('fsafe.rules.subtitle')} icon={<ShieldCheck />} className="finRulesPreview">
    {guardian.kind === 'loading' ? <Notice busy>{t('finance.guardian.loading')}</Notice>
      : guardian.kind !== 'ready' ? <p className="muted">{guardian.message}</p>
      : lines.length === 0 ? <p className="muted">{t('finance.guardian.emptyText')}</p>
      : <ul className="finRuleLines">{lines.slice(0, 4).map((line) => <li key={line}><ShieldCheck aria-hidden="true" />{line}</li>)}</ul>}
    {guardian.kind === 'ready' && guardian.value.pending.length > 0 && <StatusChip tone="warn">{t('fsafe.rules.pending', { count: guardian.value.pending.length })}</StatusChip>}
    <div className="finCardButtons">
      <button type="button" className="secondary" onClick={() => onOpen('guardian')}>{t('fsafe.rules.review')}</button>
      <a className="finLinkBtn" href="#finance-guardian" onClick={(e) => { e.preventDefault(); onOpen('guardian'); }}><ShieldCheck aria-hidden="true" />{t('fsafe.rules.open')}</a>
    </div>
  </FinCard>;
}

function RulesTab({ guardian, alerts, onOpen, retry }: { guardian: Load<FinanceGuardian>; alerts: Load<FinanceGuardianAlerts>; onOpen: (tab: FinanceTab) => void; retry: () => void }) {
  if (guardian.kind === 'loading') return <Notice busy>{t('finance.guardian.loading')}</Notice>;
  if (guardian.kind === 'denied') return <Denied />;
  if (guardian.kind === 'error') return <LoadError message={t('finance.guardian.error', { message: guardian.message })} retry={retry} />;
  const lines = describeRules(guardian.value.rules);
  return <div className="finReportGrid finSafeGridRules">
    <FinCard title={t('finance.guardian.title')} subtitle={t('finance.guardian.version', { version: guardian.value.version })} icon={<ShieldCheck />}>
      {lines.length === 0 ? <Empty icon={<ShieldCheck aria-hidden="true" />} title={t('finance.guardian.empty')} text={t('finance.guardian.emptyText')}
        action={<button type="button" onClick={() => onOpen('guardian')}>{t('finance.guardian.set')}</button>} />
        : <ul className="finRuleLines">{lines.map((line) => <li key={line}><ShieldCheck aria-hidden="true" />{line}</li>)}</ul>}
      <div className="finCardButtons"><button type="button" onClick={() => onOpen('guardian')}>{t('fsafe.rules.open')}</button></div>
    </FinCard>
    <FinCard title={t('finance.guardian.alerts')} icon={<TriangleAlert />}>
      {alerts.kind === 'loading' ? <Notice busy>{t('finance.guardian.loading')}</Notice>
        : alerts.kind !== 'ready' ? <p className="muted">{alerts.message}</p>
        : alerts.value.alerts.length === 0 ? <p className="muted">{t('fsafe.rules.noAlerts')}</p>
        : <ul className="finRuleLines">{alerts.value.alerts.map((a, i) => <li key={`${a.code}-${i}`} className={a.severity}><TriangleAlert aria-hidden="true" /><span dir="auto">{a.message}</span></li>)}</ul>}
    </FinCard>
  </div>;
}

function BusinessScope({ report, retry }: { report: Load<BooksReport>; retry: () => void }) {
  return <>
    <FinBanner icon={<Info />} title={t('fsafe.business.title')} action={<a className="finLinkBtn secondary" href="#business-books">{t('frep.openBooks')}</a>}>{t('fsafe.business.text')}</FinBanner>
    <FinCard title={t('fsafe.business.budgets')} icon={<Target />}>
      {report.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
        : report.kind === 'denied' ? <p className="muted">{t('frep.business.deniedText')}</p>
        : report.kind === 'error' ? <LoadError message={t('frep.error', { message: report.message })} retry={retry} />
        : report.value.budgets.length === 0 ? <Empty icon={<Target aria-hidden="true" />} title={t('fsafe.business.noBudgets')} text={t('fsafe.business.noBudgetsText')}
          action={<a className="finLinkBtn secondary" href="#business-books/budgets">{t('fsafe.business.addBudget')}</a>} />
        : <div className="finTableWrap" tabIndex={0}><table className="finGrid"><caption className="visuallyHidden">{t('fsafe.business.budgets')}</caption>
          <thead><tr><th scope="col">{t('fsafe.business.budget')}</th><th scope="col" className="num">{t('fsafe.guard.spent')}</th><th scope="col" className="num">{t('fsafe.guard.limit')}</th><th scope="col">{t('fsafe.guard.usage')}</th></tr></thead>
          <tbody>{report.value.budgets.map((b) => { const used = b.used_pct === null ? null : Number(b.used_pct); const tone = used === null ? 'ok' : used >= 100 ? 'bad' : used >= 80 ? 'warn' : 'ok';
            return <tr key={b.id}><th scope="row" dir="auto">{b.name}</th><td className="num">{b.actual === null ? '-' : <Money value={b.actual} currency={b.currency} />}</td>
              <td className="num"><Money value={b.budget} currency={b.currency} /></td>
              <td>{used === null ? <small className="muted">{t('fsafe.business.notComparable')}</small> : <span className={`finUsage ${tone}`}><span className="finTrack" aria-hidden="true"><span className="finFill" style={{ width: `${Math.min(100, used)}%` }} /></span><small>{formatNumber(Math.round(used))}%</small></span>}</td></tr>; })}</tbody>
        </table></div>}
    </FinCard>
  </>;
}
