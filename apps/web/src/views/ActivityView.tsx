import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Ban,
  Bot,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  FilePlus2,
  Hourglass,
  ListChecks,
  OctagonX,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  ShieldQuestion,
  ShieldX,
  XCircle,
  type LucideIcon,
} from 'lucide-react';
import type { AgentRun, DomainEvent, TenantContext } from '@anum/contracts';
import { ApiError, getAgentRun, listEvents } from '../lib/api';

type Category = 'task' | 'approval' | 'agent_run' | 'other';
type CategoryFilter = 'all' | 'task' | 'approval' | 'agent_run';

interface RunDrilldownState {
  status: 'loading' | 'loaded' | 'error';
  run?: AgentRun;
  error?: string;
}

interface DayGroup {
  label: string;
  events: DomainEvent[];
}

const CATEGORY_FILTERS: { id: CategoryFilter; label: string; icon: LucideIcon }[] = [
  { id: 'all', label: 'All', icon: Activity },
  { id: 'task', label: 'Tasks', icon: ListChecks },
  { id: 'approval', label: 'Approvals', icon: ShieldCheck },
  { id: 'agent_run', label: 'Agent runs', icon: Bot },
];

const EVENT_ICONS: Record<string, LucideIcon> = {
  'task.created': FilePlus2,
  'task.queued': Clock,
  'task.started': PlayCircle,
  'task.completed': CheckCircle2,
  'task.failed': XCircle,
  'task.cancelled': Ban,
  'agent_run.started': Bot,
  'agent_run.waiting_approval': Hourglass,
  'agent_run.completed': CheckCheck,
  'agent_run.failed': OctagonX,
  'approval.requested': ShieldQuestion,
  'approval.approved': ShieldCheck,
  'approval.rejected': ShieldX,
  'approval.expired': ShieldOff,
};

function getCategory(type: string): Category {
  if (type.startsWith('task.')) return 'task';
  if (type.startsWith('approval.')) return 'approval';
  if (type.startsWith('agent_run.')) return 'agent_run';
  return 'other';
}

function getCategoryLabel(category: Category): string {
  switch (category) {
    case 'task':
      return 'Task';
    case 'approval':
      return 'Approval';
    case 'agent_run':
      return 'Agent run';
    default:
      return 'Event';
  }
}

function getEventIcon(type: string): LucideIcon {
  return EVENT_ICONS[type] ?? Activity;
}

function getStatusVariant(type: string): 'success' | 'warning' | 'danger' | 'info' {
  if (type.endsWith('.completed') || type.endsWith('.approved')) return 'success';
  if (type.endsWith('.failed') || type.endsWith('.rejected')) return 'danger';
  if (
    type.endsWith('.waiting_approval') ||
    type.endsWith('.requested') ||
    type.endsWith('.expired') ||
    type.endsWith('.queued')
  ) {
    return 'warning';
  }
  return 'info';
}

/** Defensively pull the first non-empty string value out of a loosely-typed payload bag. */
function getPayloadString(payload: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === 'string' && value.trim().length > 0) {
      return value;
    }
  }
  return undefined;
}

function describeEvent(event: DomainEvent): string {
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  const title = getPayloadString(payload, ['title']);
  const reason = getPayloadString(payload, ['reason', 'error', 'message']);
  const action = getPayloadString(payload, ['action']);

  switch (event.type) {
    case 'task.created':
      return title ? `Task created: ${title}` : 'Task created';
    case 'task.queued':
      return 'Task queued for execution';
    case 'task.started':
      return 'Task started';
    case 'task.completed':
      return 'Task completed';
    case 'task.failed':
      return reason ? `Task failed: ${reason}` : 'Task failed';
    case 'task.cancelled':
      return 'Task cancelled';
    case 'agent_run.started':
      return 'Agent run started';
    case 'agent_run.waiting_approval':
      return 'Agent run is waiting on approval';
    case 'agent_run.completed':
      return 'Agent run completed';
    case 'agent_run.failed':
      return reason ? `Agent run failed: ${reason}` : 'Agent run failed';
    case 'approval.requested':
      return action ? `Approval requested for ${action}` : 'Approval requested';
    case 'approval.approved':
      return 'Approval granted';
    case 'approval.rejected':
      return reason ? `Approval rejected: ${reason}` : 'Approval rejected';
    case 'approval.expired':
      return 'Approval request expired';
    default:
      return event.type;
  }
}

