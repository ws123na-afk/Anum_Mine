// Team attendance (docs/attendance.md#team-attendance): the roster of the signed-in person and the colleagues who
// share their record with them, today at a glance, daily report status, the daily reports table, the quick report
// and correction forms, the features strip and the privacy note. Read only for colleagues: a share never allows a
// change, and nobody who has not shared appears. Every figure comes from the API; nothing here is a placeholder.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  BarChart3, Bell, CalendarClock, CircleAlert, ClipboardCheck, Clock3, Ellipsis, Eye, FileText, History, Laptop, MapPin, PenLine, RefreshCw,
  ShieldCheck, Sparkles, UserX, Users,
} from 'lucide-react';
import type { AttendanceCorrection, AttendanceDailyReport, AttendanceDay, AttendancePage, AttendanceTeam, AttendanceTeamRow, TeamPerson } from '@anum/contracts';
import * as api from './lib/attendanceApi';
import { clock, draftFromReport, emptyReportDraft, flagLabel, hours, kindLabel, lines, localToIso, reportBody, shiftDay } from './lib/attendance';
import {
  REASON_PRESETS, breakText, filterReports, filterBySite, initials, mergeReports, percent, personName, personRole, quickFrom, reasonText, reportStatus,
  rowState, shiftShare, sourceLabel, summaryOf, teamFigures, toReview, type ContributorReport, type QuickReport, type ReasonPreset, type ReportStatus,
} from './lib/attendanceTeam';
import { ApiError, describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Empty, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';

type Chip = 'ok' | 'warn' | 'bad' | 'info' | 'muted';
const dayLabel = (day: string) => formatDateTime(`${day}T12:00:00Z`, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export function Avatar({ name }: { name: string }) {
  return <span className="attAvatar" aria-hidden="true">{initials(name)}</span>;
}

function stateChip(row: AttendanceTeamRow): { tone: Chip; text: string } {
  const state = rowState(row);
  const past = row.status === null;
  if (state === 'on_site') return { tone: 'ok', text: t('attTeam.state.on_site') };
  if (state === 'remote') return { tone: 'info', text: t('attTeam.state.remote') };
  if (state === 'on_break') return { tone: 'warn', text: t('attTeam.state.on_break') };
  if (state === 'left') return { tone: 'muted', text: t(past ? 'attTeam.state.worked' : 'attTeam.state.left') };
  return { tone: 'muted', text: t(past ? 'attTeam.state.noRecord' : 'attTeam.state.not_checked_in') };
}

function reportChip(row: AttendanceTeamRow): { tone: Chip; text: string; meta: string } {
  const report = row.report;
  if (!report) return { tone: 'muted', text: t('attTeam.report.notShared'), meta: '' };
  if (report.status === 'submitted') return { tone: 'ok', text: t('attTeam.report.submitted'), meta: report.submitted_at ? t('attTeam.report.at', { time: clock(report.submitted_at, row.time_zone) }) : '' };
  if (report.status === 'draft') return { tone: 'info', text: t('attTeam.report.draft'), meta: report.updated_at ? t('attTeam.report.lastEdit', { time: clock(report.updated_at, row.time_zone) }) : '' };
  return { tone: 'warn', text: t('attTeam.report.pending'), meta: t('attTeam.report.notYet') };
}

// ---- KPIs ---------------------------------------------------------------------------------------------

export function TeamKpis({ rows }: { rows: AttendanceTeamRow[] }) {
  const f = teamFigures(rows);
  const review = toReview(f);
  return <KpiRow label={t('attTeam.kpis')}>
    <KpiCard icon={<Users />} label={t('attTeam.kpi.present')} value={t('attTeam.xOfY', { x: formatNumber(f.present), y: formatNumber(f.people) })}
      hint={t('attTeam.kpi.presentHint', { pct: formatNumber(percent(f.present, f.people)) })} tone={f.people && f.present === f.people ? 'good' : ''} />
    <KpiCard icon={<Clock3 />} label={t('attTeam.kpi.checkedIn')} value={formatNumber(f.checkedIn)}
      hint={f.latest && f.latestZone ? t('attTeam.kpi.latest', { time: clock(f.latest, f.latestZone) }) : t('attTeam.kpi.noneYet')} />
    <KpiCard icon={<FileText />} label={t('attTeam.kpi.reports')} value={f.reportsVisible ? t('attTeam.xOfY', { x: formatNumber(f.reportsSubmitted), y: formatNumber(f.reportsVisible) }) : '-'}
      hint={f.reportsVisible ? t('attTeam.kpi.reportsHint', { pct: formatNumber(percent(f.reportsSubmitted, f.reportsVisible)) }) : t('attTeam.kpi.reportsNone')} />
    <KpiCard icon={<CircleAlert />} label={t('attTeam.kpi.review')} value={formatNumber(review)} tone={review ? 'warn' : ''}
      hint={t('attTeam.kpi.reviewHint', { reports: formatNumber(f.myReportOpen ? 1 : 0), flagged: formatNumber(f.flagged) })} />
  </KpiRow>;
}

// ---- Roster -------------------------------------------------------------------------------------------

export function RosterCard({ team, rows, people, site, live, refreshing, onRefresh, onMore, actions }: {
  team: AttendanceTeam; rows: AttendanceTeamRow[]; people: TeamPerson[]; site: string; live: boolean; refreshing: boolean; onRefresh: () => void; onMore: (() => void) | null;
  actions: { timesheet: (row: AttendanceTeamRow) => void; reports: (row: AttendanceTeamRow) => void; correct: () => void };
}) {
  const [menu, setMenu] = useState<string | null>(null);
  const first = rows[0] ?? team.rows[0];
  const day = first ? dayLabel(first.day) : '';
  const status = <span className="attLive" role="status">
    {live && <span className="attLiveDot" aria-hidden="true" />}
    <span>{live ? t('attTeam.liveFor', { day }) : t('attTeam.recordsFor', { day })}{site ? t('attTeam.atSite', { site }) : ''}</span>
    <small>{t('attTeam.updated', { time: formatDateTime(team.generated_at, { timeStyle: 'short' }) })}</small>
  </span>;
  return <FinCard title={t('attTeam.roster')} icon={<Users />} className="attRoster" subtitle={status}
    actions={<button type="button" className="secondary attSmall" disabled={refreshing} onClick={onRefresh}><RefreshCw aria-hidden="true" />{refreshing ? t('attTeam.refreshing') : t('attTeam.refresh')}</button>}>
    {team.rows.length === 1 && <FinBanner icon={<Eye />} title={t('attTeam.aloneTitle')}>{t('attTeam.aloneText')}</FinBanner>}
    {rows.length === 0
      ? <Empty icon={<MapPin aria-hidden="true" />} title={t('attTeam.noSiteTitle')} text={t('attTeam.noSiteText', { site })} />
      : <div className="attRosterWrap"><table className="finGrid attRosterTable">
        <caption className="visuallyHidden">{t('attTeam.roster')}</caption>
        <thead><tr>
          <th scope="col">{t('attTeam.col.member')}</th><th scope="col">{t('attTeam.col.site')}</th><th scope="col">{t('attTeam.col.firstIn')}</th>
          <th scope="col">{t('attTeam.col.break')}</th><th scope="col" className="num">{t('attTeam.col.hours')}</th><th scope="col">{t('attTeam.col.report')}</th>
          <th scope="col">{t('attTeam.col.status')}</th><th scope="col"><span className="visuallyHidden">{t('attTeam.col.actions')}</span></th>
        </tr></thead>
        <tbody>{rows.map((row) => {
          const name = personName(row.user_id, people, row.me);
          const role = personRole(row, people);
          const state = stateChip(row);
          const report = reportChip(row);
          return <tr key={row.user_id}>
            <th scope="row" data-label={t('attTeam.col.member')}><span className="attWho"><Avatar name={name} /><span><strong dir="auto">{name}</strong>{role && <small dir="auto">{role}</small>}</span></span></th>
            <td data-label={t('attTeam.col.site')}>{row.site_names.length ? <span className="attSiteName"><MapPin aria-hidden="true" /><span dir="auto">{row.site_names.join(', ')}</span></span> : '-'}</td>
            <td data-label={t('attTeam.col.firstIn')}>{row.first_check_in ? <span className="attStack"><span dir="ltr">{clock(row.first_check_in, row.time_zone)}</span><small>{sourceLabel(row.first_check_in_source)}</small></span> : <span className="muted">{t('attTeam.notIn')}</span>}</td>
            <td data-label={t('attTeam.col.break')}><span className="attStack"><span>{breakText(row)}</span>{row.break_seconds > 0 && <small>{hours(row.break_seconds)}</small>}</span></td>
            <td data-label={t('attTeam.col.hours')} className="num">{row.first_check_in || row.worked_seconds ? hours(row.worked_seconds) : '-'}</td>
            <td data-label={t('attTeam.col.report')}><span className="attStack"><StatusChip tone={report.tone}>{report.text}</StatusChip>{report.meta && <small>{report.meta}</small>}</span></td>
            <td data-label={t('attTeam.col.status')}><StatusChip tone={state.tone}>{state.text}</StatusChip></td>
            <td className="attMenuCell">
              <RowMenu open={menu === row.user_id} onToggle={(open) => setMenu(open ? row.user_id : null)} label={t('attTeam.menuFor', { name })}>
                <button type="button" role="menuitem" onClick={() => { setMenu(null); actions.timesheet(row); }}>{row.me ? t('attTeam.menu.myTimesheet') : t('attTeam.menu.timesheet')}</button>
                {row.reports_shared && <button type="button" role="menuitem" onClick={() => { setMenu(null); actions.reports(row); }}>{t('attTeam.menu.reports')}</button>}
                {row.me && <button type="button" role="menuitem" onClick={() => { setMenu(null); actions.correct(); }}>{t('attTeam.menu.correct')}</button>}
              </RowMenu>
            </td>
          </tr>;
        })}</tbody>
      </table></div>}
    {onMore && <div className="actions"><button type="button" className="secondary" disabled={refreshing} onClick={onMore}>{t('attTeam.more', { shown: formatNumber(team.rows.length), total: formatNumber(team.total) })}</button></div>}
  </FinCard>;
}

function RowMenu({ open, onToggle, label, children }: { open: boolean; onToggle: (open: boolean) => void; label: string; children: ReactNode }) {
  const id = useId();
  useEffect(() => {
    if (!open) return undefined;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onToggle(false); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [open, onToggle]);
  return <div className="attMenu">
    <button type="button" className="secondary attIconButton" aria-label={label} aria-haspopup="menu" aria-expanded={open} aria-controls={id} onClick={() => onToggle(!open)}><Ellipsis aria-hidden="true" /></button>
    {open && <div id={id} role="menu" className="attMenuList">{children}</div>}
  </div>;
}

// ---- Today at a glance and report status ------------------------------------------------------------

export function GlanceCard({ rows, people }: { rows: AttendanceTeamRow[]; people: TeamPerson[] }) {
  const f = teamFigures(rows);
  const day = rows[0] ? dayLabel(rows[0].day) : '';
  return <FinCard title={t('attTeam.glance')} icon={<CalendarClock />} className="attGlance" actions={<small className="muted">{day}</small>}>
    <ul className="attMini">
      <li><Users aria-hidden="true" /><span>{t('attTeam.glance.present')}</span><strong>{formatNumber(f.present)}</strong></li>
      <li><Laptop aria-hidden="true" /><span>{t('attTeam.glance.remote')}</span><strong>{formatNumber(f.remote)}</strong></li>
      <li><UserX aria-hidden="true" /><span>{t('attTeam.glance.notIn')}</span><strong>{formatNumber(f.notIn)}</strong></li>
    </ul>
    <div className="attBarsHead"><h3>{t('attTeam.glance.hours')}</h3><small className="muted">{t('attTeam.glance.target')}</small></div>
    <ul className="attBars">{rows.map((row) => {
      const name = personName(row.user_id, people, row.me);
      const share = shiftShare(row);
      return <li key={row.user_id}>
        <span className="attBarName" dir="auto">{name}</span>
        <span className="attBarHours">{hours(row.worked_seconds)}</span>
        <span className="attBarTrack" aria-hidden="true"><span style={{ inlineSize: `${share ?? 0}%` }} /></span>
        <span className="attBarPct">{share === null ? t('attTeam.glance.noShift') : t('attTeam.glance.pct', { pct: formatNumber(share), shift: hours(row.scheduled_seconds) })}</span>
      </li>;
    })}</ul>
  </FinCard>;
}

export function ReportStatusCard({ rows, people, onEdit, onView }: {
  rows: AttendanceTeamRow[]; people: TeamPerson[]; onEdit: (day: string) => void; onView: (row: AttendanceTeamRow) => void;
}) {
  const visible = rows.filter((r) => r.report !== null);
  const hidden = rows.length - visible.length;
  const submitted = visible.filter((r) => r.report?.status === 'submitted').length;
  const drafts = visible.filter((r) => r.report?.status === 'draft').length;
  const pending = visible.length - submitted - drafts;
  return <FinCard title={t('attTeam.status')} icon={<ClipboardCheck />} className="attStatusCard"
    actions={<small className="muted">{t('attTeam.statusCounts', { submitted: formatNumber(submitted), pending: formatNumber(pending), drafts: formatNumber(drafts) })}</small>}>
    <ul className="attStatusList">{visible.map((row) => {
      const name = personName(row.user_id, people, row.me);
      const chip = reportChip(row);
      const report = row.report!;
      return <li key={row.user_id}>
        <Avatar name={name} />
        <div><strong dir="auto">{name}</strong><small dir="auto">{report.summary ?? t('attTeam.status.noSummary')}</small></div>
        <StatusChip tone={chip.tone}>{chip.text}</StatusChip>
        {row.me
          ? <button type="button" className="secondary attSmall" onClick={() => onEdit(row.day)}><PenLine aria-hidden="true" />{report.status === 'none' ? t('attTeam.write') : t('attTeam.edit')}</button>
          : report.status !== 'none'
            ? <button type="button" className="secondary attSmall" aria-label={t('attTeam.reviewFor', { name })} onClick={() => onView(row)}><Eye aria-hidden="true" />{t('attTeam.review')}</button>
            : <span className="muted attNoReport">{t('attTeam.status.noReport')}</span>}
      </li>;
    })}</ul>
    {hidden > 0 && <p className="muted attNote">{t('attTeam.status.hidden', { count: formatNumber(hidden) })}</p>}
  </FinCard>;
}

/** A colleague's (or the person's own) report, read only. */
export function ReportDialog({ userId, me, day, name, onClose }: { userId: string; me: boolean; day: string; name: string; onClose: () => void }) {
  const [load, setLoad] = useState<Load<AttendanceDailyReport | null>>({ kind: 'loading' });
  const fetchIt = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try { setLoad({ kind: 'ready', value: await api.getReport(day, me ? null : userId) }); }
    catch (e) { if (e instanceof ApiError && e.status === 404) setLoad({ kind: 'ready', value: null }); else setLoad(failed(e)); }
  }, [day, me, userId]);
  useEffect(() => { void fetchIt(); }, [fetchIt]);
  const report = load.kind === 'ready' ? load.value : null;
  return <Modal title={t('attTeam.dialogTitle', { name, day: dayLabel(day) })} onClose={onClose} className="attDialog">
    {load.kind === 'loading' && <Notice busy>{t('att.daily.loadingOne')}</Notice>}
    {load.kind === 'error' && <LoadError message={t('att.daily.error', { message: load.message })} retry={() => void fetchIt()} />}
    {load.kind === 'denied' && <Notice error>{t('att.daily.notShared')}</Notice>}
    {load.kind === 'ready' && !report && <Empty icon={<FileText aria-hidden="true" />} title={t('att.daily.noneTitle')} text={t('att.daily.noneShared')} />}
    {report && <>
      <p className="attStack"><StatusChip tone={report.submitted_at ? 'ok' : 'info'}>{report.submitted_at ? t('attTeam.report.submitted') : t('attTeam.report.draft')}</StatusChip>
        <small className="muted">{report.submitted_at ? t('attTeam.submittedOn', { when: formatDateTime(report.submitted_at) }) : t('attTeam.editedOn', { when: formatDateTime(report.updated_at) })}</small></p>
      <ReportFields report={report} />
      {!me && <p className="muted attNote">{t('attTeam.readOnly')}</p>}
    </>}
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('attTeam.close')}</button></div>
  </Modal>;
}

