// Owner rule (13 Oct 2026): no long dashes in anything a person reads. Use a colon, a comma,
// "to" for a range or a new sentence instead (docs/web-localization.md, Punctuation). The API
// test services/api/tests/test_no_long_dashes.py scans the other apps the same way.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { test } from 'node:test';

// Built from code points so this file stays free of the characters it looks for.
const LONG_DASH = new RegExp(`[${String.fromCharCode(0x2013, 0x2014, 0x2015)}]`);
const TEXT = /\.(ts|tsx|js|jsx|mjs|css|html|json|md|txt|svg|webmanifest)$/;
const web = new URL('../', import.meta.url);

function files(dir: URL): URL[] {
  return readdirSync(dir).flatMap((name) => {
    const entry = new URL(name, dir);
    if (statSync(entry).isDirectory()) return files(new URL(`${name}/`, dir));
    return TEXT.test(name) ? [entry] : [];
  });
}

function longDashLines(file: URL): string[] {
  const name = file.pathname.slice(web.pathname.length);
  return readFileSync(file, 'utf8')
    .split('\n')
    .map((line, index) => `${name}:${index + 1}: ${line.trim()}`)
    .filter((line) => LONG_DASH.test(line));
}

test('no messages file (English or Arabic) contains an em dash or an en dash', () => {
  const messages = files(new URL('src/lib/messages/', web));
  assert.ok(messages.length > 10, 'the messages folder was found');
  assert.deepEqual(messages.flatMap(longDashLines), [], 'use a colon, comma, "to" or a new sentence instead of a long dash');
});

test('no source, public page or index.html contains an em dash or an en dash', () => {
  const all = [...files(new URL('src/', web)), ...files(new URL('public/', web)), new URL('index.html', web)];
  assert.ok(all.length > 100, 'the web sources were found');
  assert.deepEqual(all.flatMap(longDashLines), [], 'use a colon, comma, "to" or a new sentence instead of a long dash');
});
