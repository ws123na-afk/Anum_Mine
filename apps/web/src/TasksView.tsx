import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Activity, CheckCircle2, CircleDot, Play, RefreshCw, Search, ShieldCheck, Square, XCircle } from 'lucide-react';
import type { AgentRun, Approval, Task } from '@anum/contracts';
import * as api from './lib/api';
import { SourcesUsed, type MemoryTarget } from './SourcesUsed';
import { Answer } from './AnswerView';
import {
  approvalsForTask, countByStatus, filterTasks, finalAnswer, hasMorePages, isTerminal, mergeTaskPages, parseAnswer,
  runForTask, streamBadge, TASK_PAGE_SIZE, type StatusFilter, type StreamState,
} from './lib/tasks';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { statusLabel, stepLabel } from './lib/labels';
import './tasks.css';

export interface TasksViewProps {
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
  /** Type of the last event received; the view refetches the selected run when events arrive. */
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

export function TasksView(p: TasksViewProps) {
  const [older, setOlder] = useState<Task[]>([]);
  const [lastPage, setLastPage] = useState<number | null>(null);
  const [paging, setPaging] = useState(false), [pageError, setPageError] = useState<string | null>(null);
  const [filter, setFilter] = useState<StatusFilter>('all'), [query, setQuery] = useState('');
  const [fetched, setFetched] = useState<AgentRun | null>(null);

  const all = useMemo(() => mergeTaskPages(p.tasks, older), [p.tasks, older]);
  const shown = useMemo(() => filterTasks(all, filter, query), [all, filter, query]);
  const counts = countByStatus(all);
  const more = hasMorePages(lastPage ?? p.tasks.length, TASK_PAGE_SIZE);
  const selected = p.task ? all.find((t) => t.id === p.task?.id) ?? p.task : null;
  const run = runForTask(selected?.id, fetched, p.run);
  const badge = streamBadge(p.stream);

  // Refetch the selected task's latest run whenever it changes or an event arrives, so the
  // timeline and answer follow the run as the runtime records steps.
  const selectedId = selected?.id, selectedUpdated = selected?.updatedAt;
  useEffect(() => {
    if (!selectedId) return;
    let live = true;
    api.getLatestRun(selectedId).then((r) => { if (live) setFetched(r); }).catch(() => { /* never ran, or unreachable: keep what we have */ });
    return () => { live = false; };
  }, [selectedId, selectedUpdated, p.lastEvent]);

  async function loadMore() {
    const cursor = all[all.length - 1]?.id;
    if (!cursor) return;
    setPaging(true); setPageError(null);
    try {
      const page = await api.getTaskPage(cursor, TASK_PAGE_SIZE);
      setOlder((o) => [...o, ...page]);
      setLastPage(page.length);
    } catch (e) {
      setPageError(e instanceof Error ? e.message : t('tasks.loadMoreFailed'));
    } finally { setPaging(false); }
  }

  return <>
    <section className="metrics three">
      <Metric icon={<Activity />} label={t('tasks.metric.active')} value={String(counts.active)} detail={t('tasks.metric.activeDetail')} />
      <Metric icon={<CheckCircle2 />} label={t('tasks.metric.completed')} value={String(counts.completed)} detail={t('tasks.metric.completedDetail')} />
      <Metric icon={<XCircle />} label={t('tasks.metric.failed')} value={String(counts.failed)} detail={t('tasks.metric.failedDetail', { count: all.length })} />
    </section>
    <div className="taskLayout">
      <section className="surface composerSurface">
        <div className="sectionHeader">
          <div><p className="eyebrow">{t('tasks.composer.eyebrow')}</p><h2>{t('tasks.composer.title')}</h2></div>
          <span className={`statusBadge ${badge.tone}`} title={t('tasks.streamTitle')}>{badge.label}</span>
        </div>
        <label className="field"><span>{t('tasks.instructions')}</span><textarea value={p.prompt} onChange={(e) => p.setPrompt(e.target.value)} placeholder={t('tasks.instructionsPlaceholder')} /></label>
        <div className="actions">
          <button onClick={p.runTask} disabled={p.busy || !p.prompt.trim()}><Play aria-hidden="true" />{p.busy ? t('tasks.running') : t('tasks.run')}</button>
          {selected && <button className="secondary" onClick={p.cancel} disabled={p.busy || isTerminal(selected.status)}><Square aria-hidden="true" />{t('tasks.cancel')}</button>}
          {run && ['failed', 'cancelled'].includes(run.status) && <button className="secondary" onClick={p.resume} disabled={p.busy}><RefreshCw aria-hidden="true" />{t('tasks.resume')}</button>}
        </div>
        <Notice>{p.message}</Notice>
      </section>
      <section className="surface taskSummary">
        <div className="sectionHeader"><div><p className="eyebrow">{t('tasks.history.eyebrow')}</p><h2>{p.loading && !all.length ? t('common.loading') : t('tasks.history.count', { shown: shown.length, count: all.length })}</h2></div></div>
        <div className="taskFilters">
          <label className="field taskSearch"><span>{t('tasks.search')}</span><input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('tasks.searchPlaceholder')} /></label>
          <label className="field"><span>{t('tasks.status')}</span><select value={filter} onChange={(e) => setFilter(e.target.value as StatusFilter)}>{FILTERS.map((f) => <option key={f} value={f}>{t(`tasks.filter.${f}` as MessageKey)}</option>)}</select></label>
        </div>
        <div className="compactList taskList">
          {shown.map((x) => <button className={`recordButton ${selected?.id === x.id ? 'selected' : ''}`} aria-pressed={selected?.id === x.id} key={x.id} onClick={() => p.setTask(x)}><span><strong dir="auto">{x.title}</strong><small className="taskPromptPreview" dir="auto">{x.prompt}</small><small>{date(x.updatedAt)}</small></span><Status value={x.status} /></button>)}
          {!p.loading && !all.length && <Empty title={t('tasks.empty.title')} text={t('tasks.empty.text')} />}
          {!!all.length && !shown.length && <Empty title={t('tasks.noMatch.title')} text={t('tasks.noMatch.text')} />}
        </div>
        {pageError && <Notice error>{pageError}</Notice>}
        {more && !!all.length && <div className="actions"><button className="secondary" onClick={() => void loadMore()} disabled={paging}>{paging ? t('common.loading') : t('tasks.loadMore')}</button></div>}
      </section>
    </div>
    {selected ? <TaskDetail task={selected} run={run} approvals={approvalsForTask(p.approvals, selected.id)} openApprovals={p.openApprovals} openMemory={p.openMemory} openFile={p.openFile} /> : <section className="surface"><Empty title={t('tasks.noneSelected.title')} text={t('tasks.noneSelected.text')} /></section>}
  </>;
}

