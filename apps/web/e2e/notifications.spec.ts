import { expect, test, type Page } from '@playwright/test';

// Settings › Notifications (docs/notifications.md): the workspace switches, whether the server can
// send phone push, and the caller's registered phones, which can be removed here.
const now = new Date().toISOString();
const phone = (id: string, label: string | null, platform: 'android' | 'ios', hint: string) => ({
  id, platform, label, token_hint: hint, created_at: now, updated_at: now, last_sent_at: null,
});

interface MockState {
  available: boolean;
  devices: ReturnType<typeof phone>[];
  prefs: Record<string, boolean>;
  puts: unknown[];
  deletes: string[];
  failDelete?: boolean;
  failStatus?: boolean;
}

async function mockApi(page: Page, options: Partial<MockState> = {}): Promise<MockState> {
  const state: MockState = {
    available: true,
    devices: [phone('device_pixel', 'Pixel 8', 'android', '1a2b'), phone('device_ipad', null, 'ios', '9z8y')],
    prefs: { task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: true, push_enabled: true },
    puts: [], deletes: [], ...options,
  };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/notification-preferences' && request.method() === 'GET') return json(state.prefs);
    if (path === '/api/v1/notification-preferences' && request.method() === 'PUT') {
      state.puts.push(request.postDataJSON());
      state.prefs = request.postDataJSON() as Record<string, boolean>;
      return json(state.prefs);
    }
    if (path === '/api/v1/push/status') {
      if (state.failStatus) return json({ error: { code: 'internal_error', message: 'The server could not answer' } }, 500);
      return json(state.available
        ? { available: true, provider: 'fcm', project_id: 'anum-example', message: 'Phone push is on. Each phone must allow notifications and be registered.' }
        : { available: false, provider: 'fcm', project_id: null, message: 'Phone push is off on this server: the operator has not set ANUM_FCM_SERVICE_ACCOUNT_JSON. Approvals and results still appear in the app.' });
    }
    if (path === '/api/v1/me/devices' && request.method() === 'GET') return json(state.devices);
    if (path.startsWith('/api/v1/me/devices/') && request.method() === 'DELETE') {
      const id = decodeURIComponent(path.split('/').pop() ?? '');
      state.deletes.push(id);
      if (state.failDelete) return json({ error: { code: 'not_found', message: 'Device not found' } }, 404);
      state.devices = state.devices.filter((item) => item.id !== id);
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/v1/onboarding') return json({ complete: true, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' });
    if (path === '/api/v1/me/preferences') return json({ remember_conversations: true, updated_at: now, conversation_memory: 'on' });
    if (path === '/api/v1/model-config') return json({ detail: 'No workspace model' }, 404);
    if (path === '/api/v1/conversations') return json([]);
    return route.abort();
  });
  return state;
}

test('lists my phones, removes one and saves the phone push switch', async ({ page }) => {
  const state = await mockApi(page);
  await page.goto('/#settings');
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('button', { name: 'Notifications' }).click();
  await expect(page).toHaveURL(/#notifications$/);

  const push = page.getByRole('region', { name: 'Phone push' });
  await expect(push.getByText('On', { exact: true })).toBeVisible();
  await expect(push.getByText(/^2 phones get pushes for this workspace/)).toBeVisible();
  const list = push.getByRole('list', { name: 'My registered phones' });
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await expect(list.getByText('Pixel 8 (Android, ends in 1a2b)')).toBeVisible();
  await expect(list.getByText('iPhone or iPad phone (iPhone or iPad, ends in 9z8y)')).toBeVisible();

  await push.getByRole('button', { name: 'Remove Pixel 8 (Android, ends in 1a2b)' }).click();
  await expect(push.getByRole('status')).toContainText('Removed Pixel 8 (Android, ends in 1a2b).');
  await expect(list.getByRole('listitem')).toHaveCount(1);
  expect(state.deletes).toEqual(['device_pixel']);

  const settings = page.getByRole('region', { name: 'Notifications' });
  const toggle = settings.getByRole('checkbox', { name: 'Phone push' });
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await settings.getByRole('button', { name: 'Save notifications' }).click();
  await expect(settings.getByRole('status')).toContainText('Saved.');
  await expect.poll(() => (state.puts.at(-1) as { push_enabled?: boolean } | undefined)?.push_enabled).toBe(false);
  // The phone section follows the saved switch at once.
  await expect(push.getByText('Off', { exact: true })).toBeVisible();
  await expect(push.getByText(/Your phones stay registered/)).toBeVisible();
});

test('says when the server cannot send push and who can turn it on', async ({ page }) => {
  await mockApi(page, { available: false, devices: [] });
  await page.goto('/#notifications');
  const push = page.getByRole('region', { name: 'Phone push' });
  await expect(push.getByText('Off on this server', { exact: true })).toBeVisible();
  await expect(push.getByText(/ANUM_FCM_SERVICE_ACCOUNT_JSON/)).toBeVisible();
  await expect(push.getByText(/person who runs this ANUM server/)).toBeVisible();
});

test('with no phone yet the next step is in the app; a failed load can be retried', async ({ page }) => {
  const state = await mockApi(page, { devices: [], failStatus: true });
  await page.goto('/#notifications');
  const push = page.getByRole('region', { name: 'Phone push' });
  await expect(push.getByRole('alert')).toContainText('Could not load phone push. The server could not answer');
  state.failStatus = false;
  await push.getByRole('button', { name: 'Retry' }).click();
  await expect(push.getByText('No phone yet', { exact: true })).toBeVisible();
  await expect(push.getByText(/turn on notifications under Settings › Notifications/)).toBeVisible();
});

test('a failed removal keeps the phone and says what failed', async ({ page }) => {
  await mockApi(page, { failDelete: true });
  await page.goto('/#notifications');
  const push = page.getByRole('region', { name: 'Phone push' });
  await push.getByRole('button', { name: 'Remove Pixel 8 (Android, ends in 1a2b)' }).click();
  await expect(push.getByRole('alert')).toContainText('Pixel 8 (Android, ends in 1a2b) was not removed. Device not found Try again.');
  await expect(push.getByRole('list', { name: 'My registered phones' }).getByRole('listitem')).toHaveCount(2);
});
