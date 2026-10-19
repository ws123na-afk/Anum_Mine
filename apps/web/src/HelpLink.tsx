// Entry point to the in-app help panel (docs/help.md): the "?" in the top bar. Small on purpose: the panel and its articles load only when opened.
import { CircleHelp } from 'lucide-react';
import { openHelp } from './lib/help';
import { t } from './lib/i18n';
import './help.css';

/** The "?" button in the top bar. */
export function HelpButton() {
  return <button type="button" className="iconButton" title={t('help.open')} aria-label={t('help.open')} aria-haspopup="dialog" onClick={() => openHelp()}><CircleHelp aria-hidden="true" /></button>;
}
