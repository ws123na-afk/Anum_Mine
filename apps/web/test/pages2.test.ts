// Unit tests for the second pass of the shared page layout (docs/design/anum360.md#page-layout): the
// Calendar page, Recent calls, the Commerce band and stock table, the Team, Assistants and
// Organisations bands, and the ANUM Store counts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  addDays, calendarCounts, dayKey, emptyForm, eventBody, eventTiming, formFromEvent, formProblem, groupByDay, matchesEvent, parseAttendees,
  shiftAnchor, startOfWeek, visibleRange, type CalendarEventView,
} from '../src/lib/calendar.ts';
import { callMinutes, callsPath, mergeCalls, transcriptGone, type CallSummaryRow } from '../src/lib/callHistory.ts';
import { channelOfMarketplace, stockAndPrices, summaryFigures } from '../src/lib/commerce.ts';
import { assistantMatches, assistantTotals, organisationMatches, organisationTotals, workloadTotals } from '../src/lib/team.ts';
import { skillCounts } from '../src/lib/catalog.ts';
import { figure } from '../src/lib/pageSummary.ts';

const at = (y: number, m: number, d: number, h = 0, min = 0) => new Date(y, m - 1, d, h, min);
const event = (over: Partial<CalendarEventView> = {}): CalendarEventView => ({
  id: 'e1', title: 'Fleet review', kind: 'meeting', status: 'scheduled', starts_at: at(2026, 10, 13, 10).toISOString(), ends_at: at(2026, 10, 13, 11).toISOString(),
  attendees: [], created_by: 'u', created_at: '', updated_at: '', ...over,
});

describe('calendar range', () => {
  test('weeks start on Monday and a day view shows one day', () => {
    assert.equal(dayKey(startOfWeek(at(2026, 10, 15))), '2026-10-12');
    assert.equal(dayKey(startOfWeek(at(2026, 10, 18))), '2026-10-12');
    const week = visibleRange('week', at(2026, 10, 15, 13));
    assert.equal(week.days.length, 7);
    assert.equal(dayKey(week.end), '2026-10-19');
    const day = visibleRange('day', at(2026, 10, 15, 13));
    assert.deepEqual(day.days.map(dayKey), ['2026-10-15']);
    assert.equal(dayKey(shiftAnchor('week', at(2026, 10, 15), 1)), '2026-10-22');
    assert.equal(dayKey(shiftAnchor('day', at(2026, 10, 15), -1)), '2026-10-14');
  });
  test('an event over midnight is listed on both days, earliest first', () => {
    const late = event({ id: 'late', title: 'Night shift', starts_at: at(2026, 10, 13, 22).toISOString(), ends_at: at(2026, 10, 14, 2).toISOString() });
    const early = event({ id: 'early', starts_at: at(2026, 10, 13, 8).toISOString(), ends_at: at(2026, 10, 13, 9).toISOString() });
    const groups = groupByDay([late, early], [at(2026, 10, 13), at(2026, 10, 14), at(2026, 10, 15)]);
    assert.deepEqual(groups.map((g) => g.events.map((e) => e.id)), [['early', 'late'], ['late'], []]);
  });
  test('counts leave out cancelled events and count replies still missing for events to come', () => {
    const now = at(2026, 10, 13, 9);
    const counts = calendarCounts([
      event({ attendees: [{ id: 'a', name: 'Sara', response: 'invited' }, { id: 'b', name: 'Omar', response: 'accepted' }] }),
      event({ id: 'past', starts_at: at(2026, 10, 12, 9).toISOString(), ends_at: at(2026, 10, 12, 10).toISOString(), attendees: [{ id: 'c', name: 'Late', response: 'invited' }] }),
      event({ id: 'x', status: 'cancelled' }),
    ], now);
    assert.deepEqual(counts, { today: 1, range: 2, awaiting: 1, cancelled: 1 });
    assert.equal(eventTiming(event(), at(2026, 10, 13, 10, 30)), 'now');
    assert.equal(eventTiming(event(), at(2026, 10, 13, 12)), 'past');
  });
  test('search folds case and accents and looks at people', () => {
    const e = event({ location: 'Room 2', attendees: [{ id: 'a', name: 'Zoë Ali', email: 'zoe@example.com', response: 'invited' }] });
    assert.ok(matchesEvent(e, 'zoe'));
    assert.ok(matchesEvent(e, 'ROOM'));
    assert.ok(!matchesEvent(e, 'budget'));
  });
});

