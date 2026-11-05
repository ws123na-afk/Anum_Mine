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
import { EquityCharts } from './FinanceEquity';
import { IntelPanel } from './FinanceIntel';
import { KillSwitches, OrderForm, PaperHoldings, ReviewIntent, TradingPanel } from './FinanceTrading';
import { Donut, Kpi } from './FinanceCharts';
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
  return <section className="finPage finLab" aria-labelledby="fin-lab">
    <div className="finPageBar">
      <div className="finPageBarTitle"><h2 id="fin-lab">{t('flab.title')}</h2><p className="muted">{t('flab.subtitle')}</p></div>
      {list.length > 0 && tab !== 'research' && tab !== 'brokers' && tab !== 'strategies' && <div className="finPageControls">
        <label className="finControl" htmlFor={pickerId}><span>{t('flab.portfolio')}</span>
          <select id={pickerId} value={current?.id ?? ''} onChange={(e) => setSelected(e.target.value)}>{list.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      </div>}
    </div>
    <p className="finPaperBanner" role="note"><TriangleAlert aria-hidden="true" /><span><strong>{t('flab.banner')}</strong> {t('flab.bannerText')}</span></p>
    <nav className="settingsTabs finSubTabs" aria-label={t('flab.tabs')}>
      {LAB_TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`flab.tab.${id}` as MessageKey)}</button>)}
    </nav>
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
  return <div className="finGrid">
    <div className="finMain">
      {current ? <PortfolioBlock key={`${current.id}-${ordersKey}`} portfolioId={current.id} base={base} running={count('PAPER_ACTIVE')} paused={count('PAPER_SUSPENDED')} strategiesKnown={intel.kind === 'ready'}
        onTab={onTab} onChanged={() => { setOrdersKey((k) => k + 1); onChanged(); }} />
        : <NoPortfolio onTab={onTab} />}
      {portfolios.length > 1 && <p className="muted finCardNote">{t('flab.portfolios', { count: portfolios.length })}</p>}
      <div className="finPair">
        <ResearchBrief onTab={onTab} />
        <BrokerStatus onTab={onTab} />
      </div>
    </div>
    <aside className="finSide" aria-label={t('flab.side')}>
      <StrategyLab onTab={onTab} />
      <RiskCaps intel={intel} retry={() => void reloadIntel()} onOpen={onOpen} />
      <KillSwitchCard />
      <Backtest onTab={onTab} />
      <LiveLocked onTab={onTab} />
      <div className="finLinks">
        <button type="button" className="finLinkCard" onClick={() => onOpen('execution')}><FileClock aria-hidden="true" /><span><strong>{t('flab.audit')}</strong><small>{t('flab.auditText')}</small></span><ChevronRight aria-hidden="true" className="cmFlip" /></button>
        <button type="button" className="finLinkCard" onClick={() => onOpen('guardian')}><ShieldCheck aria-hidden="true" /><span><strong>{t('flab.riskReview')}</strong><small>{t('flab.riskReviewText')}</small></span><ChevronRight aria-hidden="true" className="cmFlip" /></button>
      </div>
    </aside>
  </div>;
}

