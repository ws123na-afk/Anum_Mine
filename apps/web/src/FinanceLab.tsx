// Finance, Paper trading and portfolio lab (docs/finance.md#paper-trading-lab): one page for SIMULATED trading.
// The overview gathers the existing pieces (paper portfolios and orders, the SIMULATED equity curve, fund research,
// the IBKR connection, strategies and their runs, Guardian limits, kill switches, the live gates) and the tabs open
// each of them in full. Nothing here sends a real order: paper orders still run only after the person approves the
// code shown, and live trading stays locked exactly as docs/finance-live-trading.md says. Lazy chunk.
import { Suspense, lazy, useCallback, useEffect, useId, useState } from 'react';
import {
  ArrowDownUp, BookOpenCheck, Briefcase, ChartLine, ChevronRight, CircleCheck, CircleX, ExternalLink, FileClock, FlaskConical, Layers, Link2, Lock, Play, Plus,
  ShieldCheck, TriangleAlert, Wallet,
} from 'lucide-react';
import type {
  FinanceExecutionStatus, FinanceIntelOverview, FinanceIntent, FinancePaperOrder, FinancePortfolio, FinancePortfolioSummary, FinanceStrategy, FinanceStrategyRun,
  IbkrOverview, ResearchReport, ResearchSymbols,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as ibkrApi from './lib/financeIbkrApi';
import * as researchApi from './lib/financeResearchApi';
import { describeRules, formatAmount, type FinanceTab } from './lib/finance';
import { connectionFor } from './lib/financeIbkr';
import { formatMetric, metricLabel, orderedMetrics, stateLabel, stateTone, verdictLabel, verdictTone } from './lib/financeIntel';
import { LAB_TABS, labHash, labTabFromHash, paperFigures, sortOrders, type LabTab, type OrderSort } from './lib/financeLab';
import { foldSlices } from './lib/financeHoldings';
import { formatDecimal, orderTone } from './lib/financeTrading';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { Denied, Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { IntelPanel } from './FinanceIntel';
import { KillSwitches, OrderForm, PaperHoldings, ReviewIntent, TradingPanel } from './FinanceTrading';
import { Donut, LineChart } from './FinanceCharts';
import { benchmarkValues } from './lib/financeEquity';
import { FinBanner, FinCard, FinanceTabs, FinancePageHead, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import { viewLabel } from './lib/shell';
import './financeLab.css';

const ResearchPanel = lazy(() => import('./FinanceResearch').then((m) => ({ default: m.ResearchPanel })));
const IbkrPanel = lazy(() => import('./FinanceIbkr').then((m) => ({ default: m.IbkrPanel })));

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => Promise<void>] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setState({ kind: 'ready', value: await load() }); } catch (e) { setState(failed(e)); }
  }, [load]);
  useEffect(() => { void run(); }, [run]);
  return [state, run];
}

function useLabTab(): [LabTab, (next: LabTab) => void] {
  const [tab, setTab] = useState<LabTab>(() => labTabFromHash(typeof location === 'undefined' ? '' : location.hash));
  useEffect(() => {
    const sync = () => { if (location.hash.startsWith('#finance-trading')) setTab(labTabFromHash(location.hash)); };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync); };
  }, []);
  return [tab, (next) => { setTab(next); if (location.hash !== labHash(next)) history.pushState(null, '', labHash(next)); }];
}

/** A small card state: loading, error with retry, permission denied. */
function CardState({ state, retry, what }: { state: Load<unknown>; retry: () => void; what: string }) {
  if (state.kind === 'loading') return <Notice busy>{t('flab.loading')}</Notice>;
  if (state.kind === 'denied') return <p className="muted">{t('flab.denied')}</p>;
  if (state.kind === 'error') return <LoadError message={t('flab.error', { what, message: state.message })} retry={retry} />;
  return null;
}

