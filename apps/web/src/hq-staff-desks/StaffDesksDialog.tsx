import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckCircle2, ChevronDown, ChevronUp, CircleSlash, Loader2, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import type { StaffBuilding, StaffDesksPreview, StaffDesksResult, StaffDeskResult, StaffRoom } from '@anum/contracts';
import * as api from '../lib/api';
import { ApiError, describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { useModal } from '../Dialog';
import { deskLabel, pickName } from '../lib/registryNames';
import { useLocale } from '../useLocale';
import { canConfirm, createStaffDesksClient, DESK_STATES, flatten, money, pageOf, summarize } from './client';
import { TurnOnPanel } from './TurnOnPanel';
import './hqStaffDesks.css';

export const staffDesksClient = createStaffDesksClient(api.apiRequest, api.currentScope);

type Load = { kind: 'loading' } | { kind: 'denied' } | { kind: 'error'; detail: string } | { kind: 'ready' };
type Run = { index: number; total: number } | null;
type Done = { results: StaffDeskResult[]; buildings: { id: string; outcome: StaffDesksResult['outcome'] }[]; failure: { detail: string; stale: boolean } | null };
export interface StaffDesksDialogProps {
  onClose: () => void;
  /** Called after the server wrote something, so the registry, the live flow and the gaps re-read. */
  onChanged: () => void;
}

const PAGE = 25;
const doneTone = (d: Done) => (d.failure && !d.results.length ? 'failed' : d.failure || d.results.some((r) => r.status === 'failed') ? 'partial' : 'ok');
const keyOf = (keys: Iterable<string>, skip: Iterable<string>, max: number | null) => JSON.stringify([[...keys].sort(), [...skip].sort(), max]);
const nameOf = (en: string, ar: string | null, locale: 'en' | 'ar') => pickName(en, ar, locale);
const stateText = (s: string) => t(`hqSd.state.${s}` as MessageKey);

export function buildingName(b: { id: string; name: string; name_ar?: string | null }, locale: 'en' | 'ar'): string {
  if (locale !== 'ar') return b.name;
  if (b.name_ar?.trim()) return pickName(b.name, b.name_ar, locale);
  const key = `hqSd.building.${b.id}` as MessageKey;
  const text = t(key);
  return text === key ? b.name : text;
}

export function StaffDesksDialog({ onClose, onChanged }: StaffDesksDialogProps) {
  const locale = useLocale();
  const panel = useModal(onClose);
  const titleId = useId();
  const [tab, setTab] = useState<'plan' | 'agents'>('plan');
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [preview, setPreview] = useState<StaffDesksPreview | null>(null);
  const [ticked, setTicked] = useState<Set<string> | null>(null);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [capText, setCapText] = useState('');
  const [fetching, setFetching] = useState(true);
  const [run, setRun] = useState<Run>(null);
  const [done, setDone] = useState<Done | null>(null);
  const [actionError, setActionError] = useState('');
  const alive = useRef(true);
  const serial = useRef(0);
  const lastKey = useRef('');

  const cap = capText.trim() === '' ? null : Math.max(0, Math.min(200, Math.floor(Number(capText) || 0)));
  const read = useCallback(async (keys: string[] | null, skip: Set<string>, max: number | null) => {
    const mine = ++serial.current;
    setFetching(true);
    try {
      const next = await staffDesksClient.preview({ ...(keys ? { buildings: keys } : {}), exclude_rooms: [...skip], max_agents: max, lang: locale });
      if (!alive.current || mine !== serial.current) return;
      setPreview(next);
      lastKey.current = keyOf(keys ?? next.buildings.map((b) => b.id), skip, max);
      setTicked((old) => old ?? new Set(next.buildings.map((b) => b.id)));
      setLoad({ kind: 'ready' });
    } catch (e) {
      if (!alive.current || mine !== serial.current) return;
      setLoad(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', detail: describeError(e) });
    } finally {
      if (alive.current && mine === serial.current) setFetching(false);
    }
  }, [locale]);
  useEffect(() => { alive.current = true; void read(null, new Set(), null); return () => { alive.current = false; }; }, [read]);
  // Every change of the choices re-reads the plan, so the hashes always match exactly what is ticked.
  useEffect(() => {
    if (!ticked || ticked.size === 0 || done || keyOf(ticked, excluded, cap) === lastKey.current) return undefined;
    const timer = window.setTimeout(() => void read([...ticked], excluded, cap), 250);
    return () => window.clearTimeout(timer);
  }, [ticked, excluded, cap, read, done]);

  const busy = run !== null;
  const flip = (set: Set<string>, id: string) => { const next = new Set(set); if (next.has(id)) next.delete(id); else next.add(id); return next; };

  const confirm = async () => {
    if (!preview || !canConfirm(preview, busy, fetching)) return;
    setActionError('');
    const batches = preview.batches;
    const results: StaffDeskResult[] = [];
    const buildings: Done['buildings'] = [];
    let failure: Done['failure'] = null;
    for (let i = 0; i < batches.length; i += 1) {
      setRun({ index: i + 1, total: batches.length });
      try {
        const out = await staffDesksClient.confirm(batches[i], [...excluded], locale);
        results.push(...out.results);
        buildings.push({ id: out.building_id, outcome: out.outcome });
      } catch (e) {
        failure = { detail: describeError(e), stale: e instanceof ApiError && e.status === 409 };
        break;
      }
    }
    setRun(null);
    setDone({ results, buildings, failure });
    onChanged();
  };
  const reviewAgain = () => { setDone(null); setActionError(''); setLoad({ kind: 'loading' }); void read(ticked ? [...ticked] : null, excluded, cap); };
  const keepEmpty = async (deskId: string, keep: boolean) => {
    setActionError('');
    try { await staffDesksClient.keepEmpty([deskId], keep); await read(ticked ? [...ticked] : null, excluded, cap); }
    catch (e) { setActionError(t('hqSd.keepFailed', { detail: describeError(e) })); }
  };

  const ready = load.kind === 'ready' && preview !== null && ticked !== null;
  return createPortal(<div className="hqSdBackdrop" dir={locale === 'ar' ? 'rtl' : 'ltr'} onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <div className="hqSdDialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel} aria-busy={busy || fetching}>
      <header className="hqSdHead">
        <h2 id={titleId}>{done ? t(`hqSd.done.title.${doneTone(done)}` as MessageKey) : t('hqSd.title')}</h2>
        <button type="button" className="hqSdX" aria-label={t('hqSd.close')} onClick={onClose} disabled={busy}><X size={16} aria-hidden="true" /></button>
      </header>
      <div className="hqSdTabs" role="tablist" aria-label={t('hqSd.title')}>
        {(['plan', 'agents'] as const).map((id) => <button key={id} type="button" role="tab" id={`${titleId}-${id}`} aria-selected={tab === id}
          aria-controls={`${titleId}-panel`} data-active={tab === id} onClick={() => setTab(id)} disabled={busy}>{t(`hqSd.tab.${id}` as MessageKey)}</button>)}
      </div>
      <div className="hqSdBody" role="tabpanel" id={`${titleId}-panel`} aria-labelledby={`${titleId}-${tab}`}>
        {tab === 'agents' ? <TurnOnPanel locale={locale} onChanged={onChanged} />
          : <>
            {load.kind === 'loading' && <p className="hqSdNote" role="status"><Loader2 size={14} className="hqSdSpin" aria-hidden="true" /> {t('hqSd.loading')}</p>}
            {load.kind === 'denied' && <p className="hqSdNote hqSdFail" role="alert">{t('hqSd.denied')}</p>}
            {load.kind === 'error' && <div className="hqSdNote hqSdFail" role="alert">
              <p>{t('hqSd.error', { detail: load.detail })}</p>
              <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void read(ticked ? [...ticked] : null, excluded, cap); }}>{t('hqSd.retry')}</button>
            </div>}
            {done ? <ResultView done={done} locale={locale} preview={preview} />
              : ready && <PlanView preview={preview} ticked={ticked} excluded={excluded} capText={capText} locale={locale} fetching={fetching}
                onTick={(id) => setTicked(flip(ticked, id))} onAll={(all) => setTicked(new Set(all ? preview.buildings.map((b) => b.id) : []))}
                onRoom={(id) => setExcluded(flip(excluded, id))} onCap={setCapText} onKeepEmpty={keepEmpty} />}
            {!done && actionError && <p className="hqSdNote hqSdFail" role="alert">{actionError}</p>}
            {run && <p className="hqSdNote" role="status"><Loader2 size={14} className="hqSdSpin" aria-hidden="true" /> {t('hqSd.working', { n: run.index, total: run.total })}</p>}
            {done?.failure && <div className="hqSdNote hqSdFail" role="alert">
              <p>{t('hqSd.confirmFailed', { detail: done.failure.detail })}</p>
              {done.failure.stale && <p>{t('hqSd.stale')}</p>}
            </div>}
          </>}
      </div>
      {tab === 'plan' && (done ? <footer className="hqSdFoot">
        <p className="hqSdNote">{t('hqSd.done.next')}</p>
        <div className="hqSdActions">
          <button type="button" className="hqSdPrimary" onClick={() => setTab('agents')} data-autofocus>{t('hqSd.done.turnOn')}</button>
          {(done.failure || done.results.some((r) => r.status === 'failed')) && <button type="button" onClick={reviewAgain}>{t('hqSd.done.again')}</button>}
          <button type="button" onClick={onClose}>{t('hqSd.close')}</button>
        </div></footer>
        : ready && <footer className="hqSdFoot">
          <p className="hqSdNote"><ShieldCheck size={13} aria-hidden="true" /> {preview.can_confirm ? t('hqSd.clickOnly') : t('hqSd.ownersOnly')}</p>
          <div className="hqSdActions">
            <button type="button" className="hqSdPrimary" onClick={() => void confirm()} disabled={!canConfirm(preview, busy, fetching)} data-autofocus>
              {busy ? t('hqSd.working', { n: run?.index ?? 0, total: run?.total ?? 0 }) : preview.batches.length ? t('hqSd.confirm', { count: preview.totals.plan + preview.totals.resume }) : t('hqSd.nothing')}</button>
            <button type="button" onClick={onClose} disabled={busy}>{t('hqSd.cancel')}</button>
          </div>
        </footer>)}
    </div>
  </div>, document.body);
}

