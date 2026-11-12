// An insurance or registration document and its vehicle (docs/documents.md#vehicle-papers). The vault proposes the
// vehicles the person may change (the registration named in the document first); linking one sets that vehicle's
// expiry from the document, so the vehicle's renewal reminder follows it. Nothing is linked until the person picks.
import { useCallback, useEffect, useState } from 'react';
import { Car, Check, Lock } from 'lucide-react';
import type { VaultVehicleProposal, VaultVehicleProposals } from '@anum/contracts';
import { LoadingSkeleton, Notice, PaneProblem } from './PageParts';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { type Load } from './lib/pageSummary';
import type { VaultDocumentDetail } from './lib/vault';
import * as api from './lib/vaultApi';

export const VEHICLE_PAPER_CATEGORIES = ['insurance', 'vehicle_registration'];
const day = (value: string) => formatDateTime(`${value}T00:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' });

export function VehiclePapers({ doc, onChanged }: { doc: VaultDocumentDetail; onChanged(text?: string): void }) {
  const [load, setLoad] = useState<Load<VaultVehicleProposals>>({ state: 'loading' });
  const [busy, setBusy] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const fetch = useCallback(() => api.getVehicleProposals(doc.id).then((value) => setLoad({ state: 'ready', value }),
    (error) => setLoad(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) })),
  [doc.id]);
  useEffect(() => { void fetch(); }, [fetch, doc.row_version]);
  const heading = <h3 id="vault-vehicle"><Car aria-hidden="true" />{t('vault.vp.title')}</h3>;
  if (load.state === 'loading') return <section className="vaultSub" aria-labelledby="vault-vehicle">{heading}<LoadingSkeleton label={t('vault.vp.loading')} rows={2} /></section>;
  if (load.state !== 'ready') return <section className="vaultSub" aria-labelledby="vault-vehicle">{heading}
    <PaneProblem text={t('vault.vp.failed', { message: load.message })} onRetry={() => void fetch()} /></section>;
  const p = load.value;
  if (p.reason === 'not_a_vehicle_paper') return null;
  const link = async (item: VaultVehicleProposal) => {
    setBusy(item.vehicle_id); setProblem(null);
    try {
      const value = await api.confirmVehicle(doc.id, item.vehicle_id);
      setLoad({ state: 'ready', value });
      onChanged(t('vault.vp.saved', { day: value.reminder_from ? day(value.reminder_from) : '-' }));
    } catch (error) { setProblem(describeError(error)); void fetch(); } finally { setBusy(null); }
  };
  const field = p.field ? t(`vault.vp.field.${p.field}` as MessageKey) : '';
  return <section className="vaultSub" aria-labelledby="vault-vehicle">{heading}
    {p.reason === 'no_expiry' ? <p className="muted">{t('vault.vp.noExpiry')}</p>
      : p.reason === 'in_trash' ? <p className="muted">{t('vault.vp.inTrash')}</p>
        : p.vehicles_unavailable ? <p className="muted">{t('vault.vp.noAccess')}</p> : <>
          <p className="muted">{t('vault.vp.intro', { field, date: p.expiry_date ? day(p.expiry_date) : '-' })}</p>
          {p.reminder_from && p.reminder_days && <p className="muted">{t('vault.vp.reminder', { days: p.reminder_days, day: day(p.reminder_from) })}</p>}
          {p.vehicles.length === 0 ? <p className="muted">{t('vault.vp.none')}</p>
            : <ul className="vaultMatches">{p.vehicles.map((item) => <li key={item.vehicle_id} className="vaultMatch">
              <span className="vaultMatchText"><bdi dir="auto">{item.label}</bdi>
                <small className="muted">{item.current_expiry ? t('vault.vp.current', { date: day(item.current_expiry) }) : t('vault.vp.currentNone')}</small></span>
              <span className="vaultMatchActions">
                {item.matches_registration && <span className="vaultBadge ok">{t('vault.vp.matches')}</span>}
                {item.linked && !item.would_change ? <span className="vaultBadge ok"><Check aria-hidden="true" />{t('vault.vp.linked')}</span>
                  : p.can_edit && <button type="button" className="secondary" disabled={busy !== null}
                    aria-label={t('vault.vp.linkLabel', { label: item.label, date: p.expiry_date ? day(p.expiry_date) : '-' })} onClick={() => void link(item)}>
                    <Check aria-hidden="true" />{busy === item.vehicle_id ? t('vault.saving') : t('vault.vp.link')}</button>}
              </span>
            </li>)}</ul>}
          {!p.can_edit && <p className="muted vaultReadOnly"><Lock aria-hidden="true" />{t('vault.vp.readOnly')}</p>}
        </>}
    {problem && <Notice error>{problem}</Notice>}
  </section>;
}
