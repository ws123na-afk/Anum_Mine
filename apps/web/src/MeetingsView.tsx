import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { CopyAction } from './MessageActions';
import { CheckCircle2, CircleDot, Copy, Link2, ListChecks, Lock, Mail, Pencil, PhoneCall, RefreshCw, Search, Send, Sparkles, Trash2, Unlink, Upload, X } from 'lucide-react';
import * as api from './lib/api';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import {
  clock, draftText, durationText, isWorking, mailtoLink, oauthCallback, pageNumbers, parseRecipients, recentActivity, sourceLabel,
  statusLabel, statusTone, textDirection, uploadProblem, MEETING_PAGE_SIZE, STATUSES, TRANSCRIPT_PAGE_SIZE,
  type ApiEmailAccountChoice, type ApiExternalMeeting, type ApiMeeting, type ApiMeetingSetup, type ApiTranscriptPage, type ConnectorProvider, type MeetingLanguage, type MeetingStatus,
} from './lib/meetings';
import * as meetingsApi from './lib/meetingsApi';
import { Drawer } from './Dialog';
import './admin.css';
import './meetings.css';

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied' } | { state: 'error'; message: string };
const fail = <T,>(e: unknown): Load<T> => isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) };
const POLL_MS = 2500;

/**
 * Calls & Meetings (docs/meetings.md): import a recording, a transcript file or a Zoom/Teams
 * transcript; read the AI summary, decisions, action items and follow-up draft with their
 * provenance; search the transcript. Opens the Zoom/Microsoft sign-in callback too.
 */
