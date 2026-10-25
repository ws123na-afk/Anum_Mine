/** Assign work inside the headquarters (spec: docs/ANUM_MINE_ASSIGN_WORK.md). Pure helpers and the client for
 * `POST /api/v1/office/assignments`, which creates and runs a task by the Tasks page's own governed code. */
import type { AgentAutonomy, AgentCatalog, AgentProfile, AssignRequest, AssignResponse, AssignPriority, AssignSource } from '@anum/contracts';

export type Requester = <T>(path: string, init: RequestInit) => Promise<T>;
export const ASSIGN_EVENT = 'anum:hq-assign';
export const TASK_PANEL_EVENT = 'anum:hq-open-task';
export const INSTRUCTIONS_MAX = 6000;
export const EXPECTED_MAX = 1000;
export const CONTEXT_MAX = 500;
export const TITLE_MAX = 160;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;

/** What opened the dialog. Every field only pre-fills; the person can change all of it. */
export interface AssignPrefill {
  agentId?: string | null; roomId?: string | null; roomName?: string | null;
  title?: string; instructions?: string; source?: AssignSource;
}

let hostMounted = 0;
/** The headquarters mounts one host; callers outside it (the assistant) fall back to their own flow when none is. */
export const setAssignHostMounted = (on: boolean): void => { hostMounted = Math.max(0, hostMounted + (on ? 1 : -1)); };
export const assignHostAvailable = (): boolean => hostMounted > 0;

export function openAssign(prefill: AssignPrefill = {}, target: Pick<Window, 'dispatchEvent'> = window): boolean {
  if (!assignHostAvailable()) return false;
  target.dispatchEvent(new CustomEvent<AssignPrefill>(ASSIGN_EVENT, { detail: prefill }));
  return true;
}
export function openTaskPanel(taskId: string, target: Pick<Window, 'dispatchEvent'> = window): boolean {
  if (!assignHostAvailable() || !ID.test(taskId)) return false;
  target.dispatchEvent(new CustomEvent<string>(TASK_PANEL_EVENT, { detail: taskId }));
  return true;
}

export interface AssignDraft {
  agentId: string; title: string; instructions: string; expectedResult: string; contextNote: string;
  priority: AssignPriority; due: string; // yyyy-mm-ddThh:mm from a datetime-local input, local time
  run: boolean;
}
export const emptyDraft = (prefill: AssignPrefill = {}): AssignDraft => ({
  agentId: prefill.agentId ?? '', title: (prefill.title ?? '').slice(0, TITLE_MAX), instructions: prefill.instructions ?? '',
  expectedResult: '', contextNote: prefill.roomName ? prefill.roomName : '', priority: 'medium', due: '', run: true,
});

export type AssignErrors = Partial<Record<'agent' | 'title' | 'instructions' | 'expected' | 'context' | 'due', string>>;
/** Error keys are message ids (`hqAssign.err.*`). Empty means the draft can be sent. */
export function validateDraft(d: AssignDraft, now = new Date()): AssignErrors {
  const e: AssignErrors = {};
  if (!d.agentId) e.agent = 'hqAssign.err.agent';
  if (!d.title.trim()) e.title = 'hqAssign.err.title';
  else if (d.title.trim().length > TITLE_MAX) e.title = 'hqAssign.err.titleLong';
  if (!d.instructions.trim()) e.instructions = 'hqAssign.err.instructions';
  else if (d.instructions.trim().length > INSTRUCTIONS_MAX) e.instructions = 'hqAssign.err.instructionsLong';
  if (d.expectedResult.trim().length > EXPECTED_MAX) e.expected = 'hqAssign.err.expectedLong';
  if (d.contextNote.trim().length > CONTEXT_MAX) e.context = 'hqAssign.err.contextLong';
  if (d.due) {
    const when = new Date(d.due);
    if (Number.isNaN(when.getTime())) e.due = 'hqAssign.err.dueInvalid';
    else if (when.getTime() < now.getTime() - 60_000) e.due = 'hqAssign.err.duePast';
  }
  return e;
}

