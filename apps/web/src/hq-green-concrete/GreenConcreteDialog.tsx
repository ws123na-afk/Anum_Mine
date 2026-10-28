import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckCircle2, CircleSlash, Loader2, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import type { AvatarStyle, GreenConcretePreview, GreenConcreteResult, GreenLooks, GreenPerson } from '@anum/contracts';
import * as api from '../lib/api';
import { ApiError, describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { useModal } from '../Dialog';
import { useLocale } from '../useLocale';
import { roleText } from '../lib/hqRoles';
import { LOOK_CHOICES, canConfirm, createGreenConcreteClient } from './client';
import { AvatarPreview } from '../AvatarStyleEditor';
import { roleFamily } from '../lib/hqAvatar';
import { PlanMap } from './PlanMap';
import './hqGreenConcrete.css';

export const greenConcreteClient = createGreenConcreteClient(api.apiRequest, api.currentScope);

type Load = { kind: 'loading' } | { kind: 'denied' } | { kind: 'error'; detail: string } | { kind: 'ready' };
export interface GreenConcreteDialogProps {
  onClose: () => void;
  /** Called after the server wrote something, so the registry and the live flow re-read. */
  onCreated: () => void;
}

const hoursText = (p: GreenPerson) => t('hqGc.shift.hours', { start: p.work_start, end: p.work_end });
const seatText = (p: GreenPerson) => (p.seat_kind === 'manager' ? t('hqGc.seat.manager') : t('hqGc.seat.numbered', { number: p.seat_order }));
/** "06:00 to 14:00: Ijaz, Ali Zeehan, Rana; 08:00 to 17:00: ..." from the plan's grouped hours. */
const hoursList = (p: GreenConcretePreview) => {
  const names = new Map(p.people.map((x) => [x.key, x.name]));
  return p.hours.windows.map((w) => `${t('hqGc.shift.hours', { start: w.start, end: w.end })}: ${w.people.map((k) => names.get(k) ?? k).join(', ')}`).join('; ');
};

/**
 * "Add Green Concrete": the owner's company with its command center, L shaped tables, shifts and people. A reviewed, click
 * confirmed action: the plan shows every record, the people become PAUSED agents, and only a click by an owner confirms.
 */
export function GreenConcreteDialog({ onClose, onCreated }: GreenConcreteDialogProps) {
  const locale = useLocale();
  const panel = useModal(onClose);
  const titleId = useId();
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [preview, setPreview] = useState<GreenConcretePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ detail: string; stale: boolean } | null>(null);
  const [done, setDone] = useState<GreenConcreteResult | null>(null);
  // The look the owner picked for each person (nobody is listed until they choose; everyone else is neutral). Part of the hash.
  const [choices, setChoices] = useState<Record<string, AvatarStyle>>({});
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);
  const latest = useRef(0);
  const looksOf = (c: Record<string, AvatarStyle>): GreenLooks => ({ default_look: 'neutral', appearance: c });

  const read = useCallback(async (next: Record<string, AvatarStyle> = {}, quiet = false) => {
    const ticket = ++latest.current;
    if (quiet) setRefreshing(true); else setLoad({ kind: 'loading' });
    try {
      const plan = await greenConcreteClient.preview(looksOf(next));
      if (!alive.current || ticket !== latest.current) return;
      setPreview(plan);
      setLoad({ kind: 'ready' });
    } catch (e) {
      if (alive.current && ticket === latest.current) setLoad(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', detail: describeError(e) });
    } finally { if (alive.current && ticket === latest.current) setRefreshing(false); }
  }, []);
  useEffect(() => { alive.current = true; void read(); return () => { alive.current = false; }; }, [read]);
  const pickLook = (key: string, style: AvatarStyle) => {
    const next = { ...choices, [key]: style };
    setChoices(next);
    void read(next, true);
  };

  const confirm = async () => {
    if (!preview || !canConfirm(preview, busy || refreshing)) return;
    setBusy(true); setFailure(null);
    try {
      const result = await greenConcreteClient.confirm(preview, looksOf(choices));
      if (!alive.current) return;
      setDone(result);
      if (result.outcome !== 'already_set_up') onCreated();
    } catch (e) {
      if (alive.current) setFailure({ detail: describeError(e), stale: e instanceof ApiError && e.status === 409 });
    } finally { if (alive.current) setBusy(false); }
  };
  const reviewAgain = () => { setDone(null); setFailure(null); void read(choices); };

  const ready = load.kind === 'ready' && preview !== null;
  const create = preview?.counts.create;
  const nothing = ready && create !== undefined && !canConfirmWork(preview);
  return createPortal(<div className="hqGcBackdrop" dir={locale === 'ar' ? 'rtl' : 'ltr'} onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <div className="hqGcDialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel} aria-busy={busy} data-testid="gc-dialog">
      <header className="hqGcHead">
        <h2 id={titleId}>{done ? t(`hqGc.done.${done.outcome}` as MessageKey) : t('hqGc.title')}</h2>
        <button type="button" className="hqGcX" aria-label={t('hqGc.close')} onClick={onClose} disabled={busy}><X size={16} aria-hidden="true" /></button>
      </header>
      <div className="hqGcBody" tabIndex={0} role="region" aria-label={t('hqGc.title')}>
        {load.kind === 'loading' && <p className="hqGcNote" role="status"><Loader2 size={14} className="hqGcSpin" aria-hidden="true" /> {t('hqGc.loading')}</p>}
        {load.kind === 'denied' && <p className="hqGcNote hqGcFail" role="alert">{t('hqGc.denied')}</p>}
        {load.kind === 'error' && <div className="hqGcNote hqGcFail" role="alert">
          <p>{t('hqGc.error', { detail: load.detail })}</p>
          <button type="button" onClick={() => void read(choices)}>{t('hqGc.retry')}</button>
        </div>}
        {done && <div className="hqGcDone" role="status" data-testid="gc-done">
          <p data-tone={done.outcome === 'partial' ? 'warn' : 'ok'}>{done.outcome === 'partial' ? <TriangleAlert size={15} aria-hidden="true" /> : <CheckCircle2 size={15} aria-hidden="true" />}{' '}
            {done.outcome === 'already_set_up' ? t('hqGc.totals.nothing') : t('hqGc.done.summary', { ...done.created })}</p>
          {done.failed.length > 0 && <p className="hqGcNote hqGcFail">{t('hqGc.done.failed', { list: done.failed.join(', ') })}</p>}
          <p className="hqGcNote">{t('hqGc.done.next')}</p>
        </div>}
        {!done && ready && <PlanView preview={preview} nothing={nothing} choices={choices} onPick={pickLook} refreshing={refreshing} />}
        {!done && failure && <div className="hqGcNote hqGcFail" role="alert">
          <p>{t('hqGc.confirmFailed', { detail: failure.detail })}</p>
          {failure.stale && <p>{t('hqGc.stale')} <button type="button" onClick={reviewAgain}>{t('hqGc.review')}</button></p>}
        </div>}
        {busy && <p className="hqGcNote" role="status"><Loader2 size={14} className="hqGcSpin" aria-hidden="true" /> {t('hqGc.working')}</p>}
      </div>
      <footer className="hqGcFoot">
        {done ? <div className="hqGcActions">
          <button type="button" className="hqGcPrimary" onClick={onClose} data-autofocus>{t('hqGc.done.open')}</button>
          {done.outcome === 'partial' && <button type="button" onClick={reviewAgain}>{t('hqGc.review')}</button>}
        </div> : ready && <>
          <p className="hqGcNote"><ShieldCheck size={13} aria-hidden="true" /> {preview.can_confirm ? t('hqGc.clickOnly') : t('hqGc.ownersOnly')}</p>
          <div className="hqGcActions">
            <button type="button" className="hqGcPrimary" onClick={() => void confirm()} disabled={!canConfirm(preview, busy || refreshing)} data-autofocus data-testid="gc-confirm">
              {busy ? t('hqGc.working') : t('hqGc.confirm')}</button>
            <button type="button" onClick={onClose} disabled={busy}>{t('hqGc.cancel')}</button>
          </div>
        </>}
      </footer>
    </div>
  </div>, document.body);
}

