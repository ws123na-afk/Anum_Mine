// Live meeting room state (docs/ANUM_MINE_LIVE_MEETING_ROOM_UI.md). Pure: no browser, no network,
// so `pnpm --filter @anum/web test:unit` can replay event logs through it.
// Everything the room shows comes from a stored row or a stored event. A speaker is "speaking" only
// between a stored `turn_started` and its `message` or `turn_failed`, and never longer than SPEAKING_WINDOW_MS.

export type MeetingStatus = 'scheduled' | 'active' | 'paused' | 'finished' | 'cancelled' | 'failed';
export const TERMINAL: readonly MeetingStatus[] = ['finished', 'cancelled', 'failed'];
export const SPEAKING_WINDOW_MS = 150_000;

export interface RoomMeeting {
  id: string; title: string; status: MeetingStatus; revision: number; created_by: string;
  max_turns: number; max_duration_seconds: number; max_tokens: number; max_cost_usd: number | string; max_participants: number;
  total_input_tokens: number; total_output_tokens: number; total_cost_usd: number | string;
  started_at: string | null; ended_at: string | null; ended_reason: string | null;
  hand_raised_by: string | null; transcript_deleted_at: string | null;
}
export interface RoomParticipant { id: string; actor_id: string; kind: 'agent' | 'human' }
export interface RoomAttendance { actor_id: string; joined_at: string; left_at: string | null }
export interface AgendaItem { id: string; position: number; title: string; state: 'open' | 'current' | 'done' }
export type OutputKind = 'decision' | 'action_item';
export type OutputState = 'draft' | 'confirmed' | 'dismissed';
export interface RoomOutput {
  id: string; kind: OutputKind; body: string; state: OutputState; owner_label: string | null;
  source_sequences: number[]; task_id: string | null; created_by: string; decided_by: string | null;
}
export interface RoomFile { id?: string; file_id: string; name: string | null; content_type?: string | null }
export interface RoomMessage {
  sequence: number; speaker_id: string; role: 'agent' | 'human' | 'system'; body: string; created_at: string;
  redacted?: boolean; provenance?: string | null; request_key?: string | null;
}
export interface RoomViewer { user_id: string; is_owner: boolean; can_read_transcript?: boolean }
export interface RoomBundle {
  meeting: RoomMeeting; participants: RoomParticipant[]; attendance: RoomAttendance[]; agenda: AgendaItem[];
  outputs: RoomOutput[]; files: RoomFile[]; speaking: { agent_id: string | null; sequence: number | null; since: string }[];
  last_event_seq: number; transcript_deleted: boolean; viewer: RoomViewer;
  /** Set for everyone who is not a workspace owner: the API sends no text, only counts (S-03). */
  transcript_hidden?: boolean; outputs_count?: number; files_count?: number;
}
/** The workspace's monthly agent meeting budget (S-02): `GET/PUT /api/v1/agent-meetings/budget`. */
export interface MeetingBudget {
  limit_usd: number; default_limit_usd: number; is_default: boolean; used_usd: number; remaining_usd: number; percent: number;
  turns: number; exceeded: boolean; period_start: string; resets_on: string; updated_by: string | null; updated_at: string | null;
  message: string | null; can_manage: boolean;
}
export interface RoomEvent { seq: number; kind: string; actor_id: string | null; payload: Record<string, unknown>; created_at: string }

export interface Speaking { agentId: string; since: string; sequence: number | null }
export interface Notice { seq: number; kind: 'turn_failed'; reason: string }
export interface RoomState {
  meeting: RoomMeeting; participants: RoomParticipant[]; attendance: RoomAttendance[]; agenda: AgendaItem[];
  outputs: RoomOutput[]; files: RoomFile[]; messages: RoomMessage[]; speaking: Record<string, Speaking>;
  notices: Notice[]; lastSeq: number; transcriptDeleted: boolean; viewer: RoomBundle['viewer'];
  /** True when the caller is not a workspace owner: no messages, decisions or files are loaded, only counts. */
  transcriptHidden: boolean; outputsCount: number; filesCount: number;
  /** Counts events that change this month's spend (a message, the budget being reached): the room refetches the budget when it moves. */
  budgetTick: number;
}

