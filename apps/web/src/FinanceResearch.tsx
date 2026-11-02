// Fund research (docs/finance-research.md): price history, worst falls, volatility, fund facts, dividends,
// Shariah status and recent news for the funds the owner can trade, each figure with its source and date;
// an AI summary whose every number is checked against the data; backtests of simple rules on past prices;
// and a "Paper run" that turns a tested monthly rule into a PAPER-only buy plan. Not financial advice. Lazy chunk.
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { BookOpenCheck, ExternalLink, FlaskConical, Newspaper, RefreshCw, ShieldCheck, Sparkles, TrendingDown, Wallet } from 'lucide-react';
import type {
  BacktestResult, IbkrEnvironment, ResearchFact, ResearchPortfolio, ResearchReport, ResearchShariah, ResearchSummary, ResearchSymbols,
} from '@anum/contracts';
import * as api from './lib/financeResearchApi';
import { PERIODS, RULES, chartPaths, checkDraft, factValue, groupDigits, percentText, toInput, tone, type BacktestDraft } from './lib/financeResearch';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Denied, Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import './financeResearch.css';

function ModeBadge({ mode }: { mode: IbkrEnvironment }) {
  return <span className={`statusBadge ibkrMode ${mode === 'live' ? 'danger' : 'neutral'}`}>{t(mode === 'live' ? 'ibkr.mode.live' : 'ibkr.mode.paper')}</span>;
}

function ShariahBadge({ shariah }: { shariah: ResearchShariah }) {
  return <span className={`statusBadge ${shariah.screened ? 'success' : 'warning'}`}>{t(shariah.screened ? 'fresearch.shariah.yes' : 'fresearch.shariah.no')}</span>;
}

const SOURCE_KEYS = ['ibkr_history', 'stooq', 'sec_nport', 'ibkr_flex', 'news', 'shariah'];

function sourceName(report: ResearchReport | null, key: string | null): string {
  if (!key) return '-';
  if (SOURCE_KEYS.includes(key)) return t(`fresearch.src.${key}` as MessageKey);
  return report?.sources.find((s) => s.key === key)?.name ?? key;
}

function FactView({ fact, report }: { fact: ResearchFact; report: ResearchReport }) {
  const value = factValue(fact);
  if (value === null) return <><span className="fresMissing">{t('fresearch.notAvailable')}</span><small className="fresWhy" dir="auto">{fact.unavailable}</small></>;
  return <><span dir="ltr" className={`fresValue ${fact.unit === 'percent' ? tone(fact.value) : ''}`}>{value}</span>
    <small className="fresWhy">{fact.as_of ? t('fresearch.sourceDate', { source: sourceName(report, fact.source), date: fact.as_of }) : t('fresearch.source', { source: sourceName(report, fact.source) })}</small></>;
}

