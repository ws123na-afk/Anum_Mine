// Unit tests for the November 2026 web depth batch: the Notifications page model, Favorites order,
// skill list paging, the palette's record links and the new deep links (calendar, audit, vehicles).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { setLocale, t } from '../src/lib/i18n.ts';
import {
  KNOWN_KINDS, currentCursor, firstPage, kindLabel, nextPage, notificationHash, pageRows, previousPage,
} from '../src/lib/notificationCenter.ts';
import { FAVORITE_SORTS, moveAnchor, moveItem } from '../src/lib/library.ts';
import { SKILL_MAX_PAGES, SKILL_PAGE, readAllSkillPages } from '../src/lib/catalog.ts';
import { PALETTE_RECORD_KINDS, paletteHash, viewFromHash } from '../src/lib/shell.ts';
import { eventFromHash } from '../src/lib/calendar.ts';
import { recordFromHash } from '../src/lib/audit.ts';
import { vehicleFromHash } from '../src/lib/vehicles.ts';

describe('notifications page', () => {
  test('every known kind has a label in both languages, and unknown kinds read as Other', () => {
    for (const locale of ['en', 'ar'] as const) {
      setLocale(locale);
      for (const kind of KNOWN_KINDS) assert.notEqual(kindLabel(kind), `ntf.kind.${kind}`);
      assert.equal(kindLabel('something_new'), t('ntf.kind.other'));
    }
    setLocale('en');
  });
  test('opening a notice goes to its module; a work item opens itself', () => {
    assert.equal(notificationHash({ kind: 'work_assigned', work_item_id: 'wi_1' }), null);
    assert.equal(notificationHash({ kind: 'calendar_reminder', work_item_id: null }), '#calendar');
    assert.equal(notificationHash({ kind: 'finance_daily_report', work_item_id: null }), '#finance-intel-reports');
    assert.equal(notificationHash({ kind: 'finance_loan_due', work_item_id: null }), '#finance');
    assert.equal(notificationHash({ kind: 'document_expiry', work_item_id: null }), '#vault');
    assert.equal(notificationHash({ kind: 'unheard_of', work_item_id: null }), null);
  });
  test('cursor trail: next pushes, previous pops, rows count from the page', () => {
    let state = firstPage();
    assert.equal(currentCursor(state), null);
    assert.deepEqual(pageRows(state, 20), { from: 1, to: 20 });
    state = nextPage(state, 'ntf_20');
    assert.equal(currentCursor(state), 'ntf_20');
    assert.deepEqual(pageRows(state, 7), { from: 21, to: 27 });
    state = previousPage(state);
    assert.equal(currentCursor(state), null);
    assert.deepEqual(previousPage(state), state);
  });
});

describe('favorites order', () => {
  test('moveItem moves one row and leaves bad indexes alone', () => {
    assert.deepEqual(moveItem(['a', 'b', 'c', 'd'], 3, 0), ['d', 'a', 'b', 'c']);
    assert.deepEqual(moveItem(['a', 'b', 'c'], 0, 2), ['b', 'c', 'a']);
    assert.deepEqual(moveItem(['a', 'b'], 0, 5), ['a', 'b']);
  });
  test('moving down lands after the target, moving up before it', () => {
    assert.deepEqual(moveAnchor(true, 'fav_2'), { after_id: 'fav_2' });
    assert.deepEqual(moveAnchor(false, 'fav_2'), { before_id: 'fav_2' });
    assert.equal(FAVORITE_SORTS[0], 'custom');
  });
});

describe('skill list paging', () => {
  test('reads pages until a short one', async () => {
    const offsets: number[] = [];
    const result = await readAllSkillPages(async ({ limit, offset }) => { offsets.push(offset); return Array.from({ length: offset === 0 ? limit : 3 }, (_, i) => offset + i); });
    assert.deepEqual(offsets, [0, SKILL_PAGE]);
    assert.equal(result.items.length, SKILL_PAGE + 3);
    assert.equal(result.truncated, false);
  });
  test('stops after the last allowed page and says so', async () => {
    let calls = 0;
    const result = await readAllSkillPages(async ({ limit }) => { calls += 1; return Array.from({ length: limit }, () => 1); });
    assert.equal(calls, SKILL_MAX_PAGES);
    assert.equal(result.truncated, true);
  });
});

describe('links into records', () => {
  test('the palette opens each record kind by a hash its view reads', () => {
    const expected = { customer: 'business', invoice: 'business', document: 'vault', vehicle: 'vehicles', meeting: 'meetings' } as const;
    for (const kind of PALETTE_RECORD_KINDS) assert.equal(viewFromHash(paletteHash(kind, 'id 1/2')), expected[kind]);
    assert.equal(paletteHash('vehicle', 'veh 1'), '#vehicles?vehicle=veh%201');
    assert.equal(vehicleFromHash(paletteHash('vehicle', 'veh_1')), 'veh_1');
  });
  test('calendar, audit and vehicle links are read, and anything odd is ignored', () => {
    assert.equal(viewFromHash('#calendar?event=evt_1'), 'calendar');
    assert.equal(eventFromHash('#calendar?event=evt_1'), 'evt_1');
    assert.equal(eventFromHash('#calendar'), null);
    assert.equal(eventFromHash('#calendar?event=<script>'), null);
    assert.equal(viewFromHash('#audit?record=aud_1'), 'audit');
    assert.equal(recordFromHash('#audit?record=aud_1'), 'aud_1');
    assert.equal(recordFromHash('#members?record=aud_1'), null);
    assert.equal(viewFromHash('#vehicles?vehicle=v1'), 'vehicles');
    assert.equal(vehicleFromHash('#vehicles-list'), null);
    assert.equal(viewFromHash('#notificationCenter'), 'notificationCenter');
    assert.equal(viewFromHash('#notifications'), 'settings');
  });
});
