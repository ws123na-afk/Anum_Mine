// Trading intelligence actions (docs/finance-trading-intel.md#api): instruments and bar imports, new
// strategy versions, lifecycle moves, evaluations, a Supervisor cycle and the emergency pause. Every
// change asks for a confirmation and every result is SIMULATED: nothing here buys, sells or approves
// an order. Orders a cycle proposes wait under Paper trading for the person's approval.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Database, FileUp, FlaskConical, PauseCircle, Play, Plus, Workflow } from 'lucide-react';
import type {
  FinanceIntelAssetClass, FinanceIntelBarsResult, FinanceIntelCycle, FinanceIntelInstrument, FinanceIntelPhase, FinanceIntelPortfolio, FinanceIntelRule,
  FinanceIntelVenue, FinanceStrategy, FinanceStrategyState,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import { PHASES, barsFileProblem, isWeightyMove, nextStates, phaseLabel, rulesByCandidate, stateLabel } from './lib/financeIntel';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { Empty, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';

const ASSET_CLASSES: readonly FinanceIntelAssetClass[] = ['equity', 'etf', 'crypto', 'commodity', 'forex'];
type Result = { text: string; error?: boolean } | null;

function useOnce<T>(load: () => Promise<T>): [Load<T>, () => Promise<void>] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setState({ kind: 'ready', value: await load() }); } catch (e) { setState(failed(e)); }
  }, [load]);
  useEffect(() => { void run(); }, [run]);
  return [state, run];
}

// -- market data and new strategies ----------------------------------------------------------------

/** Instruments with their stored bars, bar imports, and new strategy versions built on them. */
export function IntelWorkbench({ venues, onChanged }: { venues: FinanceIntelVenue[]; onChanged: () => void }) {
  const [instruments, reloadInstruments] = useOnce(api.listInstruments);
  const [rules, reloadRules] = useOnce(api.listIntelRules);
  const changed = () => { void reloadInstruments(); onChanged(); };
  return <>
    <MarketData venues={venues} instruments={instruments} retry={() => void reloadInstruments()} onChanged={changed} />
    <NewStrategy instruments={instruments} rules={rules} retry={() => { void reloadRules(); void reloadInstruments(); }} onCreated={onChanged} />
  </>;
}