export function ResearchPanel() {
  const [symbols, setSymbols] = useState<Load<ResearchSymbols>>({ kind: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const value = await api.listSymbols();
      setSymbols({ kind: 'ready', value });
      setSelected((current) => current ?? value.symbols[0]?.symbol ?? null);
    } catch (e) { setSymbols(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (symbols.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('fresearch.loading')}</Notice></section>;
  if (symbols.kind === 'denied') return <Denied />;
  if (symbols.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('fresearch.error.load', { message: symbols.message })} retry={() => void load()} /></section>;
  const s = symbols.value;
  return <div className="fresPanel">
    <section className="surface cmPanel" aria-labelledby="fres-title">
      <div className="sectionHeader"><h2 id="fres-title"><BookOpenCheck aria-hidden="true" className="cmInlineIcon" />{t('fresearch.title')} <ModeBadge mode={s.mode} /></h2></div>
      <p className="muted">{t('fresearch.intro')}</p>
      <p className="fresDisclaimer" role="note">{s.disclaimer}</p>
      <Portfolio />
      <h3 className="finSubHead" id="fres-pick">{t('fresearch.pick')}</h3>
      <p className="muted fresHint">{t('fresearch.pickHint')}</p>
      <div className="fresPicker" role="group" aria-labelledby="fres-pick">
        {s.symbols.map((item) => <button key={item.symbol} type="button" className={`fresChip ${selected === item.symbol ? 'active' : ''}`} aria-pressed={selected === item.symbol}
          onClick={() => setSelected(item.symbol)}>
          <strong dir="ltr">{item.symbol}</strong><ShariahBadge shariah={item.shariah} />
          <small>{t(item.allowed ? 'fresearch.onList' : 'fresearch.notOnList')}</small>
        </button>)}
      </div>
    </section>
    {selected && <Report key={selected} symbol={selected} symbols={s} onPlans={() => void load()} />}
  </div>;
}

function Portfolio() {
  const [state, setState] = useState<Load<ResearchPortfolio>>({ kind: 'loading' });
  useEffect(() => { api.getPortfolio().then((value) => setState({ kind: 'ready', value }), (e) => setState(failed(e))); }, []);
  if (state.kind === 'loading') return <Notice busy>{t('fresearch.loading')}</Notice>;
  if (state.kind !== 'ready') return <Notice error>{state.message}</Notice>;
  const p = state.value;
  return <div className="fresPortfolio" aria-label={t('fresearch.portfolio.title')}>
    <h3 className="finSubHead"><Wallet aria-hidden="true" className="cmInlineIcon" />{t('fresearch.portfolio.title')} <ModeBadge mode={p.mode} /></h3>
    {!p.connected ? <p className="muted">{t('fresearch.portfolio.none')}</p> : !p.synced_at ? <p className="muted">{t('fresearch.portfolio.notRead')}</p> : <>
      <dl className="finFigures fresFigures">
        <div><dt>{t('fresearch.portfolio.value')}</dt><dd dir="ltr">{p.net_liquidation ? `USD ${p.net_liquidation}` : '-'}</dd></div>
        <div><dt>{t('fresearch.portfolio.today')}</dt><dd dir="ltr" className={tone(p.daily_pnl)}>{p.daily_pnl ? `USD ${p.daily_pnl}` : '-'}</dd></div>
        <div><dt>{t('fresearch.portfolio.unrealized')}</dt><dd dir="ltr" className={tone(p.unrealized_pnl)}>{p.unrealized_pnl ? `USD ${p.unrealized_pnl}` : '-'}</dd></div>
      </dl>
      <p className="muted fresWhy">{t('fresearch.portfolio.asOf', { date: formatDateTime(p.synced_at) })}</p>
    </>}
  </div>;
}

function Report({ symbol, symbols, onPlans }: { symbol: string; symbols: ResearchSymbols; onPlans: () => void }) {
  const [state, setState] = useState<Load<ResearchReport>>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    try { setState({ kind: 'ready', value: await api.getResearch(symbol, refresh) }); } catch (e) { setState(failed(e)); } finally { setBusy(false); }
  }, [symbol]);
  useEffect(() => { void load(); }, [load]);
  if (state.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('fresearch.gathering', { symbol })}</Notice></section>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('fresearch.error.load', { message: state.message })} retry={() => void load()} /></section>;
  const r = state.value;
  return <>
    <section className="surface cmPanel" aria-labelledby="fres-fund" aria-busy={busy || undefined}>
      <div className="sectionHeader"><h2 id="fres-fund"><span dir="ltr">{r.symbol}</span> <ShariahBadge shariah={r.shariah} /></h2>
        <div className="finHeadActions"><button type="button" className="secondary" disabled={busy} onClick={() => void load(true)}><RefreshCw aria-hidden="true" />{t('fresearch.refresh')}</button></div></div>
      <p className="fresName" dir="auto">{r.name}</p>
      <p className="muted fresShariah"><ShieldCheck aria-hidden="true" className="cmInlineIcon" /><span dir="auto">{r.shariah.screen ? t('fresearch.shariah.by', { screen: r.shariah.screen }) : ''} {r.shariah.note}</span></p>
      <p className="muted fresWhy">{t('fresearch.gatheredAt', { date: formatDateTime(r.gathered_at), until: formatDateTime(r.expires_at) })}</p>
    </section>
    <Prices report={r} />
    <Falls report={r} />
    <FundFacts report={r} />
    <Dividends report={r} />
    <Summary report={r} dailyLimit={symbols.summary.daily_limit} />
    <Backtest report={r} symbols={symbols} onPlans={onPlans} />
    <News report={r} />
    <Sources report={r} />
  </>;
}

