// Department assistants (docs/team-work.md#department-assistants): Sales, Support, Operations and
// warehouse, Finance and Tyre engineering profiles with allowed tools, autonomy, instructions and
// routing keywords; inbound messages routed to them. The page (docs/team-work.md#the-assistants-page)
// has the Overview (figures, a directory of cards and the selected assistant's detail) and Work tabs,
// and a step-by-step create and edit wizard. Owners manage them; the API decides (403).
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Bot, Check, CheckCircle2, CircleDot, Inbox, ListChecks, Pencil, Plus, Power, RefreshCw, Search, Send, ShieldCheck, ShieldX, Trash2, Wrench, X } from 'lucide-react';
import type { AssistantBody, AssistantProfile, AssistantTemplate, AutoRouteChannel, Autonomy, Department, InboundChannel, RouteResult, WorkItem, WorkItemPage, WorkStatus, Workload } from '@anum/contracts';
import * as team from './lib/teamApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { assistantGuards, assistantMatches, assistantTotals, splitList, workQuery, WIZARD_STEPS, type AssistantStep } from './lib/team';
import { type Load } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { ConfirmDialog } from './Dialog';
import { FinancePageHead, FinanceTabs, FinCard, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import { Breadcrumbs, loadFailure, PaneDenied, PaneProblem } from './PageParts';
import './team.css';
import './assistantsPage.css';

const DEPARTMENTS: Department[] = ['sales', 'support', 'operations', 'finance', 'tyre_engineering', 'custom'];
const AUTONOMY: Autonomy[] = ['observe', 'suggest', 'act_low_risk'];
const CHANNELS: InboundChannel[] = ['email', 'whatsapp', 'marketplace', 'web', 'phone', 'other'];
const AUTO_ROUTE: AutoRouteChannel[] = ['email', 'whatsapp'];
const WORK_PAGE = 20;
type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

const blank = (template?: AssistantTemplate): AssistantBody => ({
  department: template?.department ?? 'custom', name: template?.name ?? '', instructions: template?.instructions ?? '',
  allowed_tools: template?.allowed_tools ?? ['anum.respond'], routing_keywords: template?.routing_keywords ?? [],
  inbound_channels: [], autonomy: template?.autonomy ?? 'suggest', enabled: true,
});
const bodyOf = (a: AssistantProfile): AssistantBody => { const { id: _id, created_by: _by, created_at: _c, updated_at: _u, ...body } = a; return body; };
const statusTone = (s: WorkStatus): 'ok' | 'warn' | 'bad' | 'muted' | 'info' => s === 'done' ? 'ok' : s === 'blocked' ? 'bad' : s === 'cancelled' ? 'muted' : s === 'in_progress' ? 'info' : 'warn';

export function AssistantsView({ onOpenWork }: { onOpenWork?(itemId: string): void }) {
  const [assistants, setAssistants] = useState<Load<AssistantProfile[]>>({ state: 'loading' });
  const [templates, setTemplates] = useState<AssistantTemplate[]>([]);
  const [workload, setWorkload] = useState<Load<Workload>>({ state: 'loading' });
  const [done, setDone] = useState<Record<string, number | null>>({});
  const [flash, setFlash] = useState<Flash>(null);
  const [wizard, setWizard] = useState<{ id: string | null; body: AssistantBody | null } | null>(null);
  const [removing, setRemoving] = useState<AssistantProfile | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'all' | 'on' | 'off'>('all');
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<'overview' | 'work'>('overview');
  const load = useCallback(async () => {
    team.getWorkload().then((value) => setWorkload({ state: 'ready', value }), (e: unknown) => setWorkload(loadFailure(e)));
    team.getAssistantTemplates().then(setTemplates, () => setTemplates([]));
    try {
      const list = await team.getAssistants();
      setAssistants({ state: 'ready', value: list });
      setSelected((current) => current && list.some((a) => a.id === current) ? current : list[0]?.id ?? null);
      // Finished work per assistant: one page of one item each, read for its total.
      const totals = await Promise.all(list.map((a) => team.listWork(workQuery({ status: 'done', limit: 1, assigneeType: 'assistant', assigneeId: a.id })).then((page) => page.total, () => null)));
      setDone(Object.fromEntries(list.map((a, index) => [a.id, totals[index]])));
    } catch (e) { setAssistants(loadFailure(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const refuse = (e: unknown) => setFlash({ text: isPermissionDenied(e) ? t('assistants.ownersOnly', { message: describeError(e) }) : describeError(e), error: true });
  const toggle = async (a: AssistantProfile) => {
    setBusy(a.id);
    try {
      await team.updateAssistant(a.id, { ...bodyOf(a), enabled: !a.enabled });
      setFlash({ text: t(a.enabled ? 'assistants.turnedOff' : 'assistants.turnedOn', { name: a.name }) });
      await load();
    } catch (e) { refuse(e); } finally { setBusy(null); }
  };
  const remove = async (a: AssistantProfile) => {
    setBusy(a.id);
    try { await team.deleteAssistant(a.id); setRemoving(null); setFlash({ text: t('assistants.deleted', { name: a.name }) }); await load(); } catch (e) { setRemoving(null); refuse(e); } finally { setBusy(null); }
  };
  const list = assistants.state === 'ready' ? assistants.value : [];
  const used = new Set(list.map((a) => a.department));
  const totals = assistantTotals(list, workload.state === 'ready' ? workload.value.rows : [], done);
  const shown = list.filter((a) => assistantMatches(a, query, (d) => t(`team.dept.${d}` as MessageKey)) && (status === 'all' || (status === 'on') === a.enabled));
  const current = list.find((a) => a.id === selected) ?? null;
  const rowOf = (a: AssistantProfile) => workload.state === 'ready' ? workload.value.rows.find((r) => r.assignee_type === 'assistant' && r.assignee_id === a.id) ?? null : null;
  const figure = (ready: boolean, value: string) => assistants.state === 'loading' || (!ready && workload.state === 'loading') ? '…' : ready ? value : '-';
  const workReady = workload.state === 'ready';

  if (wizard) return <div className="assistantsView asPage">
    <AssistantWizard id={wizard.id} initial={wizard.body} templates={templates} used={used}
      onCancel={() => setWizard(null)}
      onSaved={(a) => { setWizard(null); setSelected(a.id); setFlash({ text: t('assistants.saved', { name: a.name }) }); void load(); }}
      onRefused={(e) => { setWizard(null); refuse(e); }} />
  </div>;

  return <div className="assistantsView asPage">
    <FinancePageHead title={viewLabel('assistants')} subtitle={t('assistants.intro')}
      actions={<button type="button" onClick={() => setWizard({ id: null, body: null })}><Plus aria-hidden="true" />{t('assistants.new')}</button>}
      tabs={<FinanceTabs label={t('asPage.tabs')} idPrefix="assistants" value={tab} onChange={setTab} items={[{ id: 'overview', label: t('asPage.tab.overview') }, { id: 'work', label: t('asPage.tab.work') }]} />} />
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {assistants.state === 'denied' && <section className="pane"><PaneDenied title={t('pages2.assistants.denied')} text={assistants.message} /></section>}
    {assistants.state !== 'denied' && tab === 'overview' && <div {...tabPanel('assistants', 'overview')}>
      <KpiRow label={t('pages2.assistants.summary')}>
        <KpiCard icon={<Bot />} label={t('pages2.assistants.fig.on')} value={figure(true, String(totals.enabled))} hint={t('pages2.assistants.fig.onHint', { count: totals.total })} tone={totals.enabled ? 'good' : ''} />
        <KpiCard icon={<Inbox />} label={t('pages2.assistants.fig.queue')} value={figure(workReady, String(totals.open))} hint={t('pages2.assistants.fig.queueHint', { count: totals.dueWeek })} />
        <KpiCard icon={<CircleDot />} label={t('team.load.overdue')} value={figure(workReady, String(totals.overdue))} hint={totals.overdue ? t('pages2.team.fig.overdueHint') : t('pages2.team.fig.noneOverdue')} tone={totals.overdue ? 'bad' : ''} />
        <KpiCard icon={<CheckCircle2 />} label={t('pages2.assistants.fig.done')} value={figure(true, totals.done === null ? '-' : String(totals.done))} hint={totals.done === null ? t('pages2.assistants.fig.doneUnknown') : t('pages2.assistants.fig.doneHint')} />
      </KpiRow>
      {assistants.state === 'error' && <PaneProblem text={t('assistants.loadFailed', { message: assistants.message })} onRetry={() => void load()} />}
      {assistants.state === 'loading' && <p className="muted" role="status">{t('team.loading')}</p>}
      {workload.state === 'error' && <PaneProblem text={t('pages2.assistants.workloadFailed', { message: workload.message })} onRetry={() => void load()} />}
      {assistants.state === 'ready' && <div className="asBoard">
        <FinCard title={t('asPage.directory')} subtitle={t('asPage.directorySub')} icon={<Bot />} className="asDirectory"
          actions={list.length > 0 ? <div className="asFilters">
            <label className="asSearch"><span className="visuallyHidden">{t('pages2.assistants.search')}</span><Search aria-hidden="true" /><input type="search" value={query} placeholder={t('pages2.assistants.search')} onChange={(e) => setQuery(e.target.value)} /></label>
            <label><span className="visuallyHidden">{t('pages2.assistants.col.status')}</span><select value={status} aria-label={t('pages2.assistants.col.status')} onChange={(e) => setStatus(e.target.value as typeof status)}>
              <option value="all">{t('asPage.status.all')}</option><option value="on">{t('assistants.on')}</option><option value="off">{t('assistants.off')}</option></select></label>
          </div> : undefined}>
          {!list.length && <div className="paneEmpty"><Bot aria-hidden="true" /><h3>{t('assistants.empty')}</h3><p>{t('assistants.emptyText')}</p><div className="actions"><button type="button" onClick={() => setWizard({ id: null, body: null })}><Plus aria-hidden="true" />{t('assistants.new')}</button></div></div>}
          {list.length > 0 && !shown.length && <div className="paneEmpty"><Search aria-hidden="true" /><h3>{t('pages2.assistants.noMatch')}</h3><div className="actions"><button type="button" className="secondary" onClick={() => { setQuery(''); setStatus('all'); }}>{t('common.clear')}</button></div></div>}
          {shown.length > 0 && <ul className="asCards" aria-label={t('asPage.directory')}>{shown.map((a) => {
            const row = rowOf(a);
            return <li key={a.id}><button type="button" className={`asCard ${a.id === selected ? 'isSelected' : ''}`} aria-pressed={a.id === selected} onClick={() => setSelected(a.id)}>
              <span className="asCardHead"><span className="asIcon" aria-hidden="true"><Bot /></span><strong dir="auto">{a.name}</strong><StatusChip tone={a.enabled ? 'ok' : 'muted'}>{a.enabled ? t('assistants.on') : t('assistants.off')}</StatusChip></span>
              <span className="asCardText" dir="auto">{a.instructions || t('asPage.noInstructions')}</span>
              <span className="asCardMeta"><span>{t(`team.dept.${a.department}` as MessageKey)}</span><span>{t(`assistants.autonomy.${a.autonomy}` as MessageKey)}</span>
                <span>{t('asPage.card.queue', { count: row?.open ?? 0 })}</span>{(row?.overdue ?? 0) > 0 && <span className="asOverdue">{t('asPage.card.overdue', { count: row?.overdue ?? 0 })}</span>}
                <span>{t('asPage.card.done', { count: done[a.id] ?? 0 })}</span></span>
            </button></li>;
          })}</ul>}
        </FinCard>
        {current && <AssistantDetail key={current.id} assistant={current} busy={busy === current.id} onOpenWork={onOpenWork}
          onEdit={() => setWizard({ id: current.id, body: bodyOf(current) })} onToggle={() => void toggle(current)} onDelete={() => setRemoving(current)} />}
      </div>}
      <details className="surface assistantsRoute">
        <summary><Inbox aria-hidden="true" className="inlineIcon" />{t('assistants.route.title')}<small>{t('pages2.assistants.routeSummary')}</small></summary>
        <RouteForm assistants={list} onOpenWork={onOpenWork} />
      </details>
    </div>}
    {assistants.state !== 'denied' && tab === 'work' && <div {...tabPanel('assistants', 'work')}><AssistantWork assistants={list} onOpenWork={onOpenWork} /></div>}
    {removing && <ConfirmDialog title={t('pages2.assistants.deleteTitle')} confirmLabel={t('common.delete')} danger busy={busy === removing.id} onCancel={() => setRemoving(null)} onConfirm={() => void remove(removing)}>
      <p>{t('assistants.deleteConfirm', { name: removing.name })}</p>
    </ConfirmDialog>}
  </div>;
}

/** The selected assistant: purpose, allowed tools and what it may not do, routing, recent work and safety. */
function AssistantDetail({ assistant: a, busy, onEdit, onToggle, onDelete, onOpenWork }: {
  assistant: AssistantProfile; busy: boolean; onEdit(): void; onToggle(): void; onDelete(): void; onOpenWork?(itemId: string): void;
}) {
  const [work, setWork] = useState<Load<WorkItem[]>>({ state: 'loading' });
  const read = useCallback(() => {
    setWork({ state: 'loading' });
    team.listWork(workQuery({ status: 'all', sort: 'updated', limit: 5, assigneeType: 'assistant', assigneeId: a.id })).then((page) => setWork({ state: 'ready', value: page.items }), (e: unknown) => setWork(loadFailure(e)));
  }, [a.id]);
  useEffect(() => { read(); }, [read]);
  const guards = assistantGuards(a.autonomy);
  return <aside className="finCard asDetail" aria-labelledby="as-detail-name">
    <div className="asDetailHead">
      <span className="asIcon lg" aria-hidden="true"><Bot /></span>
      <div><h2 id="as-detail-name" dir="auto">{a.name}</h2><p className="muted asSmall">{t(`team.dept.${a.department}` as MessageKey)} · {t('asPage.updated', { when: formatDateTime(a.updated_at) })}</p></div>
      <StatusChip tone={a.enabled ? 'ok' : 'muted'}>{a.enabled ? t('assistants.on') : t('assistants.off')}</StatusChip>
    </div>
    <div className="asDetailActions">
      <button type="button" className="secondary" disabled={busy} aria-label={t('pages2.assistants.editNamed', { name: a.name })} onClick={onEdit}><Pencil aria-hidden="true" />{t('team.edit')}</button>
      <button type="button" className="secondary" disabled={busy} aria-label={t(a.enabled ? 'pages2.assistants.offNamed' : 'pages2.assistants.onNamed', { name: a.name })} onClick={onToggle}><Power aria-hidden="true" />{a.enabled ? t('assistants.turnOff') : t('assistants.turnOn')}</button>
      <button type="button" className="secondary" disabled={busy} aria-label={t('pages2.assistants.deleteNamed', { name: a.name })} onClick={onDelete}><Trash2 aria-hidden="true" />{t('common.delete')}</button>
    </div>
    <section className="asBlock"><h3>{t('asPage.purpose')}</h3><p dir="auto">{a.instructions || t('asPage.noInstructions')}</p></section>
    <div className="asTwo">
      <section className="asBlock asAllowed"><h3><ShieldCheck aria-hidden="true" />{t('asPage.allowed', { count: a.allowed_tools.length })}</h3>
        <ul>{a.allowed_tools.map((tool) => <li key={tool}><Check aria-hidden="true" /><span dir="ltr">{tool}</span></li>)}</ul>
        <p className="asSmall muted">{t(`assistants.autonomyHelp.${a.autonomy}` as MessageKey)}</p></section>
      <section className="asBlock asBlocked"><h3><ShieldX aria-hidden="true" />{t('asPage.blocked')}</h3>
        <ul>{guards.map((key) => <li key={key}><X aria-hidden="true" /><span>{t(key)}</span></li>)}</ul></section>
    </div>
    <section className="asBlock"><h3>{t('asPage.routing')}</h3>
      <p>{a.routing_keywords.length ? t('assistants.keywordsList', { keywords: a.routing_keywords.join(', ') }) : t('pages2.assistants.noRoutes')}</p>
      {(a.inbound_channels ?? []).length > 0 && <p>{t('assistants.autoRouteList', { channels: (a.inbound_channels ?? []).map((c) => t(`assistants.channel.${c}` as MessageKey)).join(', ') })}</p>}
    </section>
    <section className="asBlock"><h3>{t('asPage.recentWork')}</h3>
      {work.state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
      {(work.state === 'error' || work.state === 'denied') && <PaneProblem text={t('asPage.workFailed', { message: work.message })} onRetry={read} />}
      {work.state === 'ready' && work.value.length === 0 && <p className="muted">{t('asPage.noWork')}</p>}
      {work.state === 'ready' && work.value.length > 0 && <ul className="asWorkList">{work.value.map((item) => <li key={item.id}>
        {onOpenWork ? <button type="button" className="linkButton" onClick={() => onOpenWork(item.id)} dir="auto">{item.title}</button> : <span dir="auto">{item.title}</span>}
        <StatusChip tone={statusTone(item.status)}>{t(`team.status.${item.status}` as MessageKey)}</StatusChip>
        <time className="muted" dateTime={item.updated_at}>{formatDateTime(item.updated_at)}</time>
      </li>)}</ul>}
    </section>
    <section className="asSafety"><h3><ShieldCheck aria-hidden="true" />{t('asPage.safety')}</h3>
      <ul><li>{t('asPage.safety.tools')}</li><li>{t('asPage.safety.approvals')}</li><li>{t('asPage.safety.audit')}</li></ul></section>
  </aside>;
}

/** Work routed to assistants, across all of them: what the mockup calls runs, from the work-items API. */
function AssistantWork({ assistants, onOpenWork }: { assistants: AssistantProfile[]; onOpenWork?(itemId: string): void }) {
  const [status, setStatus] = useState('all'), [assistant, setAssistant] = useState(''), [offset, setOffset] = useState(0), [tick, setTick] = useState(0);
  const [page, setPage] = useState<Load<WorkItemPage>>({ state: 'loading' });
  useEffect(() => {
    let live = true;
    setPage({ state: 'loading' });
    team.listWork(workQuery({ status, assigneeType: 'assistant', assigneeId: assistant || undefined, limit: WORK_PAGE, offset, sort: 'updated' }))
      .then((value) => { if (live) setPage({ state: 'ready', value }); }, (e: unknown) => { if (live) setPage(loadFailure(e)); });
    return () => { live = false; };
  }, [status, assistant, offset, tick]);
  return <FinCard title={t('asPage.work.title')} icon={<ListChecks />} subtitle={t('asPage.work.sub')}
    actions={<div className="asFilters">
      <label><span className="visuallyHidden">{t('asPage.work.assistant')}</span><select aria-label={t('asPage.work.assistant')} value={assistant} onChange={(e) => { setAssistant(e.target.value); setOffset(0); }}><option value="">{t('asPage.work.allAssistants')}</option>{assistants.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label><span className="visuallyHidden">{t('pages2.assistants.col.status')}</span><select aria-label={t('pages2.assistants.col.status')} value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="all">{t('asPage.status.all')}</option><option value="open">{t('asPage.status.open')}</option>{(['todo', 'in_progress', 'blocked', 'done', 'cancelled'] as const).map((s) => <option key={s} value={s}>{t(`team.status.${s}` as MessageKey)}</option>)}</select></label>
    </div>}>
    {page.state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
    {page.state === 'denied' && <PaneDenied title={t('pages2.assistants.denied')} text={page.message} />}
    {page.state === 'error' && <PaneProblem text={t('asPage.workFailed', { message: page.message })} onRetry={() => setTick((n) => n + 1)} />}
    {page.state === 'ready' && page.value.items.length === 0 && <div className="paneEmpty"><ListChecks aria-hidden="true" /><h3>{t('asPage.work.empty')}</h3><p>{t('asPage.work.emptyText')}</p></div>}
    {page.state === 'ready' && page.value.items.length > 0 && <>
      <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('asPage.work.title')}><table className="finGrid">
        <thead><tr><th scope="col">{t('asPage.work.item')}</th><th scope="col">{t('asPage.work.assistant')}</th><th scope="col">{t('pages2.assistants.col.status')}</th><th scope="col">{t('asPage.work.due')}</th><th scope="col">{t('asPage.work.updated')}</th></tr></thead>
        <tbody>{page.value.items.map((item) => <tr key={item.id}>
          <th scope="row">{onOpenWork ? <button type="button" className="linkButton" onClick={() => onOpenWork(item.id)} dir="auto">{item.title}</button> : <span dir="auto">{item.title}</span>}</th>
          <td dir="auto">{item.assignee_name ?? '-'}</td>
          <td><StatusChip tone={statusTone(item.status)}>{t(`team.status.${item.status}` as MessageKey)}</StatusChip>{item.overdue && <StatusChip tone="bad">{t('team.load.overdue')}</StatusChip>}</td>
          <td>{item.due_at ? formatDateTime(item.due_at) : '-'}</td>
          <td>{formatDateTime(item.updated_at)}</td>
        </tr>)}</tbody>
      </table></div>
      {page.value.total > WORK_PAGE && <nav className="asPager" aria-label={t('asPage.work.pages')}>
        <span className="muted" aria-live="polite">{t('asPage.work.range', { from: offset + 1, to: offset + page.value.items.length, total: page.value.total })}</span>
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - WORK_PAGE))}>{t('asPage.work.previous')}</button>
        <button type="button" className="secondary" disabled={page.value.next_offset === null} onClick={() => setOffset(offset + WORK_PAGE)}>{t('asPage.work.next')}</button>
      </nav>}
    </>}
  </FinCard>;
}

/**
 * Create or edit an assistant step by step: Start (department templates, new ones only), Define,
 * Configure, Safety and approval, then Review and save. The API checks every field and the role.
 */
function AssistantWizard({ id, initial, templates, used, onCancel, onSaved, onRefused }: {
  id: string | null; initial: AssistantBody | null; templates: AssistantTemplate[]; used: Set<Department>;
  onCancel(): void; onSaved(a: AssistantProfile): void; onRefused(e: unknown): void;
}) {
  const steps = WIZARD_STEPS.filter((s) => s !== 'start' || !id);
  const [step, setStep] = useState<AssistantStep>(steps[0]);
  const [body, setBody] = useState<AssistantBody>(initial ?? blank());
  const [keywords, setKeywords] = useState((initial?.routing_keywords ?? []).join(', '));
  const [tools, setTools] = useState((initial?.allowed_tools ?? ['anum.respond']).join(', '));
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const index = steps.indexOf(step);
  const set = (patch: Partial<AssistantBody>) => setBody((b) => ({ ...b, ...patch }));
  const start = (template?: AssistantTemplate) => { const next = blank(template); setBody(next); setKeywords(next.routing_keywords.join(', ')); setTools(next.allowed_tools.join(', ')); setStep('define'); };
  const next = () => {
    if (step === 'define' && !body.name.trim()) { setProblem(t('team.error.nameRequired')); return; }
    setProblem(null); setStep(steps[Math.min(index + 1, steps.length - 1)]);
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (step !== 'review') { next(); return; }
    if (!body.name.trim()) { setProblem(t('team.error.nameRequired')); setStep('define'); return; }
    const payload = { ...body, name: body.name.trim(), routing_keywords: splitList(keywords), allowed_tools: splitList(tools) };
    setSaving(true); setProblem(null);
    try { onSaved(id ? await team.updateAssistant(id, payload) : await team.createAssistant(payload)); }
    catch (e) { if (isPermissionDenied(e)) onRefused(e); else setProblem(describeError(e)); }
    finally { setSaving(false); }
  };
  const title = id ? t('assistants.edit') : t('assistants.new');
  return <>
    <Breadcrumbs trail={[{ label: viewLabel('assistants'), href: '#assistants', onClick: onCancel }, { label: title }]} />
    <form className="asWizard" aria-label={title} onSubmit={save} noValidate>
      <div className="asWizardHead"><h1 id="view-title">{title}</h1><StatusChip tone="info">{t('asPage.wizard.stepOf', { n: index + 1, total: steps.length })}</StatusChip></div>
      <div className="asWizardGrid">
        <ol className="asSteps" aria-label={t('asPage.wizard.steps')}>{steps.map((s, i) => <li key={s} aria-current={s === step ? 'step' : undefined} className={i < index ? 'done' : ''}>
          <button type="button" disabled={i > index} onClick={() => setStep(s)}><span aria-hidden="true">{i < index ? <Check /> : i + 1}</span><strong>{t(`asPage.step.${s}` as MessageKey)}</strong><small>{t(`asPage.step.${s}Text` as MessageKey)}</small></button>
        </li>)}</ol>
        <FinCard title={t(`asPage.step.${step}` as MessageKey)} className="asWizardBody">
          {step === 'start' && <>
            <p className="muted">{t('pages2.assistants.pickTemplate')}</p>
            <ul className="templateList">{templates.map((tpl) => <li key={tpl.department}>
              <div><strong>{t(`team.dept.${tpl.department}` as MessageKey)}</strong><small>{t(`assistants.autonomy.${tpl.autonomy}` as MessageKey)}</small></div>
              <button type="button" className="secondary" disabled={used.has(tpl.department)} onClick={() => start(tpl)}>{used.has(tpl.department) ? t('assistants.inUse') : t('assistants.useTemplate')}</button>
            </li>)}</ul>
            {!templates.length && <p className="muted">{t('pages2.assistants.noTemplates')}</p>}
            <div className="actions"><button type="button" onClick={() => start()}><Plus aria-hidden="true" />{t('pages2.assistants.blank')}</button></div>
          </>}
          {step === 'define' && <div className="formGrid">
            <label className="field"><span>{t('assistants.name')}</span><input value={body.name} onChange={(e) => set({ name: e.target.value })} maxLength={120} required dir="auto" /></label>
            <label className="field"><span>{t('team.profile.department')}</span><select value={body.department} onChange={(e) => set({ department: e.target.value as Department })}>{DEPARTMENTS.map((d) => <option key={d} value={d}>{t(`team.dept.${d}` as MessageKey)}</option>)}</select></label>
            <label className="field asWide"><span>{t('assistants.instructions')}</span><textarea rows={5} value={body.instructions} onChange={(e) => set({ instructions: e.target.value })} maxLength={8000} dir="auto" /><small className="muted">{t('asPage.wizard.count', { used: body.instructions.length, max: 8000 })}</small></label>
          </div>}
          {step === 'configure' && <>
            <label className="field"><span>{t('assistants.tools')}</span><input value={tools} onChange={(e) => setTools(e.target.value)} aria-describedby="tools-help" dir="ltr" /><small id="tools-help" className="muted">{t('assistants.toolsHelp')}</small></label>
            <label className="field"><span>{t('assistants.keywords')}</span><input value={keywords} onChange={(e) => setKeywords(e.target.value)} aria-describedby="keywords-help" dir="auto" /><small id="keywords-help" className="muted">{t('assistants.keywordsHelp')}</small></label>
            <fieldset className="field assistantChannels">
              <legend>{t('assistants.autoRoute')}</legend>
              <small id="auto-route-help" className="muted">{t('assistants.autoRouteHelp')}</small>
              {AUTO_ROUTE.map((c) => <label key={c} className="settingToggle"><div><strong>{t(`assistants.channel.${c}` as MessageKey)}</strong></div><input type="checkbox" aria-describedby="auto-route-help" checked={(body.inbound_channels ?? []).includes(c)} onChange={(e) => set({ inbound_channels: e.target.checked ? [...(body.inbound_channels ?? []), c] : (body.inbound_channels ?? []).filter((x) => x !== c) })} /></label>)}
            </fieldset>
          </>}
          {step === 'safety' && <>
            <fieldset className="field"><legend>{t('assistants.autonomy')}</legend>
              <div className="asChoices">{AUTONOMY.map((level) => <label key={level} className={`asChoice ${body.autonomy === level ? 'on' : ''}`}>
                <input type="radio" name="autonomy" checked={body.autonomy === level} onChange={() => set({ autonomy: level })} />
                <span><strong>{t(`assistants.autonomy.${level}` as MessageKey)}</strong><small>{t(`assistants.autonomyHelp.${level}` as MessageKey)}</small></span>
              </label>)}</div>
            </fieldset>
            <div className="asBlock asBlocked"><h3><ShieldX aria-hidden="true" />{t('asPage.blocked')}</h3><ul>{assistantGuards(body.autonomy).map((key) => <li key={key}><X aria-hidden="true" /><span>{t(key)}</span></li>)}</ul></div>
            <label className="settingToggle"><div><strong>{t('assistants.enabled')}</strong><p>{t('asPage.wizard.enabledText')}</p></div><input type="checkbox" checked={body.enabled} onChange={(e) => set({ enabled: e.target.checked })} /></label>
          </>}
          {step === 'review' && <dl className="asReview">
            <div><dt>{t('assistants.name')}</dt><dd dir="auto">{body.name || '-'}</dd></div>
            <div><dt>{t('team.profile.department')}</dt><dd>{t(`team.dept.${body.department}` as MessageKey)}</dd></div>
            <div><dt>{t('assistants.instructions')}</dt><dd dir="auto">{body.instructions || '-'}</dd></div>
            <div><dt>{t('assistants.tools')}</dt><dd dir="ltr">{splitList(tools).join(', ') || '-'}</dd></div>
            <div><dt>{t('assistants.keywords')}</dt><dd dir="auto">{splitList(keywords).join(', ') || '-'}</dd></div>
            <div><dt>{t('assistants.autoRoute')}</dt><dd>{(body.inbound_channels ?? []).map((c) => t(`assistants.channel.${c}` as MessageKey)).join(', ') || '-'}</dd></div>
            <div><dt>{t('assistants.autonomy')}</dt><dd>{t(`assistants.autonomy.${body.autonomy}` as MessageKey)}</dd></div>
            <div><dt>{t('assistants.enabled')}</dt><dd>{body.enabled ? t('assistants.on') : t('assistants.off')}</dd></div>
          </dl>}
          {problem && <Notice error>{problem}</Notice>}
          <div className="actions asWizardActions">
            <button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button>
            {index > 0 && <button type="button" className="secondary" onClick={() => setStep(steps[index - 1])}><ArrowLeft aria-hidden="true" className="flipRtl" />{t('asPage.wizard.back')}</button>}
            {step !== 'start' && (step === 'review'
              ? <button type="submit" disabled={saving}><Check aria-hidden="true" />{saving ? t('asPage.wizard.saving') : t('team.save')}</button>
              : <button type="submit"><ArrowRight aria-hidden="true" className="flipRtl" />{t('asPage.wizard.next')}</button>)}
          </div>
        </FinCard>
        <aside className="asWizardSide" aria-label={t('asPage.safety')}>
          <FinCard title={t('asPage.safety')} icon={<ShieldCheck />}>
            <ul className="asSmall"><li>{t('asPage.safety.tools')}</li><li>{t('asPage.safety.approvals')}</li><li>{t('asPage.safety.audit')}</li></ul>
          </FinCard>
          <FinCard title={t('asPage.wizard.toolsTitle')} icon={<Wrench />}><p className="asSmall muted">{t('asPage.wizard.toolsText')}</p></FinCard>
        </aside>
      </div>
    </form>
  </>;
}

function RouteForm({ assistants, onOpenWork }: { assistants: AssistantProfile[]; onOpenWork?(itemId: string): void }) {
  const [channel, setChannel] = useState<InboundChannel>('email');
  const [sender, setSender] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [target, setTarget] = useState('');
  const [result, setResult] = useState<RouteResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!sender.trim() || !body.trim()) { setProblem(t('assistants.route.required')); return; }
    setProblem(null);
    try {
      setResult(await team.routeMessage({ channel, sender: sender.trim(), subject: subject.trim(), body: body.trim(), assistant_id: target || null }));
      setBody(''); setSubject('');
    } catch (e) { setProblem(isPermissionDenied(e) ? t('team.denied', { message: describeError(e) }) : describeError(e)); }
  };
  return <form className="settingsForm routeForm" onSubmit={submit} aria-label={t('assistants.route.title')}>
    <p className="muted">{t('assistants.route.help')}</p>
    <div className="formGrid">
      <label className="field"><span>{t('assistants.route.channel')}</span><select value={channel} onChange={(e) => setChannel(e.target.value as InboundChannel)}>{CHANNELS.map((c) => <option key={c} value={c}>{t(`assistants.channel.${c}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('assistants.route.sender')}</span><input value={sender} onChange={(e) => setSender(e.target.value)} maxLength={200} /></label>
      <label className="field"><span>{t('assistants.route.subject')}</span><input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} /></label>
      <label className="field"><span>{t('assistants.route.to')}</span><select value={target} onChange={(e) => setTarget(e.target.value)}><option value="">{t('assistants.route.auto')}</option>{assistants.filter((a) => a.enabled).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
    </div>
    <label className="field"><span>{t('assistants.route.message')}</span><textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} maxLength={8000} /></label>
    <div className="actions"><button type="submit"><Send aria-hidden="true" />{t('assistants.route.submit')}</button></div>
    {problem && <Notice error>{problem}</Notice>}
    {result && <div className="notice" role="status"><CircleDot aria-hidden="true" /><span>
      {result.assistant ? t('assistants.route.routed', { name: result.assistant.name, keywords: result.matched_keywords.join(', ') || t('assistants.route.explicit') }) : t('assistants.route.triage')}
      {onOpenWork && <> <button type="button" className="secondary linkButton" onClick={() => onOpenWork(result.work_item.id)}>{t('team.openItem')}</button></>}
    </span></div>}
  </form>;
}