export function initialState(bundle: RoomBundle, messages: RoomMessage[]): RoomState {
  const speaking: Record<string, Speaking> = {};
  bundle.speaking.forEach((s, i) => { if (s.agent_id) speaking['loaded-' + i + '-' + s.agent_id] = { agentId: s.agent_id, since: s.since, sequence: s.sequence }; });
  return {
    meeting: bundle.meeting, participants: bundle.participants, attendance: bundle.attendance, agenda: bundle.agenda,
    outputs: bundle.outputs, files: bundle.files, messages: [...messages].sort((a, b) => a.sequence - b.sequence), speaking,
    notices: [], lastSeq: bundle.last_event_seq, transcriptDeleted: bundle.transcript_deleted, viewer: bundle.viewer,
    transcriptHidden: bundle.transcript_hidden === true, outputsCount: bundle.outputs_count ?? bundle.outputs.length,
    filesCount: bundle.files_count ?? bundle.files.length, budgetTick: 0,
  };
}

const upsert = <T extends { id: string }>(list: T[], item: T): T[] => list.some((x) => x.id === item.id) ? list.map((x) => x.id === item.id ? item : x) : [...list, item];
const str = (value: unknown): string => typeof value === 'string' ? value : '';

/** Apply one stored event. Idempotent: an event at or below `lastSeq` is ignored, so a replay after a reconnect never repeats. */
export function applyEvent(state: RoomState, event: RoomEvent): RoomState {
  if (event.seq <= state.lastSeq) return state;
  const p = event.payload ?? {};
  const next: RoomState = { ...state, lastSeq: event.seq };
  switch (event.kind) {
    case 'turn_started': {
      const key = str(p.request_key) || 'seq-' + String(p.sequence);
      next.speaking = { ...state.speaking, [key]: { agentId: str(p.agent_id) || str(event.actor_id), since: event.created_at, sequence: typeof p.sequence === 'number' ? p.sequence : null } };
      break;
    }
    case 'message': {
      const message: RoomMessage = {
        sequence: Number(p.sequence), speaker_id: str(p.speaker_id), role: (str(p.role) || 'agent') as RoomMessage['role'], body: str(p.body),
        created_at: str(p.created_at) || event.created_at, redacted: Boolean(p.redacted), provenance: str(p.provenance) || null, request_key: str(p.request_key) || null,
      };
      if (!state.messages.some((m) => m.sequence === message.sequence)) next.messages = [...state.messages, message].sort((a, b) => a.sequence - b.sequence);
      next.budgetTick = state.budgetTick + 1;
      const key = message.request_key;
      const rest: Record<string, Speaking> = {};
      for (const [k, v] of Object.entries(state.speaking)) {
        const loadedFor = k.startsWith('loaded-') && v.agentId === message.speaker_id;
        if (k !== key && !loadedFor) rest[k] = v;
      }
      next.speaking = rest;
      break;
    }
    case 'turn_failed': {
      const rest: Record<string, Speaking> = {};
      for (const [k, v] of Object.entries(state.speaking)) if (v.sequence !== p.sequence) rest[k] = v;
      next.speaking = rest;
      next.notices = [...state.notices, { seq: event.seq, kind: 'turn_failed' as const, reason: str(p.reason) }].slice(-5);
      break;
    }
    case 'budget_reached': next.budgetTick = state.budgetTick + 1; break;
    case 'state': {
      const status = str(p.status) as MeetingStatus;
      next.meeting = { ...state.meeting, status, revision: typeof p.revision === 'number' ? p.revision : state.meeting.revision,
        ended_reason: TERMINAL.includes(status) ? str(p.reason) || state.meeting.ended_reason : state.meeting.ended_reason,
        hand_raised_by: TERMINAL.includes(status) ? null : state.meeting.hand_raised_by };
      if (status !== 'active') next.speaking = {};
      break;
    }
    case 'hand_raised': next.meeting = { ...state.meeting, hand_raised_by: str(p.actor_id) || event.actor_id }; break;
    case 'hand_lowered': next.meeting = { ...state.meeting, hand_raised_by: null }; break;
    case 'joined': {
      const actor = str(p.actor_id);
      const others = state.attendance.filter((a) => a.actor_id !== actor);
      next.attendance = [...others, { actor_id: actor, joined_at: event.created_at, left_at: null }];
      break;
    }
    case 'left': next.attendance = state.attendance.map((a) => a.actor_id === str(p.actor_id) ? { ...a, left_at: event.created_at } : a); break;
    case 'agenda': {
      const item = p.item as AgendaItem;
      if (p.op === 'delete') next.agenda = state.agenda.filter((a) => a.id !== item.id);
      else {
        let list = upsert(state.agenda, item);
        if (item.state === 'current') list = list.map((a) => a.id !== item.id && a.state === 'current' ? { ...a, state: 'open' as const } : a);
        next.agenda = list.sort((a, b) => a.position - b.position);
      }
      break;
    }
    case 'output': next.outputs = upsert(state.outputs, p.output as RoomOutput); break;
    case 'file': {
      const id = str(p.file_id);
      next.files = p.op === 'remove' ? state.files.filter((f) => f.file_id !== id) : state.files.some((f) => f.file_id === id) ? state.files : [...state.files, { file_id: id, name: str(p.name) || null }];
      break;
    }
    default: break;
  }
  return next;
}

