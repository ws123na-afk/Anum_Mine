// Unit tests for the Privacy page helpers in src/lib/privacy.ts (docs/privacy.md).
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  PERMISSION_MESSAGE, confirmationMatches, deletedSentence, exportFileName, formatBytes, privacyErrorMessage, privateRows,
  memorySentence, providerLabel, providerSentence, retentionRows, sharedRows, totalOf, type PrivacySummary,
} from '../src/lib/privacy.ts';
import { setLocale, t } from '../src/lib/i18n.ts';

const summary: PrivacySummary = {
  tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member',
  private: { conversations: 2, conversation_turns: 6, private_memories: 1, retrieval_index_entries: 1, voice_sessions: 1, voice_transcript_segments: 3, notification_preferences: 1, user_preferences: 1, push_devices: 2, learning_signals: 2, learned_items: 3, favorites: 4, private_collections: 1, private_reports: 2, email_mailboxes: 1, email_messages: 4, email_drafts: 2 },
  shared: { tasks_created: 4, approvals_decided: 1, shared_memories_created: 0, files_uploaded: 2, email_mailboxes_connected: 1, email_drafts_sent: 2, whatsapp_messages_sent: 3, autonomy_actions_requested: 1 },
  retention: { conversation_days: 30, voice_transcript_days: 30, voice_transcript_choices: ['session', '30_days', 'permanent'], private_memories: 'Kept until you delete them.', task_memories: 'Kept with their task.', audit_records: 'Kept as the security record.' },
  model_provider: { deployment: 'anthropic', workspace: null, conversation_memory_enabled: true, conversation_memory: 'on' },
  privacy_policy_url: null, delete_confirmation: 'DELETE MY DATA', export_max_bytes: 26214400,
  account_note: 'Ask your administrator.', shared_note: 'Shared records stay.',
};

const apiError = (status: number, detail: string | null = null) => Object.assign(new Error(`ANUM API request failed: ${status}`), { status, detail });

describe('count rows', () => {
  test('list every private and shared count with a label, in a stable order', () => {
    assert.deepEqual(privateRows(summary.private).map((row) => row.value), [2, 6, 1, 1, 1, 3, 1, 1, 2, 0, 0, 0, 0, 2, 3, 4, 1, 2, 1, 4, 2, 0, 0, 0, 0, 0]);
    assert.equal(privateRows(summary.private).find((row) => row.key === 'learning_signals')?.label, 'Feedback ANUM learned from');
    assert.equal(privateRows(summary.private).find((row) => row.key === 'private_reports')?.label, 'Private reports');
    assert.equal(privateRows(summary.private).find((row) => row.key === 'email_drafts')?.label, 'Email drafts you never sent');
    assert.equal(privateRows(summary.private).at(-1)?.label, 'Finance records');
    assert.equal(privateRows(summary.private)[0].label, 'Assistant conversations');
    assert.deepEqual(sharedRows(summary.shared).map((row) => row.key), [
      'tasks_created', 'approvals_decided', 'shared_memories_created', 'files_uploaded',
      'email_mailboxes_connected', 'email_drafts_sent', 'whatsapp_messages_sent', 'autonomy_actions_requested',
      'work_items_created', 'work_items_assigned', 'tyre_connections_created', 'tyre_reports_created', 'commerce_connectors_created',
      'marketing_accounts_connected', 'marketing_campaigns_created', 'calendar_events_created', 'automation_triggers_created',
      'shared_agents_owned', 'shared_agent_memories',
    ]);
    assert.equal(totalOf(summary.private), 37);
    assert.equal(totalOf(summary.shared), 14);
    // Older servers send no email, WhatsApp or autonomy counts: the rows read 0.
    const older = { tasks_created: 1, approvals_decided: 0, shared_memories_created: 0, files_uploaded: 0 };
    assert.equal(sharedRows(older).find((row) => row.key === 'whatsapp_messages_sent')?.value, 0);
  });
});

