// The web Calendar page (docs/calendar.md#web-page): a day or week time grid, a month of days or the
// week as a list, with a side rail (small month, calendars and event types, double bookings, the next
// event, the time zone), the selected event beside it, and a strip with the range's figures. New
// event / Edit / Cancel / Delete go through the calendar API; Next steps and Sync are tabs. Times show
// in the browser's time zone; the API stores UTC.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import {
  AlertTriangle, ArrowLeft, CalendarCheck, CalendarDays, CalendarPlus, CheckCircle2, ChevronLeft, ChevronRight, ClipboardList, Clock, ExternalLink, FileText, Globe2, Lightbulb, Mail,
  MapPin, Pencil, Phone, Plus, RefreshCw, RotateCcw, Search, ShieldCheck, Trash2, UserRound, Users, Video, X, XCircle,
} from 'lucide-react';
import * as api from './lib/api';
import * as calendarApi from './lib/calendarApi';
import * as meetingsApi from './lib/meetingsApi';
import {
  ATTENDEE_RESPONSES, CALENDAR_MODES, conflictIds, dayKey, durationMinutes, emptyForm, EVENT_KINDS, eventBody, eventTiming, findConflicts, formFromEvent, formProblem, groupByDay,
  CALENDAR_CONNECT_KEY, eventSyncKind, hourWindow, inRange, invitees, layoutDay, matchesEvent, monthDays, nextEvent, rangeStats, replyCounts, shiftAnchor, startOfDay, syncTarget, visibleRange,
  type AttendeeResponse, type CalendarEventView, type CalendarMode, type EventForm, type EventKind, type EventPage, type NextStep, type SyncConnection, type SyncSetup,
} from './lib/calendar';
import { describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { type Load } from './lib/pageSummary';
import { initials, viewLabel } from './lib/shell';
import { ConfirmDialog, Modal } from './Dialog';
import { agoText, SyncPanel } from './CalendarSync';
import { FinancePageHead, FinanceTabs, StatusChip, tabPanel } from './FinanceShell';
import { loadFailure, PaneDenied, PaneProblem, SPLIT_QUERY, useMediaQuery } from './PageParts';
import './admin.css';
import './calendar.css';

const MODE_KEY = 'anum.calendar.mode';
const readMode = (): CalendarMode => {
  try { const saved = localStorage.getItem(MODE_KEY); return (CALENDAR_MODES as readonly string[]).includes(saved ?? '') ? saved as CalendarMode : 'week'; } catch { return 'week'; }
};
const STEP_ICONS = { agenda: <ClipboardList />, follow_up: <Users />, summary: <FileText />, email: <Mail />, approval: <ShieldCheck /> } as const;
const NARROW = '(max-width: 760px)';
type PageTab = 'calendar' | 'steps' | 'sync';

const timeOf = (value: string | Date) => formatDateTime(value, { hour: '2-digit', minute: '2-digit' });
const dayLabel = (date: Date) => formatDateTime(date, { weekday: 'long', day: 'numeric', month: 'long' });
const timeZone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } })();

function durationText(minutes: number): string {
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  if (!hours) return t('calendar.duration.minutes', { minutes: rest });
  return rest ? t('calendar.duration.hoursMinutes', { hours, minutes: rest }) : t('calendar.duration.hours', { hours });
}

function rangeLabelFor(mode: CalendarMode, anchor: Date, days: Date[]): string {
  if (mode === 'day') return dayLabel(anchor);
  if (mode === 'month') return formatDateTime(anchor, { month: 'long', year: 'numeric' });
  return t('calendar.range.week', { from: formatDateTime(days[0], { day: 'numeric', month: 'short' }), to: formatDateTime(days[6], { day: 'numeric', month: 'short', year: 'numeric' }) });
}

