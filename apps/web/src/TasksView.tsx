import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Archive, ArchiveRestore, ArrowLeft, CircleDot, Download, Pencil, Play, RefreshCw, RotateCcw, Search, ShieldCheck, Square, X,
} from 'lucide-react';
import type { AgentRun, Approval, DomainEvent, Task, TaskBulkAction, TaskSummary } from '@anum/contracts';
import * as api from './lib/api';
import * as tasksApi from './lib/tasksApi';
import { collectPages } from './lib/approvalPaging';
import { describeError, isPermissionDenied } from './lib/errors';
import { SourcesUsed, type MemoryTarget } from './SourcesUsed';
import { AiAnswer } from './AiAnswer';
import {
  activeFilterCount, approvalsForTask, BULK_MAX, bulkMessage, dateRangeError, EMPTY_QUERY, EVENT_PAGE_SIZE, eventLabel, eventTone, finalAnswer,
  hasMorePages, isDefaultQuery, isTerminal, mergeById, permissionHint, pruneSelection, roleAllows, roleFrom, RUN_PAGE_SIZE,
  runLabel, selectRun, stepFacts, streamBadge, summaryCounts, TASK_PAGE_SIZE, taskActions,
  type StatusFilter, type StreamState, type TaskQuery, type WorkspaceRole,
} from './lib/tasks';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { riskLabel, statusLabel, stepLabel } from './lib/labels';
import { figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { PageHeader, PageSummary, SplitView } from './PageParts';
import './tasks.css';
import { HelpLink } from './HelpLink';

export interface TasksViewProps {
  /** The workspace's newest tasks (`GET /api/v1/tasks`), reloaded by the shell on task events. */
  tasks: Task[];
  task: Task | null;
  run: AgentRun | null;
  approvals: Approval[];
  prompt: string;
  setPrompt(v: string): void;
  busy: boolean;
  loading: boolean;
  message: string;
  stream: StreamState;
  /** Type of the last event received; the view refetches the selected task when events arrive. */
  lastEvent: string | null;
  setTask(t: Task): void;
  runTask(): void;
  cancel(): void;
  resume(): void;
  openApprovals(): void;
  /** "Sources used": open a memory in the Memory view, or a file's detail. */
  openMemory(target: MemoryTarget): void;
  openFile(fileId: string): void;
}

const FILTERS: StatusFilter[] = ['all', 'active', 'waiting_approval', 'completed', 'failed', 'cancelled'];

type Notice = { text: string; error: boolean } | null;

/** A refused action's sentence: the API's reason plus who may do it for a 403. */
function refusal(error: unknown, hint?: string): string {
  const text = describeError(error);
  return isPermissionDenied(error) && hint && !text.includes(hint) ? t('tasks.refusalWithHint', { text, hint }, { isolate: false }) : text;
}

export function TasksView(p: TasksViewProps) {
  const userId = api.currentUserId();
  const [role, setRole] = useState<WorkspaceRole>(null);
  useEffect(() => { void api.getCurrentMembership().then((m) => setRole(roleFrom(m))).catch(() => setRole(null)); }, []);

  // ---- Filters (server-side) ---------------------------------------------------------------
  const [query, setQuery] = useState<TaskQuery>(EMPTY_QUERY);
  const [search, setSearch] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setQuery((q) => (q.q === search ? q : { ...q, q: search })), 300);
    return () => clearTimeout(timer);
  }, [search]);
  const rangeError = dateRangeError(query.from, query.to);
  const queryKey = JSON.stringify(query);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  // The shell reloads its task and approval lists on every event; follow them.
  const pulse = `${tick}:${p.tasks.map((t) => `${t.id}${t.updatedAt}`).join()}:${p.approvals.map((a) => a.status).join()}:${p.lastEvent ?? ''}`;

  // ---- List: first page (refreshed on every pulse) plus older keyset pages ----------------
  const [first, setFirst] = useState<{ key: string; rows: Task[] } | null>(null);
  const [older, setOlder] = useState<Task[]>([]);
  const [lastPage, setLastPage] = useState<number | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [listLoading, setListLoading] = useState(false);
  const [paging, setPaging] = useState(false), [pageError, setPageError] = useState<string | null>(null);
  useEffect(() => { setOlder([]); setLastPage(null); setPageError(null); }, [queryKey]);
  useEffect(() => {
    if (rangeError) return;
    let live = true;
    setListLoading(true);
    tasksApi.listTasks(query).then(
      (rows) => { if (live) { setFirst({ key: queryKey, rows }); setListError(null); } },
      (error: unknown) => { if (live) { setListError(error); setFirst((f) => (f?.key === queryKey ? f : null)); } },
    ).finally(() => { if (live) setListLoading(false); });
    return () => { live = false; };
  }, [queryKey, pulse, rangeError]);
  const defaultQuery = isDefaultQuery(query);
  // Until our own first page arrives, the default list is the shell's copy of the same page.
  // While another filter loads, the previous rows stay (marked busy) instead of blanking the list.
  const firstRows = first?.key === queryKey ? first.rows : defaultQuery && !first ? p.tasks : first?.rows ?? [];
  const stale = Boolean(first && first.key !== queryKey);
  const all = useMemo(() => mergeById(firstRows, older), [firstRows, older]);
  const more = hasMorePages(lastPage ?? firstRows.length, TASK_PAGE_SIZE);
  const showListError = Boolean(listError) && !(defaultQuery && p.tasks.length && !first);

  async function loadMore() {
    const cursor = all[all.length - 1]?.id;
    if (!cursor) return;
    setPaging(true); setPageError(null);
    try {
      const page = await tasksApi.listTasks(query, cursor);
      setOlder((o) => [...o, ...page]);
      setLastPage(page.length);
    } catch (e) {
      setPageError(t('tasks.loadMoreError', { reason: describeError(e) }));
    } finally { setPaging(false); }
  }

  // ---- Summary (counts across the workspace for the other filters) ------------------------
  const [summary, setSummary] = useState<TaskSummary | null>(null);
  // The band says "Unavailable" when the counts failed to load; it never shows the loaded rows as totals.
  const [summaryState, setSummaryState] = useState<'loading' | 'ready' | 'error'>('loading');
  useEffect(() => {
    if (rangeError) return;
    let live = true;
    tasksApi.getTaskSummary(query).then((s) => { if (live) { setSummary(s); setSummaryState('ready'); } }, () => { if (live) { setSummary(null); setSummaryState('error'); } });
    return () => { live = false; };
  }, [queryKey, pulse, rangeError]);
  const counts = summaryCounts(summary, all);

  // ---- Selection -------------------------------------------------------------------------
  const [selectedId, setSelectedId] = useState<string | null>(p.task?.id ?? null);
  // A run the shell just started (composer) selects its task.
  useEffect(() => { if (p.run?.taskId) setSelectedId(p.run.taskId); }, [p.run?.id, p.run?.taskId]);
  const currentId = selectedId ?? all[0]?.id ?? null;
  function select(task: Task) { setSelectedId(task.id); p.setTask(task); }

  const [checked, setChecked] = useState<Set<string>>(new Set());
  useEffect(() => { setChecked((c) => (c.size ? pruneSelection(c, all) : c)); }, [all]);
  const allChecked = all.length > 0 && all.slice(0, BULK_MAX).every((t) => checked.has(t.id));
  function toggle(id: string) { setChecked((c) => { const next = new Set(c); if (next.has(id)) next.delete(id); else if (next.size < BULK_MAX) next.add(id); return next; }); }
  function toggleAll() { setChecked(allChecked ? new Set() : new Set(all.slice(0, BULK_MAX).map((t) => t.id))); }

  const [notice, setNotice] = useState<Notice>(null);
  const [bulkBusy, setBulkBusy] = useState<TaskBulkAction | null>(null);
  const [confirmBulkCancel, setConfirmBulkCancel] = useState(false);
  async function bulk(action: TaskBulkAction) {
    setBulkBusy(action); setNotice(null); setConfirmBulkCancel(false);
    try {
      const result = await tasksApi.bulkTasks(action, [...checked]);
      setNotice({ text: bulkMessage(result), error: result.failed > 0 });
      setChecked(new Set(result.results.filter((r) => !r.ok).map((r) => r.taskId)));
    } catch (e) {
      setNotice({ text: refusal(e), error: true });
    } finally { setBulkBusy(null); refresh(); }
  }

  const badge = streamBadge(p.stream);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const filterCount = activeFilterCount(query);
  function clearFilters() { setSearch(''); setQuery(EMPTY_QUERY); }
  const canRun = roleAllows(role, 'run', null, userId);
  const canCancel = roleAllows(role, 'cancel', null, userId);
  const creators = summary?.creators ?? [];

  const [picked, setPicked] = useState(false);
  const figures = [
    figure('active', t('tasks.metric.active'), { state: summaryState }, () => ({ value: formatNumber(counts.active), hint: t('tasks.metric.activeAll'), tone: counts.active ? 'accent' : '' })),
    figure('waiting', t('tasks.metric.waiting'), { state: summaryState }, () => ({ value: formatNumber(counts.waiting), hint: t('tasks.metric.waitingDetail'), tone: counts.waiting ? 'warn' : '' })),
    figure('completed', t('tasks.metric.completed'), { state: summaryState }, () => ({ value: formatNumber(counts.completed), hint: filterCount ? t('tasks.metric.matching') : t('tasks.metric.workspace'), tone: counts.completed ? 'ok' : '' })),
    figure('failed', t('tasks.metric.failed'), { state: summaryState }, () => ({ value: formatNumber(counts.failed), hint: t('tasks.metric.archived', { count: summary?.archived ?? 0 }), tone: counts.failed ? 'danger' : '' })),
  ];
  const historyLabel = query.archived ? t('tasks.history.archived') : t('tasks.history.eyebrow');

  return <>
    <PageHeader title={viewLabel('tasks')} subtitle={t('pages2.tasks.subtitle')} actions={<span className={`statusBadge ${badge.tone}`} title={t('tasks.streamTitle')}>{badge.label}</span>} />
    <PageSummary label={t('tasks.metric.label')} figures={figures} onRetry={refresh} />
    <SplitView className="tasksSplit" show={picked && currentId ? 'detail' : 'list'} label={t('pages2.tasks.split')}>
      <section data-pane="list" className="pane tasksListPane" aria-label={historyLabel}>
        <form className="taskComposer" aria-labelledby="task-composer-heading" onSubmit={(e) => { e.preventDefault(); if (p.prompt.trim() && canRun && !p.busy) { setPicked(true); p.runTask(); } }}>
          <h2 id="task-composer-heading" className="visuallyHidden">{t('tasks.composer.title')}</h2>
          <label className="field"><span>{t('tasks.instructions')}</span><textarea ref={composerRef} dir="auto" rows={2} value={p.prompt} maxLength={8000} onChange={(e) => p.setPrompt(e.target.value)} placeholder={t('tasks.instructionsPlaceholder')}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} /></label>
          <div className="taskComposerFoot">
            <Notice>{p.message}</Notice>
            <button type="submit" disabled={p.busy || !p.prompt.trim() || !canRun} title={canRun ? undefined : permissionHint('run')}><Play aria-hidden="true" />{p.busy ? t('tasks.running') : t('tasks.run')}</button>
          </div>
          {!canRun && <p className="muted taskHint">{t('tasks.hint.readOnly', { hint: permissionHint('run') }, { isolate: false })}</p>}
        </form>
        <div className="paneHead taskListHead">
          <h2 aria-live="polite">{listLoading && !all.length ? t('common.loading') : t('tasks.history.count', { shown: all.length, count: summary ? (query.status === 'all' ? summary.total : counts.total) : all.length })}</h2>
          <label className="taskToggle"><input type="checkbox" checked={query.archived} onChange={(e) => { setChecked(new Set()); setQuery({ ...query, archived: e.target.checked }); }} />{summary ? t('tasks.filter.archivedCount', { count: summary.archived }) : t('tasks.filter.archived')}</label>
        </div>
        <div className="paneBody taskListBody">
          <div className="taskFilters" role="search" aria-label={t('tasks.filter.label')}>
            <label className="field taskSearch"><span>{t('tasks.search')}</span><input type="search" dir="auto" value={search} maxLength={200} onChange={(e) => setSearch(e.target.value)} placeholder={t('tasks.searchPlaceholder')} /></label>
            <label className="field"><span>{t('tasks.status')}</span><select value={query.status} onChange={(e) => setQuery({ ...query, status: e.target.value as StatusFilter })}>{FILTERS.map((f) => <option key={f} value={f}>{t(`tasks.filter.${f}` as MessageKey)}</option>)}</select></label>
            <label className="field"><span>{t('tasks.filter.creator')}</span><select value={query.creator} onChange={(e) => setQuery({ ...query, creator: e.target.value })}>
              <option value="">{t('tasks.filter.anyone')}</option><option value="me">{t('tasks.filter.me')}</option>
              {creators.filter((c) => c !== userId).map((c) => <option key={c} value={c}>{c}</option>)}
            </select></label>
            <details className="taskDates" open={Boolean(query.from || query.to)}>
              <summary>{query.from || query.to ? t('pages2.tasks.datesSet') : t('pages2.tasks.dates')}</summary>
              <div className="taskDateFields">
                <label className="field"><span>{t('tasks.filter.from')}</span><input type="date" value={query.from} max={query.to || undefined} onChange={(e) => setQuery({ ...query, from: e.target.value })} /></label>
                <label className="field"><span>{t('tasks.filter.to')}</span><input type="date" value={query.to} min={query.from || undefined} onChange={(e) => setQuery({ ...query, to: e.target.value })} /></label>
              </div>
            </details>
          </div>
          {filterCount > 0 && <div className="taskFilterBar"><button className="secondary taskSmallButton" onClick={clearFilters}><X aria-hidden="true" />{t('tasks.filter.clearCount', { count: filterCount })}</button></div>}
          {rangeError && <Notice error>{rangeError}</Notice>}
          {checked.size > 0 && <div className="taskBulkBar" role="toolbar" aria-label={t('tasks.bulk.label')}>
            <strong>{t('tasks.bulk.selected', { count: checked.size })}</strong>
            {query.archived
              ? <button className="secondary taskSmallButton" disabled={Boolean(bulkBusy)} onClick={() => void bulk('unarchive')}><ArchiveRestore aria-hidden="true" />{bulkBusy === 'unarchive' ? t('tasks.action.restoring') : t('tasks.action.restore')}</button>
              : <>
                <button className="secondary taskSmallButton" disabled={Boolean(bulkBusy)} onClick={() => void bulk('archive')}><Archive aria-hidden="true" />{bulkBusy === 'archive' ? t('tasks.action.archiving') : t('tasks.action.archive')}</button>
                {canCancel && (confirmBulkCancel
                  ? <button className="danger taskSmallButton" disabled={Boolean(bulkBusy)} onClick={() => void bulk('cancel')}><Square aria-hidden="true" />{bulkBusy === 'cancel' ? t('tasks.action.cancelling') : t('tasks.bulk.confirmCancel', { count: checked.size })}</button>
                  : <button className="secondary taskSmallButton" disabled={Boolean(bulkBusy)} onClick={() => setConfirmBulkCancel(true)}><Square aria-hidden="true" />{t('tasks.bulk.cancel')}</button>)}
              </>}
            <button className="secondary taskSmallButton" onClick={() => { setChecked(new Set()); setConfirmBulkCancel(false); }}>{t('tasks.bulk.clear')}</button>
          </div>}
          {notice && <Notice error={notice.error}>{notice.text}</Notice>}
          {showListError && <div className="notice errorNotice taskErrorRow" role="alert"><CircleDot aria-hidden="true" /><span>{t('tasks.listError', { reason: describeError(listError) })}</span><button className="secondary taskSmallButton" onClick={refresh}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
          {all.length > 0 && <label className="taskToggle taskSelectAll"><input type="checkbox" checked={allChecked} onChange={toggleAll} />{all.length > BULK_MAX ? t('tasks.bulk.selectFirst', { count: BULK_MAX }) : t('tasks.bulk.selectAll')}</label>}
          <div className={`compactList taskList ${stale ? 'taskListStale' : ''}`} aria-busy={listLoading}>
            {all.map((x) => <div className="taskRow" key={x.id}>
              <input type="checkbox" checked={checked.has(x.id)} onChange={() => toggle(x.id)} aria-label={t('tasks.bulk.selectOne', { title: x.title })} />
              <button className={`recordButton ${currentId === x.id ? 'selected' : ''}`} aria-pressed={currentId === x.id} onClick={() => { select(x); setPicked(true); }}>
                <span><strong dir="auto">{x.title}</strong><small className="taskPromptPreview" dir="auto">{x.prompt}</small><small>{x.createdBy ? t('tasks.row.createdBy', { when: date(x.createdAt), who: x.createdBy === userId ? t('tasks.you') : x.createdBy }) : date(x.createdAt)}</small></span>
                <Status value={x.status} />
              </button>
            </div>)}
            {!listLoading && !showListError && !rangeError && !all.length && (filterCount
              ? <Empty title={query.archived && filterCount === 1 ? t('tasks.archivedEmpty.title') : t('tasks.noMatch.title')} text={query.archived && filterCount === 1 ? t('tasks.archivedEmpty.text') : t('tasks.noMatch.filters')}>
                <button className="secondary" onClick={clearFilters}>{query.archived && filterCount === 1 ? t('tasks.archivedEmpty.back') : t('tasks.filter.clear')}</button></Empty>
              : <Empty title={t('tasks.empty.firstTitle')} text={t('tasks.empty.firstText')}>
                {canRun && <button className="secondary" onClick={() => composerRef.current?.focus()}>{t('tasks.empty.write')}</button>}</Empty>)}
            {listLoading && !all.length && <p className="muted taskLoading" role="status">{t('tasks.loadingList')}</p>}
          </div>
          {pageError && <Notice error>{pageError}</Notice>}
          {more && all.length > 0 && <div className="actions taskMore"><button className="secondary" onClick={() => void loadMore()} disabled={paging || listLoading}>{paging ? t('common.loading') : t('tasks.loadMore')}</button></div>}
        </div>
      </section>
      <section data-pane="detail" className="pane taskDetailPane" aria-label={currentId ? undefined : t('tasks.detail.eyebrow')}>
        {currentId
          ? <TaskDetail key={currentId} taskId={currentId} listed={all.find((t) => t.id === currentId) ?? (p.task?.id === currentId ? p.task : null)}
            recentRun={p.run} approvals={approvalsForTask(p.approvals, currentId)} pulse={pulse} role={role} userId={userId} onBack={() => setPicked(false)}
            onChanged={refresh} openApprovals={p.openApprovals} openMemory={p.openMemory} openFile={p.openFile} />
          : <Empty title={t('tasks.noneSelected.title')} text={t('tasks.noneSelected.text')} />}
      </section>
    </SplitView>
  </>;
}

