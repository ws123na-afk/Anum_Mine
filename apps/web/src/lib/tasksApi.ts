// Tasks API (docs/tasks.md): filtered keyset list, summary, edit, archive, bulk actions, run
// history, retry, task events and the run-log export. Maps the API's snake_case JSON to contracts.
import type { AgentRun, DomainEvent, Task, TaskBulkAction, TaskBulkResult, TaskSummary } from '@anum/contracts';
import {
  apiBaseUrl, apiRequest, authHeaders, mapApproval, mapRun, mapTask,
  type ApiAgentRun, type ApiRunTaskResponse, type ApiTask, type RunTaskResult,
} from './api';
import { apiErrorFromResponse } from './errors';
import { EVENT_PAGE_SIZE, RUN_PAGE_SIZE, TASK_PAGE_SIZE, taskQueryString, type TaskQuery } from './tasks';

interface ApiDomainEvent { id: string; type: string; version: number; tenant_id: string; workspace_id?: string | null; subject: string; correlation_id: string; created_at: string; payload: Record<string, unknown> }
interface ApiTaskSummary { total: number; by_status: Record<string, number>; archived: number; creators: string[] }
interface ApiTaskBulkResult { action: TaskBulkAction; succeeded: number; failed: number; results: { task_id: string; ok: boolean; status_code: number; message?: string | null; task?: ApiTask | null }[] }

const path = (taskId: string, suffix = '') => `/api/v1/tasks/${encodeURIComponent(taskId)}${suffix}`;
const runResult = (r: ApiRunTaskResponse): RunTaskResult => ({ task: mapTask(r.task), run: mapRun(r.run), approval: r.approval ? mapApproval(r.approval) : null });

export function mapEvent(event: ApiDomainEvent): DomainEvent {
  return {
    id: event.id, type: event.type, version: event.version, tenantId: event.tenant_id,
    workspaceId: event.workspace_id ?? undefined, subject: event.subject, correlationId: event.correlation_id,
    createdAt: event.created_at, payload: event.payload ?? {},
  };
}

/** One page of the filtered task list, newest first; `cursor` is the last task id already shown. */
export async function listTasks(query: TaskQuery, cursor: string | null = null, limit = TASK_PAGE_SIZE): Promise<Task[]> {
  return (await apiRequest<ApiTask[]>(`/api/v1/tasks?${taskQueryString(query, { cursor, limit })}`, { method: 'GET' })).map(mapTask);
}

/** Per-status counts for the list's other filters, the archived count and the creators. */
export async function getTaskSummary(query: TaskQuery): Promise<TaskSummary> {
  const qs = taskQueryString(query, { withStatus: false });
  const s = await apiRequest<ApiTaskSummary>(`/api/v1/tasks/summary${qs ? `?${qs}` : ''}`, { method: 'GET' });
  return { total: s.total, byStatus: s.by_status, archived: s.archived, creators: s.creators };
}

export async function getTask(taskId: string): Promise<Task> {
  return mapTask(await apiRequest<ApiTask>(path(taskId), { method: 'GET' }));
}

export async function updateTask(taskId: string, changes: { title?: string; prompt?: string }): Promise<Task> {
  return mapTask(await apiRequest<ApiTask>(path(taskId), { method: 'PUT', body: JSON.stringify(changes) }));
}

export async function archiveTask(taskId: string): Promise<Task> {
  return mapTask(await apiRequest<ApiTask>(path(taskId, '/archive'), { method: 'POST' }));
}

export async function unarchiveTask(taskId: string): Promise<Task> {
  return mapTask(await apiRequest<ApiTask>(path(taskId, '/unarchive'), { method: 'POST' }));
}

export async function cancelTask(taskId: string): Promise<Task> {
  return mapTask(await apiRequest<ApiTask>(path(taskId, '/cancel'), { method: 'POST' }));
}

export async function runTask(taskId: string): Promise<RunTaskResult> {
  return runResult(await apiRequest<ApiRunTaskResponse>(path(taskId, '/run'), { method: 'POST' }));
}

export async function retryTask(taskId: string): Promise<RunTaskResult> {
  return runResult(await apiRequest<ApiRunTaskResponse>(path(taskId, '/retry'), { method: 'POST' }));
}

export async function resumeRun(runId: string): Promise<RunTaskResult> {
  return runResult(await apiRequest<ApiRunTaskResponse>(`/api/v1/agent-runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' }));
}

export async function bulkTasks(action: TaskBulkAction, taskIds: string[]): Promise<TaskBulkResult> {
  const r = await apiRequest<ApiTaskBulkResult>('/api/v1/tasks/bulk', { method: 'POST', body: JSON.stringify({ action, task_ids: taskIds }) });
  return {
    action: r.action, succeeded: r.succeeded, failed: r.failed,
    results: r.results.map((x) => ({ taskId: x.task_id, ok: x.ok, statusCode: x.status_code, message: x.message ?? null, task: x.task ? mapTask(x.task) : null })),
  };
}

/** The task's runs newest first; `cursor` is the last run id already shown. */
export async function listTaskRuns(taskId: string, cursor: string | null = null, limit = RUN_PAGE_SIZE): Promise<AgentRun[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  return (await apiRequest<ApiAgentRun[]>(path(taskId, `/runs?${params}`), { method: 'GET' })).map(mapRun);
}

/** The task's events newest first; `cursor` is the last event id already shown. */
export async function listTaskEvents(taskId: string, cursor: string | null = null, limit = EVENT_PAGE_SIZE): Promise<DomainEvent[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  return (await apiRequest<ApiDomainEvent[]>(path(taskId, `/events?${params}`), { method: 'GET' })).map(mapEvent);
}

/** Download the task's run log (JSON or Markdown); resolves with the saved file's name. */
export async function downloadTaskRunLog(taskId: string, format: 'json' | 'markdown'): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${path(taskId, `/export?format=${format}`)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const blob = await response.blob();
  const match = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/i);
  const name = match?.[1] ?? `${taskId}-run-log.${format === 'json' ? 'json' : 'md'}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
  // Let the browser start the download before the object URL goes away.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}
