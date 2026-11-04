/** A task opened beside the office, never a full navigation. It shows the stored task and its latest run, follows the
 * workspace event stream (`live`), and offers the Tasks page's own actions (run, retry, resume, cancel) through the same
 * API calls and the same role rules; the server decides every one. A task waiting for a person opens its approvals here. */
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Play, RefreshCw, RotateCcw, ShieldCheck, Square } from 'lucide-react';
import type { AgentRun, Task } from '@anum/contracts';
import * as api from '../lib/api';
import { describeError, isPermissionDenied } from '../lib/errors';
import { formatDateTime, t } from '../lib/i18n';
import { statusLabel } from '../lib/labels';
import { permissionHint, roleAllows, roleFrom, taskActions, type WorkspaceRole } from '../lib/tasks';
import { cancelTask, getTask, listTaskRuns, resumeRun, retryTask, runTask } from '../lib/tasksApi';
import { fullPageHash, openHqPanel } from '../lib/hqWorkPanels';
import { ShowHistoryLink } from '../HqHistoryLauncher';
import { WorkDrawer } from './WorkDrawer';

// The action browser's live view (docs/action-browser.md#live-view): its own lazy chunk.
const ActionBrowserLive = lazy(() => import('../action-browser/ActionBrowserLive'));
const usedActionBrowser = (run: AgentRun | null) => Boolean(run?.steps.some((s) => s.metadata?.tool === 'web.act'));

export interface TaskPanelProps {
  taskId?: string | null;
  /** A run named by a history record: the panel shows the task that run belongs to. */
  runId?: string | null;
  agentName?: string;
  /** Moves on every workspace event; the panel reads the task again quietly. */
  live?: number | string;
  onClose: () => void;
  /** The office reads its flow and desks again after an action here. */
  onChanged?: () => void;
}

