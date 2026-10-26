// Arabic names of buildings, floors and rooms: the one helper that picks the display name, its fallbacks, and every pure
// place that shows a registry name (navigation model, command resolution, building signs, sign atlas script detection).
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import type { SceneFloor, SceneBuilding } from '../src/lib/headquartersProjection.ts';
import { hasArabicScript } from '../src/lib/arabicScript.ts';
import { resolveNavTarget, type NavScene } from '../src/lib/hqExtensions.ts';
import { setLocale } from '../src/lib/i18n.ts';
import type { Campus, CampusBuilding } from '../src/lib/campusModel.ts';
import { expandCampus } from '../src/lib/campusRegistry.ts';
import { filterBuildings } from '../src/lib/campusModel.ts';
import { floorEntries, roomStops, stripStops, planModel } from '../src/hq-nav/navModel.ts';
import { arabicMissing, indexRoomNames, nameOf, otherName, pickName, roomName, roomNameFor, sceneName, searchText } from '../src/lib/registryNames.ts';
import { signsNeedArabic } from '../src/lib/hqTextures.ts';

afterEach(() => setLocale('en'));

describe('pickName', () => {
  test('English mode always shows the English name', () => {
    assert.equal(pickName('Reception', 'الاستقبال', 'en'), 'Reception');
    assert.equal(pickName('Reception', null, 'en'), 'Reception');
  });
  test('Arabic mode shows the Arabic name and falls back to English when there is none', () => {
    assert.equal(pickName('Reception', 'الاستقبال', 'ar'), 'الاستقبال');
    for (const empty of [null, undefined, '', '   ']) assert.equal(pickName('Reception', empty, 'ar'), 'Reception', `fallback for ${JSON.stringify(empty)}`);
  });
  test('it trims and reads the current language by default', () => {
    assert.equal(pickName('Lobby', '  البهو  ', 'ar'), 'البهو');
    setLocale('ar');
    assert.equal(pickName('Lobby', 'البهو'), 'البهو');
    setLocale('en');
    assert.equal(pickName('Lobby', 'البهو'), 'Lobby');
  });
  test('record helpers read name_ar, nameAr and labelAr', () => {
    assert.equal(nameOf({ name: 'HQ', name_ar: 'المقر' }, 'ar'), 'المقر');
    assert.equal(nameOf({ name: 'HQ' }, 'ar'), 'HQ');
    assert.equal(sceneName({ name: 'HQ', nameAr: 'المقر' }, 'ar'), 'المقر');
    assert.equal(roomName({ label: 'Lobby', labelAr: 'البهو' }, 'ar'), 'البهو');
    assert.equal(roomName({ label: 'Lobby' }, 'ar'), 'Lobby');
  });
  test('the missing hint is for Arabic mode only', () => {
    assert.equal(arabicMissing(null, 'ar'), true);
    assert.equal(arabicMissing('  ', 'ar'), true);
    assert.equal(arabicMissing('البهو', 'ar'), false);
    assert.equal(arabicMissing(null, 'en'), false);
  });
  test('the other language is a secondary line only when it differs and exists', () => {
    assert.equal(otherName('Lobby', 'البهو', 'ar'), 'Lobby');
    assert.equal(otherName('Lobby', 'البهو', 'en'), 'البهو');
    assert.equal(otherName('Lobby', null, 'ar'), null);
    assert.equal(otherName('Same', 'Same', 'en'), null);
  });
  test('search text holds both names so either language finds the record', () => {
    assert.ok(searchText('Lobby', 'البهو').includes('lobby') && searchText('Lobby', 'البهو').includes('البهو'));
    assert.equal(searchText('Lobby', null), 'lobby ');
  });
  test('rooms known only by id and English name use the indexed Arabic name', () => {
    indexRoomNames([{ id: 'r1', labelAr: 'البهو' }, { id: 'r2' }]);
    assert.equal(roomNameFor('r1', 'Lobby', 'ar'), 'البهو');
    assert.equal(roomNameFor('r2', 'Hall', 'ar'), 'Hall');
    assert.equal(roomNameFor('unknown', 'Annex', 'ar'), 'Annex');
    assert.equal(roomNameFor('r1', 'Lobby', 'en'), 'Lobby');
    indexRoomNames([]);
    assert.equal(roomNameFor('r1', 'Lobby', 'ar'), 'Lobby', 're-indexing replaces the old names');
  });
  test('script detection', () => {
    assert.equal(hasArabicScript('الطابق الأول'), true);
    assert.equal(hasArabicScript('ANUM 360 للتجارة'), true);
    assert.equal(hasArabicScript('Ground floor 12'), false);
    assert.equal(hasArabicScript(''), false);
  });
});

