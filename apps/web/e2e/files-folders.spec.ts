import { expect, test, type Page, type Route } from '@playwright/test';

// Folders, tags and bulk actions on the Documents page against a mocked API (docs/files.md#folders,
// #tags, #bulk-actions): the folder trail and subfolders, creating, renaming, moving and deleting a
// folder (empty, move up, delete contents), the tag filter and manager, tags on one file, the
// selection with Shift ranges and the bulk bar (move, tag, download, delete), refusals, the viewer
// role, Arabic, and no horizontal overflow from 1440x900 to 390x844.
const now = '2026-11-10T09:00:00.000Z';
type Json = Record<string, unknown>;
const indexed = { status: 'indexed', error: null, chunk_count: 3, embedding_model: 'anum-local-hash-v1', indexed_at: now };
const base = { sha256: 'a'.repeat(64), created_by: 'user_local', created_at: now, updated_by: null, updated_at: null, can_manage: true, previewable: true, kind: 'text', content_type: 'text/plain', index: indexed, version: 1 };

function seed() {
  const folders: Json[] = [
    { id: 'folder_contracts', name: 'Contracts', parent_id: null, created_by: 'user_local', created_at: now },
    { id: 'folder_2026', name: '2026', parent_id: 'folder_contracts', created_by: 'user_local', created_at: now },
    { id: 'folder_photos', name: 'Photos', parent_id: null, created_by: 'user_other', created_at: now },
  ];
  const tags: Json[] = [
    { id: 'tag_urgent', name: 'Urgent', created_by: 'user_local', created_at: now },
    { id: 'tag_paid', name: 'Paid', created_by: 'user_other', created_at: now },
  ];
  const files: Json[] = [
    { ...base, id: 'file_root_a', name: 'readme.txt', size_bytes: 40, folder_id: null, tag_ids: ['tag_urgent'] },
    { ...base, id: 'file_root_b', name: 'notes.txt', size_bytes: 50, folder_id: null, tag_ids: [] },
    { ...base, id: 'file_root_c', name: 'theirs.txt', size_bytes: 60, folder_id: null, tag_ids: [], created_by: 'user_other', can_manage: false },
    { ...base, id: 'file_c1', name: 'lease.txt', size_bytes: 70, folder_id: 'folder_contracts', tag_ids: ['tag_paid'] },
    { ...base, id: 'file_c2', name: 'nda.txt', size_bytes: 80, folder_id: 'folder_contracts', tag_ids: [] },
    { ...base, id: 'file_y1', name: 'renewal.txt', size_bytes: 90, folder_id: 'folder_2026', tag_ids: [] },
  ];
  return { folders, tags, files };
}

