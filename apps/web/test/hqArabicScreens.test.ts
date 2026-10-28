// Arabic for three HQ screens (docs/web-localization.md): the guided visit, reception and office space management.
// Every key the screens read exists in English and Arabic, the Arabic is really Arabic, nothing visible is a hard-coded
// English literal, direction-bearing icons mirror and the styles use logical properties.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { CATALOGS, translate, type MessageKey } from '../src/lib/i18n.ts';
import { officeSpaceEn } from '../src/lib/messages/officeSpace.en.ts';
import { officeSpaceAr } from '../src/lib/messages/officeSpace.ar.ts';

const read = (file: string) => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
const SCREENS = {
  'GuidedExecutiveTour.tsx': ['gt.', 'hqSc.kind.', 'common.'],
  'HqReceptionPanel.tsx': ['hqRec.'],
  'OfficeSpaceLiveManager.tsx': ['osm.', 'hqSc.kind.', 'hqb.'],
} as const;
// Messages that are the same in both languages on purpose: placeholders and punctuation only, or axis letters.
const SAME_ON_PURPOSE = new Set(['hqRec.filterCount', 'osm.floor.option', 'osm.geo.x', 'osm.geo.z']);
const ARABIC = /[؀-ۿ]/;
const forms = (key: MessageKey, locale: 'en' | 'ar') => {
  const m = CATALOGS[locale][key];
  return typeof m === 'string' ? [m] : Object.values(m).filter((v): v is string => typeof v === 'string');
};

/** Literal keys (`t('a.b')`) and template families (`t(`gt.event.${...}`)`, expanded to every catalog key with that prefix). */
function keysOf(source: string): MessageKey[] {
  const literal = [...source.matchAll(/\bt\(\s*'([\w.]+)'/g)].map((m) => m[1]);
  const ternary = [...source.matchAll(/\bt\(\s*\w+\s*\?\s*'([\w.]+)'\s*:\s*'([\w.]+)'/g)].flatMap((m) => [m[1], m[2]]);
  const families = [...source.matchAll(/\bt\(\s*`([\w.]+)\.\$\{/g)].map((m) => `${m[1]}.`);
  const all = Object.keys(CATALOGS.en);
  return [...new Set([...literal, ...ternary, ...families.flatMap((p) => all.filter((k) => k.startsWith(p)))])] as MessageKey[];
}

describe('Arabic HQ screens', () => {
  for (const [file, prefixes] of Object.entries(SCREENS)) {
    const source = read(file);
    const keys = keysOf(source);

    test(`${file}: every key it reads exists in English and Arabic`, () => {
      assert.ok(keys.length > 10, `${file} reads its words from the catalog`);
      for (const key of keys) {
        assert.ok(prefixes.some((p) => key.startsWith(p)), `${file} reads an unexpected key ${key}`);
        assert.ok(CATALOGS.en[key] !== undefined, `en ${key}`);
        assert.ok(CATALOGS.ar[key] !== undefined, `ar ${key}`);
      }
    });

    test(`${file}: the Arabic is Arabic, with no long dashes`, () => {
      for (const key of keys) {
        for (const text of forms(key, 'ar')) {
          assert.ok(!/[–—]/.test(text), `ar ${key} has a long dash`);
          if (SAME_ON_PURPOSE.has(key)) continue;
          assert.ok(ARABIC.test(text), `ar ${key} is not Arabic: ${text}`);
        }
      }
    });
  }

  test('office space management has no English text outside the catalog', () => {
    const source = read('OfficeSpaceLiveManager.tsx');
    // JSX text between tags, and the attributes a person sees or hears.
    const jsxText = [...source.matchAll(/(?<!=)>\s*([A-Za-z][A-Za-z ,.'()]{2,})\s*</g)].map((m) => m[1]);
    assert.deepEqual(jsxText, []);
    const attrs = [...source.matchAll(/\b(aria-label|title|placeholder)="([^"]*[A-Za-z][^"]*)"/g)].map((m) => m[0]);
    assert.deepEqual(attrs, []);
    assert.ok(!/window\.confirm\('/.test(source), 'confirmations come from the catalog');
    assert.ok(!/set(Error|Notice)\('[^']/.test(source), 'notices and errors come from the catalog');
  });

  test('the office space catalogs have the same keys and plural shapes', () => {
    assert.deepEqual(Object.keys(officeSpaceAr).sort(), Object.keys(officeSpaceEn).sort());
    for (const key of Object.keys(officeSpaceEn) as (keyof typeof officeSpaceEn)[]) {
      assert.equal(typeof officeSpaceAr[key], typeof officeSpaceEn[key], key);
    }
  });

  test('room types, counts and names read correctly in Arabic', () => {
    assert.equal(translate('ar', 'osm.roomMeta', { kind: 'استقبال', capacity: 6, count: 2 }), 'استقبال · السعة 6 · مكتبان');
    assert.equal(translate('ar', 'osm.footer.loaded', { count: 3, revision: 4 }), 'حُمّلت سجلات الخادم · 3 مبانٍ · النسخة 4');
    // A Latin building name is isolated so it keeps its place in the right-to-left sentence.
    assert.equal(translate('ar', 'osm.addFloor', { building: 'Al Noor Fleet HQ' }), 'إضافة طابق إلى ⁨Al Noor Fleet HQ⁩');
    assert.equal(translate('en', 'osm.roomMeta', { kind: 'office', capacity: 6, count: 1 }), 'office · Capacity 6 · 1 desk');
  });

  test('icons that point a way mirror in right to left', () => {
    assert.match(read('GuidedExecutiveTour.tsx'), /<ArrowRight [^>]*className="flipRtl"/);
    const reception = read('HqReceptionPanel.tsx');
    assert.match(reception, /<LogIn className="flipRtl"/);
    assert.match(reception, /<LogOut className="flipRtl"/);
    assert.match(read('OfficeSpaceLiveManager.tsx'), /<ArrowLeft [^>]*className="flipRtl"/);
  });

  test('the screens style with logical properties only', () => {
    for (const css of ['guidedExecutiveTour.css', 'hqReception.css', 'officeSpaceRegistry.css']) {
      const text = read(css);
      assert.ok(!/(^|[;{\s])(left|right)\s*:/.test(text), `${css} uses left/right`);
      assert.ok(!/(margin|padding|border)-(left|right)\b/.test(text), `${css} uses a physical side`);
      assert.ok(!/text-align\s*:\s*(left|right)/.test(text), `${css} aligns to a physical side`);
    }
  });
});
