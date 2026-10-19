// The web Calendar page (docs/calendar.md#web-page): the workspace calendar as a week or day agenda
// with the selected event beside it, the suggested next steps, and New event / Edit / Cancel /
// Delete through the calendar API. Times show in the browser's time zone; the API stores UTC.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  ArrowLeft, CalendarCheck, CalendarDays, CalendarPlus, ChevronLeft, ChevronRight, ClipboardList, Clock, ExternalLink, FileText, Lightbulb, Mail, MapPin, Pencil, Phone,
  Plus, RotateCcw, Search, ShieldCheck, Trash2, Users, Video, XCircle,
} from 'lucide-react';
import * as api from './lib/api';
import * as calendarApi from './lib/calendarApi';
import * as meetingsApi from './lib/meetingsApi';
import {
  ATTENDEE_RESPONSES, calendarCounts, dayKey, durationMinutes, emptyForm, EVENT_KINDS, eventBody, eventTiming, formFromEvent, formProblem, groupByDay,
  CALENDAR_CONNECT_KEY, eventSyncKind, invitees, matchesEvent, replyCounts, shiftAnchor, startOfDay, syncTarget, visibleRange,
  type AttendeeResponse, type CalendarEventView, type CalendarMode, type EventForm, type EventPage, type NextStep, type SyncConnection, type SyncSetup,
} from './lib/calendar';
import { describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { figure, type Load } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { ConfirmDialog, Modal } from './Dialog';
import { SyncPanel } from './CalendarSync';
import { loadFailure, PageHeader, PageSummary, PaneDenied, PaneProblem, SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import './admin.css';
import './calendar.css';
import { HelpLink } from './HelpLink';

const MODE_KEY = 'anum.calendar.mode';
const readMode = (): CalendarMode => { try { return localStorage.getItem(MODE_KEY) === 'day' ? 'day' : 'week'; } catch { return 'week'; } };
const STEP_ICONS = { agenda: <ClipboardList />, follow_up: <Users />, summary: <FileText />, email: <Mail />, approval: <ShieldCheck /> } as const;

const timeOf = (value: string) => formatDateTime(value, { hour: '2-digit', minute: '2-digit' });
const dayLabel = (date: Date) => formatDateTime(date, { weekday: 'long', day: 'numeric', month: 'long' });

function durationText(minutes: number): string {
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  if (!hours) return t('calendar.duration.minutes', { minutes: rest });
  return rest ? t('calendar.duration.hoursMinutes', { hours, minutes: rest }) : t('calendar.duration.hours', { hours });
}

export function CalendarView({ onOpenTask, onOpenInbox, onOpenApprovals, onOpenMeetings }: {
  onOpenTask?: (taskId: string) => void; onOpenInbox?: () => void; onOpenApprovals?: () => void; onOpenMeetings?: () => void;
}) {
  const [mode, setModeState] = useState<CalendarMode>(readMode);
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [includeCancelled, setIncludeCancelled] = useState(false);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState<Load<EventPage>>({ state: 'loading' });
  const [steps, setSteps] = useState<Load<NextStep[]>>({ state: 'loading' });
  const [tools, setTools] = useState<Load<{ connected: number; total: number }>>({ state: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<'agenda' | 'steps' | 'sync'>('agenda');
  const [sync, setSync] = useState<Load<SyncSetup>>({ state: 'loading' });
  const [form, setForm] = useState<{ event: CalendarEventView | null; day: Date } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [stepBusy, setStepBusy] = useState<string | null>(null);
  const [stepProblem, setStepProblem] = useState<string | null>(null);
  const wide = useMediaQuery(SPLIT_QUERY);
  const searchId = useId();
  const now = new Date();
  const range = useMemo(() => visibleRange(mode, anchor), [mode, anchor]);

  const setMode = (next: CalendarMode) => { setModeState(next); try { localStorage.setItem(MODE_KEY, next); } catch { /* remembered for this visit only */ } };
  // Only the newest request may fill the agenda: a reload started before a range or filter change
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
  const shown = useMemo(() => events.filter((event) => matchesEvent(event, search)), [events, search]);
  const groups = useMemo(() => groupByDay(shown, range.days), [shown, range]);
  const counts = calendarCounts(events, now);
  const current = events.find((event) => event.id === selected) ?? null;
  const canWrite = value?.can_write ?? false;
  const stepList = steps.state === 'ready' ? steps.value : [];
  const connections: SyncConnection[] = sync.state === 'ready' ? sync.value.connections : [];
  const target = syncTarget(connections);
  const troubled = connections.find((connection) => connection.status !== 'connected') ?? null;

  // On a wide screen the detail pane opens on the next event still to come in the range.
  useEffect(() => {
    if (!wide || selected || !events.length) return;
    const next = events.find((event) => event.status !== 'cancelled' && Date.parse(event.ends_at) > Date.now()) ?? events[0];
    setSelected(next.id);
  }, [wide, selected, events]);

  const changed = (message: string) => { setNote(message); loadEvents(); loadSteps(); };

  async function runStep(step: NextStep) {
    setStepProblem(null);
    if (step.action.kind === 'open') {
      if (step.action.destination === 'email_urgent') onOpenInbox?.();
      else if (step.action.destination === 'approvals') onOpenApprovals?.();
      else if (step.action.destination === 'event' && step.action.event_id) { setSelected(step.action.event_id); setTab('agenda'); }
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

  const rangeLabel = mode === 'week'
    ? t('calendar.range.week', { from: formatDateTime(range.days[0], { day: 'numeric', month: 'short' }), to: formatDateTime(range.days[6], { day: 'numeric', month: 'short', year: 'numeric' }) })
    : dayLabel(anchor);
  const figures = [
    figure('today', t('calendar.fig.today'), page, () => ({ value: formatNumber(counts.today), hint: counts.today ? t('calendar.fig.todayHint') : t('calendar.fig.todayNone'), tone: counts.today ? 'accent' : '' })),
    figure('range', mode === 'week' ? t('calendar.fig.week') : t('calendar.fig.day'), page, () => ({ value: `${formatNumber(counts.range)}${value?.truncated ? '+' : ''}`, hint: counts.cancelled ? t('calendar.fig.cancelledHint', { count: counts.cancelled }) : rangeLabel })),
    figure('awaiting', t('calendar.fig.awaiting'), page, () => ({ value: formatNumber(counts.awaiting), hint: counts.awaiting ? t('calendar.fig.awaitingHint') : t('calendar.fig.awaitingNone'), tone: counts.awaiting ? 'warn' : '' })),
    figure('steps', t('calendar.fig.steps'), steps, () => ({ value: formatNumber(stepList.length), hint: stepList.length ? t('calendar.fig.stepsHint') : t('calendar.fig.stepsNone') })),
  ];

  const header = <PageHeader title={viewLabel('calendar')} subtitle={t('calendar.subtitle')} actions={<>
    {tools.state === 'ready' && <button type="button" className={`chipButton ${tools.value.connected ? 'success' : ''}`} onClick={onOpenMeetings} title={t('calendar.tools.title')}>
      <span className="chipDot" aria-hidden="true" /><span>{t('calendar.tools.chip', { count: tools.value.connected })}</span></button>}
    {canWrite && <button type="button" onClick={() => setForm({ event: null, day: mode === 'day' ? anchor : startOfDay(now) })}><CalendarPlus aria-hidden="true" />{t('calendar.new')}</button>}
  </>} />;

  if (page.state === 'denied') return <>{header}<section className="pane"><PaneDenied title={t('calendar.denied.title')} text={page.message} /></section></>;

  return <>
    {header}
    <p className="muted calendarSyncNote"><CalendarCheck aria-hidden="true" />{troubled
      ? <>{t('calendar.syncNote.problem', { provider: troubled.provider_name })} <button type="button" className="linkButton" onClick={() => setTab('sync')}>{t('calendar.tab.sync')}</button></>
      : target ? t('calendar.syncNote.on', { provider: target.provider_name, email: target.email }) : t('calendar.syncNote.none')}</p>
    {note && <p className="notice" role="status">{note}</p>}
    <PageSummary label={t('calendar.summary')} figures={figures} onRetry={() => { loadEvents(); loadSteps(); }} />
    <SplitView className="calendarSplit" show={selected && current ? 'detail' : 'list'} label={t('calendar.split')}>
      <section data-pane="list" className="pane calendarAgendaPane" aria-labelledby={`${searchId}-agenda`} aria-busy={page.state === 'loading'}>
        <div className="paneHead calendarHead">
          <h2 id={`${searchId}-agenda`} className="visuallyHidden">{t('calendar.agenda')}</h2>
          <div className="segmented" role="tablist" aria-label={t('calendar.tabs')}>
            <button type="button" role="tab" aria-selected={tab === 'agenda'} onClick={() => setTab('agenda')}>{t('calendar.tab.agenda')}</button>
            <button type="button" role="tab" aria-selected={tab === 'steps'} onClick={() => setTab('steps')}>{t('calendar.tab.steps', { count: stepList.length })}</button>
            <button type="button" role="tab" aria-selected={tab === 'sync'} onClick={() => setTab('sync')}>{t('calendar.tab.sync')}</button>
          </div>
          {tab === 'agenda' && <div className="segmented" role="group" aria-label={t('calendar.mode')}>
            <button type="button" aria-pressed={mode === 'week'} onClick={() => setMode('week')}>{t('calendar.mode.week')}</button>
            <button type="button" aria-pressed={mode === 'day'} onClick={() => setMode('day')}>{t('calendar.mode.day')}</button>
          </div>}
        </div>
        {tab === 'agenda' && <>
          <div className="calendarNav toolbar">
            <button type="button" className="secondary iconOnly" onClick={() => setAnchor(shiftAnchor(mode, anchor, -1))} aria-label={mode === 'week' ? t('calendar.prevWeek') : t('calendar.prevDay')} title={mode === 'week' ? t('calendar.prevWeek') : t('calendar.prevDay')}><ChevronLeft className="flipRtl" aria-hidden="true" /></button>
            <button type="button" className="secondary" onClick={() => setAnchor(startOfDay(new Date()))}>{t('calendar.today')}</button>
            <button type="button" className="secondary iconOnly" onClick={() => setAnchor(shiftAnchor(mode, anchor, 1))} aria-label={mode === 'week' ? t('calendar.nextWeek') : t('calendar.nextDay')} title={mode === 'week' ? t('calendar.nextWeek') : t('calendar.nextDay')}><ChevronRight className="flipRtl" aria-hidden="true" /></button>
            <strong className="calendarRange" aria-live="polite">{rangeLabel}</strong>
          </div>
          <div className="calendarFilters toolbar" role="search">
            <label className="visuallyHidden" htmlFor={`${searchId}-q`}>{t('calendar.search')}</label>
            <input id={`${searchId}-q`} type="search" value={search} maxLength={120} placeholder={t('calendar.search')} onChange={(e) => setSearch(e.target.value)} />
            <label className="calendarToggle"><input type="checkbox" checked={includeCancelled} onChange={(e) => setIncludeCancelled(e.target.checked)} />{t('calendar.showCancelled')}</label>
          </div>
          {page.state === 'loading' && <div className="paneBody"><p className="notice" role="status">{t('calendar.loading')}</p></div>}
          {page.state === 'error' && <div className="paneBody"><PaneProblem text={t('calendar.loadFailed', { reason: page.message })} onRetry={loadEvents} /></div>}
          {value?.truncated && <div className="paneBody"><p className="notice" role="status">{t('calendar.truncated')}</p></div>}
          {page.state === 'ready' && !events.length && <div className="paneEmpty"><CalendarDays aria-hidden="true" /><h3>{mode === 'week' ? t('calendar.empty.week') : t('calendar.empty.day')}</h3>
            <p>{canWrite ? t('calendar.empty.text') : t('calendar.empty.readOnly')}</p><HelpLink />
            {canWrite && <div className="actions"><button type="button" onClick={() => setForm({ event: null, day: mode === 'day' ? anchor : startOfDay(now) })}><Plus aria-hidden="true" />{t('calendar.new')}</button></div>}</div>}
          {page.state === 'ready' && events.length > 0 && !shown.length && <div className="paneEmpty"><Search aria-hidden="true" /><h3>{t('calendar.noMatch')}</h3><p>{t('calendar.noMatchText')}</p><div className="actions"><button type="button" className="secondary" onClick={() => setSearch('')}>{t('calendar.clearSearch')}</button></div></div>}
          {page.state === 'ready' && shown.length > 0 && <ol className="calendarDays">
            {groups.map((group) => <li key={group.key} className={group.key === dayKey(now) ? 'today' : undefined}>
              <h3 className="calendarDayHead"><span>{dayLabel(group.day)}</span>{group.key === dayKey(now) && <span className="statusBadge success">{t('calendar.todayBadge')}</span>}</h3>
              {group.events.length ? <ul className="calendarEvents">
                {group.events.map((event) => {
                  const replies = replyCounts(event);
                  return <li key={event.id}>
                    <button type="button" className={`calendarEventRow kind-${event.kind} ${event.status === 'cancelled' ? 'cancelled' : ''}`} aria-current={event.id === selected ? 'true' : undefined} onClick={() => setSelected(event.id)}>
                      <span className="calendarTime"><time dateTime={event.starts_at}>{timeOf(event.starts_at)}</time><small><time dateTime={event.ends_at}>{timeOf(event.ends_at)}</time></small></span>
                      <span className="calendarEventText">
                        <strong dir="auto">{event.title}</strong>
                        <small>{[t(`calendar.kind.${event.kind}` as MessageKey), event.location, event.attendees.length ? t('calendar.row.people', { count: event.attendees.length }) : null].filter(Boolean).join(' · ')}</small>
                      </span>
                      {event.status === 'cancelled' ? <span className="statusBadge dangerTone">{t('calendar.status.cancelled')}</span>
                        : replies.invited > 0 && eventTiming(event, now) !== 'past' ? <span className="statusBadge warning">{t('calendar.row.waiting', { count: replies.invited })}</span>
                          : eventTiming(event, now) === 'now' ? <span className="statusBadge success">{t('calendar.row.now')}</span> : null}
                    </button>
                  </li>;
                })}
              </ul> : <p className="calendarDayEmpty">{t('calendar.dayEmpty')}</p>}
            </li>)}
          </ol>}
        </>}
        {tab === 'sync' && <>
          {sync.state === 'loading' && <div className="paneBody"><p className="notice" role="status">{t('calendar.sync.loading')}</p></div>}
          {sync.state === 'error' && <div className="paneBody"><PaneProblem text={t('calendar.sync.loadFailed', { reason: sync.message })} onRetry={loadSync} /></div>}
          {sync.state === 'denied' && <div className="paneBody"><PaneDenied title={t('calendar.denied.title')} text={sync.message} /></div>}
          {sync.state === 'ready' && <SyncPanel setup={sync.value} onChanged={(message) => { if (message) setNote(message); loadSync(); loadEvents(); }} />}
        </>}
        {tab === 'steps' && <div className="paneBody">
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
        </div>}
      </section>
      <section data-pane="detail" className="pane calendarDetailPane" aria-label={current ? undefined : t('calendar.detail')}>
        {current
          ? <EventDetail key={current.id} event={current} now={now} connections={connections} onOpenApprovals={onOpenApprovals} onBack={() => setSelected(null)} onEdit={() => setForm({ event: current, day: new Date(current.starts_at) })}
              onChanged={changed} onCancelled={() => setIncludeCancelled(true)} onDeleted={(title) => { setSelected(null); changed(t('calendar.deleted', { title })); }} />
          : <div className="paneEmpty"><CalendarDays aria-hidden="true" /><h3>{t('calendar.pick.title')}</h3><p>{events.length ? t('calendar.pick.text') : canWrite ? t('calendar.empty.text') : t('calendar.empty.readOnly')}</p></div>}
      </section>
    </SplitView>
    {form && <EventFormDialog event={form.event} day={form.day} target={target} onClose={() => setForm(null)} onSaved={(event, created) => {
      setForm(null); setSelected(event.id); setAnchor(startOfDay(new Date(event.starts_at)));
      changed(created ? t('calendar.created', { title: event.title }) : t('calendar.saved', { title: event.title }));
    }} />}
  </>;
}

function EventDetail({ event, now, connections, onOpenApprovals, onBack, onEdit, onChanged, onCancelled, onDeleted }: {
  event: CalendarEventView; now: Date; connections: SyncConnection[]; onOpenApprovals?: () => void; onBack: () => void; onEdit: () => void; onChanged: (message: string) => void; onCancelled: () => void; onDeleted: (title: string) => void;
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

  return <article className="calendarDetail" aria-labelledby="calendar-event-title">
    <div className="paneHead">
      <button type="button" className="secondary paneBack" onClick={onBack}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('page.back')}</button>
      <h2 id="calendar-event-title" dir="auto">{event.title}</h2>
      <span className={`statusBadge ${cancelled ? 'dangerTone' : eventTiming(event, now) === 'past' ? 'neutral' : 'success'}`}>{cancelled ? t('calendar.status.cancelled') : t(`calendar.timing.${eventTiming(event, now)}` as MessageKey)}</span>
    </div>
    <div className="paneBody">
      {manage && <div className="actions calendarActions">
        <button type="button" className="secondary" disabled={Boolean(busy)} onClick={onEdit}><Pencil aria-hidden="true" />{t('calendar.edit')}</button>
        {cancelled
          ? <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void act('restore', async () => { await calendarApi.updateEvent(event.id, { status: 'scheduled' }); return t('calendar.restored', { title: event.title }); })}><RotateCcw aria-hidden="true" />{busy === 'restore' ? t('calendar.working') : t('calendar.restore')}</button>
          : <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void act('cancel', async () => { await calendarApi.updateEvent(event.id, { status: 'cancelled' }); onCancelled(); return t('calendar.cancelledNote', { title: event.title }); })}><XCircle aria-hidden="true" />{busy === 'cancel' ? t('calendar.working') : t('calendar.cancelEvent')}</button>}
        <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => setConfirmDelete(true)}><Trash2 aria-hidden="true" />{t('calendar.delete')}</button>
      </div>}
      {!manage && <p className="muted">{t('calendar.readOnlyEvent')}</p>}
      {problem && <PaneProblem text={t('calendar.actionFailed', { reason: problem })} />}
      <dl className="railFacts calendarFacts">
        <dt><Clock aria-hidden="true" />{t('calendar.when')}</dt>
        <dd>{t('calendar.range.week', { from: formatDateTime(event.starts_at, { dateStyle: 'full', timeStyle: 'short' }), to: timeOf(event.ends_at) })} <small>({durationText(durationMinutes(event))})</small></dd>
        <dt>{t('calendar.kind')}</dt><dd>{t(`calendar.kind.${event.kind}` as MessageKey)}</dd>
        {event.location && <><dt><MapPin aria-hidden="true" />{t('calendar.location')}</dt><dd dir="auto">{event.location}</dd></>}
        <dt>{t('calendar.createdBy')}</dt><dd dir="auto">{event.created_by}</dd>
        {connections.length > 0 && <><dt><CalendarCheck aria-hidden="true" />{t('calendar.tab.sync')}</dt>
          <dd>{t(`calendar.sync.state.${eventSyncKind(event)}` as MessageKey, { provider: linked?.provider_name ?? '' })}</dd></>}
      </dl>
      {(event.conference_url || event.phone) && <div className="actions">
        {event.conference_url && <a className="calendarLink primary" href={event.conference_url} target="_blank" rel="noopener noreferrer"><Video aria-hidden="true" />{t('calendar.join')}<ExternalLink aria-hidden="true" /></a>}
        {event.phone && <a className="calendarLink" href={`tel:${event.phone.replace(/[^+\d]/g, '')}`}><Phone aria-hidden="true" /><span dir="ltr">{event.phone}</span></a>}
      </div>}
      {event.description && <section className="calendarNotes" aria-label={t('calendar.notes')}><h3>{t('calendar.notes')}</h3><p dir="auto">{event.description}</p></section>}
      <section aria-labelledby="calendar-attendees">
        <h3 id="calendar-attendees" className="calendarSubhead">{t('calendar.attendees', { count: event.attendees.length })}</h3>
        {event.attendees.length > 0 && <p className="muted">{ATTENDEE_RESPONSES.filter((r) => replies[r]).map((r) => t(`calendar.replies.${r}` as MessageKey, { count: replies[r] })).join(' · ')}</p>}
        {event.attendees.length ? <ul className="calendarAttendees">
          {event.attendees.map((attendee) => <li key={attendee.id}>
            <span><strong dir="auto">{attendee.name}</strong>{attendee.email && <small dir="ltr">{attendee.email}</small>}</span>
            {manage
              ? <label className="calendarReply"><span className="visuallyHidden">{t('calendar.replyFor', { name: attendee.name })}</span>
                <select value={attendee.response} disabled={Boolean(busy)} onChange={(e) => void act(`reply-${attendee.id}`, async () => { await calendarApi.setResponse(event.id, attendee.id, e.target.value as AttendeeResponse); return t('calendar.replySaved', { name: attendee.name }); })}>
                  {ATTENDEE_RESPONSES.map((r) => <option key={r} value={r}>{t(`calendar.reply.${r}` as MessageKey)}</option>)}
                </select></label>
              : <span className={`statusBadge ${attendee.response === 'accepted' ? 'success' : attendee.response === 'declined' ? 'dangerTone' : attendee.response === 'tentative' ? 'warning' : 'neutral'}`}>{t(`calendar.reply.${attendee.response}` as MessageKey)}</span>}
          </li>)}
        </ul> : <p className="muted">{t('calendar.noAttendees')}</p>}
        <p className="muted calendarInviteNote">{t('calendar.inviteNote')}</p>
        {people.length > 0 && event.connection_id && !cancelled && <div className="calendarInvite">
          <p className="muted">{event.invitations_pending ? t('calendar.invite.pending') : t('calendar.invite.done')}</p>
          {manage && event.invitations_pending && !requested && <div className="actions">
            <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void askInvitations()}><Mail aria-hidden="true" />{busy === 'invite' ? t('calendar.invite.asking') : t('calendar.invite.ask')}</button>
          </div>}
          {requested && <p className="notice" role="status">{requested} <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('calendar.invite.review')}</button></p>}
        </div>}
      </section>
    </div>
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