export function ReportFields({ report }: { report: AttendanceDailyReport }) {
  const list = (items: string[]) => (items.length ? <ul>{items.map((x, i) => <li key={`${i}-${x}`} dir="auto">{x}</li>)}</ul> : '-');
  return <dl className="attReport">
    {report.project && <div><dt>{t('att.daily.project')}</dt><dd dir="auto">{report.project}</dd></div>}
    <div><dt>{t('attTeam.q.done')}</dt><dd>{list(report.tasks_done)}</dd></div>
    <div><dt>{t('attTeam.q.blocker')}</dt><dd dir="auto">{report.issues ?? '-'}</dd></div>
    <div><dt>{t('attTeam.q.next')}</dt><dd>{list(report.tasks_pending)}</dd></div>
    {report.site_visits.length > 0 && <div><dt>{t('att.daily.visits')}</dt><dd dir="auto">{report.site_visits.map((v) => v.name).join(', ')}</dd></div>}
    <div><dt>{t('att.daily.priority')}</dt><dd>{t(`att.priority.${report.priority}` as MessageKey)}</dd></div>
    {report.notes && <div><dt>{t('att.daily.notes')}</dt><dd dir="auto" className="attPre">{report.notes}</dd></div>}
  </dl>;
}

// ---- Daily reports table ------------------------------------------------------------------------------

