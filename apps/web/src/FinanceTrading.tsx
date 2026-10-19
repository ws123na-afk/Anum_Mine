// Finance stages 6 to 8 (docs/finance.md): paper and shadow trading (SIMULATED, against prices the
// person recorded), the execution gateway (every request checked by the Guardian rules and run only
// after the person approves the code shown, once) and limited live, which is OFF: there is no live
// executor, so live requests are refused. Nothing on these pages buys, sells, pays or sends anything.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import {
  Ban, ChartLine, ChevronLeft, ChevronRight, CircleCheck, CircleX, FlaskConical, Hourglass, ListChecks, Lock, OctagonX, Plus, Power, RefreshCw, RotateCcw, Settings2,
  ShieldAlert, ShieldCheck, Tag, Trash2, X,
} from 'lucide-react';
import type {
  FinanceAmountLimit, FinanceExecutionStatus, FinanceIntent, FinanceIntentStatus, FinanceLive, FinancePaperOrder, FinancePaperOrderStatus,
  FinancePortfolio, FinancePortfolioSummary, FinancePrice,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import { expiresLabel, formatAmount, formatAmounts, isolate, parseAmount, rangeLabel, shortHash, type FinanceTab } from './lib/finance';
import {
  assumptionsText, checkBps, checkOrder, checkPrice, checkSymbol, codeText, draftFromLimits, formatDecimal, intentLine, intentTone,
  limitsFromDraft, newIdempotencyKey, orderTone, type LiveDraft, type OrderDraft,
} from './lib/financeTrading';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { reasonText } from './FinancePlan';
import { WiderPanel } from './FinanceWider';

const PAGE = 20;

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => Promise<void>] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setState({ kind: 'ready', value: await load() }); } catch (e) { setState(failed(e)); }
  }, [load]);
  useEffect(() => { void run(); }, [run]);
  return [state, run];
}

/** Every simulated figure says so, with what it assumes and that it promises nothing. */
function SimulatedNote({ assumptions }: { assumptions?: FinancePortfolio['assumptions'] }) {
  return <p className="notice finSimNote" role="note"><FlaskConical aria-hidden="true" />
    <span><strong>{t('finance.trading.simulated')}</strong> {t('finance.trading.simulatedText')}{assumptions ? ` ${assumptionsText(assumptions)}` : ''}</span></p>;
}

function SimBadge() {
  return <span className="statusBadge neutral finSimBadge">{t('finance.trading.simulated')}</span>;
}

const statusLabel = (status: FinanceIntentStatus) => t(`finance.trading.status.${status}` as MessageKey);
const orderStatusLabel = (status: FinancePaperOrderStatus) => t(`finance.trading.order.${status}` as MessageKey);

// ---- Stage 6: the paper trading tab ---------------------------------------------------------------------

export function TradingPanel({ base, onOpen }: { base: string; onOpen: (tab: FinanceTab) => void }) {
  const [portfolios, reload] = useLoad(api.listPortfolios);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [pendingKey, setPendingKey] = useState(0);
  const refresh = () => { void reload(); setPendingKey((k) => k + 1); };
  if (portfolios.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('finance.trading.loading')}</Notice></section>;
  if (portfolios.kind === 'denied') return <Denied />;
  if (portfolios.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('finance.trading.error.load', { message: portfolios.message })} retry={() => void reload()} /></section>;
  const list = portfolios.value;
  const current = list.find((p) => p.id === selected) ?? list[0];
  return <>
    <section className="surface cmPanel" aria-labelledby="fin-trading">
      <div className="sectionHeader"><h2 id="fin-trading"><ChartLine aria-hidden="true" className="cmInlineIcon" />{t('finance.tab.trading')}</h2>
        <div className="finHeadActions"><button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('finance.trading.newPortfolio')}</button></div></div>
      {list.length === 0 && <SimulatedNote />}
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {list.length === 0
        ? <Empty icon={<FlaskConical aria-hidden="true" />} title={t('finance.trading.empty')} text={t('finance.trading.emptyText')}
          action={<button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('finance.trading.newPortfolio')}</button>} />
        : <ul className="finPortfolios" aria-label={t('finance.trading.portfolios')}>{list.map((p) => <li key={p.id}><PortfolioCard portfolio={p} active={p.id === current?.id} onPick={() => setSelected(p.id)} /></li>)}</ul>}
    </section>
    <PendingRequests key={pendingKey} modes={['paper', 'shadow']} onChanged={() => void reload()} />
    {current && <PortfolioDetail key={current.id} portfolioId={current.id} base={base} onChanged={refresh}
      onDeleted={() => { setSelected(null); setNotice({ text: t('finance.trading.deleted') }); void reload(); }} />}
    <PricesPanel onChanged={() => void reload()} onOpen={onOpen} />
    {creating && <PortfolioDialog base={base} onClose={(made) => { setCreating(false); if (made) { setSelected(made.id); setNotice({ text: t('finance.trading.created') }); void reload(); } }} />}
  </>;
}

function PortfolioCard({ portfolio: p, active, onPick }: { portfolio: FinancePortfolioSummary; active: boolean; onPick: () => void }) {
  return <button type="button" className={`finPortfolio ${active ? 'active' : ''}`} aria-pressed={active} onClick={onPick}>
    <span className="finPortfolioHead"><strong dir="auto">{p.name}</strong><span className="statusBadge neutral">{t(`finance.trading.mode.${p.mode}` as MessageKey)}</span></span>
    <small>{p.mode === 'paper' ? t('finance.trading.cashLine', { cash: isolate(formatAmounts(p.cash)) }) : t('finance.trading.shadowLine')}</small>
    <small>{t('finance.trading.positionsCount', { count: p.positions })}{p.waiting ? ` · ${t('finance.trading.waitingCount', { count: p.waiting })}` : ''}</small>
  </button>;
}

