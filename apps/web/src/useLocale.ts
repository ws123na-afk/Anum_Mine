// The current locale as React state (docs/web-localization.md). The root component calls it so a
// language switch re-renders the whole tree; pure helpers read the same locale through `t`.
import { useSyncExternalStore } from 'react';
import { getLocale, subscribeLocale, type Locale } from './lib/i18n';

export function useLocale(): Locale {
  return useSyncExternalStore(subscribeLocale, getLocale, getLocale);
}
