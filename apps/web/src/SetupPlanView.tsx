// Setup plan (docs/setup-plan.md): preview a checked-in business blueprint against this workspace
// and apply it with one confirmed action. Every write happens on the server through each
// feature's own routes; this page only shows what will happen, asks, and reports what did.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CircleDot, ClipboardList, ExternalLink, RefreshCw, Search } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import {
  CHECK_GROUPS, checkChanged, checkStatus, checkTone, evidenceProblem, type PilotCheck, type PilotCheckStatus, type PilotChecks,
  actionLabel, actionTone, applyRequestFor, changeCount, failedItems, filterItems, groupBySection, itemHref, outcomeLabel, outcomeSummary, outcomeTone, previewMatches, reasonText,
  rolloutWeeks, sectionLabel, type SetupPlanApplyResult, type SetupPlanItem, type SetupPlanPhase, type SetupPlanPreview, type SetupPlanSection, type SetupPlanSummary,
} from './lib/setupPlan';
import * as api from './lib/setupPlanApi';
import './admin.css';
import './setupPlan.css';

const PAGE = 12;

type LoadState =
  | { kind: 'loading' }
  | { kind: 'denied'; message: string }
  | { kind: 'error'; message: string }
  | { kind: 'empty' }
  | { kind: 'ready'; plans: SetupPlanSummary[] };

