/** The staffing summary of the office a selected room belongs to, in the headquarters room panel
 * (a lazy chunk; docs/ANUM_MINE_OFFICE_STAFFING.md). The changes themselves are made in the command centre. */
import { useEffect, useMemo, useState } from 'react';
import type { StaffingOffice } from '@anum/contracts';
import { apiRequest, currentScope } from '../lib/api';
import { isPermissionDenied } from '../lib/errors';
import { formatNumber, t, type MessageKey } from '../lib/i18n';
import { connectView, coverageRatio, createStaffingClient, localText, rememberOfficesTab } from '../lib/officeStaffing';
import { useLocale } from '../useLocale';
import './officeStaffing.css';

type Load = { state: 'loading' } | { state: 'ready'; office: StaffingOffice } | { state: 'denied' } | { state: 'error' };

export default function OfficeStaffingCard({ buildingId, refreshKey, onNavigate }: {
  buildingId: string | null; refreshKey?: unknown; onNavigate(hash: string): void;
}) {
  const locale = useLocale();
  const client = useMemo(() => createStaffingClient(apiRequest, currentScope), []);
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  useEffect(() => {
    if (!buildingId) return;
    let live = true;
    setLoad({ state: 'loading' });
    client.office(buildingId, locale === 'ar' ? 'ar' : 'en')
      .then((office) => { if (live) setLoad({ state: 'ready', office }); })
      .catch((error: unknown) => { if (live) setLoad(isPermissionDenied(error) ? { state: 'denied' } : { state: 'error' }); });
    return () => { live = false; };
  }, [client, buildingId, locale, refreshKey]);

  if (!buildingId) return <section className="hqPanel osHqCard" aria-label={t('ostaff.card.title')}><p className="muted">{t('ostaff.card.none')}</p></section>;
  return <section className="hqPanel osHqCard" aria-labelledby="os-hq-title">
    <h3 id="os-hq-title">{t('ostaff.card.title')}</h3>
    {load.state === 'loading' && <p className="muted" role="status">{t('ostaff.card.loading')}</p>}
    {load.state === 'denied' && <p className="muted">{t('ostaff.card.denied')}</p>}
    {load.state === 'error' && <p className="muted" role="alert">{t('ostaff.card.error')}</p>}
    {load.state === 'ready' && (() => {
      const o = load.office;
      const missing = o.sources.filter((s) => s.state === 'not_connected');
      return <>
        <p className="osCoverage"><span>{t('ostaff.coverage', { staffed: formatNumber(o.roles_staffed), total: formatNumber(o.roles_total) })}</span>
          <meter min={0} max={100} value={coverageRatio(o.roles_staffed, o.roles_total)} aria-label={t('ostaff.coverage', { staffed: o.roles_staffed, total: o.roles_total })} /></p>
        <ul className="osHqRoles">{o.roles.map((r) => <li key={r.key} data-state={r.state}><span dir="auto">{localText(r.name, locale)}</span>
          <span className={`osPill ${r.state}`}>{t(`ostaff.role.${r.state}` as MessageKey)}</span>
          <span className="muted osSmallText">{t('ostaff.agents', { count: r.agents.length })}</span></li>)}</ul>
        {missing.length > 0 && <ul className="osHqSources">{missing.map((s) => {
          const view = connectView(s);
          const label = localText(s.name, locale);
          return <li key={s.id}><span dir="auto">{label}: {t('ostaff.source.not_connected')}</span>
            {view ? <button type="button" className="secondary osSmall" onClick={() => onNavigate(`#${view}`)}>{t('ostaff.connect', { name: label })}</button>
              : <span className="muted osSmallText">{t('ostaff.connectOperator', { name: label })}</span>}</li>;
        })}</ul>}
        <button type="button" className="secondary" onClick={() => { rememberOfficesTab(); onNavigate('#executive'); }}>{t('ostaff.card.open')}</button>
      </>;
    })()}
  </section>;
}
