import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Activity, CheckCircle2, CircleDot, Play, RefreshCw, Search, ShieldCheck, Square, XCircle } from 'lucide-react';
import type { AgentRun, Approval, Task } from '@anum/contracts';
import * as api from './lib/api';
import { SourcesUsed } from './SourcesUsed';
import {
  approvalsForTask, countByStatus, filterTasks, finalAnswer, hasMorePages, isTerminal, mergeTaskPages, parseAnswer,
  runForTask, streamBadge, TASK_PAGE_SIZE, type AnswerBlock, type Inline, type StatusFilter, type StreamState,
} from './lib/tasks';
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
}

const FILTERS: { id: StatusFilter; label: string }[] = [
  { id: 'all', label: 'All statuses' }, { id: 'active', label: 'Active' }, { id: 'waiting_approval', label: 'Waiting approval' },
  { id: 'completed', label: 'Completed' }, { id: 'failed', label: 'Failed' }, { id: 'cancelled', label: 'Cancelled' },
];

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
      setPageError(e instanceof Error ? e.message : 'Could not load more tasks.');
    } finally { setPaging(false); }
  }

  return <>
    <section className="metrics three">
      <Metric icon={<Activity />} label="Active" value={String(counts.active)} detail="Queued, running or waiting" />
      <Metric icon={<CheckCircle2 />} label="Completed" value={String(counts.completed)} detail="Loaded tasks" />
      <Metric icon={<XCircle />} label="Failed or cancelled" value={String(counts.failed)} detail={`${all.length} tasks loaded`} />
    </section>
    <div className="taskLayout">
      <section className="surface composerSurface">
        <div className="sectionHeader">
          <div><p className="eyebrow">New execution</p><h2>Run an agent task</h2></div>
          <span className={`statusBadge ${badge.tone}`} title="Workspace event stream">{badge.label}</span>
        </div>
        <label className="field"><span>Task instructions</span><textarea value={p.prompt} onChange={(e) => p.setPrompt(e.target.value)} placeholder="Describe the result the agent should produce" /></label>
        <div className="actions">
          <button onClick={p.runTask} disabled={p.busy || !p.prompt.trim()}><Play />{p.busy ? 'Running...' : 'Run task'}</button>
          {selected && <button className="secondary" onClick={p.cancel} disabled={p.busy || isTerminal(selected.status)}><Square />Cancel</button>}
          {run && ['failed', 'cancelled'].includes(run.status) && <button className="secondary" onClick={p.resume} disabled={p.busy}><RefreshCw />Resume</button>}
        </div>
        <Notice>{p.message}</Notice>
      </section>
      <section className="surface taskSummary">
        <div className="sectionHeader"><div><p className="eyebrow">Task history</p><h2>{p.loading && !all.length ? 'Loading...' : `${shown.length} of ${all.length} tasks`}</h2></div></div>
        <div className="taskFilters">
          <label className="field taskSearch"><span>Search tasks</span><input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Title, instructions or id" /></label>
          <label className="field"><span>Status</span><select value={filter} onChange={(e) => setFilter(e.target.value as StatusFilter)}>{FILTERS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}</select></label>
        </div>
        <div className="compactList taskList">
          {shown.map((x) => <button className={`recordButton ${selected?.id === x.id ? 'selected' : ''}`} aria-pressed={selected?.id === x.id} key={x.id} onClick={() => p.setTask(x)}><span><strong>{x.title}</strong><small className="taskPromptPreview">{x.prompt}</small><small>{date(x.updatedAt)}</small></span><Status value={x.status} /></button>)}
          {!p.loading && !all.length && <Empty title="No tasks" text="Create a task to start the runtime." />}
          {!!all.length && !shown.length && <Empty title="No matching tasks" text="Change the search or status filter." />}
        </div>
        {pageError && <Notice error>{pageError}</Notice>}
        {more && !!all.length && <div className="actions"><button className="secondary" onClick={() => void loadMore()} disabled={paging}>{paging ? 'Loading...' : 'Load more tasks'}</button></div>}
      </section>
    </div>
    {selected ? <TaskDetail task={selected} run={run} approvals={approvalsForTask(p.approvals, selected.id)} openApprovals={p.openApprovals} /> : <section className="surface"><Empty title="No task selected" text="Run a task or pick one from the history." /></section>}
  </>;
}

