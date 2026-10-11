// Activity (everything ANUM did on its own, with Undo and "Always ask for this") and Autonomy
// (levels, thresholds, guardrails, rules). docs/autonomy.md. Everything shown comes from the API;
// who may change what comes from the API's own answer (can_manage_*), never from the token.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Ban, Bell, CircleDot, Hand, History, Lock, RefreshCw, RotateCcw, Save, Search, ShieldCheck, Sparkles, Trash2, Undo2, X } from 'lucide-react';
import type { AutonomousAction, AutonomyLevel, AutonomyOverview, AutonomyProposal, AutonomyRule, ToolCategory } from '@anum/contracts';
import * as autonomy from './lib/autonomyApi';
import {
  CATEGORIES, EMPTY_FILTERS, LEVELS, canUndoNow, categoryLabel, filtersActive, floorWarnings, guardrailsBody, guardrailsDraft,
  kindLabel, levelChoices, levelLabel, patternLabel, proposalSentence, settingsBody, settingsDraft, undoStatus,
  type ActionNotice, type FeedFilters, type GuardrailsDraft, type QuietDraft, type SettingsDraft,
} from './lib/autonomy';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import './autonomy.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function ReadOnly({ title, children }: { title: string; children: ReactNode }) {
  return <section className="surface autonomyReadOnly" aria-label={title}><Lock aria-hidden="true" /><div><h3>{title}</h3><p>{children}</p></div></section>;
}

/** A labelled control: an explicit <label for>, and help text tied to the control with aria-describedby. */
function Field({ label, help, className = '', children }: { label: string; help?: string; className?: string; children: (id: string, describedBy: string | undefined) => ReactNode }) {
  const id = useId();
  const helpId = help ? `${id}-help` : undefined;
  return <div className={`field autonomyField ${className}`}><label htmlFor={id}>{label}</label>{children(id, helpId)}{help && <small id={helpId} className="muted">{help}</small>}</div>;
}

const levelTone = (level: AutonomyLevel) => level === 'ask_always' ? 'warning' : level === 'act_silently' ? 'neutral' : 'success';

// ------------------------------------------------------------------------- Activity

