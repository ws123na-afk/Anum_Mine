// Attendance panels besides Today and Reports (docs/attendance.md#web): the daily report editor and its list,
// corrections with a required reason, and Settings (profile, schedule, sites, location check in, sharing, iPhone).
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Archive, ArchiveRestore, FileText, History, MapPin, Paperclip, Plus, Search, Share2, Smartphone, Sparkles, Trash2, X } from 'lucide-react';
import type {
  AttendanceCorrection, AttendanceDailyReport, AttendanceEvent, AttendanceEventKind, AttendancePage, AttendanceProfile, AttendanceShares,
  AttendanceShift, AttendanceSite, AttendanceSiteKind, TeamPerson,
} from '@anum/contracts';
import * as api from './lib/attendanceApi';
import { uploadFile } from './lib/api';
import {
  ZONES, checkReason, checkShift, clock, draftFromReport, emptyReportDraft, isOvernight, kindLabel, localToIso, reportBody, shiftDay,
  siteName, weekdayLabel, type ReportDraft,
} from './lib/attendance';
import { ApiError, describeError } from './lib/errors';
import { formatDateTime, getLocale, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { Empty, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';

const PAGE = 20;
const KINDS: AttendanceEventKind[] = ['arrive', 'leave', 'break_start', 'break_end', 'remote_start', 'remote_end'];
const SITE_KINDS: AttendanceSiteKind[] = ['workplace', 'site', 'client', 'home'];
type Flash = { text: string; error?: boolean } | null;

// ---- Daily report --------------------------------------------------------------------------------

export function DailyPanel({ person, sites, today, readOnly }: { person: string | null; sites: AttendanceSite[]; today: string; readOnly: boolean }) {
  const [day, setDay] = useState(today);
  const [list, setList] = useState<Load<AttendancePage<AttendanceDailyReport>>>({ kind: 'loading' });
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const loadList = useCallback(async () => {
    setList({ kind: 'loading' });
    try { setList({ kind: 'ready', value: await api.listReports({ q: search || undefined, limit: PAGE, offset, person }) }); } catch (e) { setList(failed(e)); }
  }, [search, offset, person]);
  useEffect(() => { void loadList(); }, [loadList]);
  return <div className="attDaily">
    <ReportEditor key={`${day}:${person ?? ''}`} day={day} person={person} sites={sites} readOnly={readOnly} today={today} onDay={setDay} onSaved={() => void loadList()} />
    <section className="surface cmPanel" aria-labelledby="att-report-list">
      <h2 id="att-report-list">{t('att.daily.listTitle')}</h2>
      <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setSearch(q.trim()); }}>
        <label className="cmInline cmSearch"><span>{t('att.daily.search')}</span><input type="search" dir="auto" value={q} maxLength={120} onChange={(e) => setQ(e.target.value)} /></label>
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('att.daily.find')}</button>
      </form>
      {list.kind === 'loading' && <Notice busy>{t('att.daily.loading')}</Notice>}
      {list.kind === 'error' && <LoadError message={t('att.daily.error', { message: list.message })} retry={() => void loadList()} />}
      {list.kind === 'denied' && <Notice error>{t('att.daily.notShared')}</Notice>}
      {list.kind === 'ready' && (list.value.items.length === 0
        ? <Empty icon={<FileText aria-hidden="true" />} title={search ? t('att.daily.noMatch') : t('att.daily.emptyTitle')} text={search ? t('att.daily.noMatchText') : t('att.daily.emptyText')} />
        : <>
          <ul className="cmList">{list.value.items.map((r) => <li key={r.id}>
            <div><strong dir="auto">{r.project || t('att.daily.noProject')}</strong>
              <small>{t('att.daily.rowMeta', { day: r.day, done: r.tasks_done.length, pending: r.tasks_pending.length })}</small></div>
            <button type="button" className="secondary" aria-label={t('att.daily.openLabel', { day: r.day })} onClick={() => setDay(r.day)}>{t('att.daily.open')}</button>
          </li>)}</ul>
          <div className="actions">
            {offset > 0 && <button type="button" className="secondary" onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t('att.daily.newer')}</button>}
            {offset + PAGE < list.value.total && <button type="button" className="secondary" onClick={() => setOffset(offset + PAGE)}>{t('att.daily.older')}</button>}
          </div>
        </>)}
    </section>
  </div>;
}

