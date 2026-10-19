import { expect, test, type Page, type Route } from '@playwright/test';

// File versions and the storage screen against a mocked API (docs/files.md#versions): upload a
// new version, list and page the older ones, download, restore and delete, the quota refusal, what a
// viewer sees, and the storage screen with its meter, kinds and largest files.
const now = '2026-10-06T09:00:00.000Z';
type Json = Record<string, unknown>;

const indexed = { status: 'indexed', error: null, chunk_count: 1, embedding_model: 'anum-local-hash-v1', indexed_at: now };
const plan = { id: 'file_plan', name: 'plan.txt', content_type: 'text/plain', kind: 'text', size_bytes: 300, sha256: 'a'.repeat(64), created_by: 'user_local', created_at: now,
  updated_by: null, updated_at: null, can_manage: true, previewable: true, index: indexed, version: 3 };
const photo = { ...plan, id: 'file_photo', name: 'photo.png', content_type: 'image/png', kind: 'image', size_bytes: 500, previewable: false, version: 1, index: { ...indexed, status: 'skipped', error: 'not_text' } };

function version(number: number, extra: Json = {}): Json {
  return { id: `ver_${number}`, file_id: 'file_plan', version: number, content_type: 'text/plain', size_bytes: 100 + number, sha256: String(number).repeat(64).slice(0, 64),
    created_by: number % 2 ? 'user_local' : 'user_other', created_at: now, replaced_by: 'user_local', replaced_at: now, scan_status: 'clean', downloadable: true, ...extra };
}

interface Options {
  role?: 'owner' | 'viewer';
  older?: Json[];
  quota?: number;
  versionsStatus?: 200 | 403 | 500;
  storageStatus?: 200 | 403 | 500;
  uploadRefusal?: string;
}

async function mockVersions(page: Page, options: Options = {}) {
  const viewer = options.role === 'viewer';
  let older = [...(options.older ?? [version(2), version(1)])];
  let current: Json = { ...plan, version: older.length + 1, can_manage: !viewer };
  let versionsStatus = options.versionsStatus ?? 200;
  let storageStatus = options.storageStatus ?? 200;
  const quota = options.quota ?? 10_000;
  const calls: { method: string; path: string; query: string }[] = [];
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const envelope = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    if (path.startsWith('/api/v1/files')) calls.push({ method, path, query: url.search });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    const used = () => Number(current.size_bytes) + older.reduce((sum, item) => sum + Number(item.size_bytes), 0) + Number(photo.size_bytes);
    const usage = () => ({ used_bytes: used(), file_count: 2, version_bytes: older.reduce((sum, item) => sum + Number(item.size_bytes), 0), version_count: older.length,
      quota_bytes: quota, max_upload_bytes: 4096, link_ttl_seconds: 300, accepted: 'text (.txt) and images (.png)', accepted_extensions: ['.png', '.txt'], can_upload: !viewer, can_manage_all: !viewer });
    if (path === '/api/v1/files/usage') return json(usage());
    if (path === '/api/v1/files/storage') {
      if (storageStatus === 403) return envelope(403, 'forbidden', 'Seeing files needs a viewer, member or owner role in this workspace.');
      if (storageStatus === 500) return envelope(500, 'internal_error', 'Metadata store unavailable');
      const extra = older.reduce((sum, item) => sum + Number(item.size_bytes), 0);
      return json({ usage: usage(), by_kind: [{ kind: 'text', file_count: 1, bytes: current.size_bytes }, { kind: 'image', file_count: 1, bytes: photo.size_bytes }],
        largest: [{ file: current, version_count: older.length, version_bytes: extra, total_bytes: Number(current.size_bytes) + extra }, { file: photo, version_count: 0, version_bytes: 0, total_bytes: photo.size_bytes }] });
    }
    if (path === '/api/v1/files' && method === 'GET') return json([current, photo]);
    if (path === '/api/v1/files/file_plan/versions' && method === 'GET') {
      if (versionsStatus === 403) return envelope(403, 'forbidden', 'Seeing files needs a viewer, member or owner role in this workspace.');
      if (versionsStatus === 500) return envelope(500, 'internal_error', 'Metadata store unavailable');
      const limit = Number(url.searchParams.get('limit') ?? 20), offset = Number(url.searchParams.get('offset') ?? 0);
      return json({ file: current, items: older.slice(offset, offset + limit), total: older.length, limit, offset });
    }
    if (path === '/api/v1/files/file_plan/versions' && method === 'POST') {
      if (options.uploadRefusal) return json({ error: { code: 'payload_too_large', message: options.uploadRefusal, correlation_id: 'corr', details: [{ type: 'storage_quota', message: 'used=1 quota=1' }] } }, 413);
      const size = request.postDataBuffer()?.length ?? 0;
      older = [{ ...version(Number(current.version), { size_bytes: current.size_bytes, sha256: current.sha256 }) }, ...older];
      current = { ...current, version: Number(current.version) + 1, size_bytes: size, sha256: 'f'.repeat(64) };
      return json(current, 201);
    }
    const action = /^\/api\/v1\/files\/file_plan\/versions\/(\w+)(?:\/(\w+))?$/.exec(path);
    if (action) {
      const [, id, what] = action;
      const found = older.find((item) => item.id === id);
      if (!found) return envelope(404, 'not_found', 'Version not found: it may have been deleted. Reload the versions.');
      if (what === 'content') return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'old bytes' });
      if (what === 'restore') {
        older = [version(Number(current.version), { size_bytes: current.size_bytes, sha256: current.sha256 }), ...older];
        current = { ...current, version: Number(current.version) + 1, size_bytes: found.size_bytes, sha256: found.sha256 };
        return json(current);
      }
      if (method === 'DELETE') { older = older.filter((item) => item.id !== id); return route.fulfill({ status: 204 }); }
    }
    if (path === '/api/v1/files/file_plan' && method === 'GET') return json(current);
    if (path === '/api/v1/files/file_plan/preview') return json({ kind: 'text', text: 'plan text', truncated: false, size_bytes: current.size_bytes });
    if (path === '/api/v1/files/file_photo' && method === 'GET') return json(photo);
    if (path === '/api/v1/retrieval/status') return json({ embedding_model: 'anum-local-hash-v1', indexed: 1, failed: 0, skipped: 1, stale: 0, chunks: 1, sources: [] });
    if (method === 'GET' && path.startsWith('/api/v1/')) return json([]);
    return route.abort();
  });
  return { calls, setVersions: (status: 200 | 403 | 500) => { versionsStatus = status; }, setStorage: (status: 200 | 403 | 500) => { storageStatus = status; } };
}

