// Unit tests for the theme choice (src/lib/theme.ts), the pre-paint script (public/theme-init.js) and
// the theme tokens in src/styles.css (docs/design/anum360.md#theme). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { runInNewContext } from 'node:vm';
import {
  DEFAULT_THEME_CHOICE, THEME_COLORS, THEME_STORAGE_KEY, chooseTheme, getTheme, getThemeChoice, initTheme, parseThemeChoice,
  resolveTheme, subscribeTheme,
} from '../src/lib/theme.ts';

const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');
const initScript = readFileSync(new URL('../public/theme-init.js', import.meta.url), 'utf8');

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return { data, getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); }, removeItem: (key: string) => { data.delete(key); } };
}
function fakeRoot() {
  const meta = { content: '', setAttribute(_: string, value: string) { this.content = value; } };
  return { meta, root: { dataset: {} as Record<string, string>, style: { colorScheme: '' }, ownerDocument: { querySelector: () => meta } } };
}
function fakeMedia(matches: boolean) {
  const listeners: (() => void)[] = [];
  return { media: { matches, addEventListener: (_: string, listener: () => void) => { listeners.push(listener); } }, flip(next: boolean) { this.media.matches = next; listeners.forEach((l) => l()); } };
}

describe('theme choice', () => {
  test('dark is the default, as in the app', () => {
    assert.equal(DEFAULT_THEME_CHOICE, 'dark');
    assert.equal(parseThemeChoice(null), 'dark');
    assert.equal(parseThemeChoice('sepia'), 'dark');
    assert.equal(parseThemeChoice('light'), 'light');
    assert.equal(parseThemeChoice('system'), 'system');
  });

  test('"system" follows the device; dark and light ignore it', () => {
    assert.equal(resolveTheme('system', true), 'light');
    assert.equal(resolveTheme('system', false), 'dark');
    assert.equal(resolveTheme('dark', true), 'dark');
    assert.equal(resolveTheme('light', false), 'light');
  });

  test('the app and the web share the storage key', () => assert.equal(THEME_STORAGE_KEY, 'anum.theme'));

  test('init applies the saved choice to <html>, and a choice is saved and applied at once', () => {
    const storage = fakeStorage({ [THEME_STORAGE_KEY]: 'light' });
    const { root, meta } = fakeRoot();
    const device = fakeMedia(false);
    let calls = 0;
    const off = subscribeTheme(() => { calls += 1; });
    assert.equal(initTheme({ storage, root: root as unknown as HTMLElement, media: device.media as unknown as MediaQueryList }), 'light');
    assert.equal(root.dataset.theme, 'light');
    assert.equal(root.style.colorScheme, 'light');
    assert.equal(meta.content, THEME_COLORS.light);

    assert.equal(chooseTheme('system'), 'dark');
    assert.equal(getThemeChoice(), 'system');
    assert.equal(storage.data.get(THEME_STORAGE_KEY), 'system');
    device.flip(true);
    assert.equal(getTheme(), 'light');
    assert.equal(root.dataset.theme, 'light');

    // Choosing the default forgets the saved value; the device no longer matters.
    chooseTheme('dark');
    assert.equal(storage.data.has(THEME_STORAGE_KEY), false);
    device.flip(false);
    device.flip(true);
    assert.equal(root.dataset.theme, 'dark');
    assert.ok(calls >= 4);
    off();
  });

  test('blocked storage keeps the choice for the visit', () => {
    const storage = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } };
    const { root } = fakeRoot();
    assert.equal(initTheme({ storage, root: root as unknown as HTMLElement, media: null }), 'dark');
    assert.equal(chooseTheme('light'), 'light');
    assert.equal(root.dataset.theme, 'light');
  });
});