describe('retentionRows', () => {
  test('describes the configured conversation retention', () => {
    assert.match(retentionRows(summary)[0].value, /after 30 days without activity/);
    assert.equal(retentionRows({ ...summary, retention: { ...summary.retention, conversation_days: 0 } })[0].value, 'Kept until you delete them.');
    assert.match(retentionRows({ ...summary, retention: { ...summary.retention, conversation_days: 1 } })[0].value, /after 1 day without/);
  });
});

describe('provider', () => {
  test('names where messages go for each provider', () => {
    assert.match(providerLabel('anthropic'), /Anthropic/);
    assert.match(providerLabel('openai_compatible'), /OpenAI-compatible/);
    assert.match(providerLabel('openai-compatible'), /OpenAI-compatible/);
    assert.match(providerLabel('ollama'), /own server/);
    assert.match(providerLabel('mock'), /nothing leaves/);
  });
  test('prefers the workspace override and leaves memory to its own sentence', () => {
    assert.match(providerSentence(summary), /sent to Anthropic/);
    const local = { ...summary, model_provider: { ...summary.model_provider, workspace: 'ollama' } };
    assert.match(providerSentence(local), /Ollama/);
    assert.doesNotMatch(providerSentence(local), /memor/);
  });
});

describe('memorySentence', () => {
  const withState = (conversation_memory: PrivacySummary['model_provider']['conversation_memory'], enabled = true): PrivacySummary =>
    ({ ...summary, model_provider: { ...summary.model_provider, conversation_memory_enabled: enabled, conversation_memory } });

  test('describes the post-reply extraction call only when memory is on for the caller', () => {
    const on = memorySentence(withState('on'));
    assert.match(on.text, /After each reply, a second short call .* picks out lasting facts/);
    assert.equal(on.offerSwitch, false);
    for (const state of ['off_by_user', 'off_on_server', 'off_for_organization', 'not_for_role'] as const) {
      const sentence = memorySentence(withState(state));
      assert.match(sentence.text, /no facts are picked out of your chats/, state);
      assert.doesNotMatch(sentence.text, /After each reply/, state);
    }
  });

  test('the deployment flag alone does not decide it: the caller may have turned it off', () => {
    const sentence = memorySentence(withState('off_by_user', true));
    assert.match(sentence.text, /You turned off Remember things from my chats/);
    assert.match(sentence.text, /no memories are added to your questions/);
    assert.equal(sentence.offerSwitch, true);
  });

  test('says who can change it, and that recall continues when only saving is blocked', () => {
    assert.match(memorySentence(withState('off_on_server', false)).text, /ANUM_CONVERSATION_MEMORY_ENABLED.*no memories are added/);
    const org = memorySentence(withState('off_for_organization'));
    assert.match(org.text, /organization administrator can allow the "conversation" source/);
    assert.match(org.text, /still added/);
    const role = memorySentence(withState('not_for_role'));
    assert.match(role.text, /workspace owner can change your role/);
    assert.match(role.text, /still added/);
    for (const state of ['off_on_server', 'off_for_organization', 'not_for_role'] as const) assert.equal(memorySentence(withState(state)).offerSwitch, false);
  });

  test('an older server without the field gets a neutral sentence, not a claim', () => {
    const old = { ...summary, model_provider: { deployment: 'anthropic', workspace: null, conversation_memory_enabled: true } } as unknown as PrivacySummary;
    assert.match(memorySentence(old).text, /did not say/);
  });
});

describe('memorySentence in Arabic', () => {
  test('every state and the switch label come from the Arabic catalog', () => {
    setLocale('ar');
    try {
      const of = (conversation_memory: PrivacySummary['model_provider']['conversation_memory']) =>
        memorySentence({ ...summary, model_provider: { ...summary.model_provider, conversation_memory } }).text;
      assert.match(of('off_by_user'), /^لقد أوقفت «تذكّر أشياء من محادثاتي»/);
      assert.match(of('on'), /بعد كل رد/);
      assert.match(of('off_on_server'), /ANUM_CONVERSATION_MEMORY_ENABLED/);
      // {recall} is filled in, in Arabic.
      assert.match(of('off_for_organization'), /لا تزال الذكريات/);
      assert.match(of('not_for_role'), /لا تزال الذكريات/);
      assert.doesNotMatch(of('off_for_organization'), /\{recall\}|Memories/);
      assert.equal(t('privacy.memory.change'), 'تغيير «تذكّر أشياء من محادثاتي»');
    } finally {
      setLocale('en');
    }
  });
});

