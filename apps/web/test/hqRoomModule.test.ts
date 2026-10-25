// Unit tests for the room to module mapping (src/lib/hqRoomModule.ts) and the live-view data-truth guard.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { DEPARTMENT_MODULES, KIND_MODULES, moduleHash, resolveRoomModule } from '../src/lib/hqRoomModule.ts';
import { CATALOGS } from '../src/lib/i18n.ts';
import { VIEWS } from '../src/lib/shell.ts';

const room = (over: Partial<Parameters<typeof resolveRoomModule>[0]> = {}) => ({ kind: 'office', name: 'Room', ...over });
const hashOf = (over = {}) => moduleHash(resolveRoomModule(room(over)).target);

describe('department to module table', () => {
  const table: Array<[string, string | 'flow' | 'none']> = [
    ['finance', '#finance'], ['sales', '#whatsapp'], ['support', '#inbox'], ['operations', 'flow'],
    ['tyre_engineering', '#tyre'], ['custom', 'none'], ['executive', '#executive'], ['commerce', '#commerce'],
    ['compliance', '#governance'], ['meeting', '#meetings'],
  ];
  for (const [department, expected] of table) {
    test(`${department} opens ${expected}`, () => {
      const m = resolveRoomModule(room({ department, name: 'Unrelated words' }));
      assert.equal(m.source, 'department');
      assert.equal(m.needsDepartment, false);
      if (expected === 'flow') assert.equal(m.target.type, 'flow');
      else if (expected === 'none') assert.equal(m.target.type, 'none');
      else assert.equal(moduleHash(m.target), expected);
    });
  }
  test('the department wins over the name and the kind', () => {
    assert.equal(hashOf({ department: 'finance', kind: 'meeting', name: 'Tyre Engineering' }), '#finance');
  });
  test('an unknown department has no module and never guesses from the name', () => {
    const m = resolveRoomModule(room({ department: 'legal', name: 'Finance' }));
    assert.deepEqual(m.target, { type: 'none', why: 'no_department_module' });
  });
  test('every routed module is a real shell view', () => {
    for (const e of [...Object.values(DEPARTMENT_MODULES), ...Object.values(KIND_MODULES)]) {
      if (e.target.type === 'view') assert.ok((VIEWS as readonly string[]).includes(e.target.view), e.target.view);
    }
  });
  test('every module name exists in English and Arabic', () => {
    const ids = new Set([...Object.values(DEPARTMENT_MODULES), ...Object.values(KIND_MODULES)].filter((e) => e.id !== 'none').map((e) => e.id));
    for (const id of [...ids, 'tasks', 'calendar']) for (const lang of ['en', 'ar'] as const) {
      assert.ok((CATALOGS[lang] as Record<string, string>)[`hqRoomModule.name.${id}`], `${lang} ${id}`);
    }
  });
});

describe('fallback order without a department', () => {
  test('kind comes first, with the owner hint', () => {
    const m = resolveRoomModule(room({ kind: 'meeting', name: 'Finance review' }));
    assert.equal(moduleHash(m.target), '#meetings');
    assert.equal(m.source, 'kind');
    assert.equal(m.needsDepartment, true);
    assert.equal(hashOf({ kind: 'workshop' }), '#tyre');
    assert.equal(hashOf({ kind: 'warehouse' }), '#commerce');
  });
  test('reception has no module and says there is no visitor register', () => {
    assert.deepEqual(resolveRoomModule(room({ kind: 'reception' })).target, { type: 'none', why: 'no_visitor_register' });
  });
  test('rooms of the executive tower open the executive desk', () => {
    const m = resolveRoomModule(room({ buildingKind: 'executive', name: 'Corner room' }));
    assert.equal(moduleHash(m.target), '#executive');
    assert.equal(m.source, 'building');
  });
  test('the name is the last resort, then the generic kind', () => {
    const named = resolveRoomModule(room({ name: 'Finance team' }));
    assert.equal(moduleHash(named.target), '#finance');
    assert.equal(named.source, 'name');
    assert.equal(named.needsDepartment, true);
    assert.equal(hashOf({ name: 'Back room' }), '#tasks');
    assert.equal(hashOf({ kind: 'lounge', name: 'Back room' }), '#calendar');
    assert.equal(hashOf({ name: 'قسم المالية' }), '#finance');
  });
  test('a blank department counts as not set', () => {
    assert.equal(resolveRoomModule(room({ department: '  ', kind: 'workshop' })).source, 'kind');
  });
});

describe('live office data truth', () => {
  const live = ['HeadquartersLive.tsx', 'HqPanels.tsx', 'CampusListView.tsx', 'HqRoomModule.tsx'].map((f) => readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8'));
  test('the live office never mounts the local reception simulation', () => {
    for (const src of live) assert.ok(!/ReceptionOperations|receptionOperations/.test(src));
  });
  test('no invented host or visitor names appear in the live sources', () => {
    for (const src of live) for (const name of ['Chief of Staff', 'Finance Controller', 'Tyre Fleet Planner', 'Sample Customer', 'Reception coordinator']) {
      assert.ok(!src.includes(name), name);
    }
  });
});