async function mockOrganize(page: Page, options: { role?: 'owner' | 'viewer'; folders?: 200 | 500 } = {}) {
  const data = seed();
  let nextId = 100;
  const viewer = options.role === 'viewer';
  const requests: { method: string; path: string; body: Json | null; query: URLSearchParams }[] = [];
  const pathOf = (id: string): string | null => {
    const names: string[] = [];
    let current: string | null = id;
    while (current) { const f = data.folders.find((x) => x.id === current); if (!f) break; names.unshift(String(f.name)); current = f.parent_id as string | null; }
    return names.length ? names.join(' / ') : null;
  };
  const view = (f: Json) => ({ ...f, can_manage: viewer ? false : f.can_manage, folder_path: f.folder_id ? pathOf(String(f.folder_id)) : null,
    tags: (f.tag_ids as string[]).map((id) => data.tags.find((x) => x.id === id)).filter(Boolean).map((x) => ({ id: x!.id, name: x!.name })) });
  const folderView = (f: Json) => ({ ...f, path: pathOf(String(f.id)), depth: (pathOf(String(f.id)) ?? '').split(' / ').length,
    file_count: data.files.filter((x) => x.folder_id === f.id).length, can_manage: !viewer && (f.created_by === 'user_local' || !viewer) });
  const descendants = (id: string): string[] => data.folders.filter((f) => f.parent_id === id).flatMap((f) => [String(f.id), ...descendants(String(f.id))]);
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const raw = request.postData();
    let body: Json | null = null;
    if (raw && (request.headers()['content-type'] ?? '').includes('json')) { try { body = JSON.parse(raw) as Json; } catch { body = null; } }
    if (path.startsWith('/api/v1/files')) requests.push({ method, path, body, query: url.searchParams });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const envelope = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/files/usage') return json({ used_bytes: 390, file_count: data.files.length, quota_bytes: 10_000, max_upload_bytes: 4096, link_ttl_seconds: 300, accepted: 'text (.txt, .md)', accepted_extensions: ['.md', '.txt'], can_upload: !viewer, can_manage_all: !viewer });
    if (path === '/api/v1/retrieval/status') return json({ embedding_model: 'anum-local-hash-v1', indexed: 6, failed: 0, skipped: 0, stale: 0, chunks: 18, sources: [] });
    if (path === '/api/v1/files/folders' && method === 'GET') {
      if (options.folders === 500) return envelope(500, 'internal_error', 'Database unavailable');
      return json({ items: data.folders.map(folderView), limit: 1000, max_depth: 10 });
    }
    if (path === '/api/v1/files/folders' && method === 'POST') {
      if (viewer) return envelope(403, 'forbidden', 'Uploading and changing files needs a member or owner role in this workspace.');
      if (data.folders.some((f) => f.parent_id === (body!.parent_id ?? null) && String(f.name).toLowerCase() === String(body!.name).toLowerCase())) return envelope(409, 'conflict', `A folder named ${body!.name} already exists here. Choose another name.`);
      const created = { id: `folder_${nextId++}`, name: body!.name, parent_id: body!.parent_id ?? null, created_by: 'user_local', created_at: now };
      data.folders.push(created);
      return json(folderView(created), 201);
    }
    const folderMatch = /^\/api\/v1\/files\/folders\/([^/]+)$/.exec(path);
    if (folderMatch) {
      const found = data.folders.find((f) => f.id === folderMatch[1]);
      if (!found) return envelope(404, 'not_found', 'Folder not found: it may have been deleted. Reload the folders.');
      if (method === 'PATCH') {
        if (body && 'name' in body) found.name = body.name;
        if (body && 'parent_id' in body) found.parent_id = body.parent_id;
        return json(folderView(found));
      }
      if (method === 'DELETE') {
        const contents = url.searchParams.get('contents') ?? 'refuse';
        const below = descendants(found.id as string);
        const inside = data.files.filter((f) => [found.id, ...below].includes(f.folder_id as string));
        if (contents === 'refuse' && (inside.length || below.length)) return envelope(409, 'conflict', `${found.name} is not empty: it holds ${inside.length} files and ${below.length} subfolders. Move them out first.`);
        if (contents === 'move_up') {
          for (const f of data.files) if (f.folder_id === found.id) f.folder_id = found.parent_id;
          for (const f of data.folders) if (f.parent_id === found.id) f.parent_id = found.parent_id;
        }
        if (contents === 'delete') {
          if (inside.some((f) => !f.can_manage)) return envelope(403, 'forbidden', '1 of the 3 selected files were uploaded by someone else, and only the uploader or a workspace owner can delete them. Nothing was changed.');
          data.files = data.files.filter((f) => !inside.includes(f));
          data.folders = data.folders.filter((f) => !below.includes(String(f.id)));
        }
        data.folders = data.folders.filter((f) => f !== found);
        return route.fulfill({ status: 204 });
      }
    }
    if (path === '/api/v1/files/tags' && method === 'GET') return json({ items: data.tags.map((x) => ({ ...x, file_count: data.files.filter((f) => (f.tag_ids as string[]).includes(String(x.id))).length, can_manage: !viewer && x.created_by === 'user_local' })), limit: 500 });
    if (path === '/api/v1/files/tags' && method === 'POST') {
      if (data.tags.some((x) => String(x.name).toLowerCase() === String(body!.name).toLowerCase())) return envelope(409, 'conflict', `A tag named ${body!.name} already exists here. Choose another name.`);
      const created = { id: `tag_${nextId++}`, name: body!.name, created_by: 'user_local', created_at: now };
      data.tags.push(created);
      return json({ ...created, file_count: 0, can_manage: true }, 201);
    }
    const tagMatch = /^\/api\/v1\/files\/tags\/([^/]+)$/.exec(path);
    if (tagMatch) {
      const found = data.tags.find((x) => x.id === tagMatch[1]);
      if (!found) return envelope(404, 'not_found', 'Tag not found');
      if (method === 'PATCH') { found.name = body!.name; return json({ ...found, file_count: 0, can_manage: true }); }
      if (method === 'DELETE') { data.tags = data.tags.filter((x) => x !== found); for (const f of data.files) f.tag_ids = (f.tag_ids as string[]).filter((id) => id !== found.id); return route.fulfill({ status: 204 }); }
    }
    const bulk = /^\/api\/v1\/files\/bulk\/(\w+)$/.exec(path);
    if (bulk && method === 'POST') {
      const ids = body!.file_ids as string[];
      const chosen = data.files.filter((f) => ids.includes(String(f.id)));
      if (chosen.length !== ids.length) return envelope(404, 'not_found', `${ids.length - chosen.length} of the ${ids.length} selected files no longer exist in this workspace. Reload the list and try again.`);
      if (bulk[1] === 'download') return route.fulfill({ status: 200, contentType: 'application/zip', headers: { 'Content-Disposition': 'attachment; filename="files-20261110-0900.zip"' }, body: Buffer.from('PK\u0005\u0006' + '\u0000'.repeat(18), 'latin1') });
      if ((bulk[1] === 'move' || bulk[1] === 'delete') && chosen.some((f) => !f.can_manage)) return envelope(403, 'forbidden', `${chosen.filter((f) => !f.can_manage).length} of the ${ids.length} selected files were uploaded by someone else, and only the uploader or a workspace owner can ${bulk[1]} them. Nothing was changed.`);
      if (bulk[1] === 'move') { for (const f of chosen) f.folder_id = body!.folder_id ?? null; return json({ count: chosen.length }); }
      if (bulk[1] === 'delete') { data.files = data.files.filter((f) => !chosen.includes(f)); return json({ count: chosen.length }); }
      if (bulk[1] === 'tag') { let n = 0; for (const f of chosen) for (const tagId of body!.tag_ids as string[]) if (!(f.tag_ids as string[]).includes(tagId)) { (f.tag_ids as string[]).push(tagId); n++; } return json({ count: n }); }
      if (bulk[1] === 'untag') { let n = 0; for (const f of chosen) { const before = (f.tag_ids as string[]).length; f.tag_ids = (f.tag_ids as string[]).filter((id) => !(body!.tag_ids as string[]).includes(id)); n += before - (f.tag_ids as string[]).length; } return json({ count: n }); }
    }
    if (path === '/api/v1/files' && method === 'GET') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const folder = url.searchParams.get('folder');
      const tag = url.searchParams.get('tag');
      if (folder && folder !== 'root' && !data.folders.some((f) => f.id === folder)) return envelope(404, 'not_found', 'Folder not found: it may have been deleted. Reload the folders.');
      let rows = data.files.filter((f) => String(f.name).toLowerCase().includes(q));
      if (folder === 'root') rows = rows.filter((f) => f.folder_id === null);
      else if (folder) rows = rows.filter((f) => f.folder_id === folder);
      if (tag) rows = rows.filter((f) => (f.tag_ids as string[]).includes(tag));
      rows = [...rows].sort((a, b) => String(a.name).localeCompare(String(b.name)));
      return json(rows.map(view));
    }
    if (path === '/api/v1/files' && method === 'POST') {
      const created = { ...base, id: `file_${nextId++}`, name: url.searchParams.get('name') ?? 'new.txt', size_bytes: 7, folder_id: url.searchParams.get('folder_id'), tag_ids: [] };
      data.files.push(created);
      return json(view(created), 201);
    }
    const fileTag = /^\/api\/v1\/files\/([^/]+)\/tags(?:\/([^/]+))?$/.exec(path);
    if (fileTag) {
      const found = data.files.find((f) => f.id === fileTag[1]);
      if (!found) return envelope(404, 'not_found', 'File not found');
      if (method === 'POST') { for (const id of body!.tag_ids as string[]) if (!(found.tag_ids as string[]).includes(id)) (found.tag_ids as string[]).push(id); return json(view(found)); }
      if (method === 'DELETE') { found.tag_ids = (found.tag_ids as string[]).filter((id) => id !== fileTag[2]); return json(view(found)); }
    }
    const match = /^\/api\/v1\/files\/([^/]+)(?:\/(\w+))?$/.exec(path);
    if (match) {
      const [, id, action] = match;
      const found = data.files.find((f) => f.id === id);
      if (!found) return envelope(404, 'not_found', 'File not found');
      if (!action && method === 'GET') return json(view(found));
      if (!action && method === 'DELETE') { data.files = data.files.filter((f) => f.id !== id); return route.fulfill({ status: 204 }); }
      if (action === 'preview') return json({ kind: 'text', text: 'line one', truncated: false, size_bytes: found.size_bytes });
      if (action === 'versions' && method === 'GET') return json({ file: view(found), items: [], total: 0, limit: 10, offset: 0 });
    }
    if (method === 'GET' && path.startsWith('/api/v1/')) return json([]);
    return route.abort();
  });
  return { data, requests };
}

