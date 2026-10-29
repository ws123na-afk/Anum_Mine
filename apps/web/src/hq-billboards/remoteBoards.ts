/**
 * Every building's board (docs/ANUM_MINE_HQ_BILLBOARDS.md#every-building): the billboard facts and the owner's sign of the buildings of the
 * caller's other workspaces, from `GET /api/v1/office/campus/boards`. The server reads each workspace in its own tenant and workspace context
 * with the caller's own membership; this module only reads that answer defensively, keeps it fresh and lays it onto the campus buildings.
 * Part of the billboard chunk (lazy).
 *
 * Data truth: nothing here makes a number. A workspace that is not in the answer (no membership, no read permission) or that the server
 * could not read keeps "Not connected"; a first read in flight is "Loading"; a failed first read is an error with Try again; a failed later
 * read keeps the last answer and the billboard calls it stale.
 */
import type { BuildingSign, CampusBuilding, RemoteBoardFacts, RemoteRoom } from '../lib/campusModel.ts';
import { workspaceOf } from '../lib/campusModel.ts';
import { starterKey } from '../lib/hqBuildingModules.ts';

export const BOARDS_PATH = '/api/v1/office/campus/boards';
/** How often the boards are read again while the page is visible (the server keeps each workspace's answer for 15 seconds). */
export const BOARDS_POLL_MS = 30_000;
/** Pages of 16 workspaces read at most (64 workspaces; the campus lists at most 50). */
export const MAX_PAGES = 4;

type BoardFacts = NonNullable<RemoteBoardFacts['board']>;
export type RemoteBoard = Readonly<{ workspaceId: string; accessible: boolean; sign: BuildingSign | null; facts: BoardFacts | null }>;
export type RemoteStatus = 'idle' | 'loading' | 'ready' | 'error' | 'unsupported';
export type RemoteState = Readonly<{ status: RemoteStatus; boards: ReadonlyMap<string, RemoteBoard> }>;
export const NO_REMOTE: RemoteState = { status: 'idle', boards: new Map() };

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const nat = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null);
const HEX = /^#[0-9a-f]{6}$/i;
const SHA = /^[0-9a-f]{64}$/;

function signOf(raw: unknown, buildingId: string, workspaceId: string): BuildingSign | null {
  if (!isObj(raw)) return null;
  const color = str(raw.color), sha = str(raw.logo_sha256), style = str(raw.style);
  const sign: BuildingSign = { buildingId, workspaceId, style, color: color && HEX.test(color) ? color : null, logoSha256: sha && SHA.test(sha) ? sha : null };
  return sign.style || sign.color || sign.logoSha256 ? sign : null;
}

/**
 * The sign that stands for a workspace on its one campus building: its only registry building's sign, or, with several registry buildings,
 * the starter campus headquarters building's (`campus-01-hq`). Any other case keeps the lettered sign (no building is picked by guess).
 */
export function mainSign(buildings: ReadonlyArray<Readonly<{ id: string; sign: BuildingSign | null }>>): BuildingSign | null {
  if (buildings.length === 1) return buildings[0].sign;
  return buildings.find((b) => starterKey(b.id) === 'hq')?.sign ?? null;
}

/** One answer of the boards endpoint, read defensively: a malformed board is dropped; numbers that are not counts are not shown. */
export function boardsFromApi(payload: unknown, scope: Readonly<{ tenantId: string; workspaceId: string }>): { boards: RemoteBoard[]; next: string | null } {
  if (!isObj(payload) || payload.version !== 1 || !Array.isArray(payload.boards)) throw new Error('Campus boards response is not version 1');
  if (payload.tenant_id !== scope.tenantId || payload.current_workspace_id !== scope.workspaceId) throw new Error('Campus boards belong to another scope');
  const boards: RemoteBoard[] = [];
  const seen = new Set<string>();
  for (const raw of payload.boards) {
    if (!isObj(raw)) continue;
    const ws = str(raw.workspace_id);
    if (!ws || seen.has(ws) || ws === scope.workspaceId) continue;
    seen.add(ws);
    if (raw.accessible !== true) { boards.push({ workspaceId: ws, accessible: false, sign: null, facts: null }); continue; }
    const buildings = (Array.isArray(raw.buildings) ? raw.buildings : []).filter(isObj).flatMap((b) => {
      const id = str(b.id);
      return id ? [{ id, sign: signOf(b.sign, id, ws) }] : [];
    });
    const roomsRaw = isObj(raw.rooms) ? raw.rooms : {};
    const rooms: RemoteRoom[] = (Array.isArray(roomsRaw.items) ? roomsRaw.items : []).filter(isObj).flatMap((r) => {
      const label = str(r.name);
      return label ? [{ label, labelAr: str(r.name_ar), kind: str(r.kind) ?? 'office', teamDepartment: str(r.team_department) }] : [];
    });
    const work = isObj(raw.work) ? raw.work : {};
    boards.push({
      workspaceId: ws, accessible: true, sign: mainSign(buildings),
      facts: {
        asOf: str(raw.as_of), rooms, roomsTotal: Math.max(nat(roomsRaw.total) ?? rooms.length, rooms.length),
        departments: (Array.isArray(raw.departments) ? raw.departments : []).filter((d): d is string => typeof d === 'string' && d.length > 0),
        work: { source: str(work.source) ?? 'not_connected', open: nat(work.open), waiting: nat(work.waiting), blocked: nat(work.blocked), truncated: work.truncated === true },
      },
    });
  }
  return { boards, next: str(payload.next_cursor) };
}

