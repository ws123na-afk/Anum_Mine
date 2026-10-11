import { expect, test, type Page, type Route } from '@playwright/test';

// The Files view against a mocked API (docs/files.md): list, search, sort and paging, usage, upload
// with progress, cancel and refusals, the detail panel's previews, rename, re-index and delete, and
// what a viewer sees.
const now = '2026-10-06T09:00:00.000Z';
type Json = Record<string, unknown>;

const indexed = { status: 'indexed', error: null, chunk_count: 3, embedding_model: 'anum-local-hash-v1', indexed_at: now };
const base = { sha256: 'a'.repeat(64), created_by: 'user_local', created_at: now, updated_by: null, updated_at: null, can_manage: true, previewable: true };
const guide = { ...base, id: 'file_guide', name: 'release-guide.md', content_type: 'text/markdown', kind: 'markdown', size_bytes: 2048, index: indexed };
const logo = { ...base, id: 'file_logo', name: 'logo.png', content_type: 'image/png', kind: 'image', size_bytes: 67, created_by: 'user_other', can_manage: false, index: { ...indexed, status: 'skipped', error: 'not_text', chunk_count: 0 } };
const report = { ...base, id: 'file_report', name: 'quarterly-launch-readiness-review-with-every-region-signed-off-final-v12.pdf', content_type: 'application/pdf', kind: 'pdf', size_bytes: 900, index: { ...indexed, status: 'skipped', error: 'not_text', chunk_count: 0 } };
const failed = { ...base, id: 'file_failed', name: 'notes.txt', content_type: 'text/plain', kind: 'text', size_bytes: 40, index: { ...indexed, status: 'failed', error: 'model_unavailable', chunk_count: 0 } };
const deck = { ...base, id: 'file_deck', name: 'deck.pptx', content_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', kind: 'document', previewable: false, size_bytes: 5000, index: { ...indexed, status: 'skipped', error: 'not_text', chunk_count: 0 } };

const MARKDOWN = '# Release guide\n\nShip on **Friday** after the [checklist](https://example.com/check).\n\n- tag the build\n- publish notes\n\n<script>window.__pwned = true</script>\n';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

/** A minimal one-page PDF with a correct cross-reference table. */
function tinyPdf(): Buffer {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 120] /Contents 4 0 R >>',
  ];
  const stream = '0 0 1 rg 20 20 160 80 re f';
  let body = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((object, i) => { offsets.push(body.length); body += `${i + 1} 0 obj\n${object}\nendobj\n`; });
  offsets.push(body.length);
  body += `4 0 obj\n<< /Length ${stream.length} >>\nstream\n${stream}\nendstream\nendobj\n`;
  const xref = body.length;
  body += `xref\n0 5\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')}`;
  body += `trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, 'latin1');
}

interface Options {
  role?: 'owner' | 'viewer';
  files?: Json[];
  list?: 200 | 403 | 500;
  /** POST /api/v1/files: answer right away (default), hold until released, or refuse with this envelope. */
  upload?: 'ok' | 'hold' | { status: number; message: string };
}

async function mockFiles(page: Page, options: Options = {}) {
  let files: Json[] = [...(options.files ?? [guide, logo, report, failed, deck])];
  const listCalls: URLSearchParams[] = [];
  const requests: { method: string; path: string; body?: string | null }[] = [];
  let release: (() => void) | null = null;
  let listStatus = options.list ?? 200;
  const viewer = options.role === 'viewer';
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const envelope = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    if (path.startsWith('/api/v1/files')) requests.push({ method, path, body: request.postData() });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/files/usage') {
      const used = files.reduce((sum, f) => sum + Number(f.size_bytes), 0);
      return json({ used_bytes: used, file_count: files.length, quota_bytes: 10_000, max_upload_bytes: 4096, link_ttl_seconds: 300,
        accepted: 'text (.txt, .md), images (.png) and PDF', accepted_extensions: ['.md', '.pdf', '.png', '.pptx', '.txt'], can_upload: !viewer, can_manage_all: !viewer });
    }
    if (path === '/api/v1/files' && method === 'GET') {
      listCalls.push(url.searchParams);
      if (listStatus === 403) return envelope(403, 'forbidden', 'Seeing files needs a viewer, member or owner role in this workspace.');
      if (listStatus === 500) return envelope(500, 'internal_error', 'Object storage unavailable');
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      let rows = files.filter((f) => String(f.name).toLowerCase().includes(q));
      if (url.searchParams.get('sort') === 'name') rows = [...rows].sort((a, b) => String(a.name).localeCompare(String(b.name)));
      const cursor = url.searchParams.get('cursor');
      if (cursor) rows = rows.slice(rows.findIndex((f) => f.id === cursor) + 1);
      const limit = Number(url.searchParams.get('limit') ?? 50);
      return json(rows.slice(0, limit).map((f) => viewer ? { ...f, can_manage: false } : f));
    }
    if (path === '/api/v1/files' && method === 'POST') {
      if (options.upload === 'hold') { await new Promise<void>((resolve) => { release = resolve; }); return route.abort(); }
      if (typeof options.upload === 'object') return envelope(options.upload.status, 'payload_too_large', options.upload.message);
      const name = url.searchParams.get('name') ?? 'unnamed';
      const created = { ...guide, id: `file_${files.length + 1}`, name, size_bytes: request.postDataBuffer()?.length ?? 0, created_at: now };
      files = [created, ...files];
      return json(created, 201);
    }
    const match = /^\/api\/v1\/files\/([^/]+)(?:\/(\w+))?$/.exec(path);
    if (match && match[1] === 'links') {
      const id = url.pathname.split('/').pop()!.replace('tok_', '');
      if (id === 'file_logo') return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
      if (id === 'file_report') return route.fulfill({ status: 200, contentType: 'application/pdf', body: tinyPdf() });
      return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'bytes' });
    }
    if (match) {
      const [, id, action] = match;
      const found = files.find((f) => f.id === id);
      if (!found) return envelope(404, 'not_found', 'File not found');
      if (!action && method === 'GET') return json(viewer ? { ...found, can_manage: false } : found);
      if (!action && method === 'PATCH') {
        const renamed = { ...found, name: JSON.parse(request.postData() ?? '{}').name, updated_by: 'user_local', updated_at: now };
        files = files.map((f) => f.id === id ? renamed : f);
        return json(renamed);
      }
      if (!action && method === 'DELETE') { files = files.filter((f) => f.id !== id); return route.fulfill({ status: 204 }); }
      if (action === 'preview') return json({ kind: found.kind, text: found.kind === 'markdown' ? MARKDOWN : 'line one\nline two', truncated: false, size_bytes: found.size_bytes });
      if (action === 'links') return json({ url: `/api/v1/files/links/tok_${id}`, expires_at: now }, 201);
      if (action === 'reindex') {
        const done = { ...found, index: { ...indexed, chunk_count: 1 } };
        files = files.map((f) => f.id === id ? done : f);
        return json(done);
      }
    }
    if (method === 'GET' && path.startsWith('/api/v1/')) return json([]);
    return route.abort();
  });
  return { listCalls, requests, release: () => { const go = release; release = null; go?.(); }, answerList: (status: 200 | 403 | 500) => { listStatus = status; } };
}

async function openFiles(page: Page) {
  await page.goto('/#files');
  await expect(page.getByRole('heading', { name: 'Files', level: 2 })).toBeVisible();
}

test('the list shows kind, size, uploader, index state and storage used', async ({ page }) => {
  await mockFiles(page);
  await openFiles(page);
  const list = page.getByRole('region', { name: 'Files' });
  const row = list.locator('.memoryRow').filter({ hasText: 'release-guide.md' });
  await expect(row.getByText('Markdown · 2.0 KB · You ·')).toBeVisible();
  await expect(row.getByText('Indexed')).toBeVisible();
  await expect(list.locator('.memoryRow').filter({ hasText: 'logo.png' }).getByText('PNG image · 67 B · user_other ·')).toBeVisible();
  await expect(list.locator('.memoryRow').filter({ hasText: 'notes.txt' }).getByText('Indexing failed')).toBeVisible();
  const storage = page.getByRole('progressbar', { name: 'Workspace storage used' });
  await expect(storage).toHaveAttribute('aria-valuetext', '7.9 KB of 9.8 KB used · 5 files');
  await expect(page.getByText('Up to 4.0 KB per file.')).toBeVisible();
  // Someone else's file has no delete action for a member.
  await expect(list.getByRole('button', { name: 'Delete logo.png' })).toHaveCount(0);
});

test('search, sort and load more ask the API for the right page', async ({ page }) => {
  const many = Array.from({ length: 55 }, (_, i) => ({ ...failed, id: `file_${String(i).padStart(2, '0')}`, name: `note-${String(i).padStart(2, '0')}.txt` }));
  const api = await mockFiles(page, { files: [guide, ...many] });
  await openFiles(page);
  const list = page.getByRole('region', { name: 'Files' });
  await expect(list.locator('.memoryRow')).toHaveCount(50);
  await page.getByRole('button', { name: 'Load more files' }).click();
  await expect(list.locator('.memoryRow')).toHaveCount(56);
  expect(api.listCalls.at(-1)?.get('cursor')).toBe('file_48');
  await expect(page.getByRole('button', { name: 'Load more files' })).toHaveCount(0);

  await page.getByLabel('Sort files').selectOption('name:asc');
  await expect.poll(() => api.listCalls.at(-1)?.toString()).toContain('sort=name&order=asc');
  await page.getByLabel('Search files by name').fill('guide');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(list.locator('.memoryRow')).toHaveCount(1);
  expect(api.listCalls.at(-1)?.get('q')).toBe('guide');

  await page.getByLabel('Search files by name').fill('nothing-here');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'No files match "nothing-here"' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear search' }).click();
  await expect(list.locator('.memoryRow')).toHaveCount(50);
});

test('an upload shows its result, refuses what the API would refuse, and reports the server reason', async ({ page }) => {
  const api = await mockFiles(page);
  await openFiles(page);
  await page.getByLabel('Upload files').setInputFiles({ name: 'tool.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') });
  await expect(page.getByRole('alert').filter({ hasText: 'tool.exe cannot be uploaded: this workspace accepts text (.txt, .md)' })).toBeVisible();
  await page.getByLabel('Upload files').setInputFiles({ name: 'huge.txt', mimeType: 'text/plain', buffer: Buffer.alloc(5000, 120) });
  await expect(page.getByRole('alert').filter({ hasText: 'huge.txt is 4.9 KB; files can be at most 4.0 KB' })).toBeVisible();
  expect(api.requests.filter((r) => r.method === 'POST' && r.path === '/api/v1/files')).toHaveLength(0);

  await page.getByLabel('Upload files').setInputFiles({ name: 'résumé notes.md', mimeType: 'text/markdown', buffer: Buffer.from('# Notes') });
  await expect(page.getByRole('status').filter({ hasText: 'Uploaded résumé notes.md. Tasks can use this file' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Files' }).getByRole('button', { name: 'Show details of résumé notes.md' })).toBeVisible();
  await page.getByRole('button', { name: 'Dismiss tool.exe' }).click();
  await expect(page.getByText('tool.exe cannot be uploaded')).toHaveCount(0);
});

test('a refused upload says why', async ({ page }) => {
  await mockFiles(page, { upload: { status: 413, message: 'This workspace has used 9.8 KiB of its 9.8 KiB storage quota, so a 10 B file does not fit. Delete files you no longer need.' } });
  await openFiles(page);
  await page.getByLabel('Upload files').setInputFiles({ name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('0123456789') });
  await expect(page.getByRole('alert').filter({ hasText: 'storage quota, so a 10 B file does not fit' })).toBeVisible();
});

test('an upload in progress can be cancelled', async ({ page }) => {
  const api = await mockFiles(page, { upload: 'hold' });
  await openFiles(page);
  await page.getByLabel('Upload files').setInputFiles({ name: 'slow.txt', mimeType: 'text/plain', buffer: Buffer.from('slow upload') });
  const uploads = page.getByRole('region', { name: 'Uploads' });
  await expect(uploads.getByRole('progressbar', { name: 'Uploading slow.txt' })).toBeVisible();
  await uploads.getByRole('button', { name: 'Cancel upload of slow.txt' }).click();
  await expect(uploads.getByText('Upload cancelled; nothing was stored.')).toBeVisible();
  api.release();
});

test('the detail panel previews Markdown safely, renames, re-indexes and deletes', async ({ page }) => {
  const api = await mockFiles(page);
  await openFiles(page);
  await page.getByRole('button', { name: 'Show details of release-guide.md' }).click();
  const detail = page.getByRole('region', { name: 'release-guide.md' });
  await expect(detail.getByRole('heading', { name: 'release-guide.md' })).toBeFocused();
  const preview = detail.getByRole('region', { name: 'Preview' });
  await expect(preview.getByRole('heading', { name: 'Release guide' })).toBeVisible();
  await expect(preview.getByRole('link', { name: 'checklist' })).toHaveAttribute('href', 'https://example.com/check');
  await expect(preview.getByText('<script>window.__pwned = true</script>')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __pwned?: boolean }).__pwned)).toBeUndefined();
  await preview.getByRole('button', { name: 'Show source' }).click();
  await expect(preview.getByLabel('Text of release-guide.md')).toContainText('**Friday**');

  await detail.getByRole('button', { name: 'Rename' }).click();
  const field = detail.getByLabel('New name');
  await field.fill('release-guide.pdf');
  await expect(detail.getByText('Keep the .md extension')).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Save name' })).toBeDisabled();
  await field.fill('launch-guide.md');
  await detail.getByRole('button', { name: 'Save name' }).click();
  await expect(page.getByRole('heading', { name: 'launch-guide.md' })).toBeVisible();
  expect(api.requests.find((r) => r.method === 'PATCH')?.body).toBe('{"name":"launch-guide.md"}');
  await expect(page.getByRole('region', { name: 'launch-guide.md' }).getByText(/by You/)).toBeVisible();

  await page.getByRole('button', { name: 'Show details of notes.txt' }).click();
  const notes = page.getByRole('region', { name: 'notes.txt' });
  await expect(notes.getByText('Indexing failed (model unavailable). Re-index to try again.')).toBeVisible();
  await notes.getByRole('button', { name: 'Re-index' }).click();
  await expect(notes.getByRole('status').filter({ hasText: 'Tasks can use this file (1 passage).' })).toBeVisible();
  await expect(notes.getByRole('button', { name: 'Re-index' })).toHaveCount(0);
  await expect(notes.getByLabel('Text of notes.txt')).toContainText('line two');

  await notes.getByRole('button', { name: 'Delete', exact: true }).click();
  await notes.getByRole('button', { name: 'Confirm delete of notes.txt' }).click();
  await expect(page.getByRole('region', { name: 'notes.txt' })).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: 'Deleted notes.txt.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Show details of notes.txt' })).toHaveCount(0);
});

test('images and PDFs preview from short-lived links; other kinds offer a download', async ({ page }) => {
  const api = await mockFiles(page);
  await openFiles(page);
  await page.getByRole('button', { name: 'Show details of logo.png' }).click();
  await expect(page.getByRole('img', { name: 'Preview of logo.png' })).toBeVisible();
  await expect(page.getByText('Only the person who uploaded this file or a workspace owner can rename or delete it.')).toBeVisible();
  expect(api.requests.find((r) => r.path === '/api/v1/files/file_logo/links')?.body).toBe('{"disposition":"inline"}');

  await page.getByRole('button', { name: /^Show details of quarterly-launch/ }).click();
  await expect(page.getByText('Page 1 of 1')).toBeVisible();
  await expect(page.getByRole('img', { name: /^Page 1 of quarterly-launch/ })).toBeVisible();
  // The page is drawn: the blue square of the test PDF is in the middle of the canvas.
  await expect.poll(() => page.evaluate(() => {
    const canvas = document.querySelector('.pdfPreview canvas') as HTMLCanvasElement;
    return [...canvas.getContext('2d')!.getImageData(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data];
  })).toEqual([0, 0, 255, 255]);

  await page.getByRole('button', { name: 'Show details of deck.pptx' }).click();
  await expect(page.getByText('PowerPoint deck files have no preview here. Download the file to open it.')).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('region', { name: 'deck.pptx' }).getByRole('button', { name: 'Download' }).click();
  expect((await download).suggestedFilename()).toBe('deck.pptx');
});

test('a viewer can read and download but not upload or change files', async ({ page }) => {
  await mockFiles(page, { role: 'viewer' });
  await openFiles(page);
  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeDisabled();
  await expect(page.getByText('Uploading needs a member or owner role in this workspace.')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Delete / })).toHaveCount(0);
  await page.getByRole('button', { name: 'Show details of notes.txt' }).click();
  const detail = page.getByRole('region', { name: 'notes.txt' });
  await expect(detail.getByRole('button', { name: 'Download' })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Rename' })).toHaveCount(0);
  await expect(detail.getByRole('button', { name: 'Re-index' })).toHaveCount(0);
});

test('without read permission the view says who can see files', async ({ page }) => {
  await mockFiles(page, { list: 403 });
  await openFiles(page);
  await expect(page.getByText('Seeing files needs a viewer, member or owner role in this workspace. Ask a workspace owner for access.')).toBeVisible();
});

test('a failed list says what failed and retries', async ({ page }) => {
  const api = await mockFiles(page, { list: 500 });
  await openFiles(page);
  const alert = page.getByRole('alert').filter({ hasText: 'Could not load files: Object storage unavailable' });
  await expect(alert).toBeVisible();
  api.answerList(200);
  await alert.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('region', { name: 'Files' }).locator('.memoryRow')).toHaveCount(5);
  await expect(alert).toHaveCount(0);
});

test('an empty workspace offers the first upload', async ({ page }) => {
  await mockFiles(page, { files: [] });
  await openFiles(page);
  await expect(page.getByRole('heading', { name: 'No files yet' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Upload a file' })).toBeVisible();
});

// The responsive gate's sizes plus the desktop window's minimum.
for (const size of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 960, height: 640 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
]) {
  test.describe(`at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test('the list, the detail panel and its preview fit without horizontal overflow', async ({ page }) => {
      await mockFiles(page);
      await openFiles(page);
      const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      await expect(page.getByRole('region', { name: 'Files' }).locator('.memoryRow')).toHaveCount(5);
      expect(await overflow()).toBeLessThanOrEqual(0);
      await page.getByRole('button', { name: /^Show details of quarterly-launch/ }).click();
      await expect(page.getByText('Page 1 of 1')).toBeVisible();
      expect(await overflow()).toBeLessThanOrEqual(0);
      const panel = (await page.locator('.fileDetail').boundingBox())!;
      expect(panel.x).toBeGreaterThanOrEqual(0);
      expect(panel.x + panel.width).toBeLessThanOrEqual(size.width);
      await page.getByRole('region', { name: 'Files' }).getByRole('button', { name: 'Delete release-guide.md' }).click();
      await expect(page.getByRole('button', { name: 'Confirm delete of release-guide.md' })).toBeVisible();
      expect(await overflow()).toBeLessThanOrEqual(0);
    });
  });
}