const room = (level: number, id: string, label: string, labelAr: string | null, kind: 'reception' | 'office', x: number) => ({
  id: `r${level}${id}`, floorId: 'f' + level, buildingId: 'b', label, labelAr, kind, x, z: 0, width: 10, depth: 8, height: 3, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' as const,
});
const floor = (level: number, name: string, nameAr: string | null): SceneFloor => ({
  id: 'f' + level, name, nameAr, level, bounds: { width: 40, depth: 20 },
  rooms: [room(level, 'a', 'Reception', 'الاستقبال', 'reception', 0), room(level, 'b', 'Back Office', null, 'office', 12)],
  desks: [],
});
const building: SceneBuilding = { id: 'b', name: 'ANUM HQ', nameAr: 'المقر الرئيسي لأنوم', timezone: 'Asia/Dubai', floors: [floor(0, 'Ground floor', 'الطابق الأرضي'), floor(1, 'Floor 1', null)] };

describe('navigation model', () => {
  test('floors, room stops and the plan follow the language and fall back to English', () => {
    assert.deepEqual(floorEntries(building).map((f) => f.name), ['Floor 1', 'Ground floor']);
    setLocale('ar');
    assert.deepEqual(floorEntries(building).map((f) => f.name), ['Floor 1', 'الطابق الأرضي'], 'the floor with no Arabic name keeps its English name');
    const stops = roomStops(building);
    assert.deepEqual(stops.map((s) => (s.kind === 'room' ? s.label : '')).sort(), ['Back Office', 'Back Office', 'الاستقبال', 'الاستقبال'].sort());
    assert.deepEqual(planModel(building.floors[0], 100, 4)!.rooms.map((r) => r.label).sort(), ['Back Office', 'الاستقبال'].sort());
  });
  test('the strip names buildings in Arabic but keeps its order by English name', () => {
    const campus: Campus = { buildings: [cb('ws-b', 'Tyre Pulse', 'تاير بلس'), cb('ws-a', 'ANUM HQ', 'المقر الرئيسي لأنوم')], totals: null, generated_at: '2026-10-26T08:00:00Z' };
    setLocale('ar');
    const names = stripStops(campus, null, 'ws-a').flatMap((s) => (s.kind === 'building' ? [s.name] : []));
    assert.deepEqual(names, ['المقر الرئيسي لأنوم', 'تاير بلس']);
  });
});

const cb = (id: string, name: string, nameAr: string | null, over: Partial<CampusBuilding> = {}): CampusBuilding => ({
  workspace_id: id, tenant_id: 't', name, nameAr, kind: 'business', icon: null, floors: 2, accessible: true, totals: null, presence_verified_at: null, ...over,
});