interface DetailProps {
  taskId: string; listed: Task | null; recentRun: AgentRun | null; approvals: Approval[]; pulse: string;
  role: WorkspaceRole; userId: string | null; onBack(): void; onChanged(): void; openApprovals(): void; openMemory(target: MemoryTarget): void; openFile(fileId: string): void;
}

type DetailTab = 'answer' | 'approvals' | 'runs' | 'activity';
const DETAIL_TABS: readonly DetailTab[] = ['answer', 'approvals', 'runs', 'activity'];

const newer = (a: Task | null, b: Task | null): Task | null => (!a ? b : !b ? a : b.updatedAt > a.updatedAt ? b : a);

function TaskDetail({ taskId, listed, recentRun, approvals: pendingForTask, pulse, role, userId, onBack, onChanged, openApprovals, openMemory, openFile }: DetailProps) {
  const [tab, setTab] = useState<DetailTab>(() => (pendingForTask.some((a) => a.status === 'pending') ? 'approvals' : 'answer'));
  // The shell holds only pending approvals; this task's whole history comes from its own filtered page.
  const [taskApprovals, setTaskApprovals] = useState<{ id: string; items: Approval[] } | null>(null);
  const approvals = taskApprovals?.id === taskId ? mergeById(pendingForTask, taskApprovals.items) : pendingForTask;
  const [fetched, setFetched] = useState<Task | null>(null), [taskError, setTaskError] = useState<unknown>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]), [olderRuns, setOlderRuns] = useState<AgentRun[]>([]);
  const [runsMore, setRunsMore] = useState(false), [runsError, setRunsError] = useState<string | null>(null), [runsLoading, setRunsLoading] = useState(true);
  const [chosenRun, setChosenRun] = useState<string | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]), [olderEvents, setOlderEvents] = useState<DomainEvent[]>([]);
  const [eventsMore, setEventsMore] = useState(false), [eventsError, setEventsError] = useState<string | null>(null), [eventsLoading, setEventsLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null), [notice, setNotice] = useState<Notice>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    let live = true;
    tasksApi.getTask(taskId).then((t) => { if (live) { setFetched(t); setTaskError(null); } }, (e: unknown) => { if (live) setTaskError(e); });
    collectPages((cursor) => api.getApprovalPage({ taskId, cursor, limit: 100 })).then((all) => { if (live) setTaskApprovals({ id: taskId, items: all.items }); }, () => undefined);
    setRunsLoading(true);
    tasksApi.listTaskRuns(taskId).then(
      (page) => { if (live) { setRuns(page); setRunsMore(page.length >= RUN_PAGE_SIZE); setRunsError(null); } },
      (e: unknown) => {
        // The history could not load; the latest run alone still shows the answer and steps.
        api.getLatestRun(taskId).then((r) => { if (live) { setRuns([r]); setRunsError(null); } }, () => { if (live) setRunsError(t('tasks.runs.error', { reason: describeError(e) })); });
      },
    ).finally(() => { if (live) setRunsLoading(false); });
    setEventsLoading(true);
    tasksApi.listTaskEvents(taskId).then(
      (page) => { if (live) { setEvents(page); setEventsMore(page.length >= EVENT_PAGE_SIZE); setEventsError(null); } },
      (e: unknown) => { if (live) setEventsError(isPermissionDenied(e) ? EVENTS_DENIED() : t('tasks.activity.error', { reason: describeError(e) })); },
    ).finally(() => { if (live) setEventsLoading(false); });
    return () => { live = false; };
  }, [taskId, pulse]);

  const task = newer(fetched?.id === taskId ? fetched : null, listed);
  const allRuns = useMemo(() => mergeById(runs, olderRuns), [runs, olderRuns]);
  const allEvents = useMemo(() => mergeById(events, olderEvents), [events, olderEvents]);
  const run = selectRun(allRuns, chosenRun, taskId, recentRun);
  const latestRun = selectRun(allRuns, null, taskId, recentRun);
  const historyRuns = latestRun && !allRuns.some((r) => r.id === latestRun.id) ? [latestRun, ...allRuns] : allRuns;

  if (!task) {
    return <section className="taskDetail" aria-labelledby="task-detail-heading">
      <div className="paneHead"><button type="button" className="secondary paneBack" onClick={onBack}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('page.back')}</button><h2 id="task-detail-heading">{t('tasks.detail.eyebrow')}</h2></div>
      <div className="paneBody">
        {taskError
          ? <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{(taskError as { status?: number }).status === 404 ? t('tasks.detail.gone') : t('tasks.detail.error', { reason: describeError(taskError) })}</span></div>
          : <p className="muted" role="status">{t('tasks.detail.loading')}</p>}
      </div>
    </section>;
  }

  const can = taskActions(task, latestRun);
  const allow = { run: roleAllows(role, 'run', task, userId), cancel: roleAllows(role, 'cancel', task, userId), manage: roleAllows(role, 'manage', task, userId) };
  const hints = [
    (can.run || can.retry || can.resume) && !allow.run ? permissionHint('run') : null,
    can.cancel && !allow.cancel ? permissionHint('cancel') : null,
    (can.edit || can.archive || can.unarchive) && !allow.manage ? permissionHint('manage') : null,
  ].filter(Boolean) as string[];

  async function act(name: string, hint: string, fn: () => Promise<string>) {
    setBusy(name); setNotice(null);
    try { setNotice({ text: await fn(), error: false }); setChosenRun(null); }
    catch (e) { setNotice({ text: refusal(e, hint), error: true }); }
    finally { setBusy(null); onChanged(); }
  }
  const runOutcome = (r: api.RunTaskResult) => { setRuns((rs) => mergeById([r.run], rs)); setFetched(r.task); return r.approval ? t('tasks.outcome.paused') : r.run.status === 'failed' ? t('tasks.outcome.failed') : t('tasks.outcome.status', { status: statusLabel(r.run.status) }); };
  const answer = finalAnswer(run);
  const working = !isTerminal(task.status);

  const tabCount: Record<DetailTab, string> = {
    answer: '', approvals: approvals.length ? ` (${formatNumber(approvals.length)})` : '',
    runs: historyRuns.length ? ` (${formatNumber(historyRuns.length)}${runsMore ? '+' : ''})` : '', activity: allEvents.length ? ` (${formatNumber(allEvents.length)}${eventsMore ? '+' : ''})` : '',
  };
  return <section className="taskDetail" aria-labelledby="task-detail-heading">
    <div className="paneHead taskDetailHead">
      <button type="button" className="secondary paneBack" onClick={onBack}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('page.back')}</button>
      <div className="taskDetailTitle"><p className="eyebrow">{t('tasks.detail.eyebrow')}</p><h2 id="task-detail-heading" dir="auto">{task.title}</h2></div>
      <span className="taskBadges">{task.archivedAt && <span className="statusBadge neutral">{t('tasks.detail.archivedBadge')}</span>}<Status value={task.status} /></span>
    </div>
    <div className="paneBody">
      <div className="taskToolbar" role="toolbar" aria-label={t('tasks.actions.label')}>
        {can.run && <button disabled={Boolean(busy) || !allow.run} onClick={() => void act('run', permissionHint('run'), async () => runOutcome(await tasksApi.runTask(task.id)))}><Play aria-hidden="true" />{busy === 'run' ? t('tasks.action.starting') : t('tasks.action.run')}</button>}
        {can.retry && <button disabled={Boolean(busy) || !allow.run} onClick={() => void act('retry', permissionHint('run'), async () => runOutcome(await tasksApi.retryTask(task.id)))}><RotateCcw aria-hidden="true" />{busy === 'retry' ? t('tasks.action.retrying') : t('tasks.action.retry')}</button>}
        {can.resume && latestRun && <button disabled={Boolean(busy) || !allow.run} onClick={() => void act('resume', permissionHint('run'), async () => runOutcome(await tasksApi.resumeRun(latestRun.id)))}><RefreshCw aria-hidden="true" />{busy === 'resume' ? t('tasks.action.resuming') : t('tasks.resume')}</button>}
        {can.cancel && <button className="secondary" disabled={Boolean(busy) || !allow.cancel} onClick={() => void act('cancel', permissionHint('cancel'), async () => { setFetched(await tasksApi.cancelTask(task.id)); return t('tasks.outcome.cancelled'); })}><Square aria-hidden="true" />{busy === 'cancel' ? t('tasks.action.cancelling') : t('tasks.cancel')}</button>}
        {can.edit && <button className="secondary" disabled={Boolean(busy) || !allow.manage || editing} onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('tasks.action.edit')}</button>}
        {can.archive && <button className="secondary" disabled={Boolean(busy) || !allow.manage} onClick={() => void act('archive', permissionHint('manage'), async () => { setFetched(await tasksApi.archiveTask(task.id)); return t('tasks.outcome.archived'); })}><Archive aria-hidden="true" />{busy === 'archive' ? t('tasks.action.archiving') : t('tasks.action.archive')}</button>}
        {can.unarchive && <button className="secondary" disabled={Boolean(busy) || !allow.manage} onClick={() => void act('unarchive', permissionHint('manage'), async () => { setFetched(await tasksApi.unarchiveTask(task.id)); return t('tasks.outcome.restored'); })}><ArchiveRestore aria-hidden="true" />{busy === 'unarchive' ? t('tasks.action.restoring') : t('tasks.action.restore')}</button>}
        <button className="secondary" disabled={Boolean(busy)} onClick={() => void act('export-json', '', async () => t('tasks.export.saved', { name: await tasksApi.downloadTaskRunLog(task.id, 'json') }))}><Download aria-hidden="true" />{t('tasks.export.json')}</button>
        <button className="secondary" disabled={Boolean(busy)} onClick={() => void act('export-md', '', async () => t('tasks.export.saved', { name: await tasksApi.downloadTaskRunLog(task.id, 'markdown') }))}><Download aria-hidden="true" />{t('tasks.export.markdown')}</button>
      </div>
      {hints.length > 0 && <p className="muted taskHint">{hints.join(' ')}</p>}
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {editing
        ? <EditTask task={task} instructionsEditable={can.editInstructions} onCancel={() => setEditing(false)} onSave={async (changes) => {
          try { setFetched(await tasksApi.updateTask(task.id, changes)); setEditing(false); setNotice({ text: t('tasks.outcome.saved'), error: false }); onChanged(); return null; }
          catch (e) { return refusal(e, permissionHint('manage')); }
        }} />
        : <>
          <dl className="taskMeta">
            <div><dt>{t('tasks.detail.task')}</dt><dd><code dir="ltr">{task.id}</code></dd></div>
            <div><dt>{t('tasks.detail.created')}</dt><dd>{task.createdBy ? t('tasks.detail.by', { when: date(task.createdAt), who: task.createdBy === userId ? t('tasks.you') : task.createdBy }) : date(task.createdAt)}</dd></div>
            <div><dt>{t('tasks.detail.updated')}</dt><dd>{date(task.updatedAt)}</dd></div>
            <div><dt>{t('tasks.detail.runs')}</dt><dd>{runsLoading && !historyRuns.length ? '...' : `${formatNumber(historyRuns.length)}${runsMore ? '+' : ''}`}</dd></div>
            {task.archivedAt && <div><dt>{t('tasks.detail.archived')}</dt><dd>{task.archivedBy ? t('tasks.detail.by', { when: date(task.archivedAt), who: task.archivedBy === userId ? t('tasks.you') : task.archivedBy }) : date(task.archivedAt)}</dd></div>}
          </dl>
          <h3 className="taskSubheading">{t('tasks.detail.instructions')}</h3>
          <Expandable className="taskInstructions" text={task.prompt} limit={600} />
        </>}
      <div className="segmented taskTabs" role="tablist" aria-label={t('pages2.tasks.tabs')}>
        {DETAIL_TABS.map((id) => <button key={id} type="button" role="tab" id={`task-tab-${id}`} aria-selected={tab === id} aria-controls={`task-panel-${id}`} onClick={() => setTab(id)}>{t(`pages2.tasks.tab.${id}` as MessageKey)}{tabCount[id]}</button>)}
      </div>
      <div role="tabpanel" id={`task-panel-${tab}`} aria-labelledby={`task-tab-${tab}`} className="taskTabPanel">
        {tab === 'answer' && <>
          <section className="taskAnswer" aria-labelledby="task-answer-heading" aria-live="polite" aria-busy={working && !answer}>
            <div className="taskPanelHead"><h3 id="task-answer-heading">{t('tasks.answer.title')}</h3>{working && <span className="statusBadge warning">{t('tasks.answer.updating')}</span>}</div>
            {answer ? <AiAnswer text={answer} /> : <p className="muted">{working ? t('tasks.answer.pending') : run ? t('tasks.answer.none') : t('tasks.answer.noRun')}</p>}
          </section>
          <SourcesUsed run={run} onOpenMemory={openMemory} onOpenFile={openFile} />
        </>}
        {tab === 'approvals' && <section className="taskAnswer" aria-labelledby="task-approvals-heading">
          <div className="taskPanelHead"><h3 id="task-approvals-heading">{t('tasks.approvals.title')}</h3><button className="secondary taskSmallButton" onClick={openApprovals}><ShieldCheck aria-hidden="true" />{t('tasks.approvals.open')}</button></div>
          {approvals.length
            ? <ul className="taskApprovals">{approvals.map((a) => <li key={a.id}><span><strong dir="ltr">{a.action}</strong><small>{t('tasks.approvals.meta', { risk: riskLabel(a.riskLevel), when: date(a.createdAt) })}{a.runId && historyRuns.length > 1 ? ` · ${runName(historyRuns, a.runId)}` : ''}</small><small dir="auto">{a.reason}</small></span><Status value={a.status} /></li>)}</ul>
            : <Empty title={t('pages2.tasks.noApprovals')} text={t('pages2.tasks.noApprovalsText')} />}
        </section>}
        {tab === 'runs' && <section className="timelineSurface" aria-labelledby="task-runs-heading">
          <div className="taskPanelHead"><h3 id="task-runs-heading">{t('tasks.runs.title')}</h3>{historyRuns.length > 0 && <span className="statusBadge neutral">{t(runsMore ? 'tasks.runs.countMore' : 'tasks.runs.count', { count: historyRuns.length })}</span>}</div>
          {runsError && <Notice error>{runsError}</Notice>}
          {historyRuns.length > 1 && <ul className="taskRuns" aria-label={t('tasks.runs.listLabel')}>{historyRuns.map((r, i) => <li key={r.id}>
            <button className={`recordButton ${run?.id === r.id ? 'selected' : ''}`} aria-pressed={run?.id === r.id} onClick={() => setChosenRun(r.id)}>
              <span><strong>{runLabel(i, historyRuns.length)}</strong><small>{r.createdAt ? date(r.createdAt) : r.id} · {t('tasks.trace.count', { count: r.steps.length })}</small></span><Status value={r.status} />
            </button></li>)}</ul>}
          {runsMore && <div className="actions"><button className="secondary" disabled={busy === 'runs'} onClick={() => void (async () => {
            setBusy('runs');
            try { const page = await tasksApi.listTaskRuns(taskId, allRuns[allRuns.length - 1]?.id ?? null); setOlderRuns((o) => [...o, ...page]); setRunsMore(page.length >= RUN_PAGE_SIZE); }
            catch (e) { setRunsError(t('tasks.runs.olderError', { reason: describeError(e) })); } finally { setBusy(null); }
          })()}>{busy === 'runs' ? t('common.loading') : t('tasks.runs.loadOlder')}</button></div>}
          <h4 className="taskSubheading">{run && historyRuns.length > 1 ? t('tasks.trace.of', { run: runName(historyRuns, run.id) }) : t('tasks.trace.title')}</h4>
          {run?.steps.length
            ? <ol className="timeline" aria-label={t('tasks.trace.listLabel')}>{run.steps.map((s, i) => <li className="timelineRow" key={s.id}>
              <div className="timelineRail" aria-hidden="true"><span className="stepDot">{i + 1}</span>{i < run.steps.length - 1 && <span className="railLine" />}</div>
              <div className="timelineBody"><div className="timelineTitle"><strong>{stepLabel(s.type)}</strong><time dateTime={s.createdAt}>{time(s.createdAt)}</time></div>
                <Expandable text={s.summary} limit={400} />
                {stepFacts(s).length > 0 && <dl className="stepFacts">{stepFacts(s).map((f) => <div key={f.label}><dt>{f.label}</dt><dd>{f.value}</dd></div>)}</dl>}
              </div></li>)}</ol>
            : <Empty title={runsLoading ? t('tasks.trace.loading') : t('tasks.trace.noneYet')} text={run ? t('tasks.trace.pending') : can.run ? t('tasks.trace.notRun') : t('tasks.answer.noRun')} />}
        </section>}
        {tab === 'activity' && <section className="timelineSurface" aria-labelledby="task-activity-heading">
          <div className="taskPanelHead"><h3 id="task-activity-heading">{t('tasks.activity.title')}</h3></div>
          {eventsError && <Notice error={eventsError !== EVENTS_DENIED()}>{eventsError}</Notice>}
          {!eventsError && !allEvents.length && <p className="muted taskLoading">{eventsLoading ? t('tasks.activity.loading') : t('tasks.activity.empty')}</p>}
          {allEvents.length > 0 && <ul className="taskEvents" aria-label={t('tasks.activity.listLabel')}>{allEvents.map((e) => <li key={e.id}>
            <span className={`statusBadge ${eventTone(e)}`}>{eventLabel(e)}</span><time dateTime={e.createdAt}>{date(e.createdAt)}</time>
          </li>)}</ul>}
          {eventsMore && <div className="actions"><button className="secondary" disabled={busy === 'events'} onClick={() => void (async () => {
            setBusy('events');
            try { const page = await tasksApi.listTaskEvents(taskId, allEvents[allEvents.length - 1]?.id ?? null); setOlderEvents((o) => [...o, ...page]); setEventsMore(page.length >= EVENT_PAGE_SIZE); }
            catch (e) { setEventsError(t('tasks.activity.olderError', { reason: describeError(e) })); } finally { setBusy(null); }
          })()}>{busy === 'events' ? t('common.loading') : t('tasks.activity.loadOlder')}</button></div>}
        </section>}
      </div>
    </div>
  </section>;
}

