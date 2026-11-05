// IBKR trading (docs/finance-live-trading.md): connect IBKR, account and positions from IBKR, today's
// profit or loss and dividends from IBKR's own records, the limit-order ticket that creates an approval,
// approvals showing the exact order, limits, buy plans, the live switch, the kill switch, reconciliation,
// alerts and the audit trail. Paper is the default; every order needs the owner's approval. Lazy chunk.
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import {
  Bell, CalendarClock, CircleCheck, CircleX, Link2, ListChecks, OctagonX, Plug, Plus, Power, RefreshCw, Scale, Send, ShieldAlert, Trash2,
  TrendingUp, Unplug, Wallet,
} from 'lucide-react';
import type {
  IbkrBuyPlanInput, IbkrCheck, IbkrConnectInput, IbkrConnection, IbkrEnvironment, IbkrEvent, IbkrIntent, IbkrLimits, IbkrOrder,
  IbkrOverview, IbkrQuote, IbkrSnapshot,
} from '@anum/contracts';
import * as api from './lib/financeIbkrApi';
import {
  OPEN_ORDER_STATES, checkTicket, connectionFor, needsLogin, normalizeFingerprint, notional, orderTone, secondsLeft, shortHash, sign,
} from './lib/financeIbkr';
import { formatAmount } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import './financeIbkr.css';

const PAGE = 20;
type Say = (text: string, error?: boolean) => void;

function usd(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? '-' : formatAmount(value, 'USD');
}

function Signed({ value }: { value: string | null | undefined }) {
  const s = sign(value);
  return <span className={`finMoney ${s < 0 ? 'negative' : s > 0 ? 'positive' : ''}`} dir="ltr">{usd(value)}</span>;
}

function ModeBadge({ mode }: { mode: IbkrEnvironment }) {
  return <span className={`statusBadge ibkrMode ${mode === 'live' ? 'danger' : 'neutral'}`}>{t(mode === 'live' ? 'ibkr.mode.live' : 'ibkr.mode.paper')}</span>;
}

function Checks({ items }: { items: IbkrCheck[] }) {
  return <ul className="finRules finPreconditions">{items.map((c) => <li key={c.code} className={c.ok ? '' : 'finUnmet'}>
    {c.ok ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}<span>{t(`ibkr.gate.${c.code}` as MessageKey)}<span className="visuallyHidden"> {t(c.ok ? 'finance.live.met' : 'finance.live.unmet')}</span>
      <small className="ibkrDetail" dir="auto">{c.message}</small></span></li>)}</ul>;
}

export function IbkrPanel() {
  const [state, setState] = useState<Load<IbkrOverview>>({ kind: 'loading' });
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await api.getOverview() }); } catch (e) { setState(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const say: Say = (text, error) => setNotice({ text, error });
  const changed = () => { void load(); setHistoryKey((k) => k + 1); };
  if (state.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('ibkr.loading')}</Notice></section>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('ibkr.error.load', { message: state.message })} retry={() => void load()} /></section>;
  const o = state.value;
  const active = connectionFor(o, o.mode);
  return <div className="ibkrPanel">
    <Header overview={o} say={say} onChanged={changed} />
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <Alerts key={`alerts-${historyKey}`} unread={o.unread_alerts} onRead={changed} />
    <Connections overview={o} say={say} onChanged={changed} />
    {active ? <>
      <Account overview={o} environment={o.mode} />
      <Reconcile overview={o} environment={o.mode} say={say} onChanged={changed} />
      <Approvals overview={o} say={say} onChanged={changed} />
      <Ticket overview={o} say={say} onChanged={changed} />
      <Orders key={`orders-${historyKey}`} say={say} onChanged={changed} />
    </> : <section className="surface cmPanel"><Empty icon={<Plug aria-hidden="true" />} title={t('ibkr.connectFirst.title')} text={t('ibkr.connectFirst.text')} /></section>}
    <Limits overview={o} say={say} onChanged={changed} />
    <Symbols overview={o} say={say} onChanged={changed} />
    <Plans overview={o} say={say} onChanged={changed} />
    <LiveSwitch overview={o} say={say} onChanged={changed} />
    <Trail key={`trail-${historyKey}`} />
  </div>;
}

// -- header and kill switch ----------------------------------------------------------------------------

function Header({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const [confirm, setConfirm] = useState<'kill' | 'release' | null>(null), [busy, setBusy] = useState(false), [reason, setReason] = useState('');
  async function run() {
    setBusy(true);
    try {
      if (confirm === 'kill') {
        const report = await api.kill(reason);
        const stuck = report.report.some((r) => r.error || r.orders.some((x) => !x.cancelled));
        say(t(stuck ? 'ibkr.kill.stuck' : 'ibkr.kill.done', { count: report.report.reduce((n, r) => n + r.orders.length, 0) }), stuck);
      } else { await api.releaseKill(); say(t('ibkr.kill.released')); }
      onChanged();
    } catch (e) { say(describeError(e), true); } finally { setBusy(false); setConfirm(null); }
  }
  return <section className="surface cmPanel ibkrHeader" aria-labelledby="ibkr-title">
    <div className="sectionHeader"><h2 id="ibkr-title"><TrendingUp aria-hidden="true" className="cmInlineIcon" />{t('ibkr.title')} <ModeBadge mode={o.mode} /></h2>
      <div className="finHeadActions">{o.settings.kill_switch
        ? <button type="button" className="secondary" onClick={() => setConfirm('release')}><Power aria-hidden="true" />{t('ibkr.kill.release')}</button>
        : <button type="button" className="danger" onClick={() => setConfirm('kill')}><OctagonX aria-hidden="true" />{t('ibkr.kill.button')}</button>}</div></div>
    <p className="muted">{o.risk_note}</p>
    <dl className="finFigures ibkrStatus">
      <div><dt>{t('ibkr.status.mode')}</dt><dd>{t(o.mode === 'live' ? 'ibkr.status.modeLive' : 'ibkr.status.modePaper')}</dd></div>
      <div><dt>{t('ibkr.status.market')}</dt><dd>{o.market.open ? t('ibkr.status.marketOpen') : o.market.message}</dd></div>
      <div><dt>{t('ibkr.status.kill')}</dt><dd><span className={`statusBadge ${o.settings.kill_switch ? 'danger' : 'success'}`}>{t(o.settings.kill_switch ? 'finance.execution.on' : 'finance.execution.off')}</span></dd></div>
      <div><dt>{t('ibkr.status.approval')}</dt><dd>{t('ibkr.status.approvalValue', { minutes: o.approval_minutes })}</dd></div>
    </dl>
    {confirm && <ConfirmDialog title={t(confirm === 'kill' ? 'ibkr.kill.title' : 'ibkr.kill.releaseTitle')} confirmLabel={t(confirm === 'kill' ? 'ibkr.kill.button' : 'ibkr.kill.release')}
      danger={confirm === 'kill'} busy={busy} onConfirm={() => void run()} onCancel={() => setConfirm(null)}>
      <p>{t(confirm === 'kill' ? 'ibkr.kill.text' : 'ibkr.kill.releaseText')}</p>
      {confirm === 'kill' && <label className="field"><span>{t('ibkr.kill.reason')}</span><input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} /></label>}
    </ConfirmDialog>}
  </section>;
}

