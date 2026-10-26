// The ANUM Command Center (docs/command.md#the-web-command-center): the place to give ANUM an order,
// in the classic layout the owner chose. The glowing orb with its shortcut chips, the "Tell ANUM what
// you need" composer, the capability tiles, then three panels (the command ANUM is on with its step
// timeline and approvals, live agent activity, current tasks) and three more (recent commands, results
// preview, suggestions for you), saved commands and the Talk / Chat / Automate / Agents bar.
// A request is always planned first (the plan can be reordered and trimmed) and nothing runs until
// Start. The overview of the day is Home (HomeView.tsx). Every API call goes through lib/commandApi.ts;
// the pure rules are in lib/commands.ts and lib/commandCenter.ts.
import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import {
  ArrowDown, ArrowRight, ArrowUp, BarChart3, Bot, ChevronLeft, ChevronRight, Circle, CircleAlert, CircleCheck, Download, FileText, Files, Globe, Keyboard,
  LayoutGrid, Lightbulb, ListTodo, LoaderCircle, Mail, MessageCircle, MessageSquare, Mic, Paperclip, Pencil, Play, Plus, RefreshCw, RotateCcw, Save, Search,
  ShieldCheck, Sparkles, Square, SquareCheck, Star, Trash2, Workflow, X,
} from 'lucide-react';
import type { CommandNextStep, CommandSource, CommandSuggestion, CommandSummary, CommandView, PlanStep, QuickCommand, QuickCommandCategory, Task } from '@anum/contracts';
import { AiAnswer } from './AiAnswer';
import * as api from './lib/api';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { getLocale, t, type MessageKey } from './lib/i18n';
import { riskLabel, statusLabel } from './lib/labels';
import { baseRecognitionMode, createPushToTalk } from './lib/voice';
import {
  CAPABILITY_TILES, COMMAND_MODES, EXAMPLE_COMMANDS, ORB_CHIPS, SPEECH_LANGUAGES, SPEECH_LANGUAGE_KEY, SPEECH_LOCALES, buildSuggestions, initialSpeechLanguage,
  liveCommands, mergeHistory, relativeTime, sizeLabel, stepState, trackerTitle, type CommandMode, type CommandTarget, type Shortcut, type SpeechLanguage,
  type StepState, type Suggestion,
} from './lib/commandCenter';
import {
  HISTORY_PAGE, MAX_ATTACHMENTS, MAX_REQUEST_CHARS, QUICK_CATEGORIES, canDelete, canStart, commandLocale, commandTone, isLive, moveStep, quickTitle, removeStep,
  stoppableTasks, unstartedTasks,
} from './lib/commands';
import { ACTIVE_STATUSES } from './lib/tasks';
import * as commandApi from './lib/commandApi';
import { EmptyPanel, PanelDenied, PanelError, PanelLoading } from './Panels';
import { AnumMark } from './Shell';
import './commandCenter.css';
import { BuildingCommandPanel } from './hq-command/BuildingCommandPanel';
import { parseCampusRequest } from './lib/hqCampusCommand';
// The campus proposal card loads only when someone asks for the campus (docs/ANUM_MINE_STARTER_CAMPUS.md#first-run).
const CampusProposalCard = lazy(() => import('./hq-first-run/CampusProposalCard'));
import { parseBuildingRequest, type BuildingAsk } from './lib/hqBuildingCommand';

const ICON: Record<string, ReactNode> = {
  mail: <Mail />, chart: <BarChart3 />, search: <Search />, check: <SquareCheck />, file: <FileText />, message: <MessageCircle />, workflow: <Workflow />,
  grid: <LayoutGrid />, report: <FileText />, mic: <Mic />, globe: <Globe />,
};
const MODE_ICON: Record<CommandMode, ReactNode> = { voice: <Mic />, text: <Keyboard />, files: <Files /> };
const storage = (() => { try { return window.localStorage; } catch { return null; } })();
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied' } | { state: 'error'; message: string };
type PlanState = { state: 'idle' } | { state: 'loading'; text: string } | { state: 'ready'; text: string; steps: PlanStep[]; approvals: number } | { state: 'error'; text: string; message: string; denied: boolean };
type Upload = { name: string; progress: number; controller: AbortController };
type History = { items: CommandSummary[]; state: 'loading' | 'ready' | 'error' | 'denied'; message?: string; more: boolean; loadingMore: boolean };
const POLL_MS = 3000;
const OPEN_TASKS = 6;
const RECENT_FILES = 6;
const loadInto = <T,>(promise: Promise<T>, set: (value: Load<T>) => void) =>
  promise.then((value) => set({ state: 'ready', value })).catch((e: unknown) => set(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) }));

export interface CommandCenterProps {
  userName: string | null;
  /** The caller's role here; viewers cannot give commands (the API needs `task:create`). */
  role: string | null;
  /** A request handed over from Home (an insight's "Analyze"); it is planned, never run, until confirmed. */
  initialText?: string | null;
  /** Bumped by the shell on workspace events, so a running command refreshes at once. */
  live: number;
  onGo: (view: CommandTarget | 'home') => void;
  onOpenTask: (taskId: string) => void;
  onOpenFile: (fileId: string) => void;
  onChanged: () => void;
  onBudget: (message: string) => void;
}

