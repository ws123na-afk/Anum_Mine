import type { Page, Route } from '@playwright/test';
import { readFileSync } from 'node:fs';

// A stateful mock of the Green Concrete company (docs/ANUM_MINE_GREEN_CONCRETE.md): the registry the planner makes (building, floor,
// command room, ten seats with each person's working hours), the ten agents, their presence and the work assigned to them, and the preview/confirm of "Add Green Concrete".
// The names, roles and seats are the planner's own output (test/fixtures/greenConcrete.json, kept equal to it by a Python test).
const stamp = '2026-10-20T08:00:00Z';
const TENANT = 'tenant_local';
const WORKSPACE = 'workspace_foundation';

interface Fixture {
  building: Record<string, unknown>; floor: Record<string, unknown>; room: Record<string, unknown>; desks: Record<string, unknown>[];
  people: { key: string; name: string; role: string; role_ar: string; seat_kind: 'team' | 'manager'; work_start: string; work_end: string; desk_id: string; agent_id: string }[];
}
export const FX = JSON.parse(readFileSync(new URL('../../test/fixtures/greenConcrete.json', import.meta.url), 'utf8')) as Fixture;
export const person = (key: string) => FX.people.find((p) => p.key === key)!;

const base = (id: string) => ({ id, revision: 1, tenant_id: TENANT, workspace_id: WORKSPACE, created_at: stamp, updated_at: stamp });

/** The desks of a build made before the working hours: day and night seats, the night people at the places of seats 1 to 3. */
export function olderDesks(): Record<string, unknown>[] {
  return FX.desks.map((d, i) => ({ ...d, work_start: null, work_end: null, shift: d.seat_kind === 'manager' ? null : i >= 6 ? 'night' : 'day',
    label: d.seat_kind === 'manager' ? d.label : i >= 6 ? `Night seat ${i - 5}` : `Day seat ${i + 1}`,
    ...(i >= 6 && i < 9 ? { pos_x: FX.desks[i - 6].pos_x, pos_z: FX.desks[i - 6].pos_z } : {}) }));
}

export function greenRegistry(building: Record<string, unknown> = {}, desks: Record<string, unknown>[] = FX.desks) {
  return {
    buildings: [{ ...base(String(FX.building.id)), ...FX.building, latitude: null, longitude: null, name_ar: null, shift_day_start: null, shift_night_start: null, ...building }],
    floors: [{ ...base(String(FX.floor.id)), ...FX.floor, name_ar: null }],
    rooms: [{ ...base(String(FX.room.id)), ...FX.room, name_ar: null, team_department: null, description: '', notes: '', color: 'forest', equipment: [] }],
    desks: desks.map((d) => ({ ...base(String(d.id)), ...d })),
  };
}

export type WorkState = 'working' | 'queued' | 'waiting_approval' | 'blocked' | 'idle' | 'paused' | 'meeting';
export interface Work { state: WorkState; title?: string; verifiedAt?: string }

export interface GreenState {
  /** What the platform's records say about each person's agent. Anyone not listed is idle (the owner turned the agents on). */
  work: Record<string, Work>;
  /** The page clock, so evidence is fresh against it. */
  clock: () => number;
  confirms: Array<Record<string, unknown>>;
  /** What each preview was asked for (the looks the owner picked). */
  previews?: Array<{ default_look: string | null; looks: Record<string, string> }>;
  created: boolean;
}
export function newState(clock: () => number = () => Date.now()): GreenState {
  return { work: {}, clock, confirms: [], previews: [], created: true };
}

const iso = (ms: number) => new Date(ms).toISOString();

function presenceItems(state: GreenState) {
  return FX.people.map((p) => {
    const w = state.work[p.key] ?? { state: 'idle' as WorkState };
    const at = w.verifiedAt ?? iso(state.clock() - 20_000);
    return {
      key: p.agent_id, agent_id: p.agent_id, agent_name: p.name, desk_id: p.desk_id, state: w.state,
      provenance: { kind: w.state === 'idle' || w.state === 'paused' ? 'none' : 'task', run_id: null, task_id: w.title ? `task_${p.key}` : null, approval_id: null, meeting_id: null, reason: null },
      task: w.title ? { id: `task_${p.key}`, title: w.title } : null, last_verified_at: at, stale: false,
    };
  });
}