function Notice({ children, error }: { children: React.ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

export function SetupPlanView({ onOpenMembers }: { onOpenMembers?: () => void }) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [planId, setPlanId] = useState<string | null>(null);
  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try {
      const plans = await api.listSetupPlans();
      if (!plans.length) { setState({ kind: 'empty' }); return; }
      setState({ kind: 'ready', plans });
      setPlanId((current) => current && plans.some((p) => p.id === current) ? current : plans[0].id);
    } catch (error) {
      setState(isPermissionDenied(error) ? { kind: 'denied', message: describeError(error) } : { kind: 'error', message: describeError(error) });
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return <>
    <p className="planIntro muted">{t('plan.intro')}</p>
    {state.kind === 'loading' && <Notice>{t('plan.loading')}</Notice>}
    {state.kind === 'error' && <div className="planState"><Notice error>{t('plan.loadFailed', { message: state.message })}</Notice><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('plan.retry')}</button></div>}
    {state.kind === 'denied' && <section className="surface planState" aria-labelledby="planDenied">
      <h3 id="planDenied">{t('plan.denied')}</h3>
      <p className="muted">{t('plan.deniedApi', { message: state.message })}</p>
      {onOpenMembers && <div className="actions"><button type="button" className="secondary" onClick={onOpenMembers}>{t('plan.openMembers')}</button></div>}
    </section>}
    {state.kind === 'empty' && <section className="surface planState"><h3>{t('plan.empty')}</h3><p className="muted">{t('plan.emptyHint')}</p></section>}
    {state.kind === 'ready' && <>
      {state.plans.length > 1 && <label className="planPicker">{t('plan.plan')}
        <select value={planId ?? ''} onChange={(event) => setPlanId(event.target.value)}>{state.plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      </label>}
      {planId && <PlanPanel key={planId} planId={planId} onDenied={(message) => setState({ kind: 'denied', message })} />}
    </>}
  </>;
}

function PlanPanel({ planId, onDenied }: { planId: string; onDenied: (message: string) => void }) {
  const [phase, setPhase] = useState<SetupPlanPhase>('pilot');
  const [overwrite, setOverwrite] = useState(false);
  const [preview, setPreview] = useState<SetupPlanPreview | null>(null);
  const [result, setResult] = useState<SetupPlanApplyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [tab, setTab] = useState<'plan' | 'checks'>('plan');
  const [checks, setChecks] = useState<PilotChecks | null>(null);
  const [checksError, setChecksError] = useState<string | null>(null);
  const loadChecks = useCallback(async () => {
    setChecksError(null);
    try { setChecks(await api.getPilotChecks(planId)); }
    catch (failure) { setChecksError(t('plan.loadFailed', { message: describeError(failure) })); }
  }, [planId]);
  useEffect(() => { void loadChecks(); }, [loadChecks]);

  // Only the newest preview request may change the page: an older answer that arrives late is dropped.
  const latestPreview = useRef(0);
  const refresh = useCallback(async (keepResult = false) => {
    const request = ++latestPreview.current;
    setLoading(true); setError(null);
    if (!keepResult) setResult(null);
    try {
      const next = await api.previewSetupPlan(planId, phase, overwrite);
      if (request === latestPreview.current) setPreview(next);
    } catch (failure) {
      if (request !== latestPreview.current) return;
      if (isPermissionDenied(failure)) { onDenied(describeError(failure)); return; }
      setError(t('plan.loadFailed', { message: describeError(failure) }));
    } finally { if (request === latestPreview.current) setLoading(false); }
  }, [planId, phase, overwrite, onDenied]);
  useEffect(() => { void refresh(); }, [refresh]);

  const apply = async () => {
    if (!preview || !previewMatches(preview, phase, overwrite)) return;
    setApplying(true); setApplyError(null);
    try {
      // Exactly what the shown preview was built with, never a choice whose preview has not loaded.
      const applied = await api.applySetupPlan(planId, applyRequestFor(preview));
      setResult(applied); setConfirming(false);
      await refresh(true);
    } catch (failure) {
      setConfirming(false);
      if (isPermissionDenied(failure)) { onDenied(describeError(failure)); return; }
      setApplyError(failure instanceof ApiError && failure.status === 409 ? t('plan.stale') : t('plan.applyFailed', { message: describeError(failure) }));
      if (failure instanceof ApiError && failure.status === 409) void refresh();
    } finally { setApplying(false); }
  };

  const current = preview !== null && previewMatches(preview, phase, overwrite);
  const changes = preview ? changeCount(preview) : 0;
  const shown = result ?? preview;
  const counts = useMemo(() => {
    const items = preview?.items ?? [];
    return {
      present: items.filter((i) => i.action === 'present').length,
      skipped: items.filter((i) => i.action === 'skipped' || i.action === 'kept').length,
    };
  }, [preview]);
  const phaseName = phase === 'pilot' ? t('plan.phase.pilot') : t('plan.phase.all');

  const tabs = <div className="segmented planTabs" role="group" aria-label={t('plan.title')}>
    <button type="button" aria-pressed={tab === 'plan'} onClick={() => setTab('plan')}>{t('plan.tab.plan')}</button>
    <button type="button" aria-pressed={tab === 'checks'} onClick={() => setTab('checks')}>{t('plan.tab.checks')}{checks && <span className="planTabCount">{checks.passed}/{checks.total}</span>}</button>
  </div>;
  if (tab === 'checks') {
    return <>{tabs}<PilotChecksPanel planId={planId} checks={checks} error={checksError} onRetry={() => void loadChecks()} onSaved={setChecks} /></>;
  }
  return <>
    {tabs}
    <section className="surface planControls" aria-label={t('plan.phase.label')}>
      <div className="planPlanName">
        <h3>{preview?.plan.name ?? '…'}</h3>
        {preview && <p className="muted">{preview.plan.summary}</p>}
        {preview && <p className="muted planMeta"><bdi>{preview.plan.currency}</bdi>, <bdi>{preview.plan.timezone}</bdi>, {preview.plan.business_hours}</p>}
      </div>
      <div className="planChoice">
        <div className="segmented" role="group" aria-label={t('plan.phase.label')}>
          {(['pilot', 'all'] as const).map((value) => <button key={value} type="button" aria-pressed={phase === value} disabled={applying} onClick={() => setPhase(value)}>{value === 'pilot' ? t('plan.phase.pilot') : t('plan.phase.all')}</button>)}
        </div>
        <p className="muted">{phase === 'pilot' ? t('plan.phase.pilotHint') : t('plan.phase.allHint')}</p>
        <label className="planToggle"><input type="checkbox" checked={overwrite} disabled={applying} onChange={(event) => setOverwrite(event.target.checked)} /><span>{t('plan.overwrite')}<small className="muted">{t('plan.overwriteHint')}</small></span></label>
        <div className="actions">
          <button type="button" disabled={loading || applying || !current || changes === 0} onClick={() => setConfirming(true)}>
            <ClipboardList aria-hidden="true" />{changes === 0 && preview ? t('plan.apply.nothing') : phase === 'pilot' ? t('plan.apply.pilot') : t('plan.apply.all')}
          </button>
        </div>
      </div>
    </section>

    {loading && !applying && <Notice>{t('plan.previewing')}</Notice>}
    {applying && <Notice>{t('plan.applying')}</Notice>}
    {error && <div className="planState"><Notice error>{error}</Notice><button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('plan.retry')}</button></div>}
    {applyError && <Notice error>{applyError}</Notice>}
    {result && <ResultSummary result={result} />}

    {preview && <>
      <section className="metrics" aria-label={t('plan.items.title')}>
        <PlanMetric label={t('plan.metric.changes')} value={formatNumber(changes)} detail={t('plan.metric.changesDetail')} />
        <PlanMetric label={t('plan.metric.present')} value={formatNumber(counts.present)} detail={t('plan.metric.presentDetail')} />
        <PlanMetric label={t('plan.metric.skipped')} value={formatNumber(counts.skipped)} detail={t('plan.metric.skippedDetail')} />
        <PlanMetric label={t('plan.metric.budget')} value={`AED ${formatNumber(preview.budget_aed)}`} detail={t('plan.metric.budgetDetail', { usd: formatNumber(preview.budget_usd) })} />
      </section>
      {preview.access.notes.length > 0 && <section className="surface planAccess" aria-label={t('plan.access.title')}>{preview.access.notes.map((note) => <p key={note}>{note}</p>)}</section>}
      <Rollout preview={preview} checks={checks} />
      {shown && <Items items={shown.items} />}
      <Gaps preview={preview} />
    </>}

    {confirming && preview && current && <ConfirmDialog title={t('plan.confirm.title', { phase: phaseName })} confirmLabel={t('plan.confirm.go')} busy={applying} onConfirm={() => void apply()} onCancel={() => setConfirming(false)}>
      <p>{t('plan.confirm.body', { count: changes })}</p>
      <p className="muted">{t('plan.confirm.safety')}</p>
      {overwrite && <p className="planWarn">{t('plan.confirm.overwrite')}</p>}
    </ConfirmDialog>}
  </>;
}

function PlanMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="metric"><div className="metricTop"><span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article>;
}

