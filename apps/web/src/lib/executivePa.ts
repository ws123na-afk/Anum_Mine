/** Executive PA and executive desk clients (docs/ANUM_MINE_EXECUTIVE_PA.md).
 *
 * Pure and transport injected, so `pnpm --filter @anum/web test:unit` covers it.
 * Nothing here decides anything: the server owns permissions, grants, the visual
 * confirmation gate and the audit log. This module only shapes requests, reads
 * answers defensively and turns a sentence into a proposed tool call.
 */
export type Requester = <T>(path: string, init: RequestInit) => Promise<T>;
export type InputMode = 'typed' | 'voice' | 'click';

export type PaToolName = 'navigate' | 'open_panel' | 'summarise_workspace' | 'list_blocked_work' | 'list_pending_approvals' | 'start_task' | 'delegate' | 'schedule_meeting';
export interface PaTool { name: PaToolName; kind: 'read' | 'write'; label: string; risk: 'low' | 'medium' | 'high'; autonomy: 'automatic' | 'confirm_each'; governed_by: string; permissions: string[]; allowed: boolean }
export interface TourStop { building_id: string; building_name: string; floor_id: string; floor_name: string; level: number; room_id: string; room_name: string; room_kind: string; capacity: number; desks: number }
export interface Tour { status: 'ready' | 'empty' | 'not_connected'; stops: TourStop[] }
export interface Location { building_id?: string; floor_id?: string; room_id?: string; agent_id?: string }
export interface WorkSummary { tasks_total?: number; tasks_blocked?: number; approvals_pending?: number; cost_usd_month?: number | null; meetings_active?: number | null }

export type PaStatus = 'ok' | 'done' | 'approval_pending' | 'needs_confirmation' | 'needs_visual_confirmation' | 'stale_confirmation'
  | 'denied' | 'not_found' | 'not_connected' | 'not_granted' | 'failed';
export interface PaResult {
  tool: PaToolName; status: PaStatus; note?: string;
  navigate?: Location; open_panel?: { view: string };
  summary?: WorkSummary; workspace_id?: string; scope?: 'current' | 'granted';
  items?: { id: string; title?: string; status?: string; task_id?: string; action?: string; risk_level?: string }[];
  payload_hash?: string; preview?: Record<string, unknown>; risk?: string; governed_by?: string;
  task_id?: string; approval_id?: string; meeting_id?: string; missing?: string[]; truncated?: boolean;
}

const ROOT = '/api/v1/executive-pa';

export function createPaClient(request: Requester) {
  return {
    tools: async (): Promise<PaTool[]> => {
      const r = await request<{ tools?: PaTool[] }>(`${ROOT}/tools`, { method: 'GET' });
      return Array.isArray(r?.tools) ? r.tools : [];
    },
    tour: async (): Promise<Tour> => {
      const r = await request<Tour>(`${ROOT}/tour`, { method: 'GET' });
      return { status: r?.status ?? 'empty', stops: Array.isArray(r?.stops) ? r.stops : [] };
    },
    /** Never sends a confirmation unless `confirmHash` is given AND the mode is a click. */
    invoke: (tool: PaToolName, args: Record<string, unknown>, mode: InputMode, confirmHash?: string): Promise<PaResult> =>
      request<PaResult>(`${ROOT}/invoke`, {
        method: 'POST',
        body: JSON.stringify({ tool, arguments: args, input_mode: mode, ...(confirmHash && mode === 'click' ? { confirm_hash: confirmHash } : {}) }),
      }),
  };
}

// -- sentences to proposed tool calls ----------------------------------------------------

export interface ParseContext { stops: readonly TourStop[]; agents?: readonly { id: string; name: string }[] }
export interface ProposedCall { tool: PaToolName; args: Record<string, unknown> }

const norm = (v: string) => v.toLocaleLowerCase('en').normalize('NFKC').replace(/[^\p{L}\p{N}\s]+/gu, ' ').replace(/\s+/g, ' ').trim();
const PANEL_WORDS: [RegExp, string][] = [
  [/(approvals?|الموافقات|موافقات)/, 'approvals'], [/(tasks?|المهام|مهام)/, 'tasks'], [/(meetings?|الاجتماعات|اجتماعات)/, 'meetings'],
  [/(calendar|التقويم)/, 'calendar'], [/(audit|التدقيق|سجل)/, 'audit'], [/(command|الأوامر)/, 'command'], [/(executive desk|command centre|command center|المكتب التنفيذي)/, 'executive'],
];

