/**
 * Campus view model: one building per authorised workspace.
 *
 * Data truth (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md): a building exists only when the person is
 * authorised for that workspace; its counts exist only when a presence record backs them, otherwise
 * `totals` is null and the screen says "Presence unavailable". Nothing here invents a number.
 * Pure functions, no DOM: the unit runner covers layout, totals and keyboard order.
 */
export type CampusBuildingKind = 'executive' | 'business' | 'department' | 'personal';

export type CampusTotals = Readonly<{
  agents: number; working: number; meeting: number; waiting_approval: number;
  blocked: number; idle: number; paused: number; unknown: number;
}>;

export type CampusBuilding = Readonly<{
  workspace_id: string;
  tenant_id: string;
  name: string;
  kind: CampusBuildingKind;
  icon: string | null;
  /** Floor count from the office registry, or null when it is not known without opening the workspace. */
  floors: number | null;
  accessible: boolean;
  /** Presence counts, or null when no verified presence exists ("Presence unavailable"). */
  totals: CampusTotals | null;
  presence_verified_at: string | null;
}>;

export type Campus = Readonly<{
  buildings: readonly CampusBuilding[];
  totals: CampusTotals | null;
  generated_at: string;
}>;

export const EMPTY_TOTALS: CampusTotals = {
  agents: 0, working: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0,
};

