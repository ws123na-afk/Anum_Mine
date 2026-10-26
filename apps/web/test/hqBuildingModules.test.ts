// Building module panels, the starter campus building looks, the command centre room kind and the audit that every
// room of the eight building starter campus opens a real module or says plainly why not.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { OFFICE_ROOM_KINDS, OFFICE_DESK_ROOM_KINDS, OFFICE_TEMPLATE_IDS } from '../../../packages/contracts/src/officeBuilder.ts';
import { archetypeOf } from '../src/lib/campusBuildings.ts';
import { registryLook } from '../src/lib/campusRegistry.ts';
import { STARTER_PANELS, buildingModules, moduleBuildingKind, starterKey } from '../src/lib/hqBuildingModules.ts';
import { commerceLive, tyreLive } from '../src/lib/hqModules.ts';
import { interiorKindFor } from '../src/hqAssets/interior/index.ts';
import { moduleHash, resolveRoomModule } from '../src/lib/hqRoomModule.ts';
import { ROOM_KINDS, DESK_KINDS } from '../src/hqBuilder/labels.ts';
import { CATALOGS } from '../src/lib/i18n.ts';

type StarterRoom = Readonly<{ building: string; building_id: string; floor: number; room: string; name: string; kind: string; department: string | null }>;
const ROOMS = JSON.parse(readFileSync(new URL('./fixtures/starterCampusRooms.json', import.meta.url), 'utf8')) as StarterRoom[];

const floorsOf = (departments: Array<string | null>) => [{ rooms: departments.map((teamDepartment) => ({ teamDepartment })) }];

describe('building module panels', () => {
  test('a starter campus building is known by its stable id and shows its own panels', () => {
    assert.equal(starterKey('campus-02-commerce'), 'commerce');
    assert.equal(starterKey('campus-03-tyre-pulse'), 'tyre-pulse');
    assert.equal(starterKey('b0'), null);
    assert.equal(starterKey('campus-1-hq'), null);
    const hq = buildingModules({ id: 'campus-01-hq', floors: floorsOf([null]) });
    assert.deepEqual([...hq.panels], ['team', 'os']);
    assert.equal(hq.source, 'starter');
    assert.deepEqual([...buildingModules({ id: 'campus-02-commerce', floors: [] }).panels], ['team', 'commerce']);
    assert.deepEqual([...buildingModules({ id: 'campus-03-tyre-pulse', floors: [] }).panels], ['team', 'tyre']);
  });
  test('every building of the starter campus master list has a panel entry, and nothing else does', () => {
    const keys = [...new Set(ROOMS.map((r) => r.building))];
    assert.equal(keys.length, 8);
    assert.deepEqual(Object.keys(STARTER_PANELS).sort(), [...keys].sort());
    for (const key of keys) assert.ok(STARTER_PANELS[key].includes('team'), key);
  });
  test('other buildings are read from the departments of their rooms', () => {
    assert.deepEqual([...buildingModules({ id: 'x', floors: floorsOf(['tyre_engineering', null]) }).panels], ['team', 'tyre']);
    assert.deepEqual([...buildingModules({ id: 'x', floors: floorsOf(['support']) }).panels], ['team', 'commerce']);
    assert.deepEqual([...buildingModules({ id: 'x', floors: floorsOf(['sales', 'tyre_engineering']) }).panels], ['team', 'commerce', 'tyre']);
    assert.equal(buildingModules({ id: 'x', floors: floorsOf(['sales']) }).source, 'departments');
  });
  test('a building with no hint keeps the two original panels', () => {
    const m = buildingModules({ id: 'x', floors: floorsOf([null, 'finance']) });
    assert.deepEqual([...m.panels], ['team', 'commerce', 'tyre']);
    assert.equal(m.source, 'default');
  });
  test('only a workspace building can be an executive building for room modules', () => {
    assert.equal(moduleBuildingKind({ kind: 'executive' }), 'executive');
    assert.equal(moduleBuildingKind({ kind: 'executive', registryBuildingId: 'campus-01-hq' }), null);
    assert.equal(moduleBuildingKind(null), null);
  });
  test('the panel numbers come from the dashboards and are absent, never invented, when the answer lacks them', () => {
    const live = commerceLive({ by_channel: [{ orders: 2 }], pending_total: 1, late_total: 0, returns: { count: 0 }, open_alerts: 0, currency: 'AED', low_stock: [{}, {}, {}] });
    assert.equal(live.lowStock, 3);
    assert.equal(commerceLive({ by_channel: [], pending_total: 0, late_total: 0, returns: { count: 0 }, open_alerts: 0, currency: null }).lowStock, null);
    const tyre = tyreLive({ summary: { vehicles: 4, tyres_fitted: 16, tyres_in_stock: 9, below_minimum: 0, overdue_inspections: 0, due_next_30_days: 0 }, alert_counts: {} });
    assert.deepEqual([tyre.fitted, tyre.inStock], [16, 9]);
    assert.equal(tyreLive({ summary: { vehicles: 4, tyres_fitted: 16, below_minimum: 0, overdue_inspections: 0, due_next_30_days: 0 }, alert_counts: {} }).inStock, null);
  });
});

