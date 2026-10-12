import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowLeft, ArrowUp, CalendarClock, ChevronDown, ChevronRight, CircleDot, ExternalLink, Pause, Pencil, Play, Plus, RefreshCw, RotateCcw, Search, Square, Trash2, UserCheck, Workflow, X } from 'lucide-react';
import { AutomationTriggers } from './AutomationTriggers';
import { listEmailAccounts } from './lib/emailApi';
import * as api from './lib/api';
import * as automationApi from './lib/automationApi';
import {
  OVERLAP_POLICIES, SCHEDULE_PRESETS, STEP_ACTIONS, manageHint, actionLabel, automationError, browserTimeZone, canEdit, canManage, canTakeOver, checkDraft,
  createdTaskId, cronShapeError, cronWords, runStatusLabel, waitingLabel, draftFromWorkflow, editHint, emptyDraft, formatTime, intervalCron, moveStep, newStep, nextRunLabel, nextTimes, outcomeLabel,
  overlapLabel, pageLabel, relativeTime, removeStep, runDuration, runProgress, statusLabel, statusTone, timeZones, triggerLabel,
  type AutomationRun, type AutomationSchedule, type AutomationWorkflow, type FieldErrors, type Page, type RunStatus, type SchedulePreview,
  type StepAction, type StepDraft, type Viewer, type WorkflowDraft,
} from './lib/automation';
import { t } from './lib/i18n';
import './workbench.css';

const PAGE_SIZE = 20;
const RUN_PAGE_SIZE = 10;

export interface AutomationFocus { workflowId: string; runId?: string }
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string; status?: number };
const statusOf = (error: unknown): number | undefined => (error as { status?: number })?.status;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}
function Badge({ status }: { status: string }) { return <span className={`statusBadge ${statusTone(status)}`}>{statusLabel(status)}</span>; }

/**
 * Automations (docs/automation.md): a searchable, paged list; the selected automation's steps,
 * schedules and run history; a composer for creating and editing. Owners change any automation,
 * members their own, viewers read only — the page says so instead of showing dead buttons.
 */
export function AutomationWorkbench({ focus = null, onOpenTask, refreshSignal = 0 }: { focus?: AutomationFocus | null; onOpenTask?: (taskId: string) => void; refreshSignal?: number }) {
  const [viewer, setViewer] = useState<Viewer>({ userId: api.currentUserId(), role: null });
  useEffect(() => { void api.getCurrentMembership().then((m) => setViewer({ userId: m.user_id, role: m.active ? m.role : 'viewer' })).catch(() => undefined); }, []);
  const manage = canManage(viewer);

  const [query, setQuery] = useState(''), [debounced, setDebounced] = useState(''), [statusFilter, setStatusFilter] = useState<'' | 'active' | 'disabled'>('');
  const [offset, setOffset] = useState(0), [tick, setTick] = useState(0);
  const [list, setList] = useState<Load<Page<AutomationWorkflow>>>({ state: 'loading' });
  const [selectedId, setSelectedId] = useState<string | null>(focus?.workflowId ?? null);
  const [mode, setMode] = useState<'view' | 'create' | 'edit'>('view');
  const [pane, setPane] = useState<'list' | 'detail'>(focus ? 'detail' : 'list');
  const [notice, setNotice] = useState<string | null>(null);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  // Only a changed search goes back to the first page; the mount (query unchanged) must not undo a page the person already moved to.
  useEffect(() => { if (query === debounced) return; const timer = setTimeout(() => { setDebounced(query); setOffset(0); }, 250); return () => clearTimeout(timer); }, [query]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (focus) { setSelectedId(focus.workflowId); setMode('view'); setPane('detail'); } }, [focus]);
  useEffect(() => {
    let live = true;
    setList((current) => current.state === 'ready' ? current : { state: 'loading' });
    automationApi.listWorkflows({ query: debounced, status: statusFilter, limit: PAGE_SIZE, offset })
      .then((page) => { if (!live) return; setList({ state: 'ready', value: page }); setSelectedId((current) => current ?? page.items[0]?.id ?? null); })
      .catch((error) => { if (live) setList({ state: 'error', message: automationError(error), status: statusOf(error) }); });
    return () => { live = false; };
  }, [debounced, statusFilter, offset, tick, refreshSignal]);

  const select = (id: string) => { setSelectedId(id); setMode('view'); setPane('detail'); setNotice(null); };
  const startCreate = () => { setMode('create'); setPane('detail'); setNotice(null); };
  const filtered = Boolean(debounced.trim() || statusFilter);

  return <div className="autoPage">
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('auto.eyebrow')}</p><h2>{t('auto.title')}</h2><p>{t('auto.intro')}</p></div>
      <div className="actions">
        <button type="button" className="secondary" onClick={reload}><RefreshCw aria-hidden="true" />{t('auto.refresh')}</button>
        {manage && <button type="button" onClick={startCreate}><Plus aria-hidden="true" />{t('auto.new')}</button>}
      </div>
    </section>
    {!manage && <Notice>{manageHint()}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    <div className="autoLayout" data-pane={pane}>
      <section className="surface autoList" aria-labelledby="autoListTitle">
        <div className="sectionHeader"><h2 id="autoListTitle">{t('auto.listTitle')}</h2>{list.state === 'ready' && <span className="muted">{t('auto.total', { count: list.value.total })}</span>}</div>
        <div className="autoFilters">
          <label className="field"><span>{t('auto.search')}</span><input type="search" value={query} placeholder={t('auto.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} /></label>
          <label className="field"><span>{t('auto.statusFilter')}</span><select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value as typeof statusFilter); setOffset(0); }}><option value="">{t('auto.filter.all')}</option><option value="active">{t('auto.filter.active')}</option><option value="disabled">{t('auto.filter.paused')}</option></select></label>
        </div>
        {list.state === 'loading' && <p className="muted autoPad" role="status">{t('auto.loadingList')}</p>}
        {list.state === 'error' && <div className="autoPad"><Notice error>{list.status === 403 ? list.message : t('auto.loadFailed', { message: list.message })}</Notice>{list.status !== 403 && <div className="actions"><button type="button" className="secondary" onClick={reload}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}</div>}
        {list.state === 'ready' && list.value.items.length === 0 && (filtered
          ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('auto.noMatches')}</h3><p>{t('auto.noMatchesText')}</p><button type="button" className="secondary autoInlineButton" onClick={() => { setQuery(''); setDebounced(''); setStatusFilter(''); setOffset(0); }}>{t('auto.clearFilters')}</button></div>
          : <div className="emptyState"><Workflow aria-hidden="true" /><h3>{t('auto.emptyTitle')}</h3><p>{manage ? t('auto.emptyManage') : t('auto.emptyRead')}</p>{manage && <button type="button" className="autoInlineButton" onClick={startCreate}><Plus aria-hidden="true" />{t('auto.create')}</button>}</div>)}
        {list.state === 'ready' && list.value.items.length > 0 && <>
          <ul className="autoRows" aria-label={t('auto.title')}>
            {list.value.items.map((item) => <li key={item.id}>
              <button type="button" className={`recordButton autoRow ${item.id === selectedId && mode !== 'create' ? 'selected' : ''}`} aria-current={item.id === selectedId && mode !== 'create' ? 'true' : undefined} onClick={() => select(item.id)}>
                <span><strong dir="auto">{item.name}</strong><small>{item.last_run_status ? t('auto.row.lastRun', { status: statusLabel(item.last_run_status).toLowerCase(), when: relativeTime(item.last_run_at) }, { isolate: false }) : t('auto.row.neverRun')}{item.next_run_at ? ` · ${t('auto.row.next', { when: relativeTime(item.next_run_at) }, { isolate: false })}` : ''}</small></span>
                <Badge status={item.status} />
              </button>
            </li>)}
          </ul>
          {list.value.total > PAGE_SIZE && <nav className="autoPager" aria-label={t('auto.pages')}>
            <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{t('auto.previous')}</button>
            <span className="muted" aria-live="polite">{pageLabel(offset, list.value.items.length, list.value.total)}</span>
            <button type="button" className="secondary" disabled={offset + PAGE_SIZE >= list.value.total} onClick={() => setOffset(offset + PAGE_SIZE)}>{t('auto.next')}</button>
          </nav>}
        </>}
      </section>
      <section className="autoDetail" aria-label={t('auto.detail')}>
        <button type="button" className="secondary autoBack" onClick={() => { setPane('list'); setMode('view'); }}><ArrowLeft aria-hidden="true" className="flipRtl" />{t('auto.listTitle')}</button>
        {mode === 'create' && <Composer viewer={viewer} onCancel={() => { setMode('view'); if (!selectedId) setPane('list'); }} onSaved={(saved) => { setSelectedId(saved.id); setMode('view'); setNotice(t('auto.created', { name: saved.name })); setOffset(0); reload(); }} />}
        {mode !== 'create' && selectedId && <Detail key={selectedId} workflowId={selectedId} viewer={viewer} editing={mode === 'edit'} tick={tick + refreshSignal}
          focusRunId={focus?.workflowId === selectedId ? focus.runId : undefined}
          onEdit={() => setMode('edit')} onEditDone={(saved) => { setMode('view'); if (saved) { setNotice(t('auto.saved', { name: saved.name, version: saved.version })); reload(); } }}
          onChanged={(message) => { setNotice(message); reload(); }}
          onDeleted={(name) => { setSelectedId(null); setMode('view'); setPane('list'); setNotice(t('auto.deleted', { name })); reload(); }}
          onOpenTask={onOpenTask} />}
        {mode !== 'create' && !selectedId && list.state === 'ready' && list.value.items.length > 0 && <div className="surface emptyState"><Workflow aria-hidden="true" /><h3>{t('auto.selectTitle')}</h3><p>{t('auto.selectText')}</p></div>}
      </section>
    </div>
  </div>;
}

