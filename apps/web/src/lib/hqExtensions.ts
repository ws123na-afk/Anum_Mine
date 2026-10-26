/**
 * Extension points of the headquarters (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md).
 *
 * Other workstreams plug in here and never reach into the scene:
 *  - `onSelectRoom` / `renderRoomPanel`: the meetings workstream renders its room panel.
 *  - `onSelectAgent` / `renderWorkstationPanel`: the workstation panel for a desk.
 *  - `hqCommands.navigateTo`: the executive assistant moves the view (building, floor, room, agent).
 * Pure resolution lives here so the unit runner covers it; the React side only applies the result.
 */
import type { ReactNode } from 'react';
import type { Campus } from './campusModel.ts';

export type HqRoomRef = Readonly<{
  workspaceId: string; buildingId: string | null; floorId: string; floorLevel: number;
  roomId: string; label: string; kind: string; capacity: number;
}>;

export type HqAgentRef = Readonly<{
  agentId: string; name: string; role: string | null; status: string;
  deskId: string | null; roomId: string | null; workspaceId: string;
}>;

/** What the assistant can ask for. Every field is optional; the more specific ones narrow the view. */
export type HqNavTarget = Readonly<{
  /** A workspace id or a building name. */
  building?: string;
  /** A floor id, a level number or a floor name. */
  floor?: string | number;
  /** A room id or a room name. */
  room?: string;
  /** An agent id or an agent name. */
  agent?: string;
  view?: '3d' | 'top' | 'list';
}>;

export type HqNavResult =
  | Readonly<{ ok: true; applied: Readonly<{ workspaceId: string | null; floorId: string | null; roomId: string | null; agentId: string | null }> }>
  | Readonly<{ ok: false; code: 'not-found' | 'not-accessible' | 'not-mounted' | 'ambiguous'; message: string }>;

export type HqPanelContext = Readonly<{ close: () => void }>;

export type HqSlots = Readonly<{
  /** Rendered in the side panel when a room is selected. Without it the built-in room summary shows. */
  renderRoomPanel?: (room: HqRoomRef, ctx: HqPanelContext) => ReactNode;
  /** Rendered when an agent's desk card is opened. Without it the built-in agent summary shows. */
  renderWorkstationPanel?: (agent: HqAgentRef, ctx: HqPanelContext) => ReactNode;
  /** Right-hand dock beside the scene (up to 380 px). The executive workstream renders its PA panel here. Without it no dock shows. */
  renderPaDock?: (ctx: HqPanelContext) => ReactNode;
}>;

export type HqCallbacks = Readonly<{
  onSelectRoom?: (room: HqRoomRef) => void;
  onSelectAgent?: (agent: HqAgentRef) => void;
}>;

// ---------------------------------------------------------------- resolution

export type NavScene = Readonly<{
  workspaceId: string;
  /** The open registry building, when the workspace has several (a target may name another one). */
  buildingId?: string | null;
  floors: ReadonlyArray<Readonly<{ id: string; level: number; name: string; nameAr?: string | null; rooms: ReadonlyArray<Readonly<{ id: string; label: string; labelAr?: string | null }>> }>>;
  agents: ReadonlyArray<Readonly<{ id: string; name: string; roomId: string | null }>>;
}>;

export type NavResolution =
  | Readonly<{ kind: 'apply'; workspaceId: string; buildingId?: string | null; floorId: string | null; roomId: string | null; agentId: string | null }>
  | Readonly<{ kind: 'switch'; workspaceId: string }>
  | Readonly<{ kind: 'error'; code: 'not-found' | 'not-accessible' | 'ambiguous'; message: string }>;

const fold = (s: string) => s.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();

/** A name to match against: one string, or the English and Arabic names of a registry record (either one finds it). */
type MatchNames = string | readonly (string | null | undefined)[];
const foldedNames = (names: MatchNames): string[] => (typeof names === 'string' ? [names] : names).filter((n): n is string => !!n && n.trim() !== '').map(fold);

function pickOne<T>(rows: readonly T[], id: (r: T) => string, name: (r: T) => MatchNames, query: string): T | 'ambiguous' | null {
  const q = fold(query);
  const byId = rows.filter((r) => fold(id(r)) === q);
  if (byId.length === 1) return byId[0];
  const exact = rows.filter((r) => foldedNames(name(r)).includes(q));
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) return 'ambiguous';
  const partial = rows.filter((r) => foldedNames(name(r)).some((n) => n.includes(q)));
  if (partial.length === 1) return partial[0];
  return partial.length > 1 ? 'ambiguous' : null;
}

/**
 * Turns a navigation request into one concrete step. A building in another workspace resolves to a
 * `switch` (the workspace must be opened explicitly; nothing is read across workspaces); floors,
 * rooms and agents resolve only inside the open workspace's saved layout.
 */
