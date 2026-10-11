// Unit tests for the Privacy page helpers in src/lib/privacy.ts (docs/privacy.md).
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  PERMISSION_MESSAGE, confirmationMatches, deletedSentence, exportFileName, formatBytes, privacyErrorMessage, privateRows,
  providerLabel, providerSentence, retentionRows, sharedRows, totalOf, type PrivacySummary,
} from '../src/lib/privacy.ts';

const summary: PrivacySummary = {
  tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member',
  private: { conversations: 2, conversation_turns: 6, private_memories: 1, retrieval_index_entries: 1, voice_sessions: 1, voice_transcript_segments: 3, notification_preferences: 1 },
  shared: { tasks_created: 4, approvals_decided: 1, shared_memories_created: 0, files_uploaded: 2 },
  retention: { conversation_days: 30, voice_transcript_days: 30, voice_transcript_choices: ['session', '30_days', 'permanent'], private_memories: 'Kept until you delete them.', task_memories: 'Kept with their task.', audit_records: 'Kept as the security record.' },
  model_provider: { deployment: 'anthropic', workspace: null, conversation_memory_enabled: true },
  privacy_policy_url: null, delete_confirmation: 'DELETE MY DATA', export_max_bytes: 26214400,
  account_note: 'Ask your administrator.', shared_note: 'Shared records stay.',
};

const apiError = (status: number, detail: string | null = null) => Object.assign(new Error(`ANUM API request failed: ${status}`), { status, detail });

describe('count rows', () => {
  test('list every private and shared count with a label, in a stable order', () => {
    assert.deepEqual(privateRows(summary.private).map((row) => row.value), [2, 6, 1, 1, 1, 3, 1]);
    assert.equal(privateRows(summary.private)[0].label, 'Assistant conversations');
    assert.deepEqual(sharedRows(summary.shared).map((row) => row.key), ['tasks_created', 'approvals_decided', 'shared_memories_created', 'files_uploaded']);
    assert.equal(totalOf(summary.private), 15);
    assert.equal(totalOf(summary.shared), 7);
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
  test('prefers the workspace override and mentions memory extraction when on', () => {
    assert.match(providerSentence(summary), /Anthropic.*private memories/);
    const local = { ...summary, model_provider: { deployment: 'anthropic', workspace: 'ollama', conversation_memory_enabled: false } };
    assert.match(providerSentence(local), /Ollama/);
    assert.doesNotMatch(providerSentence(local), /private memories/);
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
    assert.equal(deletedSentence(result), 'Deleted 15 private items. 7 shared workspace records stay.');
    const nothing = { ...result, deleted: { ...summary.private, conversations: 0, conversation_turns: 0, private_memories: 0, retrieval_index_entries: 0, voice_sessions: 0, voice_transcript_segments: 0, notification_preferences: 0 }, kept: { tasks_created: 1, approvals_decided: 0, shared_memories_created: 0, files_uploaded: 0 } };
    assert.equal(deletedSentence(nothing), 'There was nothing private to delete. 1 shared workspace record stays.');
  });
});
