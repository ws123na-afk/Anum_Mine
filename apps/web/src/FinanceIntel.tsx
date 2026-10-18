// The trading intelligence dashboard (docs/finance-trading-intel.md#dashboard): strategies and their
// lifecycle, backtest and walk-forward results, SIMULATED paper and shadow positions and P&L, what the
// Trading Supervisor, the specialist agents and the Guardian decided and why, and the person's limits
// (read only). Simulation only: nothing here buys, sells, pays or sends anything. The only change it
// offers is the existing kill switch, which stops every simulated executor.
import { useCallback, useEffect, useId, useState } from 'react';
import { Bot, ChevronLeft, ChevronRight, Clock, FlaskConical, Gauge, LineChart, ListChecks, OctagonX, Power, ShieldCheck, Wallet } from 'lucide-react';
import type {
  FinanceIntelAgent, FinanceIntelOverview, FinanceIntelPortfolio, FinancePage, FinanceStrategy, FinanceStrategyRun, FinanceStrategyState,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import { describeRules, formatAmounts, isolate, rangeLabel, type FinanceTab } from './lib/finance';
import { codeText, formatDecimal, intentLine, intentTone } from './lib/financeTrading';
import {
  AGENTS, STRATEGY_STATES, agentLabel, costsLine, foldMetricKeys, outcomeLabel, formatMetric, metricLabel, orderedMetrics, outcomeTone, paramsLine, sortVenues, stateLabel, stateTone, venueClock,
  verdictLabel, verdictTone,
} from './lib/financeIntel';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { Denied, Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { reasonText } from './FinancePlan';
import { EquityCharts } from './FinanceEquity';

const PAGE = 10;
const RUN_PAGE = 5;

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => Promise<void>] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setState({ kind: 'ready', value: await load() }); } catch (e) { setState(failed(e)); }
  }, [load]);
  useEffect(() => { void run(); }, [run]);
  return [state, run];
}

function Pager({ page, onPage, label, size }: { page: FinancePage<unknown>; onPage: (offset: number) => void; label: string; size: number }) {
  if (page.total <= size && page.offset === 0) return <p className="muted cmPagerText">{rangeLabel(page.offset, page.items.length, page.total)}</p>;
  return <nav className="cmPager" aria-label={label}>
    <button type="button" className="secondary" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - size))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('finance.page.previous')}</button>
    <span className="muted" aria-live="polite">{rangeLabel(page.offset, page.items.length, page.total)}</span>
    <button type="button" className="secondary" disabled={page.offset + page.items.length >= page.total} onClick={() => onPage(page.offset + size)}>{t('finance.page.next')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
  </nav>;
}

export function IntelPanel({ onOpen }: { onOpen: (tab: FinanceTab) => void }) {
  const [overview, reload] = useLoad(api.getIntelOverview);
  if (overview.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('finance.intel.loading')}</Notice></section>;
  if (overview.kind === 'denied') return <Denied />;
  if (overview.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('finance.intel.error.load', { message: overview.message })} retry={() => void reload()} /></section>;
  const o = overview.value;
  return <div className="finIntel">
    <SimulationBanner overview={o} onChanged={() => void reload()} onOpen={onOpen} />
    <Venues overview={o} />
    <Strategies counts={o.strategies} onOpen={onOpen} />
    <Positions portfolios={o.portfolios} onOpen={onOpen} />
    <AgentDecisions />
    <GuardianDecisions />
    <RiskLimits overview={o} onOpen={onOpen} />
  </div>;
}

