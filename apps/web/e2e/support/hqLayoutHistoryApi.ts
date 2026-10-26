import type { Page, Route } from '@playwright/test';

// A stateful mock of the layout history and template API (docs/ANUM_MINE_LAYOUT_HISTORY.md) on top of the
// builder mock's rooms, desks and floors. It enforces the server's rules for what the specs touch: click only
// restore, payload hash, head check (STALE_LAYOUT), the exact unassign list, a new revision per restore,
// idempotent replay. Names are plainly fake test data.
type Json = Record<string, unknown>;
interface Box { x: number; z: number; width: number; depth: number; rotation: number }
interface SnapRoom { id: string; name: string; kind: string; color: string | null; box: Box }
interface SnapDesk { id: string; room_id: string; label: string; x: number | null; z: number | null; agent_id: string | null }
interface Revision {
  no: number; action: string; actor: string; created_at: string; restored_from: number | null;
  rooms: SnapRoom[]; desks: SnapDesk[]; width_m: number; depth_m: number;
}

export interface HistoryMockOptions {
  /** Revision list and template reads answer 403. */
  denyHistory?: boolean;
  /** A history read answers 503. */
  historyDown?: boolean;
  /** Start with no history at all. */
  noHistory?: boolean;
  /** No templates saved yet (the default seeds none either). */
  seedTemplate?: boolean;
}

export interface HistoryMock {
  revisions: Revision[];
  templates: Json[];
  requests: { method: string; path: string; body: Json | null }[];
  /** Another editor saved a layout: a new head the open page does not know about. */
  otherEditorSaves(): void;
}

const stamp = (n: number) => `2026-10-2${Math.min(n, 5)}T0${Math.min(n + 5, 9)}:30:00Z`;
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };
const hash = (no: number) => `${String(no).padStart(2, '0')}`.repeat(32);
const same = (a: Box | null, b: Box | null) => !!a && !!b && a.x === b.x && a.z === b.z && a.width === b.width && a.depth === b.depth && a.rotation === b.rotation;

function diff(old: Revision | null, next: { rooms: SnapRoom[]; desks: SnapDesk[] }) {
  const before = new Map((old?.rooms ?? []).map((r) => [r.id, r]));
  const after = new Map(next.rooms.map((r) => [r.id, r]));
  const counts = { rooms_added: 0, rooms_removed: 0, rooms_moved: 0, rooms_changed: 0 };
  const rooms = [...new Set([...before.keys(), ...after.keys()])].sort().map((id) => {
    const b = before.get(id); const a = after.get(id);
    let status = 'same';
    if (old) {
      if (!b) status = 'added'; else if (!a) status = 'removed';
      else if (!same(a.box, b.box)) status = 'moved'; else if (a.name !== b.name) status = 'changed';
    }
    const key = ({ added: 'rooms_added', removed: 'rooms_removed', moved: 'rooms_moved', changed: 'rooms_changed' } as Record<string, keyof typeof counts>)[status];
    if (key) counts[key] += 1;
    const shown = (a ?? b) as SnapRoom;
    return { id, name: shown.name, kind: shown.kind, color: shown.color, status, box: shown.box, from_box: a && b && status === 'moved' ? b.box : null, from_name: a && b && a.name !== b.name ? b.name : null };
  });
  const oldDesks = new Map((old?.desks ?? []).map((d) => [d.id, d]));
  const newDesks = new Map(next.desks.map((d) => [d.id, d]));
  let added = 0; let removed = 0; let moved = 0;
  if (old) for (const id of new Set([...oldDesks.keys(), ...newDesks.keys()])) {
    const o = oldDesks.get(id); const n = newDesks.get(id);
    if (!o) added += 1; else if (!n) removed += 1; else if (o.x !== n.x || o.z !== n.z || o.label !== n.label) moved += 1;
  }
  return {
    rooms,
    desks: next.desks.map((d) => ({ id: d.id, room_id: d.room_id, label: d.label, x: d.x, z: d.z })),
    summary: { ...counts, desks_added: added, desks_removed: removed, desks_moved: moved, floor_resized: false, rooms: next.rooms.length, desks: next.desks.length },
  };
}

