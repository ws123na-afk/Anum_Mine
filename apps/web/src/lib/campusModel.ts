/**
 * Campus view model: one building per authorised workspace.
 *
 * Data truth (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md): a building exists only when the person is
 * authorised for that workspace; its counts exist only when a presence record backs them, otherwise
 * `totals` is null and the screen says "Presence unavailable". Nothing here invents a number.
 * Pure functions, no DOM: the unit runner covers layout, totals and keyboard order.
 */
import { facingTurn, footprintRect, leanOf, turnRect, type QuarterTurn } from './campusFacing.ts';

export type CampusBuildingKind = 'executive' | 'business' | 'department' | 'personal' | 'career';

export type CampusTotals = Readonly<{
  agents: number; working: number; queued: number; meeting: number; waiting_approval: number;
  blocked: number; idle: number; paused: number; unknown: number;
  /** People seated at a day or night seat whose shift is not in force now (stored hours, building time zone): not counted by a work state. */
  off_shift?: number;
}>;

/** The owner's sign for a registry building (docs/ANUM_MINE_HQ_LOGO_SIGNS.md): the style and brand colour, and the hash of the uploaded logo (null: no logo). */
export type BuildingSign = Readonly<{ buildingId: string; style: string | null; color: string | null; logoSha256: string | null }>;

export type CampusBuilding = Readonly<{
  workspace_id: string;
  tenant_id: string;
  name: string;
  /** The Arabic name of a registry building, when it has one (display only; placement and sorting use `name`). */
  nameAr?: string | null;
  /** What the building looks like. Without an owner's classification (`kindKnown` false) it is drawn as a plain business building. */
  kind: CampusBuildingKind;
  /** False until an owner sets the building type; it is never guessed. */
  kindKnown?: boolean;
  icon: string | null;
  /** The caller's role in that workspace, when the server says. */
  role?: string | null;
  /** Revision of the building profile, for the owner's next save. */
  profileRevision?: number | null;
  rooms?: number | null;
  desks?: number | null;
  archived?: boolean;
  /** Floor count from the office registry, or null when it is not known without opening the workspace. */
  floors: number | null;
  accessible: boolean;
  /** Presence counts, or null when no verified presence exists ("Presence unavailable"). */
  totals: CampusTotals | null;
  presence_verified_at: string | null;
  /**
   * Set on the buildings of the open workspace's office registry when it has more than one: `workspace_id` is then a
   * scene key (workspace id plus building id), and this is the workspace the building really belongs to.
   */
  realWorkspaceId?: string;
  /** The office registry building this card stands for (a registry building is a real record; nothing else is drawn as one). */
  registryBuildingId?: string;
  /** The sign the owner set up for this building's registry record; absent when none of style, colour or logo is set. */
  sign?: BuildingSign;
  /** The workspace's own name, shown on a registry building's card so it is never mistaken for another workspace. */
  workspaceName?: string;
  /** Initials of the agents assigned to desks in this building and their departments (real assignments and presence only). */
  people?: readonly string[];
  departments?: readonly string[];
}>;

/** The workspace a campus building belongs to (the scene key of a registry building is not a workspace id). */
export const workspaceOf = (b: Pick<CampusBuilding, 'workspace_id' | 'realWorkspaceId'>): string => b.realWorkspaceId ?? b.workspace_id;

export type Campus = Readonly<{
  buildings: readonly CampusBuilding[];
  totals: CampusTotals | null;
  generated_at: string;
}>;

export const EMPTY_TOTALS: CampusTotals = {
  agents: 0, working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0,
};

export function addTotals(a: CampusTotals, b: CampusTotals): CampusTotals {
  return {
    agents: a.agents + b.agents, working: a.working + b.working, queued: a.queued + b.queued, meeting: a.meeting + b.meeting,
    waiting_approval: a.waiting_approval + b.waiting_approval, blocked: a.blocked + b.blocked,
    idle: a.idle + b.idle, paused: a.paused + b.paused, unknown: a.unknown + b.unknown, off_shift: (a.off_shift ?? 0) + (b.off_shift ?? 0),
  };
}