async function openPlan(page: Page) {
  await page.goto('/#files');
  await expect(page.getByRole('heading', { name: 'Documents', level: 1 })).toBeVisible();
  await page.getByRole('button', { name: 'Show details of plan.txt' }).click();
  return page.getByRole('region', { name: 'plan.txt' });
}

test('older versions list, download, restore and delete', async ({ page }) => {
  const api = await mockVersions(page);
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await expect(versions.getByText('Current: version 3')).toBeVisible();
  await expect(versions.getByRole('listitem')).toHaveCount(2);
  await expect(versions.getByText('Version 2', { exact: true })).toBeVisible();

  await versions.getByRole('button', { name: 'Download version 1 of plan.txt' }).click();
  await expect.poll(() => api.calls.some((call) => call.path.endsWith('/versions/ver_1/content'))).toBe(true);

  await versions.getByRole('button', { name: 'Restore version 1 of plan.txt' }).click();
  await expect(versions.getByText('Version 1 is current again, as version 4. The version it replaced was kept.')).toBeVisible();
  await expect(versions.getByText('Current: version 4')).toBeVisible();
  await expect(versions.getByRole('listitem')).toHaveCount(3);

  await versions.getByRole('button', { name: 'Delete version 2 of plan.txt' }).click();
  await expect(versions.getByRole('status').filter({ hasText: 'Delete version 2?' })).toHaveCount(1);
  await versions.getByRole('button', { name: 'Keep version 2' }).click();
  await expect(versions.getByRole('listitem')).toHaveCount(3);
  await versions.getByRole('button', { name: 'Delete version 2 of plan.txt' }).click();
  await versions.getByRole('button', { name: 'Delete version 2 permanently' }).click();
  await expect(versions.getByText('Version 2 deleted.')).toBeVisible();
  await expect(versions.getByRole('listitem')).toHaveCount(2);
});