describe('confirmation', () => {
  test('needs the exact phrase (surrounding spaces ignored)', () => {
    assert.equal(confirmationMatches('DELETE MY DATA', 'DELETE MY DATA'), true);
    assert.equal(confirmationMatches('  DELETE MY DATA ', 'DELETE MY DATA'), true);
    assert.equal(confirmationMatches('delete my data', 'DELETE MY DATA'), false);
    assert.equal(confirmationMatches('', 'DELETE MY DATA'), false);
  });
});

describe('export file', () => {
  test('takes the server file name or a dated fallback', () => {
    assert.equal(exportFileName('attachment; filename="anum-my-data-20261011-090000.json"'), 'anum-my-data-20261011-090000.json');
    assert.equal(exportFileName(null, new Date('2026-10-11T09:00:00Z')), 'anum-my-data-2026-10-11.json');
    assert.equal(formatBytes(512), '512 B');
    assert.equal(formatBytes(2048), '2.0 KB');
    assert.equal(formatBytes(26214400), '25.0 MB');
  });
});

describe('privacyErrorMessage', () => {
  test('explains a 403 with who can fix it', () => {
    assert.equal(privacyErrorMessage(apiError(403, 'Workspace permission denied'), 'load'), PERMISSION_MESSAGE);
  });
  test('keeps the server reason for an export over the cap and a wrong phrase', () => {
    assert.equal(privacyErrorMessage(apiError(413, 'Too large: ask your administrator.'), 'export'), 'Too large: ask your administrator.');
    assert.equal(privacyErrorMessage(apiError(422, 'Type DELETE MY DATA exactly to confirm. Nothing was deleted.'), 'delete'), 'Type DELETE MY DATA exactly to confirm. Nothing was deleted.');
  });
  test('says what failed and how to retry otherwise', () => {
    assert.match(privacyErrorMessage(apiError(500, 'boom'), 'export'), /^Could not prepare your download: boom Check your connection/);
    assert.match(privacyErrorMessage(new TypeError('Failed to fetch'), 'delete'), /^Could not delete your data: Failed to fetch Reload/);
    assert.match(privacyErrorMessage(apiError(401), 'load'), /Sign in again/);
  });
});

describe('deletedSentence', () => {
  test('counts what went and what stayed', () => {
    const result = { deleted: summary.private, kept: summary.shared, account_note: '', shared_note: '' };
    assert.equal(deletedSentence(result), 'Deleted 37 private items. 14 shared workspace records stay.');
    const nothing = { ...result, deleted: { ...summary.private, conversations: 0, conversation_turns: 0, private_memories: 0, retrieval_index_entries: 0, voice_sessions: 0, voice_transcript_segments: 0, notification_preferences: 0, user_preferences: 0, push_devices: 0, learning_signals: 0, learned_items: 0, favorites: 0, private_collections: 0, private_reports: 0, email_mailboxes: 0, email_messages: 0, email_drafts: 0 }, kept: { tasks_created: 1, approvals_decided: 0, shared_memories_created: 0, files_uploaded: 0 } };
    assert.equal(deletedSentence(nothing), 'There was nothing private to delete. 1 shared workspace record stays.');
    const note = 'ANUM deleted me@example.com and its tokens, but could not withdraw its access at Zoho Mail.';
    assert.equal(deletedSentence({ ...nothing, notes: [note] }), `There was nothing private to delete. 1 shared workspace record stays. ${note}`);
  });
});