describe('calendar form', () => {
  test('attendee lines take names, emails or both, keeping known ids and replies', () => {
    const rows = parseAttendees('Sara Ali <SARA@example.com>\nOmar, omar@example.com\nkhalid@example.com\nNadia\n\n', [{ id: 'att_1', name: 'Sara Ali', email: 'sara@example.com', response: 'accepted' }]);
    assert.deepEqual(rows, [
      { name: 'Sara Ali', email: 'sara@example.com', id: 'att_1', role: null, response: 'accepted' },
      { name: 'Omar', email: 'omar@example.com' },
      { name: 'khalid', email: 'khalid@example.com' },
      { name: 'Nadia', email: null },
    ]);
  });
  test('problems are found before sending', () => {
    const form = emptyForm(at(2026, 10, 14), at(2026, 10, 13, 9));
    assert.equal(form.startTime, '09:00');
    assert.equal(formProblem(form), 'title');
    assert.equal(formProblem({ ...form, title: 'x', endTime: '08:00' }), 'order');
    assert.equal(formProblem({ ...form, title: 'x', conferenceUrl: 'http://meet' }), 'link');
    assert.equal(formProblem({ ...form, title: 'x' }), null);
  });
  test('the body sends local times as instants and empty text as null; editing round-trips', () => {
    const body = eventBody({ ...emptyForm(at(2026, 10, 14), at(2026, 10, 13, 9)), title: ' Review ', attendees: 'Sara <sara@example.com>' });
    assert.equal(body.title, 'Review');
    assert.equal(body.starts_at, at(2026, 10, 14, 9).toISOString());
    assert.equal(body.location, null);
    assert.deepEqual(body.attendees, [{ name: 'Sara', email: 'sara@example.com' }]);
    const back = formFromEvent(event({ attendees: [{ id: 'a', name: 'Sara', email: 'sara@example.com', response: 'invited' }] }));
    assert.equal(back.startTime, '10:00');
    assert.equal(back.attendees, 'Sara <sara@example.com>');
    assert.equal(dayKey(addDays(at(2026, 10, 31), 1)), '2026-11-01');
  });
  test('editing a multi-day event keeps its end date and every attendee role', () => {
    const multi = event({ starts_at: at(2026, 10, 14, 10).toISOString(), ends_at: at(2026, 10, 16, 15, 30).toISOString(),
      attendees: [{ id: 'a', name: 'Sara', email: 'sara@example.com', role: 'chair', response: 'accepted' }] });
    const form = formFromEvent(multi);
    assert.equal(form.endDate, '2026-10-16');
    assert.equal(form.endTime, '15:30');
    assert.equal(formProblem(form), null);
    const body = eventBody({ ...form, title: 'Renamed' }, multi.attendees);
    assert.equal(body.ends_at, multi.ends_at);
    assert.deepEqual(body.attendees, [{ id: 'a', name: 'Sara', email: 'sara@example.com', role: 'chair', response: 'accepted' }]);
    assert.equal(formProblem({ ...form, endDate: '2026-10-14', endTime: '09:00' }), 'order');
  });
});

describe('recent calls', () => {
  const row = (over: Partial<CallSummaryRow> = {}): CallSummaryRow => ({
    id: 'v1', locale: 'en-US', retention: 'permanent', assistant_name: 'Anum', status: 'completed', created_at: '2026-10-13T09:00:00Z', updated_at: '2026-10-13T09:04:20Z',
    expires_at: null, segment_count: 2, first_line: 'Hi', ...over,
  });
  test('paths carry the cursor and status, and pages merge without duplicates', () => {
    assert.equal(callsPath(null), '/api/v1/voice/sessions?limit=10');
    assert.equal(callsPath('abc', 5, 'completed'), '/api/v1/voice/sessions?limit=5&cursor=abc&status=completed');
    assert.deepEqual(mergeCalls([row()], [row(), row({ id: 'v2' })]).map((r) => r.id), ['v1', 'v2']);
  });
  test('length and why a transcript is missing', () => {
    assert.equal(callMinutes(row()), 4);
    assert.equal(callMinutes(row({ status: 'active' })), null);
    assert.equal(transcriptGone(row()), null);
    assert.equal(transcriptGone(row({ segment_count: 0, retention: 'session' })), 'erased');
    assert.equal(transcriptGone(row({ segment_count: 0, retention: '30_days', expires_at: '2026-01-01T00:00:00Z' }), new Date('2026-10-13')), 'expired');
    assert.equal(transcriptGone(row({ segment_count: 0 })), 'empty');
  });
});