function Alerts({ unread, onRead }: { unread: number; onRead: () => void }) {
  const [state, setState] = useState<Load<IbkrEvent[]>>({ kind: 'loading' });
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: (await api.listEvents(true, 5, 0)).items }); } catch (e) { setState(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (state.kind !== 'ready' || state.value.length === 0) return null;
  return <section className="surface cmPanel" aria-labelledby="ibkr-alerts">
    <div className="sectionHeader"><h2 id="ibkr-alerts"><Bell aria-hidden="true" className="cmInlineIcon" />{t('ibkr.alerts.title', { count: unread })}</h2>
      {unread > 0 && <button type="button" className="secondary" onClick={() => void api.markAlertsRead().then(onRead)}>{t('ibkr.alerts.read')}</button>}</div>
    <ul className="cmList finItems">{state.value.map((e) => <li key={e.id} className={e.read_at ? '' : 'ibkrUnread'}><div>
      <strong><span className={`statusBadge ${e.severity === 'critical' ? 'danger' : e.severity === 'warning' ? 'warning' : 'neutral'}`}>{t(`ibkr.severity.${e.severity}` as MessageKey)}</span> {e.message}</strong>
      <small>{formatDateTime(e.created_at)}{e.environment ? ` · ${t(`ibkr.mode.${e.environment}` as MessageKey)}` : ''}</small></div></li>)}</ul>
  </section>;
}

// -- connections ------------------------------------------------------------------------------------------

function Connections({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const [form, setForm] = useState<IbkrEnvironment | null>(null);
  return <section className="surface cmPanel" aria-labelledby="ibkr-connect">
    <h2 id="ibkr-connect"><Link2 aria-hidden="true" className="cmInlineIcon" />{t('ibkr.connect.title')}</h2>
    <p className="muted">{t('ibkr.connect.intro')}</p>
    <div className="ibkrCards">{(['paper', 'live'] as const).map((env) => {
      const c = connectionFor(o, env);
      return <article key={env} className="ibkrCard" aria-labelledby={`ibkr-card-${env}`}>
        <h3 id={`ibkr-card-${env}`}><ModeBadge mode={env} /> {t(`ibkr.connect.${env}` as MessageKey)}</h3>
        {c ? <ConnectionCard connection={c} say={say} onChanged={onChanged} />
          : env === 'live' && !o.deployment_live_enabled ? <p className="muted">{t('ibkr.connect.liveOff')}</p>
          : <><p className="muted">{t(`ibkr.connect.${env}Help` as MessageKey)}</p>
            <button type="button" onClick={() => setForm(env)}><Plug aria-hidden="true" />{t('ibkr.connect.button')}</button></>}
      </article>;
    })}</div>
    {form && <ConnectForm environment={form} hosts={o.gateway_hosts} onClose={(text) => { setForm(null); if (text) { say(text); onChanged(); } }} />}
  </section>;
}

function ConnectionCard({ connection: c, say, onChanged }: { connection: IbkrConnection; say: Say; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null), [confirm, setConfirm] = useState(false);
  async function act(name: string, fn: () => Promise<unknown>, done: string) {
    setBusy(name);
    try { await fn(); say(done); onChanged(); } catch (e) { say(describeError(e), true); } finally { setBusy(null); }
  }
  const login = needsLogin(c);
  return <>
    <dl className="finFigures">
      <div><dt>{t('ibkr.connect.account')}</dt><dd><bdi>{c.hint}</bdi></dd></div>
      <div><dt>{t('ibkr.connect.session')}</dt><dd><span className={`statusBadge ${c.session === 'authenticated' ? 'success' : 'warning'}`}>{t(`ibkr.session.${c.session}` as MessageKey)}</span></dd></div>
      <div><dt>{t('ibkr.connect.checked')}</dt><dd>{c.verified_at ? formatDateTime(c.verified_at) : t('ibkr.connect.notChecked')}</dd></div>
      <div><dt>{t('ibkr.connect.synced')}</dt><dd>{c.last_sync_at ? formatDateTime(c.last_sync_at) : '-'}</dd></div>
    </dl>
    {login && <p className="notice errorNotice" role="alert"><ShieldAlert aria-hidden="true" /><span><strong>{t('ibkr.session.loginAgain')}</strong> {t('ibkr.session.loginSteps')}</span></p>}
    {c.last_error && !login && <p className="cmFieldError">{c.last_error}</p>}
    <div className="actions ibkrActions">
      <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('check', () => api.checkConnection(c.id), t('ibkr.connect.checkedNow'))}><RefreshCw aria-hidden="true" />{t('ibkr.connect.check')}</button>
      {c.session !== 'authenticated' && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('reauth', () => api.checkConnection(c.id, true), t('ibkr.connect.checkedNow'))}>{t('ibkr.connect.reauth')}</button>}
      <button type="button" disabled={busy !== null || !c.verified_at} onClick={() => void act('sync', () => api.sync(c.environment), t('ibkr.connect.syncedNow'))}><RefreshCw aria-hidden="true" />{busy === 'sync' ? t('ibkr.connect.syncing') : t('ibkr.connect.sync')}</button>
      {c.statements_connected && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('statements', () => api.statements(c.environment), t('ibkr.connect.statementsNow'))}>{t('ibkr.connect.statements')}</button>}
      <button type="button" className="secondary" disabled={busy !== null} onClick={() => setConfirm(true)}><Unplug aria-hidden="true" />{t('ibkr.connect.disconnect')}</button>
    </div>
    {confirm && <ConfirmDialog title={t('ibkr.connect.disconnectTitle')} confirmLabel={t('ibkr.connect.disconnect')} danger busy={busy === 'off'}
      onConfirm={() => { setConfirm(false); void act('off', () => api.disconnect(c.id), t('ibkr.connect.disconnected')); }} onCancel={() => setConfirm(false)}>
      <p>{t('ibkr.connect.disconnectText')}</p></ConfirmDialog>}
  </>;
}

