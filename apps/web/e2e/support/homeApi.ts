import type { Route } from '@playwright/test';

// Home (/api/v1/home, docs/home.md) and ANUM Command (/api/v1/commands, /api/v1/quick-commands,
// docs/command.md) for the mocked workspace, plus the calendar and WhatsApp counts Home reads.
// State lives per page, so a spec can add pins or start commands and see them come back.
const iso = (offsetMinutes = 0) => new Date(Date.now() + offsetMinutes * 60_000).toISOString();
const todayAt = (hour: number, minute = 0) => { const d = new Date(); d.setHours(hour, minute, 0, 0); return d.toISOString(); };

export const homeSummary = () => ({
  generated_at: iso(),
  profile: { display_name: 'Sara Khan', updated_at: iso() },
  focus: [
    { id: 'approvals:approval_pending', source: 'approvals', kind: 'approval_pending', title: 'Approve external.action', detail: 'Review the release', severity: 'high', at: iso(-30), target: { feature: 'approvals', id: 'approval_pending' }, params: {} },
    { id: 'email:msg_1', source: 'email', kind: 'email_unread', title: 'Invoice overdue from Gulf Tyres', detail: 'accounts@example.com', severity: 'medium', at: iso(-60), target: { feature: 'email', id: 'msg_1' }, params: {} },
    { id: 'tasks:task_release', source: 'tasks', kind: 'task_running', title: 'Review the release', detail: 'Waiting for approval', progress: 0.5, severity: 'medium', at: iso(-5), target: { feature: 'tasks', id: 'task_release' }, params: {} },
  ],
  counts: { approvals: 1, email: 1, tasks: 1, automations: 0 },
  progress: { completed: 3, open: 2 },
  activity: Array.from({ length: 8 }, (_, i) => ({
    id: `activity_${i}`, kind: i % 3 === 0 ? 'automation_run' : 'task', title: i === 0 ? 'Daily digest ran' : `Task update ${i}`, status: i % 4 === 1 ? 'failed' : 'completed', at: iso(-10 * (i + 1)),
    target: { feature: i % 3 === 0 ? 'automations' : 'tasks', id: null },
  })),
  sources: [{ name: 'approvals', status: 'ok' }, { name: 'email', status: 'ok' }, { name: 'tasks', status: 'ok' }, { name: 'automations', status: 'ok' }, { name: 'meetings', status: 'not_installed' }],
});

const insights = {
  items: [{
    key: 'tasks:stalled:task_release', source: 'tasks', kind: 'task_stalled', severity: 'medium', title: 'A task has not moved for 2 hours',
    finding: '"Review the release" has waited since this morning.', question: 'Check it now?', params: {},
    action: { type: 'command', verb: 'analyze', command: 'Find out why the release review is stuck' }, created_at: iso(-120), state: 'new',
  }],
  hidden: 0,
  sources: [{ name: 'tasks', status: 'ok' }],
};

const step = (position: number, title: string, agent: string, status: string, extra: Record<string, unknown> = {}) => ({
  position, title, instruction: title, agent: { id: agent, name: agent === 'email' ? 'Email agent' : 'Calendar agent' }, tool: agent === 'email' ? 'search_email' : null,
  risk_level: 'low', approval: 'none', task_id: `task_cmd_${position}`, task: null, status, progress: status === 'completed' ? 1 : 0.4, run_id: status === 'created' ? null : `run_${position}`,
  result: status === 'completed' ? 'Three urgent emails: the overdue invoice, a delivery delay and a quote request.' : null, started_at: iso(-3), finished_at: status === 'completed' ? iso(-1) : null, approvals: [], ...extra,
});

export const doneCommand = () => ({
  id: 'cmd_done', text: 'Check my emails and summarize the urgent ones', source: 'typed', locale: 'en', status: 'completed', progress: 1, step_count: 1, completed_steps: 1,
  pending_approvals: 0, agents: [{ id: 'email', name: 'Email agent' }], created_at: iso(-60), updated_at: iso(-58),
  created_by: 'user_local', quick_command_id: null, steps: [step(1, 'Check my emails and summarize the urgent ones', 'email', 'completed')], pending: [], timeline: [],
  files: [], attachments: [], started_at: iso(-60), finished_at: iso(-58), duration_seconds: 42,
});

export const previewPlan = (text: string) => ({
  text, language: 'en',
  steps: [
    { position: 1, title: 'Check my emails, find today\'s urgent items', instruction: 'Check my emails, find today\'s urgent items', agent: { id: 'email', name: 'Email agent' }, skills: [], tool: { name: 'search_email', description: 'Search the inbox', category: 'email', action_kind: 'read', target: null }, risk_level: 'low', approval: 'none', approval_reason: 'The tool is low risk.', connected: true, note: null },
    { position: 2, title: 'Schedule a meeting with the team tomorrow at 10 AM', instruction: 'Schedule a meeting with the team tomorrow at 10 AM', agent: { id: 'calendar', name: 'Calendar agent' }, skills: [], tool: null, risk_level: 'medium', approval: 'required', approval_reason: 'Sending invitations needs approval.', connected: false, note: 'No calendar is connected yet, so ANUM drafts the meeting for you to send.' },
  ],
  agents: [{ id: 'email', name: 'Email agent' }, { id: 'calendar', name: 'Calendar agent' }],
  approvals_required: 1, blocked: 0,
});

export interface HomeMockState { pins: Record<string, unknown>[]; quick: Record<string, unknown>[]; commands: Record<string, unknown>[]; created: unknown[]; runs: string[]; approved: unknown[] }

