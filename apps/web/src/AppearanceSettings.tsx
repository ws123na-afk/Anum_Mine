// Settings > Appearance (docs/design/anum360.md#theme): Dark (the default), Light, or the device's
// setting, kept in this browser like the app's Profile & Settings > Theme.
import { useId, useSyncExternalStore } from 'react';
import { CircleDot, Moon, Sun, SunMoon } from 'lucide-react';
import { chooseTheme, getTheme, getThemeChoice, subscribeTheme, type Theme, type ThemeChoice } from './lib/theme';
import { t } from './lib/i18n';

const ICONS = { dark: Moon, light: Sun, system: SunMoon } as const;

export function AppearanceSettings() {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, getTheme);
  const choice = useSyncExternalStore(subscribeTheme, getThemeChoice, getThemeChoice);
  const headingId = useId();
  const name = (value: Theme) => t(`appearance.name.${value}`);
  const options: { value: ThemeChoice; label: string; hint: string }[] = [
    { value: 'dark', label: t('appearance.dark'), hint: t('appearance.darkHint') },
    { value: 'light', label: t('appearance.light'), hint: t('appearance.lightHint') },
    { value: 'system', label: t('appearance.system', { theme: name(theme) }), hint: t('appearance.systemHint') },
  ];
  const Current = ICONS[theme];

  return <section className="surface settingsForm languageSettings appearanceSettings" aria-labelledby={headingId}>
    <div className="sectionHeader">
      <div><h2 id={headingId}>{t('appearance.title')}</h2></div>
      <span className="statusBadge neutral"><Current aria-hidden="true" size={14} />{t(theme === 'dark' ? 'appearance.dark' : 'appearance.light')}</span>
    </div>
    <p className="muted">{t('appearance.intro')}</p>
    <fieldset className="languageChoices">
      <legend>{t('appearance.legend')}</legend>
      {options.map((option) => {
        const Icon = ICONS[option.value];
        return <label className="languageChoice themeChoice" key={option.value}>
          <input type="radio" name="interface-theme" value={option.value} checked={choice === option.value} onChange={() => chooseTheme(option.value)} aria-describedby={`${headingId}-${option.value}`} />
          <span className={`themeSwatch ${option.value}`} aria-hidden="true"><Icon /></span>
          <span><strong>{option.label}</strong><small id={`${headingId}-${option.value}`}>{option.hint}</small></span>
        </label>;
      })}
    </fieldset>
    <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{t('appearance.showing', { theme: name(theme) })}</p>
    <p className="muted">{t('appearance.storageNote')}</p>
  </section>;
}