export function CalendarView({ onOpenTask, onOpenInbox, onOpenApprovals, onOpenMeetings }: {
  onOpenTask?: (taskId: string) => void; onOpenInbox?: () => void; onOpenApprovals?: () => void; onOpenMeetings?: () => void;
}) {
  const [mode, setModeState] = useState<CalendarMode>(readMode);
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [includeCancelled, setIncludeCancelled] = useState(false);
  const [hiddenKinds, setHiddenKinds] = useState<Set<EventKind>>(new Set());
  const [search, setSearch] = useState('');
  const [page, setPage] = useState<Load<EventPage>>({ state: 'loading' });
  const [steps, setSteps] = useState<Load<NextStep[]>>({ state: 'loading' });
  const [tools, setTools] = useState<Load<{ connected: number; total: number }>>({ state: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<PageTab>('calendar');
  const [sync, setSync] = useState<Load<SyncSetup>>({ state: 'loading' });
  const [form, setForm] = useState<{ event: CalendarEventView | null; day: Date } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [stepBusy, setStepBusy] = useState<string | null>(null);
  const [stepProblem, setStepProblem] = useState<string | null>(null);
  const wide = useMediaQuery(SPLIT_QUERY);
  const narrow = useMediaQuery(NARROW);
  const ids = useId();
  const now = new Date();
  const range = useMemo(() => visibleRange(mode, anchor), [mode, anchor]);

  const setMode = (next: CalendarMode) => { setModeState(next); try { localStorage.setItem(MODE_KEY, next); } catch { /* remembered for this visit only */ } };
  // Only the newest request may fill the calendar: a reload started before a range or filter change
  // must not overwrite the answer for the new one.
  const latest = useRef(0);
  const loadEvents = useCallback(() => {
    const ticket = ++latest.current;
    setPage((old) => (old.state === 'ready' ? old : { state: 'loading' }));
    calendarApi.listEvents(range.start, range.end, includeCancelled).then(
      (value) => { if (ticket === latest.current) setPage({ state: 'ready', value }); },
      (e: unknown) => { if (ticket === latest.current) setPage(loadFailure(e)); },
    );
  }, [range, includeCancelled]);
  const loadSteps = useCallback(() => {
    setSteps({ state: 'loading' });
    calendarApi.getNextSteps().then((value) => setSteps({ state: 'ready', value: value.items }), (e: unknown) => setSteps(loadFailure(e)));
  }, []);
  const loadSync = useCallback(() => {
    calendarApi.getSync().then((value) => setSync({ state: 'ready', value }), (e: unknown) => setSync(loadFailure(e)));
  }, []);
  useEffect(loadEvents, [loadEvents]);
  useEffect(loadSteps, [loadSteps]);
  useEffect(loadSync, [loadSync]);
  // The provider sent the browser back (main.tsx kept the connection id): finish it as this person.
  useEffect(() => {
    let raw: string | null = null;
    try { raw = sessionStorage.getItem(CALENDAR_CONNECT_KEY); sessionStorage.removeItem(CALENDAR_CONNECT_KEY); } catch { raw = null; }
    if (!raw) return;
    let returned: { connection: string | null; error: string | null } | null = null;
    try { returned = JSON.parse(raw) as { connection: string | null; error: string | null }; } catch { returned = null; }
    if (!returned) return;
    setTab('sync');
    if (returned.error || !returned.connection) { setNote(t('calendar.sync.returnError', { code: (returned.error ?? 'unknown').slice(0, 60) })); return; }
    setNote(t('calendar.sync.finishing'));
    calendarApi.completeConnect(returned.connection).then(
      (connection) => { setNote(t('calendar.sync.connected', { email: connection.email, count: connection.events_synced })); loadSync(); loadEvents(); },
      (e: unknown) => setNote(t('calendar.sync.completeFailed', { reason: describeError(e) })),
    );
  }, [loadSync, loadEvents]);
  useEffect(() => {
    meetingsApi.getSetup().then(
      (setup) => setTools({ state: 'ready', value: { connected: setup.connectors.filter((c) => c.connected).length, total: setup.connectors.length } }),
      (e: unknown) => setTools(loadFailure(e)),
    );
  }, []);

  const value = page.state === 'ready' ? page.value : null;
  const events = value?.items ?? [];
  const shown = useMemo(() => events.filter((event) => !hiddenKinds.has(event.kind) && matchesEvent(event, search)), [events, search, hiddenKinds]);
  const clashes = useMemo(() => conflictIds(events), [events]);
  const current = events.find((event) => event.id === selected) ?? null;
  const canWrite = value?.can_write ?? false;
  const stepList = steps.state === 'ready' ? steps.value : [];
  const connections: SyncConnection[] = sync.state === 'ready' ? sync.value.connections : [];
  const target = syncTarget(connections);
  const troubled = connections.find((connection) => connection.status !== 'connected') ?? null;
  const filtered = search.trim() !== '' || hiddenKinds.size > 0;

  // On a wide screen the detail pane opens on the next event still to come in the range.
  useEffect(() => {
    if (!wide || selected || !events.length) return;
    const next = events.find((event) => event.status !== 'cancelled' && Date.parse(event.ends_at) > Date.now()) ?? events[0];
    setSelected(next.id);
  }, [wide, selected, events]);

  const changed = (message: string) => { setNote(message); loadEvents(); loadSteps(); };
  const newEvent = (day?: Date) => setForm({ event: null, day: day ?? (mode === 'day' ? anchor : startOfDay(now)) });
  const goTo = (day: Date, nextMode?: CalendarMode) => { setAnchor(startOfDay(day)); if (nextMode) setMode(nextMode); setTab('calendar'); };

  async function runStep(step: NextStep) {
    setStepProblem(null);
    if (step.action.kind === 'open') {
      if (step.action.destination === 'email_urgent') onOpenInbox?.();
      else if (step.action.destination === 'approvals') onOpenApprovals?.();
      else if (step.action.destination === 'event' && step.action.event_id) { setSelected(step.action.event_id); setTab('calendar'); }
      return;
    }
    if (!step.action.prompt) return;
    setStepBusy(step.id);
    try {
      const result = await api.createAndRunTask(step.action.prompt, step.action.title ?? step.title);
      onOpenTask?.(result.task.id);
    } catch (e) {
      setStepProblem(t('calendar.steps.failed', { reason: describeError(e) }));
    } finally { setStepBusy(null); }
  }

  const rangeLabel = rangeLabelFor(mode, anchor, range.days);
  const prevLabel = mode === 'day' ? t('calendar.prevDay') : mode === 'month' ? t('calendar.prevMonth') : t('calendar.prevWeek');
  const nextLabel = mode === 'day' ? t('calendar.nextDay') : mode === 'month' ? t('calendar.nextMonth') : t('calendar.nextWeek');
  const stats = rangeStats(events, now);
  const tabs = [
    { id: 'calendar' as const, label: t('calendar.tab.calendar') },
    { id: 'steps' as const, label: t('calendar.tab.steps', { count: stepList.length }) },
    { id: 'sync' as const, label: t('calendar.tab.sync'), badge: troubled ? '!' : undefined },
  ];

  const head = <FinancePageHead title={viewLabel('calendar')} subtitle={t('calendar.subtitle')} actions={<>
    {tools.state === 'ready' && <button type="button" className={`secondary chipButton ${tools.value.connected ? 'success' : ''}`} onClick={onOpenMeetings} title={t('calendar.tools.title')}>
      <span className="chipDot" aria-hidden="true" /><span>{t('calendar.tools.chip', { count: tools.value.connected })}</span></button>}
    {canWrite && <button type="button" onClick={() => newEvent()}><CalendarPlus aria-hidden="true" />{t('calendar.new')}</button>}
  </>} tabs={page.state === 'denied' ? undefined : <FinanceTabs label={t('calendar.tabs')} idPrefix={`${ids}-cal`} items={tabs} value={tab} onChange={setTab} />} />;

  if (page.state === 'denied') return <div className="calPage">{head}<section className="pane"><PaneDenied title={t('calendar.denied.title')} text={page.message} /></section></div>;

  const detailOpen = Boolean(current) && tab === 'calendar';
  // Below 1024px the event replaces the calendar until Back (like every split page).
  const showMain = wide || !detailOpen;

  return <div className="calPage">
    {head}
    <p className="muted calendarSyncNote"><CalendarCheck aria-hidden="true" />{troubled
      ? <>{t('calendar.syncNote.problem', { provider: troubled.provider_name })} <button type="button" className="linkButton" onClick={() => setTab('sync')}>{t('calendar.tab.sync')}</button></>
      : target ? t('calendar.syncNote.on', { provider: target.provider_name, email: target.email }) : t('calendar.syncNote.none')}</p>
    {note && <p className="notice" role="status">{note}</p>}
    <div {...tabPanel(`${ids}-cal`, tab)}>
      {tab === 'calendar' && <div className={`calLayout ${detailOpen ? 'withDetail' : ''}`}>
        {showMain && <CalendarRail anchor={anchor} mode={mode} now={now} events={events} page={page} sync={sync} hiddenKinds={hiddenKinds} canWrite={canWrite}
          onPick={(day) => goTo(day, mode === 'agenda' || mode === 'month' ? 'day' : undefined)} onToggleKind={(kind) => setHiddenKinds((old) => { const next = new Set(old); if (next.has(kind)) next.delete(kind); else next.add(kind); return next; })}
          onSelect={(id) => setSelected(id)} onOpenSync={() => setTab('sync')} onSynced={(message) => { setNote(message); loadSync(); loadEvents(); }} />}
        {showMain && <section className="calMain" aria-labelledby={`${ids}-agenda`} aria-busy={page.state === 'loading'}>
          <h2 id={`${ids}-agenda`} className="visuallyHidden">{t('calendar.agenda')}</h2>
          <div className="calToolbar">
            <div className="calNav">
              <button type="button" className="secondary" onClick={() => setAnchor(startOfDay(new Date()))}>{t('calendar.today')}</button>
              <button type="button" className="secondary iconOnly" onClick={() => setAnchor(shiftAnchor(mode, anchor, -1))} aria-label={prevLabel} title={prevLabel}><ChevronLeft className="flipRtl" aria-hidden="true" /></button>
              <button type="button" className="secondary iconOnly" onClick={() => setAnchor(shiftAnchor(mode, anchor, 1))} aria-label={nextLabel} title={nextLabel}><ChevronRight className="flipRtl" aria-hidden="true" /></button>
              <strong className="calendarRange" aria-live="polite">{rangeLabel}</strong>
            </div>
            <div className="finSegmented calModes" role="group" aria-label={t('calendar.mode')}>
              {CALENDAR_MODES.map((m) => <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>{t(`calendar.mode.${m}` as MessageKey)}</button>)}
            </div>
          </div>
          <div className="calendarFilters calFilters" role="search">
            <label className="visuallyHidden" htmlFor={`${ids}-q`}>{t('calendar.search')}</label>
            <span className="calSearch"><Search aria-hidden="true" /><input id={`${ids}-q`} type="search" value={search} maxLength={120} placeholder={t('calendar.search')} onChange={(e) => setSearch(e.target.value)} /></span>
            <label className="calendarToggle"><input type="checkbox" checked={includeCancelled} onChange={(e) => setIncludeCancelled(e.target.checked)} />{t('calendar.showCancelled')}</label>
          </div>
          {page.state === 'loading' && <p className="notice" role="status">{t('calendar.loading')}</p>}
          {page.state === 'error' && <PaneProblem text={t('calendar.loadFailed', { reason: page.message })} onRetry={loadEvents} />}
          {value?.truncated && <p className="notice" role="status">{t('calendar.truncated')}</p>}
          {page.state === 'ready' && !events.length && <div className="paneEmpty calEmpty"><CalendarDays aria-hidden="true" /><h3>{mode === 'day' ? t('calendar.empty.day') : mode === 'month' ? t('calendar.empty.month') : t('calendar.empty.week')}</h3>
            <p>{canWrite ? t('calendar.empty.text') : t('calendar.empty.readOnly')}</p>
            {canWrite && <div className="actions"><button type="button" onClick={() => newEvent()}><Plus aria-hidden="true" />{t('calendar.new')}</button></div>}</div>}
          {page.state === 'ready' && events.length > 0 && !shown.length && <div className="paneEmpty calEmpty"><Search aria-hidden="true" /><h3>{t('calendar.noMatch')}</h3><p>{t('calendar.noMatchText')}</p>
            <div className="actions"><button type="button" className="secondary" onClick={() => { setSearch(''); setHiddenKinds(new Set()); }}>{t('calendar.clearSearch')}</button></div></div>}
          {page.state === 'ready' && shown.length > 0 && (
            mode === 'month' ? <MonthGrid days={range.days} anchor={anchor} now={now} events={shown} clashes={clashes} selected={selected} onSelect={setSelected} onOpenDay={(day) => goTo(day, 'day')} />
              : mode === 'agenda' || (narrow && mode === 'week') ? <AgendaList days={range.days} now={now} events={shown} selected={selected} clashes={clashes} onSelect={setSelected} />
                : <TimeGrid days={range.days} now={now} events={shown} selected={selected} clashes={clashes} onSelect={setSelected} />)}
        </section>}
        {detailOpen && current && <section className="calDetail" aria-label={t('calendar.detail')}>
          <EventDetail key={current.id} event={current} now={now} connections={connections} conflicted={clashes.has(current.id)} onOpenApprovals={onOpenApprovals} onBack={() => setSelected(null)} onEdit={() => setForm({ event: current, day: new Date(current.starts_at) })}
            onChanged={changed} onCancelled={() => setIncludeCancelled(true)} onDeleted={(title) => { setSelected(null); changed(t('calendar.deleted', { title })); }} />
        </section>}
      </div>}
      {tab === 'calendar' && <section className="calStrip" aria-label={t('calendar.summary')}>
        <strong>{mode === 'day' ? t('calendar.strip.day') : mode === 'month' ? t('calendar.strip.month') : t('calendar.strip.week')}</strong>
        {page.state === 'ready' ? <ul>
          <li><CalendarDays aria-hidden="true" /><span>{t('calendar.strip.events', { count: stats.events })}</span></li>
          <li><Users aria-hidden="true" /><span>{t('calendar.strip.people', { count: stats.people })}</span></li>
          <li className={stats.conflicts ? 'bad' : ''}><AlertTriangle aria-hidden="true" /><span>{t('calendar.strip.conflicts', { count: stats.conflicts })}</span></li>
          <li className={stats.awaiting ? 'warn' : ''}><span className="calStripLabel">{t('calendar.fig.awaiting')}</span><span>{formatNumber(stats.awaiting)}</span></li>
          <li><Lightbulb aria-hidden="true" /><span>{steps.state === 'ready' ? t('calendar.strip.steps', { count: stepList.length }) : `${t('calendar.fig.steps')}: ${t('page.figure.unavailable')}`}</span></li>
        </ul> : <span className="muted">{page.state === 'error' ? t('page.figure.unavailable') : t('calendar.loading')}</span>}
      </section>}
      {tab === 'sync' && <section className="finCard calTabCard">
        {sync.state === 'loading' && <p className="notice" role="status">{t('calendar.sync.loading')}</p>}
        {sync.state === 'error' && <PaneProblem text={t('calendar.sync.loadFailed', { reason: sync.message })} onRetry={loadSync} />}
        {sync.state === 'denied' && <PaneDenied title={t('calendar.denied.title')} text={sync.message} />}
        {sync.state === 'ready' && <SyncPanel setup={sync.value} onChanged={(message) => { if (message) setNote(message); loadSync(); loadEvents(); }} />}
      </section>}
      {tab === 'steps' && <section className="finCard calTabCard">
        <p className="muted">{t('calendar.steps.intro')}</p>
        {steps.state === 'loading' && <p className="notice" role="status">{t('calendar.steps.loading')}</p>}
        {steps.state === 'error' && <PaneProblem text={t('calendar.steps.loadFailed', { reason: steps.message })} onRetry={loadSteps} />}
        {steps.state === 'denied' && <PaneDenied title={t('calendar.denied.title')} text={steps.message} />}
        {stepProblem && <PaneProblem text={stepProblem} />}
        {steps.state === 'ready' && !stepList.length && <div className="paneEmpty"><Lightbulb aria-hidden="true" /><h3>{t('calendar.steps.empty')}</h3><p>{t('calendar.steps.emptyText')}</p></div>}
        {stepList.length > 0 && <ul className="calendarSteps">
          {stepList.map((step) => <li key={step.id}>
            <span className="calendarStepIcon" aria-hidden="true">{STEP_ICONS[step.icon] ?? <Lightbulb />}</span>
            <span className="calendarStepText"><strong dir="auto">{step.title}</strong><small dir="auto">{step.detail}</small></span>
            <button type="button" className={step.action.kind === 'task' ? undefined : 'secondary'} disabled={stepBusy !== null} onClick={() => void runStep(step)}>
              {step.action.kind === 'task' ? (stepBusy === step.id ? t('calendar.steps.starting') : t('calendar.steps.start')) : t('calendar.steps.open')}
            </button>
          </li>)}
        </ul>}
      </section>}
    </div>
    {form && <EventFormDialog event={form.event} day={form.day} target={target} onClose={() => setForm(null)} onSaved={(event, created) => {
      setForm(null); setSelected(event.id); setAnchor(startOfDay(new Date(event.starts_at)));
      changed(created ? t('calendar.created', { title: event.title }) : t('calendar.saved', { title: event.title }));
    }} />}
  </div>;
}

// ------------------------------------------------------------------------- the rail

function CalendarRail({ anchor, mode, now, events, page, sync, hiddenKinds, canWrite, onPick, onToggleKind, onSelect, onOpenSync, onSynced }: {
  anchor: Date; mode: CalendarMode; now: Date; events: CalendarEventView[]; page: Load<EventPage>; sync: Load<SyncSetup>; hiddenKinds: Set<EventKind>; canWrite: boolean;
  onPick(day: Date): void; onToggleKind(kind: EventKind): void; onSelect(id: string): void; onOpenSync(): void; onSynced(message: string): void;
}) {
  const conflicts = useMemo(() => findConflicts(events), [events]);
  const next = nextEvent(events, now);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const setup = sync.state === 'ready' ? sync.value : null;
  async function syncNow(connection: SyncConnection) {
    setBusy(connection.id); setProblem(null);
    try {
      const result = await calendarApi.syncNow(connection.id);
      if (result.connection.last_sync_status === 'error') setProblem(t('calendar.sync.syncFailed', { reason: result.connection.last_error ?? '' }));
      else onSynced(t('calendar.sync.syncedNote', { email: connection.email }));
    } catch (e) { setProblem(t('calendar.sync.syncFailed', { reason: describeError(e) })); } finally { setBusy(null); }
  }
  return <aside className="calRail" aria-label={t('calendar.rail')}>
    <MiniMonth anchor={anchor} mode={mode} now={now} onPick={onPick} />
    <section className="calCard" aria-labelledby="cal-rail-calendars">
      <h3 id="cal-rail-calendars">{t('calendar.rail.calendars')}</h3>
      <ul className="calSources">
        <li><span className="calSourceDot anum" aria-hidden="true" /><span className="calSourceText"><strong>{t('calendar.rail.anum')}</strong><small>{canWrite ? t('calendar.rail.anumWrite') : t('calendar.rail.anumRead')}</small></span></li>
        {setup?.connections.map((connection) => <li key={connection.id}>
          <span className={`calSourceDot ${connection.provider}`} aria-hidden="true" />
          <span className="calSourceText"><strong>{connection.provider_name}</strong><small dir="ltr">{connection.email}</small><small>{agoText(connection.last_sync_at, now)}</small></span>
          <StatusChip tone={connection.status === 'connected' ? 'ok' : connection.status === 'error' ? 'warn' : 'bad'}>{t(`calendar.sync.status.${connection.status}` as MessageKey)}</StatusChip>
          {setup.can_manage && connection.status !== 'reauth_required' && <button type="button" className="secondary iconOnly" disabled={busy !== null} onClick={() => void syncNow(connection)}
            aria-label={t('calendar.rail.syncOne', { email: connection.email })} title={t('calendar.sync.now')}><RefreshCw aria-hidden="true" className={busy === connection.id ? 'spin' : undefined} /></button>}
        </li>)}
      </ul>
      {sync.state === 'error' && <p className="muted">{t('calendar.sync.loadFailed', { reason: sync.message })}</p>}
      {problem && <p className="notice errorNotice" role="alert">{problem}</p>}
      <button type="button" className="secondary calWide" onClick={onOpenSync}><CalendarCheck aria-hidden="true" />{setup && !setup.connections.length ? t('calendar.rail.connect') : t('calendar.rail.manage')}</button>
      <fieldset className="calKinds">
        <legend>{t('calendar.rail.kinds')}</legend>
        {EVENT_KINDS.map((kind) => <label key={kind} className={`calKind kind-${kind}`}>
          <input type="checkbox" checked={!hiddenKinds.has(kind)} onChange={() => onToggleKind(kind)} />
          <span className="calKindSwatch" aria-hidden="true" />{t(`calendar.kind.${kind}` as MessageKey)}
        </label>)}
      </fieldset>
    </section>
    <section className="calCard" aria-labelledby="cal-rail-conflicts">
      <h3 id="cal-rail-conflicts">{t('calendar.rail.conflicts')}</h3>
      {page.state !== 'ready' ? <p className="muted">{page.state === 'loading' ? t('calendar.loading') : t('page.figure.unavailable')}</p>
        : conflicts.length === 0 ? <p className="calOk"><CheckCircle2 aria-hidden="true" />{mode === 'day' ? t('calendar.rail.noConflictsDay') : mode === 'month' ? t('calendar.rail.noConflictsMonth') : t('calendar.rail.noConflictsWeek')}</p>
          : <ul className="calConflicts">{conflicts.slice(0, 4).map(([a, b]) => <li key={`${a.id}:${b.id}`}>
            <AlertTriangle aria-hidden="true" />
            <span><button type="button" className="linkButton" onClick={() => onSelect(a.id)}>{a.title}</button> {t('calendar.rail.and')} <button type="button" className="linkButton" onClick={() => onSelect(b.id)}>{b.title}</button>
              <small>{formatDateTime(b.starts_at, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</small></span>
          </li>)}{conflicts.length > 4 && <li className="muted">{t('calendar.rail.moreConflicts', { count: conflicts.length - 4 })}</li>}</ul>}
    </section>
    <section className="calCard" aria-labelledby="cal-rail-next">
      <h3 id="cal-rail-next">{t('calendar.rail.next')}</h3>
      {next ? <button type="button" className="calNext" onClick={() => onSelect(next.id)}>
        <strong dir="auto">{next.title}</strong>
        <small>{formatDateTime(next.starts_at, { weekday: 'short', day: 'numeric', month: 'short' })}, {timeOf(next.starts_at)} <span className="calIn">({inText(next.starts_at, now)})</span></small>
      </button> : <p className="muted">{page.state === 'ready' ? t('calendar.rail.noNext') : t('page.figure.unavailable')}</p>}
    </section>
    <p className="calZone"><Globe2 aria-hidden="true" /><span>{t('calendar.rail.zone', { zone: timeZone })}</span></p>
  </aside>;
}

function inText(iso: string, now: Date): string {
  const minutes = Math.max(1, Math.round((Date.parse(iso) - now.getTime()) / 60_000));
  if (minutes < 60) return t('calendar.rail.inMinutes', { count: minutes });
  if (minutes < 48 * 60) return t('calendar.rail.inHours', { count: Math.round(minutes / 60) });
  return t('calendar.rail.inDays', { count: Math.round(minutes / 1440) });
}

/** The small month: pick a day to show it. Arrows move this month only; the calendar follows a pick. */
function MiniMonth({ anchor, mode, now, onPick }: { anchor: Date; mode: CalendarMode; now: Date; onPick(day: Date): void }) {
  const [month, setMonth] = useState(() => new Date(anchor.getFullYear(), anchor.getMonth(), 1));
  useEffect(() => { setMonth(new Date(anchor.getFullYear(), anchor.getMonth(), 1)); }, [anchor]);
  const days = monthDays(month);
  const weekdays = days.slice(0, 7).map((day) => formatDateTime(day, { weekday: 'narrow' }));
  const label = formatDateTime(month, { month: 'long', year: 'numeric' });
  return <section className="calCard calMini" aria-label={t('calendar.rail.month', { month: label })}>
    <div className="calMiniHead">
      <strong aria-live="polite">{label}</strong>
      <button type="button" className="secondary iconOnly" aria-label={t('calendar.prevMonth')} title={t('calendar.prevMonth')} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft className="flipRtl" aria-hidden="true" /></button>
      <button type="button" className="secondary iconOnly" aria-label={t('calendar.nextMonth')} title={t('calendar.nextMonth')} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight className="flipRtl" aria-hidden="true" /></button>
    </div>
    <div className="calMiniGrid">
      {weekdays.map((name, index) => <span key={index} className="calMiniWeekday" aria-hidden="true">{name}</span>)}
      {days.map((day) => {
        const today = dayKey(day) === dayKey(now);
        const shown = inRange(mode === 'agenda' ? 'week' : mode, anchor, day);
        return <button key={dayKey(day)} type="button" className={`calMiniDay ${day.getMonth() !== month.getMonth() ? 'outside' : ''} ${shown ? 'shown' : ''} ${dayKey(day) === dayKey(anchor) ? 'anchor' : ''}`}
          aria-current={today ? 'date' : undefined} aria-label={formatDateTime(day, { dateStyle: 'full' })} onClick={() => onPick(day)}>{formatNumber(day.getDate())}</button>;
      })}
    </div>
  </section>;
}

// ------------------------------------------------------------------------- the calendar bodies

function eventLabel(event: CalendarEventView, conflicted: boolean): string {
  const parts = [event.title, `${timeOf(event.starts_at)} ${t('calendar.to')} ${timeOf(event.ends_at)}`, t(`calendar.kind.${event.kind}` as MessageKey)];
  if (event.status === 'cancelled') parts.push(t('calendar.status.cancelled'));
  if (conflicted) parts.push(t('calendar.conflict'));
  return parts.join(', ');
}

function TimeGrid({ days, now, events, selected, clashes, onSelect }: { days: Date[]; now: Date; events: CalendarEventView[]; selected: string | null; clashes: Set<string>; onSelect(id: string): void }) {
  const { from, to } = hourWindow(events, days);
  const hours = Array.from({ length: to - from }, (_, index) => from + index);
  const groups = groupByDay(events, days);
  const nowFraction = (now.getTime() - startOfDay(now).getTime()) / 3_600_000;
  const showNow = days.some((day) => dayKey(day) === dayKey(now)) && nowFraction >= from && nowFraction <= to;
  const style = { '--cal-hours': String(to - from), '--cal-days': String(days.length) } as CSSProperties;
  return <div className={`calGrid ${days.length === 1 ? 'oneDay' : ''}`} style={style}>
    <div className="calGridHead" aria-hidden="true">
      <span className="calGridCorner">{timeZoneShort(now)}</span>
      {days.map((day) => <span key={dayKey(day)} className={dayKey(day) === dayKey(now) ? 'today' : undefined}>
        <small>{formatDateTime(day, { weekday: 'short' })}</small><strong>{formatDateTime(day, { day: 'numeric', month: days.length === 1 ? 'long' : 'short' })}</strong>
      </span>)}
    </div>
    <div className="calGridBody">
      <div className="calHours" aria-hidden="true">{hours.map((hour) => <span key={hour}>{formatDateTime(new Date(2000, 0, 1, hour), { hour: 'numeric' })}</span>)}
        {showNow && <span className="calNowLabel" style={{ top: `${((nowFraction - from) / (to - from)) * 100}%` }}>{timeOf(now)}</span>}</div>
      {groups.map((group) => {
        const blocks = layoutDay(group.events, group.day, from, to);
        const today = group.key === dayKey(now);
        return <section key={group.key} className={`calDay ${today ? 'today' : ''}`} aria-label={dayLabel(group.day)}>
          {today && nowFraction >= from && nowFraction <= to && <span className="calNow" style={{ top: `${((nowFraction - from) / (to - from)) * 100}%` }} aria-hidden="true" />}
          {blocks.length ? <ul className="calendarEvents">
            {blocks.map(({ event, top, height, lane, lanes }) => <li key={event.id} className="calBlockSlot" style={{ top: `${top * 100}%`, height: `${height * 100}%`, insetInlineStart: `${(lane / lanes) * 100}%`, width: `${100 / lanes}%` }}>
              <button type="button" className={`calBlock kind-${event.kind} ${event.status === 'cancelled' ? 'cancelled' : ''} ${clashes.has(event.id) ? 'conflict' : ''}`}
                aria-current={event.id === selected ? 'true' : undefined} aria-label={eventLabel(event, clashes.has(event.id))} onClick={() => onSelect(event.id)}>
                <strong dir="auto">{event.title}</strong>
                <small><time dateTime={event.starts_at}>{timeOf(event.starts_at)}</time> {t('calendar.to')} <time dateTime={event.ends_at}>{timeOf(event.ends_at)}</time></small>
                {event.attendees.length > 0 && <small className="calBlockPeople"><Users aria-hidden="true" />{formatNumber(event.attendees.length)}</small>}
                {event.status === 'cancelled' && <small className="calBlockConflict">{t('calendar.status.cancelled')}</small>}
                {clashes.has(event.id) && <small className="calBlockConflict"><AlertTriangle aria-hidden="true" />{t('calendar.conflict')}</small>}
              </button>
            </li>)}
          </ul> : <p className="visuallyHidden">{t('calendar.dayEmpty')}</p>}
        </section>;
      })}
    </div>
  </div>;
}

function timeZoneShort(now: Date): string {
  try { return new Intl.DateTimeFormat('en', { timeZoneName: 'short' }).formatToParts(now).find((part) => part.type === 'timeZoneName')?.value ?? ''; } catch { return ''; }
}

function MonthGrid({ days, anchor, now, events, clashes, selected, onSelect, onOpenDay }: {
  days: Date[]; anchor: Date; now: Date; events: CalendarEventView[]; clashes: Set<string>; selected: string | null; onSelect(id: string): void; onOpenDay(day: Date): void;
}) {
  const groups = groupByDay(events, days);
  return <div className="calMonth">
    <div className="calMonthHead" aria-hidden="true">{days.slice(0, 7).map((day) => <span key={dayKey(day)}>{formatDateTime(day, { weekday: 'short' })}</span>)}</div>
    <ol className="calMonthDays">
      {groups.map((group) => <li key={group.key} className={`${group.day.getMonth() !== anchor.getMonth() ? 'outside' : ''} ${group.key === dayKey(now) ? 'today' : ''}`}>
        <button type="button" className="calMonthDate" aria-label={t('calendar.month.openDay', { day: dayLabel(group.day), count: group.events.length })} onClick={() => onOpenDay(group.day)}>{formatNumber(group.day.getDate())}</button>
        {group.events.length > 0 && <ul className="calendarEvents">
          {group.events.slice(0, 3).map((event) => <li key={event.id}>
            <button type="button" className={`calChip kind-${event.kind} ${event.status === 'cancelled' ? 'cancelled' : ''} ${clashes.has(event.id) ? 'conflict' : ''}`} aria-current={event.id === selected ? 'true' : undefined}
              aria-label={eventLabel(event, clashes.has(event.id))} onClick={() => onSelect(event.id)}><time dateTime={event.starts_at}>{timeOf(event.starts_at)}</time> <span dir="auto">{event.title}</span></button>
          </li>)}
          {group.events.length > 3 && <li><button type="button" className="linkButton calMore" onClick={() => onOpenDay(group.day)}>{t('calendar.month.more', { count: group.events.length - 3 })}</button></li>}
        </ul>}
      </li>)}
    </ol>
  </div>;
}

function AgendaList({ days, now, events, selected, clashes, onSelect }: { days: Date[]; now: Date; events: CalendarEventView[]; selected: string | null; clashes: Set<string>; onSelect(id: string): void }) {
  const groups = groupByDay(events, days);
  return <ol className="calendarDays">
    {groups.map((group) => <li key={group.key} className={group.key === dayKey(now) ? 'today' : undefined}>
      <h3 className="calendarDayHead"><span>{dayLabel(group.day)}</span>{group.key === dayKey(now) && <span className="statusBadge success">{t('calendar.todayBadge')}</span>}</h3>
      {group.events.length ? <ul className="calendarEvents">
        {group.events.map((event) => {
          const replies = replyCounts(event);
          return <li key={event.id}>
            <button type="button" className={`calendarEventRow kind-${event.kind} ${event.status === 'cancelled' ? 'cancelled' : ''}`} aria-current={event.id === selected ? 'true' : undefined} onClick={() => onSelect(event.id)}>
              <span className="calendarTime"><time dateTime={event.starts_at}>{timeOf(event.starts_at)}</time><small><time dateTime={event.ends_at}>{timeOf(event.ends_at)}</time></small></span>
              <span className="calendarEventText">
                <strong dir="auto">{event.title}</strong>
                <small>{[t(`calendar.kind.${event.kind}` as MessageKey), event.location, event.attendees.length ? t('calendar.row.people', { count: event.attendees.length }) : null].filter(Boolean).join(' · ')}</small>
              </span>
              {event.status === 'cancelled' ? <span className="statusBadge dangerTone">{t('calendar.status.cancelled')}</span>
                : clashes.has(event.id) ? <span className="statusBadge dangerTone">{t('calendar.conflict')}</span>
                  : replies.invited > 0 && eventTiming(event, now) !== 'past' ? <span className="statusBadge warning">{t('calendar.row.waiting', { count: replies.invited })}</span>
                    : eventTiming(event, now) === 'now' ? <span className="statusBadge success">{t('calendar.row.now')}</span> : null}
            </button>
          </li>;
        })}
      </ul> : <p className="calendarDayEmpty">{t('calendar.dayEmpty')}</p>}
    </li>)}
  </ol>;
}

// ------------------------------------------------------------------------- the selected event

const REPLY_TONE: Record<AttendeeResponse, 'ok' | 'warn' | 'bad' | 'muted'> = { accepted: 'ok', tentative: 'warn', declined: 'bad', invited: 'muted' };

function EventDetail({ event, now, connections, conflicted, onOpenApprovals, onBack, onEdit, onChanged, onCancelled, onDeleted }: {
  event: CalendarEventView; now: Date; connections: SyncConnection[]; conflicted: boolean; onOpenApprovals?: () => void; onBack: () => void; onEdit: () => void; onChanged: (message: string) => void; onCancelled: () => void; onDeleted: (title: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const manage = event.can_manage ?? false;
  const cancelled = event.status === 'cancelled';
  const replies = replyCounts(event);
  const [requested, setRequested] = useState<string | null>(null);
  const linked = connections.find((connection) => connection.id === event.connection_id) ?? null;
  const people = invitees(event);
  const timing = eventTiming(event, now);

  async function askInvitations() {
    setBusy('invite'); setProblem(null);
    try {
      const result = await calendarApi.requestInvitations(event.id);
      setRequested(t('calendar.invite.requested', { people: result.attendees.join(', ') }));
    } catch (e) { setProblem(t('calendar.invite.failed', { reason: describeError(e) })); } finally { setBusy(null); }
  }

  async function act(name: string, run: () => Promise<string>) {
    setBusy(name); setProblem(null);
    try { onChanged(await run()); } catch (e) { setProblem(describeError(e)); } finally { setBusy(null); }
  }

  return <article className="calendarDetail calEvent" aria-labelledby="calendar-event-title">
    <div className="calEventHead">
      <button type="button" className="secondary paneBack" onClick={onBack}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('page.back')}</button>
      <h2 id="calendar-event-title" dir="auto">{event.title}</h2>
      <button type="button" className="secondary iconOnly calClose" onClick={onBack} aria-label={t('calendar.close')} title={t('calendar.close')}><X aria-hidden="true" /></button>
    </div>
    <div className="calEventChips">
      <StatusChip tone="info">{t(`calendar.kind.${event.kind}` as MessageKey)}</StatusChip>
      <StatusChip tone={cancelled ? 'bad' : timing === 'past' ? 'muted' : 'ok'}>{cancelled ? t('calendar.status.cancelled') : t(`calendar.timing.${timing}` as MessageKey)}</StatusChip>
      {conflicted && !cancelled && <StatusChip tone="bad"><AlertTriangle aria-hidden="true" />{t('calendar.conflict')}</StatusChip>}
      {connections.length > 0 && <StatusChip tone="muted">{t(`calendar.sync.state.${eventSyncKind(event)}` as MessageKey, { provider: linked?.provider_name ?? '' })}</StatusChip>}
    </div>
    <dl className="calFacts">
      <div><dt><Clock aria-hidden="true" /><span className="visuallyHidden">{t('calendar.when')}</span></dt>
        <dd>{t('calendar.range.week', { from: formatDateTime(event.starts_at, { dateStyle: 'full', timeStyle: 'short' }), to: timeOf(event.ends_at) })} <small>({durationText(durationMinutes(event))})</small></dd></div>
      {event.location && <div><dt><MapPin aria-hidden="true" /><span className="visuallyHidden">{t('calendar.location')}</span></dt><dd dir="auto">{event.location}</dd></div>}
      <div><dt><Globe2 aria-hidden="true" /><span className="visuallyHidden">{t('calendar.zone')}</span></dt><dd>{timeZone}</dd></div>
      <div><dt><UserRound aria-hidden="true" /><span className="visuallyHidden">{t('calendar.createdBy')}</span></dt><dd>{t('calendar.addedBy', { who: event.created_by })}</dd></div>
    </dl>
    {(event.conference_url || event.phone) && <div className="calJoin">
      {event.conference_url && <a className="calendarLink primary" href={event.conference_url} target="_blank" rel="noopener noreferrer"><Video aria-hidden="true" />{t('calendar.join')}<ExternalLink aria-hidden="true" /></a>}
      {event.phone && <a className="calendarLink" href={`tel:${event.phone.replace(/[^+\d]/g, '')}`}><Phone aria-hidden="true" /><span dir="ltr">{event.phone}</span></a>}
    </div>}
    {problem && <PaneProblem text={t('calendar.actionFailed', { reason: problem })} />}
    <section aria-labelledby="calendar-attendees" className="calSection">
      <div className="calSectionHead"><h3 id="calendar-attendees"><Users aria-hidden="true" />{t('calendar.attendees', { count: event.attendees.length })}</h3>
        {event.attendees.length > 0 && <small className="muted">{ATTENDEE_RESPONSES.filter((r) => replies[r]).map((r) => t(`calendar.replies.${r}` as MessageKey, { count: replies[r] })).join(' · ')}</small>}</div>
      {event.attendees.length ? <ul className="calendarAttendees">
        {event.attendees.map((attendee) => <li key={attendee.id}>
          <span className="calAvatar" aria-hidden="true">{initials(attendee.name)}</span>
          <span><strong dir="auto">{attendee.name}</strong>{attendee.email && <small dir="ltr">{attendee.email}</small>}</span>
          {manage
            ? <label className="calendarReply"><span className="visuallyHidden">{t('calendar.replyFor', { name: attendee.name })}</span>
              <select value={attendee.response} disabled={Boolean(busy)} onChange={(e) => void act(`reply-${attendee.id}`, async () => { await calendarApi.setResponse(event.id, attendee.id, e.target.value as AttendeeResponse); return t('calendar.replySaved', { name: attendee.name }); })}>
                {ATTENDEE_RESPONSES.map((r) => <option key={r} value={r}>{t(`calendar.reply.${r}` as MessageKey)}</option>)}
              </select></label>
            : <StatusChip tone={REPLY_TONE[attendee.response]}>{t(`calendar.reply.${attendee.response}` as MessageKey)}</StatusChip>}
        </li>)}
      </ul> : <p className="muted">{t('calendar.noAttendees')}</p>}
      {people.length > 0 && event.connection_id && !cancelled && <div className="calendarInvite">
        <p className="muted">{event.invitations_pending ? t('calendar.invite.pending') : t('calendar.invite.done')}</p>
        {manage && event.invitations_pending && !requested && <div className="actions">
          <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void askInvitations()}><Mail aria-hidden="true" />{busy === 'invite' ? t('calendar.invite.asking') : t('calendar.invite.ask')}</button>
        </div>}
        {requested && <p className="notice" role="status">{requested} <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('calendar.invite.review')}</button></p>}
      </div>}
    </section>
    {event.description && <section className="calendarNotes calSection" aria-label={t('calendar.notes')}><h3><FileText aria-hidden="true" />{t('calendar.notes')}</h3><p dir="auto">{event.description}</p></section>}
    {manage ? <div className="calEventActions">
      <button type="button" disabled={Boolean(busy)} onClick={onEdit}><Pencil aria-hidden="true" />{t('calendar.edit')}</button>
      {cancelled
        ? <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void act('restore', async () => { await calendarApi.updateEvent(event.id, { status: 'scheduled' }); return t('calendar.restored', { title: event.title }); })}><RotateCcw aria-hidden="true" />{busy === 'restore' ? t('calendar.working') : t('calendar.restore')}</button>
        : <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void act('cancel', async () => { await calendarApi.updateEvent(event.id, { status: 'cancelled' }); onCancelled(); return t('calendar.cancelledNote', { title: event.title }); })}><XCircle aria-hidden="true" />{busy === 'cancel' ? t('calendar.working') : t('calendar.cancelEvent')}</button>}
      <button type="button" className="secondary dangerText" disabled={Boolean(busy)} onClick={() => setConfirmDelete(true)}><Trash2 aria-hidden="true" />{t('calendar.delete')}</button>
    </div> : <p className="muted">{t('calendar.readOnlyEvent')}</p>}
    <p className="calConfirmNote"><ShieldCheck aria-hidden="true" /><span>{t('calendar.inviteNote')}</span></p>
    {confirmDelete && <ConfirmDialog title={t('calendar.deleteTitle')} confirmLabel={t('calendar.delete')} danger busy={busy === 'delete'} onCancel={() => setConfirmDelete(false)} onConfirm={() => {
      setBusy('delete'); setProblem(null);
      calendarApi.deleteEvent(event.id).then(() => { setConfirmDelete(false); onDeleted(event.title); }, (e: unknown) => { setConfirmDelete(false); setProblem(describeError(e)); }).finally(() => setBusy(null));
    }}><p>{t('calendar.deleteText', { title: event.title })}</p></ConfirmDialog>}
  </article>;
}

function EventFormDialog({ event, day, target, onClose, onSaved }: { event: CalendarEventView | null; day: Date; target: SyncConnection | null; onClose: () => void; onSaved: (event: CalendarEventView, created: boolean) => void }) {
  const [form, setForm] = useState<EventForm>(() => (event ? formFromEvent(event) : emptyForm(day, new Date())));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [toCalendar, setToCalendar] = useState(true);
  const id = useId();
  const problem = formProblem(form);
  const set = <K extends keyof EventForm>(key: K, value: EventForm[K]) => setForm((old) => ({ ...old, [key]: value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (problem) return;
    setBusy(true); setError(null);
    try {
      const body = { ...eventBody(form, event?.attendees ?? []), ...(!event && target && !toCalendar ? { sync: false } : {}) };
      const saved = event ? await calendarApi.updateEvent(event.id, body) : await calendarApi.createEvent(body);
      onSaved(saved, !event);
    } catch (err) { setError(describeError(err)); setBusy(false); }
  }

  return <Modal title={event ? t('calendar.form.editTitle') : t('calendar.form.newTitle')} className="formDialog calendarForm" onClose={onClose}>
    <form onSubmit={(e) => void submit(e)} noValidate>
      <label className="field"><span>{t('calendar.form.title')}</span><input data-autofocus dir="auto" value={form.title} maxLength={160} required aria-invalid={touched && problem === 'title' ? true : undefined} onChange={(e) => set('title', e.target.value)} /></label>
      <div className="calendarFormRow">
        <label className="field"><span>{t('calendar.kind')}</span><select value={form.kind} onChange={(e) => set('kind', e.target.value as EventForm['kind'])}>{EVENT_KINDS.map((k) => <option key={k} value={k}>{t(`calendar.kind.${k}` as MessageKey)}</option>)}</select></label>
        <label className="field"><span>{t('calendar.form.date')}</span><input type="date" value={form.date} required onChange={(e) => { const date = e.target.value; setForm((old) => ({ ...old, date, endDate: old.endDate === old.date || old.endDate < date ? date : old.endDate })); }} /></label>
      </div>
      <div className="calendarFormRow">
        <label className="field"><span>{t('calendar.form.start')}</span><input type="time" value={form.startTime} required onChange={(e) => set('startTime', e.target.value)} /></label>
        <label className="field"><span>{t('calendar.form.endDate')}</span><input type="date" value={form.endDate} min={form.date} required aria-invalid={touched && problem === 'order' ? true : undefined} onChange={(e) => set('endDate', e.target.value)} /></label>
        <label className="field"><span>{t('calendar.form.end')}</span><input type="time" value={form.endTime} required aria-invalid={touched && problem === 'order' ? true : undefined} onChange={(e) => set('endTime', e.target.value)} /></label>
      </div>
      <label className="field"><span>{t('calendar.location')}</span><input dir="auto" value={form.location} maxLength={200} onChange={(e) => set('location', e.target.value)} /></label>
      <div className="calendarFormRow">
        <label className="field"><span>{t('calendar.form.link')}</span><input type="url" dir="ltr" value={form.conferenceUrl} maxLength={500} placeholder="https://" aria-invalid={touched && problem === 'link' ? true : undefined} onChange={(e) => set('conferenceUrl', e.target.value)} /></label>
        <label className="field"><span>{t('calendar.form.phone')}</span><input type="tel" dir="ltr" value={form.phone} maxLength={40} onChange={(e) => set('phone', e.target.value)} /></label>
      </div>
      <label className="field"><span>{t('calendar.form.attendees')}</span><textarea dir="auto" value={form.attendees} rows={3} aria-describedby={`${id}-people`} onChange={(e) => set('attendees', e.target.value)} /></label>
      <p className="muted" id={`${id}-people`}>{t('calendar.form.attendeesHint')}</p>
      {!event && target && <label className="calendarToggle calendarFormSync"><input type="checkbox" checked={toCalendar} onChange={(e) => setToCalendar(e.target.checked)} />{t('calendar.form.sync', { provider: target.provider_name, email: target.email })}</label>}
      {!event && target && toCalendar && form.attendees.trim() && <p className="muted">{t('calendar.form.syncHint')}</p>}
      <label className="field"><span>{t('calendar.notes')}</span><textarea dir="auto" value={form.description} maxLength={2000} rows={3} onChange={(e) => set('description', e.target.value)} /></label>
      {touched && problem && <p className="notice errorNotice" role="alert">{t(`calendar.form.problem.${problem}` as MessageKey)}</p>}
      {error && <p className="notice errorNotice" role="alert">{t('calendar.form.failed', { reason: error })}</p>}
      <div className="actions">
        <button type="submit" disabled={busy}>{busy ? t('calendar.working') : event ? t('calendar.form.save') : t('calendar.form.create')}</button>
        <button type="button" className="secondary" onClick={onClose}>{t('page.cancel')}</button>
      </div>
    </form>
  </Modal>;
}


