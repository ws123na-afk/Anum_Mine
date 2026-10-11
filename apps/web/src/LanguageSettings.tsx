// Settings > Language (docs/web-localization.md): the interface language for this browser.
// "Match my browser" forgets the saved choice and follows navigator.languages (what the browser
// sends as Accept-Language); English or Arabic is saved in this browser's storage.
import { useId, useState } from 'react';
import { CircleDot, Languages } from 'lucide-react';
import { LOCALES, LOCALE_NAMES, browserLocale, chooseLocale, savedLocaleChoice, t, type Locale } from './lib/i18n';
import { useLocale } from './useLocale';

type Choice = Locale | 'browser';

export function LanguageSettings() {
  const locale = useLocale();
  const [choice, setChoice] = useState<Choice>(() => savedLocaleChoice() ?? 'browser');
  const headingId = useId(), hintId = useId();
  const detected = browserLocale(navigator.languages?.length ? navigator.languages : [navigator.language]);
  const pick = (next: Choice) => { setChoice(next); chooseLocale(next === 'browser' ? null : next); };

  return <section className="surface settingsForm languageSettings" aria-labelledby={headingId}>
    <div className="sectionHeader">
      <div><p className="eyebrow">{t('language.eyebrow')}</p><h2 id={headingId}>{t('language.title')}</h2></div>
      <span className="statusBadge neutral"><Languages aria-hidden="true" size={14} /><span lang={locale}>{LOCALE_NAMES[locale]}</span></span>
    </div>
    <p className="muted">{t('language.intro')}</p>
    <fieldset className="languageChoices">
      <legend>{t('language.legend')}</legend>
      <label className="languageChoice">
        <input type="radio" name="interface-language" value="browser" checked={choice === 'browser'} onChange={() => pick('browser')} aria-describedby={hintId} />
        <span><strong>{t('language.browser', { language: LOCALE_NAMES[detected] })}</strong><small id={hintId}>{t('language.browserHint')}</small></span>
      </label>
      {LOCALES.map((option) => <label className="languageChoice" key={option}>
        <input type="radio" name="interface-language" value={option} checked={choice === option} onChange={() => pick(option)} />
        {/* Each language is named in itself, so a person who cannot read the current one still finds theirs. */}
        <span><strong lang={option} dir={option === 'ar' ? 'rtl' : 'ltr'}>{LOCALE_NAMES[option]}</strong></span>
      </label>)}
    </fieldset>
    <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{choice === 'browser' ? t('language.followsBrowser', { language: LOCALE_NAMES[locale] }) : t('language.savedChoice', { language: LOCALE_NAMES[locale] })}</p>
    <p className="muted">{t('language.storageNote')}</p>
  </section>;
}