function TaskDetail({ task, run, approvals, openApprovals }: { task: Task; run: AgentRun | null; approvals: Approval[]; openApprovals(): void }) {
  const answer = finalAnswer(run);
  const working = !isTerminal(task.status);
  return <>
    <section className="surface taskDetail" aria-labelledby="task-detail-heading">
      <div className="sectionHeader">
        <div><p className="eyebrow">Task detail</p><h2 id="task-detail-heading">{task.title}</h2></div>
        <Status value={task.status} />
      </div>
      <dl className="taskMeta">
        <div><dt>Task</dt><dd><code>{task.id}</code></dd></div>
        <div><dt>Created</dt><dd>{date(task.createdAt)}</dd></div>
        <div><dt>Updated</dt><dd>{date(task.updatedAt)}</dd></div>
        {run && <div><dt>Run</dt><dd><code>{run.id}</code></dd></div>}
      </dl>
      <h3 className="taskSubheading">Instructions</h3>
      <p className="taskInstructions">{task.prompt}</p>
    </section>
    <section className="surface taskAnswer" aria-labelledby="task-answer-heading" aria-live="polite" aria-busy={working && !answer}>
      <div className="sectionHeader"><div><p className="eyebrow">Result</p><h2 id="task-answer-heading">Answer</h2></div>{working && <span className="statusBadge warning">updating</span>}</div>
      {answer ? <Answer blocks={parseAnswer(answer)} /> : <p className="muted">{working ? 'The answer appears here as the run progresses.' : run ? 'This run produced no answer.' : 'No run recorded for this task.'}</p>}
    </section>
    {approvals.length > 0 && <section className="surface" aria-labelledby="task-approvals-heading">
      <div className="sectionHeader"><div><p className="eyebrow">Governance</p><h2 id="task-approvals-heading">Approvals</h2></div><button className="secondary" onClick={openApprovals}><ShieldCheck />Open approvals</button></div>
      <ul className="taskApprovals">{approvals.map((a) => <li key={a.id}><span><strong>{a.action}</strong><small>{a.reason}</small></span><Status value={a.status} /></li>)}</ul>
    </section>}
    <section className="surface timelineSurface">
      <div className="sectionHeader"><div><p className="eyebrow">Execution trace</p><h2>Steps</h2></div>{run && <span className="statusBadge neutral">{run.steps.length} steps</span>}</div>
      {run?.steps.length ? <div className="timeline">{run.steps.map((s, i) => <div className="timelineRow" key={s.id}><div className="timelineRail"><span className="stepDot">{i + 1}</span>{i < run.steps.length - 1 && <span className="railLine" />}</div><div className="timelineBody"><div className="timelineTitle"><strong>{s.type.replaceAll('_', ' ')}</strong><time dateTime={s.createdAt}>{time(s.createdAt)}</time></div><p>{s.summary}</p></div></div>)}</div> : <Empty title="No execution trace loaded" text="Run a task to inspect its execution steps." />}
    </section>
    <SourcesUsed run={run} />
  </>;
}

/** Renders parsed blocks as React text nodes only: model output can never inject markup. */
function Answer({ blocks }: { blocks: AnswerBlock[] }) {
  return <div className="answerBody">{blocks.map((b, i) => {
    if (b.kind === 'heading') return b.level === 1 ? <h3 key={i}><Inlines parts={b.inline} /></h3> : <h4 key={i}><Inlines parts={b.inline} /></h4>;
    if (b.kind === 'code') return <pre key={i}><code>{b.text}</code></pre>;
    if (b.kind === 'list') { const items = b.items.map((it, j) => <li key={j}><Inlines parts={it} /></li>); return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>; }
    return <p key={i}><Inlines parts={b.inline} /></p>;
  })}</div>;
}
function Inlines({ parts }: { parts: Inline[] }) {
  return <>{parts.map((x, i) => x.kind === 'strong' ? <strong key={i}>{x.text}</strong> : x.kind === 'em' ? <em key={i}>{x.text}</em> : x.kind === 'code' ? <code key={i}>{x.text}</code> : <span key={i}>{x.text}</span>)}</>;
}

function Metric({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail: string }) { return <article className="metric"><div className="metricTop"><span>{label}</span><div className="metricIcon">{icon}</div></div><strong>{value}</strong><small>{detail}</small></article>; }
function Status({ value }: { value: string }) { return <span className={`statusBadge ${value === 'completed' || value === 'approved' ? 'success' : ['failed', 'cancelled', 'rejected', 'expired'].includes(value) ? 'dangerTone' : 'warning'}`}>{value.replaceAll('_', ' ')}</span>; }
function Notice({ children, error }: { children: ReactNode; error?: boolean }) { return <p className={`notice ${error ? 'errorNotice' : ''}`}><CircleDot />{children}</p>; }
function Empty({ title, text }: { title: string; text: string }) { return <div className="emptyState"><Search /><h3>{title}</h3><p>{text}</p></div>; }
const date = (v: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(v));
const time = (v: string) => new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(v));
