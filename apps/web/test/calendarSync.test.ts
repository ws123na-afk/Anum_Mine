import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  chosenIds, eventSyncKind, extraCalendars, invitees, minutesSince, readCalendarConnectReturn, syncTarget, type CalendarEventView, type SyncConnection,
} from '../src/lib/calendar.ts';
import { calendarAr } from '../src/lib/messages/calendar.ar.ts';
import { calendarEn } from '../src/lib/messages/calendar.en.ts';

const connection = (over: Partial<SyncConnection>): SyncConnection => ({
  id: 'cal_1', provider: 'google', provider_name: 'Google Calendar', email: 'owner@example.com', display_name: null, connected_by: 'user_1', status: 'connected',
  fix: '', last_sync_at: null, last_sync_status: null, last_error: null, last_summary: {}, events_synced: 0, created_at: '2030-01-01T00:00:00Z', ...over,
});

describe('calendar connect return', () => {
  it('reads the connection id the provider flow came back with and cleans the address', () => {
    const found = readCalendarConnectReturn('https://app.example.com/?calendar_connect=coc_abc123#calendar');
    assert.deepEqual(found, { connection: 'coc_abc123', error: null, cleanUrl: '/#calendar' });
  });
  it('reads an error code and keeps other parameters', () => {
    const found = readCalendarConnectReturn('https://app.example.com/?x=1&calendar_connect_error=invalid_state#calendar');
    assert.deepEqual(found, { connection: null, error: 'invalid_state', cleanUrl: '/?x=1#calendar' });
  });
  it('ignores a normal address', () => {
    assert.equal(readCalendarConnectReturn('https://app.example.com/#calendar'), null);
    assert.equal(readCalendarConnectReturn('https://app.example.com/?email_connect=emb_1#inbox'), null);
  });
});

describe('calendar sync helpers', () => {
  it('sends new events to the oldest connection that works', () => {
    assert.equal(syncTarget([]), null);
    assert.equal(syncTarget([connection({ id: 'a', status: 'reauth_required' }), connection({ id: 'b' }), connection({ id: 'c' })])?.id, 'b');
    assert.equal(syncTarget([connection({ status: 'error' })]), null);
  });
  it('says where an event lives', () => {
    assert.equal(eventSyncKind({ connection_id: 'cal_1', sync_state: 'synced' }), 'synced');
    assert.equal(eventSyncKind({ connection_id: 'cal_1', sync_state: 'pending_push' }), 'waiting');
    assert.equal(eventSyncKind({ connection_id: null, sync_state: null }), 'local');
    assert.equal(eventSyncKind({ connection_id: null, sync_state: 'local' }), 'local');
    // Five failed sends in a row: ANUM stopped trying on its own and the detail offers Retry.
    assert.equal(eventSyncKind({ connection_id: 'cal_1', sync_state: 'pending_push', sync_failed: true }), 'failed');
    assert.equal(eventSyncKind({ connection_id: 'cal_1', sync_state: 'synced', sync_failed: false }), 'synced');
  });
  it('has every new sync, reply and email reminder text in both languages', () => {
    for (const key of ['calendar.sync.state.failed', 'calendar.sync.failedText', 'calendar.sync.retry', 'calendar.reply.scope.this', 'calendar.reply.thisOnly', 'calendar.form.emailReminder', 'calendar.reminder.withEmail'] as const) {
      assert.ok(calendarEn[key], key);
      assert.ok(calendarAr[key], key);
      assert.notEqual(calendarAr[key], calendarEn[key], key);
    }
    assert.equal('calendar.form.syncSeries' in calendarEn, false);
  });
  it('lists only attendees with an email as invitees', () => {
    const attendees = [
      { id: 'a', name: 'Sara', email: 'sara@example.com', response: 'invited' },
      { id: 'b', name: 'Omar', email: null, response: 'invited' },
    ] as CalendarEventView['attendees'];
    assert.deepEqual(invitees({ attendees }), ['sara@example.com']);
  });
  it('counts minutes since the last sync', () => {
    const now = new Date('2030-01-01T12:00:00Z');
    assert.equal(minutesSince(null, now), null);
    assert.equal(minutesSince('2030-01-01T11:30:00Z', now), 30);
    assert.equal(minutesSince('2030-01-01T13:00:00Z', now), 0);
  });
});

describe('calendar sync strings', () => {
  it('has Arabic for every sync string with the same placeholders', () => {
    const holders = (value: unknown) => JSON.stringify(value).match(/\{[a-z]+\}/g)?.sort().filter((v, i, all) => all.indexOf(v) === i).join(',') ?? '';
    for (const key of Object.keys(calendarEn).filter((k) => k.startsWith('calendar.sync') || k.startsWith('calendar.invite') || k === 'calendar.tab.sync')) {
      const arabic = (calendarAr as Record<string, unknown>)[key];
      assert.ok(arabic, key);
      assert.equal(holders(arabic), holders((calendarEn as Record<string, unknown>)[key]), key);
    }
  });
  it('uses no long dashes', () => {
    assert.doesNotMatch(JSON.stringify([calendarEn, calendarAr]), /[–—]/);
  });
});

describe('choosing calendars', () => {
  const calendars = [
    { id: 'primary', name: 'Owner', primary: true, selected: true, can_edit: true, color: null },
    { id: 'team', name: 'Team', primary: false, selected: true, can_edit: true, color: '#0b8043' },
    { id: 'holidays', name: 'Holidays', primary: false, selected: false, can_edit: false, color: null },
  ];
  it('lists the other calendars that sync and never the primary one', () => {
    assert.deepEqual(extraCalendars(connection({ calendars })).map((c) => c.name), ['Team']);
    assert.deepEqual(extraCalendars(connection({})), []);
  });
  it('sends only the chosen non-primary ids and refuses more than the limit', () => {
    assert.deepEqual(chosenIds(calendars, new Set(['primary', 'holidays']), 10), { ok: true, ids: ['holidays'] });
    assert.deepEqual(chosenIds(calendars, new Set(['team', 'holidays']), 1), { ok: false, count: 2 });
  });
  it('has every choice message in both languages', () => {
    const keys = Object.keys(calendarEn).filter((k) => k.startsWith('calendar.choose.'));
    assert.ok(keys.length >= 15);
    for (const key of keys) assert.ok(key in calendarAr, key);
  });
});