export function MeetingsView({ onOpenTasks, onOpenApprovals, onOpenSettings }: { onOpenTasks?(): void; onOpenApprovals?(): void; onOpenSettings?(): void }) {
  const searchId = useId();
  const [setup, setSetup] = useState<Load<ApiMeetingSetup>>({ state: 'loading' });
  const [page, setPage] = useState<Load<{ items: ApiMeeting[]; total: number }>>({ state: 'loading' });
  const [recent, setRecent] = useState<ApiMeeting[]>([]);
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [status, setStatus] = useState<MeetingStatus | ''>('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.hash.split('?')[1] ?? '').get('meeting'));
  const [importing, setImporting] = useState(false);
  const [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [agents, setAgents] = useState<string[]>([]);

  const loadSetup = useCallback(() => meetingsApi.getSetup().then((value) => setSetup({ state: 'ready', value }), (e) => setSetup(fail(e))), []);
  const load = useCallback(async () => {
    try {
      const result = await meetingsApi.listMeetings({ query, status, offset, limit: MEETING_PAGE_SIZE });
      setPage({ state: 'ready', value: { items: result.items, total: result.total } });
    } catch (e) { setPage(fail(e)); }
    meetingsApi.listMeetings({ status: 'ready', limit: 5 }).then((r) => setRecent(r.items), () => setRecent([]));
  }, [query, status, offset]);
  useEffect(() => { void loadSetup(); }, [loadSetup]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { meetingsApi.listAgents().then(setAgents, () => setAgents([])); }, []);

  // Keep the list fresh while anything on it is still being processed.
  const working = page.state === 'ready' && page.value.items.some((item) => isWorking(item.status));
  useEffect(() => {
    if (!working) return undefined;
    const timer = window.setInterval(() => void load(), POLL_MS * 2);
    return () => window.clearInterval(timer);
  }, [working, load]);

  // The Zoom / Microsoft sign-in comes back to /meetings/connected?code=...&state=...
  useEffect(() => {
    const callback = oauthCallback(window.location);
    if (!callback) return;
    window.history.replaceState(null, '', `${window.location.origin}/#meetings`);
    if ('error' in callback) { setProblem(t('meetings.connect.providerError', { message: callback.error })); return; }
    meetingsApi.finishConnector(callback.code, callback.state).then(
      (connected) => { setNote(t('meetings.connect.done', { provider: providerName(connected.provider), account: connected.account_label ?? '' })); void loadSetup(); setImporting(true); },
      (e) => setProblem(t('meetings.connect.failed', { message: describeError(e) })),
    );
  }, [loadSetup]);

  const open = (id: string | null) => {
    setSelected(id);
    history.replaceState(null, '', id ? `#meetings?meeting=${encodeURIComponent(id)}` : '#meetings');
  };
  const info = setup.state === 'ready' ? setup.value : null;
  const canCreate = info?.can_create ?? false;
  const searching = Boolean(query || status);
  const total = page.state === 'ready' ? page.value.total : 0;
  const pages = Math.max(1, Math.ceil(total / MEETING_PAGE_SIZE));
  const current = Math.floor(offset / MEETING_PAGE_SIZE) + 1;
  const activity = recentActivity(recent);

  function submitSearch(event: FormEvent) { event.preventDefault(); setOffset(0); setQuery(draft.trim()); }

  return <>
    <section className="moduleIntro meetingsIntro">
      <div><p className="eyebrow">{t('meetings.eyebrow')}</p><h2>{t('meetings.title')}</h2><p>{t('meetings.intro')}</p></div>
      {canCreate && <button type="button" onClick={() => setImporting(true)}><Upload aria-hidden="true" />{t('meetings.importButton')}</button>}
    </section>
    {info && !info.can_create && <p className="notice" role="status"><Lock aria-hidden="true" />{t('meetings.viewerNote')}</p>}
    {info && !info.model_ready && <div className="notice" role="status"><Sparkles aria-hidden="true" /><span>{t('meetings.noModel')}</span>{onOpenSettings && <button type="button" className="secondary" onClick={onOpenSettings}>{t('meetings.openModelSettings')}</button>}</div>}
    {setup.state === 'error' && <ErrorNotice message={t('meetings.setupFailed', { message: setup.message })} onRetry={() => void loadSetup()} />}
    {note && <Notice>{note}</Notice>}
    {problem && <Notice error>{problem}</Notice>}

    {activity.length > 0 && <section className="surface meetingsActivity" aria-labelledby={`${searchId}-activity`}>
      <h2 id={`${searchId}-activity`}>{t('meetings.activity.title')}</h2>
      <ul>{activity.map((entry) => <li key={entry.id}>
        <PhoneCall aria-hidden="true" />
        <button type="button" className="rowLink" onClick={() => open(entry.meetingId)}><span dir="auto">{entry.title}</span></button>
        {entry.agent && <span className="tagChip" dir="ltr">{entry.agent}</span>}
        <time dateTime={entry.at}>{formatDateTime(entry.at)}</time>
      </li>)}</ul>
    </section>}

    {selected && <MeetingDetail key={selected} meetingId={selected} setup={info} agents={agents} onClose={() => open(null)} onChanged={() => void load()} onDeleted={(title) => { open(null); setNote(t('meetings.deleted', { title })); void load(); }} onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} onOpenSettings={onOpenSettings} />}

    {page.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{t('meetings.readDenied')}</p>}
    {page.state === 'error' && <ErrorNotice message={t('meetings.loadFailed', { message: page.message })} onRetry={() => void load()} />}
    {page.state !== 'denied' && (total > 0 || searching) && <form className="actions meetingsToolbar" role="search" onSubmit={submitSearch}>
      <label className="visuallyHidden" htmlFor={`${searchId}-q`}>{t('meetings.searchLabel')}</label>
      <input id={`${searchId}-q`} type="search" value={draft} placeholder={t('meetings.searchLabel')} maxLength={200} onChange={(e) => { setDraft(e.target.value); if (!e.target.value) { setQuery(''); setOffset(0); } }} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
      <label className="visuallyHidden" htmlFor={`${searchId}-status`}>{t('meetings.statusFilter')}</label>
      <select id={`${searchId}-status`} value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value as MeetingStatus | ''); }}>
        <option value="">{t('meetings.allStatuses')}</option>
        {STATUSES.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}
      </select>
    </form>}
    {page.state === 'loading' && <Notice>{t('meetings.loading')}</Notice>}
    {page.state === 'ready' && page.value.items.length > 0 && <section className="surface memoryTable meetingsTable" aria-label={t('meetings.list')}>
      {page.value.items.map((meeting) => <div className={`memoryRow ${meeting.id === selected ? 'focusedRow' : ''}`} key={meeting.id}>
        <div><span className="typeIcon"><PhoneCall aria-hidden="true" /></span><div>
          <button type="button" className="rowLink" aria-current={meeting.id === selected ? 'true' : undefined} aria-label={t('meetings.open', { title: meeting.title })} onClick={() => open(meeting.id)}><strong dir="auto">{meeting.title}</strong></button>
          <p>{[sourceLabel(meeting.source), formatDateTime(meeting.occurred_at ?? meeting.created_at), durationText(meeting.duration_seconds)].filter(Boolean).join(' · ')}{meeting.agent_skill_id && <> · <span className="tagChip" dir="ltr">{meeting.agent_skill_id}</span></>}</p>
          {meeting.summary && <p className="meetingSnippet" dir={textDirection(meeting.language)}>{meeting.summary}</p>}
        </div></div>
        <span className={`statusBadge ${statusTone(meeting.status)}`}>{statusLabel(meeting.status)}</span>
      </div>)}
    </section>}
    {page.state === 'ready' && pages > 1 && <nav className="meetingsPager" aria-label={t('meetings.pages')}>
      <button type="button" className="secondary" disabled={current <= 1} onClick={() => setOffset(offset - MEETING_PAGE_SIZE)}>{t('meetings.previous')}</button>
      {pageNumbers(current, pages).map((number, index) => number === null
        ? <span key={`gap-${index}`} aria-hidden="true">…</span>
        : <button key={number} type="button" className={number === current ? '' : 'secondary'} aria-current={number === current ? 'page' : undefined} aria-label={t('meetings.pageN', { page: number })} onClick={() => setOffset((number - 1) * MEETING_PAGE_SIZE)}>{number}</button>)}
      <button type="button" className="secondary" disabled={current >= pages} onClick={() => setOffset(offset + MEETING_PAGE_SIZE)}>{t('meetings.next')}</button>
      <span className="muted">{t('meetings.totalCount', { count: total })}</span>
    </nav>}
    {page.state === 'ready' && !page.value.items.length && (searching
      ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('meetings.noMatch')}</h3><p>{t('meetings.noMatchText')}</p><div className="actions"><button type="button" className="secondary" onClick={() => { setDraft(''); setQuery(''); setStatus(''); setOffset(0); }}>{t('meetings.clearSearch')}</button></div></div>
      : <div className="emptyState"><PhoneCall aria-hidden="true" /><h3>{t('meetings.empty')}</h3><p>{canCreate ? t('meetings.emptyText') : t('meetings.emptyReadOnly')}</p>{canCreate && <div className="actions"><button type="button" onClick={() => setImporting(true)}><Upload aria-hidden="true" />{t('meetings.importFirst')}</button></div>}</div>)}

    {importing && info && <ImportDrawer setup={info} agents={agents} onClose={() => setImporting(false)} onSetupChanged={() => void loadSetup()} onImported={(meeting) => { setImporting(false); setNote(t('meetings.imported', { title: meeting.title })); void load(); open(meeting.id); }} />}
  </>;
}