function PortfolioBlock({ portfolioId, base, running, paused, strategiesKnown, onTab, onChanged }: {
  portfolioId: string; base: string; running: number; paused: number; strategiesKnown: boolean; onTab: (tab: LabTab) => void; onChanged: () => void;
}) {
  const load = useCallback(() => api.getPortfolio(portfolioId), [portfolioId]);
  const [state, reload] = useLoad(load);
  const [ordering, setOrdering] = useState(false), [review, setReview] = useState<FinanceIntent | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  if (state.kind !== 'ready') return <div className="surface finCard"><CardState state={state} retry={() => void reload()} what={t('flab.portfolioWord')} /></div>;
  const p = state.value;
  const f = paperFigures(p);
  const slices = foldSlices(f.slices.map((s) => ({ ...s, label: s.key === 'cash' ? t('fholdings.kpi.cash') : s.label })), 5, t('fholdings.allocation.other'));
  const money = (v: string | null) => (v === null ? '-' : formatAmount(v, f.currency));
  return <>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <div className="finKpis finKpisIcons" role="group" aria-label={t('flab.figures')}>
      <Kpi icon={<Layers />} label={t('flab.kpi.value')} value={<span dir="ltr">{money(f.value)}</span>} hint={f.unpriced ? t('flab.kpi.unpriced', { count: f.unpriced }) : p.mode === 'shadow' ? t('flab.kpi.shadow') : t('flab.kpi.valueHint')} />
      <Kpi icon={<ChartLine />} label={t('flab.kpi.return')} value={<span dir="ltr">{f.returnPercent ?? '-'}</span>} tone={f.returnTone}
        hint={f.returnPercent === null ? t('flab.kpi.returnNone') : t('flab.kpi.returnHint', { start: formatAmount(f.start ?? '0', f.currency) })} />
      <Kpi icon={<Briefcase />} label={t('flab.kpi.positions')} value={String(p.positions)} hint={p.waiting ? t('finance.trading.waitingCount', { count: p.waiting }) : t('flab.kpi.positionsHint')} />
      <Kpi icon={<Play />} label={t('flab.kpi.strategies')} value={strategiesKnown ? t('flab.kpi.strategiesValue', { running, paused }) : '-'} hint={t('flab.kpi.strategiesHint')} />
    </div>
    <div className="finPair finPairWide">
      <section className="surface finCard" aria-labelledby="flab-equity">
        <div className="finCardHead"><h3 id="flab-equity">{t('flab.equity')}</h3><span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span></div>
        <EquityCharts portfolioId={p.id} name={p.name} mode={p.mode} onOpenTrading={() => onTab('orders')} />
      </section>
      <section className="surface finCard" aria-labelledby="flab-alloc">
        <div className="finCardHead"><h3 id="flab-alloc">{t('flab.allocation')}</h3></div>
        {slices.length === 0 ? <Empty icon={<Wallet aria-hidden="true" />} title={t('fholdings.allocation.empty')} text={t('flab.allocationEmpty')} />
          : <Donut slices={slices} total={money(f.total)} totalLabel={t('fholdings.allocation.total')} format={(s) => formatAmount(String(s.value), f.currency)} label={t('flab.allocationLabel', { currency: f.currency })} />}
      </section>
    </div>
    <RecentOrders portfolio={p} onNew={() => setOrdering(true)} onAll={() => onTab('orders')} />
    {ordering && <Modal title={t('flab.newOrder')} className="formDialog" onClose={() => setOrdering(false)}>
      <OrderForm portfolio={p} base={base} onCreated={(intent) => { setOrdering(false); setReview(intent); }} />
    </Modal>}
    {review && <ReviewIntent intent={review} onClose={(text, error) => { setReview(null); if (text) setNotice({ text, error }); void reload(); onChanged(); }} />}
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
  return <section className="surface finCard" aria-labelledby={id}>
    <div className="finCardHead"><h3 id={id}>{t('flab.orders')}</h3>
      <div className="finHeadActions"><button type="button" className="secondary" onClick={onNew}><Plus aria-hidden="true" />{t('flab.newOrder')}</button></div></div>
    <CardState state={state} retry={() => void reload()} what={t('flab.ordersWord')} />
    {state.kind === 'ready' && (state.value.items.length === 0 ? <Empty icon={<FlaskConical aria-hidden="true" />} title={t('flab.ordersNone')} text={t('flab.ordersNoneText')}
      action={<button type="button" onClick={onNew}><Plus aria-hidden="true" />{t('flab.newOrder')}</button>} /> : <>
      <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable finOrders">
        <caption className="visuallyHidden">{t('flab.ordersCaption', { name: p.name })}</caption>
        <thead><tr>{head('date', t('fholdings.chart.date'))}{head('symbol', t('finance.trading.col.symbol'))}{head('side', t('finance.trading.side'))}
          {head('quantity', t('finance.trading.col.quantity'), true)}{head('price', t('flab.col.price'), true)}{head('status', t('flab.col.status'))}</tr></thead>
        <tbody>{sortOrders(state.value.items, sort).map((o: FinancePaperOrder) => <tr key={o.id}>
          <td>{formatDateTime(o.filled_at ?? o.created_at, { dateStyle: 'medium' })}</td>
          <th scope="row"><bdi dir="ltr">{o.symbol}</bdi>{o.requested_by === 'agent' && <small>{t('finance.trading.byAgent')}</small>}</th>
          <td><span className={`finSide-${o.side}`}>{t(o.side === 'buy' ? 'finance.trading.buy' : 'finance.trading.sell')}</span></td>
          <td className="num" dir="ltr">{formatDecimal(o.quantity, 8)}</td>
          <td className="num" dir="ltr">{o.fill_price ? formatAmount(Number(o.fill_price).toFixed(4), o.currency) : o.limit_price ? formatAmount(Number(o.limit_price).toFixed(4), o.currency) : '-'}</td>
          <td><span className={`statusBadge ${orderTone(o.status)}`}>{t(`flab.order.${o.status}` as MessageKey)}</span></td>
        </tr>)}</tbody>
      </table></div>
      {state.value.total > state.value.items.length && <button type="button" className="cmLinkButton finCardMore" onClick={onAll}>{t('flab.ordersAll', { count: state.value.total })}</button>}
    </>)}
  </section>;
}

