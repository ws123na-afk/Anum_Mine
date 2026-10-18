// Recents (the call log) and Contacts of the Phone calls page (CallsView.tsx, docs/calls.md): a list
// with search, filters and paging next to the open record (stacked on phones, with a way back).
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, Bot, Download, ListChecks, Lock, Pencil, PhoneCall, PhoneIncoming, PhoneOutgoing, Search, ShieldCheck, Sparkles, Star, Trash2, UserPlus, X } from 'lucide-react';
import { ConfirmDialog, Modal } from './Dialog';
import { CallLink, ErrorNotice, Notice, Pager, type DialTarget } from './CallsParts';
import { SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import { apiBaseUrl } from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import {
  aiCallBlocker, callWith, directionLabel, durationText, initials, isDialable, isLive, kindLabel, OUTCOMES, outcomeLabel, outcomeTone,
  PAGE_SIZE, parseDuration, type ApiCall, type ApiCallsSetup, type ApiContact, type ConsentMethod, type Direction, type Outcome,
} from './lib/calls';
import * as callsApi from './lib/callsApi';
import type { Load } from './lib/pageSummary';

const fail = <T,>(e: unknown): Load<T> => isPermissionDenied(e) ? { state: 'denied', message: describeError(e) } : { state: 'error', message: describeError(e) };
type PageState<T> = Load<{ items: T[]; total: number }>;

// ------------------------------------------------------------------------------------------ recents

export function RecentsTab({ refresh, setup, selected, onSelect, onDial, onCallInAnum, onChanged, onOpenTasks, onOpenSettings, onOpenDial }: {
  refresh: number; setup: ApiCallsSetup | null; selected: string | null; onSelect(id: string | null): void; onDial(target: DialTarget): void; onCallInAnum(target: DialTarget): void;
  onChanged(): void; onOpenTasks?(): void; onOpenSettings?(): void; onOpenDial(): void;
}) {
  const ids = useId();
  const wide = useMediaQuery(SPLIT_QUERY);
  const [page, setPage] = useState<PageState<ApiCall>>({ state: 'loading' });
  const [draft, setDraft] = useState(''), [query, setQuery] = useState('');
  const [direction, setDirection] = useState<Direction | ''>(''), [outcome, setOutcome] = useState<Outcome | ''>(''), [mine, setMine] = useState(false);
  const [offset, setOffset] = useState(0);
  const autoPicked = useRef(false);
  const load = useCallback(() => {
    callsApi.listCalls({ query, direction, outcome, mine, offset, limit: PAGE_SIZE }).then((value) => setPage({ state: 'ready', value }), (e) => setPage(fail(e)));
  }, [query, direction, outcome, mine, offset, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const live = page.state === 'ready' && page.value.items.some(isLive);
  useEffect(() => {
    if (!live) return undefined;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [live, load]);
  const first = page.state === 'ready' ? page.value.items[0]?.id ?? null : null;
  useEffect(() => {
    if (!wide || autoPicked.current || selected || !first) return;
    autoPicked.current = true;
    history.replaceState(null, '', `#calls?call=${encodeURIComponent(first)}`);
    onSelect(first);
  }, [wide, selected, first]); // eslint-disable-line react-hooks/exhaustive-deps
  const searching = Boolean(query || direction || outcome || mine);
  const clear = () => { setDraft(''); setQuery(''); setDirection(''); setOutcome(''); setMine(false); setOffset(0); };
  const submit = (event: FormEvent) => { event.preventDefault(); setOffset(0); setQuery(draft.trim()); };

  return <SplitView className="callsSplit" show={selected ? 'detail' : 'list'}>
    <section data-pane="list" className="pane callsListPane" aria-label={t('calls.tab.recents')} aria-busy={page.state === 'loading'}>
      <div className="paneHead callsListHead">
        <form className="toolbar callsToolbar" role="search" aria-label={t('calls.recents.search')} onSubmit={submit}>
          <label className="visuallyHidden" htmlFor={`${ids}-q`}>{t('calls.recents.search')}</label>
          <input id={`${ids}-q`} type="search" value={draft} placeholder={t('calls.recents.search')} maxLength={200} onChange={(event) => { setDraft(event.target.value); if (!event.target.value) { setQuery(''); setOffset(0); } }} />
          <button type="submit" className="secondary iconOnly" aria-label={t('common.search')} title={t('common.search')}><Search aria-hidden="true" /></button>
          <label className="visuallyHidden" htmlFor={`${ids}-dir`}>{t('calls.recents.direction')}</label>
          <select id={`${ids}-dir`} value={direction} onChange={(event) => { setOffset(0); setDirection(event.target.value as Direction | ''); }}>
            <option value="">{t('calls.recents.allDirections')}</option><option value="outbound">{directionLabel('outbound')}</option><option value="inbound">{directionLabel('inbound')}</option>
          </select>
          <label className="visuallyHidden" htmlFor={`${ids}-out`}>{t('calls.recents.outcomeFilter')}</label>
          <select id={`${ids}-out`} value={outcome} onChange={(event) => { setOffset(0); setOutcome(event.target.value as Outcome | ''); }}>
            <option value="">{t('calls.recents.allOutcomes')}</option>{OUTCOMES.map((value) => <option key={value} value={value}>{outcomeLabel(value)}</option>)}
          </select>
          <label className="checkRow callsMine"><input type="checkbox" checked={mine} onChange={(event) => { setOffset(0); setMine(event.target.checked); }} />{t('calls.recents.mine')}</label>
        </form>
      </div>
      {page.state === 'loading' && <div className="paneBody"><Notice>{t('calls.recents.loading')}</Notice></div>}
      {page.state === 'error' && <div className="paneBody"><ErrorNotice message={t('calls.recents.failed', { message: page.message })} onRetry={load} /></div>}
      {page.state === 'denied' && <div className="paneBody"><p className="notice" role="status"><Lock aria-hidden="true" />{page.message}</p></div>}
      {page.state === 'ready' && page.value.items.length > 0 && <ul className="callsRows">
        {page.value.items.map((call) => <li key={call.id} className={call.id === selected ? 'selected' : undefined}>
          <button type="button" className="callsRow" aria-current={call.id === selected ? 'true' : undefined} aria-label={t('calls.recents.open', { name: callWith(call) })} onClick={() => onSelect(call.id)}>
            <span className="callsRowIcon" aria-hidden="true">{call.kind.startsWith('ai') ? <Bot /> : call.direction === 'inbound' ? <PhoneIncoming /> : <PhoneOutgoing />}</span>
            <span className="callsRowText">
              <span className="callsRowTop"><strong dir="auto">{callWith(call)}</strong><span className={`statusBadge ${outcomeTone(call.outcome)}`}>{isLive(call) ? t('calls.detail.live') : outcomeLabel(call.outcome)}</span></span>
              <small>{[kindLabel(call.kind), formatDateTime(call.started_at, { dateStyle: 'medium', timeStyle: 'short' }), durationText(call.duration_seconds)].filter(Boolean).join(' · ')}</small>
              {(call.summary || call.notes) && <small className="callsSnippet" dir="auto">{call.summary ?? call.notes}</small>}
            </span>
          </button>
        </li>)}
      </ul>}
      {page.state === 'ready' && <Pager offset={offset} limit={PAGE_SIZE} total={page.value.total} onOffset={setOffset} />}
      {page.state === 'ready' && !page.value.items.length && (searching
        ? <div className="paneEmpty"><Search aria-hidden="true" /><h3>{t('calls.recents.noMatch')}</h3><p>{t('calls.recents.noMatchText')}</p><div className="actions"><button type="button" className="secondary" onClick={clear}>{t('calls.clearFilters')}</button></div></div>
        : <div className="paneEmpty"><PhoneCall aria-hidden="true" /><h3>{t('calls.recents.empty')}</h3><p>{t('calls.recents.emptyText')}</p>{setup?.can_log && <div className="actions"><button type="button" onClick={onOpenDial}>{t('calls.recents.emptyAction')}</button></div>}</div>)}
    </section>
    <section data-pane="detail" className="pane callsDetailPane" aria-label={selected ? undefined : t('calls.recents.pickTitle')}>
      {selected
        ? <CallDetail key={selected} callId={selected} setup={setup} onClose={() => { history.replaceState(null, '', '#calls'); onSelect(null); }} onChanged={() => { load(); onChanged(); }} onDeleted={() => { onSelect(null); load(); onChanged(); }} onDial={onDial} onCallInAnum={onCallInAnum} onOpenTasks={onOpenTasks} onOpenSettings={onOpenSettings} />
        : <div className="paneEmpty"><PhoneCall aria-hidden="true" /><h3>{t('calls.recents.pickTitle')}</h3><p>{t('calls.recents.pickText')}</p></div>}
    </section>
  </SplitView>;
}

function CallDetail({ callId, setup, onClose, onChanged, onDeleted, onDial, onCallInAnum, onOpenTasks, onOpenSettings }: {
  callId: string; setup: ApiCallsSetup | null; onClose(): void; onChanged(): void; onDeleted(): void; onDial(target: DialTarget): void; onCallInAnum(target: DialTarget): void;
  onOpenTasks?(): void; onOpenSettings?(): void;
}) {
  const ids = useId();
  const [detail, setDetail] = useState<Load<ApiCall> | { state: 'missing' }>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [notes, setNotes] = useState(''), [follow, setFollow] = useState(''), [due, setDue] = useState('');
  const [busy, setBusy] = useState<string | null>(null), [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState(false), [deleting, setDeleting] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const show = (call: ApiCall) => { setDetail({ state: 'ready', value: call }); setNotes(call.notes ?? ''); setFollow(call.follow_up?.text ?? ''); setDue(call.follow_up?.due_date ?? ''); };
  useEffect(() => {
    callsApi.getCall(callId).then(show, (e) => setDetail(e?.status === 404 ? { state: 'missing' } : fail(e)));
  }, [callId, attempt]);
  useEffect(() => { heading.current?.focus(); }, [callId]);
  const call = detail.state === 'ready' ? detail.value : null;
  useEffect(() => {
    if (!call || !isLive(call)) return undefined;
    const timer = window.setInterval(() => setAttempt((n) => n + 1), 5000);
    return () => window.clearInterval(timer);
  }, [call]);

  async function act(name: string, run: () => Promise<ApiCall | void>, done?: string) {
    setBusy(name); setProblem(null); setNote(null);
    try { const result = await run(); if (result) show(result); if (done) setNote(done); onChanged(); }
    catch (e) { setProblem(describeError(e)); }
    finally { setBusy(null); }
  }

  const canEdit = call?.can_edit ?? false;
  const target: DialTarget | null = call?.phone ? { phone: call.phone, contact: null } : null;
  return <section className="callsDetail" aria-labelledby={`${ids}-title`} aria-busy={detail.state === 'loading'}>
    <div className="paneHead">
      <button type="button" className="secondary paneBack" onClick={onClose}><ArrowLeft aria-hidden="true" className="flipRtl" />{t('calls.back')}</button>
      <h2 id={`${ids}-title`} ref={heading} tabIndex={-1} dir="auto">{call ? callWith(call) : t('calls.recents.pickTitle')}</h2>
      <button type="button" className="iconButton small" aria-label={t('calls.detail.close')} title={t('calls.detail.close')} onClick={onClose}><X aria-hidden="true" /></button>
    </div>
    <div className="paneBody">
      {detail.state === 'loading' && <Notice>{t('calls.detail.loading')}</Notice>}
      {detail.state === 'missing' && <Notice error>{t('calls.detail.missing')}</Notice>}
      {detail.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{detail.message}</p>}
      {detail.state === 'error' && <ErrorNotice message={t('calls.detail.failed', { message: detail.message })} onRetry={() => setAttempt((n) => n + 1)} />}
      {call && <>
        <dl className="taskMeta callsMeta">
          <div><dt>{t('calls.detail.outcome')}</dt><dd><span className={`statusBadge ${outcomeTone(call.outcome)}`}>{isLive(call) ? t('calls.detail.live') : outcomeLabel(call.outcome)}</span></dd></div>
          {call.phone && <div><dt>{t('calls.detail.number')}</dt><dd><bdi dir="ltr">{call.phone}</bdi></dd></div>}
          <div><dt>{t('calls.detail.when')}</dt><dd><time dateTime={call.started_at}>{formatDateTime(call.started_at)}</time></dd></div>
          {call.duration_seconds !== null && <div><dt>{t('calls.detail.duration')}</dt><dd dir="ltr">{durationText(call.duration_seconds)}</dd></div>}
          <div><dt>{t('calls.detail.direction')}</dt><dd>{directionLabel(call.direction)}</dd></div>
          <div><dt>{t('calls.detail.kind')}</dt><dd>{kindLabel(call.kind)}</dd></div>
          {call.cost_cents !== null && <div><dt>{t('calls.detail.cost')}</dt><dd dir="ltr">{`$${(call.cost_cents / 100).toFixed(2)}`}</dd></div>}
        </dl>
        {target && isDialable(target.phone) && setup?.can_log && <div className="actions">
          <CallLink target={target} onDial={onDial} className="buttonLink secondary" />
          {setup.capabilities.browser_calls && <button type="button" className="secondary" onClick={() => onCallInAnum(target)}><PhoneCall aria-hidden="true" />{t('calls.callInAnum')}</button>}
        </div>}
        {!canEdit && <p className="muted">{t('calls.detail.readOnly')}</p>}
        {problem && <Notice error>{problem}</Notice>}
        {note && <Notice>{note}</Notice>}

        <section className="meetingSection" aria-labelledby={`${ids}-notes`}>
          <h3 id={`${ids}-notes`}>{t('calls.notes.title')}</h3>
          {canEdit ? <form onSubmit={(event) => { event.preventDefault(); void act('notes', () => callsApi.updateCall(call.id, { notes }), t('calls.notes.saved')); }}>
            <label className="visuallyHidden" htmlFor={`${ids}-notes-input`}>{t('calls.notes.label')}</label>
            <textarea id={`${ids}-notes-input`} value={notes} dir="auto" rows={4} maxLength={8000} placeholder={t('calls.notes.placeholder')} onChange={(event) => setNotes(event.target.value)} />
            <div className="actions">
              <button type="submit" className="secondary" disabled={busy !== null || notes === (call.notes ?? '')}>{busy === 'notes' ? t('calls.saving') : t('calls.notes.save')}</button>
              <button type="button" className="secondary" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('calls.detail.edit')}</button>
            </div>
          </form> : <p dir="auto" className="callsText">{call.notes || t('calls.notes.placeholder')}</p>}
        </section>

        <section className="meetingSection" aria-labelledby={`${ids}-summary`}>
          <div className="meetingSectionHead">
            <h3 id={`${ids}-summary`}><Sparkles aria-hidden="true" />{t('calls.summary.title')}</h3>
            {call.summary_provenance && <span className="aiLabel"><Sparkles aria-hidden="true" />{t('calls.summary.label', { model: call.summary_provenance.model })}</span>}
          </div>
          {call.summary ? <><p className="meetingSummary" dir="auto">{call.summary}</p><p className="muted">{t('calls.summary.check')}</p></> : <p className="muted">{t('calls.summary.none')}</p>}
          {setup && !setup.model_ready && <div className="notice" role="status"><Sparkles aria-hidden="true" /><span>{t('calls.summary.noModel')}</span>{onOpenSettings && <button type="button" className="secondary" onClick={onOpenSettings}>{t('meetings.openModelSettings')}</button>}</div>}
          {canEdit && setup?.model_ready && <div className="actions">
            <button type="button" className="secondary" disabled={busy !== null || !(call.notes || call.transcript)} onClick={() => void act('summary', () => callsApi.summarize(call.id), t('calls.summary.done'))}>
              <Sparkles aria-hidden="true" />{busy === 'summary' ? t('calls.summary.working') : call.summary ? t('calls.summary.again') : t('calls.summary.write')}
            </button>
          </div>}
        </section>

        <section className="meetingSection" aria-labelledby={`${ids}-follow`}>
          <h3 id={`${ids}-follow`}><ListChecks aria-hidden="true" />{t('calls.followUp.title')}</h3>
          {call.follow_up?.task_id && <p className="notice" role="status"><ListChecks aria-hidden="true" /><span>{t('calls.followUp.isTask')}</span>{onOpenTasks && <button type="button" className="secondary" onClick={onOpenTasks}>{t('calls.followUp.openTasks')}</button>}</p>}
          {canEdit ? <form className="callsFollowUp" onSubmit={(event) => { event.preventDefault(); void act('follow', () => callsApi.saveFollowUp(call.id, { text: follow, due_date: due || null }), t('calls.followUp.saved')); }}>
            <label className="field"><span>{t('calls.followUp.label')}</span><input value={follow} dir="auto" maxLength={500} onChange={(event) => setFollow(event.target.value)} /></label>
            <label className="field"><span>{t('calls.followUp.due')}</span><input type="date" value={due} onChange={(event) => setDue(event.target.value)} /></label>
            <div className="actions">
              <button type="submit" className="secondary" disabled={busy !== null || !follow.trim()}>{t('calls.followUp.save')}</button>
              {call.follow_up && !call.follow_up.task_id && <button type="button" disabled={busy !== null || !setup?.can_create_tasks} title={setup?.can_create_tasks ? undefined : t('calls.followUp.taskDenied')} onClick={() => void act('task', () => callsApi.followUpTask(call.id), t('calls.followUp.taskDone'))}><ListChecks aria-hidden="true" />{t('calls.followUp.task')}</button>}
              {call.follow_up && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('clear', () => callsApi.clearFollowUp(call.id), t('calls.followUp.removed'))}>{t('calls.followUp.remove')}</button>}
            </div>
            {!call.follow_up && <p className="muted">{t('calls.followUp.none')}</p>}
            {call.follow_up && !setup?.can_create_tasks && <p className="muted">{t('calls.followUp.taskDenied')}</p>}
          </form> : <p dir="auto">{call.follow_up?.text ?? t('calls.followUp.none')}</p>}
        </section>

        {call.recording_file_id && <section className="meetingSection" aria-labelledby={`${ids}-rec`}>
          <h3 id={`${ids}-rec`}><ShieldCheck aria-hidden="true" />{t('calls.recording.title')}</h3>
          {call.recording_consent && <><p className="muted">{t('calls.recording.consent', { who: call.recording_consent.confirmed_by, time: formatDateTime(call.recording_consent.confirmed_at) })}</p><blockquote dir="auto">{call.recording_consent.notice}</blockquote></>}
          {call.recording_expires_at && <p className="muted">{t('calls.recording.expires', { date: formatDateTime(call.recording_expires_at, { dateStyle: 'medium' }) })}</p>}
          <div className="actions"><RecordingLink fileId={call.recording_file_id} /></div>
        </section>}

        {call.transcript && <section className="meetingSection" aria-labelledby={`${ids}-tr`}>
          <h3 id={`${ids}-tr`}>{t('calls.transcript.title')}</h3>
          <pre className="callsTranscript" dir="auto">{call.transcript}</pre>
        </section>}

        {canEdit && <div className="actions callsDanger">
          <button type="button" className="danger" disabled={busy !== null || isLive(call)} onClick={() => setDeleting(true)}><Trash2 aria-hidden="true" />{t('calls.delete')}</button>
        </div>}
      </>}
    </div>
    {editing && call && <EditCallDialog call={call} onClose={() => setEditing(false)} onSaved={(saved) => { setEditing(false); show(saved); setNote(t('calls.detail.saved')); onChanged(); }} />}
    {deleting && call && <ConfirmDialog title={t('calls.delete.title')} confirmLabel={t('calls.delete')} danger busy={busy === 'delete'} onCancel={() => setDeleting(false)}
      onConfirm={() => { setBusy('delete'); callsApi.deleteCall(call.id).then(() => { setDeleting(false); onDeleted(); }, (e) => { setDeleting(false); setProblem(describeError(e)); }).finally(() => setBusy(null)); }}>
      <p>{t('calls.delete.text')}</p>
    </ConfirmDialog>}
  </section>;
}

/** The recording is a workspace file; download it with the person's own session. */
function RecordingLink({ fileId }: { fileId: string }) {
  const [problem, setProblem] = useState<string | null>(null);
  async function download() {
    setProblem(null);
    try {
      const { authHeaders } = await import('./lib/api');
      const response = await fetch(`${apiBaseUrl}/api/v1/files/${encodeURIComponent(fileId)}/download`, { headers: await authHeaders() });
      if (!response.ok) throw new Error(String(response.status));
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = 'call-recording.mp3'; link.click();
      URL.revokeObjectURL(url);
    } catch (e) { setProblem(describeError(e)); }
  }
  return <><button type="button" className="secondary" onClick={() => void download()}><Download aria-hidden="true" />{t('calls.recording.download')}</button>{problem && <Notice error>{problem}</Notice>}</>;
}

function EditCallDialog({ call, onClose, onSaved }: { call: ApiCall; onClose(): void; onSaved(call: ApiCall): void }) {
  const [outcome, setOutcome] = useState<Outcome>(call.outcome ?? 'answered');
  const [direction, setDirection] = useState<Direction>(call.direction);
  const [duration, setDuration] = useState(durationText(call.duration_seconds));
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  async function save(event: FormEvent) {
    event.preventDefault();
    const seconds = duration.trim() ? parseDuration(duration) : null;
    if (duration.trim() && seconds === null) { setProblem(t('calls.log.badDuration')); return; }
    setBusy(true); setProblem(null);
    try { onSaved(await callsApi.updateCall(call.id, { outcome, direction, ...(seconds !== null ? { duration_seconds: seconds } : {}) })); }
    catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('calls.detail.edit')} className="formDialog" onClose={onClose}>
    <form className="formGrid callsForm" onSubmit={(event) => void save(event)}>
      <label className="field"><span>{t('calls.log.outcome')}</span><select value={outcome} onChange={(event) => setOutcome(event.target.value as Outcome)}>{OUTCOMES.map((value) => <option key={value} value={value}>{outcomeLabel(value)}</option>)}</select></label>
      <label className="field"><span>{t('calls.log.direction')}</span><select value={direction} onChange={(event) => setDirection(event.target.value as Direction)}><option value="outbound">{directionLabel('outbound')}</option><option value="inbound">{directionLabel('inbound')}</option></select></label>
      <label className="field"><span>{t('calls.log.duration')}</span><input value={duration} dir="ltr" maxLength={9} onChange={(event) => setDuration(event.target.value)} /></label>
      {problem && <div className="callsWide"><Notice error>{problem}</Notice></div>}
      <div className="actions callsWide"><button type="submit" disabled={busy} data-autofocus>{busy ? t('calls.saving') : t('calls.contact.save')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ----------------------------------------------------------------------------------------- contacts

export function ContactsTab({ refresh, setup, selected, onSelect, onDial, onCallInAnum, onChanged, onAdd, onOpenCall, onAskAi }: {
  refresh: number; setup: ApiCallsSetup | null; selected: string | null; onSelect(id: string | null): void; onDial(target: DialTarget): void; onCallInAnum(target: DialTarget): void;
  onChanged(): void; onAdd(): void; onOpenCall(id: string): void; onAskAi(): void;
}) {
  const ids = useId();
  const wide = useMediaQuery(SPLIT_QUERY);
  const [page, setPage] = useState<PageState<ApiContact>>({ state: 'loading' });
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [favourites, setFavourites] = useState(false), [offset, setOffset] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const autoPicked = useRef(false);
  const load = useCallback(() => {
    callsApi.listContacts({ query, favourites, offset, limit: PAGE_SIZE }).then((value) => setPage({ state: 'ready', value }), (e) => setPage(fail(e)));
  }, [query, favourites, offset, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const first = page.state === 'ready' ? page.value.items[0]?.id ?? null : null;
  useEffect(() => {
    if (!wide || autoPicked.current || selected || !first) return;
    autoPicked.current = true;
    history.replaceState(null, '', `#calls-contacts?contact=${encodeURIComponent(first)}`);
    onSelect(first);
  }, [wide, selected, first]); // eslint-disable-line react-hooks/exhaustive-deps
  const searching = Boolean(query || favourites);
  const star = (contact: ApiContact) => {
    setProblem(null);
    callsApi.updateContact(contact.id, { favourite: !contact.favourite }).then(() => { load(); }, (e) => setProblem(describeError(e)));
  };

  return <SplitView className="callsSplit" show={selected ? 'detail' : 'list'}>
    <section data-pane="list" className="pane callsListPane" aria-label={t('calls.tab.contacts')} aria-busy={page.state === 'loading'}>
      <div className="paneHead callsListHead">
        <form className="toolbar callsToolbar" role="search" aria-label={t('calls.contacts.search')} onSubmit={(event) => { event.preventDefault(); setOffset(0); setQuery(draft.trim()); }}>
          <label className="visuallyHidden" htmlFor={`${ids}-q`}>{t('calls.contacts.search')}</label>
          <input id={`${ids}-q`} type="search" value={draft} placeholder={t('calls.contacts.search')} maxLength={200} onChange={(event) => { setDraft(event.target.value); if (!event.target.value) { setQuery(''); setOffset(0); } }} />
          <button type="submit" className="secondary iconOnly" aria-label={t('common.search')} title={t('common.search')}><Search aria-hidden="true" /></button>
          <label className="checkRow callsMine"><input type="checkbox" checked={favourites} onChange={(event) => { setOffset(0); setFavourites(event.target.checked); }} />{t('calls.contacts.favourites')}</label>
        </form>
      </div>
      {problem && <div className="paneBody"><Notice error>{problem}</Notice></div>}
      {page.state === 'loading' && <div className="paneBody"><Notice>{t('calls.contacts.loading')}</Notice></div>}
      {page.state === 'error' && <div className="paneBody"><ErrorNotice message={t('calls.contacts.failed', { message: page.message })} onRetry={load} /></div>}
      {page.state === 'denied' && <div className="paneBody"><p className="notice" role="status"><Lock aria-hidden="true" />{page.message}</p></div>}
      {page.state === 'ready' && page.value.items.length > 0 && <ul className="callsRows">
        {page.value.items.map((contact) => <li key={contact.id} className={contact.id === selected ? 'selected callsContactItem' : 'callsContactItem'}>
          <button type="button" className="callsRow" aria-current={contact.id === selected ? 'true' : undefined} aria-label={t('calls.contacts.open', { name: contact.name })} onClick={() => onSelect(contact.id)}>
            <span className="callsAvatar" aria-hidden="true">{initials(contact.name)}</span>
            <span className="callsRowText">
              <span className="callsRowTop"><strong dir="auto">{contact.name}</strong>{contact.ai_consent && <span className="tagChip"><Bot aria-hidden="true" /></span>}</span>
              <small><bdi dir="ltr">{contact.phone}</bdi>{contact.company ? <> · <span dir="auto">{contact.company}</span></> : null}</small>
            </span>
          </button>
          {setup?.can_log && <button type="button" className={`iconButton small callsStar ${contact.favourite ? 'on' : ''}`} aria-pressed={contact.favourite} aria-label={contact.favourite ? t('calls.contacts.unstar', { name: contact.name }) : t('calls.contacts.star', { name: contact.name })} onClick={() => star(contact)}><Star aria-hidden="true" /></button>}
        </li>)}
      </ul>}
      {page.state === 'ready' && <Pager offset={offset} limit={PAGE_SIZE} total={page.value.total} onOffset={setOffset} />}
      {page.state === 'ready' && !page.value.items.length && (searching
        ? <div className="paneEmpty"><Search aria-hidden="true" /><h3>{t('calls.contacts.noMatch')}</h3><p>{t('calls.contacts.noMatchText')}</p><div className="actions"><button type="button" className="secondary" onClick={() => { setDraft(''); setQuery(''); setFavourites(false); setOffset(0); }}>{t('calls.clearFilters')}</button></div></div>
        : <div className="paneEmpty"><UserPlus aria-hidden="true" /><h3>{t('calls.contacts.empty')}</h3><p>{setup?.can_log ? t('calls.contacts.emptyText') : t('calls.contacts.emptyReadOnly')}</p>{setup?.can_log && <div className="actions"><button type="button" onClick={onAdd}><UserPlus aria-hidden="true" />{t('calls.action.addContact')}</button></div>}</div>)}
    </section>
    <section data-pane="detail" className="pane callsDetailPane" aria-label={selected ? undefined : t('calls.contacts.pickTitle')}>
      {selected
        ? <ContactDetail key={selected} contactId={selected} setup={setup} onClose={() => { history.replaceState(null, '', '#calls-contacts'); onSelect(null); }} onChanged={() => { load(); onChanged(); }} onDeleted={() => { onSelect(null); load(); onChanged(); }} onDial={onDial} onCallInAnum={onCallInAnum} onOpenCall={onOpenCall} onAskAi={onAskAi} />
        : <div className="paneEmpty"><UserPlus aria-hidden="true" /><h3>{t('calls.contacts.pickTitle')}</h3><p>{t('calls.contacts.pickText')}</p></div>}
    </section>
  </SplitView>;
}

const METHODS: readonly ConsentMethod[] = ['verbal', 'written', 'web_form', 'other'];

function ContactDetail({ contactId, setup, onClose, onChanged, onDeleted, onDial, onCallInAnum, onOpenCall, onAskAi }: {
  contactId: string; setup: ApiCallsSetup | null; onClose(): void; onChanged(): void; onDeleted(): void; onDial(target: DialTarget): void; onCallInAnum(target: DialTarget): void;
  onOpenCall(id: string): void; onAskAi(): void;
}) {
  const ids = useId();
  const [detail, setDetail] = useState<Load<ApiContact> | { state: 'missing' }>({ state: 'loading' });
  const [calls, setCalls] = useState<Load<ApiCall[]>>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false), [deleting, setDeleting] = useState(false), [consenting, setConsenting] = useState(false);
  const [busy, setBusy] = useState(false), [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    callsApi.getContact(contactId).then((value) => setDetail({ state: 'ready', value }), (e) => setDetail(e?.status === 404 ? { state: 'missing' } : fail(e)));
    callsApi.listCalls({ contactId, limit: 10 }).then((value) => setCalls({ state: 'ready', value: value.items }), (e) => setCalls(fail(e)));
  }, [contactId, attempt]);
  useEffect(() => { heading.current?.focus(); }, [contactId]);
  const contact = detail.state === 'ready' ? detail.value : null;
  const blocker = contact ? aiCallBlocker(contact, setup) : null;

  return <section className="callsDetail" aria-labelledby={`${ids}-title`} aria-busy={detail.state === 'loading'}>
    <div className="paneHead">
      <button type="button" className="secondary paneBack" onClick={onClose}><ArrowLeft aria-hidden="true" className="flipRtl" />{t('calls.back')}</button>
      <h2 id={`${ids}-title`} ref={heading} tabIndex={-1} dir="auto">{contact?.name ?? t('calls.contacts.pickTitle')}</h2>
      <button type="button" className="iconButton small" aria-label={t('dialog.close')} title={t('dialog.close')} onClick={onClose}><X aria-hidden="true" /></button>
    </div>
    <div className="paneBody">
      {detail.state === 'loading' && <Notice>{t('calls.contacts.loading')}</Notice>}
      {detail.state === 'missing' && <Notice error>{t('calls.detail.missing')}</Notice>}
      {detail.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{detail.message}</p>}
      {detail.state === 'error' && <ErrorNotice message={t('calls.contacts.failed', { message: detail.message })} onRetry={() => setAttempt((n) => n + 1)} />}
      {contact && <>
        <dl className="taskMeta callsMeta">
          <div><dt>{t('calls.contact.phone')}</dt><dd><bdi dir="ltr">{contact.phone}</bdi></dd></div>
          {contact.company && <div><dt>{t('calls.contact.company')}</dt><dd dir="auto">{contact.company}</dd></div>}
          {contact.email && <div><dt>{t('calls.contact.email')}</dt><dd><bdi dir="ltr">{contact.email}</bdi></dd></div>}
          {contact.language !== 'auto' && <div><dt>{t('calls.contact.language')}</dt><dd>{t(`calls.contact.lang.${contact.language}` as MessageKey)}</dd></div>}
          {contact.source === 'device' && <div><dt>{t('calls.contact.name')}</dt><dd>{t('calls.contact.fromDevice')}</dd></div>}
        </dl>
        {contact.notes && <p className="callsText" dir="auto">{contact.notes}</p>}
        {setup?.can_log && <div className="actions">
          <CallLink target={{ phone: contact.phone, contact }} onDial={onDial} />
          {setup.capabilities.browser_calls && <button type="button" className="secondary" onClick={() => onCallInAnum({ phone: contact.phone, contact })}><PhoneCall aria-hidden="true" />{t('calls.callInAnum')}</button>}
          {contact.can_edit && <button type="button" className="secondary" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('calls.contact.edit')}</button>}
        </div>}
        {!contact.can_edit && setup?.can_log && <p className="muted">{t('calls.contact.readOnly')}</p>}
        {problem && <Notice error>{problem}</Notice>}
        {note && <Notice>{note}</Notice>}

        <section className="meetingSection" aria-labelledby={`${ids}-ai`}>
          <h3 id={`${ids}-ai`}><Bot aria-hidden="true" />{t('calls.consent.title')}</h3>
          {contact.opted_out ? <p className="notice" role="status"><ShieldCheck aria-hidden="true" /><span>{t('calls.consent.optedOut')}</span></p>
            : contact.ai_consent ? <p>{t('calls.consent.given', { method: t(`calls.consent.method.${contact.ai_consent.method}` as MessageKey), date: formatDateTime(contact.ai_consent.granted_at, { dateStyle: 'medium' }), who: contact.ai_consent.recorded_by })}</p>
              : <p className="muted">{t('calls.consent.none')}</p>}
          <div className="actions">
            {setup?.can_manage && !contact.ai_consent && !contact.opted_out && <button type="button" className="secondary" onClick={() => setConsenting(true)}><ShieldCheck aria-hidden="true" />{t('calls.consent.record')}</button>}
            {setup?.can_log && contact.ai_consent && <button type="button" className="secondary" disabled={busy} onClick={() => { setBusy(true); setProblem(null); callsApi.withdrawConsent(contact.id).then((value) => { setDetail({ state: 'ready', value }); setNote(t('calls.consent.withdrawn')); onChanged(); }, (e) => setProblem(describeError(e))).finally(() => setBusy(false)); }}>{t('calls.consent.withdraw')}</button>}
            {!blocker && <button type="button" onClick={onAskAi}><Bot aria-hidden="true" />{t('calls.ai.new')}</button>}
          </div>
          {!setup?.can_manage && !contact.ai_consent && !contact.opted_out && <p className="muted">{t('calls.consent.ownerOnly')}</p>}
          {blocker && contact.ai_consent && <p className="muted">{blocker}</p>}
        </section>

        <section className="meetingSection" aria-labelledby={`${ids}-calls`}>
          <h3 id={`${ids}-calls`}>{t('calls.contact.recent', { name: contact.name })}</h3>
          {calls.state === 'loading' && <Notice>{t('calls.recents.loading')}</Notice>}
          {calls.state === 'error' && <ErrorNotice message={t('calls.recents.failed', { message: calls.message })} onRetry={() => setAttempt((n) => n + 1)} />}
          {calls.state === 'ready' && !calls.value.length && <p className="muted">{t('calls.contact.noCalls')}</p>}
          {calls.state === 'ready' && calls.value.length > 0 && <ul className="railList">{calls.value.map((call) => <li key={call.id}>
            <button type="button" className="railItem" onClick={() => onOpenCall(call.id)}>
              <strong>{outcomeLabel(call.outcome)} · {directionLabel(call.direction)}</strong>
              <small>{[formatDateTime(call.started_at, { dateStyle: 'medium', timeStyle: 'short' }), durationText(call.duration_seconds)].filter(Boolean).join(' · ')}</small>
            </button>
          </li>)}</ul>}
        </section>

        {contact.can_edit && <div className="actions callsDanger"><button type="button" className="danger" onClick={() => setDeleting(true)}><Trash2 aria-hidden="true" />{t('calls.contact.delete')}</button></div>}
      </>}
    </div>
    {editing && contact && <Modal title={t('calls.contact.editTitle', { name: contact.name })} className="formDialog" onClose={() => setEditing(false)}>
      <ContactForm contact={contact} onCancel={() => setEditing(false)} onSaved={(saved) => { setEditing(false); setDetail({ state: 'ready', value: saved }); setNote(t('calls.contact.saved')); onChanged(); }} />
    </Modal>}
    {consenting && contact && <ConsentDialog contact={contact} onClose={() => setConsenting(false)} onSaved={(saved) => { setConsenting(false); setDetail({ state: 'ready', value: saved }); setNote(t('calls.consent.recorded')); onChanged(); }} />}
    {deleting && contact && <ConfirmDialog title={t('calls.contact.deleteTitle', { name: contact.name })} confirmLabel={t('calls.contact.delete')} danger busy={busy} onCancel={() => setDeleting(false)}
      onConfirm={() => { setBusy(true); callsApi.deleteContact(contact.id).then(() => { setDeleting(false); onDeleted(); }, (e) => { setDeleting(false); setProblem(describeError(e)); }).finally(() => setBusy(false)); }}>
      <p>{t('calls.contact.deleteText')}</p>
    </ConfirmDialog>}
  </section>;
}

function ConsentDialog({ contact, onClose, onSaved }: { contact: ApiContact; onClose(): void; onSaved(contact: ApiContact): void }) {
  const [method, setMethod] = useState<ConsentMethod>('verbal'), [noteText, setNoteText] = useState(''), [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try { onSaved(await callsApi.recordConsent(contact.id, { method, note: noteText.trim() || undefined, confirmed })); }
    catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('calls.consent.record')} className="formDialog" onClose={onClose}>
    <form className="formGrid callsForm" onSubmit={(event) => void save(event)}>
      <label className="field"><span>{t('calls.consent.method')}</span><select value={method} onChange={(event) => setMethod(event.target.value as ConsentMethod)}>{METHODS.map((value) => <option key={value} value={value}>{t(`calls.consent.method.${value}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('calls.consent.note')}</span><input value={noteText} dir="auto" maxLength={500} onChange={(event) => setNoteText(event.target.value)} /></label>
      <label className="checkRow callsWide"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />{t('calls.consent.confirm', { name: contact.name })}</label>
      {problem && <div className="callsWide"><Notice error>{problem}</Notice></div>}
      <div className="actions callsWide"><button type="submit" disabled={busy || !confirmed} data-autofocus>{busy ? t('calls.saving') : t('calls.consent.record')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

/** Add or edit a contact; the number is checked here first and again by the API. */
export function ContactForm({ contact, onCancel, onSaved }: { contact?: ApiContact; onCancel(): void; onSaved(contact: ApiContact): void }) {
  const ids = useId();
  const [name, setName] = useState(contact?.name ?? ''), [phone, setPhone] = useState(contact?.phone ?? '');
  const [company, setCompany] = useState(contact?.company ?? ''), [email, setEmail] = useState(contact?.email ?? ''), [notes, setNotes] = useState(contact?.notes ?? '');
  const [language, setLanguage] = useState<'auto' | 'en' | 'ar'>(contact?.language ?? 'auto');
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setProblem(t('calls.contact.nameRequired')); return; }
    if (!isDialable(phone)) { setProblem(t('calls.contact.badPhone')); return; }
    setBusy(true); setProblem(null);
    const body = { name: name.trim(), phone: phone.trim(), company: company.trim() || null, email: email.trim() || null, notes: notes.trim() || null, language };
    try { onSaved(contact ? await callsApi.updateContact(contact.id, body) : await callsApi.createContact(body)); }
    catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="formGrid callsForm" onSubmit={(event) => void save(event)}>
    <label className="field"><span>{t('calls.contact.name')}</span><input value={name} dir="auto" maxLength={200} required autoComplete="off" data-autofocus onChange={(event) => setName(event.target.value)} /></label>
    <label className="field"><span>{t('calls.contact.phone')}</span><input value={phone} dir="ltr" inputMode="tel" maxLength={40} required aria-describedby={`${ids}-phone`} autoComplete="off" onChange={(event) => setPhone(event.target.value)} /><small id={`${ids}-phone`} className="muted">{t('calls.contact.phoneHint')}</small></label>
    <label className="field"><span>{t('calls.contact.company')}</span><input value={company} dir="auto" maxLength={200} onChange={(event) => setCompany(event.target.value)} /></label>
    <label className="field"><span>{t('calls.contact.email')}</span><input type="email" value={email} dir="ltr" maxLength={320} onChange={(event) => setEmail(event.target.value)} /></label>
    <label className="field"><span>{t('calls.contact.language')}</span><select value={language} onChange={(event) => setLanguage(event.target.value as 'auto' | 'en' | 'ar')}>{(['auto', 'en', 'ar'] as const).map((value) => <option key={value} value={value}>{t(`calls.contact.lang.${value}` as MessageKey)}</option>)}</select></label>
    <label className="field callsWide"><span>{t('calls.contact.notes')}</span><textarea value={notes} dir="auto" maxLength={4000} rows={3} onChange={(event) => setNotes(event.target.value)} /></label>
    {problem && <div className="callsWide"><Notice error>{problem}</Notice></div>}
    <div className="actions callsWide"><button type="submit" disabled={busy}>{busy ? t('calls.saving') : t('calls.contact.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
  </form>;
}