export function resolveNavTarget(target: HqNavTarget, campus: Campus, open: NavScene | null): NavResolution {
  let workspaceId = open?.workspaceId ?? null;
  let buildingId: string | null = null;
  if (target.building !== undefined) {
    const found = pickOne(campus.buildings, (b) => b.workspace_id, (b) => [b.name, b.nameAr], target.building);
    if (found === 'ambiguous') return { kind: 'error', code: 'ambiguous', message: `More than one building matches "${target.building}".` };
    if (!found) return { kind: 'error', code: 'not-found', message: `No building matches "${target.building}".` };
    if (!found.accessible) return { kind: 'error', code: 'not-accessible', message: `${found.name} is not available to you right now.` };
    const realWorkspace = found.realWorkspaceId ?? found.workspace_id;
    if (realWorkspace !== open?.workspaceId) return { kind: 'switch', workspaceId: realWorkspace };
    workspaceId = realWorkspace;
    buildingId = found.registryBuildingId ?? null;
    // Another registry building of the open workspace: its floors and rooms are read once it is open.
    if (buildingId && open.buildingId && buildingId !== open.buildingId) return { kind: 'apply', workspaceId, buildingId, floorId: null, roomId: null, agentId: null };
  }
  if (!workspaceId || !open) return { kind: 'error', code: 'not-found', message: 'No building is open.' };

  let floorId: string | null = null;
  if (target.floor !== undefined) {
    const q = String(target.floor);
    const level = Number(q);
    const byLevel = Number.isFinite(level) && q.trim() !== '' ? open.floors.filter((f) => f.level === level) : [];
    const floor = byLevel.length === 1 ? byLevel[0] : pickOne(open.floors, (f) => f.id, (f) => [f.name, f.nameAr], q);
    if (floor === 'ambiguous') return { kind: 'error', code: 'ambiguous', message: `More than one floor matches "${q}".` };
    if (!floor) return { kind: 'error', code: 'not-found', message: `No floor matches "${q}".` };
    floorId = floor.id;
  }

  let roomId: string | null = null;
  if (target.room !== undefined) {
    const pool = floorId ? open.floors.filter((f) => f.id === floorId) : open.floors;
    const rooms = pool.flatMap((f) => f.rooms.map((r) => ({ ...r, floorId: f.id })));
    const room = pickOne(rooms, (r) => r.id, (r) => [r.label, r.labelAr], target.room);
    if (room === 'ambiguous') return { kind: 'error', code: 'ambiguous', message: `More than one room matches "${target.room}".` };
    if (!room) return { kind: 'error', code: 'not-found', message: `No room matches "${target.room}".` };
    roomId = room.id;
    floorId = room.floorId;
  }

  let agentId: string | null = null;
  if (target.agent !== undefined) {
    const agent = pickOne(open.agents, (a) => a.id, (a) => a.name, target.agent);
    if (agent === 'ambiguous') return { kind: 'error', code: 'ambiguous', message: `More than one agent matches "${target.agent}".` };
    if (!agent) return { kind: 'error', code: 'not-found', message: `No agent matches "${target.agent}".` };
    agentId = agent.id;
    if (agent.roomId && !roomId) {
      roomId = agent.roomId;
      floorId = open.floors.find((f) => f.rooms.some((r) => r.id === agent.roomId))?.id ?? floorId;
    }
  }
  return { kind: 'apply', workspaceId, ...(buildingId ? { buildingId } : {}), floorId, roomId, agentId };
}

// ---------------------------------------------------------------- command bus

type Handler = (target: HqNavTarget) => Promise<HqNavResult>;
const PENDING_KEY = 'anum.hq.pending';
export const NAV_EVENT = 'anum:hq-navigate';
const PENDING_TTL_MS = 60_000;

/**
 * The executive assistant's way in. When the headquarters is open it applies the target at once.
 * When it is not, the target is kept for a minute (it survives the workspace switch that a
 * building in another workspace needs) and the view opens at `#office-preview`.
 */
export class HqCommandBus {
  private handler: Handler | null = null;

  /** The mounted headquarters registers itself here; returns the unsubscribe function. */
  attach(handler: Handler): () => void {
    this.handler = handler;
    // The same move as a DOM event, for callers outside the module graph: detail is an HqNavTarget.
    const onEvent = (event: Event) => { const target = (event as CustomEvent<HqNavTarget>).detail; if (target && typeof target === 'object') void handler(target); };
    if (typeof window !== 'undefined') window.addEventListener(NAV_EVENT, onEvent);
    const pending = this.takePending();
    if (pending) void handler(pending);
    return () => {
      if (typeof window !== 'undefined') window.removeEventListener(NAV_EVENT, onEvent);
      if (this.handler === handler) this.handler = null;
    };
  }

  get mounted(): boolean { return this.handler !== null; }

  async navigateTo(target: HqNavTarget): Promise<HqNavResult> {
    if (this.handler) return this.handler(target);
    this.keepPending(target);
    if (typeof window !== 'undefined' && !window.location.hash.startsWith('#office')) window.location.hash = '#office-preview';
    return { ok: false, code: 'not-mounted', message: 'The headquarters is opening; the move will apply when it is ready.' };
  }

  /** Keeps a target across a remount (workspace switch) or until the view opens. */
  keepPending(target: HqNavTarget): void {
    try { sessionStorage.setItem(PENDING_KEY, JSON.stringify({ target, at: Date.now() })); } catch { /* storage blocked: the move is dropped */ }
  }

  takePending(now = Date.now()): HqNavTarget | null {
    try {
      const raw = sessionStorage.getItem(PENDING_KEY);
      if (!raw) return null;
      sessionStorage.removeItem(PENDING_KEY);
      const row = JSON.parse(raw) as { target?: HqNavTarget; at?: number };
      if (!row.target || typeof row.at !== 'number' || now - row.at > PENDING_TTL_MS) return null;
      return row.target;
    } catch { return null; }
  }
}

/** The one bus of the page. */
export const hqCommands = new HqCommandBus();
