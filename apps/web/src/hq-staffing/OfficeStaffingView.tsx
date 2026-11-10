/** Offices and agents: the command centre tab (docs/ANUM_MINE_OFFICE_STAFFING.md).
 * Every office of the workspace with its blueprint coverage, agents and data sources; a drawer per office
 * shows each role and lets a workspace owner apply the missing agents, skills, tools and rules. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Building2, Link2Off, LockKeyhole, Search, Users } from 'lucide-react';
import type { StaffingOfficePage, StaffingOfficeSummary } from '@anum/contracts';
import { apiRequest, currentScope } from '../lib/api';
import { ApiError, describeError, isPermissionDenied } from '../lib/errors';
import { formatDateTime, formatNumber, t } from '../lib/i18n';
import { clampOffset, coverageRatio, createStaffingClient, localText, PAGE_SIZE } from '../lib/officeStaffing';
import { useLocale } from '../useLocale';
import { LoadingSkeleton } from '../PageParts';
import { OfficeDetail } from './OfficeDetail';
import { openInOffice } from './openInOffice';
import './officeStaffing.css';

type Load = { state: 'loading' } | { state: 'ready'; page: StaffingOfficePage } | { state: 'denied' } | { state: 'notConnected' } | { state: 'error'; message: string };

export default function OfficeStaffingView({ onGo }: { onGo(view: string): void }) {
  const locale = useLocale();
  const client = useMemo(() => createStaffingClient(apiRequest, currentScope), []);
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [offset, setOffset] = useState(0);
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [open, setOpen] = useState<StaffingOfficeSummary | null>(null);

  // Back to page 1 only when the search text really changed: an unchanged query must not undo a page the person just picked.
  useEffect(() => {
    if (query === debounced) return undefined;
    const id = window.setTimeout(() => { setDebounced(query); setOffset(0); }, 250);
    return () => window.clearTimeout(id);
  }, [query, debounced]);

  const refresh = useCallback(async () => {
    setLoad((old) => (old.state === 'ready' ? old : { state: 'loading' }));
    try {
      const page = await client.list(debounced, offset);
      const fixed = clampOffset(offset, page.total);
      if (fixed !== offset) { setOffset(fixed); return; }
      setLoad({ state: 'ready', page });
    } catch (error) {
      if (isPermissionDenied(error)) setLoad({ state: 'denied' });
      else if (error instanceof ApiError && error.status === 503) setLoad({ state: 'notConnected' });
      else setLoad({ state: 'error', message: t('ostaff.error', { detail: describeError(error) }) });
    }
  }, [client, debounced, offset]);
  useEffect(() => { void refresh(); }, [refresh]);

  const page = load.state === 'ready' ? load.page : null;
  const pages = page ? Math.max(1, Math.ceil(page.total / PAGE_SIZE)) : 1;
  const current = page ? Math.floor(page.offset / PAGE_SIZE) + 1 : 1;

  return <section className="osView" aria-labelledby="ostaff-title">
    <div className="osHead">
      <div>
        <h2 id="ostaff-title">{t('ostaff.title')}</h2>
        <p className="muted">{t('ostaff.intro')}</p>
      </div>
      <label className="osSearch">
        <span className="visuallyHidden">{t('ostaff.search')}</span>
        <Search aria-hidden="true" />
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('ostaff.searchPlaceholder')} aria-label={t('ostaff.search')} maxLength={100} />
      </label>
    </div>
    {load.state === 'loading' && <LoadingSkeleton label={t('ostaff.loading')} rows={3} />}
    {load.state === 'error' && <div className="notice errorNotice" role="alert"><span>{load.message}</span><button type="button" className="secondary" onClick={() => void refresh()}>{t('ostaff.retry')}</button></div>}
    {load.state === 'denied' && <div className="exState"><LockKeyhole aria-hidden="true" /><p>{t('ostaff.denied')}</p></div>}
    {load.state === 'notConnected' && <div className="exState"><Link2Off aria-hidden="true" /><p>{t('ostaff.notConnected')}</p></div>}
    {page && page.buildings_total === 0 && <div className="exState"><Building2 aria-hidden="true" /><p>{t('ostaff.empty')}</p>
      <button type="button" onClick={() => onGo('office')}>{t('ostaff.emptyAction')}</button></div>}
    {page && page.buildings_total > 0 && page.total === 0 && <div className="exState"><Search aria-hidden="true" /><p>{t('ostaff.noMatch', { q: debounced })}</p>
      <button type="button" className="secondary" onClick={() => setQuery('')}>{t('ostaff.clearSearch')}</button></div>}
    {page && page.items.length > 0 && <>
      <p className="muted osMeta" role="status">{t('ostaff.count', { count: page.total })} | {t('ostaff.asOf', { time: formatDateTime(page.as_of) })}</p>
      <ul className="osCards">{page.items.map((o) => <OfficeCard key={o.building_id} office={o} locale={locale} onOpen={() => setOpen(o)} onGo={onGo} />)}</ul>
      {pages > 1 && <nav className="osPager" aria-label={t('ostaff.page', { page: current, pages })}>
        <button type="button" className="secondary" disabled={current <= 1} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{t('ostaff.prev')}</button>
        <span>{t('ostaff.page', { page: current, pages })}</span>
        <button type="button" className="secondary" disabled={current >= pages} onClick={() => setOffset(offset + PAGE_SIZE)}>{t('ostaff.next')}</button>
      </nav>}
    </>}
    {open && <OfficeDetail client={client} summary={open} onClose={() => setOpen(null)} onChanged={() => void refresh()} onGo={onGo} />}
  </section>;
}

function OfficeCard({ office, locale, onOpen, onGo }: { office: StaffingOfficeSummary; locale: string; onOpen(): void; onGo(view: string): void }) {
  const name = localText(office.name, locale);
  const pct = coverageRatio(office.roles_staffed, office.roles_total);
  return <li className="exCard osCard" data-building={office.building_id}>
    <div className="exCardHead"><h3 dir="auto">{name}</h3><span className="exBadge">{localText(office.kind_name, locale)}</span></div>
    <div className="osCoverage">
      <span>{t('ostaff.coverage', { staffed: formatNumber(office.roles_staffed), total: formatNumber(office.roles_total) })}</span>
      <meter min={0} max={100} value={pct} aria-label={t('ostaff.coverage', { staffed: office.roles_staffed, total: office.roles_total })} />
    </div>
    <p className="osLine"><Users aria-hidden="true" />{t('ostaff.agents', { count: office.agents_total })}{office.agents_total > 0 ? ` | ${t('ostaff.activeAgents', { count: formatNumber(office.agents_active) })}` : ''}</p>
    {office.agents_needing_update > 0 && <p className="osLine osWarn">{t('ostaff.needUpdate', { count: office.agents_needing_update })}</p>}
    <p className={`osLine ${office.sources_not_connected ? 'osWarn' : ''}`}>{office.sources_not_connected ? t('ostaff.sourcesMissing', { count: office.sources_not_connected }) : t('ostaff.allSourcesOk')}</p>
    <div className="exActions">
      <button type="button" onClick={onOpen}>{t('ostaff.open', { name })}</button>
      <button type="button" className="secondary" onClick={() => openInOffice(office.name.en, onGo)}>{t('ostaff.open3d')}</button>
    </div>
  </li>;
}
