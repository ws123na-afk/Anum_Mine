// In-app help (docs/help.md): article parsing, locale fallback, search, the page-to-article map,
// and the bundled articles themselves (every module has English and Arabic, all parse, all link).
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import { DOCS_BASE_URL, articleForView, articlesFor, docsHref, parseArticle, searchArticles } from '../src/lib/help.ts';
import { NAV, VIEWS } from '../src/lib/shell.ts';

const helpDir = new URL('../../../docs/help/', import.meta.url);
const docsDir = new URL('../../../docs/', import.meta.url);

const sample = `---
id: tasks
title: Tasks
summary: Ask ANUM to do a piece of work.
keywords: task, run, archive
docs: ../tasks.md
---

# Tasks

Tasks are work ANUM does for you.

## Start a task

1. Open **Tasks** in the sidebar.

## Learn more

[Tasks and agent runs](../tasks.md#runs)
`;

test('parses frontmatter, drops the title heading and makes doc links absolute', () => {
  const article = parseArticle('../../../docs/help/tasks.md', sample);
  assert.ok(article);
  assert.equal(article.id, 'tasks');
  assert.equal(article.locale, 'en');
  assert.equal(article.title, 'Tasks');
  assert.deepEqual(article.keywords, ['task', 'run', 'archive']);
  assert.equal(article.docsUrl, `${DOCS_BASE_URL}tasks.md`);
  assert.ok(!article.body.startsWith('# Tasks'));
  assert.ok(article.body.includes(`](${DOCS_BASE_URL}tasks.md#runs)`));
});

test('reads the Arabic file as the ar locale and refuses malformed files', () => {
  assert.equal(parseArticle('tasks.ar.md', sample)?.locale, 'ar');
  assert.equal(parseArticle('tasks.md', '# no frontmatter'), null);
  assert.equal(parseArticle('other.md', sample), null, 'the id must match the file name');
  assert.equal(parseArticle('Tasks.fr.md', sample), null);
});

test('docsHref keeps links inside the docs and refuses traversal', () => {
  assert.equal(docsHref('../calendar.md'), `${DOCS_BASE_URL}calendar.md`);
  assert.equal(docsHref('https://example.com/x'), 'https://example.com/x');
  assert.equal(docsHref('../../etc/passwd'), null);
  assert.equal(docsHref('javascript:alert(1)'), null);
});

test('articlesFor prefers the locale and falls back to English', () => {
  const en = parseArticle('tasks.md', sample)!;
  const ar = { ...en, locale: 'ar' as const, title: 'المهام' };
  const onlyEn = { ...en, id: 'files', title: 'Files' };
  const list = articlesFor([en, ar, onlyEn], 'ar');
  assert.deepEqual(list.map((a) => a.title).sort(), ['Files', 'المهام']);
  assert.deepEqual(articlesFor([en, ar, onlyEn], 'en').map((a) => a.title), ['Files', 'Tasks']);
});

test('search needs every word and ranks title hits first', () => {
  const tasks = parseArticle('tasks.md', sample)!;
  const files = { ...tasks, id: 'files', title: 'Files', keywords: ['upload'], summary: '', body: 'Archive a task file.' };
  assert.deepEqual(searchArticles([files, tasks], 'task').map((a) => a.id), ['tasks', 'files']);
  assert.deepEqual(searchArticles([files, tasks], 'upload').map((a) => a.id), ['files']);
  assert.deepEqual(searchArticles([files, tasks], 'task nothing'), []);
  assert.equal(searchArticles([files, tasks], '  ').length, 2);
});

test('tabs share their parent article', () => {
  assert.equal(articleForView('memory'), 'knowledge');
  assert.equal(articleForView('autonomy'), 'activity');
  assert.equal(articleForView('tasks'), 'tasks');
});

const files = readdirSync(helpDir).filter((name) => name.endsWith('.md'));

test('every bundled article parses and links only to docs that exist', () => {
  assert.ok(files.length > 0);
  for (const name of files) {
    const source = readFileSync(new URL(name, helpDir), 'utf8');
    const article = parseArticle(name, source);
    assert.ok(article, `${name} does not parse`);
    assert.ok(article.summary, `${name} has no summary`);
    const docs = /^docs: (.+)$/m.exec(source)?.[1];
    assert.ok(docs && existsSync(new URL(docs.replace(/^\.\.\//, ''), docsDir)), `${name}: docs target missing`);
    assert.ok((source.match(/^## /gm) ?? []).length >= 4, `${name} needs three tasks and who can do them`);
  }
});

test('every sidebar page has a help article in English and Arabic', () => {
  const ids = new Set(NAV.flatMap((group) => group.views).concat(VIEWS).map(articleForView));
  for (const id of ids) {
    assert.ok(files.includes(`${id}.md`), `missing docs/help/${id}.md`);
    assert.ok(files.includes(`${id}.ar.md`), `missing docs/help/${id}.ar.md`);
  }
  for (const name of files.filter((n) => !n.endsWith('.ar.md'))) {
    assert.ok(files.includes(name.replace(/\.md$/, '.ar.md')), `${name} has no Arabic version`);
  }
});