const providerName = (provider: ConnectorProvider) => provider === 'zoom' ? 'Zoom' : 'Microsoft Teams';

// ------------------------------------------------------------------------------------------- import

type ImportTab = 'transcript' | 'recording' | 'zoom' | 'teams';

function ImportDrawer({ setup, agents, onClose, onImported, onSetupChanged }: { setup: ApiMeetingSetup; agents: string[]; onClose(): void; onImported(meeting: ApiMeeting): void; onSetupChanged(): void }) {
  const [tab, setTab] = useState<ImportTab>(() => setup.connectors.some((c) => c.connected) ? (setup.connectors.find((c) => c.connected)?.provider ?? 'transcript') : 'transcript');
  const [title, setTitle] = useState(''), [language, setLanguage] = useState<MeetingLanguage>('auto'), [agent, setAgent] = useState('');
  const ids = useId();
  const tabs: ImportTab[] = ['transcript', 'recording', 'zoom', 'teams'];
  return <Drawer title={t('meetings.import.title')} onClose={onClose}>
    <div className="meetingImport">
      <div className="settingsTabs" role="tablist" aria-label={t('meetings.import.how')}>
        {tabs.map((value) => <button key={value} type="button" role="tab" id={`${ids}-tab-${value}`} aria-selected={tab === value} aria-controls={`${ids}-panel`} onClick={() => setTab(value)}>{t(`meetings.import.tab.${value}`)}</button>)}
      </div>
      <div id={`${ids}-panel`} role="tabpanel" aria-labelledby={`${ids}-tab-${tab}`} className="meetingImportPanel">
        <div className="formGrid">
          {(tab === 'transcript' || tab === 'recording') && <label className="field"><span>{t('meetings.import.titleField')}</span><input value={title} maxLength={200} dir="auto" placeholder={t('meetings.import.titlePlaceholder')} onChange={(e) => setTitle(e.target.value)} /></label>}
          <label className="field"><span>{t('meetings.import.language')}</span><select value={language} onChange={(e) => setLanguage(e.target.value as MeetingLanguage)}>
            <option value="auto">{t('meetings.language.auto')}</option><option value="en">{t('meetings.language.en')}</option><option value="ar">{t('meetings.language.ar')}</option>
          </select></label>
          <label className="field"><span>{t('meetings.import.agent')}</span><select value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">{t('meetings.import.noAgent')}</option>{agents.map((id) => <option key={id} value={id}>{id}</option>)}
          </select></label>
        </div>
        {(tab === 'transcript' || tab === 'recording') && <FileImport kind={tab} setup={setup} options={{ title, language, agentSkillId: agent || undefined }} onImported={onImported} />}
        {(tab === 'zoom' || tab === 'teams') && <ConnectorImport provider={tab} setup={setup} language={language} agent={agent} onImported={onImported} onSetupChanged={onSetupChanged} />}
      </div>
    </div>
  </Drawer>;
}

function FileImport({ kind, setup, options, onImported }: { kind: 'recording' | 'transcript'; setup: ApiMeetingSetup; options: { title: string; language: MeetingLanguage; agentSkillId?: string }; onImported(meeting: ApiMeeting): void }) {
  const picker = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<{ loaded: number; total: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const blocked = kind === 'recording' && !setup.transcription.configured;
  const accept = (kind === 'recording' ? setup.transcription.accepted_extensions : setup.transcript_extensions).join(',');
  const limit = Math.floor((kind === 'recording' ? setup.transcription.max_recording_bytes : setup.max_transcript_bytes) / (1024 * 1024));

  async function send(file: File) {
    setProblem(null);
    const refusal = uploadProblem(file, kind, setup);
    if (refusal) { setProblem(refusal); return; }
    abort.current = new AbortController();
    setProgress({ loaded: 0, total: file.size });
    try {
      const meeting = await meetingsApi.uploadMeetingFile(kind, file, options, (loaded, total) => setProgress({ loaded, total }), abort.current.signal);
      onImported(meeting);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') setProblem(t('meetings.import.cancelled'));
      else setProblem(isPermissionDenied(e) ? t('meetings.viewerNote') : describeError(e));
    } finally { setProgress(null); abort.current = null; }
  }

  if (blocked) return <div className="meetingSetupNote">
    <h3>{t('meetings.setup.transcriptionTitle')}</h3>
    <p>{t('meetings.setup.transcriptionText')}</p>
    <ul><li>{t('meetings.setup.localWhisper')}</li><li>{t('meetings.setup.hosted')}</li></ul>
    {setup.transcription.problem && <p className="muted" dir="ltr">{setup.transcription.problem}</p>}
  </div>;
  return <div className="meetingFileImport">
    <p className="muted">{kind === 'recording'
      ? t('meetings.import.recordingHint', { limit, provider: setup.transcription.target ?? setup.transcription.provider })
      : t('meetings.import.transcriptHint', { limit })}</p>
    <input ref={picker} type="file" className="visuallyHidden" tabIndex={-1} aria-hidden="true" accept={accept} onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void send(file); }} />
    {progress
      ? <div className="uploadItem"><div className="meterTrack" role="progressbar" aria-label={t('meetings.import.uploading')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.total ? Math.round(progress.loaded / progress.total * 100) : 0}><span style={{ width: `${progress.total ? Math.min(100, progress.loaded / progress.total * 100) : 0}%` }} /></div>
        <div className="actions"><button type="button" className="secondary" onClick={() => abort.current?.abort()}>{t('common.cancel')}</button></div></div>
      : <div className="actions"><button type="button" onClick={() => picker.current?.click()}><Upload aria-hidden="true" />{kind === 'recording' ? t('meetings.import.chooseRecording') : t('meetings.import.chooseTranscript')}</button></div>}
    {problem && <Notice error>{problem}</Notice>}
  </div>;
}

