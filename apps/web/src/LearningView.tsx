import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import {
  CalendarClock, Check, ChevronLeft, ChevronRight, CircleDot, Lightbulb, MessageSquare, Pencil, Plus, RefreshCw, RotateCcw, Search,
  ShieldAlert, Trash2, Workflow, X,
} from 'lucide-react';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import {
  LANGUAGES, LENGTHS, PAGE_SIZE, SIGNAL_KINDS, SURFACE_SCOPES, TONES, appliedMessage, browserTimeZone, editLine, joinScope, kindLabel,
  languageLabel, lengthLabel, overridesFrom, pageInfo, routineEvidence, routineStatusTone, scopeLabel, signalTotal, splitScope,
  stateStatus, styleForm, suggestionBlocker, summaryRows, surfaceLabel, switchEditable, toneLabel,
  type ImprovementNoteView, type LearnedPreference, type LearnedRoutine, type LearningOverview, type LearningSignal, type ScopeKind,
  type StyleForm,
} from './lib/learning';
import * as learningApi from './lib/learningApi';
import './learning.css';

type Load = { kind: 'loading' } | { kind: 'ready'; overview: LearningOverview } | { kind: 'denied' } | { kind: 'error'; message: string };
type Outcome = { tone: 'ok' | 'error'; text: string } | null;

const fail = (key: MessageKey, error: unknown) => t(key, { reason: describeError(error) });

