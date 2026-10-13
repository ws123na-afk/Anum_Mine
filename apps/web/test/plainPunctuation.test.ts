// Owner rule (13 Oct 2026): no long dashes in the app's words. Use a colon, a comma, "to" for a
// range or a new sentence instead (docs/web-localization.md, Punctuation).
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';

const messages = new URL('../src/lib/messages/', import.meta.url);
const LONG_DASH = /[\u2013\u2014]/;

test('no messages file (English or Arabic) contains an em dash or an en dash', () => {
  const files = readdirSync(messages).filter((name) => name.endsWith('.ts'));
  assert.ok(files.length > 10, 'the messages folder was found');
  for (const name of files) {
    const lines = readFileSync(new URL(name, messages), 'utf8').split('\n');
    const found = lines.map((line, index) => `${name}:${index + 1}: ${line.trim()}`).filter((line) => LONG_DASH.test(line));
    assert.deepEqual(found, [], `use a colon, comma, "to" or a new sentence instead of a long dash in ${name}`);
  }
});
