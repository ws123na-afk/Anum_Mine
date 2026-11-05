/** An agent's work log and results inside the headquarters: the agent record (status, department) and the tasks run as
 * this agent, newest first, paged (`GET /api/v1/tasks?agent_id=`, the Tasks list's own route and permission). Each task
 * opens its drawer with the latest run's result and steps. Assigning more work opens the assign dialog in place. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ClipboardPlus, Loader2 } from 'lucide-react';
import type { AgentProfile, Task } from '@anum/contracts';
import * as api from '../lib/api';
import { agentsApi } from '../lib/agents';
import { describeError, isPermissionDenied } from '../lib/errors';
import { formatDateTime, t } from '../lib/i18n';
import { statusLabel } from '../lib/labels';
import { openAssign } from '../lib/hqAssign';
import { fullPageHash, openHqPanel } from '../lib/hqWorkPanels';
import { WorkDrawer } from './WorkDrawer';
import { AGENT_TASKS_PAGE, agentTasksPath } from './agentTasksPath';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);
const PAGE = AGENT_TASKS_PAGE;

type Load = 'loading' | 'ready' | 'denied' | 'error';

export function AgentWorkPanel({ agentId, name, live, onClose }: { agentId: string; name?: string | null; live?: number | string; onClose: () => void }) {
  const [agent, setAgent] = useState<AgentProfile | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [load, setLoad] = useState<Load>('loading');
  const [error, setError] = useState('');
  const [paging, setPaging] = useState(false);
  const seq = useRef(0);

  const fetchFirst = useCallback(async (quiet = false) => {
    const mine = ++seq.current;
    if (!quiet) setLoad('loading');
    const [a, list] = await Promise.allSettled([agents.get(agentId), api.apiRequest<api.ApiTask[]>(agentTasksPath(agentId, null), { method: 'GET' })]);
    if (mine !== seq.current) return;
    if (a.status === 'fulfilled') { setAgent(a.value); setAgentError(null); }
    else setAgentError(isPermissionDenied(a.reason) ? t('hqWork.agent.private') : describeError(a.reason));
    if (list.status === 'fulfilled') {
      setTasks(list.value.map(api.mapTask)); setHasMore(list.value.length === PAGE); setLoad('ready');
    } else if (!(quiet && load === 'ready')) {
      setError(describeError(list.reason)); setLoad(isPermissionDenied(list.reason) ? 'denied' : 'error');
    }
  }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void fetchFirst(); }, [fetchFirst]);
  const first = useRef(true);
  useEffect(() => { if (first.current) { first.current = false; return; } void fetchFirst(true); }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  const more = async () => {
    const last = tasks.at(-1);
    if (!last || paging) return;
    setPaging(true);
    try {
      const page = (await api.apiRequest<api.ApiTask[]>(agentTasksPath(agentId, last.id), { method: 'GET' })).map(api.mapTask);
      setTasks((ts) => [...ts, ...page.filter((x) => !ts.some((y) => y.id === x.id))]); setHasMore(page.length === PAGE);
    } catch (e) { setError(describeError(e)); setLoad('error'); }
    finally { setPaging(false); }
  };

  const shown = agent?.name ?? name ?? '';
  const canAssign = agent ? agent.status === 'active' && agent.can_run : false;
  return <WorkDrawer region={t('hqWork.agent.region')} title={shown ? t('hqWork.agent.title', { name: shown }) : null} onClose={onClose} onRefresh={() => void fetchFirst()}
    testId="hq-agent-panel" fullPage={{ hash: fullPageHash({ kind: 'agent', id: agentId }), label: t('hqWork.fullPage') }}
    foot={agent && <button type="button" className="hqAsPrimary" disabled={!canAssign} onClick={() => openAssign({ agentId, source: 'agent_card' })}><ClipboardPlus size={14} aria-hidden="true" /> {t('hqAssign.openFor', { name: agent.name })}</button>}>
    {agent && <ul className="hqAsFacts">
      <li>{t('hqWork.agent.status', { status: agent.status === 'paused' ? t('hqAssign.paused') : statusLabel(agent.status) })}</li>
      {agent.department && <li dir="auto">{agent.department}</li>}
      {!canAssign && <li>{agent.status === 'paused' ? t('hqAssign.state.paused') : t('hqAssign.state.denied')}</li>}
    </ul>}
    {agentError && <p className="hqAsNote" role="note">{t('hqWork.agent.recordMissing', { detail: agentError })}</p>}
    <h3>{t('hqWork.agent.log')}</h3>
    {load === 'loading' && <p className="hqAsNote" role="status"><Loader2 size={14} className="hqAsSpin" aria-hidden="true" /> {t('hqWork.agent.loading')}</p>}
    {load === 'denied' && <p className="hqAsBox hqAsWarn" role="alert">{t('hqWork.agent.denied')}</p>}
    {load === 'error' && <div className="hqAsBox hqAsFail" role="alert"><p>{t('hqWork.agent.error', { detail: error })}</p><button type="button" onClick={() => void fetchFirst()}>{t('hqAssign.retry')}</button></div>}
    {load === 'ready' && tasks.length === 0 && <div className="hqAsBox" role="status"><p>{t('hqWork.agent.empty')}</p>
      {canAssign && <button type="button" onClick={() => openAssign({ agentId, source: 'agent_card' })}>{t('hqAssign.openFor', { name: shown })}</button>}</div>}
    {tasks.length > 0 && <ul className="hqWdList" aria-label={t('hqWork.agent.log')}>
      {tasks.map((task) => <li key={task.id} className="hqWdRow">
        <strong dir="auto">{task.title}</strong>
        <small>{statusLabel(task.status)} · {formatDateTime(task.updatedAt || task.createdAt)}</small>
        <button type="button" onClick={() => openHqPanel({ kind: 'task', id: task.id })} aria-label={t('hqWork.agent.openTask', { title: task.title })}>{t('hqWork.open')}</button>
      </li>)}
    </ul>}
    {hasMore && load === 'ready' && <button type="button" onClick={() => void more()} disabled={paging}>{paging ? t('common.loading') : t('hqWork.agent.more')}</button>}
  </WorkDrawer>;
}