function Stat({ state, value }: { state: string; value: number }) {
  return <li data-zero={value === 0}><strong>{value}</strong><span>{stateText(state)}</span></li>;
}

function PlanView({ preview, ticked, excluded, capText, locale, fetching, onTick, onAll, onRoom, onCap, onKeepEmpty }: {
  preview: StaffDesksPreview; ticked: Set<string>; excluded: Set<string>; capText: string; locale: 'en' | 'ar'; fetching: boolean;
  onTick: (id: string) => void; onAll: (all: boolean) => void; onRoom: (id: string) => void; onCap: (text: string) => void;
  onKeepEmpty: (deskId: string, keep: boolean) => Promise<void>;
}) {
  const { totals, limits, cost } = preview;
  const roleName = (id: string) => { const r = preview.roles.find((x) => x.id === id); return r ? r.name[locale] : id; };
  const budget = cost.workspace_budget;
  const capId = useId();
  return <>
    <p className="hqSdNote">{t('hqSd.intro')}</p>
    <ul className="hqSdRules">
      <li>{t('hqSd.rule.paused')}</li><li>{t('hqSd.rule.occupied')}</li><li>{t('hqSd.rule.privilege')}</li>
    </ul>
    <section className="hqSdSection" aria-label={t('hqSd.totals')} aria-live="polite" aria-busy={fetching}>
      <h3>{t('hqSd.totals')}</h3>
      {ticked.size === 0 ? <p className="hqSdNote"><CircleSlash size={14} aria-hidden="true" /> {t('hqSd.noneSelected')}</p>
        : <ul className="hqSdStats">
          <Stat state="plan" value={totals.plan} />{totals.resume > 0 && <Stat state="resume" value={totals.resume} />}
          <Stat state="occupied" value={totals.occupied} /><Stat state="keep_empty" value={totals.keep_empty} />
          <Stat state="excluded" value={totals.excluded} /><Stat state="no_role" value={totals.no_role} />
        </ul>}
      <p className="hqSdNote">{t('hqSd.limit.line', { used: limits.agents, max: limits.max_agents, free: limits.free_slots })}</p>
      {totals.over_limit > 0 && <p className="hqSdWarn" role="status"><TriangleAlert size={14} aria-hidden="true" /> {t('hqSd.limit.warn', { count: totals.over_limit, max: limits.max_agents })}</p>}
      {totals.over_cap > 0 && <p className="hqSdWarn" role="status"><TriangleAlert size={14} aria-hidden="true" /> {t('hqSd.limit.capWarn', { count: totals.over_cap, cap: limits.run_cap ?? 0 })}</p>}
      {totals.no_role > 0 && <p className="hqSdWarn" role="status"><TriangleAlert size={14} aria-hidden="true" /> {t('hqSd.noRoleWarn', { count: totals.no_role })}</p>}
      <div className="hqSdCap"><label htmlFor={capId}>{t('hqSd.cap.label')}</label>
        <input id={capId} type="number" min={0} max={200} inputMode="numeric" value={capText} placeholder={t('hqSd.cap.none')} onChange={(e) => onCap(e.target.value)} aria-describedby={`${capId}-h`} />
        <small id={`${capId}-h`}>{t('hqSd.cap.help')}</small></div>
    </section>
    <section className="hqSdSection" aria-label={t('hqSd.cost.title')}>
      <h3>{t('hqSd.cost.title')}</h3>
      <p><strong>{t('hqSd.cost.line', { agents: cost.agents, total: money(cost.max_monthly_usd, locale) })}</strong></p>
      <p className="hqSdNote">{t('hqSd.cost.note')}</p>
      <p className="hqSdNote" data-tone={cost.within_budget === false ? 'warn' : undefined}>
        {!budget.visible ? t('hqSd.cost.hidden') : budget.monthly_cost_limit_usd == null ? t('hqSd.cost.noBudget')
          : <>{t('hqSd.cost.budget', { limit: money(budget.monthly_cost_limit_usd, locale) })} {cost.within_budget ? t('hqSd.cost.within') : t('hqSd.cost.over')}</>}
      </p>
    </section>
    {preview.roles.length > 0 && <section className="hqSdSection" aria-label={t('hqSd.roles.title')}>
      <h3>{t('hqSd.roles.title')}</h3>
      <ul className="hqSdRoles">
        {preview.roles.map((r) => <li key={r.id}>
          <span>{t('hqSd.roles.row', { count: r.count, name: r.name[locale], cap: money(r.monthly_cost_limit_usd, locale) })}</span>
          {r.limited && <span className="hqSdBadge" data-tone="warn">{t('hqSd.limited')}</span>}
        </li>)}
      </ul>
      {preview.capability_gaps.length > 0 && <>
        <h4>{t('hqSd.gaps.title')}</h4>
        <p className="hqSdNote">{t('hqSd.gaps.note')}</p>
        <ul className="hqSdGaps">{preview.capability_gaps.map((g) => <li key={`${g.template_id}:${g.id}`}>{t('hqSd.gaps.row', { role: roleName(g.template_id), gap: locale === 'ar' ? g.ar : g.en })}</li>)}</ul>
      </>}
    </section>}
    <section className="hqSdSection" aria-label={t('hqSd.pick.title')}>
      <div className="hqSdPickHead">
        <h3>{t('hqSd.pick.title')}</h3>
        <span className="hqSdCount" role="status">{t('hqSd.pick.count', { selected: ticked.size, total: preview.buildings.length })}</span>
        <button type="button" onClick={() => onAll(true)} disabled={ticked.size === preview.buildings.length}>{t('hqSd.selectAll')}</button>
        <button type="button" onClick={() => onAll(false)} disabled={ticked.size === 0}>{t('hqSd.selectNone')}</button>
      </div>
      <ul className="hqSdBuildings">
        {preview.buildings.map((b) => <BuildingRow key={b.id} b={b} locale={locale} checked={ticked.has(b.id)} excluded={excluded}
          roleName={roleName} onFlip={() => onTick(b.id)} onRoom={onRoom} />)}
      </ul>
    </section>
    <DeskList preview={preview} locale={locale} onKeepEmpty={onKeepEmpty} />
  </>;
}