function ResultSummary({ result }: { result: SetupPlanApplyResult }) {
  const failed = failedItems(result);
  return <section className="surface planResult" aria-live="polite" aria-label={t('plan.done', { time: formatDateTime(result.applied_at) })}>
    <h3>{t('plan.done', { time: formatDateTime(result.applied_at) })}</h3>
    <ul className="planOutcomes">{outcomeSummary(result).map((row) => <li key={row.outcome}><span className={`statusBadge ${outcomeTone(row.outcome)}`}>{outcomeLabel(row.outcome)}</span><strong>{formatNumber(row.count)}</strong></li>)}</ul>
    {failed.length > 0 && <>
      <p className="planWarn">{t('plan.done.failed', { count: failed.length })}</p>
      <ul className="planFailures">{failed.map((item) => <li key={item.key}><strong>{item.name}</strong><span>{item.error}</span></li>)}</ul>
    </>}
  </section>;
}

function Rollout({ preview, checks }: { preview: SetupPlanPreview; checks: PilotChecks | null }) {
  const weeks = rolloutWeeks(preview.plan.weeks, preview.items);
  const gate = checks ? `${t('plan.checks.summary', { passed: checks.passed, total: checks.total })}. ${checks.done ? t('plan.checks.gateOpen') : t('plan.checks.gateClosed')}` : null;
  return <section className="surface planRollout" aria-labelledby="planRolloutTitle">
    <div className="sectionHeader"><div><h2 id="planRolloutTitle">{t('plan.rollout.title')}</h2><p className="muted">{t('plan.rollout.intro')}</p></div></div>
    <ol className="planTrack">
      {weeks.map((week) => <li key={week.week} className={week.pilot ? 'pilot' : undefined}>
        <span className="planWeek">{t('plan.rollout.week', { week: week.week })}{week.pilot && <em>{t('plan.rollout.pilot')}</em>}</span>
        <span className="planWeekTitle">{week.title}</span>
        {week.names.length ? <ul>{week.names.map((name) => <li key={name}><bdi>{name}</bdi></li>)}</ul> : <span className="muted">{t('plan.rollout.none')}</span>}
        {week.pilot && gate && <span className={`planGate ${checks?.done ? 'done' : ''}`}>{t('plan.rollout.gate', { summary: gate })}</span>}
      </li>)}
    </ol>
  </section>;
}