function Section({ id, icon, title, children }: { id: string; icon: ReactNode; title: string; children: ReactNode }) {
  return <section className="surface cmPanel" aria-labelledby={id}><h2 id={id}>{icon}{title}</h2>{children}</section>;
}

function Prices({ report: r }: { report: ResearchReport }) {
  const p = r.prices;
  return <Section id="fres-prices" icon={<TrendingDown aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.prices.title')}>
    {p.source === null ? <p className="notice" role="status">{t('fresearch.prices.none', { reason: p.unavailable ?? '-' })}</p> : <>
      <dl className="finFigures fresFigures">
        <div><dt>{t('fresearch.prices.lastClose')}</dt><dd><FactView fact={p.last_close} report={r} /></dd></div>
        <div><dt>{t('fresearch.vol.yearly')}</dt><dd><FactView fact={r.volatility.yearly} report={r} /></dd></div>
        <div><dt>{t('fresearch.vol.worst')}</dt><dd><FactView fact={r.volatility.worst_month} report={r} /></dd></div>
        <div><dt>{t('fresearch.vol.best')}</dt><dd><FactView fact={r.volatility.best_month} report={r} /></dd></div>
      </dl>
      <p className="muted fresWhy" dir="auto">{p.label}. {p.adjusted}. {t('fresearch.prices.range', { from: p.from ?? '-', to: p.to ?? '-', points: p.points ?? 0 })}</p>
    </>}
    <div className="cmTableWrap" tabIndex={0} role="group" aria-label={t('fresearch.prices.title')}><table className="cmTable finTable fresTable">
      <thead><tr><th scope="col">{t('fresearch.col.period')}</th><th scope="col">{t('fresearch.col.total')}</th><th scope="col">{t('fresearch.col.yearly')}</th></tr></thead>
      <tbody>{PERIODS.map((key) => {
        const row = r.returns[key];
        return <tr key={key}><th scope="row">{t(`fresearch.period.${key}` as MessageKey)}</th>
          <td><FactView fact={row.total} report={r} /></td><td><FactView fact={row.yearly} report={r} /></td></tr>;
      })}</tbody>
    </table></div>
  </Section>;
}

function Falls({ report: r }: { report: ResearchReport }) {
  const d = r.drawdowns;
  return <Section id="fres-falls" icon={<TrendingDown aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.falls.title')}>
    <p className="muted">{t('fresearch.falls.intro')}</p>
    {d.items.length === 0 ? <p className="muted">{t('fresearch.falls.none', { reason: d.unavailable ?? '-' })}</p> : <div className="cmTableWrap" tabIndex={0} role="group" aria-label={t('fresearch.falls.title')}><table className="cmTable finTable fresTable">
      <thead><tr><th scope="col">{t('fresearch.col.high')}</th><th scope="col">{t('fresearch.col.low')}</th><th scope="col">{t('fresearch.col.fall')}</th><th scope="col">{t('fresearch.col.back')}</th></tr></thead>
      <tbody>{d.items.map((f) => <tr key={f.peak_date}>
        <td dir="ltr">{f.peak_date} <small>({f.peak})</small></td><td dir="ltr">{f.trough_date} <small>({f.trough})</small></td>
        <td dir="ltr" className="negative">{percentText(f.fall_percent)}</td>
        <td>{f.recovered_date ? t('fresearch.falls.backIn', { date: f.recovered_date, months: f.months_to_recover ?? 0 }) : <strong>{t('fresearch.falls.notBack')}</strong>}</td>
      </tr>)}</tbody>
    </table></div>}
    <p className="muted fresWhy">{t('fresearch.source', { source: sourceName(r, d.source) })}</p>
  </Section>;
}

function FundFacts({ report: r }: { report: ResearchReport }) {
  const f = r.fund;
  return <Section id="fres-facts" icon={<BookOpenCheck aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.fund.title')}>
    <dl className="finFigures fresFigures">
      <div><dt>{t('fresearch.fund.issuer')}</dt><dd><FactView fact={f.issuer} report={r} /></dd></div>
      <div><dt>{t('fresearch.fund.holdings')}</dt><dd><FactView fact={f.holdings_count} report={r} /></dd></div>
      <div><dt>{t('fresearch.fund.netAssets')}</dt><dd><FactView fact={f.net_assets} report={r} /></dd></div>
      <div><dt>{t('fresearch.fund.expense')}</dt><dd><FactView fact={f.expense_ratio} report={r} /></dd></div>
    </dl>
    {f.top_holdings.items.length > 0 && <>
      <h3 className="finSubHead">{t('fresearch.fund.top')}</h3>
      <ol className="fresHoldings">{f.top_holdings.items.map((h) => <li key={h.name}><span dir="auto">{h.name}</span><span dir="ltr">{h.percent}%</span></li>)}</ol>
      <p className="muted fresWhy">{t('fresearch.sourceDate', { source: sourceName(r, f.top_holdings.source), date: f.top_holdings.as_of ?? '-' })}</p>
    </>}
    {f.url && <p><a href={f.url} target="_blank" rel="noreferrer noopener">{t('fresearch.fund.report')}<ExternalLink aria-hidden="true" className="cmInlineIcon" /></a></p>}
  </Section>;
}

function Dividends({ report: r }: { report: ResearchReport }) {
  const d = r.dividends;
  return <Section id="fres-div" icon={<Wallet aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.div.title')}>
    <dl className="finFigures fresFigures">
      <div><dt>{t('fresearch.div.history')}</dt><dd><FactView fact={d.fund_history} report={r} /></dd></div>
      <div><dt>{t('fresearch.div.yield')}</dt><dd><FactView fact={d.yield} report={r} /></dd></div>
    </dl>
    <h3 className="finSubHead">{t('fresearch.div.yours')}</h3>
    {d.yours.unavailable ? <p className="muted">{d.yours.unavailable}</p> : d.yours.items.length === 0 ? <p className="muted">{t('fresearch.div.noneYours')}</p>
      : <ul className="fresList">{d.yours.items.map((item, i) => <li key={`${item.date}-${i}`}><span dir="ltr">{item.date}</span>
        <span>{item.kind === 'withholding_tax' ? t('fresearch.div.tax') : t('fresearch.div.title')}</span><span dir="ltr">USD {item.amount}</span></li>)}</ul>}
    <p className="muted fresWhy">{d.withholding_note}</p>
  </Section>;
}

function Summary({ report: r, dailyLimit }: { report: ResearchReport; dailyLimit: number }) {
  const [summary, setSummary] = useState<ResearchSummary | null>(r.summary);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  async function write() {
    setBusy(true); setError(null);
    try { setSummary(await api.writeSummary(r.symbol)); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const s = summary?.status === 'ok' ? summary.summary : null;
  return <Section id="fres-ai" icon={<Sparkles aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.ai.title')}>
    <p className="muted">{t('fresearch.ai.intro')} {t('fresearch.ai.limit', { count: dailyLimit })}</p>
    {error && <Notice error>{error}</Notice>}
    {!summary && <p className="muted">{t('fresearch.ai.none')}</p>}
    {summary && summary.status === 'withheld' && <p className="notice errorNotice" role="status">{t(`fresearch.ai.withheld.${summary.reason}` as MessageKey, { numbers: summary.unknown_numbers.join(', ') })}</p>}
    {summary && summary.status === 'failed' && <p className="notice errorNotice" role="status">{t('fresearch.ai.failed')}</p>}
    {s && <div className="fresSummary">
      {!summary?.current && <p className="notice" role="status">{t('fresearch.ai.old')}</p>}
      <p className="fresSuggestion">{t('fresearch.ai.suggestion')}: <span className={`statusBadge ${s.suggestion === 'buy' ? 'success' : s.suggestion === 'avoid' ? 'danger' : 'warning'}`}>{t(`fresearch.ai.s.${s.suggestion}` as MessageKey)}</span></p>
      <div className="fresColumns">
        <div><h3 className="finSubHead">{t('fresearch.ai.good')}</h3><ul>{s.good.map((x) => <li key={x} dir="auto">{x}</li>)}</ul></div>
        <div><h3 className="finSubHead">{t('fresearch.ai.bad')}</h3><ul>{s.bad.map((x) => <li key={x} dir="auto">{x}</li>)}</ul></div>
      </div>
      <h3 className="finSubHead">{t('fresearch.ai.history')}</h3><p dir="auto">{s.history}</p>
      <h3 className="finSubHead">{t('fresearch.ai.range')}</h3><p dir="auto">{s.range}</p>
      <h3 className="finSubHead">{t('fresearch.ai.reasons')}</h3><ul>{s.reasons.map((x) => <li key={x} dir="auto">{x}</li>)}</ul>
      <p className="fresDisclaimer" role="note">{summary?.label}</p>
    </div>}
    <div className="actions"><button type="button" disabled={busy} onClick={() => void write()}><Sparkles aria-hidden="true" />{busy ? t('fresearch.ai.writing') : t('fresearch.ai.write')}</button></div>
  </Section>;
}

function Backtest({ report: r, symbols, onPlans }: { report: ResearchReport; symbols: ResearchSymbols; onPlans: () => void }) {
  const id = useId();
  const funds = symbols.symbols.map((s) => s.symbol);
  const [draft, setDraft] = useState<BacktestDraft>(() => ({
    rule: 'monthly', first: r.symbol, second: funds.find((f) => f !== r.symbol) ?? '', monthly: symbols.limits.max_order, total: symbols.limits.max_capital,
    perOrder: symbols.limits.max_order, years: 10, weight: 60, every: 12, dipPercent: 10, dipAmount: symbols.limits.max_order,
    perShare: '0.005', minimum: '1.00', maxPercent: '1',
  }));
  const [result, setResult] = useState<BacktestResult | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<BacktestDraft>) => setDraft((d) => ({ ...d, ...patch }));
  async function run(e: FormEvent) {
    e.preventDefault();
    const problem = checkDraft(draft, symbols.limits);
    if (problem) { setError(t(`fresearch.bt.err.${problem}` as MessageKey, { limit: problem === 'overTotal' ? symbols.limits.max_capital : symbols.limits.max_order })); return; }
    setBusy(true); setError(null);
    try { setResult(await api.runBacktest(toInput(draft))); } catch (err) { setError(describeError(err)); setResult(null); } finally { setBusy(false); }
  }
  const field = (key: keyof BacktestDraft, label: MessageKey, extra: Record<string, unknown> = {}) => <label className="field" htmlFor={`${id}-${key}`}>
    <span>{t(label)}</span><input id={`${id}-${key}`} dir="ltr" inputMode="decimal" value={String(draft[key])} {...extra}
      onChange={(e) => set({ [key]: typeof draft[key] === 'number' ? Number(e.target.value) : e.target.value } as Partial<BacktestDraft>)} /></label>;
  return <Section id="fres-bt" icon={<FlaskConical aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.bt.title')}>
    <p className="muted">{t('fresearch.bt.intro')}</p>
    <form className="finForm fresForm" onSubmit={(e) => void run(e)} aria-describedby={error ? `${id}-err` : undefined}>
      <div className="fresGrid">
        <label className="field" htmlFor={`${id}-rule`}><span>{t('fresearch.bt.rule')}</span>
          <select id={`${id}-rule`} value={draft.rule} onChange={(e) => set({ rule: e.target.value as BacktestDraft['rule'] })}>
            {RULES.map((rule) => <option key={rule} value={rule}>{t(`fresearch.bt.rule.${rule}` as MessageKey)}</option>)}</select></label>
        <label className="field" htmlFor={`${id}-first`}><span>{t('fresearch.bt.fund')}</span>
          <select id={`${id}-first`} value={draft.first} onChange={(e) => set({ first: e.target.value })} dir="ltr">{funds.map((f) => <option key={f}>{f}</option>)}</select></label>
        {draft.rule === 'rebalance' && <label className="field" htmlFor={`${id}-second`}><span>{t('fresearch.bt.second')}</span>
          <select id={`${id}-second`} value={draft.second} onChange={(e) => set({ second: e.target.value })} dir="ltr">{funds.map((f) => <option key={f}>{f}</option>)}</select></label>}
        {draft.rule !== 'buy_hold' && field('monthly', 'fresearch.bt.monthly')}
        {field('total', 'fresearch.bt.total')}
        {field('perOrder', 'fresearch.bt.perOrder')}
        {field('years', 'fresearch.bt.years', { type: 'number', min: 1, max: 10, inputMode: 'numeric' })}
        {draft.rule === 'rebalance' && field('weight', 'fresearch.bt.weight', { type: 'number', min: 10, max: 90, inputMode: 'numeric' })}
        {draft.rule === 'rebalance' && <label className="field" htmlFor={`${id}-every`}><span>{t('fresearch.bt.every')}</span>
          <select id={`${id}-every`} value={draft.every} onChange={(e) => set({ every: Number(e.target.value) as 3 | 6 | 12 })}>{[3, 6, 12].map((m) => <option key={m} value={m}>{m}</option>)}</select></label>}
        {draft.rule === 'dip' && field('dipPercent', 'fresearch.bt.dipPercent', { type: 'number', min: 2, max: 60, inputMode: 'numeric' })}
        {draft.rule === 'dip' && field('dipAmount', 'fresearch.bt.dipAmount')}
      </div>
      <fieldset className="fresCosts"><legend>{t('fresearch.bt.costs')}</legend><div className="fresGrid">
        {field('perShare', 'fresearch.bt.perShare')}{field('minimum', 'fresearch.bt.minimum')}{field('maxPercent', 'fresearch.bt.maxPercent')}
      </div></fieldset>
      {error && <p id={`${id}-err`} className="cmFieldError" role="alert">{error}</p>}
      <div className="actions"><button type="submit" disabled={busy}><FlaskConical aria-hidden="true" />{busy ? t('fresearch.bt.running') : t('fresearch.bt.run')}</button></div>
    </form>
    {result && <BacktestView result={result} symbols={symbols} draft={draft} onPlans={onPlans} />}
  </Section>;
}

function BacktestView({ result: b, symbols, draft, onPlans }: { result: BacktestResult; symbols: ResearchSymbols; draft: BacktestDraft; onPlans: () => void }) {
  if (!b.ok || !b.result) return <p className="notice errorNotice" role="status">{t('fresearch.bt.notEnough', { reason: b.reason ?? '-' })}</p>;
  const x = b.result, hold = b.simply_holding;
  const chart = chartPaths(x.timeline, 600, 160);
  const row = (label: MessageKey, mine: ReactNode, held: ReactNode) => <tr><th scope="row">{t(label)}</th><td dir="ltr">{mine}</td><td dir="ltr">{held}</td></tr>;
  return <div className="fresResult" aria-live="polite">
    <h3 className="finSubHead">{t('fresearch.bt.result', { from: b.from ?? '-', to: b.to ?? '-' })}</h3>
    <p className="fresDisclaimer" role="note">{b.warning}</p>
    <div className="cmTableWrap" tabIndex={0} role="group" aria-label={t('fresearch.bt.result', { from: b.from ?? '-', to: b.to ?? '-' })}><table className="cmTable finTable fresTable">
      <thead><tr><th scope="col"><span className="visuallyHidden">{t('fresearch.col.period')}</span></th><th scope="col">{t(`fresearch.bt.rule.${b.rule ?? 'monthly'}` as MessageKey)}</th><th scope="col">{t('fresearch.bt.hold')}</th></tr></thead>
      <tbody>
        {row('fresearch.bt.final', `USD ${groupDigits(x.final_value)}`, hold ? `USD ${groupDigits(hold.final_value)}` : '-')}
        {row('fresearch.bt.putIn', `USD ${groupDigits(x.put_in)}`, hold ? `USD ${groupDigits(hold.put_in)}` : '-')}
        {row('fresearch.bt.return', <span className={tone(x.total_return_percent)}>{percentText(x.total_return_percent) ?? '-'}</span>, hold ? <span className={tone(hold.total_return_percent)}>{percentText(hold.total_return_percent) ?? '-'}</span> : '-')}
        {row('fresearch.bt.worst', percentText(x.worst_fall_percent) ?? '-', hold ? percentText(hold.worst_fall_percent) ?? '-' : '-')}
        {row('fresearch.bt.below', t('fresearch.bt.belowValue', { count: x.months_below_put_in, months: x.months }), hold ? t('fresearch.bt.belowValue', { count: hold.months_below_put_in, months: x.months }) : '-')}
        {row('fresearch.bt.fees', `USD ${x.fees}`, hold ? `USD ${hold.fees}` : '-')}
      </tbody>
    </table></div>
    <dl className="finFigures fresFigures">
      <div><dt>{t('fresearch.bt.orders')}</dt><dd dir="ltr">{x.orders}</dd></div>
      <div><dt>{t('fresearch.bt.skipped')}</dt><dd dir="ltr">{x.skipped_buys}</dd></div>
    </dl>
    {chart.value && <figure className="fresChart">
      <svg viewBox="0 0 600 160" role="img" aria-label={t('fresearch.bt.chart')} preserveAspectRatio="none">
        <path d={chart.putIn} className="fresLinePut" /><path d={chart.value} className="fresLineValue" />
      </svg>
      <figcaption><span className="fresKey fresKeyValue">{t('fresearch.bt.chartValue')}</span><span className="fresKey fresKeyPut">{t('fresearch.bt.chartPutIn')}</span></figcaption>
    </figure>}
    {b.dividends_note && <p className="muted fresWhy">{b.dividends_note}</p>}
    <PaperRun result={b} symbols={symbols} draft={draft} onPlans={onPlans} />
  </div>;
}

function PaperRun({ result: b, symbols, draft, onPlans }: { result: BacktestResult; symbols: ResearchSymbols; draft: BacktestDraft; onPlans: () => void }) {
  const id = useId();
  const [day, setDay] = useState(5), [busy, setBusy] = useState(false), [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  async function create() {
    setBusy(true);
    try { await api.paperRun(draft.first, draft.monthly.trim(), day); setNote({ text: t('fresearch.bt.paper.done', { day }) }); onPlans(); }
    catch (e) { setNote({ text: describeError(e), error: true }); } finally { setBusy(false); }
  }
  return <div className="fresPaper">
    <h3 className="finSubHead">{t('fresearch.bt.paper.title')} <ModeBadge mode="paper" /></h3>
    <p className="muted">{t('fresearch.bt.paper.intro')}</p>
    {!b.paper_run?.available ? <p className="muted">{t('fresearch.bt.paper.unavailable', { reason: b.paper_run?.reason ?? '-' })}</p> : <>
      {note && <Notice error={note.error}>{note.text}</Notice>}
      <div className="fresInline">
        <label className="field" htmlFor={`${id}-day`}><span>{t('fresearch.bt.paper.day')}</span>
          <input id={`${id}-day`} type="number" min={1} max={28} value={day} dir="ltr" onChange={(e) => setDay(Math.min(28, Math.max(1, Number(e.target.value) || 1)))} /></label>
        <button type="button" disabled={busy || symbols.mode === 'live'} onClick={() => void create()}>{t('fresearch.bt.paper.create')}</button>
      </div>
    </>}
  </div>;
}

function News({ report: r }: { report: ResearchReport }) {
  const n = r.news;
  return <Section id="fres-news" icon={<Newspaper aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.news.title')}>
    <p className="muted">{t('fresearch.news.untrusted')}</p>
    {n.items.length === 0 ? <Empty icon={<Newspaper aria-hidden="true" />} title={t('fresearch.notAvailable')} text={n.unavailable ?? '-'} />
      : <ul className="fresList">{n.items.map((item) => <li key={item.url}>
        <a href={item.url} target="_blank" rel="noreferrer noopener nofollow" dir="auto">{item.title}</a>
        <small dir="ltr">{item.source}{item.published ? `, ${item.published}` : ''}</small></li>)}</ul>}
  </Section>;
}

function Sources({ report: r }: { report: ResearchReport }) {
  return <Section id="fres-sources" icon={<BookOpenCheck aria-hidden="true" className="cmInlineIcon" />} title={t('fresearch.sources.title')}>
    <ul className="fresList">{r.sources.map((s) => <li key={s.key}><span>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{sourceName(r, s.key)}</a> : sourceName(r, s.key)}</span>
      <span className={`statusBadge ${s.used ? 'success' : 'neutral'}`}>{t(s.used ? 'fresearch.sources.used' : 'fresearch.sources.notUsed')}</span></li>)}</ul>
    {r.source_problems.length > 0 && <>
      <h3 className="finSubHead">{t('fresearch.sources.problems')}</h3>
      <ul className="fresList">{r.source_problems.map((p) => <li key={p.source}><span>{sourceName(r, p.source)}</span><small dir="auto">{p.reason}</small></li>)}</ul>
    </>}
    <p className="fresDisclaimer" role="note">{r.disclaimer}</p>
  </Section>;
}
