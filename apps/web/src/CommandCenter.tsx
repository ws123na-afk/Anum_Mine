// The ANUM Command Center (docs/design/anum360.md#web-command-center): the web home. One place to
// give ANUM a command by voice, text or with documents, follow it step by step, and see what is
// running, what is open, what came out and what needs the person next. Everything shown comes from
// the workspace's API; every API call goes through lib/commandApi.ts.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { AiAnswer } from './AiAnswer';
import {
  ArrowRight, BarChart3, Bot, Check, ChevronLeft, ChevronRight, Circle, CircleAlert, CircleCheck, Ellipsis, FileText, Files, Globe, Keyboard, LayoutGrid,
  Lightbulb, ListTodo, LoaderCircle, Mail, MessageCircle, MessageSquare, Mic, Paperclip, RefreshCw, RotateCcw, Search, Square, SquareCheck, Workflow, X,
} from 'lucide-react';
import type { AgentRun, Approval, Task } from '@anum/contracts';
import type { FileRecord } from './lib/api';
import { budgetMessage, describeError, isBudgetExceeded } from './lib/errors';
import { getLocale, t, type MessageKey } from './lib/i18n';
import { statusLabel, stepLabel } from './lib/labels';
import { baseRecognitionMode, createPushToTalk } from './lib/voice';
import {
  CAPABILITY_TILES, COMMAND_MODES, EXAMPLE_COMMANDS, ORB_CHIPS, SPEECH_LANGUAGES, SPEECH_LANGUAGE_KEY, SPEECH_LOCALES,
  activeTasks, buildSuggestions, currentTasks, initialSpeechLanguage, latestStep, phaseProgress, quickCommands, recentCommands, relativeTime,
  sizeLabel, statusTone, trackerPhases, type CommandMode, type CommandTarget, type Shortcut, type SpeechLanguage, type Suggestion,
} from './lib/commandCenter';
import {
  fileUsage, getCommandRun, inboxSummary, recentFiles, retryCommand, stopCommand, submitCommand, uploadAttachment, type CommandAttachment, type CommandResult,
} from './lib/commandApi';
import { AnumMark } from './Shell';
import './commandCenter.css';

const ICON: Record<string, ReactNode> = {
  mail: <Mail />, chart: <BarChart3 />, search: <Search />, check: <SquareCheck />, file: <FileText />, message: <MessageCircle />, workflow: <Workflow />,
  grid: <LayoutGrid />, report: <FileText />, mic: <Mic />, globe: <Globe />,
};
const MODE_ICON: Record<CommandMode, ReactNode> = { voice: <Mic />, text: <Keyboard />, files: <Files /> };
const storage = (() => { try { return window.localStorage; } catch { return null; } })();

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string };
type Current = { text: string; source: 'text' | 'voice'; submitting: boolean; error: string | null; result: CommandResult | null };
type Upload = { name: string; progress: number; controller: AbortController };

export interface CommandCenterProps {
  tasks: readonly Task[]; approvals: readonly Approval[]; loading: boolean; userName: string | null; autoFocus?: boolean;
  onGo: (view: CommandTarget | 'assistant' | 'agents') => void; onOpenTask: (taskId: string) => void; onOpenFile: (fileId: string) => void;
  onOpenPalette: () => void; onChanged: () => void; onBudget: (message: string) => void;
}

