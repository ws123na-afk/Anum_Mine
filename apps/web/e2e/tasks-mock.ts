import type { Page, Route } from '@playwright/test';

// A stateful mock of the Tasks API (docs/tasks.md) for e2e/tasks.spec.ts: filtered keyset list,
// summary, edit, archive, bulk, run history, retry, task events and the run-log export.
export type ApiTask = { id: string; title: string; prompt: string; status: string; tenant_id: string; workspace_id: string; created_at: string; updated_at: string; created_by: string; archived_at: string | null; archived_by: string | null };
type ApiRun = { id: string; task_id: string; status: string; result: string | null; steps: { id: string; type: string; summary: string; created_at: string; metadata?: Record<string, unknown> }[]; created_at: string; updated_at: string; checkpoint: { phase: string } };
type ApiEvent = { id: string; type: string; version: number; tenant_id: string; workspace_id: string; subject: string; correlation_id: string; created_at: string; payload: Record<string, unknown> };

const at = (minutes: number) => new Date(Date.UTC(2026, 9, 10, 9, 0) + minutes * 60_000).toISOString();
export const makeTask = (n: number, extra: Partial<ApiTask> = {}): ApiTask => ({
  id: `task_${String(n).padStart(3, '0')}`, title: `Report ${n}`, prompt: `Write report number ${n}`, status: 'completed',
  tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: at(n), updated_at: at(n), created_by: 'user_local',
  archived_at: null, archived_by: null, ...extra,
});

export interface Options { role?: 'owner' | 'member' | 'viewer'; tasks?: ApiTask[]; failSearch?: boolean }

