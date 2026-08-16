import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Task, TaskStatus, TenantContext } from '@anum/contracts';
import { AlertTriangle, Ban, Inbox, ListChecks, RefreshCw, Send } from 'lucide-react';
import { ApiError, cancelTask, createAndRunTask, getTask, listEvents } from '../lib/api';

const MAX_TASKS = 15;
const NON_CANCELLABLE_STATUSES: TaskStatus[] = ['completed', 'failed', 'cancelled'];

interface StatusPillInfo {
  className: string;
  label: string;
}

function statusPillInfo(status: TaskStatus): StatusPillInfo {
  switch (status) {
    case 'created':
      return { className: 'badge', label: 'Created' };
    case 'queued':
      return { className: 'badge', label: 'Queued' };
    case 'running':
      return { className: 'pill--info', label: 'Running' };
    case 'waiting_approval':
      return { className: 'pill--warning', label: 'Waiting approval' };
    case 'completed':
      return { className: 'pill--success', label: 'Completed' };
    case 'failed':
      return { className: 'pill--danger', label: 'Failed' };
    case 'cancelled':
      return { className: 'pill--danger', label: 'Cancelled' };
    default:
      return { className: 'badge', label: status };
  }
}

function mergeTasks(fetched: Task[], session: Task[]): Task[] {
  const byId = new Map<string, Task>();
  for (const task of fetched) {
    byId.set(task.id, task);
  }
  for (const task of session) {
    const existing = byId.get(task.id);
    if (!existing || new Date(task.updatedAt).getTime() >= new Date(existing.updatedAt).getTime()) {
      byId.set(task.id, task);
    }
  }
  return Array.from(byId.values())
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
    .slice(0, MAX_TASKS);
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

interface ComposerOutcome {
  taskId: string;
  tone: 'success' | 'warning';
  message: string;
}

export default function TasksView({ tenantContext }: { tenantContext: TenantContext }) {
  void tenantContext;

  const [fetchedTasks, setFetchedTasks] = useState<Task[]>([]);
  const [sessionTasks, setSessionTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);

  const [prompt, setPrompt] = useState('');
  const [composerBusy, setComposerBusy] = useState(false);
  const [composerError, setComposerError] = useState<string | null>(null);
  const [composerOutcome, setComposerOutcome] = useState<ComposerOutcome | null>(null);

  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [cancelErrors, setCancelErrors] = useState<Record<string, string>>({});

  const tasks = useMemo(() => mergeTasks(fetchedTasks, sessionTasks), [fetchedTasks, sessionTasks]);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const events = await listEvents();
      const createdEvents = events
        .filter((event) => event.type === 'task.created')
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
        .slice(0, MAX_TASKS);

      const hydrated = await Promise.all(
        createdEvents.map(async (event) => {
          try {
            return await getTask(event.subject);
          } catch {
            return null;
          }
        }),
      );

      setFetchedTasks(hydrated.filter((task): task is Task => task !== null));
    } catch (err) {
      if (err instanceof ApiError) {
        setLoadError(err.message);
      } else {
        setLoadError('Unable to reach ANUM API. Start the API to run live tasks.');
      }
    } finally {
      setLoading(false);
      setHasLoadedOnce(true);
    }
  }, []);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  async function handleRunTask() {
    const trimmed = prompt.trim();
    if (!trimmed || composerBusy) return;

    setComposerBusy(true);
    setComposerError(null);
    setComposerOutcome(null);
    try {
      const result = await createAndRunTask(trimmed);
      setSessionTasks((prev) => [result.task, ...prev.filter((task) => task.id !== result.task.id)]);
      setComposerOutcome({
        taskId: result.task.id,
        tone: result.approval || result.task.status === 'waiting_approval' ? 'warning' : 'success',
        message: result.approval
          ? `Waiting for approval: "${result.approval.action}" (${result.approval.riskLevel} risk).`
          : `Task ${statusPillInfo(result.task.status).label.toLowerCase()}.`,
      });
      setPrompt('');
    } catch (err) {
      setComposerError(
        err instanceof ApiError ? err.message : 'Unable to reach ANUM API. Start the API to run live tasks.',
      );
    } finally {
      setComposerBusy(false);
    }
  }

  async function handleCancel(taskId: string) {
    setCancellingId(taskId);
    setCancelErrors((prev) => {
      const next = { ...prev };
      delete next[taskId];
      return next;
    });
    try {
      const updated = await cancelTask(taskId);
      setFetchedTasks((prev) => prev.map((task) => (task.id === taskId ? updated : task)));
      setSessionTasks((prev) => prev.map((task) => (task.id === taskId ? updated : task)));
    } catch (err) {
      setCancelErrors((prev) => ({
        ...prev,
        [taskId]: err instanceof ApiError ? err.message : 'Cancel failed. The API may be unreachable.',
      }));
    } finally {
      setCancellingId(null);
    }
  }

  const promptEmpty = prompt.trim().length === 0;

  return (
    <div>
      <div className="viewHeader">
        <div>
          <p className="eyebrow">Tasks</p>
          <h2>Run &amp; monitor agent tasks</h2>
        </div>
      </div>

      <section className="card">
        <h3>New task</h3>
        <div className="taskComposer">
          <label className="field">
            <span>Prompt</span>
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Describe what the agent should do…"
              disabled={composerBusy}
              rows={4}
            />
          </label>
          <div className="actions">
            <button type="button" onClick={handleRunTask} disabled={composerBusy || promptEmpty}>
              <Send size={16} aria-hidden="true" />
              {composerBusy ? 'Running…' : 'Run task'}
            </button>
          </div>
          {composerError ? (
            <div className="errorNotice">
              <AlertTriangle size={16} aria-hidden="true" /> {composerError}
            </div>
          ) : null}
          {composerOutcome ? (
            <div className="notice">
              <span className={composerOutcome.tone === 'warning' ? 'pill--warning' : 'pill--success'}>
                {composerOutcome.tone === 'warning' ? 'Waiting for approval' : 'Completed'}
              </span>{' '}
              {composerOutcome.message}
            </div>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <div className="panelHeader">
          <h3>Recent tasks</h3>
          <button type="button" className="secondary" onClick={loadTasks} disabled={loading}>
            <RefreshCw size={16} aria-hidden="true" />
            Refresh
          </button>
        </div>

        {loading && !hasLoadedOnce ? (
          <div className="list">
            <div className="skeleton" style={{ height: 64 }} />
            <div className="skeleton" style={{ height: 64 }} />
            <div className="skeleton" style={{ height: 64 }} />
          </div>
        ) : loadError ? (
          <div className="errorNotice">
            <AlertTriangle size={16} aria-hidden="true" />
            <p style={{ margin: 0 }}>Unable to reach ANUM API. Start the API to run live tasks.</p>
            <p style={{ margin: '4px 0 0', fontWeight: 400 }}>{loadError}</p>
            <div className="actions" style={{ marginTop: 'var(--space-3)' }}>
              <button type="button" onClick={loadTasks}>
                <RefreshCw size={16} aria-hidden="true" />
                Retry
              </button>
            </div>
          </div>
        ) : tasks.length === 0 ? (
          <div className="emptyState">
            <Inbox size={28} aria-hidden="true" />
            <p>No tasks yet. Run a task above to see it appear here.</p>
          </div>
        ) : (
          <ul className="list" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {tasks.map((task) => {
              const pill = statusPillInfo(task.status);
              const cancellable = !NON_CANCELLABLE_STATUSES.includes(task.status);
              const isCancelling = cancellingId === task.id;
              return (
                <li key={task.id} className="listRow">
                  <ListChecks size={18} aria-hidden="true" />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 'var(--space-2)',
                        flexWrap: 'wrap',
                      }}
                    >
                      <strong>{task.title || 'Untitled task'}</strong>
                      <span className={pill.className}>{pill.label}</span>
                    </div>
                    <p>{task.prompt}</p>
                    <p style={{ fontSize: 'var(--text-xs)' }}>
                      Created {formatTimestamp(task.createdAt)} · Updated {formatTimestamp(task.updatedAt)}
                    </p>
                    {cancelErrors[task.id] ? (
                      <p style={{ color: 'var(--color-danger-text)' }}>{cancelErrors[task.id]}</p>
                    ) : null}
                  </div>
                  <div className="actions" style={{ marginTop: 0, flex: '0 0 auto' }}>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => handleCancel(task.id)}
                      disabled={!cancellable || isCancelling}
                    >
                      <Ban size={16} aria-hidden="true" />
                      {isCancelling ? 'Cancelling…' : 'Cancel'}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
