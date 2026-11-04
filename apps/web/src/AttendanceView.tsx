// Work attendance and daily reports (docs/attendance.md#web): the signed-in person's own log. Today (check in and
// out, breaks, remote, timeline), Reports (week and month hours, site history, CSV and PDF), Daily report, Corrections
// and Settings (profile, schedule, sites, location check in, sharing, iPhone Shortcuts). A colleague's shared record
// opens read only. A personal log, never an employer-certified timesheet: the page says so.
import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, Clock3, Coffee, Download, Eye, House, LogIn, LogOut, MapPin, RefreshCw, Wifi } from 'lucide-react';
import type { AttendanceEventKind, AttendancePeriod, AttendanceShares, AttendanceSite, AttendanceToday } from '@anum/contracts';
import * as api from './lib/attendanceApi';
import {
  ATTENDANCE_TABS, ATTENTION, clock, dayIn, decimalHours, flagLabel, hashForTab, hours, kindLabel, newKey, nextActions, shiftDay, siteName,
  statusText, tabFromHash, timeline, type AttendanceTab,
} from './lib/attendance';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { PageHeader, PageSummary, type Crumb } from './PageParts';
import { CorrectionsPanel, DailyPanel, SettingsPanel } from './AttendanceParts';
import './commerce.css';
import './attendance.css';

const ICONS: Record<AttendanceEventKind, typeof LogIn> = {
  arrive: LogIn, leave: LogOut, break_start: Coffee, break_end: Coffee, remote_start: House, remote_end: House,
};

function useAttendanceTab(): [AttendanceTab, (next: AttendanceTab) => void] {
  const [tab, setTab] = useState<AttendanceTab>(() => tabFromHash(typeof location === 'undefined' ? '' : location.hash));
  useEffect(() => {
    const sync = () => { if (location.hash.startsWith('#attendance')) setTab(tabFromHash(location.hash)); };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync); };
  }, []);
  const pick = (next: AttendanceTab) => {
    setTab(next);
    if (location.hash !== hashForTab(next)) history.pushState(null, '', hashForTab(next));
  };
  return [tab, pick];
}

export function AttendanceDenied() {
  return <section className="surface cmPanel cmDenied" aria-labelledby="att-denied"><Eye aria-hidden="true" /><div><h2 id="att-denied">{t('att.denied.title')}</h2><p className="muted">{t('att.denied.text')}</p></div></section>;
}