function ConnectorImport({ provider, setup, language, agent, onImported, onSetupChanged }: { provider: ConnectorProvider; setup: ApiMeetingSetup; language: MeetingLanguage; agent: string; onImported(meeting: ApiMeeting): void; onSetupChanged(): void }) {
  const status = setup.connectors.find((item) => item.provider === provider);
  const [list, setList] = useState<Load<ApiExternalMeeting[]>>({ state: 'loading' });
  const [busy, setBusy] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(() => { setList({ state: 'loading' }); meetingsApi.listExternal(provider).then((value) => setList({ state: 'ready', value }), (e) => setList(fail(e))); }, [provider]);
  useEffect(() => { if (status?.connected) load(); }, [status?.connected, load]);
  const name = providerName(provider);

  if (!status?.available) return <div className="meetingSetupNote">
    <h3>{t('meetings.connect.notSetUp', { provider: name })}</h3>
    <p>{t(provider === 'zoom' ? 'meetings.connect.zoomSetup' : 'meetings.connect.teamsSetup')}</p>
    {status?.problem && <p className="muted" dir="ltr">{status.problem}</p>}
  </div>;
  if (!status.connected) return <div className="meetingSetupNote">
    <h3>{t('meetings.connect.title', { provider: name })}</h3>
    <p>{t('meetings.connect.text', { provider: name })}</p>
    <div className="actions"><button type="button" disabled={busy !== null} onClick={() => { setBusy('connect'); setProblem(null); meetingsApi.authorizeConnector(provider).then((link) => { window.location.assign(link.authorize_url); }, (e) => { setBusy(null); setProblem(describeError(e)); }); }}><Link2 aria-hidden="true" />{busy ? t('meetings.connect.opening') : t('meetings.connect.button', { provider: name })}</button></div>
    {problem && <Notice error>{problem}</Notice>}
  </div>;
  return <div className="meetingConnector">
    <div className="meetingConnectorHead">
      <p>{t('meetings.connect.connectedAs', { provider: name })} <bdi>{status.account_label}</bdi></p>
      <div className="actions">
        <button type="button" className="secondary" onClick={load}><RefreshCw aria-hidden="true" />{t('meetings.connect.refresh')}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => { setBusy('disconnect'); meetingsApi.disconnectConnector(provider).then(onSetupChanged, (e) => setProblem(describeError(e))).finally(() => setBusy(null)); }}><Unlink aria-hidden="true" />{t('meetings.connect.disconnect')}</button>
      </div>
    </div>
    {list.state === 'loading' && <Notice>{t('meetings.connect.loading', { provider: name })}</Notice>}
    {list.state === 'error' && <ErrorNotice message={list.message} onRetry={load} />}
    {list.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{t('meetings.viewerNote')}</p>}
    {list.state === 'ready' && !list.value.length && <p className="muted">{t('meetings.connect.none', { provider: name })}</p>}
    {list.state === 'ready' && list.value.length > 0 && <ul className="externalMeetings">{list.value.map((item) => <li key={item.external_id}>
      <div><strong dir="auto">{item.title}</strong><small className="muted">{[item.started_at ? formatDateTime(item.started_at) : '', item.duration_minutes ? t('meetings.duration.minutes', { count: item.duration_minutes }) : ''].filter(Boolean).join(' · ')}</small></div>
      {item.imported_meeting_id
        ? <span className="statusBadge success">{t('meetings.connect.imported')}</span>
        : <button type="button" className="secondary" disabled={busy !== null} aria-label={t('meetings.connect.importOf', { title: item.title })} onClick={() => { setBusy(item.external_id); setProblem(null); meetingsApi.importExternal(provider, { external_id: item.external_id, language, ...(agent ? { agent_skill_id: agent } : {}) }).then(onImported, (e) => setProblem(describeError(e))).finally(() => setBusy(null)); }}>{busy === item.external_id ? t('meetings.connect.importing') : t('meetings.connect.import')}</button>}
    </li>)}</ul>}
    {problem && <Notice error>{problem}</Notice>}
  </div>;
}

// ------------------------------------------------------------------------------------------- detail