async function openFiles(page: Page) {
  await page.goto('/#files');
  await expect(page.getByRole('heading', { name: 'Documents', level: 1 })).toBeVisible();
}
const list = (page: Page) => page.getByRole('region', { name: 'Files' });
const rows = (page: Page) => list(page).locator('tbody tr');
const folderList = (page: Page, name: string) => page.getByRole('list', { name: `Folders in ${name}` });

test('the root shows its folders with counts and the files in no folder; opening a folder lists its files and the trail', async ({ page }) => {
  const api = await mockOrganize(page);
  await openFiles(page);
  await expect(rows(page)).toHaveCount(3);
  const root = folderList(page, 'All documents');
  await expect(root.getByRole('button', { name: /^Contracts/ })).toContainText('2 files');
  await expect(root.getByRole('button', { name: /^Photos/ })).toContainText('0 files');
  expect(api.requests.find((r) => r.method === 'GET' && r.path === '/api/v1/files')?.query.get('folder')).toBe('root');

  await root.getByRole('button', { name: /^Contracts/ }).click();
  await expect(rows(page)).toHaveCount(2);
  await expect(list(page).getByText('lease.txt')).toBeVisible();
  const trail = page.getByRole('navigation', { name: 'Folders' });
  await expect(trail.getByText('Contracts')).toHaveAttribute('aria-current', 'location');
  await expect(folderList(page, 'Contracts').getByRole('button', { name: /^2026/ })).toBeVisible();
  expect(api.requests.at(-1)?.query.get('folder')).toBe('folder_contracts');
  // Back to the root through the trail.
  await trail.getByRole('button', { name: 'All documents' }).click();
  await expect(rows(page)).toHaveCount(3);
  // The lease is tagged Paid: the row shows it, the detail shows the folder and the tag.
  await folderList(page, 'All documents').getByRole('button', { name: /^Contracts/ }).click();
  await list(page).getByRole('button', { name: 'Show details of lease.txt' }).click();
  const detail = page.getByRole('region', { name: 'lease.txt' });
  await expect(detail.getByText('Contracts', { exact: true })).toBeVisible();
  await expect(detail.getByRole('group', { name: 'Tags' }).getByText('Paid')).toBeVisible();
});

