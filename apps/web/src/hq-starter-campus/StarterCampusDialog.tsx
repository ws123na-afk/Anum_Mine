import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckCircle2, ChevronDown, ChevronUp, Circle, CircleSlash, Loader2, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import type { StarterBuilding, StarterCampusPreview, StarterCampusResult, StarterRoom, StarterSkipReason } from '@anum/contracts';
import * as api from '../lib/api';
import { ApiError, describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { useModal } from '../Dialog';
import { useLocale } from '../useLocale';
import { canConfirm, createStarterCampusClient, departmentFills, deskLabels, selectable } from './client';
import './hqStarterCampus.css';

export const starterCampusClient = createStarterCampusClient(api.apiRequest, api.currentScope);

type Load = { kind: 'loading' } | { kind: 'denied' } | { kind: 'error'; detail: string } | { kind: 'ready' };
export interface StarterCampusDialogProps {
  onClose: () => void;
  /** Called after the server wrote something, so the registry and the live flow re-read. */
  onCreated: () => void;
  onOpenGaps: () => void;
}

const hasCreate = (p: StarterCampusPreview) => Object.values(p.totals.create).some((n) => n > 0);
const nameOf = (n: { en: string; ar: string }, locale: 'en' | 'ar') => (locale === 'ar' ? n.ar : n.en);
const skipText = (reason: StarterSkipReason | null) => t(`hqSc.skip.${reason ?? 'exists'}` as MessageKey);

export function StarterCampusDialog({ onClose, onCreated, onOpenGaps }: StarterCampusDialogProps) {
  const locale = useLocale();
  const panel = useModal(onClose);
  const titleId = useId();
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [preview, setPreview] = useState<StarterCampusPreview | null>(null);
  const [ticked, setTicked] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ detail: string; stale: boolean } | null>(null);
  const [done, setDone] = useState<StarterCampusResult | null>(null);
  const [fetching, setFetching] = useState(true);
  const alive = useRef(true);
  const serial = useRef(0);

  const read = useCallback(async (keys: string[] | null) => {
    const mine = ++serial.current;
    setFetching(true);
    try {
      const next = await starterCampusClient.preview(keys);
      if (!alive.current || mine !== serial.current) return;
      setPreview(next);
      setTicked((old) => old ?? new Set(next.selected));
      setLoad({ kind: 'ready' });
    } catch (e) {
      if (!alive.current || mine !== serial.current) return;
      setLoad(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', detail: describeError(e) });
    } finally {
      if (alive.current && mine === serial.current) setFetching(false);
    }
  }, []);
  useEffect(() => { alive.current = true; void read(null); return () => { alive.current = false; }; }, [read]);

  const toggle = (next: Set<string>) => {
    setTicked(next);
    setFailure(null);
    if (next.size > 0) void read([...next]);
    else setFetching(false);
  };
  const confirm = async () => {
    if (!preview || !ticked || !canConfirm(preview, ticked, busy, fetching)) return;
    setBusy(true); setFailure(null);
    try {
      const result = await starterCampusClient.confirm(preview);
      setDone(result);
      if (result.outcome !== 'already_set_up') onCreated();
    } catch (e) {
      setFailure({ detail: describeError(e), stale: e instanceof ApiError && e.status === 409 });
    } finally { setBusy(false); }
  };
  const reviewAgain = () => {
    setDone(null); setFailure(null); setTicked(null); setLoad({ kind: 'loading' });
    void read(null);
  };

  const ready = load.kind === 'ready' && preview !== null && ticked !== null;
  return createPortal(<div className="hqScBackdrop" dir={locale === 'ar' ? 'rtl' : 'ltr'} onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <div className="hqScDialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel} aria-busy={busy || fetching}>
      <header className="hqScHead">
        <h2 id={titleId}>{done ? t(`hqSc.done.${done.outcome}` as MessageKey) : t('hqSc.title')}</h2>
        <button type="button" className="hqScX" aria-label={t('hqSc.close')} onClick={onClose} disabled={busy}><X size={16} aria-hidden="true" /></button>
      </header>
      <div className="hqScBody">
        {load.kind === 'loading' && <p className="hqScNote" role="status"><Loader2 size={14} className="hqScSpin" aria-hidden="true" /> {t('hqSc.loading')}</p>}
        {load.kind === 'denied' && <p className="hqScNote hqScFail" role="alert">{t('hqSc.denied')}</p>}
        {load.kind === 'error' && <div className="hqScNote hqScFail" role="alert">
          <p>{t('hqSc.error', { detail: load.detail })}</p>
          <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void read(ticked && ticked.size ? [...ticked] : null); }}>{t('hqSc.retry')}</button>
        </div>}
        {done ? <ResultView result={done} locale={locale} />
          : ready && <PlanView preview={preview} ticked={ticked} locale={locale} fetching={fetching} onChange={toggle} />}
        {!done && failure && <div className="hqScNote hqScFail" role="alert">
          <p>{t('hqSc.confirmFailed', { detail: failure.detail })}</p>
          {failure.stale && <p>{t('hqSc.stale')} <button type="button" onClick={reviewAgain}>{t('hqSc.review')}</button></p>}
        </div>}
        {busy && <p className="hqScNote" role="status"><Loader2 size={14} className="hqScSpin" aria-hidden="true" /> {t('hqSc.working')}</p>}
      </div>
      {done ? <footer className="hqScFoot"><p className="hqScNote">{t('hqSc.done.next')}</p>
        <div className="hqScActions">
          <button type="button" className="hqScPrimary" onClick={() => { onOpenGaps(); onClose(); }} data-autofocus>{t('hqSc.done.gaps')}</button>
          {(done.outcome === 'partial' || done.outcome === 'failed') && <button type="button" onClick={reviewAgain}>{t('hqSc.done.again')}</button>}
          <button type="button" onClick={onClose}>{t('hqSc.close')}</button>
        </div></footer>
        : ready && <footer className="hqScFoot">
          <p className="hqScNote"><ShieldCheck size={13} aria-hidden="true" /> {preview.can_confirm ? t('hqSc.clickOnly') : t('hqSc.ownersOnly')}</p>
          <div className="hqScActions">
            <button type="button" className="hqScPrimary" onClick={() => void confirm()} disabled={!canConfirm(preview, ticked, busy, fetching)} data-autofocus>
              {busy ? t('hqSc.working') : preview && !hasCreate(preview) && departmentFills(preview.totals) > 0 ? t('hqSc.confirmFill') : t('hqSc.confirm')}</button>
            <button type="button" onClick={onClose} disabled={busy}>{t('hqSc.cancel')}</button>
          </div>
        </footer>}
    </div>
  </div>, document.body);
}