export function CommandCenter({ tasks, approvals, loading, userName, autoFocus, onGo, onOpenTask, onOpenFile, onOpenPalette, onChanged, onBudget }: CommandCenterProps) {
  const [mode, setMode] = useState<CommandMode>('voice');
  const [language, setLanguage] = useState<SpeechLanguage>(() => initialSpeechLanguage(storage?.getItem(SPEECH_LANGUAGE_KEY), getLocale()));
  const [draft, setDraft] = useState('');
  const [heard, setHeard] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<CommandAttachment[]>([]);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [current, setCurrent] = useState<Current | null>(null);
  const [focusRun, setFocusRun] = useState<AgentRun | null>(null);
  const [runs, setRuns] = useState<Record<string, AgentRun | null>>({});
  const [files, setFiles] = useState<Load<FileRecord[]>>({ state: 'loading' });
  const [usage, setUsage] = useState<{ count: number; accept: string; canUpload: boolean } | null>(null);
  const [inbox, setInbox] = useState<{ mailboxes: number; unread: number } | null | undefined>(undefined);
  const [rotation, setRotation] = useState(0);
  const [stopping, setStopping] = useState(false);
  const composer = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const talk = useRef<ReturnType<typeof createPushToTalk> | null>(null);
  const composerId = useId();
  const voiceAvailable = useMemo(() => baseRecognitionMode() !== 'typing', []);

  // ---- overview data: documents, usage and the inbox (tasks and approvals come from the shell)
  const loadOverview = useCallback(() => {
    setFiles({ state: 'loading' });
    recentFiles(5).then((value) => setFiles({ state: 'ready', value })).catch((e: unknown) => setFiles({ state: 'error', message: describeError(e) }));
    fileUsage().then((u) => setUsage({ count: u.file_count, accept: u.accepted_extensions.join(','), canUpload: u.can_upload })).catch(() => setUsage(null));
    void inboxSummary().then(setInbox);
  }, []);
  useEffect(loadOverview, [loadOverview]);
  useEffect(() => { if (autoFocus) composer.current?.focus(); }, [autoFocus]);
  useEffect(() => () => talk.current?.cancel(), []);

  // ---- the command the tracker follows: the one just given, else the newest task
  const recent = useMemo(() => recentCommands(tasks), [tasks]);
  const focusId = current?.result?.task.id ?? (current ? null : recent[0]?.id ?? null);
  const focusTask = (focusId && tasks.find((task) => task.id === focusId)) || current?.result?.task || null;
  const focusKey = focusTask ? `${focusTask.id}:${focusTask.status}:${focusTask.updatedAt}` : '';
  useEffect(() => {
    if (!focusTask) { setFocusRun(null); return; }
    if (current?.result?.run && current.result.task.id === focusTask.id && current.result.task.status === focusTask.status) { setFocusRun(current.result.run); return; }
    let live = true;
    getCommandRun(focusTask.id).then((run) => { if (live) setFocusRun(run); }).catch(() => undefined);
    return () => { live = false; };
    // Refetch when the task changes (the event stream refreshes the task list).
  }, [focusKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- live agent activity: the latest run of each task in flight
  const active = useMemo(() => activeTasks(tasks), [tasks]);
  const activeKey = active.map((task) => `${task.id}:${task.updatedAt}`).join('|');
  useEffect(() => {
    let live = true;
    void Promise.all(active.map(async (task) => [task.id, await getCommandRun(task.id).catch(() => null)] as const)).then((rows) => { if (live) setRuns(Object.fromEntries(rows)); });
    return () => { live = false; };
  }, [activeKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- voice
  const chooseLanguage = (next: SpeechLanguage) => {
    setLanguage(next);
    try { storage?.setItem(SPEECH_LANGUAGE_KEY, next); } catch { /* kept for this page */ }
    if (listening) { talk.current?.cancel(); setListening(false); }
  };
  const startListening = () => {
    setMode('voice');
    if (!voiceAvailable) { setVoiceNote(t('command.voice.unsupported')); composer.current?.focus(); return; }
    if (listening) { talk.current?.stop(); return; }
    setVoiceNote(null);
    let last = '';
    talk.current?.cancel();
    talk.current = createPushToTalk(
      (text) => { last = text; setDraft(text); setHeard(text); },
      () => { setListening(false); setVoiceNote(t('command.voice.failed')); },
      SPEECH_LOCALES[language],
      () => { setListening(false); if (last.trim()) { setVoiceNote(t('command.voice.heard')); window.setTimeout(() => document.getElementById(`${composerId}-send`)?.focus(), 0); } },
      false,
      false,
    );
    try { talk.current.start(); setListening(true); } catch { setListening(false); setVoiceNote(t('command.voice.failed')); }
  };

  // ---- submit, stop, retry
  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || current?.submitting || upload) { composer.current?.focus(); return; }
    if (listening) { talk.current?.stop(); setListening(false); }
    const source: 'text' | 'voice' = heard !== null && heard.trim() === text ? 'voice' : 'text';
    setCurrent({ text, source, submitting: true, error: null, result: null });
    setFocusRun(null);
    try {
      const result = await submitCommand(text, { source, locale: SPEECH_LOCALES[language], attachments });
      setCurrent({ text, source, submitting: false, error: null, result });
      setDraft(''); setHeard(null); setAttachments([]); setVoiceNote(null);
      onChanged();
    } catch (e) {
      if (isBudgetExceeded(e)) onBudget(budgetMessage(e));
      setCurrent({ text, source, submitting: false, error: describeError(e), result: null });
    }
  };
  const stop = async () => {
    if (!focusTask) return;
    setStopping(true);
    try { await stopCommand(focusTask.id); onChanged(); } catch (e) { setCurrent((c) => ({ text: c?.text ?? focusTask.prompt, source: c?.source ?? 'text', submitting: false, result: c?.result ?? null, error: describeError(e) })); } finally { setStopping(false); }
  };
  const retry = async (taskId: string) => {
    const task = tasks.find((x) => x.id === taskId);
    setCurrent({ text: task?.prompt ?? '', source: 'text', submitting: true, error: null, result: null });
    try { const result = await retryCommand(taskId); setCurrent({ text: task?.prompt ?? '', source: 'text', submitting: false, error: null, result }); onChanged(); }
    catch (e) { if (isBudgetExceeded(e)) onBudget(budgetMessage(e)); setCurrent({ text: task?.prompt ?? '', source: 'text', submitting: false, error: describeError(e), result: null }); }
  };
  const onComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(); }
  };

  // ---- documents
  const attach = (file: FileRecord) => setAttachments((list) => list.some((x) => x.id === file.id) ? list.filter((x) => x.id !== file.id) : [...list, { id: file.id, name: file.name }]);
  const onPick = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const controller = new AbortController();
    setUpload({ name: file.name, progress: 0, controller });
    setUploadError(null);
    try {
      const record = await uploadAttachment(file, (loaded, total) => setUpload((u) => u && { ...u, progress: total ? Math.round((loaded / total) * 100) : 0 }), controller.signal);
      setAttachments((list) => [...list, { id: record.id, name: record.name }]);
      loadOverview();
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) setUploadError(t('command.upload.failed', { name: file.name, reason: describeError(e) }));
    } finally { setUpload(null); }
  };

  // ---- shortcuts
  const run = (action: Shortcut) => {
    if (action.kind === 'view') onGo(action.view);
    else if (action.kind === 'palette') onOpenPalette();
    else { setDraft(t(action.text)); setHeard(null); if (action.mode) setMode(action.mode); window.setTimeout(() => { const el = composer.current; if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }, 0); }
  };
  const fill = (text: string) => { setDraft(text); setHeard(null); composer.current?.focus(); };
  const onSuggestion = (s: Suggestion) => {
    if (s.action.kind === 'view') onGo(s.action.view);
    else if (s.action.kind === 'retry') void retry(s.action.taskId);
    else { setMode('text'); composer.current?.focus(); }
  };

  const phases = trackerPhases(focusTask, focusRun, Boolean(current?.submitting));
  const focusStatus = current?.submitting ? 'created' : focusRun?.status ?? focusTask?.status ?? null;
  const working = Boolean(current?.submitting) || focusStatus === 'queued' || focusStatus === 'running';
  const waitingApproval = focusStatus === 'waiting_approval';
  const inFlight = working || waitingApproval;
  const open = currentTasks(tasks);
  const suggestions = buildSuggestions({ approvals, tasks, unreadEmails: inbox ? inbox.unread : null, mailboxes: inbox ? inbox.mailboxes : null, files: usage ? usage.count : null });
  const chips = quickCommands(tasks, rotation);
  const greetingName = userName ? t('command.listeningNamed', { name: userName }) : t('command.listeningUnnamed');
  const orbState = listening ? 'listening' : working ? 'working' : 'idle';
  const caption = listening ? (draft || greetingName) : working ? t('command.orb.working') : mode === 'voice' ? (voiceAvailable ? t('command.orb.tap') : t('command.voice.unsupportedShort')) : mode === 'files' ? t('command.orb.files') : t('command.orb.type');

  return <div className="commandCenter">
    <header className="commandHeader">
      <div><h1 id="view-title">{t('command.title')}</h1><p>{t('command.subtitle')}</p></div>
      <div className="modeTabs" role="group" aria-label={t('command.modes')}>
        {COMMAND_MODES.map((m) => <button key={m} type="button" aria-pressed={mode === m} className={mode === m ? 'on' : ''} onClick={() => { setMode(m); if (m !== 'voice') { talk.current?.cancel(); setListening(false); } if (m === 'text') composer.current?.focus(); }}>{MODE_ICON[m]}<span>{t(`command.mode.${m}` as MessageKey)}</span></button>)}
      </div>
    </header>

    <section className="commandStage" aria-label={t('command.stage')}>
      <ul className="orbChips start" aria-label={t('command.chips')}>{ORB_CHIPS.slice(0, 4).map((chip) => <li key={chip.id}><button type="button" className="orbChip" onClick={() => run(chip.action)}><span className="chipIcon" aria-hidden="true">{ICON[chip.icon]}</span>{t(chip.label)}</button></li>)}</ul>
      <div className="orbColumn">
        <button type="button" className={`commandOrb ${orbState}`} aria-pressed={listening} aria-label={listening ? t('command.voice.stop') : t('command.voice.start')} onClick={startListening} disabled={Boolean(current?.submitting)}>
          <span className="orbHalo" aria-hidden="true" />
          <AnumMark size={64} className="orbMark" />
          <span className="orbWave" aria-hidden="true">{Array.from({ length: 23 }, (_, i) => <i key={i} style={{ ['--i' as string]: i }} />)}</span>
          <span className="orbTitle" aria-hidden="true">{listening ? t('command.orb.listening') : working ? t('command.orb.workingTitle') : t('command.orb.ready')}</span>
          <span className="orbCaption" aria-hidden="true" dir="auto">{caption}</span>
        </button>
        <p className="visuallyHidden" role="status" aria-live="polite">{listening ? t('command.orb.listening') : working ? t('command.orb.workingTitle') : ''}</p>
        <div className="languagePills" role="group" aria-label={t('command.language')}>
          <Globe aria-hidden="true" />
          {SPEECH_LANGUAGES.map((code) => <button key={code} type="button" lang={code} aria-pressed={language === code} className={language === code ? 'on' : ''} onClick={() => chooseLanguage(code)}>{t(`command.language.${code}` as MessageKey)}</button>)}
        </div>
      </div>
      <ul className="orbChips end" aria-label={t('command.chipsMore')}>{ORB_CHIPS.slice(4).map((chip) => <li key={chip.id}><button type="button" className="orbChip" onClick={() => run(chip.action)}><span className="chipIcon" aria-hidden="true">{ICON[chip.icon]}</span>{t(chip.label)}</button></li>)}</ul>
    </section>

    {mode === 'files' && <section className="surface filesPick" aria-labelledby="files-pick-title">
      <div className="panelHead"><h2 id="files-pick-title">{t('command.files.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('files')}>{t('command.viewAll')}</button></div>
      {files.state === 'loading' && <p className="muted" role="status">{t('command.files.loading')}</p>}
      {files.state === 'error' && <PanelError message={files.message} onRetry={loadOverview} />}
      {files.state === 'ready' && files.value.length === 0 && <p className="muted">{t('command.files.empty')}</p>}
      {files.state === 'ready' && files.value.length > 0 && <ul className="pickList">{files.value.map((file) => <li key={file.id}><label className="pickRow">
        <input type="checkbox" checked={attachments.some((x) => x.id === file.id)} onChange={() => attach(file)} />
        <FileText aria-hidden="true" /><span dir="auto">{file.name}</span><small>{sizeLabel(file.size_bytes)}</small>
      </label></li>)}</ul>}
      <p className="muted">{t('command.files.hint')}</p>
    </section>}

    <form className="surface commandComposer" onSubmit={(event) => void submit(event)} aria-label={t('command.composer')}>
      <div className="composerRow">
        <button type="button" className={`composerMic ${listening ? 'on' : ''}`} aria-pressed={listening} aria-label={listening ? t('command.voice.stop') : t('command.voice.start')} title={listening ? t('command.voice.stop') : t('command.voice.start')} onClick={startListening}><Mic aria-hidden="true" /></button>
        <label className="visuallyHidden" htmlFor={composerId}>{t('command.inputLabel')}</label>
        <textarea id={composerId} ref={composer} rows={1} value={draft} dir="auto" placeholder={t('command.placeholder')} maxLength={8000}
          onChange={(event) => { setDraft(event.target.value); }} onKeyDown={onComposerKey} />
        <div className="composerTools">
          {usage?.canUpload !== false && <button type="button" className="toolButton" aria-label={t('command.attach')} title={t('command.attach')} disabled={Boolean(upload)} onClick={() => picker.current?.click()}><Paperclip aria-hidden="true" /></button>}
          <button type="button" className="toolButton" aria-label={t('command.pickDocuments')} title={t('command.pickDocuments')} aria-pressed={mode === 'files'} onClick={() => setMode(mode === 'files' ? 'text' : 'files')}><FileText aria-hidden="true" /></button>
          <input ref={picker} type="file" hidden accept={usage?.accept || undefined} onChange={(event) => void onPick(event)} tabIndex={-1} aria-hidden="true" />
        </div>
        <button type="submit" id={`${composerId}-send`} className="sendButton" aria-label={t('command.send')} title={t('command.send')} disabled={!draft.trim() || Boolean(current?.submitting) || Boolean(upload)}>
          {current?.submitting ? <LoaderCircle className="spin" aria-hidden="true" /> : <ArrowRight className="flipRtl" aria-hidden="true" />}
        </button>
      </div>
      {(attachments.length > 0 || upload) && <ul className="attachList" aria-label={t('command.attachments')}>
        {attachments.map((file) => <li key={file.id}><FileText aria-hidden="true" /><span dir="auto">{file.name}</span><button type="button" aria-label={t('command.removeAttachment', { name: file.name })} onClick={() => setAttachments((list) => list.filter((x) => x.id !== file.id))}><X aria-hidden="true" /></button></li>)}
        {upload && <li className="uploading" role="status"><LoaderCircle className="spin" aria-hidden="true" /><span dir="auto">{t('command.upload.progress', { name: upload.name, percent: upload.progress })}</span><button type="button" aria-label={t('command.upload.cancel')} onClick={() => upload.controller.abort()}><X aria-hidden="true" /></button></li>}
      </ul>}
      {uploadError && <p className="notice errorNotice" role="alert"><CircleAlert aria-hidden="true" />{uploadError}</p>}
      {voiceNote && <p className="composerNote" role="status">{voiceNote}</p>}
      <p className="composerExample">{t('command.exampleLine', { example: t(EXAMPLE_COMMANDS[rotation % EXAMPLE_COMMANDS.length]) })}</p>
      <div className="quickRow">
        <ul className="quickChips" aria-label={t('command.quick')}>{chips.map((chip) => <li key={chip.id}><button type="button" className="quickChip" title={chip.fromHistory ? t('command.quickHistory') : undefined} onClick={() => fill(chip.text)}>
          {chip.fromHistory ? <RotateCcw aria-hidden="true" /> : <Lightbulb aria-hidden="true" />}<span dir="auto">{chip.text}</span>
        </button></li>)}</ul>
        <button type="button" className="toolButton" aria-label={t('command.moreExamples')} title={t('command.moreExamples')} onClick={() => setRotation((r) => r + 1)}><RefreshCw aria-hidden="true" /></button>
      </div>
    </form>

    <Tiles onRun={run} />

    <div className="commandGrid top">
      <section className="surface commandPanel trackerPanel" aria-labelledby="tracker-title">
        <div className="panelHead">
          <h2 id="tracker-title">{working ? t('command.tracker.working') : waitingApproval ? t('command.tracker.waiting') : current?.error ? t('command.tracker.failedTitle') : focusTask ? t('command.tracker.last') : t('command.tracker.ready')}</h2>
          {inFlight && focusTask && focusStatus !== 'created' && <button type="button" className="stopButton" onClick={() => void stop()} disabled={stopping}><Square aria-hidden="true" />{stopping ? t('command.tracker.stopping') : t('command.tracker.stop')}</button>}
        </div>
        {(focusTask || current) && <p className="trackerCommand" dir="auto">{current?.text || focusTask?.prompt || focusTask?.title}</p>}
        {current?.error && <PanelError message={current.error} onRetry={current.text ? () => { setDraft(current.text); composer.current?.focus(); } : undefined} retryLabel={t('command.tracker.editRetry')} />}
        {!focusTask && !current && !loading && <EmptyPanel icon={<Bot />} title={t('command.tracker.emptyTitle')} text={t('command.tracker.emptyText')} action={t('command.tracker.emptyAction')} onAction={() => { setMode('text'); composer.current?.focus(); }} />}
        {(focusTask || current?.submitting) && <ol className="tracker">{phases.map((phase) => <li key={phase.key} className={`phase ${phase.state}`}>
          <span className="phaseIcon" aria-hidden="true">{phase.state === 'done' ? <CircleCheck /> : phase.state === 'failed' ? <CircleAlert /> : phase.state === 'todo' ? <Circle /> : <LoaderCircle className={phase.state === 'active' ? 'spin' : ''} />}</span>
          <span className="phaseText"><strong>{t(`command.phase.${phase.key}` as MessageKey)}</strong><small>{t(`command.phase.${phase.key}.${phase.state === 'waiting' ? 'waiting' : phase.state === 'failed' ? 'failed' : 'detail'}` as MessageKey)}</small><span className="visuallyHidden">{t(`command.phaseState.${phase.state}` as MessageKey)}</span></span>
          {phase.seconds !== null && <span className="phaseTime">{t('command.seconds', { count: phase.seconds })}</span>}
        </li>)}</ol>}
        {focusStatus === 'waiting_approval' && <div className="actions"><button type="button" onClick={() => onGo('approvals')}>{t('command.tracker.review')}</button></div>}
        {focusTask && focusRun?.result && focusStatus === 'completed' && <div className="trackerResult"><AiAnswer text={focusRun.result} /></div>}
        {focusTask && !inFlight && <div className="actions"><button type="button" className="secondary" onClick={() => onOpenTask(focusTask.id)}>{t('command.tracker.open')}</button>{focusStatus === 'failed' && <button type="button" onClick={() => void retry(focusTask.id)}><RotateCcw aria-hidden="true" />{t('command.tracker.retry')}</button>}</div>}
      </section>

      <section className="surface commandPanel" aria-labelledby="live-title">
        <div className="panelHead"><h2 id="live-title">{t('command.live.title')}</h2><span className="activePill">{t('command.live.count', { count: active.length })}</span></div>
        {loading && !tasks.length ? <p className="muted" role="status">{t('common.loading')}</p> : active.length === 0
          ? <EmptyPanel icon={<Bot />} title={t('command.live.emptyTitle')} text={t('command.live.emptyText')} action={t('command.live.emptyAction')} onAction={() => onGo('agents')} />
          : <ul className="activityList">{active.map((task) => {
            const run = runs[task.id] ?? null;
            const step = latestStep(run);
            const progress = phaseProgress(trackerPhases(task, run));
            return <li key={task.id} className="activityRow">
              <span className={`activityIcon ${statusTone(task.status)}`} aria-hidden="true"><Bot /></span>
              <div className="activityBody">
                <div className="activityTop"><strong dir="auto">{task.title}</strong><span className={`statusBadge ${statusTone(task.status)}`}>{statusLabel(task.status)}</span></div>
                <small dir="auto">{step ? `${stepLabel(step.type)} · ${step.summary}` : t('command.live.starting')}</small>
                <div className="progress" role="progressbar" aria-label={t('command.live.progress', { title: task.title })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div>
              </div>
              <button type="button" className="iconButton small" aria-label={t('command.openNamed', { title: task.title })} title={t('command.openNamed', { title: task.title })} onClick={() => onOpenTask(task.id)}><Ellipsis aria-hidden="true" /></button>
            </li>;
          })}</ul>}
      </section>

      <section className="surface commandPanel" aria-labelledby="current-title">
        <div className="panelHead"><h2 id="current-title">{t('command.current.title')} <span className="countPill">{open.total}</span></h2><button type="button" className="linkButton" onClick={() => onGo('tasks')}>{t('command.viewAll')}</button></div>
        {loading && !tasks.length ? <p className="muted" role="status">{t('common.loading')}</p> : open.items.length === 0
          ? <EmptyPanel icon={<ListTodo />} title={t('command.current.emptyTitle')} text={t('command.current.emptyText')} action={t('command.current.emptyAction')} onAction={() => { setMode('text'); composer.current?.focus(); }} />
          : <ul className="rowList light">{open.items.map((task) => <li key={task.id}><button type="button" className="listRow" onClick={() => onOpenTask(task.id)}>
            <span className={`rowIcon ${statusTone(task.status)}`} aria-hidden="true"><SquareCheck /></span>
            <span className="rowText"><strong dir="auto">{task.title}</strong><span className={`statusBadge ${statusTone(task.status)}`}>{statusLabel(task.status)}</span></span>
          </button></li>)}</ul>}
      </section>
    </div>

    <div className="commandGrid bottom">
      <section className="surface commandPanel" aria-labelledby="recent-title">
        <div className="panelHead"><h2 id="recent-title">{t('command.recent.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('tasks')}>{t('command.viewAll')}</button></div>
        {loading && !tasks.length ? <p className="muted" role="status">{t('common.loading')}</p> : recent.length === 0
          ? <EmptyPanel icon={<Mic />} title={t('command.recent.emptyTitle')} text={t('command.recent.emptyText')} />
          : <ul className="rowList light">{recent.map((task) => <li key={task.id} className="splitRow">
            <button type="button" className="listRow" onClick={() => onOpenTask(task.id)}>
              <span className="rowIcon" aria-hidden="true"><MessageSquare /></span>
              <span className="rowText"><strong dir="auto">{task.prompt || task.title}</strong><small>{relativeTime(task.createdAt)}</small></span>
              <span className={`statusBadge ${statusTone(task.status)}`}>{statusLabel(task.status)}</span>
            </button>
            <button type="button" className="iconButton small" aria-label={t('command.recent.reuse', { title: task.title })} title={t('command.recent.reuse', { title: task.title })} onClick={() => fill(task.prompt || task.title)}><RotateCcw aria-hidden="true" /></button>
          </li>)}</ul>}
      </section>

      <section className="surface commandPanel" aria-labelledby="results-title">
        <div className="panelHead"><h2 id="results-title">{t('command.results.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('files')}>{t('command.viewAll')}</button></div>
        {files.state === 'loading' && <p className="muted" role="status">{t('command.files.loading')}</p>}
        {files.state === 'error' && <PanelError message={files.message} onRetry={loadOverview} />}
        {files.state === 'ready' && files.value.length === 0 && <EmptyPanel icon={<FileText />} title={t('command.results.emptyTitle')} text={t('command.results.emptyText')} action={t('command.results.emptyAction')} onAction={() => onGo('files')} />}
        {files.state === 'ready' && files.value.length > 0 && <ul className="rowList light">{files.value.map((file) => <li key={file.id}><button type="button" className="listRow" onClick={() => onOpenFile(file.id)}>
          <span className="rowIcon file" aria-hidden="true"><FileText /></span>
          <span className="rowText"><strong dir="auto">{file.name}</strong><small>{t('command.results.meta', { size: sizeLabel(file.size_bytes), when: relativeTime(file.created_at) })}</small></span>
          <ChevronRight className="flipRtl rowChevron" aria-hidden="true" />
        </button></li>)}</ul>}
      </section>

      <section className="surface commandPanel" aria-labelledby="suggest-title">
        <div className="panelHead"><h2 id="suggest-title">{t('command.suggest.title')}</h2><button type="button" className="iconButton small" aria-label={t('command.suggest.refresh')} title={t('command.suggest.refresh')} onClick={() => { loadOverview(); onChanged(); }}><RefreshCw aria-hidden="true" /></button></div>
        {suggestions.length === 0
          ? <EmptyPanel icon={<Check />} title={t('command.suggest.emptyTitle')} text={t('command.suggest.emptyText')} />
          : <ul className="rowList light">{suggestions.map((s) => <li key={s.id}><button type="button" className="listRow" onClick={() => onSuggestion(s)}>
            <span className={`rowIcon bulb ${s.tone}`} aria-hidden="true"><Lightbulb /></span>
            <span className="rowText"><strong dir="auto">{s.title}</strong><small>{s.detail}</small></span>
          </button></li>)}</ul>}
      </section>
    </div>

    <nav className="actionBar" aria-label={t('command.bar')}>
      <button type="button" className="barItem primary" onClick={startListening}><Mic aria-hidden="true" /><strong>{t('command.bar.talk')}</strong><small>{t('command.bar.talkDetail')}</small></button>
      <button type="button" className="barItem" onClick={() => onGo('assistant')}><MessageSquare aria-hidden="true" /><strong>{t('command.bar.chat')}</strong><small>{t('command.bar.chatDetail')}</small></button>
      <button type="button" className="barItem" onClick={() => onGo('automation')}><Workflow aria-hidden="true" /><strong>{t('command.bar.automate')}</strong><small>{t('command.bar.automateDetail')}</small></button>
      <button type="button" className="barItem" onClick={() => onGo('agents')}><Bot aria-hidden="true" /><strong>{t('command.bar.agents')}</strong><small>{t('command.bar.agentsDetail')}</small></button>
    </nav>
  </div>;
}

/** The capability tiles, a row that scrolls sideways on narrow screens (arrow buttons on wide ones). */
function Tiles({ onRun }: { onRun: (action: Shortcut) => void }) {
  const row = useRef<HTMLUListElement>(null);
  const scroll = (direction: 1 | -1) => {
    const el = row.current;
    if (!el) return;
    const rtl = getComputedStyle(el).direction === 'rtl';
    el.scrollBy({ left: direction * (rtl ? -1 : 1) * el.clientWidth * 0.8, behavior: 'smooth' });
  };
  return <section className="tilesRow" aria-label={t('command.tiles')}>
    <button type="button" className="tilesArrow" aria-label={t('command.tilesBack')} onClick={() => scroll(-1)}><ChevronLeft className="flipRtl" aria-hidden="true" /></button>
    <ul ref={row} className="tiles">{CAPABILITY_TILES.map((tile) => <li key={tile.id}><button type="button" className={`tile tile-${tile.id}`} onClick={() => onRun(tile.action)}>
      <span className="tileIcon" aria-hidden="true">{ICON[tile.icon]}</span><span>{t(tile.label)}</span>
    </button></li>)}</ul>
    <button type="button" className="tilesArrow" aria-label={t('command.tilesMore')} onClick={() => scroll(1)}><ChevronRight className="flipRtl" aria-hidden="true" /></button>
  </section>;
}

function EmptyPanel({ icon, title, text, action, onAction }: { icon: ReactNode; title: string; text: string; action?: string; onAction?: () => void }) {
  return <div className="panelEmpty"><span aria-hidden="true">{icon}</span><h3>{title}</h3><p>{text}</p>{action && onAction && <button type="button" className="secondary smallButton" onClick={onAction}>{action}</button>}</div>;
}

function PanelError({ message, onRetry, retryLabel }: { message: string; onRetry?: () => void; retryLabel?: string }) {
  return <div className="panelError" role="alert"><p><CircleAlert aria-hidden="true" />{message}</p>{onRetry && <button type="button" className="secondary smallButton" onClick={onRetry}><RefreshCw aria-hidden="true" />{retryLabel ?? t('common.retry')}</button>}</div>;
}
