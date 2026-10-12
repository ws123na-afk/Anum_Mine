// Every API call the Command Center makes (docs/design/anum360.md#web-command-center), in one file.
//
// A command runs today as a task: create it and run it (POST /api/v1/tasks, POST /tasks/{id}/run);
// a spoken command first goes through the voice API, which keeps the transcript under the
// session's retention and creates the task from it (POST /voice/sessions/{id}/commands).
// Progress comes from the run (GET /tasks/{id}/latest-run) and the workspace event stream.
// When the plan-preview API (docs/command.md) lands, `submitCommand` becomes "preview the plan,
// then confirm it" here; the page keeps calling the same functions.
import type { AgentRun, Approval, Task } from '@anum/contracts';
import * as api from './api';
import { ApiError } from './errors';
import { listEmailAccounts, listEmailMessages } from './emailApi';
import { EMPTY_FILTERS, type EmailMessage } from './email';
import { listTasks, retryTask, runTask } from './tasksApi';
import { EMPTY_QUERY } from './tasks';
import { appendTranscript, completeVoiceSession, createVoiceSession, submitVoiceCommand } from './voice';
import { commandPrompt, commandTitle } from './commandCenter';

export interface CommandResult { task: Task; run: AgentRun | null; approval: Approval | null }
export interface CommandAttachment { id: string; name: string }

/** Give ANUM a command: typed (a task) or spoken (a voice session's transcript, then the task). */
export async function submitCommand(text: string, options: { source: 'text' | 'voice'; locale: string; attachments?: readonly CommandAttachment[] }): Promise<CommandResult> {
  const prompt = commandPrompt(text, options.attachments ?? []);
  if (options.source === 'text') return api.createAndRunTask(prompt, commandTitle(text));
  const session = await createVoiceSession(options.locale, 'session');
  try {
    const segment = await appendTranscript(session.id, prompt, 1, true);
    const created = await submitVoiceCommand(session.id, segment.id);
    return await runTask(created.task.id);
  } finally {
    void completeVoiceSession(session.id).catch(() => undefined);
  }
}

/** The task's latest run, or null when it has not run yet. */
export async function getCommandRun(taskId: string): Promise<AgentRun | null> {
  try {
    return await api.getLatestRun(taskId);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const getCommandTask = (taskId: string): Promise<Task> => api.getTask(taskId);
export const stopCommand = (taskId: string): Promise<Task> => api.cancelTask(taskId);
export const retryCommand = (taskId: string): Promise<CommandResult> => retryTask(taskId);

/** Upload a document to attach to a command (it lands in Documents like any upload). */
export const uploadAttachment = (file: File, onProgress?: (loaded: number, total: number) => void, signal?: AbortSignal): Promise<api.FileRecord> =>
  api.uploadFile(file, onProgress, signal);

/** Recent documents, for Files mode and Results Preview. */
export const recentFiles = (limit = 5): Promise<api.FileRecord[]> => api.getFiles({ sort: 'created_at:desc', limit });
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
