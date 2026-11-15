// Automatic matching on Finance settings (docs/finance-matching.md): the record-linking policy, the matches ANUM made
// or holds for review (link, dismiss, undo), the history backfill with its progress and results, Tamara installments with
// the payment that paid each one, and the SMS and Apple Pay capture health. Every action goes through the API, which checks
// finance:manage and links only through the one links system; nothing here moves money.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Activity, History, Link2, ListChecks, RefreshCw, Smartphone, Undo2, X } from 'lucide-react';
import type {
  FinanceAccount, FinanceBackfillRun, FinanceCaptureHealth, FinanceInstallmentMatchView, FinanceMatchOutcome, FinanceMatchRecord, FinanceMatchRecordPage,
  FinanceMatchingPolicy, FinanceMatchingPolicyView,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as fm from './lib/financeMatching';
import * as fma from './lib/financeMatchingApi';
import { describeError } from './lib/errors';
import { formatDateTime, joinList, t } from './lib/i18n';
import type { MessageKey } from './lib/messages/en';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import './financeMatching.css';

const day = (value: string | null | undefined) => (value ? formatDateTime(`${value}T12:00:00`, { dateStyle: 'medium' }) : '-');
const when = (value: string | null | undefined) => (value ? formatDateTime(value) : '-');
const toneChip = (tone: fm.Tone) => (tone === 'good' ? 'ok' : tone === 'bad' ? 'bad' : tone === 'warn' ? 'warn' : 'muted');

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(() => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    load().then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [load]);
  useEffect(() => { run(); }, [run]);
  return [state, run];
}

export function FinanceMatchingSection() {
  const [policy, reloadPolicy] = useLoad(fma.getPolicy);
  const [accounts] = useLoad(api.listAccounts);
  const [changed, setChanged] = useState(0);
  const refresh = () => setChanged((n) => n + 1);
  if (policy.kind === 'denied') return <Denied />;
  return <>
    <PolicyCard policy={policy} accounts={accounts} retry={reloadPolicy} onSaved={() => { reloadPolicy(); refresh(); }} />
    <ReviewCard changed={changed} onChanged={refresh} />
    <InstallmentsCard changed={changed} />
    <HistoryCard policy={policy} onChanged={refresh} />
    <CaptureCard />
  </>;
}

// -- the policy -------------------------------------------------------------------------------------------------------