describe('starter campus building looks', () => {
  const look = (id: string) => registryLook(id);
  test('the personal office is the villa, HQ the executive tower, Tyre Pulse the workshop, Commerce the shop pavilion', () => {
    const type = (id: string) => archetypeOf({ ...look(id), workspaceId: `w~${id}` });
    assert.equal(look('campus-07-personal').kind, 'personal');
    assert.equal(type('campus-07-personal'), 'villa');
    assert.equal(type('campus-01-hq'), 'executive');
    assert.equal(type('campus-03-tyre-pulse'), 'workshop');
    assert.equal(type('campus-02-commerce'), 'retail');
  });
  test('any other registry building keeps the plain business look', () => {
    assert.deepEqual(look('b3'), { kind: 'business', icon: null });
    assert.deepEqual(look('campus-04-corporate-services'), { kind: 'business', icon: null });
  });
});

describe('command centre room kind', () => {
  test('is in the contract, the builder lists and both languages', () => {
    assert.ok((OFFICE_ROOM_KINDS as readonly string[]).includes('command'));
    assert.deepEqual([...ROOM_KINDS], [...OFFICE_ROOM_KINDS]);
    assert.deepEqual([...DESK_KINDS], [...OFFICE_DESK_ROOM_KINDS]);
    assert.ok((OFFICE_TEMPLATE_IDS as readonly string[]).includes('command_center'));
    for (const lang of ['en', 'ar'] as const) {
      const catalog = CATALOGS[lang] as Record<string, string>;
      for (const key of ['hqb.kind.command', 'hq.roomKind.command', 'hqSc.kind.command']) assert.ok(catalog[key], `${lang} ${key}`);
    }
  });
  test('opens the live flow by its kind, whatever it is called, and is dressed as the operations room', () => {
    for (const name of ['Command Center', 'Room 7', 'مركز القيادة', 'Finance']) {
      const m = resolveRoomModule({ kind: 'command', name });
      assert.deepEqual(m.target, { type: 'flow' }, name);
      assert.equal(m.source, 'kind');
    }
    assert.equal(interiorKindFor('command', 'Anything'), 'operations');
    // A stored department still wins: a command room owned by finance opens Finance.
    assert.equal(moduleHash(resolveRoomModule({ kind: 'command', name: 'x', department: 'finance' }).target), '#finance');
  });
  test('an office named like a command centre is still only decoration, as before', () => {
    assert.equal(interiorKindFor('office', 'Command Center'), 'operations');
    assert.equal(resolveRoomModule({ kind: 'office', name: 'Command Center' }).source, 'name');
  });
});

describe('every room of the eight building starter campus opens a real module or says why not', () => {
  const resolved = ROOMS.map((r) => ({ r, m: resolveRoomModule({ kind: r.kind, name: r.name, department: r.department, buildingKind: null }) }));
  test('62 rooms in 8 buildings', () => {
    assert.equal(ROOMS.length, 62);
    assert.equal(new Set(ROOMS.map((r) => r.building)).size, 8);
  });
  test('each room resolves to a view, the live flow, a meeting, or an honest none with a reason', () => {
    for (const { r, m } of resolved) {
      if (m.target.type === 'none') assert.ok(['no_department_module', 'no_visitor_register'].includes(m.target.why), r.room);
      else assert.ok(['view', 'flow', 'meeting'].includes(m.target.type), r.room);
    }
  });
  test('the rooms with no module are exactly reception, procurement and nothing else', () => {
    const none = resolved.filter(({ m }) => m.target.type === 'none').map(({ r }) => `${r.building}/${r.room}`);
    assert.deepEqual(none.sort(), ['corporate-services/procurement', 'hq/reception']);
  });
  test('Marketing, Technology, Research and Legal rooms open their own module', () => {
    const open = (dept: string) => resolved.filter(({ r }) => r.department === dept).map(({ m }) => moduleHash(m.target));
    assert.ok(open('marketing').length > 0 && open('marketing').every((h) => h === '#marketing'));
    assert.ok(open('technology').length > 0 && open('technology').every((h) => h === '#developer'));
    assert.ok(open('research').length > 0 && open('research').every((h) => h === '#knowledge'));
    assert.ok(open('legal').length > 0 && open('legal').every((h) => h === '#files'));
  });
  test('the command centre is the command kind and opens the live flow', () => {
    const rooms = resolved.filter(({ r }) => r.kind === 'command');
    assert.equal(rooms.length, 1);
    assert.equal(rooms[0].r.name, 'Command Center');
    assert.deepEqual(rooms[0].m.target, { type: 'flow' });
  });
  test('rooms that have no department fall back to a named module, never to a guess about the business', () => {
    const generic = resolved.filter(({ m }) => m.source === 'generic' || m.source === 'name').map(({ r, m }) => `${r.room}:${m.source}`);
    // Listed on purpose: these are the rooms the owner can still give a department in the builder.
    assert.deepEqual(generic.sort(), [
      'ai-labs:generic', 'ai-organization:generic', 'automation:generic', 'career-office:generic', 'data:generic', 'employment-desk:generic',
      'events-hall:generic', 'executive-office:name', 'facilities:generic', 'future-businesses:generic', 'lounge:generic', 'personal-office:generic', 'terrace:generic',
    ]);
    for (const { m } of resolved.filter(({ m }) => m.source === 'generic' || m.source === 'name')) assert.equal(m.needsDepartment, true);
  });
  test('every view target exists in the shell', async () => {
    const { VIEWS } = await import('../src/lib/shell.ts');
    for (const { r, m } of resolved) if (m.target.type === 'view') assert.ok((VIEWS as readonly string[]).includes(m.target.view), r.room);
  });
});