const RANGES = [7, 30, 90] as const;
const PER_PERSON = 100;

export function ReportsTable({ rows, people, anchor, compact, contributor, onContributor, onEdit, onView }: {
  rows: AttendanceTeamRow[]; people: TeamPerson[]; anchor: string; compact?: boolean; contributor: string; onContributor: (userId: string) => void;
  onEdit: (day: string) => void; onView: (item: ContributorReport) => void;
}) {
  const [range, setRange] = useState<typeof RANGES[number]>(7);
  const [status, setStatus] = useState<'' | ReportStatus>('');
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [shown, setShown] = useState(compact ? 5 : 10);
  const [load, setLoad] = useState<Load<{ items: ContributorReport[]; capped: boolean }>>({ kind: 'loading' });
  const contributors = useMemo(() => rows.filter((r) => r.reports_shared), [rows]);
  const key = contributors.map((r) => r.user_id).join(',');
  const ids = { range: useId(), who: useId(), status: useId(), search: useId() };
  const fetchAll = useCallback(async () => {
    setLoad({ kind: 'loading' });
    const from = shiftDay(anchor, -(range - 1));
    const wanted = contributors.filter((r) => !contributor || r.user_id === contributor);
    try {
      const pages = await Promise.all(wanted.map(async (r) => ({ userId: r.user_id, me: r.me, page: await api.listReports({ from, to: anchor, q: search || undefined, limit: PER_PERSON, offset: 0, person: r.me ? null : r.user_id }) })));
      setLoad({ kind: 'ready', value: { items: mergeReports(pages.map((p) => ({ userId: p.userId, me: p.me, items: p.page.items }))), capped: pages.some((p) => p.page.total > p.page.items.length) } });
    } catch (e) { setLoad(failed(e)); }
  }, [key, contributor, range, anchor, search]);
  useEffect(() => { void fetchAll(); }, [fetchAll]);
  useEffect(() => { setShown(compact ? 5 : 10); }, [compact, contributor, range, status, search]);
  const items = load.kind === 'ready' ? filterReports(load.value.items, status) : [];
  const name = (item: ContributorReport) => personName(item.userId, people, item.me);
  return <FinCard title={t('attTeam.reports')} icon={<FileText />} className="attReportsCard">
    <form className="attFilters" role="search" onSubmit={(e) => { e.preventDefault(); setSearch(q.trim()); }}>
      <label htmlFor={ids.range}><span className="visuallyHidden">{t('attTeam.f.range')}</span>
        <select id={ids.range} value={range} onChange={(e) => setRange(Number(e.target.value) as typeof RANGES[number])}>
          {RANGES.map((n) => <option key={n} value={n}>{t('attTeam.f.lastDays', { n })}</option>)}</select></label>
      <label htmlFor={ids.who}><span className="visuallyHidden">{t('attTeam.f.contributor')}</span>
        <select id={ids.who} value={contributor} onChange={(e) => onContributor(e.target.value)}>
          <option value="">{t('attTeam.f.allContributors')}</option>
          {contributors.map((r) => <option key={r.user_id} value={r.user_id}>{personName(r.user_id, people, r.me)}</option>)}</select></label>
      <label htmlFor={ids.status}><span className="visuallyHidden">{t('attTeam.f.status')}</span>
        <select id={ids.status} value={status} onChange={(e) => setStatus(e.target.value as '' | ReportStatus)}>
          <option value="">{t('attTeam.f.allStatuses')}</option>
          <option value="submitted">{t('attTeam.report.submitted')}</option>
          <option value="draft">{t('attTeam.report.draft')}</option></select></label>
      {!compact && <>
        <label htmlFor={ids.search} className="attSearch"><span className="visuallyHidden">{t('att.daily.search')}</span>
          <input id={ids.search} type="search" dir="auto" placeholder={t('att.daily.search')} value={q} maxLength={120} onChange={(e) => setQ(e.target.value)} /></label>
        <button type="submit" className="secondary attSmall">{t('att.daily.find')}</button>
      </>}
    </form>
    {load.kind === 'loading' && <Notice busy>{t('att.daily.loading')}</Notice>}
    {load.kind === 'error' && <LoadError message={t('att.daily.error', { message: load.message })} retry={() => void fetchAll()} />}
    {load.kind === 'denied' && <Notice error>{t('att.daily.notShared')}</Notice>}
    {load.kind === 'ready' && (items.length === 0
      ? <Empty icon={<FileText aria-hidden="true" />} title={search ? t('att.daily.noMatch') : t('attTeam.reportsEmptyTitle')}
          text={search ? t('att.daily.noMatchText') : t('attTeam.reportsEmptyText')} />
      : <>
        <div className="attRosterWrap"><table className="finGrid attReportsTable">
          <caption className="visuallyHidden">{t('attTeam.reports')}</caption>
          <thead><tr>
            <th scope="col">{t('attTeam.col.date')}</th><th scope="col">{t('attTeam.col.contributor')}</th><th scope="col">{t('attTeam.q.done')}</th>
            <th scope="col">{t('attTeam.q.blocker')}</th>{!compact && <th scope="col">{t('attTeam.q.next')}</th>}<th scope="col">{t('attTeam.col.submitted')}</th>
            <th scope="col">{t('attTeam.col.status')}</th><th scope="col">{t('attTeam.col.action')}</th>
          </tr></thead>
          <tbody>{items.slice(0, shown).map((item) => {
            const r = item.report;
            const who = name(item);
            const zone = rows.find((x) => x.user_id === item.userId)?.time_zone ?? 'UTC';
            return <tr key={`${item.userId}-${r.day}`}>
              <td data-label={t('attTeam.col.date')}>{dayLabel(r.day)}</td>
              <th scope="row" data-label={t('attTeam.col.contributor')} dir="auto">{who}</th>
              <td data-label={t('attTeam.q.done')} dir="auto">{summaryOf(r) || '-'}</td>
              <td data-label={t('attTeam.q.blocker')} dir="auto">{r.issues ?? '-'}</td>
              {!compact && <td data-label={t('attTeam.q.next')} dir="auto">{r.tasks_pending[0] ?? '-'}</td>}
              <td data-label={t('attTeam.col.submitted')}>{r.submitted_at ? <span dir="ltr">{clock(r.submitted_at, zone)}</span> : '-'}</td>
              <td data-label={t('attTeam.col.status')}><StatusChip tone={reportStatus(r) === 'submitted' ? 'ok' : 'info'}>{t(`attTeam.report.${reportStatus(r)}` as MessageKey)}</StatusChip></td>
              <td data-label={t('attTeam.col.action')}>{item.me
                ? <button type="button" className="secondary attSmall" aria-label={t('attTeam.editFor', { day: dayLabel(r.day) })} onClick={() => onEdit(r.day)}>{t('attTeam.edit')}</button>
                : <button type="button" className="secondary attSmall" aria-label={t('attTeam.reviewOf', { name: who, day: dayLabel(r.day) })} onClick={() => onView(item)}>{t('attTeam.review')}</button>}</td>
            </tr>;
          })}</tbody>
        </table></div>
        <div className="attTableFoot">
          <small className="muted">{t('attTeam.showing', { shown: formatNumber(Math.min(shown, items.length)), total: formatNumber(items.length) })}{load.value.capped ? ` ${t('attTeam.capped', { n: PER_PERSON })}` : ''}</small>
          {shown < items.length && <button type="button" className="secondary attSmall" onClick={() => setShown(shown + (compact ? 5 : 10))}>{t('attTeam.showMore')}</button>}
        </div>
      </>)}
  </FinCard>;
}

