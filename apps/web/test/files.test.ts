// Unit tests for the Files view helpers in src/lib/files.ts.
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  bytesLeft, canReindex, fileBadge, fileTypeLabel, kindLabel, mergeVersions, versionProblem, versionsPath, isQuarantined, filesListPath, formatBytes, indexBadge, mergePage, parseInline, parseMarkdown, previewMode, progressText,
  renameProblem, safeHref, uploadProblem, uploaderLabel, usageMeter, type ApiFile, type ApiFileUsage,
} from '../src/lib/files.ts';

const usage: ApiFileUsage = {
  used_bytes: 900, file_count: 3, quota_bytes: 1000, max_upload_bytes: 500, link_ttl_seconds: 300,
  accepted: 'text, images and PDF', accepted_extensions: ['.md', '.pdf', '.png', '.txt'], can_upload: true, can_manage_all: false,
};
const file = (id: string): ApiFile => ({ id, name: `${id}.txt`, content_type: 'text/plain', size_bytes: 1, sha256: 'a', created_by: 'u', created_at: '2026-10-06T12:00:00Z' });

describe('filesListPath', () => {
  test('defaults to newest first with the page size', () => {
    assert.equal(filesListPath(), '/api/v1/files?limit=50&sort=created_at&order=desc');
  });
  test('carries a trimmed search, the sort and the keyset cursor, encoded', () => {
    assert.equal(
      filesListPath({ query: '  q3 & plan ', sort: 'name:asc', cursor: 'file_9', limit: 20 }),
      '/api/v1/files?limit=20&sort=name&order=asc&q=q3+%26+plan&cursor=file_9',
    );
    assert.equal(filesListPath({ query: '   ' }), '/api/v1/files?limit=50&sort=created_at&order=desc');
  });
});

test('mergePage appends a page without duplicating files a reload already listed', () => {
  assert.deepEqual(mergePage([file('a'), file('b')], [file('b'), file('c')]).map((f) => f.id), ['a', 'b', 'c']);
});

test('formatBytes reads like the API', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(1023), '1023 B');
  assert.equal(formatBytes(2048), '2.0 KB');
  assert.equal(formatBytes(25 * 1024 * 1024), '25.0 MB');
  assert.equal(formatBytes(2 * 1024 ** 3), '2.0 GB');
});

test('fileTypeLabel names common types and falls back to the subtype', () => {
  assert.equal(fileTypeLabel('text/markdown'), 'Markdown');
  assert.equal(fileTypeLabel('image/png'), 'PNG image');
  assert.equal(fileTypeLabel('application/vnd.openxmlformats-officedocument.wordprocessingml.document'), 'Word document');
  assert.equal(fileTypeLabel('text/x-python; charset=utf-8'), 'PYTHON');
});

test('previewMode previews only what the API marked previewable', () => {
  assert.equal(previewMode({ kind: 'markdown', previewable: true }), 'markdown');
  assert.equal(previewMode({ kind: 'data', previewable: true }), 'text');
  assert.equal(previewMode({ kind: 'pdf', previewable: true }), 'pdf');
  assert.equal(previewMode({ kind: 'image', previewable: true }), 'image');
  assert.equal(previewMode({ kind: 'document', previewable: false }), null);
  assert.equal(previewMode({ kind: 'image', previewable: false }), null);
});

describe('indexBadge and canReindex', () => {
  const base = { error: null, chunk_count: 0, embedding_model: null, indexed_at: null };
  test('every state has a label, a tone and a sentence', () => {
    assert.deepEqual(indexBadge({ ...base, status: 'indexed', chunk_count: 1 }), { label: 'Indexed', tone: 'success', detail: 'Tasks can use this file (1 passage).' });
    assert.equal(indexBadge({ ...base, status: 'skipped', error: 'not_text' }).detail, 'Not indexed: this file type is not indexed.');
    assert.equal(indexBadge({ ...base, status: 'skipped', error: 'no_text_layer' }).detail, 'Not indexed: no extractable text, OCR not available.');
    assert.equal(indexBadge({ ...base, status: 'failed', error: 'model_budget_exceeded' }).detail, 'Indexing failed (model budget exceeded). Re-index to try again.');
    assert.equal(indexBadge({ ...base, status: 'stale' }).tone, 'warning');
    assert.equal(indexBadge({ ...base, status: 'pending' }).label, 'Not indexed yet');
    assert.equal(indexBadge(null).label, 'Index unknown');
  });
  test('re-index is offered only when it can change the outcome', () => {
    assert.equal(canReindex({ ...base, status: 'failed' }), true);
    assert.equal(canReindex({ ...base, status: 'stale' }), true);
    assert.equal(canReindex({ ...base, status: 'pending' }), true);
    assert.equal(canReindex({ ...base, status: 'indexed' }), false);
    assert.equal(canReindex({ ...base, status: 'skipped' }), false);
  });
});