export function LabPage({ base, onOpen }: { base: string; onOpen: (tab: FinanceTab) => void }) {
  const [tab, pick] = useLabTab();
  const [portfolios, reload] = useLoad(api.listPortfolios);
  const [selected, setSelected] = useState('');
  const pickerId = useId();
  const list = portfolios.kind === 'ready' ? portfolios.value : [];
  const current = list.find((p) => p.id === selected) ?? list[0] ?? null;
  const crumbs = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('finance'), href: '#finance', onClick: () => onOpen('overview') }, { label: t('finance.tab.trading') }];
  const picker = list.length > 0 && (tab === 'overview' || tab === 'holdings') ? <label className="finControl" htmlFor={pickerId}><span className="finControlLabel">{t('flab.portfolio')}</span>
    <select id={pickerId} value={current?.id ?? ''} onChange={(e) => setSelected(e.target.value)}>{list.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label> : undefined;
  return <section className="finPage finLab" aria-labelledby="view-title">
    <FinancePageHead title={t('flab.title')} subtitle={t('flab.subtitle')} crumbs={crumbs} controls={picker}
      tabs={<FinanceTabs label={t('flab.tabs')} idPrefix="flab" value={tab} onChange={pick} items={LAB_TABS.map((id) => ({ id, label: t(`flab.tab.${id}` as MessageKey) }))} />} />
    <FinBanner tone="warn" icon={<TriangleAlert />} title={t('flab.banner')}>{t('flab.bannerText')}</FinBanner>
    <div {...tabPanel('flab', tab)}>
    {tab === 'overview' && (portfolios.kind === 'ready'
      ? <Overview portfolios={list} current={current} base={base} onTab={pick} onOpen={onOpen} onChanged={() => void reload()} />
      : portfolios.kind === 'denied' ? <Denied />
        : portfolios.kind === 'error' ? <div className="surface cmPanel"><LoadError message={t('finance.trading.error.load', { message: portfolios.message })} retry={() => void reload()} /></div>
          : <Notice busy>{t('finance.trading.loading')}</Notice>)}
    {tab === 'holdings' && (current ? <LabHoldings key={current.id} portfolioId={current.id} /> : <NoPortfolio onTab={pick} />)}
    {tab === 'orders' && <TradingPanel base={base} onOpen={onOpen} />}
    {tab === 'strategies' && <IntelPanel onOpen={onOpen} />}
    {tab === 'research' && <Suspense fallback={<Notice busy>{t('fresearch.loading')}</Notice>}><ResearchPanel /></Suspense>}
    {tab === 'brokers' && <Suspense fallback={<Notice busy>{t('ibkr.loading')}</Notice>}><IbkrPanel /></Suspense>}
    </div>
  </section>;
}

function NoPortfolio({ onTab }: { onTab: (tab: LabTab) => void }) {
  return <div className="surface cmPanel"><Empty icon={<FlaskConical aria-hidden="true" />} title={t('finance.trading.empty')} text={t('finance.trading.emptyText')}
    action={<button type="button" onClick={() => onTab('orders')}><Plus aria-hidden="true" />{t('finance.trading.newPortfolio')}</button>} /></div>;
}

// ---- Overview --------------------------------------------------------------------------------------------