export async function mockApi(page: Page, options: Options = {}) {
  const tasks: ApiTask[] = options.tasks ?? [
    makeTask(1, { title: 'Quarterly invoice summary', prompt: 'Summarise the quarter invoices', status: 'failed' }),
    makeTask(2, { title: 'Launch checklist', prompt: 'Draft the launch checklist', status: 'completed', created_by: 'user_other' }),
    makeTask(3, { title: 'Draft plan', prompt: 'Plan next week', status: 'created' }),
  ];
  const runs = new Map<string, ApiRun[]>();
  const events: ApiEvent[] = [];
  const seen = { lists: [] as URLSearchParams[], bulk: [] as unknown[], updates: [] as unknown[] };
  // Searches fail until recover() is called (the list error state and its retry).
  let failSearch = options.failSearch ?? false;
  let counter = 0;
  const event = (type: string, taskId: string) => events.unshift({ id: `evt_${++counter}`, type, version: 1, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', subject: taskId, correlation_id: taskId, created_at: new Date(Date.UTC(2026, 9, 10, 10, counter)).toISOString(), payload: { task_id: taskId } });
  const newRun = (task: ApiTask, status: string): ApiRun => {
    const id = `run_${task.id}_${(runs.get(task.id)?.length ?? 0) + 1}`;
    const when = new Date(Date.UTC(2026, 9, 10, 11, ++counter)).toISOString();
    const run: ApiRun = {
      id, task_id: task.id, status, result: status === 'completed' ? `Answer from ${id}` : null, created_at: when, updated_at: when, checkpoint: { phase: status },
      steps: [
        { id: `${id}_model`, type: 'model_call', summary: `Planned ${id}`, created_at: when, metadata: { usage: { input_tokens: 10, output_tokens: 4 } } },
        { id: `${id}_tool`, type: 'tool_proposal', summary: 'Proposed tool call: workspace.summarize', created_at: when, metadata: { tool: 'workspace.summarize', risk_level: 'low', policy_outcome: 'allow' } },
        ...(status === 'failed' ? [{ id: `${id}_result`, type: 'tool_result', summary: `Failure in ${id}`, created_at: when }] : [{ id: `${id}_final`, type: 'final', summary: `Answer from ${id}`, created_at: when }]),
      ],
    };
    runs.set(task.id, [run, ...(runs.get(task.id) ?? [])]);
    return run;
  };
  if (tasks[0]?.status === 'failed') newRun(tasks[0], 'failed');
  if (tasks[1]?.status === 'completed') newRun(tasks[1], 'completed');
  for (const t of tasks) event('task.created', t.id);

  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname, method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const refuse = (status: number, message: string) => json({ error: { code: status === 403 ? 'forbidden' : 'conflict', message, correlation_id: 'corr', details: [] } }, status);
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true, created_at: at(0), updated_at: at(0) });
    const listed = (q: URLSearchParams) => {
      const statuses = q.getAll('status'), text = (q.get('q') ?? '').toLowerCase(), archived = q.get('archived') === 'true';
      const creator = q.get('created_by') === 'me' ? 'user_local' : q.get('created_by');
      return tasks
        .filter((t) => Boolean(t.archived_at) === archived && (!statuses.length || statuses.includes(t.status)) && (!creator || t.created_by === creator)
          && (!text || `${t.title} ${t.prompt} ${t.id}`.toLowerCase().includes(text)))
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
    };
    if (path === '/api/v1/tasks' && method === 'GET') {
      seen.lists.push(url.searchParams);
      if (failSearch && url.searchParams.has('q')) { return json({ error: { code: 'internal_error', message: 'Database unavailable', correlation_id: 'corr', details: [] } }, 503); }
      let rows = listed(url.searchParams);
      const cursor = url.searchParams.get('cursor');
      if (cursor) rows = rows.slice(rows.findIndex((t) => t.id === cursor) + 1);
      return json(rows.slice(0, Number(url.searchParams.get('limit') ?? 50)));
    }
    if (path === '/api/v1/tasks' && method === 'POST') {
      const body = request.postDataJSON() as { title: string; prompt: string };
      const created = makeTask(100 + tasks.length, { title: body.title, prompt: body.prompt, status: 'created' });
      tasks.push(created); event('task.created', created.id);
      return json(created, 201);
    }
    if (path === '/api/v1/tasks/summary') {
      const rows = listed(new URLSearchParams([...url.searchParams].filter(([k]) => k !== 'archived')));
      const byStatus: Record<string, number> = {};
      for (const t of rows) byStatus[t.status] = (byStatus[t.status] ?? 0) + 1;
      return json({ total: rows.length, by_status: byStatus, archived: tasks.filter((t) => t.archived_at).length, creators: [...new Set(tasks.map((t) => t.created_by))].sort() });
    }
    if (path === '/api/v1/tasks/bulk') {
      const body = request.postDataJSON() as { action: string; task_ids: string[] };
      seen.bulk.push(body);
      const results = body.task_ids.map((id) => {
        const t = tasks.find((x) => x.id === id)!;
        if (body.action === 'archive' && t.status === 'running') return { task_id: id, ok: false, status_code: 409, message: 'Cancel the task or let it finish before archiving it', task: null };
        if (body.action === 'archive') { t.archived_at = at(500); t.archived_by = 'user_local'; event('task.archived', id); }
        return { task_id: id, ok: true, status_code: 200, message: null, task: t };
      });
      return json({ action: body.action, succeeded: results.filter((r) => r.ok).length, failed: results.filter((r) => !r.ok).length, results });
    }
    const match = /^\/api\/v1\/tasks\/([^/]+)(\/[a-z-]+)?$/.exec(path);
    const t = match ? tasks.find((x) => x.id === decodeURIComponent(match[1])) : undefined;
    if (match && !t) return refuse(404, 'Task not found');
    if (t && match) {
      const action = match[2] ?? '';
      if (action === '' && method === 'GET') return json(t);
      if (action === '' && method === 'PUT') {
        if (options.role === 'member' && t.created_by !== 'user_local') return refuse(403, "Only the task's creator or a workspace owner can change this task.");
        const body = request.postDataJSON() as { title?: string; prompt?: string };
        seen.updates.push(body);
        Object.assign(t, body, { updated_at: at(600) }); event('task.updated', t.id);
        return json(t);
      }
      if (action === '/runs') {
        const all = runs.get(t.id) ?? [];
        const cursor = url.searchParams.get('cursor');
        const rest = cursor ? all.slice(all.findIndex((r) => r.id === cursor) + 1) : all;
        return json(rest.slice(0, Number(url.searchParams.get('limit') ?? 20)));
      }
      if (action === '/latest-run') return (runs.get(t.id) ?? [])[0] ? json(runs.get(t.id)![0]) : refuse(404, 'Agent run not found');
      if (action === '/events') return json(events.filter((e) => e.correlation_id === t.id));
      if (action === '/retry' || action === '/run') {
        if (options.role === 'viewer') return refuse(403, 'Permission denied: task:run');
        const run = newRun(t, 'completed');
        Object.assign(t, { status: 'completed', updated_at: run.updated_at }); event(action === '/retry' ? 'task.retried' : 'agent_run.completed', t.id);
        return json({ task: t, run, approval: null });
      }
      if (action === '/archive') { Object.assign(t, { archived_at: at(700), archived_by: 'user_local', updated_at: at(700) }); event('task.archived', t.id); return json(t); }
      if (action === '/unarchive') { Object.assign(t, { archived_at: null, archived_by: null, updated_at: at(701) }); event('task.unarchived', t.id); return json(t); }
      if (action === '/cancel') { Object.assign(t, { status: 'cancelled' }); return json(t); }
      if (action === '/export') {
        const format = url.searchParams.get('format');
        return route.fulfill({ status: 200, contentType: format === 'markdown' ? 'text/markdown' : 'application/json', headers: { 'content-disposition': `attachment; filename="${t.id}-run-log.${format === 'markdown' ? 'md' : 'json'}"` }, body: format === 'markdown' ? '# Task run log' : JSON.stringify({ format: 'anum.task-run-log/v1', task: t, runs: runs.get(t.id) ?? [] }) });
      }
    }
    return route.abort();
  });
  return { tasks, seen, recover: () => { failSearch = false; } };
}