function MarketData({ venues, instruments, retry, onChanged }: {
  venues: FinanceIntelVenue[]; instruments: Load<FinanceIntelInstrument[]>; retry: () => void; onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [result, setResult] = useState<Result>(null);
  return <section className="surface cmPanel" aria-labelledby="fin-intel-data">
    <div className="sectionHeader"><h2 id="fin-intel-data"><Database aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.data.title')}</h2>
      {!adding && instruments.kind === 'ready' && <button type="button" className="secondary" onClick={() => { setAdding(true); setResult(null); }}><Plus aria-hidden="true" />{t('finance.intel.data.add')}</button>}</div>
    <p className="muted finHelp">{t('finance.intel.data.help')}</p>
    {result && <Notice error={result.error}>{result.text}</Notice>}
    {adding && <InstrumentForm venues={venues} onDone={(text) => { setAdding(false); if (text) { setResult({ text }); onChanged(); } }} />}
    {instruments.kind === 'loading' && <Notice busy>{t('finance.intel.data.loading')}</Notice>}
    {(instruments.kind === 'error' || instruments.kind === 'denied') && <LoadError message={t('finance.intel.data.error', { message: instruments.message })} retry={retry} />}
    {instruments.kind === 'ready' && instruments.value.length === 0 && !adding && <Empty icon={<Database aria-hidden="true" />} title={t('finance.intel.data.none')}
      text={t('finance.intel.data.noneText')} action={<button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('finance.intel.data.add')}</button>} />}
    {instruments.kind === 'ready' && instruments.value.length > 0 && <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable finIntelCards" aria-label={t('finance.intel.data.table')}>
      <thead><tr><th scope="col">{t('finance.intel.symbol')}</th><th scope="col">{t('finance.intel.venue')}</th><th scope="col">{t('finance.intel.data.bars')}</th>
        <th scope="col">{t('finance.intel.data.source')}</th><th scope="col"><span className="visuallyHidden">{t('finance.intel.data.import')}</span></th></tr></thead>
      <tbody>{instruments.value.map((i) => <tr key={i.id}>
        <th scope="row"><span dir="ltr">{i.symbol}</span> <small className="muted" dir="ltr">{i.currency}</small> <small className="muted">{t(`finance.intel.class.${i.asset_class}` as MessageKey)}</small></th>
        <td data-label={t('finance.intel.venue')}><span dir="ltr">{i.venue}</span></td>
        <td data-label={t('finance.intel.data.bars')}>{i.bars === 0 ? <span className="statusBadge warning">{t('finance.intel.data.noBars')}</span>
          : t('finance.intel.data.barsRange', { count: i.bars, first: i.first_bar ?? '-', last: i.last_bar ?? '-' })}</td>
        <td data-label={t('finance.intel.data.source')}><small dir="auto">{i.last_source ?? '-'}</small></td>
        <td><ImportBars instrument={i} onImported={(r) => { setResult({ text: importText(r) }); onChanged(); }} onFailed={(text) => setResult({ text, error: true })} /></td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}

function importText(r: FinanceIntelBarsResult): string {
  const base = t('finance.intel.data.imported', { imported: r.imported, changed: r.changed });
  return r.issues.length ? `${base} ${t('finance.intel.data.issues', { count: r.issues.length, first: r.issues[0].message })}` : base;
}

function InstrumentForm({ venues, onDone }: { venues: FinanceIntelVenue[]; onDone: (message: string | null) => void }) {
  const [symbol, setSymbol] = useState(''), [assetClass, setAssetClass] = useState<FinanceIntelAssetClass>('etf');
  const [venue, setVenue] = useState(venues[0]?.code ?? 'XNYS'), [currency, setCurrency] = useState('USD'), [listed, setListed] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const symbolInput = useRef<HTMLInputElement>(null);
  useEffect(() => { symbolInput.current?.focus(); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!symbol.trim() || !currency.trim()) { setError(t('finance.intel.data.required')); symbolInput.current?.focus(); return; }
    setBusy(true); setError(null);
    try {
      const made = await api.createInstrument({ symbol: symbol.trim(), asset_class: assetClass, venue, currency: currency.trim(), listed_on: listed || null });
      onDone(t('finance.intel.data.added', { symbol: made.symbol }));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} noValidate aria-label={t('finance.intel.data.add')} aria-describedby={error ? errorId : undefined}>
    <div className="cmFilters">
      <label className="field"><span>{t('finance.intel.symbol')}</span><input ref={symbolInput} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase().slice(0, 20))} dir="ltr" aria-required="true" aria-invalid={Boolean(error) && !symbol.trim()} /></label>
      <label className="field"><span>{t('finance.intel.data.class')}</span><select value={assetClass} onChange={(e) => setAssetClass(e.target.value as FinanceIntelAssetClass)}>
        {ASSET_CLASSES.map((c) => <option key={c} value={c}>{t(`finance.intel.class.${c}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('finance.intel.venue')}</span><select value={venue} onChange={(e) => setVenue(e.target.value)}>
        {venues.map((v) => <option key={v.code} value={v.code}>{v.name}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 10))} dir="ltr" className="finCurrencyInput" aria-required="true" /></label>
      <label className="field"><span>{t('finance.intel.data.listed')}</span><input type="date" value={listed} onChange={(e) => setListed(e.target.value)} dir="ltr" /></label>
    </div>
    <p className="muted finHelp">{t('finance.intel.data.listedHelp')}</p>
    <FormError id={errorId} text={error} />
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('finance.saving') : t('finance.intel.data.add')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => onDone(null)}>{t('common.cancel')}</button></div>
  </form>;
}

function ImportBars({ instrument, onImported, onFailed }: { instrument: FinanceIntelInstrument; onImported: (r: FinanceIntelBarsResult) => void; onFailed: (text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  async function pick(file: File | undefined) {
    if (!file) return;
    const problem = barsFileProblem(file);
    if (problem) { onFailed(t(problem)); return; }
    setBusy(true);
    try {
      const payload = await file.text();
      onImported(await api.importBars(instrument.id, { provider: 'csv_import', payload, source: file.name.slice(0, 100) }));
    } catch (e) { onFailed(t('finance.intel.data.importFailed', { message: describeError(e) })); } finally { setBusy(false); }
  }
  return <span className="finIntelImport">
    <input ref={input} type="file" accept=".csv,.txt,text/csv" className="visuallyHidden" tabIndex={-1} aria-hidden="true" disabled={busy}
      onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = ''; }} />
    <button type="button" className="secondary" disabled={busy} aria-label={t('finance.intel.data.importFor', { symbol: instrument.symbol })}
      onClick={() => input.current?.click()}><FileUp aria-hidden="true" />{busy ? t('finance.intel.data.importing') : t('finance.intel.data.import')}</button>
  </span>;
}

function NewStrategy({ instruments, rules, retry, onCreated }: {
  instruments: Load<FinanceIntelInstrument[]>; rules: Load<FinanceIntelRule[]>; retry: () => void; onCreated: () => void;
}) {
  const [open, setOpen] = useState(false), [key, setKey] = useState(''), [chosen, setChosen] = useState<string[]>([]), [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [done, setDone] = useState<string | null>(null);
  const errorId = useId(), ruleId = useId();
  const ruleSelect = useRef<HTMLSelectElement>(null);
  useEffect(() => { if (open) ruleSelect.current?.focus(); }, [open]);
  const ready = instruments.kind === 'ready' && rules.kind === 'ready';
  const usable = instruments.kind === 'ready' ? instruments.value.filter((i) => i.bars > 0) : [];
  const rule = rules.kind === 'ready' ? rules.value.find((r) => r.key === key) ?? null : null;
  function check(event: FormEvent) {
    event.preventDefault();
    if (!rule) { setError(t('finance.intel.new.pickRule')); ruleSelect.current?.focus(); return; }
    if (chosen.length < rule.min_symbols || chosen.length > rule.max_symbols) {
      setError(t('finance.intel.new.symbolCount', { min: rule.min_symbols, max: rule.max_symbols })); return;
    }
    if (reason.trim().length < 3) { setError(t('finance.intel.new.reasonRequired')); return; }
    setError(null); setConfirming(true);
  }
  async function create() {
    if (!rule) return;
    setBusy(true);
    try {
      const made = await api.createStrategy({ key: rule.key, instrument_ids: chosen, reason: reason.trim() });
      setDone(t('finance.intel.new.created', { name: made.name, version: made.version }));
      setOpen(false); setKey(''); setChosen([]); setReason(''); onCreated();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); setConfirming(false); }
  }
  return <section className="surface cmPanel" aria-labelledby="fin-intel-new">
    <div className="sectionHeader"><h2 id="fin-intel-new"><FlaskConical aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.new.title')}</h2>
      {!open && ready && usable.length > 0 && <button type="button" onClick={() => { setOpen(true); setDone(null); }}><Plus aria-hidden="true" />{t('finance.intel.new.open')}</button>}</div>
    <p className="muted finHelp">{t('finance.intel.new.help')}</p>
    {done && <Notice>{done}</Notice>}
    {!ready && (instruments.kind === 'loading' || rules.kind === 'loading') && <Notice busy>{t('finance.intel.new.loading')}</Notice>}
    {(rules.kind === 'error' || rules.kind === 'denied') && <LoadError message={t('finance.intel.new.error', { message: rules.message })} retry={retry} />}
    {ready && usable.length === 0 && <p className="muted">{t('finance.intel.new.needBars')}</p>}
    {open && rules.kind === 'ready' && <form className="finForm" onSubmit={check} noValidate aria-label={t('finance.intel.new.title')} aria-describedby={error ? errorId : undefined}>
      <label className="field" htmlFor={ruleId}><span>{t('finance.intel.new.rule')}</span>
        <select ref={ruleSelect} id={ruleId} value={key} onChange={(e) => { setKey(e.target.value); setError(null); }} aria-required="true">
          <option value="">{t('finance.intel.new.chooseRule')}</option>
          {rulesByCandidate(rules.value).map((g) => <optgroup key={g.candidate} label={g.candidate === 0 ? t('finance.intel.new.benchmark') : `${g.candidate}. ${g.name}`}>
            {g.rules.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</optgroup>)}
        </select></label>
      {rule && <dl className="finIntelRule">
        <div><dt>{t('finance.intel.new.hypothesis')}</dt><dd dir="auto">{rule.hypothesis}</dd></div>
        <div><dt>{t('finance.intel.new.entry')}</dt><dd dir="auto">{rule.entry}</dd></div>
        <div><dt>{t('finance.intel.new.exit')}</dt><dd dir="auto">{rule.exit}</dd></div>
        <div><dt>{t('finance.intel.new.suits')}</dt><dd dir="auto">{rule.instruments}. {rule.timeframe}.</dd></div>
      </dl>}
      <fieldset className="finIntelPick finChecks"><legend>{t('finance.intel.new.instruments')}</legend>
        {usable.map((i) => <label key={i.id} className="finCheck"><input type="checkbox" checked={chosen.includes(i.id)}
          onChange={(e) => setChosen(e.target.checked ? [...chosen, i.id] : chosen.filter((x) => x !== i.id))} />
          <span dir="ltr">{i.symbol}</span> <small className="muted">{t('finance.intel.data.barsRange', { count: i.bars, first: i.first_bar ?? '-', last: i.last_bar ?? '-' })}</small></label>)}
      </fieldset>
      <label className="field"><span>{t('finance.intel.new.reason')}</span><input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} dir="auto" aria-required="true" /></label>
      <FormError id={errorId} text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{t('finance.intel.new.create')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => { setOpen(false); setError(null); }}>{t('common.cancel')}</button></div>
    </form>}
    {confirming && rule && <ConfirmDialog title={t('finance.intel.new.confirmTitle')} confirmLabel={t('finance.intel.new.create')} busy={busy}
      onConfirm={() => void create()} onCancel={() => setConfirming(false)}>
      <p>{t('finance.intel.new.confirmText', { name: rule.name, count: chosen.length })}</p></ConfirmDialog>}
  </section>;
}

// -- one strategy: evaluations, its paper portfolio and lifecycle moves -------------------------------

export function StrategyActions({ strategy, portfolios, onChanged }: { strategy: FinanceStrategy; portfolios: FinanceIntelPortfolio[]; onChanged: (s?: FinanceStrategy) => void }) {
  const moves = nextStates(strategy.state);
  const paper = portfolios.filter((p) => p.mode === 'paper');
  const [target, setTarget] = useState<FinanceStrategyState | ''>(''), [reason, setReason] = useState(''), [portfolio, setPortfolio] = useState(strategy.portfolio_id ?? '');
  const [confirming, setConfirming] = useState<'move' | 'backtest' | 'walk_forward' | null>(null), [busy, setBusy] = useState(false), [result, setResult] = useState<Result>(null);
  const moveId = useId(), portfolioId = useId(), reasonId = useId();
  const canChoosePortfolio = strategy.state !== 'PAPER_ACTIVE' && strategy.state !== 'RETIRED';
  async function act(work: () => Promise<string>) {
    setBusy(true);
    try { setResult({ text: await work() }); } catch (e) { setResult({ text: describeError(e), error: true }); } finally { setBusy(false); setConfirming(null); }
  }
  const move = () => act(async () => {
    if (!target) return '';
    const moved = await api.moveStrategy(strategy.id, target, reason.trim());
    setTarget(''); setReason(''); onChanged(moved);
    return t('finance.intel.act.moved', { state: stateLabel(moved.state) });
  });
  const evaluate = (kind: 'backtest' | 'walk_forward') => act(async () => {
    const run = await api.evaluateStrategy(strategy.id, kind);
    onChanged();
    return t(kind === 'backtest' ? 'finance.intel.act.backtested' : 'finance.intel.act.validated', { verdict: t(`finance.intel.verdict.${run.verdict}` as MessageKey) });
  });
  const choose = () => act(async () => {
    const updated = await api.chooseStrategyPortfolio(strategy.id, portfolio);
    onChanged(updated);
    return t('finance.intel.act.portfolioChosen', { name: paper.find((p) => p.id === portfolio)?.name ?? '' });
  });
  return <section className="finIntelStrategyActions" aria-label={t('finance.intel.act.title', { name: strategy.name })}>
    <h3>{t('finance.intel.act.title', { name: strategy.name })}</h3>
    {result && <Notice error={result.error}>{result.text}</Notice>}
    {strategy.state !== 'RETIRED' && <div className="actions finIntelActions">
      <button type="button" className="secondary" disabled={busy} onClick={() => setConfirming('backtest')}><Play aria-hidden="true" />{t('finance.intel.act.backtest')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => setConfirming('walk_forward')}><Workflow aria-hidden="true" />{t('finance.intel.act.walkForward')}</button>
    </div>}
    {canChoosePortfolio && (paper.length === 0
      ? <p className="muted">{t('finance.intel.act.noPaper')}</p>
      : <div className="cmFilters">
        <label className="field" htmlFor={portfolioId}><span>{t('finance.intel.act.portfolio')}</span>
          <select id={portfolioId} value={portfolio} onChange={(e) => setPortfolio(e.target.value)}>
            <option value="">{t('finance.intel.act.choosePortfolio')}</option>{paper.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <button type="button" className="secondary" disabled={busy || !portfolio || portfolio === strategy.portfolio_id} onClick={() => void choose()}>{t('finance.intel.act.usePortfolio')}</button>
      </div>)}
    {moves.length === 0 ? <p className="muted">{t('finance.intel.act.retired')}</p> : <form className="cmFilters" onSubmit={(e) => { e.preventDefault(); if (target && reason.trim().length >= 3) setConfirming('move'); else setResult({ text: t('finance.intel.act.moveRequired'), error: true }); }}>
      <label className="field" htmlFor={moveId}><span>{t('finance.intel.act.moveTo')}</span>
        <select id={moveId} value={target} onChange={(e) => setTarget(e.target.value as FinanceStrategyState | '')}>
          <option value="">{t('finance.intel.act.chooseState')}</option>{moves.map((s) => <option key={s} value={s}>{stateLabel(s)}</option>)}</select></label>
      <label className="field" htmlFor={reasonId}><span>{t('finance.intel.new.reason')}</span><input id={reasonId} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} dir="auto" /></label>
      <button type="submit" disabled={busy}>{t('finance.intel.act.move')}</button>
    </form>}
    <p className="muted finHelp">{t('finance.intel.act.help')}</p>
    {confirming === 'move' && target && <ConfirmDialog title={t('finance.intel.act.confirmMove', { state: stateLabel(target) })} confirmLabel={t('finance.intel.act.move')}
      danger={target === 'RETIRED'} busy={busy} onConfirm={() => void move()} onCancel={() => setConfirming(null)}>
      <p>{t(isWeightyMove(target) ? (target === 'RETIRED' ? 'finance.intel.act.confirmRetire' : 'finance.intel.act.confirmActive') : 'finance.intel.act.confirmMoveText',
        { name: strategy.name, version: strategy.version, state: stateLabel(target) })}</p></ConfirmDialog>}
    {(confirming === 'backtest' || confirming === 'walk_forward') && <ConfirmDialog title={t(confirming === 'backtest' ? 'finance.intel.act.confirmBacktest' : 'finance.intel.act.confirmWalkForward')}
      confirmLabel={t(confirming === 'backtest' ? 'finance.intel.act.backtest' : 'finance.intel.act.walkForward')} busy={busy}
      onConfirm={() => void evaluate(confirming)} onCancel={() => setConfirming(null)}><p>{t('finance.intel.act.evaluateText')}</p></ConfirmDialog>}
  </section>;
}

// -- the Supervisor ----------------------------------------------------------------------------------

export function SupervisorPanel({ activeCount, onRan }: { activeCount: number; onRan: () => void }) {
  const [phase, setPhase] = useState<FinanceIntelPhase>('pre_open');
  const [confirming, setConfirming] = useState<'run' | 'pause' | null>(null), [busy, setBusy] = useState(false);
  const [cycle, setCycle] = useState<FinanceIntelCycle | null>(null), [result, setResult] = useState<Result>(null);
  const phaseId = useId();
  async function run() {
    setBusy(true); setResult(null);
    try { setCycle(await api.runSupervisor(phase)); onRan(); } catch (e) { setResult({ text: describeError(e), error: true }); } finally { setBusy(false); setConfirming(null); }
  }
  async function pause() {
    setBusy(true); setResult(null);
    try {
      const done = await api.pauseAllStrategies(t('finance.intel.sup.pauseReason'));
      setResult({ text: t('finance.intel.sup.paused', { count: done.paused.length }) }); onRan();
    } catch (e) { setResult({ text: describeError(e), error: true }); } finally { setBusy(false); setConfirming(null); }
  }
  return <section className="surface cmPanel" aria-labelledby="fin-intel-sup">
    <div className="sectionHeader"><h2 id="fin-intel-sup"><Workflow aria-hidden="true" className="cmInlineIcon" />{t('finance.intel.sup.title')}</h2>
      <span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span></div>
    <p className="muted finHelp">{t('finance.intel.sup.help')}</p>
    {result && <Notice error={result.error}>{result.text}</Notice>}
    {cycle && <div className="notice" role="status">{cycle.skipped ? t('finance.intel.sup.skipped', { phase: phaseLabel(cycle.phase) })
      : t('finance.intel.sup.ran', { phase: phaseLabel(cycle.phase), active: cycle.active_strategies ?? 0, proposed: cycle.proposed_orders ?? 0,
        refused: cycle.refused_signals ?? 0, suspended: cycle.suspended ?? 0, decisions: cycle.decisions })}
      {!cycle.skipped && (cycle.halted ?? 0) > 0 && <> {t('finance.intel.sup.halted')}</>}</div>}
    <div className="cmFilters">
      <label className="field" htmlFor={phaseId}><span>{t('finance.intel.sup.phase')}</span>
        <select id={phaseId} value={phase} onChange={(e) => setPhase(e.target.value as FinanceIntelPhase)}>{PHASES.map((p) => <option key={p} value={p}>{phaseLabel(p)}</option>)}</select></label>
      <button type="button" disabled={busy} onClick={() => setConfirming('run')}><Play aria-hidden="true" />{t('finance.intel.sup.run')}</button>
      <button type="button" className="danger" disabled={busy || activeCount === 0} onClick={() => setConfirming('pause')}><PauseCircle aria-hidden="true" />{t('finance.intel.sup.pause')}</button>
    </div>
    {activeCount === 0 && <p className="muted finHelp">{t('finance.intel.sup.noneActive')}</p>}
    {confirming === 'run' && <ConfirmDialog title={t('finance.intel.sup.confirmRun', { phase: phaseLabel(phase) })} confirmLabel={t('finance.intel.sup.run')} busy={busy}
      onConfirm={() => void run()} onCancel={() => setConfirming(null)}><p>{t('finance.intel.sup.confirmRunText')}</p></ConfirmDialog>}
    {confirming === 'pause' && <ConfirmDialog title={t('finance.intel.sup.confirmPause')} confirmLabel={t('finance.intel.sup.pause')} danger busy={busy}
      onConfirm={() => void pause()} onCancel={() => setConfirming(null)}><p>{t('finance.intel.sup.confirmPauseText', { count: activeCount })}</p></ConfirmDialog>}
  </section>;
}