export function ActivityView({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [filters, setFilters] = useState<FeedFilters>(EMPTY_FILTERS);
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AutonomousAction[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [more, setMore] = useState(false);
  const [proposals, setProposals] = useState<AutonomyProposal[]>([]);
  const [overview, setOverview] = useState<AutonomyOverview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});

  const refresh = useCallback(async (next: FeedFilters) => {
    setLoad({ kind: 'loading' });
    try {
      const [page, open, info] = await Promise.all([
        autonomy.getActions(next, null),
        autonomy.getProposals().catch(() => []),
        autonomy.getAutonomy().catch(() => null),
      ]);
      setItems(page.items); setCursor(page.nextCursor); setProposals(open); setOverview(info); setRowError({});
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(filters); }, [refresh, filters]);

  const loadMore = async () => {
    if (!cursor) return;
    setMore(true);
    try {
      const page = await autonomy.getActions(filters, cursor);
      setItems((old) => [...old, ...page.items]); setCursor(page.nextCursor);
    } catch (error) {
      setFlash({ text: describeError(error), error: true });
    } finally { setMore(false); }
  };

  const replace = (next: AutonomousAction) => setItems((old) => old.map((item) => item.id === next.id ? next : item));
  const undo = async (action: AutonomousAction) => {
    setBusy(action.id); setFlash(null);
    try {
      replace(await autonomy.undoAction(action));
      setRowError(({ [action.id]: _, ...rest }) => rest);
      setFlash({ text: t('activity.undone', { summary: action.summary }) });
    } catch (error) {
      setRowError((old) => ({ ...old, [action.id]: describeError(error) }));
    } finally { setBusy(null); }
  };
  const tighten = async (action: AutonomousAction) => {
    setBusy(action.id); setFlash(null);
    try {
      await autonomy.tightenAction(action.id);
      setItems((old) => old.map((item) => item.patternKey === action.patternKey ? { ...item, patternLevel: 'ask_always' } : item));
      setFlash({ text: t('activity.tightened', { pattern: patternLabel(action.tool, action.template) }) });
    } catch (error) {
      setRowError((old) => ({ ...old, [action.id]: describeError(error) }));
    } finally { setBusy(null); }
  };
  const decide = async (proposal: AutonomyProposal, decision: 'accept' | 'decline') => {
    setBusy(proposal.id); setFlash(null);
    try {
      await autonomy.decideProposal(proposal.id, decision);
      setProposals((old) => old.filter((p) => p.id !== proposal.id));
      setFlash({ text: t(decision === 'accept' ? 'activity.proposalAccepted' : 'activity.proposalDeclined', { pattern: patternLabel(proposal.tool, proposal.template) }) });
    } catch (error) {
      setFlash({ text: describeError(error), error: true });
    } finally { setBusy(null); }
  };

  const submitSearch = (event: FormEvent) => { event.preventDefault(); setFilters({ ...filters, q: query }); };
  const owner = overview?.canManageWorkspace ?? false;
  const canTighten = overview?.canTighten ?? true;
  const now = Date.now();

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('activity.eyebrow')}</p><h2>{t('activity.title')}</h2><p>{t('activity.intro')}</p></div>
      <div className="actions">
        <button type="button" className="secondary" onClick={onOpenSettings}><ShieldCheck aria-hidden="true" />{t('activity.openSettings')}</button>
        <button type="button" onClick={() => void refresh(filters)}><RefreshCw aria-hidden="true" />{t('shell.refresh')}</button>
      </div>
    </section>

    {proposals.length > 0 && <section className="surface autonomyProposals" aria-labelledby="proposals-title">
      <div className="sectionHeader"><h2 id="proposals-title"><Sparkles aria-hidden="true" />{t('activity.proposalsTitle')}</h2></div>
      <p className="muted">{t('activity.proposalsIntro')}</p>
      <ul className="autonomyList">{proposals.map((p) => <li key={p.id} className="autonomyRow">
        <div className="autonomyRowText"><strong>{proposalSentence(p)}</strong><small>{categoryLabel(p.category)} · {kindLabel(p.actionKind)}</small></div>
        {owner ? <div className="rowActions">
          <button type="button" disabled={busy === p.id} onClick={() => void decide(p, 'accept')}>{t('activity.accept')}</button>
          <button type="button" className="secondary" disabled={busy === p.id} onClick={() => void decide(p, 'decline')}>{t('activity.keepAsking')}</button>
        </div> : <small className="muted">{t('activity.ownerDecides')}</small>}
      </li>)}</ul>
    </section>}

    <section className="surface autonomyFeed" aria-labelledby="feed-title">
      <div className="sectionHeader"><h2 id="feed-title"><History aria-hidden="true" />{t('activity.feedTitle')}</h2></div>
      <form className="autonomyFilters" onSubmit={submitSearch} role="search" aria-label={t('activity.filtersLabel')}>
        <Field label={t('activity.filter.category')}>{(id) =>
          <select id={id} value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value as FeedFilters['category'] })}>
            <option value="">{t('activity.filter.all')}</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}
          </select>}</Field>
        <Field label={t('activity.filter.level')}>{(id) =>
          <select id={id} value={filters.level} onChange={(e) => setFilters({ ...filters, level: e.target.value as FeedFilters['level'] })}>
            <option value="">{t('activity.filter.all')}</option>
            <option value="act_and_notify">{levelLabel('act_and_notify')}</option>
            <option value="act_silently">{levelLabel('act_silently')}</option>
          </select>}</Field>
        <Field label={t('activity.filter.status')}>{(id) =>
          <select id={id} value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value as FeedFilters['status'] })}>
            <option value="">{t('activity.filter.all')}</option>
            <option value="done">{t('activity.status.done')}</option>
            <option value="undone">{t('activity.status.undone')}</option>
          </select>}</Field>
        <Field label={t('common.search')} className="autonomySearch">{(id) =>
          <span className="autonomySearchBox"><input id={id} type="search" value={query} maxLength={200} placeholder={t('activity.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} />
            <button type="submit" className="secondary" aria-label={t('activity.searchButton')}><Search aria-hidden="true" /></button></span>}</Field>
      </form>
      {filtersActive(filters) && <div className="actions autonomyClear"><button type="button" className="secondary" onClick={() => { setQuery(''); setFilters(EMPTY_FILTERS); }}><X aria-hidden="true" />{t('activity.clearFilters')}</button></div>}

      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      {load.kind === 'loading' && <Notice>{t('activity.loading')}</Notice>}
      {load.kind === 'error' && <><Notice error>{t('activity.loadFailed', { detail: load.detail })}</Notice><div className="actions"><button type="button" onClick={() => void refresh(filters)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></>}
      {load.kind === 'forbidden' && <ReadOnly title={t('activity.forbiddenTitle')}>{t('activity.forbidden', { detail: load.detail })}</ReadOnly>}
      {load.kind === 'ready' && items.length === 0 && (filtersActive(filters)
        ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('activity.noMatchTitle')}</h3><p>{t('activity.noMatch')}</p><div className="actions"><button type="button" className="secondary" onClick={() => { setQuery(''); setFilters(EMPTY_FILTERS); }}>{t('activity.clearFilters')}</button></div></div>
        : <div className="emptyState"><Bell aria-hidden="true" /><h3>{t('activity.emptyTitle')}</h3><p>{t('activity.empty')}</p><div className="actions"><button type="button" onClick={onOpenSettings}><ShieldCheck aria-hidden="true" />{t('activity.openSettings')}</button></div></div>)}
      {load.kind === 'ready' && items.length > 0 && <ul className="autonomyList" aria-label={t('activity.feedTitle')}>{items.map((action) => {
        const status = undoStatus(action, now);
        const asks = action.patternLevel === 'ask_always';
        return <li key={action.id} className={`autonomyRow ${action.status === 'undone' ? 'undone' : ''}`}>
          <div className="autonomyRowText">
            <strong>{action.summary}</strong>
            <small>{categoryLabel(action.category)} · <code>{action.tool}</code>{action.template ? ` · ${action.template}` : ''} · {formatDateTime(action.createdAt)}</small>
            <span className="autonomyBadges">
              <span className={`statusBadge ${levelTone(action.level)}`}>{levelLabel(action.level)}</span>
              {status && <span className={`statusBadge ${status.tone}`}>{status.text}</span>}
              {asks && <span className="statusBadge warning">{t('activity.asksNow')}</span>}
            </span>
            {action.undoError && action.status === 'done' && <small className="autonomyRowError">{t('activity.lastUndoFailed', { detail: action.undoError })}</small>}
            {rowError[action.id] && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{rowError[action.id]}</p>}
          </div>
          <div className="rowActions">
            {canUndoNow(action, now) && <button type="button" disabled={busy === action.id} onClick={() => void undo(action)} aria-label={t('activity.undoLabel', { summary: action.summary })}><Undo2 aria-hidden="true" />{t('activity.undo')}</button>}
            {action.reversible && action.status === 'done' && !action.canUndo && <small className="muted">{t('activity.undoWho')}</small>}
            {canTighten && !asks && <button type="button" className="secondary" disabled={busy === action.id} onClick={() => void tighten(action)} aria-label={t('activity.tightenLabel', { pattern: patternLabel(action.tool, action.template) })}><Hand aria-hidden="true" />{t('activity.tighten')}</button>}
          </div>
        </li>;
      })}</ul>}
      {load.kind === 'ready' && cursor && <div className="actions"><button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('common.loading') : t('activity.loadMore')}</button></div>}
    </section>
  </>;
}