function Items({ items }: { items: SetupPlanItem[] }) {
  const [section, setSection] = useState<SetupPlanSection | 'all'>('all');
  const [query, setQuery] = useState('');
  const [changesOnly, setChangesOnly] = useState(false);
  const filtered = filterItems(items, { section, query, changesOnly });
  const groups = groupBySection(filtered);
  const sections = groupBySection(items).map((g) => g.section);
  return <section className="surface planItems" aria-labelledby="planItemsTitle">
    <div className="sectionHeader"><h2 id="planItemsTitle">{t('plan.items.title')}</h2></div>
    <div className="planFilters">
      <label className="planSearch"><Search aria-hidden="true" /><span className="visuallyHidden">{t('plan.items.search')}</span>
        <input type="search" value={query} placeholder={t('plan.items.search')} onChange={(event) => setQuery(event.target.value)} /></label>
      <label>{t('plan.items.section')}<select value={section} onChange={(event) => setSection(event.target.value as SetupPlanSection | 'all')}>
        <option value="all">{t('plan.items.all')}</option>{sections.map((s) => <option key={s} value={s}>{sectionLabel(s)}</option>)}</select></label>
      <label className="planToggle planOnly"><input type="checkbox" checked={changesOnly} onChange={(event) => setChangesOnly(event.target.checked)} /><span>{t('plan.items.changesOnly')}</span></label>
    </div>
    {groups.length === 0 && <p className="muted">{t('plan.items.none')}</p>}
    {groups.map((group) => <ItemGroup key={`${group.section}-${section}-${query ? 'q' : ''}`} section={group.section} items={group.items} open={section !== 'all' || Boolean(query.trim()) || groups.length === 1 || group.section === 'manual' || group.section === 'unsupported' || group.items.some((i) => i.outcome === 'failed')} />)}
  </section>;
}

function ItemGroup({ section, items, open }: { section: SetupPlanSection; items: SetupPlanItem[]; open: boolean }) {
  const [limit, setLimit] = useState(PAGE);
  const visible = items.slice(0, limit);
  const counts = new Map<string, number>();
  for (const item of items) { const label = item.outcome ? outcomeLabel(item.outcome) : actionLabel(item.action); counts.set(label, (counts.get(label) ?? 0) + 1); }
  return <details className="planGroup" open={open}>
    <summary><h3>{sectionLabel(section)}</h3><span className="muted">{t('plan.items.count', { count: items.length })}</span>
      <span className="planGroupCounts">{[...counts].map(([label, count]) => <span key={label}>{label} {formatNumber(count)}</span>)}</span></summary>
    <ul className="planRows">{visible.map((item) => <ItemRow key={item.key} item={item} />)}</ul>
    {items.length > limit && <button type="button" className="secondary planMore" onClick={() => setLimit(limit + PAGE)}>{t('plan.items.more', { count: Math.min(PAGE, items.length - limit) })}</button>}
  </details>;
}

