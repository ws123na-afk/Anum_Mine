import { expect, test, type Page } from '@playwright/test';

// Settings -> Privacy (docs/privacy.md): what is stored, retention, Download my data and Delete my data.
const counts = { conversations: 2, conversation_turns: 6, private_memories: 1, retrieval_index_entries: 1, voice_sessions: 1, voice_transcript_segments: 3, notification_preferences: 1, user_preferences: 1, email_mailboxes: 1, email_messages: 3, email_drafts: 1 };
const zero = Object.fromEntries(Object.keys(counts).map((key) => [key, 0]));
const shared = { tasks_created: 4, approvals_decided: 1, shared_memories_created: 0, files_uploaded: 2, email_mailboxes_connected: 0, email_drafts_sent: 0, whatsapp_messages_sent: 2, autonomy_actions_requested: 1 };
const revokeNote = 'ANUM deleted me@example.com and its tokens, but could not withdraw its access at Zoho Mail. Remove ANUM in your Zoho Mail account’s security settings (third-party apps).';

function summary(privateCounts: Record<string, number>, memory = 'on') {
  return {
    tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner',
    private: privateCounts, shared,
    retention: { conversation_days: 30, voice_transcript_days: 30, voice_transcript_choices: ['session', '30_days', 'permanent'], private_memories: 'Kept until you delete them (Memory, or Delete my data).', task_memories: 'Kept with their task, or until the expiry set when they were saved.', audit_records: 'Kept as the workspace’s security record; they hold ids and counts, not your content.' },
    model_provider: { deployment: 'anthropic', workspace: null, conversation_memory_enabled: memory !== 'off_on_server', conversation_memory: memory },
    privacy_policy_url: 'https://example.com/privacy', delete_confirmation: 'DELETE MY DATA', export_max_bytes: 26214400,
    account_note: 'Your sign-in account is managed by your organization’s identity provider, not by ANUM. To close it, ask your workspace owner or organization administrator.',
    shared_note: 'Tasks, runs, approvals, shared memories and files belong to the workspace, so they are kept. Ask a workspace owner to remove them.',
  };
}

async function mockApi(page: Page, options: { summaryStatus?: number; exportStatus?: number; memory?: string } = {}) {
  let current: Record<string, number> = { ...counts };
  const calls = { deleted: [] as unknown[] };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' });
    if (path === '/api/v1/privacy') {
      if (options.summaryStatus) return json({ detail: 'Workspace permission denied' }, options.summaryStatus);
      return json(summary(current, options.memory));
    }
    if (path === '/api/v1/privacy/export') {
      if (options.exportStatus) return json({ detail: 'Your data is larger than this server’s export limit (25 MB). Delete conversations you no longer need, or ask your administrator to raise ANUM_PRIVACY_EXPORT_MAX_BYTES, then try again.' }, options.exportStatus);
      return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'content-disposition': 'attachment; filename="anum-my-data-20261011-090000.json"', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'X-Correlation-ID, Retry-After, Content-Disposition' }, body: JSON.stringify({ format: 'anum.privacy-export/v1' }) });
    }
    // The Workspace tab, for the way from Privacy to "Remember things from my chats".
    if (path === '/api/v1/me/preferences') return json({ remember_conversations: options.memory !== 'off_by_user', updated_at: null, conversation_memory: options.memory ?? 'on' });
    if (path === '/api/v1/onboarding') return json({ complete: true, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' });
    if (path === '/api/v1/notification-preferences') return json({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: true });
    if (path === '/api/v1/model-config') return json({ detail: 'No workspace model' }, 404);
    if (path === '/api/v1/privacy/delete' && request.method() === 'POST') {
      calls.deleted.push(request.postDataJSON());
      const deleted = current;
      current = { ...zero };
      return json({ deleted, kept: shared, account_note: summary(current).account_note, shared_note: summary(current).shared_note, notes: [revokeNote] });
    }
    return route.abort();
  });
  return calls;
}

