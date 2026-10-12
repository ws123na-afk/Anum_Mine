// Integrations > connection setup details (docs/integrations-setup.md#the-integrations-page): what
// one owner-facing integration does, its live status, what is missing on the server, the callback
// URLs to register and where it is connected. Opened in the drawer from the item's row. Members
// see the status; workspace owners and organization administrators also see the setting names and
// callback URLs; only organization administrators run a connection check. The API never returns
// a secret value.
import type { ReactNode } from 'react';
import { Activity, BookOpen, CircleDot, Copy, ExternalLink } from 'lucide-react';
import { t } from './lib/i18n';
import { docUrl, itemCost, itemName, itemText, statusBadge, statusSentence, type IntegrationSetup, type SetupItem } from './lib/integrationSetup';
import { isView, viewLabel, type View } from './lib/shell';
import './connections.css';

export type SetupFlash = { id: string; text: string; error?: boolean };

export function Note({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

export function SetupDetails({ item, setup, onGo, checking, flash, onCheck, onCopy }: {
  item: SetupItem; setup: IntegrationSetup; onGo?: (view: View) => void; checking: string | null; flash: SetupFlash | null;
  onCheck: () => void; onCopy: (value: string) => void;
}) {
  const name = itemName(item.id);
  const badge = statusBadge(item.status);
  const view = item.connect_view && isView(item.connect_view) ? item.connect_view : null;
  const cost = itemCost(item.id);
  const text = itemText(item.id);
  return <div className="setupDetail" data-setup-detail={item.id}>
    <div className="setupDetailStatus">
      <span className={`statusBadge ${badge.tone}`}>{badge.label}</span>
      <p className="setupSay">{statusSentence(item, setup.can_view_setup)}</p>
    </div>
    {text && <p className="setupText">{text}</p>}
    {cost && <p className="setupCost"><strong>{t('setup.cost')}:</strong> {cost}</p>}
    <div className="setupActions">
      {view && onGo && <button type="button" className={item.status === 'ready' ? 'setupPrimary' : 'secondary'} onClick={() => onGo(view)}>{t('setup.open', { view: viewLabel(view) })}</button>}
      <a className="setupGuide" href={docUrl(item)} target="_blank" rel="noopener noreferrer" aria-label={t('setup.guideFor', { name })}><BookOpen aria-hidden="true" />{t('setup.guide')}<ExternalLink aria-hidden="true" className="setupExternal" /></a>
      {setup.can_check && item.checkable && <button type="button" className="secondary" disabled={checking !== null} aria-label={t('setup.checkFor', { name })} onClick={onCheck}><Activity aria-hidden="true" />{checking === item.id ? t('setup.checking') : t('setup.check')}</button>}
    </div>
    {flash && <div className="setupFlash"><Note error={flash.error}>{flash.text}</Note></div>}
    {setup.can_view_setup && item.missing.length > 0 && <div><p className="setupLabel">{t('setup.missing')}</p><ul className="setupCodes">{item.missing.map((setting) => <li key={setting}><code dir="ltr">{setting}</code></li>)}</ul></div>}
    {setup.can_view_setup && item.redirect_urls.length > 0 && <div><p className="setupLabel">{t('setup.redirects')}</p><ul className="setupCodes">{item.redirect_urls.map((url) => <li key={url} className="setupUrl"><code dir="ltr">{url}</code><button type="button" className="secondary iconText" onClick={() => onCopy(url)}><Copy aria-hidden="true" />{t('setup.copy')}</button></li>)}</ul></div>}
  </div>;
}