function error(route: Route, status: number, message: string, details: Json[] = []) {
  return route.fulfill({
    status, contentType: 'application/json',
    body: JSON.stringify({ error: { code: status === 409 ? 'conflict' : status === 403 ? 'forbidden' : status === 404 ? 'not_found' : 'validation_error', message, details, correlation_id: 'corr_example' } }),
  });
}

export async function mockLayoutHistory(
  page: Page, rooms: Map<string, Json>, desks: Map<string, Json>, floors: Map<string, Json>, options: HistoryMockOptions, role: string,
): Promise<HistoryMock> {
  const live = () => ({
    rooms: [...rooms.values()].filter((r) => r.scene_x != null).map<SnapRoom>((r) => ({
      id: String(r.id), name: String(r.name), kind: String(r.kind), color: (r.color as string | null) ?? null,
      box: { x: Number(r.scene_x), z: Number(r.scene_z), width: Number(r.scene_width), depth: Number(r.scene_depth), rotation: Number(r.rotation ?? 0) },
    })),
    desks: [...desks.values()].map<SnapDesk>((d) => ({ id: String(d.id), room_id: String(d.room_id), label: String(d.label), x: d.pos_x as number | null, z: d.pos_z as number | null, agent_id: (d.agent_id as string | null) ?? null })),
  });
  const floor = () => floors.get('floor-1') as Json;
  const mock: HistoryMock = {
    revisions: [], templates: [], requests: [],
    otherEditorSaves() {
      const head = mock.revisions[mock.revisions.length - 1];
      mock.revisions.push({ ...head, no: head.no + 1, action: 'office.floor.layout', actor: 'user_aisha', created_at: stamp(head.no + 1), restored_from: null });
    },
  };
  if (!options.noHistory && rooms.has('r-sales')) {
    const now = live();
    const w = Number(floor().width_m); const d = Number(floor().depth_m);
    const sales = now.rooms.find((r) => r.id === 'r-sales') as SnapRoom;
    const old: SnapRoom = { id: 'r-old', name: 'Old store', kind: 'warehouse', color: null, box: { x: 40, z: 10, width: 8, depth: 6, rotation: 0 } };
    const movedSales = { ...sales, box: { ...sales.box, x: 6 } };
    const noAgent = now.desks.map((x) => ({ ...x, agent_id: null }));
    mock.revisions.push(
      { no: 1, action: 'baseline', actor: 'user_local', created_at: stamp(1), restored_from: null, rooms: [sales, ...now.rooms.filter((r) => r.id === 'r-meet'), old], desks: noAgent.filter((x) => x.id !== 'd-1'), width_m: w, depth_m: d },
      { no: 2, action: 'office.floor.layout', actor: 'user_local', created_at: stamp(2), restored_from: null, rooms: [movedSales, ...now.rooms.filter((r) => r.id === 'r-meet'), old], desks: noAgent.filter((x) => x.id !== 'd-1'), width_m: w, depth_m: d },
      { no: 3, action: 'office.room.create', actor: 'user_aisha', created_at: stamp(3), restored_from: null, rooms: now.rooms, desks: now.desks, width_m: w, depth_m: d },
    );
  }
  if (options.seedTemplate) {
    mock.templates.push({ id: 'tpl-seed', kind: 'floor', name: 'Standard floor', revision: 1, created_by: 'user_local', created_at: stamp(2), summary: { floors: 1, rooms: 3, desks: 3 } });
  }

  const head = () => mock.revisions[mock.revisions.length - 1];
  const summaryOf = (i: number) => ({ ...diff(i > 0 ? mock.revisions[i - 1] : null, mock.revisions[i]).summary, baseline: i === 0 || undefined });
  const view = (i: number) => {
    const r = mock.revisions[i];
    return { revision_no: r.no, action: r.action, actor: r.actor, created_at: r.created_at, summary: summaryOf(i), snapshot_hash: hash(r.no), restored_from: r.restored_from, is_current: r.no === head().no };
  };
  const unassignFor = (target: Revision) => {
    const keep = new Set(target.desks.map((x) => x.id));
    const keepRooms = new Set(target.rooms.map((x) => x.id));
    return live().desks.filter((x) => x.agent_id && (!keep.has(x.id) || !keepRooms.has(x.room_id))).map((x) => ({
      desk_id: x.id, label: x.label, room: String(rooms.get(x.room_id)?.name ?? ''), agent_id: x.agent_id, agent_hidden: false,
    }));
  };

  await page.route(/http:\/\/localhost:8000\/api\/v1\/office-layout\/.*/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/office-layout', '');
    const method = request.method();
    const body = request.postData() ? (JSON.parse(request.postData() as string) as Json) : null;
    mock.requests.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const owner = role === 'owner';
    if (options.historyDown) return error(route, 503, 'Office persistence needs the PostgreSQL backend');
    if (options.denyHistory) return error(route, 403, 'Permission required');

    if (method === 'GET' && path === '/floors/floor-1/revisions') {
      const items = mock.revisions.map((_, i) => view(i)).reverse();
      return json({ items, head_revision: head()?.no ?? 0, cap: 50, next_before: null, can_restore: owner });
    }
    const one = path.match(/^\/floors\/floor-1\/revisions\/(\d+)$/);
    if (method === 'GET' && one) {
      const index = mock.revisions.findIndex((r) => r.no === Number(one[1]));
      if (index < 0) return error(route, 404, 'That revision does not exist or was removed to keep the history short', [{ message: 'gone', location: null, type: 'REVISION_NOT_FOUND' }]);
      const target = mock.revisions[index];
      const against = url.searchParams.get('against') ?? 'current';
      const result = against === 'previous' ? diff(index > 0 ? mock.revisions[index - 1] : null, target) : diff({ ...head(), ...live() } as Revision, target);
      return json({
        revision: view(index), head_revision: head().no, against, current_hash: 'cc'.repeat(32), payload_hash: hash(target.no),
        matches_current: target.no === head().no, floor: { width_m: target.width_m, depth_m: target.depth_m },
        diff: result, unassign: against === 'current' ? unassignFor(target) : [], can_restore: owner,
      });
    }
    const restore = path.match(/^\/floors\/floor-1\/revisions\/(\d+)\/restore$/);
    if (method === 'POST' && restore) {
      if (!owner) return error(route, 403, 'Workspace manager permission is required');
      if (body?.input_mode !== 'click') return error(route, 403, 'Confirm this on screen with a click.');
      const target = mock.revisions.find((r) => r.no === Number(restore[1]));
      if (!target) return error(route, 404, 'That revision does not exist', [{ message: 'gone', location: null, type: 'REVISION_NOT_FOUND' }]);
      if (head().restored_from === target.no && body?.expected_head === head().no - 1) return json({ floor_id: 'floor-1', restored: true, replayed: true, noop: false, revision_no: head().no, restored_from: target.no, unassigned: [], summary: {} });
      if (body?.expected_head !== head().no) return error(route, 409, 'The floor changed since you opened this.', [{ message: 'stale', location: null, type: 'STALE_LAYOUT' }]);
      if (body?.payload_hash !== hash(target.no)) return error(route, 409, 'Review it again', [{ message: 'hash', location: null, type: 'PAYLOAD_HASH_MISMATCH' }]);
      const need = unassignFor(target).map((u) => u.desk_id).sort();
      const given = ((body?.confirm_unassign ?? []) as string[]).slice().sort();
      if (need.join() !== given.join()) return error(route, 409, 'Confirm the list', need.map((id) => ({ message: id, location: `desks.${id}`, type: 'UNASSIGN_NOT_CONFIRMED' })));
      const before = live();
      for (const id of [...rooms.keys()]) if (!target.rooms.some((r) => r.id === id)) rooms.delete(id);
      for (const id of [...desks.keys()]) if (!target.desks.some((x) => x.id === id)) desks.delete(id);
      for (const r of target.rooms) {
        const existing = rooms.get(r.id) ?? { id: r.id, revision: 1, ...scope, created_at: stamp(1), updated_at: stamp(1), floor_id: 'floor-1', capacity: 8, rotation: 0, door_side: 'south', description: '', notes: '', name_ar: null, equipment: [], team_department: null };
        rooms.set(r.id, { ...existing, revision: Number(existing.revision) + 1, name: r.name, kind: r.kind, color: r.color, scene_x: r.box.x, scene_z: r.box.z, scene_width: r.box.width, scene_depth: r.box.depth, rotation: r.box.rotation });
      }
      for (const x of target.desks) {
        const existing = desks.get(x.id) ?? { id: x.id, revision: 1, ...scope, created_at: stamp(1), updated_at: stamp(1), room_id: x.room_id, agent_id: null };
        desks.set(x.id, { ...existing, revision: Number(existing.revision) + 1, label: x.label, pos_x: x.x, pos_z: x.z });
      }
      const next: Revision = { ...target, no: head().no + 1, action: 'office.floor.restore', actor: 'user_local', created_at: stamp(head().no + 1), restored_from: target.no, desks: live().desks };
      mock.revisions.push(next);
      return json({ floor_id: 'floor-1', restored: true, replayed: false, noop: false, revision_no: next.no, restored_from: target.no, unassigned: unassignFor({ ...target, rooms: before.rooms, desks: before.desks } as Revision).length ? need : [], summary: {} });
    }

    if (method === 'GET' && path === '/templates') return json({ items: mock.templates, can_manage: owner });
    if (!owner) return error(route, 403, 'Workspace manager permission is required');
    if (method === 'POST' && path === '/templates/from-floor') {
      if (mock.templates.some((t) => t.kind === 'floor' && String(t.name).toLowerCase() === String(body?.name).toLowerCase())) return error(route, 409, 'name', [{ message: 'name', location: null, type: 'TEMPLATE_NAME_TAKEN' }]);
      const saved = { id: String(body?.id), kind: 'floor', name: String(body?.name), revision: 1, created_by: 'user_local', created_at: stamp(4), summary: { floors: 1, rooms: live().rooms.length, desks: live().desks.length } };
      mock.templates.push(saved);
      return json({ ...saved, skipped_unplaced_rooms: 1 }, 201);
    }
    const tpl = path.match(/^\/templates\/([^/]+)$/);
    if (tpl && method === 'PATCH') {
      const row = mock.templates.find((t) => t.id === tpl[1]);
      if (!row) return error(route, 404, 'Template not found');
      if (row.revision !== body?.expected_revision) return error(route, 409, 'stale', [{ message: 'stale', location: null, type: 'STALE_REVISION' }]);
      row.name = String(body?.name); row.revision = Number(row.revision) + 1;
      return json(row);
    }
    if (tpl && method === 'DELETE') {
      const at = mock.templates.findIndex((t) => t.id === tpl[1]);
      if (at < 0) return error(route, 404, 'Template not found');
      mock.templates.splice(at, 1);
      return route.fulfill({ status: 204 });
    }
    if (method === 'POST' && path === '/floors/from-template') {
      const row = mock.templates.find((t) => t.id === body?.template_id);
      if (!row) return error(route, 404, 'Template not found');
      if ([...floors.values()].some((f) => f.level === body?.level)) return error(route, 409, 'level', [{ message: 'level', location: null, type: 'FLOOR_LEVEL_TAKEN' }]);
      const id = String(body?.id);
      const made = { id, revision: 1, ...scope, created_at: stamp(4), updated_at: stamp(4), building_id: 'building-1', level: Number(body?.level), name: String(body?.name ?? 'Standard floor'), width_m: 60, depth_m: 40 };
      floors.set(id, made);
      return json({ floor: made, room_ids: [`${id}-r1`, `${id}-r2`], desk_count: 2 }, 201);
    }
    return error(route, 404, 'Not found');
  });
  return mock;
}
