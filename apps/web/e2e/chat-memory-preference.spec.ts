import { expect, test, type Page } from '@playwright/test';

// "Remember things from my chats" (docs/memory.md#from-conversations): the caller's own switch in
// Settings and in the Memory view, the way from Settings to the memories already remembered, and
// deleting all of them at once.
const now = '2026-10-11T09:00:00.000Z';
const chatMemory = (id: string, content: string) => ({
  id, task_id: null, owner_user_id: 'user_local', content,
  provenance: { source_type: 'conversation', source_id: 'conv_trip', created_by_user_id: 'user_local', created_at: now, metadata: { conversation_title: 'Planning the Lisbon trip' } },
  retention: { kind: 'indefinite', expires_at: null }, created_at: now, updated_at: null,
});
const taskMemory = {
  id: 'memory_task', task_id: 'task_launch', content: 'Launch is on Friday.',
  provenance: { source_type: 'user', source_id: null, created_by_user_id: 'user_local', created_at: now },
  retention: { kind: 'task', expires_at: null }, created_at: now,
};

interface MockState { remember: boolean; state?: string; failSave?: boolean; puts: unknown[]; memoryQueries: string[]; bulkDeletes: number }

async function mockApi(page: Page, options: Partial<MockState> = {}): Promise<MockState> {
  const state: MockState = { remember: true, puts: [], memoryQueries: [], bulkDeletes: 0, ...options };
  let memories: Record<string, unknown>[] = [chatMemory('memory_a', 'The user prefers short answers.'), chatMemory('memory_b', 'The user lives in Porto.'), taskMemory];
  const prefs = () => ({ remember_conversations: state.remember, updated_at: now, conversation_memory: state.state ?? (state.remember ? 'on' : 'off_by_user') });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/me/preferences' && request.method() === 'GET') return json(prefs());
    if (path === '/api/v1/me/preferences' && request.method() === 'PUT') {
      const body = request.postDataJSON() as { remember_conversations: boolean };
      state.puts.push(body);
      if (state.failSave) return json({ detail: 'Complete onboarding for this workspace first' }, 409);
      state.remember = body.remember_conversations;
      return json(prefs());
    }
    if (path === '/api/v1/memories' && request.method() === 'GET') {
      state.memoryQueries.push(url.search);
      const onlyChats = url.searchParams.get('source_type') === 'conversation';
      return json(memories.filter((m) => !onlyChats || (m.provenance as { source_type: string }).source_type === 'conversation'));
    }
    if (path === '/api/v1/memories' && request.method() === 'DELETE' && url.searchParams.get('source_type') === 'conversation') {
      state.bulkDeletes += 1;
      const before = memories.length;
      memories = memories.filter((m) => (m.provenance as { source_type: string }).source_type !== 'conversation');
      return json({ deleted: before - memories.length });
    }
    if (path === '/api/v1/retrieval/status') return json({ embedding_model: 'anum-local-hash-v1', indexed: 0, failed: 0, skipped: 0, stale: 0, chunks: 0, sources: [] });
    if (path === '/api/v1/onboarding') return json({ complete: true, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' });
    if (path === '/api/v1/notification-preferences') return json({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: true });
    if (path === '/api/v1/model-config') return json({ detail: 'No workspace model' }, 404);
    if (path === '/api/v1/conversations') return json([]);
    return route.abort();
  });
  return state;
}

test('the switch in Settings saves at once and leads to the chat memories, which can all be deleted', async ({ page }) => {
  const state = await mockApi(page);
  await page.goto('/#settings');
  const section = page.getByRole('region', { name: 'Chat memory' });
  const toggle = section.getByRole('checkbox', { name: 'Remember things from my chats' });
  await expect(toggle).toBeChecked();
  await expect(section.getByText('Only you can see them.', { exact: false })).toBeVisible();

  await toggle.uncheck();
  await expect(section.getByText('Saved. The Assistant will no longer remember or use things from your chats.')).toBeVisible();
  await expect(toggle).not.toBeChecked();
  await expect(section.getByText('Off', { exact: true })).toBeVisible();
  await expect.poll(() => state.puts).toEqual([{ remember_conversations: false }]);

  await section.getByRole('button', { name: 'Review or delete memories from my chats' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Memory' })).toBeVisible();
  await expect(page.getByRole('radio', { name: 'From my chats' })).toBeChecked();
  await expect.poll(() => state.memoryQueries.at(-1)).toBe('?source_type=conversation');
  const list = page.getByRole('region', { name: 'Memories from my chats' });
  await expect(list.locator('.memoryRow')).toHaveCount(2);
  await expect(list.getByText('Launch is on Friday.')).toHaveCount(0);
  // The Memory view shows the same switch, still off.
  await expect(page.getByRole('region', { name: 'Chat memory' }).getByRole('checkbox', { name: 'Remember things from my chats' })).not.toBeChecked();

  await page.getByRole('button', { name: 'Delete all from my chats' }).click();
  await page.getByRole('button', { name: 'Delete all', exact: true }).click();
  await expect(page.getByText('Deleted 2 memories from your chats.')).toBeVisible();
  await expect.poll(() => state.bulkDeletes).toBe(1);
  await expect(page.getByRole('heading', { name: 'No memories from your chats' })).toBeVisible();
  await page.getByRole('button', { name: 'Show all memories' }).click();
  await expect(page.getByRole('radio', { name: 'All memories' })).toBeChecked();
  await expect(page.getByText('Launch is on Friday.')).toBeVisible();
});

test('a failed save keeps the previous choice and says what to do', async ({ page }) => {
  await mockApi(page, { failSave: true });
  await page.goto('/#memory');
  const section = page.getByRole('region', { name: 'Chat memory' });
  const toggle = section.getByRole('checkbox', { name: 'Remember things from my chats' });
  await toggle.uncheck();
  await expect(section.getByRole('alert')).toContainText('Your choice was not saved. Complete onboarding for this workspace first Try again.');
  await expect(toggle).toBeChecked();
});

test('when the organization keeps chat memory off, the reason and who can change it are shown', async ({ page }) => {
  await mockApi(page, { state: 'off_for_organization' });
  await page.goto('/#memory');
  const section = page.getByRole('region', { name: 'Chat memory' });
  await expect(section.getByText('Off for your organization')).toBeVisible();
  await expect(section.getByText(/An organization administrator can allow/)).toBeVisible();
});
