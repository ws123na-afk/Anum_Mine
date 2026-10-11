// Unit tests for the "What ANUM learned" helpers in src/lib/learning.ts (docs/learning.md).
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import {
  appliedMessage, editLine, joinScope, overridesFrom, pageInfo, routineEvidence, scopeLabel, signalTotal, splitScope, stateStatus,
  styleForm, suggestionBlocker, summaryRows, switchEditable, type LearningProfile,
} from '../src/lib/learning.ts';
import { setLocale } from '../src/lib/i18n.ts';

afterEach(() => setLocale('en'));

const style = { tone: 'formal', length: 'short', target_words: 24, greeting: 'Dear', sign_off: 'Best regards', language: 'en', contact_languages: { 'sara@example.com': 'ar' }, emoji: false, edit_length_ratio: 0.6, samples: 4 } as const;
const profile = (overrides: LearningProfile['overrides'] = {}): LearningProfile => ({
  enabled: true, derived: { ...style, contact_languages: { ...style.contact_languages } }, overrides, effective: { ...style, contact_languages: {} }, derived_at: null, updated_at: null,
});

describe('learning state', () => {
  test('says what each state means and who can change it', () => {
    assert.equal(stateStatus('on').tone, 'success');
    assert.match(stateStatus('off_by_user').text, /You turned learning off/);
    const chat = stateStatus('off_chat_memory');
    assert.equal(chat.offerChatMemory, true);
    assert.match(chat.text, /Remember things from my chats/);
    assert.match(stateStatus('off_on_server').text, /ANUM_LEARNING_ENABLED/);
    assert.equal(stateStatus('something_new').badge, 'Unknown');
    assert.equal(switchEditable('on'), true);
    assert.equal(switchEditable('off_by_user'), true);
    assert.equal(switchEditable('off_chat_memory'), false);
  });

  test('reads in Arabic', () => {
    setLocale('ar');
    assert.equal(stateStatus('on').badge, 'يتعلّم');
    assert.equal(scopeLabel('surface:whatsapp'), 'مسودات واتساب');
  });
});

describe('scopes', () => {
  test('label, split and join every kind of scope', () => {
    assert.equal(scopeLabel('all'), 'Everything');
    assert.equal(scopeLabel('surface:email'), 'Email drafts');
    assert.equal(scopeLabel('contact:+966500000000'), 'Contact: +966500000000');
    assert.equal(scopeLabel('tool:external.action'), 'Tool: external.action');
    assert.deepEqual(splitScope('contact:a:b'), { kind: 'contact', target: 'a:b' });
    assert.deepEqual(splitScope('surface:chat'), { kind: 'surface:chat', target: '' });
    assert.deepEqual(splitScope('weird'), { kind: 'all', target: '' });
    assert.equal(joinScope('surface:task', 'ignored'), 'surface:task');
    assert.equal(joinScope('tool', '  web_search '), 'tool:web_search');
    assert.equal(joinScope('contact', '   '), null);
  });
});

describe('style form', () => {
  test('empty fields keep what was learned; "no greeting" sends an empty greeting', () => {
    const form = styleForm(profile({ tone: 'casual', greeting: '', contact_languages: { 'omar@example.com': 'en' } }));
    assert.equal(form.tone, 'casual');
    assert.equal(form.noGreeting, true);
    assert.deepEqual(form.contacts, [{ contact: 'omar@example.com', language: 'en' }]);
    const body = overridesFrom({ ...form, signOff: '  Thanks  ', contacts: [...form.contacts, { contact: '  ', language: 'ar' }] });
    assert.deepEqual(body, {
      tone: 'casual', length: null, greeting: '', sign_off: 'Thanks', language: null, notes: null,
      contact_languages: { 'omar@example.com': 'en' },
    });
    assert.equal(overridesFrom(styleForm(profile())).contact_languages, null);
  });
});

describe('lists', () => {
  test('page info for the pager', () => {
    assert.deepEqual(pageInfo(0, 10, 0), { page: 1, pages: 1, from: 0, to: 0, hasPrev: false, hasNext: false });
    assert.deepEqual(pageInfo(31, 10, 10), { page: 2, pages: 4, from: 11, to: 20, hasPrev: true, hasNext: true });
    assert.deepEqual(pageInfo(31, 10, 30), { page: 4, pages: 4, from: 31, to: 31, hasPrev: true, hasNext: false });
  });

  test('edit lines, routine evidence, totals and note summaries', () => {
    const edit = { words_before: 30, words_after: 12, words_added: 2, words_removed: 20, changed_ratio: 0.454, greeting_before: null, greeting_after: 'Dear', sign_off_before: null, sign_off_after: null };
    assert.equal(editLine({ metadata: { edit } }), 'Changed 45% · 30 → 12 words');
    assert.equal(editLine({ metadata: {} }), null);
    assert.equal(routineEvidence({ occurrences: 4, timezone: 'Asia/Riyadh', confidence: 0.5 }), 'Seen 4 times · 50% of weeks · Asia/Riyadh');
    assert.equal(signalTotal({ rating_up: 3, draft_edited: 2 }), 5);
    const rows = summaryRows({ ratings_up: 1, ratings_down: 2, drafts_accepted: 0, drafts_edited: 3, approvals_approved: 0, approvals_rejected: 2, retries: 1, run_failures: 4 });
    assert.equal(rows.length, 8);
    assert.deepEqual(rows.at(-1), { key: 'run_failures', label: 'Failed runs', value: 4 });
  });
});

describe('suggestions', () => {
  test('autonomy changes need an owner, and applying says what changed', () => {
    assert.match(suggestionBlocker({ needs_permission: 'policy:manage', status: 'open' }) ?? '', /workspace owner/);
    assert.equal(suggestionBlocker({ needs_permission: null, status: 'open' }), null);
    assert.equal(suggestionBlocker({ needs_permission: 'policy:manage', status: 'applied' }), null);
    assert.match(appliedMessage({ change: { type: 'approval_policy', medium_risk_requires_approval: false } }), /audit log/);
    assert.match(appliedMessage({ change: { type: 'preference', scope: 'all', text: 'x' } }), /learned preferences/);
  });
});