function ConnectForm({ environment, hosts, onClose }: { environment: IbkrEnvironment; hosts: string[]; onClose: (message: string | null) => void }) {
  const [draft, setDraft] = useState<IbkrConnectInput>({ environment, gateway_host: hosts[0] ?? 'ibkr-gateway', tls_fingerprint: '', account_id: '', flex_token: '', flex_query_id: '', cash_account_confirmed: false });
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  const set = <K extends keyof IbkrConnectInput>(k: K, v: IbkrConnectInput[K]) => setDraft((d) => ({ ...d, [k]: v }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const pin = normalizeFingerprint(draft.tls_fingerprint);
    if (!pin) { setError(t('ibkr.form.badPin')); return; }
    if (!draft.cash_account_confirmed) { setError(t('ibkr.form.needCash')); return; }
    setBusy(true); setError(null);
    try {
      const made = await api.connect({ ...draft, tls_fingerprint: pin, account_id: draft.account_id.trim().toUpperCase() });
      onClose(made.verified_at ? t('ibkr.form.connected') : t('ibkr.form.savedLogin'));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t(environment === 'live' ? 'ibkr.form.titleLive' : 'ibkr.form.titlePaper')} className="formDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined} autoComplete="off">
      <p className="muted">{t('ibkr.form.intro')}</p>
      <label className="field"><span>{t('ibkr.form.host')}</span><select value={draft.gateway_host} onChange={(e) => set('gateway_host', e.target.value)}>
        {hosts.map((h) => <option key={h} value={h}>{h}:5000</option>)}</select></label>
      <label className="field"><span>{t('ibkr.form.pin')}</span><input value={draft.tls_fingerprint} dir="ltr" spellCheck={false} required onChange={(e) => set('tls_fingerprint', e.target.value)} />
        <small className="muted">{t('ibkr.form.pinHelp')}</small></label>
      <label className="field"><span>{t(environment === 'live' ? 'ibkr.form.accountLive' : 'ibkr.form.accountPaper')}</span><input value={draft.account_id} dir="ltr" required maxLength={20} onChange={(e) => set('account_id', e.target.value)} /></label>
      <fieldset className="finMapping"><legend>{t('ibkr.form.flex')}</legend>
        <p className="muted">{t('ibkr.form.flexHelp')}</p>
        <label className="field"><span>{t('ibkr.form.flexToken')}</span><input type="password" value={draft.flex_token} dir="ltr" autoComplete="new-password" maxLength={64} onChange={(e) => set('flex_token', e.target.value.trim())} /></label>
        <label className="field"><span>{t('ibkr.form.flexQuery')}</span><input value={draft.flex_query_id} dir="ltr" inputMode="numeric" maxLength={12} onChange={(e) => set('flex_query_id', e.target.value.trim())} /></label>
      </fieldset>
      <label className="finCheck"><input type="checkbox" checked={draft.cash_account_confirmed} onChange={(e) => set('cash_account_confirmed', e.target.checked)} /><span>{t('ibkr.form.cash')}</span></label>
      <p className="muted finHelp">{t('ibkr.form.noPassword')}</p>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} label={t('ibkr.connect.button')} />
    </form>
  </Modal>;
}

// -- account, P&L and positions ------------------------------------------------------------------------

function Account({ overview: o, environment }: { overview: IbkrOverview; environment: IbkrEnvironment }) {
  const snap: IbkrSnapshot = o.snapshots[environment] ?? {};
  const statement = o.statements[environment] ?? {};
  const positions = snap.positions ?? [];
  return <section className="surface cmPanel" aria-labelledby="ibkr-account">
    <h2 id="ibkr-account"><Wallet aria-hidden="true" className="cmInlineIcon" />{t('ibkr.account.title')} <ModeBadge mode={environment} /></h2>
    {!snap.synced_at ? <p className="muted">{t('ibkr.account.notSynced')}</p> : <>
      <p className="muted">{t('ibkr.account.source', { date: formatDateTime(snap.synced_at) })}</p>
      <dl className="finFigures ibkrFigures">
        <div><dt>{t('ibkr.account.cash')}</dt><dd>{usd(snap.settled_cash?.USD ?? snap.cash?.USD)}</dd></div>
        <div><dt>{t('ibkr.account.net')}</dt><dd>{usd(snap.net_liquidation)}</dd></div>
        <div><dt>{t('ibkr.account.today')}</dt><dd><Signed value={snap.daily_pnl} /></dd></div>
        <div><dt>{t('ibkr.account.unrealised')}</dt><dd><Signed value={snap.unrealized_pnl} /></dd></div>
        <div><dt>{t('ibkr.account.realised')}</dt><dd><Signed value={snap.realized_pnl} /></dd></div>
        <div><dt>{t('ibkr.account.dividends')}</dt><dd>{statement.ready ? usd(statement.dividends_received) : t('ibkr.account.notConnected')}</dd></div>
        <div><dt>{t('ibkr.account.withholding')}</dt><dd>{statement.ready ? usd(statement.withholding_tax) : '-'}</dd></div>
        <div><dt>{t('ibkr.account.purification')}</dt><dd>{t('ibkr.account.purificationNone')}</dd></div>
      </dl>
      <p className="muted finHelp">{t('ibkr.account.pnlHelp')}</p>
      {statement.message && !statement.ready && <p className="muted">{statement.message}</p>}
      {positions.length === 0 ? <p className="muted">{t('ibkr.account.noPositions')}</p>
        : <div className="cmTableWrap" tabIndex={0} role="region" aria-label={t('ibkr.account.positions')}><table className="cmTable finTable">
          <caption className="visuallyHidden">{t('ibkr.account.positions')}</caption>
          <thead><tr><th scope="col">{t('ibkr.col.symbol')}</th><th scope="col">{t('ibkr.col.quantity')}</th><th scope="col">{t('ibkr.col.value')}</th><th scope="col">{t('ibkr.col.unrealised')}</th></tr></thead>
          <tbody>{positions.map((p) => <tr key={p.conid}><td><span dir="ltr">{p.symbol}</span>{o.shariah_etfs.includes(p.symbol) ? <> <span className="statusBadge success">{t('ibkr.symbols.shariah')}</span></> : null}</td><td dir="ltr">{p.quantity}</td><td>{usd(p.market_value)}</td><td><Signed value={p.unrealized_pnl} /></td></tr>)}</tbody>
        </table></div>}
      {(snap.open_orders ?? []).length > 0 && <><h3 className="finSubHead">{t('ibkr.account.openAtIbkr')}</h3>
        <ul className="cmList finItems">{(snap.open_orders ?? []).map((b) => <li key={b.order_id}><div><strong dir="ltr">{b.side} {b.quantity ?? '-'} {b.symbol} @ {b.limit_price ?? '-'}</strong>
          <small>{b.status}{b.order_ref ? '' : ` · ${t('ibkr.account.notFromAnum')}`}</small></div></li>)}</ul></>}
    </>}
  </section>;
}

function Reconcile({ overview: o, environment, say, onChanged }: { overview: IbkrOverview; environment: IbkrEnvironment; say: Say; onChanged: () => void }) {
  const recon = o.settings.reconciliation[environment];
  const [note, setNote] = useState(''), [busy, setBusy] = useState(false);
  const noteId = useId();
  async function accept(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try { await api.acceptReconciliation(environment, note.trim()); setNote(''); say(t('ibkr.recon.accepted')); onChanged(); } catch (e) { say(describeError(e), true); } finally { setBusy(false); }
  }
  const state = recon?.state ?? 'not_checked';
  return <section className="surface cmPanel" aria-labelledby="ibkr-recon">
    <h2 id="ibkr-recon"><Scale aria-hidden="true" className="cmInlineIcon" />{t('ibkr.recon.title')} <span className={`statusBadge ${state === 'ok' ? 'success' : state === 'mismatch' ? 'danger' : 'warning'}`}>{t(`ibkr.recon.${state}` as MessageKey)}</span></h2>
    <p className="muted">{t('ibkr.recon.intro')}{recon?.checked_at ? ` ${t('ibkr.recon.checkedAt', { date: formatDateTime(recon.checked_at) })}` : ''}</p>
    {state === 'mismatch' && <>
      <ul className="finRules">{recon?.mismatches.map((m, i) => <li key={`${m.code}-${i}`} className="finUnmet"><CircleX aria-hidden="true" /><span>{m.message}</span></li>)}</ul>
      <form className="finForm" onSubmit={(e) => void accept(e)}>
        <label className="field"><span>{t('ibkr.recon.note')}</span><textarea id={noteId} value={note} minLength={10} maxLength={300} required onChange={(e) => setNote(e.target.value)} /></label>
        <div className="actions"><button type="submit" disabled={busy || note.trim().length < 10}>{t('ibkr.recon.accept')}</button></div>
      </form></>}
  </section>;
}

// -- the order ticket and approvals ------------------------------------------------------------------------

function Ticket({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const allowed = o.settings.allowed.map((a) => a.symbol);
  const [draft, setDraft] = useState({ symbol: allowed[0] ?? '', side: 'buy' as 'buy' | 'sell', quantity: '1', limit_price: '' });
  const [quote, setQuote] = useState<IbkrQuote | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const set = <K extends keyof typeof draft>(k: K, v: (typeof draft)[K]) => setDraft((d) => ({ ...d, [k]: v }));
  async function price() {
    setError(null);
    try { const q = await api.getQuote(draft.symbol); setQuote(q); if (q.last && !draft.limit_price) set('limit_price', q.last); } catch (e) { setError(describeError(e)); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = checkTicket(draft, allowed, o.settings.fractional_ok, o.settings.limits.max_order);
    if (problem) { setError(t(`ibkr.ticket.error.${problem}` as MessageKey, { max: usd(o.settings.limits.max_order) })); return; }
    setBusy(true); setError(null);
    try { await api.createOrder(draft); say(t('ibkr.ticket.created')); onChanged(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  if (allowed.length === 0) return <section className="surface cmPanel" aria-labelledby="ibkr-ticket"><h2 id="ibkr-ticket">{t('ibkr.ticket.title')}</h2>
    <Empty icon={<ListChecks aria-hidden="true" />} title={t('ibkr.ticket.noSymbols')} text={t('ibkr.ticket.noSymbolsText')} /></section>;
  const total = notional(draft.quantity, draft.limit_price);
  return <section className="surface cmPanel" aria-labelledby="ibkr-ticket">
    <h2 id="ibkr-ticket"><Send aria-hidden="true" className="cmInlineIcon" />{t('ibkr.ticket.title')} <ModeBadge mode={o.mode} /></h2>
    <p className="muted">{t('ibkr.ticket.intro')}</p>
    <form className="finForm ibkrTicket" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <label className="field"><span>{t('ibkr.col.symbol')}</span><select value={draft.symbol} onChange={(e) => { set('symbol', e.target.value); setQuote(null); }}>
        {o.settings.allowed.map((a) => <option key={a.symbol} value={a.symbol}>{a.symbol} ({t(a.shariah_screened ? 'ibkr.symbols.shariah' : 'ibkr.symbols.notScreened')})</option>)}</select></label>
      <fieldset className="ibkrSide"><legend>{t('ibkr.ticket.side')}</legend>
        {(['buy', 'sell'] as const).map((side) => <label key={side} className="finCheck"><input type="radio" name="ibkr-side" checked={draft.side === side} onChange={() => set('side', side)} /><span>{t(`ibkr.side.${side}` as MessageKey)}</span></label>)}</fieldset>
      <label className="field"><span>{t('ibkr.col.quantity')}</span><input value={draft.quantity} inputMode="decimal" dir="ltr" required onChange={(e) => set('quantity', e.target.value)} />
        <small className="muted">{t(o.settings.fractional_ok ? 'ibkr.ticket.fractional' : 'ibkr.ticket.whole')}</small></label>
      <label className="field"><span>{t('ibkr.ticket.limit')}</span><input value={draft.limit_price} inputMode="decimal" dir="ltr" required onChange={(e) => set('limit_price', e.target.value)} /></label>
      <div className="actions"><button type="button" className="secondary" onClick={() => void price()}><RefreshCw aria-hidden="true" />{t('ibkr.ticket.getPrice')}</button></div>
      {quote && <p className="muted" role="status">{quote.last ? t(quote.delayed ? 'ibkr.ticket.quoteDelayed' : 'ibkr.ticket.quoteLive', { price: usd(quote.last), band: quote.band_percent }) : t('ibkr.ticket.noQuote')}</p>}
      <p className="ibkrEstimate">{t('ibkr.ticket.estimate', { total: usd(total), max: usd(o.settings.limits.max_order) })}</p>
      <FormError id={errorId} text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('finance.saving') : t('ibkr.ticket.create')}</button></div>
    </form>
  </section>;
}

function Approvals({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const [review, setReview] = useState<IbkrIntent | null>(null);
  return <section className="surface cmPanel" aria-labelledby="ibkr-approvals">
    <h2 id="ibkr-approvals"><ListChecks aria-hidden="true" className="cmInlineIcon" />{t('ibkr.approvals.title', { count: o.pending.length })}</h2>
    {o.pending.length === 0 ? <p className="muted">{t('ibkr.approvals.none')}</p>
      : <ul className="cmList finItems">{o.pending.map((i) => <li key={i.id}><div>
        <strong dir="ltr">{i.side === 'buy' ? t('ibkr.side.buy') : t('ibkr.side.sell')} {i.quantity} {i.symbol} @ {usd(i.limit_price)} <ModeBadge mode={i.environment} /></strong>
        <small>{t('ibkr.approvals.line', { total: usd(i.amount), date: formatDateTime(i.expires_at) })}{i.reason ? ` · ${i.reason}` : ''}</small></div>
        <div className="finItemEnd"><button type="button" onClick={() => setReview(i)}>{t('finance.trading.review')}</button></div></li>)}</ul>}
    {review && <ReviewOrder intent={review} onClose={(text, error) => { setReview(null); if (text) say(text, error); onChanged(); }} />}
  </section>;
}

function ReviewOrder({ intent: i, onClose }: { intent: IbkrIntent; onClose: (text: string | null, error?: boolean) => void }) {
  const [busy, setBusy] = useState(false), [left, setLeft] = useState(() => secondsLeft(i.expires_at, Date.now()));
  useEffect(() => { const timer = setInterval(() => setLeft(secondsLeft(i.expires_at, Date.now())), 1000); return () => clearInterval(timer); }, [i.expires_at]);
  async function decide(yes: boolean) {
    setBusy(true);
    try {
      if (yes) { const r = await api.approve(i.id, i.payload_hash); onClose(t(r.order.state === 'rejected' ? 'ibkr.approvals.rejectedByIbkr' : 'ibkr.approvals.sent', { message: r.order.message ?? '' }), r.order.state === 'rejected'); }
      else { await api.reject(i.id); onClose(t('ibkr.approvals.declined')); }
    } catch (e) { onClose(describeError(e), true); } finally { setBusy(false); }
  }
  const live = i.environment === 'live';
  return <Modal title={t(live ? 'ibkr.review.titleLive' : 'ibkr.review.titlePaper')} className="formDialog" onClose={() => onClose(null)}>
    {live && <p className="notice errorNotice" role="alert"><ShieldAlert aria-hidden="true" /><strong>{t('ibkr.review.realMoney')}</strong></p>}
    <dl className="finFigures ibkrReview">
      <div><dt>{t('ibkr.review.account')}</dt><dd><ModeBadge mode={i.environment} /></dd></div>
      <div><dt>{t('ibkr.col.symbol')}</dt><dd><bdi>{i.symbol}</bdi></dd></div>
      <div><dt>{t('ibkr.ticket.side')}</dt><dd>{t(`ibkr.side.${i.side}` as MessageKey)}</dd></div>
      <div><dt>{t('ibkr.col.quantity')}</dt><dd><bdi>{i.quantity}</bdi></dd></div>
      <div><dt>{t('ibkr.review.type')}</dt><dd>{t('ibkr.review.typeValue')}</dd></div>
      <div><dt>{t('ibkr.ticket.limit')}</dt><dd>{usd(i.limit_price)}</dd></div>
      <div><dt>{t('ibkr.review.total')}</dt><dd>{usd(i.amount)}</dd></div>
      <div><dt>{t('ibkr.review.expires')}</dt><dd>{left > 0 ? t('ibkr.review.seconds', { count: left }) : t('ibkr.review.expired')}</dd></div>
      <div><dt>{t('ibkr.review.hash')}</dt><dd title={i.payload_hash}><bdi>{shortHash(i.payload_hash)}</bdi></dd></div>
    </dl>
    <p className="muted">{t('ibkr.review.checks')}</p>
    <div className="actions">
      <button type="button" className={live ? 'danger' : undefined} disabled={busy || left === 0} onClick={() => void decide(true)}>{t(live ? 'ibkr.review.approveLive' : 'ibkr.review.approve')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => void decide(false)}>{t('ibkr.review.reject')}</button>
    </div>
  </Modal>;
}

function Orders({ say, onChanged }: { say: Say; onChanged: () => void }) {
  const [offset, setOffset] = useState(0), [state, setState] = useState<Load<{ items: IbkrOrder[]; total: number }>>({ kind: 'loading' });
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await api.listOrders(PAGE, offset) }); } catch (e) { setState(failed(e)); }
  }, [offset]);
  useEffect(() => { void load(); }, [load]);
  async function act(fn: () => Promise<unknown>, done: string) {
    try { await fn(); say(done); onChanged(); } catch (e) { say(describeError(e), true); void load(); }
  }
  return <section className="surface cmPanel" aria-labelledby="ibkr-orders">
    <h2 id="ibkr-orders"><ListChecks aria-hidden="true" className="cmInlineIcon" />{t('ibkr.orders.title')}</h2>
    {state.kind === 'loading' && <Notice busy>{t('ibkr.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={state.message} retry={() => void load()} />}
    {state.kind === 'ready' && (state.value.items.length === 0 ? <p className="muted">{t('ibkr.orders.none')}</p> : <>
      <ul className="cmList finItems">{state.value.items.map((o) => <li key={o.id}><div>
        <strong dir="ltr">{t(`ibkr.side.${o.side}` as MessageKey)} {o.quantity} {o.symbol} @ {usd(o.limit_price)} <span className={`statusBadge ${orderTone(o.state)}`}>{t(`ibkr.state.${o.state}` as MessageKey)}</span> <ModeBadge mode={o.environment} /></strong>
        <small>{formatDateTime(o.created_at)}{Number(o.filled_quantity) > 0 ? ` · ${t('ibkr.orders.filled', { qty: o.filled_quantity, price: usd(o.average_price) })}` : ''}{o.message ? ` · ${o.message}` : ''}</small></div>
        <div className="finItemEnd">
          {OPEN_ORDER_STATES.includes(o.state) && <button type="button" className="secondary" onClick={() => void act(() => api.refreshOrder(o.id), t('ibkr.orders.checked'))}>{t('ibkr.orders.check')}</button>}
          {o.state === 'unknown' && <button type="button" className="secondary" onClick={() => void act(() => api.resendOrder(o.id), t('ibkr.orders.resent'))}>{t('ibkr.orders.resend')}</button>}
          {['submitted', 'partially_filled'].includes(o.state) && <button type="button" className="danger" onClick={() => void act(() => api.cancelOrder(o.id), t('ibkr.orders.cancelled'))}>{t('ibkr.orders.cancel')}</button>}
        </div></li>)}</ul>
      <Pager offset={offset} total={state.value.total} onPage={setOffset} />
    </>)}
  </section>;
}

function Pager({ offset, total, onPage }: { offset: number; total: number; onPage: (offset: number) => void }) {
  if (total <= PAGE) return null;
  return <nav className="cmPager" aria-label={t('ibkr.pages')}>
    <button type="button" className="secondary" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE))}>{t('finance.page.previous')}</button>
    <span className="muted">{t('ibkr.pageOf', { from: offset + 1, to: Math.min(offset + PAGE, total), total })}</span>
    <button type="button" className="secondary" disabled={offset + PAGE >= total} onClick={() => onPage(offset + PAGE)}>{t('finance.page.next')}</button>
  </nav>;
}

// -- limits, symbols, plans -----------------------------------------------------------------------------

const LIMIT_FIELDS: (keyof IbkrLimits)[] = ['max_capital', 'max_order', 'max_daily_loss', 'max_orders_per_day', 'price_band_percent', 'session_buffer_minutes', 'fee_buffer'];

function Limits({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const limits = o.settings.limits;
  return <section className="surface cmPanel" aria-labelledby="ibkr-limits">
    <div className="sectionHeader"><h2 id="ibkr-limits"><ShieldAlert aria-hidden="true" className="cmInlineIcon" />{t('ibkr.limits.title')}</h2>
      <button type="button" className="secondary" onClick={() => setEditing(true)}>{t('ibkr.limits.edit')}</button></div>
    <dl className="finFigures ibkrFigures">{LIMIT_FIELDS.map((f) => <div key={f}><dt>{t(`ibkr.limit.${f}` as MessageKey)}</dt><dd><bdi>{String(limits[f])}</bdi></dd></div>)}</dl>
    <p className="notice" role="note"><ShieldAlert aria-hidden="true" /><span><strong>{t('ibkr.limits.lossWhy')}</strong> {t('ibkr.limits.lossText', { amount: usd(limits.max_daily_loss) })}</span></p>
    {o.settings.live.on && <p className="muted">{t('ibkr.limits.liveStricter')}</p>}
    {editing && <LimitsEditor limits={limits} version={o.settings.version} onClose={(saved) => { setEditing(false); if (saved) { say(t('ibkr.limits.saved')); onChanged(); } }} />}
  </section>;
}

function LimitsEditor({ limits, version, onClose }: { limits: IbkrLimits; version: number; onClose: (saved: boolean) => void }) {
  const [draft, setDraft] = useState<Record<keyof IbkrLimits, string>>(() => Object.fromEntries(LIMIT_FIELDS.map((f) => [f, String(limits[f])])) as Record<keyof IbkrLimits, string>);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null);
    const body: IbkrLimits = { ...draft, max_orders_per_day: Number(draft.max_orders_per_day), session_buffer_minutes: Number(draft.session_buffer_minutes) };
    try { await api.saveLimits(body, version); onClose(true); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('ibkr.limits.edit')} className="formDialog" onClose={() => onClose(false)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      {LIMIT_FIELDS.map((f) => <label key={f} className="field"><span>{t(`ibkr.limit.${f}` as MessageKey)}</span>
        <input value={draft[f]} inputMode="decimal" dir="ltr" required onChange={(e) => setDraft((d) => ({ ...d, [f]: e.target.value }))} />
        {f === 'max_daily_loss' && <small className="muted">{t('ibkr.limits.lossShort')}</small>}</label>)}
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(false)} />
    </form>
  </Modal>;
}

function Symbols({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const [showAll, setShowAll] = useState(false);
  const screened = (s: string) => o.shariah_etfs.includes(s);
  const choices = Object.keys(o.etfs).filter((s) => !o.settings.allowed.some((a) => a.symbol === s) && (showAll || screened(s)))
    .sort((a, b) => Number(screened(b)) - Number(screened(a)) || a.localeCompare(b));
  const [pick, setPick] = useState(''), [busy, setBusy] = useState(false), [confirm, setConfirm] = useState(false);
  const chosen = choices.includes(pick) ? pick : choices[0] ?? '';
  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try { await fn(); say(done); onChanged(); } catch (e) { say(describeError(e), true); } finally { setBusy(false); }
  }
  const tag = (s: string) => <span className={`statusBadge ${screened(s) ? 'success' : 'warning'}`}>{t(screened(s) ? 'ibkr.symbols.shariah' : 'ibkr.symbols.notScreened')}</span>;
  return <section className="surface cmPanel" aria-labelledby="ibkr-symbols">
    <h2 id="ibkr-symbols"><ListChecks aria-hidden="true" className="cmInlineIcon" />{t('ibkr.symbols.title')}</h2>
    <p className="muted">{t('ibkr.symbols.intro')}</p>
    <p className="muted finHelp">{t('ibkr.symbols.screenNote')}</p>
    {o.settings.allowed.length === 0 ? <p className="muted">{t('ibkr.symbols.none')}</p>
      : <ul className="cmList finItems">{o.settings.allowed.map((a) => <li key={a.symbol}><div><strong><span dir="ltr">{a.symbol}</span> {tag(a.symbol)}</strong><small>{a.name} · <span dir="ltr">{a.exchange} · conid {a.conid}</span></small></div>
        <div className="finItemEnd"><button type="button" className="iconButton" disabled={busy} aria-label={t('ibkr.symbols.remove', { symbol: a.symbol })} onClick={() => void act(() => api.removeSymbol(a.symbol), t('ibkr.symbols.removed'))}><Trash2 aria-hidden="true" /></button></div></li>)}</ul>}
    <div className="cmFilters">
      {choices.length > 0 && <><label className="cmInline"><span>{t('ibkr.symbols.add')}</span><select value={chosen} onChange={(e) => setPick(e.target.value)}>
        {choices.map((s) => <option key={s} value={s}>{s}: {o.etfs[s]} ({t(screened(s) ? 'ibkr.symbols.shariah' : 'ibkr.symbols.notScreened')})</option>)}</select></label>
      <button type="button" disabled={busy || !chosen} onClick={() => (screened(chosen) ? void act(() => api.addSymbol(chosen), t('ibkr.symbols.added')) : setConfirm(true))}><Plus aria-hidden="true" />{t('ibkr.symbols.addButton')}</button></>}
      <label className="finCheck"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /><span>{t('ibkr.symbols.showAll')}</span></label>
    </div>
    <label className="finCheck"><input type="checkbox" checked={o.settings.fractional_ok} disabled={busy} onChange={(e) => void act(() => api.setFractional(e.target.checked), t('ibkr.limits.saved'))} /><span>{t('ibkr.symbols.fractional')}</span></label>
    {confirm && <ConfirmDialog title={t('ibkr.symbols.confirmTitle', { symbol: chosen })} confirmLabel={t('ibkr.symbols.confirmAdd')} busy={busy}
      onConfirm={() => { setConfirm(false); void act(() => api.addSymbol(chosen, true), t('ibkr.symbols.added')); }} onCancel={() => setConfirm(false)}>
      <p>{t('ibkr.symbols.confirmText', { symbol: chosen })}</p></ConfirmDialog>}
  </section>;
}

function Plans({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const [rows, setRows] = useState<IbkrBuyPlanInput[]>(() => o.settings.buy_plans.map((p) => ({ id: p.id, symbol: p.symbol, amount: p.amount, day_of_month: p.day_of_month, active: p.active })));
  const [busy, setBusy] = useState(false);
  const allowed = o.settings.allowed.map((a) => a.symbol);
  async function save() {
    setBusy(true);
    try { await api.savePlans(rows, o.settings.version); say(t('ibkr.plans.saved')); onChanged(); } catch (e) { say(describeError(e), true); } finally { setBusy(false); }
  }
  async function propose() {
    setBusy(true);
    try {
      const r = await api.proposePlans();
      say(r.proposed.length ? t('ibkr.plans.proposed', { count: r.proposed.length }) : r.skipped.length ? r.skipped.map((s) => s.reason).join(' ') : t('ibkr.plans.noneDue'));
      onChanged();
    } catch (e) { say(describeError(e), true); } finally { setBusy(false); }
  }
  const set = (index: number, patch: Partial<IbkrBuyPlanInput>) => setRows((r) => r.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return <section className="surface cmPanel" aria-labelledby="ibkr-plans">
    <h2 id="ibkr-plans"><CalendarClock aria-hidden="true" className="cmInlineIcon" />{t('ibkr.plans.title')}</h2>
    <p className="muted">{t('ibkr.plans.intro')}</p>
    {allowed.length === 0 ? <p className="muted">{t('ibkr.ticket.noSymbolsText')}</p> : <>
      {rows.length === 0 && <p className="muted">{t('ibkr.plans.none')}</p>}
      {rows.map((row, index) => <fieldset key={row.id ?? `new-${index}`} className="finMapping ibkrPlan"><legend>{t('ibkr.plans.plan', { n: index + 1 })}{o.settings.buy_plans.find((p) => p.id === row.id)?.paper_only && <> <span className="statusBadge neutral">{t('ibkr.plans.paperOnly')}</span></>}</legend>
        <label className="cmInline"><span>{t('ibkr.col.symbol')}</span><select value={row.symbol} onChange={(e) => set(index, { symbol: e.target.value })}>{allowed.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
        <label className="cmInline"><span>{t('ibkr.plans.amount')}</span><input value={row.amount} inputMode="decimal" dir="ltr" onChange={(e) => set(index, { amount: e.target.value })} /></label>
        <label className="cmInline"><span>{t('ibkr.plans.day')}</span><input type="number" min={1} max={28} value={row.day_of_month} dir="ltr" onChange={(e) => set(index, { day_of_month: Number(e.target.value) })} /></label>
        <label className="finCheck"><input type="checkbox" checked={row.active} onChange={(e) => set(index, { active: e.target.checked })} /><span>{t('ibkr.plans.active')}</span></label>
        <button type="button" className="iconButton" aria-label={t('ibkr.plans.remove', { n: index + 1 })} onClick={() => setRows((r) => r.filter((_, i) => i !== index))}><Trash2 aria-hidden="true" /></button>
      </fieldset>)}
      <div className="actions">
        <button type="button" className="secondary" disabled={rows.length >= 10} onClick={() => setRows((r) => [...r, { symbol: allowed[0], amount: o.settings.limits.max_order, day_of_month: 1, active: true }])}><Plus aria-hidden="true" />{t('ibkr.plans.add')}</button>
        <button type="button" disabled={busy} onClick={() => void save()}>{t('ibkr.plans.save')}</button>
        <button type="button" className="secondary" disabled={busy || o.settings.buy_plans.length === 0} onClick={() => void propose()}>{t('ibkr.plans.propose')}</button>
      </div></>}
  </section>;
}

// -- the live switch ------------------------------------------------------------------------------------

function LiveSwitch({ overview: o, say, onChanged }: { overview: IbkrOverview; say: Say; onChanged: () => void }) {
  const [code, setCode] = useState<{ code: string; expires_at: string } | null>(null), [typed, setTyped] = useState(''), [busy, setBusy] = useState(false);
  const live = o.settings.live;
  async function act(fn: () => Promise<unknown>, done: string, after?: () => void) {
    setBusy(true);
    try { await fn(); say(done); after?.(); onChanged(); } catch (e) { say(describeError(e), true); } finally { setBusy(false); }
  }
  return <section className="surface cmPanel" aria-labelledby="ibkr-live">
    <h2 id="ibkr-live"><Power aria-hidden="true" className="cmInlineIcon" />{t('ibkr.live.title')} <ModeBadge mode={o.mode} /></h2>
    <p className="muted">{t('ibkr.live.intro', { orders: o.paper_min_orders, days: o.paper_min_days, hours: o.settings.cooling_off_hours })}</p>
    <h3 className="finSubHead">{t('ibkr.live.proof')}</h3>
    <Checks items={o.paper_proof} />
    <h3 className="finSubHead">{t('ibkr.live.gates')}</h3>
    <Checks items={o.live_gates} />
    {live.on ? <>
      <p className="notice errorNotice" role="status"><ShieldAlert aria-hidden="true" /><span>{live.effective_at ? t('ibkr.live.onSince', { date: formatDateTime(live.effective_at) }) : t('ibkr.live.on')}</span></p>
      <div className="actions"><button type="button" className="danger" disabled={busy} onClick={() => void act(() => api.liveOff(), t('ibkr.live.turnedOff'))}><Power aria-hidden="true" />{t('ibkr.live.off')}</button></div>
    </> : code ? <form className="finForm" onSubmit={(e) => { e.preventDefault(); void act(() => api.confirmLive(typed), t('ibkr.live.turnedOn'), () => setCode(null)); }}>
      <p className="ibkrCode" aria-live="polite">{t('ibkr.live.codeShown')} <strong dir="ltr">{code.code}</strong></p>
      <label className="field"><span>{t('ibkr.live.typeCode')}</span><input value={typed} dir="ltr" autoComplete="off" maxLength={12} required onChange={(e) => setTyped(e.target.value.toUpperCase())} /></label>
      <div className="actions"><button type="submit" className="danger" disabled={busy || typed.length < 4}>{t('ibkr.live.confirm')}</button><button type="button" className="secondary" onClick={() => setCode(null)}>{t('common.cancel')}</button></div>
    </form> : <div className="actions"><button type="button" disabled={busy || o.switch_gates.some((g) => !g.ok)} onClick={() => void act(async () => setCode(await api.requestLive()), t('ibkr.live.codeReady'))}>{t('ibkr.live.request')}</button>
      {o.switch_gates.some((g) => !g.ok) && <small className="muted">{t('ibkr.live.notYet')}</small>}</div>}
  </section>;
}

// -- audit trail ----------------------------------------------------------------------------------------

function Trail() {
  const [offset, setOffset] = useState(0), [state, setState] = useState<Load<{ items: IbkrEvent[]; total: number }>>({ kind: 'loading' });
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await api.listEvents(false, PAGE, offset) }); } catch (e) { setState(failed(e)); }
  }, [offset]);
  useEffect(() => { void load(); }, [load]);
  let body: ReactNode = null;
  if (state.kind === 'loading') body = <Notice busy>{t('ibkr.loading')}</Notice>;
  else if (state.kind !== 'ready') body = <LoadError message={state.message} retry={() => void load()} />;
  else body = state.value.items.length === 0 ? <p className="muted">{t('ibkr.trail.none')}</p> : <>
    <ul className="cmList finItems">{state.value.items.map((e) => <li key={e.id}><div><strong>{e.message}</strong><small>{formatDateTime(e.created_at)}{e.environment ? ` · ${t(`ibkr.mode.${e.environment}` as MessageKey)}` : ''}</small></div></li>)}</ul>
    <Pager offset={offset} total={state.value.total} onPage={setOffset} /></>;
  return <section className="surface cmPanel" aria-labelledby="ibkr-trail">
    <h2 id="ibkr-trail"><ListChecks aria-hidden="true" className="cmInlineIcon" />{t('ibkr.trail.title')}</h2>{body}</section>;
}
