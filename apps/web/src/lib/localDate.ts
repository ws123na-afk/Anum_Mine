/**
 * The value of a date or date-and-time field written in the app's language (docs/web-localization.md, Numbers and dates).
 * The browser's own date picker follows the browser's language, not the page's, so an Arabic page can show an English
 * picker: the field then shows the chosen day in Arabic next to it. Pure: the unit runner covers it.
 */
import { intlLocale, type Locale } from './i18n.ts';

export type DateFieldKind = 'date' | 'datetime-local';

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DATE_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,3})?)?$/;

/** The field's value (`2026-10-28` or `2026-10-28T09:30`, the browser's own wall time) as words of `locale`; '' when empty or not a real day. */
export function localDateText(value: string, kind: DateFieldKind, locale: Locale): string {
  const m = (kind === 'date' ? DATE : DATE_TIME).exec(value.trim());
  if (!m) return '';
  const [y, mo, d, h = '0', mi = '0'] = m.slice(1).map((part) => part ?? '0');
  const at = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi));
  // 2026-02-31 rolls over to March: that is not the day the field holds, so say nothing.
  if (at.getFullYear() !== Number(y) || at.getMonth() !== Number(mo) - 1 || at.getDate() !== Number(d) || at.getHours() !== Number(h) || at.getMinutes() !== Number(mi)) return '';
  const options: Intl.DateTimeFormatOptions = kind === 'date'
    ? { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }
    : { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', hour: 'numeric', minute: '2-digit' };
  return new Intl.DateTimeFormat(intlLocale(locale), options).format(at);
}