function PolicyCard({ policy, accounts, retry, onSaved }: {
  policy: Load<FinanceMatchingPolicyView>; accounts: Load<FinanceAccount[]>; retry: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState<FinanceMatchingPolicy | null>(null);
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const minId = useId();
  const view = policy.kind === 'ready' ? policy.value : null;
  useEffect(() => { if (view) setForm(view.policy); }, [view]);
  const body = (() => {
    if (policy.kind === 'loading' || (view && !form)) return <Notice busy>{t('fmatch.loading')}</Notice>;
    if (policy.kind === 'error') return <LoadError message={t('fmatch.error.load', { message: policy.message })} retry={retry} />;
    if (!view || !form) return null;
    const set = (patch: Partial<FinanceMatchingPolicy>) => setForm({ ...form, ...patch });
    const toggle = (list: string[], value: string, on: boolean) => (on ? [...new Set([...list, value])] : list.filter((v) => v !== value));
    const dirty = fm.policyChanged(view.policy, form);
    async function save(approve: boolean, event?: FormEvent) {
      event?.preventDefault();
      setBusy(true); setFlash(null);
      try {
        const saved = await fma.savePolicy(fm.policyInput({ ...form!, revision: view!.policy.revision }, approve));
        setFlash({ text: t(saved.approved ? 'fmatch.savedApproved' : 'fmatch.saved') });
        onSaved();
      } catch (e) { setFlash({ text: t('fmatch.error.action', { message: describeError(e) }), error: true }); } finally { setBusy(false); }
    }
    const connected = view.providers.filter((p) => p.connected).map((p) => p.name);
    const others = view.providers.filter((p) => !p.connected).map((p) => p.name);
    return <form className="fmatchPolicy" onSubmit={(e) => void save(false, e)}>
      <p className={view.approved ? 'fmatchState ok' : 'fmatchState warn'} role="status">
        {view.approved && !dirty ? t('fmatch.policy.approved', { version: view.version }) : t('fmatch.policy.notApproved')}
      </p>
      <div className="fmatchSwitches">
        {fm.SWITCHES.map((key) => <label key={key} className="fmatchCheck">
          <input type="checkbox" checked={form[key]} disabled={key !== 'match_transactions' && key !== 'prevent_duplicates' && key !== 'notify_exceptions' && !form.match_transactions}
            onChange={(e) => set({ [key]: e.target.checked } as Partial<FinanceMatchingPolicy>)} />
          <span>{t(`fmatch.switch.${key}` as MessageKey)}{key === 'match_transactions' && <small className="muted">{t('fmatch.switch.match_transactionsHint')}</small>}</span>
        </label>)}
      </div>
      <ul className="fmatchFixed muted"><li>{t('fmatch.fixed.personLoans')}</li><li>{t('fmatch.fixed.categorisation')}</li></ul>
      <div className="field fmatchMin"><label htmlFor={minId}>{t('fmatch.minConfidence')}</label>
        <select id={minId} value={Number(form.min_confidence).toFixed(2)} onChange={(e) => set({ min_confidence: e.target.value })}>
          {fm.CONFIDENCE_STEPS.map((v) => <option key={v} value={v}>{fm.percent(v)}</option>)}
        </select></div>
      <fieldset className="fmatchFieldset"><legend>{t('fmatch.settled.title')}</legend>
        <p className="muted">{t('fmatch.settled.sub')}</p>
        {fm.SOURCES.map((s) => <label key={s} className="fmatchCheck"><input type="checkbox" checked={form.settled_sources.includes(s)}
          onChange={(e) => set({ settled_sources: toggle(form.settled_sources, s, e.target.checked) as FinanceMatchingPolicy['settled_sources'] })} />
          {t(`fmatch.source.${s}` as MessageKey)}</label>)}
      </fieldset>
      <fieldset className="fmatchFieldset"><legend>{t('fmatch.accounts.title')}</legend>
        <p className="muted">{t('fmatch.accounts.sub')}</p>
        {accounts.kind === 'loading' && <Notice busy>{t('fmatch.loading')}</Notice>}
        {accounts.kind === 'error' && <Notice error>{t('fmatch.error.load', { message: accounts.message })}</Notice>}
        {accounts.kind === 'ready' && (accounts.value.length === 0 ? <p className="muted">{t('fmatch.accounts.none')}</p>
          : accounts.value.filter((a) => !a.archived).map((a) => <label key={a.id} className="fmatchCheck"><input type="checkbox" checked={form.source_account_ids.includes(a.id)}
            onChange={(e) => set({ source_account_ids: toggle(form.source_account_ids, a.id, e.target.checked) })} /><span dir="auto">{a.name}</span>
            {a.last4 && <small className="muted" dir="ltr">{a.last4}</small>}</label>))}
      </fieldset>
      <p className="muted fmatchNote">{t('fmatch.spending')}</p>
      <p className="muted fmatchNote">{t('fmatch.providers', { connected: joinList(connected) || '-', others: joinList(others) || '-' })}</p>
      <details className="fmatchNever"><summary>{t('fmatch.never.title')}</summary>
        <ul>{view.never_automated.map((k) => <li key={k}>{t(`fsug.never.${k}` as MessageKey)}</li>)}</ul></details>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      <div className="actions">
        <button type="button" disabled={busy} onClick={() => void save(true)}>{t('fmatch.saveApprove')}</button>
        <button type="submit" className="secondary" disabled={busy || !dirty}>{t('fmatch.save')}</button>
      </div>
    </form>;
  })();
  return <FinCard title={t('fmatch.policy.title')} subtitle={t('fmatch.policy.subtitle')} icon={<Link2 />} className="fmatchCard fmatchWide">{body}</FinCard>;
}

// -- the matches --------------------------------------------------------------------------------------------------------

const PAGE = 10;

function ReviewCard({ changed, onChanged }: { changed: number; onChanged: () => void }) {
  const [tab, setTab] = useState<FinanceMatchOutcome>('review');
  const [limit, setLimit] = useState(PAGE);
  const [state, setState] = useState<Load<FinanceMatchRecordPage>>({ kind: 'loading' });
  const [busy, setBusy] = useState<string | null>(null), [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(() => {
    fma.listRecords(tab, limit, 0).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [tab, limit, changed]);
  useEffect(() => { load(); }, [load]);
  async function act(record: FinanceMatchRecord, action: 'accept' | 'reject' | 'undo') {
    setBusy(record.id); setFlash(null);
    try {
      await fma.decideRecord(record.id, action, record.version);
      setFlash({ text: t(action === 'accept' ? 'fmatch.review.accepted' : action === 'reject' ? 'fmatch.review.rejected' : 'fmatch.review.undone') });
      onChanged();
    } catch (e) { setFlash({ text: t('fmatch.error.action', { message: describeError(e) }), error: true }); } finally { setBusy(null); }
  }
  async function runNow() {
    setBusy('run'); setFlash(null);
    try {
      const result = await fma.runMatching();
      setFlash({ text: t('fmatch.review.ran', { linked: result.counts.auto_linked ?? 0, review: result.counts.review ?? 0 }) });
      onChanged();
    } catch (e) { setFlash({ text: t('fmatch.error.action', { message: describeError(e) }), error: true }); } finally { setBusy(null); }
  }
  const counts = state.kind === 'ready' ? state.value.counts : {};
  return <FinCard title={t('fmatch.review.title')} subtitle={t('fmatch.review.subtitle')} icon={<ListChecks />} className="fmatchCard fmatchWide"
    actions={<button type="button" className="secondary" disabled={busy !== null} onClick={() => void runNow()}><RefreshCw aria-hidden="true" />{t('fmatch.review.run')}</button>}>
    <div className="fmatchTabs" role="group" aria-label={t('fmatch.review.tabs')}>
      {fm.REVIEW_TABS.map((key) => <button key={key} type="button" className={`fmatchTab ${tab === key ? 'on' : ''}`} aria-pressed={tab === key}
        onClick={() => { setTab(key); setLimit(PAGE); }}>{t(`fmatch.review.tab.${key}` as MessageKey)}{counts[key] ? <span className="fmatchBadge">{counts[key]}</span> : null}</button>)}
    </div>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('fmatch.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('fmatch.error.load', { message: state.message })} retry={load} />}
    {state.kind === 'ready' && (state.value.items.length === 0
      ? <Empty icon={<ListChecks aria-hidden="true" />} title={t(`fmatch.review.empty.${tab}` as MessageKey)} text={t(`fmatch.review.emptyText.${tab}` as MessageKey)} />
      : <ul className="fmatchList">{state.value.items.map((r) => <RecordItem key={r.id} record={r} busy={busy === r.id} onAct={(a) => void act(r, a)} />)}</ul>)}
    {state.kind === 'ready' && state.value.total > state.value.items.length && <div className="actions">
      <button type="button" className="secondary" onClick={() => setLimit((n) => Math.min(n + PAGE, 100))} disabled={limit >= 100}>{t('fmatch.review.more')}</button></div>}
  </FinCard>;
}

function RecordItem({ record, busy, onAct }: { record: FinanceMatchRecord; busy: boolean; onAct: (action: 'accept' | 'reject' | 'undo') => void }) {
  const label = record.label || t(`fmatch.kind.${record.kind}` as MessageKey);
  const waiting = record.outcome === 'review' || record.outcome === 'stale';
  return <li className="fmatchItem">
    <div className="fmatchItemHead">
      <span className="fmatchKind">{t(`fmatch.kind.${record.kind}` as MessageKey)}</span>
      <StatusChip tone={record.evidence_state === 'verified' ? 'ok' : record.evidence_state === 'provisional' ? 'warn' : 'muted'}>
        {t(`fmatch.state.${record.evidence_state}` as MessageKey)}</StatusChip>
      <span className="muted">{t('fmatch.review.confidence', { percent: fm.percent(record.confidence) })}</span>
    </div>
    <h3 dir="auto">{label}{record.due_on && <small className="muted"> {t('fmatch.review.due', { date: day(record.due_on) })}</small>}</h3>
    <p className="fmatchWhy">{t('fmatch.review.amount', { posted: day(record.posted_on) })}<Money value={record.amount} currency={record.currency} /></p>
    <p className="muted fmatchWhy">{t(fm.reasonKey(record.reason_code) as MessageKey)}{record.competitors > 0 && <> · {t('fmatch.review.others', { count: record.competitors })}</>}</p>
    {record.flags.length > 0 && <ul className="fmatchFlags">{record.flags.map((f) => <li key={f}>{t(`fmatch.flag.${f}` as MessageKey)}</li>)}</ul>}
    {record.evidence.length > 0 && <details className="fmatchEvidence"><summary>{t('fmatch.review.evidence')}</summary>
      <ul>{record.evidence.map((e, i) => <li key={`${e.code}-${i}`}>{t(`fmatch.evidence.${e.code}` as MessageKey)}{e.value && <span className="muted" dir="auto"> ({e.value})</span>}</li>)}</ul>
    </details>}
    <div className="fmatchActions">
      {waiting && <button type="button" disabled={busy} aria-label={t('fmatch.review.acceptLabel', { label })} onClick={() => onAct('accept')}><Link2 aria-hidden="true" />{t('fmatch.review.accept')}</button>}
      {waiting && <button type="button" className="secondary" disabled={busy} aria-label={t('fmatch.review.rejectLabel', { label })} onClick={() => onAct('reject')}><X aria-hidden="true" />{t('fmatch.review.reject')}</button>}
      {(record.outcome === 'auto_linked' || record.outcome === 'accepted' || (record.outcome === 'stale' && record.link_id)) &&
        <button type="button" className="secondary" disabled={busy} aria-label={t('fmatch.review.undoLabel', { label })} onClick={() => onAct('undo')}><Undo2 aria-hidden="true" />{t('fmatch.review.undo')}</button>}
    </div>
  </li>;
}

// -- installments -------------------------------------------------------------------------------------------------------

function InstallmentsCard({ changed }: { changed: number }) {
  const [state, reload] = useLoad<FinanceInstallmentMatchView>(fma.installmentStatus);
  useEffect(() => { if (changed) reload(); }, [changed, reload]);
  return <FinCard title={t('fmatch.inst.title')} subtitle={t('fmatch.inst.subtitle')} icon={<Activity />} className="fmatchCard fmatchWide">
    {state.kind === 'loading' && <Notice busy>{t('fmatch.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('fmatch.error.load', { message: state.message })} retry={reload} />}
    {state.kind === 'ready' && (state.value.orders.length === 0
      ? <Empty icon={<Activity aria-hidden="true" />} title={t('fmatch.inst.empty')} text={t('fmatch.inst.emptyText')}
        action={<a className="button secondary" href="#finance-bills">{t('fmatch.inst.title')}</a>} />
      : <ul className="fmatchOrders">{state.value.orders.map((o) => <li key={`${o.provider}-${o.order_id}`} className="fmatchOrder">
        <div className="fmatchItemHead"><span className="fmatchKind">{o.provider}</span>
          <h3 dir="auto">{t('fmatch.inst.order', { merchant: o.merchant, order: o.order_id })}</h3></div>
        <p className="muted">{t('fmatch.inst.left')}<Money value={o.still_due} currency={o.currency} />
          {o.next_due_on && <> · {t('fmatch.inst.next', { date: day(o.next_due_on) })}</>}</p>
        <table className="fmatchTable"><tbody>{o.installments.map((r) => <tr key={r.installment}>
          <th scope="row">{t('fmatch.inst.number', { number: r.installment, total: r.total_installments })}</th>
          <td>{day(r.due_on)}</td>
          <td><Money value={r.amount} currency={r.currency} /></td>
          <td><StatusChip tone={toneChip(fm.installmentTone(r.status))}>{t(`fmatch.inst.status.${r.status}` as MessageKey)}</StatusChip></td>
        </tr>)}</tbody></table>
      </li>)}</ul>)}
  </FinCard>;
}

// -- history backfill ---------------------------------------------------------------------------------------------------

function HistoryCard({ policy, onChanged }: { policy: Load<FinanceMatchingPolicyView>; onChanged: () => void }) {
  const [runs, reload] = useLoad(fma.listRuns);
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const current = runs.kind === 'ready' ? runs.value.items[0] ?? null : null;
  const applyAllowed = policy.kind === 'ready' && fm.canApplyHistory(policy.value);
  async function step(action: () => Promise<FinanceBackfillRun>) {
    setBusy(true); setFlash(null);
    try {
      let run = await action();
      // Each call works through a few months; keep going while the page is open (the daily job carries on otherwise).
      for (let i = 0; run.status === 'running' && i < 40; i += 1) run = await fma.continueRun(run.id);
      reload(); onChanged();
    } catch (e) { setFlash({ text: t('fmatch.error.action', { message: describeError(e) }), error: true }); reload(); } finally { setBusy(false); }
  }
  async function undo(run: FinanceBackfillRun) {
    setBusy(true); setFlash(null);
    try { const done = await fma.undoRun(run.id); setFlash({ text: t('fmatch.history.undone', { count: done.links_removed }) }); reload(); onChanged(); }
    catch (e) { setFlash({ text: t('fmatch.error.action', { message: describeError(e) }), error: true }); } finally { setBusy(false); }
  }
  return <FinCard title={t('fmatch.history.title')} subtitle={t('fmatch.history.subtitle')} icon={<History />} className="fmatchCard">
    <div className="actions">
      <button type="button" className="secondary" disabled={busy} onClick={() => void step(() => fma.startRun('preview'))}>{t('fmatch.history.preview')}</button>
      <button type="button" disabled={busy || !applyAllowed} onClick={() => void step(() => fma.startRun('apply'))}>{t('fmatch.history.apply')}</button>
    </div>
    {!applyAllowed && policy.kind === 'ready' && <p className="muted">{t('fmatch.history.applyOff')}</p>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {busy && <Notice busy>{t('fmatch.loading')}</Notice>}
    {runs.kind === 'loading' && <Notice busy>{t('fmatch.loading')}</Notice>}
    {runs.kind === 'error' && <LoadError message={t('fmatch.error.load', { message: runs.message })} retry={reload} />}
    {runs.kind === 'ready' && !current && <Empty icon={<History aria-hidden="true" />} title={t('fmatch.history.empty')} text={t('fmatch.history.emptyText')} />}
    {current && <RunView run={current} busy={busy} onContinue={() => void step(() => fma.continueRun(current.id))} onUndo={() => void undo(current)} />}
    {runs.kind === 'ready' && runs.value.items.length > 1 && <details className="fmatchEvidence"><summary>{t('fmatch.history.earlier')}</summary>
      <ul>{runs.value.items.slice(1).map((r) => <li key={r.id}>{t(`fmatch.history.mode.${r.mode}` as MessageKey)} · {t(`fmatch.history.status.${r.status}` as MessageKey)} · {when(r.started_at)}</li>)}</ul>
    </details>}
  </FinCard>;
}

function RunView({ run, busy, onContinue, onUndo }: { run: FinanceBackfillRun; busy: boolean; onContinue: () => void; onUndo: () => void }) {
  const progress = fm.runProgress(run);
  const shown = fm.shownCounts(run.counts);
  return <div className="fmatchRun">
    <div className="fmatchItemHead">
      <StatusChip tone={run.status === 'done' ? 'ok' : run.status === 'failed' ? 'bad' : run.status === 'running' ? 'info' : 'muted'}>
        {t(`fmatch.history.status.${run.status}` as MessageKey)}</StatusChip>
      <span className="fmatchKind">{t(`fmatch.history.mode.${run.mode}` as MessageKey)}</span>
      <span className="muted">{t('fmatch.history.started', { time: when(run.started_at) })}</span>
    </div>
    {run.start_on && <p className="muted">{run.cursor_on ? t('fmatch.history.progress', { from: day(run.start_on), to: day(run.end_on), cursor: day(run.cursor_on) })
      : t('fmatch.history.progressStart', { from: day(run.start_on), to: day(run.end_on) })}</p>}
    <progress className="fmatchProgress" max={100} value={progress} aria-label={t('fmatch.history.progressLabel')}>{progress}%</progress>
    {run.status === 'failed' && <Notice error>{t('fmatch.history.error', { code: run.error_code ?? '-' })}</Notice>}
    {run.status === 'running' && run.error_code && <Notice error>{t('fmatch.history.retry', { code: run.error_code })}</Notice>}
    {shown.length > 0 && <dl className="fmatchCounts">{shown.map((c) => <div key={c.key}><dt>{t(`fmatch.count.${c.key}` as MessageKey)}</dt><dd>{c.value}</dd></div>)}</dl>}
    <div className="actions">
      {run.status === 'running' && <button type="button" disabled={busy} onClick={onContinue}>{t('fmatch.history.continue')}</button>}
      {run.mode === 'apply' && (run.status === 'done' || run.status === 'failed') && (run.counts.auto_linked ?? 0) > 0 &&
        <button type="button" className="secondary" disabled={busy} onClick={onUndo}><Undo2 aria-hidden="true" />{t('fmatch.history.undo')}</button>}
    </div>
  </div>;
}

// -- capture health -----------------------------------------------------------------------------------------------------

function CaptureCard() {
  const [state, reload] = useLoad<FinanceCaptureHealth>(fma.captureHealth);
  return <FinCard title={t('fmatch.cap.title')} subtitle={t('fmatch.cap.subtitle')} icon={<Smartphone />} className="fmatchCard"
    actions={<button type="button" className="secondary" onClick={reload} aria-label={t('common.tryAgain')}><RefreshCw aria-hidden="true" /></button>}>
    {state.kind === 'loading' && <Notice busy>{t('fmatch.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('fmatch.error.load', { message: state.message })} retry={reload} />}
    {state.kind === 'ready' && <CaptureView health={state.value} />}
  </FinCard>;
}

function CaptureView({ health }: { health: FinanceCaptureHealth }) {
  return <div className="fmatchCapture">
    <p className={`fmatchState ${health.state === 'ok' ? 'ok' : 'warn'}`} role="status">{t(`fmatch.cap.state.${health.state}` as MessageKey)}</p>
    {health.problems.length > 0 && <ul className="fmatchFlags">{health.problems.map((p) => <li key={p}>{t(`fmatch.cap.problem.${p}` as MessageKey)}</li>)}</ul>}
    <dl className="fmatchCounts">{health.channels.map((c) => <div key={c.channel}>
      <dt>{t(`fmatch.cap.channel.${c.channel}` as MessageKey)}</dt>
      <dd>{c.last_captured_at ? t('fmatch.cap.lastCaptured', { time: when(c.last_captured_at) }) : t('fmatch.cap.never')}</dd>
      {c.last_failed_at && <dd className="fmatchBad">{t('fmatch.cap.lastFailed', { time: when(c.last_failed_at), code: c.last_failed_code ?? '-' })}</dd>}
      <dd className="muted">{t('fmatch.cap.refused', { count: c.refused_7d })} · {t('fmatch.cap.waiting', { count: c.awaiting_review })}</dd>
    </div>)}</dl>
    <h3 className="fmatchSub">{t('fmatch.cap.devices')}</h3>
    {health.devices.length === 0 ? <p className="muted">{t('fmatch.cap.noDevices')}</p>
      : <ul className="fmatchPlain">{health.devices.map((d) => <li key={d.id}><span dir="auto">{d.last_ok_at
        ? t('fmatch.cap.device', { name: d.name, status: d.status, time: when(d.last_ok_at) }) : t('fmatch.cap.deviceNever', { name: d.name, status: d.status })}</span>
        {d.last_error && <small className="fmatchBad"> {t('fmatch.cap.deviceError', { code: d.last_error })}</small>}</li>)}</ul>}
    {health.unknown_senders.length > 0 && <><h3 className="fmatchSub">{t('fmatch.cap.senders')}</h3>
      <ul className="fmatchPlain">{health.unknown_senders.map((s) => <li key={s.sender} dir="auto">{t('fmatch.cap.sender', { sender: s.sender, count: s.messages })}</li>)}</ul></>}
    <h3 className="fmatchSub">{t('fmatch.cap.mappings')}</h3>
    {health.mappings.length === 0 ? <p className="muted">{t('fmatch.cap.noMappings')}</p>
      : <ul className="fmatchPlain">{health.mappings.map((m) => <li key={`${m.kind}-${m.label}`} dir="auto">
        {t(`fmatch.cap.mapping.${m.kind}` as MessageKey, { label: m.label, account: m.account_name ?? '-' })}</li>)}</ul>}
    {health.accounts_without_mapping.length > 0 && <p className="muted" dir="auto">{t('fmatch.cap.unmappedAccounts', { names: joinList(health.accounts_without_mapping) })}</p>}
    {health.unmapped_wallet_cards > 0 && <p className="fmatchBad">{t('fmatch.cap.unmappedWallet', { count: health.unmapped_wallet_cards })}</p>}
    {health.gaps.length > 0 && <><h3 className="fmatchSub">{t('fmatch.cap.gaps')}</h3>
      <ul className="fmatchPlain">{health.gaps.map((g) => <li key={g.start}>{t('fmatch.cap.gap', { hours: g.hours, start: when(g.start) })}</li>)}</ul></>}
    <p className="muted fmatchNote">{t('fmatch.cap.limits')}</p>
    <div className="actions"><a className="button secondary" href="#finance-sms">{t('fmatch.cap.open')}</a></div>
  </div>;
}
