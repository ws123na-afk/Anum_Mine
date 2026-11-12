// The workspace attendance view (docs/attendance.md#manager-view): off by default; a workspace owner (or an
// organisation administrator) turns it on, then reads everyone's day in this workspace, read only, a page at a
// time, and exports a date range as one CSV. Daily reports and corrections are never part of it: they stay with
// the person and their own shares. Lazy chunk, loaded only when the Workspace tab opens.
import { useCallback, useEffect, useId, useState } from 'react';
import { Building2, Download, Eye, EyeOff, MapPin, ShieldCheck, Users } from 'lucide-react';
import type { AttendanceTeam, AttendanceWorkspaceSettings, TeamPerson } from '@anum/contracts';
import * as api from './lib/attendanceApi';
import { clock, flagLabel, hours, shiftDay } from './lib/attendance';
import { breakText, personName, sourceLabel } from './lib/attendanceTeam';
import { describeError } from './lib/errors';
import { formatNumber, t } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { Empty, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import { stateChip } from './AttendanceTeam';

export const MANAGER_PAGE = 25;

export default function AttendanceWorkspace({ settings, people, today, day, onSettings }: {
  settings: AttendanceWorkspaceSettings; people: TeamPerson[]; today: string; day: string; onSettings: (next: AttendanceWorkspaceSettings) => void;
}) {
  const [asking, setAsking] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(on: boolean) {
    setBusy(true); setError(null);
    try { onSettings(await api.saveWorkspaceSettings(on, settings.version)); setAsking(null); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  if (!settings.can_manage) {
    return <section className="surface cmPanel cmDenied" aria-labelledby="att-mgr-denied"><Eye aria-hidden="true" /><div>
      <h2 id="att-mgr-denied">{t('attMgr.denied.title')}</h2><p className="muted">{t('attMgr.denied.text')}</p></div></section>;
  }
  return <>
    <FinCard title={t('attMgr.switch.title')} icon={<Building2 />} className="attMgrSwitch"
      actions={<StatusChip tone={settings.manager_view ? 'ok' : 'muted'}>{t(settings.manager_view ? 'attMgr.switch.on' : 'attMgr.switch.off')}</StatusChip>}>
      <p className="muted">{t('attMgr.switch.text')}</p>
      <ul className="attMgrRules">
        <li><Eye aria-hidden="true" /><span>{t('attMgr.rule.who')}</span></li>
        <li><EyeOff aria-hidden="true" /><span>{t('attMgr.rule.private')}</span></li>
        <li><ShieldCheck aria-hidden="true" /><span>{t('attMgr.rule.told')}</span></li>
      </ul>
      <FormError id="att-mgr-switch-error" text={error} />
      <div className="actions">
        {settings.manager_view
          ? <button type="button" className="secondary" disabled={busy} onClick={() => setAsking(false)}>{t('attMgr.switch.turnOff')}</button>
          : <button type="button" disabled={busy} onClick={() => setAsking(true)}>{t('attMgr.switch.turnOn')}</button>}
      </div>
    </FinCard>
    {settings.manager_view
      ? <Roster key={day} people={people} today={today} day={day} />
      : <Empty icon={<Users aria-hidden="true" />} title={t('attMgr.off.title')} text={t('attMgr.off.text')}
        action={<button type="button" onClick={() => setAsking(true)}>{t('attMgr.switch.turnOn')}</button>} />}
    {asking !== null && <ConfirmDialog title={t(asking ? 'attMgr.confirm.onTitle' : 'attMgr.confirm.offTitle')}
      confirmLabel={t(asking ? 'attMgr.switch.turnOn' : 'attMgr.switch.turnOff')} busy={busy} onConfirm={() => void save(asking)} onCancel={() => setAsking(null)}>
      <p>{t(asking ? 'attMgr.confirm.onText' : 'attMgr.confirm.offText')}</p>
      {error && <p className="formError" role="alert">{error}</p>}
    </ConfirmDialog>}
  </>;
}

// The day is the page's own Day control (the page head), so there is one day picker on the page.
function Roster({ people, today, day }: { people: TeamPerson[]; today: string; day: string }) {
  const ids = { from: useId(), to: useId() };
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<AttendanceTeam>>({ kind: 'loading' });
  const [from, setFrom] = useState(shiftDay(today, -29));
  const [to, setTo] = useState(today);
  const [exporting, setExporting] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    setPage((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setPage({ kind: 'ready', value: await api.getManagerView(day === today ? null : day, MANAGER_PAGE, offset) }); } catch (e) { setPage(failed(e)); }
  }, [day, offset, today]);
  useEffect(() => { void load(); }, [load]);
  async function exportCsv() {
    if (!from || !to || from > to) { setNote({ text: t('attMgr.export.range'), error: true }); return; }
    setExporting(true); setNote(null);
    try { setNote({ text: t('attMgr.export.saved', { name: await api.downloadManagerCsv(from, to) }) }); }
    catch (e) { setNote({ text: t('attMgr.export.failed', { message: describeError(e) }), error: true }); }
    finally { setExporting(false); }
  }
  const value = page.kind === 'ready' ? page.value : null;
  return <>
    <FinCard title={t('attMgr.roster.title')} icon={<Users />} className="attMgrRoster"
      subtitle={value ? <span>{t('attMgr.roster.count', { count: formatNumber(value.total) })}</span> : undefined}>
      {page.kind === 'loading' && <Notice busy>{t('attMgr.loading')}</Notice>}
      {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={t('attMgr.error', { message: page.message })} retry={() => void load()} />}
      {value && (value.rows.length === 0
        ? <Empty icon={<Users aria-hidden="true" />} title={t('attMgr.empty.title')} text={t('attMgr.empty.text')} />
        : <div className="attRosterWrap" tabIndex={0} role="region" aria-label={t('attMgr.roster.title')}><table className="finGrid attRosterTable">
          <caption className="visuallyHidden">{t('attMgr.roster.caption', { day })}</caption>
          <thead><tr>
            <th scope="col">{t('attTeam.col.member')}</th><th scope="col">{t('attTeam.col.status')}</th><th scope="col">{t('attTeam.col.firstIn')}</th>
            <th scope="col">{t('attTeam.col.break')}</th><th scope="col" className="num">{t('attTeam.col.hours')}</th><th scope="col">{t('attTeam.col.site')}</th>
            <th scope="col">{t('attMgr.col.flags')}</th>
          </tr></thead>
          <tbody>{value.rows.map((row) => {
            const name = personName(row.user_id, people, row.me);
            const state = stateChip(row);
            return <tr key={row.user_id}>
              <th scope="row" data-label={t('attTeam.col.member')}><span className="attStack"><strong dir="auto">{name}</strong>{row.role_title && <small dir="auto">{row.role_title}</small>}</span></th>
              <td data-label={t('attTeam.col.status')}><StatusChip tone={state.tone}>{state.text}</StatusChip></td>
              <td data-label={t('attTeam.col.firstIn')}>{row.first_check_in ? <span className="attStack"><span dir="ltr">{clock(row.first_check_in, row.time_zone)}</span><small>{sourceLabel(row.first_check_in_source)}</small></span> : <span className="muted">{t('attTeam.notIn')}</span>}</td>
              <td data-label={t('attTeam.col.break')}>{breakText(row)}</td>
              <td data-label={t('attTeam.col.hours')} className="num">{row.first_check_in || row.worked_seconds ? hours(row.worked_seconds) : '-'}</td>
              <td data-label={t('attTeam.col.site')}>{row.site_names.length ? <span className="attSiteName"><MapPin aria-hidden="true" /><span dir="auto">{row.site_names.join(', ')}</span></span> : '-'}</td>
              <td data-label={t('attMgr.col.flags')}>{row.flags.length ? row.flags.map((f) => flagLabel(f)).join(', ') : '-'}</td>
            </tr>;
          })}</tbody>
        </table></div>)}
      {value && value.total > MANAGER_PAGE && <nav className="cmPager" aria-label={t('attMgr.pages')}>
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - MANAGER_PAGE))}>{t('attMgr.previous')}</button>
        <span className="muted">{t('attMgr.pageOf', { from: formatNumber(offset + 1), to: formatNumber(Math.min(offset + MANAGER_PAGE, value.total)), total: formatNumber(value.total) })}</span>
        <button type="button" className="secondary" disabled={offset + MANAGER_PAGE >= value.total} onClick={() => setOffset(offset + MANAGER_PAGE)}>{t('attMgr.next')}</button>
      </nav>}
      <p className="muted attNote">{t('attMgr.readOnly')}</p>
    </FinCard>
    <FinCard title={t('attMgr.export.title')} icon={<Download />} className="attMgrExport">
      <p className="muted">{t('attMgr.export.text')}</p>
      <div className="attMgrExportRow">
        <label className="field"><span>{t('attMgr.export.from')}</span><input id={ids.from} type="date" value={from} max={to || today} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="field"><span>{t('attMgr.export.to')}</span><input id={ids.to} type="date" value={to} max={today} onChange={(e) => setTo(e.target.value)} /></label>
        <button type="button" disabled={exporting} onClick={() => void exportCsv()}><Download aria-hidden="true" />{exporting ? t('attMgr.export.busy') : t('attMgr.export.button')}</button>
      </div>
      {note && <p className={note.error ? 'formError' : 'muted'} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
    </FinCard>
  </>;
}
