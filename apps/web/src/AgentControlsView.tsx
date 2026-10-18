// Agent controls (docs/agent-controls.md): the organization's emergency stop for every agent and
// outbound send, and a monthly cost cap per agent. Everything shown comes from the API; who may turn
// the stop on or off and who may change caps is the API's answer (`can_engage`, `can_release`,
// `can_manage`, or its 403), never guessed from the token here.
import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { Bot, CircleDot, Lock, OctagonX, Play, RefreshCw, Save, Search, ShieldAlert, ShieldCheck } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import { formatResetDate } from './lib/admin';
import {
  CAPS_PAGE_SIZE, REASON_MAX, capDraft, capMeter, parseCap, parseReason, stopSummary,
  type AgentCap, type AgentCapsPage, type AgentControls,
} from './lib/agentControls';
import { getAgentControls, listAgentCaps, setAgentCap, setEmergencyStop } from './lib/agentControlsApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import './admin.css';
import './agentControls.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

export function AgentControlsView({ onOpenAgents, onStopChanged }: { onOpenAgents: () => void; onStopChanged?: (engaged: boolean) => void }) {
  const [controls, setControls] = useState<AgentControls | null>(null);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const refresh = useCallback(async () => {
    try {
      const next = await getAgentControls();
      setControls(next);
      onStopChanged?.(next.emergency_stop.engaged);
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, [onStopChanged]);
  useEffect(() => { void refresh(); }, [refresh]);
  const changed = (next: AgentControls) => { setControls(next); onStopChanged?.(next.emergency_stop.engaged); };
  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('agentControls.eyebrow')}</p><h2>{t('agentControls.title')}</h2><p>{t('agentControls.intro')}</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('admin.refresh')}</button>
    </section>
    {load.kind === 'loading' && !controls && <Notice>{t('agentControls.loading')}</Notice>}
    {load.kind === 'error' && <div className="agentControlsRetry"><Notice error>{t('agentControls.error', { message: load.detail })}</Notice>
      <button type="button" className="secondary" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}>{t('agentControls.retry')}</button></div>}
    {load.kind === 'forbidden' && <Notice error>{load.detail}</Notice>}
    {controls && <EmergencyStopCard controls={controls} onChanged={changed} />}
    <AgentCapsPanel onOpenAgents={onOpenAgents} />
  </>;
}

// ------------------------------------------------------------------------- emergency stop

