// Unit tests for the folder, tag and selection helpers in src/lib/files.ts (docs/files.md#folders, #tags, #bulk-actions).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  bundleName, filesListPath, folderChildren, folderChoices, folderDescendants, folderNameProblem, folderTrail, tagNameProblem, tagState, toggleSelection,
  type ApiFolder, type ApiTag,
} from '../src/lib/files.ts';
import { filesOrganizeAr } from '../src/lib/messages/filesOrganize.ar.ts';
import { filesOrganizeEn } from '../src/lib/messages/filesOrganize.en.ts';

const folder = (id: string, name: string, parent: string | null, depth: number, count = 0): ApiFolder =>
  ({ id, name, parent_id: parent, path: name, depth, file_count: count, can_manage: true, created_by: 'u', created_at: '2026-11-10T00:00:00Z' });
const tree = [folder('a', 'Contracts', null, 1, 2), folder('b', '2026', 'a', 2), folder('c', 'Q1', 'b', 3, 5), folder('d', 'Photos', null, 1), folder('e', 'archive', null, 1)];
const tag = (id: string, name: string): ApiTag => ({ id, name, file_count: 0, can_manage: true, created_by: 'u', created_at: '2026-11-10T00:00:00Z' });

describe('filesListPath with folders and tags', () => {
  test('omits the folder for every file, sends root for the root and the id for a folder', () => {
    assert.equal(filesListPath(), '/api/v1/files?limit=50&sort=created_at&order=desc');
    assert.equal(filesListPath({ folder: 'all' }), '/api/v1/files?limit=50&sort=created_at&order=desc');
    assert.equal(filesListPath({ folder: null }), '/api/v1/files?limit=50&sort=created_at&order=desc&folder=root');
    assert.equal(filesListPath({ folder: 'folder_1', tag: 'tag_1' }), '/api/v1/files?limit=50&sort=created_at&order=desc&folder=folder_1&tag=tag_1');
  });
});

describe('the folder tree helpers', () => {
  test('children are A to Z under a parent or the root', () => {
    assert.deepEqual(folderChildren(tree, null).map((f) => f.name), ['archive', 'Contracts', 'Photos']);
    assert.deepEqual(folderChildren(tree, 'a').map((f) => f.id), ['b']);
    assert.deepEqual(folderChildren(tree, 'zzz'), []);
  });
  test('the trail walks up to the root and stops on a missing or looping parent', () => {
    assert.deepEqual(folderTrail(tree, 'c').map((f) => f.name), ['Contracts', '2026', 'Q1']);
    assert.deepEqual(folderTrail(tree, null), []);
    assert.deepEqual(folderTrail(tree, 'missing'), []);
    const loop = [folder('p', 'P', 'q', 1), folder('q', 'Q', 'p', 1)];
    assert.deepEqual(folderTrail(loop, 'p').map((f) => f.id), ['q', 'p']);
  });
  test('descendants and move choices leave out the moved folder and what lies below it', () => {
    assert.deepEqual(folderDescendants(tree, 'a'), ['b', 'c']);
    assert.deepEqual(folderChoices(tree).map((f) => f.id), ['e', 'a', 'b', 'c', 'd']);
    assert.deepEqual(folderChoices(tree, 'a').map((f) => f.id), ['e', 'd']);
    assert.deepEqual(folderChoices(tree, 'b').map((f) => f.id), ['e', 'a', 'd']);
  });
});

describe('name checks', () => {
  test('folder names follow the file-name rules and are unique among siblings, case folded', () => {
    const siblings = folderChildren(tree, null);
    assert.equal(folderNameProblem('Invoices', siblings), null);
    assert.equal(folderNameProblem('   ', siblings), 'Enter a folder name.');
    assert.equal(folderNameProblem('a/b', siblings), 'Folder names cannot contain / or \\ or control characters.');
    assert.equal(folderNameProblem('..', siblings), 'Folder names cannot contain / or \\ or control characters.');
    assert.equal(folderNameProblem('x'.repeat(256), siblings), 'Folder names are at most 255 characters.');
    assert.equal(folderNameProblem('contracts', siblings), 'A folder named contracts already exists here.');
    assert.equal(folderNameProblem('contracts', siblings, 'a'), null, 'renaming keeps its own name');
  });
  test('tag names are short, trimmed and unique', () => {
    const tags = [tag('t1', 'Urgent')];
    assert.equal(tagNameProblem('  Paid  ', tags), null);
    assert.equal(tagNameProblem('', tags), 'Enter a tag name.');
    assert.equal(tagNameProblem('x'.repeat(65), tags), 'Tag names are at most 64 characters.');
    assert.equal(tagNameProblem('urgent', tags), 'A tag named urgent already exists.');
    assert.equal(tagNameProblem('urgent', tags, 't1'), null);
    assert.equal(tagNameProblem('bad\u0001', tags), 'Tag names cannot contain control characters.');
  });
});

describe('selection', () => {
  const order = ['a', 'b', 'c', 'd', 'e'];
  test('a click toggles one id and drops ids no longer listed', () => {
    assert.deepEqual([...toggleSelection(new Set(['zzz']), order, 'b', null, false)], ['b']);
    assert.deepEqual([...toggleSelection(new Set(['b']), order, 'b', 'b', false)], []);
  });
  test('shift adds the range between the anchor and the target in either direction', () => {
    assert.deepEqual([...toggleSelection(new Set(['b']), order, 'd', 'b', true)].sort(), ['b', 'c', 'd']);
    assert.deepEqual([...toggleSelection(new Set(['d']), order, 'a', 'd', true)].sort(), ['a', 'b', 'c', 'd']);
    assert.deepEqual([...toggleSelection(new Set(), order, 'c', null, true)], ['c'], 'no anchor: a plain toggle');
  });
  test('tagState says whether all, some or none of the files carry a tag', () => {
    const files = [{ tags: [{ id: 't1', name: 'A' }] }, { tags: [{ id: 't1', name: 'A' }, { id: 't2', name: 'B' }] }, { tags: [] }];
    assert.equal(tagState(files.slice(0, 2), 't1'), 'all');
    assert.equal(tagState(files, 't1'), 'some');
    assert.equal(tagState(files, 't3'), 'none');
    assert.equal(tagState([{}], 't1'), 'none');
  });
  test('the zip name comes from the open folder and the day', () => {
    assert.equal(bundleName(null, new Date(2026, 10, 11)), 'files-20261111.zip');
    assert.equal(bundleName(folder('a', 'Q1 / Contracts', null, 1), new Date(2026, 0, 5)), 'Q1-Contracts-20260105.zip');
    assert.equal(bundleName(folder('a', '///', null, 1), new Date(2026, 0, 5)), 'files-20260105.zip');
  });
});

test('the Arabic catalog has every key of the English one and the same placeholders', () => {
  const en = filesOrganizeEn as Record<string, unknown>;
  const ar = filesOrganizeAr as Record<string, unknown>;
  assert.deepEqual(Object.keys(ar).sort(), Object.keys(en).sort());
  const placeholders = (value: unknown) => [...JSON.stringify(value).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  for (const key of Object.keys(en)) {
    const expected = new Set(placeholders(en[key]));
    for (const found of placeholders(ar[key])) assert.ok(expected.has(found) || found === 'count', `${key}: ${found}`);
  }
});