describe('pre-paint script (public/theme-init.js)', () => {
  function run(saved: string | null, prefersLight: boolean) {
    const attributes: Record<string, string> = {};
    const root = { setAttribute: (name: string, value: string) => { attributes[name] = value; }, style: { colorScheme: '' } };
    const meta = { content: '', setAttribute(_: string, value: string) { this.content = value; } };
    const window = { localStorage: { getItem: () => saved }, matchMedia: () => ({ matches: prefersLight }) };
    runInNewContext(initScript, { window, document: { documentElement: root, querySelector: () => meta } });
    return { theme: attributes['data-theme'], scheme: root.style.colorScheme, color: meta.content };
  }

  test('applies the same rules as resolveTheme', () => {
    for (const saved of [null, 'dark', 'light', 'system', 'unknown']) {
      for (const prefersLight of [false, true]) {
        const expected = resolveTheme(parseThemeChoice(saved), prefersLight);
        assert.deepEqual(run(saved, prefersLight), { theme: expected, scheme: expected, color: THEME_COLORS[expected] }, `${saved} / ${prefersLight}`);
      }
    }
  });
});

// ---------------------------------------------------------------- tokens

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  assert.ok(start >= 0, `${selector} block`);
  const body = css.slice(start, css.indexOf('\n}', start));
  return Object.fromEntries([...body.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}
/** Tokens with `var(--x)` references resolved within the same theme (the accent aliases point at --d-accent). */
function resolved(tokens: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  const get = (name: string, depth = 0): string => {
    const value = tokens[name] ?? '';
    return depth > 8 ? value : value.replace(/var\((--[a-z0-9-]+)\)/g, (_, ref: string) => get(ref, depth + 1));
  };
  for (const name of Object.keys(tokens)) out[name] = get(name);
  return out;
}
const darkRaw = block(':root');
const dark = resolved(darkRaw);
const light = resolved({ ...darkRaw, ...block(':root[data-theme="light"]') });

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

describe('theme tokens (src/styles.css)', () => {
  test('the light theme only redefines tokens the dark theme has', () => {
    for (const name of Object.keys(block(':root[data-theme="light"]'))) assert.ok(name in darkRaw, `${name} is not a dark token`);
  });

  test('the palette is graphite and jade (docs/design/visual-identity.md)', () => {
    assert.deepEqual([dark['--d-bg'], dark['--d-surface-1'], dark['--d-surface-2'], dark['--d-surface-3']], ['#0b0d12', '#12151c', '#171b24', '#1f2430']);
    assert.deepEqual([dark['--d-text'], dark['--d-muted'], dark['--d-faint']], ['#eceef3', '#a2a8b6', '#868d9c']);
    assert.equal(light['--d-bg'], '#f7f8fa');
    assert.equal(light['--d-text'], '#12151c');
    // One accent: every older accent alias resolves to it, in both themes.
    for (const theme of [dark, light]) for (const alias of ['--d-sky', '--d-violet', '--d-blue', '--d-rose']) assert.equal(theme[alias], theme['--d-accent'], alias);
    assert.equal(THEME_COLORS.dark, dark['--d-bg']);
    assert.equal(THEME_COLORS.light, light['--d-bg']);
  });

  for (const [name, theme] of [['dark', dark], ['light', light]] as const) {
    test(`${name}: text, links and status colours pass AA on the page and on panels`, () => {
      for (const surface of ['--d-bg', '--d-surface-1', '--d-surface-2', '--d-surface-3']) {
        for (const text of ['--d-text', '--d-muted', '--d-faint', '--d-accent', '--d-ok', '--d-warn', '--d-stop']) {
          const ratio = contrast(theme[text], theme[surface]);
          assert.ok(ratio >= 4.5, `${name} ${text} ${theme[text]} on ${surface} ${theme[surface]} is ${ratio.toFixed(2)}:1`);
        }
      }
    });
  }

  test('white text passes AA on the primary button (and its hover) and on the danger button', () => {
    for (const colour of [dark['--d-primary'], dark['--d-primary-hover'], ...[...dark['--d-primary-grad'].matchAll(/#[0-9a-f]{6}/g)].map((m) => m[0])]) assert.ok(contrast('#ffffff', colour) >= 4.5, colour);
    assert.ok(contrast('#ffffff', dark['--d-danger']) >= 4.5);
  });
});