function Note({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  return <p className={`notice ${outcome.tone === 'error' ? 'errorNotice' : ''}`} role={outcome.tone === 'error' ? 'alert' : 'status'}>
    <CircleDot aria-hidden="true" /><span>{outcome.text}</span>
  </p>;
}

function Pager({ total, offset, onPage, label }: { total: number; offset: number; onPage: (offset: number) => void; label: string }) {
  const info = pageInfo(total, PAGE_SIZE, offset);
  if (info.pages <= 1) return null;
  return <nav className="learningPager" aria-label={label}>
    <button type="button" className="secondary" disabled={!info.hasPrev} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft aria-hidden="true" className="flipRtl" />{t('learning.prev')}</button>
    <span role="status">{t('learning.showing', { from: info.from, to: info.to, total })}</span>
    <button type="button" className="secondary" disabled={!info.hasNext} onClick={() => onPage(offset + PAGE_SIZE)}>{t('learning.next')}<ChevronRight aria-hidden="true" className="flipRtl" /></button>
  </nav>;
}

/**
 * What ANUM learned (docs/learning.md): the caller's learning switch, style profile, learned
 * preferences, detected routines, weekly improvement notes and every feedback signal, each with
 * edit and delete. Everything here is the caller's own data in this workspace.
 */
export function LearningView({ onOpenChatMemory, onOpenWorkbench }: { onOpenChatMemory?: () => void; onOpenWorkbench?: () => void } = {}) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  // Bumped after changes that affect other sections (an applied suggestion adds a preference).
  const [version, setVersion] = useState(0);

  const refresh = useCallback(async () => {
    try { setLoad({ kind: 'ready', overview: await learningApi.getOverview() }); }
    catch (e) { setLoad(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', message: fail('learning.loadFailed', e) }); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const changed = useCallback(() => { setVersion((v) => v + 1); void refresh(); }, [refresh]);

  if (load.kind === 'loading') return <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{t('learning.loading')}</p>;
  if (load.kind === 'denied') return <section className="surface settingsForm" aria-labelledby="learning-denied"><h2 id="learning-denied">{t('learning.title')}</h2><p className="notice errorNotice" role="alert"><ShieldAlert aria-hidden="true" />{t('learning.denied')}</p></section>;
  if (load.kind === 'error') return <section className="surface settingsForm" aria-labelledby="learning-error"><h2 id="learning-error">{t('learning.title')}</h2><p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{load.message}</p><div className="actions"><button type="button" className="secondary" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></section>;

  const overview = load.overview;
  return <div className="learningPage">
    <StateCard overview={overview} onChange={(next) => setLoad({ kind: 'ready', overview: next })} onOpenChatMemory={onOpenChatMemory} />
    <div className="learningColumns">
      <StyleCard overview={overview} onSaved={changed} />
      <PreferencesCard version={version} onChanged={() => void refresh()} />
    </div>
    <NotesCard overview={overview} onApplied={changed} />
    <RoutinesCard overview={overview} onChanged={() => void refresh()} onOpenWorkbench={onOpenWorkbench} />
    <SignalsCard version={version} onChanged={changed} />
  </div>;
}

// --------------------------------------------------------------------------- switch

function StateCard({ overview, onChange, onOpenChatMemory }: { overview: LearningOverview; onChange: (next: LearningOverview) => void; onOpenChatMemory?: () => void }) {
  const [saving, setSaving] = useState(false), [outcome, setOutcome] = useState<Outcome>(null);
  // The choice shows at once; a failed save puts the previous one back.
  const [pending, setPending] = useState<boolean | null>(null);
  const describedBy = useId();
  const status = stateStatus(overview.state);
  const counts = overview.counts;

  async function toggle(enabled: boolean) {
    setSaving(true); setOutcome(null); setPending(enabled);
    try {
      const next = await learningApi.setLearning(enabled);
      onChange(next);
      setOutcome({ tone: 'ok', text: enabled ? t('learning.saved.on') : t('learning.saved.off') });
    } catch (e) { setOutcome({ tone: 'error', text: fail('learning.saveFailed', e) }); }
    finally { setSaving(false); setPending(null); }
  }

  const stats: [string, number][] = [
    [t('learning.stat.signals'), signalTotal(counts.signals)],
    [t('learning.stat.preferences'), counts.preferences],
    [t('learning.stat.routines'), counts.routines_proposed],
    [t('learning.stat.suggestions'), counts.suggestions_open],
  ];
  return <section className="surface settingsForm learningState" aria-labelledby="learning-state-title" aria-busy={saving}>
    <div className="sectionHeader">
      <div><p className="eyebrow">{t('learning.eyebrow')}</p><h2 id="learning-state-title">{t('learning.title')}</h2></div>
      <span className={`statusBadge ${status.tone}`} data-testid="learning-state">{status.badge}</span>
    </div>
    <p className="muted">{t('learning.intro')}</p>
    <label className="settingToggle">
      <div><strong>{t('learning.switch.label')}</strong><p id={describedBy}>{status.text}</p></div>
      <input type="checkbox" checked={pending ?? overview.profile.enabled} disabled={saving || overview.state === 'off_on_server'} aria-describedby={describedBy}
        onChange={(e) => void toggle(e.target.checked)} />
    </label>
    {!switchEditable(overview.state) && status.offerChatMemory && onOpenChatMemory && <div className="actions"><button type="button" className="secondary" onClick={onOpenChatMemory}><MessageSquare aria-hidden="true" />{t('learning.openChatMemory')}</button></div>}
    <dl className="learningStats">{stats.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatNumber(value)}</dd></div>)}</dl>
    <Note outcome={outcome} />
  </section>;
}

// --------------------------------------------------------------------------- style

function StyleCard({ overview, onSaved }: { overview: LearningOverview; onSaved: () => void }) {
  const profile = overview.profile, learned = profile.derived;
  const [form, setForm] = useState<StyleForm>(() => styleForm(profile));
  const [saving, setSaving] = useState(false), [outcome, setOutcome] = useState<Outcome>(null), [confirmReset, setConfirmReset] = useState(false);
  const id = useId();
  useEffect(() => { setForm(styleForm(overview.profile)); }, [overview.profile]);
  const set = <K extends keyof StyleForm>(key: K, value: StyleForm[K]) => setForm((current) => ({ ...current, [key]: value }));
  const learnedText = (value: string | null | undefined, label?: (v: string) => string) => value ? t('learning.style.learned', { value: label ? label(value) : value }) : t('learning.style.notLearned');

  async function save(event: FormEvent) {
    event.preventDefault(); setSaving(true); setOutcome(null);
    try { await learningApi.saveProfile(overridesFrom(form)); setOutcome({ tone: 'ok', text: t('learning.style.saved') }); onSaved(); }
    catch (e) { setOutcome({ tone: 'error', text: fail('learning.style.saveFailed', e) }); }
    finally { setSaving(false); }
  }
  async function reset() {
    setSaving(true); setOutcome(null); setConfirmReset(false);
    try { const result = await learningApi.resetProfile(); setOutcome({ tone: 'ok', text: t('learning.style.resetDone', { count: result.signals_deleted }) }); onSaved(); }
    catch (e) { setOutcome({ tone: 'error', text: fail('learning.style.resetFailed', e) }); }
    finally { setSaving(false); }
  }

  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={saving}>
    <div className="sectionHeader"><h2 id={`${id}-title`}>{t('learning.style.title')}</h2>
      {profile.derived_at && <span className="muted learningWhen">{t('learning.style.derivedAt', { when: formatDateTime(profile.derived_at) })}</span>}</div>
    <p className="muted">{t('learning.style.text', { count: learned.samples })}</p>
    <form onSubmit={(e) => void save(e)}>
      <div className="formGrid">
        <label className="field"><span>{t('learning.style.tone')}</span>
          <select value={form.tone} onChange={(e) => set('tone', e.target.value as StyleForm['tone'])} aria-describedby={`${id}-tone`}>
            <option value="">{t('learning.style.useLearned')}</option>{TONES.map((v) => <option key={v} value={v}>{toneLabel(v)}</option>)}
          </select><small id={`${id}-tone`} className="muted">{learnedText(learned.tone, toneLabel)}</small></label>
        <label className="field"><span>{t('learning.style.length')}</span>
          <select value={form.length} onChange={(e) => set('length', e.target.value as StyleForm['length'])} aria-describedby={`${id}-length`}>
            <option value="">{t('learning.style.useLearned')}</option>{LENGTHS.map((v) => <option key={v} value={v}>{lengthLabel(v)}</option>)}
          </select><small id={`${id}-length`} className="muted">{learned.length ? t('learning.style.learnedLength', { value: lengthLabel(learned.length), words: learned.target_words ?? 0 }) : t('learning.style.notLearned')}</small></label>
        <div className="field"><label htmlFor={`${id}-greeting`}><span>{t('learning.style.greeting')}</span></label>
          <input id={`${id}-greeting`} value={form.greeting} disabled={form.noGreeting} maxLength={80} dir="auto" placeholder={learned.greeting ?? ''} onChange={(e) => set('greeting', e.target.value)} aria-describedby={`${id}-greeting-help`} />
          <label className="learningCheck"><input type="checkbox" checked={form.noGreeting} onChange={(e) => set('noGreeting', e.target.checked)} />{t('learning.style.noGreeting')}</label>
          <small id={`${id}-greeting-help`} className="muted"><bdi>{learnedText(learned.greeting)}</bdi></small></div>
        <div className="field"><label htmlFor={`${id}-signoff`}><span>{t('learning.style.signOff')}</span></label>
          <input id={`${id}-signoff`} value={form.signOff} disabled={form.noSignOff} maxLength={80} dir="auto" placeholder={learned.sign_off ?? ''} onChange={(e) => set('signOff', e.target.value)} aria-describedby={`${id}-signoff-help`} />
          <label className="learningCheck"><input type="checkbox" checked={form.noSignOff} onChange={(e) => set('noSignOff', e.target.checked)} />{t('learning.style.noSignOff')}</label>
          <small id={`${id}-signoff-help`} className="muted"><bdi>{learnedText(learned.sign_off)}</bdi></small></div>
        <label className="field"><span>{t('learning.style.language')}</span>
          <select value={form.language} onChange={(e) => set('language', e.target.value as StyleForm['language'])} aria-describedby={`${id}-language`}>
            <option value="">{t('learning.style.useLearned')}</option>{LANGUAGES.map((v) => <option key={v} value={v}>{languageLabel(v)}</option>)}
          </select><small id={`${id}-language`} className="muted">{learnedText(learned.language, languageLabel)}</small></label>
      </div>
      <fieldset className="learningContacts">
        <legend>{t('learning.style.contacts')}</legend>
        {Object.entries(learned.contact_languages).length > 0 && <p className="muted">{t('learning.style.contactsLearned', { list: Object.entries(learned.contact_languages).map(([c, l]) => `${c}: ${languageLabel(l)}`).join(t('common.listSeparator')) })}</p>}
        {form.contacts.map((row, index) => <div className="learningContactRow" key={index}>
          <input aria-label={t('learning.style.contactPlaceholder')} placeholder={t('learning.style.contactPlaceholder')} value={row.contact} maxLength={160} dir="ltr"
            onChange={(e) => set('contacts', form.contacts.map((r, i) => i === index ? { ...r, contact: e.target.value } : r))} />
          <select aria-label={t('learning.style.language')} value={row.language} onChange={(e) => set('contacts', form.contacts.map((r, i) => i === index ? { ...r, language: e.target.value as typeof row.language } : r))}>
            {LANGUAGES.map((v) => <option key={v} value={v}>{languageLabel(v)}</option>)}
          </select>
          <button type="button" className="iconButton" aria-label={t('learning.style.removeContact', { contact: row.contact || '-' })} onClick={() => set('contacts', form.contacts.filter((_, i) => i !== index))}><X aria-hidden="true" /></button>
        </div>)}
        <button type="button" className="secondary learningInlineButton" disabled={form.contacts.length >= 100} onClick={() => set('contacts', [...form.contacts, { contact: '', language: 'ar' }])}><Plus aria-hidden="true" />{t('learning.style.addContact')}</button>
      </fieldset>
      <label className="field"><span>{t('learning.style.notes')}</span>
        <textarea className="learningShortText" value={form.notes} maxLength={500} dir="auto" placeholder={t('learning.style.notesPlaceholder')} onChange={(e) => set('notes', e.target.value)} /></label>
      <div className="actions">
        <button type="submit" disabled={saving}><Check aria-hidden="true" />{t('learning.style.save')}</button>
        {!confirmReset && <button type="button" className="secondary" disabled={saving} onClick={() => setConfirmReset(true)}><RotateCcw aria-hidden="true" />{t('learning.style.reset')}</button>}
      </div>
    </form>
    {confirmReset && <div className="learningConfirm" role="group" aria-labelledby={`${id}-reset`}>
      <p id={`${id}-reset`}>{t('learning.style.resetConfirm')}</p>
      <div className="actions"><button type="button" className="danger" onClick={() => void reset()}><RotateCcw aria-hidden="true" />{t('learning.style.resetYes')}</button><button type="button" className="secondary" onClick={() => setConfirmReset(false)}>{t('common.cancel')}</button></div>
    </div>}
    <Note outcome={outcome} />
  </section>;
}

// --------------------------------------------------------------------------- preferences

function ScopeFields({ kind, target, onKind, onTarget, idPrefix }: { kind: ScopeKind; target: string; onKind: (k: ScopeKind) => void; onTarget: (v: string) => void; idPrefix: string }) {
  const needsTarget = kind === 'contact' || kind === 'tool' || kind === 'skill';
  return <>
    <label className="field"><span>{t('learning.prefs.scope')}</span>
      <select id={`${idPrefix}-scope`} value={kind} onChange={(e) => onKind(e.target.value as ScopeKind)}>
        <option value="all">{t('learning.scope.all')}</option>
        {SURFACE_SCOPES.map((s) => <option key={s} value={`surface:${s}`}>{t(`learning.scope.surface.${s}` as MessageKey)}</option>)}
        <option value="contact">{t('learning.scope.contact')}</option>
        <option value="tool">{t('learning.scope.tool')}</option>
        <option value="skill">{t('learning.scope.skill')}</option>
      </select></label>
    {needsTarget && <label className="field"><span>{t('learning.scope.target')}</span><input value={target} maxLength={190} dir="ltr" onChange={(e) => onTarget(e.target.value)} required /></label>}
  </>;
}

function PreferencesCard({ version, onChanged }: { version: number; onChanged: () => void }) {
  const [items, setItems] = useState<LearnedPreference[]>([]), [total, setTotal] = useState(0), [offset, setOffset] = useState(0);
  const [query, setQuery] = useState(''), [applied, setApplied] = useState('');
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [error, setError] = useState('');
  const [kind, setKind] = useState<ScopeKind>('all'), [target, setTarget] = useState(''), [text, setText] = useState('');
  const [editing, setEditing] = useState<{ id: string; kind: ScopeKind; target: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false), [outcome, setOutcome] = useState<Outcome>(null);
  const id = useId();

  const load = useCallback(async () => {
    setState('loading');
    try { const page = await learningApi.listPreferences({ q: applied, limit: PAGE_SIZE, offset }); setItems(page.items); setTotal(page.total); setState('ready'); }
    catch (e) { setError(fail('learning.prefs.loadFailed', e)); setState('error'); }
  }, [applied, offset]);
  useEffect(() => { void load(); }, [load, version]);

  async function add(event: FormEvent) {
    event.preventDefault();
    const scope = joinScope(kind, target);
    if (!scope || !text.trim()) return;
    setBusy(true); setOutcome(null);
    try { await learningApi.createPreference(scope, text); setText(''); setTarget(''); setOutcome({ tone: 'ok', text: t('learning.prefs.added') }); setOffset(0); await load(); onChanged(); }
    catch (e) { setOutcome({ tone: 'error', text: fail('learning.prefs.saveFailed', e) }); }
    finally { setBusy(false); }
  }
  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const scope = joinScope(editing.kind, editing.target);
    if (!scope || !editing.text.trim()) return;
    setBusy(true); setOutcome(null);
    try { await learningApi.updatePreference(editing.id, scope, editing.text); setEditing(null); setOutcome({ tone: 'ok', text: t('learning.prefs.updated') }); await load(); }
    catch (e) { setOutcome({ tone: 'error', text: fail('learning.prefs.saveFailed', e) }); }
    finally { setBusy(false); }
  }
  async function remove(item: LearnedPreference) {
    setBusy(true); setOutcome(null);
    try { await learningApi.deletePreference(item.id); setOutcome({ tone: 'ok', text: t('learning.prefs.deleted') }); if (items.length === 1 && offset) setOffset(offset - PAGE_SIZE); else await load(); onChanged(); }
    catch (e) { setOutcome({ tone: 'error', text: fail('learning.prefs.deleteFailed', e) }); }
    finally { setBusy(false); }
  }

  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={state === 'loading' || busy}>
    <h2 id={`${id}-title`}>{t('learning.prefs.title')}</h2>
    <p className="muted">{t('learning.prefs.intro')}</p>
    <form className="learningSearch" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied(query.trim()); }}>
      <input type="search" value={query} maxLength={200} aria-label={t('learning.prefs.search')} placeholder={t('learning.prefs.search')} onChange={(e) => setQuery(e.target.value)} dir="auto" />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
    </form>
    {state === 'loading' && items.length === 0 && <p className="muted" role="status">{t('common.loading')}</p>}
    {state === 'error' && <div role="alert"><p className="notice errorNotice"><CircleDot aria-hidden="true" />{error}</p><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>}
    {state === 'ready' && items.length === 0 && <p className="muted learningEmpty">{applied ? t('learning.prefs.noMatch', { query: applied }) : t('learning.prefs.empty')}</p>}
    {items.length > 0 && <ul className="learningList">{items.map((item) => <li key={item.id}>
      {editing?.id === item.id
        ? <form className="learningEdit" onSubmit={(e) => void saveEdit(e)}>
          <div className="formGrid"><ScopeFields idPrefix={`${id}-edit`} kind={editing.kind} target={editing.target} onKind={(k) => setEditing({ ...editing, kind: k })} onTarget={(v) => setEditing({ ...editing, target: v })} /></div>
          <label className="field"><span>{t('learning.prefs.text')}</span><input value={editing.text} maxLength={300} dir="auto" onChange={(e) => setEditing({ ...editing, text: e.target.value })} required /></label>
          <div className="actions"><button type="submit" disabled={busy}><Check aria-hidden="true" />{t('learning.prefs.save')}</button><button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button></div>
        </form>
        : <div className="learningRow">
          <div><p dir="auto">{item.text}</p><small>{scopeLabel(item.scope)}{item.source === 'suggestion' ? ` · ${t('learning.prefs.fromSuggestion')}` : ''}</small></div>
          <div className="rowActions">
            <button type="button" className="iconButton" aria-label={t('learning.prefs.editNamed', { text: item.text })} onClick={() => { const s = splitScope(item.scope); setEditing({ id: item.id, kind: s.kind, target: s.target, text: item.text }); }}><Pencil aria-hidden="true" /></button>
            <button type="button" className="iconButton" aria-label={t('learning.prefs.deleteNamed', { text: item.text })} disabled={busy} onClick={() => void remove(item)}><Trash2 aria-hidden="true" /></button>
          </div>
        </div>}
    </li>)}</ul>}
    <Pager total={total} offset={offset} onPage={setOffset} label={t('learning.prefs.pages')} />
    <form className="learningAdd" onSubmit={(e) => void add(e)} aria-labelledby={`${id}-add`}>
      <h3 id={`${id}-add`}>{t('learning.prefs.add')}</h3>
      <div className="formGrid"><ScopeFields idPrefix={`${id}-new`} kind={kind} target={target} onKind={setKind} onTarget={setTarget} /></div>
      <label className="field"><span>{t('learning.prefs.text')}</span><input value={text} maxLength={300} dir="auto" placeholder={t('learning.prefs.placeholder')} onChange={(e) => setText(e.target.value)} /></label>
      <div className="actions"><button type="submit" disabled={busy || !text.trim() || !joinScope(kind, target)}><Plus aria-hidden="true" />{t('learning.prefs.add')}</button></div>
    </form>
    <Note outcome={outcome} />
  </section>;
}