function PlanView({ preview, ticked, locale, fetching, onChange }: {
  preview: StarterCampusPreview; ticked: Set<string>; locale: 'en' | 'ar'; fetching: boolean; onChange: (next: Set<string>) => void;
}) {
  const { create, skip } = preview.totals;
  const pickable = selectable(preview.buildings);
  const flip = (key: string) => { const next = new Set(ticked); if (next.has(key)) next.delete(key); else next.add(key); onChange(next); };
  const fills = departmentFills(preview.totals);
  const nothing = ticked.size > 0 && !fetching && !Object.values(create).some((n) => n > 0) && fills === 0;
  return <>
    <p className="hqScNote">{t('hqSc.intro')}</p>
    <ul className="hqScRules">
      <li>{t('hqSc.noAgents')}</li>
      <li>{t('hqSc.keep')}</li>
      <li>{t('hqSc.zone', { zone: preview.timezone })}</li>
    </ul>
    <section className="hqScTotals" aria-label={t('hqSc.title')} aria-live="polite">
      {ticked.size === 0 ? <p><CircleSlash size={14} aria-hidden="true" /> {t('hqSc.noneSelected')}</p>
        : nothing ? <p><CheckCircle2 size={14} aria-hidden="true" /> {t('hqSc.nothingToDo')}</p>
        : <p><strong>{Object.values(create).some((n) => n > 0) ? t('hqSc.totalsCreate', { ...create }) : t('hqSc.totalsNoCreate')}</strong></p>}
      {ticked.size > 0 && fills > 0 && <p className="hqScNote" data-testid="sc-fills">{t('hqSc.totalsFill', { count: fills })}</p>}
      {ticked.size > 0 && (skip.buildings + skip.floors + skip.rooms > 0) && <p className="hqScNote">{t('hqSc.totalsSkip', { ...skip })}</p>}
    </section>
    <section className="hqScSection" aria-label={t('hqSc.pick')}>
      <div className="hqScPickHead">
        <h3>{t('hqSc.pick')}</h3>
        <span className="hqScCount" role="status">{t('hqSc.pickCount', { selected: ticked.size, total: preview.buildings.length })}</span>
        <button type="button" onClick={() => onChange(new Set(pickable))} disabled={pickable.length === 0}>{t('hqSc.selectAll')}</button>
        <button type="button" onClick={() => onChange(new Set())} disabled={ticked.size === 0}>{t('hqSc.selectNone')}</button>
      </div>
      <ul className="hqScBuildings" aria-busy={fetching}>
        {preview.buildings.map((b) => <BuildingRow key={b.key} b={b} locale={locale} checked={ticked.has(b.key)} onFlip={() => flip(b.key)} />)}
      </ul>
    </section>
  </>;
}