function TaskDetail({ task, run, approvals, openApprovals, openMemory, openFile }: { task: Task; run: AgentRun | null; approvals: Approval[]; openApprovals(): void; openMemory(target: MemoryTarget): void; openFile(fileId: string): void }) {
  const answer = finalAnswer(run);
  const working = !isTerminal(task.status);
  return <>
    <section className="surface taskDetail" aria-labelledby="task-detail-heading">
      <div className="sectionHeader">
        <div><p className="eyebrow">{t('tasks.detail.eyebrow')}</p><h2 id="task-detail-heading" dir="auto">{task.title}</h2></div>
        <Status value={task.status} />
      </div>
      <dl className="taskMeta">
        <div><dt>{t('tasks.detail.task')}</dt><dd><code dir="ltr">{task.id}</code></dd></div>
        <div><dt>{t('tasks.detail.created')}</dt><dd>{date(task.createdAt)}</dd></div>
        <div><dt>{t('tasks.detail.updated')}</dt><dd>{date(task.updatedAt)}</dd></div>
        {run && <div><dt>{t('tasks.detail.run')}</dt><dd><code dir="ltr">{run.id}</code></dd></div>}
      </dl>
      <h3 className="taskSubheading">{t('tasks.detail.instructions')}</h3>
      <p className="taskInstructions" dir="auto">{task.prompt}</p>
    </section>
    <section className="surface taskAnswer" aria-labelledby="task-answer-heading" aria-live="polite" aria-busy={working && !answer}>
      <div className="sectionHeader"><div><p className="eyebrow">{t('tasks.answer.eyebrow')}</p><h2 id="task-answer-heading">{t('tasks.answer.title')}</h2></div>{working && <span className="statusBadge warning">{t('tasks.answer.updating')}</span>}</div>
      {answer ? <Answer blocks={parseAnswer(answer)} /> : <p className="muted">{working ? t('tasks.answer.pending') : run ? t('tasks.answer.none') : t('tasks.answer.noRun')}</p>}
    </section>
    {approvals.length > 0 && <section className="surface taskApprovalsSurface" aria-labelledby="task-approvals-heading">
      <div className="sectionHeader"><div><p className="eyebrow">{t('tasks.approvals.eyebrow')}</p><h2 id="task-approvals-heading">{t('tasks.approvals.title')}</h2></div><button className="secondary" onClick={openApprovals}><ShieldCheck aria-hidden="true" />{t('tasks.approvals.open')}</button></div>
      <ul className="taskApprovals">{approvals.map((a) => <li key={a.id}><span><strong dir="ltr">{a.action}</strong><small dir="auto">{a.reason}</small></span><Status value={a.status} /></li>)}</ul>
    </section>}
    <section className="surface timelineSurface" aria-labelledby="task-steps-heading">
      <div className="sectionHeader"><div><p className="eyebrow">{t('tasks.trace.eyebrow')}</p><h2 id="task-steps-heading">{t('tasks.trace.title')}</h2></div>{run && <span className="statusBadge neutral">{t('tasks.trace.count', { count: run.steps.length })}</span>}</div>
      {run?.steps.length ? <div className="timeline">{run.steps.map((s, i) => <div className="timelineRow" key={s.id}><div className="timelineRail"><span className="stepDot">{i + 1}</span>{i < run.steps.length - 1 && <span className="railLine" />}</div><div className="timelineBody"><div className="timelineTitle"><strong>{stepLabel(s.type)}</strong><time dateTime={s.createdAt}>{time(s.createdAt)}</time></div><p dir="auto">{s.summary}</p></div></div>)}</div> : <Empty title={t('tasks.trace.empty.title')} text={t('tasks.trace.empty.text')} />}
    </section>
    <SourcesUsed run={run} onOpenMemory={openMemory} onOpenFile={openFile} />
  </>;
}


function Metric({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail: string }) { return <article className="metric"><div className="metricTop"><span>{label}</span><div className="metricIcon" aria-hidden="true">{icon}</div></div><strong>{value}</strong><small>{detail}</small></article>; }
function Status({ value }: { value: string }) { return <span className={`statusBadge ${value === 'completed' || value === 'approved' ? 'success' : ['failed', 'cancelled', 'rejected', 'expired'].includes(value) ? 'dangerTone' : 'warning'}`}>{statusLabel(value)}</span>; }
function Notice({ children, error }: { children: ReactNode; error?: boolean }) { return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>; }
function Empty({ title, text }: { title: string; text: string }) { return <div className="emptyState"><Search aria-hidden="true" /><h3>{title}</h3><p>{text}</p></div>; }
const date = (v: string) => formatDateTime(v);
const time = (v: string) => formatDateTime(v, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
