// Integrations > Connection setup (docs/integrations-setup.md): every owner-facing integration
// with its live status from the API, what is missing on the server and where it is connected.
// Members see the status; workspace owners and organization administrators also see the setting
// names and callback URLs; only organization administrators run a connection check. The API
// never returns a secret value.
import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { Activity, BookOpen, CircleDot, Copy, ExternalLink, Plug, Search, ShieldAlert } from 'lucide-react';
import { copyText } from './lib/clipboard';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import {
  checkSentence, docUrl, filterSetup, groupName, groupSetup, itemCost, itemName, itemText, setupCounts, statusBadge, statusSentence,
  type IntegrationSetup, type SetupFilter, type SetupItem,
} from './lib/integrationSetup';
import { checkIntegrationSetup, getIntegrationSetup } from './lib/integrationSetupApi';
import { isView, viewLabel, type View } from './lib/shell';
import './connections.css';

function Note({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

type Flash = { id: string; text: string; error?: boolean };

export function ConnectionSetup({ onGo, reloadKey = 0 }: { onGo?: (view: View) => void; reloadKey?: number }) {
  const [setup, setSetup] = useState<IntegrationSetup | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'denied' | 'error'>('loading');
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<SetupFilter>('all');
  const [query, setQuery] = useState('');
  const [checking, setChecking] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const id = useId();
  const load = useCallback(async () => {
    setState((old) => (old === 'ready' ? 'ready' : 'loading'));
    try { setSetup(await getIntegrationSetup()); setState('ready'); }
    catch (e) {
      if (isPermissionDenied(e)) { setState('denied'); return; }
      setError(describeError(e)); setState('error');
    }
  }, []);
  // The page's Refresh reloads this section too (reloadKey changes).
  useEffect(() => { void load(); }, [load, reloadKey]);

  const check = async (item: SetupItem) => {
    setChecking(item.id); setFlash(null);
    try {
      const result = await checkIntegrationSetup(item.id);
      setFlash({ id: item.id, text: `${checkSentence(result, itemName(item.id))} ${formatDateTime(result.checked_at)}`, error: !result.reachable });
    } catch (e) { setFlash({ id: item.id, text: describeError(e), error: true }); }
    finally { setChecking(null); }
  };
  const copy = async (item: SetupItem, value: string) => {
    const ok = await copyText(value);
    setFlash({ id: item.id, text: ok ? t('setup.copied', { value }) : t('setup.copyFailed'), error: !ok });
  };

  const header = <div className="setupHeader">
    <p className="eyebrow">{t('setup.eyebrow')}</p><h2 id={`${id}-title`}>{t('setup.title')}</h2><p className="setupIntro">{t('setup.intro')}</p>
  </div>;

  if (state === 'loading') return <section className="surface setupPanel" aria-labelledby={`${id}-title`} aria-busy="true">{header}<Note>{t('setup.loading')}</Note></section>;
  if (state === 'denied') return <section className="surface setupPanel" aria-labelledby={`${id}-title`}>{header}<p className="setupState"><ShieldAlert aria-hidden="true" />{t('setup.forbidden')}</p></section>;
  if (state === 'error' || !setup) {
    return <section className="surface setupPanel" aria-labelledby={`${id}-title`}>{header}
      <Note error>{t('setup.loadFailed', { error })}</Note>
      <div className="actions"><button type="button" onClick={() => void load()}>{t('setup.tryAgain')}</button></div>
    </section>;
  }

  const counts = setupCounts(setup.items);
  const shown = filterSetup(setup.items, filter, query);
  const groups = groupSetup(shown);
  return <section className="surface setupPanel" aria-labelledby={`${id}-title`}>
    {header}
    {!setup.can_view_setup && <Note>{t('setup.memberNotice')}</Note>}
    {setup.can_view_setup && !setup.can_check && <Note>{t('setup.ownerNotice')}</Note>}
    {setup.items.length === 0
      ? <div className="emptyState"><Plug aria-hidden="true" /><h3>{t('setup.emptyTitle')}</h3><p>{t('setup.emptyText')}</p><button type="button" onClick={() => void load()}>{t('setup.tryAgain')}</button></div>
      : <>
        <p className="catalogSummary" aria-live="polite">{t('setup.summary', counts)}</p>
        <div className="setupToolbar">
          <fieldset className="catalogSegments">
            <legend>{t('setup.filter.label')}</legend>
            {(['all', 'attention', 'connected'] as const).map((value) => <label key={value}><input type="radio" name={`${id}-filter`} value={value} checked={filter === value} onChange={() => setFilter(value)} />{t(`setup.filter.${value}`)}</label>)}
          </fieldset>
          <label className="field setupSearch"><span>{t('setup.search')}</span>
            <span className="setupSearchBox"><Search aria-hidden="true" /><input type="search" value={query} placeholder={t('setup.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} /></span>
          </label>
        </div>
        {shown.length === 0 && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('setup.noMatchTitle')}</h3><p>{t('setup.noMatchText')}</p><button type="button" className="secondary" onClick={() => { setQuery(''); setFilter('all'); }}>{t('setup.clear')}</button></div>}
        {groups.map((group) => <div className="setupGroup" key={group.group} role="group" aria-labelledby={`${id}-${group.group}`}>
          <h3 id={`${id}-${group.group}`}>{groupName(group.group)}</h3>
          <ul className="setupList">{group.items.map((item) => <SetupRow key={item.id} item={item} setup={setup} onGo={onGo}
            checking={checking} flash={flash?.id === item.id ? flash : null} onCheck={() => void check(item)} onCopy={(value) => void copy(item, value)} />)}</ul>
        </div>)}
        <p className="setupFoot">{t('setup.serverOnly')}</p>
      </>}
  </section>;
}

function SetupRow({ item, setup, onGo, checking, flash, onCheck, onCopy }: {
  item: SetupItem; setup: IntegrationSetup; onGo?: (view: View) => void; checking: string | null; flash: Flash | null;
  onCheck: () => void; onCopy: (value: string) => void;
}) {
  const name = itemName(item.id);
  const badge = statusBadge(item.status);
  const view = item.connect_view && isView(item.connect_view) ? item.connect_view : null;
  const details = setup.can_view_setup && (item.missing.length > 0 || item.redirect_urls.length > 0);
  const nameId = `setup-${item.id}`;
  return <li className="setupRow" aria-labelledby={nameId}>
    <div className="setupMain">
      <div className="setupTitle"><h4 id={nameId}>{name}</h4><span className={`statusBadge ${badge.tone}`}>{badge.label}</span></div>
      <p className="setupText">{itemText(item.id)}</p>
      <p className="setupSay">{statusSentence(item, setup.can_view_setup)}</p>
      {itemCost(item.id) && <p className="setupCost"><strong>{t('setup.cost')}:</strong> {itemCost(item.id)}</p>}
    </div>
    <div className="setupActions">
      {view && onGo && <button type="button" className="secondary" onClick={() => onGo(view)}>{t('setup.open', { view: viewLabel(view) })}</button>}
      <a className="setupGuide" href={docUrl(item)} target="_blank" rel="noopener noreferrer" aria-label={t('setup.guideFor', { name })}><BookOpen aria-hidden="true" />{t('setup.guide')}<ExternalLink aria-hidden="true" className="setupExternal" /></a>
      {setup.can_check && item.checkable && <button type="button" className="secondary" disabled={checking !== null} aria-label={t('setup.checkFor', { name })} onClick={onCheck}><Activity aria-hidden="true" />{checking === item.id ? t('setup.checking') : t('setup.check')}</button>}
    </div>
    {details && <div className="setupDetails">
      {item.missing.length > 0 && <div><p className="setupLabel">{t('setup.missing')}</p><ul className="setupCodes">{item.missing.map((name) => <li key={name}><code dir="ltr">{name}</code></li>)}</ul></div>}
      {item.redirect_urls.length > 0 && <div><p className="setupLabel">{t('setup.redirects')}</p><ul className="setupCodes">{item.redirect_urls.map((url) => <li key={url} className="setupUrl"><code dir="ltr">{url}</code><button type="button" className="secondary iconText" onClick={() => onCopy(url)}><Copy aria-hidden="true" />{t('setup.copy')}</button></li>)}</ul></div>}
    </div>}
    {flash && <div className="setupFlash"><Note error={flash.error}>{flash.text}</Note></div>}
  </li>;
}
