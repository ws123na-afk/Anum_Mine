import { useEffect, useId, useState } from 'react';
import { Copy, KeyRound, Pause, Pencil, Play, Plus, RefreshCw, Trash2, UserCheck, X, Zap } from 'lucide-react';
import { apiBaseUrl } from './lib/api';
import * as automationApi from './lib/automationApi';
import {
  CONDITION_OPERATORS, MAX_CONDITIONS, automationError, canEdit, canManage, canTakeOver, checkTriggerDraft, conditionText, emptyTriggerDraft, eventLabel,
  newCondition, relativeTime, signingExample, triggerDraftFrom, triggerOutcomeLabel, webhookUrl,
  type AutomationTrigger, type AutomationWorkflow, type ConditionOperator, type FieldErrors, type TriggerDraft, type TriggerEventInfo, type TriggerKind, type Viewer,
} from './lib/automation';
import { t } from './lib/i18n';

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string };
interface Revealed { triggerId: string; name: string; secret: string; url: string }

/**
 * Event and webhook triggers of one automation (docs/automation.md#triggers): list, create,
 * edit, turn on or off, delete, rotate a webhook's secret (shown once) and take over another
 * member's trigger (owners). Viewers see the triggers and why they cannot change them.
 */
export function AutomationTriggers({ workflow, viewer, tick, onChanged }: { workflow: AutomationWorkflow; viewer: Viewer; tick: number; onChanged(message: string): void }) {
  const [load, setLoad] = useState<Load<AutomationTrigger[]>>({ state: 'loading' });
  const [events, setEvents] = useState<TriggerEventInfo[]>([]);
  const [editing, setEditing] = useState<AutomationTrigger | TriggerKind | null>(null);
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [localTick, setLocalTick] = useState(0);
  useEffect(() => {
    let live = true;
    automationApi.listTriggers(workflow.id).then((page) => { if (live) setLoad({ state: 'ready', value: page.items }); })
      .catch((e) => { if (live) setLoad({ state: 'error', message: automationError(e) }); });
    return () => { live = false; };
  }, [workflow.id, tick, localTick]);
  useEffect(() => { void automationApi.listTriggerEvents().then(setEvents).catch(() => setEvents([])); }, []);
  const manage = canManage(viewer);
  const reveal = (trigger: AutomationTrigger, secret: string | null | undefined) => {
    if (secret) setRevealed({ triggerId: trigger.id, name: trigger.name, secret, url: webhookUrl(apiBaseUrl, trigger.webhook_path) });
  };
  async function act(operation: () => Promise<unknown>, message: string) {
    setBusy(true); setError(null);
    try { await operation(); setLocalTick((n) => n + 1); onChanged(message); } catch (e) { setError(automationError(e)); } finally { setBusy(false); }
  }

  return <section className="surface autoPanel" aria-labelledby={`triggers-${workflow.id}`}>
    <div className="sectionHeader"><h2 id={`triggers-${workflow.id}`}>{t('auto.trig.title')}</h2>
      {manage && editing === null && <div className="actions autoHeaderActions">
        <button type="button" className="secondary" onClick={() => setEditing('event')}><Zap aria-hidden="true" />{t('auto.trig.addEvent')}</button>
        <button type="button" className="secondary" onClick={() => setEditing('webhook')}><KeyRound aria-hidden="true" />{t('auto.trig.addWebhook')}</button>
      </div>}
    </div>
    <p className="muted">{t('auto.trig.help')}</p>
    {revealed && <SecretPanel revealed={revealed} onDone={() => setRevealed(null)} />}
    {editing !== null && <TriggerForm key={typeof editing === 'string' ? editing : editing.id} workflowId={workflow.id} events={events}
      existing={typeof editing === 'string' ? undefined : editing} kind={typeof editing === 'string' ? editing : editing.kind}
      onCancel={() => setEditing(null)}
      onSaved={(saved, secret) => { setEditing(null); setLocalTick((n) => n + 1); reveal(saved, secret); onChanged(t(secret !== undefined ? 'auto.trig.added' : 'auto.trig.saved', { name: saved.name })); }} />}
    {error && <p className="notice errorNotice" role="alert">{error}</p>}
    {load.state === 'loading' && <p className="muted" role="status">{t('auto.trig.loading')}</p>}
    {load.state === 'error' && <><p className="notice errorNotice" role="alert">{t('auto.trig.loadFailed', { message: load.message })}</p>
      <div className="actions"><button type="button" className="secondary" onClick={() => setLocalTick((n) => n + 1)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></>}
    {load.state === 'ready' && load.value.length === 0 && editing === null && <div className="emptyState autoCompactEmpty"><Zap aria-hidden="true" /><h3>{t('auto.trig.emptyTitle')}</h3><p>{manage ? t('auto.trig.emptyManage') : t('auto.trig.emptyRead')}</p></div>}
    {load.state === 'ready' && load.value.length > 0 && <ul className="autoScheduleList" aria-label={t('auto.trig.title')}>{load.value.map((trigger) => {
      const editable = canEdit(viewer, trigger.created_by), outcome = triggerOutcomeLabel(trigger.last_outcome);
      const what = trigger.kind === 'webhook' ? t('auto.trig.kindWebhook') : eventLabel(trigger.event_type, events.find((e) => e.type === trigger.event_type)?.label);
      return <li key={trigger.id} className="autoSchedule">
        <div className="autoScheduleText">
          <strong dir="auto">{trigger.name}</strong>
          <span>{what}{trigger.conditions.length > 0 && <span className="muted"> · {trigger.conditions.map(conditionText).join(t('auto.trig.and'))}</span>}</span>
          {trigger.kind === 'webhook' && <small className="muted">{t('auto.trig.secretHint', { hint: trigger.secret_hint ?? '????' })} · <code dir="ltr" className="autoBreak">{trigger.webhook_path}</code></small>}
          <small className="muted">{[
            trigger.last_fired_at ? t('auto.trig.lastFired', { when: relativeTime(trigger.last_fired_at), count: trigger.fire_count }, { isolate: false }) : t('auto.trig.neverFired'),
            ...(outcome && trigger.last_outcome !== 'fired' ? [t('auto.trig.lastOutcome', { outcome })] : []),
            t('auto.trig.limit', { count: trigger.rate_limit_per_minute }),
            t('auto.sched.runsAs', { user: trigger.created_by ?? t('auto.fact.unknown') }),
          ].join(' · ')}</small>
        </div>
        <span className={`statusBadge ${trigger.enabled ? 'success' : 'neutral'}`}>{trigger.enabled ? t('auto.sched.on') : t('auto.sched.off')}</span>
        {manage && <div className="runActions">
          {editable && <>
            {trigger.enabled
              ? <button type="button" title={t('auto.sched.turnOff')} aria-label={t('auto.trig.turnOffNamed', { name: trigger.name })} disabled={busy} onClick={() => void act(() => automationApi.setTriggerEnabled(trigger.id, false), t('auto.trig.turnedOff', { name: trigger.name }))}><Pause aria-hidden="true" size={16} /></button>
              : <button type="button" title={t('auto.sched.turnOn')} aria-label={t('auto.trig.turnOnNamed', { name: trigger.name })} disabled={busy} onClick={() => void act(() => automationApi.setTriggerEnabled(trigger.id, true), t('auto.trig.turnedOn', { name: trigger.name }))}><Play aria-hidden="true" size={16} /></button>}
            <button type="button" title={t('auto.edit')} aria-label={t('auto.trig.editNamed', { name: trigger.name })} disabled={busy} onClick={() => setEditing(trigger)}><Pencil aria-hidden="true" size={16} /></button>
            {trigger.kind === 'webhook' && <button type="button" title={t('auto.trig.rotate')} aria-label={t('auto.trig.rotateNamed', { name: trigger.name })} disabled={busy} onClick={() => {
              if (!window.confirm(t('auto.trig.confirmRotate', { name: trigger.name }, { isolate: false }))) return;
              setBusy(true); setError(null);
              automationApi.rotateTriggerSecret(trigger.id).then((result) => { reveal(result.trigger, result.secret); setLocalTick((n) => n + 1); onChanged(t('auto.trig.rotated', { name: trigger.name })); })
                .catch((e) => setError(automationError(e))).finally(() => setBusy(false));
            }}><KeyRound aria-hidden="true" size={16} /></button>}
            <button type="button" title={t('auto.delete')} aria-label={t('auto.trig.deleteNamed', { name: trigger.name })} disabled={busy} onClick={() => { if (window.confirm(t('auto.trig.confirmDelete', { name: trigger.name }, { isolate: false }))) void act(() => automationApi.deleteTrigger(trigger.id), t('auto.trig.deleted', { name: trigger.name })); }}><Trash2 aria-hidden="true" size={16} /></button>
          </>}
          {canTakeOver(viewer, trigger.created_by) && <button type="button" title={t('auto.takeOver')} aria-label={t('auto.trig.takeOverNamed', { name: trigger.name })} disabled={busy} onClick={() => { if (window.confirm(t('auto.trig.confirmTakeOver', { name: trigger.name, user: trigger.created_by ?? '' }, { isolate: false }))) void act(() => automationApi.takeOverTrigger(trigger.id), t('auto.trig.takenOver', { name: trigger.name })); }}><UserCheck aria-hidden="true" size={16} /></button>}
        </div>}
      </li>;
    })}</ul>}
    {load.state === 'ready' && load.value.some((trigger) => !canEdit(viewer, trigger.created_by)) && manage && <p className="muted">{t('auto.trig.editHint')}</p>}
  </section>;
}

function SecretPanel({ revealed, onDone }: { revealed: Revealed; onDone(): void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (label: string, value: string) => { void navigator.clipboard?.writeText(value).then(() => setCopied(label)).catch(() => setCopied(null)); };
  return <div className="autoSecret" role="region" aria-label={t('auto.trig.secretTitle', { name: revealed.name })}>
    <p><strong>{t('auto.trig.secretTitle', { name: revealed.name })}</strong> {t('auto.trig.secretOnce')}</p>
    <div className="field"><span>{t('auto.trig.url')}</span><div className="autoCopyRow"><code dir="ltr" className="autoBreak">{revealed.url}</code>
      <button type="button" className="secondary" onClick={() => copy('url', revealed.url)}><Copy aria-hidden="true" size={16} />{t('auto.trig.copy')}</button></div></div>
    <div className="field"><span>{t('auto.trig.secret')}</span><div className="autoCopyRow"><code dir="ltr" className="autoBreak" data-testid="webhook-secret">{revealed.secret}</code>
      <button type="button" className="secondary" onClick={() => copy('secret', revealed.secret)}><Copy aria-hidden="true" size={16} />{t('auto.trig.copy')}</button></div></div>
    <details><summary>{t('auto.trig.howToSign')}</summary><p className="muted">{t('auto.trig.signHelp')}</p><pre dir="ltr" className="autoCode">{signingExample(revealed.url)}</pre></details>
    {copied && <p className="muted" role="status">{t('auto.trig.copied')}</p>}
    <div className="actions"><button type="button" onClick={onDone}>{t('auto.trig.savedSecret')}</button></div>
  </div>;
}

function TriggerForm({ workflowId, events, existing, kind, onCancel, onSaved }: {
  workflowId: string; events: TriggerEventInfo[]; existing?: AutomationTrigger; kind: TriggerKind; onCancel(): void; onSaved(saved: AutomationTrigger, secret?: string | null): void;
}) {
  const [draft, setDraft] = useState<TriggerDraft>(() => existing ? triggerDraftFrom(existing) : { ...emptyTriggerDraft(kind), eventType: events[0]?.type ?? 'email.received' });
  const [errors, setErrors] = useState<FieldErrors>({}), [summary, setSummary] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const base = useId(), info = events.find((e) => e.type === draft.eventType);
  const fieldsList = draft.kind === 'event' ? info?.fields ?? [] : ['body'];
  const patch = (key: string, change: Partial<TriggerDraft['conditions'][number]>) =>
    setDraft((d) => ({ ...d, conditions: d.conditions.map((c) => c.key === key ? { ...c, ...change } : c) }));

  async function save() {
    const checked = checkTriggerDraft(draft, events.map((e) => e.type));
    if (!checked.ok) { setErrors(checked.errors); setSummary(checked.summary); return; }
    setErrors({}); setSummary(null); setBusy(true);
    try {
      if (existing) onSaved(await automationApi.updateTrigger(existing.id, checked.body));
      else { const created = await automationApi.createTrigger({ ...checked.body, workflow_id: workflowId, kind: draft.kind }); onSaved(created.trigger, created.secret); }
    } catch (e) { setSummary(automationError(e)); } finally { setBusy(false); }
  }
  const err = (name: string) => errors[name] ? <small className="autoFieldError" id={`${base}-${name}-error`}>{errors[name]}</small> : null;
  const invalid = (name: string) => errors[name] ? { 'aria-invalid': true as const, 'aria-describedby': `${base}-${name}-error` } : {};

  return <form className="autoScheduleForm" aria-label={existing ? t('auto.trig.editNamed', { name: existing.name }) : draft.kind === 'webhook' ? t('auto.trig.addWebhook') : t('auto.trig.addEvent')} noValidate onSubmit={(e) => { e.preventDefault(); void save(); }}>
    <div className="wbScheduleForm">
      <div className="field"><label htmlFor={`${base}-name`}>{t('auto.trig.field.name')}</label><input id={`${base}-name`} {...invalid('name')} value={draft.name} maxLength={160} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />{err('name')}</div>
      {draft.kind === 'event' && <div className="field"><label htmlFor={`${base}-event`}>{t('auto.trig.field.event')}</label>
        <select id={`${base}-event`} {...invalid('eventType')} value={draft.eventType} onChange={(e) => setDraft({ ...draft, eventType: e.target.value })}>
          {events.length === 0 && <option value={draft.eventType}>{eventLabel(draft.eventType)}</option>}
          {events.map((e) => <option key={e.type} value={e.type}>{eventLabel(e.type, e.label)}</option>)}
        </select>{err('eventType')}</div>}
      <div className="field"><label htmlFor={`${base}-rate`}>{t('auto.trig.field.rate')}</label><input id={`${base}-rate`} type="number" min={1} max={600} {...invalid('rateLimit')} value={draft.rateLimit} onChange={(e) => setDraft({ ...draft, rateLimit: Number(e.target.value) })} />{err('rateLimit')}</div>
      {draft.kind === 'webhook' && <div className="field"><label htmlFor={`${base}-replay`}>{t('auto.trig.field.replay')}</label><input id={`${base}-replay`} type="number" min={30} max={3600} {...invalid('replayWindow')} value={draft.replayWindow} onChange={(e) => setDraft({ ...draft, replayWindow: Number(e.target.value) })} />{err('replayWindow')}</div>}
    </div>
    {draft.kind === 'event' && info && <p className="muted">{info.description}</p>}
    <fieldset className="autoConditions"><legend>{t('auto.trig.conditions')}</legend>
      <p className="muted">{draft.kind === 'webhook' ? t('auto.trig.conditionsWebhook') : t('auto.trig.conditionsHelp', { fields: fieldsList.join(', ') || '-' })}</p>
      {errors.conditions && <p className="autoFieldError">{errors.conditions}</p>}
      <datalist id={`${base}-fields`}>{fieldsList.map((f) => <option key={f} value={f} />)}</datalist>
      {draft.conditions.map((condition, index) => <div className="autoConditionRow" key={condition.key}>
        <div className="field"><label htmlFor={`${base}-${condition.key}-field`}>{t('auto.trig.field.field', { n: index + 1 })}</label>
          <input id={`${base}-${condition.key}-field`} dir="ltr" list={`${base}-fields`} {...invalid(`${condition.key}.field`)} value={condition.field} maxLength={120} spellCheck={false} onChange={(e) => patch(condition.key, { field: e.target.value })} />{err(`${condition.key}.field`)}</div>
        <div className="field"><label htmlFor={`${base}-${condition.key}-op`}>{t('auto.trig.field.operator')}</label>
          <select id={`${base}-${condition.key}-op`} value={condition.operator} onChange={(e) => patch(condition.key, { operator: e.target.value as ConditionOperator })}>{CONDITION_OPERATORS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></div>
        {condition.operator !== 'exists' && <div className="field"><label htmlFor={`${base}-${condition.key}-value`}>{t('auto.trig.field.value')}</label>
          <input id={`${base}-${condition.key}-value`} dir="auto" {...invalid(`${condition.key}.value`)} value={condition.value} maxLength={300} onChange={(e) => patch(condition.key, { value: e.target.value })} />{err(`${condition.key}.value`)}</div>}
        <button type="button" className="secondary autoConditionRemove" aria-label={t('auto.trig.removeCondition', { n: index + 1 })} onClick={() => setDraft((d) => ({ ...d, conditions: d.conditions.filter((c) => c.key !== condition.key) }))}><X aria-hidden="true" size={16} /></button>
      </div>)}
      <div className="actions"><button type="button" className="secondary" disabled={draft.conditions.length >= MAX_CONDITIONS} onClick={() => setDraft((d) => ({ ...d, conditions: [...d.conditions, newCondition(fieldsList[0] === 'body' ? 'body.' : fieldsList[0] ?? '')] }))}><Plus aria-hidden="true" />{t('auto.trig.addCondition')}</button></div>
    </fieldset>
    {summary && <p className="notice errorNotice" role="alert">{summary}</p>}
    <div className="actions">
      <button type="submit" disabled={busy}>{busy ? t('auto.saving') : existing ? t('auto.trig.save') : t('auto.trig.create')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={onCancel}>{t('common.cancel')}</button>
    </div>
  </form>;
}