// ------------------------------------------------------------------------------- Detail

function Detail({ workflowId, viewer, editing, tick, focusRunId, onEdit, onEditDone, onChanged, onDeleted, onOpenTask }: {
  workflowId: string; viewer: Viewer; editing: boolean; tick: number; focusRunId?: string;
  onEdit(): void; onEditDone(saved: AutomationWorkflow | null): void; onChanged(message: string): void; onDeleted(name: string): void; onOpenTask?: (taskId: string) => void;
}) {
  const [load, setLoad] = useState<Load<AutomationWorkflow>>({ state: 'loading' });
  const [localTick, setLocalTick] = useState(0), [busy, setBusy] = useState(false), [actionError, setActionError] = useState<string | null>(null), [confirming, setConfirming] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null), confirmButton = useRef<HTMLButtonElement>(null);
  const runKey = useRef<string | null>(null);
  useEffect(() => {
    let live = true;
    automationApi.getWorkflow(workflowId).then((value) => { if (live) setLoad({ state: 'ready', value }); })
      .catch((error) => { if (live) setLoad({ state: 'error', message: automationError(error), status: statusOf(error) }); });
    return () => { live = false; };
  }, [workflowId, tick, localTick]);
  useEffect(() => { if (confirming) confirmButton.current?.focus(); }, [confirming]);
  const refresh = () => setLocalTick((t) => t + 1);

  if (load.state === 'loading') return <div className="surface autoPad" role="status">{t('auto.loadingOne')}</div>;
  if (load.state === 'error') return <div className="surface autoPad">
    <Notice error>{load.status === 404 ? t('auto.gone') : t('auto.loadOneFailed', { message: load.message })}</Notice>
    {load.status !== 404 && <div className="actions"><button type="button" className="secondary" onClick={refresh}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
  </div>;
  const workflow = load.value;
  if (editing) return <Composer viewer={viewer} existing={workflow} onCancel={() => onEditDone(null)} onSaved={onEditDone} />;

  const manage = canManage(viewer), editable = canEdit(viewer, workflow.created_by);
  async function act(operation: () => Promise<unknown>, message: string | ((result: unknown) => string)) {
    setBusy(true); setActionError(null);
    try { const result = await operation(); refresh(); onChanged(typeof message === 'string' ? message : message(result)); } catch (error) { setActionError(automationError(error)); refresh(); } finally { setBusy(false); }
  }
  // One key per click: a double click starts one run, a later click starts another.
  const runNow = () => {
    runKey.current = runKey.current ?? crypto.randomUUID();
    const key = runKey.current;
    void act(() => automationApi.startRun(workflow.id, key), (run) => t('auto.runStarted', { name: workflow.name, status: statusLabel((run as AutomationRun).status).toLowerCase() })).finally(() => { runKey.current = null; });
  };

  return <>
    <article className="surface autoPanel" aria-labelledby="autoTitle">
      <div className="sectionHeader autoHead">
        <div className="autoHeadText"><h2 id="autoTitle" ref={heading} tabIndex={-1} dir="auto">{workflow.name}</h2>
          <p className="muted" dir="auto">{workflow.description || t('auto.noDescription')}</p></div>
        <Badge status={workflow.status} />
      </div>
      <dl className="autoFacts">
        <div><dt>{t('auto.fact.lastRun')}</dt><dd>{workflow.last_run_status ? <><Badge status={workflow.last_run_status} /> {relativeTime(workflow.last_run_at)}</> : t('auto.fact.never')}</dd></div>
        <div><dt>{t('auto.fact.nextRun')}</dt><dd>{workflow.status === 'disabled' ? t('auto.status.disabled') : workflow.next_run_at ? t('auto.time.withRelative', { when: formatTime(workflow.next_run_at), relative: relativeTime(workflow.next_run_at) }, { isolate: false }) : t('auto.fact.noSchedule')}</dd></div>
        <div><dt>{t('auto.fact.overlap')}</dt><dd>{overlapLabel(workflow.overlap_policy)}</dd></div>
        <div><dt>{t('auto.fact.version')}</dt><dd>{workflow.updated_by ? t('auto.fact.editedBy', { version: workflow.version, user: workflow.updated_by }) : t('auto.fact.versionValue', { version: workflow.version })}</dd></div>
        <div><dt>{t('auto.fact.createdBy')}</dt><dd>{workflow.created_by ?? t('auto.fact.unknown')}</dd></div>
        <div><dt>{t('auto.fact.runs')}</dt><dd>{workflow.active_runs ? t('auto.fact.runsActive', { runs: workflow.run_count, active: workflow.active_runs }) : workflow.run_count}</dd></div>
      </dl>
      {manage && <div className="actions">
        <button type="button" disabled={busy || workflow.status === 'disabled'} aria-describedby={workflow.status === 'disabled' ? 'autoPausedHint' : undefined} onClick={runNow}><Play aria-hidden="true" />{t('auto.runNow')}</button>
        {editable && <>
          <button type="button" className="secondary" disabled={busy} onClick={onEdit}><Pencil aria-hidden="true" />{t('auto.edit')}</button>
          {workflow.status === 'active'
            ? <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => automationApi.setWorkflowEnabled(workflow.id, false), t('auto.pausedNotice', { name: workflow.name }))}><Pause aria-hidden="true" />{t('auto.pause')}</button>
            : <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => automationApi.setWorkflowEnabled(workflow.id, true), t('auto.resumedNotice', { name: workflow.name }))}><Play aria-hidden="true" />{t('auto.resume')}</button>}
          <button type="button" className="secondary" disabled={busy} onClick={() => setConfirming(true)}><Trash2 aria-hidden="true" />{t('auto.delete')}</button>
        </>}
      </div>}
      {workflow.status === 'disabled' && manage && <p className="muted" id="autoPausedHint">{t('auto.pausedHint')}</p>}
      {manage && !editable && <p className="muted">{editHint(workflow.created_by)}</p>}
      {confirming && <div className="autoConfirm" role="group" aria-labelledby="autoConfirmTitle">
        <p id="autoConfirmTitle"><strong>{t('auto.confirmDelete', { name: workflow.name })}</strong> {t('auto.confirmDeleteText', { schedules: workflow.schedule_count, runs: workflow.run_count })}{workflow.active_runs ? ` ${t('auto.confirmActive', { count: workflow.active_runs })}` : ''}</p>
        <div className="actions">
          <button type="button" ref={confirmButton} className="danger" disabled={busy || workflow.active_runs > 0} onClick={() => {
            setBusy(true); setActionError(null);
            automationApi.deleteWorkflow(workflow.id).then(() => onDeleted(workflow.name)).catch((error) => { setActionError(automationError(error)); setConfirming(false); }).finally(() => setBusy(false));
          }}><Trash2 aria-hidden="true" />{t('auto.deleteConfirm')}</button>
          <button type="button" className="secondary" onClick={() => setConfirming(false)}>{t('auto.keep')}</button>
        </div>
      </div>}
      {actionError && <Notice error>{actionError}</Notice>}
      <h3 className="autoSubhead">{t('auto.steps')}</h3>
      <ol className="autoStepList">{workflow.steps.map((step, index) => <li key={step.id}>
        <span className="autoStepIndex" aria-hidden="true">{index + 1}</span>
        <div><strong>{step.name}</strong><small>{actionLabel(step.action)}{step.action === 'task.create' && typeof step.input.title === 'string' ? `: ${step.input.title}` : ''}{step.action === 'task.create' && step.input.run === false ? ` (${t('auto.step.createOnly')})` : ''}{step.action === 'email.send' ? `: ${Array.isArray(step.input.to) ? step.input.to.join(', ') : String(step.input.to ?? '')}` : ''}{(step.action === 'notify' || step.action === 'fail') && typeof step.input.message === 'string' ? `: ${step.input.message}` : ''}{step.max_attempts > 1 && step.action !== 'pause' && step.action !== 'fail' ? ` · ${t('auto.step.attempts', { count: step.max_attempts })}` : ''}</small></div>
      </li>)}</ol>
    </article>
    <Schedules workflow={workflow} viewer={viewer} tick={tick + localTick} onChanged={(message) => { refresh(); onChanged(message); }} />
    <AutomationTriggers workflow={workflow} viewer={viewer} tick={tick + localTick} onChanged={(message) => { refresh(); onChanged(message); }} />
    <RunHistory workflow={workflow} viewer={viewer} tick={tick + localTick} focusRunId={focusRunId} onChanged={(message) => { refresh(); onChanged(message); }} onOpenTask={onOpenTask} />
  </>;
}

