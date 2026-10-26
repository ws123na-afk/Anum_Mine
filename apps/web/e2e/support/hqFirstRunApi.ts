import type { Page, Route } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { campusFixture } from './hqCampusApi';

// A stateful mock of the office registry plus the starter campus API for the first-run specs
// (docs/ANUM_MINE_STARTER_CAMPUS.md). It starts EMPTY (no buildings) and a confirm fills the registry with the real master
// list (generated from services/api/anum_api/office_starter_campus.py: 8 buildings, 18 floors, 62 rooms, 220 desks), at the
// positions the API's packer gives them, so the scene is drawn from the same geometry the real server stores.
// Regenerate `starterCampusMaster.json` with the snippet in docs/ANUM_MINE_STARTER_CAMPUS.md ("Fixture for the browser tests").
const TENANT = 'tenant_local';
const WS = 'workspace_foundation';
const STAMP = '2026-10-20T08:00:00Z';

interface MasterRoom { key: string; en: string; ar: string; kind: string; x: number; z: number; w: number; d: number; cap: number; desks: number; dept: string | null }
interface MasterFloor { level: number; en: string; ar: string; w: number; d: number; rooms: MasterRoom[] }
interface MasterBuilding { number: number; key: string; en: string; ar: string; floors: MasterFloor[] }
const CAMPUS: MasterBuilding[] = JSON.parse(readFileSync(new URL('./starterCampusMaster.json', import.meta.url), 'utf8'));
const zero = () => ({ buildings: 0, floors: 0, rooms: 0, desks: 0 });
const bid = (b: MasterBuilding) => `campus-${String(b.number).padStart(2, '0')}-${b.key}`;
const base = (id: string) => ({ id, revision: 1, tenant_id: TENANT, workspace_id: WS, created_at: STAMP, updated_at: STAMP });

export const MASTER_TOTALS = { buildings: CAMPUS.length, floors: CAMPUS.reduce((n, b) => n + b.floors.length, 0), rooms: CAMPUS.reduce((n, b) => n + b.floors.reduce((m, f) => m + f.rooms.length, 0), 0), desks: CAMPUS.reduce((n, b) => n + b.floors.reduce((m, f) => m + f.rooms.reduce((k, r) => k + r.desks, 0), 0), 0) };

function countsOf(b: MasterBuilding) {
  const c = zero();
  c.buildings = 1; c.floors = b.floors.length;
  for (const f of b.floors) for (const r of f.rooms) { c.rooms += 1; c.desks += r.desks; }
  return c;
}

export interface FirstRunMock {
  requests: { method: string; path: string; body: Record<string, unknown> | null }[];
  created: () => string[];
  setCanConfirm(v: boolean): void;
  setPreviewFailure(status: number | null): void;
  setConfirmFailure(status: number | null, detail?: string): void;
  /** The next confirm builds every building except the one at this index (it fails and leaves nothing); null builds all. */
  setFailBuilding(index: number | null): void;
  /** Builds the whole campus now, as another browser would (the page learns of it through an event or a refetch). */
  createAllNow(): void;
  /** The server's registry holds this many buildings right now. */
  buildingCount(): number;
  /** Holds the plan answers until this promise settles (the loading state). */
  holdPreview(p: Promise<void> | null): void;
  /** Makes the next confirm answer 409 once (the records moved since the preview). */
  staleOnce(): void;
}