function LabHoldings({ portfolioId }: { portfolioId: string }) {
  const load = useCallback(() => api.getPortfolio(portfolioId), [portfolioId]);
  const [state, reload] = useLoad(load);
  if (state.kind !== 'ready') return <div className="surface finCard"><CardState state={state} retry={() => void reload()} what={t('flab.portfolioWord')} /></div>;
  return <section className="surface finCard" aria-labelledby="flab-holdings">
    <div className="finCardHead"><h3 id="flab-holdings"><span dir="auto">{state.value.name}</span></h3><span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span></div>
    <PaperHoldings portfolio={state.value} />
  </section>;
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
  return <section className="surface finCard" aria-labelledby={id}>
    <div className="finCardHead"><h3 id={id}><BookOpenCheck aria-hidden="true" className="cmInlineIcon" />{t('flab.research')}</h3></div>
    <CardState state={symbols} retry={() => void reload()} what={t('flab.researchWord')} />
    {symbols.kind === 'ready' && !first && <Empty icon={<BookOpenCheck aria-hidden="true" />} title={t('flab.researchNone')} text={t('flab.researchNoneText')}
      action={<button type="button" className="secondary" onClick={() => onTab('brokers')}>{t('flab.openBrokers')}</button>} />}
    {report && report.kind !== 'ready' && <CardState state={report} retry={() => void reload()} what={t('flab.researchWord')} />}
    {report?.kind === 'ready' && <Brief report={report.value} symbols={symbols.kind === 'ready' ? symbols.value : null} />}
    <button type="button" className="cmLinkButton finCardMore" onClick={() => onTab('research')}>{t('flab.researchOpen')}</button>
  </section>;
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
    <p className="finInfo" role="note">{symbols?.disclaimer ?? r.disclaimer}</p>
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
  return <section className="surface finCard" aria-labelledby={id}>
    <div className="finCardHead"><h3 id={id}><Link2 aria-hidden="true" className="cmInlineIcon" />{t('flab.broker')}</h3></div>
    <CardState state={state} retry={() => void reload()} what={t('flab.brokerWord')} />
    {state.kind === 'ready' && <>
      <p className={`finStatusLine ${ok ? 'ok' : 'warn'}`}>{ok ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
        <span>{paper ? t('flab.brokerPaper', { status: t(`ibkr.session.${paper.session}` as MessageKey), account: paper.hint }) : t('flab.brokerNone')}</span></p>
      <p className="muted finCardNote">{t('flab.brokerHelp')}</p>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      <div className="finCardActions">
        <button type="button" onClick={() => onTab('brokers')}><Link2 aria-hidden="true" />{paper ? t('flab.brokerManage') : t('flab.brokerConnect')}</button>
        {paper && <button type="button" className="secondary" disabled={busy} onClick={() => void test()}><Play aria-hidden="true" />{busy ? t('finance.saving') : t('flab.brokerTest')}</button>}
      </div>
      <p className="finInfo"><span>{t('flab.builtIn')}</span> <button type="button" className="cmLinkButton" onClick={() => onTab('orders')}>{t('flab.builtInOpen')}</button></p>
    </>}
  </section>;
}

// ---- Side column ---------------------------------------------------------------------------------------------

function StrategyLab({ onTab }: { onTab: (tab: LabTab) => void }) {
  const load = useCallback(() => api.listStrategies({ state: '', limit: 5, offset: 0 }), []);
  const [state, reload] = useLoad(load);
  const id = useId();
  return <section className="surface finCard" aria-labelledby={id}>
    <div className="finCardHead"><h3 id={id}>{t('flab.strategies')}</h3></div>
    <CardState state={state} retry={() => void reload()} what={t('flab.strategiesWord')} />
    {state.kind === 'ready' && (state.value.items.length === 0
      ? <Empty icon={<FlaskConical aria-hidden="true" />} title={t('flab.strategiesNone')} text={t('flab.strategiesNoneText')} action={<button type="button" className="secondary" onClick={() => onTab('strategies')}>{t('flab.strategiesOpen')}</button>} />
      : <ul className="finStrategyList">{state.value.items.map((s: FinanceStrategy) => <li key={s.id}>
        <button type="button" className="finStrategy" onClick={() => onTab('strategies')}>
          <span><strong dir="auto">{s.name}</strong><small dir="ltr">{s.symbols.join(', ')} · v{s.version}</small></span>
          <span className={`statusBadge ${stateTone(s.state)}`}>{stateLabel(s.state)}</span><ChevronRight aria-hidden="true" className="cmFlip" /></button></li>)}</ul>)}
    {state.kind === 'ready' && state.value.total > state.value.items.length && <button type="button" className="cmLinkButton finCardMore" onClick={() => onTab('strategies')}>{t('flab.strategiesAll', { count: state.value.total })}</button>}
  </section>;
}

function RiskCaps({ intel, retry, onOpen }: { intel: Load<FinanceIntelOverview>; retry: () => void; onOpen: (tab: FinanceTab) => void }) {
  const id = useId();
  const lines = intel.kind === 'ready' ? describeRules(intel.value.risk.rules) : [];
  return <section className="surface finCard" aria-labelledby={id}>
    <div className="finCardHead"><h3 id={id}><ShieldCheck aria-hidden="true" className="cmInlineIcon" />{t('flab.risk')}</h3></div>
    <CardState state={intel} retry={retry} what={t('flab.riskWord')} />
    {intel.kind === 'ready' && (lines.length === 0 ? <p className="muted">{t('flab.riskNone')}</p>
      : <ul className="finRiskList">{lines.slice(0, 5).map((line) => <li key={line}>{line}</li>)}</ul>)}
    {intel.kind === 'ready' && intel.value.risk.paper_always_needs_approval && <p className="muted finCardNote">{t('flab.riskApproval')}</p>}
    <button type="button" className="cmLinkButton finCardMore" onClick={() => onOpen('guardian')}>{t('flab.riskEdit')}</button>
  </section>;
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
  return <section className="surface finCard" aria-labelledby={id}>
    <div className="finCardHead"><h3 id={id}>{t('flab.backtest')}</h3><span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span></div>
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
    <p className="finInfo" role="note">{t('flab.pastNote')}</p>
  </section>;
}

function LiveLocked({ onTab }: { onTab: (tab: LabTab) => void }) {
  const [state, reload] = useLoad<IbkrOverview>(ibkrApi.getOverview);
  const id = useId();
  const gates = state.kind === 'ready' ? state.value.live_gates : [];
  const open = state.kind === 'ready' && state.value.mode === 'live';
  return <section className="surface finCard finLocked" aria-labelledby={id}>
    <div className="finCardHead"><h3 id={id}><Lock aria-hidden="true" className="cmInlineIcon" />{t('flab.live')}</h3>
      <span className={`statusBadge ${open ? 'danger' : 'neutral'}`}>{t(open ? 'flab.liveOn' : 'flab.liveOff')}</span></div>
    <CardState state={state} retry={() => void reload()} what={t('flab.liveWord')} />
    {state.kind === 'ready' && <>
      <p className="muted finCardNote">{t('flab.liveIntro', { done: gates.filter((g) => g.ok).length, total: gates.length })}</p>
      <ol className="finGates">{gates.map((g) => <li key={g.code} className={g.ok ? 'ok' : ''}>{g.ok ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
        <span>{t(`ibkr.gate.${g.code}` as MessageKey)}<span className="visuallyHidden"> {t(g.ok ? 'finance.live.met' : 'finance.live.unmet')}</span></span></li>)}</ol>
    </>}
    <button type="button" className="cmLinkButton finCardMore" onClick={() => onTab('brokers')}>{t('flab.liveOpen')}</button>
  </section>;
}
