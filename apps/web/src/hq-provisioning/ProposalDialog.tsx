import { useCallback, useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckCircle2, Circle, Loader2, ShieldCheck, X } from 'lucide-react';
import type { ProvisioningMissing, ProvisioningProposal, ProvisioningResult, ProvisioningTemplateId } from '@anum/contracts';
import * as api from '../lib/api';
import { ApiError, describeError } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { useModal } from '../Dialog';
import { useLocale } from '../useLocale';
import { canConfirm } from './client';
import { provisioningClient } from './useProvisioning';
import { reasonLine, roleName } from './OrgGapsPanel';

type Load = { kind: 'loading' } | { kind: 'error'; detail: string; stale: boolean } | { kind: 'ready'; proposal: ProvisioningProposal };
type Done = { result: ProvisioningResult; paused: boolean; turnOnError: string };

export interface ProposalDialogProps {
  templateId: ProvisioningTemplateId;
  onClose: () => void;
  onNavigate: (hash: string) => void;
  onProvisioned: () => void;
}

const levelText = (level: string) => t(`hqProv.dlg.level.${level === 'auto' || level === 'ask' ? level : 'off'}` as MessageKey);

export function ProposalDialog({ templateId, onClose, onNavigate, onProvisioned }: ProposalDialogProps) {
  const locale = useLocale();
  const panel = useModal(onClose);
  const titleId = useId();
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [roomId, setRoomId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ detail: string; stale: boolean } | null>(null);
  const [done, setDone] = useState<Done | null>(null);

  const read = useCallback(async (room: string | null) => {
    setLoad({ kind: 'loading' });
    setFailure(null);
    try {
      setLoad({ kind: 'ready', proposal: await provisioningClient.proposal(templateId, locale, room) });
    } catch (e) {
      setLoad({ kind: 'error', detail: describeError(e), stale: false });
    }
  }, [templateId, locale]);
  useEffect(() => { void read(roomId); }, [read, roomId]);

  const proposal = load.kind === 'ready' ? load.proposal : null;
  const confirm = async () => {
    if (!proposal || !canConfirm(proposal, busy)) return;
    setBusy(true); setFailure(null);
    try {
      const result = await provisioningClient.confirm(proposal, roomId);
      setDone({ result, paused: result.agent?.status !== 'active', turnOnError: '' });
      onProvisioned();
    } catch (e) {
      const stale = e instanceof ApiError && e.status === 409;
      setFailure({ detail: describeError(e), stale });
    } finally { setBusy(false); }
  };
  const turnOn = async () => {
    const agent = done?.result.agent;
    if (!agent) return;
    setBusy(true);
    try {
      await api.apiRequest(`/api/v1/agents/${encodeURIComponent(agent.id)}`, {
        method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ status: 'active', version: agent.version }),
      });
      setDone((d) => d && { ...d, paused: false, turnOnError: '' });
      onProvisioned();
    } catch (e) {
      setDone((d) => d && { ...d, turnOnError: describeError(e) });
    } finally { setBusy(false); }
  };

  const name = proposal ? proposal.content.agent.name : roleName(templateId, locale);
  // Rendered on the body: the headquarters creates its own stacking context and a fixed mode switcher above it.
  return createPortal(<div className="hqProvBackdrop" dir={locale === 'ar' ? 'rtl' : 'ltr'} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="hqProvDialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel} aria-busy={busy || load.kind === 'loading'}>
      <header className="hqProvDlgHead">
        <h2 id={titleId}>{done ? t(`hqProv.done.${done.result.outcome}` as MessageKey, { name: done.result.agent?.name ?? name }) : t('hqProv.dlg.title', { name })}</h2>
        <button type="button" className="hqProvX" aria-label={t('hqProv.close')} onClick={onClose}><X size={16} aria-hidden="true" /></button>
      </header>
      <div className="hqProvDlgBody">
        {load.kind === 'loading' && <p className="hqProvNote" role="status"><Loader2 size={14} className="hqProvSpin" aria-hidden="true" /> {t('hqProv.dlg.loading')}</p>}
        {load.kind === 'error' && <div className="hqProvNote" role="alert">
          <p>{t('hqProv.dlg.error', { detail: load.detail })}</p>
          <button type="button" onClick={() => void read(roomId)}>{t('hqProv.retry')}</button>
        </div>}
        {done ? <DoneView done={done} busy={busy} onTurnOn={() => void turnOn()} onOpenAgents={() => { onNavigate('#agents'); onClose(); }} onClose={onClose} />
          : proposal && <ProposalBody proposal={proposal} locale={locale} roomId={roomId} onRoom={setRoomId} />}
        {!done && failure && <div className="hqProvNote hqProvFail" role="alert">
          <p>{t('hqProv.dlg.confirmFailed', { detail: failure.detail })}</p>
          {failure.stale && <p>{t('hqProv.dlg.reloadHint')} <button type="button" onClick={() => void read(roomId)}>{t('hqProv.dlg.reload')}</button></p>}
        </div>}
      </div>
      {!done && proposal && <footer className="hqProvDlgFoot">
        <p className="hqProvNote"><ShieldCheck size={13} aria-hidden="true" /> {proposal.can_confirm ? t('hqProv.dlg.clickOnly') : t('hqProv.confirmOnlyOwners')}</p>
        <div className="hqProvActions">
          <button type="button" className="hqProvPrimary" onClick={() => void confirm()} disabled={!canConfirm(proposal, busy)} data-autofocus>
            {busy ? t('hqProv.dlg.working') : t('hqProv.dlg.confirm')}</button>
          <button type="button" onClick={onClose} disabled={busy}>{t('hqProv.dlg.cancel')}</button>
        </div>
      </footer>}
    </div>
  </div>, document.body);
}