// ---- Quick report -------------------------------------------------------------------------------------

export function QuickReportCard({ day, myName, onSaved }: { day: string; myName: string; onSaved: () => void }) {
  const [load, setLoad] = useState<Load<AttendanceDailyReport | null>>({ kind: 'loading' });
  const [form, setForm] = useState<QuickReport>(quickFrom(null));
  const [busy, setBusy] = useState<'save' | 'submit' | null>(null);
  const [review, setReview] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const ids = { error: useId(), done: useId(), blocker: useId(), next: useId() };
  const fetchIt = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try { const r = await api.getReport(day); setLoad({ kind: 'ready', value: r }); setForm(quickFrom(r)); }
    catch (e) { if (e instanceof ApiError && e.status === 404) { setLoad({ kind: 'ready', value: null }); setForm(quickFrom(null)); } else setLoad(failed(e)); }
  }, [day]);
  useEffect(() => { void fetchIt(); }, [fetchIt]);
  const report = load.kind === 'ready' ? load.value : null;
  const body = () => {
    const base = report ? draftFromReport(report) : emptyReportDraft();
    return reportBody({ ...base, done: form.done, issues: form.blocker, pending: form.next }, [], report?.version ?? null);
  };
  const check = (): boolean => {
    if (!lines(form.done).length) { setError(t('attTeam.q.doneRequired')); return false; }
    const b = body();
    if (!b.ok) { setError(b.error); return false; }
    setError(null);
    return true;
  };
  const save = async (submit: boolean) => {
    const b = body();
    if (!b.ok) { setError(b.error); return; }
    // Keep the places a report written on the full form already named.
    const value = { ...b.value, site_visits: report?.site_visits ?? b.value.site_visits, submit };
    setBusy(submit ? 'submit' : 'save'); setError(null); setFlash(null);
    try {
      const saved = await api.saveReport(day, value);
      setLoad({ kind: 'ready', value: saved }); setForm(quickFrom(saved)); setReview(false);
      setFlash(submit ? t('attTeam.q.submitted') : t('attTeam.q.saved')); onSaved();
    } catch (e) { setError(describeError(e)); setReview(false); } finally { setBusy(null); }
  };
  const submitted = Boolean(report?.submitted_at);
  return <FinCard title={t('attTeam.q.title', { day: dayLabel(day) })} icon={<PenLine />} className="attQuick" subtitle={<span dir="auto">{myName}</span>}
    actions={report ? <StatusChip tone={submitted ? 'ok' : 'info'}>{submitted ? t('attTeam.report.submitted') : t('attTeam.report.draft')}</StatusChip> : undefined}>
    {load.kind === 'loading' && <Notice busy>{t('att.daily.loadingOne')}</Notice>}
    {load.kind === 'error' && <LoadError message={t('att.daily.error', { message: load.message })} retry={() => void fetchIt()} />}
    {load.kind === 'ready' && <form className="attQuickForm" noValidate onSubmit={(e: FormEvent) => { e.preventDefault(); if (check()) setReview(true); }} aria-describedby={error ? ids.error : undefined}>
      <label htmlFor={ids.done}>{t('attTeam.q.done')} <span aria-hidden="true">*</span></label>
      <textarea id={ids.done} dir="auto" rows={2} required aria-required="true" value={form.done} onChange={(e) => setForm({ ...form, done: e.target.value })} />
      <label htmlFor={ids.blocker}>{t('attTeam.q.blockerLabel')}</label>
      <input id={ids.blocker} dir="auto" maxLength={4000} value={form.blocker} onChange={(e) => setForm({ ...form, blocker: e.target.value })} />
      <label htmlFor={ids.next}>{t('attTeam.q.nextLabel')}</label>
      <input id={ids.next} dir="auto" value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} />
      <FormError id={ids.error} text={error} />
      {flash && <Notice>{flash}</Notice>}
      <div className="attQuickFoot">
        <small className="muted"><CircleAlert aria-hidden="true" className="cmInlineIcon" />{submitted ? t('attTeam.q.submittedNote', { when: formatDateTime(report!.submitted_at!) }) : t('attTeam.q.note')}</small>
        <div className="actions">
          <button type="button" className="secondary" disabled={busy !== null} onClick={() => { if (check()) void save(false); }}>{busy === 'save' ? t('att.saving') : submitted ? t('attTeam.q.saveChanges') : t('attTeam.q.saveDraft')}</button>
          {!submitted && <button type="submit" disabled={busy !== null}><ClipboardCheck aria-hidden="true" />{t('attTeam.q.review')}</button>}
        </div>
      </div>
    </form>}
    {review && <ConfirmDialog title={t('attTeam.q.reviewTitle', { day: dayLabel(day) })} confirmLabel={t('attTeam.q.submit')} busy={busy === 'submit'}
      onConfirm={() => void save(true)} onCancel={() => setReview(false)}>
      <dl className="attReport">
        <div><dt>{t('attTeam.q.done')}</dt><dd><ul>{lines(form.done).map((x, i) => <li key={`${i}-${x}`} dir="auto">{x}</li>)}</ul></dd></div>
        <div><dt>{t('attTeam.q.blocker')}</dt><dd dir="auto">{form.blocker.trim() || '-'}</dd></div>
        <div><dt>{t('attTeam.q.next')}</dt><dd dir="auto">{lines(form.next).join('; ') || '-'}</dd></div>
      </dl>
      <p className="muted">{t('attTeam.q.reviewNote')}</p>
    </ConfirmDialog>}
  </FinCard>;
}

