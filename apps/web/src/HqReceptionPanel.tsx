/**
 * Reception: the visitor register of the open workspace (owner decision O4, docs/ANUM_MINE_HQ_RECEPTION.md). A side drawer
 * from 761 px and a bottom sheet below, like the office history. Today's visitors by default (expected today, checked in
 * today, or still in the building), every status with counts, search, paging, and for owners: add (also a walk-in), edit,
 * check in, check out, cancel and delete, each a click the server checks again. A lazy chunk: the office never loads it
 * until the panel opens.
 */
import { LogIn, LogOut, Lock, Pencil, Plus, RefreshCcw, Search, Trash2, X, XCircle } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { Visit, VisitPage, VisitStatus } from '@anum/contracts';
import * as api from './lib/api';
import { getMembers } from './lib/adminApi';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import {
  LIMITS, PAGE_SIZE, VISIT_STATUSES, actionsFor, createBody, draftFrom, draftProblems, emptyDraft, mergePage, newVisitId, updateBody, visitQuery,
  type Draft, type DraftField, type DraftProblem, type Scope,
} from './lib/hqReception';
import { createReceptionClient } from './lib/hqReceptionApi';
import { getLocale, t, type MessageKey } from './lib/i18n';
import { useLocale } from './useLocale';
import './hqReception.css';

const client = createReceptionClient(api.apiRequest, api.apiRequestVoid);

export interface ReceptionRoomOption { id: string; buildingId: string; kind: string; label: string; building: string }
export interface HqReceptionPanelProps {
  /** The reception room the panel was opened from: the add form starts with it. */
  roomId: string | null;
  rooms: readonly ReceptionRoomOption[];
  agents: readonly { id: string; label: string }[];
  /** After any saved change, so the 3D reception rooms read the new count. */
  onChanged: () => void;
  onClose: () => void;
}

type Status = 'loading' | 'ready' | 'error' | 'denied' | 'unavailable';
type Form = { mode: 'add'; id: string; draft: Draft } | { mode: 'edit'; visit: Visit; draft: Draft };
type Note = { text: string; error: boolean };

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])';
const STATUS_ICON: Record<VisitStatus, string> = { expected: 'expected', checked_in: 'in', checked_out: 'out', cancelled: 'cancelled' };