function Overview({ portfolios, current, base, onTab, onOpen, onChanged }: {
  portfolios: FinancePortfolioSummary[]; current: FinancePortfolioSummary | null; base: string; onTab: (tab: LabTab) => void; onOpen: (tab: FinanceTab) => void; onChanged: () => void;
}) {
  const [intel, reloadIntel] = useLoad(api.getIntelOverview);
  const [ordersKey, setOrdersKey] = useState(0);
  const strategyCounts = intel.kind === 'ready' ? intel.value.strategies : [];
  const count = (state: string) => strategyCounts.find((s) => s.state === state)?.count ?? 0;
  const currentId = current?.id ?? '';
  const load = useCallback(() => (currentId ? api.getPortfolio(currentId) : Promise.resolve(null)), [currentId]);
  const [portfolio, reload] = useLoad<FinancePortfolio | null>(load);
  const p = portfolio.kind === 'ready' ? portfolio.value : null;
  const f = p ? paperFigures(p) : null;
  const money = (v: string | null) => (v === null || !f ? '-' : formatAmount(v, f.currency));
  const running = count('PAPER_ACTIVE'), paused = count('PAPER_SUSPENDED');
  return <>
    {p && f && <KpiRow label={t('flab.figures')}>
      <KpiCard icon={<Layers />} label={t('flab.kpi.value')} value={<span dir="ltr">{money(f.value)}</span>} hint={f.unpriced ? t('flab.kpi.unpriced', { count: f.unpriced }) : p.mode === 'shadow' ? t('flab.kpi.shadow') : t('flab.kpi.valueHint')} />
      <KpiCard icon={<ChartLine />} label={t('flab.kpi.return')} value={<span dir="ltr" className={f.returnTone}>{f.returnPercent ?? '-'}</span>}
        hint={f.returnPercent === null ? t('flab.kpi.returnNone') : t('flab.kpi.returnHint', { start: formatAmount(f.start ?? '0', f.currency) })} />
      <KpiCard icon={<Briefcase />} label={t('flab.kpi.positions')} value={String(p.positions)} hint={p.waiting ? t('finance.trading.waitingCount', { count: p.waiting }) : t('flab.kpi.positionsHint')} />
      <KpiCard icon={<Play />} label={t('flab.kpi.strategies')} value={intel.kind === 'ready' ? t('flab.kpi.strategiesValue', { running, paused }) : '-'} hint={t('flab.kpi.strategiesHint')} />
    </KpiRow>}
    <div className="finCols">
    <div className="finMain">
      {!current ? <NoPortfolio onTab={onTab} /> : !p ? <FinCard title={t('flab.portfolioTitle')}><CardState state={portfolio} retry={() => void reload()} what={t('flab.portfolioWord')} /></FinCard>
        : <PortfolioBlock key={p.id} version={ordersKey} portfolio={p} base={base} onTab={onTab} onChanged={() => { setOrdersKey((k) => k + 1); void reload(); onChanged(); }} />}
      {portfolios.length > 1 && <p className="muted finCardNote">{t('flab.portfolios', { count: portfolios.length })}</p>}
      <div className="finPair">
        <ResearchBrief onTab={onTab} />
        <BrokerStatus onTab={onTab} />
      </div>
      <Backtest onTab={onTab} />
    </div>
    <aside className="finSide" aria-label={t('flab.side')}>
      <StrategyLab onTab={onTab} />
      <RiskCaps intel={intel} retry={() => void reloadIntel()} onOpen={onOpen} />
      <KillSwitchCard />
      <LiveLocked onTab={onTab} />
      <div className="finLinks">
        <button type="button" className="finLinkCard" onClick={() => onOpen('execution')}><FileClock aria-hidden="true" /><span><strong>{t('flab.audit')}</strong><small>{t('flab.auditText')}</small></span><ChevronRight aria-hidden="true" className="cmFlip" /></button>
        <button type="button" className="finLinkCard" onClick={() => onOpen('guardian')}><ShieldCheck aria-hidden="true" /><span><strong>{t('flab.riskReview')}</strong><small>{t('flab.riskReviewText')}</small></span><ChevronRight aria-hidden="true" className="cmFlip" /></button>
      </div>
    </aside>
    </div>
  </>;
}

function PortfolioBlock({ portfolio: p, version, base, onTab, onChanged }: { portfolio: FinancePortfolio; version: number; base: string; onTab: (tab: LabTab) => void; onChanged: () => void }) {
  const [ordering, setOrdering] = useState(false), [review, setReview] = useState<FinanceIntent | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const f = paperFigures(p);
  const slices = foldSlices(f.slices.map((s) => ({ ...s, label: s.key === 'cash' ? t('fholdings.kpi.cash') : s.label })), 5, t('fholdings.allocation.other'));
  const money = (v: string | null) => (v === null ? '-' : formatAmount(v, f.currency));
  return <>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <div className="finPair finPairWide">
      <FinCard title={t('flab.equity')} actions={<StatusChip tone="info">{t('finance.trading.simulated')}</StatusChip>}>
        <EquityMini key={version} portfolioId={p.id} onTab={onTab} />
      </FinCard>
      <FinCard title={t('flab.allocation')}>
        {slices.length === 0 ? <Empty icon={<Wallet aria-hidden="true" />} title={t('fholdings.allocation.empty')} text={t('flab.allocationEmpty')} />
          : <Donut slices={slices} total={money(f.total)} totalLabel={t('fholdings.allocation.total')} format={(s) => formatAmount(String(s.value), f.currency)} label={t('flab.allocationLabel', { currency: f.currency })} />}
      </FinCard>
    </div>
    <RecentOrders key={version} portfolio={p} onNew={() => setOrdering(true)} onAll={() => onTab('orders')} />
    {ordering && <Modal title={t('flab.newOrder')} className="formDialog" onClose={() => setOrdering(false)}>
      <OrderForm portfolio={p} base={base} onCreated={(intent) => { setOrdering(false); setReview(intent); }} />
    </Modal>}
    {review && <ReviewIntent intent={review} onClose={(text, error) => { setReview(null); if (text) setNotice({ text, error }); onChanged(); }} />}
  </>;
}

