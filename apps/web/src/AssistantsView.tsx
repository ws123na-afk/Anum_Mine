// Department assistants (docs/team-work.md#department-assistants): Sales, Support, Operations and
// warehouse, Finance and Tyre engineering profiles with allowed tools, autonomy, instructions and
// routing keywords; inbound messages routed to them. Owners manage them; the API decides (403).
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Bot, CircleDot, Inbox, Pencil, Plus, Power, Search, Send, Trash2 } from 'lucide-react';
import type { AssistantBody, AssistantProfile, AssistantTemplate, AutoRouteChannel, Autonomy, Department, InboundChannel, RouteResult, Workload } from '@anum/contracts';
import * as team from './lib/teamApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { assistantMatches, assistantTotals, workQuery } from './lib/team';
import { figure, type Load } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { ConfirmDialog, Modal } from './Dialog';
import { loadFailure, PageHeader, PageSummary, PaneDenied, PaneProblem } from './PageParts';
import './team.css';

const DEPARTMENTS: Department[] = ['sales', 'support', 'operations', 'finance', 'tyre_engineering', 'custom'];
const AUTONOMY: Autonomy[] = ['observe', 'suggest', 'act_low_risk'];
const CHANNELS: InboundChannel[] = ['email', 'whatsapp', 'marketplace', 'web', 'phone', 'other'];
const AUTO_ROUTE: AutoRouteChannel[] = ['email', 'whatsapp'];
type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

const blank = (template?: AssistantTemplate): AssistantBody => ({
  department: template?.department ?? 'custom', name: template?.name ?? '', instructions: template?.instructions ?? '',
  allowed_tools: template?.allowed_tools ?? ['anum.respond'], routing_keywords: template?.routing_keywords ?? [],
  inbound_channels: [], autonomy: template?.autonomy ?? 'suggest', enabled: true,
});

interface AssistantFigures { open: number; overdue: number; dueWeek: number; done: number | null }