function ItemRow({ item }: { item: SetupPlanItem }) {
  const href = itemHref(item.link);
  const badge = item.outcome
    ? <span className={`statusBadge ${outcomeTone(item.outcome)}`}>{outcomeLabel(item.outcome)}</span>
    : <span className={`statusBadge ${actionTone(item.action)}`}>{actionLabel(item.action)}</span>;
  return <li className="planRow">
    <div className="planRowHead">
      {badge}
      <strong className="planRowName"><bdi>{item.name}</bdi></strong>
      {item.week !== null && <span className="planTag">{t('plan.items.week', { week: item.week })}</span>}
      {href && <a className="planOpen" href={href} aria-label={t('plan.items.openLabel', { name: item.name })}><ExternalLink aria-hidden="true" />{t('plan.items.open')}</a>}
    </div>
    <p className="planReason">{reasonText(item)}</p>
    {item.error && <p className="planWarn" role="alert">{item.error}</p>}
    {item.facts.length > 0 && <details><summary>{t('plan.items.details')}</summary><ul>{item.facts.map((fact) => <li key={fact} dir="auto">{fact}</li>)}</ul></details>}
  </li>;
}

function Gaps({ preview }: { preview: SetupPlanPreview }) {
  const rows = (notes: SetupPlanPreview['approval_thresholds']) => <ul className="planNotes">{notes.map((note) => <li key={note.label}>
    <span className={`statusBadge ${note.support === 'supported' ? 'success' : note.support === 'partial' ? 'warning' : 'dangerTone'}`}>{t(`plan.support.${note.support}` as MessageKey)}</span>
    <div><strong dir="auto">{note.label}</strong><span className="muted" dir="auto">{note.plan}</span><p dir="auto">{note.anum}</p></div>
  </li>)}</ul>;
  return <section className="surface planGaps" aria-labelledby="planGapsTitle">
    <div className="sectionHeader"><div><h2 id="planGapsTitle">{t('plan.gaps.title')}</h2><p className="muted">{t('plan.gaps.intro')}</p></div></div>
    <div className="planGapColumns">
      <details className="planGroup"><summary><h3>{t('plan.gaps.thresholds')}</h3><span className="muted">{t('plan.items.count', { count: preview.approval_thresholds.length })}</span></summary>{rows(preview.approval_thresholds)}</details>
      <details className="planGroup"><summary><h3>{t('plan.gaps.memory')}</h3><span className="muted">{t('plan.items.count', { count: preview.memory_rules.length })}</span></summary>{rows(preview.memory_rules)}</details>
    </div>
  </section>;
}

function PilotChecksPanel({ planId, checks, error, onRetry, onSaved }: {
  planId: string; checks: PilotChecks | null; error: string | null; onRetry: () => void; onSaved: (checks: PilotChecks) => void;
}) {
  if (error) return <div className="planState"><Notice error>{error}</Notice><button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('plan.retry')}</button></div>;
  if (!checks) return <Notice>{t('plan.checks.loading')}</Notice>;
  return <>
    <section className={`surface planChecksHead ${checks.done ? 'done' : ''}`} aria-live="polite">
      <div>
        <h3>{t('plan.checks.summary', { passed: checks.passed, total: checks.total })}{checks.failed > 0 && <span className="statusBadge dangerTone">{t('plan.checks.failedCount', { count: checks.failed })}</span>}</h3>
        <p>{checks.done ? t('plan.checks.gateOpen') : t('plan.checks.gateClosed')}</p>
        <p className="muted" dir="auto">{checks.gate}</p>
        <p className="muted">{t('plan.checks.intro')}</p>
      </div>
      <progress max={checks.total} value={checks.passed} aria-label={t('plan.checks.summary', { passed: checks.passed, total: checks.total })} />
    </section>
    {CHECK_GROUPS.map((group) => {
      const rows = checks.checks.filter((check) => check.group === group);
      return rows.length > 0 && <section key={group} className="surface planItems" aria-labelledby={`checks-${group}`}>
        <h2 id={`checks-${group}`}>{t(`plan.checks.group.${group}` as MessageKey)}</h2>
        <ul className="planRows">{rows.map((check) => <CheckRow key={check.id} planId={planId} check={check} onSaved={onSaved} />)}</ul>
      </section>;
    })}
  </>;
}