test('uploaderLabel says You for the caller', () => {
  assert.equal(uploaderLabel('user_1', 'user_1'), 'You');
  assert.equal(uploaderLabel('user_2', 'user_1'), 'user_2');
  assert.equal(uploaderLabel('user_2', null), 'user_2');
});

describe('uploadProblem', () => {
  test('refuses empty, oversized, unaccepted and over-quota files with a sentence', () => {
    assert.match(uploadProblem({ name: 'a.txt', size: 0 }, usage) ?? '', /is empty/);
    assert.match(uploadProblem({ name: 'big.pdf', size: 600 }, usage) ?? '', /at most 500 B/);
    assert.match(uploadProblem({ name: 'tool.exe', size: 10 }, usage) ?? '', /accepts text, images and PDF/);
    assert.match(uploadProblem({ name: 'README', size: 10 }, usage) ?? '', /cannot be uploaded/);
    assert.match(uploadProblem({ name: 'b.png', size: 200 }, usage) ?? '', /does not fit: 900 B of 1000 B is used/);
  });
  test('lets a fitting file through, and everything but empty when limits are unknown', () => {
    assert.equal(uploadProblem({ name: 'Notes.MD', size: 50 }, usage), null);
    assert.equal(uploadProblem({ name: 'tool.exe', size: 10 }, null), null);
  });
});

test('renameProblem keeps the extension and refuses bad names', () => {
  assert.equal(renameProblem('a.txt', 'b.txt'), null);
  assert.equal(renameProblem('a.txt', '  '), 'Enter a name.');
  assert.equal(renameProblem('a.txt', 'a.txt'), 'Enter a different name.');
  assert.match(renameProblem('a.txt', 'b.pdf') ?? '', /Keep the \.txt extension/);
  assert.match(renameProblem('a.txt', 'x/y.txt') ?? '', /cannot contain/);
  assert.match(renameProblem('README', 'README.md') ?? '', /Do not add an extension/);
  assert.match(renameProblem('a.txt', `${'x'.repeat(256)}.txt`) ?? '', /255/);
});

test('usageMeter clamps and warns near the quota', () => {
  assert.deepEqual(usageMeter({ ...usage, used_bytes: 500 }), { percent: 50, tone: 'ok', text: '500 B of 1000 B used · 3 files' });
  assert.equal(usageMeter(usage).tone, 'warn');
  assert.equal(usageMeter({ ...usage, used_bytes: 1200, file_count: 1 }).percent, 100);
  assert.equal(usageMeter({ ...usage, used_bytes: 1200, file_count: 1 }).text.endsWith('1 file'), true);
  assert.equal(usageMeter({ ...usage, used_bytes: 990 }).tone, 'stop');
});

test('progressText', () => {
  assert.equal(progressText(512, 2048), '25% of 2.0 KB');
  assert.equal(progressText(0, 0), 'Starting...');
});