// ---- Quick correction ---------------------------------------------------------------------------------

interface Issue { id: string; day: string; kind: 'arrive' | 'leave'; label: string }

/** The days the person's own record flags, as fixes the quick form can make (add the missing record). */
export function issuesFrom(days: readonly AttendanceDay[]): Issue[] {
  const out: Issue[] = [];
  for (const d of [...days].sort((a, b) => b.day.localeCompare(a.day))) {
    if (d.reduced) continue;
    if (d.flags.includes('missing_checkout')) out.push({ id: `${d.day}-leave`, day: d.day, kind: 'leave', label: t('attTeam.c.issue', { what: flagLabel('missing_checkout'), day: dayLabel(d.day) }) });
    if (d.flags.includes('no_arrival')) out.push({ id: `${d.day}-arrive`, day: d.day, kind: 'arrive', label: t('attTeam.c.issue', { what: flagLabel('no_arrival'), day: dayLabel(d.day) }) });
  }
  return out;
}

export function ReasonFields({ preset, details, onPreset, onDetails, ids }: {
  preset: ReasonPreset | ''; details: string; onPreset: (p: ReasonPreset | '') => void; onDetails: (d: string) => void; ids: { reason: string; details: string };
}) {
  return <>
    <label htmlFor={ids.reason}>{t('attTeam.c.reason')} <span aria-hidden="true">*</span></label>
    <select id={ids.reason} required aria-required="true" value={preset} onChange={(e) => onPreset(e.target.value as ReasonPreset | '')}>
      <option value="">{t('attTeam.c.reasonPick')}</option>
      {REASON_PRESETS.map((p) => <option key={p} value={p}>{t(`attTeam.reason.${p}` as MessageKey)}</option>)}
    </select>
    <label htmlFor={ids.details}>{preset === 'other' ? t('attTeam.c.detailsRequired') : t('attTeam.c.details')}</label>
    <textarea id={ids.details} dir="auto" rows={2} maxLength={400} placeholder={t('attTeam.c.detailsHint')} value={details} onChange={(e) => onDetails(e.target.value)} />
  </>;
}