/** Overrides the agents, tasks, presence and work routes of the standard headquarters mock with the Green Concrete people. */
export async function mockGreenWork(page: Page, state: GreenState, building: Record<string, unknown> = {}, desks: Record<string, unknown>[] = FX.desks) {
  const registry = greenRegistry(building, desks);
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const listed = /^\/api\/v1\/office-spaces\/(buildings|floors|rooms|desks)$/.exec(path);
    if (listed && method === 'GET') return json({ items: state.created ? registry[listed[1] as keyof typeof registry] : [], next_cursor: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ workspace_id: WORKSPACE, workspace_name: 'Green Concrete', role: 'owner', active: true }]);
    if (path === '/api/v1/office/campus') {
      const by: Record<string, number> = { working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0 };
      if (state.created) for (const p of FX.people) by[state.work[p.key]?.state ?? 'idle'] += 1;
      const at = iso(state.clock() - 20_000);
      return json({ version: 1, tenant_id: TENANT, current_workspace_id: WORKSPACE, generated_at: at, truncated: false, buildings: [{
        workspace_id: WORKSPACE, name: 'Green Concrete', kind: 'business', icon: null, profile_revision: 1, role: 'owner', archived: false, accessible: true,
        floors: state.created ? [{ id: FX.floor.id, building_id: FX.building.id, level: 0, name: 'Ground floor' }] : [], office_buildings: state.created ? 1 : 0, rooms: state.created ? 1 : 0, desks: state.created ? 10 : 0,
        agents: state.created ? { total: 10, by_state: by } : { total: 0, by_state: by }, presence_verified_at: at, presence_as_of: at }] });
    }
    if (path === '/api/v1/agents' && method === 'GET') {
      const items = (state.created ? FX.people : []).map((p) => ({
        id: p.agent_id, tenant_id: TENANT, workspace_id: WORKSPACE, owner_user_id: 'user_local', status: state.work[p.key]?.state === 'paused' ? 'paused' : 'active',
        created_at: stamp, updated_at: stamp, version: 1, can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], name: p.name, icon: 'user',
        color: '#2f6f4f', category: 'work', department: p.role, description: `${p.role} at Green Concrete.`, instructions: '', model_route: 'fast', allowed_skills: [],
        allowed_tools: [], autonomy: { read: 'auto', write: 'off', external: 'off' }, knowledge_mode: 'none', knowledge_sources: [], sharing: 'workspace', template_id: `gc:${p.key}`, avatar: null,
      }));
      return json({ items, next_cursor: null, counts: { all: items.length, work: items.length, personal: 0, custom: 0 } });
    }
    if (path === '/api/v1/tasks' && method === 'GET') {
      const tasks = FX.people.filter((p) => state.work[p.key]?.title).map((p) => ({
        id: `task_${p.key}`, title: state.work[p.key].title, prompt: state.work[p.key].title, tenant_id: TENANT, workspace_id: WORKSPACE, created_at: stamp, updated_at: stamp,
        agent_id: p.agent_id, status: state.work[p.key].state === 'working' ? 'running' : state.work[p.key].state,
      }));
      return json(tasks);
    }
    if (path === '/api/v1/office/presence') {
      return json({ version: 1, workspace_id: WORKSPACE, as_of: iso(state.clock()), cursor: null, totals: {}, items: presenceItems(state) });
    }
    const work = /^\/api\/v1\/office\/workstations\/([^/]+)\/work$/.exec(path);
    if (work) {
      const p = FX.people.find((x) => x.desk_id === work[1]);
      const w = p ? state.work[p.key] : undefined;
      const at = iso(state.clock() - 20_000);
      return json({
        version: 1, workspace_id: WORKSPACE, as_of: at, desk: { id: work[1], room_id: FX.room.id, label: 'Seat' },
        agent: p ? { id: p.agent_id, name: p.name, status: 'active' } : null, presence: null, reason: p ? null : 'unassigned',
        tasks: w?.title ? [{ id: `task_${p!.key}`, title: w.title, status: w.state === 'working' ? 'running' : w.state, priority: 'normal', due_at: null, created_at: at, updated_at: w.verifiedAt ?? at,
          links: { task: `/api/v1/tasks/task_${p!.key}`, runs: `/api/v1/tasks/task_${p!.key}/runs` }, run: null }] : [],
        approvals: [], errors: [], documents: [],
      });
    }
    return route.fallback();
  });
}

