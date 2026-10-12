// Self-hosted fonts (docs/web-localization.md#fonts, owner decision 12 Oct 2026).
import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { test } from 'node:test';

const web = new URL('../', import.meta.url);
const read = (path: string) => readFileSync(new URL(path, web), 'utf8');

test('both faces are same-origin woff2 files with swap and their licences', () => {
  const css = read('public/fonts/fonts.css');
  const sources = [...css.matchAll(/url\("([^"]+)"\)/g)].map((m) => m[1]);
  assert.deepEqual(sources, ['/fonts/inter-latin-var.woff2', '/fonts/noto-sans-arabic-var.woff2']);
  for (const source of sources) {
    const file = new URL(`public${source}`, web);
    assert.ok(statSync(file).size > 20_000, source);
  }
  assert.equal(css.match(/font-display: swap;/g)?.length, 2);
  assert.match(css, /font-family: "Noto Sans Arabic";[\s\S]*unicode-range: U\+0600-06FF/);
  for (const licence of ['OFL-Inter.txt', 'OFL-NotoSansArabic.txt']) {
    assert.ok(existsSync(new URL(`public/fonts/${licence}`, web)), licence);
    assert.match(read(`public/fonts/${licence}`), /SIL Open Font License/);
  }
});

test('only the Latin face is preloaded and the stack keeps the system fallbacks', () => {
  const html = read('index.html');
  const preloads = [...html.matchAll(/<link rel="preload" href="([^"]+)" as="font"[^>]*crossorigin/g)].map((m) => m[1]);
  assert.deepEqual(preloads, ['/fonts/inter-latin-var.woff2']);
  assert.match(html, /<link rel="stylesheet" href="\/fonts\/fonts.css" \/>/);
  const stack = read('src/styles.css').match(/--d-font: ([^;]+);/)?.[1] ?? '';
  assert.match(stack, /^Inter, "Noto Sans Arabic", /);
  assert.match(stack, /system-ui/);
  assert.match(stack, /sans-serif$/);
});

test('the CSP loads fonts and their stylesheet from the same origin only', () => {
  const csp = read('nginx/default.conf.template').match(/set \$csp "([^"]+)"/)?.[1] ?? '';
  assert.match(csp, /font-src 'self' data:;/);
  assert.match(csp, /style-src 'self' 'unsafe-inline';/);
});