export function AssistantsView({ onOpenWork }: { onOpenWork?(itemId: string): void }) {
  const [assistants, setAssistants] = useState<Load<AssistantProfile[]>>({ state: 'loading' });
  const [templates, setTemplates] = useState<AssistantTemplate[]>([]);
  const [workload, setWorkload] = useState<Load<Workload>>({ state: 'loading' });
  const [done, setDone] = useState<Record<string, number | null>>({});
  const [flash, setFlash] = useState<Flash>(null);
  const [editing, setEditing] = useState<{ id: string | null; body: AssistantBody } | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [removing, setRemoving] = useState<AssistantProfile | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const load = useCallback(async () => {
    team.getWorkload().then((value) => setWorkload({ state: 'ready', value }), (e: unknown) => setWorkload(loadFailure(e)));
    team.getAssistantTemplates().then(setTemplates, () => setTemplates([]));
    try {
      const list = await team.getAssistants();
      setAssistants({ state: 'ready', value: list });
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
      const { id: _id, created_by: _by, created_at: _c, updated_at: _u, ...body } = a;
      await team.updateAssistant(a.id, { ...body, enabled: !a.enabled });
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
  const figuresOf = (a: AssistantProfile): AssistantFigures | null => {
    if (workload.state !== 'ready') return null;
    const row = workload.value.rows.find((r) => r.assignee_type === 'assistant' && r.assignee_id === a.id);
    return { open: row?.open ?? 0, overdue: row?.overdue ?? 0, dueWeek: row?.due_this_week ?? 0, done: done[a.id] ?? null };
  };
  const totals = assistantTotals(list, workload.state === 'ready' ? workload.value.rows : [], done);
  const shown = list.filter((a) => assistantMatches(a, query, (d) => t(`team.dept.${d}` as MessageKey)));
  const both = (a: Load<unknown>, b: Load<unknown>) => ({ state: a.state === 'ready' && b.state === 'ready' ? 'ready' as const : a.state === 'loading' || b.state === 'loading' ? 'loading' as const : 'error' as const });
  const figures = [
    figure('on', t('pages2.assistants.fig.on'), assistants, () => ({ value: `${totals.enabled}`, hint: t('pages2.assistants.fig.onHint', { count: totals.total }), tone: totals.enabled ? 'ok' : '' })),
    figure('queue', t('pages2.assistants.fig.queue'), both(assistants, workload), () => ({ value: String(totals.open), hint: t('pages2.assistants.fig.queueHint', { count: totals.dueWeek }), tone: totals.open ? 'accent' : '' })),
    figure('overdue', t('team.load.overdue'), both(assistants, workload), () => ({ value: String(totals.overdue), hint: totals.overdue ? t('pages2.team.fig.overdueHint') : t('pages2.team.fig.noneOverdue'), tone: totals.overdue ? 'danger' : '' })),
    figure('done', t('pages2.assistants.fig.done'), assistants, () => ({ value: totals.done === null ? '–' : String(totals.done), hint: totals.done === null ? t('pages2.assistants.fig.doneUnknown') : t('pages2.assistants.fig.doneHint') })),
  ];

  return <div className="assistantsView">
    <PageHeader title={viewLabel('assistants')} subtitle={t('assistants.intro')} actions={<button type="button" aria-haspopup="dialog" onClick={() => setChoosing(true)}><Plus aria-hidden="true" />{t('assistants.new')}</button>} />
    {assistants.state !== 'denied' && <PageSummary label={t('pages2.assistants.summary')} figures={figures} onRetry={() => void load()} />}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {assistants.state === 'denied' && <section className="pane"><PaneDenied title={t('pages2.assistants.denied')} text={assistants.message} /></section>}
    {assistants.state === 'error' && <PaneProblem text={t('assistants.loadFailed', { message: assistants.message })} onRetry={() => void load()} />}
    {assistants.state === 'loading' && <p className="muted" role="status">{t('team.loading')}</p>}
    {assistants.state === 'ready' && <section className="pane assistantsTablePane" aria-labelledby="assistants-table-title">
      <div className="paneHead">
        <h2 id="assistants-table-title">{t('pages2.assistants.tableTitle', { count: list.length })}</h2>
        {list.length > 4 && <div className="toolbar"><label className="visuallyHidden" htmlFor="assistants-search">{t('pages2.assistants.search')}</label><input id="assistants-search" type="search" value={query} placeholder={t('pages2.assistants.search')} onChange={(e) => setQuery(e.target.value)} /></div>}
      </div>
      {!list.length && <div className="paneEmpty"><Bot aria-hidden="true" /><h3>{t('assistants.empty')}</h3><p>{t('assistants.emptyText')}</p><div className="actions"><button type="button" onClick={() => setChoosing(true)}><Plus aria-hidden="true" />{t('assistants.new')}</button></div></div>}
      {list.length > 0 && !shown.length && <div className="paneEmpty"><Search aria-hidden="true" /><h3>{t('pages2.assistants.noMatch')}</h3><div className="actions"><button type="button" className="secondary" onClick={() => setQuery('')}>{t('common.clear')}</button></div></div>}
      {shown.length > 0 && <div className="dataTableWrap"><table className="dataTable assistantsTable">
        <thead><tr>
          <th scope="col">{t('assistants.name')}</th><th scope="col">{t('pages2.assistants.col.status')}</th>
          <th scope="col" className="num">{t('pages2.assistants.col.queue')}</th><th scope="col" className="num">{t('team.load.overdue')}</th><th scope="col" className="num">{t('pages2.assistants.col.done')}</th>
          <th scope="col" className="optional">{t('pages2.assistants.col.routes')}</th><th scope="col"><span className="visuallyHidden">{t('pages2.assistants.col.actions')}</span></th>
        </tr></thead>
        <tbody>{shown.map((a) => {
          const f = figuresOf(a);
          const routes = [a.routing_keywords.length ? t('assistants.keywordsList', { keywords: a.routing_keywords.join(', ') }) : null, (a.inbound_channels ?? []).length ? t('assistants.autoRouteList', { channels: (a.inbound_channels ?? []).map((c) => t(`assistants.channel.${c}` as MessageKey)).join(', ') }) : null].filter(Boolean);
          return <tr key={a.id}>
            <th scope="row"><button type="button" className="cellButton" onClick={() => { const { id, created_by: _b, created_at: _c, updated_at: _u, ...body } = a; setEditing({ id, body }); }}>
              <strong><Bot aria-hidden="true" className="inlineIcon" />{a.name}</strong>
              <small>{t(`team.dept.${a.department}` as MessageKey)} · {t(`assistants.autonomy.${a.autonomy}` as MessageKey)}</small>
            </button></th>
            <td><span className={`statusBadge ${a.enabled ? 'success' : 'neutral'}`}>{a.enabled ? t('assistants.on') : t('assistants.off')}</span></td>
            <td className="num">{f ? f.open : '–'}</td>
            <td className={`num ${f?.overdue ? 'overdueCell' : ''}`}>{f ? f.overdue : '–'}</td>
            <td className="num">{f?.done ?? '–'}</td>
            <td className="optional"><small className="assistantRoutes">{routes.length ? routes.join(' · ') : t('pages2.assistants.noRoutes')}</small><span className="tagRow assistantTools" aria-label={t('assistants.tools')}>{a.allowed_tools.map((x) => <span key={x}>{x}</span>)}</span></td>
            <td><div className="cellActions">
              <button type="button" className="iconButton" disabled={busy === a.id} aria-label={t('pages2.assistants.editNamed', { name: a.name })} title={t('team.edit')} onClick={() => { const { id, created_by: _b, created_at: _c, updated_at: _u, ...body } = a; setEditing({ id, body }); }}><Pencil aria-hidden="true" /></button>
              <button type="button" className="iconButton" disabled={busy === a.id} aria-label={t(a.enabled ? 'pages2.assistants.offNamed' : 'pages2.assistants.onNamed', { name: a.name })} title={a.enabled ? t('assistants.turnOff') : t('assistants.turnOn')} onClick={() => void toggle(a)}><Power aria-hidden="true" /></button>
              <button type="button" className="iconButton" disabled={busy === a.id} aria-label={t('pages2.assistants.deleteNamed', { name: a.name })} title={t('common.delete')} onClick={() => setRemoving(a)}><Trash2 aria-hidden="true" /></button>
            </div></td>
          </tr>;
        })}</tbody>
      </table></div>}
      {workload.state === 'error' && <div className="paneBody"><PaneProblem text={t('pages2.assistants.workloadFailed', { message: workload.message })} onRetry={() => void load()} /></div>}
    </section>}
    <details className="surface assistantsRoute">
      <summary><Inbox aria-hidden="true" className="inlineIcon" />{t('assistants.route.title')}<small>{t('pages2.assistants.routeSummary')}</small></summary>
      <RouteForm assistants={list} onOpenWork={onOpenWork} />
    </details>
    {choosing && <Modal title={t('assistants.new')} className="formDialog assistantDialog" onClose={() => setChoosing(false)}>
      <p className="muted">{t('pages2.assistants.pickTemplate')}</p>
      <ul className="templateList">{templates.map((tpl) => <li key={tpl.department}>
        <div><strong>{t(`team.dept.${tpl.department}` as MessageKey)}</strong><small>{t(`assistants.autonomy.${tpl.autonomy}` as MessageKey)}</small></div>
        <button type="button" className="secondary" disabled={used.has(tpl.department)} onClick={() => { setChoosing(false); setEditing({ id: null, body: blank(tpl) }); }}>{used.has(tpl.department) ? t('assistants.inUse') : t('assistants.useTemplate')}</button>
      </li>)}</ul>
      {!templates.length && <p className="muted">{t('pages2.assistants.noTemplates')}</p>}
      <div className="actions"><button type="button" onClick={() => { setChoosing(false); setEditing({ id: null, body: blank() }); }}><Plus aria-hidden="true" />{t('pages2.assistants.blank')}</button><button type="button" className="secondary" onClick={() => setChoosing(false)}>{t('common.cancel')}</button></div>
    </Modal>}
    {editing && <Modal title={editing.id ? t('assistants.edit') : t('assistants.new')} className="formDialog assistantDialog" onClose={() => setEditing(null)}>
      <AssistantForm initial={editing.body} id={editing.id} onCancel={() => setEditing(null)} onSaved={(a) => { setEditing(null); setFlash({ text: t('assistants.saved', { name: a.name }) }); void load(); }} onRefused={(e) => { setEditing(null); refuse(e); }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('pages2.assistants.deleteTitle')} confirmLabel={t('common.delete')} danger busy={busy === removing.id} onCancel={() => setRemoving(null)} onConfirm={() => void remove(removing)}>
      <p>{t('assistants.deleteConfirm', { name: removing.name })}</p>
    </ConfirmDialog>}
  </div>;
}

function AssistantForm({ initial, id, onCancel, onSaved, onRefused }: { initial: AssistantBody; id: string | null; onCancel(): void; onSaved(a: AssistantProfile): void; onRefused(e: unknown): void }) {
  const [body, setBody] = useState<AssistantBody>(initial);
  const [keywords, setKeywords] = useState(initial.routing_keywords.join(', '));
  const [tools, setTools] = useState(initial.allowed_tools.join(', '));
  const [problem, setProblem] = useState<string | null>(null);
  const set = (patch: Partial<AssistantBody>) => setBody((b) => ({ ...b, ...patch }));
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!body.name.trim()) { setProblem(t('team.error.nameRequired')); return; }
    const payload = { ...body, name: body.name.trim(), routing_keywords: keywords.split(',').map((k) => k.trim()).filter(Boolean), allowed_tools: tools.split(',').map((k) => k.trim()).filter(Boolean) };
    try { onSaved(id ? await team.updateAssistant(id, payload) : await team.createAssistant(payload)); }
    catch (e) { if (isPermissionDenied(e)) onRefused(e); else setProblem(describeError(e)); }
  };
  return <form className="settingsForm assistantForm" onSubmit={save}>
    <div className="formGrid">
      <label className="field"><span>{t('assistants.name')}</span><input value={body.name} onChange={(e) => set({ name: e.target.value })} maxLength={120} required /></label>
      <label className="field"><span>{t('team.profile.department')}</span><select value={body.department} onChange={(e) => set({ department: e.target.value as Department })}>{DEPARTMENTS.map((d) => <option key={d} value={d}>{t(`team.dept.${d}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('assistants.autonomy')}</span><select value={body.autonomy} onChange={(e) => set({ autonomy: e.target.value as Autonomy })} aria-describedby="autonomy-help">{AUTONOMY.map((a) => <option key={a} value={a}>{t(`assistants.autonomy.${a}` as MessageKey)}</option>)}</select>
        <small id="autonomy-help" className="muted">{t(`assistants.autonomyHelp.${body.autonomy}` as MessageKey)}</small></label>
      <label className="field"><span>{t('assistants.tools')}</span><input value={tools} onChange={(e) => setTools(e.target.value)} aria-describedby="tools-help" /><small id="tools-help" className="muted">{t('assistants.toolsHelp')}</small></label>
    </div>
    <label className="field"><span>{t('assistants.instructions')}</span><textarea rows={4} value={body.instructions} onChange={(e) => set({ instructions: e.target.value })} maxLength={8000} /></label>
    <label className="field"><span>{t('assistants.keywords')}</span><input value={keywords} onChange={(e) => setKeywords(e.target.value)} aria-describedby="keywords-help" /><small id="keywords-help" className="muted">{t('assistants.keywordsHelp')}</small></label>
    <fieldset className="field assistantChannels">
      <legend>{t('assistants.autoRoute')}</legend>
      <small id="auto-route-help" className="muted">{t('assistants.autoRouteHelp')}</small>
      {AUTO_ROUTE.map((c) => <label key={c} className="settingToggle"><div><strong>{t(`assistants.channel.${c}` as MessageKey)}</strong></div><input type="checkbox" aria-describedby="auto-route-help" checked={(body.inbound_channels ?? []).includes(c)} onChange={(e) => set({ inbound_channels: e.target.checked ? [...(body.inbound_channels ?? []), c] : (body.inbound_channels ?? []).filter((x) => x !== c) })} /></label>)}
    </fieldset>
    <label className="settingToggle"><div><strong>{t('assistants.enabled')}</strong></div><input type="checkbox" checked={body.enabled} onChange={(e) => set({ enabled: e.target.checked })} /></label>
    <div className="actions"><button type="submit">{t('team.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    {problem && <Notice error>{problem}</Notice>}
  </form>;
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