export function TaskPanel({ taskId, runId, agentName, live, onClose, onChanged }: TaskPanelProps) {
  const [id, setId] = useState<string | null>(taskId ?? null);
  const [task, setTask] = useState<Task | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied' | 'gone'>('loading');
  const [error, setError] = useState('');
  const [role, setRole] = useState<WorkspaceRole>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const alive = useRef(true);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setState((s) => (s === 'ready' ? s : 'loading'));
    try {
      let tid = id;
      if (!tid && runId) { tid = (await api.getRun(runId)).taskId; if (alive.current) setId(tid); }
      if (!tid) return;
      const [next, runs] = await Promise.all([getTask(tid), listTaskRuns(tid, null, 1)]);
      if (!alive.current) return;
      setTask(next); setRun(runs[0] ?? null); setState('ready'); setError('');
    } catch (e) {
      if (!alive.current) return;
      const status = (e as { status?: number }).status;
      setError(describeError(e));
      setState((s) => (s === 'ready' && quiet ? s : isPermissionDenied(e) ? 'denied' : status === 404 ? 'gone' : 'error'));
    }
  }, [id, runId]);
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [load]);
  const first = useRef(true);
  useEffect(() => { if (first.current) { first.current = false; return; } void load(true); }, [live]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let on = true;
    api.getCurrentMembership().then((m) => { if (on) setRole(roleFrom(m)); }).catch(() => undefined);
    return () => { on = false; };
  }, []);

  const act = async (name: string, hint: string, fn: () => Promise<string>) => {
    setBusy(name); setNotice(null);
    try { setNotice({ text: await fn(), error: false }); }
    catch (e) { setNotice({ text: isPermissionDenied(e) ? hint : t('hqWork.task.actionFailed', { detail: describeError(e) }), error: true }); }
    finally { setBusy(null); onChanged?.(); void load(true); }
  };
  const outcome = (r: api.RunTaskResult) => { setTask(r.task); setRun(r.run); return r.approval ? t('hqWork.task.paused') : t('hqWork.task.now', { status: statusLabel(r.run.status) }); };

  const can = task ? taskActions(task, run) : null;
  const allowRun = task ? roleAllows(role, 'run', task, api.currentUserId()) : false;
  const allowCancel = task ? roleAllows(role, 'cancel', task, api.currentUserId()) : false;
  const waiting = task?.status === 'waiting_approval' || run?.status === 'waiting_approval';

  return <WorkDrawer region={t('hqAssign.task.region')} title={task?.title ?? t('hqAssign.task.title')} onClose={onClose} onRefresh={() => void load()}
    fullPage={{ hash: fullPageHash({ kind: 'task', id: id ?? '' }), label: t('hqWork.fullPage') }} testId="hq-task-panel"
    foot={task && <ShowHistoryLink record={{ recordKind: 'task', id: task.id, label: task.title }} onOpen={onClose} />}>
    {state === 'loading' && <p className="hqAsNote" role="status"><Loader2 size={14} className="hqAsSpin" aria-hidden="true" /> {t('hqAssign.task.loading')}</p>}
    {state === 'denied' && <p className="hqAsBox hqAsWarn" role="alert">{t('hqWork.task.denied')}</p>}
    {state === 'gone' && <p className="hqAsBox hqAsWarn" role="alert">{t('hqWork.task.gone')}</p>}
    {state === 'error' && <div className="hqAsBox hqAsFail" role="alert"><p>{t('hqAssign.task.error', { detail: error })}</p><button type="button" onClick={() => void load()}>{t('hqAssign.retry')}</button></div>}
    {task && <>
      <ul className="hqAsFacts" aria-live="polite">
        <li data-testid="hq-task-status">{t('hqAssign.task.status', { status: statusLabel(task.status) })}</li>
        {agentName && <li dir="auto">{t('hqAssign.task.agent', { name: agentName })}</li>}
        <li>{t('hqAssign.task.created', { time: formatDateTime(task.createdAt) })}</li>
      </ul>
      {waiting && <div className="hqAsBox hqAsWarn" role="status"><p>{t('hqWork.task.waiting')}</p>
        <button type="button" className="hqAsPrimary" onClick={() => openHqPanel({ kind: 'approvals', taskId: task.id })}><ShieldCheck size={14} aria-hidden="true" /> {t('hqWork.task.review')}</button></div>}
      {can && (can.run || can.retry || can.resume || can.cancel) && <div className="hqWdActions" role="toolbar" aria-label={t('hqWork.task.actions')}>
        {can.run && <button type="button" disabled={Boolean(busy) || !allowRun} onClick={() => void act('run', permissionHint('run'), async () => outcome(await runTask(task.id)))}><Play size={14} aria-hidden="true" /> {busy === 'run' ? t('tasks.action.starting') : t('tasks.action.run')}</button>}
        {can.retry && <button type="button" disabled={Boolean(busy) || !allowRun} onClick={() => void act('retry', permissionHint('run'), async () => outcome(await retryTask(task.id)))}><RotateCcw size={14} aria-hidden="true" /> {busy === 'retry' ? t('tasks.action.retrying') : t('tasks.action.retry')}</button>}
        {can.resume && run && <button type="button" disabled={Boolean(busy) || !allowRun} onClick={() => void act('resume', permissionHint('run'), async () => outcome(await resumeRun(run.id)))}><RefreshW /> {busy === 'resume' ? t('tasks.action.resuming') : t('tasks.resume')}</button>}
        {can.cancel && <button type="button" className="secondary" disabled={Boolean(busy) || !allowCancel} onClick={() => void act('cancel', permissionHint('cancel'), async () => { setTask(await cancelTask(task.id)); return t('tasks.outcome.cancelled'); })}><Square size={14} aria-hidden="true" /> {busy === 'cancel' ? t('tasks.action.cancelling') : t('tasks.cancel')}</button>}
      </div>}
      {can && ((can.run || can.retry || can.resume) && !allowRun || can.cancel && !allowCancel) && <p className="hqAsNote">{!allowRun && (can.run || can.retry || can.resume) ? permissionHint('run') : permissionHint('cancel')}</p>}
      {notice && <p className={notice.error ? 'hqAsBox hqAsFail' : 'hqWdOk'} role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
      {usedActionBrowser(run) && <Suspense fallback={null}><ActionBrowserLive taskId={task.id} pulse={live} onChanged={() => { onChanged?.(); void load(true); }} /></Suspense>}
      <h3>{t('hqAssign.task.instructions')}</h3>
      <p className="hqAsPre" dir="auto">{task.prompt}</p>
      <h3>{t('hqAssign.task.runs')}</h3>
      {!run ? <p className="hqAsNote">{t('hqAssign.task.noRun')}</p> : <>
        <p className="hqAsNote" data-testid="hq-task-run">{statusLabel(run.status)}{run.phase ? ` (${run.phase.replace(/_/g, ' ')})` : ''}</p>
        {run.result && <><h4>{t('hqAssign.task.result')}</h4><p className="hqAsPre" dir="auto">{run.result}</p></>}
        {run.steps.length > 0 && <><h4>{t('hqAssign.task.steps')}</h4><ol className="hqAsSteps">{run.steps.slice(-8).map((s) => <li key={s.id}><code>{s.type}</code> {s.summary}</li>)}</ol></>}
      </>}
    </>}
  </WorkDrawer>;
}

const RefreshW = () => <RefreshCw size={14} aria-hidden="true" />;