test('a member sees what is stored, downloads it and deletes their private data after typing the phrase', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#privacy');
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Privacy' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'What ANUM stores about you' })).toBeVisible();
  await expect(page.getByText('Assistant conversations').first()).toBeVisible();
  await expect(page.getByText('Private mailboxes you connected', { exact: true })).toBeVisible();
  await expect(page.getByText('WhatsApp messages you sent', { exact: true })).toBeVisible();
  await expect(page.getByText(/Deleted after 30 days without activity/)).toBeVisible();
  await expect(page.getByText(/sent to Anthropic \(Claude\)/)).toBeVisible();
  await expect(page.getByTestId('privacy-memory')).toContainText('After each reply, a second short call to the same provider picks out lasting facts');
  await expect(page.getByRole('button', { name: 'Change Remember things from my chats' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Read the privacy policy' })).toHaveAttribute('href', 'https://example.com/privacy');

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download my data' }).click();
  expect((await download).suggestedFilename()).toBe('anum-my-data-20261011-090000.json');
  await expect(page.getByText(/Downloaded anum-my-data-20261011-090000.json/)).toBeVisible();

  const remove = page.getByRole('button', { name: 'Delete my data' });
  await expect(remove).toBeDisabled();
  await page.getByLabel('Type DELETE MY DATA to confirm').fill('delete my data');
  await expect(remove).toBeDisabled();
  await page.getByLabel('Type DELETE MY DATA to confirm').fill('DELETE MY DATA');
  await remove.click();
  await expect(page.getByText(/Deleted 21 private items\. 10 shared workspace records stay\. ANUM deleted me@example\.com/)).toBeVisible();
  expect(calls.deleted).toEqual([{ confirm: 'DELETE MY DATA' }]);
  await expect(page.getByText('Nothing private is stored for you here yet.', { exact: false })).toBeVisible();
});

test('an export over the server limit says how to fix it', async ({ page }) => {
  await mockApi(page, { exportStatus: 413 });
  await page.goto('/#privacy');
  await page.getByRole('button', { name: 'Download my data' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'ANUM_PRIVACY_EXPORT_MAX_BYTES' })).toBeVisible();
});

test('a caller without an active membership is told who can help', async ({ page }) => {
  await mockApi(page, { summaryStatus: 403 });
  await page.goto('/#privacy');
  await expect(page.getByRole('alert').filter({ hasText: 'Ask a workspace owner to restore your membership' })).toBeVisible();
});

test('the Workspace and Privacy sections switch from Settings', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#privacy');
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await expect(page).toHaveURL(/#settings$/);
  await page.getByRole('button', { name: 'Privacy', exact: true }).click();
  await expect(page).toHaveURL(/#privacy$/);
  await expect(page.getByRole('heading', { name: 'Delete my data' })).toBeVisible();
});

test('when the caller turned chat memory off, the page says nothing is picked out and leads to the switch', async ({ page }) => {
  await mockApi(page, { memory: 'off_by_user' });
  await page.goto('/#privacy');
  const memory = page.getByTestId('privacy-memory');
  await expect(memory).toContainText('You turned off Remember things from my chats, so no facts are picked out of your chats and no memories are added to your questions.');
  await expect(memory).not.toContainText('After each reply');
  await page.getByRole('button', { name: 'Change Remember things from my chats' }).click();
  await expect(page).toHaveURL(/#settings$/);
  await expect(page.getByRole('button', { name: 'Workspace', exact: true })).toHaveAttribute('aria-current', 'page');
  const toggle = page.getByRole('checkbox', { name: /Remember things from my chats/ });
  await expect(toggle).toBeFocused();
  await expect(toggle).toBeInViewport();
  await expect(toggle).not.toBeChecked();
});

for (const [state, expected, hint] of [
  ['off_on_server', 'Chat memory is turned off on this server', 'no memories are added to your questions'],
  ['off_for_organization', "Your organization's memory policy does not allow memories from chats, so no facts are picked out of your chats.", 'organization administrator can allow'],
  ['not_for_role', 'Your role in this workspace cannot save memories, so no facts are picked out of your chats.', 'workspace owner can change your role'],
] as const) {
  test(`chat memory ${state}: the page states what happens and who can change it`, async ({ page }) => {
    await mockApi(page, { memory: state });
    await page.goto('/#privacy');
    const memory = page.getByTestId('privacy-memory');
    await expect(memory).toContainText(expected);
    await expect(memory).toContainText(hint);
    await expect(memory).not.toContainText('After each reply');
    await expect(page.getByRole('button', { name: 'Change Remember things from my chats' })).toHaveCount(0);
  });
}

test.describe('in Arabic', () => {
  test.use({ locale: 'ar-SA' });

  test('the chat memory sentence and the way to the switch are translated, right to left', async ({ page }) => {
    await mockApi(page, { memory: 'off_by_user' });
    await page.goto('/#privacy');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.getByTestId('privacy-memory')).toContainText('لقد أوقفت «تذكّر أشياء من محادثاتي»، لذلك لا تُستخرج أي حقائق من محادثاتك');
    await page.getByRole('button', { name: 'تغيير «تذكّر أشياء من محادثاتي»' }).click();
    const toggle = page.getByRole('checkbox', { name: /تذكّر أشياء من محادثاتي/ });
    await expect(toggle).toBeFocused();
    await expect(toggle).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
  });
});
