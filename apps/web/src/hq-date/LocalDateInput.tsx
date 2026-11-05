/**
 * A date or date-and-time field that follows the app's language: `lang` and `dir` come from the app locale, so a browser
 * that can localise its picker does. Most pickers follow the browser's own language instead, so in Arabic the chosen
 * value is also written out in Arabic under the field (lib/localDate.ts). The written value describes the field for
 * screen readers and is hidden from its label, so the field's name stays the label alone.
 */
import { useId, type InputHTMLAttributes } from 'react';
import { directionOf } from '../lib/i18n.ts';
import { localDateText, type DateFieldKind } from '../lib/localDate.ts';
import { useLocale } from '../useLocale';
import './hqDate.css';

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'lang' | 'dir'> & Readonly<{ type: DateFieldKind; value: string }>;

export function LocalDateInput({ type, value, ...rest }: Props) {
  const locale = useLocale();
  const id = useId();
  const shown = locale === 'en' ? '' : localDateText(value, type, locale);
  const shownId = `${id}-shown`;
  const describedBy = [rest['aria-describedby'], shown ? shownId : null].filter(Boolean).join(' ') || undefined;
  return (
    <>
      <input {...rest} type={type} value={value} lang={locale} dir={directionOf(locale)} aria-describedby={describedBy} />
      {shown && <span id={shownId} className="hqDateShown" lang={locale} dir={directionOf(locale)} aria-hidden="true" data-testid="hq-date-shown">{shown}</span>}
    </>
  );
}
