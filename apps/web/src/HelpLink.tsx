// Entry points to the in-app help panel (docs/help.md): the "?" in the top bar and the link in a
// page's empty state. Small on purpose: the panel and its articles load only when opened.
import { CircleHelp } from 'lucide-react';
import { articleForView, openHelp } from './lib/help';
import { t } from './lib/i18n';
import { viewFromHash } from './lib/shell';
import './help.css';

/** The "?" button in the top bar. */
export function HelpButton() {
  return <button type="button" className="iconButton" title={t('help.open')} aria-label={t('help.open')} aria-haspopup="dialog" onClick={() => openHelp()}><CircleHelp aria-hidden="true" /></button>;
}

/** A link from a page's empty state to that page's help article (the current page when not given). */
export function HelpLink({ article }: { article?: string }) {
  const open = () => openHelp(article ?? articleForView(viewFromHash(location.hash)));
  return <button type="button" className="linkButton helpLink" onClick={open}><CircleHelp aria-hidden="true" />{t('help.emptyLink')}</button>;
}