// ------------------------------------------------------------------------- Notifications

/** "ANUM acted: ..." notifications from `autonomy.action_taken` events, with Undo while it is open. */
export function ActionNotices({ notices, onDismiss, onOpenActivity }: { notices: ActionNotice[]; onDismiss: (id: string) => void; onOpenActivity: () => void }) {
  const [state, setState] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const timers = useRef<number[]>([]);
  useEffect(() => () => { for (const timer of timers.current) clearTimeout(timer); }, []);
  const undo = async (notice: ActionNotice) => {
    setBusy(notice.actionId);
    try {
      const action = await autonomy.getAction(notice.actionId);
      if (!canUndoNow(action)) { setState((old) => ({ ...old, [notice.actionId]: action.canUndo ? t('activity.undoClosed') : t('activity.undoWho') })); return; }
      await autonomy.undoAction(action);
      setState((old) => ({ ...old, [notice.actionId]: t('activity.undoneShort') }));
      timers.current.push(window.setTimeout(() => onDismiss(notice.actionId), 4000));
    } catch (error) {
      setState((old) => ({ ...old, [notice.actionId]: describeError(error) }));
    } finally { setBusy(null); }
  };
  if (notices.length === 0) return null;
  return <div className="actionNotices" role="region" aria-label={t('activity.noticesLabel')}>
    {notices.map((notice) => {
      const open = notice.undoUntil !== null && new Date(notice.undoUntil).getTime() > Date.now();
      return <div key={notice.actionId} className="actionNotice surface" role="status" aria-live="polite">
        <Bell aria-hidden="true" />
        <div><strong>{t('activity.noticeTitle')}</strong><p>{notice.summary || notice.tool}</p>{state[notice.actionId] && <small>{state[notice.actionId]}</small>}</div>
        <div className="actionNoticeButtons">
          {open && !state[notice.actionId] && <button type="button" disabled={busy === notice.actionId} onClick={() => void undo(notice)}><Undo2 aria-hidden="true" />{t('activity.undo')}</button>}
          <button type="button" className="secondary" onClick={onOpenActivity}>{t('activity.view')}</button>
          <button type="button" className="secondary iconOnly" aria-label={t('activity.dismiss')} onClick={() => onDismiss(notice.actionId)}><X aria-hidden="true" /></button>
        </div>
      </div>;
    })}
  </div>;
}

