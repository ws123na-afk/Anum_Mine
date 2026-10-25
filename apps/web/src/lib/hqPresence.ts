/**
 * Presence for the headquarters, built only from records the signed-in person may read:
 * workstation evidence (agent profiles plus open tasks) and the participants of live agent
 * meetings. A status exists only when a record backs it; `unreported` lists the statuses the
 * current sources cannot tell, so the screen shows "-" for them instead of a zero.
 */
import { STATUS_ORDER, type CampusTotals, type StatusKey } from './campusModel.ts';

export type HqAgentPresence = Readonly<{
  /** The agent id, or `desk:<id>` for a desk whose agent is private to someone else. */
  agentId: string;
  name: string;
  role: string | null;
  status: StatusKey;
  taskTitles: readonly string[];
  observedAt: string;
  /** True when the evidence is older than the server's freshness window: draw it greyed out. */
  stale?: boolean;
  /** Machine reason from the server (agent_not_visible, run_silent, approval_expired, ...). */
  reason?: string | null;
  deskId?: string | null;
  /** Where the status came from: the presence API or the browser's own reading of tasks and meetings. */
  source?: 'presence-api' | 'task-evidence';
}>;

/** Agents of the open workspace by agent id, and the private-agent desks by desk id. */
export type HqPresenceIndex = Readonly<{ byAgent: ReadonlyMap<string, HqAgentPresence>; byDesk: ReadonlyMap<string, HqAgentPresence> }>;

export function indexPresence(list: readonly HqAgentPresence[]): HqPresenceIndex {
  const byAgent = new Map<string, HqAgentPresence>();
  const byDesk = new Map<string, HqAgentPresence>();
  for (const p of list) {
    if (p.agentId.startsWith('desk:')) { if (p.deskId) byDesk.set(p.deskId, p); } else byAgent.set(p.agentId, p);
  }
  return { byAgent, byDesk };
}

/** The presence record that backs a desk: its agent's, or the private-agent record of that desk. */
export function presenceForDesk(index: HqPresenceIndex | null, desk: Readonly<{ id: string; agentId: string | null }>): HqAgentPresence | undefined {
  if (!index) return undefined;
  return (desk.agentId ? index.byAgent.get(desk.agentId) : undefined) ?? index.byDesk.get(desk.id);
}

export type PresenceItemLike = Readonly<{
  key: string; agent_id: string | null; agent_name: string | null; desk_id: string | null; state: StatusKey;
  provenance: Readonly<{ reason: string | null }>; task: Readonly<{ id: string; title: string }> | null; last_verified_at: string; stale: boolean;
}>;

/** Maps the presence API's items; the department comes from the agent profile when it is known. */
export function presenceFromItems(items: readonly PresenceItemLike[], departmentOf: (agentId: string) => string | null): HqAgentPresence[] {
  return items.map((i) => ({
    agentId: i.agent_id ?? i.key, name: i.agent_name ?? '', role: i.agent_id ? departmentOf(i.agent_id) : null, status: i.state,
    taskTitles: i.task ? [i.task.title] : [], observedAt: i.last_verified_at, stale: i.stale, reason: i.provenance.reason, deskId: i.desk_id, source: 'presence-api',
  }));
}

export type WorkstationLike = Readonly<{
  agentId: string; name: string; department: string | null; suggestedDepartment: string;
  status: 'working' | 'queued' | 'waiting_approval' | 'blocked' | 'paused' | 'idle' | 'unknown';
  taskTitles: readonly string[]; observedAt: string;
}>;

/** A live meeting held in an office room (the stored `room_id` of the meeting). Only stored participants are listed. */
export type RoomMeeting = Readonly<{ meetingId: string; title: string; agentIds: readonly string[]; people: number }>;

export type MeetingEvidence = Readonly<{
  liveMeetings: number; agentIds: ReadonlySet<string>;
  /** Live meetings by office room id. A meeting with no room link is counted above but belongs to no room. */
  rooms: ReadonlyMap<string, RoomMeeting>;
}>;

/** Maps task-derived workstation status plus meeting evidence to one display status per agent. */
export function agentPresence(records: readonly WorkstationLike[], meetings: MeetingEvidence | null): HqAgentPresence[] {
  return records.map((r) => {
    let status: StatusKey;
    if (r.status === 'paused') status = 'paused';
    else if (meetings?.agentIds.has(r.agentId)) status = 'meeting';
    else if (r.status === 'working') status = 'working';
    else if (r.status === 'waiting_approval') status = 'waiting_approval';
    else if (r.status === 'blocked') status = 'blocked';
    // A queued task does not prove execution: it reads as queued, never as working.
    else if (r.status === 'queued') status = 'queued';
    else if (r.status === 'idle') status = 'idle';
    else status = 'unknown';
    return { agentId: r.agentId, name: r.name, role: r.department?.trim() || (r.suggestedDepartment !== 'Unassigned' ? r.suggestedDepartment : null), status, taskTitles: r.taskTitles, observedAt: r.observedAt, source: 'task-evidence' as const };
  });
}

export function totalsOf(presence: readonly HqAgentPresence[]): CampusTotals {
  const totals: Record<string, number> = { agents: presence.length };
  for (const key of STATUS_ORDER) totals[key] = 0;
  for (const p of presence) totals[p.status] += 1;
  return totals as unknown as CampusTotals;
}

/** Statuses the sources cannot report (meeting evidence missing), shown as "-" and never as zero. */
export function unreportedStatuses(meetings: MeetingEvidence | null): StatusKey[] {
  return meetings ? [] : ['meeting'];
}

type MeetingRow = Readonly<{ id: string; status: string; title?: string; room_id?: string | null }>;
type ParticipantRow = Readonly<{ actor_id: string; kind: string }>;

/**
 * Reads live (active) agent meetings and their agent participants. `read` is a GET helper that
 * resolves JSON. Any failure returns null, so callers report "unknown" and not "nobody is in a meeting".
 */
export async function loadMeetingEvidence(read: <T>(path: string) => Promise<T>): Promise<MeetingEvidence | null> {
  try {
    const list = await read<{ items: MeetingRow[]; next_cursor: string | null }>('/api/v1/agent-meetings?status=active&limit=100');
    if (!Array.isArray(list.items)) return null;
    const active = list.items.filter((m) => m.status === 'active').slice(0, 20);
    const agentIds = new Set<string>();
    const rooms = new Map<string, RoomMeeting>();
    for (const meeting of active) {
      const detail = await read<{ participants?: ParticipantRow[] }>(`/api/v1/agent-meetings/${encodeURIComponent(meeting.id)}`);
      const agents: string[] = [];
      let people = 0;
      for (const p of detail.participants ?? []) {
        if (p.kind === 'agent') { agentIds.add(p.actor_id); agents.push(p.actor_id); } else people += 1;
      }
      // One live meeting per room is shown; a second one in the same room keeps the first (oldest id) and is still counted above.
      if (typeof meeting.room_id === 'string' && meeting.room_id !== '' && !rooms.has(meeting.room_id)) {
        rooms.set(meeting.room_id, { meetingId: meeting.id, title: meeting.title ?? '', agentIds: agents, people });
      }
    }
    // More than one page of meetings might hide a live one: treat that as not fully known.
    if (list.next_cursor) return null;
    return { liveMeetings: active.length, agentIds, rooms };
  } catch {
    return null;
  }
}

/** The rooms that have a live meeting: what the scene, the labels and the list show as "In a meeting". */
export function liveMeetingRoomIdsOf(meetings: MeetingEvidence | null): ReadonlySet<string> {
  return new Set(meetings ? [...meetings.rooms.keys()] : []);
}
