/** "Add a building" typed or spoken in ANUM Command (spec: docs/ANUM_MINE_EXECUTIVE_PA.md#add-a-building).
 * It asks the read-only `propose_add_building` tool, asks one short question when the name or floor count is missing,
 * and shows the proposal card. Creation happens only from the card's Confirm button. */
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Loader2, X } from 'lucide-react';
import type { BuildingProposal } from '@anum/contracts';
import { apiRequest } from '../lib/api';
import { describeError } from '../lib/errors';
import { createPaClient, type InputMode } from '../lib/executivePa';
import { answerQuestion, missingQuestion, proposalArgs, type BuildingAsk } from '../lib/hqBuildingCommand';
import { t } from '../lib/i18n';
import { useLocale } from '../useLocale';
import { BuildingProposalCard } from './BuildingProposalCard';
import '../hq-assign/hqAssign.css';

const pa = createPaClient(apiRequest);
type View = { kind: 'ask'; ask: BuildingAsk } | { kind: 'loading' } | { kind: 'proposal'; proposal: BuildingProposal } | { kind: 'message'; text: string; tone: 'warn' | 'error' };

export function BuildingCommandPanel({ initial, mode, onClose, onShow }: { initial: BuildingAsk; mode: InputMode; onClose: () => void; onShow: () => void }) {
  const locale = useLocale();
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [reply, setReply] = useState('');
  const field = useId();
  const input = useRef<HTMLInputElement>(null);
  const run = useCallback(async (ask: BuildingAsk) => {
    const missing = missingQuestion(ask);
    if (missing) { setView({ kind: 'ask', ask }); return; }
    setView({ kind: 'loading' });
    try {
      const r = await pa.invoke('propose_add_building', proposalArgs(ask, locale), mode === 'voice' ? 'voice' : 'typed');
      if (r.status === 'proposal' && r.content && r.payload_hash) {
        setView({ kind: 'proposal', proposal: { version: 1, content: r.content, payload_hash: r.payload_hash, bounds: r.bounds ?? { width_m: 60, depth_m: 40 },
          creates: r.creates ?? r.content.totals, can_confirm: r.can_confirm === true, governed_by: r.governed_by ?? '', note: r.note ?? '' } });
      } else if (r.status === 'needs_info') setView({ kind: 'ask', ask });
      else if (r.status === 'invalid') setView({ kind: 'message', tone: 'warn', text: t('hqBuild.invalid', { detail: r.note ?? '' }) });
      else if (r.status === 'not_connected') setView({ kind: 'message', tone: 'warn', text: t('hqBuild.notConnected') });
      else if (r.status === 'denied') setView({ kind: 'message', tone: 'warn', text: t('hqBuild.denied') });
      else setView({ kind: 'message', tone: 'error', text: t('hqBuild.invalid', { detail: r.note ?? '' }) });
    } catch (e) { setView({ kind: 'message', tone: 'error', text: t('hqBuild.invalid', { detail: describeError(e) }) }); }
  }, [locale, mode]);
  useEffect(() => { void run(initial); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (view.kind === 'ask') input.current?.focus(); }, [view.kind]);

  const answer = (event: FormEvent) => {
    event.preventDefault();
    if (view.kind !== 'ask' || !reply.trim()) return;
    const next = answerQuestion(view.ask, reply); setReply('');
    void run(next);
  };

  return <section className="surface hqAsCommand" aria-label={t('hqBuild.title')}>
    <div className="hqAsLabelRow"><h2>{t('hqBuild.title')}</h2><button type="button" className="hqAsX" onClick={onClose} aria-label={t('hqAssign.close')}><X size={16} aria-hidden="true" /></button></div>
    {view.kind === 'loading' && <p className="hqAsNote" role="status"><Loader2 size={14} className="hqAsSpin" aria-hidden="true" /> {t('hqAssign.sending')}</p>}
    {view.kind === 'ask' && <form onSubmit={answer} className="hqAsField">
      <label htmlFor={field} className="hqAsLabel" aria-live="polite">{view.ask.name === null ? t('hqBuild.askName') : t('hqBuild.askFloors', { name: view.ask.name })}</label>
      <input id={field} ref={input} value={reply} onChange={(e) => setReply(e.target.value)} dir="auto" autoComplete="off" />
      <div className="hqAsFoot"><button type="submit" disabled={!reply.trim()}>{t('pa.send')}</button><button type="button" onClick={onClose}>{t('hqBuild.cancel')}</button></div>
    </form>}
    {view.kind === 'message' && <p className={view.tone === 'error' ? 'hqAsErr' : 'hqAsNote'} role="alert">{view.text}</p>}
    {view.kind === 'proposal' && <BuildingProposalCard proposal={view.proposal} inputMode={mode === 'voice' ? 'voice' : 'typed'} onShow={() => onShow()}
      onReview={() => void run({ name: view.proposal.content.building.name, floors: view.proposal.content.floors.length })} />}
  </section>;
}
