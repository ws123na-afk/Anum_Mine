// Web localization (docs/web-localization.md): one message catalog per locale, `{name}`
// interpolation, CLDR plural categories through Intl.PluralRules, and the locale choice
// (the person's pick, saved per browser, else the browser's languages, else English).
// Pure and dependency-free, so `pnpm --filter @anum/web test:unit` runs it without a browser;
// the browser wiring (storage, <html lang dir>) is `initLocale` below, called once from main.tsx.
import { en, type MessageKey } from './messages/en.ts';
import { ar } from './messages/ar.ts';
import type { Message, PluralMessage } from './messages/types.ts';

export type { MessageKey } from './messages/en.ts';
export type { Message, PluralMessage } from './messages/types.ts';

export const LOCALES = ['en', 'ar'] as const;
export type Locale = (typeof LOCALES)[number];
export type Direction = 'ltr' | 'rtl';

export type Catalog = Record<MessageKey, Message>;
export type Vars = Record<string, string | number>;

export const CATALOGS: Record<Locale, Catalog> = { en, ar };
/** The language names the switcher shows, each in its own language. */
export const LOCALE_NAMES: Record<Locale, string> = { en: 'English', ar: 'العربية' };
/** Browser storage key; the same name the Flutter app uses for its saved choice. */
export const LOCALE_STORAGE_KEY = 'anum.locale';

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as readonly string[]).includes(value);
}

export function directionOf(locale: Locale): Direction {
  return locale === 'ar' ? 'rtl' : 'ltr';
}

/**
 * The Intl locale for numbers and dates. Arabic keeps Western digits so ids, hashes, counts and
 * codes read the same in both languages (docs/web-localization.md, Numbers and dates).
 */
export function intlLocale(locale: Locale): string {
  return locale === 'ar' ? 'ar-u-nu-latn' : 'en';
}

/**
 * The locale to start with: a saved choice wins; otherwise the first of the browser's languages
 * (navigator.languages, the same list it sends as Accept-Language) that has a catalog; else English.
 */
export function resolveLocale(saved: string | null | undefined, languages: readonly string[] = []): Locale {
  if (isLocale(saved)) return saved;
  for (const tag of languages) {
    const primary = tag.trim().toLowerCase().split(/[-_]/)[0];
    if (isLocale(primary)) return primary;
  }
  return 'en';
}

/** The first catalog-backed locale in an Accept-Language-style list, ignoring a saved choice. */
export function browserLocale(languages: readonly string[] = []): Locale {
  return resolveLocale(null, languages);
}

const pluralRules = new Map<Locale, Intl.PluralRules>();
function pluralCategory(locale: Locale, count: number): Intl.LDMLPluralRule {
  let rules = pluralRules.get(locale);
  if (!rules) { rules = new Intl.PluralRules(intlLocale(locale)); pluralRules.set(locale, rules); }
  return rules.select(count);
}

function pickPlural(message: PluralMessage, locale: Locale, count: number): string {
  // An explicit zero form ("No tasks") applies in every language, not only where CLDR has one.
  if (count === 0 && message.zero !== undefined) return message.zero;
  return message[pluralCategory(locale, count)] ?? message.other;
}

export function formatNumber(value: number, locale: Locale = current): string {
  return new Intl.NumberFormat(intlLocale(locale)).format(value);
}

/** First-strong isolate and its closing mark (Unicode bidi isolation, invisible). */
export const FSI = '\u2068', PDI = '\u2069';

function interpolate(text: string, vars: Vars | undefined, locale: Locale, isolate: boolean): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = vars[name];
    if (value === undefined) return whole;
    if (typeof value === 'number') return formatNumber(value, locale);
    // In right-to-left text, a left-to-right value (an id, a name, an English server message)
    // is isolated so its punctuation and digits do not reorder the sentence around it.
    return isolate && /[A-Za-z]/.test(value) ? `${FSI}${value}${PDI}` : value;
  });
}

