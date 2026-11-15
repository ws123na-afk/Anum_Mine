// Every message catalog spreads into one `en`/`ar` object, so a key defined in two catalog files silently loses one text
// (a new catalog once overrode the statement-match wording). Each key lives in exactly one catalog file per language.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

const dir = join(import.meta.dirname, '..', 'src', 'lib', 'messages');

for (const lang of ['en', 'ar']) {
  test(`no ${lang} message key is defined in two catalog files`, () => {
    const files = readdirSync(dir).filter((name) => name === `${lang}.ts` || name.endsWith(`.${lang}.ts`));
    const seen = new Map<string, string>();
    const clashes: string[] = [];
    for (const file of files) {
      for (const match of readFileSync(join(dir, file), 'utf8').matchAll(/^\s*'([A-Za-z0-9_.:-]+)'\s*:/gm)) {
        const key = match[1]!;
        const first = seen.get(key);
        if (first && first !== file) clashes.push(`${key} (${first}, ${file})`);
        else seen.set(key, file);
      }
    }
    assert.deepEqual(clashes, []);
  });
}