/** The SIMULATED P&L of this run against buy and hold of the same symbols (the full charts are in Strategies). */
function EquityMini({ portfolioId, onTab }: { portfolioId: string; onTab: (tab: LabTab) => void }) {
  const [days, setDays] = useState(90);
  const load = useCallback(() => api.getPortfolioEquity(portfolioId, days), [portfolioId, days]);
  const [state, reload] = useLoad(load);
  const rangeId = useId();
  const series = state.kind === 'ready' ? state.value.series[0] ?? null : null;
  const picker = <label className="finControl" htmlFor={rangeId}><span className="finControlLabel">{t('finance.equity.range')}</span>
    <select id={rangeId} value={days} onChange={(e) => setDays(Number(e.target.value))}>{[30, 90, 180, 365].map((d) => <option key={d} value={d}>{t('finance.equity.days', { count: d })}</option>)}</select></label>;
  if (state.kind !== 'ready') return <CardState state={state} retry={() => void reload()} what={t('flab.equityWord')} />;
  if (state.value.orders === 0 || !series) return <>{picker}<Empty icon={<ChartLine aria-hidden="true" />} title={t('finance.equity.empty')} text={t('finance.equity.emptyPaper')}
    action={<button type="button" className="secondary" onClick={() => onTab('orders')}>{t('flab.builtInOpen')}</button>} /></>;
  const points = series.points.map((p) => ({ label: formatDateTime(`${p.day}T12:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' }), short: formatDateTime(`${p.day}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' }), value: p.complete && p.pnl !== null ? Number(p.pnl) : null }));
  const bench = benchmarkValues(series);
  const cur = series.currency;
  return <>
    {picker}
    <LineChart points={points} format={(v) => formatAmount(v.toFixed(2), cur)} axis={(v) => new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(v)}
      label={t('flab.equityLabel', { currency: cur })} seriesLabel={t('flab.equityThis')} tableCaption={t('flab.equityLabel', { currency: cur })} testId="lab-equity-chart"
      compare={bench.some((v) => v !== null) ? { values: bench, label: t('flab.equityBench') } : undefined} />
    <p className="muted finCardNote">{t('flab.equityNote')}</p>
  </>;
}

function RecentOrders({ portfolio: p, onNew, onAll }: { portfolio: FinancePortfolio; onNew: () => void; onAll: () => void }) {
  const load = useCallback(() => api.listOrders(p.id, { run: 'current', limit: 10, offset: 0 }), [p.id]);
  const [state, reload] = useLoad(load);
  const [sort, setSort] = useState<OrderSort>({ key: 'date', descending: true });
  const id = useId();
  const by = (key: OrderSort['key']) => setSort((s) => ({ key, descending: s.key === key ? !s.descending : key === 'date' }));
  const head = (key: OrderSort['key'], label: string, num = false) => <th scope="col" className={num ? 'num' : undefined} aria-sort={sort.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'}>
    <button type="button" className="finSortButton" onClick={() => by(key)}>{label}<ArrowDownUp aria-hidden="true" /></button></th>;
  return <FinCard title={t('flab.orders')} actions={<button type="button" className="secondary" onClick={onNew}><Plus aria-hidden="true" />{t('flab.newOrder')}</button>}>
    <CardState state={state} retry={() => void reload()} what={t('flab.ordersWord')} />
    {state.kind === 'ready' && (state.value.items.length === 0 ? <Empty icon={<FlaskConical aria-hidden="true" />} title={t('flab.ordersNone')} text={t('flab.ordersNoneText')}
      action={<button type="button" onClick={onNew}><Plus aria-hidden="true" />{t('flab.newOrder')}</button>} /> : <>
      <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable finGrid finOrders">
        <caption className="visuallyHidden">{t('flab.ordersCaption', { name: p.name })}</caption>
        <thead><tr>{head('date', t('fholdings.chart.date'))}{head('symbol', t('finance.trading.col.symbol'))}{head('side', t('finance.trading.side'))}
          {head('quantity', t('finance.trading.col.quantity'), true)}{head('price', t('flab.col.price'), true)}{head('status', t('flab.col.status'))}</tr></thead>
        <tbody>{sortOrders(state.value.items, sort).map((o: FinancePaperOrder) => <tr key={o.id}>
          <td>{formatDateTime(o.filled_at ?? o.created_at, { dateStyle: 'medium' })}</td>
          <th scope="row"><bdi dir="ltr">{o.symbol}</bdi>{o.requested_by === 'agent' && <small>{t('finance.trading.byAgent')}</small>}</th>
          <td><span className={`finOrderSide-${o.side}`}>{t(o.side === 'buy' ? 'finance.trading.buy' : 'finance.trading.sell')}</span></td>
          <td className="num" dir="ltr">{formatDecimal(o.quantity, 8)}</td>
          <td className="num" dir="ltr">{o.fill_price ? formatAmount(Number(o.fill_price).toFixed(4), o.currency) : o.limit_price ? formatAmount(Number(o.limit_price).toFixed(4), o.currency) : '-'}</td>
          <td><span className={`statusBadge ${orderTone(o.status)}`}>{t(`flab.order.${o.status}` as MessageKey)}</span></td>
        </tr>)}</tbody>
      </table></div>
      {state.value.total > state.value.items.length && <button type="button" className="cmLinkButton finCardMore" onClick={onAll}>{t('flab.ordersAll', { count: state.value.total })}</button>}
    </>)}
  </FinCard>;
}

function LabHoldings({ portfolioId }: { portfolioId: string }) {
  const load = useCallback(() => api.getPortfolio(portfolioId), [portfolioId]);
  const [state, reload] = useLoad(load);
  if (state.kind !== 'ready') return <div className="surface finCard"><CardState state={state} retry={() => void reload()} what={t('flab.portfolioWord')} /></div>;
  return <FinCard title={state.value.name} actions={<StatusChip tone="info">{t('finance.trading.simulated')}</StatusChip>}>
    <PaperHoldings portfolio={state.value} />
  </FinCard>;
}

// ---- Research and broker -----------------------------------------------------------------------------------

function ResearchBrief({ onTab }: { onTab: (tab: LabTab) => void }) {
  const [symbols, reload] = useLoad(researchApi.listSymbols);
  const [report, setReport] = useState<Load<ResearchReport> | null>(null);
  const first = symbols.kind === 'ready' ? symbols.value.symbols[0]?.symbol ?? null : null;
  useEffect(() => {
    if (!first) return;
    let live = true;
    setReport({ kind: 'loading' });
    researchApi.getResearch(first).then((value) => { if (live) setReport({ kind: 'ready', value }); }, (e) => { if (live) setReport(failed(e)); });
    return () => { live = false; };
  }, [first]);
  const id = useId();
  return <FinCard title={t('flab.research')} icon={<BookOpenCheck />}>
    <CardState state={symbols} retry={() => void reload()} what={t('flab.researchWord')} />
    {symbols.kind === 'ready' && !first && <Empty icon={<BookOpenCheck aria-hidden="true" />} title={t('flab.researchNone')} text={t('flab.researchNoneText')}
      action={<button type="button" className="secondary" onClick={() => onTab('brokers')}>{t('flab.openBrokers')}</button>} />}
    {report && report.kind !== 'ready' && <CardState state={report} retry={() => void reload()} what={t('flab.researchWord')} />}
    {report?.kind === 'ready' && <Brief report={report.value} symbols={symbols.kind === 'ready' ? symbols.value : null} />}
    <button type="button" className="cmLinkButton finCardMore" onClick={() => onTab('research')}>{t('flab.researchOpen')}</button>
  </FinCard>;
}

function Brief({ report: r, symbols }: { report: ResearchReport; symbols: ResearchSymbols | null }) {
  const sources = r.sources.filter((s) => s.used && s.url && /^https:\/\//.test(s.url));
  const problems = r.source_problems.length;
  const summary = r.summary?.status === 'ok' && r.summary.summary ? r.summary.summary.history : null;
  return <>
    <p className="finBriefMeta"><strong dir="ltr">{r.symbol}</strong> <span dir="auto">{r.name}</span> · {t('flab.gathered', { date: formatDateTime(r.gathered_at) })}
      {problems > 0 && <> · <span className="statusBadge warning">{t('flab.sourceProblems', { count: problems })}</span></>}</p>
    {summary ? <p dir="auto" className="finBriefText">{summary}</p> : <p className="muted">{t('flab.noSummary')}</p>}
    {sources.length > 0 && <ul className="finEvidence" aria-label={t('flab.evidence')}>{sources.slice(0, 4).map((s) => <li key={s.key}>
      <a href={s.url ?? undefined} target="_blank" rel="noopener noreferrer"><span dir="auto">{s.name}</span><ExternalLink aria-hidden="true" /><span className="visuallyHidden">{t('flab.newTab')}</span></a></li>)}</ul>}
    <FinBanner tone="info">{symbols?.disclaimer ?? r.disclaimer}</FinBanner>
  </>;
}

function BrokerStatus({ onTab }: { onTab: (tab: LabTab) => void }) {
  const [state, reload] = useLoad(ibkrApi.getOverview);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const id = useId();
  const paper = state.kind === 'ready' ? connectionFor(state.value, 'paper') : null;
  async function test() {
    if (!paper) return;
    setBusy(true);
    try { const c = await ibkrApi.checkConnection(paper.id); setNotice({ text: t('flab.brokerChecked', { status: t(`ibkr.session.${c.session}` as MessageKey) }) }); void reload(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(false); }
  }
  const ok = paper?.session === 'authenticated';
  return <FinCard title={t('flab.broker')} icon={<Link2 />}>
    <CardState state={state} retry={() => void reload()} what={t('flab.brokerWord')} />
    {state.kind === 'ready' && <>
      <p className={`finStatusLine ${ok ? 'ok' : 'warn'}`}>{ok ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
        <span>{paper ? t('flab.brokerPaper', { status: t(`ibkr.session.${paper.session}` as MessageKey), account: paper.hint }) : t('flab.brokerNone')}</span></p>
      <p className="muted finCardNote">{t('flab.brokerHelp')}</p>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      <div className="finCardButtons">
        <button type="button" onClick={() => onTab('brokers')}><Link2 aria-hidden="true" />{paper ? t('flab.brokerManage') : t('flab.brokerConnect')}</button>
        {paper && <button type="button" className="secondary" disabled={busy} onClick={() => void test()}><Play aria-hidden="true" />{busy ? t('finance.saving') : t('flab.brokerTest')}</button>}
      </div>
      <p className="finInfo"><span>{t('flab.builtIn')}</span> <button type="button" className="cmLinkButton" onClick={() => onTab('orders')}>{t('flab.builtInOpen')}</button></p>
    </>}
  </FinCard>;
}

// ---- Side column ---------------------------------------------------------------------------------------------

function StrategyLab({ onTab }: { onTab: (tab: LabTab) => void }) {
  const load = useCallback(() => api.listStrategies({ state: '', limit: 5, offset: 0 }), []);
  const [state, reload] = useLoad(load);
  const id = useId();
  return <FinCard title={t('flab.strategies')}>
    <CardState state={state} retry={() => void reload()} what={t('flab.strategiesWord')} />
    {state.kind === 'ready' && (state.value.items.length === 0
      ? <Empty icon={<FlaskConical aria-hidden="true" />} title={t('flab.strategiesNone')} text={t('flab.strategiesNoneText')} action={<button type="button" className="secondary" onClick={() => onTab('strategies')}>{t('flab.strategiesOpen')}</button>} />
      : <ul className="finStrategyList">{state.value.items.map((s: FinanceStrategy) => <li key={s.id}>
        <button type="button" className="finStrategy" onClick={() => onTab('strategies')}>
          <span><strong dir="auto">{s.name}</strong><small dir="ltr">{s.symbols.join(', ')} · v{s.version}</small></span>
          <span className={`statusBadge ${stateTone(s.state)}`}>{stateLabel(s.state)}</span><ChevronRight aria-hidden="true" className="cmFlip" /></button></li>)}</ul>)}
    {state.kind === 'ready' && state.value.total > state.value.items.length && <button type="button" className="cmLinkButton finCardMore" onClick={() => onTab('strategies')}>{t('flab.strategiesAll', { count: state.value.total })}</button>}
  </FinCard>;
}

function RiskCaps({ intel, retry, onOpen }: { intel: Load<FinanceIntelOverview>; retry: () => void; onOpen: (tab: FinanceTab) => void }) {
  const id = useId();
  const lines = intel.kind === 'ready' ? describeRules(intel.value.risk.rules) : [];
  return <FinCard title={t('flab.risk')} icon={<ShieldCheck />}>
    <CardState state={intel} retry={retry} what={t('flab.riskWord')} />
    {intel.kind === 'ready' && (lines.length === 0 ? <p className="muted">{t('flab.riskNone')}</p>
      : <ul className="finRiskList">{lines.slice(0, 5).map((line) => <li key={line}>{line}</li>)}</ul>)}
    {intel.kind === 'ready' && intel.value.risk.paper_always_needs_approval && <p className="muted finCardNote">{t('flab.riskApproval')}</p>}
    <button type="button" className="cmLinkButton finCardMore" onClick={() => onOpen('guardian')}>{t('flab.riskEdit')}</button>
  </FinCard>;
}

function KillSwitchCard() {
  const [state, reload] = useLoad<FinanceExecutionStatus>(api.getExecutionStatus);
  if (state.kind !== 'ready') return <div className="surface finCard"><CardState state={state} retry={() => void reload()} what={t('flab.killWord')} /></div>;
  return <div className="finKill"><KillSwitches status={state.value} onChanged={() => void reload()} /></div>;
}

function Backtest({ onTab }: { onTab: (tab: LabTab) => void }) {
  const load = useCallback(async (): Promise<{ strategy: FinanceStrategy; run: FinanceStrategyRun } | null> => {
    const page = await api.listStrategies({ state: '', limit: 10, offset: 0 });
    for (const strategy of page.items) {
      const runs = await api.listStrategyRuns(strategy.id, { limit: 1, offset: 0 });
      if (runs.items[0]) return { strategy, run: runs.items[0] };
    }
    return null;
  }, []);
  const [state, reload] = useLoad(load);
  const id = useId();
  return <FinCard title={t('flab.backtest')} actions={<StatusChip tone="info">{t('finance.trading.simulated')}</StatusChip>}>
    <CardState state={state} retry={() => void reload()} what={t('flab.backtestWord')} />
    {state.kind === 'ready' && (state.value === null
      ? <Empty icon={<ChartLine aria-hidden="true" />} title={t('flab.backtestNone')} text={t('flab.backtestNoneText')} action={<button type="button" className="secondary" onClick={() => onTab('strategies')}>{t('flab.strategiesOpen')}</button>} />
      : <>
        <p className="finBriefMeta"><strong dir="auto">{state.value.strategy.name}</strong> · {t(state.value.run.kind === 'walk_forward' ? 'flab.walkForward' : 'flab.backtestKind')} · {formatDateTime(state.value.run.created_at, { dateStyle: 'medium' })}
          {' '}<span className={`statusBadge ${verdictTone(state.value.run.verdict)}`}>{verdictLabel(state.value.run.verdict)}</span></p>
        <dl className="finMetrics">{orderedMetrics(state.value.run.metrics).slice(0, 4).map((m) => <div key={m.key}><dt>{metricLabel(m.key)}</dt><dd dir="ltr">{formatMetric(m.key, m.value)}</dd></div>)}
          {state.value.run.benchmark.total_return !== undefined && <div><dt>{t('flab.benchmark')}</dt><dd dir="ltr">{formatMetric('total_return', state.value.run.benchmark.total_return)}</dd></div>}</dl>
        <p className="muted finCardNote">{t('flab.dataSource', { source: state.value.run.data_source })}</p>
      </>)}
    <FinBanner tone="info">{t('flab.pastNote')}</FinBanner>
  </FinCard>;
}

function LiveLocked({ onTab }: { onTab: (tab: LabTab) => void }) {
  const [state, reload] = useLoad<IbkrOverview>(ibkrApi.getOverview);
  const id = useId();
  const gates = state.kind === 'ready' ? state.value.live_gates : [];
  const open = state.kind === 'ready' && state.value.mode === 'live';
  return <FinCard title={t('flab.live')} icon={<Lock />} actions={<span className={`statusBadge ${open ? 'danger' : 'neutral'}`}>{t(open ? 'flab.liveOn' : 'flab.liveOff')}</span>}>
    <CardState state={state} retry={() => void reload()} what={t('flab.liveWord')} />
    {state.kind === 'ready' && <>
      <p className="muted finCardNote">{t('flab.liveIntro', { done: gates.filter((g) => g.ok).length, total: gates.length })}</p>
      <ol className="finGates">{gates.map((g) => <li key={g.code} className={g.ok ? 'ok' : ''}>{g.ok ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
        <span>{t(`ibkr.gate.${g.code}` as MessageKey)}<span className="visuallyHidden"> {t(g.ok ? 'finance.live.met' : 'finance.live.unmet')}</span></span></li>)}</ol>
    </>}
    <button type="button" className="cmLinkButton finCardMore" onClick={() => onTab('brokers')}>{t('flab.liveOpen')}</button>
  </FinCard>;
}