// ------------------------------------------------------------------------- Autonomy settings

function QuietFields({ value, onChange, disabled, idPrefix }: { value: QuietDraft; onChange: (next: QuietDraft) => void; disabled: boolean; idPrefix: string }) {
  return <fieldset className="autonomyFieldset" disabled={disabled}>
    <legend>{t('autonomy.quiet.title')}</legend>
    <label className="settingToggle"><span><strong>{t('autonomy.quiet.enable')}</strong><small className="muted">{t('autonomy.quiet.help')}</small></span>
      <input type="checkbox" checked={value.enabled} onChange={(e) => onChange({ ...value, enabled: e.target.checked })} /></label>
    {value.enabled && <div className="autonomyGrid">
      <Field label={t('autonomy.quiet.start')}>{(id) => <input id={id} type="time" value={value.start} onChange={(e) => onChange({ ...value, start: e.target.value })} />}</Field>
      <Field label={t('autonomy.quiet.end')}>{(id) => <input id={id} type="time" value={value.end} onChange={(e) => onChange({ ...value, end: e.target.value })} />}</Field>
      <Field label={t('autonomy.quiet.timezone')}>{(id) => <input id={id} value={value.timezone} placeholder="Asia/Riyadh" onChange={(e) => onChange({ ...value, timezone: e.target.value })} />}</Field>
    </div>}
  </fieldset>;
}

function NumberField({ label, help, value, onChange, disabled, step = 'any' }: { label: string; help?: string; value: string; onChange: (v: string) => void; disabled: boolean; step?: string }) {
  return <Field label={label} help={help}>{(id, described) => <input id={id} aria-describedby={described} type="number" min={0} step={step} inputMode="decimal" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />}</Field>;
}

function LinesField({ label, help, value, onChange, disabled, rows = 3 }: { label: string; help: string; value: string; onChange: (v: string) => void; disabled: boolean; rows?: number }) {
  return <Field label={label} help={help}>{(id, described) => <textarea id={id} aria-describedby={described} rows={rows} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />}</Field>;
}