// ----------------------------------------------------------------------------- Composer

type ControlProps = { id: string; 'aria-invalid'?: true; 'aria-describedby'?: string };
/** A labelled control whose error is announced with it but kept out of its accessible name. */
function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: string; children: (props: ControlProps) => ReactNode }) {
  const describedBy = [error ? `${id}-error` : '', hint ? `${id}-hint` : ''].filter(Boolean).join(' ');
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    {children({ id, ...(error ? { 'aria-invalid': true as const } : {}), ...(describedBy ? { 'aria-describedby': describedBy } : {}) })}
    {hint && <small className="muted" id={`${id}-hint`}>{hint}</small>}
    {error && <small className="autoFieldError" id={`${id}-error`}>{error}</small>}
  </div>;
}

function Composer({ viewer, existing, onCancel, onSaved }: { viewer: Viewer; existing?: AutomationWorkflow; onCancel(): void; onSaved(saved: AutomationWorkflow): void }) {
  const [draft, setDraft] = useState<WorkflowDraft>(() => existing ? draftFromWorkflow(existing) : emptyDraft());
  const [errors, setErrors] = useState<FieldErrors>({}), [summary, setSummary] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null), base = useId();
  const [mailboxes, setMailboxes] = useState<{ state: 'loading' } | { state: 'ready'; value: { id: string; address: string }[] } | { state: 'error'; message: string }>({ state: 'loading' });
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    if (!draft.steps.some((step) => step.action === 'email.send') || mailboxes.state === 'ready') return;
    let live = true;
    listEmailAccounts().then((accounts) => { if (live) setMailboxes({ state: 'ready', value: accounts.map((a) => ({ id: a.id, address: a.address })) }); })
      .catch((error) => { if (live) setMailboxes({ state: 'error', message: automationError(error) }); });
    return () => { live = false; };
  }, [draft.steps, mailboxes.state]);
  const patchStep = (key: string, patch: Partial<StepDraft>) => setDraft((d) => ({ ...d, steps: d.steps.map((s) => s.key === key ? { ...s, ...patch } : s) }));
  const fieldId = (name: string) => `${base}-${name.replace(/[^A-Za-z0-9_-]/g, '-')}`;

  async function save() {
    const checked = checkDraft(draft);
    if (!checked.ok) { setErrors(checked.errors); setSummary(checked.summary); return; }
    setErrors({}); setSummary(null); setBusy(true);
    try {
      onSaved(existing ? await automationApi.updateWorkflow(existing.id, checked.body) : await automationApi.createWorkflow(checked.body));
    } catch (error) { setSummary(automationError(error)); } finally { setBusy(false); }
  }

  if (!canManage(viewer)) return <div className="surface autoPad"><Notice error>{manageHint()}</Notice></div>;
  const title = existing ? t('auto.composer.edit', { name: existing.name }) : t('auto.new');
  return <form className="surface autoPanel" aria-label={title} noValidate onSubmit={(e) => { e.preventDefault(); void save(); }}>
    <div className="sectionHeader"><h2 ref={heading} tabIndex={-1}>{title}</h2></div>
    {existing && <p className="muted">{t('auto.composer.versionNote', { version: existing.version + 1 })}</p>}
    <Field id={fieldId('name')} label={t('auto.field.name')} error={errors.name}>{(a) => <input {...a} value={draft.name} maxLength={160} autoComplete="off" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />}</Field>
    <Field id={fieldId('description')} label={t('auto.field.description')} error={errors.description}>{(a) => <input {...a} value={draft.description} maxLength={1000} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />}</Field>
    <Field id={fieldId('policy')} label={t('auto.field.overlap')} hint={OVERLAP_POLICIES.find((p) => p.value === draft.overlapPolicy)?.help}>{(a) =>
      <select {...a} value={draft.overlapPolicy} onChange={(e) => setDraft({ ...draft, overlapPolicy: e.target.value as WorkflowDraft['overlapPolicy'] })}>
        {OVERLAP_POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
      </select>}</Field>
    <h3 className="autoSubhead">{t('auto.steps')}</h3>
    <p className="muted">{t('auto.stepsHelp')}</p>
    <p className="muted">{t('auto.templatesHelp')}</p>
    {errors.steps && <p className="autoFieldError">{errors.steps}</p>}
    <ol className="wbSteps" aria-label={t('auto.steps')}>{draft.steps.map((step, index) => {
      const help = STEP_ACTIONS.find((a) => a.value === step.action)?.help, id = (name: string) => fieldId(`${step.key}.${name}`), e = (name: string) => errors[`${step.key}.${name}`];
      return <li className="wbStep" key={step.key}>
        <div className="wbStepHead"><strong>{t('auto.stepN', { n: index + 1 })}</strong><div className="runActions">
          <button type="button" title={t('auto.moveUpShort')} aria-label={t('auto.moveUp', { n: index + 1 })} disabled={index === 0} onClick={() => setDraft((d) => ({ ...d, steps: moveStep(d.steps, index, -1) }))}><ArrowUp aria-hidden="true" size={16} /></button>
          <button type="button" title={t('auto.moveDownShort')} aria-label={t('auto.moveDown', { n: index + 1 })} disabled={index === draft.steps.length - 1} onClick={() => setDraft((d) => ({ ...d, steps: moveStep(d.steps, index, 1) }))}><ArrowDown aria-hidden="true" size={16} /></button>
          <button type="button" title={t('auto.removeShort')} aria-label={t('auto.removeStep', { n: index + 1 })} disabled={draft.steps.length === 1} onClick={() => setDraft((d) => ({ ...d, steps: removeStep(d.steps, index) }))}><X aria-hidden="true" size={16} /></button>
        </div></div>
        <div className="wbStepFields">
          <Field id={id('action')} label={t('auto.field.does')} error={e('action')}>{(a) => <select {...a} value={step.action} onChange={(ev) => patchStep(step.key, { action: ev.target.value })}>
            {!STEP_ACTIONS.some((x) => x.value === step.action) && <option value={step.action}>{t('auto.unsupported', { action: step.action })}</option>}
            {STEP_ACTIONS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
          </select>}</Field>
          <Field id={id('name')} label={t('auto.field.stepName')} error={e('name')}>{(a) => <input {...a} value={step.name} maxLength={160} onChange={(ev) => patchStep(step.key, { name: ev.target.value })} />}</Field>
          <Field id={id('id')} label={t('auto.field.stepId')} error={e('id')}>{(a) => <input {...a} value={step.id} maxLength={80} spellCheck={false} onChange={(ev) => patchStep(step.key, { id: ev.target.value })} />}</Field>
        </div>
        {help && <small className="muted">{help}</small>}
        {step.action === 'task.create' && <>
          <Field id={id('title')} label={t('auto.field.taskTitle')} error={e('title')}>{(a) => <input {...a} value={step.title} maxLength={160} onChange={(ev) => patchStep(step.key, { title: ev.target.value })} />}</Field>
          <Field id={id('prompt')} label={t('auto.field.prompt')} error={e('prompt')}>{(a) => <textarea {...a} rows={3} value={step.prompt} maxLength={8000} onChange={(ev) => patchStep(step.key, { prompt: ev.target.value })} />}</Field>
          <label className="autoCheck"><input type="checkbox" checked={step.runTask} onChange={(ev) => patchStep(step.key, { runTask: ev.target.checked })} />{t('auto.field.runTask')}</label>
        </>}
        {step.action === 'email.send' && <>
          <Field id={id('accountId')} label={t('auto.field.mailbox')} error={e('accountId') ?? (mailboxes.state === 'error' ? mailboxes.message : undefined)}
            hint={mailboxes.state === 'ready' && mailboxes.value.length === 0 ? t('auto.field.noMailbox') : undefined}>{(a) =>
            <select {...a} value={step.accountId} onChange={(ev) => patchStep(step.key, { accountId: ev.target.value })}>
              <option value="">{mailboxes.state === 'loading' ? t('common.loading') : t('auto.field.chooseMailbox')}</option>
              {step.accountId && !(mailboxes.state === 'ready' && mailboxes.value.some((m) => m.id === step.accountId)) && <option value={step.accountId}>{step.accountId}</option>}
              {mailboxes.state === 'ready' && mailboxes.value.map((m) => <option key={m.id} value={m.id}>{m.address}</option>)}
            </select>}</Field>
          <Field id={id('to')} label={t('auto.field.to')} error={e('to')} hint={t('auto.field.toHint')}>{(a) => <input {...a} dir="ltr" value={step.to} maxLength={2000} onChange={(ev) => patchStep(step.key, { to: ev.target.value })} />}</Field>
          <Field id={id('subject')} label={t('auto.field.subject')} error={e('subject')}>{(a) => <input {...a} value={step.subject} maxLength={500} onChange={(ev) => patchStep(step.key, { subject: ev.target.value })} />}</Field>
          <Field id={id('body')} label={t('auto.field.body')} error={e('body')}>{(a) => <textarea {...a} rows={4} value={step.body} maxLength={20000} onChange={(ev) => patchStep(step.key, { body: ev.target.value })} />}</Field>
        </>}
        {(step.action === 'notify' || step.action === 'fail') && <Field id={id('message')} label={step.action === 'notify' ? t('auto.field.messageOptional') : t('auto.field.message')} error={e('message')}>{(a) => <input {...a} value={step.message} maxLength={500} onChange={(ev) => patchStep(step.key, { message: ev.target.value })} />}</Field>}
        {step.action === 'notify' && <label className="autoCheck"><input type="checkbox" checked={step.push} onChange={(ev) => patchStep(step.key, { push: ev.target.checked })} />{t('auto.field.push')}</label>}
        {(step.action === 'task.create' || step.action === 'email.send' || step.action === 'notify') && <div className="wbStepFields">
          <Field id={id('maxAttempts')} label={t('auto.field.attempts')} error={e('maxAttempts')}>{(a) => <input {...a} type="number" min={1} max={10} value={step.maxAttempts} onChange={(ev) => patchStep(step.key, { maxAttempts: Number(ev.target.value) })} />}</Field>
          <Field id={id('retryDelay')} label={t('auto.field.retryDelay')} error={e('retryDelay')}>{(a) => <input {...a} type="number" min={5} max={3600} value={step.retryDelay} onChange={(ev) => patchStep(step.key, { retryDelay: Number(ev.target.value) })} />}</Field>
        </div>}
      </li>;
    })}</ol>
    <div className="actions">
      <button type="button" className="secondary" disabled={draft.steps.length >= 100} onClick={() => setDraft((d) => ({ ...d, steps: [...d.steps, newStep(d.steps, 'notify' as StepAction)] }))}><Plus aria-hidden="true" />{t('auto.addStep')}</button>
    </div>
    {summary && <Notice error>{summary}</Notice>}
    <div className="actions">
      <button type="submit" disabled={busy}>{busy ? t('auto.saving') : existing ? t('auto.saveChanges') : t('auto.create')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={onCancel}>{t('common.cancel')}</button>
    </div>
  </form>;
}

// ----------------------------------------------------------------------------- Schedules

type CronMode = 'preset' | 'interval' | 'cron';
interface ScheduleDraft { name: string; mode: CronMode; preset: string; interval: number; cron: string; timezone: string }
const scheduleDraft = (schedule?: AutomationSchedule): ScheduleDraft => {
  if (!schedule) return { name: '', mode: 'preset', preset: SCHEDULE_PRESETS[3].value, interval: 30, cron: '0 9 * * 1-5', timezone: browserTimeZone() };
  const preset = SCHEDULE_PRESETS.find((p) => p.value === schedule.cron);
  return { name: schedule.name, mode: preset ? 'preset' : 'cron', preset: preset?.value ?? SCHEDULE_PRESETS[3].value, interval: 30, cron: schedule.cron, timezone: schedule.timezone };
};
function draftCron(draft: ScheduleDraft): { cron: string } | { error: string } {
  try {
    const cron = draft.mode === 'preset' ? draft.preset : draft.mode === 'interval' ? intervalCron(draft.interval) : draft.cron.trim();
    const shape = cronShapeError(cron);
    return shape ? { error: shape } : { cron };
  } catch (error) { return { error: error instanceof Error ? error.message : t('auto.err.invalidSchedule') }; }
}

function Schedules({ workflow, viewer, tick, onChanged }: { workflow: AutomationWorkflow; viewer: Viewer; tick: number; onChanged(message: string): void }) {
  const [load, setLoad] = useState<Load<AutomationSchedule[]>>({ state: 'loading' });
  const [editing, setEditing] = useState<AutomationSchedule | 'new' | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [localTick, setLocalTick] = useState(0);
  useEffect(() => {
    let live = true;
    automationApi.listSchedules(workflow.id).then((page) => { if (live) setLoad({ state: 'ready', value: page.items }); })
      .catch((e) => { if (live) setLoad({ state: 'error', message: automationError(e) }); });
    return () => { live = false; };
  }, [workflow.id, tick, localTick]);
  const manage = canManage(viewer);
  async function act(operation: () => Promise<unknown>, message: string) {
    setBusy(true); setError(null);
    try { await operation(); setLocalTick((t) => t + 1); onChanged(message); } catch (e) { setError(automationError(e)); } finally { setBusy(false); }
  }

  return <section className="surface autoPanel" aria-labelledby={`schedules-${workflow.id}`}>
    <div className="sectionHeader"><h2 id={`schedules-${workflow.id}`}>{t('auto.sched.title')}</h2>
      {manage && editing === null && <div className="actions autoHeaderActions"><button type="button" className="secondary" onClick={() => setEditing('new')}><CalendarClock aria-hidden="true" />{t('auto.sched.add')}</button></div>}
    </div>
    <p className="muted">{t('auto.sched.help')}</p>
    {editing !== null && <ScheduleForm key={editing === 'new' ? 'new' : editing.id} workflowId={workflow.id} existing={editing === 'new' ? undefined : editing}
      onCancel={() => setEditing(null)} onSaved={(saved, created) => { setEditing(null); setLocalTick((t) => t + 1); onChanged(created ? t('auto.sched.added', { name: saved.name, description: cronWords(saved.cron_description.toLowerCase(), saved.cron_description_ar) }) : t('auto.sched.saved', { name: saved.name })); }} />}
    {error && <Notice error>{error}</Notice>}
    {load.state === 'loading' && <p className="muted" role="status">{t('auto.sched.loading')}</p>}
    {load.state === 'error' && <><Notice error>{t('auto.sched.loadFailed', { message: load.message })}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => setLocalTick((t) => t + 1)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></>}
    {load.state === 'ready' && load.value.length === 0 && editing === null && <div className="emptyState autoCompactEmpty"><CalendarClock aria-hidden="true" /><h3>{t('auto.sched.emptyTitle')}</h3><p>{manage ? t('auto.sched.emptyManage') : t('auto.sched.emptyRead')}</p></div>}
    {load.state === 'ready' && load.value.length > 0 && <ul className="autoScheduleList" aria-label={t('auto.sched.title')}>{load.value.map((schedule) => {
      const editable = canEdit(viewer, schedule.created_by), outcome = outcomeLabel(schedule.last_outcome);
      return <li key={schedule.id} className="autoSchedule">
        <div className="autoScheduleText">
          <strong>{schedule.name}</strong>
          <span>{cronWords(schedule.cron_description, schedule.cron_description_ar)} <span className="muted">(<bdi>{schedule.timezone}</bdi>, <code dir="ltr">{schedule.cron}</code>)</span></span>
          <small className="muted">{[
            t('auto.sched.next', { when: workflow.status === 'disabled' && schedule.enabled ? t('auto.sched.skippedPaused') : nextRunLabel(schedule.next_run_at, schedule.enabled, new Date(), schedule.timezone) }, { isolate: false }),
            ...(schedule.last_run_at ? [outcome ? t('auto.sched.lastOutcome', { when: formatTime(schedule.last_run_at, schedule.timezone), outcome }, { isolate: false }) : t('auto.sched.last', { when: formatTime(schedule.last_run_at, schedule.timezone) }, { isolate: false })] : []),
            t('auto.sched.runsAs', { user: schedule.created_by ?? t('auto.fact.unknown') }),
          ].join(' · ')}</small>
        </div>
        <span className={`statusBadge ${schedule.enabled ? 'success' : 'neutral'}`}>{schedule.enabled ? t('auto.sched.on') : t('auto.sched.off')}</span>
        {editable && <div className="runActions">
          {schedule.enabled
            ? <button type="button" title={t('auto.sched.turnOff')} aria-label={t('auto.sched.turnOffNamed', { name: schedule.name })} disabled={busy} onClick={() => void act(() => automationApi.setScheduleEnabled(schedule.id, false), t('auto.sched.turnedOff', { name: schedule.name }))}><Pause aria-hidden="true" size={16} /></button>
            : <button type="button" title={t('auto.sched.turnOn')} aria-label={t('auto.sched.turnOnNamed', { name: schedule.name })} disabled={busy} onClick={() => void act(() => automationApi.setScheduleEnabled(schedule.id, true), t('auto.sched.turnedOn', { name: schedule.name }))}><Play aria-hidden="true" size={16} /></button>}
          <button type="button" title={t('auto.edit')} aria-label={t('auto.sched.editNamed', { name: schedule.name })} disabled={busy} onClick={() => setEditing(schedule)}><Pencil aria-hidden="true" size={16} /></button>
          <button type="button" title={t('auto.delete')} aria-label={t('auto.sched.deleteNamed', { name: schedule.name })} disabled={busy} onClick={() => { if (window.confirm(t('auto.sched.confirmDelete', { name: schedule.name }, { isolate: false }))) void act(() => automationApi.deleteSchedule(schedule.id), t('auto.sched.deleted', { name: schedule.name })); }}><Trash2 aria-hidden="true" size={16} /></button>
          {canTakeOver(viewer, schedule.created_by) && <button type="button" title={t('auto.takeOver')} aria-label={t('auto.sched.takeOverNamed', { name: schedule.name })} disabled={busy} onClick={() => { if (window.confirm(t('auto.sched.confirmTakeOver', { name: schedule.name, user: schedule.created_by ?? '' }, { isolate: false }))) void act(() => automationApi.takeOverSchedule(schedule.id), t('auto.sched.takenOver', { name: schedule.name })); }}><UserCheck aria-hidden="true" size={16} /></button>}
        </div>}
      </li>;
    })}</ul>}
  </section>;
}

function ScheduleForm({ workflowId, existing, onCancel, onSaved }: { workflowId: string; existing?: AutomationSchedule; onCancel(): void; onSaved(saved: AutomationSchedule, created: boolean): void }) {
  const [draft, setDraft] = useState<ScheduleDraft>(() => scheduleDraft(existing));
  const [preview, setPreview] = useState<{ state: 'idle' | 'loading' } | { state: 'ready'; value: SchedulePreview } | { state: 'error'; message: string }>({ state: 'idle' });
  const [error, setError] = useState<string | null>(null), [nameError, setNameError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const zones = useMemo(() => timeZones(), []), base = useId();
  const resolved = draftCron(draft);
  const cronText = 'cron' in resolved ? resolved.cron : '';
  useEffect(() => {
    if (!cronText) { setPreview({ state: 'idle' }); return; }
    let live = true;
    setPreview({ state: 'loading' });
    const timer = setTimeout(() => {
      automationApi.previewSchedule(cronText, draft.timezone.trim() || 'UTC').then((value) => { if (live) setPreview({ state: 'ready', value }); })
        .catch((e) => { if (live) setPreview({ state: 'error', message: automationError(e) }); });
    }, 300);
    return () => { live = false; clearTimeout(timer); };
  }, [cronText, draft.timezone]);

  async function save() {
    if (!draft.name.trim()) { setNameError(t('auto.sched.nameRequired')); return; }
    setNameError(null);
    if ('error' in resolved) { setError(resolved.error); return; }
    if (preview.state === 'error') { setError(preview.message); return; }
    setBusy(true); setError(null);
    const body = { name: draft.name.trim(), cron: resolved.cron, timezone: draft.timezone.trim() || 'UTC' };
    try {
      onSaved(existing ? await automationApi.updateSchedule(existing.id, body) : await automationApi.createSchedule({ ...body, workflow_id: workflowId, enabled: true }), !existing);
    } catch (e) { setError(automationError(e)); } finally { setBusy(false); }
  }

  return <form className="autoScheduleForm" aria-label={existing ? t('auto.sched.editNamed', { name: existing.name }) : t('auto.sched.formNew')} noValidate onSubmit={(e) => { e.preventDefault(); void save(); }}>
    <div className="wbScheduleForm">
      <Field id={`${base}-name`} label={t('auto.sched.field.name')} error={nameError ?? undefined}>{(a) => <input {...a} value={draft.name} maxLength={160} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />}</Field>
      <label className="field"><span>{t('auto.sched.field.repeat')}</span><select value={draft.mode} onChange={(e) => setDraft({ ...draft, mode: e.target.value as CronMode })}><option value="preset">{t('auto.sched.mode.preset')}</option><option value="interval">{t('auto.sched.mode.interval')}</option><option value="cron">{t('auto.sched.mode.cron')}</option></select></label>
      {draft.mode === 'preset' && <label className="field"><span>{t('auto.sched.field.when')}</span><select value={draft.preset} onChange={(e) => setDraft({ ...draft, preset: e.target.value })}>{SCHEDULE_PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select></label>}
      {draft.mode === 'interval' && <label className="field"><span>{t('auto.sched.field.minutes')}</span><input type="number" min={1} max={1440} value={draft.interval} onChange={(e) => setDraft({ ...draft, interval: Number(e.target.value) })} /></label>}
      {draft.mode === 'cron' && <label className="field"><span>{t('auto.sched.field.cron')}</span><input dir="ltr" value={draft.cron} maxLength={120} spellCheck={false} autoComplete="off" onChange={(e) => setDraft({ ...draft, cron: e.target.value })} /></label>}
      <label className="field"><span>{t('auto.sched.field.timezone')}</span><input dir="ltr" value={draft.timezone} list={`${base}-zones`} maxLength={80} autoComplete="off" onChange={(e) => setDraft({ ...draft, timezone: e.target.value })} /><datalist id={`${base}-zones`}>{zones.map((z) => <option key={z} value={z} />)}</datalist></label>
    </div>
    <div className="autoPreview" aria-live="polite">
      {'error' in resolved && <p className="autoFieldError">{resolved.error}</p>}
      {'cron' in resolved && preview.state === 'loading' && <p className="muted">{t('auto.sched.checking')}</p>}
      {'cron' in resolved && preview.state === 'error' && <p className="autoFieldError">{preview.message}</p>}
      {'cron' in resolved && preview.state === 'ready' && <>
        <p><strong>{cronWords(preview.value.description, preview.value.description_ar)}</strong> <span className="muted">(<bdi>{preview.value.timezone}</bdi>)</span></p>
        <p className="muted">{t('auto.sched.nextRuns', { times: nextTimes(preview.value.next_runs, preview.value.timezone) }, { isolate: false })}</p>
      </>}
    </div>
    {error && <Notice error>{error}</Notice>}
    <div className="actions">
      <button type="submit" disabled={busy}>{busy ? t('auto.saving') : existing ? t('auto.sched.save') : t('auto.sched.add')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={onCancel}>{t('common.cancel')}</button>
    </div>
  </form>;
}

// --------------------------------------------------------------------------- Run history

function RunHistory({ workflow, viewer, tick, focusRunId, onChanged, onOpenTask }: { workflow: AutomationWorkflow; viewer: Viewer; tick: number; focusRunId?: string; onChanged(message: string): void; onOpenTask?: (taskId: string) => void }) {
  const [status, setStatus] = useState<'' | RunStatus>(''), [offset, setOffset] = useState(0), [localTick, setLocalTick] = useState(0);
  const [load, setLoad] = useState<Load<Page<AutomationRun>>>({ state: 'loading' });
  const [expanded, setExpanded] = useState<string | null>(focusRunId ?? null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const focused = useRef<HTMLLIElement>(null);
  useEffect(() => {
    let live = true;
    automationApi.listRuns({ workflowId: workflow.id, status, limit: RUN_PAGE_SIZE, offset }).then((value) => { if (live) setLoad({ state: 'ready', value }); })
      .catch((e) => { if (live) setLoad({ state: 'error', message: automationError(e) }); });
    return () => { live = false; };
  }, [workflow.id, status, offset, tick, localTick]);
  useEffect(() => { if (focusRunId && load.state === 'ready') focused.current?.scrollIntoView({ block: 'nearest' }); }, [focusRunId, load.state]);
  const manage = canManage(viewer);
  async function transition(run: AutomationRun, action: 'cancel' | 'resume' | 'retry') {
    setBusy(true); setError(null);
    try {
      const result = await automationApi.transitionRun(run.id, action);
      setLocalTick((t) => t + 1);
      onChanged(action === 'retry' ? t('auto.runs.retried', { status: statusLabel(result.status).toLowerCase() }) : action === 'resume' ? t('auto.runs.resumed', { status: statusLabel(result.status).toLowerCase() }) : t('auto.runs.cancelledNotice'));
    } catch (e) { setError(automationError(e)); setLocalTick((t) => t + 1); } finally { setBusy(false); }
  }

  return <section className="surface autoPanel" aria-labelledby={`runs-${workflow.id}`}>
    <div className="sectionHeader"><h2 id={`runs-${workflow.id}`}>{t('auto.runs.title')}</h2>
      <label className="autoInlineField"><span>{t('auto.runs.show')}</span><select value={status} onChange={(e) => { setStatus(e.target.value as typeof status); setOffset(0); }}>
        <option value="">{t('auto.runs.all')}</option><option value="running">{t('auto.runs.running')}</option><option value="failed">{t('auto.runs.failed')}</option><option value="paused">{t('auto.runs.waiting')}</option><option value="completed">{t('auto.runs.completed')}</option><option value="cancelled">{t('auto.runs.cancelled')}</option>
      </select></label>
    </div>
    {error && <Notice error>{error}</Notice>}
    {load.state === 'loading' && <p className="muted" role="status">{t('auto.runs.loading')}</p>}
    {load.state === 'error' && <><Notice error>{t('auto.runs.loadFailed', { message: load.message })}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => setLocalTick((t) => t + 1)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></>}
    {load.state === 'ready' && load.value.items.length === 0 && <div className="emptyState autoCompactEmpty"><Workflow aria-hidden="true" /><h3>{status ? t('auto.runs.emptyFiltered') : t('auto.runs.empty')}</h3><p>{status ? t('auto.runs.emptyFilteredText') : manage ? t('auto.runs.emptyManage') : t('auto.runs.emptyRead')}</p></div>}
    {load.state === 'ready' && load.value.items.length > 0 && <>
      <ul className="autoRunList" aria-label={t('auto.runs.list')}>{load.value.items.map((run) => {
        const open = expanded === run.id;
        return <li key={run.id} ref={run.id === focusRunId ? focused : undefined} className={`autoRun ${run.id === focusRunId ? 'focusedRow' : ''}`}>
          <div className="autoRunRow">
            <button type="button" className="wbToggle" aria-expanded={open} aria-controls={`steps-${run.id}`} onClick={() => setExpanded(open ? null : run.id)}>
              {open ? <ChevronDown aria-hidden="true" size={16} /> : <ChevronRight aria-hidden="true" className="flipRtl" size={16} />}
              <span><strong>{formatTime(run.created_at)}</strong> <span className="muted">{triggerLabel(run.trigger)}{run.created_by ? ` · ${run.created_by}` : ''}</span></span>
            </button>
            <span className={`statusBadge ${statusTone(run.status)}`}>{runStatusLabel(run)}</span>
            <span className="muted autoRunMeta">{runProgress(run)}{runDuration(run) ? ` · ${runDuration(run)}` : ''}{run.workflow_version ? ` · ${t('auto.fact.versionValue', { version: run.workflow_version })}` : ''}</span>
            {manage && <div className="runActions">
              {run.status === 'paused' && <button type="button" title={t('auto.runs.resumeShort')} aria-label={t('auto.runs.resume')} disabled={busy} onClick={() => void transition(run, 'resume')}><Play aria-hidden="true" size={16} /></button>}
              {(run.status === 'failed' || run.status === 'cancelled') && <button type="button" title={t('auto.runs.retryShort')} aria-label={t('auto.runs.retry')} disabled={busy} onClick={() => void transition(run, 'retry')}><RotateCcw aria-hidden="true" size={16} /></button>}
              {(run.status === 'queued' || run.status === 'running' || run.status === 'paused') && <button type="button" title={t('auto.runs.cancelShort')} aria-label={t('auto.runs.cancel')} disabled={busy} onClick={() => void transition(run, 'cancel')}><Square aria-hidden="true" size={16} /></button>}
            </div>}
          </div>
          {run.error && <p className="wbError autoRunError" dir="auto">{run.error}</p>}
          {open && <ol className="wbStepLog" id={`steps-${run.id}`} aria-label={t('auto.runs.steps')}>{run.steps.map((step) => {
            const taskId = createdTaskId(step);
            return <li key={step.id}>
              <div className="wbStepHead"><span><strong>{step.name}</strong> <span className="muted">{actionLabel(step.action)}</span></span><Badge status={step.status} /></div>
              {step.attempt > 0 && <span className="muted">{(step.max_attempts ?? 1) > 1 ? t('auto.runs.attemptOf', { n: step.attempt, max: step.max_attempts ?? 1 }) : t('auto.runs.attempt', { n: step.attempt })}</span>}
              {waitingLabel(step) && <span className="autoWaiting" role="status">{waitingLabel(step)}</span>}
              {typeof step.output.result === 'string' && step.output.result && <p className="autoResult" dir="auto">{t('auto.runs.result', { result: step.output.result })}</p>}
              {step.error && <p className="wbError" dir="auto">{step.error}</p>}
              {taskId && <button type="button" className="rowLink autoTaskLink" onClick={() => onOpenTask?.(taskId)} disabled={!onOpenTask}><ExternalLink aria-hidden="true" className="flipRtl" size={14} />{t('auto.runs.openTask', { title: String(step.output.title ?? taskId) })}</button>}
              {typeof step.output.message === 'string' && <span className="muted">{t('auto.runs.message', { message: step.output.message })}</span>}
              {step.output.resumed_by !== undefined && <span className="muted">{t('auto.runs.resumedBy', { user: String(step.output.resumed_by) })}</span>}
            </li>;
          })}<li className="autoRunId"><span className="muted">{t('auto.runs.runId')}</span> <code dir="ltr">{run.id}</code>{run.retry_of ? <> · {t('auto.runs.retryOf')} <code dir="ltr">{run.retry_of}</code></> : null}</li></ol>}
        </li>;
      })}</ul>
      {load.value.total > RUN_PAGE_SIZE && <nav className="autoPager" aria-label={t('auto.runs.pages')}>
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - RUN_PAGE_SIZE))}>{t('auto.runs.newer')}</button>
        <span className="muted" aria-live="polite">{pageLabel(offset, load.value.items.length, load.value.total)}</span>
        <button type="button" className="secondary" disabled={offset + RUN_PAGE_SIZE >= load.value.total} onClick={() => setOffset(offset + RUN_PAGE_SIZE)}>{t('auto.runs.older')}</button>
      </nav>}
    </>}
  </section>;
}