test('a new version is uploaded, becomes current and keeps the old one', async ({ page }) => {
  const api = await mockVersions(page);
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await versions.getByLabel('Choose the new version of plan.txt').setInputFiles({ name: 'plan.txt', mimeType: 'text/plain', buffer: Buffer.from('a newer plan') });
  await expect(versions.getByText('Version 4 of plan.txt is now current.')).toBeVisible();
  await expect(versions.getByRole('listitem')).toHaveCount(3);
  expect(api.calls.some((call) => call.method === 'POST' && call.path === '/api/v1/files/file_plan/versions')).toBe(true);

  // A file of another type is refused before sending, with the reason.
  await versions.getByLabel('Choose the new version of plan.txt').setInputFiles({ name: 'plan.png', mimeType: 'image/png', buffer: Buffer.from('x') });
  await expect(versions.getByRole('alert').filter({ hasText: 'A new version keeps the file type (.txt)' })).toBeVisible();
});

test('the quota refusal from the server says what to free', async ({ page }) => {
  await mockVersions(page, { uploadRefusal: 'This workspace has used 9.8 KiB of its 9.8 KiB storage quota, so a 12 B file does not fit. Delete files or older versions you no longer need (Storage lists the largest), or ask the operator to raise ANUM_FILES_WORKSPACE_QUOTA_BYTES.' });
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await versions.getByLabel('Choose the new version of plan.txt').setInputFiles({ name: 'plan.txt', mimeType: 'text/plain', buffer: Buffer.from('a newer plan') });
  await expect(versions.getByRole('alert')).toContainText('The new version of plan.txt was not stored: This workspace has used 9.8 KiB');
  await expect(versions.getByRole('alert')).toContainText('older versions you no longer need');
  await expect(versions.getByText('Current: version 3')).toBeVisible();
});

test('a client-side quota check warns before sending', async ({ page }) => {
  const api = await mockVersions(page, { quota: 1050 });
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await versions.getByLabel('Choose the new version of plan.txt').setInputFiles({ name: 'plan.txt', mimeType: 'text/plain', buffer: Buffer.alloc(900, 'x') });
  await expect(versions.getByRole('alert')).toContainText('does not fit');
  expect(api.calls.some((call) => call.method === 'POST')).toBe(false);
});

test('older versions page with Show more', async ({ page }) => {
  const many = Array.from({ length: 12 }, (_, i) => version(12 - i));
  const api = await mockVersions(page, { older: many });
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await expect(versions.getByRole('listitem')).toHaveCount(10);
  await versions.getByRole('button', { name: 'Show more versions (10 of 12)' }).click();
  await expect(versions.getByRole('listitem')).toHaveCount(12);
  await expect(versions.getByRole('button', { name: /Show more versions/ })).toHaveCount(0);
  expect(api.calls.some((call) => call.query.includes('offset=10'))).toBe(true);
});

test('a quarantined version cannot be downloaded or restored', async ({ page }) => {
  await mockVersions(page, { older: [version(1, { downloadable: false, scan_status: 'infected', scan_signature: 'Test.Sig' })] });
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await expect(versions.getByText('Quarantined: the malware scanner found Test.Sig')).toBeVisible();
  await expect(versions.getByRole('button', { name: /Download version 1/ })).toHaveCount(0);
  await expect(versions.getByRole('button', { name: /Restore version 1/ })).toHaveCount(0);
  await expect(versions.getByRole('button', { name: 'Delete version 1 of plan.txt' })).toBeVisible();
});

test('a viewer sees the versions and may download them but not change them', async ({ page }) => {
  await mockVersions(page, { role: 'viewer' });
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await expect(versions.getByRole('listitem')).toHaveCount(2);
  await expect(versions.getByRole('button', { name: 'Upload new version' })).toHaveCount(0);
  await expect(versions.getByRole('button', { name: /Restore version/ })).toHaveCount(0);
  await expect(versions.getByRole('button', { name: /Delete version/ })).toHaveCount(0);
  await expect(versions.getByRole('button', { name: 'Download version 2 of plan.txt' })).toBeVisible();
  await expect(versions.getByText('Only the person who uploaded this file or a workspace owner can change its versions.')).toBeVisible();
});

test('no earlier versions, a failed load and a denied load each say so', async ({ page }) => {
  const api = await mockVersions(page, { older: [], versionsStatus: 500 });
  const detail = await openPlan(page);
  const versions = detail.getByRole('region', { name: 'Versions' });
  await expect(versions.getByRole('alert').filter({ hasText: 'Could not load the versions: Metadata store unavailable' })).toBeVisible();
  api.setVersions(200);
  await versions.getByRole('button', { name: 'Retry' }).click();
  await expect(versions.getByText('No earlier versions')).toBeVisible();
  await expect(versions.getByText('Upload a new version and this file becomes version 1 in the list.')).toBeVisible();
});

