// Finance stages 3 to 5 (docs/finance.md): Safe to spend, the Guardian rules and read-only
// connections. Every figure is the person's own and comes from the API; nothing here moves money.
// A Guardian change applies only when the person confirms it with the payload hash shown.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { AlertTriangle, Gauge, Landmark, Plug, Plus, RefreshCw, Scale, Settings2, Trash2, Unplug } from 'lucide-react';
import type { FinanceConnection, FinanceCurrencySafeToSpend, FinanceFxRate, FinancePlan, FinanceProvider, FinanceSafeLine, FinanceSafeToSpend } from '@anum/contracts';
import * as api from './lib/financeApi';
import { SAFE_GROUPS, formatAmount, isolate, parseAmount, safeNote, type FinanceTab, type LimitDraft } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => Promise<void>] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setState({ kind: 'ready', value: await load() }); } catch (e) { setState(failed(e)); }
  }, [load]);
  useEffect(() => { void run(); }, [run]);
  return [state, run];
}

// ---- Stage 3: safe-to-spend ------------------------------------------------------------------------

/** The overview's short version: the base currency's figure and the way to the full build. */
export function SafeToSpendCard({ base, onOpen }: { base: string; onOpen: (tab: FinanceTab) => void }) {
  const [state, reload] = useLoad(api.getSafeToSpend);
  const headingId = useId();
  const row = state.kind === 'ready' ? state.value.currencies.find((c) => c.currency === base) ?? state.value.currencies[0] : undefined;
  return <section className="surface cmPanel finSafeCard" aria-labelledby={headingId}>
    <div className="sectionHeader"><h2 id={headingId}>{t('finance.tab.safe')}</h2><button type="button" className="cmLinkButton" onClick={() => onOpen('safe')}>{t('finance.safe.howBuilt')}</button></div>
    {state.kind === 'loading' && <Notice busy>{t('finance.safe.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={t('finance.safe.error', { message: state.message })} retry={() => void reload()} />}
    {state.kind === 'ready' && (!row ? <p className="muted">{t('finance.safe.emptyShort')}</p> : <div className="finSafeFigure">
      <strong className={row.short ? 'finShort' : ''}><Money value={row.short ? row.safe_to_spend.replace(/^-/, '') : row.safe_to_spend} currency={row.currency} /></strong>
      <span className="muted">{row.short ? t('finance.safe.shortBy') : t('finance.safe.perDay', { amount: isolate(formatAmount(row.per_day, row.currency)), count: state.value.days_left })}</span>
    </div>)}
  </section>;
}

export function SafeToSpendPanel({ onOpen, onChanged }: { onOpen: (tab: FinanceTab) => void; onChanged: () => void }) {
  const [state, reload] = useLoad(api.getSafeToSpend);
  const [settings, setSettings] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  return <section className="surface cmPanel" aria-labelledby="fin-safe">
    <div className="sectionHeader"><h2 id="fin-safe"><Gauge aria-hidden="true" className="cmInlineIcon" />{t('finance.tab.safe')}</h2>
      <div className="finHeadActions"><button type="button" className="secondary" onClick={() => setSettings(true)}><Settings2 aria-hidden="true" />{t('finance.safe.settings')}</button></div>
    </div>
    {notice && <Notice>{notice}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('finance.safe.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('finance.safe.error', { message: state.message })} retry={() => void reload()} />}
    {state.kind === 'ready' && <SafeBody data={state.value} onOpen={onOpen} />}
    {settings && <PlanDialog onClose={(saved) => { setSettings(false); if (saved) { setNotice(t('finance.safe.saved')); void reload(); onChanged(); } }} />}
  </section>;
}

function SafeBody({ data, onOpen }: { data: FinanceSafeToSpend; onOpen: (tab: FinanceTab) => void }) {
  if (data.currencies.length === 0) {
    return <Empty icon={<Landmark aria-hidden="true" />} title={t('finance.safe.empty')} text={t('finance.safe.emptyText')}
      action={<button type="button" onClick={() => onOpen('accounts')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} />;
  }
  return <>
    <p className="muted finSafeIntro">{t('finance.safe.intro', { end: isolate(data.month_end), count: data.days_left })}</p>
    <div className="finSafeGrid">{data.currencies.map((row) => <SafeCurrency key={row.currency} row={row} days={data.days_left} />)}</div>
    {data.combined_status === 'ready' && data.combined && <section className="finSafeCombined" aria-labelledby="fin-safe-combined">
      <h3 id="fin-safe-combined">{t('finance.safe.combined', { currency: data.combined.currency })}</h3>
      <p className="finSafeFigure"><strong><Money value={data.combined.safe_to_spend} currency={data.combined.currency} signed /></strong>
        <span className="muted">{t('finance.safe.perDay', { amount: isolate(formatAmount(data.combined.per_day, data.combined.currency)), count: data.days_left })}</span></p>
      <ul className="cmList finRates">{data.combined.conversions.map((c) => <li key={c.from_currency}>
        <div><strong>{t('finance.safe.conversion', { from: isolate(formatAmount(c.amount, c.from_currency)), rate: isolate(c.rate.replace(/0+$/, '').replace(/\.$/, '')), to: isolate(formatAmount(c.converted, c.to_currency)) })}</strong>
          <small dir="auto">{t('finance.safe.rateSource', { date: isolate(c.as_of), source: c.source })}{c.via ? ` · ${t('finance.safe.via', { currency: c.via })}` : ''}</small></div>
      </li>)}</ul>
    </section>}
    {data.combined_status === 'missing_rates' && <div className="notice finSafeMissing" role="status"><AlertTriangle aria-hidden="true" />
      <span>{t('finance.safe.missingRates', { pairs: isolate(data.missing_rates.join(', ')), days: data.fx_max_age_days })}</span>
      <button type="button" className="secondary" onClick={() => onOpen('connections')}>{t('finance.safe.openRates')}</button></div>}
    {data.excluded.length > 0 && <details className="finExcluded"><summary>{t('finance.safe.notCounted', { count: data.excluded.length })}</summary>
      <ul className="cmList">{data.excluded.map((line) => <li key={`${line.kind}-${line.ref_id}`}><div><strong dir="auto">{line.label}</strong><small>{safeNote(line)}</small></div>
        {line.amount !== '0' && Number(line.amount) !== 0 ? <Money value={line.amount} currency={line.currency} /> : null}</li>)}</ul>
    </details>}
    <p className="muted finReadOnly">{t('finance.safe.disclaimer')}</p>
  </>;
}

/** A positive deduction total shown as the minus it is ("150.00" to "-150.00"; zero stays zero). */
function negate(value: string): string {
  if (Number(value) === 0) return value;
  return value.startsWith('-') ? value.slice(1) : `-${value}`;
}

function SafeCurrency({ row, days }: { row: FinanceCurrencySafeToSpend; days: number }) {
  const titleId = useId();
  const groups = SAFE_GROUPS.map((kind) => ({ kind, lines: row.lines.filter((l) => l.kind === kind) })).filter((g) => g.lines.length > 0);
  const totals: Record<string, string> = { balance: row.available, bill: row.bills, minimum_payment: row.minimum_payments, goal: row.goals, buffer: row.buffer };
  return <article className="finSafeCurrency" aria-labelledby={titleId}>
    <h3 id={titleId} className="visuallyHidden">{t('finance.safe.inCurrency', { currency: row.currency })}</h3>
    <div className="finSafeFigure">
      <span className="muted">{row.short ? t('finance.safe.shortBy') : t('finance.safe.left', { currency: row.currency })}</span>
      <strong className={row.short ? 'finShort' : ''}><Money value={row.short ? row.safe_to_spend.replace(/^-/, '') : row.safe_to_spend} currency={row.currency} /></strong>
      <span className="muted">{row.short ? t('finance.safe.shortHint') : t('finance.safe.perDay', { amount: isolate(formatAmount(row.per_day, row.currency)), count: days })}</span>
    </div>
    <table className="finBuild">
      <caption className="visuallyHidden">{t('finance.safe.buildCaption', { currency: row.currency })}</caption>
      {groups.map((group) => <tbody key={group.kind}>
        <tr className="finBuildGroup"><th scope="rowgroup" colSpan={2}>{t(`finance.safe.group.${group.kind}` as MessageKey)}</th>
          <td className="num"><Money value={group.kind === 'balance' ? totals[group.kind] : negate(totals[group.kind])} currency={row.currency} signed /></td></tr>
        {group.lines.map((line: FinanceSafeLine, index: number) => <tr key={`${line.ref_id ?? line.label}-${index}`}>
          <th scope="row" dir="auto">{line.kind === 'buffer' ? t('finance.safe.buffer') : line.label}</th>
          <td className="finBuildNote">{safeNote(line)}</td>
          <td className="num"><Money value={line.amount} currency={line.currency} /></td>
        </tr>)}
      </tbody>)}
      <tfoot><tr><th scope="row" colSpan={2}>{t('finance.safe.result')}</th><td className="num"><strong><Money value={row.safe_to_spend} currency={row.currency} signed /></strong></td></tr></tfoot>
    </table>
  </article>;
}

function PlanDialog({ onClose }: { onClose: (saved: boolean) => void }) {
  const [state, setState] = useState<Load<FinancePlan>>({ kind: 'loading' });
  const [buffers, setBuffers] = useState<LimitDraft[]>([]), [combine, setCombine] = useState(false), [age, setAge] = useState('7');
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId(), ageHelp = useId();
  useEffect(() => {
    void api.getPlan().then((plan) => {
      setState({ kind: 'ready', value: plan }); setBuffers(plan.buffers.map((b) => ({ currency: b.currency, amount: b.amount }))); setCombine(plan.combine_currencies); setAge(String(plan.fx_max_age_days));
    }).catch((e) => setState(failed(e)));
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const rows: { currency: string; amount: string }[] = [];
    for (const row of buffers) {
      if (!row.currency.trim() && !row.amount.trim()) continue;
      const currency = row.currency.trim().toUpperCase();
      if (!/^[A-Z]{3}$/.test(currency)) { setError(t('finance.error.currency')); return; }
      if (rows.some((r) => r.currency === currency)) { setError(t('finance.guardian.error.twice', { currency })); return; }
      const amount = parseAmount(row.amount || '0', currency, { signed: false });
      if (!amount.ok) { setError(amount.error); return; }
      rows.push({ currency, amount: amount.value });
    }
    const days = Number(age);
    if (!Number.isInteger(days) || days < 1 || days > 60) { setError(t('finance.safe.error.age')); return; }
    setBusy(true); setError(null);
    try { await api.savePlan({ buffers: rows, combine_currencies: combine, fx_max_age_days: days }); onClose(true); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('finance.safe.settingsTitle')} className="formDialog" onClose={() => onClose(false)}>
    {state.kind === 'loading' && <Notice busy>{t('finance.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <Notice error>{state.message}</Notice>}
    {state.kind === 'ready' && <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <fieldset className="finMapping"><legend>{t('finance.safe.buffers')}</legend>
        <p className="muted">{t('finance.safe.buffersText')}</p>
        <LimitRows rows={buffers} onChange={setBuffers} label={t('finance.safe.bufferAmount')} addLabel={t('finance.safe.addBuffer')} />
      </fieldset>
      <label className="finCheck"><input type="checkbox" checked={combine} onChange={(e) => setCombine(e.target.checked)} />{t('finance.safe.combine')}</label>
      <label className="field"><span>{t('finance.safe.maxAge')}</span><input type="number" min={1} max={60} value={age} onChange={(e) => setAge(e.target.value)} aria-describedby={ageHelp} dir="ltr" /></label>
      <p id={ageHelp} className="muted finHelp">{t('finance.safe.maxAgeHelp')}</p>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(false)} />
    </form>}
  </Modal>;
}

/** Rows of currency and amount (buffers and the Guardian's per-currency limits). */
export function LimitRows({ rows, onChange, label, addLabel }: { rows: LimitDraft[]; onChange: (rows: LimitDraft[]) => void; label: string; addLabel: string }) {
  return <div className="finLimitRows">
    {rows.map((row, index) => <div key={index} className="cmFilters finLimitRow">
      <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={row.currency} maxLength={3} dir="ltr" className="finCurrencyInput"
        onChange={(e) => onChange(rows.map((r, i) => (i === index ? { ...r, currency: e.target.value.toUpperCase().slice(0, 3) } : r)))} /></label>
      <label className="cmInline"><span>{label}</span><input value={row.amount} inputMode="decimal" dir="ltr"
        onChange={(e) => onChange(rows.map((r, i) => (i === index ? { ...r, amount: e.target.value } : r)))} /></label>
      <button type="button" className="iconButton" onClick={() => onChange(rows.filter((_, i) => i !== index))} aria-label={t('finance.guardian.removeRow', { currency: row.currency || String(index + 1) })}><Trash2 aria-hidden="true" /></button>
    </div>)}
    <button type="button" className="secondary" disabled={rows.length >= 20} onClick={() => onChange([...rows, { currency: 'AED', amount: '' }])}><Plus aria-hidden="true" />{addLabel}</button>
  </div>;
}

// ---- Stage 4: Guardian lives in FinanceGuardian.tsx (a lazy chunk); its reason text is shared here. ----
export { reasonText } from './lib/financeGuardian';

// ---- Stage 5: read-only connections ----------------------------------------------------------------

/** A source's name, summary, terms or owner action in the person's language (the server's text otherwise). */
function providerText(provider: FinanceProvider, field: 'name' | 'summary' | 'terms' | 'owner_action'): string {
  const key = `finance.provider.${provider.id}.${field}` as MessageKey;
  const text = t(key);
  return text === key ? provider[field] ?? '' : text;
}

function providerName(connection: FinanceConnection): string {
  const key = `finance.provider.${connection.provider}.name` as MessageKey;
  const text = t(key);
  return text === key ? connection.provider_name : text;
}

function statusTone(status: FinanceConnection['status']): string {
  return status === 'active' ? 'success' : status === 'error' ? 'warning' : 'neutral';
}

export function ConnectionsPanel() {
  const [connections, reloadConnections] = useLoad(api.listConnections);
  const [providers, reloadProviders] = useLoad(api.listProviders);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<FinanceConnection | null>(null);
  const [connecting, setConnecting] = useState<FinanceProvider | null>(null);
  const [ratesVersion, setRatesVersion] = useState(0);
  const say = (text: string, error = false) => setNotice({ text, error });
  async function act(connection: FinanceConnection, action: 'sync' | 'disconnect') {
    setBusy(connection.id);
    try {
      if (action === 'sync') {
        const report = await api.syncConnection(connection.id);
        say(report.status === 'ok' ? t('finance.connections.synced', { count: report.counts.rates_new ?? 0, rows: report.counts.transactions_new ?? 0 }) : t('finance.connections.syncFailed', { message: report.message }), report.status !== 'ok');
        setRatesVersion((v) => v + 1);
      } else { await api.disconnectConnection(connection.id); say(t('finance.connections.disconnected', { name: connection.label })); }
      await reloadConnections();
    } catch (e) { say(describeError(e), true); } finally { setBusy(null); }
  }
  async function connect(provider: FinanceProvider) {
    if (provider.needs_credentials) { setConnecting(provider); return; }
    setBusy(provider.id);
    try { await api.createConnection({ provider: provider.id }); say(t('finance.connections.connected', { name: providerText(provider, 'name') })); await reloadConnections(); }
    catch (e) { say(describeError(e), true); } finally { setBusy(null); }
  }
  async function confirmRemove() {
    if (!removing) return;
    setBusy(removing.id);
    try { await api.deleteConnection(removing.id); say(t('finance.connections.deleted', { name: removing.label })); setRatesVersion((v) => v + 1); await reloadConnections(); }
    catch (e) { say(describeError(e), true); } finally { setBusy(null); setRemoving(null); }
  }
  const mine = connections.kind === 'ready' ? connections.value : [];
  const connected = new Set(mine.filter((c) => c.status !== 'disconnected').map((c) => c.provider));
  return <>
    <section className="surface cmPanel" aria-labelledby="fin-connections">
      <h2 id="fin-connections"><Plug aria-hidden="true" className="cmInlineIcon" />{t('finance.connections.title')}</h2>
      <p className="muted">{t('finance.connections.intro')}</p>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {connections.kind === 'loading' && <Notice busy>{t('finance.connections.loading')}</Notice>}
      {connections.kind === 'denied' && <Denied />}
      {connections.kind === 'error' && <LoadError message={t('finance.connections.error', { message: connections.message })} retry={() => void reloadConnections()} />}
      {connections.kind === 'ready' && (mine.length === 0 ? <p className="muted">{t('finance.connections.none')}</p>
        : <ul className="cmList finItems">{mine.map((c) => <li key={c.id}>
          <div><strong dir="auto">{c.label}<span className={`statusBadge ${statusTone(c.status)}`}>{t(`finance.connections.status.${c.status}` as MessageKey)}</span></strong>
            <small>{providerName(c)} · <bdi dir="ltr">{c.scopes.join(', ')}</bdi>{c.has_credentials ? ` · ${t('finance.connections.credentialsSaved')}` : ''}</small>
            <small>{c.last_success_at ? t('finance.connections.lastSync', { date: formatDateTime(c.last_success_at) }) : t('finance.connections.neverSynced')}
              {c.status !== 'disconnected' ? ` · ${t('finance.connections.nextSync', { date: formatDateTime(c.next_sync_at) })}` : ''}</small>
            {c.last_error && <small className="finLoosens" dir="auto">{c.last_error}</small>}</div>
          <div className="finItemEnd">
            {c.status !== 'disconnected' && <button type="button" className="secondary" disabled={busy === c.id} onClick={() => void act(c, 'sync')}><RefreshCw aria-hidden="true" />{t('finance.connections.sync')}</button>}
            {c.status !== 'disconnected' && <button type="button" className="secondary" disabled={busy === c.id} onClick={() => void act(c, 'disconnect')}><Unplug aria-hidden="true" />{t('finance.connections.disconnect')}</button>}
            <button type="button" className="iconButton small" onClick={() => setRemoving(c)} aria-label={t('finance.connections.delete', { name: c.label })}><Trash2 aria-hidden="true" /></button>
          </div>
        </li>)}</ul>)}
    </section>
    <section className="surface cmPanel" aria-labelledby="fin-sources">
      <h2 id="fin-sources">{t('finance.connections.sources')}</h2>
      {providers.kind === 'loading' && <Notice busy>{t('finance.connections.loading')}</Notice>}
      {(providers.kind === 'error' || providers.kind === 'denied') && <LoadError message={providers.message} retry={() => void reloadProviders()} />}
      {providers.kind === 'ready' && <ul className="finSources">{providers.value.map((p) => <li key={p.id} className="finSource">
        <div className="finSourceHead"><strong>{providerText(p, 'name')}</strong><span className={`statusBadge ${p.available ? 'success' : 'neutral'}`}>{p.available ? t('finance.connections.available') : t('finance.connections.needsSetup')}</span></div>
        <p>{providerText(p, 'summary')}</p>
        <p className="finScopes">{t('finance.connections.readOnlyScopes')} {p.scopes.map((s) => <code key={s} dir="ltr">{s}</code>)}</p>
        {p.terms && <p className="muted finHelp">{providerText(p, 'terms')}</p>}
        {!p.available && p.owner_action && <p className="muted finHelp"><strong>{t('finance.connections.ownerAction')}</strong> {providerText(p, 'owner_action')}</p>}
        {p.available && (connected.has(p.id) && !p.needs_credentials
          ? <p className="muted finHelp">{t('finance.connections.alreadyConnected')}</p>
          : <button type="button" className="cmPrimary" disabled={busy === p.id || connections.kind !== 'ready'} onClick={() => void connect(p)}><Plug aria-hidden="true" />{t('finance.connections.connect')}</button>)}
      </li>)}</ul>}
    </section>
    <Rates version={ratesVersion} />
    {connecting && <CredentialsDialog provider={connecting} onClose={(text) => { setConnecting(null); if (text) { say(text); void reloadConnections(); } }} />}
    {removing && <ConfirmDialog title={t('finance.connections.deleteTitle')} confirmLabel={t('finance.delete')} danger busy={busy === removing.id} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p dir="auto">{t('finance.connections.deleteText', { name: removing.label })}</p>
    </ConfirmDialog>}
  </>;
}

function CredentialsDialog({ provider, onClose }: { provider: FinanceProvider; onClose: (message: string | null) => void }) {
  const [values, setValues] = useState<Record<string, string>>({}), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null);
    try { await api.createConnection({ provider: provider.id, credentials: values }); onClose(t('finance.connections.connected', { name: providerText(provider, 'name') })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('finance.connections.connectTitle', { name: providerText(provider, 'name') })} className="formDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <p className="muted">{t('finance.connections.credentialsText')}</p>
      {provider.credential_fields.map((field) => <label key={field} className="field"><span dir="ltr">{field}</span>
        <input type="password" autoComplete="off" value={values[field] ?? ''} onChange={(e) => setValues({ ...values, [field]: e.target.value })} required dir="ltr" /></label>)}
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} label={t('finance.connections.connect')} />
    </form>
  </Modal>;
}

function Rates({ version }: { version: number }) {
  const [state, setState] = useState<Load<FinanceFxRate[]>>({ kind: 'loading' });
  const [filter, setFilter] = useState(''), [adding, setAdding] = useState(false), [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const load = useCallback(async () => { try { setState({ kind: 'ready', value: await api.listRates(200) }); } catch (e) { setState(failed(e)); } }, []);
  useEffect(() => { void load(); }, [load, version]);
  const shown = useMemo(() => {
    if (state.kind !== 'ready') return [];
    const code = filter.trim().toUpperCase();
    return state.value.filter((r) => !code || r.base.includes(code) || r.quote.includes(code)).slice(0, 50);
  }, [state, filter]);
  async function remove(rate: FinanceFxRate) {
    try { await api.deleteRate(rate.id); setNotice({ text: t('finance.rates.deleted') }); await load(); } catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  return <section className="surface cmPanel" aria-labelledby="fin-rates">
    <div className="sectionHeader"><h2 id="fin-rates">{t('finance.rates.title')}</h2><button type="button" className="secondary" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('finance.rates.add')}</button></div>
    <p className="muted">{t('finance.rates.intro')}</p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('finance.rates.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={state.message} retry={() => void load()} />}
    {state.kind === 'ready' && (state.value.length === 0 ? <Empty icon={<Scale aria-hidden="true" />} title={t('finance.rates.empty')} text={t('finance.rates.emptyText')} />
      : <>
        <label className="cmInline cmSearch finRateFilter"><span>{t('finance.rates.filter')}</span><input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} maxLength={3} dir="ltr" placeholder="USD" /></label>
        <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable finRateTable">
          <thead><tr><th scope="col">{t('finance.rates.pair')}</th><th scope="col" className="num">{t('finance.rates.rate')}</th><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('finance.rates.source')}</th><th scope="col"><span className="visuallyHidden">{t('finance.col.actions')}</span></th></tr></thead>
          <tbody>{shown.map((r) => <tr key={r.id}>
            <td><bdi dir="ltr">1 {r.base} = {r.quote}</bdi></td>
            <td className="num"><bdi dir="ltr">{r.rate.replace(/0+$/, '').replace(/\.$/, '')}</bdi></td>
            <td><bdi dir="ltr">{r.as_of}</bdi></td>
            <td className="finRateSource">{r.source === 'manual' ? t('finance.rates.manual') : r.source}</td>
            <td className="finRowActions">{r.source === 'manual' && <button type="button" className="iconButton small" onClick={() => void remove(r)} aria-label={t('finance.rates.delete', { pair: `${r.base}/${r.quote}` })}><Trash2 aria-hidden="true" /></button>}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="muted cmPagerText">{t('finance.rates.shown', { shown: shown.length, total: state.value.length })}</p>
      </>)}
    {adding && <RateDialog onClose={(text) => { setAdding(false); if (text) { setNotice({ text }); void load(); } }} />}
  </section>;
}

function RateDialog({ onClose }: { onClose: (message: string | null) => void }) {
  const [base, setBase] = useState('USD'), [quote, setQuote] = useState('AED'), [rate, setRate] = useState(''), [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!/^[A-Z]{3}$/.test(base) || !/^[A-Z]{3}$/.test(quote)) { setError(t('finance.error.currency')); return; }
    if (base === quote) { setError(t('finance.rates.error.same')); return; }
    if (!/^\d{1,8}(\.\d{1,10})?$/.test(rate.trim()) || Number(rate) <= 0) { setError(t('finance.rates.error.rate')); return; }
    setBusy(true); setError(null);
    try { await api.addRate({ base, quote, rate: rate.trim(), as_of: asOf }); onClose(t('finance.rates.added')); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('finance.rates.add')} className="formDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <p className="muted">{t('finance.rates.addText')}</p>
      <div className="cmFormGrid">
        <label className="field"><span>{t('finance.rates.from')}</span><input value={base} onChange={(e) => setBase(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
        <label className="field"><span>{t('finance.rates.to')}</span><input value={quote} onChange={(e) => setQuote(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
        <label className="field"><span>{t('finance.rates.rateLabel', { base, quote })}</span><input value={rate} onChange={(e) => setRate(e.target.value)} inputMode="decimal" dir="ltr" placeholder="3.6725" required /></label>
        <label className="field"><span>{t('finance.col.date')}</span><input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} required /></label>
      </div>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} />
    </form>
  </Modal>;
}