/** Only agent_run.* events can be drilled into. Look for a run id in a few likely payload shapes,
 *  falling back to `subject` (the related-entity id) since that is often the run id for these events. */
function extractRunId(event: DomainEvent): string | undefined {
  if (!event.type.startsWith('agent_run.')) return undefined;
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  const fromPayload = getPayloadString(payload, ['runId', 'run_id', 'agentRunId', 'agent_run_id', 'id']);
  if (fromPayload) return fromPayload;
  if (typeof event.subject === 'string' && event.subject.trim().length > 0) return event.subject;
  return undefined;
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function formatDayLabel(date: Date): string {
  const today = startOfDay(new Date());
  const day = startOfDay(date);
  const diffDays = Math.round((today.getTime() - day.getTime()) / 86_400_000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  return day.toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
    year: day.getFullYear() !== today.getFullYear() ? 'numeric' : undefined,
  });
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

function groupByDay(events: DomainEvent[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const event of events) {
    const label = formatDayLabel(new Date(event.createdAt));
    const last = groups[groups.length - 1];
    if (last && last.label === label) {
      last.events.push(event);
    } else {
      groups.push({ label, events: [event] });
    }
  }
  return groups;
}

export default function ActivityView({ tenantContext: _tenantContext }: { tenantContext: TenantContext }) {
  const [events, setEvents] = useState<DomainEvent[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<CategoryFilter>('all');
  const [expandedEventIds, setExpandedEventIds] = useState<Set<string>>(new Set());
  const [runCache, setRunCache] = useState<Record<string, RunDrilldownState>>({});

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listEvents();
      setEvents(data);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Failed to load the activity feed.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchEvents();
  }, [fetchEvents]);

  const sortedEvents = useMemo(() => {
    if (!events) return [];
    return [...events].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
  }, [events]);

  const filteredEvents = useMemo(() => {
    if (filter === 'all') return sortedEvents;
    return sortedEvents.filter((event) => getCategory(event.type) === filter);
  }, [sortedEvents, filter]);

  const groups = useMemo(() => groupByDay(filteredEvents), [filteredEvents]);

  const loadRun = useCallback(async (runId: string) => {
    setRunCache((prev) => ({ ...prev, [runId]: { status: 'loading' } }));
    try {
      const run = await getAgentRun(runId);
      setRunCache((prev) => ({ ...prev, [runId]: { status: 'loaded', run } }));
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Failed to load run details.';
      setRunCache((prev) => ({ ...prev, [runId]: { status: 'error', error: message } }));
    }
  }, []);

  const handleToggleRun = useCallback(
    (eventId: string, runId: string) => {
      setExpandedEventIds((prev) => {
        const next = new Set(prev);
        if (next.has(eventId)) {
          next.delete(eventId);
        } else {
          next.add(eventId);
        }
        return next;
      });
      const cached = runCache[runId];
      if (!cached || cached.status === 'error') {
        void loadRun(runId);
      }
    },
    [runCache, loadRun],
  );

  const hasEvents = !loading && !loadError && !!events;

  return (
    <>
      <div className="viewHeader">
        <div>
          <p className="eyebrow">Live feed</p>
          <h2>Activity</h2>
        </div>
        <button type="button" className="secondary" onClick={() => void fetchEvents()} disabled={loading}>
          <RefreshCw size={16} aria-hidden="true" />
          Refresh
        </button>
      </div>

      <div className="card">
        {loading && (
          <div className="list">
            <div className="skeleton" style={{ height: 64 }} />
            <div className="skeleton" style={{ height: 64 }} />
            <div className="skeleton" style={{ height: 64 }} />
          </div>
        )}

        {!loading && loadError && (
          <div className="errorNotice">
            <p>{loadError}</p>
            <div className="actions">
              <button type="button" onClick={() => void fetchEvents()}>
                <RefreshCw size={16} aria-hidden="true" />
                Retry
              </button>
            </div>
          </div>
        )}

        {hasEvents && events!.length === 0 && (
          <div className="emptyState">
            <Activity size={28} aria-hidden="true" />
            <p>No activity yet. Task, approval, and agent run events will show up here as they happen.</p>
          </div>
        )}

        {hasEvents && events!.length > 0 && (
          <>
            <div className="filterBar" role="group" aria-label="Filter activity by category">
              {CATEGORY_FILTERS.map((f) => {
                const FilterIcon = f.icon;
                const active = filter === f.id;
                return (
                  <button
                    key={f.id}
                    type="button"
                    className={active ? undefined : 'secondary'}
                    aria-pressed={active}
                    onClick={() => setFilter(f.id)}
                  >
                    <FilterIcon size={16} aria-hidden="true" />
                    {f.label}
                  </button>
                );
              })}
            </div>

            {filteredEvents.length === 0 && (
              <div className="emptyState">
                <Activity size={28} aria-hidden="true" />
                <p>No events match this filter.</p>
              </div>
            )}

            {filteredEvents.length > 0 &&
              groups.map((group) => (
                <section key={group.label} style={{ marginTop: 'var(--space-6)' }}>
                  <h3 style={{ marginBottom: 'var(--space-2)', color: 'var(--color-text-muted)' }}>{group.label}</h3>
                  <ul className="timeline" style={{ listStyleType: 'none', paddingLeft: 0 }}>
                    {group.events.map((event) => {
                      const Icon = getEventIcon(event.type);
                      const category = getCategory(event.type);
                      const runId = extractRunId(event);
                      const isExpanded = expandedEventIds.has(event.id);
                      const runState = runId ? runCache[runId] : undefined;

                      return (
                        <li key={event.id} className="step">
                          <Icon size={18} aria-hidden="true" />
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                              <strong>{describeEvent(event)}</strong>
                              <span className={`pill pill--${getStatusVariant(event.type)}`}>
                                {getCategoryLabel(category)}
                              </span>
                            </div>
                            <p>
                              {formatTime(event.createdAt)}
                              {' · '}
                              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
                                subject {event.subject}
                              </span>
                              {event.correlationId ? (
                                <>
                                  {' · '}
                                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
                                    corr {event.correlationId}
                                  </span>
                                </>
                              ) : null}
                            </p>

                            {runId && (
                              <div style={{ marginTop: 'var(--space-2)' }}>
                                <button
                                  type="button"
                                  className="secondary"
                                  aria-expanded={isExpanded}
                                  onClick={() => handleToggleRun(event.id, runId)}
                                >
                                  {isExpanded ? (
                                    <ChevronDown size={16} aria-hidden="true" />
                                  ) : (
                                    <ChevronRight size={16} aria-hidden="true" />
                                  )}
                                  {isExpanded ? 'Hide run details' : 'View run details'}
                                </button>

                                {isExpanded && (
                                  <div className="card" style={{ marginTop: 'var(--space-3)' }}>
                                    {(!runState || runState.status === 'loading') && (
                                      <div className="skeleton" style={{ height: 48 }} />
                                    )}

                                    {runState && runState.status === 'error' && (
                                      <div className="errorNotice">
                                        <p>{runState.error}</p>
                                        <div className="actions">
                                          <button type="button" onClick={() => void loadRun(runId)}>
                                            <RefreshCw size={16} aria-hidden="true" />
                                            Retry
                                          </button>
                                        </div>
                                      </div>
                                    )}

                                    {runState && runState.status === 'loaded' && runState.run && (
                                      runState.run.steps.length > 0 ? (
                                        <ul className="list" style={{ listStyleType: 'none', paddingLeft: 0 }}>
                                          {runState.run.steps.map((step) => (
                                            <li key={step.id} className="listRow">
                                              <div>
                                                <strong>{step.type.replace(/_/g, ' ')}</strong>
                                                <p>{step.summary}</p>
                                                <p style={{ fontSize: 'var(--text-xs)' }}>{formatTime(step.createdAt)}</p>
                                              </div>
                                            </li>
                                          ))}
                                        </ul>
                                      ) : (
                                        <p style={{ color: 'var(--color-text-muted)' }}>
                                          No steps recorded for this run yet.
                                        </p>
                                      )
                                    )}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
          </>
        )}
      </div>
    </>
  );
}
