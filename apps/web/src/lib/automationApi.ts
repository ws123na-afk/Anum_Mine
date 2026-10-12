// `/api/v1/automation` calls (docs/automation.md). List endpoints return one page and the
// total in the `X-Total-Count` header.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import type {
  AutomationRun, AutomationSchedule, AutomationTrigger, AutomationWorkflow, Page, RunStatus, SchedulePreview, TriggerBody, TriggerEventInfo, TriggerWithSecret, WorkflowBody,
} from './automation';

const base = '/api/v1/automation';
const json = (method: string, body?: unknown): RequestInit => ({ method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

async function page<T>(path: string): Promise<Page<T>> {
  const response = await fetch(`${apiBaseUrl}${path}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const items = (await response.json()) as T[];
  const total = Number(response.headers.get('X-Total-Count'));
  return { items, total: Number.isFinite(total) && response.headers.has('X-Total-Count') ? total : items.length };
}

async function remove(path: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

const id = encodeURIComponent;

export const listWorkflows = (filter: { query?: string; status?: 'active' | 'disabled' | ''; limit: number; offset: number }) =>
  page<AutomationWorkflow>(`${base}/workflows${query({ query: filter.query?.trim(), status: filter.status, limit: filter.limit, offset: filter.offset })}`);
export const getWorkflow = (workflowId: string) => apiRequest<AutomationWorkflow>(`${base}/workflows/${id(workflowId)}`, json('GET'));
export const createWorkflow = (body: WorkflowBody) => apiRequest<AutomationWorkflow>(`${base}/workflows`, json('POST', body));
export const updateWorkflow = (workflowId: string, body: WorkflowBody) => apiRequest<AutomationWorkflow>(`${base}/workflows/${id(workflowId)}`, json('PUT', body));
export const setWorkflowEnabled = (workflowId: string, enabled: boolean) =>
  apiRequest<AutomationWorkflow>(`${base}/workflows/${id(workflowId)}/${enabled ? 'enable' : 'disable'}`, json('POST'));
export const deleteWorkflow = (workflowId: string) => remove(`${base}/workflows/${id(workflowId)}`);

export const listSchedules = (workflowId: string) => page<AutomationSchedule>(`${base}/schedules${query({ workflow_id: workflowId, limit: 500 })}`);
export const previewSchedule = (cron: string, timezone: string, count = 3) => apiRequest<SchedulePreview>(`${base}/schedules/preview`, json('POST', { cron, timezone, count }));
export const createSchedule = (body: { workflow_id: string; name: string; cron: string; timezone: string; enabled: boolean }) =>
  apiRequest<AutomationSchedule>(`${base}/schedules`, json('POST', body));
export const updateSchedule = (scheduleId: string, body: { name?: string; cron?: string; timezone?: string; enabled?: boolean }) =>
  apiRequest<AutomationSchedule>(`${base}/schedules/${id(scheduleId)}`, json('PUT', body));
export const setScheduleEnabled = (scheduleId: string, enabled: boolean) =>
  apiRequest<AutomationSchedule>(`${base}/schedules/${id(scheduleId)}/${enabled ? 'enable' : 'disable'}`, json('POST'));
export const deleteSchedule = (scheduleId: string) => remove(`${base}/schedules/${id(scheduleId)}`);

export const listRuns = (filter: { workflowId?: string; status?: RunStatus | ''; limit: number; offset: number }) =>
  page<AutomationRun>(`${base}/runs${query({ workflow_id: filter.workflowId, status: filter.status, limit: filter.limit, offset: filter.offset })}`);
export const getRun = (runId: string) => apiRequest<AutomationRun>(`${base}/runs/${id(runId)}`, json('GET'));
/** Run now; the key makes a double click start one run, not two. */
export const startRun = (workflowId: string, idempotencyKey: string) =>
  apiRequest<AutomationRun>(`${base}/workflows/${id(workflowId)}/runs`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } });
export const transitionRun = (runId: string, action: 'cancel' | 'resume' | 'retry') => apiRequest<AutomationRun>(`${base}/runs/${id(runId)}/${action}`, json('POST'));

export const takeOverSchedule = (scheduleId: string) => apiRequest<AutomationSchedule>(`${base}/schedules/${id(scheduleId)}/take-over`, json('POST'));

export const listTriggerEvents = () => apiRequest<TriggerEventInfo[]>(`${base}/trigger-events`, json('GET'));
export const listTriggers = (workflowId: string) => page<AutomationTrigger>(`${base}/triggers${query({ workflow_id: workflowId, limit: 500 })}`);
export const createTrigger = (body: TriggerBody & { workflow_id: string; kind: 'event' | 'webhook' }) =>
  apiRequest<TriggerWithSecret>(`${base}/triggers`, json('POST', body));
export const updateTrigger = (triggerId: string, body: Partial<TriggerBody> & { enabled?: boolean }) =>
  apiRequest<AutomationTrigger>(`${base}/triggers/${id(triggerId)}`, json('PUT', { name: body.name, event_type: body.event_type ?? undefined, conditions: body.conditions, rate_limit_per_minute: body.rate_limit_per_minute, replay_window_seconds: body.replay_window_seconds, enabled: body.enabled }));
export const setTriggerEnabled = (triggerId: string, enabled: boolean) =>
  apiRequest<AutomationTrigger>(`${base}/triggers/${id(triggerId)}/${enabled ? 'enable' : 'disable'}`, json('POST'));
export const rotateTriggerSecret = (triggerId: string) => apiRequest<TriggerWithSecret>(`${base}/triggers/${id(triggerId)}/rotate-secret`, json('POST'));
export const takeOverTrigger = (triggerId: string) => apiRequest<AutomationTrigger>(`${base}/triggers/${id(triggerId)}/take-over`, json('POST'));
export const deleteTrigger = (triggerId: string) => remove(`${base}/triggers/${id(triggerId)}`);