export interface TranslateOptions {
  /** Isolate Latin-script values in right-to-left locales (default true). */
  isolate?: boolean;
}

/** The message for `key` in `locale` (falling back to English, then the key), with `vars` filled in. */
export function translate(locale: Locale, key: MessageKey, vars?: Vars, options: TranslateOptions = {}): string {
  const message: Message | undefined = CATALOGS[locale][key] ?? CATALOGS.en[key];
  if (message === undefined) return key;
  const text = typeof message === 'string' ? message : pickPlural(message, locale, Number(vars?.count ?? 0));
  return interpolate(text, vars, locale, (options.isolate ?? true) && directionOf(locale) === 'rtl');
}

/** Placeholder names in a message (every plural form), for catalog checks. */
export function placeholdersOf(message: Message): string[] {
  const forms = typeof message === 'string' ? [message] : Object.values(message).filter((v): v is string => typeof v === 'string');
  return [...new Set(forms.flatMap((form) => [...form.matchAll(/\{(\w+)\}/g)].map((m) => m[1])))].sort();
}

// ---- Current locale (module state, so pure helpers and components share one source) ----

let current: Locale = 'en';
const listeners = new Set<(locale: Locale) => void>();

export function getLocale(): Locale { return current; }

/** Switch the current locale and notify subscribers (React re-renders, <html> updates). */
export function setLocale(locale: Locale): void {
  if (locale === current) return;
  current = locale;
  for (const listener of [...listeners]) listener(locale);
}

export function subscribeLocale(listener: (locale: Locale) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Translate in the current locale. */
export function t(key: MessageKey, vars?: Vars, options?: TranslateOptions): string {
  return translate(current, key, vars, options);
}

export function formatDateTime(value: string | number | Date, options: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' }, locale: Locale = current): string {
  return new Intl.DateTimeFormat(intlLocale(locale), options).format(new Date(value));
}

/** Names joined for a sentence: "a", "a and b", "a, b and c" (and the Arabic "أ، ب وج"). */
export function joinList(names: string[], locale: Locale = current): string {
  if (names.length <= 1) return names[0] ?? '';
  return translate(locale, 'common.listAnd', { head: names.slice(0, -1).join(translate(locale, 'common.listSeparator')), last: names[names.length - 1] });
}

// ---- Browser wiring ----

export interface LocaleEnvironment {
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null;
  languages?: readonly string[];
  root?: { lang: string; dir: string } | null;
}

function readSaved(storage: LocaleEnvironment['storage']): string | null {
  try { return storage?.getItem(LOCALE_STORAGE_KEY) ?? null; } catch { return null; }
}

function applyRoot(root: LocaleEnvironment['root'], locale: Locale): void {
  if (!root) return;
  root.lang = locale;
  root.dir = directionOf(locale);
}

let environment: LocaleEnvironment = {};

/** Pick the starting locale, set <html lang dir>, and keep them in step with later changes. */
export function initLocale(env: LocaleEnvironment): Locale {
  environment = env;
  current = resolveLocale(readSaved(env.storage), env.languages ?? []);
  applyRoot(env.root, current);
  subscribeLocale((locale) => applyRoot(environment.root, locale));
  return current;
}

/** The saved choice, or null when the page follows the browser's languages. */
export function savedLocaleChoice(): Locale | null {
  const saved = readSaved(environment.storage);
  return isLocale(saved) ? saved : null;
}

/**
 * The person's choice from Settings: a locale is saved in this browser; null forgets the choice
 * and follows the browser's languages again. Storage failures (private mode) still switch the page.
 */
export function chooseLocale(choice: Locale | null): Locale {
  try {
    if (choice) environment.storage?.setItem(LOCALE_STORAGE_KEY, choice);
    else environment.storage?.removeItem(LOCALE_STORAGE_KEY);
  } catch { /* the choice applies to this page only */ }
  const next = choice ?? browserLocale(environment.languages ?? []);
  setLocale(next);
  return next;
}
