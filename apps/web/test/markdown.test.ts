// Unit tests for the safe Markdown reader and code highlighting (src/lib/markdown.ts,
// src/lib/highlight.ts; docs/conversations.md#rendering).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { markdownToPlainText, parseMarkdown, parseMdInline, safeHref } from '../src/lib/markdown.ts';
import { highlight, languageLabel } from '../src/lib/highlight.ts';

describe('links', () => {
  test('only http(s) and mailto targets become links', () => {
    assert.equal(safeHref('https://anum.example/a?b=1'), 'https://anum.example/a?b=1');
    assert.equal(safeHref('mailto:ops@anum.example'), 'mailto:ops@anum.example');
    for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<b>x</b>', 'vbscript:x', '/relative', 'https://a b', 'file:///etc/passwd']) assert.equal(safeHref(bad), null, bad);
  });

  test('an unsafe link keeps its words as text; bare URLs link without trailing punctuation', () => {
    assert.deepEqual(parseMdInline('[click](vbscript:x)'), [{ kind: 'text', text: 'click' }]);
    const spans = parseMdInline('See [docs](https://docs.example/x) or https://anum.example/path.');
    assert.deepEqual(spans, [
      { kind: 'text', text: 'See ' }, { kind: 'link', text: 'docs', href: 'https://docs.example/x' }, { kind: 'text', text: ' or ' },
      { kind: 'link', text: 'https://anum.example/path', href: 'https://anum.example/path' }, { kind: 'text', text: '.' },
    ]);
    assert.ok(parseMdInline('[x](javascript:alert(1))').every((span) => span.kind !== 'link'));
  });

  test('markup in model output stays text', () => {
    const blocks = parseMarkdown('<img src=x onerror=alert(1)> **bold** ~~old~~');
    assert.deepEqual(blocks, [{ kind: 'paragraph', inline: [{ kind: 'text', text: '<img src=x onerror=alert(1)> ' }, { kind: 'strong', text: 'bold' }, { kind: 'text', text: ' ' }, { kind: 'del', text: 'old' }] }]);
  });
});

describe('blocks', () => {
  test('headings, numbered lists with their start, quotes and rules', () => {
    const kinds = parseMarkdown('# Title\n\n3. three\n4. four\n\n> quoted\n> more\n\n---\n\n- a\n- b').map((b) => b.kind === 'list' ? `list:${b.ordered}:${b.start}:${b.items.length}` : b.kind);
    assert.deepEqual(kinds, ['heading', 'list:true:3:2', 'quote', 'rule', 'list:false:1:2']);
  });

  test('pipe tables with alignment; rows padded to the header', () => {
    const [table] = parseMarkdown('| Name | Qty |\n| :--- | ---: |\n| Tyres | 4 |\n| Oil |');
    assert.equal(table.kind, 'table');
    if (table.kind !== 'table') return;
    assert.deepEqual(table.align, ['left', 'right']);
    assert.equal(table.rows.length, 2);
    assert.deepEqual(table.rows[1][1], []);
  });

  test('fenced code keeps its language; an unclosed fence (still streaming) runs to the end', () => {
    assert.deepEqual(parseMarkdown('```Python\nprint("hi")\n```\nafter'), [{ kind: 'code', lang: 'python', text: 'print("hi")' }, { kind: 'paragraph', inline: [{ kind: 'text', text: 'after' }] }]);
    assert.deepEqual(parseMarkdown('```ts\nconst a = 1'), [{ kind: 'code', lang: 'ts', text: 'const a = 1' }]);
  });

  test('Arabic text passes through untouched', () => {
    assert.deepEqual(parseMarkdown('## الإطارات\n- **إطار** أمامي'), [
      { kind: 'heading', level: 2, inline: [{ kind: 'text', text: 'الإطارات' }] },
      { kind: 'list', ordered: false, start: 1, items: [[{ kind: 'strong', text: 'إطار' }, { kind: 'text', text: ' أمامي' }]] },
    ]);
  });

  test('plain text drops the marks but keeps lists, links and tables readable', () => {
    assert.equal(markdownToPlainText('# Plan\n\n**Do** this:\n\n1. one\n2. [two](https://x.example)\n\n| a | b |\n|---|---|\n| 1 | 2 |'),
      'Plan\n\nDo this:\n\n1. one\n2. two (https://x.example/)\n\na\tb\n1\t2');
  });
});

describe('highlight', () => {
  test('tokens join back into the exact code', () => {
    const code = 'const total = 42; // sum\nreturn "done";';
    const tokens = highlight(code, 'ts');
    assert.equal(tokens.map((t) => t.text).join(''), code);
    assert.deepEqual(tokens.filter((t) => t.kind !== 'plain').map((t) => [t.kind, t.text]), [
      ['keyword', 'const'], ['number', '42'], ['comment', '// sum'], ['keyword', 'return'], ['string', '"done"'],
    ]);
  });

  test('language comment styles and case-insensitive SQL keywords', () => {
    assert.deepEqual(highlight('# note\nx = None', 'python').filter((t) => t.kind !== 'plain').map((t) => t.kind), ['comment', 'literal']);
    assert.deepEqual(highlight('SELECT 1 -- one', 'sql').filter((t) => t.kind !== 'plain').map((t) => t.kind), ['keyword', 'number', 'comment']);
  });

  test('no language: shown as is, an apostrophe is not a string', () => {
    assert.deepEqual(highlight("don't stop", ''), [{ kind: 'plain', text: "don't stop" }]);
    assert.equal(languageLabel('py'), 'Python');
    assert.equal(languageLabel(''), '');
  });
});