/**
 * The campus buildings with every other workspace's board facts and sign laid on. The open workspace's buildings and buildings the person
 * cannot open are returned as they are. A building whose sign the open workspace's registry already set keeps it.
 */
export function withRemote(buildings: readonly CampusBuilding[], state: RemoteState, currentWorkspaceId: string): CampusBuilding[] {
  return buildings.map((b) => {
    const ws = workspaceOf(b);
    if (ws === currentWorkspaceId || !b.accessible) return b;
    const board = state.boards.get(ws);
    const remote: RemoteBoardFacts = {
      status: state.status === 'idle' ? 'loading' : state.status,
      board: board?.accessible ? board.facts : null,
    };
    const sign = b.sign ?? board?.sign ?? undefined;
    return { ...b, remote, ...(sign ? { sign } : {}) };
  });
}

export type BoardsLoader = (cursor: string | null) => Promise<unknown>;
export type RemoteFeed = Readonly<{ retry: () => void; stop: () => void }>;

const statusOf = (e: unknown): number | null => (isObj(e) || e instanceof Error ? nat((e as { status?: unknown }).status) : null);

/**
 * Reads every page of the boards, then again every `intervalMs` while the page is visible, and reports each state change. A 404 or 403 (an
 * older server, or no campus read) is "unsupported": the buildings keep Not connected. Any other failure is an error; earlier boards are kept.
 */
export function startRemoteBoards(args: Readonly<{
  load: BoardsLoader; scope: () => Readonly<{ tenantId: string; workspaceId: string }>; onChange: (state: RemoteState) => void;
  intervalMs?: number; hidden?: () => boolean;
}>): RemoteFeed {
  let state: RemoteState = NO_REMOTE;
  let stopped = false, running = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const set = (next: RemoteState) => { state = next; if (!stopped) args.onChange(next); };
  const schedule = () => { if (!stopped) timer = setTimeout(() => { void read(); }, args.intervalMs ?? BOARDS_POLL_MS); };
  const read = async (): Promise<void> => {
    timer = null;
    if (stopped || running) return;
    if (args.hidden?.() && state.status !== 'idle') { schedule(); return; }
    running = true;
    if (state.status === 'idle' || (state.status === 'error' && state.boards.size === 0)) set({ status: 'loading', boards: state.boards });
    const scope = args.scope();
    try {
      const boards = new Map<string, RemoteBoard>();
      let cursor: string | null = null;
      for (let page = 0; page < MAX_PAGES; page++) {
        const answer = boardsFromApi(await args.load(cursor), scope);
        for (const b of answer.boards) boards.set(b.workspaceId, b);
        cursor = answer.next;
        if (!cursor) break;
      }
      const now = args.scope();
      if (now.tenantId === scope.tenantId && now.workspaceId === scope.workspaceId) set({ status: 'ready', boards });
    } catch (e) {
      const code = statusOf(e);
      set(code === 404 || code === 403 ? { status: 'unsupported', boards: new Map() } : { status: 'error', boards: state.boards });
    } finally {
      running = false;
      if (state.status !== 'unsupported') schedule();
    }
  };
  void read();
  return {
    retry: () => { if (timer) clearTimeout(timer); timer = null; if (state.status !== 'unsupported') void read(); },
    stop: () => { stopped = true; if (timer) clearTimeout(timer); timer = null; },
  };
}
