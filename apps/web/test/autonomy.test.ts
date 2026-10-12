// Unit tests for the autonomy helpers in src/lib/autonomy.ts (docs/autonomy.md).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { AutonomousAction, OrganizationGuardrails } from '@anum/contracts';
import {
  EMPTY_FILTERS, canUndoNow, feedQuery, filtersActive, floorWarnings, guardrailsBody, guardrailsDraft, levelChoices, levelLabel,
  mapAction, mapOverview, noticeFromEvent, parseCaps, proposalSentence, settingsBody, settingsDraft, splitLines, undoStatus,
  type ApiAction, type ApiOverview,
} from '../src/lib/autonomy.ts';
import { setLocale } from '../src/lib/i18n.ts';

afterEach(() => setLocale('en'));

const now = Date.parse('2026-10-11T12:00:00Z');

const apiAction: ApiAction = {
  id: 'auto_1', task_id: 'task_1', tool: 'email.archive', category: 'email', action_kind: 'archive', level: 'act_and_notify',
  pattern_key: 'email.archive', template: null, summary: 'Archived email m1', target: null, status: 'done', requested_by: 'owner_a',
  created_at: '2026-10-11T11:59:00Z', reversible: true, undo_available: true, undo_expires_at: '2026-10-11T12:05:00Z',
  undo_token: 'undo_example', can_undo: true, undone_by: null, undone_at: null, undo_error: null, pattern_level: null,
};

const overview: ApiOverview = {
  settings: {
    category_levels: { email: 'act_and_notify' }, amount_threshold: 0, recipients_threshold: 5, price_change_percent_threshold: 0,
    daily_caps: { email: 20 }, quiet_hours: { start: '22:00', end: '07:00', timezone: 'Asia/Riyadh' }, blocked_recipients: [],
    blocked_domains: ['competitor.example'], approved_templates: ['order_status'], proposal_after: 3, updated_by: 'owner_a', updated_at: '2026-10-11T10:00:00Z',
  },
  guardrails: {
    spending_limit: 1000, max_auto_amount: 100, max_auto_recipients: 10, max_auto_price_change_percent: null,
    category_ceilings: { commerce: 'ask_always' }, daily_caps: {}, quiet_hours: null, blocked_recipients: [], blocked_domains: [],
  },
  categories: [{ category: 'commerce', level: 'act_silently', ceiling: 'ask_always', effective: 'ask_always' }],
  recommendations: [{ kind: 'price_change', recommended: 'ask_always', protected: true, note: 'Always asks.' }],
  pending_proposals: 1, can_manage_workspace: true, can_manage_guardrails: false, can_tighten: true,
};

describe('mapping', () => {
  test('actions and the overview become client shapes', () => {
    const action = mapAction(apiAction);
    assert.equal(action.undoToken, 'undo_example');
    assert.equal(action.patternKey, 'email.archive');
    const mapped = mapOverview(overview);
    assert.equal(mapped.settings.approvedTemplates[0], 'order_status');
    assert.equal(mapped.guardrails.categoryCeilings.commerce, 'ask_always');
    assert.equal(mapped.canManageGuardrails, false);
  });
});

describe('undo', () => {
  test('is offered only while the window is open, to someone allowed, with a token', () => {
    const action = mapAction(apiAction);
    assert.equal(canUndoNow(action, now), true);
    assert.equal(canUndoNow(action, Date.parse('2026-10-11T12:06:00Z')), false);
    assert.equal(canUndoNow({ ...action, canUndo: false, undoToken: null }, now), false);
    assert.equal(canUndoNow({ ...action, status: 'undone' }, now), false);
  });

  test('status line: undo until, closed, undone by, nothing for irreversible actions', () => {
    const action = mapAction(apiAction);
    assert.match(undoStatus(action, now)?.text ?? '', /^Undo until /);
    assert.equal(undoStatus(action, Date.parse('2026-10-12T00:00:00Z'))?.text, 'Undo window closed');
    assert.equal(undoStatus({ ...action, status: 'undone', undoneBy: 'sara' }, now)?.text, 'Undone by sara');
    assert.equal(undoStatus({ ...action, reversible: false } as AutonomousAction, now), null);
  });
});