export function AutonomySettingsView({ onOpenActivity }: { onOpenActivity: () => void }) {
  const [overview, setOverview] = useState<AutonomyOverview | null>(null);
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [guard, setGuard] = useState<GuardrailsDraft | null>(null);
  const [rules, setRules] = useState<AutonomyRule[]>([]);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [busy, setBusy] = useState<'settings' | 'guardrails' | 'rule' | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [guardFlash, setGuardFlash] = useState<Flash>(null);
  const [ruleFlash, setRuleFlash] = useState<Flash>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [newRule, setNewRule] = useState<{ tool: string; template: string; level: AutonomyLevel }>({ tool: '', template: '', level: 'ask_always' });

  const refresh = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try {
      const [info, list] = await Promise.all([autonomy.getAutonomy(), autonomy.getRules()]);
      setOverview(info); setDraft(settingsDraft(info.settings)); setGuard(guardrailsDraft(info.guardrails)); setRules(list);
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  if (load.kind === 'loading') return <Notice>{t('autonomy.loading')}</Notice>;
  if (load.kind === 'error') return <><Notice error>{t('autonomy.loadFailed', { detail: load.detail })}</Notice><div className="actions"><button type="button" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></>;
  if (load.kind === 'forbidden') return <ReadOnly title={t('activity.forbiddenTitle')}>{t('activity.forbidden', { detail: load.detail })}</ReadOnly>;
  if (!overview || !draft || !guard) return null;

  const owner = overview.canManageWorkspace && denied === null;
  const admin = overview.canManageGuardrails;
  const set = (patch: Partial<SettingsDraft>) => setDraft({ ...draft, ...patch });
  const setG = (patch: Partial<GuardrailsDraft>) => setGuard({ ...guard, ...patch });
  const warnings = floorWarnings(draft, overview.guardrails);
  const protectedKinds = overview.recommendations.filter((r) => r.protected);

  const save = async () => {
    const body = settingsBody(draft);
    if (!body.ok) { setFlash({ text: body.error, error: true }); return; }
    setBusy('settings'); setFlash(null);
    try {
      const next = await autonomy.saveAutonomy(body.value);
      setOverview(next); setDraft(settingsDraft(next.settings));
      setFlash({ text: t('autonomy.saved') });
    } catch (error) {
      if (isPermissionDenied(error)) { setDenied(describeError(error)); setDraft(settingsDraft(overview.settings)); }
      else setFlash({ text: describeError(error), error: true });
    } finally { setBusy(null); }
  };
  const saveGuard = async () => {
    const body = guardrailsBody(guard);
    if (!body.ok) { setGuardFlash({ text: body.error, error: true }); return; }
    setBusy('guardrails'); setGuardFlash(null);
    try {
      const next = await autonomy.saveGuardrails(body.value);
      setOverview({ ...overview, guardrails: next }); setGuard(guardrailsDraft(next));
      setGuardFlash({ text: t('autonomy.guardrailsSaved') });
    } catch (error) {
      setGuardFlash({ text: describeError(error), error: true });
    } finally { setBusy(null); }
  };
  const addRule = async (event: FormEvent) => {
    event.preventDefault();
    if (!newRule.tool.trim()) { setRuleFlash({ text: t('autonomy.rules.toolRequired'), error: true }); return; }
    setBusy('rule'); setRuleFlash(null);
    try {
      const rule = await autonomy.createRule(newRule.tool.trim(), newRule.template.trim() || null, newRule.level);
      setRules((old) => [...old.filter((r) => r.patternKey !== rule.patternKey), rule]);
      setNewRule({ tool: '', template: '', level: 'ask_always' });
      setRuleFlash({ text: t('autonomy.rules.added', { pattern: patternLabel(rule.tool, rule.template) }) });
    } catch (error) {
      setRuleFlash({ text: describeError(error), error: true });
    } finally { setBusy(null); }
  };
  const removeRule = async (rule: AutonomyRule) => {
    setBusy('rule'); setRuleFlash(null);
    try {
      await autonomy.deleteRule(rule.id);
      setRules((old) => old.filter((r) => r.id !== rule.id));
      setRuleFlash({ text: t('autonomy.rules.removed', { pattern: patternLabel(rule.tool, rule.template) }) });
    } catch (error) {
      setRuleFlash({ text: describeError(error), error: true });
    } finally { setBusy(null); }
  };

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('autonomy.eyebrow')}</p><h2>{t('autonomy.title')}</h2><p>{t('autonomy.intro')}</p></div>
      <div className="actions">
        <button type="button" className="secondary" onClick={onOpenActivity}><History aria-hidden="true" />{t('autonomy.openActivity', { count: overview.pendingProposals })}</button>
        <button type="button" onClick={() => { setDenied(null); setFlash(null); void refresh(); }}><RefreshCw aria-hidden="true" />{t('shell.refresh')}</button>
      </div>
    </section>
    {!owner && <ReadOnly title={t('autonomy.ownerRequiredTitle')}>{denied ?? t('autonomy.ownerRequired')}</ReadOnly>}

    <div className="autonomyLayout">
      <section className="surface autonomyPanel" aria-labelledby="levels-title">
        <div className="sectionHeader"><h2 id="levels-title">{t('autonomy.levels.title')}</h2></div>
        <p className="muted">{t('autonomy.levels.help')}</p>
        <div className="autonomyLevels">{CATEGORIES.map((category) => {
          const view = overview.categories.find((c) => c.category === category);
          const ceiling = view?.ceiling ?? null;
          return <Field key={category} label={categoryLabel(category)} className="autonomyLevel" help={ceiling ? t('autonomy.levels.ceiling', { level: levelLabel(ceiling) }) : undefined}>{(id, described) =>
            <select id={id} aria-describedby={described} value={draft.categoryLevels[category]} disabled={!owner} onChange={(e) => set({ categoryLevels: { ...draft.categoryLevels, [category]: e.target.value as AutonomyLevel } })}>
              {levelChoices(ceiling).map(({ level, allowed }) => <option key={level} value={level} disabled={!allowed}>{levelLabel(level)}</option>)}
            </select>}</Field>;
        })}</div>
        <div className="autonomyProtected">
          <h3><Ban aria-hidden="true" />{t('autonomy.protected.title')}</h3>
          <p className="muted">{t('autonomy.protected.help')}</p>
          <ul>{protectedKinds.map((r) => <li key={r.kind}>{kindLabel(r.kind)}</li>)}<li>{t('autonomy.protected.firstContact')}</li><li>{t('autonomy.protected.bulk')}</li></ul>
        </div>
      </section>

      <section className="surface autonomyPanel" aria-labelledby="thresholds-title">
        <div className="sectionHeader"><h2 id="thresholds-title">{t('autonomy.thresholds.title')}</h2></div>
        <p className="muted">{t('autonomy.thresholds.help')}</p>
        <div className="autonomyGrid">
          <NumberField label={t('autonomy.field.amountThreshold')} value={draft.amountThreshold} disabled={!owner} onChange={(v) => set({ amountThreshold: v })}
            help={overview.guardrails.maxAutoAmount !== null ? t('autonomy.orgMax', { max: overview.guardrails.maxAutoAmount }) : undefined} />
          <NumberField label={t('autonomy.field.recipientsThreshold')} step="1" value={draft.recipientsThreshold} disabled={!owner} onChange={(v) => set({ recipientsThreshold: v })}
            help={overview.guardrails.maxAutoRecipients !== null ? t('autonomy.orgMax', { max: overview.guardrails.maxAutoRecipients }) : undefined} />
          <NumberField label={t('autonomy.field.priceThreshold')} value={draft.priceChangePercentThreshold} disabled={!owner} onChange={(v) => set({ priceChangePercentThreshold: v })}
            help={overview.guardrails.maxAutoPriceChangePercent !== null ? t('autonomy.orgMax', { max: overview.guardrails.maxAutoPriceChangePercent }) : undefined} />
          <NumberField label={t('autonomy.field.proposalAfter')} step="1" value={draft.proposalAfter} disabled={!owner} onChange={(v) => set({ proposalAfter: v })} help={t('autonomy.field.proposalAfterHelp')} />
        </div>
        <LinesField label={t('autonomy.field.templates')} help={t('autonomy.field.templatesHelp')} value={draft.approvedTemplates} disabled={!owner} onChange={(v) => set({ approvedTemplates: v })} />
        <LinesField label={t('autonomy.field.caps')} help={t('autonomy.field.capsHelp')} value={draft.dailyCaps} disabled={!owner} onChange={(v) => set({ dailyCaps: v })} />
        <QuietFields idPrefix="ws-quiet" value={draft.quiet} disabled={!owner} onChange={(quiet) => set({ quiet })} />
        <div className="autonomyGrid">
          <LinesField label={t('autonomy.field.blockedRecipients')} help={t('autonomy.field.blockedRecipientsHelp')} value={draft.blockedRecipients} disabled={!owner} onChange={(v) => set({ blockedRecipients: v })} />
          <LinesField label={t('autonomy.field.blockedDomains')} help={t('autonomy.field.blockedDomainsHelp')} value={draft.blockedDomains} disabled={!owner} onChange={(v) => set({ blockedDomains: v })} />
        </div>
        {warnings.length > 0 && <Notice error>{warnings.join(' ')}</Notice>}
        {flash && <Notice error={flash.error}>{flash.text}</Notice>}
        {owner && <div className="actions">
          <button type="button" disabled={busy !== null} onClick={() => void save()}><Save aria-hidden="true" />{busy === 'settings' ? t('autonomy.saving') : t('autonomy.save')}</button>
          <button type="button" className="secondary" disabled={busy !== null} onClick={() => { setDraft(settingsDraft(overview.settings)); setFlash(null); }}><RotateCcw aria-hidden="true" />{t('autonomy.reset')}</button>
        </div>}
        {overview.settings.updatedAt && <p className="muted">{t('autonomy.lastChanged', { when: formatDateTime(overview.settings.updatedAt), who: overview.settings.updatedBy ?? '?' })}</p>}
      </section>

      <section className="surface autonomyPanel" aria-labelledby="rules-title">
        <div className="sectionHeader"><h2 id="rules-title">{t('autonomy.rules.title')}</h2></div>
        <p className="muted">{t('autonomy.rules.help')}</p>
        {rules.length === 0
          ? <p className="muted autonomyEmptyLine">{t('autonomy.rules.empty')}</p>
          : <ul className="autonomyList">{rules.map((rule) => <li key={rule.id} className="autonomyRow">
            <div className="autonomyRowText"><strong><code>{patternLabel(rule.tool, rule.template)}</code></strong>
              <small>{categoryLabel(rule.category)} · {t(`autonomy.rules.source.${rule.source}`)} · {rule.createdBy} · {formatDateTime(rule.createdAt)}</small>
              <span className="autonomyBadges"><span className={`statusBadge ${levelTone(rule.level)}`}>{levelLabel(rule.level)}</span></span></div>
            {owner && <div className="rowActions"><button type="button" className="secondary" disabled={busy !== null} onClick={() => void removeRule(rule)} aria-label={t('autonomy.rules.removeLabel', { pattern: patternLabel(rule.tool, rule.template) })}><Trash2 aria-hidden="true" />{t('autonomy.rules.remove')}</button></div>}
          </li>)}</ul>}
        {overview.canTighten && <form className="autonomyRuleForm" onSubmit={(e) => void addRule(e)} aria-label={t('autonomy.rules.addLabel')}>
          <Field label={t('autonomy.rules.tool')}>{(id) => <input id={id} value={newRule.tool} maxLength={160} placeholder="whatsapp.send_template" onChange={(e) => setNewRule({ ...newRule, tool: e.target.value })} />}</Field>
          <Field label={t('autonomy.rules.template')}>{(id) => <input id={id} value={newRule.template} maxLength={120} placeholder="order_status" onChange={(e) => setNewRule({ ...newRule, template: e.target.value })} />}</Field>
          <Field label={t('autonomy.rules.level')}>{(id) => <select id={id} value={newRule.level} onChange={(e) => setNewRule({ ...newRule, level: e.target.value as AutonomyLevel })}>
            {LEVELS.map((level) => <option key={level} value={level} disabled={!owner && level !== 'ask_always'}>{levelLabel(level)}</option>)}</select>}</Field>
          <div className="actions"><button type="submit" disabled={busy !== null}>{t('autonomy.rules.add')}</button></div>
        </form>}
        {!owner && overview.canTighten && <p className="muted">{t('autonomy.rules.memberHelp')}</p>}
        {ruleFlash && <Notice error={ruleFlash.error}>{ruleFlash.text}</Notice>}
      </section>

      <section className="surface autonomyPanel" aria-labelledby="guardrails-title">
        <div className="sectionHeader"><h2 id="guardrails-title">{t('autonomy.guardrails.title')}</h2></div>
        <p className="muted">{t('autonomy.guardrails.help')}</p>
        {!admin && <ReadOnly title={t('autonomy.guardrails.readOnlyTitle')}>{t('autonomy.guardrails.readOnly')}</ReadOnly>}
        <div className="autonomyGrid">
          <NumberField label={t('autonomy.field.spendingLimit')} help={t('autonomy.field.spendingLimitHelp')} value={guard.spendingLimit} disabled={!admin} onChange={(v) => setG({ spendingLimit: v })} />
          <NumberField label={t('autonomy.field.maxAutoAmount')} value={guard.maxAutoAmount} disabled={!admin} onChange={(v) => setG({ maxAutoAmount: v })} />
          <NumberField label={t('autonomy.field.maxAutoRecipients')} step="1" value={guard.maxAutoRecipients} disabled={!admin} onChange={(v) => setG({ maxAutoRecipients: v })} />
          <NumberField label={t('autonomy.field.maxAutoPrice')} value={guard.maxAutoPriceChangePercent} disabled={!admin} onChange={(v) => setG({ maxAutoPriceChangePercent: v })} />
        </div>
        <div className="autonomyLevels">{CATEGORIES.map((category) => <Field key={category} className="autonomyLevel" label={t('autonomy.guardrails.ceiling', { category: categoryLabel(category) })}>{(id) =>
          <select id={id} value={guard.categoryCeilings[category]} disabled={!admin} onChange={(e) => setG({ categoryCeilings: { ...guard.categoryCeilings, [category]: e.target.value as AutonomyLevel | '' } as Record<ToolCategory, AutonomyLevel | ''> })}>
            <option value="">{t('autonomy.guardrails.noCeiling')}</option>
            {LEVELS.map((level) => <option key={level} value={level}>{levelLabel(level)}</option>)}
          </select>}</Field>)}</div>
        <LinesField label={t('autonomy.field.caps')} help={t('autonomy.field.capsHelp')} value={guard.dailyCaps} disabled={!admin} onChange={(v) => setG({ dailyCaps: v })} />
        <QuietFields idPrefix="org-quiet" value={guard.quiet} disabled={!admin} onChange={(quiet) => setG({ quiet })} />
        <div className="autonomyGrid">
          <LinesField label={t('autonomy.field.blockedRecipients')} help={t('autonomy.field.blockedRecipientsHelp')} value={guard.blockedRecipients} disabled={!admin} onChange={(v) => setG({ blockedRecipients: v })} />
          <LinesField label={t('autonomy.field.blockedDomains')} help={t('autonomy.field.blockedDomainsHelp')} value={guard.blockedDomains} disabled={!admin} onChange={(v) => setG({ blockedDomains: v })} />
        </div>
        {guardFlash && <Notice error={guardFlash.error}>{guardFlash.text}</Notice>}
        {admin && <div className="actions"><button type="button" disabled={busy !== null} onClick={() => void saveGuard()}><Save aria-hidden="true" />{busy === 'guardrails' ? t('autonomy.saving') : t('autonomy.guardrails.save')}</button></div>}
      </section>
    </div>
  </>;
}
