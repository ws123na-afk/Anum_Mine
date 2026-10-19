// Public Privacy Policy and Terms of Service pages (docs/legal-pages.md). TikTok for Developers
// and the app stores link to https://<web host>/privacy and /terms, so the files and the nginx
// locations that serve them without sign-in must stay.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const web = new URL('../', import.meta.url);
const read = (path: string) => readFileSync(new URL(path, web), 'utf8');
const pages = { privacy: read('public/policies/privacy.html'), terms: read('public/policies/terms.html') };
const template = read('nginx/default.conf.template');

/** The directives inside `location <match> { ... }` (no nested blocks in this template). */
function locationBody(match: string): string {
  const start = template.indexOf(`location ${match} {`);
  assert.notEqual(start, -1, `nginx has location ${match}`);
  return template.slice(start, template.indexOf('}', start));
}

const headers = (body: string) => body.split('\n').map((line) => line.trim()).filter((line) => line.startsWith('add_header')).sort();

test('each page is self-contained HTML that the CSP allows', () => {
  for (const [name, html] of Object.entries(pages)) {
    assert.match(html, /^<!doctype html>/i, name);
    assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1">/, name);
    assert.doesNotMatch(html, /<script/i, `${name}: the CSP blocks inline scripts`);
    assert.doesNotMatch(html, /\son[a-z]+=/i, `${name}: no inline event handlers`);
    assert.doesNotMatch(html, /<(link|img)[^>]+(src|href)="https?:\/\/(?!app\.anum360os\.com)/i, `${name}: nothing loaded from another origin`);
    assert.doesNotMatch(html, /[–—]/, `${name}: no long dashes`);
    assert.match(html, /Effective 13 October 2026/, name);
    assert.match(html, /info@shahzebrahman\.com/, name);
    assert.match(html, /href="\/privacy"/, name);
    assert.match(html, /href="\/terms"/, name);
    assert.match(html, /href="\/"/, `${name} links to the app`);
  }
  for (const scope of ['user.info.basic', 'video.upload', 'video.publish', 'video.list']) assert.match(pages.privacy, new RegExp(scope.replace('.', '\\.')), scope);
  assert.match(pages.terms, /Emirate of Abu Dhabi/);
});

test('nginx serves /privacy and /terms before the SPA fallback, with the full header set', () => {
  const spa = headers(locationBody('/'));
  assert.ok(spa.some((line) => line.startsWith('add_header Content-Security-Policy $csp')));
  for (const name of ['privacy', 'terms']) {
    for (const path of [`/${name}`, `/${name}/`]) {
      const body = locationBody(`= ${path}`);
      assert.deepEqual(headers(body), spa, `${path} repeats the security headers and no-cache`);
      assert.match(body, new RegExp(`try_files /policies/${name}\\.html =404;`), path);
    }
  }
});