/** Reads one sentence. It only ever returns a PROPOSAL; the server and the visual
 * confirmation decide. Words that would approve or reject are not understood on purpose. */
export function parseUtterance(text: string, ctx: ParseContext): ProposedCall | null {
  const q = ' ' + norm(text) + ' ';
  if (q.trim() === '') return null;
  if (/(blocked|stuck|waiting|معطل|عالق)/.test(q)) return { tool: 'list_blocked_work', args: {} };
  if (/(pending approvals|what needs my approval|بانتظار الموافقة)/.test(q)) return { tool: 'list_pending_approvals', args: {} };
  if (/(summar|overview|how is|status of|ملخص)/.test(q)) return { tool: 'summarise_workspace', args: {} };
  const delegate = text.match(/^\s*(?:ask|tell|delegate to|have)\s+(.+?)\s+to\s+(.+)$/i);
  if (delegate) {
    const wanted = norm(delegate[1]);
    const agent = ctx.agents?.find((a) => norm(a.name) === wanted || norm(a.name).startsWith(wanted));
    if (agent) return { tool: 'delegate', args: { agent_id: agent.id, title: titleOf(delegate[2]), prompt: delegate[2].trim() } };
    return null;
  }
  const start = text.match(/^\s*(?:start|create|run)\s+(?:a\s+)?task\s*(?:to|:)?\s*(.+)$/i);
  if (start) return { tool: 'start_task', args: { title: titleOf(start[1]), prompt: start[1].trim() } };
  const meet = text.match(/^\s*(?:schedule|set up|create|start)\s+(?:a\s+)?(?:live\s+)?meeting\s*(?:called|about|for|:)?\s*(.*)$/i);
  if (meet) return { tool: 'schedule_meeting', args: { title: meet[1].trim() || 'Meeting' } };
  const open = q.match(/ (?:open|show|go to|افتح) (.+) $/);
  if (open) {
    for (const [re, panel] of PANEL_WORDS) if (re.test(open[1])) return { tool: 'open_panel', args: { panel } };
  }
  // Rooms: longest name first, so "executive office" beats "office".
  const stop = [...ctx.stops].sort((a, b) => b.room_name.length - a.room_name.length)
    .find((s) => q.includes(' ' + norm(s.room_name) + ' ') || q.includes(' ' + norm(s.room_id) + ' '));
  if (stop) return { tool: 'navigate', args: { target_kind: 'room', target_id: stop.room_id } };
  const building = ctx.stops.find((s) => q.includes(' ' + norm(s.building_name) + ' '));
  if (building) return { tool: 'navigate', args: { target_kind: 'building', target_id: building.building_id } };
  const agent = ctx.agents?.find((a) => q.includes(' ' + norm(a.name) + ' '));
  if (agent) return { tool: 'navigate', args: { target_kind: 'agent', target_id: agent.id } };
  return null;
}
const titleOf = (work: string) => work.trim().replace(/\s+/g, ' ').slice(0, 80) || 'Task';

// -- the executive desk -------------------------------------------------------------------

export type WorkspaceKind = 'personal' | 'employment' | 'anum_os' | 'business';
export type GrantState = 'own' | 'granted' | 'not_granted' | 'not_connected';
export interface DeskWorkspace {
  workspace_id: string; tenant_id: string; name: string; kind: WorkspaceKind;
  grant_state: GrantState; grant_id?: string | null; summary: WorkSummary | null;
}
export interface Overview { workspaces: DeskWorkspace[] }
export interface Grant { id: string; workspace_id: string; scopes: string[]; status: 'active' | 'pending_approval' | 'revoked'; approval_id?: string | null }
export type GrantOutcome = { kind: 'applied'; grant: Grant } | { kind: 'pending_approval'; approvalId: string | null };
export const GRANT_SCOPES = ['tasks', 'approvals', 'costs', 'meetings'] as const;
export type GrantScope = (typeof GRANT_SCOPES)[number];

const KINDS: readonly WorkspaceKind[] = ['personal', 'employment', 'anum_os', 'business'];
export const KIND_ORDER = KINDS;