function BuildingRow({ b, locale, checked, excluded, roleName, onFlip, onRoom }: {
  b: StaffBuilding; locale: 'en' | 'ar'; checked: boolean; excluded: Set<string>; roleName: (id: string) => string; onFlip: () => void; onRoom: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const boxId = useId();
  const detailId = useId();
  const name = buildingName(b, locale);
  return <li className="hqSdBuilding" data-selected={checked}>
    <div className="hqSdRow">
      <input id={boxId} type="checkbox" checked={checked} onChange={onFlip} />
      <label htmlFor={boxId}><strong>{name}</strong>
        <small>{t('hqSd.row.counts', { plan: b.counts.plan + b.counts.resume, occupied: b.counts.occupied, total: b.counts.total })}</small></label>
      <button type="button" className="hqSdToggle" aria-expanded={open} aria-controls={detailId}
        aria-label={`${open ? t('hqSd.pick.hideRooms') : t('hqSd.pick.rooms')}: ${name}`} onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}</button>
    </div>
    {open && <ul id={detailId} className="hqSdRooms" aria-label={t('hqSd.pick.roomsOf', { name })}>
      {b.rooms.filter((r) => r.desks.length > 0 || excluded.has(r.id)).map((r) => <RoomLine key={r.id} r={r} locale={locale} off={excluded.has(r.id)} roleName={roleName} onFlip={() => onRoom(r.id)} />)}
    </ul>}
  </li>;
}