// --------------------------------------------------------------------------- routines

function RoutinesCard({ overview, onChanged, onOpenWorkbench }: { overview: LearningOverview; onChanged: () => void; onOpenWorkbench?: () => void }) {
  const [items, setItems] = useState<LearnedRoutine[]>([]), [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null), [outcome, setOutcome] = useState<Outcome>(null);
  const id = useId();
  const on = overview.state === 'on', canManage = overview.can_manage_automations;

  const load = useCallback(async () => {
    setState('loading');
    try { setItems((await learningApi.listRoutines()).items); setState('ready'); }
    catch (e) { setError(fail('learning.routines.loadFailed', e)); setState('error'); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function detect() {
    setBusy('detect'); setOutcome(null);
    try { const result = await learningApi.detectRoutines(browserTimeZone()); setItems(result.items); setOutcome({ tone: 'ok', text: t('learning.routines.detected', { count: result.detected }) }); onChanged(); }
    catch (e) { setOutcome({ tone: 'error', text: fail('learning.routines.failed', e) }); }
    finally { setBusy(null); }
  }
  async function act(routine: LearnedRoutine, action: 'accept' | 'confirm' | 'dismiss' | 'delete') {
    setBusy(routine.id); setOutcome(null);
    try {
      if (action === 'delete') { await learningApi.deleteRoutine(routine.id); setItems((all) => all.filter((r) => r.id !== routine.id)); setOutcome({ tone: 'ok', text: t('learning.routines.deleted') }); }
      else {
        const next = await learningApi.routineAction(routine.id, action);
        setItems((all) => all.map((r) => r.id === next.id ? next : r));
        setOutcome({ tone: 'ok', text: t(`learning.routines.done.${action}` as MessageKey) });
      }
      onChanged();
    } catch (e) {
      setOutcome({ tone: 'error', text: isPermissionDenied(e) ? t('learning.routines.noPermission') : fail('learning.routines.failed', e) });
    } finally { setBusy(null); }
  }

  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={state === 'loading' || busy !== null}>
    <div className="sectionHeader"><h2 id={`${id}-title`}>{t('learning.routines.title')}</h2>
      <button type="button" className="secondary" disabled={!on || busy !== null} onClick={() => void detect()}><CalendarClock aria-hidden="true" />{busy === 'detect' ? t('learning.routines.detecting') : t('learning.routines.detect')}</button></div>
    <p className="muted">{t('learning.routines.text')}</p>
    {!on && <p className="muted">{stateStatus(overview.state).text}</p>}
    {!canManage && <p className="notice" role="note"><ShieldAlert aria-hidden="true" />{t('learning.routines.noPermission')}</p>}
    {state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
    {state === 'error' && <div role="alert"><p className="notice errorNotice"><CircleDot aria-hidden="true" />{error}</p><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>}
    {state === 'ready' && items.length === 0 && <p className="muted learningEmpty">{t('learning.routines.empty')}</p>}
    {items.length > 0 && <ul className="learningList">{items.map((routine) => <li key={routine.id} className="learningRoutine">
      <div className="sectionHeader"><strong dir="auto">{routine.title}</strong><span className={`statusBadge ${routineStatusTone(routine.status)}`}>{t(`learning.routines.status.${routine.status}` as MessageKey)}</span></div>
      <ol>{routine.steps.map((step, index) => <li key={index} dir="auto">{step.label}</li>)}</ol>
      <small className="muted">{routineEvidence(routine)} · <code dir="ltr">{routine.cron}</code></small>
      <div className="actions">
        {routine.status === 'proposed' && <button type="button" disabled={!canManage || busy !== null} onClick={() => void act(routine, 'accept')}><Workflow aria-hidden="true" />{t('learning.routines.accept')}</button>}
        {routine.status === 'accepted' && <button type="button" disabled={!canManage || busy !== null} onClick={() => void act(routine, 'confirm')}><Check aria-hidden="true" />{t('learning.routines.confirm')}</button>}
        {routine.status !== 'proposed' && routine.workflow_id && onOpenWorkbench && <button type="button" className="secondary" onClick={onOpenWorkbench}><Workflow aria-hidden="true" />{t('learning.routines.openWorkbench')}</button>}
        {routine.status === 'proposed' && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(routine, 'dismiss')}><X aria-hidden="true" />{t('learning.routines.dismiss')}</button>}
        <button type="button" className="secondary" disabled={busy !== null} aria-label={t('learning.routines.deleteNamed', { title: routine.title })} onClick={() => void act(routine, 'delete')}><Trash2 aria-hidden="true" />{t('common.delete')}</button>
      </div>
    </li>)}</ul>}
    <Note outcome={outcome} />
  </section>;
}

// --------------------------------------------------------------------------- notes

function NotesCard({ overview, onApplied }: { overview: LearningOverview; onApplied: () => void }) {
  const [notes, setNotes] = useState<ImprovementNoteView[]>([]), [total, setTotal] = useState(0);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null), [outcome, setOutcome] = useState<Outcome>(null);
  const id = useId();
  const on = overview.state === 'on';

  const load = useCallback(async (from = 0) => {
    if (!from) setState('loading');
    try {
      const page = await learningApi.listNotes(4, from);
      setNotes((current) => from ? [...current, ...page.items] : page.items); setTotal(page.total); setState('ready');
    } catch (e) { setError(fail('learning.notes.loadFailed', e)); setState('error'); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function refresh() {
    setBusy('refresh'); setOutcome(null);
    try {
      const view = await learningApi.refreshNote();
      setNotes((current) => [view, ...current.filter((n) => n.note.week !== view.note.week)]);
      setOutcome({ tone: 'ok', text: t('learning.notes.refreshed', { count: view.suggestions.filter((s) => s.status === 'open').length }) });
    } catch (e) { setOutcome({ tone: 'error', text: fail('learning.notes.loadFailed', e) }); }
    finally { setBusy(null); }
  }
  async function act(suggestionId: string, action: 'apply' | 'dismiss') {
    setBusy(suggestionId); setOutcome(null);
    try {
      const next = await learningApi.suggestionAction(suggestionId, action);
      setNotes((current) => current.map((view) => ({ ...view, suggestions: view.suggestions.map((s) => s.id === next.id ? next : s) })));
      setOutcome({ tone: 'ok', text: action === 'apply' ? t('learning.suggestion.applied', { what: appliedMessage(next) }) : t('learning.suggestion.dismissed') });
      if (action === 'apply') onApplied();
    } catch (e) {
      setOutcome({ tone: 'error', text: isPermissionDenied(e) ? t('learning.suggestion.needsOwner') : fail('learning.suggestion.failed', e) });
    } finally { setBusy(null); }
  }

  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={state === 'loading' || busy !== null}>
    <div className="sectionHeader"><h2 id={`${id}-title`}>{t('learning.notes.title')}</h2>
      <button type="button" className="secondary" disabled={!on || busy !== null} onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('learning.notes.refresh')}</button></div>
    <p className="muted">{t('learning.notes.text')}</p>
    {!on && <p className="muted">{stateStatus(overview.state).text}</p>}
    {state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
    {state === 'error' && <div role="alert"><p className="notice errorNotice"><CircleDot aria-hidden="true" />{error}</p><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>}
    {state === 'ready' && notes.length === 0 && <p className="muted learningEmpty">{t('learning.notes.empty')}</p>}
    {notes.map((view) => <article key={view.note.week} className="learningNote" aria-labelledby={`${id}-${view.note.week}`}>
      <h3 id={`${id}-${view.note.week}`}>{t('learning.notes.week', { week: view.note.week })} <small className="muted">{t('learning.notes.period', { from: formatDateTime(view.note.period_start, { month: 'short', day: 'numeric' }), to: formatDateTime(view.note.period_end, { month: 'short', day: 'numeric' }) })}</small></h3>
      <dl className="learningStats compact">{summaryRows(view.note.summary).map((row) => <div key={row.key}><dt>{row.label}</dt><dd>{formatNumber(row.value)}</dd></div>)}</dl>
      {view.suggestions.length === 0 && <p className="muted">{t('learning.notes.noSuggestions')}</p>}
      {view.suggestions.length > 0 && <ul className="learningList">{view.suggestions.map((s) => {
        const blocker = suggestionBlocker(s);
        return <li key={s.id} className="learningSuggestion">
          <div className="sectionHeader"><strong dir="auto"><Lightbulb aria-hidden="true" />{s.title}</strong>
            <span className={`statusBadge ${s.status === 'applied' ? 'success' : s.kind === 'autonomy' ? 'warning' : 'neutral'}`}>{s.status === 'open' ? t(`learning.suggestion.kind.${s.kind}` as MessageKey) : t(`learning.suggestion.status.${s.status}` as MessageKey)}</span></div>
          <p dir="auto">{s.body}</p>
          {blocker && <p className="muted" role="note">{blocker}</p>}
          {s.status === 'open' && <div className="actions">
            <button type="button" disabled={Boolean(blocker) || busy !== null} onClick={() => void act(s.id, 'apply')}><Check aria-hidden="true" />{t('learning.suggestion.apply')}</button>
            <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(s.id, 'dismiss')}><X aria-hidden="true" />{t('learning.suggestion.dismiss')}</button>
          </div>}
        </li>;
      })}</ul>}
    </article>)}
    {notes.length < total && state === 'ready' && <div className="actions"><button type="button" className="secondary" onClick={() => void load(notes.length)}>{t('learning.notes.older')}</button></div>}
    <Note outcome={outcome} />
  </section>;
}

// --------------------------------------------------------------------------- signals

function SignalText({ label, text }: { label: string; text: string | null }): ReactNode {
  if (!text) return null;
  return <div className="learningText"><span>{label}</span><p dir="auto">{text}</p></div>;
}

function SignalsCard({ version, onChanged }: { version: number; onChanged: () => void }) {
  const [items, setItems] = useState<LearningSignal[]>([]), [total, setTotal] = useState(0), [offset, setOffset] = useState(0);
  const [kind, setKind] = useState(''), [query, setQuery] = useState(''), [applied, setApplied] = useState('');
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [error, setError] = useState('');
  const [open, setOpen] = useState<Record<string, LearningSignal>>({}), [busy, setBusy] = useState<string | null>(null), [outcome, setOutcome] = useState<Outcome>(null);
  const id = useId();

  const load = useCallback(async () => {
    setState('loading');
    try { const page = await learningApi.listSignals({ kind, q: applied, limit: PAGE_SIZE, offset }); setItems(page.items); setTotal(page.total); setState('ready'); }
    catch (e) { setError(fail('learning.signals.loadFailed', e)); setState('error'); }
  }, [kind, applied, offset]);
  useEffect(() => { void load(); }, [load, version]);

  async function toggle(signal: LearningSignal) {
    if (open[signal.id]) { setOpen(({ [signal.id]: _, ...rest }) => rest); return; }
    if (!signal.truncated) { setOpen((current) => ({ ...current, [signal.id]: signal })); return; }
    setBusy(signal.id);
    try { const full = await learningApi.getSignal(signal.id); setOpen((current) => ({ ...current, [signal.id]: full })); }
    catch (e) { setOutcome({ tone: 'error', text: fail('learning.signals.loadFailed', e) }); }
    finally { setBusy(null); }
  }
  async function remove(signal: LearningSignal) {
    setBusy(signal.id); setOutcome(null);
    try {
      await learningApi.deleteSignal(signal.id);
      setOutcome({ tone: 'ok', text: t('learning.signals.deleted') });
      if (items.length === 1 && offset) setOffset(offset - PAGE_SIZE); else await load();
      if (signal.kind === 'draft_accepted' || signal.kind === 'draft_edited') onChanged();
    } catch (e) { setOutcome({ tone: 'error', text: e instanceof ApiError && e.status === 404 ? t('learning.signals.gone') : fail('learning.signals.deleteFailed', e) }); }
    finally { setBusy(null); }
  }

  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={state === 'loading' || busy !== null}>
    <h2 id={`${id}-title`}>{t('learning.signals.title')}</h2>
    <p className="muted">{t('learning.signals.text')}</p>
    <form className="learningSearch" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied(query.trim()); }}>
      <select aria-label={t('learning.signals.filter')} value={kind} onChange={(e) => { setOffset(0); setKind(e.target.value); }}>
        <option value="">{t('learning.signals.all')}</option>{SIGNAL_KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
      </select>
      <input type="search" value={query} maxLength={200} aria-label={t('learning.signals.search')} placeholder={t('learning.signals.search')} onChange={(e) => setQuery(e.target.value)} dir="auto" />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
    </form>
    {state === 'loading' && items.length === 0 && <p className="muted" role="status">{t('common.loading')}</p>}
    {state === 'error' && <div role="alert"><p className="notice errorNotice"><CircleDot aria-hidden="true" />{error}</p><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>}
    {state === 'ready' && items.length === 0 && <p className="muted learningEmpty">{applied || kind ? t('learning.signals.noMatch') : t('learning.signals.empty')}</p>}
    {items.length > 0 && <ul className="learningList">{items.map((signal) => {
      const full = open[signal.id], edit = editLine(signal);
      return <li key={signal.id} className="learningSignal">
        <div className="learningRow">
          <div>
            <p><strong>{kindLabel(signal.kind)}</strong> · {surfaceLabel(signal.surface)}{signal.tool ? <> · <code dir="ltr">{signal.tool}</code></> : null}{signal.contact ? <> · {t('learning.signals.contact', { contact: signal.contact })}</> : null}</p>
            <small>{formatDateTime(signal.created_at)}{edit ? ` · ${edit}` : ''}</small>
            {signal.reason && <p className="muted" dir="auto">{t('learning.signals.reason', { reason: signal.reason })}</p>}
          </div>
          <div className="rowActions">
            {(signal.original_text || signal.final_text) && <button type="button" className="secondary" aria-expanded={Boolean(full)} disabled={busy === signal.id} onClick={() => void toggle(signal)}>{full ? t('learning.signals.hideFull') : t('learning.signals.showFull')}</button>}
            <button type="button" className="iconButton" aria-label={t('learning.signals.deleteNamed', { kind: kindLabel(signal.kind) })} disabled={busy === signal.id} onClick={() => void remove(signal)}><Trash2 aria-hidden="true" /></button>
          </div>
        </div>
        {full && <div className="learningTexts"><SignalText label={t('learning.signals.before')} text={full.original_text} /><SignalText label={t('learning.signals.after')} text={full.final_text} /></div>}
      </li>;
    })}</ul>}
    <Pager total={total} offset={offset} onPage={setOffset} label={t('learning.signals.pages')} />
    <Note outcome={outcome} />
  </section>;
}