function MeetingDetail({ meetingId, setup, agents, onClose, onChanged, onDeleted, onOpenTasks, onOpenApprovals, onOpenSettings }: {
  meetingId: string; setup: ApiMeetingSetup | null; agents: string[]; onClose(): void; onChanged(): void; onDeleted(title: string): void; onOpenTasks?(): void; onOpenApprovals?(): void; onOpenSettings?(): void;
}) {
  const [detail, setDetail] = useState<Load<ApiMeeting> | { state: 'missing' }>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState<string | null>(null), [actionError, setActionError] = useState<string | null>(null), [actionNote, setActionNote] = useState<string | null>(null);
  const [editing, setEditing] = useState(false), [confirmDelete, setConfirmDelete] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const me = api.currentUserId();

  useEffect(() => {
    let live = true;
    meetingsApi.getMeeting(meetingId).then((value) => { if (live) setDetail({ state: 'ready', value }); }, (e) => {
      if (!live) return;
      setDetail(e instanceof ApiError && e.status === 404 ? { state: 'missing' } : fail(e));
    });
    return () => { live = false; };
  }, [meetingId, attempt]);
  useEffect(() => { if (detail.state !== 'loading') heading.current?.focus(); }, [detail.state === 'loading']); // eslint-disable-line react-hooks/exhaustive-deps

  const meeting = detail.state === 'ready' ? detail.value : null;
  // Poll while the meeting is being transcribed or summarised.
  useEffect(() => {
    if (!meeting || !isWorking(meeting.status)) return undefined;
    const timer = window.setTimeout(() => setAttempt((n) => n + 1), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [meeting]);
  const prevStatus = useRef<string | null>(null);
  useEffect(() => { if (meeting && prevStatus.current && prevStatus.current !== meeting.status) onChanged(); prevStatus.current = meeting?.status ?? null; }, [meeting, onChanged]);

  const canEdit = Boolean(meeting && setup && (setup.can_manage_all || (setup.can_create && meeting.created_by === me)));
  async function act(name: string, work: () => Promise<ApiMeeting>, done?: string) {
    setBusy(name); setActionError(null); setActionNote(null);
    try { const value = await work(); setDetail({ state: 'ready', value }); if (done) setActionNote(done); onChanged(); return true; }
    catch (e) { setActionError(isPermissionDenied(e) ? t('meetings.editDenied') : describeError(e)); return false; }
    finally { setBusy(null); }
  }

  const title = meeting?.title ?? t('meetings.detail.title');
  const dir = meeting ? textDirection(meeting.language) : 'auto';
  return <section className="surface meetingDetail" aria-labelledby="meeting-detail-heading" aria-busy={detail.state === 'loading'}>
    <div className="sectionHeader">
      <div><p className="eyebrow">{t('meetings.detail.eyebrow')}</p><h2 id="meeting-detail-heading" ref={heading} tabIndex={-1} dir="auto">{title}</h2></div>
      <button type="button" className="iconButton" aria-label={t('meetings.detail.close')} title={t('meetings.detail.close')} onClick={onClose}><X aria-hidden="true" /></button>
    </div>
    {detail.state === 'loading' && <Notice>{t('meetings.detail.loading')}</Notice>}
    {detail.state === 'missing' && <Notice error>{t('meetings.detail.missing')}</Notice>}
    {detail.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{t('meetings.readDenied')}</p>}
    {detail.state === 'error' && <ErrorNotice message={t('meetings.detail.loadFailed', { message: detail.message })} onRetry={() => setAttempt((n) => n + 1)} />}
    {meeting && <>
      <dl className="taskMeta">
        <div><dt>{t('meetings.detail.status')}</dt><dd><span className={`statusBadge ${statusTone(meeting.status)}`}>{statusLabel(meeting.status)}</span></dd></div>
        <div><dt>{t('meetings.detail.source')}</dt><dd>{sourceLabel(meeting.source)}{meeting.source_file_name && <> · <bdi>{meeting.source_file_name}</bdi></>}</dd></div>
        <div><dt>{t('meetings.detail.when')}</dt><dd><time dateTime={meeting.occurred_at ?? meeting.created_at}>{formatDateTime(meeting.occurred_at ?? meeting.created_at)}</time></dd></div>
        {meeting.duration_seconds ? <div><dt>{t('meetings.detail.duration')}</dt><dd>{durationText(meeting.duration_seconds)}</dd></div> : null}
        <div><dt>{t('meetings.detail.language')}</dt><dd>{t(`meetings.language.${meeting.language}`)}</dd></div>
        <div><dt>{t('meetings.detail.agent')}</dt><dd>{meeting.agent_skill_id ? <span className="tagChip" dir="ltr">{meeting.agent_skill_id}</span> : t('meetings.detail.noAgent')}</dd></div>
        <div><dt>{t('meetings.detail.importedBy')}</dt><dd>{meeting.created_by === me ? t('meetings.you') : <code dir="ltr">{meeting.created_by}</code>}</dd></div>
      </dl>
      {isWorking(meeting.status) && <p className="notice" role="status" aria-live="polite"><RefreshCw aria-hidden="true" className="spin" />{t(`meetings.working.${meeting.status}` as MessageKey)}</p>}
      {meeting.error && <div className={`notice ${meeting.status === 'failed' ? 'errorNotice' : ''}`} role={meeting.status === 'failed' ? 'alert' : 'status'}>
        <CircleDot aria-hidden="true" /><span>{meeting.error}</span>
        {meeting.error_code === 'no_model' && onOpenSettings && <button type="button" className="secondary" onClick={onOpenSettings}>{t('meetings.openModelSettings')}</button>}
      </div>}
      {actionError && <Notice error>{actionError}</Notice>}
      {actionNote && <Notice>{actionNote}</Notice>}
      {canEdit && !editing && <div className="actions">
        {!isWorking(meeting.status) && <button type="button" disabled={busy !== null} onClick={() => void act('process', () => meetingsApi.reprocess(meeting.id), t('meetings.detail.processing'))}><RefreshCw aria-hidden="true" />{meeting.status === 'failed' ? t('meetings.detail.retry') : meeting.summary ? t('meetings.detail.summarizeAgain') : t('meetings.detail.summarize')}</button>}
        <button type="button" className="secondary" onClick={() => { setEditing(true); setConfirmDelete(false); }}><Pencil aria-hidden="true" />{t('meetings.detail.edit')}</button>
        {confirmDelete
          ? <><button type="button" className="danger" disabled={busy !== null} onClick={() => { setBusy('delete'); meetingsApi.deleteMeeting(meeting.id).then(() => onDeleted(meeting.title), (e) => { setBusy(null); setActionError(isPermissionDenied(e) ? t('meetings.editDenied') : describeError(e)); }); }}>{busy === 'delete' ? t('meetings.detail.deleting') : t('meetings.detail.deleteConfirm')}</button><button type="button" className="secondary" onClick={() => setConfirmDelete(false)}>{t('meetings.detail.keep')}</button></>
          : <button type="button" className="secondary" onClick={() => setConfirmDelete(true)}><Trash2 aria-hidden="true" />{t('common.delete')}</button>}
      </div>}
      {confirmDelete && <p className="muted" role="status">{meeting.recording_file_id ? t('meetings.detail.deleteWithRecording') : t('meetings.detail.deleteText')}</p>}
      {editing && <EditForm meeting={meeting} agents={agents} busy={busy !== null} onCancel={() => setEditing(false)} onSave={async (body) => { if (await act('edit', () => meetingsApi.updateMeeting(meeting.id, body), t('meetings.detail.saved'))) setEditing(false); }} />}
      {!canEdit && setup && <p className="muted meetingsNote"><Lock aria-hidden="true" />{t('meetings.editDenied')}</p>}

      {meeting.summary && <section className="meetingSection" aria-labelledby="meeting-summary">
        <div className="meetingSectionHead"><h3 id="meeting-summary">{t('meetings.summary')}</h3>{meeting.provenance && <span className="aiLabel" title={t('meetings.ai.detail', { provider: meeting.provenance.provider })}><Sparkles aria-hidden="true" />{t('meetings.ai.label', { model: meeting.provenance.model })}</span>}<CopyAction text={meeting.summary} label={t('meetings.copySummary')} showLabel /></div>
        <p dir={dir} className="meetingSummary">{meeting.summary}</p>
        {meeting.provenance?.truncated && <p className="muted">{t('meetings.ai.truncated')}</p>}
        <p className="muted">{t('meetings.ai.check')}</p>
      </section>}
      {meeting.summary && <section className="meetingSection" aria-labelledby="meeting-decisions">
        <h3 id="meeting-decisions">{t('meetings.decisions')}</h3>
        {meeting.decisions.length ? <ul dir={dir}>{meeting.decisions.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p className="muted">{t('meetings.noDecisions')}</p>}
      </section>}
      {(meeting.summary || meeting.action_items.length > 0) && <ActionItems meeting={meeting} canEdit={canEdit} canCreateTasks={Boolean(setup?.can_create)} dir={dir} busy={busy} act={act} onOpenTasks={onOpenTasks} />}
      {meeting.follow_up && <FollowUp key={meeting.follow_up.approval_id ?? 'draft'} meeting={meeting} canEdit={canEdit} mailboxes={setup?.email_accounts ?? []} dir={dir} busy={busy} act={act} onOpenTasks={onOpenApprovals} />}
      {meeting.segment_count > 0 && <Transcript meeting={meeting} />}
    </>}
  </section>;
}

function EditForm({ meeting, agents, busy, onSave, onCancel }: { meeting: ApiMeeting; agents: string[]; busy: boolean; onSave(body: { title?: string; agent_skill_id?: string; clear_agent?: boolean; language?: MeetingLanguage }): void; onCancel(): void }) {
  const [title, setTitle] = useState(meeting.title), [agent, setAgent] = useState(meeting.agent_skill_id ?? ''), [language, setLanguage] = useState<MeetingLanguage>(meeting.requested_language);
  const choices = [...new Set([...agents, ...(meeting.agent_skill_id ? [meeting.agent_skill_id] : [])])];
  return <form className="meetingEdit" onSubmit={(e) => { e.preventDefault(); onSave({ title: title.trim() || meeting.title, ...(agent ? { agent_skill_id: agent } : { clear_agent: true }), language }); }}>
    <div className="formGrid">
      <label className="field"><span>{t('meetings.import.titleField')}</span><input value={title} maxLength={200} dir="auto" required onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="field"><span>{t('meetings.import.agent')}</span><select value={agent} onChange={(e) => setAgent(e.target.value)}><option value="">{t('meetings.import.noAgent')}</option>{choices.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>
      <label className="field"><span>{t('meetings.import.language')}</span><select value={language} onChange={(e) => setLanguage(e.target.value as MeetingLanguage)}><option value="auto">{t('meetings.language.auto')}</option><option value="en">{t('meetings.language.en')}</option><option value="ar">{t('meetings.language.ar')}</option></select></label>
    </div>
    <small className="muted">{t('meetings.detail.languageHint')}</small>
    <div className="actions"><button type="submit" disabled={busy}>{t('meetings.detail.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
  </form>;
}

type Act = (name: string, work: () => Promise<ApiMeeting>, done?: string) => Promise<boolean>;

function ActionItems({ meeting, canEdit, canCreateTasks, dir, busy, act, onOpenTasks }: { meeting: ApiMeeting; canEdit: boolean; canCreateTasks: boolean; dir: string; busy: string | null; act: Act; onOpenTasks?(): void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [owner, setOwner] = useState(''), [due, setDue] = useState('');
  return <section className="meetingSection" aria-labelledby="meeting-actions">
    <h3 id="meeting-actions"><ListChecks aria-hidden="true" />{t('meetings.actions.title')}</h3>
    {!meeting.action_items.length && <p className="muted">{t('meetings.actions.none')}</p>}
    <ul className="actionItems">{meeting.action_items.map((item) => <li key={item.id} className={item.done ? 'doneItem' : ''}>
      <label className="actionItemCheck"><input type="checkbox" checked={item.done} disabled={!canEdit || busy !== null} onChange={(e) => void act(`done-${item.id}`, () => meetingsApi.updateActionItem(meeting.id, item.id, { done: e.target.checked }))} /><span className="visuallyHidden">{t('meetings.actions.doneOf', { text: item.text })}</span></label>
      <div className="actionItemBody">
        <p dir={dir}>{item.text}</p>
        <small className="muted">{item.owner ? <bdi>{item.owner}</bdi> : t('meetings.actions.noOwner')} · {item.due_date ? formatDateTime(`${item.due_date}T12:00:00`, { dateStyle: 'medium' }) : t('meetings.actions.noDate')}</small>
        {editing === item.id && <form className="actionItemEdit" onSubmit={(e) => { e.preventDefault(); void act(`edit-${item.id}`, () => meetingsApi.updateActionItem(meeting.id, item.id, { ...(owner.trim() ? { owner: owner.trim() } : { clear_owner: true }), ...(due ? { due_date: due } : { clear_due_date: true }) }), t('meetings.actions.saved')).then((ok) => { if (ok) setEditing(null); }); }}>
          <label className="field"><span>{t('meetings.actions.owner')}</span><input value={owner} maxLength={120} dir="auto" onChange={(e) => setOwner(e.target.value)} /></label>
          <label className="field"><span>{t('meetings.actions.due')}</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
          <div className="actions"><button type="submit" disabled={busy !== null}>{t('meetings.detail.save')}</button><button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button></div>
        </form>}
      </div>
      <div className="rowActions">
        {canEdit && editing !== item.id && <button type="button" className="iconButton" aria-label={t('meetings.actions.editOf', { text: item.text })} title={t('meetings.detail.edit')} onClick={() => { setEditing(item.id); setOwner(item.owner ?? ''); setDue(item.due_date ?? ''); }}><Pencil aria-hidden="true" /></button>}
        {item.task_id
          ? <button type="button" className="secondary" onClick={onOpenTasks}><CheckCircle2 aria-hidden="true" />{t('meetings.actions.taskCreated')}</button>
          : canEdit && canCreateTasks && <button type="button" className="secondary" disabled={busy !== null} aria-label={t('meetings.actions.createTaskOf', { text: item.text })} onClick={() => void act(`task-${item.id}`, () => meetingsApi.createTaskFromItem(meeting.id, item.id), t('meetings.actions.taskDone'))}>{busy === `task-${item.id}` ? t('meetings.actions.creating') : t('meetings.actions.createTask')}</button>}
      </div>
    </li>)}</ul>
    {canEdit && meeting.action_items.length > 0 && <p className="muted">{t('meetings.actions.approvalNote')}</p>}
  </section>;
}

function FollowUp({ meeting, canEdit, mailboxes, dir, busy, act, onOpenTasks }: { meeting: ApiMeeting; canEdit: boolean; mailboxes: ApiEmailAccountChoice[]; dir: string; busy: string | null; act: Act; onOpenTasks?(): void }) {
  const draft = meeting.follow_up!;
  const ids = useId();
  const [to, setTo] = useState(draft.to.join(', ')), [subject, setSubject] = useState(draft.subject), [body, setBody] = useState(draft.body);
  const [copied, setCopied] = useState(false);
  const [mailbox, setMailbox] = useState(mailboxes[0]?.id ?? '');
  const recipients = parseRecipients(to);
  const changed = to !== draft.to.join(', ') || subject !== draft.subject || body !== draft.body;
  const current = { to: recipients.valid, subject, body };
  const mailto = mailtoLink(current);
  const save = () => act('save-email', () => meetingsApi.saveFollowUp(meeting.id, current), t('meetings.email.saved'));
  return <section className="meetingSection" aria-labelledby={`${ids}-h`}>
    <div className="meetingSectionHead"><h3 id={`${ids}-h`}><Mail aria-hidden="true" />{t('meetings.email.title')}</h3>{meeting.provenance && !draft.edited && <span className="aiLabel"><Sparkles aria-hidden="true" />{t('meetings.ai.draft')}</span>}</div>
    <div className="formGrid">
      <label className="field wideField"><span>{t('meetings.email.to')}</span><input value={to} dir="ltr" disabled={!canEdit} placeholder="name@example.com" aria-invalid={recipients.invalid.length > 0} aria-describedby={`${ids}-to`} onChange={(e) => setTo(e.target.value)} /></label>
      <small id={`${ids}-to`} className={recipients.invalid.length ? 'fieldError' : 'muted'}>{recipients.invalid.length ? t('meetings.email.invalid', { value: recipients.invalid[0] }) : t('meetings.email.toHint')}</small>
      <label className="field wideField"><span>{t('meetings.email.subject')}</span><input value={subject} maxLength={200} dir="auto" disabled={!canEdit} onChange={(e) => setSubject(e.target.value)} /></label>
      <label className="field wideField"><span>{t('meetings.email.body')}</span><textarea value={body} maxLength={6000} rows={8} dir={dir === 'auto' ? 'auto' : dir} disabled={!canEdit} onChange={(e) => setBody(e.target.value)} /></label>
    </div>
    <div className="actions">
      {canEdit && <button type="button" disabled={!changed || recipients.invalid.length > 0 || busy !== null} onClick={() => void save()}>{t('meetings.email.save')}</button>}
      <button type="button" className="secondary" onClick={() => { void navigator.clipboard?.writeText(draftText(current)).then(() => setCopied(true), () => setCopied(false)); }}><Copy aria-hidden="true" />{copied ? t('meetings.email.copied') : t('meetings.email.copy')}</button>
      {mailto && <a className="buttonLink secondary" href={mailto}><Mail aria-hidden="true" />{t('meetings.email.openMail')}</a>}
      {canEdit && !draft.approval_id && mailboxes.length > 1 && <label className="field mailboxField"><span>{t('meetings.email.from')}</span><select value={mailbox} onChange={(e) => setMailbox(e.target.value)}>{mailboxes.map((m) => <option key={m.id} value={m.id}>{m.address}</option>)}</select></label>}
      {canEdit && !draft.approval_id && <button type="button" className="secondary" disabled={!mailbox || changed || !recipients.valid.length || !subject.trim() || !body.trim() || busy !== null} onClick={() => void act('send', () => meetingsApi.sendFollowUp(meeting.id, mailbox), t('meetings.email.sent'))}><Send aria-hidden="true" />{t('meetings.email.send')}</button>}
    </div>
    {draft.approval_id
      ? <p className="notice" role="status"><CheckCircle2 aria-hidden="true" /><span>{t('meetings.email.handedOff', { mailbox: draft.sent_from ?? '' })}</span>{onOpenTasks && <button type="button" className="secondary" onClick={onOpenTasks}>{t('meetings.openApprovals')}</button>}</p>
      : canEdit && <p className="muted">{mailboxes.length ? (changed ? t('meetings.email.saveFirst') : t('meetings.email.approvalNote', { mailbox: mailboxes.find((m) => m.id === mailbox)?.address ?? '' })) : t('meetings.email.noTool')}</p>}
  </section>;
}

function Transcript({ meeting }: { meeting: ApiMeeting }) {
  const ids = useId();
  const [data, setData] = useState<Load<ApiTranscriptPage>>({ state: 'loading' });
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [speaker, setSpeaker] = useState(''), [offset, setOffset] = useState(0);
  const load = useCallback(() => {
    meetingsApi.getTranscript(meeting.id, { query, speaker, offset }).then((value) => setData({ state: 'ready', value }), (e) => setData(fail(e)));
  }, [meeting.id, query, speaker, offset]);
  useEffect(() => { load(); }, [load, meeting.segment_count]);
  const dir = textDirection(meeting.language);
  const value = data.state === 'ready' ? data.value : null;
  const pages = value ? Math.max(1, Math.ceil(value.total / TRANSCRIPT_PAGE_SIZE)) : 1;
  const current = Math.floor(offset / TRANSCRIPT_PAGE_SIZE) + 1;
  return <section className="meetingSection" aria-labelledby={`${ids}-h`}>
    <h3 id={`${ids}-h`}>{t('meetings.transcript.title', { count: meeting.segment_count })}</h3>
    <form className="actions meetingsToolbar" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setQuery(draft.trim()); }}>
      <label className="visuallyHidden" htmlFor={`${ids}-q`}>{t('meetings.transcript.search')}</label>
      <input id={`${ids}-q`} type="search" value={draft} dir="auto" placeholder={t('meetings.transcript.search')} maxLength={200} onChange={(e) => { setDraft(e.target.value); if (!e.target.value) { setQuery(''); setOffset(0); } }} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
      {value && value.speakers.length > 0 && <><label className="visuallyHidden" htmlFor={`${ids}-s`}>{t('meetings.transcript.speaker')}</label>
        <select id={`${ids}-s`} value={speaker} onChange={(e) => { setOffset(0); setSpeaker(e.target.value); }}><option value="">{t('meetings.transcript.allSpeakers')}</option>{value.speakers.map((name) => <option key={name} value={name}>{name}</option>)}</select></>}
    </form>
    {data.state === 'loading' && <Notice>{t('meetings.transcript.loading')}</Notice>}
    {data.state === 'error' && <ErrorNotice message={t('meetings.transcript.failed', { message: data.message })} onRetry={load} />}
    {value && !value.items.length && <p className="muted">{t('meetings.transcript.noMatch')}</p>}
    {value && value.items.length > 0 && <ol className="transcriptLines" dir={dir} aria-live="polite">{value.items.map((line) => <li key={line.id}>
      {line.start_ms !== null && <time className="transcriptTime" dir="ltr">{clock(line.start_ms)}</time>}
      <div>{line.speaker && <strong>{line.speaker}</strong>}<p>{highlight(line.text, query)}</p></div>
    </li>)}</ol>}
    {value && pages > 1 && <nav className="meetingsPager" aria-label={t('meetings.transcript.pages')}>
      <button type="button" className="secondary" disabled={current <= 1} onClick={() => setOffset(offset - TRANSCRIPT_PAGE_SIZE)}>{t('meetings.previous')}</button>
      <span className="muted">{t('meetings.pageOf', { page: current, pages })}</span>
      <button type="button" className="secondary" disabled={current >= pages} onClick={() => setOffset(offset + TRANSCRIPT_PAGE_SIZE)}>{t('meetings.next')}</button>
    </nav>}
  </section>;
}

function highlight(text: string, query: string): ReactNode {
  if (!query) return text;
  const lower = text.toLocaleLowerCase(), needle = query.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let from = 0, at = lower.indexOf(needle);
  while (at >= 0 && needle) {
    parts.push(text.slice(from, at), <mark key={at}>{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
    at = lower.indexOf(needle, from);
  }
  parts.push(text.slice(from));
  return parts;
}

function Notice({ children, error }: { children: ReactNode; error?: boolean }) { return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>; }
function ErrorNotice({ message, onRetry }: { message: string; onRetry(): void }) {
  return <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{message}</span><button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>;
}