test('without read permission the versions say who can see them', async ({ page }) => {
  await mockVersions(page, { versionsStatus: 403 });
  const detail = await openPlan(page);
  await expect(detail.getByText('Seeing versions needs a viewer, member or owner role in this workspace.')).toBeVisible();
});

test('the storage screen shows usage against the limit, kinds and the largest files', async ({ page }) => {
  const api = await mockVersions(page);
  await page.goto('/#files');
  await page.getByRole('button', { name: 'Storage', exact: true }).click();
  const storage = page.getByRole('region', { name: 'Storage' });
  await expect(storage.getByText('1003 B of 9.8 KB used')).toBeVisible();
  await expect(storage.getByText('8.8 KB left')).toBeVisible();
  await expect(storage.getByText('2 older versions').first()).toBeVisible();
  await expect(storage.getByRole('progressbar', { name: 'Workspace storage used' })).toBeVisible();
  await expect(storage.getByRole('table', { name: 'By kind' }).getByRole('row')).toHaveCount(3);
  const largest = storage.getByRole('table', { name: 'Largest files' });
  await expect(largest.getByRole('row').nth(1)).toContainText('plan.txt');
  expect(api.calls.some((call) => call.path === '/api/v1/files/storage')).toBe(true);
  await largest.getByRole('button', { name: 'Open plan.txt' }).click();
  await expect(page.getByRole('region', { name: 'plan.txt' })).toBeVisible();
  await expect(storage).toHaveCount(0);
});

test('the storage screen warns when the workspace is nearly full or full', async ({ page }) => {
  await mockVersions(page, { quota: 1050 });
  await page.goto('/#files');
  await page.getByRole('button', { name: 'Storage', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Storage' }).getByRole('alert')).toContainText('almost full');

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockVersions(page, { quota: 800 });
  await page.reload();
  await page.getByRole('button', { name: 'Storage', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Storage' }).getByRole('alert')).toContainText('Storage is full');
});

test('the storage screen reports a failed or denied load', async ({ page }) => {
  const api = await mockVersions(page, { storageStatus: 500 });
  await page.goto('/#files');
  await page.getByRole('button', { name: 'Storage', exact: true }).click();
  const storage = page.getByRole('region', { name: 'Storage' });
  await expect(storage.getByRole('alert')).toContainText('Storage could not be loaded: Metadata store unavailable');
  api.setStorage(200);
  await storage.getByRole('button', { name: 'Try again' }).click();
  await expect(storage.getByText('of 9.8 KB used')).toBeVisible();
  api.setStorage(403);
  await page.getByRole('button', { name: 'Close storage' }).click();
  await page.getByRole('button', { name: 'Storage', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Storage' })).toContainText('Seeing storage needs a viewer, member or owner role');
});

for (const size of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
]) {
  test.describe(`at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test('versions and storage fit without horizontal overflow', async ({ page }) => {
      await mockVersions(page, { older: Array.from({ length: 4 }, (_, i) => version(4 - i)) });
      const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      await page.goto('/#files');
      await page.getByRole('button', { name: 'Storage', exact: true }).click();
      await expect(page.getByRole('region', { name: 'Storage' }).getByText('of 9.8 KB used')).toBeVisible();
      expect(await overflow()).toBeLessThanOrEqual(0);
      await page.getByRole('region', { name: 'Storage' }).getByRole('button', { name: 'Open plan.txt' }).click();
      const versions = page.getByRole('region', { name: 'Versions' });
      await expect(versions.getByRole('listitem')).toHaveCount(4);
      await versions.getByRole('button', { name: 'Delete version 4 of plan.txt' }).click();
      expect(await overflow()).toBeLessThanOrEqual(0);
      const box = (await versions.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(size.width);
    });
  });
}

test('versions and storage work in Arabic, right to left', async ({ page }) => {
  await mockVersions(page);
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await page.goto('/#files');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.getByRole('button', { name: 'التخزين', exact: true }).click();
  await expect(page.getByRole('region', { name: 'التخزين' }).getByText('أكبر الملفات')).toBeVisible();
  await page.getByRole('button', { name: 'أغلق التخزين' }).click();
  await page.getByRole('button', { name: /plan\.txt/ }).first().click();
  await expect(page.getByRole('region', { name: 'الإصدارات' }).getByText('الحالي: الإصدار 3')).toBeVisible();
});
