import type { Page, Route } from '@playwright/test';
import { mockOrgChart } from './orgChartApi';
import { mockLayoutHistory, type HistoryMock, type HistoryMockOptions } from './hqLayoutHistoryApi';

// A stateful mock of the office builder API (docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md, "Office
// builder API"). It enforces the same rules as the server for the paths the specs use: revision
// checks, overlap, bounds, desk inside room, empty-only deletes. Names are plainly fake test data.
type Json = Record<string, unknown>;
const stamp = '2026-10-25T08:00:00Z';
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };
const base = (id: string, revision = 1) => ({ id, revision, ...scope, created_at: stamp, updated_at: stamp });

export interface BuilderMockOptions extends HistoryMockOptions {
  role?: 'owner' | 'member' | 'viewer';
  /** Every write answers 403, like a caller without workspace:manage. */
  forbidWrites?: boolean;
  /** The office service is down: reads answer 503. */
  down?: boolean;
  /** Extra rooms placed in a grid (for the large-floor spec). */
  extraRooms?: number;
  /** Desk 2 is an older night seat (a shift, no working hours of its own), as a build made before the working hours has. */
  olderSeat?: boolean;
  empty?: boolean;
  /** The building starts with this logo (a PNG), as if the owner had uploaded it. */
  withLogo?: Buffer;
}

export interface BuilderMock {
  requests: { method: string; path: string; body: Json | null }[];
  rooms: Map<string, Json>;
  desks: Map<string, Json>;
  /** The layout history and template mock (docs/ANUM_MINE_LAYOUT_HISTORY.md). */
  history: HistoryMock;
  floors: Map<string, Json>;
  /** Simulates another editor: bumps a room's revision (and optionally moves it) behind the page's back. */
  touchRoom(id: string, patch?: Json): void;
  /** The stored logo bytes by building id (what the logo route serves). */
  logos: Map<string, Buffer>;
  /** Simulates another editor: bumps a building's revision behind the page's back. */
  touchBuilding(id: string): void;
}

const TEMPLATES = [
  { id: 'open_office', name: 'Open office', name_ar: 'مكتب مفتوح', kind: 'office', capacity: 12, width: 12, depth: 8, door_side: 'south', color: 'forest', equipment: [{ kind: 'monitor', quantity: 12 }], desks: 12 },
  { id: 'meeting_room', name: 'Meeting room', name_ar: 'غرفة اجتماعات', kind: 'meeting', capacity: 8, width: 6, depth: 5, door_side: 'south', color: 'sapphire', equipment: [{ kind: 'display', quantity: 1 }, { kind: 'whiteboard', quantity: 1 }], desks: 0 },
  { id: 'reception', name: 'Reception', name_ar: 'الاستقبال', kind: 'reception', capacity: 3, width: 6, depth: 4, door_side: 'south', color: 'brass', equipment: [{ kind: 'display', quantity: 1 }], desks: 0 },
  { id: 'warehouse', name: 'Warehouse', name_ar: 'مستودع', kind: 'warehouse', capacity: 8, width: 16, depth: 12, door_side: 'south', color: 'slate', equipment: [{ kind: 'shelving', quantity: 10 }], desks: 4 },
  { id: 'workshop', name: 'Workshop', name_ar: 'ورشة عمل', kind: 'workshop', capacity: 6, width: 10, depth: 8, door_side: 'east', color: 'sand', equipment: [{ kind: 'workbench', quantity: 4 }], desks: 4 },
];

function room(id: string, name: string, kind: string, x: number | null, z: number | null, w: number, d: number, extra: Json = {}): Json {
  return {
    ...base(id), floor_id: 'floor-1', name, kind, capacity: 8, scene_x: x, scene_z: z, scene_width: x == null ? null : w, scene_depth: x == null ? null : d,
    rotation: 0, door_side: 'south', description: '', notes: '', color: null, name_ar: null, equipment: [], team_department: null, ...extra,
  };
}
function desk(id: string, roomId: string, label: string, x: number | null, z: number | null, agent: string | null = null): Json {
  return { ...base(id), room_id: roomId, label, agent_id: agent, pos_x: x, pos_z: z };
}