export function toRequest(d: AssignDraft, source: AssignSource): AssignRequest {
  const due = d.due ? new Date(d.due) : null;
  return {
    agent_id: d.agentId, title: d.title.trim(), instructions: d.instructions.trim(),
    expected_result: d.expectedResult.trim() || null, context_note: d.contextNote.trim() || null,
    priority: d.priority, due_at: due && !Number.isNaN(due.getTime()) ? due.toISOString() : null, run: d.run, source,
  };
}

/** One key per attempt. A retry of the same attempt (double click, lost answer) sends the same key. */
export function newAttemptKey(): string {
  const c = globalThis.crypto;
  const id = c?.randomUUID ? c.randomUUID() : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  return `assign-${id}`.slice(0, 200);
}

export type Availability = { kind: 'ready' } | { kind: 'paused'; canTurnOn: boolean } | { kind: 'denied' } | { kind: 'unavailable' };
/** Whether this agent can take work now, from the agent record the API returned (never guessed). */
export function availability(agent: AgentProfile | null | undefined, mayRunTasks: boolean): Availability {
  if (!agent) return { kind: 'unavailable' };
  if (agent.status === 'paused') return { kind: 'paused', canTurnOn: agent.can_edit };
  if (!agent.can_run || !mayRunTasks) return { kind: 'denied' };
  return { kind: 'ready' };
}

export interface RiskLine { level: 'auto' | 'ask' | 'off'; tools: string[] }
export interface RiskView { read: RiskLine; write: RiskLine; external: RiskLine; highRisk: string[]; unavailable: string[] }
/** What the agent can and cannot do with its tools, from its allowed tools, the catalogue and its autonomy.
 * The effective level is never looser than the workspace floor. */
export function riskView(agent: AgentProfile, catalog: AgentCatalog | null): RiskView {
  const floor: AgentAutonomy | null = catalog?.autonomy_floor ?? null;
  const rank = { off: 0, ask: 1, auto: 2 } as const;
  const effective = (cat: 'read' | 'write' | 'external') => {
    const own = agent.autonomy[cat];
    const cap = floor?.[cat];
    return cap && rank[cap] < rank[own] ? cap : own;
  };
  const byName = new Map((catalog?.tools ?? []).map((tool) => [tool.name, tool]));
  const lines: RiskView = {
    read: { level: effective('read'), tools: [] }, write: { level: effective('write'), tools: [] },
    external: { level: effective('external'), tools: [] }, highRisk: [], unavailable: [...agent.unavailable_tools],
  };
  for (const name of agent.allowed_tools) {
    const tool = byName.get(name);
    if (!tool || tool.always_on) continue;
    lines[tool.category].tools.push(name);
    if (tool.risk_level === 'high') lines.highRisk.push(name);
  }
  return lines;
}

export function createAssignClient(request: Requester) {
  return {
    /** Sends exactly one attempt. Same `key` + same body = the same task (the API answers `replayed`). */
    assign: (body: AssignRequest, key: string): Promise<AssignResponse> =>
      request<AssignResponse>('/api/v1/office/assignments', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) }),
  };
}

/** "ask <agent> to <work>" / "tell ..." / "delegate to ..." / "start a task to <work>". Only a request to OPEN the
 * assign dialog pre-filled: the dialog is the review, and only its Assign button creates anything. */
export function parseAssignSentence(text: string): { agentQuery: string | null; work: string } | null {
  const d = /^\s*(?:ask|tell|have|delegate to|assign to)\s+(.+?)\s+to\s+(.+)$/i.exec(text);
  if (d) return { agentQuery: d[1].trim(), work: d[2].trim() };
  const a = /^\s*assign\s+(.+?)\s+to\s+(.+)$/i.exec(text);
  if (a) return { agentQuery: a[2].trim(), work: a[1].trim() };
  const s = /^\s*(?:start|create|run)\s+(?:a\s+)?task\s*(?:to|:)?\s*(.+)$/i.exec(text);
  if (s) return { agentQuery: null, work: s[1].trim() };
  return null;
}
export const titleFromWork = (work: string): string => work.replace(/\s+/g, ' ').trim().slice(0, 80) || '';
