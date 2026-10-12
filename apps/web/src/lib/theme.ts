// The interface theme (docs/design/anum360.md#theme): Dark (the default, the mockups' look), Light, or
// the device's setting, as in the Flutter app (Profile & Settings > Theme). The choice is kept in this
// browser under the key the app uses (`anum.theme`). The tokens for both themes are in styles.css;
// <html data-theme> names the one in use. index.html applies the saved choice before the first paint
// (the same rules as `resolveTheme`, inlined), so a light page never flashes dark or the other way.
// Pure helpers first (unit tested in test/theme.test.ts), then the browser wiring.

export const THEME_CHOICES = ['dark', 'light', 'system'] as const;
export type ThemeChoice = (typeof THEME_CHOICES)[number];
export type Theme = 'dark' | 'light';
/** Browser storage key; the same name the Flutter app uses for its saved choice. */
export const THEME_STORAGE_KEY = 'anum.theme';
/** The default: the ANUM 360 look is designed dark first. */
export const DEFAULT_THEME_CHOICE: ThemeChoice = 'dark';
/** The browser chrome colour (meta theme-color) for each theme: its page background. */
export const THEME_COLORS: Record<Theme, string> = { dark: '#070b1a', light: '#f4f6fb' };

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return typeof value === 'string' && (THEME_CHOICES as readonly string[]).includes(value);
}

/** The saved choice, or the default when nothing (or something unknown) is saved. */
export function parseThemeChoice(saved: string | null | undefined): ThemeChoice {
  return isThemeChoice(saved) ? saved : DEFAULT_THEME_CHOICE;
}

/** The theme a choice shows: "system" follows the device's light or dark setting. */
export function resolveTheme(choice: ThemeChoice, prefersLight: boolean): Theme {
  return choice === 'system' ? (prefersLight ? 'light' : 'dark') : choice;
}

// ---------------------------------------------------------------- browser wiring

type ThemeStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null;
interface ThemeEnvironment { storage: ThemeStorage; root: HTMLElement | null; media: MediaQueryList | null }

let environment: ThemeEnvironment = { storage: null, root: null, media: null };
let choice: ThemeChoice = DEFAULT_THEME_CHOICE;
let theme: Theme = 'dark';
const listeners = new Set<() => void>();

function readSaved(storage: ThemeStorage): string | null {
  try { return storage?.getItem(THEME_STORAGE_KEY) ?? null; } catch { return null; }
}

function apply(): void {
  theme = resolveTheme(choice, environment.media?.matches ?? false);
  const root = environment.root;
  if (root) {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    root.ownerDocument.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLORS[theme]);
  }
  for (const listener of listeners) listener();
}

/** Called once from main.tsx: reads the saved choice and follows the device while the choice is "system". */
export function initTheme(env: ThemeEnvironment): Theme {
  environment = env;
  choice = parseThemeChoice(readSaved(env.storage));
  env.media?.addEventListener('change', () => { if (choice === 'system') apply(); });
  apply();
  return theme;
}

export function getThemeChoice(): ThemeChoice { return choice; }
export function getTheme(): Theme { return theme; }

/** Saves a choice in this browser and applies it at once. */
export function chooseTheme(next: ThemeChoice): Theme {
  choice = next;
  try {
    if (next === DEFAULT_THEME_CHOICE) environment.storage?.removeItem(THEME_STORAGE_KEY);
    else environment.storage?.setItem(THEME_STORAGE_KEY, next);
  } catch { /* the choice still applies for this visit */ }
  apply();
  return theme;
}

export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
