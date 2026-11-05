// An agent's working memory (docs/agents.md#agent-memories): recurring facts and decisions, each with where it
// comes from and the date it holds from. People who can see the agent read them; its creator or a workspace owner
// adds, edits and deletes them. Expiry follows the organisation's memory governance and is shown on each memory.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { CircleDot, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import * as api from './lib/api';
import {
  agentsApi, emptyMemoryForm, MEMORY_MAX, memoryChanges, memoryCreateBody, memoryFormOf, memoryProblems, mergeById, SOURCE_LABEL_MAX,
  type AgentMemory, type MemoryForm,
} from './lib/agents';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import './agentMemory.css';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);
type KindFilter = 'all' | 'fact' | 'decision';

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** A calendar date (YYYY-MM-DD) shown as that day everywhere; a timestamp shown in the reader's own day. */
function formatDate(value: string): string {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? formatDateTime(`${value}T00:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' })
    : formatDateTime(value, { dateStyle: 'medium' });
}

function explain(error: unknown, action: MessageKey): string {
  return isPermissionDenied(error) ? describeError(error) : t(action, { reason: describeError(error) });
}

function MemoryFields({ form, onChange, idPrefix, contentLabel }: {
  form: MemoryForm; onChange(next: MemoryForm): void; idPrefix: string; contentLabel: string;
}) {
  return <>
    <label className="field"><span>{contentLabel}</span>
      <textarea id={`${idPrefix}-text`} className="short" value={form.content} maxLength={MEMORY_MAX} dir="auto" placeholder={t('agents.memory.placeholder')}
        onChange={(e) => onChange({ ...form, content: e.target.value })} /></label>
    <div className="memoryMeta">
      <label className="field"><span>{t('agents.memory.kind')}</span>
        <select value={form.kind} onChange={(e) => onChange({ ...form, kind: e.target.value as MemoryForm['kind'] })}>
          <option value="fact">{t('agents.memory.kind.fact')}</option>
          <option value="decision">{t('agents.memory.kind.decision')}</option>
        </select></label>
      <label className="field"><span>{t('agents.memory.source')}</span>
        <input type="text" value={form.source_label} maxLength={SOURCE_LABEL_MAX} dir="auto" placeholder={t('agents.memory.sourcePlaceholder')}
          onChange={(e) => onChange({ ...form, source_label: e.target.value })} /></label>
      <label className="field"><span>{t('agents.memory.date')}</span>
        <input type="date" value={form.source_date} max={today()} onChange={(e) => onChange({ ...form, source_date: e.target.value })} /></label>
    </div>
  </>;
}

/** The memory list with search, a kind filter, paging, add, edit and delete. */
export function AgentMemoryPanel({ agentId, canEdit, heading = 'h3' }: { agentId: string; canEdit: boolean; heading?: 'h3' | 'h4' }) {
  const [items, setItems] = useState<AgentMemory[]>([]);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [q, setQ] = useState('');
  const [kind, setKind] = useState<KindFilter>('all');
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<MemoryForm>(emptyMemoryForm);
  const [problems, setProblems] = useState<MessageKey[]>([]);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ memory: AgentMemory; form: MemoryForm; problems: MessageKey[] } | null>(null);
  const [removing, setRemoving] = useState<AgentMemory | null>(null);
  const uid = useId();
  const Heading = heading;
  useEffect(() => { const timer = window.setTimeout(() => setQ(query), 250); return () => window.clearTimeout(timer); }, [query]);
  const load = useCallback(async () => {
    setState('loading'); setError(null);
    try {
      const page = await agents.memories(agentId, q, null, kind === 'all' ? null : kind);
      setItems(page.items); setTotal(page.total); setCursor(page.next_cursor); setState('ready');
    } catch (e) {
      if (isPermissionDenied(e)) { setError(describeError(e)); setState('denied'); return; }
      setError(explain(e, 'agents.err.memories')); setState('error');
    }
  }, [agentId, q, kind]);
  useEffect(() => { void load(); }, [load]);

  async function add(event: FormEvent) {
    event.preventDefault();
    const found = memoryProblems(form, today());
    setProblems(found);
    if (found.length) return;
    setBusy(true); setError(null);
    try { await agents.addMemory(agentId, memoryCreateBody(form)); setForm(emptyMemoryForm()); await load(); }
    catch (e) { setError(explain(e, 'agents.err.memorySave')); }
    finally { setBusy(false); }
  }
  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const found = memoryProblems(editing.form, today());
    if (found.length) { setEditing({ ...editing, problems: found }); return; }
    const changes = memoryChanges(editing.memory, editing.form);
    if (!changes) { setEditing(null); return; }
    setBusy(true); setError(null);
    try { await agents.updateMemory(agentId, editing.memory.id, changes); setEditing(null); await load(); }
    catch (e) { setError(explain(e, 'agents.err.memorySave')); }
    finally { setBusy(false); }
  }
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try { await agents.removeMemory(agentId, removing.id); setRemoving(null); await load(); }
    catch (e) { setError(explain(e, 'agents.err.memoryDelete')); setRemoving(null); }
    finally { setBusy(false); }
  }
  async function loadMore() {
    if (!cursor) return;
    try { const page = await agents.memories(agentId, q, cursor, kind === 'all' ? null : kind); setItems((old) => mergeById(old, page.items)); setCursor(page.next_cursor); }
    catch (e) { setError(explain(e, 'agents.err.memories')); }
  }
  const origin = (m: AgentMemory) => m.source_label || (m.source === 'task' ? t('agents.memory.fromTask') : m.source === 'file' ? t('agents.memory.fromFile') : t('agents.memory.manual'));

  return <section className="surface agentPanel amPanel" aria-labelledby={`${uid}-h`}>
    <div className="sectionHeader"><Heading id={`${uid}-h`}>{t('agents.memory.title')}</Heading><span className="muted">{t('agents.memory.count', { count: total })}</span></div>
    <p className="muted">{t('agents.memory.intro')}</p>
    {canEdit ? <form className="memoryAdd" onSubmit={(e) => void add(e)} noValidate>
      <MemoryFields form={form} onChange={(next) => { setForm(next); setProblems([]); }} idPrefix={`${uid}-add`} contentLabel={t('agents.memory.add')} />
      {problems.length > 0 && <ul className="fieldErrors" role="alert">{problems.map((p) => <li key={p}>{t(p)}</li>)}</ul>}
      <div className="actions"><button type="submit" disabled={busy || !form.content.trim()}><Plus aria-hidden="true" />{t('agents.memory.save')}</button></div>
    </form> : <p className="muted">{t('agents.memory.readOnly')}</p>}
    <div className="memoryFilters">
      <div className="agentSearch memorySearch"><label className="visuallyHidden" htmlFor={`${uid}-q`}>{t('agents.memory.search')}</label><Search aria-hidden="true" />
        <input id={`${uid}-q`} type="search" value={query} placeholder={t('agents.memory.search')} onChange={(e) => setQuery(e.target.value)} /></div>
      <label className="memoryKindFilter"><span className="visuallyHidden">{t('agents.memory.filter')}</span>
        <select value={kind} aria-label={t('agents.memory.filter')} onChange={(e) => setKind(e.target.value as KindFilter)}>
          <option value="all">{t('agents.memory.filter.all')}</option>
          <option value="fact">{t('agents.memory.kind.fact')}</option>
          <option value="decision">{t('agents.memory.kind.decision')}</option>
        </select></label>
    </div>
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
    {state === 'error' && <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button>}
    {state === 'ready' && items.length === 0 && <p className="muted">{q || kind !== 'all' ? t('agents.memory.noMatch') : canEdit ? t('agents.memory.emptyEdit') : t('agents.memory.empty')}</p>}
    {items.length > 0 && <ul className="agentMemoryList">{items.map((memory) => <li key={memory.id} data-kind={memory.kind ?? 'fact'}>
      {editing?.memory.id === memory.id ? <form className="memoryEdit" onSubmit={(e) => void saveEdit(e)} noValidate>
        <MemoryFields form={editing.form} onChange={(next) => setEditing({ ...editing, form: next, problems: [] })} idPrefix={`${uid}-${memory.id}`} contentLabel={t('agents.memory.editText')} />
        {editing.problems.length > 0 && <ul className="fieldErrors" role="alert">{editing.problems.map((p) => <li key={p}>{t(p)}</li>)}</ul>}
        <div className="actions"><button type="submit" disabled={busy}>{t('agents.memory.saveEdit')}</button>
          <button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button></div>
      </form> : <>
        <div>
          <p dir="auto">{memory.content}</p>
          <small className="muted">
            <span className={`memoryKind ${memory.kind ?? 'fact'}`}>{t(memory.kind === 'decision' ? 'agents.memory.kind.decision' : 'agents.memory.kind.fact')}</span>
            {' · '}<span dir="auto">{origin(memory)}</span>
            {memory.source_date && <>{' · '}{t('agents.memory.asOf', { date: formatDate(memory.source_date) })}</>}
            {' · '}<time dateTime={memory.updated_at ?? memory.created_at}>{formatDateTime(memory.updated_at ?? memory.created_at)}</time>
            {memory.updated_at && <>{' · '}{t('agents.memory.edited')}</>}
            {' · '}{memory.expires_at ? t('agents.memory.expires', { date: formatDate(memory.expires_at) }) : t('agents.memory.kept')}
          </small>
        </div>
        {canEdit && <div className="memoryActions">
          <button type="button" className="iconButton" aria-label={t('agents.memory.editNamed', { text: memory.content.slice(0, 40) })} title={t('agents.memory.edit')}
            onClick={() => setEditing({ memory, form: memoryFormOf(memory), problems: [] })}><Pencil aria-hidden="true" /></button>
          <button type="button" className="iconButton" aria-label={t('agents.memory.deleteNamed', { text: memory.content.slice(0, 40) })} title={t('common.delete')} onClick={() => setRemoving(memory)}><Trash2 aria-hidden="true" /></button>
        </div>}
      </>}
    </li>)}</ul>}
    {cursor && <div className="actions"><button type="button" className="secondary" onClick={() => void loadMore()}>{t('agents.loadMore')}</button></div>}
    {removing && <ConfirmDialog title={t('agents.memory.deleteTitle')} confirmLabel={t('common.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p dir="auto">{removing.content}</p>
    </ConfirmDialog>}
  </section>;
}