function RoomLine({ r, locale, off, roleName, onFlip }: { r: StaffRoom; locale: 'en' | 'ar'; off: boolean; roleName: (id: string) => string; onFlip: () => void }) {
  const id = useId();
  const name = nameOf(r.name, r.name_ar, locale);
  return <li className="hqSdRoom">
    <input id={id} type="checkbox" checked={!off} onChange={onFlip} />
    <label htmlFor={id}>
      <span>{name}</span>
      <small>{r.template_id ? t('hqSd.room.role', { role: roleName(r.template_id) }) : t('hqSd.room.noRole')}
        {' · '}{t('hqSd.room.desks', { plan: r.counts.plan + r.counts.resume, total: r.counts.total })}</small>
    </label>
  </li>;
}

function DeskList({ preview, locale, onKeepEmpty }: { preview: StaffDesksPreview; locale: 'en' | 'ar'; onKeepEmpty: (deskId: string, keep: boolean) => Promise<void> }) {
  const [query, setQuery] = useState('');
  const [state, setState] = useState('all');
  const [page, setPage] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const searchId = useId();
  const filterId = useId();
  const all = useMemo(() => flatten(preview), [preview]);
  const roleName = (id: string) => preview.roles.find((r) => r.id === id)?.name[locale] ?? id;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter((f) => (state === 'all' || f.desk.state === state) && (!q
      || `${f.room.name} ${f.room.name_ar ?? ''} ${f.desk.label} ${f.desk.agent?.name ?? ''} ${f.buildingName}`.toLowerCase().includes(q)));
  }, [all, query, state]);
  const view = pageOf(shown, page, PAGE);
  useEffect(() => { setPage(0); }, [query, state]);
  const act = async (id: string, keep: boolean) => { setPending(id); try { await onKeepEmpty(id, keep); } finally { setPending(null); } };
  return <section className="hqSdSection" aria-label={t('hqSd.desks.title')}>
    <div className="hqSdPickHead">
      <h3>{t('hqSd.desks.title')}</h3>
      <span className="hqSdCount" role="status">{t('hqSd.desks.count', { count: shown.length })}</span>
    </div>
    <div className="hqSdFilters">
      <label htmlFor={searchId}>{t('hqSd.desks.search')}</label>
      <input id={searchId} type="search" value={query} onChange={(e) => setQuery(e.target.value)} />
      <label htmlFor={filterId}>{t('hqSd.desks.filter')}</label>
      <select id={filterId} value={state} onChange={(e) => setState(e.target.value)}>
        <option value="all">{t('hqSd.desks.all')}</option>
        {DESK_STATES.map((s) => <option key={s} value={s}>{stateText(s)}</option>)}
      </select>
    </div>
    {view.items.length === 0 ? <p className="hqSdNote">{t('hqSd.desks.empty')}</p>
      : <ul className="hqSdDesks">
        {view.items.map(({ buildingName: bn, room, desk }) => {
          const flipable = desk.state === 'plan' || desk.state === 'resume' || desk.state === 'over_limit' || desk.state === 'over_cap';
          return <li key={desk.id} data-state={desk.state}>
            <div>
              <strong>{desk.agent?.name ?? `${nameOf(room.name, room.name_ar, locale)} ${desk.number}`}</strong>
              <small>{bn}, {t('hqSd.desks.where', { floor: room.floor_level, room: nameOf(room.name, room.name_ar, locale), desk: deskLabel(desk.label, locale) })}
                {desk.agent && <> {' · '}{roleName(desk.agent.template_id)}</>}</small>
            </div>
            <span className="hqSdBadge" data-state={desk.state}>{stateText(desk.state)}</span>
            {(flipable || desk.state === 'keep_empty') && preview.can_confirm && <button type="button" disabled={pending === desk.id}
              aria-label={`${desk.state === 'keep_empty' ? t('hqSd.staffAgain') : t('hqSd.keepEmpty')}: ${deskLabel(desk.label, locale)}, ${nameOf(room.name, room.name_ar, locale)}`}
              onClick={() => void act(desk.id, desk.state !== 'keep_empty')}>{desk.state === 'keep_empty' ? t('hqSd.staffAgain') : t('hqSd.keepEmpty')}</button>}
          </li>;
        })}
      </ul>}
    {view.pages > 1 && <nav className="hqSdPager" aria-label={t('hqSd.desks.pager')}>
      <button type="button" onClick={() => setPage(view.page - 1)} disabled={view.page === 0}>{t('hqSd.desks.prev')}</button>
      <span role="status">{t('hqSd.desks.page', { page: view.page + 1, pages: view.pages })}</span>
      <button type="button" onClick={() => setPage(view.page + 1)} disabled={view.page >= view.pages - 1}>{t('hqSd.desks.next')}</button>
    </nav>}
  </section>;
}

