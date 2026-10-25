/** ANUM MINE live flow: snapshot read, validation and the pure rules that turn two consecutive
 * snapshots into movements (contract: packages/contracts/src/officeFlow.ts, spec:
 * docs/ANUM_MINE_HQ_LIVE_FLOW.md). A marker moves only because a stored record moved the work:
 * `diffFlow` compares what the server said before with what it says now, never anything else. */
import type {
  OfficeFlowAction, OfficeFlowItem, OfficeFlowKind, OfficeFlowSnapshot, OfficeFlowStage, OfficeFlowStuck,
  OfficeFlowTrail, OfficeTeam,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export type FlowRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class FlowApiError extends Error { constructor(message: string) { super(message); this.name = 'FlowApiError'; } }

export const FLOW_PATH = '/api/v1/office/flow';
export const TEAM_PATH = '/api/v1/office/team';
const STAGES: readonly OfficeFlowStage[] = ['arrived', 'in_office', 'waiting_human', 'blocked', 'done'];
const KINDS: readonly OfficeFlowKind[] = ['task', 'lead', 'order'];
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;

/** Events that can change what the flow shows; they only tell the browser to read again. */
export function isFlowEvent(type: string): boolean {
  return /^(task|agent_run|approval|agent_meeting|office|work_item)\./.test(type);
}

function validItem(item: OfficeFlowItem): boolean {
  return !!item && typeof item.key === 'string' && KINDS.includes(item.kind) && STAGES.includes(item.stage)
    && typeof item.title === 'string' && typeof item.since === 'string'
    && (item.location === null || typeof item.location?.room_id === 'string')
    && (item.stuck === null || typeof item.stuck?.code === 'string');
}

export function parseFlowSnapshot(raw: OfficeFlowSnapshot): OfficeFlowSnapshot {
  if (!raw || raw.version !== 1 || !Array.isArray(raw.items) || !raw.sources || !raw.totals) throw new FlowApiError('The flow response is not version 1');
  const seen = new Set<string>();
  for (const item of raw.items) {
    if (!validItem(item)) throw new FlowApiError('The flow returned an invalid item');
    if (seen.has(item.key)) throw new FlowApiError('The flow returned a repeated item');
    seen.add(item.key);
  }
  return raw;
}

export function createFlowClient(request: FlowRequester, scope: () => { tenantId: string; workspaceId: string }) {
  const guard = <T extends { workspace_id: string }>(started: { tenantId: string; workspaceId: string }, body: T): T => {
    const now = scope();
    if (body.workspace_id !== started.workspaceId || now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId) {
      throw new FlowApiError('The answer belongs to another workspace');
    }
    return body;
  };
  return {
    async load(): Promise<OfficeFlowSnapshot> {
      const started = scope();
      return guard(started, parseFlowSnapshot(await request<OfficeFlowSnapshot>(FLOW_PATH, { method: 'GET' })));
    },
    async trail(kind: OfficeFlowKind, id: string): Promise<OfficeFlowTrail> {
      if (!KINDS.includes(kind) || !ID.test(id)) throw new FlowApiError('Invalid item');
      return request<OfficeFlowTrail>(`${FLOW_PATH}/items/${kind}/${encodeURIComponent(id)}/trail`, { method: 'GET' });
    },
    async team(filter: { buildingId?: string | null; floorId?: string | null; roomId?: string | null }): Promise<OfficeTeam> {
      const started = scope();
      const params = new URLSearchParams();
      for (const [key, value] of [['building_id', filter.buildingId], ['floor_id', filter.floorId], ['room_id', filter.roomId]] as const) {
        if (value) { if (!ID.test(value)) throw new FlowApiError('Invalid office'); params.set(key, value); }
      }
      const query = params.toString();
      return guard(started, await request<OfficeTeam>(`${TEAM_PATH}${query ? `?${query}` : ''}`, { method: 'GET' }));
    },
  };
}

// ---- Movements -------------------------------------------------------------------------

export type FlowChangeKind = 'arrived' | 'moved' | 'stuck' | 'done';
export interface FlowChange {
  kind: FlowChangeKind; key: string; title: string;
  /** Room the stored record placed the item in before, or null when it had no office. */
  fromRoomId: string | null;
  toRoomId: string | null;
  stuck: OfficeFlowStuck | null;
}

/** What changed between two answers. Items that exist in both with the same room, stage and
 * stuck code produce nothing, so a marker never moves without a record that moved it. */
export function diffFlow(previous: OfficeFlowSnapshot | null, next: OfficeFlowSnapshot): FlowChange[] {
  if (!previous) return [];
  const before = new Map(previous.items.map((i) => [i.key, i]));
  const changes: FlowChange[] = [];
  for (const item of next.items) {
    const old = before.get(item.key);
    const to = item.location?.room_id ?? null;
    if (!old) {
      if (item.stage !== 'done') changes.push({ kind: 'arrived', key: item.key, title: item.title, fromRoomId: null, toRoomId: to, stuck: item.stuck });
      continue;
    }
    const from = old.location?.room_id ?? null;
    if (from !== to) { changes.push({ kind: 'moved', key: item.key, title: item.title, fromRoomId: from, toRoomId: to, stuck: item.stuck }); continue; }
    if (item.stage === 'done' && old.stage !== 'done') { changes.push({ kind: 'done', key: item.key, title: item.title, fromRoomId: from, toRoomId: to, stuck: null }); continue; }
    if (item.stuck && item.stuck.code !== old.stuck?.code) changes.push({ kind: 'stuck', key: item.key, title: item.title, fromRoomId: from, toRoomId: to, stuck: item.stuck });
  }
  return changes;
}

// ---- Words -----------------------------------------------------------------------------

export function ageText(seconds: number): string {
  if (seconds < 60) return t('hqFlow.age.s');
  const minutes = Math.floor(seconds / 60);
  if (minutes < 120) return t('hqFlow.age.m', { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return t('hqFlow.age.h', { count: hours });
  return t('hqFlow.age.d', { count: Math.floor(hours / 24) });
}

/** The plain-language reason an item is stuck, built only from the rule's own facts. */
export function reasonText(item: Pick<OfficeFlowItem, 'stuck' | 'location'>): string {
  const s = item.stuck;
  if (!s) return '';
  const age = ageText(s.age_seconds);
  const limit = s.threshold_seconds === null ? '' : ageText(s.threshold_seconds);
  switch (s.code) {
    case 'run_failed': {
      const error = typeof s.facts.error === 'string' ? s.facts.error : '';
      return error ? t('hqFlow.reason.run_failed', { error }) : t('hqFlow.reason.run_failed_plain');
    }
    case 'agent_paused': return t('hqFlow.reason.agent_paused', { agent: String(s.facts.agent_name ?? item.location?.agent_name ?? '') });
    case 'order_ship_overdue': return t('hqFlow.reason.order_ship_overdue', { age, status: String(s.facts.order_status ?? '') });
    case 'approval_expired': case 'approval_missing': return t(`hqFlow.reason.${s.code}` as MessageKey);
    default: return t(`hqFlow.reason.${s.code}` as MessageKey, { age, limit });
  }
}

export function actionText(action: OfficeFlowAction): string { return t(`hqFlow.action.${action}` as MessageKey); }

/** Where each next step lives in the app shell (hash routes of the existing views). */
export function actionHash(action: OfficeFlowAction): string {
  switch (action) {
    case 'decide_approval': return '#approvals';
    case 'retry_task': case 'open_task': return '#tasks';
    case 'resume_agent': return '#agents';
    case 'assign_owner': case 'open_lead': return '#marketing';
    case 'open_order': return '#commerce';
  }
}

export function stuckItems(snapshot: OfficeFlowSnapshot | null): OfficeFlowItem[] {
  return snapshot ? snapshot.items.filter((i) => i.stuck) : [];
}

export function itemsInRoom(snapshot: OfficeFlowSnapshot | null, roomId: string): OfficeFlowItem[] {
  return snapshot ? snapshot.items.filter((i) => i.location?.room_id === roomId) : [];
}

export function changeAnnouncement(change: FlowChange, roomName: (id: string | null) => string): string {
  const room = roomName(change.toRoomId);
  switch (change.kind) {
    case 'arrived': return t('hqFlow.announce.arrived', { title: change.title, room });
    case 'moved': return t('hqFlow.announce.moved', { title: change.title, room });
    case 'done': return t('hqFlow.announce.done', { title: change.title });
    case 'stuck': return t('hqFlow.announce.stuck', { title: change.title, reason: reasonText({ stuck: change.stuck, location: null }) });
  }
}
