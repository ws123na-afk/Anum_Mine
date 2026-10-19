// Unit tests for the Business support ticket helpers (src/lib/businessSupport.ts, docs/business.md#support-tickets).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { SupportPolicy } from '@anum/contracts';
import {
  defaultFilters, emptyTicketDraft, filtersActive, filtersToQuery, policyBody, policyDraft, resolveProblem, severityTone, slaBadge, slaLabel, slaTone, sourceHref,
  ticketBody, ticketQuery, ticketStatusLabel, ticketStatusTone, workingTimeText,
} from '../src/lib/businessSupport.ts';
import { tabFromHash } from '../src/lib/business.ts';
import { setLocale } from '../src/lib/i18n.ts';

const policy: SupportPolicy = {
  timezone: 'Asia/Riyadh', work_days: [6, 0, 1, 2, 3], start_hour: 9, end_hour: 17, note: '',
  targets: { critical: { response_hours: 2, resolution_hours: 16 }, high: { response_hours: 4, resolution_hours: 40 }, normal: { response_hours: 8, resolution_hours: 80 }, low: { response_hours: 16, resolution_hours: 160 } },
};

describe('business support', () => {
  test('the support tab and a ticket open from the hash', () => {
    assert.equal(tabFromHash('#business-support'), 'support');
    assert.equal(tabFromHash('#business-support/btkt_1'), 'support');
  });

  test('the SLA badge shows the more urgent clock, and a breach always wins', () => {
    setLocale('en');
    assert.deepEqual(slaBadge({ response: 'met', resolution: 'on_track', breached: false }), { state: 'on_track', clock: 'resolution', tone: 'neutral', label: 'On track' });
    assert.equal(slaBadge({ response: 'breached', resolution: 'on_track', breached: true }).state, 'breached');
    assert.equal(slaBadge({ response: 'breached', resolution: 'on_track', breached: true }).clock, 'response');
    assert.equal(slaBadge({ response: 'met', resolution: 'due_soon', breached: false }).tone, 'warning');
    assert.equal(slaBadge({ response: 'missed', resolution: 'paused', breached: false }).state, 'missed');
    assert.equal(slaBadge({ response: 'met', resolution: 'paused', breached: false }).label, 'Paused');
    assert.equal(slaBadge({ response: 'met', resolution: 'met', breached: false }).tone, 'success');
    assert.equal(slaTone('missed'), 'dangerTone');
    assert.equal(slaLabel('due_soon'), 'Due soon');
    setLocale('ar');
    assert.equal(slaLabel('breached'), 'متجاوز');
    setLocale('en');
  });

  test('labels and tones for statuses and severities', () => {
    assert.equal(ticketStatusLabel('waiting_on_customer'), 'Waiting on customer');
    assert.equal(ticketStatusTone('resolved'), 'success');
    assert.equal(ticketStatusTone('new'), 'warning');
    assert.equal(severityTone('critical'), 'dangerTone');
    assert.equal(severityTone('low'), 'neutral');
  });

  test('the source is a link to the inbox, never the message', () => {
    assert.equal(sourceHref('email'), '#email');
    assert.equal(sourceHref('whatsapp'), '#whatsapp');
    assert.equal(sourceHref('manual'), null);
  });

  test('filters become the API query; false flags are left out', () => {
    const base = defaultFilters();
    assert.equal(filtersActive(base), false);
    assert.equal(ticketQuery(filtersToQuery(base, 25, 0)), '?limit=25&offset=0&sort=updated&open_only=true');
    const mine = { ...base, q: ' printer ', status: 'in_progress' as const, severity: 'high' as const, who: 'me' as const, escalated: true, sort: 'due' as const };
    assert.equal(filtersActive(mine), true);
    assert.equal(ticketQuery(filtersToQuery(mine, 25, 50)), '?limit=25&offset=50&sort=due&q=printer&status=in_progress&severity=high&assignee=me&escalated=true');
    assert.equal(ticketQuery(filtersToQuery({ ...base, status: 'all', who: 'unassigned' }, 25, 0)), '?limit=25&offset=0&sort=updated&unassigned=true');
    assert.equal(ticketQuery({ limit: 10, offset: 0, status: ['new', 'triaged'] }), '?limit=10&offset=0&status=new&status=triaged');
  });

  test('a new ticket needs a title, valid contact details and its entitlement', () => {
    setLocale('en');
    const draft = emptyTicketDraft();
    assert.equal(ticketBody(draft).ok, false);
    const ok = ticketBody({ ...draft, title: '  Login   fails ', requesterEmail: 'sara@example.com', assigneeId: 'user_local' });
    assert.ok(ok.ok);
    if (ok.ok) {
      assert.equal(ok.value.title, 'Login fails');
      assert.equal(ok.value.requester_email, 'sara@example.com');
      assert.equal(ok.value.requester_phone, null);
      assert.equal(ok.value.entitlement, 'none');
      assert.equal(ok.value.support_offer_id, null);
      assert.equal(ok.value.assignee_id, 'user_local');
    }
    assert.equal(ticketBody({ ...draft, title: 'Login fails', requesterEmail: 'nope' }).ok, false);
    assert.equal(ticketBody({ ...draft, title: 'Login fails', requesterPhone: '123' }).ok, false);
    const plan = ticketBody({ ...draft, title: 'Login fails', entitlement: 'support_plan' });
    assert.equal(plan.ok ? '' : plan.error, 'Choose the support plan that covers this request, or set the entitlement to none.');
    assert.equal(ticketBody({ ...draft, title: 'Login fails', entitlement: 'project' }).ok, false);
    const withPlan = ticketBody({ ...draft, title: 'Login fails', entitlement: 'support_plan', offerId: 'boffer_1' });
    assert.equal(withPlan.ok && withPlan.value.support_offer_id, 'boffer_1');
  });

  test('resolving needs a resolution and a summary of at least 10 characters', () => {
    assert.ok(resolveProblem('', 'Restarted the sync service'));
    assert.ok(resolveProblem('fixed', 'short'));
    assert.equal(resolveProblem('fixed', 'Restarted the sync service'), null);
  });

  test('the policy round-trips and refuses bad hours and targets', () => {
    const draft = policyDraft(policy);
    const body = policyBody(draft);
    assert.ok(body.ok);
    if (body.ok) assert.deepEqual(body.value, { ...policy, work_days: [0, 1, 2, 3, 6] });
    assert.equal(policyBody({ ...draft, workDays: [] }).ok, false);
    assert.equal(policyBody({ ...draft, startHour: '17', endHour: '9' }).ok, false);
    assert.equal(policyBody({ ...draft, endHour: '25' }).ok, false);
    const order = policyBody({ ...draft, targets: { ...draft.targets, high: { response: '10', resolution: '5' } } });
    assert.equal(order.ok ? '' : order.error, 'The High resolution target must not be shorter than its response target.');
    assert.equal(policyBody({ ...draft, targets: { ...draft.targets, low: { response: '0', resolution: '5' } } }).ok, false);
  });

  test('the working time reads as days and hours in the time zone', () => {
    setLocale('en');
    assert.equal(workingTimeText(policy), 'Monday, Tuesday, Wednesday, Thursday, Sunday, 9:00 to 17:00 (Asia/Riyadh)');
  });
});
