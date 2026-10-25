/**
 * Reads for the office history panel: history pages and the one-record summaries behind a source
 * reference. Read only; the server decides who may read (workspace owners and organisation administrators
 * for the history, each record's own permission for its summary).
 */
import type { OfficeHistoryPage } from '@anum/contracts';
import { parseHistoryPage, type SourceKind } from './hqHistory.ts';

export type HistoryRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export interface SummaryField { label: string; value: string; time: boolean }
export interface SourceSummary { title: string; fields: SummaryField[] }

const ENDPOINT: Readonly<Partial<Record<SourceKind, string>>> = {
  task: '/api/v1/tasks/', run: '/api/v1/agent-runs/', approval: '/api/v1/approvals/', meeting: '/api/v1/agent-meetings/',
};

const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null);

/**
 * A short summary of a task, run, approval or meeting. Only named, non-sensitive fields are read: never a
 * prompt, a result, an approval's arguments or a meeting's turns. `label` is a message key the panel translates.
 */
export function summarizeSource(kind: SourceKind, raw: unknown): SourceSummary {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const fields: SummaryField[] = [];
  const add = (label: string, value: unknown, time = false) => { const v = text(value); if (v) fields.push({ label, value: v, time }); };
  if (kind === 'task') {
    add('hqHist.source.field.status', r.status); add('hqHist.source.field.created', r.created_at, true); add('hqHist.source.field.updated', r.updated_at, true); add('hqHist.source.field.agent', r.agent_id);
    return { title: text(r.title) ?? text(r.id) ?? '-', fields };
  }
  if (kind === 'run') {
    add('hqHist.source.field.status', r.status); add('hqHist.source.field.task', r.task_id); add('hqHist.source.field.created', r.created_at, true); add('hqHist.source.field.updated', r.updated_at, true);
    return { title: text(r.id) ?? '-', fields };
  }
  if (kind === 'approval') {
    add('hqHist.source.field.status', r.status); add('hqHist.source.field.risk', r.risk_level); add('hqHist.source.field.task', r.task_id);
    add('hqHist.source.field.created', r.created_at, true); add('hqHist.source.field.decided', r.decided_at, true);
    return { title: text(r.action) ?? text(r.id) ?? '-', fields };
  }
  add('hqHist.source.field.status', r.status); add('hqHist.source.field.started', r.started_at, true);
  if (Array.isArray(r.participants)) fields.push({ label: 'hqHist.source.field.participants', value: String(r.participants.length), time: false });
  return { title: text(r.title) ?? text(r.id) ?? '-', fields };
}

export function createHistoryClient(request: HistoryRequester, scope: () => { tenantId: string; workspaceId: string }) {
  return {
    async page(path: string): Promise<OfficeHistoryPage> {
      const started = scope();
      const body = await request<OfficeHistoryPage>(path, { method: 'GET' });
      const now = scope();
      if (now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId) throw new Error('The workspace changed while the history loaded');
      return parseHistoryPage(body, started.workspaceId);
    },
    async source(kind: SourceKind, id: string): Promise<SourceSummary> {
      const base = ENDPOINT[kind];
      if (!base) throw new Error('This kind of record has no summary route');
      return summarizeSource(kind, await request<unknown>(`${base}${encodeURIComponent(id)}`, { method: 'GET' }));
    },
  };
}