/** Agents with a turn really in flight now. Stale starts (a crashed worker) are ignored. */
export function activeSpeakers(state: RoomState, now: number = Date.now()): string[] {
  if (state.meeting.status !== 'active') return [];
  const ids = new Set<string>();
  for (const s of Object.values(state.speaking)) if (now - Date.parse(s.since) <= SPEAKING_WINDOW_MS) ids.add(s.agentId);
  return [...ids];
}

export function presentActors(state: RoomState): string[] {
  return state.attendance.filter((a) => a.left_at === null).map((a) => a.actor_id);
}

/** Incremental SSE parser: feed text chunks, get whole frames back and the unfinished rest. */
export function parseSse(buffer: string): { events: (RoomEvent & { name: string })[]; rest: string; end: boolean; reconnect: boolean } {
  const frames = buffer.split('\n\n');
  const rest = frames.pop() ?? '';
  const events: (RoomEvent & { name: string })[] = [];
  let end = false, reconnect = false;
  for (const frame of frames) {
    const lines = frame.split('\n');
    const name = lines.find((l) => l.startsWith('event: '))?.slice(7) ?? '';
    const data = lines.find((l) => l.startsWith('data: '))?.slice(6);
    if (name === 'end') { end = true; continue; }
    if (name === 'reconnect') { reconnect = true; continue; }
    if (!data || !name) continue;
    try { events.push({ ...(JSON.parse(data) as RoomEvent), name }); } catch { /* a torn frame is dropped; the next replay recovers it */ }
  }
  return { events, rest, end, reconnect };
}

export function budgetUsed(m: RoomMeeting, turns: number) {
  const num = (v: number | string) => typeof v === 'number' ? v : Number(v);
  return {
    turns: { used: turns, max: m.max_turns },
    tokens: { used: m.total_input_tokens + m.total_output_tokens, max: m.max_tokens },
    cost: { used: num(m.total_cost_usd), max: num(m.max_cost_usd) },
  };
}

/** The owner's draft text from a message: first 300 characters, whitespace collapsed. */
export function draftFromMessage(body: string): string {
  const flat = body.replace(/\s+/g, ' ').trim();
  return flat.length > 300 ? flat.slice(0, 297) + '...' : flat;
}

/** The meeting id a `#office/meeting/<id>` hash points at, or null. */
export function meetingIdFromHash(hash: string): string | null {
  const match = /^#?office\/meeting\/([A-Za-z0-9][A-Za-z0-9._:-]{0,79})$/.exec(hash);
  return match ? match[1] : null;
}
export const meetingHash = (id: string): string => `#office/meeting/${encodeURIComponent(id)}`;