function PortfolioDialog({ base, onClose }: { base: string; onClose: (made: FinancePortfolio | null) => void }) {
  const [name, setName] = useState(''), [mode, setMode] = useState<'paper' | 'shadow'>('paper');
  const [currency, setCurrency] = useState(base), [cash, setCash] = useState('10000');
  const [fee, setFee] = useState('10'), [feeMin, setFeeMin] = useState('0'), [slip, setSlip] = useState('5'), [age, setAge] = useState('3');
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.trading.error.name')); return; }
    const feeOk = checkBps(fee), slipOk = checkBps(slip);
    if (!feeOk.ok) { setError(feeOk.error); return; }
    if (!slipOk.ok) { setError(slipOk.error); return; }
    const min = parseAmount(feeMin || '0', 'AED', { signed: false });
    if (!min.ok) { setError(min.error); return; }
    const days = Number(age);
    if (!Number.isInteger(days) || days < 1 || days > 30) { setError(t('finance.trading.error.age')); return; }
    let starting: { currency: string; amount: string }[] = [];
    if (mode === 'paper') {
      const code = currency.trim().toUpperCase();
      if (!/^[A-Z]{3}$/.test(code)) { setError(t('finance.error.currency')); return; }
      const amount = parseAmount(cash, code, { signed: false, allowZero: false });
      if (!amount.ok) { setError(amount.error); return; }
      starting = [{ currency: code, amount: amount.value }];
    }
    setBusy(true); setError(null);
    try { onClose(await api.createPortfolio({ name: name.trim(), mode, starting_cash: starting, fee_bps: feeOk.value, fee_min: min.value, slippage_bps: slipOk.value, max_price_age_days: days })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('finance.trading.newPortfolio')} className="formDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} dir="auto" required data-autofocus /></label>
      <fieldset className="finMapping"><legend>{t('finance.trading.modeLegend')}</legend>
        <label className="finCheck"><input type="radio" name="mode" checked={mode === 'paper'} onChange={() => setMode('paper')} />{t('finance.trading.modePaper')}</label>
        <label className="finCheck"><input type="radio" name="mode" checked={mode === 'shadow'} onChange={() => setMode('shadow')} />{t('finance.trading.modeShadow')}</label>
      </fieldset>
      {mode === 'paper' && <div className="cmFormGrid">
        <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" className="finCurrencyInput" /></label>
        <label className="field"><span>{t('finance.trading.startingCash')}</span><input value={cash} onChange={(e) => setCash(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      </div>}
      <fieldset className="finMapping"><legend>{t('finance.trading.costs')}</legend>
        <p className="muted finHelp">{t('finance.trading.costsHelp')}</p>
        <div className="cmFormGrid">
          <label className="field"><span>{t('finance.trading.feeBps')}</span><input value={fee} onChange={(e) => setFee(e.target.value)} inputMode="decimal" dir="ltr" /></label>
          <label className="field"><span>{t('finance.trading.feeMin')}</span><input value={feeMin} onChange={(e) => setFeeMin(e.target.value)} inputMode="decimal" dir="ltr" /></label>
          <label className="field"><span>{t('finance.trading.slippageBps')}</span><input value={slip} onChange={(e) => setSlip(e.target.value)} inputMode="decimal" dir="ltr" /></label>
          <label className="field"><span>{t('finance.trading.priceAge')}</span><input type="number" min={1} max={30} value={age} onChange={(e) => setAge(e.target.value)} dir="ltr" /></label>
        </div>
      </fieldset>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} label={t('finance.trading.create')} />
    </form>
  </Modal>;
}

function PortfolioDetail({ portfolioId, base, onChanged, onDeleted }: { portfolioId: string; base: string; onChanged: () => void; onDeleted: () => void }) {
  const load = useCallback(() => api.getPortfolio(portfolioId), [portfolioId]);
  const [state, reload] = useLoad(load);
  const [ordersKey, setOrdersKey] = useState(0);
  const [confirm, setConfirm] = useState<'reset' | 'delete' | null>(null), [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [review, setReview] = useState<FinanceIntent | null>(null);
  const changed = () => { void reload(); setOrdersKey((k) => k + 1); onChanged(); };
  if (state.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('finance.trading.loading')}</Notice></section>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('finance.trading.error.portfolio', { message: state.message })} retry={() => void reload()} /></section>;
  const p = state.value;
  async function act() {
    setBusy(true);
    try {
      if (confirm === 'delete') { await api.deletePortfolio(p.id); onDeleted(); return; }
      await api.resetPortfolio(p.id); setNotice({ text: t('finance.trading.resetDone') }); changed();
    } catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(false); setConfirm(null); }
  }
  async function check() {
    try { const next = await api.checkWaiting(p.id); setNotice({ text: t('finance.trading.checked', { count: next.waiting }) }); changed(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); }
  }
  const headingId = `fin-portfolio-${p.id}`;
  return <section className="surface cmPanel" aria-labelledby={headingId}>
    <div className="sectionHeader"><h2 id={headingId}><span dir="auto">{p.name}</span> <SimBadge /></h2>
      <div className="finHeadActions">
        {p.waiting > 0 && <button type="button" className="secondary" onClick={() => void check()}><RefreshCw aria-hidden="true" />{t('finance.trading.check')}</button>}
        <button type="button" className="secondary" onClick={() => setEditing(true)}><Settings2 aria-hidden="true" />{t('finance.trading.assumptionsButton')}</button>
        <button type="button" className="secondary" onClick={() => setConfirm('reset')}><RotateCcw aria-hidden="true" />{t('finance.trading.reset')}</button>
        <button type="button" className="secondary" onClick={() => setConfirm('delete')} aria-label={t('finance.trading.deleteNamed', { name: p.name })}><Trash2 aria-hidden="true" />{t('finance.delete')}</button>
      </div></div>
    <SimulatedNote assumptions={p.assumptions} />
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <dl className="finFigures">
      {p.mode === 'paper' && <div><dt>{t('finance.trading.cash')}</dt><dd>{formatAmounts(p.cash)}</dd></div>}
      <div><dt>{t('finance.trading.realised')}</dt><dd>{p.realised.length ? p.realised.map((r) => <Money key={r.currency} value={r.amount} currency={r.currency} signed />) : '-'}</dd></div>
      <div><dt>{t('finance.trading.unrealised')}</dt><dd>{p.unrealised.length ? p.unrealised.map((r) => <Money key={r.currency} value={r.amount} currency={r.currency} signed />) : '-'}</dd></div>
      <div><dt>{t('finance.trading.costsPaid')}</dt><dd>{p.fees.length ? t('finance.trading.costsPaidValue', { fees: isolate(formatAmounts(p.fees)), slippage: isolate(formatAmounts(p.slippage)) }) : '-'}</dd></div>
    </dl>
    {p.unpriced.length > 0 && <p className="notice" role="status"><Hourglass aria-hidden="true" />{t('finance.trading.unpriced', { symbols: isolate(p.unpriced.join(', ')), days: p.assumptions.max_price_age_days })}</p>}
    <Holdings portfolio={p} />
    {p.mode === 'shadow' && <ShadowCompare portfolio={p} />}
    <OrderForm portfolio={p} base={base} onCreated={(intent) => setReview(intent)} />
    <OrderHistory key={ordersKey} portfolio={p} onChanged={(text) => { setNotice({ text }); changed(); }} />
    {review && <ReviewIntent intent={review} onClose={(text, error) => { setReview(null); if (text) setNotice({ text, error }); changed(); }} />}
    {editing && <AssumptionsDialog portfolio={p} onClose={(saved) => { setEditing(false); if (saved) { setNotice({ text: t('finance.trading.assumptionsSaved') }); changed(); } }} />}
    {confirm && <ConfirmDialog title={t(confirm === 'reset' ? 'finance.trading.resetTitle' : 'finance.trading.deleteTitle')} confirmLabel={t(confirm === 'reset' ? 'finance.trading.reset' : 'finance.delete')}
      danger busy={busy} onConfirm={() => void act()} onCancel={() => setConfirm(null)}>
      <p>{t(confirm === 'reset' ? 'finance.trading.resetText' : 'finance.trading.deleteText', { name: p.name })}</p>
    </ConfirmDialog>}
  </section>;
}

function Holdings({ portfolio: p }: { portfolio: FinancePortfolio }) {
  const id = useId();
  return <section aria-labelledby={id} className="finSub">
    <h3 id={id}>{t('finance.trading.holdings')}</h3>
    {p.holdings.length === 0 ? <p className="muted">{t('finance.trading.noHoldings')}</p> : <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable finTradeTable">
      <caption className="visuallyHidden">{t('finance.trading.holdingsCaption', { name: p.name })}</caption>
      <thead><tr><th scope="col">{t('finance.trading.col.symbol')}</th><th scope="col" className="num">{t('finance.trading.col.quantity')}</th><th scope="col" className="num">{t('finance.trading.col.cost')}</th>
        <th scope="col" className="num">{t('finance.trading.col.price')}</th><th scope="col" className="num">{t('finance.trading.col.value')}</th><th scope="col" className="num">{t('finance.trading.unrealised')}</th></tr></thead>
      <tbody>{p.holdings.map((h) => <tr key={`${h.instrument_kind}-${h.symbol}-${h.currency}`}>
        <th scope="row"><bdi dir="ltr">{h.symbol}</bdi><small>{t(`finance.trading.kind.${h.instrument_kind}` as MessageKey)} · {h.currency}</small></th>
        <td className="num" data-label={t('finance.trading.col.quantity')}><span dir="ltr">{formatDecimal(h.quantity, 8)}</span></td>
        <td className="num" data-label={t('finance.trading.col.cost')}><Money value={h.cost} currency={h.currency} /></td>
        <td className="num" data-label={t('finance.trading.col.price')}>{h.price ? <><span dir="ltr">{formatDecimal(h.price)}</span><small>{t('finance.trading.priceOn', { date: isolate(h.price_as_of ?? '') })}</small></> : <span className="muted">{t('finance.trading.noPrice')}</span>}</td>
        <td className="num" data-label={t('finance.trading.col.value')}>{h.market_value ? <Money value={h.market_value} currency={h.currency} /> : '-'}</td>
        <td className="num" data-label={t('finance.trading.unrealised')}>{h.unrealised ? <Money value={h.unrealised} currency={h.currency} signed /> : '-'}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}

function ShadowCompare({ portfolio: p }: { portfolio: FinancePortfolio }) {
  const id = useId();
  return <section aria-labelledby={id} className="finSub">
    <h3 id={id}>{t('finance.trading.shadowTitle')}</h3>
    <p className="muted finHelp">{t('finance.trading.shadowHelp')}</p>
    {p.shadow.length === 0 ? <p className="muted">{t('finance.trading.shadowNone')}</p> : <>
      {p.shadow_totals.length > 0 && <p className="finSafeFigure"><strong>{p.shadow_totals.map((r) => <Money key={r.currency} value={r.amount} currency={r.currency} signed />)}</strong>
        <span className="muted">{t('finance.trading.shadowTotal')}</span></p>}
      <ul className="cmList">{p.shadow.map((e) => <li key={e.order_id}><div>
        <strong>{intentLine({ kind: 'trade', side: e.side, quantity: e.quantity, symbol: e.symbol, order_type: 'market', limit_price: null, currency: e.currency })}</strong>
        <small>{t('finance.trading.shadowEntry', { price: isolate(formatDecimal(e.fill_price)), date: formatDateTime(e.filled_at), days: e.days })}</small></div>
        <span>{e.result ? <Money value={e.result} currency={e.currency} signed /> : <span className="muted">{t('finance.trading.noPrice')}</span>}</span></li>)}</ul>
    </>}
  </section>;
}

function OrderForm({ portfolio: p, base, onCreated }: { portfolio: FinancePortfolio; base: string; onCreated: (intent: FinanceIntent) => void }) {
  const [draft, setDraft] = useState<OrderDraft>({ kind: 'security', symbol: '', currency: p.starting_cash[0]?.currency ?? base, side: 'buy', type: 'market', quantity: '', limit: '' });
  const [key, setKey] = useState(newIdempotencyKey);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const id = useId(), errorId = useId();
  const set = <K extends keyof OrderDraft>(k: K, v: OrderDraft[K]) => { setDraft((d) => ({ ...d, [k]: v })); setKey(newIdempotencyKey()); };
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = checkOrder(draft);
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    try { onCreated(await api.createIntent({ mode: p.mode, portfolio_id: p.id, ...checked.value, idempotency_key: key })); setKey(newIdempotencyKey()); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <section aria-labelledby={id} className="finSub">
    <h3 id={id}>{t('finance.trading.newOrder')}</h3>
    <p className="muted finHelp">{t('finance.trading.newOrderHelp')}</p>
    <form className="cmFilters finOrderForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <label className="cmInline"><span>{t('finance.trading.side')}</span><select value={draft.side} onChange={(e) => set('side', e.target.value as OrderDraft['side'])}>
        <option value="buy">{t('finance.trading.buy')}</option><option value="sell">{t('finance.trading.sell')}</option></select></label>
      <label className="cmInline"><span>{t('finance.trading.instrument')}</span><select value={draft.kind} onChange={(e) => set('kind', e.target.value as OrderDraft['kind'])}>
        <option value="security">{t('finance.trading.kind.security')}</option><option value="fx">{t('finance.trading.kind.fx')}</option></select></label>
      <label className="cmInline"><span>{draft.kind === 'fx' ? t('finance.trading.currencyToBuy') : t('finance.trading.col.symbol')}</span>
        <input value={draft.symbol} onChange={(e) => set('symbol', e.target.value.toUpperCase().slice(0, 20))} dir="ltr" maxLength={20} placeholder={draft.kind === 'fx' ? 'USD' : 'EXMPL'} className="finSymbolInput" /></label>
      <label className="cmInline"><span>{t('finance.trading.payIn')}</span><input value={draft.currency} onChange={(e) => set('currency', e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" className="finCurrencyInput" /></label>
      <label className="cmInline"><span>{t('finance.trading.col.quantity')}</span><input value={draft.quantity} onChange={(e) => set('quantity', e.target.value)} inputMode="decimal" dir="ltr" className="finQtyInput" /></label>
      <label className="cmInline"><span>{t('finance.trading.type')}</span><select value={draft.type} onChange={(e) => set('type', e.target.value as OrderDraft['type'])}>
        <option value="market">{t('finance.trading.market')}</option><option value="limit">{t('finance.trading.limit')}</option></select></label>
      {draft.type === 'limit' && <label className="cmInline"><span>{t('finance.trading.limitPrice')}</span><input value={draft.limit} onChange={(e) => set('limit', e.target.value)} inputMode="decimal" dir="ltr" className="finQtyInput" /></label>}
      <button type="submit" className="cmPrimary" disabled={busy}><ShieldCheck aria-hidden="true" />{busy ? t('finance.saving') : t('finance.trading.checkOrder')}</button>
    </form>
    <FormError id={errorId} text={error} />
  </section>;
}

function Pager({ offset, shown, total, onPage, label }: { offset: number; shown: number; total: number; onPage: (offset: number) => void; label: string }) {
  if (total <= PAGE && offset === 0) return <p className="muted cmPagerText">{rangeLabel(offset, shown, total)}</p>;
  return <nav className="cmPager" aria-label={label}>
    <button type="button" className="secondary" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('finance.page.previous')}</button>
    <span className="muted" aria-live="polite">{rangeLabel(offset, shown, total)}</span>
    <button type="button" className="secondary" disabled={offset + shown >= total} onClick={() => onPage(offset + PAGE)}>{t('finance.page.next')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
  </nav>;
}

function OrderHistory({ portfolio: p, onChanged }: { portfolio: FinancePortfolio; onChanged: (message: string) => void }) {
  const label = t('finance.trading.historyPages');
  const [status, setStatus] = useState<FinancePaperOrderStatus | ''>(''), [run, setRun] = useState<'current' | 'all'>('current'), [offset, setOffset] = useState(0);
  const load = useCallback(() => api.listOrders(p.id, { status, run, limit: PAGE, offset }), [p.id, status, run, offset]);
  const [state, reload] = useLoad(load);
  const [message, setMessage] = useState<string | null>(null);
  const id = useId();
  async function cancel(order: FinancePaperOrder) {
    try { await api.cancelOrder(order.id); onChanged(t('finance.trading.cancelled')); } catch (e) { setMessage(describeError(e)); }
  }
  return <section aria-labelledby={id} className="finSub">
    <h3 id={id}>{t('finance.trading.history')}</h3>
    <div className="cmFilters">
      <label className="cmInline"><span>{t('finance.trading.statusFilter')}</span><select value={status} onChange={(e) => { setStatus(e.target.value as FinancePaperOrderStatus | ''); setOffset(0); }}>
        <option value="">{t('finance.trading.allStatuses')}</option>{(['filled', 'waiting', 'refused', 'cancelled'] as const).map((s) => <option key={s} value={s}>{orderStatusLabel(s)}</option>)}</select></label>
      <label className="cmInline"><span>{t('finance.trading.run')}</span><select value={run} onChange={(e) => { setRun(e.target.value as 'current' | 'all'); setOffset(0); }}>
        <option value="current">{t('finance.trading.runCurrent')}</option><option value="all">{t('finance.trading.runAll')}</option></select></label>
    </div>
    {message && <Notice>{message}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('finance.trading.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={state.message} retry={() => void reload()} />}
    {state.kind === 'ready' && (state.value.items.length === 0 ? <p className="muted">{status || run === 'all' ? t('finance.trading.noOrdersFilter') : t('finance.trading.noOrders')}</p> : <>
      <ul className="cmList finItems">{state.value.items.map((o) => <li key={o.id}><div>
        <strong>{intentLine({ kind: 'trade', ...o })} <span className={`statusBadge ${orderTone(o.status)}`}>{orderStatusLabel(o.status)}</span>{o.requested_by === 'agent' && <span className="statusBadge neutral">{t('finance.trading.byAgent')}</span>}</strong>
        <small>{formatDateTime(o.filled_at ?? o.created_at)}{o.fill_price ? ` · ${t('finance.trading.filledAt', { price: isolate(formatDecimal(o.fill_price)), value: isolate(formatAmount(o.notional, o.currency)), fee: isolate(formatAmount(o.fee, o.currency)) })}` : ''}</small>
        {o.reason && <small>{codeText(o.reason_code, o.reason)}</small>}</div>
        {o.status === 'waiting' && <div className="finItemEnd"><button type="button" className="secondary" onClick={() => void cancel(o)} aria-label={t('finance.trading.cancelNamed', { order: intentLine({ kind: 'trade', ...o }) })}><X aria-hidden="true" />{t('common.cancel')}</button></div>}
      </li>)}</ul>
      <Pager offset={offset} shown={state.value.items.length} total={state.value.total} onPage={setOffset} label={label} />
    </>)}
  </section>;
}

function AssumptionsDialog({ portfolio: p, onClose }: { portfolio: FinancePortfolio; onClose: (saved: boolean) => void }) {
  const [name, setName] = useState(p.name), [fee, setFee] = useState(p.assumptions.fee_bps), [feeMin, setFeeMin] = useState(p.assumptions.fee_min);
  const [slip, setSlip] = useState(p.assumptions.slippage_bps), [age, setAge] = useState(String(p.assumptions.max_price_age_days));
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    const feeOk = checkBps(fee), slipOk = checkBps(slip), min = parseAmount(feeMin || '0', 'AED', { signed: false }), days = Number(age);
    if (!name.trim()) { setError(t('finance.trading.error.name')); return; }
    if (!feeOk.ok || !slipOk.ok) { setError(t('finance.trading.error.bps')); return; }
    if (!min.ok) { setError(min.error); return; }
    if (!Number.isInteger(days) || days < 1 || days > 30) { setError(t('finance.trading.error.age')); return; }
    setBusy(true); setError(null);
    try { await api.updatePortfolio(p.id, { name: name.trim(), fee_bps: feeOk.value, fee_min: min.value, slippage_bps: slipOk.value, max_price_age_days: days }); onClose(true); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('finance.trading.assumptionsTitle')} className="formDialog" onClose={() => onClose(false)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} dir="auto" /></label>
      <p className="muted finHelp">{t('finance.trading.assumptionsHelp')}</p>
      <div className="cmFormGrid">
        <label className="field"><span>{t('finance.trading.feeBps')}</span><input value={fee} onChange={(e) => setFee(e.target.value)} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('finance.trading.feeMin')}</span><input value={feeMin} onChange={(e) => setFeeMin(e.target.value)} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('finance.trading.slippageBps')}</span><input value={slip} onChange={(e) => setSlip(e.target.value)} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('finance.trading.priceAge')}</span><input type="number" min={1} max={30} value={age} onChange={(e) => setAge(e.target.value)} dir="ltr" /></label>
      </div>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(false)} />
    </form>
  </Modal>;
}

// ---- Recorded prices ------------------------------------------------------------------------------------

function PricesPanel({ onChanged, onOpen }: { onChanged: () => void; onOpen: (tab: FinanceTab) => void }) {
  const [filter, setFilter] = useState('');
  const load = useCallback(() => api.listPrices(filter.trim() ? filter.trim().toUpperCase() : undefined, 100), [filter]);
  const [state, reload] = useLoad(load);
  const [symbol, setSymbol] = useState(''), [currency, setCurrency] = useState('USD'), [price, setPrice] = useState('');
  const [asOf, setAsOf] = useState(() => new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [removing, setRemoving] = useState<FinancePrice | null>(null);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    const sym = checkSymbol(symbol, false);
    if (!sym.ok) { setError(sym.error); return; }
    const cur = currency.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(cur)) { setError(t('finance.error.currency')); return; }
    const value = checkPrice(price);
    if (!value.ok) { setError(value.error); return; }
    setBusy(true); setError(null);
    try {
      const saved = await api.addPrice({ symbol: sym.value, currency: cur, price: value.value, as_of: asOf });
      setNotice(saved.orders_changed ? t('finance.prices.savedOrders', { count: saved.orders_changed }) : t('finance.prices.saved'));
      setPrice(''); void reload(); onChanged();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  async function remove() {
    if (!removing) return;
    try { await api.deletePrice(removing.id); setNotice(t('finance.prices.deleted')); void reload(); } catch (e) { setNotice(describeError(e)); } finally { setRemoving(null); }
  }
  return <section className="surface cmPanel" aria-labelledby="fin-prices">
    <h2 id="fin-prices"><Tag aria-hidden="true" className="cmInlineIcon" />{t('finance.prices.title')}</h2>
    <p className="muted">{t('finance.prices.intro')} <button type="button" className="cmLinkButton" onClick={() => onOpen('connections')}>{t('finance.prices.fxLink')}</button></p>
    <form className="cmFilters finCheckForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <label className="cmInline"><span>{t('finance.trading.col.symbol')}</span><input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase().slice(0, 20))} dir="ltr" maxLength={20} className="finSymbolInput" /></label>
      <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" className="finCurrencyInput" /></label>
      <label className="cmInline"><span>{t('finance.trading.col.price')}</span><input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" dir="ltr" className="finQtyInput" /></label>
      <label className="cmInline"><span>{t('finance.prices.date')}</span><input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} dir="ltr" /></label>
      <button type="submit" className="secondary" disabled={busy}><Plus aria-hidden="true" />{t('finance.prices.add')}</button>
    </form>
    <FormError id={errorId} text={error} />
    {notice && <Notice>{notice}</Notice>}
    <label className="cmInline finRateFilter"><span>{t('finance.prices.filter')}</span><input value={filter} onChange={(e) => setFilter(e.target.value.toUpperCase().slice(0, 20))} dir="ltr" maxLength={20} /></label>
    {state.kind === 'loading' && <Notice busy>{t('finance.trading.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={state.message} retry={() => void reload()} />}
    {state.kind === 'ready' && (state.value.length === 0 ? <p className="muted">{filter ? t('finance.prices.noneFilter') : t('finance.prices.none')}</p>
      : <ul className="cmList finItems">{state.value.map((p) => <li key={p.id}><div>
        <strong><bdi dir="ltr">{p.symbol}</bdi> <span dir="ltr">{formatDecimal(p.price)}</span> {p.currency}</strong>
        <small>{t('finance.prices.line', { date: isolate(p.as_of), source: p.source === 'manual' ? t('finance.prices.manual') : p.source })}</small></div>
        <div className="finItemEnd"><button type="button" className="iconButton" onClick={() => setRemoving(p)} aria-label={t('finance.prices.deleteNamed', { symbol: p.symbol, date: p.as_of })}><Trash2 aria-hidden="true" /></button></div></li>)}</ul>)}
    {removing && <ConfirmDialog title={t('finance.prices.deleteTitle')} confirmLabel={t('finance.delete')} danger onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('finance.prices.deleteText', { symbol: removing.symbol, date: removing.as_of })}</p></ConfirmDialog>}
  </section>;
}

// ---- Stage 7: requests waiting and the review with the code ---------------------------------------------

function PendingRequests({ modes, onChanged }: { modes: string[]; onChanged: () => void }) {
  const load = useCallback(() => api.listIntents({ status: 'pending_approval', limit: 50, offset: 0 }), []);
  const [state, reload] = useLoad(load);
  const [review, setReview] = useState<FinanceIntent | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const items = state.kind === 'ready' ? state.value.items.filter((i) => modes.includes(i.mode)) : [];
  if (state.kind === 'ready' && items.length === 0 && !notice) return null;
  return <section className="surface cmPanel finPendingPanel" aria-labelledby="fin-requests">
    <h2 id="fin-requests"><Hourglass aria-hidden="true" className="cmInlineIcon" />{t('finance.trading.waitingTitle', { count: items.length })}</h2>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('finance.trading.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={state.message} retry={() => void reload()} />}
    {items.length > 0 && <ul className="cmList finItems">{items.map((i) => <li key={i.id}><div>
      <strong>{intentLine(i)} <span className="statusBadge neutral">{t(`finance.trading.mode.${i.mode}` as MessageKey)}</span>{i.requested_by === 'agent' && <span className="statusBadge neutral">{t('finance.trading.byAgent')}</span>}</strong>
      <small>{i.portfolio_name ? <span dir="auto">{i.portfolio_name}</span> : null}{i.portfolio_name ? ' · ' : ''}{expiresLabel(i.expires_at)}</small></div>
      <div className="finItemEnd"><button type="button" onClick={() => setReview(i)}>{t('finance.trading.review')}</button></div></li>)}</ul>}
    {review && <ReviewIntent intent={review} onClose={(text, error) => { setReview(null); if (text) setNotice({ text, error }); void reload(); onChanged(); }} />}
  </section>;
}

/** The review: what would happen, what the rules say and the code; approving sends that code once. */
function ReviewIntent({ intent, onClose }: { intent: FinanceIntent; onClose: (message: string | null, error?: boolean) => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const reasons = 'reasons' in intent.decision ? intent.decision.reasons : [];
  const outcome = 'outcome' in intent.decision ? intent.decision.outcome : null;
  const pending = intent.status === 'pending_approval';
  async function approve() {
    setBusy(true); setError(null);
    try {
      const done = await api.approveIntent(intent.id, intent.payload_hash);
      if (done.kind === 'live_profile') { onClose(t('finance.live.applied')); return; }
      const status = done.result.order_status;
      onClose(status === 'filled' ? t('finance.trading.approvedFilled') : status === 'waiting' ? t('finance.trading.approvedWaiting')
        : t('finance.trading.approvedRefused', { reason: codeText(done.result.reason_code, done.result.reason_code ?? '') }));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  async function reject() {
    setBusy(true);
    try { await api.rejectIntent(intent.id); onClose(t('finance.trading.rejected')); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const e = intent.estimate;
  return <Modal title={t('finance.trading.reviewTitle')} className="formDialog" onClose={() => onClose(null)}>
    <div className="finChange">
      <p className="finReviewLine"><strong>{intentLine(intent)}</strong> {intent.simulated && <SimBadge />}
        <span className={`statusBadge ${intentTone(intent.status)}`}>{statusLabel(intent.status)}</span></p>
      {intent.portfolio_name && <p className="muted">{t('finance.trading.inPortfolio', { name: intent.portfolio_name })}{intent.requested_by === 'agent' ? ` · ${t('finance.trading.byAgentLong')}` : ''}</p>}
      {intent.kind === 'live_profile' && intent.live_profile && <LiveLimitsList limits={intent.live_profile.limits} />}
      {e && <dl className="finFigures">
        <div><dt>{t('finance.trading.recordedPrice')}</dt><dd><span dir="ltr">{formatDecimal(e.market_price)}</span><small dir="auto">{t('finance.trading.priceOnFrom', { date: isolate(e.price_as_of), source: e.price_source === 'manual' ? t('finance.prices.manual') : e.price_source })}</small></dd></div>
        <div><dt>{t('finance.trading.fillPrice')}</dt><dd dir="ltr">{formatDecimal(e.fill_price)}</dd></div>
        <div><dt>{t('finance.trading.value')}</dt><dd>{formatAmount(e.notional, intent.currency ?? '')}</dd></div>
        <div><dt>{t('finance.trading.costs')}</dt><dd>{t('finance.trading.costsValue', { fee: isolate(formatAmount(e.fee, intent.currency ?? '')), slippage: isolate(formatAmount(e.slippage, intent.currency ?? '')) })}</dd></div>
      </dl>}
      {e && <p className="muted finHelp">{assumptionsText(e)}{!e.limit_met ? ` ${t('finance.trading.limitNotMet')}` : ''}</p>}
      {!e && intent.kind === 'trade' && intent.status !== 'refused' && <p className="muted">{t('finance.trading.noEstimate')}</p>}
      {intent.refusal && <p className="notice errorNotice" role="alert"><Ban aria-hidden="true" />{codeText(intent.refusal_code, intent.refusal)}</p>}
      {outcome && <div className={`finVerdict finVerdict-${outcome}`}>
        <strong>{t(`finance.trading.verdict.${outcome}` as MessageKey)}</strong>
        <ul>{reasons.map((r, index) => <li key={`${r.code}-${index}`}>{reasonText(r)}</li>)}</ul>
      </div>}
      {intent.simulated && <p className="muted finHelp">{t('finance.trading.simulatedText')}</p>}
      {pending && <p className="muted finHash">{t('finance.trading.code')} <code dir="ltr">{shortHash(intent.payload_hash)}</code> · {expiresLabel(intent.expires_at)}</p>}
    </div>
    <FormError id={errorId} text={error} />
    <div className="actions">
      {pending && <button type="button" disabled={busy} onClick={() => void approve()} aria-describedby={error ? errorId : undefined}><CircleCheck aria-hidden="true" />{busy ? t('finance.saving') : t(intent.kind === 'live_profile' ? 'finance.live.approve' : 'finance.trading.approve')}</button>}
      {pending && <button type="button" className="secondary" disabled={busy} onClick={() => void reject()}><CircleX aria-hidden="true" />{t('finance.trading.reject')}</button>}
      <button type="button" className="secondary" disabled={busy} onClick={() => onClose(null)}>{t('dialog.close')}</button>
    </div>
  </Modal>;
}

// ---- Stages 7 and 8: execution, kill switches and limited live (off) ------------------------------------

export function ExecutionPanel() {
  const [status, reloadStatus] = useLoad(api.getExecutionStatus);
  const [historyKey, setHistoryKey] = useState(0), [liveKey, setLiveKey] = useState(0);
  const refresh = () => { void reloadStatus(); setHistoryKey((k) => k + 1); };
  if (status.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('finance.execution.loading')}</Notice></section>;
  if (status.kind === 'denied') return <Denied />;
  if (status.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('finance.execution.error', { message: status.message })} retry={() => void reloadStatus()} /></section>;
  return <>
    <KillSwitches status={status.value} onChanged={() => { refresh(); setLiveKey((k) => k + 1); }} />
    <section className="surface cmPanel" aria-labelledby="fin-executors">
      <h2 id="fin-executors"><ListChecks aria-hidden="true" className="cmInlineIcon" />{t('finance.execution.executors')}</h2>
      <p className="muted">{t('finance.execution.pipeline', { minutes: status.value.approval_minutes })}</p>
      <ul className="cmList finItems">{status.value.executors.map((ex) => <li key={ex.mode}><div>
        <strong>{t(`finance.execution.executor.${ex.mode}` as MessageKey)}</strong><small>{t(`finance.execution.executorNote.${ex.mode}` as MessageKey)}</small></div>
        <span className={`statusBadge ${ex.available ? 'success' : 'warning'}`}>{ex.available ? t('finance.execution.available') : t('finance.execution.notEnabled')}</span></li>)}</ul>
    </section>
    <LiveSection key={`live-${liveKey}`} onChanged={refresh} />
    <WiderPanel key={historyKey} />
    <RequestHistory key={historyKey} />
  </>;
}

function KillSwitches({ status, onChanged }: { status: FinanceExecutionStatus; onChanged: () => void }) {
  const [confirm, setConfirm] = useState<{ scope: 'person' | 'global'; engaged: boolean } | null>(null);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  async function apply() {
    if (!confirm) return;
    setBusy(true);
    try { await api.setKillSwitch(confirm.scope, confirm.engaged); setNotice({ text: t(confirm.engaged ? 'finance.execution.stopped' : 'finance.execution.resumed') }); onChanged(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(false); setConfirm(null); }
  }
  const row = (scope: 'person' | 'global') => {
    const s = scope === 'person' ? status.person_switch : status.global_switch;
    return <li key={scope}><div>
      <strong>{t(`finance.execution.switch.${scope}` as MessageKey)} <span className={`statusBadge ${s.engaged ? 'warning' : 'success'}`}>{t(s.engaged ? 'finance.execution.on' : 'finance.execution.off')}</span></strong>
      <small>{s.engaged ? t('finance.execution.onHelp') : t(`finance.execution.offHelp.${scope}` as MessageKey)}{s.changed_at ? ` · ${t('finance.execution.changedAt', { date: formatDateTime(s.changed_at) })}` : ''}</small>
      {!s.can_change && <small>{t('finance.execution.globalWho')}</small>}</div>
      <div className="finItemEnd"><button type="button" className={s.engaged ? 'secondary' : 'danger'} disabled={!s.can_change} onClick={() => setConfirm({ scope, engaged: !s.engaged })}>
        {s.engaged ? <Power aria-hidden="true" /> : <OctagonX aria-hidden="true" />}{t(s.engaged ? 'finance.execution.turnOff' : 'finance.execution.turnOn')}</button></div></li>;
  };
  return <section className="surface cmPanel" aria-labelledby="fin-kill">
    <h2 id="fin-kill"><ShieldAlert aria-hidden="true" className="cmInlineIcon" />{t('finance.execution.killTitle')}</h2>
    <p className="muted">{t('finance.execution.killIntro')}</p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <ul className="cmList finItems">{row('person')}{row('global')}</ul>
    {confirm && <ConfirmDialog title={t(confirm.engaged ? 'finance.execution.stopTitle' : 'finance.execution.resumeTitle')} confirmLabel={t(confirm.engaged ? 'finance.execution.turnOn' : 'finance.execution.turnOff')}
      danger={confirm.engaged} busy={busy} onConfirm={() => void apply()} onCancel={() => setConfirm(null)}>
      <p>{t(`finance.execution.confirm.${confirm.scope}.${confirm.engaged ? 'on' : 'off'}` as MessageKey)}</p></ConfirmDialog>}
  </section>;
}

function LiveLimitsList({ limits }: { limits: FinanceLive['limits'] }) {
  const money = (rows: FinanceAmountLimit[]) => rows.length ? rows.map((r) => formatAmount(r.amount, r.currency)).join(' · ') : t('finance.live.nothingAllowed');
  return <dl className="finFigures finLiveLimits">
    <div><dt>{t('finance.live.perAction')}</dt><dd>{money(limits.max_per_action)}</dd></div>
    <div><dt>{t('finance.live.perDay')}</dt><dd>{money(limits.max_per_day)}</dd></div>
    <div><dt>{t('finance.live.perMonth')}</dt><dd>{money(limits.max_per_month)}</dd></div>
    <div><dt>{t('finance.live.currencies')}</dt><dd dir="ltr">{limits.currencies.join(', ') || '-'}</dd></div>
    <div><dt>{t('finance.live.instruments')}</dt><dd dir="ltr">{limits.instruments.join(', ') || '-'}</dd></div>
    <div><dt>{t('finance.live.accounts')}</dt><dd dir="ltr">{limits.accounts.join(', ') || '-'}</dd></div>
    <div><dt>{t('finance.live.cooling')}</dt><dd>{t('finance.live.hoursValue', { count: limits.cooling_off_hours })}</dd></div>
  </dl>;
}

function LiveSection({ onChanged }: { onChanged: () => void }) {
  const [live, reload] = useLoad(api.getLive);
  return <section className="surface cmPanel" aria-labelledby="fin-live">
    {live.kind === 'loading' && <><h2 id="fin-live" className="visuallyHidden">{t('finance.live.title')}</h2><Notice busy>{t('finance.execution.loading')}</Notice></>}
    {(live.kind === 'error' || live.kind === 'denied') && <><h2 id="fin-live">{t('finance.live.title')}</h2><LoadError message={live.message} retry={() => void reload()} /></>}
    {live.kind === 'ready' && <LivePanel live={live.value} onChanged={() => { void reload(); onChanged(); }} />}
  </section>;
}

function LivePanel({ live, onChanged }: { live: FinanceLive; onChanged: () => void }) {
  const [editing, setEditing] = useState(false), [review, setReview] = useState<FinanceIntent | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  async function optOut() {
    try { await api.optOutLive(); setNotice({ text: t('finance.live.optedOut') }); onChanged(); } catch (e) { setNotice({ text: describeError(e), error: true }); }
  }
  return <>
    <div className="sectionHeader"><h2 id="fin-live"><Lock aria-hidden="true" className="cmInlineIcon" />{t('finance.live.title')}</h2>
      <div className="finHeadActions"><button type="button" className="secondary" onClick={() => setEditing(true)}><Settings2 aria-hidden="true" />{t('finance.live.setLimits')}</button>
        {live.opt_in && <button type="button" className="secondary" onClick={() => void optOut()}>{t('finance.live.optOut')}</button>}</div></div>
    <p className="notice errorNotice finLiveOff" role="status"><Ban aria-hidden="true" /><span><strong>{t('finance.live.off')}</strong> {t('finance.live.offText')}</span></p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <h3 className="finSubHead">{t('finance.live.preconditions')}</h3>
    <ul className="finRules finPreconditions">{live.preconditions.map((p) => <li key={p.code} className={p.met ? '' : 'finUnmet'}>
      {p.met ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}<span>{t(`finance.live.pre.${p.code}` as MessageKey)}<span className="visuallyHidden"> {t(p.met ? 'finance.live.met' : 'finance.live.unmet')}</span></span></li>)}</ul>
    <h3 className="finSubHead">{t('finance.live.yourLimits', { version: live.version })}</h3>
    <LiveLimitsList limits={live.limits} />
    {live.pending.length > 0 && <ul className="cmList finItems">{live.pending.map((i) => <li key={i.id}><div><strong>{t('finance.live.changeLine')}</strong><small>{expiresLabel(i.expires_at)}</small></div>
      <div className="finItemEnd"><button type="button" onClick={() => setReview(i)}>{t('finance.trading.review')}</button></div></li>)}</ul>}
    <details className="finExcluded"><summary>{t('finance.live.ownerSteps')}</summary>
      <ol className="finSteps">{(['provider', 'key', 'executor', 'limits', 'approval'] as const).map((step) => <li key={step}>{t(`finance.live.step.${step}` as MessageKey)}</li>)}</ol>
      <p className="muted finHelp">{t('finance.live.stage9')}</p></details>
    {editing && <LiveLimitsEditor live={live} onClose={(made) => { setEditing(false); if (made) setReview(made); }} />}
    {review && <ReviewIntent intent={review} onClose={(text, error) => { setReview(null); if (text) setNotice({ text, error }); onChanged(); }} />}
  </>;
}

function LiveLimitsEditor({ live, onClose }: { live: FinanceLive; onClose: (made: FinanceIntent | null) => void }) {
  const [draft, setDraft] = useState<LiveDraft>(() => draftFromLimits(live.limits));
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  const set = <K extends keyof LiveDraft>(k: K, v: LiveDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = limitsFromDraft(draft);
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    try { onClose(await api.proposeLiveChange({ limits: checked.value, opt_in: false })); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const rows = (key: 'perAction' | 'perDay' | 'perMonth', legend: string) => <fieldset className="finMapping"><legend>{legend}</legend>
    {draft[key].map((row, index) => <div key={index} className="cmFilters finLimitRow">
      <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={row.currency} maxLength={3} dir="ltr" className="finCurrencyInput"
        onChange={(e) => set(key, draft[key].map((r, i) => (i === index ? { ...r, currency: e.target.value.toUpperCase().slice(0, 3) } : r)))} /></label>
      <label className="cmInline"><span>{t('finance.guardian.amount')}</span><input value={row.amount} inputMode="decimal" dir="ltr"
        onChange={(e) => set(key, draft[key].map((r, i) => (i === index ? { ...r, amount: e.target.value } : r)))} /></label>
      <button type="button" className="iconButton" onClick={() => set(key, draft[key].filter((_, i) => i !== index))} aria-label={t('finance.guardian.removeRow', { currency: row.currency || String(index + 1) })}><Trash2 aria-hidden="true" /></button>
    </div>)}
    <button type="button" className="secondary" disabled={draft[key].length >= 20} onClick={() => set(key, [...draft[key], { currency: 'AED', amount: '' }])}><Plus aria-hidden="true" />{t('finance.guardian.addLimit')}</button>
  </fieldset>;
  return <Modal title={t('finance.live.editTitle')} className="formDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <p className="muted">{t('finance.live.editIntro')}</p>
      {rows('perAction', t('finance.live.perAction'))}
      {rows('perDay', t('finance.live.perDay'))}
      {rows('perMonth', t('finance.live.perMonth'))}
      <label className="field"><span>{t('finance.live.currencies')}</span><input value={draft.currencies} onChange={(e) => set('currencies', e.target.value.toUpperCase())} dir="ltr" placeholder="AED" /></label>
      <label className="field"><span>{t('finance.live.instruments')}</span><input value={draft.instruments} onChange={(e) => set('instruments', e.target.value.toUpperCase())} dir="ltr" placeholder="EXMPL" /></label>
      <label className="field"><span>{t('finance.live.accounts')}</span><input value={draft.accounts} onChange={(e) => set('accounts', e.target.value)} dir="ltr" /></label>
      <label className="field"><span>{t('finance.live.cooling')}</span><input type="number" min={1} max={168} value={draft.coolingHours} onChange={(e) => set('coolingHours', e.target.value)} dir="ltr" /></label>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} label={t('finance.guardian.reviewChange')} />
    </form>
  </Modal>;
}

function RequestHistory() {
  const label = t('finance.execution.historyPages');
  const [status, setStatus] = useState<FinanceIntentStatus | ''>(''), [offset, setOffset] = useState(0);
  const load = useCallback(() => api.listIntents({ status, limit: PAGE, offset }), [status, offset]);
  const [state, reload] = useLoad(load);
  const [review, setReview] = useState<FinanceIntent | null>(null);
  return <section className="surface cmPanel" aria-labelledby="fin-request-history">
    <h2 id="fin-request-history"><ListChecks aria-hidden="true" className="cmInlineIcon" />{t('finance.execution.history')}</h2>
    <div className="cmFilters"><label className="cmInline"><span>{t('finance.trading.statusFilter')}</span><select value={status} onChange={(e) => { setStatus(e.target.value as FinanceIntentStatus | ''); setOffset(0); }}>
      <option value="">{t('finance.trading.allStatuses')}</option>
      {(['pending_approval', 'executed', 'denied', 'refused', 'rejected', 'expired', 'failed'] as const).map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}</select></label></div>
    {state.kind === 'loading' && <Notice busy>{t('finance.execution.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={state.message} retry={() => void reload()} />}
    {state.kind === 'ready' && (state.value.items.length === 0 ? <p className="muted">{status ? t('finance.execution.historyNoneFilter') : t('finance.execution.historyNone')}</p> : <>
      <ul className="cmList finItems">{state.value.items.map((i) => <li key={i.id}><div>
        <strong>{intentLine(i)} <span className={`statusBadge ${intentTone(i.status)}`}>{statusLabel(i.status)}</span> <span className="statusBadge neutral">{t(`finance.trading.mode.${i.mode}` as MessageKey)}</span></strong>
        <small>{formatDateTime(i.created_at)}{i.requested_by === 'agent' ? ` · ${t('finance.trading.byAgent')}` : ''}{i.refusal ? ` · ${codeText(i.refusal_code, i.refusal)}` : ''}</small></div>
        <div className="finItemEnd"><button type="button" className="secondary" onClick={() => setReview(i)}>{t(i.status === 'pending_approval' ? 'finance.trading.review' : 'finance.execution.details')}</button></div></li>)}</ul>
      <Pager offset={offset} shown={state.value.items.length} total={state.value.total} onPage={setOffset} label={label} />
    </>)}
    {review && <ReviewIntent intent={review} onClose={() => { setReview(null); void reload(); }} />}
  </section>;
}