describe('Markdown reader', () => {
  test('links are kept only for web and mail addresses', () => {
    assert.equal(safeHref('https://example.com/a'), 'https://example.com/a');
    assert.equal(safeHref('mailto:ops@example.com'), 'mailto:ops@example.com');
    assert.equal(safeHref('javascript:alert(1)'), null);
    assert.equal(safeHref('data:text/html,<script>'), null);
    assert.equal(parseInline('[x](javascript:alert(1))').some((part) => part.type === 'link'), false);
    assert.deepEqual(parseInline('[docs](https://example.com)'), [{ type: 'link', href: 'https://example.com', children: [{ type: 'text', text: 'docs' }] }]);
  });
  test('inline marks parse and HTML stays text', () => {
    assert.deepEqual(parseInline('a **b** and *c* `d` <b>e</b>'), [
      { type: 'text', text: 'a ' },
      { type: 'strong', children: [{ type: 'text', text: 'b' }] },
      { type: 'text', text: ' and ' },
      { type: 'em', children: [{ type: 'text', text: 'c' }] },
      { type: 'text', text: ' ' },
      { type: 'code', text: 'd' },
      { type: 'text', text: ' <b>e</b>' },
    ]);
    assert.deepEqual(parseInline('<img src=x onerror=alert(1)>'), [{ type: 'text', text: '<img src=x onerror=alert(1)>' }]);
  });
  test('blocks: headings, lists, quotes, fenced code, rules and paragraphs', () => {
    const blocks = parseMarkdown('# Title\n\nFirst line\nsecond line\n\n- one\n- two\n1. a\n2. b\n> quoted\n---\n```\n<script>x</script>\n```');
    assert.deepEqual(blocks.map((b) => b.type), ['heading', 'paragraph', 'list', 'list', 'quote', 'rule', 'code']);
    assert.deepEqual(blocks[1], { type: 'paragraph', children: [{ type: 'text', text: 'First line second line' }] });
    assert.equal(blocks[2].type === 'list' && blocks[2].ordered, false);
    assert.equal(blocks[3].type === 'list' && blocks[3].items.length, 2);
    assert.deepEqual(blocks[6], { type: 'code', text: '<script>x</script>' });
  });
});

describe('quarantined files', () => {
  test('the quarantine badge wins over the index state and names the signature', () => {
    const index = { status: 'indexed' as const, error: null, chunk_count: 2, embedding_model: 'm', indexed_at: null };
    const badge = fileBadge({ index, scan_status: 'infected', scan_signature: 'Eicar-Test-Signature' });
    assert.equal(badge.label, 'Quarantined');
    assert.equal(badge.tone, 'dangerTone');
    assert.match(badge.detail, /found Eicar-Test-Signature in this file/);
    assert.equal(fileBadge({ index, scan_status: 'clean' }).label, 'Indexed');
    assert.equal(isQuarantined({ scan_status: 'infected' }), true);
    assert.equal(isQuarantined({ scan_status: null }), false);
  });
});

describe('file versions and storage helpers', () => {
  const ver = (id: string) => ({
    id, file_id: 'f', version: 1, content_type: 'text/plain', size_bytes: 1, sha256: 'a', created_by: 'u', created_at: 'x',
    replaced_by: 'u', replaced_at: 'x', downloadable: true,
  });
  test('the versions path pages by offset and encodes the id', () => {
    assert.equal(versionsPath('file 1', 0), '/api/v1/files/file%201/versions?limit=10&offset=0');
    assert.equal(versionsPath('f', -5, 20), '/api/v1/files/f/versions?limit=20&offset=0');
  });
  test('merging a page keeps the first copy of a version', () => {
    assert.deepEqual(mergeVersions([ver('a'), ver('b')], [ver('b'), ver('c')]).map((item) => item.id), ['a', 'b', 'c']);
  });
  test('bytes left never go negative', () => {
    assert.equal(bytesLeft({ used_bytes: 900, quota_bytes: 1000 }), 100);
    assert.equal(bytesLeft({ used_bytes: 1200, quota_bytes: 1000 }), 0);
  });
  test('a new version is checked for size, type and the bytes left before it is sent', () => {
    const current = { name: 'plan.txt', size_bytes: 10 };
    assert.equal(versionProblem({ name: 'plan.txt', size: 50 }, current, usage), null);
    assert.equal(versionProblem({ name: 'plan.txt', size: 50 }, current, null), null);
    assert.match(versionProblem({ name: 'plan.txt', size: 0 }, current, usage) ?? '', /empty/i);
    assert.notEqual(versionProblem({ name: 'plan.txt', size: 600 }, current, usage), null);
    assert.match(versionProblem({ name: 'plan.png', size: 5 }, current, usage) ?? '', /keeps the file type \(\.txt\)/);
    assert.match(versionProblem({ name: 'plan.txt', size: 200 }, current, usage) ?? '', /every version counts/);
  });
  test('kinds read as labels, including the catch-all', () => {
    assert.equal(kindLabel('other'), 'Other');
    assert.equal(kindLabel('image'), 'Images');
  });
});
