// Unit tests for the room to module mapping (src/lib/hqRoomModule.ts) and the live-view data-truth guard.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { OFFICE_TEAM_DEPARTMENTS } from '../../../packages/contracts/src/officeBuilder.ts';
import { DEPARTMENTS } from '../src/hqBuilder/labels.ts';
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
    ['marketing', '#marketing'], ['technology', '#developer'], ['research', '#knowledge'], ['legal', '#files'],
    ['security', '#audit'], ['hr', '#members'], ['procurement', 'none'],
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
  test('a stored executive department opens the executive desk in any kind of room and any building', () => {
    for (const buildingKind of [null, undefined, 'office', 'personal', 'executive']) {
      for (const kind of ['office', 'lounge', 'warehouse']) {
        const m = resolveRoomModule(room({ department: 'executive', kind, name: 'Facilities', buildingKind }));
        assert.equal(moduleHash(m.target), '#executive');
        assert.equal(m.source, 'department');
      }
    }
    // Case and spaces in the stored value do not matter; the building kind alone is still only the fallback.
    assert.equal(hashOf({ department: ' Executive ', kind: 'office', name: 'Room' }), '#executive');
    assert.equal(resolveRoomModule(room({ buildingKind: 'executive' })).source, 'building');
  });
  test('the department wins over the name and the kind', () => {
    assert.equal(hashOf({ department: 'finance', kind: 'meeting', name: 'Tyre Engineering' }), '#finance');
  });
  test('an unknown department has no module and never guesses from the name', () => {
    const m = resolveRoomModule(room({ department: 'astrology', name: 'Finance' }));
    assert.deepEqual(m.target, { type: 'none', why: 'no_department_module' });
  });
  test('every storable department has an entry, a builder label and an English and Arabic name', () => {
    assert.deepEqual([...DEPARTMENTS], [...OFFICE_TEAM_DEPARTMENTS], 'the builder list is the contract list');
    for (const department of OFFICE_TEAM_DEPARTMENTS) {
      assert.ok(DEPARTMENT_MODULES[department], `${department} has a module entry (or an honest none)`);
      for (const lang of ['en', 'ar'] as const) {
        const catalog = CATALOGS[lang] as Record<string, string>;
        for (const prefix of ['hqb.dept.', 'team.dept.', 'hqSc.dept.']) assert.ok(catalog[`${prefix}${department}`], `${lang} ${prefix}${department}`);
      }
    }
  });
  test('only custom and procurement say no module is connected, and every other department opens a view', () => {
    const none = OFFICE_TEAM_DEPARTMENTS.filter((d) => DEPARTMENT_MODULES[d].target.type === 'none');
    assert.deepEqual([...none].sort(), ['custom', 'procurement']);
    for (const d of OFFICE_TEAM_DEPARTMENTS.filter((x) => !none.includes(x) && x !== 'operations')) assert.equal(DEPARTMENT_MODULES[d].target.type, 'view', d);
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

describe('a live meeting in the room', () => {
  test('the meeting module link opens that meeting, not the Meetings list', () => {
    const m = resolveRoomModule(room({ kind: 'meeting', liveMeetingId: 'agent_meeting_1' }));
    assert.deepEqual(m.target, { type: 'meeting', meetingId: 'agent_meeting_1' });
    assert.equal(moduleHash(m.target), '#office/meeting/agent_meeting_1');
    assert.equal(moduleHash(resolveRoomModule(room({ department: 'meeting', liveMeetingId: 'a b' })).target), '#office/meeting/a%20b');
  });
  test('without a live meeting, or in a room of another module, the link is unchanged', () => {
    assert.equal(hashOf({ kind: 'meeting' }), '#meetings');
    assert.equal(hashOf({ kind: 'meeting', liveMeetingId: null }), '#meetings');
    assert.equal(hashOf({ department: 'legal', kind: 'meeting', liveMeetingId: 'm1' }), '#files');
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
  test('reception opens the real visitor register (owner decision O4), never a link to another view', () => {
    const m = resolveRoomModule(room({ kind: 'reception' }));
    assert.deepEqual(m.target, { type: 'reception' });
    assert.equal(m.id, 'reception');
    assert.equal(moduleHash(m.target), null);
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
