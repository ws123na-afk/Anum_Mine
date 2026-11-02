// Per-agent loop and retry limits (docs/agent-controls.md#loop-and-retry-limits): the most runs one task
// as an agent may have, and the most attempts an automation step that runs as the agent may make.
// Owners change them; the API decides who may (`can_manage` or its 403).
import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { CircleDot, Lock, Repeat, Save, Search } from 'lucide-react';
import { LOOP_PAGE_SIZE, limitDraft, parseLimit, type LoopLimit, type LoopLimitPage } from './lib/agentControls';
import { listLoopLimits, setLoopLimit } from './lib/agentControlsApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden' } | { kind: 'error'; detail: string };

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

export function AgentLoopLimitsPanel({ onOpenAgents }: { onOpenAgents: () => void }) {
  const [page, setPage] = useState<LoopLimitPage | null>(null);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const id = useId();
  const fetchPage = useCallback(async (q: string, at: number) => {
    try { setPage(await listLoopLimits(q, at)); setLoad({ kind: 'ready' }); }
    catch (error) { setLoad(isPermissionDenied(error) ? { kind: 'forbidden' } : { kind: 'error', detail: describeError(error) }); }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => { void fetchPage(query, offset); }, query ? 250 : 0);
    return () => clearTimeout(timer);
  }, [fetchPage, query, offset]);
  const replace = (row: LoopLimit) => setPage((current) => current && { ...current, items: current.items.map((item) => item.agent_id === row.agent_id ? row : item) });
  if (load.kind === 'forbidden') {
    return <section className="surface adminForbidden" aria-label={t('agentControls.loop.title')}>
      <span className="adminIcon" aria-hidden="true"><Lock /></span>
      <div><h3>{t('agentControls.loop.title')}</h3><p>{t('agentControls.loop.forbidden')}</p></div>
    </section>;
  }
  return <section className="surface capsPanel" aria-labelledby={`${id}-title`}>
    <div className="sectionHeader"><div>
      <h2 id={`${id}-title`}>{t('agentControls.loop.title')}</h2>
      <p className="muted">{t('agentControls.loop.intro')}</p>
    </div></div>
    {page && !page.can_manage && <Notice>{t('agentControls.loop.readOnly')}</Notice>}
    <label className="field capsSearch" htmlFor={`${id}-search`}><span>{t('agentControls.loop.search')}</span>
      <span className="capsSearchBox"><Search aria-hidden="true" /><input id={`${id}-search`} type="search" value={query} placeholder={t('agentControls.searchPlaceholder')}
        onChange={(event) => { setQuery(event.target.value); setOffset(0); }} /></span></label>
    {load.kind === 'loading' && !page && <Notice>{t('agentControls.loading')}</Notice>}
    {load.kind === 'error' && <div className="agentControlsRetry"><Notice error>{t('agentControls.loop.error', { message: load.detail })}</Notice>
      <button type="button" className="secondary" onClick={() => { setLoad({ kind: 'loading' }); void fetchPage(query, offset); }}>{t('agentControls.retry')}</button></div>}
    {page && page.total === 0 && !query.trim() && <div className="emptyState"><Repeat aria-hidden="true" /><h3>{t('agentControls.loop.emptyTitle')}</h3><p>{t('agentControls.loop.emptyText')}</p>
      <div className="actions"><button type="button" onClick={onOpenAgents}>{t('agentControls.emptyAction')}</button></div></div>}
    {page && page.total === 0 && query.trim() && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('agentControls.noMatchTitle')}</h3>
      <button type="button" className="secondary" onClick={() => { setQuery(''); setOffset(0); }}>{t('agentControls.clearSearch')}</button></div>}
    {page && page.items.length > 0 && <ul className="capsList" aria-label={t('agentControls.loop.title')}>
      {page.items.map((row) => <LoopRow key={row.agent_id} row={row} canManage={page.can_manage} onSaved={replace} />)}
    </ul>}
    {page && page.total > LOOP_PAGE_SIZE && <nav className="capsPager" aria-label={t('agentControls.paging')}>
      <span className="muted">{t('agentControls.range', { from: offset + 1, to: Math.min(offset + page.items.length, page.total), total: page.total })}</span>
      <div className="actions">
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LOOP_PAGE_SIZE))}>{t('agentControls.previous')}</button>
        <button type="button" className="secondary" disabled={offset + LOOP_PAGE_SIZE >= page.total} onClick={() => setOffset(offset + LOOP_PAGE_SIZE)}>{t('agentControls.next')}</button>
      </div>
    </nav>}
  </section>;
}

function LoopRow({ row, canManage, onSaved }: { row: LoopLimit; canManage: boolean; onSaved: (row: LoopLimit) => void }) {
  const [runs, setRuns] = useState(() => limitDraft(row.max_runs_per_task));
  const [attempts, setAttempts] = useState(() => limitDraft(row.max_step_attempts));
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const id = useId();
  useEffect(() => { setRuns(limitDraft(row.max_runs_per_task)); setAttempts(limitDraft(row.max_step_attempts)); }, [row]);
  const save = () => {
    const r = parseLimit(runs, 20);
    if (!r.ok) { setFlash({ text: r.error, error: true }); return; }
    const a = parseLimit(attempts, 10);
    if (!a.ok) { setFlash({ text: a.error, error: true }); return; }
    setBusy(true); setFlash(null);
    void setLoopLimit(row.agent_id, r.value, a.value)
      .then((next) => { onSaved(next); setFlash({ text: t('agentControls.loop.saved', { name: row.name }) }); })
      .catch((error: unknown) => setFlash({ text: t('agentControls.loop.failed', { name: row.name, message: describeError(error) }), error: true }))
      .finally(() => setBusy(false));
  };
  const none = t('agentControls.loop.none');
  return <li className="capRow">
    <div className="capWho">
      <strong dir="auto">{row.name}</strong>
      {row.status !== 'active' && <span className="capBadges"><span className="statusBadge neutral">{t('agentControls.paused')}</span></span>}
    </div>
    {canManage ? <form className="capForm" onSubmit={(event) => { event.preventDefault(); save(); }}>
      <label className="field" htmlFor={`${id}-runs`}><span>{t('agentControls.loop.runs')}</span>
        <input id={`${id}-runs`} dir="ltr" inputMode="numeric" value={runs} placeholder={none} aria-describedby={`${id}-hint`}
          aria-label={t('agentControls.loop.runsInput', { name: row.name }, { isolate: false })} onChange={(event) => setRuns(event.target.value)} /></label>
      <label className="field" htmlFor={`${id}-attempts`}><span>{t('agentControls.loop.attempts')}</span>
        <input id={`${id}-attempts`} dir="ltr" inputMode="numeric" value={attempts} placeholder={none} aria-describedby={`${id}-hint`}
          aria-label={t('agentControls.loop.attemptsInput', { name: row.name }, { isolate: false })} onChange={(event) => setAttempts(event.target.value)} /></label>
      <div className="actions capSave"><button type="submit" disabled={busy} aria-label={t('agentControls.loop.save', { name: row.name }, { isolate: false })}><Save aria-hidden="true" />{busy ? t('agentControls.working') : t('agentControls.save')}</button></div>
      <small id={`${id}-hint`} className="muted">{t('agentControls.loop.hint')}</small>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    </form> : <div className="capForm readOnly"><span className="muted">{t('agentControls.loop.runs')}</span><strong dir="ltr">{row.max_runs_per_task ?? none}</strong>
      <span className="muted">{t('agentControls.loop.attempts')}</span><strong dir="ltr">{row.max_step_attempts ?? none}</strong></div>}
  </li>;
}