describe('commerce overview', () => {
  test('low stock and price alerts merge by listing, worst first', () => {
    const rows = stockAndPrices(
      [{ listing_id: 'l1', sku: 'A', title: '', channel: 'amazon', marketplace: 'Amazon.sa', quantity: 3, threshold: 5 }, { listing_id: 'l2', sku: 'B', title: '', channel: 'noon', marketplace: 'noon Saudi', quantity: 0, threshold: 5 }],
      [{ listing_id: 'l1', sku: 'A', marketplace: 'Amazon.sa', price: 450, buy_box_price: 430, buy_box_winner: false, lowest_competitor_price: 430, currency: 'SAR' }, { listing_id: 'l3', sku: 'C', marketplace: 'noon UAE', price: 100, buy_box_price: null, buy_box_winner: null, lowest_competitor_price: 90, currency: 'AED' }],
    );
    assert.deepEqual(rows.map((r) => r.sku), ['B', 'A', 'C']);
    assert.equal(rows[1].price?.buyBoxLost, true);
    assert.equal(rows[2].channel, 'noon');
    assert.equal(channelOfMarketplace('My tyre shop'), 'own_store');
  });
  test('the summary numbers are read defensively', () => {
    assert.deepEqual(summaryFigures({ orders: 4, units: 9, revenue: { AED: 410, SAR: 1980 }, top_skus: [['MX', 4]] }), { orders: 4, units: 9, revenue: [{ currency: 'SAR', amount: 1980 }, { currency: 'AED', amount: 410 }], topSku: 'MX', topUnits: 4 });
    assert.deepEqual(summaryFigures({ orders: 'x' }), { orders: 0, units: 0, revenue: [], topSku: null, topUnits: 0 });
  });
});

describe('team, assistants and organisations bands', () => {
  const rows = [
    { assignee_type: 'user', assignee_id: 'u1', open: 2, in_progress: 1, blocked: 1, overdue: 1, due_this_week: 1 },
    { assignee_type: 'assistant', assignee_id: 'a1', open: 3, in_progress: 0, blocked: 0, overdue: 2, due_this_week: 2 },
  ];
  test('workload totals include unassigned open work', () => {
    assert.deepEqual(workloadTotals({ rows, unassigned_open: 4 }), { open: 9, inProgress: 1, blocked: 1, overdue: 3, dueThisWeek: 3, unassigned: 4, people: 2 });
  });
  test('assistant totals count only assistants and never call an unknown done count 0', () => {
    const list = [{ id: 'a1', enabled: true }, { id: 'a2', enabled: false }];
    assert.deepEqual(assistantTotals(list, rows, { a1: 5, a2: 1 }), { total: 2, enabled: 1, open: 3, overdue: 2, dueWeek: 2, done: 6 });
    assert.equal(assistantTotals(list, rows, { a1: 5, a2: null }).done, null);
    assert.ok(assistantMatches({ name: 'Sales', department: 'sales', routing_keywords: ['quote'], allowed_tools: [] }, 'QUO'));
    assert.ok(!assistantMatches({ name: 'Sales', department: 'sales', routing_keywords: [], allowed_tools: [] }, 'finance'));
  });
  test('organisation totals and search', () => {
    const orgs = [
      { display_name: 'Gulf Tyres', tenant_id: 'gulf_tyres', health: 'attention', workspaces: 1, active_members: 4, pending_invitations: 0, open_work_items: 7, overdue_work_items: 1 },
      { display_name: 'Desert Fleet', tenant_id: 'desert', health: 'active', workspaces: 2, active_members: 3, pending_invitations: 1, open_work_items: 2, overdue_work_items: 0 },
    ];
    assert.deepEqual(organisationTotals(orgs), { count: 2, attention: 1, workspaces: 3, members: 7, invitations: 1, openWork: 9, overdue: 1 });
    assert.deepEqual(orgs.filter((o) => organisationMatches(o, 'GULF')).map((o) => o.tenant_id), ['gulf_tyres']);
  });
  test('skill counts and a denied figure shows as unavailable', () => {
    assert.deepEqual(skillCounts([{ state: 'installed', upgrade: null }, { state: 'disabled', upgrade: {} as never }, { state: 'available', upgrade: null }], 2), { total: 5, builtin: 2, installed: 1, disabled: 1, available: 1, upgrades: 1 });
    assert.equal(figure('a', 'A', { state: 'denied' }, () => ({ value: '0' })).state, 'error');
  });
});