function ReportEditor({ day, person, sites, readOnly, today, onDay, onSaved }: {
  day: string; person: string | null; sites: AttendanceSite[]; readOnly: boolean; today: string; onDay: (day: string) => void; onSaved: () => void;
}) {
  const [load, setLoad] = useState<Load<AttendanceDailyReport | null>>({ kind: 'loading' });
  const [draft, setDraft] = useState<ReportDraft>(emptyReportDraft);
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [error, setError] = useState<string | null>(null);
  const [aiText, setAiText] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const ids = { error: useId(), day: useId() };
  const fetchReport = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try {
      const report = await api.getReport(day, person);
      setLoad({ kind: 'ready', value: report }); setDraft(draftFromReport(report));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) { setLoad({ kind: 'ready', value: null }); setDraft(emptyReportDraft()); }
      else setLoad(failed(e));
    }
  }, [day, person]);
  useEffect(() => { void fetchReport(); }, [fetchReport]);
  const report = load.kind === 'ready' ? load.value : null;
  const set = (patch: Partial<ReportDraft>) => setDraft((d) => ({ ...d, ...patch }));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const body = reportBody(draft, sites, report?.version ?? null);
    if (!body.ok) { setError(body.error); return; }
    setBusy('save'); setError(null); setFlash(null);
    try {
      const saved = await api.saveReport(day, body.value);
      setLoad({ kind: 'ready', value: saved }); setDraft(draftFromReport(saved)); setFlash({ text: t('att.daily.saved', { day }) }); onSaved();
    } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const attach = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy('upload'); setError(null);
    try {
      const uploaded = [];
      for (const file of Array.from(files).slice(0, 5)) uploaded.push(await uploadFile(file));
      set({ fileIds: [...draft.fileIds, ...uploaded.map((f) => f.id)].slice(0, 20) });
      setFlash({ text: t('att.daily.attached', { count: uploaded.length }) });
    } catch (e) { setError(t('att.daily.uploadFailed', { message: describeError(e) })); } finally { setBusy(null); }
  };
  const draftAi = async (period: 'day' | 'week') => {
    setBusy(`ai-${period}`); setError(null); setAiText(null);
    try { setAiText((await api.draftSummary(period, day, getLocale())).text); } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const remove = async () => {
    setBusy('delete');
    try { await api.deleteReport(day); setConfirmDelete(false); setFlash({ text: t('att.daily.deleted', { day }) }); await fetchReport(); onSaved(); }
    catch (e) { setError(describeError(e)); setConfirmDelete(false); } finally { setBusy(null); }
  };

  return <section className="surface cmPanel attEditor" aria-labelledby="att-editor">
    <div className="attBar">
      <h2 id="att-editor">{t('att.daily.title')}</h2>
      <label className="cmInline" htmlFor={ids.day}><span>{t('att.daily.day')}</span>
        <input id={ids.day} type="date" value={day} max={shiftDay(today, 1)} onChange={(e) => e.target.value && onDay(e.target.value)} /></label>
    </div>
    {load.kind === 'loading' && <Notice busy>{t('att.daily.loadingOne')}</Notice>}
    {load.kind === 'error' && <LoadError message={t('att.daily.error', { message: load.message })} retry={() => void fetchReport()} />}
    {load.kind === 'denied' && <Notice error>{t('att.daily.notShared')}</Notice>}
    {load.kind === 'ready' && readOnly && (report ? <ReportView report={report} /> : <Empty icon={<FileText aria-hidden="true" />} title={t('att.daily.noneTitle')} text={t('att.daily.noneShared')} />)}
    {load.kind === 'ready' && !readOnly && <form className="attForm" noValidate onSubmit={(e) => void save(e)} aria-describedby={error ? ids.error : undefined}>
      {!report && <p className="muted">{t('att.daily.new')}</p>}
      <label className="field"><span>{t('att.daily.project')}</span><input dir="auto" value={draft.project} maxLength={160} onChange={(e) => set({ project: e.target.value })} /></label>
      <div className="attTwo">
        <label className="field"><span>{t('att.daily.done')}</span><textarea dir="auto" rows={4} value={draft.done} onChange={(e) => set({ done: e.target.value })} /><small className="muted">{t('att.daily.onePerLine')}</small></label>
        <label className="field"><span>{t('att.daily.pending')}</span><textarea dir="auto" rows={4} value={draft.pending} onChange={(e) => set({ pending: e.target.value })} /><small className="muted">{t('att.daily.onePerLine')}</small></label>
      </div>
      <div className="attTwo">
        <label className="field"><span>{t('att.daily.visits')}</span><textarea dir="auto" rows={2} value={draft.visits} onChange={(e) => set({ visits: e.target.value })} /><small className="muted">{t('att.daily.visitsHint')}</small></label>
        <label className="field"><span>{t('att.daily.issues')}</span><textarea dir="auto" rows={2} value={draft.issues} maxLength={4000} onChange={(e) => set({ issues: e.target.value })} /></label>
      </div>
      <div className="attThree">
        <label className="field"><span>{t('att.daily.minutes')}</span><input inputMode="numeric" dir="ltr" value={draft.minutes} onChange={(e) => set({ minutes: e.target.value })} /></label>
        <label className="field"><span>{t('att.daily.priority')}</span><select value={draft.priority} onChange={(e) => set({ priority: e.target.value as ReportDraft['priority'] })}>
          {(['low', 'normal', 'high', 'urgent'] as const).map((p) => <option key={p} value={p}>{t(`att.priority.${p}` as MessageKey)}</option>)}</select></label>
        <label className="field"><span>{t('att.daily.followUp')}</span><input type="date" value={draft.followUp} onChange={(e) => set({ followUp: e.target.value })} /></label>
      </div>
      <label className="field"><span>{t('att.daily.notes')}</span><textarea dir="auto" rows={3} value={draft.notes} maxLength={8000} onChange={(e) => set({ notes: e.target.value })} /></label>
      <div className="attFiles">
        <label className="secondary attUpload"><Paperclip aria-hidden="true" />{busy === 'upload' ? t('att.daily.uploading') : t('att.daily.attach')}
          <input type="file" multiple className="visuallyHidden" disabled={busy !== null} onChange={(e) => void attach(e.target.files)} /></label>
        {draft.fileIds.length > 0 && <ul className="tagRow">{draft.fileIds.map((fid) => <li key={fid}><span dir="ltr">{fid}</span>
          <button type="button" className="cmLinkButton" aria-label={t('att.daily.removeFile', { id: fid })} onClick={() => set({ fileIds: draft.fileIds.filter((x) => x !== fid) })}><X aria-hidden="true" /></button></li>)}</ul>}
        <small className="muted">{t('att.daily.filesHint')}</small>
      </div>
      <FormError id={ids.error} text={error} />
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      <div className="actions">
        <button type="submit" disabled={busy !== null}>{busy === 'save' ? t('att.saving') : t('att.daily.save')}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void draftAi('day')}><Sparkles aria-hidden="true" />{busy === 'ai-day' ? t('att.daily.drafting') : t('att.daily.aiDay')}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void draftAi('week')}><Sparkles aria-hidden="true" />{busy === 'ai-week' ? t('att.daily.drafting') : t('att.daily.aiWeek')}</button>
        {report && <button type="button" className="secondary danger" disabled={busy !== null} onClick={() => setConfirmDelete(true)}><Trash2 aria-hidden="true" />{t('att.daily.delete')}</button>}
      </div>
      {aiText !== null && <div className="surface attAi" role="region" aria-label={t('att.daily.aiTitle')}>
        <h3>{t('att.daily.aiTitle')}</h3><p dir="auto">{aiText}</p><p className="muted">{t('att.daily.aiNote')}</p>
        <div className="actions"><button type="button" className="secondary" onClick={() => { set({ notes: draft.notes ? `${draft.notes}\n\n${aiText}` : aiText, ai: true }); setAiText(null); }}>{t('att.daily.aiUse')}</button>
          <button type="button" className="secondary" onClick={() => setAiText(null)}>{t('att.daily.aiDiscard')}</button></div>
      </div>}
    </form>}
    {confirmDelete && <ConfirmDialog title={t('att.daily.deleteTitle', { day })} confirmLabel={t('att.daily.delete')} danger busy={busy === 'delete'}
      onConfirm={() => void remove()} onCancel={() => setConfirmDelete(false)}><p>{t('att.daily.deleteBody')}</p></ConfirmDialog>}
  </section>;
}