test('a search looks in every folder and says where each result is', async ({ page }) => {
  const api = await mockOrganize(page);
  await openFiles(page);
  await page.getByLabel('Search files by name').fill('re');
  await page.keyboard.press('Enter');
  await expect(page.getByText('Search results come from every folder.')).toBeVisible();
  expect(api.requests.at(-1)?.query.has('folder')).toBe(false);
  await expect(list(page).getByText('In Contracts / 2026')).toBeVisible();
  await expect(rows(page)).toHaveCount(2);
});

test('an empty folder offers an upload into it, and the upload names the folder', async ({ page }) => {
  const api = await mockOrganize(page);
  await openFiles(page);
  await folderList(page, 'All documents').getByRole('button', { name: /^Photos/ }).click();
  await expect(page.getByRole('heading', { name: 'This folder is empty' })).toBeVisible();
  await page.getByRole('button', { name: 'Upload to Photos' }).click();
  await page.getByLabel('Upload files').setInputFiles({ name: 'cat.txt', mimeType: 'text/plain', buffer: Buffer.from('meow') });
  await expect(page.getByRole('status').filter({ hasText: 'Uploaded cat.txt' })).toBeVisible();
  expect(api.requests.find((r) => r.method === 'POST' && r.path === '/api/v1/files')?.query.get('folder_id')).toBe('folder_photos');
  await expect(rows(page)).toHaveCount(1);
});