export function AttendanceView() {
  const [tab, pick] = useAttendanceTab();
  const [person, setPerson] = useState<string | null>(null);
  const [shares, setShares] = useState<Load<AttendanceShares>>({ kind: 'loading' });
  const [sites, setSites] = useState<Load<AttendanceSite[]>>({ kind: 'loading' });
  const [today, setToday] = useState<Load<AttendanceToday>>({ kind: 'loading' });
  const [tick, setTick] = useState(0);
  const readOnly = person !== null;

  const loadAll = useCallback(async () => {
    setToday((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    const [s, d] = await Promise.allSettled([api.listSites(person), api.getToday(person)]);
    setSites(s.status === 'fulfilled' ? { kind: 'ready', value: s.value } : failed(s.reason));
    setToday(d.status === 'fulfilled' ? { kind: 'ready', value: d.value } : failed(d.reason));
  }, [person]);
  const loadShares = useCallback(async () => {
    try { setShares({ kind: 'ready', value: await api.listShares() }); } catch (e) { setShares(failed(e)); }
  }, []);
  useEffect(() => { void loadAll(); }, [loadAll, tick]);
  useEffect(() => { void loadShares(); }, [loadShares]);
  const refresh = () => setTick((n) => n + 1);

  const sharedWithMe = shares.kind === 'ready' ? shares.value.with_me : [];
  const crumbs: Crumb[] = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('attendance'), href: '#attendance', onClick: () => pick('today') }];
  if (tab !== 'today') crumbs.push({ label: t(`att.tab.${tab}` as MessageKey) });
  const header = <PageHeader title={viewLabel('attendance')} subtitle={t('att.subtitle')} crumbs={tab === 'today' ? undefined : crumbs} actions={sharedWithMe.length > 0 ? <label className="cmInline attPerson">
    <span>{t('att.person.label')}</span>
    <select value={person ?? ''} onChange={(e) => { setPerson(e.target.value || null); if (e.target.value && (tab === 'settings' || tab === 'corrections')) pick('today'); }}>
      <option value="">{t('att.person.me')}</option>
      {sharedWithMe.map((s) => <option key={s.id} value={s.owner_user_id}>{s.owner_user_id}</option>)}
    </select>
  </label> : undefined} />;

  if (today.kind === 'denied' || sites.kind === 'denied') return <div className="cmLayout">{header}<AttendanceDenied /></div>;
  if (today.kind === 'loading') return <div className="cmLayout">{header}<Notice busy>{t('att.loading')}</Notice></div>;
  if (today.kind === 'error') return <div className="cmLayout">{header}<LoadError message={t('att.error.load', { message: today.message })} retry={refresh} /></div>;
  if (today.kind !== 'ready') return <div className="cmLayout">{header}</div>;
  const siteList = sites.kind === 'ready' ? sites.value : [];
  const totals = today.value.totals;
  const figures: Figure[] = [
    figure('worked', t('att.figure.worked'), { state: 'ready' }, () => ({ value: hours(totals?.worked_seconds ?? 0), hint: totals?.open_seconds ? t('att.figure.ongoing') : t('att.figure.today') })),
    figure('verified', t('att.figure.verified'), { state: 'ready' }, () => ({ value: hours(totals?.verified_seconds ?? 0), hint: t('att.figure.verifiedHint') })),
    figure('presence', t('att.figure.presence'), { state: 'ready' }, () => ({ value: hours(totals?.presence_seconds ?? 0), hint: totals?.remote_seconds ? t('att.figure.remote', { time: hours(totals.remote_seconds) }) : t('att.figure.onSite') })),
    figure('scheduled', t('att.figure.scheduled'), { state: 'ready' }, () => ({ value: totals?.scheduled_seconds ? hours(totals.scheduled_seconds) : '-', hint: totals?.scheduled_seconds ? t('att.figure.scheduledHint') : t('att.figure.noSchedule') })),
  ];
  const visibleTabs = ATTENDANCE_TABS.filter((id) => !readOnly || (id !== 'settings' && id !== 'corrections'));
  return <div className="cmLayout attLayout">
    {header}
    {readOnly && <Notice>{t('att.person.viewing', { person: person ?? '' })}</Notice>}
    <PageSummary label={t('att.summary.label')} figures={figures} footer={<p className="muted attNote">{t('att.notCertified')}</p>} />
    <nav className="settingsTabs cmTabs" aria-label={t('att.tabs')}>
      {visibleTabs.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`att.tab.${id}` as MessageKey)}</button>)}
    </nav>
    {sites.kind === 'error' && <LoadError message={t('att.error.sites', { message: sites.message })} retry={refresh} />}
    {tab === 'today' && <TodayPanel today={today.value} sites={siteList} readOnly={readOnly} onChanged={refresh} onSettings={() => pick('settings')} onCorrect={() => pick('corrections')} />}
    {tab === 'reports' && <ReportsPanel person={person} sites={siteList} timeZone={today.value.time_zone} />}
    {tab === 'daily' && <DailyPanel person={person} sites={siteList} today={today.value.day} readOnly={readOnly} />}
    {tab === 'corrections' && !readOnly && <CorrectionsPanel sites={siteList} timeZone={today.value.time_zone} today={today.value.day} onChanged={refresh} />}
    {tab === 'settings' && !readOnly && <SettingsPanel sites={siteList} shares={shares} onChanged={() => { refresh(); void loadShares(); }} />}
  </div>;
}