function ReportView({ report }: { report: AttendanceDailyReport }) {
  return <dl className="attReport">
    {report.project && <div><dt>{t('att.daily.project')}</dt><dd dir="auto">{report.project}</dd></div>}
    <div><dt>{t('att.daily.done')}</dt><dd>{report.tasks_done.length ? <ul>{report.tasks_done.map((x) => <li key={x} dir="auto">{x}</li>)}</ul> : '-'}</dd></div>
    <div><dt>{t('att.daily.pending')}</dt><dd>{report.tasks_pending.length ? <ul>{report.tasks_pending.map((x) => <li key={x} dir="auto">{x}</li>)}</ul> : '-'}</dd></div>
    <div><dt>{t('att.daily.issues')}</dt><dd dir="auto">{report.issues ?? '-'}</dd></div>
    <div><dt>{t('att.daily.priority')}</dt><dd>{t(`att.priority.${report.priority}` as MessageKey)}</dd></div>
    <div><dt>{t('att.daily.notes')}</dt><dd dir="auto">{report.notes ?? '-'}</dd></div>
  </dl>;
}

// ---- Corrections ---------------------------------------------------------------------------------

export function CorrectionsPanel({ sites, timeZone, today, onChanged }: { sites: AttendanceSite[]; timeZone: string; today: string; onChanged: () => void }) {
  const [action, setAction] = useState<'add' | 'change' | 'void'>('add');
  const [events, setEvents] = useState<Load<AttendanceEvent[]>>({ kind: 'loading' });
  const [history, setHistory] = useState<Load<AttendancePage<AttendanceCorrection>>>({ kind: 'loading' });
  const [form, setForm] = useState({ eventId: '', kind: 'arrive' as AttendanceEventKind, day: today, time: '09:00', siteId: '', reason: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const ids = { error: useId() };
  const load = useCallback(async () => {
    const [e, h] = await Promise.allSettled([api.listEvents(shiftDay(today, -13), today), api.listCorrections(50, 0)]);
    setEvents(e.status === 'fulfilled' ? { kind: 'ready', value: e.value } : failed(e.reason));
    setHistory(h.status === 'fulfilled' ? { kind: 'ready', value: h.value } : failed(h.reason));
  }, [today]);
  useEffect(() => { void load(); }, [load]);
  const recent = events.kind === 'ready' ? [...events.value].reverse() : [];
  const label = (e: AttendanceEvent) => t('att.fix.eventOption', { day: e.occurred_at.slice(0, 10), time: clock(e.occurred_at, e.time_zone), what: kindLabel(e.kind), site: e.site_id ? siteName(sites, e.site_id) : '-' });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const reason = checkReason(form.reason);
    if (!reason.ok) { setError(reason.error); return; }
    if (action !== 'add' && !form.eventId) { setError(t('att.fix.pickEvent')); return; }
    const when = action === 'void' ? null : localToIso(form.day, form.time, timeZone);
    if (action !== 'void' && !when) { setError(t('att.form.clock')); return; }
    setBusy(true); setError(null); setFlash(null);
    try {
      await api.addCorrection({ action, reason: reason.value, event_id: form.eventId || undefined, kind: action === 'void' ? undefined : form.kind,
        occurred_at: when ?? undefined, site_id: action === 'void' ? undefined : form.siteId || null });
      setFlash({ text: t('att.fix.saved') }); setForm((f) => ({ ...f, reason: '', eventId: '' })); await load(); onChanged();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };

  return <div className="attFix">
    <form className="surface cmPanel attForm" noValidate onSubmit={(e) => void submit(e)} aria-labelledby="att-fix" aria-describedby={error ? ids.error : undefined}>
      <h2 id="att-fix">{t('att.fix.title')}</h2>
      <p className="muted">{t('att.fix.lead')}</p>
      <div className="cmSegment" role="radiogroup" aria-label={t('att.fix.action')}>
        {(['add', 'change', 'void'] as const).map((id) => <button key={id} type="button" role="radio" aria-checked={action === id} onClick={() => setAction(id)}>{t(`att.fix.${id}` as MessageKey)}</button>)}
      </div>
      {action !== 'add' && <label className="field"><span>{t('att.fix.event')}</span>
        <select value={form.eventId} onChange={(e) => {
          const picked = recent.find((x) => x.id === e.target.value);
          setForm((f) => ({ ...f, eventId: e.target.value, ...(picked ? { kind: picked.kind, siteId: picked.site_id ?? '', day: picked.occurred_at.slice(0, 10), time: clock(picked.occurred_at, picked.time_zone) } : {}) }));
        }}>
          <option value="">{recent.length ? t('att.fix.choose') : t('att.fix.noEvents')}</option>
          {recent.map((e) => <option key={e.id} value={e.id}>{label(e)}</option>)}
        </select></label>}
      {action !== 'void' && <div className="attThree">
        <label className="field"><span>{t('att.fix.kind')}</span><select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as AttendanceEventKind })}>
          {KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}</select></label>
        <label className="field"><span>{t('att.fix.day')}</span><input type="date" value={form.day} max={today} onChange={(e) => setForm({ ...form, day: e.target.value })} /></label>
        <label className="field"><span>{t('att.fix.time', { zone: timeZone })}</span><input type="time" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} /></label>
        <label className="field"><span>{t('att.fix.site')}</span><select value={form.siteId} onChange={(e) => setForm({ ...form, siteId: e.target.value })}>
          <option value="">{t('att.fix.noSite')}</option>
          {sites.filter((s) => !s.archived).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      </div>}
      <label className="field"><span>{t('att.fix.reason')}</span><input dir="auto" value={form.reason} maxLength={500} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></label>
      <FormError id={ids.error} text={error} />
      {flash && <Notice>{flash.text}</Notice>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('att.saving') : t('att.fix.submit')}</button></div>
    </form>
    <section className="surface cmPanel" aria-labelledby="att-fix-history">
      <h2 id="att-fix-history">{t('att.fix.history')}</h2>
      {history.kind === 'loading' && <Notice busy>{t('att.fix.loading')}</Notice>}
      {history.kind === 'error' && <LoadError message={t('att.fix.error', { message: history.message })} retry={() => void load()} />}
      {history.kind === 'ready' && (history.value.items.length === 0
        ? <Empty icon={<History aria-hidden="true" />} title={t('att.fix.emptyTitle')} text={t('att.fix.emptyText')} />
        : <ul className="cmList">{history.value.items.map((c) => <li key={c.id}><div>
          <strong>{t(`att.fix.did.${c.action}` as MessageKey)}</strong>
          <small dir="auto">{c.reason}</small>
          <small>{[c.before ? t('att.fix.before', { what: kindLabel(c.before.kind), when: formatDateTime(c.before.occurred_at) }) : '', c.after ? t('att.fix.after', { what: kindLabel(c.after.kind), when: formatDateTime(c.after.occurred_at) }) : ''].filter(Boolean).join(' · ')}</small>
        </div><small>{formatDateTime(c.created_at)}</small></li>)}</ul>)}
    </section>
  </div>;
}