function EmergencyStopCard({ controls, onChanged }: { controls: AgentControls; onChanged: (next: AgentControls) => void }) {
  const stop = controls.emergency_stop;
  const [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const reasonId = useId();
  const canAct = stop.engaged ? controls.can_release : controls.can_engage;
  const ask = () => {
    const parsed = parseReason(reason);
    if (!parsed.ok) { setFlash({ text: parsed.error, error: true }); return; }
    setFlash(null);
    setConfirming(true);
  };
  const act = () => {
    const parsed = parseReason(reason);
    if (!parsed.ok) return;
    setBusy(true);
    void setEmergencyStop(!stop.engaged, parsed.value)
      .then((next) => {
        onChanged(next);
        setReason('');
        setFlash({ text: next.emergency_stop.engaged ? t('agentControls.engaged', { count: next.cancelled_tasks }) : t('agentControls.released') });
      })
      .catch((error: unknown) => setFlash({ text: describeError(error), error: true }))
      .finally(() => { setBusy(false); setConfirming(false); });
  };
  const stateWord = stop.engaged ? t('agentControls.word.on') : t('agentControls.word.off');
  return <section className={`surface stopCard ${stop.engaged ? 'engaged' : ''}`} aria-label={t('agentControls.stopLabel')}>
    <div className="sectionHeader">
      <div className="stopHeading">
        <span className="adminIcon" aria-hidden="true">{stop.engaged ? <OctagonX /> : <ShieldCheck />}</span>
        <div><h2>{t('agentControls.stopTitle')}</h2><p className="muted">{stopSummary(controls)}</p></div>
      </div>
      <span className={`statusBadge ${stop.engaged ? 'dangerTone' : 'success'}`} role="status">{stop.engaged ? t('agentControls.stateOn') : t('agentControls.stateOff')}</span>
    </div>
    <div className="stopBody">
      <div>
        <h3>{t('agentControls.whatStops')}</h3>
        <ul className="stopList">
          <li>{t('agentControls.stops.models')}</li>
          <li>{t('agentControls.stops.tools')}</li>
          <li>{t('agentControls.stops.approvals')}</li>
          <li>{t('agentControls.stops.automations')}</li>
          <li>{t('agentControls.stops.running')}</li>
        </ul>
        {stop.changed_at && <p className="muted">{stop.changed_by_you
          ? t('agentControls.changedByYou', { state: stateWord, when: formatDateTime(stop.changed_at) })
          : t('agentControls.changedBy', { state: stateWord, user: stop.changed_by ?? '-', when: formatDateTime(stop.changed_at) })}</p>}
        {stop.reason && <p className="stopReason" dir="auto">{t('agentControls.reasonShown', { reason: stop.reason })}</p>}
      </div>
      {canAct ? <form className="stopForm" onSubmit={(event) => { event.preventDefault(); ask(); }}>
        <label className="field" htmlFor={reasonId}><span>{t('agentControls.reason')}</span>
          <textarea id={reasonId} dir="auto" rows={3} maxLength={REASON_MAX} value={reason} onChange={(event) => setReason(event.target.value)}
            placeholder={stop.engaged ? t('agentControls.reasonOffPlaceholder') : t('agentControls.reasonOnPlaceholder')} /></label>
        {flash && <Notice error={flash.error}>{flash.text}</Notice>}
        <div className="actions">
          <button type="submit" className={stop.engaged ? undefined : 'danger'} disabled={busy}>
            {stop.engaged ? <Play aria-hidden="true" /> : <OctagonX aria-hidden="true" />}
            {busy ? t('agentControls.working') : stop.engaged ? t('agentControls.release') : t('agentControls.engage')}
          </button>
        </div>
      </form> : <div className="stopForm">
        {flash && <Notice error={flash.error}>{flash.text}</Notice>}
        <p className="notice stopDenied" role="note"><Lock aria-hidden="true" />{stop.engaged ? t('agentControls.cannotRelease') : t('agentControls.cannotEngage')}</p>
      </div>}
    </div>
    {confirming && <ConfirmDialog
      title={stop.engaged ? t('agentControls.confirmReleaseTitle') : t('agentControls.confirmEngageTitle')}
      confirmLabel={stop.engaged ? t('agentControls.release') : t('agentControls.engage')}
      danger={!stop.engaged}
      busy={busy}
      onConfirm={act}
      onCancel={() => setConfirming(false)}>
      <p>{stop.engaged ? t('agentControls.confirmReleaseText') : t('agentControls.confirmEngageText')}</p>
    </ConfirmDialog>}
  </section>;
}

// ------------------------------------------------------------------------- caps

function AgentCapsPanel({ onOpenAgents }: { onOpenAgents: () => void }) {
  const [page, setPage] = useState<AgentCapsPage | null>(null);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const searchId = useId();
  const fetchPage = useCallback(async (q: string, at: number) => {
    try {
      setPage(await listAgentCaps(q, at));
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => { void fetchPage(query, offset); }, query ? 250 : 0);
    return () => clearTimeout(timer);
  }, [fetchPage, query, offset]);
  const replace = (cap: AgentCap) => setPage((current) => current && { ...current, items: current.items.map((item) => item.agent_id === cap.agent_id ? cap : item) });

  if (load.kind === 'forbidden') {
    return <section className="surface adminForbidden" aria-label={t('agentControls.capsForbiddenTitle')}>
      <span className="adminIcon" aria-hidden="true"><Lock /></span>
      <div><h3>{t('agentControls.capsForbiddenTitle')}</h3><p>{t('agentControls.capsForbidden')}</p></div>
    </section>;
  }
  const first = page && page.total ? offset + 1 : 0;
  const last = page ? Math.min(offset + page.items.length, page.total) : 0;
  return <section className="surface capsPanel" aria-labelledby={`${searchId}-title`}>
    <div className="sectionHeader">
      <div>
        <h2 id={`${searchId}-title`}>{t('agentControls.capsTitle')}</h2>
        <p className="muted">{t('agentControls.capsIntro')}</p>
        {page && <p className="muted">{t('agentControls.capsPeriod', { start: formatResetDate(page.period_start, false), reset: formatResetDate(page.resets_on) })}</p>}
      </div>
    </div>
    {page && !page.can_manage && <Notice>{t('agentControls.capsReadOnly')}</Notice>}
    <label className="field capsSearch" htmlFor={searchId}><span>{t('agentControls.search')}</span>
      <span className="capsSearchBox"><Search aria-hidden="true" /><input id={searchId} type="search" value={query} placeholder={t('agentControls.searchPlaceholder')}
        onChange={(event) => { setQuery(event.target.value); setOffset(0); }} /></span></label>
    {load.kind === 'loading' && !page && <Notice>{t('agentControls.loading')}</Notice>}
    {load.kind === 'error' && <div className="agentControlsRetry"><Notice error>{t('agentControls.capsError', { message: load.detail })}</Notice>
      <button type="button" className="secondary" onClick={() => { setLoad({ kind: 'loading' }); void fetchPage(query, offset); }}>{t('agentControls.retry')}</button></div>}
    {page && page.total === 0 && !query.trim() && <div className="emptyState"><Bot aria-hidden="true" /><h3>{t('agentControls.emptyTitle')}</h3><p>{t('agentControls.emptyText')}</p>
      <div className="actions"><button type="button" onClick={onOpenAgents}><Bot aria-hidden="true" />{t('agentControls.emptyAction')}</button></div></div>}
    {page && page.total === 0 && query.trim() && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('agentControls.noMatchTitle')}</h3><p>{t('agentControls.noMatchText')}</p>
      <button type="button" className="secondary" onClick={() => { setQuery(''); setOffset(0); }}>{t('agentControls.clearSearch')}</button></div>}
    {page && page.items.length > 0 && <ul className="capsList" aria-label={t('agentControls.table')}>
      {page.items.map((cap) => <CapRow key={cap.agent_id} cap={cap} canManage={page.can_manage} onSaved={replace} />)}
    </ul>}
    {page && page.total > CAPS_PAGE_SIZE && <nav className="capsPager" aria-label={t('agentControls.paging')}>
      <span className="muted">{t('agentControls.range', { from: first, to: last, total: page.total })}</span>
      <div className="actions">
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - CAPS_PAGE_SIZE))}>{t('agentControls.previous')}</button>
        <button type="button" className="secondary" disabled={offset + CAPS_PAGE_SIZE >= page.total} onClick={() => setOffset(offset + CAPS_PAGE_SIZE)}>{t('agentControls.next')}</button>
      </div>
    </nav>}
  </section>;
}