function SimulationBanner({ overview: o, onChanged, onOpen }: { overview: FinanceIntelOverview; onChanged: () => void; onOpen: (tab: FinanceTab) => void }) {
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const stopped = o.switches.person_engaged || o.switches.global_engaged;
  async function toggle() {
    setBusy(true);
    try {
      await api.setKillSwitch('person', !o.switches.person_engaged, o.switches.person_engaged ? undefined : t('finance.intel.stopReason'));
      setMessage({ text: o.switches.person_engaged ? t('finance.intel.allowed') : t('finance.intel.stopped') });
      setConfirming(false); onChanged();
    } catch (e) { setMessage({ text: describeError(e), error: true }); setConfirming(false); } finally { setBusy(false); }
  }
  return <section className="surface cmPanel finIntelBanner" aria-labelledby="fin-intel-title">
    <div className="sectionHeader"><h2 id="fin-intel-title"><Gauge aria-hidden="true" className="cmInlineIcon" />{t('finance.tab.intel')}</h2>
      <span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span></div>
    <div className="notice finIntelSimOnly" role="note"><FlaskConical aria-hidden="true" />
      <span><strong>{t('finance.intel.simOnly')}</strong> {t('finance.intel.simOnlyText')}</span></div>
    {message && <Notice error={message.error}>{message.text}</Notice>}
    <dl className="finIntelFacts">
      <div><dt>{t('finance.intel.live')}</dt><dd><span className="statusBadge warning">{o.simulation.live_enabled ? t('finance.intel.liveOn') : t('finance.intel.liveOff')}</span></dd></div>
      <div><dt>{t('finance.intel.execution')}</dt><dd><span className={`statusBadge ${stopped ? 'warning' : 'success'}`}>{stopped ? t('finance.intel.executionStopped') : t('finance.intel.executionRunning')}</span>
        {o.switches.global_engaged && <small>{t('finance.intel.globalStopped')}</small>}</dd></div>
      <div><dt>{t('finance.intel.waiting')}</dt><dd><button type="button" className="cmLinkButton" onClick={() => onOpen('trading')}>{t('finance.intel.waitingCount', { count: o.pending_requests })}</button></dd></div>
      <div><dt>{t('finance.intel.agentRequests')}</dt><dd>{t('finance.intel.agentRequestsCount', { count: o.agent_requests })}</dd></div>
    </dl>
    <div className="actions finIntelActions">
      <button type="button" className={o.switches.person_engaged ? 'secondary' : 'danger'} onClick={() => setConfirming(true)}>
        {o.switches.person_engaged ? <Power aria-hidden="true" /> : <OctagonX aria-hidden="true" />}{o.switches.person_engaged ? t('finance.intel.allow') : t('finance.intel.stopAll')}</button>
      <button type="button" className="secondary" onClick={() => onOpen('execution')}>{t('finance.intel.openExecution')}</button>
    </div>
    {confirming && <ConfirmDialog title={o.switches.person_engaged ? t('finance.intel.allowTitle') : t('finance.intel.stopTitle')} confirmLabel={o.switches.person_engaged ? t('finance.intel.allow') : t('finance.intel.stopAll')}
      danger={!o.switches.person_engaged} busy={busy} onConfirm={() => void toggle()} onCancel={() => setConfirming(false)}>
      <p>{o.switches.person_engaged ? t('finance.intel.allowText') : t('finance.intel.stopText')}</p></ConfirmDialog>}
  </section>;
}

