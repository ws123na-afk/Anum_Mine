import type { Page, Route } from '@playwright/test';

// An in-memory live meeting room for e2e/meeting-room.spec.ts. It behaves like the API: every message is
// stored first and then announced by a numbered event, the stream replays after Last-Event-ID, and a turn
// shows `turn_started` before its `message`. The conversation text is a fixture for the UI, not a simulation of agents.
export interface RoomEventRow { seq: number; kind: string; actor_id: string | null; payload: Record<string, unknown>; created_at: string }

export function createRoomMock(options: {
  status?: string; owner?: boolean;
  /** May read the text (a workspace owner). Defaults to `owner`; a Member or Viewer is `false` and gets no text, only counts. */
  reader?: boolean;
  /** The workspace's monthly agent meeting budget: used and limit in USD (default 12.34 of 50). */
  budget?: { used: number; limit: number; isDefault?: boolean; canManage?: boolean };
  messages?: { speaker: string; role: string; body: string }[];
  /** How `POST /summary` answers: a draft (default), the server's refusal at the monthly cap, or a model that did not answer. */
  summary?: 'ok' | 'error';
} = {}) {
  const owner = options.owner ?? true;
  const reader = options.reader ?? owner;
  const me = 'user_local';
  const state = {
    meeting: {
      id: 'mtg_1', title: 'Launch planning', status: options.status ?? 'active', revision: 3, created_by: owner ? me : 'user_boss',
      max_turns: 8, max_duration_seconds: 900, max_tokens: 6000, max_cost_usd: '1.000000', max_participants: 6,
      total_input_tokens: 0, total_output_tokens: 0, total_cost_usd: '0.000000', started_at: new Date().toISOString(), ended_at: null, ended_reason: null,
      hand_raised_by: null as string | null, transcript_deleted_at: null as string | null,
    },
    participants: [{ id: 'p1', actor_id: 'agent_maya', kind: 'agent' }, { id: 'p2', actor_id: 'agent_omar', kind: 'agent' }],
    attendance: [] as { actor_id: string; joined_at: string; left_at: string | null }[],
    agenda: [{ id: 'ag1', position: 1, title: 'Fix the launch date', state: 'current' }],
    outputs: [] as Record<string, unknown>[],
    links: [] as Record<string, unknown>[],
    files: [] as Record<string, unknown>[],
    turns: [] as { sequence: number; speaker_id: string; role: string; body: string; request_key: string; created_at: string; redacted: boolean }[],
    events: [] as RoomEventRow[],
    outage: false,
    transcriptDeleted: false,
    calls: { summaries: 0, links: 0, jsonExports: 0, patches: [] as Record<string, unknown>[], runs: 0, tasks: 0, messages: [] as string[], exports: 0, streamHits: 0, forbidden: [] as string[], budgetGets: 0, budgetPuts: [] as (number | null)[] },
    budget: { used: options.budget?.used ?? 12.34, limit: options.budget?.limit ?? 50, isDefault: options.budget?.isDefault ?? true, canManage: options.budget?.canManage ?? owner },
    nextTurn: [] as { speaker: string; body: string }[],
  };
  const push = (kind: string, payload: Record<string, unknown> = {}, actor: string | null = null) => {
    state.events.push({ seq: state.events.length + 1, kind, actor_id: actor, payload, created_at: new Date().toISOString() });
  };
  const addTurn = (speaker: string, role: string, body: string, key: string) => {
    const turn = { sequence: state.turns.length + 1, speaker_id: speaker, role, body, request_key: key, created_at: new Date().toISOString(), redacted: false };
    state.turns.push(turn);
    state.budget.used += 0.002; state.meeting.total_input_tokens += 40; state.meeting.total_output_tokens += 60; state.meeting.total_cost_usd = (Number(state.meeting.total_cost_usd) + 0.002).toFixed(6);
    push('message', { sequence: turn.sequence, speaker_id: speaker, role, body, request_key: key, created_at: turn.created_at, redacted: false, provenance: role === 'human' ? 'human' : 'fake/fake-1' }, speaker);
    return turn;
  };
  for (const m of options.messages ?? []) addTurn(m.speaker, m.role, m.body, 'seed-' + state.turns.length);
  const budgetView = () => {
    const { used, limit, isDefault, canManage } = state.budget;
    return {
      limit_usd: limit, default_limit_usd: 50, is_default: isDefault, used_usd: used, remaining_usd: Math.max(0, limit - used), percent: limit > 0 ? Math.min(100, Math.floor((used / limit) * 1000) / 10) : 100,
      turns: state.turns.length, exceeded: used >= limit, period_start: '2026-10-01', resets_on: '2026-11-01', updated_by: isDefault ? null : 'user_local', updated_at: isDefault ? null : new Date().toISOString(),
      message: used >= limit ? 'Monthly agent meeting budget reached' : null, can_manage: canManage,
    };
  };
  const bundle = () => ({
    meeting: state.meeting, participants: state.participants, attendance: state.attendance, agenda: state.agenda,
    // A Member or Viewer gets who, when and status, never the text: decisions and files arrive as counts (S-03).
    outputs: reader ? state.outputs : [], links: reader ? state.links : [], files: reader ? state.files : [],
    ...(reader ? {} : { transcript_hidden: true, outputs_count: state.outputs.length, files_count: state.files.length, links_count: state.links.length }),
    speaking: [], last_event_seq: state.events.length, transcript_deleted: state.transcriptDeleted, viewer: { user_id: me, is_owner: owner, can_read_transcript: reader },
  });

  async function handle(route: Route): Promise<boolean> {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname; const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/agent-meetings' && method === 'GET') { await json({ items: [{ id: 'mtg_1', title: state.meeting.title, status: state.meeting.status, revision: state.meeting.revision, created_by: state.meeting.created_by }], next_cursor: null }); return true; }
    if (path === '/api/v1/agent-meetings/budget') {
      if (method === 'PUT') {
        const limit = (request.postDataJSON() as { monthly_cost_limit_usd: number | null }).monthly_cost_limit_usd;
        state.calls.budgetPuts.push(limit);
        state.budget.limit = limit ?? 50; state.budget.isDefault = limit === null;
      } else state.calls.budgetGets += 1;
      await json(budgetView()); return true;
    }
    if (path === '/api/v1/tasks' && method === 'GET') {
      const row = (id: string, title: string) => ({ id, title, prompt: 'x', status: 'created', tenant_id: 't', workspace_id: 'w', created_at: '2026-10-25T09:00:00Z', updated_at: '2026-10-25T09:00:00Z' });
      await json([row('task_plan', 'Plan the launch'), row('task_notes', 'Release notes')]); return true;
    }
    if (path === '/api/v1/approvals/page') {
      await json({ items: [{ id: 'apr_1', task_id: 'task_plan', action: 'send_email', risk_level: 'high', status: 'pending', reason: 'r', created_at: '2026-10-25T09:00:00Z' }], next_cursor: null }); return true;
    }
    const latest = /^\/api\/v1\/tasks\/(\w+)\/latest-run$/.exec(path);
    if (latest) {
      if (latest[1] === 'task_plan') await json({ id: 'run_plan', task_id: 'task_plan', status: 'completed', created_at: '2026-10-25T09:00:00Z', updated_at: '2026-10-25T09:00:00Z', checkpoint: {}, steps: [] });
      else await json({ error: { code: 'not_found', message: 'No run', correlation_id: 'c11', details: [] } }, 404);
      return true;
    }
    const agent = /^\/api\/v1\/agents\/(agent_\w+)$/.exec(path);
    if (agent) { await json({ id: agent[1], name: agent[1] === 'agent_maya' ? 'Maya' : 'Omar' }); return true; }
    const m = /^\/api\/v1\/agent-meetings\/mtg_1(\/.*)?$/.exec(path);
    if (!m) return false;
    const rest = m[1] ?? '';
    if (rest === '' && method === 'GET') { await json(state.meeting); return true; }
    if (rest === '/room') { await json(bundle()); return true; }
    const textPath = rest === '/turns' || rest === '/events' || rest === '/events/stream' || rest.startsWith('/export');
    if (textPath && !reader) {
      state.calls.forbidden.push(rest);
      await json({ error: { code: 'forbidden', message: 'Only a workspace owner can read meeting transcripts. You can still see that the meeting took place, who attended and its status.', correlation_id: 'c1', details: [] } }, 403);
      return true;
    }
    if (rest === '/turns') { const after = Number(url.searchParams.get('after') ?? 0); await json({ items: state.turns.filter((t) => t.sequence > after), next_after: null }); return true; }
    if (rest === '/events') { const after = Number(url.searchParams.get('after') ?? 0); await json({ items: state.events.filter((e) => e.seq > after), next_after: null }); return true; }
    if (rest === '/events/stream') {
      state.calls.streamHits += 1;
      if (state.outage) { await route.abort(); return true; }
      const after = Number(request.headers()['last-event-id'] ?? url.searchParams.get('after') ?? 0);
      const frames = state.events.filter((e) => e.seq > after).map((e) => `id: ${e.seq}\nevent: ${e.kind}\ndata: ${JSON.stringify(e)}\n\n`).join('');
      const over = ['finished', 'cancelled', 'failed'].includes(state.meeting.status);
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' }, body: 'retry: 3000\n\n' + frames + (over ? 'event: end\ndata: {}\n\n' : 'event: reconnect\ndata: {}\n\n') });
      return true;
    }
    const body = () => request.postDataJSON() as Record<string, unknown>;
    if (rest === '/presence') {
      const joining = body().action === 'join';
      if (joining) { state.attendance.push({ actor_id: me, joined_at: new Date().toISOString(), left_at: null }); push('joined', { actor_id: me }, me); }
      else { state.attendance.forEach((a) => { a.left_at = new Date().toISOString(); }); push('left', { actor_id: me }, me); }
      await json({ ok: true }); return true;
    }
    if (rest === '/hand') { state.meeting.hand_raised_by = null; push('hand_lowered', {}, me); await json({ hand_raised_by: null }); return true; }
    if (rest === '/run') {
      state.calls.runs += 1;
      if (state.budget.used >= state.budget.limit) {
        state.meeting.status = 'paused'; state.meeting.revision += 1;
        push('state', { status: 'paused', reason: 'budget', revision: state.meeting.revision }, me);
        push('budget_reached', { limit_usd: state.budget.limit, used_usd: state.budget.used, period_start: '2026-10-01', resets_on: '2026-11-01' }, me);
        await json({ error: { code: 'agent_meeting_budget_exceeded', message: `Monthly agent meeting budget reached: this workspace has used USD ${state.budget.used.toFixed(2)} of its USD ${state.budget.limit.toFixed(2)} for agent meetings this month. It resets on 2026-11-01 (UTC); a workspace owner can raise the limit under Budgets.`, correlation_id: 'c2', details: [] } }, 402);
        return true;
      }
      const next = state.nextTurn.shift() ?? { speaker: 'agent_maya', body: 'I suggest we **launch on Tuesday** and announce it Monday.' };
      const key = 'run-' + state.calls.runs;
      push('turn_started', { sequence: state.turns.length + 1, agent_id: next.speaker, request_key: key }, next.speaker);
      await new Promise((resolve) => setTimeout(resolve, 1800));
      addTurn(next.speaker, 'agent', next.body, key);
      if (state.budget.used >= state.budget.limit) {
        // The turn that used the last of the month pauses the meeting, as the API does.
        state.meeting.status = 'paused'; state.meeting.revision += 1;
        push('state', { status: 'paused', reason: 'budget', revision: state.meeting.revision }, me);
        push('budget_reached', { limit_usd: state.budget.limit, used_usd: state.budget.used, period_start: '2026-10-01', resets_on: '2026-11-01' }, me);
      }
      await json({ items: [], completed: 1 }); return true;
    }
    if (rest === '/messages') { const text = String(body().body); state.calls.messages.push(text); const turn = addTurn(me, 'human', text, String(body().request_key)); if (state.meeting.hand_raised_by) { state.meeting.hand_raised_by = null; push('hand_lowered', {}, me); } await json(turn, 201); return true; }
    if (rest === '/state') {
      const target = String(body().status);
      if (target === 'active' && state.budget.used >= state.budget.limit) {
        await json({ error: { code: 'agent_meeting_budget_exceeded', message: 'Monthly agent meeting budget reached: this workspace has used USD ' + state.budget.used.toFixed(2) + ' of its USD ' + state.budget.limit.toFixed(2) + ' for agent meetings this month. It resets on 2026-11-01 (UTC); a workspace owner can raise the limit under Budgets.', correlation_id: 'c3', details: [] } }, 402);
        return true;
      }
      state.meeting.status = target; state.meeting.revision += 1;
      if (['finished', 'cancelled', 'failed'].includes(target)) { state.meeting.ended_reason = 'owner'; state.meeting.ended_at = new Date().toISOString(); }
      push('state', { status: target, reason: 'owner', revision: state.meeting.revision }, me);
      await json(state.meeting); return true;
    }
    if (rest === '/outputs' && method === 'POST') {
      const b = body();
      const output = { id: 'out_' + (state.outputs.length + 1), session_id: 'mtg_1', kind: b.kind, body: b.body, state: 'draft', owner_label: b.owner_label ?? null, source_sequences: b.source_sequences, task_id: null, created_by: me, decided_by: null };
      state.outputs.push(output); push('output', { op: 'create', output }, me); await json(output, 201); return true;
    }
    if (rest === '/summary' && method === 'POST') {
      state.calls.summaries += 1;
      if (state.meeting.status === 'active') { await json({ error: { code: 'conflict', message: 'End the meeting before drafting its summary', correlation_id: 'c5', details: [] } }, 409); return true; }
      if (state.budget.used >= state.budget.limit) {
        await json({ error: { code: 'agent_meeting_budget_exceeded', message: 'Monthly agent meeting budget reached: this workspace has used USD ' + state.budget.used.toFixed(2) + ' of its USD ' + state.budget.limit.toFixed(2) + ' for agent meetings this month. It resets on 2026-11-01 (UTC); a workspace owner can raise the limit under Budgets.', correlation_id: 'c6', details: [] } }, 402);
        return true;
      }
      await new Promise((resolve) => setTimeout(resolve, 900));
      if (options.summary === 'error') { await json({ error: { code: 'service_unavailable', message: 'The model did not answer. Nothing was stored and nothing was charged; try again.', correlation_id: 'c7', details: [] } }, 503); return true; }
      const make = (kind: string, text: string, sources: number[], label: string | null = null) => {
        const output = { id: 'out_' + (state.outputs.length + 1), session_id: 'mtg_1', kind, body: text, state: 'draft', owner_label: label, source_sequences: sources, task_id: null, created_by: me, decided_by: null, origin: 'model', drafted_with: 'fake/fake-1', payload_hash: String(state.outputs.length + 1).padStart(64, 'a') };
        state.outputs.push(output); push('output', { op: 'create', output }, me); return output;
      };
      state.budget.used += 0.02;
      const summary = make('summary', '- The group agreed to launch on Tuesday. [#1]\n- Risks were raised and left open. [#2]', [1, 2]);
      const decision = make('decision', 'Launch on Tuesday.', [1]);
      const action = make('action_item', 'Announce the launch on Monday.', [2], 'Maya');
      await json({ summary, decisions: [decision], action_items: [action], dropped: 1, covered_up_to: state.turns.length, messages: state.turns.length, cost_usd: 0.02 }, 201); return true;
    }
    const link = /^\/outputs\/(out_\d+)\/links$/.exec(rest);
    if (link && method === 'POST') {
      const b = body();
      if (b.input_mode !== 'click') { await json({ error: { code: 'forbidden', message: 'Confirm this on screen with a click. A spoken or typed answer cannot confirm it.', correlation_id: 'c8', details: [] } }, 403); return true; }
      const labels: Record<string, string> = { task_plan: 'Plan the launch', task_notes: 'Release notes', apr_1: 'send_email', run_plan: 'run_plan' };
      const existing = state.links.find((l) => l.output_id === link[1] && l.target_id === b.target_id);
      if (existing) { await json(existing, 200); return true; }
      state.calls.links += 1;
      const row = { id: 'lnk_' + (state.links.length + 1), session_id: 'mtg_1', output_id: link[1], target_kind: b.target_kind, target_id: b.target_id, target_label: labels[String(b.target_id)] ?? String(b.target_id), target_status: 'created', created_by: me };
      state.links.push(row); push('output', { op: 'link', link: row }, me); await json(row, 201); return true;
    }
    const unlink = /^\/links\/(lnk_\d+)$/.exec(rest);
    if (unlink && method === 'DELETE') {
      const row = state.links.find((l) => l.id === unlink[1]);
      state.links = state.links.filter((l) => l.id !== unlink[1]);
      if (row) push('output', { op: 'unlink', link: row }, me);
      await route.fulfill({ status: 204 }); return true;
    }
    const out = /^\/outputs\/(out_\d+)(\/task)?$/.exec(rest);
    if (out) {
      const item = state.outputs.find((o) => o.id === out[1])!;
      if (!out[2]) {
        const b = body(); state.calls.patches.push(b);
        const guarded = item.kind === 'summary' || item.origin === 'model';
        if (b.state === 'confirmed' && guarded && b.input_mode !== 'click') { await json({ error: { code: 'forbidden', message: 'Confirm this on screen with a click. A spoken or typed answer cannot confirm it.', correlation_id: 'c9', details: [] } }, 403); return true; }
        if (b.state === 'confirmed' && guarded && b.payload_hash !== item.payload_hash) { await json({ error: { code: 'conflict', message: 'This draft changed since you read it. Reload and review it again before confirming.', correlation_id: 'c10', details: [] } }, 409); return true; }
      }
      if (out[2]) { state.calls.tasks += 1; item.task_id = 'task_77'; push('output', { op: 'task', output: item }, me); await json(item, 201); return true; }
      item.state = body().state; item.decided_by = me; push('output', { op: 'update', output: item }, me); await json(item); return true;
    }
    if (rest.startsWith('/export')) {
      state.calls.exports += 1;
      if (url.searchParams.get('format') === 'json') {
        state.calls.jsonExports += 1;
        const live = state.outputs.filter((o) => o.kind === 'summary' && o.state !== 'dismissed');
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ meeting: state.meeting, turns: state.turns, outputs: state.outputs, links: state.links, summary: live[0] ?? null,
          decisions: state.outputs.filter((o) => o.kind === 'decision'), confirmed_outputs: state.outputs.filter((o) => o.state === 'confirmed') }) });
        return true;
      }
      await route.fulfill({ status: 200, contentType: 'text/markdown', body: '# Launch planning\n' }); return true;
    }
    if (rest === '/transcript' && method === 'DELETE') {
      const turns = state.turns.length; state.turns = []; state.events = []; state.transcriptDeleted = true; state.meeting.transcript_deleted_at = new Date().toISOString();
      state.outputs.forEach((o) => { o.source_sequences = []; }); await json({ turns, events: 0 }); return true;
    }
    return false;
  }
  return { state, push, addTurn, handle, bundle };
}

export async function mockRoom(page: Page, mock: ReturnType<typeof createRoomMock>) {
  await page.route('http://localhost:8000/**', async (route) => {
    if (await mock.handle(route)) return;
    return route.abort();
  });
}
