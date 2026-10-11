// Unit tests for "Remember things from my chats" (src/lib/chatMemory.ts) and the chat filter path.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { CHAT_MEMORIES_PATH, PREFERENCES_PATH, chatMemoryStatus, deletedMessage, savedMessage, type ConversationMemoryState } from '../src/lib/chatMemory.ts';
import { memorySearchPath } from '../src/lib/capabilities.ts';

describe('chatMemoryStatus', () => {
  test('explains every state the API reports', () => {
    const states: ConversationMemoryState[] = ['on', 'off_by_user', 'off_on_server', 'off_for_organization', 'not_for_role'];
    for (const state of states) {
      const status = chatMemoryStatus({ conversation_memory: state });
      assert.ok(status.badge && status.text.length > 40, state);
    }
    assert.equal(chatMemoryStatus({ conversation_memory: 'on' }).tone, 'success');
    assert.match(chatMemoryStatus({ conversation_memory: 'on' }).text, /Only you can see them/);
    assert.match(chatMemoryStatus({ conversation_memory: 'off_by_user' }).text, /stay until you delete them/);
    // When someone else keeps it off, the text says who can change it.
    assert.match(chatMemoryStatus({ conversation_memory: 'off_on_server' }).text, /server administrator/);
    assert.match(chatMemoryStatus({ conversation_memory: 'off_for_organization' }).text, /organization administrator/);
    assert.match(chatMemoryStatus({ conversation_memory: 'not_for_role' }).text, /workspace owner/);
  });
  test('an unknown state is not presented as on', () => {
    const status = chatMemoryStatus({ conversation_memory: 'something_new' as ConversationMemoryState });
    assert.equal(status.tone, 'neutral');
    assert.equal(status.badge, 'Unknown');
  });
});

describe('messages and paths', () => {
  test('saved and deleted sentences', () => {
    assert.match(savedMessage({ remember_conversations: true }), /will remember/);
    assert.match(savedMessage({ remember_conversations: false }), /no longer/);
    assert.equal(deletedMessage(0), 'There were no memories from your chats to delete.');
    assert.equal(deletedMessage(1), 'Deleted 1 memory from your chats.');
    assert.equal(deletedMessage(3), 'Deleted 3 memories from your chats.');
  });
  test('the API paths', () => {
    assert.equal(PREFERENCES_PATH, '/api/v1/me/preferences');
    assert.equal(CHAT_MEMORIES_PATH, '/api/v1/memories?source_type=conversation');
    assert.equal(memorySearchPath('', undefined, true), '/api/v1/memories?source_type=conversation');
    assert.equal(memorySearchPath(' tea ', undefined, true), '/api/v1/memories?query=tea&source_type=conversation');
    assert.equal(memorySearchPath('tea'), '/api/v1/memories?query=tea');
  });
});