export async function mockFirstRun(page: Page, options: { prebuilt?: boolean } = {}): Promise<FirstRunMock> {
  const done = new Set<string>(options.prebuilt ? CAMPUS.map((b) => b.key) : []);
  let canConfirm = true; let previewFail: number | null = null; let confirmFail: { status: number; detail: string } | null = null;
  let failIndex: number | null = null; let stale = false; let hold: Promise<void> | null = null;
  const mock: FirstRunMock = {
    requests: [],
    created: () => [...done],
    setCanConfirm(v) { canConfirm = v; }, setPreviewFailure(s) { previewFail = s; },
    setConfirmFailure(status, detail = 'failed') { confirmFail = status === null ? null : { status, detail }; },
    setFailBuilding(i) { failIndex = i; },
    createAllNow() { for (const b of CAMPUS) done.add(b.key); },
    buildingCount: () => done.size,
    staleOnce() { stale = true; },
    holdPreview(p) { hold = p; },
  };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

  const built = () => CAMPUS.filter((b) => done.has(b.key));
  const registry = {
    buildings: () => built().map((b) => ({ ...base(bid(b)), name: b.en, timezone: 'Asia/Dubai', latitude: null, longitude: null })),
    floors: () => built().flatMap((b) => b.floors.map((f) => ({ ...base(`${bid(b)}-l${f.level}`), building_id: bid(b), level: f.level, name: f.en, width_m: f.w, depth_m: f.d }))),
    rooms: () => built().flatMap((b) => b.floors.flatMap((f) => f.rooms.map((r) => ({
      ...base(`${bid(b)}-${r.key}`.slice(0, 80)), floor_id: `${bid(b)}-l${f.level}`, name: r.en, name_ar: r.ar, kind: r.kind, capacity: r.cap,
      scene_x: r.x, scene_z: r.z, scene_width: r.w, scene_depth: r.d, team_department: r.dept,
    })))),
    desks: () => built().flatMap((b) => b.floors.flatMap((f) => f.rooms.flatMap((r) => Array.from({ length: r.desks }, (_, i) => ({
      ...base(`${`${bid(b)}-${r.key}`.slice(0, 72)}-d${i + 1}`), room_id: `${bid(b)}-${r.key}`.slice(0, 80), label: `Desk ${i + 1}`, agent_id: null,
    }))))),
  };
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office-spaces\/(buildings|floors|rooms|desks)(\?.*)?$/, async (route: Route) => {
    const kind = new URL(route.request().url()).pathname.split('/').pop() as keyof typeof registry;
    return json(route, { items: registry[kind](), next_cursor: null });
  });

  // A fresh workspace is one building on the campus with no office layout behind it.
  await page.route('http://localhost:8000/api/v1/office/campus', (route: Route) => {
    const own = { ...campusFixture.buildings[0], floors: [], rooms: 0, desks: 0, office_buildings: 0, agents: null, presence_verified_at: null };
    return json(route, { ...campusFixture, buildings: [own] });
  });

  const plan = (b: MasterBuilding, selected: boolean) => {
    const exists = done.has(b.key);
    const id = bid(b);
    const create = exists ? zero() : countsOf(b);
    const skip = exists ? countsOf(b) : zero();
    return {
      key: b.key, number: b.number, id, name: { en: b.en, ar: b.ar }, timezone: 'Asia/Dubai', selected,
      action: exists ? 'skip' : 'create', reason: exists ? 'exists' : null, status: exists ? 'complete' : 'new',
      floors: b.floors.map((f) => ({
        key: `l${f.level}`, id: `${id}-l${f.level}`, level: f.level, name: { en: f.en, ar: f.ar }, width_m: f.w, depth_m: f.d,
        action: exists ? 'skip' : 'create', reason: exists ? 'exists' : null,
        rooms: f.rooms.map((r) => {
          const rid = `${id}-${r.key}`.slice(0, 80);
          return {
            key: r.key, id: rid, name: { en: r.en, ar: r.ar }, kind: r.kind, capacity: r.cap, width: r.w, depth: r.d, door_side: 'south', color: 'slate',
            team_department: r.dept, equipment: [], desk_count: r.desks, action: exists ? 'skip' : 'create', reason: exists ? 'exists' : null,
            x: r.x, z: r.z, rotation: 0,
            desks: exists ? [] : Array.from({ length: r.desks }, (_, i) => ({ id: `${rid.slice(0, 72)}-d${i + 1}`, label: `Desk ${i + 1}`, pos_x: 1, pos_z: 1, action: 'create' as const })),
          };
        }),
      })),
      counts: { create, skip, update: { room_departments: 0 } },
    };
  };
  const hashFor = (keys: string[]) => {
    const seed = `${[...done].sort().join('')}|${keys.join('')}`;
    let h = 0; for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return h.toString(16).padStart(8, '0').repeat(8);
  };

  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/starter-campus\/.*/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/office/starter-campus', '');
    const raw = request.postData();
    mock.requests.push({ method: request.method(), path: path + url.search, body: raw ? JSON.parse(raw) : null });
    if (path === '/preview') {
      if (hold) await hold;
      if (previewFail !== null) return json(route, { detail: previewFail === 403 ? 'Forbidden' : 'The service failed.' }, previewFail);
      const asked = url.searchParams.get('buildings');
      const selected = asked === null ? CAMPUS.map((b) => b.key) : asked.split(',');
      const plans = CAMPUS.map((b) => plan(b, selected.includes(b.key)));
      const totals = { create: zero(), skip: zero(), update: { room_departments: 0 } };
      for (const p of plans.filter((x) => x.selected)) for (const side of ['create', 'skip'] as const) for (const k of Object.keys(zero()) as (keyof ReturnType<typeof zero>)[]) totals[side][k] += p.counts[side][k];
      return json(route, { version: 1, catalog_version: 1, workspace_id: WS, timezone: 'Asia/Dubai', selected, payload_hash: hashFor(selected), can_confirm: canConfirm,
        buildings: plans, totals, rules: { creates_agents: false, seats_agents: false, changes_existing: false, fills_empty_departments: true } });
    }
    if (path === '/confirm') {
      if (confirmFail) return json(route, { detail: confirmFail.detail }, confirmFail.status);
      const body = mock.requests[mock.requests.length - 1].body as { buildings: string[]; payload_hash: string; input_mode: string };
      if (body.input_mode !== 'click') return json(route, { detail: 'A person must confirm this by clicking.' }, 403);
      if (stale || body.payload_hash !== hashFor(body.buildings)) { stale = false; return json(route, { detail: 'The campus changed since you opened it. Review it again.' }, 409); }
      const items = body.buildings.map((k, i) => {
        const b = CAMPUS.find((x) => x.key === k)!;
        const exists = done.has(k);
        const failed = !exists && failIndex === i;
        if (!exists && !failed) done.add(k);
        return { key: k, id: bid(b), name: { en: b.en, ar: b.ar }, status: exists ? 'already_exists' : failed ? 'failed' : 'created',
          created: exists || failed ? zero() : countsOf(b), skipped: null, departments_set: 0,
          message: failed ? 'A desk with that id already exists. Nothing was created for this building.' : 'Created.' };
      });
      const totals = zero();
      for (const it of items) for (const k of Object.keys(zero()) as (keyof ReturnType<typeof zero>)[]) totals[k] += it.created[k];
      const made = items.some((i) => i.status === 'created'); const bad = items.some((i) => i.status === 'failed');
      return json(route, { version: 1, outcome: made ? (bad ? 'partial' : 'created') : bad ? 'failed' : 'already_set_up', payload_hash: 'a'.repeat(64), timezone: 'Asia/Dubai', buildings: items, totals, departments_set: 0, agents_created: 0 });
    }
    return json(route, { detail: 'Not found' }, 404);
  });
  return mock;
}