function CapRow({ cap, canManage, onSaved }: { cap: AgentCap; canManage: boolean; onSaved: (cap: AgentCap) => void }) {
  const [draft, setDraft] = useState(() => capDraft(cap));
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const inputId = useId();
  useEffect(() => { setDraft(capDraft(cap)); }, [cap]);
  const meter = capMeter(cap);
  const save = () => {
    const parsed = parseCap(draft);
    if (!parsed.ok) { setFlash({ text: parsed.error, error: true }); return; }
    setBusy(true);
    setFlash(null);
    void setAgentCap(cap.agent_id, parsed.value)
      .then((next) => { onSaved(next); setFlash({ text: parsed.value === null ? t('agentControls.capCleared', { name: cap.name }) : t('agentControls.capSaved', { name: cap.name }) }); })
      .catch((error: unknown) => setFlash({ text: t('agentControls.capFailed', { name: cap.name, message: describeError(error) }), error: true }))
      .finally(() => setBusy(false));
  };
  return <li className={`capRow ${cap.exceeded ? 'exceeded' : ''}`}>
    <div className="capWho">
      <strong dir="auto">{cap.name}</strong>
      <span className="capBadges">
        {cap.sharing === 'private' && <span className="statusBadge neutral">{t('agentControls.private')}</span>}
        {cap.status !== 'active' && <span className="statusBadge neutral">{t('agentControls.paused')}</span>}
        {cap.exceeded && <span className="statusBadge dangerTone"><ShieldAlert aria-hidden="true" />{t('agentControls.capReached')}</span>}
        {!cap.exceeded && meter.tone === 'warn' && <span className="statusBadge warning">{t('agentControls.capNear')}</span>}
      </span>
    </div>
    <div className="capUsage">
      <div className="budgetMeterTop"><span>{t('agentControls.col.usage')}</span><strong dir="ltr">{meter.usedText}<small> / {meter.limitText}</small></strong></div>
      <div className={`meterTrack ${meter.tone}`} role="progressbar" aria-label={t('agentControls.meter', { name: cap.name }, { isolate: false })} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={meter.percent === null ? undefined : Math.round(meter.barPercent)} aria-valuetext={meter.percent === null ? t('agentControls.noCap') : t('agentControls.percentUsed', { percent: Math.round(meter.percent) })}>
        <span style={{ width: `${meter.barPercent}%` }} />
      </div>
      <small className="muted">{t('agentControls.calls', { count: cap.usage.calls })}{meter.percent !== null ? ` · ${t('agentControls.percentUsed', { percent: Math.round(meter.percent) })}` : ''}</small>
    </div>
    {canManage ? <form className="capForm" onSubmit={(event) => { event.preventDefault(); save(); }}>
      <label className="field" htmlFor={inputId}><span>{t('agentControls.col.cap')}</span>
        <input id={inputId} dir="ltr" inputMode="decimal" value={draft} placeholder={t('agentControls.noCap')} aria-label={t('agentControls.capInput', { name: cap.name }, { isolate: false })}
          aria-describedby={`${inputId}-hint`} onChange={(event) => setDraft(event.target.value)} /></label>
      <div className="actions capSave"><button type="submit" disabled={busy} aria-label={t('agentControls.saveCap', { name: cap.name }, { isolate: false })}><Save aria-hidden="true" />{busy ? t('agentControls.working') : t('agentControls.save')}</button></div>
      <small id={`${inputId}-hint`} className="muted">{t('agentControls.capHint')}</small>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    </form> : <div className="capForm readOnly"><span className="muted">{t('agentControls.col.cap')}</span><strong dir="ltr">{meter.limitText}</strong></div>}
  </li>;
}
