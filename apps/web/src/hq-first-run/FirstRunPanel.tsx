/** First-run panel of the headquarters scene (spec: docs/ANUM_MINE_STARTER_CAMPUS.md#first-run).
 * A workspace with no buildings gets this inside the scene: the real plan from the API (buildings, floors, rooms, desks), a list to
 * preview, and ONE primary button that confirms exactly the plan on screen with a click. Typed or spoken words cannot confirm; the
 * button is disabled for anyone without workspace:manage and says who can. Loaded lazily, so it adds nothing to the scene's own chunk. */
import { useId, useState } from 'react';
import { Armchair, Building2, CheckCircle2, DoorOpen, Layers, Loader2, LockKeyhole, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { StarterBuilding, StarterCampusResult } from '@anum/contracts';
import { formatNumber, t } from '../lib/i18n';
import { useLocale } from '../useLocale';
import { canCreate, createCounts, useCampusSetup } from './useCampusSetup';
import './hqFirstRun.css';

export interface FirstRunPanelProps {
  /** The server wrote buildings (all or some): the page reads the office again and lands the camera on the campus. */
  onCreated: (result: StarterCampusResult) => void;
  /** A partial or failed run keeps the panel open; the page must not close it just because some buildings now exist. */
  onKeep: (keep: boolean) => void;
  /** Leave the panel (after a finished run). */
  onClose: () => void;
  /** The by-hand way: the office builder (Manage spaces). */
  onManageSpaces?: () => void;
}

const nameOf = (b: StarterBuilding, locale: 'en' | 'ar') => (locale === 'ar' ? b.name.ar : b.name.en);

/** Short, readable form of the plan hash: the person sees the same characters the server checks. */
export const shortHash = (hash: string) => `${hash.slice(0, 8)} ${hash.slice(8, 16)}`;

export function FirstRunPanel({ onCreated, onKeep, onClose, onManageSpaces }: FirstRunPanelProps) {
  const locale = useLocale();
  const titleId = useId();
  const listId = useId();
  const [open, setOpen] = useState(false);
  const setup = useCampusSetup((result) => {
    onCreated(result);
    onKeep(result.outcome === 'partial');
  });
  const { phase, preview, result } = setup;
  const counts = createCounts(preview);
  const busy = phase === 'creating';
  const allThere = phase === 'ready' && preview !== null && counts !== null && !canCreate(setup) && preview.can_confirm && !Object.values(counts).some((n) => n > 0);
  const failedItems = result ? result.buildings.filter((b) => b.status === 'failed' || b.status === 'changed') : [];
  const someExist = preview !== null && preview.buildings.some((b) => b.status !== 'new');
  const needsReview = phase === 'partial' || phase === 'failed';
  const label = someExist ? t('hqFr.createMissing') : t('hqFr.create');

  return (
    <section className="hqFirstRun" aria-labelledby={titleId} aria-busy={busy || phase === 'loading'} data-phase={phase} data-testid="hq-first-run">
      <div className="hqFrCard">
        <header>
          <span className="hqFrIcon" aria-hidden="true"><Building2 /></span>
          <div>
            <h2 id={titleId}>{phase === 'done' ? t('hqFr.doneTitle') : t('hqFr.title')}</h2>
            <p className="hqFrLead">
              {phase === 'done' ? t('hqFr.done', { ...(result?.totals ?? { buildings: 0, floors: 0, rooms: 0, desks: 0 }) })
                : counts ? t('hqFr.lead', { ...counts }) : t('hqFr.leadPlain')}
            </p>
          </div>
        </header>

        {phase === 'loading' && <p className="hqFrNote" role="status"><Loader2 aria-hidden="true" className="hqFrSpin" /> {t('hqFr.loading')}</p>}
        {phase === 'denied' && <p className="hqFrNote hqFrFail" role="alert"><LockKeyhole aria-hidden="true" /> {t('hqFr.denied')}</p>}
        {phase === 'error' && (
          <div className="hqFrNote hqFrFail" role="alert">
            <p><TriangleAlert aria-hidden="true" /> {t('hqFr.error', { detail: setup.detail ?? '' })}</p>
            <button type="button" onClick={setup.reload}>{t('hqFr.retry')}</button>
          </div>
        )}

        {counts && phase !== 'done' && (
          <ul className="hqFrStats" aria-label={t('hqFr.statsLabel')}>
            <li><Building2 aria-hidden="true" /><strong data-testid="fr-buildings">{formatNumber(counts.buildings)}</strong><span>{t('hqFr.stat.buildings')}</span></li>
            <li><Layers aria-hidden="true" /><strong data-testid="fr-floors">{formatNumber(counts.floors)}</strong><span>{t('hqFr.stat.floors')}</span></li>
            <li><DoorOpen aria-hidden="true" /><strong data-testid="fr-rooms">{formatNumber(counts.rooms)}</strong><span>{t('hqFr.stat.rooms')}</span></li>
            <li><Armchair aria-hidden="true" /><strong data-testid="fr-desks">{formatNumber(counts.desks)}</strong><span>{t('hqFr.stat.desks')}</span></li>
          </ul>
        )}

        {setup.stale && phase === 'ready' && <p className="hqFrNote hqFrWarn" role="alert"><TriangleAlert aria-hidden="true" /> {t('hqFr.stale')}</p>}
        {setup.detail && phase === 'ready' && !setup.stale && <p className="hqFrNote hqFrFail" role="alert"><TriangleAlert aria-hidden="true" /> {t('hqFr.failed', { detail: setup.detail })}</p>}
        {phase === 'failed' && <p className="hqFrNote hqFrFail" role="alert"><TriangleAlert aria-hidden="true" /> {t('hqFr.failedAll')}</p>}
        {phase === 'partial' && result && (
          <div className="hqFrNote hqFrWarn" role="alert">
            <p><TriangleAlert aria-hidden="true" /> {t('hqFr.partial', { created: result.buildings.filter((b) => b.status === 'created').length, total: result.buildings.length })}</p>
          </div>
        )}
        {(phase === 'partial' || phase === 'failed') && failedItems.length > 0 && (
          <ul className="hqFrFailed" aria-label={t('hqFr.failedList')}>
            {failedItems.map((b) => <li key={b.key}><strong dir="auto">{locale === 'ar' ? b.name.ar : b.name.en}</strong><span>{b.message}</span></li>)}
          </ul>
        )}
        {allThere && <p className="hqFrNote" role="status"><CheckCircle2 aria-hidden="true" /> {t('hqFr.already')}</p>}

        {preview && phase !== 'done' && (
          <div className="hqFrPreview">
            <button type="button" className="hqFrLink" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((v) => !v)}>{open ? t('hqFr.previewHide') : t('hqFr.preview')}</button>
            {open && (
              <ul id={listId} className="hqFrList" aria-label={t('hqFr.preview')}>
                {preview.buildings.map((b) => (
                  <li key={b.key} data-status={b.status}>
                    <strong dir="auto">{nameOf(b, locale)}</strong>
                    <span>{b.status === 'complete' ? t('hqSc.row.nothing') : t('hqFr.building', { floors: b.counts.create.floors, rooms: b.counts.create.rooms, desks: b.counts.create.desks })}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {phase !== 'done' && phase !== 'loading' && phase !== 'denied' && phase !== 'error' && preview && (
          <footer>
            <div className="hqFrActions">
              <button type="button" className="hqFrPrimary" data-autofocus onClick={needsReview ? setup.reload : setup.create} disabled={needsReview ? false : !canCreate(setup)} data-testid="hq-first-run-create">
                {busy ? <><Loader2 aria-hidden="true" className="hqFrSpin" /> {t('hqFr.creating')}</> : needsReview ? t('hqFr.reviewLeft') : label}
              </button>
              {setup.stale && phase === 'ready' && <button type="button" onClick={setup.reload}>{t('hqFr.review')}</button>}
              {needsReview && <button type="button" onClick={onClose}>{t('hqFr.note.close')}</button>}
            </div>
            <p className="hqFrNote">{preview.can_confirm ? t('hqFr.clickOnly') : t('hqFr.ownersOnly')} {t('hqFr.rules')}</p>
            <p className="hqFrNote hqFrHash" data-testid="fr-hash"><ShieldCheck aria-hidden="true" /> {t('hqFr.hash', { hash: shortHash(preview.payload_hash) })}</p>
          </footer>
        )}
        {phase === 'done' && (
          <footer>
            <div className="hqFrActions"><button type="button" className="hqFrPrimary" data-autofocus onClick={onClose}>{t('hqFr.land')}</button></div>
          </footer>
        )}
        {onManageSpaces && phase !== 'done' && !busy && (
          <p className="hqFrManual"><button type="button" className="hqFrLink" onClick={onManageSpaces}>{t('hq.manageSpaces')}</button><span>{t('hqFr.manual')}</span></p>
        )}
      </div>
    </section>
  );
}

export default FirstRunPanel;
