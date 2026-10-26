/** The assistant's campus proposal (spec: docs/ANUM_MINE_STARTER_CAMPUS.md#first-run): the plan from the API, what it creates, the plan hash,
 * and one button. Creating happens only from that button, with `input_mode: "click"`, whatever way the request was made. */
import { useState } from 'react';
import { Building2, CheckCircle2, Loader2, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { StarterCampusResult } from '@anum/contracts';
import { formatNumber, t } from '../lib/i18n';
import { canCreate, createCounts, useCampusSetup } from './useCampusSetup';
import { shortHash } from './FirstRunPanel';
import './hqFirstRun.css';

export interface CampusProposalCardProps {
  /** How the request was made. A spoken request still needs this click. */
  inputMode: 'typed' | 'voice';
  onCreated?: (result: StarterCampusResult) => void;
  /** Open the office to look at the campus. */
  onShow?: () => void;
  onCancel?: () => void;
}

export function CampusProposalCard({ inputMode, onCreated, onShow, onCancel }: CampusProposalCardProps) {
  const [cancelled, setCancelled] = useState(false);
  const setup = useCampusSetup((result) => onCreated?.(result));
  const { phase, preview, result } = setup;
  const counts = createCounts(preview);
  const busy = phase === 'creating';
  const needsReview = phase === 'partial' || phase === 'failed';
  if (cancelled) return <section className="hqFrCmd" aria-label={t('hqFr.cmd.title')}><p className="hqFrNote">{t('hqFr.cmd.cancelled')}</p></section>;
  return (
    <section className="hqFrCmd" aria-label={t('hqFr.cmd.title')} aria-busy={busy || phase === 'loading'} data-phase={phase} data-testid="campus-proposal">
      <h3><Building2 size={15} aria-hidden="true" /> {t('hqFr.cmd.title')}</h3>
      {phase === 'done' && result ? (
        <>
          <p className="hqFrNote" role="status"><CheckCircle2 size={14} aria-hidden="true" /> {result.outcome === 'already_set_up' ? t('hqFr.already') : t('hqFr.cmd.done', { ...result.totals })}</p>
          {onShow && <div className="hqFrActions"><button type="button" className="hqFrPrimary" onClick={onShow}>{t('hqFr.cmd.show')}</button></div>}
        </>
      ) : (
        <>
          <p className="hqFrNote">{t('hqFr.cmd.intro')}</p>
          {phase === 'loading' && <p className="hqFrNote" role="status"><Loader2 size={14} className="hqFrSpin" aria-hidden="true" /> {t('hqFr.loading')}</p>}
          {phase === 'denied' && <p className="hqFrNote hqFrFail" role="alert">{t('hqFr.denied')}</p>}
          {phase === 'error' && <div className="hqFrNote hqFrFail" role="alert"><p>{t('hqFr.error', { detail: setup.detail ?? '' })}</p><button type="button" onClick={setup.reload}>{t('hqFr.retry')}</button></div>}
          {counts && <p className="hqFrNote"><strong>{t('hqFr.cmd.counts', { ...counts })}</strong></p>}
          {setup.stale && phase === 'ready' && <p className="hqFrNote hqFrWarn" role="alert"><TriangleAlert size={14} aria-hidden="true" /> {t('hqFr.stale')}</p>}
          {setup.detail && phase === 'ready' && !setup.stale && <p className="hqFrNote hqFrFail" role="alert">{t('hqFr.failed', { detail: setup.detail })}</p>}
          {phase === 'failed' && <p className="hqFrNote hqFrFail" role="alert">{t('hqFr.failedAll')}</p>}
          {phase === 'partial' && result && <p className="hqFrNote hqFrWarn" role="alert">{t('hqFr.partial', { created: formatNumber(result.buildings.filter((b) => b.status === 'created').length), total: formatNumber(result.buildings.length) })}</p>}
          {preview && phase !== 'loading' && (
            <>
              {inputMode === 'voice' && <p className="hqFrNote">{t('hqFr.cmd.voice')}</p>}
              <p className="hqFrNote"><ShieldCheck size={13} aria-hidden="true" /> {preview.can_confirm ? t('hqFr.clickOnly') : t('hqFr.ownersOnly')}</p>
              <p className="hqFrNote hqFrHash">{t('hqFr.hash', { hash: shortHash(preview.payload_hash) })}</p>
              <div className="hqFrActions">
                <button type="button" className="hqFrPrimary" onClick={needsReview ? setup.reload : setup.create} disabled={needsReview ? false : !canCreate(setup)} data-testid="campus-proposal-create">
                  {busy ? t('hqFr.creating') : needsReview ? t('hqFr.reviewLeft') : t('hqFr.create')}
                </button>
                {setup.stale && phase === 'ready' && <button type="button" onClick={setup.reload}>{t('hqFr.review')}</button>}
                {!busy && <button type="button" onClick={() => { setCancelled(true); onCancel?.(); }}>{t('hqFr.cmd.cancel')}</button>}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

export default CampusProposalCard;