function Venues({ overview: o }: { overview: FinanceIntelOverview }) {
  const venues = sortVenues(o.venues);
  return <section className="surface cmPanel" aria-labelledby="fin-intel-venues">
    <div className="sectionHeader"><h2 id="fin-intel-venues"><Clock aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.venues')}</h2></div>
    <p className="muted finHelp">{t('finance.intel.venuesHelp')}</p>
    <div className="cmTableWrap"><table className="cmTable finTable finIntelVenues finIntelCardsSm" aria-label={t('finance.intel.venues')}>
      <thead><tr><th scope="col">{t('finance.intel.venue')}</th><th scope="col">{t('finance.intel.session')}</th><th scope="col">{t('finance.intel.localTime')}</th><th scope="col">{t('finance.intel.why')}</th></tr></thead>
      <tbody>{venues.map((v) => <tr key={v.code}>
        <th scope="row"><span dir="auto">{v.name}</span></th>
        <td data-label={t('finance.intel.session')}><span className={`statusBadge ${v.state === 'open' ? 'success' : 'neutral'}`}>{t(`finance.intel.session.${v.state}` as MessageKey)}</span></td>
        <td data-label={t('finance.intel.localTime')}><span dir="ltr">{venueClock(v)}</span></td>
        <td data-label={t('finance.intel.why')}><span dir="auto">{v.reason}</span>{!v.holidays_known && <small className="muted"> {t('finance.intel.holidaysUnknown')}</small>}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

function Strategies({ counts, onOpen }: { counts: FinanceIntelOverview['strategies']; onOpen: (tab: FinanceTab) => void }) {
  const count = (s: FinanceStrategyState) => counts.find((c) => c.state === s)?.count ?? 0;
  const [state, setState] = useState<FinanceStrategyState | ''>(''), [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<FinanceStrategy | null>(null);
  const load = useCallback(() => api.listStrategies({ state, limit: PAGE, offset }), [state, offset]);
  const [page, reload] = useLoad(load);
  const filterId = useId();
  return <section className="surface cmPanel" aria-labelledby="fin-intel-strategies">
    <div className="sectionHeader"><h2 id="fin-intel-strategies"><LineChart aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.strategies')}</h2>
      <label className="field finIntelFilter" htmlFor={filterId}><span>{t('finance.intel.stateFilter')}</span>
        <select id={filterId} value={state} onChange={(e) => { setState(e.target.value as FinanceStrategyState | ''); setOffset(0); setSelected(null); }}>
          <option value="">{t('finance.intel.allStates')}</option>
          {STRATEGY_STATES.map((s) => <option key={s} value={s}>{t('finance.intel.stateCount', { state: stateLabel(s), count: count(s) })}</option>)}
        </select></label></div>
    <p className="muted finHelp">{t('finance.intel.lifecycleHelp')}</p>
    {page.kind === 'loading' && <Notice busy>{t('finance.intel.loadingStrategies')}</Notice>}
    {page.kind === 'denied' && <Notice error>{t('finance.denied.text')}</Notice>}
    {page.kind === 'error' && <LoadError message={t('finance.intel.error.strategies', { message: page.message })} retry={() => void reload()} />}
    {page.kind === 'ready' && page.value.items.length === 0 && (state
      ? <Empty icon={<LineChart aria-hidden="true" />} title={t('finance.intel.noneInState', { state: stateLabel(state) })} text={t('finance.intel.noneInStateText')}
        action={<button type="button" className="secondary" onClick={() => setState('')}>{t('finance.intel.allStates')}</button>} />
      : <Empty icon={<LineChart aria-hidden="true" />} title={t('finance.intel.noStrategies')} text={t('finance.intel.noStrategiesText')}
        action={<button type="button" className="secondary" onClick={() => onOpen('trading')}>{t('finance.intel.openPaper')}</button>} />)}
    {page.kind === 'ready' && page.value.items.length > 0 && <>
      <div className="cmTableWrap"><table className="cmTable finTable finIntelStrategies finIntelCards" aria-label={t('finance.intel.strategiesTable')}>
        <thead><tr><th scope="col">{t('finance.intel.strategy')}</th><th scope="col">{t('finance.intel.state')}</th><th scope="col">{t('finance.intel.instruments')}</th><th scope="col">{t('finance.intel.params')}</th><th scope="col">{t('finance.intel.updated')}</th><th scope="col"><span className="visuallyHidden">{t('finance.intel.results')}</span></th></tr></thead>
        <tbody>{page.value.items.map((s) => <tr key={s.id} aria-current={selected?.id === s.id ? 'true' : undefined}>
          <th scope="row"><span dir="auto">{s.name}</span> <small className="muted">{t('finance.intel.version', { version: s.version })}</small>
            <small className="finIntelReason"><span dir="auto">{s.reason}</span> {t('finance.intel.movedBy', { who: agentLabel(s.state_changed_by) })}</small></th>
          <td data-label={t('finance.intel.state')}><span className={`statusBadge ${stateTone(s.state)}`}>{stateLabel(s.state)}</span></td>
          <td data-label={t('finance.intel.instruments')}><span dir="ltr">{s.symbols.join(', ') || '-'}</span></td>
          <td data-label={t('finance.intel.params')}><small dir="auto">{paramsLine(s.params)}</small></td>
          <td data-label={t('finance.intel.updated')}>{formatDateTime(s.state_changed_at)}</td>
          <td><button type="button" className="secondary" aria-pressed={selected?.id === s.id} onClick={() => setSelected(selected?.id === s.id ? null : s)}>{t('finance.intel.results')}</button></td>
        </tr>)}</tbody>
      </table></div>
      <Pager page={page.value} onPage={(next) => { setOffset(next); setSelected(null); }} label={t('finance.intel.strategyPages')} size={PAGE} />
    </>}
    {selected && <Runs key={selected.id} strategy={selected} />}
  </section>;
}

function Runs({ strategy }: { strategy: FinanceStrategy }) {
  const [offset, setOffset] = useState(0);
  const load = useCallback(() => api.listStrategyRuns(strategy.id, { limit: RUN_PAGE, offset }), [strategy.id, offset]);
  const [page, reload] = useLoad(load);
  const headingId = useId();
  return <section className="finIntelRuns" aria-labelledby={headingId}>
    <h3 id={headingId}>{t('finance.intel.runsFor', { name: strategy.name })}</h3>
    <p className="muted finHelp">{t('finance.intel.runsHelp')}</p>
    {page.kind === 'loading' && <Notice busy>{t('finance.intel.loadingRuns')}</Notice>}
    {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={t('finance.intel.error.runs', { message: page.message })} retry={() => void reload()} />}
    {page.kind === 'ready' && page.value.items.length === 0 && <p className="muted">{t('finance.intel.noRuns')}</p>}
    {page.kind === 'ready' && page.value.items.length > 0 && <>
      <ul className="cmList finIntelRunList">{page.value.items.map((run) => <li key={run.id}><RunCard run={run} /></li>)}</ul>
      <Pager page={page.value} onPage={setOffset} label={t('finance.intel.runPages')} size={RUN_PAGE} />
    </>}
  </section>;
}

function RunCard({ run }: { run: FinanceStrategyRun }) {
  const metrics = orderedMetrics(run.metrics), benchmark = orderedMetrics(run.benchmark);
  const foldKeys = foldMetricKeys(run.folds);
  return <article className="finIntelRun" aria-label={t(`finance.intel.kind.${run.kind === 'walk_forward' ? 'walk_forward' : 'backtest'}` as MessageKey)}>
    <p className="finIntelRunHead"><strong>{t(`finance.intel.kind.${run.kind === 'walk_forward' ? 'walk_forward' : 'backtest'}` as MessageKey)}</strong>
      <span className={`statusBadge ${verdictTone(run.verdict)}`}>{verdictLabel(run.verdict)}</span>
      <span className={`statusBadge ${run.reproduced ? 'success' : 'neutral'}`}>{run.reproduced ? t('finance.intel.reproduced') : t('finance.intel.notReproduced')}</span>
      <span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span>
      <small className="muted">{formatDateTime(run.created_at)}</small></p>
    <p className="muted finHelp"><span>{t('finance.intel.dataSource')}</span> <span dir="auto">{run.data_source}</span> · <span dir="ltr">{run.data_hash.slice(0, 12)}</span>
      {' · '}{t('finance.intel.codeVersion', { version: run.code_version })}{costsLine(run.costs) && <> · <span dir="ltr">{costsLine(run.costs)}</span></>}</p>
    {metrics.length > 0 && <dl className="finFigures finIntelMetrics">{metrics.map((m) => <div key={m.key}><dt>{metricLabel(m.key)}</dt><dd dir="ltr">{formatMetric(m.key, m.value)}</dd></div>)}</dl>}
    {benchmark.length > 0 && <><h4 className="finIntelSub">{t('finance.intel.benchmark')}</h4>
      <dl className="finFigures finIntelMetrics">{benchmark.map((m) => <div key={m.key}><dt>{metricLabel(m.key)}</dt><dd dir="ltr">{formatMetric(m.key, m.value)}</dd></div>)}</dl></>}
    {run.reasons.length > 0 && <ul className="finIntelReasons">{run.reasons.map((r, i) => <li key={i} dir="auto">{r}</li>)}</ul>}
    {run.warnings.length > 0 && <div className="notice finIntelWarnings" role="note"><strong>{t('finance.intel.warnings')}</strong>
      <ul className="finIntelReasons">{run.warnings.map((w, i) => <li key={i} dir="auto">{w}</li>)}</ul></div>}
    {run.folds.length > 0 && <details className="cmChartTable"><summary>{t('finance.intel.folds', { count: run.folds.length })}</summary>
      <div className="cmTableWrap"><table className="cmTable finTable finIntelCards" aria-label={t('finance.intel.foldsTable')}>
        <thead><tr><th scope="col">#</th><th scope="col">{t('finance.intel.train')}</th><th scope="col">{t('finance.intel.test')}</th>{foldKeys.map((k) => <th key={k} scope="col">{metricLabel(k)}</th>)}</tr></thead>
        <tbody>{run.folds.map((f) => <tr key={f.index}><th scope="row">{f.index + 1}</th>
          <td data-label={t('finance.intel.train')}><span dir="ltr">{f.train_start ?? '-'} / {f.train_end ?? '-'}</span></td>
          <td data-label={t('finance.intel.test')}><span dir="ltr">{f.test_start ?? '-'} / {f.test_end ?? '-'}</span></td>
          {foldKeys.map((k) => <td key={k} data-label={metricLabel(k)}><span dir="ltr">{formatMetric(k, f.metrics[k])}</span></td>)}</tr>)}</tbody>
      </table></div></details>}
    <p className="muted finHelp">{t('finance.intel.noPromise')}</p>
  </article>;
}

function Positions({ portfolios, onOpen }: { portfolios: FinanceIntelPortfolio[]; onOpen: (tab: FinanceTab) => void }) {
  return <section className="surface cmPanel" aria-labelledby="fin-intel-positions">
    <div className="sectionHeader"><h2 id="fin-intel-positions"><Wallet aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.positions')}</h2>
      <button type="button" className="cmLinkButton" onClick={() => onOpen('trading')}>{t('finance.intel.managePortfolios')}</button></div>
    {portfolios.length === 0
      ? <Empty icon={<Wallet aria-hidden="true" />} title={t('finance.intel.noPortfolios')} text={t('finance.intel.noPortfoliosText')}
        action={<button type="button" onClick={() => onOpen('trading')}>{t('finance.trading.newPortfolio')}</button>} />
      : <ul className="finIntelPortfolios">{portfolios.map((p) => <li key={p.id}><PortfolioBook portfolio={p} onOpen={onOpen} /></li>)}</ul>}
  </section>;
}

function PortfolioBook({ portfolio: p, onOpen }: { portfolio: FinanceIntelPortfolio; onOpen: (tab: FinanceTab) => void }) {
  const headingId = useId();
  return <article className="finIntelBook" aria-labelledby={headingId}>
    <h3 id={headingId}><span dir="auto">{p.name}</span> <span className="statusBadge neutral">{t(`finance.trading.mode.${p.mode}` as MessageKey)}</span> <span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span></h3>
    <dl className="finFigures">
      {p.mode === 'paper' && <div><dt>{t('finance.intel.cash')}</dt><dd>{isolate(formatAmounts(p.cash))}</dd></div>}
      <div><dt>{t('finance.intel.realised')}</dt><dd>{isolate(formatAmounts(p.realised))}</dd></div>
      <div><dt>{t('finance.intel.unrealised')}</dt><dd>{isolate(formatAmounts(p.unrealised))}</dd></div>
      <div><dt>{t('finance.intel.costs')}</dt><dd>{t('finance.trading.costsValue', { fee: isolate(formatAmounts(p.fees)), slippage: isolate(formatAmounts(p.slippage)) })}</dd></div>
    </dl>
    {p.unpriced.length > 0 && <p className="notice" role="note">{t('finance.intel.unpriced', { list: isolate(p.unpriced.join(', ')) })}</p>}
    {p.holdings.length === 0
      ? <p className="muted">{t('finance.intel.noHoldings')}</p>
      : <div className="cmTableWrap"><table className="cmTable finTable finIntelCards" aria-label={t('finance.intel.holdingsOf', { name: p.name })}>
        <thead><tr><th scope="col">{t('finance.intel.symbol')}</th><th scope="col">{t('finance.intel.quantity')}</th><th scope="col">{t('finance.intel.average')}</th><th scope="col">{t('finance.intel.price')}</th><th scope="col">{t('finance.intel.value')}</th><th scope="col">{t('finance.intel.unrealised')}</th></tr></thead>
        <tbody>{p.holdings.map((h) => <tr key={`${h.instrument_kind}-${h.symbol}-${h.currency}`}>
          <th scope="row" dir="ltr">{h.symbol}</th>
          <td data-label={t('finance.intel.quantity')}><span dir="ltr">{formatDecimal(h.quantity, 8)}</span></td>
          <td data-label={t('finance.intel.average')}><span dir="ltr">{formatDecimal(h.average_price)} {h.currency}</span></td>
          <td data-label={t('finance.intel.price')}><span dir="ltr">{h.price ? `${formatDecimal(h.price)} ${h.currency}` : '-'}</span>{h.price_as_of && <small className="muted"> <span dir="ltr">{h.price_as_of}</span></small>}</td>
          <td data-label={t('finance.intel.value')}><span dir="ltr">{h.market_value ? `${formatDecimal(h.market_value, 2)} ${h.currency}` : '-'}</span></td>
          <td data-label={t('finance.intel.unrealised')}><span dir="ltr">{h.unrealised ? `${formatDecimal(h.unrealised, 2)} ${h.currency}` : '-'}</span></td>
        </tr>)}</tbody>
      </table></div>}
    {p.holdings_total > p.holdings.length && <p className="muted">{t('finance.intel.moreHoldings', { shown: p.holdings.length, total: p.holdings_total })}</p>}
    <EquityCharts portfolioId={p.id} name={p.name} mode={p.mode} onOpenTrading={() => onOpen('trading')} />
  </article>;
}

function AgentDecisions() {
  const [agent, setAgent] = useState<FinanceIntelAgent | ''>(''), [offset, setOffset] = useState(0);
  const load = useCallback(() => api.listAgentDecisions({ agent, limit: PAGE, offset }), [agent, offset]);
  const [page, reload] = useLoad(load);
  const filterId = useId();
  return <section className="surface cmPanel" aria-labelledby="fin-intel-agents">
    <div className="sectionHeader"><h2 id="fin-intel-agents"><Bot aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.agents')}</h2>
      <label className="field finIntelFilter" htmlFor={filterId}><span>{t('finance.intel.agentFilter')}</span>
        <select id={filterId} value={agent} onChange={(e) => { setAgent(e.target.value as FinanceIntelAgent | ''); setOffset(0); }}>
          <option value="">{t('finance.intel.allAgents')}</option>{AGENTS.map((a) => <option key={a} value={a}>{agentLabel(a)}</option>)}
        </select></label></div>
    <p className="muted finHelp">{t('finance.intel.agentsHelp')}</p>
    {page.kind === 'loading' && <Notice busy>{t('finance.intel.loadingDecisions')}</Notice>}
    {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={t('finance.intel.error.decisions', { message: page.message })} retry={() => void reload()} />}
    {page.kind === 'ready' && page.value.items.length === 0 && <p className="muted">{agent ? t('finance.intel.noDecisionsAgent') : t('finance.intel.noDecisions')}</p>}
    {page.kind === 'ready' && page.value.items.length > 0 && <>
      <ol className="cmList finIntelTimeline" aria-label={t('finance.intel.agentsTimeline')}>{page.value.items.map((d) => <li key={d.id}>
        <p><strong>{agentLabel(d.agent)}</strong> <span dir="auto">{d.action.replace(/_/g, ' ')}</span> <span className={`statusBadge ${outcomeTone(d.outcome)}`} dir="auto">{outcomeLabel(d.outcome)}</span>
          <small className="muted"> {formatDateTime(d.created_at)}</small></p>
        {d.reasons.length > 0 && <ul className="finIntelReasons">{d.reasons.map((r, i) => <li key={i} dir="auto">{r}</li>)}</ul>}
      </li>)}</ol>
      <Pager page={page.value} onPage={setOffset} label={t('finance.intel.decisionPages')} size={PAGE} />
    </>}
  </section>;
}

function GuardianDecisions() {
  const [offset, setOffset] = useState(0);
  const load = useCallback(() => api.listIntents({ limit: PAGE, offset }), [offset]);
  const [page, reload] = useLoad(load);
  return <section className="surface cmPanel" aria-labelledby="fin-intel-guardian">
    <div className="sectionHeader"><h2 id="fin-intel-guardian"><ShieldCheck aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.guardianDecisions')}</h2></div>
    <p className="muted finHelp">{t('finance.intel.guardianHelp')}</p>
    {page.kind === 'loading' && <Notice busy>{t('finance.intel.loadingDecisions')}</Notice>}
    {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={t('finance.intel.error.decisions', { message: page.message })} retry={() => void reload()} />}
    {page.kind === 'ready' && page.value.items.length === 0 && <p className="muted">{t('finance.intel.noGuardianDecisions')}</p>}
    {page.kind === 'ready' && page.value.items.length > 0 && <>
      <ul className="cmList finIntelTimeline" aria-label={t('finance.intel.guardianList')}>{page.value.items.map((i) => {
        const outcome = 'outcome' in i.decision ? i.decision.outcome : null;
        const reasons = 'reasons' in i.decision ? i.decision.reasons : [];
        return <li key={i.id}>
          <p><strong>{intentLine(i)}</strong> <span className="statusBadge neutral">{t(`finance.trading.mode.${i.mode}` as MessageKey)}</span>
            <span className={`statusBadge ${intentTone(i.status)}`}>{t(`finance.trading.status.${i.status}` as MessageKey)}</span>
            {i.requested_by === 'agent' && <span className="statusBadge neutral">{t('finance.trading.byAgent')}</span>}
            <small className="muted"> {formatDateTime(i.created_at)}</small></p>
          {outcome && <p className="finIntelVerdict"><span className={`statusBadge ${outcome === 'deny' ? 'warning' : outcome === 'allow' ? 'success' : 'neutral'}`}>{t(`finance.trading.verdict.${outcome}` as MessageKey)}</span></p>}
          {reasons.length > 0 && <ul className="finIntelReasons">{reasons.map((r, n) => <li key={`${r.code}-${n}`}>{reasonText(r)}</li>)}</ul>}
          {i.refusal && <p className="muted">{codeText(i.refusal_code, i.refusal)}</p>}
        </li>;
      })}</ul>
      <Pager page={page.value} onPage={setOffset} label={t('finance.intel.guardianPages')} size={PAGE} />
    </>}
  </section>;
}

function RiskLimits({ overview: o, onOpen }: { overview: FinanceIntelOverview; onOpen: (tab: FinanceTab) => void }) {
  const lines = describeRules(o.risk.rules);
  return <section className="surface cmPanel finIntelRisk" aria-labelledby="fin-intel-risk">
    <div className="sectionHeader"><h2 id="fin-intel-risk"><ListChecks aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.risk')}</h2>
      <span className="statusBadge neutral">{t('finance.intel.readOnly')}</span></div>
    <p className="muted finHelp">{t('finance.intel.riskHelp')}</p>
    {lines.length === 0
      ? <Empty icon={<ShieldCheck aria-hidden="true" />} title={t('finance.intel.noRules')} text={t('finance.intel.noRulesText')}
        action={<button type="button" className="secondary" onClick={() => onOpen('guardian')}>{t('finance.intel.openGuardian')}</button>} />
      : <><p className="muted">{t('finance.intel.rulesVersion', { version: o.risk.version })}</p>
        <ul className="finIntelRules">{lines.map((line) => <li key={line}>{line}</li>)}</ul>
        <button type="button" className="secondary" onClick={() => onOpen('guardian')}>{t('finance.intel.openGuardian')}</button></>}
    {o.risk.paper_always_needs_approval && <p className="muted finHelp">{t('finance.intel.alwaysApprove')}</p>}
  </section>;
}