export function CommandCenter({ userName, role, initialText, live, onGo, onOpenTask, onOpenFile, onChanged, onBudget }: CommandCenterProps) {
  const viewer = role === 'viewer';
  const [mode, setMode] = useState<CommandMode>('voice');
  const [draft, setDraft] = useState(initialText ?? '');
  // "Add a building ..." is answered with a proposal card, not a task plan (docs/ANUM_MINE_EXECUTIVE_PA.md#add-a-building).
  const [building, setBuilding] = useState<{ ask: BuildingAsk; mode: 'typed' | 'voice'; n: number } | null>(null);
  // "Set up my campus" is answered with the same kind of card; its button is the only way to create.
  const [campus, setCampus] = useState<{ mode: 'typed' | 'voice'; n: number } | null>(null);
  const [source, setSource] = useState<{ kind: CommandSource; quickId: string | null }>({ kind: initialText ? 'suggestion' : 'typed', quickId: null });
  const [language, setLanguage] = useState<SpeechLanguage>(() => initialSpeechLanguage(storage?.getItem(SPEECH_LANGUAGE_KEY), getLocale()));
  const [listening, setListening] = useState(false);
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<commandApi.CommandAttachment[]>([]);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ accept: string; allowed: boolean; count: number } | null>(null);
  const [plan, setPlan] = useState<PlanState>({ state: 'idle' });
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Load<CommandView> | null>(null);
  const [history, setHistory] = useState<History>({ items: [], state: 'loading', more: false, loadingMore: false });
  const [chips, setChips] = useState<CommandSuggestion[]>([]);
  const [rotation, setRotation] = useState(0);
  const [files, setFiles] = useState<Load<api.FileRecord[]>>({ state: 'loading' });
  const [openTasks, setOpenTasks] = useState<Load<Task[]>>({ state: 'loading' });
  const [pendingApprovals, setPendingApprovals] = useState<number | null>(null);
  const [inbox, setInbox] = useState<{ mailboxes: number; unread: number } | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const talk = useRef<ReturnType<typeof createPushToTalk> | null>(null);
  const heard = useRef<string | null>(null);
  const composerId = useId();
  const voiceAvailable = useMemo(() => baseRecognitionMode() !== 'typing', []);
  const focusComposer = () => window.setTimeout(() => { const el = composer.current; if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }, 0);

  // ---- history (paged with the API's cursor); a quiet reload keeps the older pages already shown
  const loadHistory = useCallback((quiet = false) => {
    if (!quiet) setHistory((h) => ({ ...h, state: 'loading' }));
    commandApi.listCommands(HISTORY_PAGE, null)
      .then((items) => setHistory((h) => quiet && h.state === 'ready'
        ? { ...h, items: mergeHistory(items, h.items) }
        : { items, state: 'ready', more: items.length === HISTORY_PAGE, loadingMore: false }))
      .catch((e: unknown) => { if (!quiet) setHistory({ items: [], state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e), more: false, loadingMore: false }); });
  }, []);
  const moreHistory = () => {
    const last = history.items[history.items.length - 1];
    if (!last) return;
    setHistory((h) => ({ ...h, loadingMore: true }));
    commandApi.listCommands(HISTORY_PAGE, last.id)
      .then((items) => setHistory((h) => ({ ...h, items: [...h.items, ...items.filter((x) => !h.items.some((y) => y.id === x.id))], more: items.length === HISTORY_PAGE, loadingMore: false })))
      .catch((e: unknown) => setHistory((h) => ({ ...h, loadingMore: false, state: 'error', message: describeError(e) })));
  };
  const loadChips = useCallback(() => { commandApi.suggestions(getLocale() === 'ar' ? 'ar' : 'en').then(setChips).catch(() => setChips([])); }, []);
  // ---- the workspace around the command: open tasks, documents, approvals and the inbox
  const loadTasks = useCallback(() => loadInto(commandApi.openTasks(OPEN_TASKS).then((items) => items.filter((task) => ACTIVE_STATUSES.includes(task.status) && !task.archivedAt)), setOpenTasks), []);
  const loadFiles = useCallback(() => {
    setFiles({ state: 'loading' });
    void loadInto(commandApi.recentFiles(RECENT_FILES), setFiles);
    commandApi.fileUsage().then((u) => setUsage({ accept: u.accepted_extensions.join(','), allowed: u.can_upload, count: u.file_count })).catch(() => setUsage(null));
  }, []);
  const loadSignals = useCallback(() => {
    commandApi.pendingApprovals().then(setPendingApprovals).catch(() => setPendingApprovals(null));
    void commandApi.inboxSummary().then(setInbox);
  }, []);
  useEffect(() => { loadHistory(); loadChips(); void loadTasks(); loadFiles(); loadSignals(); }, [loadHistory, loadChips, loadTasks, loadFiles, loadSignals]);
  useEffect(() => { composer.current?.focus({ preventScroll: true }); }, []);
  // A request handed over from Home is planned at once (read-only); nothing runs until Start.
  useEffect(() => { if (initialText?.trim() && !viewer) void preview(undefined, initialText.trim()); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => talk.current?.cancel(), []);
  // Workspace events: refresh what moves (history, open tasks, approvals) without a loading flash.
  useEffect(() => { if (live > 0) { loadHistory(true); void loadTasks(); loadSignals(); } }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- the command the tracker follows: refreshed every few seconds while it runs, and on workspace events
  const refreshDetail = useCallback((id: string, quiet = true) => {
    if (!quiet) setDetail({ state: 'loading' });
    commandApi.getCommand(id).then((value) => {
      setDetail({ state: 'ready', value });
      setHistory((h) => ({ ...h, items: h.items.map((item) => item.id === value.id ? { ...item, status: value.status, progress: value.progress, completedSteps: value.completedSteps, pendingApprovals: value.pendingApprovals } : item) }));
    }).catch((e: unknown) => { if (!quiet) setDetail(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) }); });
  }, []);
  const status = detail?.state === 'ready' ? detail.value.status : null;
  useEffect(() => {
    if (!selected || !status || !isLive(status)) return;
    const timer = window.setTimeout(() => refreshDetail(selected), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [selected, status, detail, refreshDetail]);
  useEffect(() => { if (selected && live > 0) refreshDetail(selected); }, [live]); // eslint-disable-line react-hooks/exhaustive-deps
  const open = (id: string) => { setSelected(id); refreshDetail(id, false); window.setTimeout(() => document.getElementById('tracker-title')?.focus(), 0); };
  // With nothing picked yet, the newest command is followed, so the page opens on its steps and results.
  const newest = history.state === 'ready' ? history.items[0]?.id ?? null : null;
  useEffect(() => { if (!selected && newest) { setSelected(newest); refreshDetail(newest, false); } }, [newest]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- voice: push to talk fills the composer; the person checks it and asks for the plan
  const chooseLanguage = (next: SpeechLanguage) => {
    setLanguage(next);
    try { storage?.setItem(SPEECH_LANGUAGE_KEY, next); } catch { /* kept for this page */ }
    if (listening) { talk.current?.cancel(); setListening(false); }
  };
  const toggleListening = () => {
    setMode('voice');
    if (!voiceAvailable) { setVoiceNote(t('command.voice.unsupported')); composer.current?.focus(); return; }
    if (listening) { talk.current?.stop(); return; }
    setVoiceNote(null);
    let last = '';
    talk.current?.cancel();
    talk.current = createPushToTalk(
      (text) => { last = text; heard.current = text; setDraft(text); },
      () => { setListening(false); setVoiceNote(t('command.voice.failed')); },
      SPEECH_LOCALES[language],
      () => { setListening(false); if (last.trim()) { setSource({ kind: 'voice', quickId: null }); setVoiceNote(t('cmd.voice.heard')); window.setTimeout(() => document.getElementById(`${composerId}-send`)?.focus(), 0); } },
      false,
      false,
    );
    try { talk.current.start(); setListening(true); } catch { setListening(false); setVoiceNote(t('command.voice.failed')); }
  };
  const chooseMode = (next: CommandMode) => {
    setMode(next);
    if (next !== 'voice' && listening) { talk.current?.cancel(); setListening(false); }
    if (next === 'text') composer.current?.focus();
  };

  // ---- plan, edit, start
  const preview = async (event?: FormEvent, text = draft.trim()) => {
    event?.preventDefault();
    if (!text || upload) { composer.current?.focus(); return; }
    if (listening) { talk.current?.stop(); setListening(false); }
    setStartError(null);
    if (parseCampusRequest(text)) { setPlan({ state: 'idle' }); setBuilding(null); setCampus((c) => ({ mode: source.kind === 'voice' ? 'voice' : 'typed', n: (c?.n ?? 0) + 1 })); setDraft(''); return; }
    const ask = parseBuildingRequest(text);
    if (ask) { setPlan({ state: 'idle' }); setCampus(null); setBuilding((b) => ({ ask, mode: source.kind === 'voice' ? 'voice' : 'typed', n: (b?.n ?? 0) + 1 })); setDraft(''); return; }
    setBuilding(null); setCampus(null);
    setPlan({ state: 'loading', text });
    try {
      const result = await commandApi.previewCommand(text, commandLocale(text, getLocale()));
      setPlan({ state: 'ready', text, steps: result.steps, approvals: result.approvalsRequired });
      window.setTimeout(() => document.getElementById('plan-title')?.focus(), 0);
    } catch (e) {
      setPlan({ state: 'error', text, message: describeError(e), denied: isPermissionDenied(e) });
    }
  };
  const use = (text: string, kind: CommandSource, quickId: string | null = null) => {
    setDraft(text); heard.current = null; setSource({ kind, quickId });
    void preview(undefined, text.trim());
  };
  const fill = (text: string) => { setDraft(text); heard.current = null; setSource({ kind: 'typed', quickId: null }); setPlan({ state: 'idle' }); focusComposer(); };
  const editPlan = () => { if (plan.state !== 'idle' && plan.state !== 'loading') setDraft(plan.text); setPlan({ state: 'idle' }); focusComposer(); };
  const start = async () => {
    if (plan.state !== 'ready' || !canStart(plan.steps)) return;
    setStarting(true); setStartError(null);
    const kind = source.kind === 'voice' && heard.current?.trim() !== plan.text ? 'typed' : source.kind;
    try {
      const created = await commandApi.createCommand({
        text: plan.text, steps: plan.steps.map((s) => s.instruction), source: kind, locale: commandLocale(plan.text, getLocale()),
        attachments: attachments.map((a) => a.id), quickCommandId: source.quickId,
      });
      setSelected(created.id);
      setDetail({ state: 'ready', value: created });
      setHistory((h) => ({ ...h, state: h.state === 'loading' ? 'ready' : h.state, items: [created, ...h.items.filter((x) => x.id !== created.id)] }));
      setPlan({ state: 'idle' }); setDraft(''); setAttachments([]); setVoiceNote(null); heard.current = null; setSource({ kind: 'typed', quickId: null });
      try { await commandApi.startSteps(unstartedTasks(created)); } catch (e) {
        if (isBudgetExceeded(e)) onBudget(budgetMessage(e));
        setStartError(t('cmd.start.someFailed', { reason: describeError(e) }));
      }
      refreshDetail(created.id);
      onChanged(); loadChips(); void loadTasks();
      window.setTimeout(() => document.getElementById('tracker-title')?.focus(), 0);
    } catch (e) {
      if (isBudgetExceeded(e)) onBudget(budgetMessage(e));
      setStartError(describeError(e));
    } finally { setStarting(false); }
  };
  const onComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void preview(); }
  };

  // ---- documents: upload with the paperclip, or tick recent ones in Files mode
  const toggleAttachment = (file: api.FileRecord) => {
    setUploadError(null);
    if (attachments.some((x) => x.id === file.id)) setAttachments(attachments.filter((x) => x.id !== file.id));
    else if (attachments.length >= MAX_ATTACHMENTS) setUploadError(t('cmd.attach.tooMany', { count: MAX_ATTACHMENTS }));
    else setAttachments([...attachments, { id: file.id, name: file.name }]);
  };
  const onPick = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (attachments.length >= MAX_ATTACHMENTS) { setUploadError(t('cmd.attach.tooMany', { count: MAX_ATTACHMENTS })); return; }
    const controller = new AbortController();
    setUpload({ name: file.name, progress: 0, controller });
    setUploadError(null);
    try {
      const record = await commandApi.uploadAttachment(file, (loaded, total) => setUpload((u) => u && { ...u, progress: total ? Math.round((loaded / total) * 100) : 0 }), controller.signal);
      setAttachments((list) => [...list, { id: record.id, name: record.name }]);
      loadFiles();
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) setUploadError(t('command.upload.failed', { name: file.name, reason: describeError(e) }));
    } finally { setUpload(null); }
  };

  // ---- shortcuts: chips, tiles and suggestions
  const run = (action: Shortcut) => {
    if (action.kind === 'view') onGo(action.view);
    // The shell's search palette opens on Ctrl/Cmd+K (Shell.tsx usePaletteShortcut).
    else if (action.kind === 'palette') document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
    else if (!viewer) { fill(t(action.text)); if (action.mode) setMode(action.mode); }
  };
  const onSuggestion = (s: Suggestion) => {
    if (s.action.kind === 'view') onGo(s.action.view);
    else if (s.action.kind === 'replan') use(s.action.text, 'suggestion');
    else { setMode('text'); focusComposer(); }
  };

  const planning = plan.state === 'loading';
  const view = detail?.state === 'ready' ? detail.value : null;
  const working = planning || starting || (view !== null && isLive(view.status));
  const orbState = listening ? 'listening' : working ? 'working' : 'idle';
  const caption = viewer ? t('command.orb.viewer') : listening ? (draft || (userName ? t('command.listeningNamed', { name: userName }) : t('command.listeningUnnamed')))
    : working ? t('command.orb.working') : mode === 'voice' ? (voiceAvailable ? t('command.orb.tap') : t('command.voice.unsupportedShort')) : mode === 'files' ? t('command.orb.files') : t('command.orb.type');
  const active = liveCommands(history.items);
  const suggestions = buildSuggestions({
    pendingApprovals, commands: history.state === 'ready' ? history.items : null, unreadEmails: inbox ? inbox.unread : null, mailboxes: inbox ? inbox.mailboxes : null,
    files: usage ? usage.count : null,
  });
  const example = t(EXAMPLE_COMMANDS[rotation % EXAMPLE_COMMANDS.length]);
  const fallbackChips: CommandSuggestion[] = [0, 1, 2, 3].map((i) => { const text = t(EXAMPLE_COMMANDS[(rotation + i + 1) % EXAMPLE_COMMANDS.length]); return { kind: 'starter', text, title: text, quickCommandId: null, useCount: 0, lastUsedAt: null }; });
  const shownChips = (chips.length ? chips : fallbackChips).slice(0, 6);
  const chip = (item: (typeof ORB_CHIPS)[number]) => <li key={item.id}><button type="button" className="orbChip" disabled={viewer && item.action.kind === 'prefill'} onClick={() => run(item.action)}><span className="chipIcon" aria-hidden="true">{ICON[item.icon]}</span>{t(item.label)}</button></li>;

  return <div className="commandCenter">
    <header className="commandHeader">
      <div><h1 id="view-title">{t('command.title')}</h1><p>{t('command.subtitle')}</p></div>
      {!viewer && <div className="modeTabs" role="group" aria-label={t('command.modes')}>
        {COMMAND_MODES.map((m) => <button key={m} type="button" aria-pressed={mode === m} className={mode === m ? 'on' : ''} onClick={() => chooseMode(m)}>{MODE_ICON[m]}<span>{t(`command.mode.${m}` as MessageKey)}</span></button>)}
      </div>}
    </header>

    <section className="commandStage" aria-label={t('command.stage')}>
      <ul className="orbChips start" aria-label={t('command.chips')}>{ORB_CHIPS.slice(0, 4).map(chip)}</ul>
      <div className="orbColumn">
        <button type="button" className={`commandOrb ${orbState}`} aria-pressed={listening} aria-label={listening ? t('command.voice.stop') : t('command.voice.start')} onClick={toggleListening} disabled={viewer || planning || starting}>
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
      <ul className="orbChips end" aria-label={t('command.chipsMore')}>{ORB_CHIPS.slice(4).map(chip)}</ul>
    </section>

    {!viewer && mode === 'files' && <section className="surface filesPick" aria-labelledby="files-pick-title">
      <div className="panelHead"><h2 id="files-pick-title">{t('command.files.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('files')}>{t('command.viewAll')}</button></div>
      {files.state === 'loading' && <PanelLoading />}
      {files.state === 'error' && <PanelError message={t('command.files.failed', { reason: files.message })} onRetry={loadFiles} />}
      {files.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
      {files.state === 'ready' && files.value.length === 0 && <p className="muted">{t('command.files.empty')}</p>}
      {files.state === 'ready' && files.value.length > 0 && <ul className="pickList">{files.value.map((file) => <li key={file.id}><label className="pickRow">
        <input type="checkbox" checked={attachments.some((x) => x.id === file.id)} onChange={() => toggleAttachment(file)} />
        <FileText aria-hidden="true" /><span dir="auto">{file.name}</span><small>{sizeLabel(file.size_bytes)}</small>
      </label></li>)}</ul>}
      <p className="muted small">{t('command.files.hint')}</p>
    </section>}

    {viewer
      ? <section className="surface commandPanel"><PanelDenied title={t('cmd.viewer.title')} text={t('cmd.viewer.text')} /></section>
      : <form className="surface commandComposer" onSubmit={(event) => void preview(event)} aria-label={t('cmd.composer.title')}>
        <div className="composerRow">
          <button type="button" className={`composerMic ${listening ? 'on' : ''}`} aria-pressed={listening} aria-label={listening ? t('command.voice.stop') : t('command.voice.start')} title={listening ? t('command.voice.stop') : t('command.voice.start')} onClick={toggleListening}><Mic aria-hidden="true" /></button>
          <label className="visuallyHidden" htmlFor={composerId}>{t('command.inputLabel')}</label>
          <textarea id={composerId} ref={composer} rows={1} value={draft} dir="auto" placeholder={t('command.placeholder')} maxLength={MAX_REQUEST_CHARS}
            onChange={(event) => { setDraft(event.target.value); if (source.kind !== 'typed' && source.kind !== 'voice') setSource({ kind: 'typed', quickId: null }); }} onKeyDown={onComposerKey} />
          <div className="composerTools">
            {usage?.allowed !== false && <button type="button" className="toolButton" aria-label={t('command.attach')} title={t('command.attach')} disabled={Boolean(upload) || attachments.length >= MAX_ATTACHMENTS} onClick={() => picker.current?.click()}><Paperclip aria-hidden="true" /></button>}
            <button type="button" className="toolButton" aria-label={t('command.pickDocuments')} title={t('command.pickDocuments')} aria-pressed={mode === 'files'} onClick={() => chooseMode(mode === 'files' ? 'text' : 'files')}><FileText aria-hidden="true" /></button>
            <input ref={picker} type="file" hidden accept={usage?.accept || undefined} onChange={(event) => void onPick(event)} tabIndex={-1} aria-hidden="true" />
          </div>
          <button type="submit" id={`${composerId}-send`} className="sendButton" aria-label={t('cmd.preview')} title={t('cmd.preview')} disabled={!draft.trim() || planning || Boolean(upload)}>
            {planning ? <LoaderCircle className="spin" aria-hidden="true" /> : <ArrowRight className="flipRtl" aria-hidden="true" />}
          </button>
        </div>
        {(attachments.length > 0 || upload) && <ul className="attachList" aria-label={t('command.attachments')}>
          {attachments.map((file) => <li key={file.id}><FileText aria-hidden="true" /><span dir="auto">{file.name}</span><button type="button" aria-label={t('command.removeAttachment', { name: file.name })} onClick={() => setAttachments((list) => list.filter((x) => x.id !== file.id))}><X aria-hidden="true" /></button></li>)}
          {upload && <li className="uploading" role="status"><LoaderCircle className="spin" aria-hidden="true" /><span dir="auto">{t('command.upload.progress', { name: upload.name, percent: upload.progress })}</span><button type="button" aria-label={t('command.upload.cancel')} onClick={() => upload.controller.abort()}><X aria-hidden="true" /></button></li>}
        </ul>}
        {uploadError && <p className="notice errorNotice" role="alert"><CircleAlert aria-hidden="true" />{uploadError}</p>}
        {voiceNote && <p className="composerNote" role="status">{voiceNote}</p>}
        <p className="composerExample" dir="auto">{t('command.exampleLine', { example })}</p>
        <div className="quickRow">
          <ul className="quickChips" aria-label={t('cmd.chips')}>{shownChips.map((c, i) => <li key={`${c.kind}-${i}`}><button type="button" className="quickChip" title={c.text} onClick={() => use(c.text, c.kind === 'quick' ? 'quick_command' : 'suggestion', c.quickCommandId)}>
            {c.kind === 'quick' ? <Star aria-hidden="true" /> : c.kind === 'recent' ? <RotateCcw aria-hidden="true" /> : <Lightbulb aria-hidden="true" />}<span dir="auto">{c.title}</span>
          </button></li>)}</ul>
          <button type="button" className="toolButton" aria-label={t('command.moreExamples')} title={t('command.moreExamples')} onClick={() => { setRotation((r) => r + 1); loadChips(); }}><RefreshCw aria-hidden="true" /></button>
        </div>
      </form>}

    {campus && <Suspense fallback={<p className="muted" role="status">{t('hqFr.loading')}</p>}><CampusProposalCard key={campus.n} inputMode={campus.mode} onShow={() => { window.location.hash = '#office'; }} onCancel={() => undefined} /></Suspense>}
    {building && <BuildingCommandPanel key={building.n} initial={building.ask} mode={building.mode} onClose={() => setBuilding(null)} onShow={() => { window.location.hash = '#office'; }} />}
    {plan.state !== 'idle' && <PlanPanel plan={plan} starting={starting} error={startError} attachments={attachments.length}
      onChange={(steps) => setPlan((p) => p.state === 'ready' ? { ...p, steps, approvals: steps.filter((s) => s.approval === 'required').length } : p)}
      onEdit={editPlan} onStart={() => void start()} onRetry={() => void preview(undefined, plan.text)} onCancel={() => { setPlan({ state: 'idle' }); setStartError(null); }} />}
    {plan.state === 'idle' && startError && <PanelError message={startError} />}

    <Tiles onRun={run} viewer={viewer} />

    <div className="commandGrid">
      <TrackerPanel detail={detail} historyLoading={history.state === 'loading'} viewer={viewer} onRetry={() => selected && refreshDetail(selected, false)} onOpenTask={onOpenTask} onOpenFile={onOpenFile} onGo={onGo}
        onRefresh={() => selected && refreshDetail(selected)} onBudget={onBudget} onFocusComposer={() => { setMode('text'); focusComposer(); }}
        onDeleted={(id) => { setSelected(null); setDetail(null); setHistory((h) => ({ ...h, items: h.items.filter((x) => x.id !== id) })); }}
        onUse={(text) => use(text, 'next_step')} onChanged={() => { onChanged(); void loadTasks(); loadSignals(); }} onSaved={loadChips} />

      <section className="surface commandPanel" aria-labelledby="live-title">
        <div className="panelHead"><h2 id="live-title">{t('command.live.title')}</h2><span className="activePill">{t('command.live.count', { count: active.length })}</span></div>
        {history.state === 'loading' && history.items.length === 0 && <PanelLoading />}
        {history.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
        {history.state === 'error' && <PanelError message={t('cmd.history.failed', { reason: history.message ?? '' })} onRetry={() => loadHistory()} />}
        {history.state === 'ready' && active.length === 0 && <EmptyPanel icon={<Bot />} title={t('command.live.emptyTitle')} text={t('command.live.emptyText')} action={t('command.live.emptyAction')} onAction={() => onGo('agents')} />}
        {active.length > 0 && <ul className="rowList">{active.map((command) => <li key={command.id}><button type="button" className={`listRow activityRow ${selected === command.id ? 'selected' : ''}`} onClick={() => open(command.id)}>
          <span className={`rowIcon ${commandTone(command.status)}`} aria-hidden="true"><Bot /></span>
          <span className="rowText"><strong dir="auto">{command.text}</strong>
            <small dir="auto">{t('command.live.meta', { agents: command.agents.map((a) => a.name).join(', ') || t('cmd.status.ready'), done: command.completedSteps, steps: command.stepCount })}</small>
            <span className="progress mini" role="progressbar" aria-label={t('command.live.progress', { title: command.text })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(command.progress * 100)}><span style={{ width: `${Math.round(command.progress * 100)}%` }} /></span></span>
          <span className={`statusBadge ${commandTone(command.status)}`}>{t(`cmd.status.${command.status}` as MessageKey)}</span>
        </button></li>)}</ul>}
      </section>

      <section className="surface commandPanel" aria-labelledby="current-title">
        <div className="panelHead"><h2 id="current-title">{t('command.current.title')} {openTasks.state === 'ready' && <span className="countPill">{openTasks.value.length}</span>}</h2><button type="button" className="linkButton" onClick={() => onGo('tasks')}>{t('command.viewAll')}</button></div>
        {openTasks.state === 'loading' && <PanelLoading />}
        {openTasks.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
        {openTasks.state === 'error' && <PanelError message={t('command.current.failed', { reason: openTasks.message })} onRetry={() => void loadTasks()} />}
        {openTasks.state === 'ready' && openTasks.value.length === 0 && <EmptyPanel icon={<ListTodo />} title={t('command.current.emptyTitle')} text={t('command.current.emptyText')} action={viewer ? undefined : t('command.current.emptyAction')} onAction={() => { setMode('text'); focusComposer(); }} />}
        {openTasks.state === 'ready' && openTasks.value.length > 0 && <ul className="rowList">{openTasks.value.map((task) => <li key={task.id}><button type="button" className="listRow" onClick={() => onOpenTask(task.id)}>
          <span className={`rowIcon ${commandTone(task.status)}`} aria-hidden="true"><SquareCheck /></span>
          <span className="rowText"><strong dir="auto">{task.title}</strong><small>{relativeTime(task.updatedAt)}</small></span>
          <span className={`statusBadge ${commandTone(task.status)}`}>{statusLabel(task.status)}</span>
        </button></li>)}</ul>}
      </section>
    </div>

    <div className="commandGrid">
      <HistoryPanel history={history} selected={selected} viewer={viewer} onOpen={open} onMore={moreHistory} onRetry={() => loadHistory()} onReuse={fill} onFocusComposer={() => { setMode('text'); focusComposer(); }} />

      <section className="surface commandPanel" aria-labelledby="results-title">
        <div className="panelHead"><h2 id="results-title">{t('command.results.title')}</h2><button type="button" className="linkButton" onClick={() => onGo('files')}>{t('command.viewAll')}</button></div>
        {files.state === 'loading' && <PanelLoading />}
        {files.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
        {files.state === 'error' && <PanelError message={t('command.results.failed', { reason: files.message })} onRetry={loadFiles} />}
        {files.state === 'ready' && files.value.length === 0 && <EmptyPanel icon={<FileText />} title={t('command.results.emptyTitle')} text={t('command.results.emptyText')} action={t('command.results.emptyAction')} onAction={() => onGo('files')} />}
        {files.state === 'ready' && files.value.length > 0 && <ul className="rowList">{files.value.map((file) => <li key={file.id}><button type="button" className="listRow" onClick={() => onOpenFile(file.id)}>
          <span className="rowIcon file" aria-hidden="true"><FileText /></span>
          <span className="rowText"><strong dir="auto">{file.name}</strong><small>{t('command.results.meta', { size: sizeLabel(file.size_bytes), when: relativeTime(file.created_at) })}</small></span>
          <ChevronRight className="flipRtl rowChevron" aria-hidden="true" />
        </button></li>)}</ul>}
      </section>

      <section className="surface commandPanel" aria-labelledby="suggest-title">
        <div className="panelHead"><h2 id="suggest-title">{t('command.suggest.title')}</h2><button type="button" className="iconButton small" aria-label={t('command.suggest.refresh')} title={t('command.suggest.refresh')} onClick={() => { loadSignals(); loadFiles(); loadHistory(true); }}><RefreshCw aria-hidden="true" /></button></div>
        {history.state === 'loading' && suggestions.length === 0
          ? <PanelLoading />
          : suggestions.length === 0
            ? <EmptyPanel icon={<CircleCheck />} title={t('command.suggest.emptyTitle')} text={t('command.suggest.emptyText')} />
            : <ul className="rowList">{suggestions.map((s) => <li key={s.id}><button type="button" className="listRow" disabled={viewer && s.action.kind !== 'view'} onClick={() => onSuggestion(s)}>
              <span className={`rowIcon bulb ${s.tone}`} aria-hidden="true"><Lightbulb /></span>
              <span className="rowText"><strong dir="auto">{s.title}</strong><small>{s.detail}</small></span>
              <ChevronRight className="flipRtl rowChevron" aria-hidden="true" />
            </button></li>)}</ul>}
      </section>
    </div>

    <QuickCommandsPanel viewer={viewer} onUse={(q) => use(q.text, 'quick_command', q.id)} onChanged={loadChips} />

    <nav className="actionBar" aria-label={t('command.bar')}>
      <button type="button" className="barItem primary" onClick={() => onGo('voice')}><Mic aria-hidden="true" /><strong>{t('command.bar.talk')}</strong><small>{t('cmd.openVoice')}</small></button>
      <button type="button" className="barItem" onClick={() => onGo('assistant')}><MessageSquare aria-hidden="true" /><strong>{t('command.bar.chat')}</strong><small>{t('command.bar.chatDetail')}</small></button>
      <button type="button" className="barItem" onClick={() => onGo('automation')}><Workflow aria-hidden="true" /><strong>{t('command.bar.automate')}</strong><small>{t('command.bar.automateDetail')}</small></button>
      <button type="button" className="barItem" onClick={() => onGo('agents')}><Bot aria-hidden="true" /><strong>{t('command.bar.agents')}</strong><small>{t('command.bar.agentsDetail')}</small></button>
    </nav>
  </div>;
}

/** The capability tiles: one row on wide screens, a sideways-scrolling row with arrow buttons on narrower ones. */
function Tiles({ onRun, viewer }: { onRun: (action: Shortcut) => void; viewer: boolean }) {
  const row = useRef<HTMLUListElement>(null);
  const scroll = (direction: 1 | -1) => {
    const el = row.current;
    if (!el) return;
    const rtl = getComputedStyle(el).direction === 'rtl';
    el.scrollBy({ left: direction * (rtl ? -1 : 1) * el.clientWidth * 0.8, behavior: 'smooth' });
  };
  return <section className="tilesRow" aria-label={t('command.tiles')}>
    <button type="button" className="tilesArrow" aria-label={t('command.tilesBack')} onClick={() => scroll(-1)}><ChevronLeft className="flipRtl" aria-hidden="true" /></button>
    <ul ref={row} className="tiles">{CAPABILITY_TILES.map((tile) => <li key={tile.id}><button type="button" className={`tile tile-${tile.id}`} disabled={viewer && tile.action.kind === 'prefill'} onClick={() => onRun(tile.action)}>
      <span className="tileIcon" aria-hidden="true">{ICON[tile.icon]}</span><span>{t(tile.label)}</span>
    </button></li>)}</ul>
    <button type="button" className="tilesArrow" aria-label={t('command.tilesMore')} onClick={() => scroll(1)}><ChevronRight className="flipRtl" aria-hidden="true" /></button>
  </section>;
}

// ---------------------------------------------------------------- the plan

function PlanPanel({ plan, starting, error, attachments, onChange, onEdit, onStart, onRetry, onCancel }: {
  plan: Exclude<PlanState, { state: 'idle' }>; starting: boolean; error: string | null; attachments: number;
  onChange: (steps: PlanStep[]) => void; onEdit: () => void; onStart: () => void; onRetry: () => void; onCancel: () => void;
}) {
  return <section className="surface commandPanel planPanel" aria-labelledby="plan-title" aria-busy={plan.state === 'loading'}>
    <div className="panelHead"><h2 id="plan-title" tabIndex={-1}>{t('cmd.plan.title')}</h2>
      <button type="button" className="iconButton small" aria-label={t('cmd.plan.cancel')} title={t('cmd.plan.cancel')} onClick={onCancel}><X aria-hidden="true" /></button></div>
    <p className="planRequest" dir="auto">{plan.text}</p>
    {plan.state === 'loading' && <PanelLoading label={t('cmd.plan.loading')} />}
    {plan.state === 'error' && (plan.denied
      ? <PanelDenied title={t('cmd.viewer.title')} text={t('cmd.viewer.text')} />
      : <PanelError message={t('cmd.plan.failed', { reason: plan.message })} onRetry={onRetry} />)}
    {plan.state === 'ready' && <>
      <p className="planSummary">{t('cmd.plan.summary', { steps: plan.steps.length, approvals: plan.approvals })}{attachments > 0 && <> · {t('cmd.plan.attachments', { count: attachments })}</>}</p>
      {plan.steps.length === 0
        ? <EmptyPanel icon={<Bot />} title={t('cmd.plan.emptyTitle')} text={t('cmd.plan.emptyText')} action={t('cmd.plan.edit')} onAction={onEdit} />
        : <ol className="planSteps">{plan.steps.map((step, index) => <li key={`${step.position}-${step.instruction}`} className={`planStep ${step.approval}`}>
          <span className="planNumber" aria-hidden="true">{index + 1}</span>
          <div className="planBody">
            <strong dir="auto">{step.title}</strong>
            <div className="planTags">
              <span className="planAgent"><Bot aria-hidden="true" />{step.agent.name}</span>
              {step.tool && <code dir="ltr">{step.tool.name}</code>}
              <span className={`riskBadge risk-${step.riskLevel}`}>{riskLabel(step.riskLevel)}</span>
              <span className={`statusBadge ${step.approval === 'blocked' ? 'dangerTone' : step.approval === 'required' ? 'warning' : 'success'}`}>{t(`cmd.approval.${step.approval}` as MessageKey)}</span>
            </div>
            {step.approval !== 'none' && <small dir="auto">{step.approvalReason}</small>}
            {step.note && <small className="planNote" dir="auto">{step.note}</small>}
          </div>
          <div className="planTools">
            <button type="button" className="iconButton small" disabled={index === 0} aria-label={t('cmd.plan.up', { position: index + 1 })} title={t('cmd.plan.upShort')} onClick={() => onChange(moveStep(plan.steps, index, -1))}><ArrowUp aria-hidden="true" /></button>
            <button type="button" className="iconButton small" disabled={index === plan.steps.length - 1} aria-label={t('cmd.plan.down', { position: index + 1 })} title={t('cmd.plan.downShort')} onClick={() => onChange(moveStep(plan.steps, index, 1))}><ArrowDown aria-hidden="true" /></button>
            <button type="button" className="iconButton small" disabled={plan.steps.length === 1} aria-label={t('cmd.plan.remove', { position: index + 1 })} title={t('cmd.plan.removeShort')} onClick={() => onChange(removeStep(plan.steps, index))}><Trash2 aria-hidden="true" /></button>
          </div>
        </li>)}</ol>}
      {plan.steps.some((s) => s.approval === 'blocked') && <p className="approvalWarning" role="alert">{t('cmd.plan.blocked')}</p>}
      {error && <PanelError message={error} />}
      <div className="planActions">
        <button type="button" className="secondary" onClick={onEdit}><Pencil aria-hidden="true" />{t('cmd.plan.edit')}</button>
        <button type="button" className="primaryButton" disabled={starting || !canStart(plan.steps)} onClick={onStart}>{starting ? <LoaderCircle className="spin" aria-hidden="true" /> : <Play aria-hidden="true" />}{starting ? t('cmd.plan.starting') : t('cmd.plan.start')}</button>
      </div>
    </>}
  </section>;
}

// ---------------------------------------------------------------- the command ANUM is on: step timeline, approvals, results

const STEP_ICON: Record<StepState, ReactNode> = {
  done: <CircleCheck />, active: <LoaderCircle className="spin" />, waiting: <LoaderCircle />, failed: <CircleAlert />, todo: <Circle />,
};

function TrackerPanel({ detail, historyLoading, viewer, onRetry, onOpenTask, onOpenFile, onGo, onRefresh, onBudget, onFocusComposer, onDeleted, onUse, onChanged, onSaved }: {
  detail: Load<CommandView> | null; historyLoading: boolean; viewer: boolean; onRetry: () => void; onOpenTask: (id: string) => void; onOpenFile: (id: string) => void;
  onGo: CommandCenterProps['onGo']; onRefresh: () => void; onBudget: (message: string) => void; onFocusComposer: () => void; onDeleted: (id: string) => void;
  onUse: (text: string) => void; onChanged: () => void; onSaved: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [next, setNext] = useState<CommandNextStep[] | null>(null);
  const [saved, setSaved] = useState(false);
  const view = detail?.state === 'ready' ? detail.value : null;
  const settled = view ? !isLive(view.status) : false;
  useEffect(() => { setProblem(null); setSaved(false); }, [view?.id]);
  useEffect(() => {
    if (!view || !settled) { setNext(null); return; }
    let live = true;
    commandApi.nextSteps(view.id).then((items) => { if (live) setNext(items); }).catch(() => { if (live) setNext([]); });
    return () => { live = false; };
  }, [view?.id, view?.status, settled]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setProblem(null);
    try { await fn(); onRefresh(); onChanged(); } catch (e) { if (isBudgetExceeded(e)) onBudget(budgetMessage(e)); setProblem(describeError(e)); } finally { setBusy(null); }
  };
  const approveAll = (command: CommandView) => act('approve', async () => {
    const refusals: string[] = [];
    // One request per approval, each with the payload hash shown (docs/command.md#approve-all): a refusal does not stop the others.
    for (const approval of command.pending) {
      try { await api.approveTask(approval); } catch (e) { if (isBudgetExceeded(e)) onBudget(budgetMessage(e)); refusals.push(`${approval.action}: ${describeError(e)}`); }
    }
    if (refusals.length) throw new Error(t('cmd.detail.approveRefused', { reasons: refusals.join(' · ') }));
  });
  const saveQuick = (command: CommandView) => act('save', async () => { await commandApi.createQuick({ title: quickTitle(command.text), text: command.text, category: 'custom' }); setSaved(true); onSaved(); });
  const onNext = (item: CommandNextStep, command: CommandView) => {
    if (item.kind === 'review_approvals') onGo('approvals');
    else if (item.kind === 'save_quick_command') void saveQuick(command);
    else if (item.text) onUse(item.text);
  };
  const stoppable = view ? stoppableTasks(view) : [];

  return <section className="surface commandPanel trackerPanel" aria-labelledby="tracker-title" aria-busy={detail?.state === 'loading'}>
    <div className="panelHead">
      <h2 id="tracker-title" tabIndex={-1}>{t(trackerTitle(view?.status ?? null))}</h2>
      {view && stoppable.length > 0 && !viewer
        ? <button type="button" className="stopButton" disabled={busy !== null} onClick={() => void act('stop', () => commandApi.stopTasks(stoppable))}><Square aria-hidden="true" />{busy === 'stop' ? t('command.tracker.stopping') : t('cmd.detail.stop')}</button>
        : view && <span className={`statusBadge ${commandTone(view.status)}`}>{t(`cmd.status.${view.status}` as MessageKey)}</span>}
    </div>
    {(detail?.state === 'loading' || (!detail && historyLoading)) && <PanelLoading />}
    {!detail && !historyLoading && <EmptyPanel icon={<Bot />} title={t('command.tracker.emptyTitle')} text={t('command.tracker.emptyText')} action={viewer ? undefined : t('command.tracker.emptyAction')} onAction={onFocusComposer} />}
    {detail?.state === 'error' && <PanelError message={t('cmd.detail.failed', { reason: detail.message })} onRetry={onRetry} />}
    {detail?.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
    {view && <>
      <p className="trackerCommand" dir="auto">{view.text}</p>
      <div className="progress" role="progressbar" aria-label={t('cmd.detail.progress')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(view.progress * 100)}><span style={{ width: `${Math.round(view.progress * 100)}%` }} /></div>
      <p className="planSummary" role="status">{t('cmd.detail.summary', { done: view.completedSteps, steps: view.stepCount })}{view.durationSeconds !== null && <> · {t('command.seconds', { count: Math.round(view.durationSeconds) })}</>}</p>
      <ol className="tracker">{view.steps.map((step) => {
        const state = stepState(step.status);
        return <li key={step.taskId} className={`phase ${state}`}>
          <span className="phaseIcon" aria-hidden="true">{STEP_ICON[state]}</span>
          <div className="phaseText">
            <strong dir="auto">{step.title}</strong>
            <small><Bot aria-hidden="true" />{step.agent.name}{step.tool && <> · <code dir="ltr">{step.tool}</code></>} · {step.status === 'missing' ? t('cmd.step.missing') : t(`command.stepState.${state}` as MessageKey)}</small>
            {state === 'active' && <span className="progress mini" role="progressbar" aria-label={t('cmd.step.progress', { position: step.position })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(step.progress * 100)}><span style={{ width: `${Math.round(step.progress * 100)}%` }} /></span>}
            {step.result && <div className="trackerResult"><AiAnswer text={step.result} /></div>}
            {step.status !== 'missing' && <button type="button" className="linkButton" onClick={() => onOpenTask(step.taskId)}>{t('cmd.step.open')}</button>}
          </div>
        </li>;
      })}</ol>
      {view.pending.length > 0 && <div className="approvalStrip" role="group" aria-label={t('cmd.detail.approvals')}>
        <p><ShieldCheck aria-hidden="true" />{t('cmd.detail.waiting', { count: view.pending.length })}</p>
        <div><button type="button" className="primaryButton smallButton" onClick={() => onGo('approvals')}>{t('command.tracker.review')}</button>
          {!viewer && <button type="button" className="secondary smallButton" disabled={busy !== null} onClick={() => void approveAll(view)}>{busy === 'approve' ? <LoaderCircle className="spin" aria-hidden="true" /> : <CircleCheck aria-hidden="true" />}{t('cmd.detail.approveAll')}</button>}</div>
      </div>}
      {view.files.some((f) => f.kind === 'attachment' && f.fileId) && <div className="detailFiles"><h3>{t('cmd.detail.files')}</h3><ul>{view.files.filter((f) => f.kind === 'attachment' && f.fileId).map((file) => <li key={file.path}><button type="button" className="linkButton" onClick={() => onOpenFile(file.fileId!)}><FileText aria-hidden="true" /><span dir="auto">{file.name}</span></button></li>)}</ul></div>}
      {problem && <PanelError message={problem} />}
      {settled && next && next.length > 0 && <div className="nextSteps"><h3>{t('cmd.detail.next')}</h3><ul className="quickChips">{next.map((item) => <li key={item.id}><button type="button" className="quickChip" disabled={(item.kind === 'save_quick_command' && saved) || (viewer && item.kind !== 'review_approvals')} onClick={() => onNext(item, view)}>
        {item.kind === 'save_quick_command' ? <Save aria-hidden="true" /> : item.kind === 'review_approvals' ? <ShieldCheck aria-hidden="true" /> : <Sparkles aria-hidden="true" />}<span dir="auto">{item.kind === 'save_quick_command' && saved ? t('cmd.detail.saved') : item.label}</span>
      </button></li>)}</ul></div>}
      <div className="planActions">
        <button type="button" className="secondary smallButton" disabled={busy !== null} onClick={() => void act('report', () => commandApi.downloadReport(view.id))}><Download aria-hidden="true" />{t('cmd.detail.report')}</button>
        {canDelete(view.status) && !viewer && <button type="button" className="secondary smallButton" disabled={busy !== null} onClick={() => { setBusy('delete'); commandApi.deleteCommand(view.id).then(() => onDeleted(view.id)).catch((e: unknown) => setProblem(describeError(e))).finally(() => setBusy(null)); }}><Trash2 aria-hidden="true" />{t('cmd.detail.delete')}</button>}
      </div>
    </>}
  </section>;
}

// ---------------------------------------------------------------- recent commands

function HistoryPanel({ history, selected, viewer, onOpen, onMore, onRetry, onReuse, onFocusComposer }: {
  history: History; selected: string | null; viewer: boolean; onOpen: (id: string) => void; onMore: () => void; onRetry: () => void; onReuse: (text: string) => void; onFocusComposer: () => void;
}) {
  const running = liveCommands(history.items, Infinity).length;
  return <section className="surface commandPanel" aria-labelledby="history-title">
    <div className="panelHead"><h2 id="history-title">{t('command.recent.title')}</h2>{running > 0 && <span className="activePill">{t('cmd.history.running', { count: running })}</span>}</div>
    {history.state === 'loading' && history.items.length === 0 && <PanelLoading />}
    {history.state === 'error' && <PanelError message={t('cmd.history.failed', { reason: history.message ?? '' })} onRetry={onRetry} />}
    {history.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
    {history.state === 'ready' && history.items.length === 0 && <EmptyPanel icon={<Sparkles />} title={t('cmd.history.emptyTitle')} text={viewer ? t('cmd.history.emptyViewer') : t('cmd.history.emptyText')} action={viewer ? undefined : t('cmd.history.emptyAction')} onAction={onFocusComposer} />}
    {history.items.length > 0 && <ul className="rowList">{history.items.map((command) => <li key={command.id} className="splitRow">
      <button type="button" className={`listRow ${selected === command.id ? 'selected' : ''}`} aria-current={selected === command.id ? 'true' : undefined} onClick={() => onOpen(command.id)}>
        <span className={`rowIcon ${commandTone(command.status)}`} aria-hidden="true">{isLive(command.status) ? <LoaderCircle /> : command.status === 'completed' ? <CircleCheck /> : command.status === 'waiting_approval' ? <ShieldCheck /> : <MessageSquare />}</span>
        <span className="rowText"><strong dir="auto">{command.text}</strong><small>{t('cmd.history.meta', { done: command.completedSteps, steps: command.stepCount, when: relativeTime(command.createdAt) })}</small></span>
        <span className={`statusBadge ${commandTone(command.status)}`}>{t(`cmd.status.${command.status}` as MessageKey)}</span>
      </button>
      {!viewer && <button type="button" className="iconButton small" aria-label={t('command.recent.reuse', { title: command.text })} title={t('command.recent.reuse', { title: command.text })} onClick={() => onReuse(command.text)}><RotateCcw aria-hidden="true" /></button>}
    </li>)}</ul>}
    {history.more && <button type="button" className="linkButton showMore" disabled={history.loadingMore} onClick={onMore}>{history.loadingMore ? t('common.loading') : t('cmd.history.more')}</button>}
  </section>;
}

// ---------------------------------------------------------------- saved and favourite commands

function QuickCommandsPanel({ viewer, onUse, onChanged }: { viewer: boolean; onUse: (q: QuickCommand) => void; onChanged: () => void }) {
  const [items, setItems] = useState<Load<QuickCommand[]>>({ state: 'loading' });
  const [category, setCategory] = useState<QuickCommandCategory | 'all'>('all');
  const [form, setForm] = useState<{ id: string | null; title: string; text: string; category: QuickCommandCategory } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const formId = useId();
  const load = useCallback(() => { setItems({ state: 'loading' }); void loadInto(commandApi.listQuick(), setItems); }, []);
  useEffect(load, [load]);
  const list = items.state === 'ready' ? items.value.filter((q) => category === 'all' || q.category === category).sort((a, b) => QUICK_CATEGORIES.indexOf(a.category) - QUICK_CATEGORIES.indexOf(b.category) || a.position - b.position) : [];
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    const title = form.title.trim(), text = form.text.trim();
    if (!title || !text) { setProblem(t('cmd.quick.needBoth')); return; }
    setSaving(true); setProblem(null);
    try {
      if (form.id) await commandApi.updateQuick(form.id, { title, text, category: form.category });
      else await commandApi.createQuick({ title, text, category: form.category });
      setForm(null); load(); onChanged();
    } catch (e) { setProblem(describeError(e)); } finally { setSaving(false); }
  };
  const remove = (q: QuickCommand) => { setProblem(null); commandApi.deleteQuick(q.id).then(() => { load(); onChanged(); }).catch((e: unknown) => setProblem(describeError(e))); };
  return <section className="surface commandPanel" aria-labelledby="quick-title">
    <div className="panelHead"><h2 id="quick-title"><Star aria-hidden="true" />{t('cmd.quick.title')}</h2>
      <div className="quickHeadTools">
        <div className="segmented" role="group" aria-label={t('cmd.quick.filter')}>
          {(['all', ...QUICK_CATEGORIES] as const).map((key) => <button key={key} type="button" aria-pressed={category === key} className={category === key ? 'on' : ''} onClick={() => setCategory(key)}>{t(`cmd.quick.cat.${key}` as MessageKey)}</button>)}
        </div>
        {!viewer && items.state === 'ready' && !form && <button type="button" className="secondary smallButton" onClick={() => setForm({ id: null, title: '', text: '', category: category === 'all' ? 'custom' : category })}><Plus aria-hidden="true" />{t('cmd.quick.add')}</button>}
      </div></div>
    {form && <form className="quickForm" onSubmit={(e) => void save(e)} aria-label={form.id ? t('cmd.quick.editTitle') : t('cmd.quick.add')}>
      <label htmlFor={`${formId}-title`}><span>{t('cmd.quick.name')}</span><input id={`${formId}-title`} maxLength={80} dir="auto" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
      <label htmlFor={`${formId}-cat`}><span>{t('cmd.quick.category')}</span><select id={`${formId}-cat`} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value as QuickCommandCategory })}>{QUICK_CATEGORIES.map((c) => <option key={c} value={c}>{t(`cmd.quick.cat.${c}` as MessageKey)}</option>)}</select></label>
      <label htmlFor={`${formId}-text`} className="wide"><span>{t('cmd.quick.text')}</span><textarea id={`${formId}-text`} rows={2} maxLength={2000} dir="auto" value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} /></label>
      <div className="planActions wide"><button type="button" className="secondary smallButton" onClick={() => { setForm(null); setProblem(null); }}>{t('common.cancel')}</button><button type="submit" className="primaryButton smallButton" disabled={saving}><Save aria-hidden="true" />{t('cmd.quick.save')}</button></div>
    </form>}
    {problem && <PanelError message={problem} />}
    {items.state === 'loading' && <PanelLoading />}
    {items.state === 'error' && <PanelError message={t('cmd.quick.failed', { reason: items.message })} onRetry={load} />}
    {items.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
    {items.state === 'ready' && list.length === 0 && !form && <EmptyPanel icon={<Star />} title={t('cmd.quick.emptyTitle')} text={viewer ? t('cmd.quick.emptyViewer') : t('cmd.quick.emptyText')} action={viewer ? undefined : t('cmd.quick.add')} onAction={() => setForm({ id: null, title: '', text: '', category: category === 'all' ? 'custom' : category })} />}
    {list.length > 0 && <ul className="quickGrid">{list.map((q) => <li key={q.id} className="quickCard">
      <div className="quickCardTop"><strong dir="auto">{q.title}</strong><span className="countPill">{t(`cmd.quick.cat.${q.category}` as MessageKey)}</span></div>
      <p dir="auto">{q.text}</p>
      <small>{q.useCount ? t('cmd.quick.used', { count: q.useCount }) : t('cmd.quick.unused')}</small>
      <div className="quickCardActions">
        {!viewer && <button type="button" className="primaryButton smallButton" onClick={() => onUse(q)}><Play aria-hidden="true" />{t('cmd.quick.use')}</button>}
        {!viewer && <button type="button" className="iconButton small" aria-label={t('cmd.quick.editNamed', { title: q.title })} title={t('cmd.quick.editTitle')} onClick={() => setForm({ id: q.id, title: q.title, text: q.text, category: q.category })}><Pencil aria-hidden="true" /></button>}
        {!viewer && <button type="button" className="iconButton small" aria-label={t('cmd.quick.deleteNamed', { title: q.title })} title={t('cmd.quick.delete')} onClick={() => remove(q)}><Trash2 aria-hidden="true" /></button>}
      </div>
    </li>)}</ul>}
  </section>;
}