export function createHomeMock() {
  const state: HomeMockState = {
    pins: [{ id: 'pin_tasks', kind: 'feature', label: 'Tasks', target: 'tasks', icon: null, position: 0, created_at: iso(), updated_at: iso() }],
    quick: [
      { id: 'quick_1', title: 'Morning briefing', text: 'Summarize my unread email and today\'s tasks', category: 'work', position: 0, use_count: 4, last_used_at: iso(-1440), created_at: iso(-9000), updated_at: iso(-9000) },
      { id: 'quick_2', title: 'Plan my week', text: 'Plan my week from my open tasks', category: 'personal', position: 0, use_count: 0, last_used_at: null, created_at: iso(-9000), updated_at: iso(-9000) },
    ],
    commands: [doneCommand()],
    created: [], runs: [], approved: [],
  };
  async function handle(route: Route, path: string, method: string): Promise<boolean> {
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).then(() => true);
    const body = () => route.request().postDataJSON() as Record<string, unknown>;
    if (path === '/api/v1/home/summary') return json(homeSummary());
    if (path === '/api/v1/home/insights') return json(insights);
    if (path === '/api/v1/home/pins' && method === 'GET') return json(state.pins);
    if (path === '/api/v1/home/pins' && method === 'POST') { const pin = { id: `pin_${state.pins.length + 1}`, position: state.pins.length, icon: null, created_at: iso(), updated_at: iso(), ...body() }; state.pins.push(pin); return json(pin, 201); }
    if (path.startsWith('/api/v1/home/pins/') && method === 'DELETE') { state.pins = state.pins.filter((p) => !path.endsWith(`/${p.id as string}`)); await route.fulfill({ status: 204 }); return true; }
    if (path.startsWith('/api/v1/home/insights/')) return json(insights.items[0]);
    if (path === '/api/v1/calendar/events') return json({ items: [{ id: 'evt_1', title: 'Fleet review with the team', starts_at: todayAt(23, 0), ends_at: todayAt(23, 30), status: 'scheduled', kind: 'meeting', location: 'Room 2', conference_url: 'https://meet.example.com/fleet' }], start: todayAt(0), end: todayAt(23, 59), truncated: false, can_write: true });
    if (path === '/api/v1/whatsapp/conversations') return json([{ id: 'wa_1', unread_count: 3, contact_name: 'Ahmed', last_message_at: iso(-5) }]);
    if (path === '/api/v1/commands/suggestions') return json([
      { kind: 'quick', text: 'Summarize my unread email and today\'s tasks', title: 'Morning briefing', quick_command_id: 'quick_1', use_count: 4, last_used_at: iso(-1440) },
      { kind: 'starter', text: 'Check my emails and summarize the urgent ones', title: 'Check my emails', quick_command_id: null, use_count: 0, last_used_at: null },
    ]);
    if (path === '/api/v1/commands/preview') return json(previewPlan(String(body().text)));
    if (path === '/api/v1/commands' && method === 'GET') return json(state.commands.map(({ steps: _s, pending: _p, timeline: _t, files: _f, attachments: _a, ...summary }) => summary));
    if (path === '/api/v1/commands' && method === 'POST') {
      const request = body();
      state.created.push(request);
      const steps = (request.steps as { instruction: string }[]).map((s, i) => step(i + 1, s.instruction, i === 0 ? 'email' : 'calendar', 'created'));
      const command = { ...doneCommand(), id: 'cmd_new', text: request.text, source: request.source, status: 'ready', progress: 0, step_count: steps.length, completed_steps: 0, steps, created_at: iso(), updated_at: iso(), finished_at: null, duration_seconds: null };
      state.commands.unshift(command);
      return json(command, 201);
    }
    const run = /^\/api\/v1\/tasks\/(task_cmd_\d+)\/run$/.exec(path);
    if (run && method === 'POST') {
      state.runs.push(run[1]);
      const command = state.commands.find((c) => c.id === 'cmd_new');
      if (command) {
        const steps = (command.steps as Record<string, unknown>[]).map((s) => s.task_id === run[1] ? { ...s, status: 'completed', progress: 1, run_id: `run_${run[1]}`, result: s.position === 1 ? 'Three urgent emails today.' : 'Meeting drafted for tomorrow at 10:00.' } : s);
        const done = steps.filter((s) => s.status === 'completed').length;
        Object.assign(command, { steps, completed_steps: done, progress: done / steps.length, status: done === steps.length ? 'completed' : 'running' });
      }
      const task = { id: run[1], title: 'Step', prompt: 'Step', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: iso(), updated_at: iso() };
      return json({ task, run: { id: `run_${run[1]}`, task_id: run[1], status: 'completed', result: 'Done.', steps: [] }, approval: null });
    }
    const next = /^\/api\/v1\/commands\/([^/]+)\/next-steps$/.exec(path);
    if (next) return json([{ id: 'next_1', kind: 'command', label: 'Draft replies to the urgent emails', text: 'Draft replies to the urgent emails' }, { id: 'next_save', kind: 'save_quick_command', label: 'Save as a quick command', text: null }]);
    const one = /^\/api\/v1\/commands\/([^/]+)$/.exec(path);
    if (one && method === 'GET') { const command = state.commands.find((c) => c.id === one[1]); return command ? json(command) : json({ detail: 'Command not found' }, 404); }
    if (one && method === 'DELETE') { state.commands = state.commands.filter((c) => c.id !== one[1]); await route.fulfill({ status: 204 }); return true; }
    if (path === '/api/v1/quick-commands' && method === 'GET') return json(state.quick);
    if (path === '/api/v1/quick-commands' && method === 'POST') { const q = { id: `quick_${state.quick.length + 1}`, position: 9, use_count: 0, last_used_at: null, created_at: iso(), updated_at: iso(), ...body() }; state.quick.push(q); return json(q, 201); }
    return false;
  }
  return { state, handle };
}