function canConfirmWork(p: GreenConcretePreview): boolean {
  const c = p.counts.create;
  return c.buildings + c.floors + c.rooms + c.desks + c.agents + p.counts.seat > 0;
}

function PlanView({ preview, nothing, choices, onPick, refreshing }: { preview: GreenConcretePreview; nothing: boolean; choices: Record<string, AvatarStyle>; onPick: (key: string, style: AvatarStyle) => void; refreshing: boolean }) {
  const c = preview.counts.create;
  const blocked = preview.building.reason === 'name_in_use';
  const cap = preview.monthly_cap_usd;
  return <>
    <p className="hqGcNote">{t('hqGc.intro')}</p>
    <ul className="hqGcRules">
      <li>{t('hqGc.rule.paused')}</li>
      <li>{t('hqGc.rule.keep')}</li>
      <li data-testid="gc-hours">{t('hqGc.rule.hours', { list: hoursList(preview), zone: preview.timezone })}</li>
      <li>{t('hqGc.rule.avatar')}</li>
      <li>{t('hqGc.rule.cost', { cap, total: Math.round(cap * preview.people.length) })}</li>
    </ul>
    <section className="hqGcTotals" aria-live="polite" data-testid="gc-totals">
      {blocked ? <p className="hqGcFail"><CircleSlash size={14} aria-hidden="true" /> {t('hqGc.totals.blocked')}</p>
        : nothing ? <p><CheckCircle2 size={14} aria-hidden="true" /> {t('hqGc.totals.nothing')}</p>
          : <p><strong>{t('hqGc.totals.create', { ...c, seat: preview.counts.seat })}</strong></p>}
    </section>
    {preview.older_seats.length > 0 && <p className="hqGcNote" role="note" data-testid="gc-older">{t('hqGc.older', { count: preview.older_seats.length })}</p>}
    <ul className="hqGcRecords" aria-label={t('hqGc.title')}>
      <li data-action={preview.building.action}>{t('hqGc.records.building', { name: preview.building.name.en })} <Tag action={preview.building.action} reason={preview.building.reason} /></li>
      <li data-action={preview.floor.action}>{t('hqGc.records.floor', { name: preview.floor.name.en, width: preview.floor.width_m, depth: preview.floor.depth_m })} <Tag action={preview.floor.action} reason={preview.floor.reason} /></li>
      <li data-action={preview.room.action}>{t('hqGc.records.room', { name: preview.room.name.en, width: preview.room.width, depth: preview.room.depth })} <Tag action={preview.room.action} reason={preview.room.reason} /></li>
    </ul>
    <section className="hqGcSection" aria-label={t('hqGc.layout.title')}>
      <h3>{t('hqGc.layout.title')}</h3>
      <PlanMap seats={preview.seats} width={preview.room.width} depth={preview.room.depth} />
    </section>
    <section className="hqGcSection">
      <h3>{t('hqGc.people.title', { count: preview.people.length })}</h3>
      <p className="hqGcNote">{t('hqGc.look.help')}</p>
      {refreshing && <p className="hqGcNote" role="status">{t('hqGc.look.refreshing')}</p>}
      <div className="hqGcTableWrap" tabIndex={0} role="region" aria-label={t('hqGc.people.title', { count: preview.people.length })}>
        <table className="hqGcPeople" data-testid="gc-people">
          <thead><tr><th scope="col">{t('hqGc.people.name')}</th><th scope="col">{t('hqGc.people.role')}</th><th scope="col">{t('hqGc.people.hours')}</th><th scope="col">{t('hqGc.people.seat')}</th><th scope="col">{t('hqGc.people.look')}</th><th scope="col">{t('hqGc.people.state')}</th></tr></thead>
          <tbody>
            {preview.people.map((p) => <tr key={p.key} data-person={p.key}>
              <th scope="row"><bdi>{p.name}</bdi></th>
              <td>{roleText(p.role.en)}</td>
              <td dir="ltr"><bdi>{hoursText(p)}</bdi></td>
              <td>{seatText(p)}</td>
              <td className="hqGcLook">{p.agent_action === 'create' && preview.can_confirm
                ? <>
                  <span className="hqGcLookPreview" aria-hidden="true"><AvatarPreview seed={`gc:${p.key}`} value={{ avatar_style: choices[p.key] ?? 'neutral' }} role={roleFamily({ department: p.role.en })} decorative /></span>
                  <label className="hqGcLookPick"><span className="hqGcSr">{t('hqGc.look.label', { name: p.name })}</span>
                    <select value={choices[p.key] ?? 'neutral'} onChange={(e) => onPick(p.key, e.target.value as AvatarStyle)} data-look-for={p.key}>
                      {LOOK_CHOICES.map((l) => <option key={l} value={l}>{t(`hqGc.look.${l}` as MessageKey)}</option>)}
                    </select></label>
                </>
                : <span className="hqGcNote">{p.agent_action === 'create' ? t('hqGc.look.neutral') : t('hqGc.look.exists')}</span>}</td>
              <td>{p.agent_action === 'create' ? t('hqGc.person.agentNew') : t('hqGc.person.agentExists')}{', '}
                {p.seat_action === 'seat' ? t('hqGc.person.seat') : t(`hqGc.person.${p.seat_reason ?? 'seated'}` as MessageKey)}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>
  </>;
}

function Tag({ action, reason }: { action: 'create' | 'skip'; reason: string | null }) {
  return <span className="hqGcTag" data-action={action}>{action === 'create' ? t('hqGc.action.create') : t(`hqGc.skip.${reason ?? 'exists'}` as MessageKey)}</span>;
}
