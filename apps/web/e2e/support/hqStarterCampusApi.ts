import type { Page, Route } from '@playwright/test';
import { ARABIC_NAMES_NONE } from './hqArabicNamesApi';

// Mock of the starter campus API (docs/ANUM_MINE_STARTER_CAMPUS.md): preview and confirm. The plan is plainly fake
// test data in the real response shape; the mock keeps the one rule the UI relies on: the hash changes with the selection.
const WS = 'workspace_foundation';
const zero = { buildings: 0, floors: 0, rooms: 0, desks: 0 };

interface RoomSeed { key: string; en: string; ar: string; kind: string; w: number; d: number; desks: number; dept?: string; skip?: string }
interface BuildingSeed { key: string; n: number; en: string; ar: string; floors: { level: number; en: string; ar: string; w: number; d: number; rooms: RoomSeed[] }[]; status: 'new' | 'partial' | 'complete' }

const SEEDS: BuildingSeed[] = [
  { key: 'hq', n: 1, en: 'ANUM HQ', ar: 'المقر الرئيسي لأنوم', status: 'new', floors: [
    { level: 0, en: 'Ground floor', ar: 'الطابق الأرضي', w: 38, d: 19, rooms: [
      { key: 'reception', en: 'Reception', ar: 'الاستقبال', kind: 'reception', w: 10, d: 6, desks: 0 },
      { key: 'meeting-a', en: 'Meeting Room A', ar: 'غرفة الاجتماعات أ', kind: 'meeting', w: 8, d: 6, desks: 0 }] },
    { level: 1, en: 'First floor', ar: 'الطابق الأول', w: 38, d: 14, rooms: [
      { key: 'ai-organization', en: 'AI Organization', ar: 'المؤسسة الذكية', kind: 'office', w: 16, d: 10, desks: 8 },
      { key: 'command-center', en: 'Command Center', ar: 'مركز القيادة', kind: 'office', w: 14, d: 10, desks: 6 }] }] },
  { key: 'corporate-services', n: 4, en: 'Corporate Services', ar: 'الخدمات المؤسسية', status: 'new', floors: [
    { level: 0, en: 'Ground floor', ar: 'الطابق الأرضي', w: 34, d: 24, rooms: [
      { key: 'executive-office', en: 'Executive Office', ar: 'المكتب التنفيذي', kind: 'office', w: 14, d: 9, desks: 3 },
      { key: 'boardroom', en: 'Boardroom', ar: 'قاعة مجلس الإدارة', kind: 'meeting', w: 16, d: 9, desks: 0 }] }] },
  { key: 'tyre-pulse', n: 3, en: 'Tyre Pulse', ar: 'تاير بلس', status: 'partial', floors: [
    { level: 0, en: 'Ground floor', ar: 'الطابق الأرضي', w: 36, d: 37, rooms: [
      { key: 'fleet', en: 'Fleet', ar: 'الأسطول', kind: 'office', w: 12, d: 8, desks: 6, dept: 'operations', skip: 'exists' },
      { key: 'maintenance', en: 'Maintenance', ar: 'الصيانة', kind: 'workshop', w: 12, d: 9, desks: 4, dept: 'tyre_engineering' },
      { key: 'warehouse', en: 'Warehouse', ar: 'المستودع', kind: 'warehouse', w: 16, d: 12, desks: 4, dept: 'operations', skip: 'name_in_use' }] }] },
  { key: 'career', n: 8, en: 'Career & Employment', ar: 'المسار المهني والتوظيف', status: 'complete', floors: [
    { level: 0, en: 'Ground floor', ar: 'الطابق الأرضي', w: 28, d: 19, rooms: [
      { key: 'career-office', en: 'Career Office', ar: 'مكتب المسار المهني', kind: 'office', w: 10, d: 8, desks: 3, skip: 'exists' }] }] },
];