describe('commands and search find a record by either name', () => {
  const campus: Campus = { buildings: [cb('ws-a', 'ANUM HQ', 'المقر الرئيسي لأنوم'), cb('ws-b', 'Tyre Pulse', 'تاير بلس')], totals: null, generated_at: '2026-10-26T08:00:00Z' };
  const scene: NavScene = {
    workspaceId: 'ws-a',
    floors: [{ id: 'f0', level: 0, name: 'Ground floor', nameAr: 'الطابق الأرضي', rooms: [{ id: 'r-rec', label: 'Reception', labelAr: 'الاستقبال' }, { id: 'r-met', label: 'Meeting Room', labelAr: null }] }],
    agents: [],
  };
  test('a floor and a room are found by their Arabic or English name', () => {
    assert.deepEqual(resolveNavTarget({ floor: 'الطابق الأرضي' }, campus, scene), { kind: 'apply', workspaceId: 'ws-a', floorId: 'f0', roomId: null, agentId: null });
    assert.deepEqual(resolveNavTarget({ room: 'الاستقبال' }, campus, scene), { kind: 'apply', workspaceId: 'ws-a', floorId: 'f0', roomId: 'r-rec', agentId: null });
    assert.deepEqual(resolveNavTarget({ room: 'reception' }, campus, scene), { kind: 'apply', workspaceId: 'ws-a', floorId: 'f0', roomId: 'r-rec', agentId: null });
    assert.equal(resolveNavTarget({ room: 'meeting' }, campus, scene).kind, 'apply', 'a room without an Arabic name is still found by its English one');
  });
  test('a building in another workspace is found by its Arabic name', () => {
    assert.deepEqual(resolveNavTarget({ building: 'تاير بلس' }, campus, scene), { kind: 'switch', workspaceId: 'ws-b' });
  });
  test('the campus search matches the Arabic name', () => {
    assert.deepEqual(filterBuildings(campus.buildings, 'تاير').map((x) => x.workspace_id), ['ws-b']);
    assert.deepEqual(filterBuildings(campus.buildings, 'tyre').map((x) => x.workspace_id), ['ws-b']);
  });
});

describe('campus cards carry the Arabic name of a registry building', () => {
  test('expandCampus keeps nameAr', () => {
    const home = cb('ws-a', 'Workspace', null);
    const registry: SceneBuilding[] = [building, { ...building, id: 'b2', name: 'Tyre Pulse', nameAr: 'تاير بلس' }];
    const out = expandCampus({ buildings: [home], totals: null, generated_at: 'x' }, 'ws-a', registry, null);
    assert.deepEqual(out.buildings.map((x) => x.nameAr), ['المقر الرئيسي لأنوم', 'تاير بلس']);
  });
});

describe('building signs', () => {
  test('English signs are unchanged', () => {
    const scene = buildBuildingScene([floor(0, 'Ground floor', 'الطابق الأرضي')], { activeIndex: 0 });
    assert.ok(scene.signs.some((s) => s.title === 'LEVEL 0' && s.subtitle === 'Ground floor'));
    assert.ok(scene.signs.some((s) => s.title === 'RECEPTION' && s.subtitle === 'Reception'));
    assert.ok(scene.signs.some((s) => s.title === 'OFFICE' && s.subtitle === 'Back Office'));
    assert.equal(signsNeedArabic(scene.signs), false);
  });
  test('Arabic signs carry the Arabic room and floor names and an Arabic title', () => {
    const scene = buildBuildingScene([floor(0, 'Ground floor', 'الطابق الأرضي'), floor(1, 'Floor 1', null)], { activeIndex: 0, locale: 'ar' });
    assert.ok(scene.signs.some((s) => s.title === 'المستوى 0' && s.subtitle === 'الطابق الأرضي'));
    assert.ok(scene.signs.some((s) => s.title === 'الاستقبال' && s.subtitle === 'الاستقبال'));
    assert.ok(scene.signs.some((s) => s.title === 'المكتب' && s.subtitle === 'Back Office'), 'a room with no Arabic name shows its English name');
    assert.equal(signsNeedArabic(scene.signs), true);
    const second = buildBuildingScene([floor(0, 'Ground floor', 'الطابق الأرضي'), floor(1, 'Floor 1', null)], { activeIndex: 1, locale: 'ar' });
    assert.ok(second.signs.some((s) => s.title === 'المستوى 1' && s.subtitle === 'Floor 1'), 'a floor with no Arabic name shows its English name');
    assert.equal(scene.signCount, buildBuildingScene([floor(0, 'Ground floor', 'الطابق الأرضي'), floor(1, 'Floor 1', null)], { activeIndex: 0 }).signCount, 'the sign cells do not depend on the language');
  });
});
