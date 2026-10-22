/**
 * Presence for the headquarters, built only from records the signed-in person may read:
 * workstation evidence (agent profiles plus open tasks) and the participants of live agent
 * meetings. A status exists only when a record backs it; `unreported` lists the statuses the
 * current sources cannot tell, so the screen shows "-" for them instead of a zero.
 */
import { STATUS_ORDER, type CampusTotals, type StatusKey } from './campusModel.ts';

export type HqAgentPresence = Readonly<{
  agentId: string;
  name: string;
  role: string | null;
  status: StatusKey;
  taskTitles: readonly string[];
  observedAt: string;
}>;

export type WorkstationLike = Readonly<{
  agentId: string; name: string; department: string | null; suggestedDepartment: string;
  status: 'working' | 'queued' | 'waiting_approval' | 'blocked' | 'paused' | 'idle' | 'unknown';
  taskTitles: readonly string[]; observedAt: string;
}>;

export type MeetingEvidence = Readonly<{ liveMeetings: number; agentIds: ReadonlySet<string> }>;

/** Maps task-derived workstation status plus meeting evidence to one display status per agent. */
export function agentPresence(records: readonly WorkstationLike[], meetings: MeetingEvidence | null): HqAgentPresence[] {
  return records.map((r) => {
    let status: StatusKey;
    if (r.status === 'paused') status = 'paused';
    else if (meetings?.agentIds.has(r.agentId)) status = 'meeting';
    else if (r.status === 'working') status = 'working';
    else if (r.status === 'waiting_approval') status = 'waiting_approval';
    else if (r.status === 'blocked') status = 'blocked';
    // A queued task does not prove execution, so it reads as idle with the task visible on the card.
    else if (r.status === 'queued' || r.status === 'idle') status = 'idle';
    else status = 'unknown';
    return { agentId: r.agentId, name: r.name, role: r.department?.trim() || (r.suggestedDepartment !== 'Unassigned' ? r.suggestedDepartment : null), status, taskTitles: r.taskTitles, observedAt: r.observedAt };
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

type MeetingRow = Readonly<{ id: string; status: string }>;
type ParticipantRow = Readonly<{ actor_id: string; kind: string }>;

/**
 * Reads live (active) agent meetings and their agent participants. `read` is a GET helper that
 * resolves JSON. Any failure returns null, so callers report "unknown" and not "nobody is in a meeting".
 */
export async function loadMeetingEvidence(read: <T>(path: string) => Promise<T>): Promise<MeetingEvidence | null> {
  try {
    const list = await read<{ items: MeetingRow[]; next_cursor: string | null }>('/api/v1/agent-meetings?limit=100');
    if (!Array.isArray(list.items)) return null;
    const active = list.items.filter((m) => m.status === 'active').slice(0, 20);
    const agentIds = new Set<string>();
    for (const meeting of active) {
      const detail = await read<{ participants?: ParticipantRow[] }>(`/api/v1/agent-meetings/${encodeURIComponent(meeting.id)}`);
      for (const p of detail.participants ?? []) if (p.kind === 'agent') agentIds.add(p.actor_id);
    }
    // More than one page of meetings might hide a live one: treat that as not fully known.
    if (list.next_cursor) return null;
    return { liveMeetings: active.length, agentIds };
  } catch {
    return null;
  }
}
