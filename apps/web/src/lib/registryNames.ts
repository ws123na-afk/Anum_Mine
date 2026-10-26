/**
 * Display names of office registry records (buildings, floors, rooms) by language.
 *
 * The record keeps its English `name` (the stable, searchable one) and an optional Arabic `name_ar`. Every screen that
 * shows a registry name goes through this module: Arabic mode shows the Arabic name when there is one and falls back to
 * the English name when there is not, so nothing is ever blank. Only the builder says that an Arabic name is missing
 * (`arabicMissing`); the rest of the product just shows the English name. Pure: the unit runner covers it.
 */
import { getLocale, type Locale } from './i18n.ts';

export type RegistryNamed = Readonly<{ name: string; name_ar?: string | null }>;
export type SceneNamed = Readonly<{ name: string; nameAr?: string | null }>;

const clean = (value: string | null | undefined): string => (value ?? '').replace(/\s+/g, ' ').trim();

/** The Arabic name in Arabic mode when one is set, else the English name. */
export function pickName(en: string, ar: string | null | undefined, locale: Locale = getLocale()): string {
  const arabic = clean(ar);
  return locale === 'ar' && arabic ? arabic : en;
}

/** A registry record as the API returns it (`name`, `name_ar`). */
export const nameOf = (record: RegistryNamed, locale: Locale = getLocale()): string => pickName(record.name, record.name_ar, locale);

/** A scene record (`name`, `nameAr`), or a room (`label`, `labelAr`) through `pickName` directly. */
export const sceneName = (record: SceneNamed, locale: Locale = getLocale()): string => pickName(record.name, record.nameAr, locale);

/** True in Arabic mode when the record has no Arabic name: the builder shows a hint, nothing else does. */
export function arabicMissing(ar: string | null | undefined, locale: Locale = getLocale()): boolean {
  return locale === 'ar' && !clean(ar);
}

/** The other language's name, for a secondary line, or null when it would repeat the shown one. */
export function otherName(en: string, ar: string | null | undefined, locale: Locale = getLocale()): string | null {
  const arabic = clean(ar);
  if (!arabic) return null;
  const other = locale === 'ar' ? en : arabic;
  return other === pickName(en, ar, locale) ? null : other;
}

/** Lower case text to search in: both names, so a person finds a record in either language. */
export function searchText(en: string, ar: string | null | undefined): string {
  return `${en} ${clean(ar)}`.toLowerCase();
}

export { hasArabicScript } from './arabicScript.ts';

/** A scene room (`label`, `labelAr`) in the current language. */
export const roomName = (room: Readonly<{ label: string; labelAr?: string | null }>, locale: Locale = getLocale()): string => pickName(room.label, room.labelAr, locale);

// Some server answers (the flow, the team panel) carry only a room id and its English name. HeadquartersLive indexes the
// Arabic names of the open registry here, so those places can show the right language without another request.
const arabicByRoomId = new Map<string, string | null>();
export function indexRoomNames(rooms: Iterable<Readonly<{ id: string; labelAr?: string | null }>>): void {
  arabicByRoomId.clear();
  for (const room of rooms) arabicByRoomId.set(room.id, room.labelAr ?? null);
}
/** A room named only by id and English name, in the current language (English when the index has no Arabic name for it). */
export const roomNameFor = (id: string, english: string, locale: Locale = getLocale()): string => pickName(english, arabicByRoomId.get(id), locale);