function runName(runs: AgentRun[], runId: string): string {
  const index = runs.findIndex((r) => r.id === runId);
  return index < 0 ? t('tasks.runs.earlier') : runLabel(index, runs.length);
}

function EditTask({ task, instructionsEditable, onCancel, onSave }: { task: Task; instructionsEditable: boolean; onCancel(): void; onSave(changes: { title?: string; prompt?: string }): Promise<string | null> }) {
  const [title, setTitle] = useState(task.title), [prompt, setPrompt] = useState(task.prompt);
  const [error, setError] = useState<string | null>(null), [saving, setSaving] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  useEffect(() => { titleRef.current?.focus(); }, []);
  const changes: { title?: string; prompt?: string } = {};
  if (title.trim() && title.trim() !== task.title) changes.title = title.trim();
  if (instructionsEditable && prompt.trim() && prompt.trim() !== task.prompt) changes.prompt = prompt.trim();
  const invalid = !title.trim() ? t('tasks.edit.titleEmpty') : instructionsEditable && !prompt.trim() ? t('tasks.edit.promptEmpty') : null;
  return <form className="taskEdit" onSubmit={(e) => {
    e.preventDefault();
    if (invalid || !Object.keys(changes).length) return;
    setSaving(true); setError(null);
    void onSave(changes).then((refused) => { setError(refused); setSaving(false); });
  }} onKeyDown={(e) => { if (e.key === 'Escape') onCancel(); }}>
    <label className="field"><span>{t('tasks.edit.title')}</span><input ref={titleRef} dir="auto" value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} /></label>
    <label className="field"><span>{t('tasks.detail.instructions')}</span><textarea dir="auto" value={prompt} maxLength={8000} disabled={!instructionsEditable} aria-describedby={instructionsEditable ? undefined : 'task-edit-locked'} onChange={(e) => setPrompt(e.target.value)} /></label>
    {!instructionsEditable && <p className="muted taskHint" id="task-edit-locked">{t('tasks.edit.locked')}</p>}
    {(error || invalid) && <Notice error>{error ?? invalid}</Notice>}
    <div className="actions"><button type="submit" disabled={saving || Boolean(invalid) || !Object.keys(changes).length}>{saving ? t('tasks.edit.saving') : t('tasks.edit.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('tasks.edit.cancel')}</button></div>
  </form>;
}

function Expandable({ text, limit, className }: { text: string; limit: number; className?: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > limit;
  return <div className={className}><p className="taskText" dir="auto">{long && !open ? `${text.slice(0, limit).trimEnd()}…` : text}</p>
    {long && <button type="button" className="linkButton" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? t('tasks.text.less') : t('tasks.text.all', { count: text.length })}</button>}</div>;
}

function Status({ value }: { value: string }) { return <span className={`statusBadge ${value === 'completed' || value === 'approved' ? 'success' : ['failed', 'cancelled', 'rejected', 'expired'].includes(value) ? 'dangerTone' : 'warning'}`}>{statusLabel(value)}</span>; }
function Notice({ children, error }: { children: ReactNode; error?: boolean }) { return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>; }
function Empty({ title, text, children }: { title: string; text: string; children?: ReactNode }) { return <div className="emptyState"><Search aria-hidden="true" /><h3>{title}</h3><p>{text}</p><HelpLink />{children}</div>; }
const date = (v: string) => formatDateTime(v);
const time = (v: string) => formatDateTime(v, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const EVENTS_DENIED = () => t('tasks.activity.denied');