const STATUSES: PilotCheckStatus[] = ['pass', 'fail', 'not_run'];

function CheckRow({ planId, check, onSaved }: { planId: string; check: PilotCheck; onSaved: (checks: PilotChecks) => void }) {
  const [draft, setDraft] = useState({ status: checkStatus(check), note: check.result?.note ?? '', evidence: check.result?.evidence ?? '' });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const problem = evidenceProblem(draft.evidence);
  const status = checkStatus(check);
  const save = async () => {
    setBusy(true); setMessage(null);
    try {
      onSaved(await api.recordPilotCheck(planId, check.id, { status: draft.status, note: draft.note.trim(), evidence: draft.evidence.trim() }));
      setMessage({ text: t('plan.checks.saved', { id: check.id }) });
    } catch (failure) { setMessage({ text: t('plan.checks.saveFailed', { id: check.id, message: describeError(failure) }), error: true }); }
    finally { setBusy(false); }
  };
  const field = (id: string) => `check-${check.id}-${id}`;
  return <li className="planRow planCheck">
    <div className="planRowHead">
      <span className={`statusBadge ${checkTone(status)}`}>{t(`plan.checks.status.${status}` as MessageKey)}</span>
      <strong className="planRowName"><bdi>{check.id}</bdi> {check.title}</strong>
      <span className={`statusBadge ${check.support === 'supported' ? 'success' : check.support === 'partial' ? 'warning' : 'dangerTone'}`}>{t(`plan.support.${check.support}` as MessageKey)}</span>
    </div>
    <dl className="planCheckFacts">
      <div><dt>{t('plan.checks.input')}</dt><dd dir="auto">{check.input}</dd></div>
      <div><dt>{t('plan.checks.expected')}</dt><dd>{check.expected}</dd></div>
      <div><dt>{t('plan.checks.gate')}</dt><dd>{check.approval_gate}</dd></div>
      <div><dt>{t('plan.checks.criteria')}</dt><dd>{check.pass_criteria}</dd></div>
      <div><dt>{t('plan.checks.anum')}</dt><dd>{check.anum}{check.verified_by && <> {t('plan.checks.tested')}</>}</dd></div>
    </dl>
    <fieldset className="planCheckForm">
      <legend>{t('plan.checks.result')}</legend>
      <div className="segmented" role="group" aria-label={`${t('plan.checks.result')} ${check.id}`}>
        {STATUSES.map((value) => <button key={value} type="button" aria-pressed={draft.status === value} disabled={busy} onClick={() => setDraft({ ...draft, status: value })}>{t(`plan.checks.status.${value}` as MessageKey)}</button>)}
      </div>
      <label htmlFor={field('note')}>{t('plan.checks.note')}</label>
      <textarea id={field('note')} rows={2} maxLength={1000} value={draft.note} disabled={busy} onChange={(event) => setDraft({ ...draft, note: event.target.value })} />
      <label htmlFor={field('evidence')}>{t('plan.checks.evidence')}</label>
      <input id={field('evidence')} type="text" maxLength={300} value={draft.evidence} disabled={busy} aria-invalid={problem ? true : undefined} aria-describedby={problem ? field('problem') : undefined} onChange={(event) => setDraft({ ...draft, evidence: event.target.value })} />
      {problem && <p id={field('problem')} className="planWarn">{t(problem)}</p>}
      <div className="actions"><button type="button" disabled={busy || !!problem || !checkChanged(check, draft)} onClick={() => void save()}>{t('plan.checks.save')}</button></div>
      {message && <p className={message.error ? 'planWarn' : 'muted'} role={message.error ? 'alert' : 'status'}>{message.text}</p>}
      {check.result && <p className="muted">{t('plan.checks.recorded', { user: check.result.updated_by, time: formatDateTime(check.result.updated_at) })}</p>}
    </fieldset>
  </li>;
}
