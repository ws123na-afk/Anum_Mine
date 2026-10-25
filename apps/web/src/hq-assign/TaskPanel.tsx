/** A task opened beside the office, never a full navigation. Read only: it shows the stored task and its latest run. */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, RefreshCcw, X } from 'lucide-react';
import type { AgentRun, Task } from '@anum/contracts';
import { describeError } from '../lib/errors';
import { formatDateTime, t } from '../lib/i18n';
import { getTask, listTaskRuns } from '../lib/tasksApi';
import { useLocale } from '../useLocale';
import './hqAssign.css';

export function TaskPanel({ taskId, agentName, onClose, onNavigate }: { taskId: string; agentName?: string; onClose: () => void; onNavigate: (hash: string) => void }) {
  const locale = useLocale();
  const heading = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const [task, setTask] = useState<Task | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const alive = useRef(true);
  const load = useCallback(async () => {
    setState((s) => (s === 'ready' ? s : 'loading'));
    try {
      const [next, runs] = await Promise.all([getTask(taskId), listTaskRuns(taskId, null, 1)]);
      if (!alive.current) return;
      setTask(next); setRun(runs[0] ?? null); setState('ready'); setError('');
    } catch (e) {
      if (!alive.current) return;
      setError(describeError(e)); setState((s) => (s === 'ready' ? s : 'error'));
    }
  }, [taskId]);
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [load]);
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    heading.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); opener?.focus?.(); };
  }, [onClose]);

  return createPortal(<aside className="hqAsSide" dir={locale === 'ar' ? 'rtl' : 'ltr'} role="complementary" aria-label={t('hqAssign.task.region')}>
    <header className="hqAsHead">
      <h2 id={titleId} ref={heading} tabIndex={-1} dir="auto">{task?.title ?? t('hqAssign.task.title')}</h2>
      <button type="button" className="hqAsX" onClick={() => void load()} aria-label={t('hqAssign.task.refresh')}><RefreshCcw size={15} aria-hidden="true" /></button>
      <button type="button" className="hqAsX" onClick={onClose} aria-label={t('hqAssign.task.close')}><X size={16} aria-hidden="true" /></button>
    </header>
    <div className="hqAsBody">
      {state === 'loading' && <p className="hqAsNote" role="status"><Loader2 size={14} className="hqAsSpin" aria-hidden="true" /> {t('hqAssign.task.loading')}</p>}
      {error && <div className="hqAsBox hqAsFail" role="alert"><p>{t('hqAssign.task.error', { detail: error })}</p><button type="button" onClick={() => void load()}>{t('hqAssign.retry')}</button></div>}
      {task && <>
        <ul className="hqAsFacts">
          <li>{t('hqAssign.task.status', { status: task.status.replace(/_/g, ' ') })}</li>
          {agentName && <li dir="auto">{t('hqAssign.task.agent', { name: agentName })}</li>}
          <li>{t('hqAssign.task.created', { time: formatDateTime(task.createdAt) })}</li>
        </ul>
        <h3>{t('hqAssign.task.instructions')}</h3>
        <p className="hqAsPre" dir="auto">{task.prompt}</p>
        <h3>{t('hqAssign.task.runs')}</h3>
        {!run ? <p className="hqAsNote">{t('hqAssign.task.noRun')}</p> : <>
          <p className="hqAsNote">{run.status.replace(/_/g, ' ')}{run.phase ? ` - ${run.phase}` : ''}</p>
          {run.result && <><h4>{t('hqAssign.task.result')}</h4><p className="hqAsPre" dir="auto">{run.result}</p></>}
          {run.steps.length > 0 && <><h4>{t('hqAssign.task.steps')}</h4><ol className="hqAsSteps">{run.steps.slice(-8).map((s) => <li key={s.id}><code>{s.type}</code> {s.summary}</li>)}</ol></>}
        </>}
        <div className="hqAsFoot"><button type="button" onClick={() => onNavigate('#tasks')}>{t('hqAssign.task.openTasks')}</button></div>
      </>}
    </div>
  </aside>, document.body);
}