/** Sum of the buildings that report presence, and how many do not. */
export function campusTotals(buildings: readonly CampusBuilding[]): { totals: CampusTotals | null; reporting: number; missing: number } {
  let totals: CampusTotals | null = null;
  let reporting = 0;
  for (const b of buildings) {
    if (!b.totals) continue;
    reporting += 1;
    totals = totals ? addTotals(totals, b.totals) : b.totals;
  }
  return { totals, reporting, missing: buildings.length - reporting };
}

export type StatusKey = 'working' | 'queued' | 'meeting' | 'waiting_approval' | 'blocked' | 'idle' | 'paused' | 'unknown' | 'off_shift';
export const STATUS_ORDER: readonly StatusKey[] = ['working', 'meeting', 'queued', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown', 'off_shift'];
/** A count of a status (an older reading has no off shift count: zero). */
export const countOf = (totals: CampusTotals, key: StatusKey): number => totals[key] ?? 0;

/** Tone per status, shared by the dot, the donut and the agent pill. */
export const STATUS_TONE: Readonly<Record<StatusKey, 'ok' | 'meeting' | 'queue' | 'warn' | 'stop' | 'info' | 'muted' | 'faint'>> = {
  working: 'ok', queued: 'queue', meeting: 'meeting', waiting_approval: 'warn', blocked: 'stop', idle: 'info', paused: 'muted', unknown: 'faint', off_shift: 'faint',
};

/** The dominant status of a building: what the status dot on its label shows. */
export function buildingStatus(b: CampusBuilding): StatusKey | 'unavailable' | 'empty' {
  if (!b.totals) return 'unavailable';
  if (b.totals.agents === 0) return 'empty';
  if (b.totals.blocked > 0) return 'blocked';
  if (b.totals.waiting_approval > 0) return 'waiting_approval';
  if (b.totals.working > 0) return 'working';
  if (b.totals.meeting > 0) return 'meeting';
  if (b.totals.queued > 0) return 'queued';
  if (b.totals.idle > 0) return 'idle';
  if (b.totals.paused > 0) return 'paused';
  return countOf(b.totals, 'off_shift') > 0 && b.totals.unknown === 0 ? 'off_shift' : 'unknown';
}

export type DonutArc = Readonly<{ key: StatusKey; value: number; start: number; end: number }>;

/** Donut slices in radians, clockwise from twelve o'clock. Zero-valued statuses are left out. */
export function donutArcs(totals: CampusTotals): DonutArc[] {
  const sum = STATUS_ORDER.reduce((s, k) => s + countOf(totals, k), 0);
  if (sum <= 0) return [];
  const arcs: DonutArc[] = [];
  let angle = 0;
  for (const key of STATUS_ORDER) {
    const value = countOf(totals, key);
    if (value <= 0) continue;
    const span = (value / sum) * Math.PI * 2;
    arcs.push({ key, value, start: angle, end: angle + span });
    angle += span;
  }
  return arcs;
}

/** SVG path for one ring segment. A full circle is split in two so the arc is well defined. */
export function arcPath(cx: number, cy: number, outer: number, inner: number, start: number, end: number): string {
  const full = end - start >= Math.PI * 2 - 1e-6;
  if (full) return `${arcPath(cx, cy, outer, inner, start, start + Math.PI)} ${arcPath(cx, cy, outer, inner, start + Math.PI, start + Math.PI * 2)}`;
  const pt = (r: number, a: number) => `${(cx + Math.sin(a) * r).toFixed(2)} ${(cy - Math.cos(a) * r).toFixed(2)}`;
  const large = end - start > Math.PI ? 1 : 0;
  return `M ${pt(outer, start)} A ${outer} ${outer} 0 ${large} 1 ${pt(outer, end)} L ${pt(inner, end)} A ${inner} ${inner} 0 ${large} 0 ${pt(inner, start)} Z`;
}

// ---------------------------------------------------------------- layout

export type Placement = Readonly<{
  workspaceId: string;
  /** Centre on the ground plane. */
  x: number; z: number;
  width: number; depth: number; height: number;
  /** Visual floor count used for the window rhythm (at least one). */
  levels: number;
  kind: CampusBuildingKind;
  /** The icon an owner picked for the building (its name in the campus contract), or null. It decides the drawn building type. */
  icon: string | null;
  /**
   * Quarter turns of the building (`campusFacing.ts`): its front (door, sign, forecourt) faces the plaza centre. `width` and `depth`
   * stay the building's own (along and across its front); `footprintRect` gives the world rectangle.
   */
  turn: QuarterTurn;
  /**
   * The lean on top of the quarter turn (radians, at most 45 degrees either way; `campusFacing.leanOf`): with it the front points straight
   * at the plaza centre, also on a diagonal plot. Absent or 0 means the quarter turn alone.
   */
  yaw?: number;
}>;

export const FLOOR_HEIGHT = 3.4;
const SIZE: Record<CampusBuildingKind, { w: number; d: number }> = {
  executive: { w: 30, d: 26 }, business: { w: 17, d: 14 }, department: { w: 14, d: 12 }, personal: { w: 11, d: 10 }, career: { w: 15, d: 12 },
};

/**
 * The centre of the campus (ground plan, metres from the middle): the fountain basin, the paved plaza round it, the
 * roundabout (centre line and width of the road), the outer edge of its pavement, and the radius inside which no building
 * lot may stand. `corridor` is the half width kept free for the two avenues (south and east) that join the roundabout to the ring road.
 */
export const PLAZA = { basin: 12.5, paved: 18.8, roadCentre: 25.8, roadWidth: 8, outer: 32.6, clear: 35, corridor: 13 } as const;

/**
 * What a building takes from the ground: its footprint plus a margin at the sides and back, and its front garden on the side where
 * the door is (the side that faces the plaza). The executive building drawn from its own asset has a long forecourt (drive, pool, flags) and a wider podium.
 */
export const LOT = { side: 6, back: 6, front: 16 } as const;
export const EXECUTIVE_LOT = { side: 10.5, back: 6, front: 31 } as const;

export type Lot = Readonly<{ x0: number; x1: number; z0: number; z1: number }>;

/** The lot in world axes: built in the building's own frame (front garden on +z) and turned with the building. */
export function lotOf(p: Readonly<{ x: number; z: number; width: number; depth: number; turn?: number; yaw?: number }>, margins: Readonly<{ side: number; back: number; front: number }> = LOT): Lot {
  return turnRect({ x0: -p.width / 2 - margins.side, x1: p.width / 2 + margins.side, z0: -p.depth / 2 - margins.back, z1: p.depth / 2 + margins.front }, p.turn ?? 0, p.x, p.z, p.yaw ?? 0);
}

/** The margins of a placement: the executive building has its long forecourt unless it is drawn from floor plans. */
export const marginsOf = (kind: CampusBuildingKind, planned: boolean): Readonly<{ side: number; back: number; front: number }> => (kind === 'executive' && !planned ? EXECUTIVE_LOT : LOT);

/** Size of a building drawn from its real floor plans: footprint, storeys and roof height (see `hqPlanFrame.ts`). */
export type PlannedSize = Readonly<{ w: number; d: number; levels: number; height: number }>;

/** A stable number in [0, 1) from a workspace id (FNV-1a with a final mix): where a building prefers to stand does not depend on the others. */
export function unitOf(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
  h ^= h >>> 15; h = Math.imul(h, 2246822519); h ^= h >>> 13; h = Math.imul(h, 3266489917); h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/** How many slots of a ring a building tries before it goes to the next ring out: a chain of displaced buildings stays short. */
const TRIES = 4;

const distanceToLot = (l: Lot): number => Math.hypot(l.x0 > 0 ? l.x0 : l.x1 < 0 ? -l.x1 : 0, l.z0 > 0 ? l.z0 : l.z1 < 0 ? -l.z1 : 0);
const lotsOverlap = (a: Lot, b: Lot, pad: number): boolean => a.x0 < b.x1 + pad && a.x1 > b.x0 - pad && a.z0 < b.z1 + pad && a.z1 > b.z0 - pad;
/** True when a lot stands on the plaza or on either avenue (south from the roundabout, east from the roundabout). */
export const lotBlocksCentre = (l: Lot): boolean => distanceToLot(l) < PLAZA.clear
  || (l.z1 > 0 && l.x0 < PLAZA.corridor && l.x1 > -PLAZA.corridor) || (l.x1 > 0 && l.z0 < PLAZA.corridor && l.z1 > -PLAZA.corridor);

/**
 * Deterministic placement round the plaza at the centre. Every building is turned so its front faces the plaza (`facingTurn`, then `leanOf` points it straight at the centre), and its
 * front garden lies on that side. The executive building takes the north slot (its door looks down on the fountain); every other building is tried first at the slot its id hashes to, then at the next slots, then on the next ring out, and
 * stands at the smallest radius where its whole lot clears the plaza, both avenues and every lot already placed. Buildings are placed in
 * order of their hash, never of the list, and a slot does not move when another building comes or goes: adding or removing one building
 * moves at most the few that were next to its slot. A building drawn from its floor plans (`sizes`, keyed by workspace id) keeps its real
 * footprint, and the rings widen with the largest one. The returned order is the executive first, then by name (the order of the signs).
 */
export function layoutCampus(buildings: readonly CampusBuilding[], sizes?: ReadonlyMap<string, PlannedSize>): Placement[] {
  const executive = buildings.find((b) => b.kind === 'executive');
  const others = buildings.filter((b) => b !== executive).slice().sort((a, b) => a.name.localeCompare(b.name) || a.workspace_id.localeCompare(b.workspace_id));
  const make = (b: CampusBuilding, x: number, z: number): Placement => {
    const size = SIZE[b.kind];
    const planned = sizes?.get(b.workspace_id);
    const turn = facingTurn(x, z);
    const yaw = leanOf(x, z, turn);
    if (planned) {
      return { workspaceId: b.workspace_id, x, z, width: planned.w, depth: planned.d, levels: Math.max(1, planned.levels), kind: b.kind, icon: b.icon ?? null, height: planned.height + 6, turn, yaw };
    }
    const levels = Math.max(b.kind === 'executive' ? 4 : 1, Math.min(14, b.floors ?? (b.kind === 'personal' ? 1 : 2)));
    return {
      workspaceId: b.workspace_id, x, z, width: size.w, depth: size.d, levels, kind: b.kind, icon: b.icon ?? null, turn, yaw,
      height: levels * FLOOR_HEIGHT * (b.kind === 'executive' ? 1.25 : 1) + 1.2,
    };
  };
  const all = executive ? [executive, ...others] : others;
  const dims = all.map((b) => make(b, 0, 0));
  // The executive forecourt is the one oversized lot: the ring spacing follows the other buildings.
  const regular = dims.filter((p) => p.kind !== 'executive' || sizes?.has(p.workspaceId));
  const maxW = Math.max(0, ...regular.map((p) => p.width)), maxD = Math.max(0, ...regular.map((p) => p.depth));
  const lotW = maxW + 2 * LOT.side, lotD = maxD + LOT.back + LOT.front;
  // Ring r accepts a building whose smallest clear radius is at most `limit(r)`; `slots(r)` angles share the ring.
  const step = Math.max(lotW, lotD) * 0.85;
  const limit0 = PLAZA.clear + LOT.front + maxD / 2 + 4;
  const limit = (r: number) => limit0 + r * step;
  const slots = (r: number) => Math.max(4, Math.floor((Math.PI * 2 * (limit(r) - 8)) / (lotW + 3)));
  const priority = all.slice().sort((a, b) => (a === executive ? -1 : 0) - (b === executive ? -1 : 0) || unitOf(a.workspace_id) - unitOf(b.workspace_id) || a.workspace_id.localeCompare(b.workspace_id));
  const taken: Lot[] = [];
  const at = new Map<string, Placement>();
  for (const b of priority) {
    const size = make(b, 0, 0);
    const margins = marginsOf(b.kind, !!sizes?.has(b.workspace_id));
    let placed: Placement | null = null;
    for (let ring = 0; !placed && ring < 40; ring++) {
      const m = slots(ring);
      // This building's own order of the slots of the ring: a fixed shuffle of its id, so a slot taken by another building moves only the buildings that wanted that slot.
      const order = b === executive
        ? [0]
        : Array.from({ length: m }, (_, k) => k).sort((p, q) => unitOf(`${b.workspace_id}|${ring}|${p}`) - unitOf(`${b.workspace_id}|${ring}|${q}`) || p - q);
      for (const k of ring === 0 ? order.slice(0, TRIES) : order) {
        if (placed) break;
        const angle = -Math.PI / 2 + ((k + (ring % 2) * 0.5) / m) * Math.PI * 2;
        // The radius depends on this building and the slot alone (the plaza, the avenues and the ring), never on its neighbours.
        const floor = ring === 0 ? PLAZA.clear : limit(ring - 1) + step * 0.15;
        for (let r = floor; r <= floor + 60; r += 1) {
          const x = Math.cos(angle) * r, z = Math.sin(angle) * r;
          const turn = facingTurn(x, z);
          const lot = lotOf({ x, z, width: size.width, depth: size.depth, turn, yaw: leanOf(x, z, turn) }, margins);
          if (lotBlocksCentre(lot)) continue;
          if (!taken.some((t) => lotsOverlap(lot, t, 1))) { taken.push(lot); placed = make(b, x, z); }
          break;
        }
      }
    }
    at.set(b.workspace_id, placed ?? make(b, 0, 0));
  }
  return all.map((b) => at.get(b.workspace_id)!);
}

export function campusExtent(placements: readonly Placement[]): { minX: number; maxX: number; minZ: number; maxZ: number; radius: number } {
  if (!placements.length) return { minX: -30, maxX: 30, minZ: -30, maxZ: 30, radius: 30 };
  const rects = placements.map((p) => footprintRect(p));
  const minX = Math.min(...rects.map((r) => r.x0));
  const maxX = Math.max(...rects.map((r) => r.x1));
  const minZ = Math.min(...rects.map((r) => r.z0));
  const maxZ = Math.max(...rects.map((r) => r.z1));
  const radius = Math.max(Math.abs(minX), Math.abs(maxX), Math.abs(minZ), Math.abs(maxZ));
  return { minX, maxX, minZ, maxZ, radius };
}

// ---------------------------------------------------------------- list and keyboard

/** Buildings whose name or kind matches the query (case and accent insensitive). */
export function filterBuildings(buildings: readonly CampusBuilding[], query: string): CampusBuilding[] {
  const q = query.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();
  if (!q) return [...buildings];
  return buildings.filter((b) => `${b.name} ${b.nameAr ?? ''} ${b.kind}`.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().includes(q));
}

/** The next or previous workspace id in reading order (wraps). Null when there are no buildings. */
export function stepBuilding(order: readonly string[], current: string | null, direction: 1 | -1): string | null {
  if (!order.length) return null;
  const at = current === null ? -1 : order.indexOf(current);
  if (at < 0) return direction === 1 ? order[0] : order[order.length - 1];
  return order[(at + direction + order.length) % order.length];
}

/** Buildings in the order the keyboard and the list visit them: executive first, then by name. */
export function readingOrder(buildings: readonly CampusBuilding[]): CampusBuilding[] {
  return buildings.slice().sort((a, b) => (a.kind === 'executive' ? -1 : 0) - (b.kind === 'executive' ? -1 : 0) || a.name.localeCompare(b.name) || a.workspace_id.localeCompare(b.workspace_id));
}

/** Short label such as "6 agents, 2 working", or null when presence is unavailable. */
export function agentSummary(totals: CampusTotals | null): { agents: number; working: number; meeting: number } | null {
  return totals ? { agents: totals.agents, working: totals.working, meeting: totals.meeting } : null;
}

// ---------------------------------------------------------------- adapter

export type MembershipLike = Readonly<{ workspaceId: string; workspaceName: string | null; archived?: boolean }>;

/**
 * Adapter used until `GET /api/v1/office/campus` is available: buildings are the caller's workspace
 * memberships. Presence is known only for the open workspace (`currentTotals`), so every other
 * building shows "Presence unavailable". When the campus endpoint lands, `campusFromApi` replaces it.
 */
export function campusFromMemberships(
  memberships: readonly MembershipLike[],
  scope: Readonly<{ tenantId: string; workspaceId: string }>,
  current: Readonly<{ totals: CampusTotals | null; floors: number | null; verifiedAt: string | null }>,
  now: Date = new Date(),
): Campus {
  const seen = new Set<string>();
  const rows: MembershipLike[] = [];
  for (const m of memberships) {
    if (seen.has(m.workspaceId)) continue;
    seen.add(m.workspaceId);
    rows.push(m);
  }
  if (!seen.has(scope.workspaceId)) rows.unshift({ workspaceId: scope.workspaceId, workspaceName: null });
  const buildings: CampusBuilding[] = rows.map((m) => {
    const isCurrent = m.workspaceId === scope.workspaceId;
    return {
      workspace_id: m.workspaceId, tenant_id: scope.tenantId, name: m.workspaceName ?? m.workspaceId, kind: 'business', kindKnown: false,
      icon: null, archived: m.archived === true, floors: isCurrent ? current.floors : null, accessible: !m.archived,
      totals: isCurrent ? current.totals : null, presence_verified_at: isCurrent ? current.verifiedAt : null,
    };
  });
  return { buildings, totals: campusTotals(buildings).totals, generated_at: now.toISOString() };
}

const KINDS: readonly string[] = ['executive', 'business', 'department', 'personal', 'career'];
const STATE_KEYS: readonly StatusKey[] = ['working', 'queued', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'];

/** Reads `{ total, by_state }` of the campus contract; anything missing or malformed means no presence. */
function readAgents(value: unknown): CampusTotals | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const by = row.by_state;
  if (typeof row.total !== 'number' || !Number.isFinite(row.total) || row.total < 0 || !by || typeof by !== 'object') return null;
  const out: Record<string, number> = { agents: Math.floor(row.total) };
  for (const key of STATE_KEYS) {
    const n = (by as Record<string, unknown>)[key];
    if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return null;
    out[key] = Math.floor(n);
  }
  const off = row.off_shift;
  if (typeof off === 'number' && Number.isFinite(off) && off > 0) out.off_shift = Math.floor(off);
  return out as unknown as CampusTotals;
}

const count = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.floor(v) : null);

/**
 * Reads `GET /api/v1/office/campus` (contract version 1, packages/contracts/src/officeCampus.ts)
 * defensively: a malformed building is dropped, a missing kind stays unclassified, and counts that
 * do not add up are treated as no presence, never repaired.
 */
export function campusFromApi(payload: unknown, now: Date = new Date()): Campus {
  const root = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
  const tenant = typeof root.tenant_id === 'string' ? root.tenant_id : '';
  const list = Array.isArray(root.buildings) ? root.buildings : [];
  const buildings: CampusBuilding[] = [];
  const seen = new Set<string>();
  for (const raw of list) {
    if (!raw || typeof raw !== 'object') continue;
    const r = raw as Record<string, unknown>;
    const id = typeof r.workspace_id === 'string' ? r.workspace_id : '';
    if (!id || seen.has(id) || typeof r.name !== 'string') continue;
    seen.add(id);
    const known = KINDS.includes(String(r.kind));
    const floors = Array.isArray(r.floors) ? r.floors.length : null;
    const accessible = r.accessible !== false;
    buildings.push({
      workspace_id: id, tenant_id: tenant, name: r.name,
      kind: known ? (r.kind as CampusBuildingKind) : 'business', kindKnown: known,
      icon: typeof r.icon === 'string' ? r.icon : null, role: typeof r.role === 'string' ? r.role : null,
      profileRevision: count(r.profile_revision), rooms: count(r.rooms), desks: count(r.desks), archived: r.archived === true,
      floors: floors !== null && floors > 0 ? floors : null, accessible,
      totals: accessible ? readAgents(r.agents) : null,
      presence_verified_at: typeof r.presence_verified_at === 'string' ? r.presence_verified_at : (typeof r.presence_as_of === 'string' ? r.presence_as_of : null),
    });
  }
  return { buildings, totals: campusTotals(buildings).totals, generated_at: typeof root.generated_at === 'string' ? root.generated_at : now.toISOString() };
}
