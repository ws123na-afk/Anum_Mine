/** ANUM MINE presence adapter: snapshot read, SSE frame parsing and a pure state reducer
 * (contract: packages/contracts/src/officePresence.ts). The server derives every state from
 * persisted records; this module never invents one. */
import type {
  OfficePresenceDeltaFrame, OfficePresenceItem, OfficePresenceSnapshot, OfficePresenceSnapshotFrame,
  OfficePresenceStateTotals,
} from '@anum/contracts';

export type PresenceRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class PresenceApiError extends Error {
  constructor(message: string) { super(message); this.name = 'PresenceApiError'; }
}

export const PRESENCE_PATH = '/api/v1/office/presence';
export const PRESENCE_STREAM_PATH = '/api/v1/office/presence/stream';
const STATES = ['working', 'queued', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'] as const;

export interface PresenceState {
  workspaceId: string;
  asOf: string;
  cursor: string | null;
  sequence: number;
  totals: OfficePresenceStateTotals;
  items: Map<string, OfficePresenceItem>;
}

export type PresenceFrame =
  | { event: 'snapshot'; id: string | null; data: OfficePresenceSnapshotFrame }
  | { event: 'delta'; id: string | null; data: OfficePresenceDeltaFrame };

const emptyTotals = (): OfficePresenceStateTotals =>
  Object.fromEntries(STATES.map((s) => [s, 0])) as OfficePresenceStateTotals;

export function totalsOf(items: Iterable<OfficePresenceItem>): OfficePresenceStateTotals {
  const totals = emptyTotals();
  for (const item of items) totals[item.state] += 1;
  return totals;
}

function validItem(item: OfficePresenceItem): boolean {
  return !!item && typeof item.key === 'string' && (STATES as readonly string[]).includes(item.state)
    && !!item.provenance && typeof item.last_verified_at === 'string' && typeof item.stale === 'boolean';
}

export function stateFromSnapshot(snapshot: OfficePresenceSnapshot, sequence = 0): PresenceState {
  if (!snapshot || snapshot.version !== 1 || !Array.isArray(snapshot.items)) throw new PresenceApiError('Presence response is not version 1');
  const items = new Map<string, OfficePresenceItem>();
  for (const item of snapshot.items) {
    if (!validItem(item)) throw new PresenceApiError('Presence returned an invalid item');
    if (items.has(item.key)) throw new PresenceApiError('Presence returned a repeated item');
    items.set(item.key, item);
  }
  return { workspaceId: snapshot.workspace_id, asOf: snapshot.as_of, cursor: snapshot.cursor, sequence, totals: totalsOf(items.values()), items };
}

/** Applies a delta. A sequence gap, a repeat or an older frame returns `null`: reconnect and take the new snapshot. */
export function applyPresenceDelta(state: PresenceState, delta: OfficePresenceDeltaFrame): PresenceState | null {
  if (!delta || delta.version !== 1) return null;
  if (delta.sequence !== state.sequence + 1) return null;
  const items = new Map(state.items);
  for (const key of delta.removed) items.delete(key);
  for (const item of delta.changed) {
    if (!validItem(item)) return null;
    items.set(item.key, item);
  }
  return { ...state, asOf: delta.as_of, sequence: delta.sequence, totals: totalsOf(items.values()), items };
}

/** Parses SSE text (possibly several frames) into frames; comments and unknown events are skipped. */
export function parsePresenceFrames(text: string): PresenceFrame[] {
  const frames: PresenceFrame[] = [];
  for (const block of text.split('\n\n')) {
    let event = '', id: string | null = null, data = '';
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7);
      else if (line.startsWith('id: ')) id = line.slice(4);
      else if (line.startsWith('data: ')) data += line.slice(6);
    }
    if (!data || (event !== 'snapshot' && event !== 'delta')) continue;
    try { frames.push({ event, id, data: JSON.parse(data) } as PresenceFrame); } catch { /* skip a broken frame */ }
  }
  return frames;
}

export function createPresenceClient(request: PresenceRequester, scope: () => { tenantId: string; workspaceId: string }) {
  const load = async (): Promise<PresenceState> => {
    const started = scope();
    const snapshot = await request<OfficePresenceSnapshot>(PRESENCE_PATH, { method: 'GET' });
    const state = stateFromSnapshot(snapshot);
    const now = scope();
    if (state.workspaceId !== started.workspaceId || now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId) {
      throw new PresenceApiError('Presence belongs to another workspace');
    }
    return state;
  };
  return { load };
}

export type DeltaOutcome =
  | { kind: 'applied'; state: PresenceState }
  /** A frame this connection already applied (or an older one): ignore it. */
  | { kind: 'duplicate' }
  /** A frame was skipped, or the frame is broken: the state cannot be trusted, take a new snapshot. */
  | { kind: 'gap' };

/** Like `applyPresenceDelta`, but tells a repeat (harmless) from a gap (resynchronise). */
export function classifyPresenceDelta(state: PresenceState, delta: OfficePresenceDeltaFrame): DeltaOutcome {
  if (!delta || delta.version !== 1 || !Number.isInteger(delta.sequence) || !Array.isArray(delta.changed) || !Array.isArray(delta.removed)) return { kind: 'gap' };
  if (delta.sequence <= state.sequence) return { kind: 'duplicate' };
  const next = applyPresenceDelta(state, delta);
  return next ? { kind: 'applied', state: next } : { kind: 'gap' };
}