export function addTotals(a: CampusTotals, b: CampusTotals): CampusTotals {
  return {
    agents: a.agents + b.agents, working: a.working + b.working, meeting: a.meeting + b.meeting,
    waiting_approval: a.waiting_approval + b.waiting_approval, blocked: a.blocked + b.blocked,
    idle: a.idle + b.idle, paused: a.paused + b.paused, unknown: a.unknown + b.unknown,
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

export type StatusKey = 'working' | 'meeting' | 'waiting_approval' | 'blocked' | 'idle' | 'paused' | 'unknown';
export const STATUS_ORDER: readonly StatusKey[] = ['working', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'];

/** Tone per status, shared by the dot, the donut and the agent pill. */
export const STATUS_TONE: Readonly<Record<StatusKey, 'ok' | 'meeting' | 'warn' | 'stop' | 'info' | 'muted' | 'faint'>> = {
  working: 'ok', meeting: 'meeting', waiting_approval: 'warn', blocked: 'stop', idle: 'info', paused: 'muted', unknown: 'faint',
};

/** The dominant status of a building: what the status dot on its label shows. */
export function buildingStatus(b: CampusBuilding): StatusKey | 'unavailable' | 'empty' {
  if (!b.totals) return 'unavailable';
  if (b.totals.agents === 0) return 'empty';
  if (b.totals.blocked > 0) return 'blocked';
  if (b.totals.waiting_approval > 0) return 'waiting_approval';
  if (b.totals.working > 0) return 'working';
  if (b.totals.meeting > 0) return 'meeting';
  if (b.totals.idle > 0) return 'idle';
  if (b.totals.paused > 0) return 'paused';
  return 'unknown';
}

export type DonutArc = Readonly<{ key: StatusKey; value: number; start: number; end: number }>;

/** Donut slices in radians, clockwise from twelve o'clock. Zero-valued statuses are left out. */
export function donutArcs(totals: CampusTotals): DonutArc[] {
  const sum = STATUS_ORDER.reduce((s, k) => s + totals[k], 0);
  if (sum <= 0) return [];
  const arcs: DonutArc[] = [];
  let angle = 0;
  for (const key of STATUS_ORDER) {
    const value = totals[key];
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
}>;

export const FLOOR_HEIGHT = 3.4;
const SIZE: Record<CampusBuildingKind, { w: number; d: number }> = {
  executive: { w: 15, d: 15 }, business: { w: 17, d: 14 }, department: { w: 14, d: 12 }, personal: { w: 11, d: 10 },
};
const RING_CAPACITY = [7, 12, 18, 24];
const RING_RADIUS = [38, 66, 94, 122];

/** Deterministic placement: the executive tower in the middle, the rest on rings ordered by name. */
export function layoutCampus(buildings: readonly CampusBuilding[]): Placement[] {
  const executive = buildings.find((b) => b.kind === 'executive');
  const others = buildings.filter((b) => b !== executive).slice().sort((a, b) => a.name.localeCompare(b.name) || a.workspace_id.localeCompare(b.workspace_id));
  const out: Placement[] = [];
  const make = (b: CampusBuilding, x: number, z: number): Placement => {
    const size = SIZE[b.kind];
    const levels = Math.max(b.kind === 'executive' ? 4 : 1, Math.min(14, b.floors ?? (b.kind === 'personal' ? 1 : 2)));
    return {
      workspaceId: b.workspace_id, x, z, width: size.w, depth: size.d, levels, kind: b.kind,
      height: levels * FLOOR_HEIGHT * (b.kind === 'executive' ? 1.25 : 1) + 1.2,
    };
  };
  if (executive) out.push(make(executive, 0, 0));
  let index = 0;
  for (let ring = 0; index < others.length; ring++) {
    const capacity = RING_CAPACITY[Math.min(ring, RING_CAPACITY.length - 1)];
    const radius = RING_RADIUS[Math.min(ring, RING_RADIUS.length - 1)] + Math.max(0, ring - 3) * 28;
    const count = Math.min(capacity, others.length - index);
    const slots = ring === 0 && count < capacity ? Math.max(count, 3) : count;
    for (let i = 0; i < count; i++) {
      const angle = Math.PI * 0.75 + ((i + (slots > count ? 0.5 : 0)) / slots) * Math.PI * 2;
      out.push(make(others[index + i], Math.cos(angle) * radius, Math.sin(angle) * radius));
    }
    index += count;
  }
  return out;
}

export function campusExtent(placements: readonly Placement[]): { minX: number; maxX: number; minZ: number; maxZ: number; radius: number } {
  if (!placements.length) return { minX: -30, maxX: 30, minZ: -30, maxZ: 30, radius: 30 };
  const minX = Math.min(...placements.map((p) => p.x - p.width / 2));
  const maxX = Math.max(...placements.map((p) => p.x + p.width / 2));
  const minZ = Math.min(...placements.map((p) => p.z - p.depth / 2));
  const maxZ = Math.max(...placements.map((p) => p.z + p.depth / 2));
  const radius = Math.max(Math.abs(minX), Math.abs(maxX), Math.abs(minZ), Math.abs(maxZ));
  return { minX, maxX, minZ, maxZ, radius };
}

// ---------------------------------------------------------------- list and keyboard

/** Buildings whose name or kind matches the query (case and accent insensitive). */
export function filterBuildings(buildings: readonly CampusBuilding[], query: string): CampusBuilding[] {
  const q = query.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();
  if (!q) return [...buildings];
  return buildings.filter((b) => `${b.name} ${b.kind}`.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().includes(q));
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
      workspace_id: m.workspaceId, tenant_id: scope.tenantId, name: m.workspaceName ?? m.workspaceId, kind: 'business',
      icon: null, floors: isCurrent ? current.floors : null, accessible: !m.archived,
      totals: isCurrent ? current.totals : null, presence_verified_at: isCurrent ? current.verifiedAt : null,
    };
  });
  return { buildings, totals: campusTotals(buildings).totals, generated_at: now.toISOString() };
}

const KINDS: readonly string[] = ['executive', 'business', 'department', 'personal'];
const COUNT_KEYS: readonly (keyof CampusTotals)[] = ['agents', 'working', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'];

function readTotals(value: unknown): CampusTotals | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const out: Record<string, number> = {};
  for (const key of COUNT_KEYS) {
    const n = row[key];
    if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return null;
    out[key] = Math.floor(n);
  }
  return out as unknown as CampusTotals;
}

/** Reads the campus endpoint's JSON defensively: a malformed building is dropped, not guessed. */
export function campusFromApi(payload: unknown, now: Date = new Date()): Campus {
  const root = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
  const list = Array.isArray(root.buildings) ? root.buildings : [];
  const buildings: CampusBuilding[] = [];
  const seen = new Set<string>();
  for (const raw of list) {
    if (!raw || typeof raw !== 'object') continue;
    const r = raw as Record<string, unknown>;
    const id = typeof r.workspace_id === 'string' ? r.workspace_id : '';
    if (!id || seen.has(id) || typeof r.tenant_id !== 'string' || typeof r.name !== 'string') continue;
    seen.add(id);
    const floors = typeof r.floors === 'number' && Number.isFinite(r.floors) && r.floors > 0 ? Math.floor(r.floors) : null;
    buildings.push({
      workspace_id: id, tenant_id: r.tenant_id, name: r.name,
      kind: KINDS.includes(String(r.kind)) ? (r.kind as CampusBuildingKind) : 'business',
      icon: typeof r.icon === 'string' ? r.icon : null, floors, accessible: r.accessible !== false,
      totals: readTotals(r.totals),
      presence_verified_at: typeof r.presence_verified_at === 'string' ? r.presence_verified_at : null,
    });
  }
  const totals = readTotals(root.totals) ?? campusTotals(buildings).totals;
  return { buildings, totals, generated_at: typeof root.generated_at === 'string' ? root.generated_at : now.toISOString() };
}