export function AuditTrail({ items, timeZone }: { items: AttendanceCorrection[]; timeZone: string }) {
  if (!items.length) return <p className="muted attNote">{t('attTeam.c.noTrail')}</p>;
  return <ol className="attTrail">{items.map((c) => <li key={c.id}>
    <strong>{t(`att.fix.did.${c.action}` as MessageKey)}</strong>
    <small>{formatDateTime(c.created_at)}</small>
    <small dir="auto">{c.reason}</small>
    <small>{[c.before ? t('att.fix.before', { what: kindLabel(c.before.kind), when: clock(c.before.occurred_at, timeZone) }) : '', c.after ? t('att.fix.after', { what: kindLabel(c.after.kind), when: clock(c.after.occurred_at, timeZone) }) : ''].filter(Boolean).join(' · ')}</small>
    <small className="attApplied">{t('attTeam.c.applied')}</small>
  </li>)}</ol>;
}

export function QuickCorrectionCard({ today, timeZone, onChanged, onOpenCorrections }: { today: string; timeZone: string; onChanged: () => void; onOpenCorrections: () => void }) {
  const [days, setDays] = useState<Load<AttendanceDay[]>>({ kind: 'loading' });
  const [trail, setTrail] = useState<Load<AttendancePage<AttendanceCorrection>>>({ kind: 'loading' });
  const [issue, setIssue] = useState('');
  const [time, setTime] = useState('');
  const [preset, setPreset] = useState<ReasonPreset | ''>('');
  const [details, setDetails] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const ids = { issue: useId(), time: useId(), reason: useId(), details: useId(), error: useId() };
  const fetchIt = useCallback(async () => {
    const [d, c] = await Promise.allSettled([api.listDays(shiftDay(today, -13), today), api.listCorrections(3, 0)]);
    setDays(d.status === 'fulfilled' ? { kind: 'ready', value: d.value } : failed(d.reason));
    setTrail(c.status === 'fulfilled' ? { kind: 'ready', value: c.value } : failed(c.reason));
  }, [today]);
  useEffect(() => { void fetchIt(); }, [fetchIt]);
  const issues = days.kind === 'ready' ? issuesFrom(days.value) : [];
  const picked = issues.find((i) => i.id === issue) ?? null;
  const reset = () => { setIssue(''); setTime(''); setPreset(''); setDetails(''); setError(null); };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!picked) { setError(t('attTeam.c.pickIssue')); return; }
    const when = localToIso(picked.day, time, timeZone);
    if (!when) { setError(t('att.form.clock')); return; }
    const reason = reasonText(preset, details);
    if (reason.trim().length < 3) { setError(t('att.form.reason')); return; }
    setBusy(true); setError(null); setFlash(null);
    try { await api.addCorrection({ action: 'add', kind: picked.kind, occurred_at: when, reason }); reset(); setFlash(t('att.fix.saved')); await fetchIt(); onChanged(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <FinCard title={t('attTeam.c.title')} icon={<History />} className="attQuickFix"
    actions={days.kind === 'ready' ? <StatusChip tone={issues.length ? 'warn' : 'ok'}>{issues.length ? t('attTeam.c.due', { n: formatNumber(issues.length) }) : t('attTeam.c.nothingDue')}</StatusChip> : undefined}>
    <div className="attFixGrid">
      <div>
        {days.kind === 'loading' && <Notice busy>{t('att.fix.loading')}</Notice>}
        {days.kind === 'error' && <LoadError message={t('att.fix.error', { message: days.message })} retry={() => void fetchIt()} />}
        {days.kind === 'ready' && (issues.length === 0
          ? <div className="attQuickEmpty"><p className="muted">{t('attTeam.c.none')}</p><button type="button" className="secondary attSmall" onClick={onOpenCorrections}>{t('attTeam.c.open')}</button></div>
          : <form className="attQuickForm" noValidate onSubmit={(e) => void submit(e)} aria-describedby={error ? ids.error : undefined}>
            <label htmlFor={ids.issue}>{t('attTeam.c.issueLabel')}</label>
            <select id={ids.issue} value={issue} onChange={(e) => setIssue(e.target.value)}>
              <option value="">{t('attTeam.c.issuePick')}</option>
              {issues.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}</select>
            <label htmlFor={ids.time}>{picked?.kind === 'arrive' ? t('attTeam.c.timeIn', { zone: timeZone }) : t('attTeam.c.timeOut', { zone: timeZone })}</label>
            <input id={ids.time} type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            <ReasonFields preset={preset} details={details} onPreset={setPreset} onDetails={setDetails} ids={ids} />
            <FormError id={ids.error} text={error} />
            {flash && <Notice>{flash}</Notice>}
            <div className="actions">
              <button type="button" className="secondary" disabled={busy} onClick={reset}>{t('attTeam.c.cancel')}</button>
              <button type="submit" disabled={busy}>{busy ? t('att.saving') : t('attTeam.c.submit')}</button>
            </div>
          </form>)}
        {days.kind === 'ready' && issues.length === 0 && flash && <Notice>{flash}</Notice>}
      </div>
      <div className="attTrailBox" aria-labelledby={ids.details + '-trail'} role="region">
        <h3 id={ids.details + '-trail'}>{t('attTeam.c.trail')}</h3>
        {trail.kind === 'loading' && <Notice busy>{t('att.fix.loading')}</Notice>}
        {trail.kind === 'error' && <LoadError message={t('att.fix.error', { message: trail.message })} retry={() => void fetchIt()} />}
        {trail.kind === 'ready' && <AuditTrail items={trail.value.items} timeZone={timeZone} />}
      </div>
    </div>
  </FinCard>;
}

// ---- Features strip and privacy note ------------------------------------------------------------------

export function FeatureStrip({ onTimesheet, onSettings, onDaily }: { onTimesheet: () => void; onSettings: () => void; onDaily: () => void }) {
  const items: { icon: ReactNode; title: string; text: string; go: () => void }[] = [
    { icon: <BarChart3 />, title: t('attTeam.x.insights'), text: t('attTeam.x.insightsText'), go: onTimesheet },
    { icon: <Bell />, title: t('attTeam.x.reminders'), text: t('attTeam.x.remindersText'), go: onSettings },
    { icon: <CalendarClock />, title: t('attTeam.x.rules'), text: t('attTeam.x.rulesText'), go: onSettings },
    { icon: <Sparkles />, title: t('attTeam.x.drafts'), text: t('attTeam.x.draftsText'), go: onDaily },
  ];
  return <section className="attStrip" aria-labelledby="att-strip">
    <div className="attStripLead"><h2 id="att-strip">{t('attTeam.x.title')}</h2><p className="muted">{t('attTeam.x.lead')}</p></div>
    <ul>{items.map((item) => <li key={item.title}><button type="button" className="attStripItem" onClick={item.go}>
      <span className="attStripIcon" aria-hidden="true">{item.icon}</span><span><strong>{item.title}</strong><small>{item.text}</small></span></button></li>)}</ul>
  </section>;
}

export function PrivacyNote({ onSettings }: { onSettings: () => void }) {
  return <FinBanner tone="ok" icon={<ShieldCheck />}>
    <p className="attPrivacy"><strong>{t('attTeam.privacy.title')}</strong> {t('attTeam.privacy.text')}{' '}
      <button type="button" className="cmLinkButton" onClick={onSettings}>{t('attTeam.privacy.link')}</button></p>
  </FinBanner>;
}

export { filterBySite };