describe('feed query and notices', () => {
  test('only set filters are sent, with paging', () => {
    assert.equal(feedQuery(EMPTY_FILTERS), 'limit=25');
    assert.equal(feedQuery({ category: 'email', level: '', status: 'undone', q: '  archived ' }, 'auto_9', 10), 'category=email&status=undone&q=archived&limit=10&cursor=auto_9');
    assert.equal(filtersActive(EMPTY_FILTERS), false);
    assert.equal(filtersActive({ ...EMPTY_FILTERS, q: ' x ' }), true);
  });

  test('autonomy.action_taken events become notices; other events do not', () => {
    const notice = noticeFromEvent({ type: 'autonomy.action_taken', payload: { action_id: 'auto_1', summary: 'Archived', tool: 'email.archive', undo_until: '2026-10-11T12:05:00Z' } });
    assert.deepEqual(notice, { actionId: 'auto_1', summary: 'Archived', tool: 'email.archive', undoUntil: '2026-10-11T12:05:00Z' });
    assert.equal(noticeFromEvent({ type: 'task.created', payload: {} }), null);
    assert.equal(noticeFromEvent({ type: 'autonomy.action_taken', payload: {} }), null);
  });
});

describe('settings drafts', () => {
  test('round trip to the PUT body, lines de-duplicated', () => {
    const draft = settingsDraft(mapOverview(overview).settings);
    assert.equal(draft.dailyCaps, 'email = 20');
    assert.equal(draft.quiet.enabled, true);
    const body = settingsBody({ ...draft, approvedTemplates: 'order_status\n order_status \nshipping' });
    assert.ok(body.ok);
    if (body.ok) {
      assert.deepEqual(body.value.approved_templates, ['order_status', 'shipping']);
      assert.deepEqual(body.value.daily_caps, { email: 20 });
      assert.equal(body.value.category_levels.email, 'act_and_notify');
      assert.equal(body.value.category_levels.general, 'act_silently');
    }
  });

  test('refuses bad numbers, caps and times with a sentence', () => {
    const draft = settingsDraft(mapOverview(overview).settings);
    assert.equal(settingsBody({ ...draft, recipientsThreshold: '2.5' }).ok, false);
    assert.equal(settingsBody({ ...draft, proposalAfter: '1' }).ok, false);
    const caps = settingsBody({ ...draft, dailyCaps: 'email twenty' });
    assert.ok(!caps.ok && caps.error.includes('email twenty'));
    assert.equal(settingsBody({ ...draft, quiet: { ...draft.quiet, start: '25:00' } }).ok, false);
    assert.deepEqual(parseCaps('Email = 5\nwhatsapp.send_template: 2'), { ok: true, value: { email: 5, 'whatsapp.send_template': 2 } });
    assert.deepEqual(splitLines('a@x.example, b@x.example\n\na@x.example'), ['a@x.example', 'b@x.example']);
  });

  test('guardrails: empty numbers are no limit, ceilings only when chosen', () => {
    const g: OrganizationGuardrails = mapOverview(overview).guardrails;
    const draft = guardrailsDraft(g);
    const body = guardrailsBody({ ...draft, maxAutoAmount: '' });
    assert.ok(body.ok);
    if (body.ok) {
      assert.equal(body.value.max_auto_amount, null);
      assert.equal(body.value.spending_limit, 1000);
      assert.deepEqual(body.value.category_ceilings, { commerce: 'ask_always' });
    }
  });

  test('warns before saving above the organization floors', () => {
    const info = mapOverview(overview);
    const draft = { ...settingsDraft(info.settings), recipientsThreshold: '12', categoryLevels: { ...settingsDraft(info.settings).categoryLevels, commerce: 'act_and_notify' as const } };
    const warnings = floorWarnings(draft, info.guardrails);
    assert.equal(warnings.length, 2);
    assert.match(warnings[0], /limit of 10/);
    assert.deepEqual(levelChoices('act_and_notify').map((c) => c.allowed), [true, true, false]);
    assert.deepEqual(levelChoices(null).map((c) => c.allowed), [true, true, true]);
  });
});

describe('words', () => {
  test('levels and proposals read in both languages', () => {
    assert.equal(levelLabel('act_and_notify'), 'Act and notify');
    const proposal = { id: 'p', patternKey: 'x', tool: 'whatsapp.send_template', template: 'order_status', category: 'whatsapp' as const, actionKind: 'send' as const, proposedLevel: 'act_and_notify' as const, approvals: 3, status: 'pending' as const, createdAt: '' };
    assert.equal(proposalSentence(proposal), 'Let ANUM "Act and notify" for whatsapp.send_template with template order_status? You approved it 3 times.');
    setLocale('ar');
    assert.equal(levelLabel('ask_always'), 'اسأل دائماً');
    assert.match(proposalSentence(proposal), /3 مرات/);
  });
});
