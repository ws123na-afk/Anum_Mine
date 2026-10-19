// Finance stages 3 to 5 (docs/finance.md): Safe to spend, the Guardian rules and read-only
// connections. Every figure is the person's own and comes from the API; nothing here moves money.
// A Guardian change applies only when the person confirms it with the payload hash shown.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import {
  AlertTriangle, CalendarClock, Check, Gauge, Landmark, Plug, Plus, RefreshCw, Scale, Settings2, ShieldCheck, Trash2, Unplug, X,
} from 'lucide-react';
import type {
  FinanceCategory, FinanceConnection, FinanceCurrencySafeToSpend, FinanceEvaluation, FinanceFxRate, FinanceGuardian, FinanceGuardianAlerts,
  FinanceGuardianChange, FinanceGuardianVersion, FinancePlan, FinanceProvider, FinanceSafeLine, FinanceSafeToSpend,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import {
  SAFE_GROUPS, categoryLabel, describeRules, draftFromRules, expiresLabel, formatAmount, isolate, parseAmount, ruleKindLabel,
  rulesFromDraft, safeNote, shortHash, type FinanceTab, type LimitDraft, type RulesDraft,
} from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';

const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'cash', 'other',
];

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
function LimitRows({ rows, onChange, label, addLabel }: { rows: LimitDraft[]; onChange: (rows: LimitDraft[]) => void; label: string; addLabel: string }) {
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

// ---- Stage 4: Guardian ----------------------------------------------------------------------------

export function GuardianPanel({ base }: { base: string }) {
  const [state, reload] = useLoad(api.getGuardian);
  const [alerts, reloadAlerts] = useLoad(api.guardianAlerts);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const refresh = () => { void reload(); void reloadAlerts(); };
  if (state.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('finance.guardian.loading')}</Notice></section>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <section className="surface cmPanel"><LoadError message={t('finance.guardian.error', { message: state.message })} retry={() => void reload()} /></section>;
  const guardian = state.value;
  const lines = describeRules(guardian.rules);
  return <>
    <section className="surface cmPanel" aria-labelledby="fin-guardian">
      <div className="sectionHeader"><h2 id="fin-guardian"><ShieldCheck aria-hidden="true" className="cmInlineIcon" />{t('finance.guardian.title')}</h2>
        <button type="button" className="cmPrimary" onClick={() => setEditing(true)}>{lines.length ? t('finance.guardian.change') : t('finance.guardian.set')}</button></div>
      <p className="muted">{t('finance.guardian.intro')}</p>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {guardian.pending.length > 0 && <Pending changes={guardian.pending} current={guardian.rules} onDone={(text, error) => { setNotice({ text, error }); refresh(); }} />}
      {lines.length === 0
        ? <Empty icon={<ShieldCheck aria-hidden="true" />} title={t('finance.guardian.empty')} text={t('finance.guardian.emptyText')}
          action={<button type="button" onClick={() => setEditing(true)}><Plus aria-hidden="true" />{t('finance.guardian.set')}</button>} />
        : <>
          <p className="finVersion"><span className="statusBadge neutral">{t('finance.guardian.version', { version: guardian.version })}</span>
            {guardian.confirmed_at && <small className="muted">{t('finance.guardian.confirmedAt', { date: formatDateTime(guardian.confirmed_at) })}</small>}</p>
          <ul className="finRules">{lines.map((line) => <li key={line}><Check aria-hidden="true" />{line}</li>)}</ul>
        </>}
      {editing && <RulesEditor rules={guardian.rules} base={base} onClose={(text) => { setEditing(false); if (text) { setNotice({ text }); refresh(); } }} />}
    </section>
    <section className="surface cmPanel" aria-labelledby="fin-alerts">
      <h2 id="fin-alerts"><AlertTriangle aria-hidden="true" className="cmInlineIcon" />{t('finance.guardian.alerts')}</h2>
      {alerts.kind === 'loading' && <Notice busy>{t('finance.guardian.loading')}</Notice>}
      {(alerts.kind === 'error' || alerts.kind === 'denied') && <LoadError message={alerts.message} retry={() => void reloadAlerts()} />}
      {alerts.kind === 'ready' && <AlertList data={alerts.value} hasRules={lines.length > 0} />}
    </section>
    <CheckSpend base={base} />
    <History version={guardian.version} />
  </>;
}

function alertText(alert: FinanceGuardianAlerts['alerts'][number]): string {
  const p = alert.params;
  const money = (amount: string | undefined) => isolate(formatAmount(amount ?? '0', p.currency ?? 'AED'));
  switch (alert.code) {
    case 'cap_over': case 'cap_near':
      return t(`finance.guardian.alert.${alert.code}`, { category: categoryLabel(p.category as FinanceCategory), spent: money(p.spent), limit: money(p.limit) });
    case 'merchant_blocked_seen': return t('finance.guardian.alert.merchant', { count: Number(p.count), merchant: p.merchant ?? '' });
    case 'category_blocked_seen': return t('finance.guardian.alert.category', { count: Number(p.count), category: categoryLabel(p.category as FinanceCategory) });
    case 'daily_over': return t('finance.guardian.alert.daily', { count: Number(p.count), limit: money(p.limit) });
    default: return t('finance.guardian.alert.single', { count: Number(p.count), limit: money(p.limit) });
  }
}

function AlertList({ data, hasRules }: { data: FinanceGuardianAlerts; hasRules: boolean }) {
  if (!hasRules) return <p className="muted">{t('finance.guardian.alertsNoRules')}</p>;
  if (data.alerts.length === 0) return <p className="muted">{t('finance.guardian.alertsNone')}</p>;
  return <ul className="cmList finAlerts">{data.alerts.map((alert, index) => <li key={`${alert.code}-${index}`} className={alert.severity === 'over' ? 'finAlertOver' : 'finAlertNear'}>
    <div><strong>{alertText(alert)}</strong><small>{ruleKindLabel(alert.rule)}</small></div>
    <span className={`statusBadge ${alert.severity === 'over' ? 'warning' : 'neutral'}`}>{t(`finance.guardian.severity.${alert.severity}` as MessageKey)}</span>
  </li>)}</ul>;
}

function Pending({ changes, current, onDone }: { changes: FinanceGuardianChange[]; current: FinanceGuardian['rules']; onDone: (text: string, error?: boolean) => void }) {
  const [reviewing, setReviewing] = useState<FinanceGuardianChange | null>(null), [busy, setBusy] = useState(false);
  async function reject(change: FinanceGuardianChange) {
    try { await api.rejectChange(change.id); onDone(t('finance.guardian.rejected')); } catch (e) { onDone(describeError(e), true); }
  }
  async function confirm() {
    if (!reviewing) return;
    setBusy(true);
    try { const result = await api.confirmChange(reviewing.id, reviewing.payload_hash); onDone(t('finance.guardian.confirmed', { version: result.version })); }
    catch (e) { onDone(describeError(e), true); } finally { setBusy(false); setReviewing(null); }
  }
  return <section className="finPending" aria-labelledby="fin-pending">
    <h3 id="fin-pending">{t('finance.guardian.pending', { count: changes.length })}</h3>
    <ul className="cmList">{changes.map((change) => <li key={change.id}>
      <div><strong>{change.proposed_by === 'agent' ? t('finance.guardian.byAgent') : t('finance.guardian.byYou')}</strong>
        <small>{expiresLabel(change.expires_at)}{change.reason ? ` · ` : ''}{change.reason && <span dir="auto">{change.reason}</span>}</small>
        {change.loosens.length > 0 && <small className="finLoosens">{t('finance.guardian.loosensList', { list: change.loosens.map(ruleKindLabel).join(', ') })}</small>}</div>
      <div className="finItemEnd">
        <button type="button" onClick={() => setReviewing(change)}>{t('finance.guardian.review')}</button>
        <button type="button" className="secondary" onClick={() => void reject(change)}><X aria-hidden="true" />{t('finance.guardian.reject')}</button>
      </div>
    </li>)}</ul>
    {reviewing && <ConfirmDialog title={t('finance.guardian.reviewTitle')} confirmLabel={t('finance.guardian.confirm')} busy={busy} onConfirm={() => void confirm()} onCancel={() => setReviewing(null)}>
      <ChangeSummary change={reviewing} current={current} />
    </ConfirmDialog>}
  </section>;
}

function ChangeSummary({ change, current }: { change: FinanceGuardianChange; current: FinanceGuardian['rules'] }) {
  const before = new Set(describeRules(current));
  const after = describeRules(change.rules);
  const removed = [...before].filter((line) => !after.includes(line));
  return <div className="finChange">
    <p>{t('finance.guardian.reviewText')}</p>
    {after.length === 0 ? <p className="muted">{t('finance.guardian.noRulesAfter')}</p> : <ul className="finRules">{after.map((line) => <li key={line} className={before.has(line) ? '' : 'finRuleNew'}>
      <Check aria-hidden="true" />{line}{!before.has(line) && <span className="statusBadge neutral">{t('finance.guardian.new')}</span>}</li>)}</ul>}
    {removed.length > 0 && <><p className="finLoosens">{t('finance.guardian.removed')}</p><ul className="finRules finRemoved">{removed.map((line) => <li key={line}><X aria-hidden="true" />{line}</li>)}</ul></>}
    {change.loosens.length > 0 && <p className="notice" role="note"><AlertTriangle aria-hidden="true" />{t('finance.guardian.loosensWarning', { list: change.loosens.map(ruleKindLabel).join(', ') })}</p>}
    <p className="muted finHash">{t('finance.guardian.code')} <code dir="ltr">{shortHash(change.payload_hash)}</code></p>
  </div>;
}

function RulesEditor({ rules, base, onClose }: { rules: FinanceGuardian['rules']; base: string; onClose: (message: string | null) => void }) {
  const [draft, setDraft] = useState<RulesDraft>(() => draftFromRules(rules));
  const [reason, setReason] = useState(''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [proposed, setProposed] = useState<FinanceGuardianChange | null>(null);
  const errorId = useId();
  const set = <K extends keyof RulesDraft>(key: K, value: RulesDraft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = rulesFromDraft(draft);
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    try { setProposed(await api.proposeRules(checked.value, reason.trim())); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!proposed) return;
    setBusy(true); setError(null);
    try { const result = await api.confirmChange(proposed.id, proposed.payload_hash); onClose(t('finance.guardian.confirmed', { version: result.version })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  if (proposed) {
    return <Modal title={t('finance.guardian.reviewTitle')} className="formDialog" onClose={() => onClose(t('finance.guardian.left'))}>
      <ChangeSummary change={proposed} current={rules} />
      <FormError id={errorId} text={error} />
      <div className="actions"><button type="button" disabled={busy} onClick={() => void confirm()}><ShieldCheck aria-hidden="true" />{busy ? t('finance.saving') : t('finance.guardian.confirm')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setProposed(null)}>{t('finance.guardian.back')}</button></div>
    </Modal>;
  }
  const limitSection = (key: 'maxSingle' | 'maxDaily' | 'approvalAbove', legend: string, help: string) => <fieldset className="finMapping">
    <legend>{legend}</legend><p className="muted finHelp">{help}</p>
    <LimitRows rows={draft[key]} onChange={(rows) => set(key, rows)} label={t('finance.guardian.amount')} addLabel={t('finance.guardian.addLimit')} />
  </fieldset>;
  return <Modal title={t('finance.guardian.editTitle')} className="formDialog finRulesDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <p className="muted">{t('finance.guardian.editIntro')}</p>
      <fieldset className="finMapping"><legend>{t('finance.guardian.kind.category_caps')}</legend>
        <p className="muted finHelp">{t('finance.guardian.capsHelp')}</p>
        {draft.caps.map((cap, index) => <div key={index} className="cmFilters finLimitRow">
          <label className="cmInline"><span>{t('finance.col.category')}</span><select value={cap.category} onChange={(e) => set('caps', draft.caps.map((c, i) => (i === index ? { ...c, category: e.target.value as FinanceCategory } : c)))}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
          <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={cap.currency} maxLength={3} dir="ltr" className="finCurrencyInput" onChange={(e) => set('caps', draft.caps.map((c, i) => (i === index ? { ...c, currency: e.target.value.toUpperCase().slice(0, 3) } : c)))} /></label>
          <label className="cmInline"><span>{t('finance.guardian.perMonth')}</span><input value={cap.amount} inputMode="decimal" dir="ltr" onChange={(e) => set('caps', draft.caps.map((c, i) => (i === index ? { ...c, amount: e.target.value } : c)))} /></label>
          <button type="button" className="iconButton" onClick={() => set('caps', draft.caps.filter((_, i) => i !== index))} aria-label={t('finance.guardian.removeCap', { category: categoryLabel(cap.category) })}><Trash2 aria-hidden="true" /></button>
        </div>)}
        <button type="button" className="secondary" disabled={draft.caps.length >= 50} onClick={() => set('caps', [...draft.caps, { category: 'dining', currency: base, amount: '' }])}><Plus aria-hidden="true" />{t('finance.guardian.addCap')}</button>
      </fieldset>
      {limitSection('maxSingle', t('finance.guardian.kind.max_single'), t('finance.guardian.singleHelp'))}
      {limitSection('maxDaily', t('finance.guardian.kind.max_daily_total'), t('finance.guardian.dailyHelp'))}
      {limitSection('approvalAbove', t('finance.guardian.kind.approval_above'), t('finance.guardian.approvalHelp'))}
      <fieldset className="finMapping"><legend>{t('finance.guardian.kind.blocked_categories')}</legend>
        <div className="finChecks">{CATEGORIES.map((c) => <label key={c} className="finCheck"><input type="checkbox" checked={draft.blockedCategories.includes(c)}
          onChange={(e) => set('blockedCategories', e.target.checked ? [...draft.blockedCategories, c] : draft.blockedCategories.filter((x) => x !== c))} />{categoryLabel(c)}</label>)}</div>
      </fieldset>
      <label className="field"><span>{t('finance.guardian.kind.blocked_merchants')}</span>
        <textarea value={draft.blockedMerchants} onChange={(e) => set('blockedMerchants', e.target.value)} rows={3} maxLength={4000} dir="auto" placeholder={t('finance.guardian.merchantsPlaceholder')} /></label>
      <label className="field"><span>{t('finance.guardian.kind.allowed_currencies')}</span>
        <input value={draft.allowedCurrencies} onChange={(e) => set('allowedCurrencies', e.target.value.toUpperCase())} dir="ltr" placeholder="AED, USD" /></label>
      <fieldset className="finMapping"><legend>{t('finance.guardian.kind.quiet_hours')}</legend>
        <label className="finCheck"><input type="checkbox" checked={draft.quietOn} onChange={(e) => set('quietOn', e.target.checked)} />{t('finance.guardian.quietOn')}</label>
        {draft.quietOn && <div className="cmFormGrid">
          <label className="field"><span>{t('finance.guardian.from')}</span><input type="time" value={draft.quietStart} onChange={(e) => set('quietStart', e.target.value)} dir="ltr" /></label>
          <label className="field"><span>{t('finance.guardian.to')}</span><input type="time" value={draft.quietEnd} onChange={(e) => set('quietEnd', e.target.value)} dir="ltr" /></label>
        </div>}
      </fieldset>
      <fieldset className="finMapping"><legend>{t('finance.guardian.kind.cooling_off')}</legend>
        <label className="finCheck"><input type="checkbox" checked={draft.coolingOn} onChange={(e) => set('coolingOn', e.target.checked)} />{t('finance.guardian.coolingOn')}</label>
        {draft.coolingOn && <>
          <label className="field"><span>{t('finance.guardian.hours')}</span><input type="number" min={1} max={168} value={draft.coolingHours} onChange={(e) => set('coolingHours', e.target.value)} dir="ltr" /></label>
          <LimitRows rows={draft.coolingAbove} onChange={(rows) => set('coolingAbove', rows)} label={t('finance.guardian.atOrAbove')} addLabel={t('finance.guardian.addLimit')} />
        </>}
      </fieldset>
      <label className="field"><span>{t('finance.guardian.reason')}</span><input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} dir="auto" /></label>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} label={t('finance.guardian.reviewChange')} />
    </form>
  </Modal>;
}

function CheckSpend({ base }: { base: string }) {
  const [amount, setAmount] = useState(''), [currency, setCurrency] = useState(base), [category, setCategory] = useState<FinanceCategory | ''>(''), [merchant, setMerchant] = useState('');
  const [result, setResult] = useState<FinanceEvaluation | null>(null), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = parseAmount(amount, currency.toUpperCase(), { signed: false, allowZero: false });
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    try { setResult(await api.evaluateAction({ amount: checked.value, currency: currency.toUpperCase(), category: category || null, merchant: merchant.trim() || null })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const outcome = result?.rules_decision.outcome;
  return <section className="surface cmPanel" aria-labelledby="fin-check">
    <h2 id="fin-check"><Scale aria-hidden="true" className="cmInlineIcon" />{t('finance.guardian.check')}</h2>
    <p className="muted">{t('finance.guardian.checkText')}</p>
    <form className="cmFilters finCheckForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <label className="cmInline"><span>{t('finance.col.amount')}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" className="finCurrencyInput" /></label>
      <label className="cmInline"><span>{t('finance.col.category')}</span><select value={category} onChange={(e) => setCategory(e.target.value as FinanceCategory | '')}><option value="">{t('finance.guardian.anyCategory')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
      <label className="cmInline cmSearch"><span>{t('finance.guardian.merchant')}</span><input value={merchant} onChange={(e) => setMerchant(e.target.value)} maxLength={200} dir="auto" /></label>
      <button type="submit" className="secondary" disabled={busy}><Scale aria-hidden="true" />{t('finance.guardian.checkButton')}</button>
    </form>
    <FormError id={errorId} text={error} />
    {result && outcome && <div className={`finVerdict finVerdict-${outcome}`} role="status" aria-live="polite">
      <strong>{t(`finance.guardian.outcome.${outcome}` as MessageKey)}</strong>
      {result.rules_decision.reasons.length === 0 ? <p>{t('finance.guardian.noRuleApplies')}</p> : <ul>{result.rules_decision.reasons.map((r, i) => <li key={`${r.code}-${i}`}>{reasonText(r)}</li>)}</ul>}
      <p className="muted">{t('finance.guardian.checkNote')}</p>
    </div>}
  </section>;
}

/** A Guardian reason in the person's language (the server's English text if a code is new). */
export function reasonText(reason: FinanceEvaluation['rules_decision']['reasons'][number]): string {
  const key = `finance.guardian.reason.${reason.code}` as MessageKey;
  const p = reason.params;
  const money = (amount: string | undefined) => isolate(formatAmount(amount ?? '0', p.currency ?? 'AED'));
  const text = t(key, {
    currency: p.currency ?? '', category: p.category ? categoryLabel(p.category as FinanceCategory) : '', merchant: p.merchant ?? '',
    limit: money(p.limit), spent: money(p.spent), start: isolate(p.start ?? ''), end: isolate(p.end ?? ''), until: isolate(p.until ? formatDateTime(p.until) : ''),
    hours: p.hours ?? '',
  });
  return text === key ? reason.message : text;
}

function History({ version }: { version: number }) {
  const [items, setItems] = useState<FinanceGuardianVersion[]>([]), [total, setTotal] = useState(0), [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [message, setMessage] = useState('');
  const load = useCallback(async (offset: number) => {
    try { const page = await api.guardianVersions(10, offset); setItems((current) => (offset === 0 ? page.items : [...current, ...page.items])); setTotal(page.total); setState('ready'); }
    catch (e) { setMessage(describeError(e)); setState('error'); }
  }, []);
  useEffect(() => { void load(0); }, [load, version]);
  return <section className="surface cmPanel" aria-labelledby="fin-history">
    <h2 id="fin-history"><CalendarClock aria-hidden="true" className="cmInlineIcon" />{t('finance.guardian.history')}</h2>
    {state === 'loading' && <Notice busy>{t('finance.guardian.loading')}</Notice>}
    {state === 'error' && <LoadError message={message} retry={() => void load(0)} />}
    {state === 'ready' && (items.length === 0 ? <p className="muted">{t('finance.guardian.historyNone')}</p> : <>
      <ul className="cmList">{items.map((item) => <li key={item.id}><div><strong>{t('finance.guardian.version', { version: item.version })}</strong>
        <small>{formatDateTime(item.created_at)} · {t('finance.guardian.ruleCount', { count: describeRules(item.rules).length })}</small></div></li>)}</ul>
      {items.length < total && <button type="button" className="secondary" onClick={() => void load(items.length)}>{t('finance.guardian.more')}</button>}
    </>)}
  </section>;
}

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

