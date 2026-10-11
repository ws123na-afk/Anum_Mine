import { expect, test, type Page } from '@playwright/test';

// Memories the Assistant remembered from the user's chats (docs/memory.md#from-conversations):
// labeled with their conversation, linked back to it, and deletable.
const now = '2026-10-11T09:00:00.000Z';

const chatMemory = {
  id: 'memory_chat', task_id: null, owner_user_id: 'user_local', content: 'The user prefers short answers with code samples.',
  provenance: { source_type: 'conversation', source_id: 'conv_trip', created_by_user_id: 'user_local', created_at: now, metadata: { conversation_title: 'Planning the Lisbon trip' } },
  retention: { kind: 'indefinite', expires_at: null }, created_at: now, updated_at: null,
};
const goneChatMemory = {
  ...chatMemory, id: 'memory_gone', content: 'The user works from Porto on Fridays.',
  provenance: { ...chatMemory.provenance, source_id: 'conv_deleted', metadata: { conversation_title: 'An old chat' } },
};
const taskMemory = {
  id: 'memory_task', task_id: 'task_launch', content: 'Launch is on Friday.',
  provenance: { source_type: 'user', source_id: null, created_by_user_id: 'user_local', created_at: now },
  retention: { kind: 'task', expires_at: null }, created_at: now,
};

async function mockApi(page: Page) {
  let memories: Record<string, unknown>[] = [chatMemory, goneChatMemory, taskMemory];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/memories') return json(memories);
    if (path.startsWith('/api/v1/memories/') && request.method() === 'DELETE') {
      memories = memories.filter((m) => m.id !== path.split('/').pop());
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/v1/retrieval/status') {
      return json({ embedding_model: 'anum-local-hash-v1', indexed: 3, failed: 0, skipped: 0, stale: 0, chunks: 3, sources: memories.map((m) => ({ source_type: 'memory', source_id: m.id, name: null, status: 'indexed', error: null, chunk_count: 1, embedding_model: 'anum-local-hash-v1', indexed_at: now })) });
    }
    if (path === '/api/v1/conversations') return json([{ id: 'conv_recent', title: 'Most recent chat', created_at: now, updated_at: now }]);
    if (path === '/api/v1/conversations/conv_trip') {
      return json({ id: 'conv_trip', title: 'Planning the Lisbon trip', created_at: now, updated_at: now, turns: [
        { id: 't1', conversation_id: 'conv_trip', role: 'user', content: 'I prefer short answers with code samples.', status: 'complete', created_at: now },
        { id: 't2', conversation_id: 'conv_trip', role: 'assistant', content: 'Noted.', status: 'complete', created_at: now },
      ] });
    }
    if (path === '/api/v1/conversations/conv_recent') return json({ id: 'conv_recent', title: 'Most recent chat', created_at: now, updated_at: now, turns: [] });
    if (path.startsWith('/api/v1/conversations/')) return json({ detail: 'Conversation not found' }, 404);
    return route.abort();
  });
}

test('a memory from a chat names its conversation, opens it and can be deleted', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#memory');
  const row = page.locator('.memoryRow').filter({ hasText: 'short answers with code samples' });
  await expect(row.getByText('From chat', { exact: true })).toBeVisible();
  await expect(row.getByText('Private to you · from “Planning the Lisbon trip”')).toBeVisible();
  // A task note keeps its task and has no chat link.
  const note = page.locator('.memoryRow').filter({ hasText: 'Launch is on Friday.' });
  await expect(note.getByText('task_launch')).toBeVisible();
  await expect(note.getByRole('button', { name: /Open chat/ })).toHaveCount(0);

  await row.getByRole('button', { name: 'Open chat Planning the Lisbon trip' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Assistant' })).toBeVisible();
  await expect(page.getByText('Noted.')).toBeVisible();
  await expect(page.getByText('I prefer short answers with code samples.')).toBeVisible();

  await page.getByRole('button', { name: 'Memory', exact: true }).click();
  const again = page.locator('.memoryRow').filter({ hasText: 'short answers with code samples' });
  await again.getByRole('button', { name: 'Delete memory' }).click();
  await again.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByText('Memory deleted.')).toBeVisible();
  await expect(page.locator('.memoryRow').filter({ hasText: 'short answers with code samples' })).toHaveCount(0);
});

test('opening a memory whose chat was deleted explains that its memories stay', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#memory');
  await page.getByRole('button', { name: 'Open chat An old chat' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'That chat was deleted.' })).toBeVisible();
});