function ResultView({ done, locale, preview }: { done: Done; locale: 'en' | 'ar'; preview: StaffDesksPreview | null }) {
  const [filter, setFilter] = useState<'all' | StaffDeskResult['status']>('all');
  const [page, setPage] = useState(0);
  const filterId = useId();
  const counts = summarize(done.results);
  const tone = counts.failed || done.failure ? 'warn' : 'ok';
  const shown = done.results.filter((r) => filter === 'all' || r.status === filter);
  const view = pageOf(shown, page, PAGE);
  const names = new Map((preview?.buildings ?? []).map((b) => [b.id, buildingName(b, locale)]));
  useEffect(() => { setPage(0); }, [filter]);
  return <div className="hqSdDone" role="status">
    <p data-tone={tone}>{tone === 'ok' ? <CheckCircle2 size={15} aria-hidden="true" /> : <TriangleAlert size={15} aria-hidden="true" />}{' '}
      {t('hqSd.done.summary', { created: counts.created, seated: counts.seated_existing, skipped: counts.skipped, failed: counts.failed })}</p>
    <h3>{t('hqSd.done.buildings')}</h3>
    <ul className="hqSdResults">{done.buildings.map((b) => <li key={b.id} data-status={b.outcome}>
      <strong>{names.get(b.id) ?? b.id}</strong><span>{t(`hqSd.done.outcome.${b.outcome}` as MessageKey)}</span></li>)}</ul>
    <h3>{t('hqSd.done.desks')}</h3>
    <div className="hqSdFilters">
      <label htmlFor={filterId}>{t('hqSd.desks.filter')}</label>
      <select id={filterId} value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}>
        <option value="all">{t('hqSd.desks.all')}</option>
        {(['created', 'seated_existing', 'skipped', 'failed'] as const).map((s) => <option key={s} value={s}>{t(`hqSd.done.status.${s}` as MessageKey)}</option>)}
      </select>
    </div>
    {view.items.length === 0 ? <p className="hqSdNote">{t('hqSd.desks.empty')}</p>
      : <ul className="hqSdDesks">{view.items.map((r) => <li key={r.desk_id} data-state={r.status}>
        <div><strong>{r.agent_name}</strong>{r.reason && <small>{t(`hqSd.done.reason.${r.reason}` as MessageKey)}</small>}</div>
        <span className="hqSdBadge" data-state={r.status}>{t(`hqSd.done.status.${r.status}` as MessageKey)}</span>
      </li>)}</ul>}
    {view.pages > 1 && <nav className="hqSdPager" aria-label={t('hqSd.desks.pager')}>
      <button type="button" onClick={() => setPage(view.page - 1)} disabled={view.page === 0}>{t('hqSd.desks.prev')}</button>
      <span role="status">{t('hqSd.desks.page', { page: view.page + 1, pages: view.pages })}</span>
      <button type="button" onClick={() => setPage(view.page + 1)} disabled={view.page >= view.pages - 1}>{t('hqSd.desks.next')}</button>
    </nav>}
  </div>;
}