function formatTime(iso: string | null, now: Date): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  const locale = getLocale() === 'ar' ? 'ar-u-nu-latn' : 'en-GB';
  const sameDay = d.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat(locale, sameDay ? { hour: '2-digit', minute: '2-digit', hour12: false } : { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
}

export function HqReceptionPanel(props: HqReceptionPanelProps) {
  useLocale();
  const { rooms, agents } = props;
  const [scope, setScope] = useState<Scope>('today');
  const [statusFilter, setStatusFilter] = useState<VisitStatus | null>(null);
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState<VisitPage | null>(null);
  const [items, setItems] = useState<Visit[]>([]);
  const [status, setStatus] = useState<Status>('loading');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [problems, setProblems] = useState<Partial<Record<DraftField, DraftProblem>>>({});
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [members, setMembers] = useState<{ id: string; label: string }[] | null>(null);
  const [membersOff, setMembersOff] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(typeof document === 'undefined' ? null : document.activeElement);
  const generation = useRef(0);
  const formId = useId();

  // The search waits for a short pause in typing, so a request is not sent per key.
  useEffect(() => { const timer = window.setTimeout(() => setQ(text.trim()), 300); return () => window.clearTimeout(timer); }, [text]);

  const load = useCallback(async (cursor: string | null) => {
    const id = cursor ? generation.current : ++generation.current;
    if (cursor) setBusy(true); else { setStatus('loading'); setError(''); }
    try {
      const next = await client.page(visitQuery({ q, status: statusFilter, scope, cursor, now: new Date(), limit: PAGE_SIZE }));
      if (id !== generation.current) return;
      setPage(next);
      setItems((current) => (cursor ? mergePage(current, next.items) : next.items));
      setStatus('ready');
    } catch (failure) {
      if (id !== generation.current) return;
      if (isPermissionDenied(failure)) setStatus('denied');
      else if (failure instanceof ApiError && failure.status === 503) setStatus('unavailable');
      else if (cursor) setNote({ text: t('hqRec.error', { detail: describeError(failure) }), error: true });
      else { setError(describeError(failure)); setStatus('error'); }
    } finally {
      if (id === generation.current) setBusy(false);
    }
  }, [q, statusFilter, scope]);
  useEffect(() => { void load(null); return () => { generation.current += 1; }; }, [load]);

  const canManage = page?.can_manage === true;
  // People can host a visit: the member list is read for owners only (it names hosts in the list and the form).
  useEffect(() => {
    if (!canManage || members !== null || membersOff) return;
    let live = true;
    getMembers().then((list) => { if (live) setMembers(list.filter((m) => m.active).map((m) => ({ id: m.userId, label: m.email || m.userId }))); })
      .catch(() => { if (live) setMembersOff(true); });
    return () => { live = false; };
  }, [canManage, members, membersOff]);

  const roomById = useMemo(() => new Map(rooms.map((r) => [r.id, r] as const)), [rooms]);
  const buildingName = useMemo(() => new Map(rooms.map((r) => [r.buildingId, r.building] as const)), [rooms]);
  const hostName = useMemo(() => new Map<string, string>([...(members ?? []).map((m) => [`user:${m.id}`, m.label] as const), ...agents.map((a) => [`agent:${a.id}`, a.label] as const)]), [members, agents]);
  // Reception rooms first in the picker, then every other room, each with its building.
  const roomOptions = useMemo(() => [...rooms].sort((a, b) => Number(b.kind === 'reception') - Number(a.kind === 'reception')), [rooms]);

  const close = useCallback(() => {
    props.onClose();
    const target = opener.current;
    if (target instanceof HTMLElement && document.contains(target)) window.setTimeout(() => target.focus(), 0);
  }, [props]);
  useEffect(() => { root.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus(); }, []);
  const escape = useRef<() => void>(() => undefined);
  escape.current = () => { if (confirm) setConfirm(null); else if (form) setForm(null); else close(); };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.stopPropagation();
      escape.current();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== 'Tab' || !root.current) return;
    const nodes = [...root.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null);
    if (nodes.length === 0) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  const failed = (failure: unknown) => {
    if (failure instanceof ApiError && failure.status === 409) { setNote({ text: t('hqRec.stale'), error: true }); void load(null); return; }
    setNote({ text: isPermissionDenied(failure) ? t('hqRec.ownerOnly') : t('hqRec.failed', { detail: describeError(failure) }), error: true });
  };
  const saved = (key: MessageKey, name: string) => { setNote({ text: t(key, { name }), error: false }); props.onChanged(); void load(null); };

  const run = async (v: Visit, action: 'check-in' | 'check-out' | 'cancel' | 'delete') => {
    setActing(v.id);
    setNote(null);
    try {
      if (action === 'delete') { await client.remove(v.id, v.revision); setConfirm(null); saved('hqRec.done.deleted', v.visitor_name); }
      else {
        await client.move(v.id, action, v.revision);
        saved(action === 'check-in' ? 'hqRec.done.checkedIn' : action === 'check-out' ? 'hqRec.done.checkedOut' : 'hqRec.done.cancelled', v.visitor_name);
      }
    } catch (failure) { failed(failure); } finally { setActing(null); }
  };

  const openAdd = () => { setNote(null); setProblems({}); setForm({ mode: 'add', id: newVisitId(), draft: emptyDraft(new Date(), props.roomId && roomById.has(props.roomId) ? props.roomId : '') }); };
  const openEdit = (v: Visit) => { setNote(null); setProblems({}); setForm({ mode: 'edit', visit: v, draft: draftFrom(v) }); };
  const setDraft = (patch: Partial<Draft>) => setForm((f) => (f ? { ...f, draft: { ...f.draft, ...patch } } as Form : f));
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form) return;
    const found = draftProblems(form.draft, new Date());
    setProblems(found);
    if (Object.keys(found).length > 0) { root.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(); return; }
    setSaving(true);
    setNote(null);
    try {
      if (form.mode === 'add') { const v = await client.create(createBody(form.draft, form.id)); setForm(null); saved('hqRec.done.created', v.visitor_name); }
      else { const v = await client.update(form.visit.id, updateBody(form.draft, form.visit.revision)); setForm(null); saved('hqRec.done.updated', v.visitor_name); }
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409 && form.mode === 'edit') setForm(null);
      failed(failure);
    } finally { setSaving(false); }
  };

  const now = new Date();
  const counts = page?.counts;
  const allCount = counts ? VISIT_STATUSES.reduce((n, s) => n + counts[s], 0) : 0;
  const filtered = statusFilter !== null || q !== '';
  const clear = () => { setText(''); setQ(''); setStatusFilter(null); };
  const problemText = (f: DraftField) => (problems[f] ? t(`hqRec.form.${problems[f]}` as MessageKey) : null);

  return (
    <>
      <div className="hqRecScrim" onClick={close} aria-hidden="true" />
      <div ref={root} className="hqRec" role="dialog" aria-modal="true" aria-labelledby="hqRecTitle" onKeyDown={onKeyDown} data-testid="hq-reception">
        <header className="hqRecHead">
          <div>
            <h2 id="hqRecTitle">{t('hqRec.title')}</h2>
            <p className="hqRecHint">{t('hqRec.intro')}</p>
          </div>
          <button type="button" className="hqIconButton" data-autofocus onClick={close} aria-label={t('hqRec.close')}><X aria-hidden="true" /></button>
        </header>
        <div className="hqRecBody">
          {status !== 'denied' && status !== 'unavailable' && (
            <div className="hqRecTools">
              <div className="hqRecSearchRow">
                <label className="hqRecSearch"><Search aria-hidden="true" /><span className="visuallyHidden">{t('hqRec.search')}</span>
                  <input type="search" value={text} maxLength={100} placeholder={t('hqRec.searchPlaceholder')} onChange={(e) => setText(e.target.value)} /></label>
                <label className="hqRecScope"><span className="visuallyHidden">{t('hqRec.scope')}</span>
                  <select value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
                    <option value="today">{t('hqRec.scope.today')}</option>
                    <option value="all">{t('hqRec.scope.all')}</option>
                  </select></label>
                {canManage && !form && <button type="button" className="hqPrimary" onClick={openAdd} data-testid="reception-add"><Plus aria-hidden="true" />{t('hqRec.add')}</button>}
              </div>
              <div className="hqRecFilters" role="group" aria-label={t('hqRec.filter')}>
                <button type="button" aria-pressed={statusFilter === null} onClick={() => setStatusFilter(null)}>{t('hqRec.filterCount', { label: t('hqRec.filter.all'), count: allCount })}</button>
                {VISIT_STATUSES.map((s) => (
                  <button key={s} type="button" aria-pressed={statusFilter === s} data-status={s} onClick={() => setStatusFilter(s)}>
                    {t('hqRec.filterCount', { label: t(`hqRec.status.${s}` as MessageKey), count: counts?.[s] ?? 0 })}</button>
                ))}
              </div>
              {page && !canManage && <p className="hqRecHint" role="note" data-testid="reception-owner-only">{t('hqRec.ownerOnly')}</p>}
            </div>
          )}

          {form && (
            <form className="hqRecForm" onSubmit={(e) => void submit(e)} aria-labelledby={`${formId}-title`} noValidate data-testid="reception-form">
              <h3 id={`${formId}-title`}>{form.mode === 'add' ? t('hqRec.form.addTitle') : t('hqRec.form.editTitle', { name: form.visit.visitor_name })}</h3>
              <Field id={`${formId}-name`} label={t('hqRec.form.name')} problem={problemText('name')}>
                <input id={`${formId}-name`} dir="auto" required maxLength={LIMITS.name} value={form.draft.name} aria-invalid={problems.name ? true : undefined} aria-describedby={problems.name ? `${formId}-name-p` : undefined} onChange={(e) => setDraft({ name: e.target.value })} />
              </Field>
              <Field id={`${formId}-company`} label={t('hqRec.form.company')} problem={problemText('company')}>
                <input id={`${formId}-company`} dir="auto" maxLength={LIMITS.company} value={form.draft.company} aria-invalid={problems.company ? true : undefined} onChange={(e) => setDraft({ company: e.target.value })} />
              </Field>
              <Field id={`${formId}-purpose`} label={t('hqRec.form.purpose')} problem={problemText('purpose')}>
                <input id={`${formId}-purpose`} dir="auto" required maxLength={LIMITS.purpose} value={form.draft.purpose} aria-invalid={problems.purpose ? true : undefined} onChange={(e) => setDraft({ purpose: e.target.value })} />
              </Field>
              <Field id={`${formId}-expected`} label={t('hqRec.form.expected')} problem={problemText('expected')}>
                <input id={`${formId}-expected`} type="datetime-local" required value={form.draft.expected} aria-invalid={problems.expected ? true : undefined} onChange={(e) => setDraft({ expected: e.target.value })} />
              </Field>
              <Field id={`${formId}-host`} label={t('hqRec.form.host')} problem={null}>
                <select id={`${formId}-host`} value={form.draft.host} onChange={(e) => setDraft({ host: e.target.value })}>
                  <option value="">{t('hqRec.form.host.none')}</option>
                  {members && members.length > 0 && <optgroup label={t('hqRec.form.host.members')}>{members.map((m) => <option key={m.id} value={`user:${m.id}`}>{m.label}</option>)}</optgroup>}
                  {agents.length > 0 && <optgroup label={t('hqRec.form.host.agents')}>{agents.map((a) => <option key={a.id} value={`agent:${a.id}`}>{a.label}</option>)}</optgroup>}
                  {form.draft.host && !hostName.has(form.draft.host) && <option value={form.draft.host}>{t('hqRec.row.unknownHost')}</option>}
                </select>
              </Field>
              {membersOff && <p className="hqRecHint">{t('hqRec.form.host.membersOff')}</p>}
              <Field id={`${formId}-room`} label={t('hqRec.form.room')} problem={null}>
                <select id={`${formId}-room`} value={form.draft.room} onChange={(e) => setDraft({ room: e.target.value })}>
                  <option value="">{t('hqRec.form.room.none')}</option>
                  {roomOptions.map((r) => <option key={r.id} value={r.id}>{t('hqRec.form.room.option', { room: r.label, building: r.building })}</option>)}
                  {form.draft.room && !roomById.has(form.draft.room) && <option value={form.draft.room}>{t('hqRec.row.unknownRoom')}</option>}
                </select>
              </Field>
              {form.mode === 'add' && (
                <label className="hqRecCheck"><input type="checkbox" checked={form.draft.arrived} onChange={(e) => setDraft({ arrived: e.target.checked })} />{t('hqRec.form.arrived')}</label>
              )}
              {Object.keys(problems).length > 0 && <p className="hqRecError" role="alert">{t('hqRec.form.fix')}</p>}
              <div className="hqRecFormActions">
                <button type="submit" className="hqPrimary" disabled={saving}>{saving ? t('hqRec.working') : form.mode === 'add' ? t('hqRec.form.save') : t('hqRec.form.saveEdit')}</button>
                <button type="button" className="hqGhost" onClick={() => setForm(null)} disabled={saving}>{t('hqRec.form.cancel')}</button>
              </div>
            </form>
          )}

          {note && <p className={note.error ? 'hqRecError' : 'hqRecDone'} role={note.error ? 'alert' : 'status'} data-testid="reception-note">{note.text}</p>}

          <div aria-live="polite" aria-busy={status === 'loading' || busy}>
            {status === 'loading' && <p className="hqRecLoading" role="status"><span className="hqSpinner" aria-hidden="true" />{t('hqRec.loading')}</p>}
            {status === 'denied' && <div className="hqRecBox" role="alert" data-testid="reception-denied"><Lock aria-hidden="true" /><p>{t('hqRec.denied')}</p></div>}
            {status === 'unavailable' && <div className="hqRecBox" role="alert"><Lock aria-hidden="true" /><p>{t('hqRec.unavailable')}</p></div>}
            {status === 'error' && (
              <div className="hqRecBox" role="alert" data-testid="reception-error"><p>{t('hqRec.error', { detail: error })}</p>
                <button type="button" className="hqGhost" onClick={() => void load(null)}><RefreshCcw aria-hidden="true" />{t('hqRec.retry')}</button></div>
            )}
          </div>

          {status === 'ready' && page && (
            <>
              {items.length === 0 ? (
                <div className="hqRecEmpty" data-testid="reception-empty">
                  <h3>{t('hqRec.empty.title')}</h3>
                  <p>{filtered ? t('hqRec.empty.filtered') : scope === 'today' ? t('hqRec.empty.today') : t('hqRec.empty.all')}</p>
                  {filtered ? <button type="button" className="hqGhost" onClick={clear}>{t('hqRec.empty.clear')}</button>
                    : canManage && !form ? <button type="button" className="hqPrimary" onClick={openAdd}><Plus aria-hidden="true" />{t('hqRec.add')}</button>
                      : !canManage ? <p className="hqRecHint">{t('hqRec.ownerOnly')}</p> : null}
                </div>
              ) : (
                <>
                  <p className="hqRecCount" role="status">{t('hqRec.count', { shown: items.length, count: Math.max(page.total, items.length) })}</p>
                  <ul className="hqRecList" data-testid="reception-list">
                    {items.map((v) => {
                      const can = actionsFor(v, canManage);
                      const when = v.status === 'checked_in' ? t('hqRec.row.in', { time: formatTime(v.checked_in_at, now) })
                        : v.status === 'checked_out' ? t('hqRec.row.out', { time: formatTime(v.checked_out_at, now) })
                          : v.status === 'cancelled' ? t('hqRec.row.cancelled', { time: formatTime(v.cancelled_at, now) })
                            : t('hqRec.row.expected', { time: formatTime(v.expected_at, now) });
                      const host = v.host ? hostName.get(`${v.host.kind}:${v.host.id}`) ?? t('hqRec.row.unknownHost') : null;
                      const place = v.room_id ? roomById.get(v.room_id)?.label ?? t('hqRec.row.unknownRoom') : v.building_id ? buildingName.get(v.building_id) ?? null : null;
                      return (
                        <li key={v.id} className="hqRecRow" data-status={v.status} data-visit-id={v.id}>
                          <div className="hqRecMain">
                            <strong><bdi>{v.visitor_name}</bdi></strong>
                            {v.company && <small><bdi>{v.company}</bdi></small>}
                            <small><bdi>{v.purpose}</bdi></small>
                            <small>{when}{host && <>{' · '}<span dir="auto">{t('hqRec.row.host', { name: host })}</span></>}{place && <>{' · '}<span dir="auto">{t('hqRec.row.room', { name: place })}</span></>}</small>
                          </div>
                          <span className="hqRecChip" data-status={STATUS_ICON[v.status]}>{t(`hqRec.status.${v.status}` as MessageKey)}</span>
                          {canManage && (
                            <div className="hqRecActions" role="group" aria-label={t('hqRec.actionsFor', { name: v.visitor_name })}>
                              {confirm === v.id ? (
                                <div className="hqRecConfirm" role="alertdialog" aria-label={t('hqRec.confirmDelete', { name: v.visitor_name })}>
                                  <p>{t('hqRec.confirmDelete', { name: v.visitor_name })}</p>
                                  <button type="button" className="hqRecDanger" disabled={acting === v.id} onClick={() => void run(v, 'delete')}>{t('hqRec.confirmDelete.yes')}</button>
                                  <button type="button" className="hqGhost" onClick={() => setConfirm(null)}>{t('hqRec.confirmDelete.no')}</button>
                                </div>
                              ) : (
                                <>
                                  {can.checkIn && <button type="button" className="hqPrimary" disabled={acting === v.id} onClick={() => void run(v, 'check-in')}><LogIn className="flipRtl" aria-hidden="true" />{t('hqRec.checkIn')}</button>}
                                  {can.checkOut && <button type="button" className="hqPrimary" disabled={acting === v.id} onClick={() => void run(v, 'check-out')}><LogOut className="flipRtl" aria-hidden="true" />{t('hqRec.checkOut')}</button>}
                                  {can.edit && <button type="button" className="hqGhost" disabled={acting === v.id} onClick={() => openEdit(v)}><Pencil aria-hidden="true" />{t('hqRec.edit')}</button>}
                                  {can.cancel && <button type="button" className="hqGhost" disabled={acting === v.id} onClick={() => void run(v, 'cancel')}><XCircle aria-hidden="true" />{t('hqRec.cancel')}</button>}
                                  {can.remove && <button type="button" className="hqGhost" disabled={acting === v.id} onClick={() => setConfirm(v.id)}><Trash2 aria-hidden="true" />{t('hqRec.delete')}</button>}
                                </>
                              )}
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                  {page.next_cursor ? (
                    <button type="button" className="hqGhost hqRecMore" onClick={() => void load(page.next_cursor)} disabled={busy}>{busy ? t('hqRec.loadingMore') : t('hqRec.more')}</button>
                  ) : <p className="hqRecHint">{t('hqRec.end')}</p>}
                </>
              )}
            </>
          )}
          <p className="hqRecNotice">{t('hqRec.notice')}</p>
        </div>
      </div>
    </>
  );
}

function Field(props: { id: string; label: string; problem: string | null; children: React.ReactNode }) {
  return (
    <div className="hqRecField">
      <label htmlFor={props.id}>{props.label}</label>
      {props.children}
      {props.problem && <small id={`${props.id}-p`} className="hqRecError">{props.problem}</small>}
    </div>
  );
}

export default HqReceptionPanel;
