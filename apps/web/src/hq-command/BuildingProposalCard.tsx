/** The proposal card in the assistant conversation (spec: docs/ANUM_MINE_EXECUTIVE_PA.md#add-a-building).
 * It shows exactly what will be created and offers Confirm and Cancel. Confirm is a button: the only path to creation. */
import { useRef, useState } from 'react';
import { Building2, CheckCircle2, ShieldCheck } from 'lucide-react';
import type { BuildingConfirmResult, BuildingProposal } from '@anum/contracts';
import * as api from '../lib/api';
import { ApiError, describeError } from '../lib/errors';
import { createBuildingClient } from '../lib/hqBuildingCommand';
import { newAttemptKey } from '../lib/hqAssign';
import { formatNumber, t } from '../lib/i18n';
import '../hq-assign/hqAssign.css';

const client = createBuildingClient(api.apiRequest);

export interface BuildingProposalCardProps {
  proposal: BuildingProposal;
  /** How the request was made. A spoken request still needs this click. */
  inputMode: 'typed' | 'voice';
  onCreated?: (result: BuildingConfirmResult) => void;
  onShow?: (buildingId: string) => void;
  /** Ask again with the same words, after a stale answer. */
  onReview?: () => void;
}

export function BuildingProposalCard({ proposal, inputMode, onCreated, onShow, onReview }: BuildingProposalCardProps) {
  const [phase, setPhase] = useState<'idle' | 'busy' | 'done' | 'cancelled'>('idle');
  const [result, setResult] = useState<BuildingConfirmResult | null>(null);
  const [failure, setFailure] = useState<{ detail: string; stale: boolean } | null>(null);
  const key = useRef(newAttemptKey());
  const { content } = proposal;
  const name = content.building.name;

  const confirm = async () => {
    if (phase !== 'idle' || !proposal.can_confirm) return;
    setPhase('busy'); setFailure(null);
    try {
      const done = await client.confirm(proposal, key.current);
      setResult(done); setPhase('done'); onCreated?.(done);
    } catch (e) {
      setFailure({ detail: describeError(e), stale: e instanceof ApiError && e.status === 409 });
      setPhase('idle');
    }
  };

  return <section className="hqAsCard" aria-label={t('hqBuild.card')} data-phase={phase}>
    <h3><Building2 size={15} aria-hidden="true" /> <span dir="auto">{t('hqBuild.proposalFor', { name })}</span></h3>
    {phase !== 'done' && phase !== 'cancelled' && <>
      <p className="hqAsNote">{t('hqBuild.intro')}</p>
      <h4>{t('hqBuild.creates')}</h4>
      <ul className="hqAsCardList">
        <li dir="auto">{t('hqBuild.createsBuilding', { name, zone: content.building.timezone })}</li>
        <li>{t('hqBuild.createsFloors', { count: formatNumber(content.floors.length), width: formatNumber(proposal.bounds.width_m), depth: formatNumber(proposal.bounds.depth_m) })}</li>
        <li>{t('hqBuild.createsRooms', { count: formatNumber(proposal.creates.rooms) })}</li>
      </ul>
      <ul className="hqAsCardList" aria-label={t('hqBuild.creates')}>
        {content.floors.map((f) => <li key={f.id} dir="auto">{t('hqBuild.floorLine', { floor: f.name, rooms: f.rooms.map((r) => (content.lang === 'ar' ? r.name_ar : r.name)).join(', ') })}</li>)}
      </ul>
      <p className="hqAsNote">{t('hqBuild.noDesks')}</p>
      {inputMode === 'voice' && <p className="hqAsNote">{t('hqBuild.voiceNote')}</p>}
      <p className="hqAsNote"><ShieldCheck size={13} aria-hidden="true" /> {proposal.can_confirm ? t('hqBuild.clickOnly') : t('hqBuild.owners')}</p>
      {failure && <div className="hqAsBox hqAsFail" role="alert">
        <p>{failure.stale ? t('hqBuild.stale') : t('hqBuild.failed', { detail: failure.detail })}</p>
        {failure.stale && onReview && <button type="button" onClick={onReview}>{t('hqBuild.review')}</button>}
      </div>}
      <div className="hqAsFoot">
        <button type="button" className="hqAsPrimary" onClick={() => void confirm()} disabled={phase === 'busy' || !proposal.can_confirm}>{phase === 'busy' ? t('hqBuild.working') : t('hqBuild.confirm')}</button>
        <button type="button" onClick={() => setPhase('cancelled')} disabled={phase === 'busy'}>{t('hqBuild.cancel')}</button>
      </div>
    </>}
    {phase === 'cancelled' && <p role="status">{t('hqBuild.cancelled')}</p>}
    {phase === 'done' && result && <div role="status">
      <p><CheckCircle2 size={14} aria-hidden="true" /> {result.replayed ? t('hqBuild.doneReplayed', { name: result.building.name })
        : t('hqBuild.done', { name: result.building.name, floors: formatNumber(content.floors.length), rooms: formatNumber(proposal.creates.rooms) })}</p>
      {onShow && <button type="button" onClick={() => onShow(result.building.id)}>{t('hqBuild.open')}</button>}
    </div>}
  </section>;
}