function missingText(m: ProvisioningMissing, locale: 'en' | 'ar'): string {
  return locale === 'ar' ? m.ar : m.en;
}

function ProposalBody({ proposal, locale, roomId, onRoom }: {
  proposal: ProvisioningProposal; locale: 'en' | 'ar'; roomId: string | null; onRoom: (id: string | null) => void;
}) {
  const { content, template, placement, gap } = proposal;
  const agent = content.agent;
  const roomLabel = placement.room_name ?? '';
  const kinds = content.work_kinds.length ? content.work_kinds.map((k) => t(`hqProv.kind.${k}` as MessageKey)).join(', ') : t('hqProv.kind.none');
  const seated = placement.mode !== 'none';
  const effectKey = !content.work_kinds.length ? 'hqProv.dlg.effect.none' : !seated ? 'hqProv.dlg.effect.noDesk'
    : content.work_kinds.includes('orders') ? 'hqProv.dlg.effect.orders' : 'hqProv.dlg.effect.leads';
  const roomSelectId = useId();
  return <>
    <p className="hqProvNote">{t('hqProv.dlg.intro')}</p>
    <section className="hqProvSection" aria-label={t('hqProv.dlg.why')}>
      <h3>{t('hqProv.dlg.why')}</h3>
      {gap ? <ul className="hqProvReasons">{gap.reasons.map((r, i) => <li key={i}>{reasonLine(r)}</li>)}</ul> : <p>{t('hqProv.dlg.noEvidence')}</p>}
    </section>
    <section className="hqProvSection" aria-label={t('hqProv.dlg.creates')}>
      <h3>{t('hqProv.dlg.creates')}</h3>
      <ul className="hqProvChecks">
        <li>{t('hqProv.dlg.createsAgent', { name: agent.name, department: agent.department })}</li>
        {proposal.creates.desk > 0 && <li>{t('hqProv.dlg.createsDesk')}</li>}
        {proposal.creates.seat > 0 && <li>{t('hqProv.dlg.createsSeat')}</li>}
        <li>{t('hqProv.dlg.createsBudget', { amount: content.budget.monthly_cost_limit_usd })}</li>
        <li>{t('hqProv.dlg.createsLedger', { kinds })}</li>
      </ul>
      <p className="hqProvNote">{agent.description}</p>
      <p className="hqProvNote"><strong>{t('hqProv.dlg.statusLine')}</strong></p>
    </section>
    <section className="hqProvSection" aria-label={t('hqProv.dlg.access')}>
      <h3>{t('hqProv.dlg.access')} <span className="hqProvBadge" data-severity={content.limited ? 'warning' : 'ok'}>{content.limited ? t('hqProv.dlg.limited') : t('hqProv.dlg.present')}</span></h3>
      <p className="hqProvNote">{t('hqProv.dlg.autonomy', { write: levelText(template.autonomy.write), external: levelText(template.autonomy.external) })}</p>
      <h4>{t('hqProv.dlg.tools')}</h4>
      <ul className="hqProvCaps" aria-label={t('hqProv.dlg.tools')}>
        {template.tools.map((tool) => <li key={tool.name} data-state={tool.state}>
          {tool.state === 'present' ? <CheckCircle2 size={13} aria-hidden="true" /> : <Circle size={13} aria-hidden="true" />}
          <code>{tool.name}</code> <small>{tool.state === 'present' ? t('hqProv.dlg.present')
            : `${t('hqProv.dlg.missingState')}: ${tool.reason === 'not_permitted' ? t('hqProv.dlg.notPermitted') : t('hqProv.dlg.notRegistered')}`}</small></li>)}
      </ul>
      <h4>{t('hqProv.dlg.skills')}</h4>
      <ul className="hqProvCaps" aria-label={t('hqProv.dlg.skills')}>
        {template.skills.map((s) => <li key={s.id} data-state={s.state}>
          {s.state === 'present' ? <CheckCircle2 size={13} aria-hidden="true" /> : <Circle size={13} aria-hidden="true" />}
          <code>{s.id}</code> <small>{s.state === 'present' ? t('hqProv.dlg.present')
            : `${t('hqProv.dlg.missingState')}: ${s.reason === 'needs_tools' ? t('hqProv.dlg.needsTools') : t('hqProv.dlg.notAvailable')}`}</small></li>)}
      </ul>
      <h4>{t('hqProv.dlg.missingTitle')}</h4>
      {template.missing.length === 0 ? <p className="hqProvNote">{t('hqProv.dlg.noMissing')} {t('hqProv.dlg.fullHint')}</p>
        : <><p className="hqProvNote">{t('hqProv.dlg.limitedHint')}</p>
          <ul className="hqProvMissing" aria-label={t('hqProv.dlg.missingTitle')}>{template.missing.map((m) => <li key={`${m.kind}:${m.id}`}>{missingText(m, locale)}</li>)}</ul></>}
    </section>
    <section className="hqProvSection" aria-label={t('hqProv.dlg.seat')}>
      <h3>{t('hqProv.dlg.seat')}</h3>
      <p className="hqProvNote">{placement.mode === 'existing_desk' ? t('hqProv.dlg.seatExisting', { desk: placement.desk_label ?? '', room: roomLabel })
        : placement.mode === 'new_desk' ? t('hqProv.dlg.seatNew', { desk: placement.desk_label ?? '', room: roomLabel })
        : t((['no_linked_room', 'linked_rooms_full', 'room_full'].includes(placement.reason ?? '') ? `hqProv.dlg.seatNone.${placement.reason}` : 'hqProv.dlg.seatNone.default') as MessageKey)}</p>
      {placement.candidates.length > 0 && <div className="hqProvField">
        <label htmlFor={roomSelectId}>{t('hqProv.dlg.room')}</label>
        <select id={roomSelectId} value={roomId ?? ''} onChange={(e) => onRoom(e.target.value || null)}>
          <option value="">{t('hqProv.dlg.roomAuto')}</option>
          {placement.candidates.map((c) => <option key={c.room_id} value={c.room_id} disabled={!c.fits}>
            {t('hqProv.dlg.roomOption', { room: c.room_name, vacant: c.vacant, desks: c.desks, capacity: c.capacity,
              linked: `${c.linked ? t('hqProv.dlg.roomLinked') : ''}${c.fits ? '' : t('hqProv.dlg.roomFull')}` })}</option>)}
        </select>
      </div>}
    </section>
    <section className="hqProvSection" aria-label={t('hqProv.dlg.effect')}>
      <h3>{t('hqProv.dlg.effect')}</h3>
      <p className="hqProvNote">{t(effectKey as MessageKey, { room: roomLabel })}</p>
    </section>
  </>;
}

function DoneView({ done, busy, onTurnOn, onOpenAgents, onClose }: {
  done: Done; busy: boolean; onTurnOn: () => void; onOpenAgents: () => void; onClose: () => void;
}) {
  const { result } = done;
  return <div className="hqProvDone" role="status">
    <p><CheckCircle2 size={15} aria-hidden="true" /> {done.paused ? t('hqProv.done.paused') : t('hqProv.done.active')}</p>
    {result.limited && <p>{t('hqProv.done.limited', { count: result.missing.length })}</p>}
    {done.turnOnError && <p role="alert">{t('hqProv.done.turnOnFailed', { detail: done.turnOnError })}</p>}
    <div className="hqProvActions">
      {done.paused && result.agent && <button type="button" className="hqProvPrimary" onClick={onTurnOn} disabled={busy} data-autofocus>{t('hqProv.done.turnOn')}</button>}
      <button type="button" onClick={onOpenAgents}>{t('hqProv.done.openAgents')}</button>
      <button type="button" onClick={onClose}>{t('hqProv.done.close')}</button>
    </div>
  </div>;
}
