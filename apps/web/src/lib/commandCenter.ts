// Small helpers ANUM Command and Home share (docs/command.md#the-web-command-center): the speaking
// language for push-to-talk, relative times and file sizes. No browser APIs at import time, so
// `pnpm --filter @anum/web test:unit` runs them in Node. The command rules are in commands.ts.
import { getLocale, intlLocale, t } from './i18n.ts';

/**
 * Speaking languages for voice commands. Only languages the interface has a catalog for are offered
 * (English and Arabic); Urdu returns when the web catalogs gain it (docs/web-localization.md).
 */
export type SpeechLanguage = 'en' | 'ar';
export const SPEECH_LANGUAGES: readonly SpeechLanguage[] = ['en', 'ar'];
/** The recognizer locale for each speaking language. */
export const SPEECH_LOCALES: Record<SpeechLanguage, string> = { en: 'en-US', ar: 'ar-SA' };
export const SPEECH_LANGUAGE_KEY = 'anum.command.language';

export function isSpeechLanguage(value: unknown): value is SpeechLanguage {
  return typeof value === 'string' && (SPEECH_LANGUAGES as readonly string[]).includes(value);
}

/** The saved speaking language, else the interface language. */
export function initialSpeechLanguage(saved: string | null | undefined, interfaceLocale: string): SpeechLanguage {
  if (isSpeechLanguage(saved)) return saved;
  return interfaceLocale === 'ar' ? 'ar' : 'en';
}

// ---------------------------------------------------------------- time

/** "2 hours ago" in the interface language (Intl.RelativeTimeFormat). */
export function relativeTime(iso: string, now = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return '';
  const seconds = Math.round((at - now) / 1000);
  const format = new Intl.RelativeTimeFormat(intlLocale(getLocale()), { numeric: 'auto' });
  const abs = Math.abs(seconds);
  if (abs < 45) return t('command.time.justNow');
  if (abs < 3600) return format.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return format.format(Math.round(seconds / 3600), 'hour');
  if (abs < 86400 * 30) return format.format(Math.round(seconds / 86400), 'day');
  return format.format(Math.round(seconds / (86400 * 30)), 'month');
}

/** A file size in the units the Documents view uses. */
export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return t('command.size.bytes', { size: bytes });
  if (bytes < 1024 * 1024) return t('command.size.kb', { size: Math.round(bytes / 102.4) / 10 });
  return t('command.size.mb', { size: Math.round(bytes / (1024 * 102.4)) / 10 });
}
