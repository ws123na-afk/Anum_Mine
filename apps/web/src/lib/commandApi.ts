// Every API call the ANUM Command page makes (docs/command.md#the-web-command-center), in one file,
// plus the global search the command palette uses.
//
// A command is planned first (POST /commands/preview: nothing runs), then confirmed (POST /commands
// creates one task per step) and every step is started (POST /tasks/{id}/run). The live view comes
// from GET /commands/{id}; Stop cancels the command's tasks (POST /tasks/bulk).
import type { CommandNextStep, CommandPlan, CommandSource, CommandSuggestion, CommandSummary, CommandView, QuickCommand, QuickCommandCategory, Task } from '@anum/contracts';
import * as api from './api';
import { listEmailAccounts, listEmailMessages } from './emailApi';
import { EMPTY_FILTERS, type EmailMessage } from './email';
import { listTasks, runTask } from './tasksApi';
import { EMPTY_QUERY } from './tasks';
import { apiErrorFromResponse } from './errors';
import { downloadText } from './clipboard';
import { mapNextStep, mapPlan, mapQuick, mapSuggestion, mapSummary, mapView } from './commands';

type Raw = Record<string, unknown>;
const send = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
const view = (raw: Raw): CommandView => mapView(raw, api.mapTask as (t: never) => Task, api.mapApproval as never);

export interface CommandAttachment { id: string; name: string }

/** The plan a request would run. Read-only: nothing is created or run. */
export const previewCommand = async (text: string, locale: 'en' | 'ar'): Promise<CommandPlan> =>
  mapPlan(await api.apiRequest<Raw>('/api/v1/commands/preview', send('POST', { text, locale })));

/** Create the command (one task per confirmed step, in order). */
export async function createCommand(body: { text: string; steps: string[]; source: CommandSource; locale: 'en' | 'ar'; attachments: string[]; quickCommandId?: string | null }): Promise<CommandView> {
  return view(await api.apiRequest<Raw>('/api/v1/commands', send('POST', {
    text: body.text, steps: body.steps.map((instruction) => ({ instruction })), source: body.source, locale: body.locale, attachments: body.attachments,
    ...(body.quickCommandId ? { quick_command_id: body.quickCommandId } : {}),
  })));
}

/** Start every step at once; rejects with the first refusal (for example the budget) after all were tried. */
export async function startSteps(taskIds: readonly string[]): Promise<void> {
  const results = await Promise.allSettled(taskIds.map((id) => runTask(id)));
  const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected');
  if (failed) throw failed.reason;
}

export const getCommand = async (id: string): Promise<CommandView> => view(await api.apiRequest<Raw>(`/api/v1/commands/${encodeURIComponent(id)}`, { method: 'GET' }));
export async function listCommands(limit: number, cursor: string | null): Promise<CommandSummary[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set('cursor', cursor);
  return (await api.apiRequest<Raw[]>(`/api/v1/commands?${query}`, { method: 'GET' })).map(mapSummary);
}
export const deleteCommand = (id: string) => api.apiRequestVoid(`/api/v1/commands/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const nextSteps = async (id: string): Promise<CommandNextStep[]> =>
  (await api.apiRequest<Raw[]>(`/api/v1/commands/${encodeURIComponent(id)}/next-steps`, { method: 'GET' })).map(mapNextStep);
export const suggestions = async (locale: 'en' | 'ar'): Promise<CommandSuggestion[]> =>
  (await api.apiRequest<Raw[]>(`/api/v1/commands/suggestions?locale=${locale}`, { method: 'GET' })).map(mapSuggestion);
/** The full report as Markdown, saved by the browser (`GET /commands/{id}/report?format=markdown`). */
export async function downloadReport(id: string): Promise<void> {
  const response = await fetch(`${api.apiBaseUrl}/api/v1/commands/${encodeURIComponent(id)}/report?format=markdown`, { headers: await api.authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  downloadText(`anum-command-${id}.md`, await response.text());
}
/** Stop: cancel the command's unfinished tasks (finished ones answer 409 per item and stay as they are). */
export const stopTasks = (taskIds: readonly string[]) => api.apiRequest<unknown>('/api/v1/tasks/bulk', send('POST', { action: 'cancel', task_ids: taskIds }));

export const listQuick = async (): Promise<QuickCommand[]> => (await api.apiRequest<Raw[]>('/api/v1/quick-commands', { method: 'GET' })).map(mapQuick);
export const createQuick = async (body: { title: string; text: string; category: QuickCommandCategory }): Promise<QuickCommand> =>
  mapQuick(await api.apiRequest<Raw>('/api/v1/quick-commands', send('POST', body)));
export const updateQuick = async (id: string, body: Partial<{ title: string; text: string; category: QuickCommandCategory; position: number }>): Promise<QuickCommand> =>
  mapQuick(await api.apiRequest<Raw>(`/api/v1/quick-commands/${encodeURIComponent(id)}`, send('PUT', body)));
export const deleteQuick = (id: string) => api.apiRequestVoid(`/api/v1/quick-commands/${encodeURIComponent(id)}`, { method: 'DELETE' });

/** Upload a document to attach to a command (it lands in Documents like any upload). */
export const uploadAttachment = (file: File, onProgress?: (loaded: number, total: number) => void, signal?: AbortSignal): Promise<api.FileRecord> =>
  api.uploadFile(file, onProgress, signal);
export const fileUsage = () => api.getFileUsage();

/** Unread mail across the connected mailboxes; null when the inbox is not available to this person. */
export async function inboxSummary(): Promise<{ mailboxes: number; unread: number } | null> {
  try {
    const accounts = await listEmailAccounts();
    return { mailboxes: accounts.length, unread: accounts.reduce((sum, account) => sum + account.unread_count, 0) };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- global search (command palette)

export const searchTasks = async (query: string, limit = 5): Promise<Task[]> => listTasks({ ...EMPTY_QUERY, q: query }, null, limit);
export const searchFiles = (query: string, limit = 5): Promise<api.FileRecord[]> => api.getFiles({ query, limit });
export async function searchEmails(query: string, limit = 5): Promise<EmailMessage[]> {
  return (await listEmailMessages({ ...EMPTY_FILTERS, query }, null)).items.slice(0, limit);
}