function plan(seed: BuildingSeed, selected: boolean) {
  const id = `campus-${String(seed.n).padStart(2, '0')}-${seed.key}`;
  const create = { ...zero }; const skip = { ...zero };
  if (seed.status === 'complete' || seed.status === 'partial') skip.buildings = 1; else create.buildings = 1;
  const floors = seed.floors.map((f) => {
    const exists = seed.status === 'complete';
    if (exists) skip.floors += 1; else create.floors += 1;
    const rooms = f.rooms.map((r) => {
      const skipped = Boolean(r.skip) || exists;
      const rid = `${id}-${r.key}`;
      const desks = skipped ? [] : Array.from({ length: r.desks }, (_, i) => ({ id: `${rid}-d${i + 1}`, label: `Desk ${i + 1}`, pos_x: 0.75 + i * 1.5, pos_z: 0.75, action: 'create' as const }));
      if (skipped) skip.rooms += 1; else { create.rooms += 1; create.desks += desks.length; }
      return { key: r.key, id: rid, name: { en: r.en, ar: r.ar }, kind: r.kind, capacity: r.desks + 4, width: r.w, depth: r.d, door_side: 'south', color: 'forest',
        team_department: r.dept ?? null, equipment: [], desk_count: r.desks, action: skipped ? 'skip' : 'create', reason: skipped ? (r.skip ?? 'exists') : null,
        x: 2, z: 2, rotation: 0, desks };
    });
    return { key: `l${f.level}`, id: `${id}-l${f.level}`, level: f.level, name: { en: f.en, ar: f.ar }, width_m: f.w, depth_m: f.d, action: exists ? 'skip' : 'create', reason: exists ? 'exists' : null, rooms };
  });
  return { key: seed.key, number: seed.n, id, name: { en: seed.en, ar: seed.ar }, timezone: 'Asia/Dubai', selected,
    action: seed.status === 'new' ? 'create' : 'skip', reason: seed.status === 'new' ? null : 'exists', status: seed.status, floors, counts: { create, skip } };
}

export interface StarterCampusMock {
  requests: { method: string; path: string; body: Record<string, unknown> | null }[];
  setCanConfirm(v: boolean): void;
  setPreviewFailure(status: number | null): void;
  setConfirmFailure(status: number | null, detail?: string): void;
  setOutcome(o: 'created' | 'partial'): void;
}

export async function mockStarterCampus(page: Page): Promise<StarterCampusMock> {
  let canConfirm = true; let previewFail: number | null = null; let confirmFail: { status: number; detail: string } | null = null; let outcome: 'created' | 'partial' = 'created';
  const mock: StarterCampusMock = {
    requests: [],
    setCanConfirm(v) { canConfirm = v; }, setPreviewFailure(s) { previewFail = s; }, setOutcome(o) { outcome = o; },
    setConfirmFailure(status, detail = 'failed') { confirmFail = status === null ? null : { status, detail }; },
  };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const hashFor = (keys: string[]) => (keys.join('').replace(/[^a-z0-9]/g, '').padEnd(64, 'c')).slice(0, 64).replace(/[^0-9a-f]/g, 'a');
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/starter-campus\/.*/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/office/starter-campus', '');
    const raw = request.postData();
    mock.requests.push({ method: request.method(), path: path + url.search, body: raw ? JSON.parse(raw) : null });
    if (path.startsWith('/arabic-names')) return json(route, ARABIC_NAMES_NONE);
    if (path === '/preview') {
      if (previewFail !== null) return json(route, { detail: previewFail === 403 ? 'Forbidden' : 'The service failed.' }, previewFail);
      const asked = url.searchParams.get('buildings');
      const all = SEEDS.map((s) => s.key);
      const selected = asked === null ? all : asked.split(',');
      const plans = SEEDS.map((s) => plan(s, selected.includes(s.key)));
      const totals = { create: { ...zero }, skip: { ...zero } };
      for (const p of plans.filter((x) => x.selected)) for (const side of ['create', 'skip'] as const) for (const k of Object.keys(zero) as (keyof typeof zero)[]) totals[side][k] += p.counts[side][k];
      return json(route, { version: 1, catalog_version: 1, workspace_id: WS, timezone: 'Asia/Dubai', selected, payload_hash: hashFor(selected), can_confirm: canConfirm,
        buildings: plans, totals, rules: { creates_agents: false, seats_agents: false, changes_existing: false } });
    }
    if (path === '/confirm') {
      if (confirmFail) return json(route, { detail: confirmFail.detail }, confirmFail.status);
      const body = mock.requests[mock.requests.length - 1].body as { buildings: string[] };
      const items = body.buildings.map((k, i) => {
        const seed = SEEDS.find((s) => s.key === k)!;
        const p = plan(seed, true);
        const failed = outcome === 'partial' && i === 1;
        return { key: k, id: p.id, name: p.name, status: seed.status === 'complete' ? 'already_exists' : failed ? 'failed' : 'created',
          created: seed.status === 'complete' || failed ? zero : p.counts.create, skipped: null,
          message: failed ? 'A desk with that id already exists. Nothing was created for this building.' : 'Created.' };
      });
      const totals = { ...zero };
      for (const it of items) for (const k of Object.keys(zero) as (keyof typeof zero)[]) totals[k] += it.created[k];
      const made = items.some((i) => i.status === 'created'); const bad = items.some((i) => i.status === 'failed');
      return json(route, { version: 1, outcome: made ? (bad ? 'partial' : 'created') : bad ? 'failed' : 'already_set_up', payload_hash: 'a'.repeat(64), timezone: 'Asia/Dubai', buildings: items, totals, agents_created: 0 });
    }
    return json(route, { detail: 'Not found' }, 404);
  });
  return mock;
}