export function readOverview(raw: unknown): Overview {
  const list = (raw as { workspaces?: unknown } | null)?.workspaces;
  if (!Array.isArray(list)) return { workspaces: [] };
  const seen = new Set<string>();
  const out: DeskWorkspace[] = [];
  for (const row of list as Partial<DeskWorkspace>[]) {
    if (!row || typeof row.workspace_id !== 'string' || typeof row.tenant_id !== 'string' || seen.has(row.tenant_id + '/' + row.workspace_id)) continue;
    seen.add(row.tenant_id + '/' + row.workspace_id);
    const state: GrantState = row.grant_state === 'own' || row.grant_state === 'granted' || row.grant_state === 'not_connected' ? row.grant_state : 'not_granted';
    out.push({
      workspace_id: row.workspace_id, tenant_id: row.tenant_id, name: String(row.name ?? row.workspace_id),
      kind: KINDS.includes(row.kind as WorkspaceKind) ? (row.kind as WorkspaceKind) : 'business',
      grant_state: state, grant_id: row.grant_id ?? null,
      // A summary is shown only for a workspace that is the person's own or granted.
      summary: (state === 'own' || state === 'granted') && row.summary ? row.summary : null,
    });
  }
  return { workspaces: out };
}

export function groupByKind(rows: readonly DeskWorkspace[]): { kind: WorkspaceKind; rows: DeskWorkspace[] }[] {
  return KINDS.map((kind) => ({ kind, rows: rows.filter((r) => r.kind === kind) })).filter((g) => g.rows.length > 0);
}

/** Confidential content opens only in the workspace the shell is acting in. */
export function needsSwitch(row: Pick<DeskWorkspace, 'workspace_id' | 'tenant_id'>, scope: { tenantId: string; workspaceId: string }): boolean {
  return row.workspace_id !== scope.workspaceId || row.tenant_id !== scope.tenantId;
}

export type VoidRequester = (path: string, init: RequestInit) => Promise<void>;
export function createDeskClient(request: Requester, requestVoid?: VoidRequester) {
  const grantResult = (status: number | undefined, body: Partial<Grant> & { approval_id?: string | null }): GrantOutcome =>
    status === 202 || body.status === 'pending_approval'
      ? { kind: 'pending_approval', approvalId: body.approval_id ?? null }
      : { kind: 'applied', grant: body as Grant };
  return {
    overview: async (): Promise<Overview> => readOverview(await request<unknown>('/api/v1/executive/overview', { method: 'GET' })),
    grants: async (): Promise<Grant[]> => {
      const r = await request<{ items?: Grant[] } | Grant[]>('/api/v1/executive/grants', { method: 'GET' });
      return Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : [];
    },
    grant: async (workspaceId: string, scopes: GrantScope[]) =>
      grantResult(undefined, await request<Grant>('/api/v1/executive/grants', { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId, scopes }) })),
    widen: async (grantId: string, scopes: GrantScope[]) =>
      grantResult(undefined, await request<Grant>(`/api/v1/executive/grants/${encodeURIComponent(grantId)}`, { method: 'PATCH', body: JSON.stringify({ scopes }) })),
    revoke: async (grantId: string): Promise<void> => {
      const path = `/api/v1/executive/grants/${encodeURIComponent(grantId)}`;
      // A DELETE answers 204 with no body, so the shell's void requester is the right one.
      if (requestVoid) await requestVoid(path, { method: 'DELETE' }); else await request<unknown>(path, { method: 'DELETE' });
    },
  };
}

/** The scene's navigation target for a PA location. The headquarters scene reads this
 * from `navigateTo`; until it is installed the target is kept for the next scene mount. */
export const HQ_NAVIGATE_EVENT = 'anum:hq-navigate';
export const HQ_PENDING_KEY = 'anum.hq.pendingNavigation';
export function publishNavigation(location: Location, host: Pick<Window, 'dispatchEvent'> & { sessionStorage?: Storage } = window): void {
  try { host.sessionStorage?.setItem(HQ_PENDING_KEY, JSON.stringify(location)); } catch { /* storage may be blocked */ }
  host.dispatchEvent(new CustomEvent(HQ_NAVIGATE_EVENT, { detail: location }));
}