/** The preview and confirm of "Add Green Concrete". Stateful: a confirm makes `state.created` true. */
export async function mockGreenAction(page: Page, state: GreenState, options: { created?: boolean; older?: boolean; denied?: boolean; viewer?: boolean; failConfirm?: 409 | 500 } = {}) {
  state.created = options.created ?? false;
  // The hash binds the plan AND the looks the owner picked (the real API does the same): a confirm made for other looks is refused.
  const looksOf = (default_look: string | null, chosen: Record<string, string>) => {
    const out: Record<string, string> = {};
    if (default_look) for (const p of FX.people) out[p.key] = default_look;
    return { ...out, ...chosen };
  };
  const hash = (n: number, looks: Record<string, string> = {}) => {
    let h = 7;
    for (const ch of `${n}|${JSON.stringify(Object.entries(looks).sort())}`) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return h.toString(16).padStart(8, '0').repeat(8);
  };
  // The planner's hours, grouped by window as the API answers them.
  const windows = () => {
    const by = new Map<string, { start: string; end: string; people: string[] }>();
    for (const p of FX.people) {
      const key = `${p.work_start}-${p.work_end}`;
      by.set(key, { start: p.work_start, end: p.work_end, people: [...(by.get(key)?.people ?? []), p.key] });
    }
    return [...by.values()].sort((a, b) => a.start.localeCompare(b.start));
  };
  await page.route('http://localhost:8000/api/v1/office/green-concrete/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (options.denied) return json({ error: { code: 'forbidden', message: 'No access.', correlation_id: 'c', details: [] } }, 403);
    if (path.endsWith('/preview')) {
      const query = new URL(route.request().url()).searchParams;
      const chosen: Record<string, string> = {};
      for (const v of query.getAll('look')) { const [k, st] = v.split(':'); chosen[k] = st; }
      const looks = looksOf(query.get('default_look'), chosen);
      state.previews?.push({ default_look: query.get('default_look'), looks: chosen });
      const made = state.created;
      const skip = made ? 'exists' : null;
      const seats = FX.desks.map((d, i) => ({ desk_id: d.id, label: d.label, person: FX.people[i].key, pos_x: d.pos_x, pos_z: d.pos_z, shift: d.shift, seat_order: d.seat_order, facing: d.facing, seat_kind: d.seat_kind, table_group: d.table_group, work_start: d.work_start, work_end: d.work_end, action: made ? 'skip' : 'create', reason: skip }));
      const people = FX.people.map((p) => ({ key: p.key, name: p.name, role: { en: p.role, ar: p.role_ar }, seat_kind: p.seat_kind, work_start: p.work_start, work_end: p.work_end, seat_order: FX.desks.find((d) => d.id === p.desk_id)!.seat_order, desk_id: p.desk_id, agent_id: made ? p.agent_id : null,
        agent_action: made ? 'exists' : 'create', seat_action: made ? 'skip' : 'seat', seat_reason: made ? 'seated' : null, appearance: made ? null : looks[p.key] ?? null }));
      const z = { buildings: 0, floors: 0, rooms: 0, desks: 0, agents: 0 };
      return json({
        version: 2, catalog_version: 2, workspace_id: WORKSPACE, timezone: 'Asia/Dubai', payload_hash: hash(made ? 2 : 1, looks), can_confirm: !options.viewer,
        building: { id: FX.building.id, name: { en: 'Green Concrete', ar: null }, timezone: 'Asia/Dubai', action: made ? 'skip' : 'create', reason: skip },
        floor: { id: FX.floor.id, name: { en: 'Ground floor', ar: null }, level: 0, width_m: 22, depth_m: 16, action: made ? 'skip' : 'create', reason: skip },
        room: { id: FX.room.id, name: { en: 'Command Center', ar: null }, kind: 'command', capacity: 12, x: 2, z: 2, width: 16, depth: 12, door_side: 'south', action: made ? 'skip' : 'create', reason: skip },
        seats, people, status: made ? 'complete' : 'new', agent_slots_left: 200,
        counts: made ? { create: z, skip: { buildings: 1, floors: 1, rooms: 1, desks: 10, agents: 10 }, seat: 0 }
          : { create: { buildings: 1, floors: 1, rooms: 1, desks: 10, agents: 10 }, skip: z, seat: 10 },
        hours: { timezone: 'Asia/Dubai', night_shift: false, windows: windows() }, monthly_cap_usd: 3,
        older_seats: made && options.older ? FX.desks.map((d) => String(d.id)).sort() : [],
        rules: { creates_agents: true, agents_start_paused: true, attaches_tools: false, changes_existing: false, infers_gender_or_ethnicity: false },
      });
    }
    if (path.endsWith('/confirm')) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      state.confirms.push(body);
      const sent = looksOf((body.default_look as string | undefined) ?? null, (body.appearance as Record<string, string> | undefined) ?? {});
      if (body.payload_hash !== hash(state.created ? 2 : 1, sent)) return json({ error: { code: 'conflict', message: 'What would be created has changed since you opened it. Review the preview again.', correlation_id: 'c', details: [] } }, 409);
      if (options.viewer || body.input_mode !== 'click') return json({ error: { code: 'forbidden', message: 'Only a click confirms.', correlation_id: 'c', details: [] } }, 403);
      if (options.failConfirm === 409) return json({ error: { code: 'conflict', message: 'What would be created has changed since you opened it. Review the preview again.', correlation_id: 'c', details: [] } }, 409);
      if (options.failConfirm === 500) return json({ error: { code: 'internal', message: 'Something failed.', correlation_id: 'c', details: [] } }, 500);
      state.created = true;
      return json({ version: 2, outcome: 'created', payload_hash: body.payload_hash, timezone: 'Asia/Dubai', created: { buildings: 1, floors: 1, rooms: 1, desks: 10, agents: 10, seated: 10 }, message: 'Created.', failed: [], agents_started: 0 });
    }
    return route.fallback();
  });
}