// ---- Today ---------------------------------------------------------------------------------------

function TodayPanel({ today, sites, readOnly, onChanged, onSettings, onCorrect }: {
  today: AttendanceToday; sites: AttendanceSite[]; readOnly: boolean; onChanged: () => void; onSettings: () => void; onCorrect: () => void;
}) {
  const active = sites.filter((s) => !s.archived);
  const [siteId, setSiteId] = useState<string>(() => today.status.site_id ?? active.find((s) => s.kind === 'workplace')?.id ?? active[0]?.id ?? '');
  const [busy, setBusy] = useState<AttendanceEventKind | null>(null);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [pending, setPending] = useState<{ kind: AttendanceEventKind; key: string } | null>(null);
  const rows = timeline(today.events, sites);
  const flags = (today.totals?.flags ?? []).filter((f) => ATTENTION.has(f));

  const press = async (kind: AttendanceEventKind) => {
    // A retry after a failure reuses the same key, so a request that did reach the server is never stored twice.
    const key = pending?.kind === kind ? pending.key : newKey();
    setPending({ kind, key }); setBusy(kind); setMessage(null);
    try {
      const needsSite = kind === 'arrive' || kind === 'leave';
      const result = await api.check(kind, key, needsSite ? (kind === 'leave' ? today.status.site_id : siteId || null) : null);
      setPending(null);
      setMessage({ text: t(result.duplicate ? 'att.today.already' : 'att.today.recorded', { what: kindLabel(kind), time: clock(result.event.occurred_at, result.event.time_zone) }) });
      onChanged();
    } catch (error) {
      setMessage({ text: t('att.today.failed', { message: describeError(error) }), error: true });
    } finally { setBusy(null); }
  };

  return <div className="attToday">
    <section className="surface cmPanel attStatus" aria-labelledby="att-status">
      <div className="attStatusHead">
        <span className={`attDot ${today.status.at_site || today.status.remote ? 'on' : ''} ${today.status.on_break ? 'pause' : ''}`} aria-hidden="true" />
        <div>
          <h2 id="att-status">{statusText(today, sites)}</h2>
          <p className="muted">{today.status.since ? t('att.today.since', { time: clock(today.status.since, today.time_zone) }) : t('att.today.notStarted')}
            {' '}<span dir="ltr">({today.time_zone})</span></p>
        </div>
      </div>
      {!readOnly && <>
        {!today.status.at_site && active.length > 0 && <label className="cmInline attSite"><span>{t('att.today.site')}</span>
          <select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
            {active.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select></label>}
        {active.length === 0 && <p className="muted">{t('att.today.noSites')} <button type="button" className="cmLinkButton" onClick={onSettings}>{t('att.today.addSite')}</button></p>}
        <div className="actions attActions">
          {nextActions(today).map((kind) => {
            const Icon = ICONS[kind];
            const disabled = busy !== null || ((kind === 'arrive') && active.length === 0);
            return <button key={kind} type="button" className={kind === 'arrive' || kind === 'leave' ? '' : 'secondary'} disabled={disabled} onClick={() => void press(kind)}>
              <Icon aria-hidden="true" />{busy === kind ? t('att.today.saving') : t(`att.action.${kind}` as MessageKey)}</button>;
          })}
        </div>
        {message && <Notice error={message.error}>{message.text}</Notice>}
        {today.location_consent
          ? (today.tracking_paused ? <p className="muted attTrack"><MapPin aria-hidden="true" />{t('att.today.paused')} <button type="button" className="cmLinkButton" onClick={onSettings}>{t('att.today.manage')}</button></p>
            : <p className="muted attTrack"><Wifi aria-hidden="true" />{t('att.today.tracking')}</p>)
          : <p className="muted attTrack"><MapPin aria-hidden="true" />{t('att.today.noTracking')} <button type="button" className="cmLinkButton" onClick={onSettings}>{t('att.today.setUp')}</button></p>}
      </>}
    </section>

    {flags.length > 0 && <section className="surface cmPanel attFlags" aria-labelledby="att-flags">
      <h2 id="att-flags">{t('att.today.attention')}</h2>
      <ul>{flags.map((f) => <li key={f}>{flagLabel(f)}</li>)}</ul>
      {!readOnly && <div className="actions"><button type="button" className="secondary" onClick={onCorrect}>{t('att.today.fix')}</button></div>}
    </section>}

    <section className="surface cmPanel" aria-labelledby="att-timeline">
      <h2 id="att-timeline">{t('att.today.timeline')}</h2>
      {rows.length === 0
        ? <Empty icon={<Clock3 aria-hidden="true" />} title={t('att.today.emptyTitle')} text={readOnly ? t('att.today.emptyShared') : t('att.today.emptyText')} />
        : <ol className="attTimeline">{rows.map((row) => <li key={row.id}>
          <time dir="ltr">{row.time}</time>
          <div><strong>{row.label}</strong>{row.site && <span className="muted"> · {row.site}</span>}
            {row.tags.length > 0 && <div className="tagRow">{row.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}</div>
        </li>)}</ol>}
    </section>
  </div>;
}

// ---- Reports -------------------------------------------------------------------------------------

function ReportsPanel({ person, sites, timeZone }: { person: string | null; sites: AttendanceSite[]; timeZone: string }) {
  const [period, setPeriod] = useState<'week' | 'month'>('week');
  const [anchor, setAnchor] = useState(() => dayIn(timeZone));
  const [data, setData] = useState<Load<AttendancePeriod>>({ kind: 'loading' });
  const [busy, setBusy] = useState<'csv' | 'pdf' | null>(null);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    setData({ kind: 'loading' });
    try { setData({ kind: 'ready', value: await api.getSummary(period, anchor, person) }); } catch (e) { setData(failed(e)); }
  }, [period, anchor, person]);
  useEffect(() => { void load(); }, [load]);
  const move = (direction: number) => setAnchor((day) => (period === 'week' ? shiftDay(day, 7 * direction) : shiftDay(`${day.slice(0, 7)}-15`, 30 * direction)));
  const download = async (format: 'csv' | 'pdf') => {
    if (data.kind !== 'ready') return;
    setBusy(format); setMessage(null);
    try { setMessage({ text: t('att.reports.saved', { name: await api.downloadReport(format, data.value.start, data.value.end, person) }) }); }
    catch (error) { setMessage({ text: t('att.reports.exportFailed', { message: describeError(error) }), error: true }); }
    finally { setBusy(null); }
  };
  const p = data.kind === 'ready' ? data.value : null;
  const visits = p ? p.days.flatMap((d) => d.segments.filter((s) => s.kind === 'site' && s.site_id).map((s) => ({ day: d.day, zone: d.time_zone, ...s }))) : [];
  return <section className="surface cmPanel" aria-labelledby="att-reports">
    <div className="attBar">
      <h2 id="att-reports">{t('att.reports.title')}</h2>
      <div className="cmSegment" role="radiogroup" aria-label={t('att.reports.period')}>
        {(['week', 'month'] as const).map((id) => <button key={id} type="button" role="radio" aria-checked={period === id} onClick={() => setPeriod(id)}>{t(`att.reports.${id}` as MessageKey)}</button>)}
      </div>
      <div className="attNav">
        <button type="button" className="secondary" aria-label={t('att.reports.previous')} onClick={() => move(-1)}><ChevronLeft className="cmFlip" aria-hidden="true" /></button>
        <strong>{p ? t('att.range', { start: p.start, end: p.end }) : anchor}</strong>
        <button type="button" className="secondary" aria-label={t('att.reports.next')} onClick={() => move(1)}><ChevronRight className="cmFlip" aria-hidden="true" /></button>
      </div>
      <div className="actions attExport">
        <button type="button" className="secondary" disabled={!p || busy !== null} onClick={() => void download('csv')}><Download aria-hidden="true" />{busy === 'csv' ? t('att.reports.exporting') : t('att.reports.csv')}</button>
        <button type="button" className="secondary" disabled={!p || busy !== null} onClick={() => void download('pdf')}><Download aria-hidden="true" />{busy === 'pdf' ? t('att.reports.exporting') : t('att.reports.pdf')}</button>
      </div>
    </div>
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {data.kind === 'loading' && <Notice busy>{t('att.reports.loading')}</Notice>}
    {data.kind === 'error' && <LoadError message={t('att.reports.error', { message: data.message })} retry={() => void load()} />}
    {data.kind === 'denied' && <AttendanceDenied />}
    {p && <>
      <dl className="cmFacts attFacts">
        <div><dt>{t('att.figure.worked')}</dt><dd>{hours(p.worked_seconds)}</dd></div>
        <div><dt>{t('att.figure.verified')}</dt><dd>{hours(p.verified_seconds)}</dd></div>
        <div><dt>{t('att.figure.presence')}</dt><dd>{hours(p.presence_seconds)}</dd></div>
        <div><dt>{t('att.reports.remote')}</dt><dd>{hours(p.remote_seconds)}</dd></div>
        <div><dt>{t('att.reports.days')}</dt><dd>{p.days_worked}</dd></div>
        <div><dt>{t('att.reports.overtime')}</dt><dd>{p.overtime_configured ? hours(p.overtime_seconds) : t('att.reports.overtimeOff')}</dd></div>
      </dl>
      {p.days.length === 0
        ? <Empty icon={<Clock3 aria-hidden="true" />} title={t('att.reports.emptyTitle')} text={t('att.reports.emptyText')} />
        : <div className="cmTableWrap"><table className="cmTable attTable">
          <caption className="visuallyHidden">{t('att.reports.tableCaption')}</caption>
          <thead><tr><th scope="col">{t('att.col.date')}</th><th scope="col">{t('att.col.in')}</th><th scope="col">{t('att.col.out')}</th><th scope="col" className="num">{t('att.col.worked')}</th><th scope="col" className="num">{t('att.col.verified')}</th><th scope="col">{t('att.col.sites')}</th><th scope="col">{t('att.col.notes')}</th></tr></thead>
          <tbody>{p.days.map((d) => <tr key={d.day}>
            <td dir="ltr">{d.day}</td><td dir="ltr">{clock(d.first_arrival, d.time_zone)}</td><td dir="ltr">{clock(d.last_departure, d.time_zone)}</td>
            <td className="num">{decimalHours(d.worked_seconds)}</td><td className="num">{decimalHours(d.verified_seconds)}</td>
            <td>{d.site_ids.map((id) => siteName(sites, id)).join(', ') || '-'}</td>
            <td>{[...d.flags.map(flagLabel), ...(d.reduced ? [t('att.reports.reduced')] : [])].join(', ') || '-'}</td>
          </tr>)}</tbody>
        </table></div>}
      <h3 className="attSub">{t('att.reports.siteHistory')}</h3>
      {visits.length === 0 ? <p className="muted">{t('att.reports.noVisits')}</p>
        : <ul className="cmList attVisits">{visits.map((v) => <li key={`${v.day}-${v.start}`}><div><strong>{siteName(sites, v.site_id)}</strong><small>{t('att.visitTime', { day: v.day, start: clock(v.start, v.zone), end: clock(v.end, v.zone) })}</small></div><span className="num">{hours(v.seconds)}</span></li>)}</ul>}
      <p className="muted attNote"><RefreshCw aria-hidden="true" className="cmInlineIcon" />{t('att.reports.explain')}</p>
    </>}
  </section>;
}