const issue = (location: string, type: string, message: string) => ({ location, type, message });
function error(route: Route, status: number, message: string, details: Json[] = []) {
  return route.fulfill({
    status, contentType: 'application/json',
    body: JSON.stringify({ error: { code: status === 409 ? 'conflict' : status === 403 ? 'forbidden' : status === 404 ? 'not_found' : 'validation_error', message, details, correlation_id: 'corr_example' } }),
  });
}

const overlaps = (a: Json, b: Json) =>
  Math.min(Number(a.x) + Number(a.w), Number(b.x) + Number(b.w)) - Math.max(Number(a.x), Number(b.x)) > 1e-6
  && Math.min(Number(a.z) + Number(a.d), Number(b.z) + Number(b.d)) - Math.max(Number(a.z), Number(b.z)) > 1e-6;

export async function mockOfficeBuilder(page: Page, options: BuilderMockOptions = {}): Promise<BuilderMock> {
  const floors = new Map<string, Json>([['floor-1', { ...base('floor-1'), building_id: 'building-1', level: 0, name: 'Ground floor', name_ar: null, width_m: 60, depth_m: 40 }]]);
  const buildings = new Map<string, Json>([['building-1', { ...base('building-1'), name: 'Dubai HQ', name_ar: null, timezone: 'Asia/Dubai', latitude: null, longitude: null }]]);
  const rooms = new Map<string, Json>();
  const desks = new Map<string, Json>();
  if (!options.empty) {
    rooms.set('r-sales', room('r-sales', 'Sales room', 'office', 2, 2, 12, 8, { color: 'forest', team_department: 'sales', description: 'Sales team', equipment: [{ kind: 'monitor', quantity: 3 }] }));
    rooms.set('r-meet', room('r-meet', 'Board room', 'meeting', 20, 2, 6, 5, { color: 'sapphire' }));
    rooms.set('r-recep', room('r-recep', 'Front desk', 'reception', 30, 2, 6, 4));
    rooms.set('r-spare', room('r-spare', 'Spare room', 'office', null, null, 6, 5));
    desks.set('d-1', desk('d-1', 'r-sales', 'Desk 1', 1.5, 1.5, 'agent_fleet'));
    desks.set('d-2', { ...desk('d-2', 'r-sales', 'Desk 2', 4.5, 1.5), ...(options.olderSeat ? { shift: 'night' } : {}) });
    desks.set('d-3', desk('d-3', 'r-sales', 'Desk 3', null, null));
    for (let i = 0; i < (options.extraRooms ?? 0); i += 1) {
      rooms.set(`r-x${i}`, room(`r-x${i}`, `Bulk room ${i + 1}`, 'office', 40 + (i % 20) * 7, 2 + Math.floor(i / 20) * 6, 6, 5));
    }
  }
  if (options.extraRooms) floors.set('floor-1', { ...floors.get('floor-1'), width_m: 200, depth_m: 200 });

  const logos = new Map<string, Buffer>();
  const fakeSha = (bytes: Buffer) => { let h = 0; for (const b of bytes) h = (h * 31 + b) >>> 0; return h.toString(16).padStart(8, '0').repeat(8); };
  if (options.withLogo) { logos.set('building-1', options.withLogo); buildings.set('building-1', { ...buildings.get('building-1'), logo_sha256: fakeSha(options.withLogo) }); }
  const mock: BuilderMock = {
    logos, touchBuilding(id) { const b = buildings.get(id); if (b) buildings.set(id, { ...b, revision: Number(b.revision) + 1 }); },
    requests: [], rooms, desks, floors, history: undefined as unknown as HistoryMock,
    touchRoom(id, patch = {}) {
      const current = rooms.get(id);
      if (current) rooms.set(id, { ...current, ...patch, revision: Number(current.revision) + 1 });
    },
  };

  const roomRect = (r: Json) => ({ x: Number(r.scene_x), z: Number(r.scene_z), w: Number(r.scene_width), d: Number(r.scene_depth) });

  await mockOrgChart(page, { role: options.role ?? 'owner' });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office-spaces\/.*/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/office-spaces', '');
    const method = request.method();
    const isLogo = /\/buildings\/[^/]+\/logo$/.test(path);
    const body = !isLogo && request.postData() ? (JSON.parse(request.postData() as string) as Json) : null;
    mock.requests.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });

    if (options.down) return error(route, 503, 'Office persistence needs the PostgreSQL backend');
    const logoPath = path.match(/^\/buildings\/([^/]+)\/logo$/);
    if (logoPath && method === 'GET') {
      const bytes = logos.get(logoPath[1]);
      if (!bytes) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'This building has no logo.' }) });
      return route.fulfill({ status: 200, contentType: 'image/png', headers: { 'cache-control': 'private, no-cache', etag: `"${fakeSha(bytes)}"` }, body: bytes });
    }
    if (method === 'GET') {
      const list = path.match(/^\/(buildings|floors|rooms|desks)$/);
      if (list) {
        const items = list[1] === 'buildings' ? [...buildings.values()]
          : list[1] === 'floors' ? [...floors.values()] : list[1] === 'rooms' ? [...rooms.values()] : [...desks.values()];
        return json({ items, next_cursor: null });
      }
      if (path === '/templates') return json({ items: TEMPLATES, equipment_kinds: ['monitor', 'display', 'whiteboard', 'projector', 'conference_phone', 'video_conference_kit', 'printer', 'server_rack', 'network_switch', 'workbench', 'shelving', 'locker', 'charging_station', 'first_aid_kit'], colors: ['slate', 'forest', 'brass', 'sapphire', 'amethyst', 'crimson', 'teal', 'sand'], departments: ['sales', 'support', 'operations', 'finance', 'tyre_engineering', 'marketing', 'technology', 'research', 'legal', 'compliance', 'security', 'hr', 'procurement', 'custom'], units: 'metres' });
      const team = path.match(/^\/rooms\/([^/]+)\/team$/);
      if (team) {
        const r = rooms.get(team[1]);
        if (!r) return error(route, 404, 'Room not found');
        return json({ room_id: team[1], department: r.team_department, member_profiles: r.team_department ? 3 : null, assistants: r.team_department ? 2 : null });
      }
      return error(route, 404, 'Not found');
    }
    if (options.forbidWrites) return error(route, 403, 'Workspace manager permission is required');

    // ---- atomic layout save
    const layout = path.match(/^\/floors\/([^/]+)\/layout$/);
    if (layout && method === 'PUT') {
      const floor = floors.get(layout[1]);
      if (!floor) return error(route, 404, 'Floor not found');
      const items = (body?.rooms ?? []) as Json[];
      const deskItems = (body?.desks ?? []) as Json[];
      const stale: Json[] = [];
      for (const item of items) { const r = rooms.get(String(item.id)); if (r && r.revision !== item.expected_revision) stale.push(issue(`rooms.${item.id}`, 'STALE_REVISION', `Room ${r.name} changed since you loaded it`)); }
      for (const item of deskItems) { const d = desks.get(String(item.id)); if (d && d.revision !== item.expected_revision) stale.push(issue(`desks.${item.id}`, 'STALE_REVISION', `Desk ${d.label} changed since you loaded it`)); }
      if (stale.length) return error(route, 409, 'Office layout changed. Reload before retrying', stale);
      const final = new Map<string, { x: number; z: number; w: number; d: number }>();
      for (const [id, r] of rooms) if (r.scene_x != null) final.set(id, roomRect(r));
      for (const item of items) final.set(String(item.id), { x: Number(item.x), z: Number(item.z), w: Number(item.width), d: Number(item.depth) });
      const changed = new Set(items.map((i) => String(i.id)));
      const problems: Json[] = [];
      for (const id of changed) {
        const r = final.get(id) as { x: number; z: number; w: number; d: number };
        if (r.x < -1e-6 || r.z < -1e-6 || r.x + r.w > Number(floor.width_m) + 1e-6 || r.z + r.d > Number(floor.depth_m) + 1e-6) problems.push(issue(`rooms.${id}`, 'ROOM_OUT_OF_BOUNDS', `Room ${id} must stay inside the floor`));
      }
      const ids = [...final.keys()].sort();
      for (let i = 0; i < ids.length; i += 1) for (let j = i + 1; j < ids.length; j += 1) {
        if (!changed.has(ids[i]) && !changed.has(ids[j])) continue;
        const a = final.get(ids[i]) as { x: number; z: number; w: number; d: number };
        const b = final.get(ids[j]) as { x: number; z: number; w: number; d: number };
        if (overlaps(a as unknown as Json, b as unknown as Json)) problems.push(issue(`rooms.${ids[i]}`, 'ROOM_OVERLAP', `Room ${ids[i]} overlaps room ${ids[j]}`));
      }
      const posOf = new Map(deskItems.map((i) => [String(i.id), i]));
      for (const [id, d] of desks) {
        const rm = final.get(String(d.room_id));
        const p = posOf.get(id);
        const px = p ? Number(p.pos_x) : d.pos_x == null ? null : Number(d.pos_x);
        const pz = p ? Number(p.pos_z) : d.pos_z == null ? null : Number(d.pos_z);
        if (!rm || px == null || pz == null) continue;
        if (!(changed.has(String(d.room_id)) || [...posOf.keys()].some((k) => desks.get(k)?.room_id === d.room_id))) continue;
        if (px < 0 || pz < 0 || px > rm.w + 1e-6 || pz > rm.d + 1e-6) problems.push(issue(`desks.${id}`, 'DESK_OUT_OF_ROOM', `Desk ${d.label} would be outside its room`));
      }
      if (problems.length) return error(route, 422, `The layout cannot be saved: ${problems.length} problem(s)`, problems);
      const savedRooms: Json[] = [];
      const savedDesks: Json[] = [];
      for (const item of items) {
        const r = rooms.get(String(item.id)) as Json;
        const next = { ...r, scene_x: item.x, scene_z: item.z, scene_width: item.width, scene_depth: item.depth, rotation: item.rotation ?? r.rotation, door_side: item.door_side ?? r.door_side, revision: Number(r.revision) + 1 };
        rooms.set(String(item.id), next); savedRooms.push(next);
      }
      for (const item of deskItems) {
        const d = desks.get(String(item.id)) as Json;
        const next = { ...d, pos_x: item.pos_x, pos_z: item.pos_z, revision: Number(d.revision) + 1 };
        desks.set(String(item.id), next); savedDesks.push(next);
      }
      return json({ floor_id: layout[1], rooms: savedRooms, desks: savedDesks });
    }

    // ---- creates
    if (path === '/rooms/from-template' && method === 'POST') {
      const tpl = TEMPLATES.find((t) => t.id === body?.template_id);
      if (!tpl) return error(route, 422, 'Unknown template');
      const name = (body?.name as string) ?? tpl.name;
      if ([...rooms.values()].some((r) => String(r.name).toLowerCase() === name.toLowerCase())) return error(route, 409, 'A room with that name already exists on this floor', [issue('', 'ROOM_NAME_TAKEN', 'A room with that name already exists on this floor')]);
      const created = room(String(body?.id), name, tpl.kind, Number(body?.x), Number(body?.z), tpl.width, tpl.depth, { capacity: tpl.capacity, color: tpl.color, door_side: tpl.door_side, equipment: tpl.equipment, name_ar: body?.name ? null : tpl.name_ar });
      rooms.set(String(body?.id), created);
      const made: Json[] = [];
      if (body?.with_desks && tpl.desks) for (let i = 0; i < tpl.desks; i += 1) { const d = desk(`${body?.id}-d${i + 1}`, String(body?.id), `Desk ${i + 1}`, 0.75 + (i % 7) * 1.5, 0.75 + Math.floor(i / 7) * 1.5); desks.set(String(d.id), d); made.push(d); }
      return json({ room: created, desks: made }, 201);
    }
    if (path === '/rooms' && method === 'POST') {
      const created = room(String(body?.id), String(body?.name), String(body?.kind), null, null, 6, 5, { capacity: body?.capacity });
      rooms.set(String(body?.id), created);
      return json(created, 201);
    }
    const copy = path.match(/^\/rooms\/([^/]+)\/copy$/);
    if (copy && method === 'POST') {
      const source = rooms.get(copy[1]);
      if (!source) return error(route, 404, 'Room not found');
      const created = { ...source, ...base(String(body?.id)), name: body?.name ?? `${source.name} copy`, scene_x: body?.x ?? source.scene_x, scene_z: body?.z ?? source.scene_z };
      rooms.set(String(body?.id), created);
      const made: Json[] = [];
      if (body?.copy_desks) for (const d of [...desks.values()].filter((x) => x.room_id === copy[1])) { const c = { ...desk(`${body?.id}-d${made.length + 1}`, String(body?.id), String(d.label), d.pos_x as number | null, d.pos_z as number | null), shift: d.shift ?? null, seat_order: d.seat_order ?? null, facing: d.facing ?? null, seat_kind: d.seat_kind ?? null, table_group: d.table_group ?? null }; desks.set(String(c.id), c); made.push(c); }
      return json({ room: created, desks: made }, 201);
    }
    if (path === '/desks' && method === 'POST') {
      const created = desk(String(body?.id), String(body?.room_id), String(body?.label), null, null);
      desks.set(String(body?.id), created);
      return json(created, 201);
    }

    // ---- the building sign: logo upload and removal (mirrors services/api office_logo_api.py)
    if (logoPath && (method === 'PUT' || method === 'DELETE')) {
      const current = buildings.get(logoPath[1]);
      if (!current) return error(route, 404, 'Office record not found in this workspace');
      if (url.searchParams.get('input_mode') !== 'click') return error(route, 403, "Confirm this on screen with a click. A spoken or typed answer cannot change a building's logo.");
      if (String(current.revision) !== url.searchParams.get('expected_revision')) return error(route, 409, 'Office layout changed. Reload before retrying', [issue(`buildings.${logoPath[1]}`, 'STALE_REVISION', 'changed')]);
      if (method === 'DELETE') {
        if (!logos.has(logoPath[1])) return error(route, 404, 'This building has no logo.');
        logos.delete(logoPath[1]);
        const next = { ...current, logo_sha256: null, revision: Number(current.revision) + 1 };
        buildings.set(logoPath[1], next);
        return json(next);
      }
      const raw = request.postDataBuffer() ?? Buffer.alloc(0);
      const refuse = (status: number, message: string) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ error: { code: status === 413 ? 'payload_too_large' : 'bad_request', message, details: [], correlation_id: 'corr_example' } }) });
      if (raw.length === 0) return refuse(422, 'The file is empty. Choose a logo picture.');
      if (raw.length > 1_048_576) return refuse(413, 'Request body exceeds the 1048576 byte limit');
      const head = raw.subarray(0, 512).toString('latin1').toLowerCase();
      if (head.includes('<svg')) return refuse(415, 'SVG logos are not accepted because they can carry script. Export the logo as PNG, JPEG or WebP.');
      if (!raw.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return refuse(415, 'This file is not a PNG, JPEG or WebP picture. Choose a logo in one of those formats.');
      logos.set(logoPath[1], raw);
      const next = { ...current, logo_sha256: fakeSha(raw), revision: Number(current.revision) + 1 };
      buildings.set(logoPath[1], next);
      return json(next);
    }

    // ---- patches
    const patch = path.match(/^\/(rooms|desks|floors|buildings)\/([^/]+)(\/assignment)?$/);
    if (patch && method === 'PATCH') {
      const store = patch[1] === 'rooms' ? rooms : patch[1] === 'desks' ? desks : patch[1] === 'floors' ? floors : buildings;
      const current = store?.get(patch[2]);
      if (!store || !current) return error(route, 404, 'Not found');
      if (current.revision !== body?.expected_revision) return error(route, 409, 'Office layout changed. Reload before retrying', [issue(`${patch[1]}.${patch[2]}`, 'STALE_REVISION', 'changed')]);
      const { expected_revision: _ignored, ...changes } = body as Json;
      if (patch[3]) { const next = { ...current, agent_id: body?.agent_id, revision: Number(current.revision) + 1 }; store.set(patch[2], next); return json(next); }
      const next = { ...current, ...changes, revision: Number(current.revision) + 1 };
      store.set(patch[2], next);
      return json(next);
    }

    // ---- deletes
    const del = path.match(/^\/(rooms|desks|floors|buildings)\/([^/]+)$/);
    if (del && method === 'DELETE') {
      if (del[1] === 'rooms') {
        const owned = [...desks.values()].filter((d) => d.room_id === del[2]);
        if (owned.length) return error(route, 409, `This room still has ${owned.length} desk(s). Delete the desks first`, owned.map((d) => issue(`desks.${d.id}`, 'DESK_PRESENT', `Desk ${d.label}`)));
        rooms.delete(del[2]);
      } else if (del[1] === 'desks') desks.delete(del[2]);
      else if (del[1] === 'floors') {
        const owned = [...rooms.values()].filter((r) => r.floor_id === del[2]);
        if (owned.length) return error(route, 409, `This floor still has ${owned.length} room(s). Delete the rooms first`, owned.map((r) => issue(`rooms.${r.id}`, 'ROOM_PRESENT', `Room ${r.name} (0 desk(s))`)));
        floors.delete(del[2]);
      } else return error(route, 409, 'This building still has floors', [issue('floors.floor-1', 'FLOOR_PRESENT', 'Floor Ground floor (level 0)')]);
      return route.fulfill({ status: 204 });
    }
    return error(route, 404, 'Not found');
  });
  // Registered last: Playwright runs the newest matching route first, and the org chart mock owns http://localhost:8000/**.
  mock.history = await mockLayoutHistory(page, rooms, desks, floors, options, options.role ?? 'owner');
  return mock;
}

export interface OpenBuilderOptions extends BuilderMockOptions { locale?: 'en' | 'ar'; reducedMotion?: boolean }

/** Opens the owner's visual builder from the headquarters: Manage spaces, then Open visual builder. */
export async function openBuilder(page: Page, options: OpenBuilderOptions = {}): Promise<BuilderMock> {
  await page.addInitScript((locale) => {
    try { if (locale) localStorage.setItem('anum.locale', locale); } catch { /* storage blocked */ }
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
      if (/webgl/i.test(kind)) return null;
      return (original as (...a: unknown[]) => unknown).call(this, kind, ...rest) as never;
    } as typeof original;
  }, options.locale);
  if (options.reducedMotion) await page.emulateMedia({ reducedMotion: 'reduce' });
  const mock = await mockOfficeBuilder(page, options);
  await page.goto('/#office-preview');
  // The headquarters keeps its tools in one menu.
  await page.locator('.hqLive').getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
  await page.locator('.hqLive').getByRole('button', { name: /Manage spaces|إدارة المساحات/ }).first().click();
  await page.getByRole('button', { name: /Open visual builder|فتح المنشئ المرئي/ }).click();
  return mock;
}