test('folders are created, renamed, moved and deleted with their contents dealt with', async ({ page }) => {
  const api = await mockOrganize(page);
  await openFiles(page);
  await page.getByRole('button', { name: 'New folder' }).click();
  const dialog = page.getByRole('dialog', { name: 'New folder in All documents' });
  await dialog.getByLabel('Folder name').fill('contracts');
  await expect(dialog.getByText('A folder named contracts already exists here.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Create folder' })).toBeDisabled();
  await dialog.getByLabel('Folder name').fill('Invoices');
  await dialog.getByRole('button', { name: 'Create folder' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Folder Invoices created.' })).toBeVisible();
  await expect(folderList(page, 'All documents').getByRole('button', { name: /^Invoices/ })).toBeVisible();

  // Rename through the folder's actions.
  await page.getByRole('button', { name: 'Actions for folder Invoices' }).click();
  await page.getByRole('group', { name: 'Actions for folder Invoices' }).getByRole('button', { name: 'Rename folder' }).click();
  await page.getByRole('dialog', { name: 'Rename folder Invoices' }).getByLabel('Folder name').fill('Bills');
  await page.getByRole('dialog', { name: 'Rename folder Invoices' }).getByRole('button', { name: 'Rename folder' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Folder renamed to Bills.' })).toBeVisible();
  // Move Bills into Contracts: the picker leaves the moved folder out.
  await page.getByRole('button', { name: 'Actions for folder Bills' }).click();
  await page.getByRole('group', { name: 'Actions for folder Bills' }).getByRole('button', { name: 'Move folder' }).click();
  const move = page.getByRole('dialog', { name: 'Move folder Bills' });
  await expect(move.getByRole('option', { name: 'Bills' })).toHaveCount(0);
  await move.getByLabel('Choose a folder').selectOption({ label: 'Contracts' });
  await move.getByRole('button', { name: 'Move here' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Folder Bills moved to Contracts.' })).toBeVisible();
  expect(api.requests.filter((r) => r.method === 'PATCH').at(-1)?.body).toEqual({ parent_id: 'folder_contracts' });

  // Deleting a full folder asks what to do with the contents; move up is the default.
  await page.getByRole('button', { name: 'Actions for folder Contracts' }).click();
  await page.getByRole('group', { name: 'Actions for folder Contracts' }).getByRole('button', { name: 'Delete folder' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Delete Contracts?' });
  await expect(confirm.getByText('This folder holds 3 files and 2 subfolders.')).toBeVisible();
  await expect(confirm.getByRole('radio', { name: /Move the contents to All documents/ })).toBeChecked();
  await confirm.getByRole('button', { name: 'Delete folder' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Folder Contracts deleted.' })).toBeVisible();
  expect(api.requests.filter((r) => r.method === 'DELETE').at(-1)?.query.get('contents')).toBe('move_up');
  await expect(rows(page)).toHaveCount(5);
  await expect(folderList(page, 'All documents').getByRole('button', { name: /^2026/ })).toBeVisible();
  // An empty folder goes without a choice.
  await page.getByRole('button', { name: 'Actions for folder Bills' }).click();
  await page.getByRole('group', { name: 'Actions for folder Bills' }).getByRole('button', { name: 'Delete folder' }).click();
  await expect(page.getByRole('alertdialog', { name: 'Delete Bills?' }).getByText('The folder is empty.')).toBeVisible();
  await page.getByRole('alertdialog', { name: 'Delete Bills?' }).getByRole('button', { name: 'Delete folder' }).click();
  await expect(folderList(page, 'All documents').getByRole('button', { name: /^Bills/ })).toHaveCount(0);
});

test('deleting a folder with its contents is refused as a whole when a file is not yours', async ({ page }) => {
  const api = await mockOrganize(page);
  api.data.files.find((f) => f.id === 'file_c2')!.can_manage = false;
  await openFiles(page);
  await page.getByRole('button', { name: 'Actions for folder Contracts' }).click();
  await page.getByRole('group', { name: 'Actions for folder Contracts' }).getByRole('button', { name: 'Delete folder' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Delete Contracts?' });
  await confirm.getByRole('radio', { name: 'Delete the folder with everything inside' }).check();
  await confirm.getByRole('button', { name: 'Delete folder' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'The folder was not deleted: 1 of the 3 selected files were uploaded by someone else' })).toBeVisible();
  await expect(folderList(page, 'All documents').getByRole('button', { name: /^Contracts/ })).toBeVisible();
});

test('tags filter the list, are managed in a dialog and are added to or removed from one file', async ({ page }) => {
  const api = await mockOrganize(page);
  await openFiles(page);
  const tags = page.getByRole('group', { name: 'Tags' }).first();
  await tags.getByRole('button', { name: /^Urgent/ }).click();
  await expect(rows(page)).toHaveCount(1);
  expect(api.requests.at(-1)?.query.get('tag')).toBe('tag_urgent');
  await tags.getByRole('button', { name: 'Show every tag' }).click();
  await expect(rows(page)).toHaveCount(3);

  await tags.getByRole('button', { name: 'Manage tags' }).click();
  const dialog = page.getByRole('dialog', { name: 'Tags' });
  await dialog.getByLabel('Tag name').fill('urgent');
  await expect(dialog.getByText('A tag named urgent already exists.')).toBeVisible();
  await dialog.getByLabel('Tag name').fill('Review');
  await dialog.getByRole('button', { name: 'Create tag' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Tag Review created.' })).toBeVisible();
  // Someone else's tag has no rename or delete for a member.
  await expect(dialog.getByRole('button', { name: 'Rename tag Paid' })).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Rename tag Review' }).click();
  await dialog.getByRole('textbox', { name: 'Tag name' }).last().fill('To review');
  await dialog.getByRole('button', { name: 'Save name' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Tag renamed to To review.' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Delete tag Urgent' }).click();
  await dialog.getByRole('button', { name: 'Delete tag Urgent for good' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Tag Urgent deleted.' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(tags.getByRole('button', { name: /^Urgent/ })).toHaveCount(0);

  // Tags on one file in the detail pane: remove one, add an existing one, create one.
  await list(page).getByRole('button', { name: 'Show details of readme.txt' }).click();
  const detail = page.getByRole('region', { name: 'readme.txt' });
  const onFile = detail.getByRole('group', { name: 'Tags' });
  await expect(onFile.getByText('No tags')).toBeVisible();
  await onFile.getByRole('button', { name: 'Add tag' }).click();
  await onFile.getByLabel('Choose a tag').selectOption({ label: 'Paid' });
  await onFile.getByRole('button', { name: 'Add tag' }).click();
  await expect(onFile.getByText('Paid')).toBeVisible();
  await onFile.getByRole('button', { name: 'Add tag' }).click();
  await onFile.getByLabel('New tag').fill('Legal');
  await onFile.getByRole('button', { name: 'Create and add "Legal"' }).click();
  await expect(onFile.getByText('Legal')).toBeVisible();
  await expect(tags.getByRole('button', { name: /^Legal/ })).toBeVisible();
  await onFile.getByRole('button', { name: 'Remove tag Paid from readme.txt' }).click();
  await expect(onFile.getByText('Paid')).toHaveCount(0);
});

test('a selection moves, tags, downloads and deletes files together, with Shift ranges and refusals', async ({ page }) => {
  const api = await mockOrganize(page);
  await openFiles(page);
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await list(page).getByRole('checkbox', { name: 'Select notes.txt' }).click();
  await list(page).getByRole('checkbox', { name: 'Select theirs.txt' }).click({ modifiers: ['Shift'] });
  const bar = page.getByRole('region', { name: 'Selected files' });
  await expect(bar.getByRole('status')).toHaveText('3 selected');
  // One of them is not mine: move and delete are off and the note says why.
  await expect(bar.getByRole('button', { name: 'Move' })).toBeDisabled();
  await expect(bar.getByText('1 of the selected files are not yours')).toBeVisible();
  await list(page).getByRole('checkbox', { name: 'Select theirs.txt' }).click();
  await expect(bar.getByRole('status')).toHaveText('2 selected');
  await expect(bar.getByRole('button', { name: 'Move' })).toBeEnabled();

  // Tag both: Urgent is on some (mixed), Paid on none.
  await bar.getByRole('button', { name: 'Tag' }).click();
  const tagDialog = page.getByRole('dialog', { name: 'Tags for 2 files' });
  await tagDialog.getByRole('checkbox', { name: 'Paid' }).check();
  await tagDialog.getByRole('button', { name: 'Apply tags' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Tags updated on 2 files.' })).toBeVisible();
  expect(api.requests.find((r) => r.path === '/api/v1/files/bulk/tag')?.body).toEqual({ file_ids: ['file_root_b', 'file_root_a'], tag_ids: ['tag_paid'] });

  // Download as a zip (selection mode stays on; the bar cleared the selection).
  await list(page).getByRole('checkbox', { name: 'Select notes.txt' }).click();
  await list(page).getByRole('checkbox', { name: 'Select readme.txt' }).click();
  const download = page.waitForEvent('download');
  await bar.getByRole('button', { name: 'Download' }).click();
  expect((await download).suggestedFilename()).toMatch(/^files-\d{8}\.zip$/);
  await expect(page.getByRole('status').filter({ hasText: 'Downloaded 2 files as files-' })).toBeVisible();

  // Move both into Contracts / 2026.
  await bar.getByRole('button', { name: 'Move' }).click();
  const move = page.getByRole('dialog', { name: 'Move 2 files to' });
  await move.getByLabel('Choose a folder').selectOption({ label: '   2026' });
  await move.getByRole('button', { name: 'Move here' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Moved 2 files to 2026.' })).toBeVisible();
  await expect(rows(page)).toHaveCount(1);
  expect(api.requests.find((r) => r.path === '/api/v1/files/bulk/move')?.body).toEqual({ file_ids: ['file_root_b', 'file_root_a'], folder_id: 'folder_2026' });

  // Delete what is left at the root (theirs: refused, nothing deleted), then the owner's own files in 2026.
  await list(page).getByRole('checkbox', { name: 'Select every listed file' }).check();
  await expect(bar.getByRole('button', { name: 'Delete' })).toBeDisabled();
  await bar.getByRole('button', { name: 'Clear selection' }).click();
  await folderList(page, 'All documents').getByRole('button', { name: /^Contracts/ }).click();
  await folderList(page, 'Contracts').getByRole('button', { name: /^2026/ }).click();
  await expect(rows(page)).toHaveCount(3);
  await list(page).getByRole('checkbox', { name: 'Select every listed file' }).check();
  await bar.getByRole('button', { name: 'Delete' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Delete 3 files?' });
  await expect(confirm.getByText('renewal.txt')).toBeVisible();
  await confirm.getByRole('button', { name: 'Delete 3 files' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Deleted 3 files.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'This folder is empty' })).toBeVisible();
  expect(api.requests.find((r) => r.path === '/api/v1/files/bulk/delete')?.body).toEqual({ file_ids: ['file_root_b', 'file_root_a', 'file_y1'] });
});

test('a viewer browses folders and tags but changes nothing', async ({ page }) => {
  await mockOrganize(page, { role: 'viewer' });
  await openFiles(page);
  await expect(folderList(page, 'All documents').getByRole('button', { name: /^Contracts/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'New folder' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Actions for folder Contracts' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Manage tags' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await list(page).getByRole('checkbox', { name: 'Select every listed file' }).check();
  const bar = page.getByRole('region', { name: 'Selected files' });
  await expect(bar.getByRole('button', { name: 'Download' })).toBeEnabled();
  await expect(bar.getByRole('button', { name: 'Move' })).toBeDisabled();
  await expect(bar.getByRole('button', { name: 'Tag' })).toBeDisabled();
  await expect(bar.getByText('Moving and deleting several files needs a member or owner role')).toBeVisible();
  await list(page).getByRole('button', { name: 'Show details of readme.txt' }).click();
  const onFile = page.getByRole('region', { name: 'readme.txt' }).getByRole('group', { name: 'Tags' });
  await expect(onFile.getByText('Urgent')).toBeVisible();
  await expect(onFile.getByRole('button', { name: 'Add tag' })).toHaveCount(0);
});

test('a failed folder read says so and the files still list', async ({ page }) => {
  await mockOrganize(page, { folders: 500 });
  await openFiles(page);
  await expect(page.getByRole('alert').filter({ hasText: 'Could not load the folders:' })).toBeVisible();
  await expect(rows(page)).toHaveCount(3);
});

test('the Arabic page shows the folders, tags and the selection bar with no horizontal overflow at every size', async ({ page }) => {
  await mockOrganize(page);
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    await page.goto('/#files');
    await page.reload();
    await expect(page.getByRole('heading', { name: 'المستندات', level: 1 })).toBeVisible();
    await expect(page.getByRole('list', { name: 'المجلدات داخل كل المستندات' }).getByRole('button', { name: /^Contracts/ })).toBeVisible();
    await page.getByRole('button', { name: 'تحديد', exact: true }).click();
    await page.getByRole('region', { name: 'الملفات' }).getByRole('checkbox', { name: 'حدد كل الملفات المعروضة' }).check();
    await expect(page.getByRole('region', { name: 'الملفات المحددة' }).getByRole('status')).toHaveText('محدد 3 ملفات');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${size.width}x${size.height}`).toBeLessThanOrEqual(0);
  }
});