function BuildingRow({ b, locale, checked, onFlip }: { b: StarterBuilding; locale: 'en' | 'ar'; checked: boolean; onFlip: () => void }) {
  const [open, setOpen] = useState(false);
  const boxId = useId();
  const detailId = useId();
  const name = nameOf(b.name, locale);
  const c = b.counts.create;
  return <li className="hqScBuilding" data-status={b.status} data-selected={checked}>
    <div className="hqScRow">
      <input id={boxId} type="checkbox" checked={checked} onChange={onFlip} disabled={b.status === 'complete'} />
      <label htmlFor={boxId}>
        <strong>{name}</strong>
        <small>{b.status === 'complete' ? t('hqSc.row.nothing') : Object.values(c).some((n) => n > 0)
          ? t('hqSc.row.counts', { floors: c.floors, rooms: c.rooms, desks: c.desks }) : t('hqSc.row.fillOnly', { count: departmentFills(b.counts) })}</small>
      </label>
      <span className="hqScBadge" data-status={b.status}>{t(`hqSc.status.${b.status}` as MessageKey)}</span>
      <button type="button" className="hqScToggle" aria-expanded={open} aria-controls={detailId} aria-label={`${open ? t('hqSc.hide') : t('hqSc.details')}: ${name}`} onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}</button>
    </div>
    {open && <div id={detailId} className="hqScDetail" role="group" aria-label={t('hqSc.detailsFor', { name })}>
      {b.floors.map((f) => <div key={f.id} className="hqScFloor">
        <h4>{t('hqSc.floor', { level: f.level, name: nameOf(f.name, locale), width: f.width_m, depth: f.depth_m })}
          {' '}<span className="hqScTag" data-action={f.action}>{f.action === 'create' ? t('hqSc.action.create') : skipText(f.reason)}</span></h4>
        <ul>{f.rooms.map((r) => <RoomLine key={r.id} r={r} locale={locale} />)}</ul>
      </div>)}
    </div>}
  </li>;
}

function RoomLine({ r, locale }: { r: StarterRoom; locale: 'en' | 'ar' }) {
  const dept = r.team_department ? t(`hqSc.dept.${r.team_department}` as MessageKey) : null;
  return <li className="hqScRoom" data-action={r.action}>
    <span>{r.action === 'create' ? <Circle size={11} aria-hidden="true" /> : <CircleSlash size={11} aria-hidden="true" />}
      {' '}{t('hqSc.room', { name: nameOf(r.name, locale), kind: t(`hqSc.kind.${r.kind}` as MessageKey), width: r.width, depth: r.depth })}
      {dept && <>{', '}{t('hqSc.dept', { department: dept })}</>}
      {r.department_set && <>{', '}<span data-testid="sc-room-fill">{t('hqSc.fillRoom', { department: t(`hqSc.dept.${r.department_set.to}` as MessageKey) })}</span></>}
      {' '}<span className="hqScTag" data-action={r.action}>{r.action === 'create' ? t('hqSc.action.create') : skipText(r.reason)}</span></span>
    {r.action === 'create' && <small>{r.desks.length ? t('hqSc.desks', { count: r.desks.length, list: deskLabels(r) }) : t('hqSc.noDesks')}</small>}
  </li>;
}

function ResultView({ result, locale }: { result: StarterCampusResult; locale: 'en' | 'ar' }) {
  const tone = result.outcome === 'created' || result.outcome === 'already_set_up' ? 'ok' : 'warn';
  return <div className="hqScDone" role="status">
    <p data-tone={tone}>{tone === 'ok' ? <CheckCircle2 size={15} aria-hidden="true" /> : <TriangleAlert size={15} aria-hidden="true" />}{' '}
      {t('hqSc.done.summary', { ...result.totals })}{(result.departments_set ?? 0) > 0 && <> {t('hqSc.done.filled', { count: result.departments_set })}</>}</p>
    <h3>{t('hqSc.done.list')}</h3>
    <ul className="hqScResults">
      {result.buildings.map((b) => <li key={b.key} data-status={b.status}>
        <strong>{nameOf(b.name, locale)}</strong>
        <span>{b.status === 'created' ? t('hqSc.done.created_item', { ...b.created })
          : b.status === 'already_exists' ? t('hqSc.done.already_exists')
          : b.status === 'changed' ? t('hqSc.done.changed')
          : t('hqSc.done.failed_item', { detail: b.message })}</span>
      </li>)}
    </ul>
  </div>;
}
