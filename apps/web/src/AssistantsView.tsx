// Department assistants (docs/team-work.md#department-assistants): Sales, Support, Operations and
// warehouse, Finance and Tyre engineering profiles with allowed tools, autonomy, instructions and
// routing keywords; inbound messages routed to them. Owners manage them; the API decides (403).
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Bot, CircleDot, Inbox, Pencil, Plus, Power, RefreshCw, Send, Trash2 } from 'lucide-react';
import type { AssistantBody, AssistantProfile, AssistantTemplate, AutoRouteChannel, Autonomy, Department, InboundChannel, RouteResult } from '@anum/contracts';
import * as team from './lib/teamApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
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

export function AssistantsView({ onOpenWork }: { onOpenWork?(itemId: string): void }) {
  const [assistants, setAssistants] = useState<AssistantProfile[] | null>(null);
  const [templates, setTemplates] = useState<AssistantTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [editing, setEditing] = useState<{ id: string | null; body: AssistantBody } | null>(null);
  const load = useCallback(async () => {
    try {
      const [list, tpl] = await Promise.all([team.getAssistants(), team.getAssistantTemplates()]);
      setAssistants(list); setTemplates(tpl); setError(null);
    } catch (e) { setError(describeError(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const refuse = (e: unknown) => setFlash({ text: isPermissionDenied(e) ? t('assistants.ownersOnly', { message: describeError(e) }) : describeError(e), error: true });
  const toggle = async (a: AssistantProfile) => {
    try {
      const { id: _id, created_by: _by, created_at: _c, updated_at: _u, ...body } = a;
      await team.updateAssistant(a.id, { ...body, enabled: !a.enabled });
      setFlash({ text: t(a.enabled ? 'assistants.turnedOff' : 'assistants.turnedOn', { name: a.name }) });
      await load();
    } catch (e) { refuse(e); }
  };
  const remove = async (a: AssistantProfile) => {
    if (!window.confirm(t('assistants.deleteConfirm', { name: a.name }))) return;
    try { await team.deleteAssistant(a.id); setFlash({ text: t('assistants.deleted', { name: a.name }) }); await load(); } catch (e) { refuse(e); }
  };
  const used = new Set((assistants ?? []).map((a) => a.department));
  return <div className="assistantsView">
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('assistants.eyebrow')}</p><h2>{t('assistants.title')}</h2><p>{t('assistants.intro')}</p></div>
      <div className="actions"><button type="button" onClick={() => setEditing({ id: null, body: blank() })}><Plus aria-hidden="true" />{t('assistants.new')}</button></div>
    </section>
    {error && <section className="surface teamPanel"><Notice error>{t('assistants.loadFailed', { message: error })}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></section>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {editing && <AssistantForm initial={editing.body} id={editing.id} onCancel={() => setEditing(null)} onSaved={(a) => { setEditing(null); setFlash({ text: t('assistants.saved', { name: a.name }) }); void load(); }} onRefused={refuse} />}
    {assistants === null && !error && <p className="muted" role="status">{t('team.loading')}</p>}
    {assistants && !assistants.length && <section className="surface teamPanel"><div className="emptyState"><Bot aria-hidden="true" /><h3>{t('assistants.empty')}</h3><p>{t('assistants.emptyText')}</p></div></section>}
    {templates.length > 0 && <section className="surface teamPanel" aria-labelledby="templates-title">
      <h2 id="templates-title">{t('assistants.templates')}</h2>
      <ul className="templateList">{templates.map((tpl) => <li key={tpl.department}>
        <div><strong>{t(`team.dept.${tpl.department}` as MessageKey)}</strong><small>{t(`assistants.autonomy.${tpl.autonomy}` as MessageKey)}</small></div>
        <button type="button" className="secondary" disabled={used.has(tpl.department)} onClick={() => setEditing({ id: null, body: blank(tpl) })}>{used.has(tpl.department) ? t('assistants.inUse') : t('assistants.useTemplate')}</button>
      </li>)}</ul>
    </section>}
    {assistants && assistants.length > 0 && <ul className="assistantList">{assistants.map((a) => <li key={a.id} className={`surface assistantCard ${a.enabled ? '' : 'featureDisabled'}`}>
      <div className="rowTitle"><h3><Bot aria-hidden="true" className="inlineIcon" />{a.name}</h3><span className={`statusBadge ${a.enabled ? 'success' : 'neutral'}`}>{a.enabled ? t('assistants.on') : t('assistants.off')}</span></div>
      <p className="muted">{t(`team.dept.${a.department}` as MessageKey)} · {t(`assistants.autonomy.${a.autonomy}` as MessageKey)}</p>
      <p className="muted">{t(`assistants.autonomyHelp.${a.autonomy}` as MessageKey)}</p>
      {a.instructions && <p className="instructions">{a.instructions}</p>}
      <div className="tagRow" aria-label={t('assistants.tools')}>{a.allowed_tools.map((x) => <span key={x}>{x}</span>)}</div>
      {a.routing_keywords.length > 0 && <p className="muted">{t('assistants.keywordsList', { keywords: a.routing_keywords.join(', ') })}</p>}
      {(a.inbound_channels ?? []).length > 0 && <p className="muted">{t('assistants.autoRouteList', { channels: (a.inbound_channels ?? []).map((c) => t(`assistants.channel.${c}` as MessageKey)).join(', ') })}</p>}
      <div className="actions">
        <button type="button" className="secondary" onClick={() => { const { id, created_by: _b, created_at: _c, updated_at: _u, ...body } = a; setEditing({ id, body }); }}><Pencil aria-hidden="true" />{t('team.edit')}</button>
        <button type="button" className="secondary" onClick={() => void toggle(a)}><Power aria-hidden="true" />{a.enabled ? t('assistants.turnOff') : t('assistants.turnOn')}</button>
        <button type="button" className="secondary" onClick={() => void remove(a)}><Trash2 aria-hidden="true" />{t('common.delete')}</button>
      </div>
    </li>)}</ul>}
    <RouteForm assistants={assistants ?? []} onOpenWork={onOpenWork} />
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
  return <form className="surface settingsForm" onSubmit={save} aria-labelledby="assistant-form-title">
    <h2 id="assistant-form-title">{id ? t('assistants.edit') : t('assistants.new')}</h2>
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
  return <form className="surface settingsForm" onSubmit={submit} aria-labelledby="route-title">
    <h2 id="route-title"><Inbox aria-hidden="true" className="inlineIcon" />{t('assistants.route.title')}</h2>
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
