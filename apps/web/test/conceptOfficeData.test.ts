// Data truth (security review S-11): the example rooms and example staff of the "Concept preview" tab can never
// become a stored record or an API request. The sample data lives in one module that only the concept view imports,
// the concept view itself makes no request, and the reception simulation with its example hosts is gone.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, test } from 'node:test';
import { ROOMS, SITE_ROOMS } from '../src/lib/conceptOfficeData.ts';

const SRC = new URL('../src/', import.meta.url).pathname;
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : /\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}
const FILES = walk(SRC).map((f) => ({ file: relative(SRC, f), text: readFileSync(f, 'utf8') }));
const exampleStrings = [...new Set(SITE_ROOMS.flat().concat(ROOMS).flatMap((r) => r.people))];

describe('concept preview sample data cannot be stored or sent', () => {
  test('there are example strings to guard', () => {
    assert.ok(exampleStrings.length >= 10);
    assert.ok(exampleStrings.includes('Finance Controller'));
  });
  test('only the concept view imports the sample data', () => {
    const importers = FILES.filter((f) => /conceptOfficeData/.test(f.text) && f.file !== 'lib/conceptOfficeData.ts').map((f) => f.file);
    assert.deepEqual(importers, ['OfficeTourView.tsx']);
  });
  test('the concept view makes no request and does not hand sample data to a component that does', () => {
    const view = FILES.find((f) => f.file === 'OfficeTourView.tsx')!.text;
    assert.ok(!/\bfetch\s*\(|apiRequest|from '\.\/lib\/api|XMLHttpRequest|sendBeacon|localStorage|sessionStorage/.test(view));
    for (const tag of ['OfficeSpaceLiveManager', 'OfficeWorkstations']) {
      const use = view.match(new RegExp(`<${tag}\\b[\\s\\S]*?/>`));
      assert.ok(use, tag);
      assert.ok(!/rooms|people|ROOMS|SITE_ROOMS|visible|active/.test(use[0]), `${tag} receives no sample data`);
    }
  });
  test('the sample data module itself has no effects', () => {
    const data = FILES.find((f) => f.file === 'lib/conceptOfficeData.ts')!.text;
    assert.ok(!/\bimport\b|fetch|apiRequest|localStorage/.test(data));
  });
  test('no example host or example room list outside the sample data and the concept view', () => {
    for (const f of FILES.filter((x) => !['lib/conceptOfficeData.ts', 'OfficeTourView.tsx'].includes(x.file))) {
      assert.ok(!/EXAMPLE_HOSTS|DEMO_ROOMS|ReceptionOperations|Sample Customer|Sample Supplier|Sample Delivery/.test(f.text), f.file);
      for (const name of ['Reception coordinator', 'Tyre Fleet Planner', 'Automation Guardian', 'Fleet Proposal Consultant']) {
        assert.ok(!f.text.includes(name), `${f.file} contains the example name ${name}`);
      }
    }
  });
  test('the reception simulation is gone', () => {
    for (const gone of ['ReceptionOperations.tsx', 'receptionOperations.css', 'lib/receptionOperations.ts']) assert.equal(existsSync(join(SRC, gone)), false, gone);
    assert.ok(!FILES.some((f) => /Reception Operations|register arrival/i.test(f.text)));
  });
});