// ---- Settings ------------------------------------------------------------------------------------

export function SettingsPanel({ sites, shares, onChanged }: { sites: AttendanceSite[]; shares: Load<AttendanceShares>; onChanged: () => void }) {
  return <div className="attSettings">
    <ProfileForm sites={sites} onChanged={onChanged} />
    <SitesCard sites={sites} onChanged={onChanged} />
    <SharingCard shares={shares} onChanged={onChanged} />
    <ShortcutCard sites={sites} />
  </div>;
}

function ProfileForm({ sites, onChanged }: { sites: AttendanceSite[]; onChanged: () => void }) {
  const [load, setLoad] = useState<Load<AttendanceProfile>>({ kind: 'loading' });
  const [form, setForm] = useState<AttendanceProfile | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const ids = { error: useId() };
  const fetchProfile = useCallback(async () => {
    try { const p = await api.getProfile(); setLoad({ kind: 'ready', value: p }); setForm(p); } catch (e) { setLoad(failed(e)); }
  }, []);
  useEffect(() => { void fetchProfile(); }, [fetchProfile]);
  if (load.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('att.profile.loading')}</Notice></section>;
  if (load.kind === 'error' || load.kind === 'denied') return <section className="surface cmPanel"><LoadError message={t('att.profile.error', { message: load.message })} retry={() => void fetchProfile()} /></section>;
  if (!form) return null;
  const set = (patch: Partial<AttendanceProfile>) => setForm({ ...form, ...patch });
  const setShift = (index: number, patch: Partial<AttendanceShift>) => set({ schedule: form.schedule.map((s, i) => (i === index ? { ...s, ...patch } : s)) });
  const minutes = (value: number | null) => (value === null ? '' : String(value / 60));
  const fromHours = (text: string): number | null => (text.trim() === '' ? null : Math.round(Number(text) * 60));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const bad = form.schedule.map(checkShift).find(Boolean);
    if (bad) { setError(bad); return; }
    for (const value of [form.daily_overtime_minutes, form.weekly_overtime_minutes]) {
      if (value !== null && (!Number.isFinite(value) || value < 60)) { setError(t('att.profile.overtimeInvalid')); return; }
    }
    setBusy('save'); setError(null); setFlash(null);
    try {
      const saved = await api.saveProfile({
        organisation: form.organisation, department: form.department, role_title: form.role_title, workplace_site_id: form.workplace_site_id || null,
        time_zone: form.time_zone, week_starts_on: form.week_starts_on, schedule: form.schedule, daily_overtime_minutes: form.daily_overtime_minutes,
        weekly_overtime_minutes: form.weekly_overtime_minutes, ai_drafts_enabled: form.ai_drafts_enabled, missed_check_alerts: form.missed_check_alerts,
        expected_version: load.value.saved ? load.value.version : 0,
      });
      setLoad({ kind: 'ready', value: saved }); setForm(saved); setFlash({ text: t('att.profile.saved') }); onChanged();
    } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const tracking = async (action: 'consent' | 'withdraw' | 'pause' | 'resume') => {
    setBusy(action); setError(null); setFlash(null);
    try { const saved = await api.setTracking(action); setLoad({ kind: 'ready', value: saved }); setForm(saved); setFlash({ text: t(`att.track.done.${action}` as MessageKey) }); onChanged(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const zones = ZONES.includes(form.time_zone) ? ZONES : [form.time_zone, ...ZONES];

  return <form className="surface cmPanel attForm" noValidate onSubmit={(e) => void save(e)} aria-labelledby="att-profile" aria-describedby={error ? ids.error : undefined}>
    <h2 id="att-profile">{t('att.profile.title')}</h2>
    <div className="attThree">
      <label className="field"><span>{t('att.profile.organisation')}</span><input dir="auto" value={form.organisation ?? ''} maxLength={160} onChange={(e) => set({ organisation: e.target.value })} /></label>
      <label className="field"><span>{t('att.profile.department')}</span><input dir="auto" value={form.department ?? ''} maxLength={120} onChange={(e) => set({ department: e.target.value })} /></label>
      <label className="field"><span>{t('att.profile.role')}</span><input dir="auto" value={form.role_title ?? ''} maxLength={120} onChange={(e) => set({ role_title: e.target.value })} /></label>
      <label className="field"><span>{t('att.profile.workplace')}</span><select value={form.workplace_site_id ?? ''} onChange={(e) => set({ workplace_site_id: e.target.value || null })}>
        <option value="">{t('att.profile.noWorkplace')}</option>{sites.filter((s) => !s.archived).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label className="field"><span>{t('att.profile.zone')}</span><select dir="ltr" value={form.time_zone} onChange={(e) => set({ time_zone: e.target.value })}>
        {zones.map((z) => <option key={z} value={z}>{z}</option>)}</select></label>
      <label className="field"><span>{t('att.profile.weekStart')}</span><select value={form.week_starts_on} onChange={(e) => set({ week_starts_on: Number(e.target.value) })}>
        {[0, 1, 2, 3, 4, 5, 6].map((d) => <option key={d} value={d}>{weekdayLabel(d)}</option>)}</select></label>
    </div>
    <fieldset className="attFieldset"><legend>{t('att.profile.schedule')}</legend>
      {form.schedule.length === 0 && <p className="muted">{t('att.profile.noSchedule')}</p>}
      {form.schedule.map((shift, index) => <div className="attShift" key={index}>
        <label className="cmInline"><span>{t('att.profile.day')}</span><select value={shift.weekday} onChange={(e) => setShift(index, { weekday: Number(e.target.value) })}>
          {[0, 1, 2, 3, 4, 5, 6].map((d) => <option key={d} value={d}>{weekdayLabel(d)}</option>)}</select></label>
        <label className="cmInline"><span>{t('att.profile.start')}</span><input type="time" value={shift.start} onChange={(e) => setShift(index, { start: e.target.value })} /></label>
        <label className="cmInline"><span>{t('att.profile.end')}</span><input type="time" value={shift.end} onChange={(e) => setShift(index, { end: e.target.value })} /></label>
        {isOvernight(shift) && <small className="muted">{t('att.profile.overnight')}</small>}
        <button type="button" className="secondary" aria-label={t('att.profile.removeShift', { day: weekdayLabel(shift.weekday) })} onClick={() => set({ schedule: form.schedule.filter((_, i) => i !== index) })}><X aria-hidden="true" /></button>
      </div>)}
      <div className="actions">
        <button type="button" className="secondary" disabled={form.schedule.length >= 21} onClick={() => set({ schedule: [...form.schedule, { weekday: 0, start: '09:00', end: '17:00' }] })}><Plus aria-hidden="true" />{t('att.profile.addShift')}</button>
        {form.schedule.length === 0 && <button type="button" className="secondary" onClick={() => set({ schedule: [0, 1, 2, 3, 4].map((weekday) => ({ weekday, start: '09:00', end: '17:00' })) })}>{t('att.profile.weekdays')}</button>}
      </div>
    </fieldset>
    <div className="attTwo">
      <label className="field"><span>{t('att.profile.dailyOt')}</span><input inputMode="decimal" dir="ltr" value={minutes(form.daily_overtime_minutes)} onChange={(e) => set({ daily_overtime_minutes: fromHours(e.target.value) })} /><small className="muted">{t('att.profile.otHint')}</small></label>
      <label className="field"><span>{t('att.profile.weeklyOt')}</span><input inputMode="decimal" dir="ltr" value={minutes(form.weekly_overtime_minutes)} onChange={(e) => set({ weekly_overtime_minutes: fromHours(e.target.value) })} /></label>
    </div>
    <label className="attCheck"><input type="checkbox" checked={form.missed_check_alerts} onChange={(e) => set({ missed_check_alerts: e.target.checked })} />{t('att.profile.alerts')}</label>
    <label className="attCheck"><input type="checkbox" checked={form.ai_drafts_enabled} onChange={(e) => set({ ai_drafts_enabled: e.target.checked })} />{t('att.profile.ai')}</label>
    <small className="muted">{t('att.profile.aiHint')}</small>
    <FormError id={ids.error} text={error} />
    {flash && <Notice>{flash.text}</Notice>}
    <div className="actions"><button type="submit" disabled={busy !== null}>{busy === 'save' ? t('att.saving') : t('att.profile.save')}</button></div>

    <fieldset className="attFieldset"><legend>{t('att.track.title')}</legend>
      <p className="muted">{t('att.track.lead')}</p>
      {!load.value.location_consent
        ? <div className="actions"><button type="button" disabled={busy !== null} onClick={() => void tracking('consent')}><MapPin aria-hidden="true" />{t('att.track.consent')}</button></div>
        : <>
          <p>{load.value.tracking_paused ? t('att.track.isPaused') : t('att.track.isOn')}</p>
          <div className="actions">
            {load.value.tracking_paused
              ? <button type="button" disabled={busy !== null} onClick={() => void tracking('resume')}>{t('att.track.resume')}</button>
              : <button type="button" className="secondary" disabled={busy !== null} onClick={() => void tracking('pause')}>{t('att.track.pause')}</button>}
            <button type="button" className="secondary danger" disabled={busy !== null} onClick={() => void tracking('withdraw')}>{t('att.track.withdraw')}</button>
          </div>
        </>}
    </fieldset>
  </form>;
}

function SitesCard({ sites, onChanged }: { sites: AttendanceSite[]; onChanged: () => void }) {
  const [form, setForm] = useState({ name: '', kind: 'workplace' as AttendanceSiteKind, address: '', radius: '150' });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ids = { error: useId() };
  const add = async (event: FormEvent) => {
    event.preventDefault();
    const radius = Number(form.radius);
    if (!form.name.trim()) { setError(t('att.sites.nameRequired')); return; }
    if (!Number.isInteger(radius) || radius < 50 || radius > 2000) { setError(t('att.sites.radiusInvalid')); return; }
    setBusy('add'); setError(null);
    try { await api.createSite({ name: form.name.trim(), kind: form.kind, address_label: form.address.trim() || null, radius_m: radius }); setForm({ ...form, name: '', address: '' }); onChanged(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const toggle = async (site: AttendanceSite) => {
    setBusy(site.id); setError(null);
    try { if (site.archived) await api.updateSite(site.id, { archived: false }); else await api.archiveSite(site.id); onChanged(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  return <section className="surface cmPanel" aria-labelledby="att-sites">
    <h2 id="att-sites">{t('att.sites.title')}</h2>
    <p className="muted">{t('att.sites.lead')}</p>
    {sites.length === 0 ? <Empty icon={<MapPin aria-hidden="true" />} title={t('att.sites.emptyTitle')} text={t('att.sites.emptyText')} />
      : <ul className="cmList">{sites.map((s) => <li key={s.id}><div><strong dir="auto">{s.name}</strong>
        <small>{t(`att.siteKind.${s.kind}` as MessageKey)} · {t('att.sites.radius', { m: s.radius_m })}{s.address_label ? ` · ${s.address_label}` : ''}{s.archived ? ` · ${t('att.sites.archived')}` : ''}</small></div>
        <button type="button" className="secondary" disabled={busy !== null} aria-label={t(s.archived ? 'att.sites.restoreLabel' : 'att.sites.archiveLabel', { name: s.name })} onClick={() => void toggle(s)}>
          {s.archived ? <ArchiveRestore aria-hidden="true" /> : <Archive aria-hidden="true" />}{s.archived ? t('att.sites.restore') : t('att.sites.archive')}</button></li>)}</ul>}
    <form className="attForm" noValidate onSubmit={(e) => void add(e)} aria-describedby={error ? ids.error : undefined}>
      <h3 className="attSub">{t('att.sites.add')}</h3>
      <div className="attThree">
        <label className="field"><span>{t('att.sites.name')}</span><input dir="auto" value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label className="field"><span>{t('att.sites.kind')}</span><select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as AttendanceSiteKind })}>
          {SITE_KINDS.map((k) => <option key={k} value={k}>{t(`att.siteKind.${k}` as MessageKey)}</option>)}</select></label>
        <label className="field"><span>{t('att.sites.radiusLabel')}</span><input inputMode="numeric" dir="ltr" value={form.radius} onChange={(e) => setForm({ ...form, radius: e.target.value })} /></label>
      </div>
      <label className="field"><span>{t('att.sites.address')}</span><input dir="auto" value={form.address} maxLength={200} onChange={(e) => setForm({ ...form, address: e.target.value })} /><small className="muted">{t('att.sites.addressHint')}</small></label>
      <FormError id={ids.error} text={error} />
      <div className="actions"><button type="submit" disabled={busy !== null}><Plus aria-hidden="true" />{busy === 'add' ? t('att.saving') : t('att.sites.addButton')}</button></div>
    </form>
  </section>;
}

function SharingCard({ shares, onChanged }: { shares: Load<AttendanceShares>; onChanged: () => void }) {
  const [people, setPeople] = useState<Load<TeamPerson[]>>({ kind: 'loading' });
  const [viewer, setViewer] = useState('');
  const [reports, setReports] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void (async () => { try { setPeople({ kind: 'ready', value: await api.listPeople() }); } catch (e) { setPeople(failed(e)); } })(); }, []);
  const mine = shares.kind === 'ready' ? shares.value.mine : [];
  const name = (userId: string) => (people.kind === 'ready' ? people.value.find((p) => p.user_id === userId)?.display_name : undefined) ?? userId;
  const share = async (event: FormEvent) => {
    event.preventDefault();
    if (!viewer) { setError(t('att.share.pick')); return; }
    setBusy('add'); setError(null);
    try { await api.createShare(viewer, reports); setViewer(''); setReports(false); onChanged(); } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const stop = async (id: string) => {
    setBusy(id); setError(null);
    try { await api.revokeShare(id); onChanged(); } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const candidates = people.kind === 'ready' ? people.value.filter((p) => p.role !== 'viewer' && !mine.some((s) => s.viewer_user_id === p.user_id)) : [];
  return <section className="surface cmPanel" aria-labelledby="att-share">
    <h2 id="att-share">{t('att.share.title')}</h2>
    <p className="muted">{t('att.share.lead')}</p>
    {shares.kind === 'error' && <Notice error>{t('att.share.error', { message: shares.message })}</Notice>}
    {mine.length === 0 ? <p className="muted">{t('att.share.none')}</p>
      : <ul className="cmList">{mine.map((s) => <li key={s.id}><div><strong dir="auto">{name(s.viewer_user_id)}</strong>
        <small>{s.include_reports ? t('att.share.withReports') : t('att.share.hoursOnly')}</small></div>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void stop(s.id)}>{t('att.share.stop')}</button></li>)}</ul>}
    <form className="attForm" noValidate onSubmit={(e) => void share(e)}>
      <label className="field"><span>{t('att.share.with')}</span><select value={viewer} onChange={(e) => setViewer(e.target.value)} disabled={people.kind !== 'ready'}>
        <option value="">{people.kind === 'loading' ? t('att.share.loadingPeople') : t('att.share.choose')}</option>
        {candidates.map((p) => <option key={p.user_id} value={p.user_id}>{p.display_name}</option>)}</select></label>
      {people.kind === 'error' && <small className="muted">{t('att.share.peopleError', { message: people.message })}</small>}
      <label className="attCheck"><input type="checkbox" checked={reports} onChange={(e) => setReports(e.target.checked)} />{t('att.share.includeReports')}</label>
      {error && <Notice error>{error}</Notice>}
      <div className="actions"><button type="submit" disabled={busy !== null}><Share2 aria-hidden="true" />{t('att.share.submit')}</button></div>
    </form>
  </section>;
}

function ShortcutCard({ sites }: { sites: AttendanceSite[] }) {
  const site = sites.find((s) => !s.archived)?.name ?? t('att.shortcut.siteExample');
  return <section className="surface cmPanel" aria-labelledby="att-shortcut">
    <h2 id="att-shortcut"><Smartphone aria-hidden="true" className="cmInlineIcon" />{t('att.shortcut.title')}</h2>
    <p className="muted">{t('att.shortcut.lead')}</p>
    <ol className="attSteps">
      {(['1', '2', '3', '4', '5', '6'] as const).map((n) => <li key={n}>{t(`att.shortcut.step${n}` as MessageKey, { site })}</li>)}
    </ol>
    <pre className="attCode" dir="ltr" tabIndex={0} aria-label={t('att.shortcut.request')}>{`POST /api/v1/attendance/device/events\nAuthorization: Bearer <token>\nContent-Type: application/json\n\n{"event": "arrive", "site": "${site}", "idempotency_key": "<date>-arrive"}`}</pre>
    <p className="muted">{t('att.shortcut.token')}</p>
  </section>;
}
