/**
 * The work record of one desk (GET /api/v1/office/workstations/{desk}/work): the agent's newest
 * tasks with run phase, checkpoint and latest steps, pending approvals, recent errors and the
 * documents its runs read. Every part is a stored record; prompts and tool arguments never arrive.
 * Without the endpoint the panel falls back to the task titles the presence reading already has.
 */
import { AlertTriangle, FileText, ShieldAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { endpointMissing, loadDeskWork } from './lib/hqPresenceApi.ts';
import { t } from './lib/i18n.ts';
import type { OfficeWorkstationWork } from '@anum/contracts';
import { ShowHistoryLink } from './HqHistoryLauncher';

type State =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'missing' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; work: OfficeWorkstationWork };

export function WorkstationWork(props: Readonly<{ deskId: string | null; fallbackTasks: readonly string[]; hasPresence: boolean; refreshKey: string | null }>) {
  const [state, setState] = useState<State>(props.deskId ? { kind: 'loading' } : { kind: 'idle' });
  const seq = useRef(0);
  const load = useCallback(async (quiet: boolean) => {
    if (!props.deskId) { setState({ kind: 'idle' }); return; }
    const mine = ++seq.current;
    if (!quiet) setState({ kind: 'loading' });
    try {
      const work = await loadDeskWork(props.deskId);
      if (mine === seq.current) setState({ kind: 'ready', work });
    } catch (error) {
      if (mine !== seq.current) return;
      if (endpointMissing(error)) setState({ kind: 'missing' });
      else setState({ kind: 'error', message: error instanceof Error ? error.message : t('hq.error.generic') });
    }
  }, [props.deskId]);
  useEffect(() => { void load(false); return () => { seq.current += 1; }; }, [load]);
  useEffect(() => { if (props.refreshKey) void load(true); }, [props.refreshKey, load]);

  const fallback = (
    <>
      <h3>{t('hq.agent.tasks')}</h3>
      {props.fallbackTasks.length > 0
        ? <ul className="hqTaskList">{props.fallbackTasks.map((title, i) => <li key={i}>{title}</li>)}</ul>
        : <p className="hqNote">{props.hasPresence ? t('hq.agent.noTasks') : t('hq.agent.noEvidence')}</p>}
    </>
  );
  if (state.kind === 'idle') return fallback;
  if (state.kind === 'loading') return <p className="hqNote" role="status">{t('hq.work.loading')}</p>;
  if (state.kind === 'missing') return <>{fallback}<p className="hqNote">{t('hq.work.missing')}</p></>;
  if (state.kind === 'error') {
    return (
      <div className="hqUnavailable" role="alert">
        <strong>{t('hq.work.error')}</strong>
        <span>{state.message}</span>
        <button type="button" className="hqGhost" onClick={() => void load(false)}>{t('hq.retry')}</button>
      </div>
    );
  }
  const w = state.work;
  if (w.reason === 'unassigned') return <p className="hqNote">{t('hq.work.unassigned')}</p>;
  if (w.reason === 'agent_not_visible') return <p className="hqNote">{t('hq.work.private')}</p>;
  return (
    <div className="hqWork">
      <h3>{t('hq.work.tasks')}</h3>
      {w.tasks.length === 0 ? <p className="hqNote">{t('hq.agent.noTasks')}</p> : (
        <ul className="hqTaskList">
          {w.tasks.map((task) => (
            <li key={task.id}>
              <details>
                <summary>
                  <strong>{task.title ?? t('hq.work.untitled')}</strong>
                  <small>{task.status}{task.due_at ? ` · ${t('hq.work.due', { time: new Date(task.due_at).toLocaleDateString() })}` : ''}</small>
                </summary>
                {task.run ? (
                  <div className="hqRun">
                    <p className="hqMuted">{t('hq.work.run', { status: task.run.status, phase: task.run.phase ?? '-' })}{task.run.checkpoint_version !== null ? ` · ${t('hq.work.checkpoint', { version: task.run.checkpoint_version })}` : ''}</p>
                    {task.run.steps.length > 0 && (
                      <ol className="hqSteps" aria-label={t('hq.work.steps')}>
                        {task.run.steps.slice(-6).map((step) => <li key={step.id}><span>{step.type}</span>{step.summary ? <small>{step.summary}</small> : null}</li>)}
                      </ol>
                    )}
                  </div>
                ) : <p className="hqMuted">{t('hq.work.noRun')}</p>}
                <a className="hqLink" href={'#tasks'}>{t('hq.work.openTasks')}</a>
                <ShowHistoryLink record={{ recordKind: 'task', id: task.id, label: task.title ?? undefined }} />
              </details>
            </li>
          ))}
        </ul>
      )}
      {w.approvals.length > 0 && (
        <>
          <h3><ShieldAlert aria-hidden="true" /> {t('hq.work.approvals')}</h3>
          <ul className="hqTaskList">
            {w.approvals.map((a) => (
              <li key={a.id}>
                <strong>{a.action ?? t('hq.work.untitled')}</strong>
                <small>{a.risk_level}{a.expired ? ` · ${t('hq.work.expired')}` : ''}</small>
                <ShowHistoryLink record={{ recordKind: 'approval', id: a.id, label: a.action ?? undefined }} />
              </li>
            ))}
          </ul>
          <a className="hqLink" href="#approvals">{t('hq.work.openApprovals')}</a>
        </>
      )}
      {w.errors.length > 0 && (
        <>
          <h3><AlertTriangle aria-hidden="true" /> {t('hq.work.errors')}</h3>
          <ul className="hqTaskList">{w.errors.map((e) => <li key={e.run_id}><span>{e.message}</span><small>{new Date(e.at).toLocaleString()}</small></li>)}</ul>
        </>
      )}
      {w.documents.length > 0 && (
        <>
          <h3><FileText aria-hidden="true" /> {t('hq.work.documents')}</h3>
          <ul className="hqTaskList">{w.documents.map((d) => <li key={d.id}>{d.name ?? d.id}</li>)}</ul>
          <a className="hqLink" href="#files">{t('hq.work.openFiles')}</a>
        </>
      )}
      <p className="hqNote">{t('hq.work.asOf', { time: new Date(w.as_of).toLocaleTimeString() })}</p>
    </div>
  );
}
