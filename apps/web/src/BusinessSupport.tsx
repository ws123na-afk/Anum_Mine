// Business support tickets (docs/business.md#support-tickets): the queue with its summary, filters
// and paging, a ticket's detail with status, escalation, notes, the SLA in working hours and replies
// that only ever wait for an owner in Approvals, and the support policy in Settings.
// A ticket links to the email or WhatsApp conversation it came from; the text stays in the inbox.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { AlertTriangle, ExternalLink, Fingerprint, Flag, Plus, Search, ShieldAlert, Trash2, UserCheck } from 'lucide-react';
import type {
  BusinessAccountRow, BusinessContact, BusinessProduct, BusinessProject, BusinessSetup, ResolutionCode, SupportAssignee, SupportPolicy, SupportSetup,
  SupportSummary, SupportTicket, TicketDetail, TicketEntitlement, TicketFields, TicketReply, TicketSeverity, TicketStatus,
} from '@anum/contracts';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatNumber, t } from './lib/i18n';
import { PAGE_SIZE } from './lib/business';
import {
  ENTITLEMENTS, MIN_RESOLUTION_SUMMARY, RESOLUTION_CODES, SEVERITIES, TICKET_STATUSES, WEEKDAYS, defaultFilters, emptyTicketDraft, entitlementLabel, filtersActive,
  filtersToQuery, policyBody, policyDraft, replyStatusLabel, replyStatusTone, resolutionLabel, resolveProblem, severityLabel, severityTone, slaBadge, slaLabel,
  slaTone, sourceHref, sourceLabel, ticketBody, ticketStatusLabel, ticketStatusTone, weekdayLabel, workingTimeText, type PolicyDraft, type TicketDraft,
  type TicketFilters,
} from './lib/businessSupport';
import { shortHash } from './lib/approvals';
import * as b from './lib/businessApi';
import { figure, type Load } from './lib/pageSummary';
import { PageSummary } from './PageParts';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { Badge, Denied, Empty, Failed, Note, Pager, ReasonDialog, failure, when } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';

type SetupState = { kind: 'loading' } | { kind: 'ready'; value: SupportSetup } | { kind: 'denied'; text: string } | { kind: 'error'; text: string };

function useSupportSetup() {
  const [state, setState] = useState<SetupState>({ kind: 'loading' });
  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try { setState({ kind: 'ready', value: await b.getSupportSetup() }); }
    catch (error) { setState(isPermissionDenied(error) ? { kind: 'denied', text: describeError(error) } : { kind: 'error', text: describeError(error) }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return { state, load };
}

const assigneeName = (assignees: SupportAssignee[], userId: string | null) => {
  if (!userId) return t('biz.sup.unassigned');
  const found = assignees.find((item) => item.user_id === userId);
  return found?.you ? t('biz.sup.you', { id: userId }) : userId;
};

// ---- the queue ---------------------------------------------------------------------------------

export function SupportTab({ setup, products, openId, onOpen, onChanged }: {
  setup: BusinessSetup; products: BusinessProduct[]; openId: string | null; onOpen: (id: string | null) => void; onChanged: () => void;
}) {
  const support = useSupportSetup();
  const [assignees, setAssignees] = useState<SupportAssignee[]>([]);
  const [summary, setSummary] = useState<Load<SupportSummary>>({ state: 'loading' });
  const [draft, setDraft] = useState<TicketFilters>(defaultFilters), [filters, setFilters] = useState<TicketFilters>(defaultFilters);
  const [creating, setCreating] = useState(false);
  const ready = support.state.kind === 'ready';
  const ids = { q: useId(), status: useId(), severity: useId(), who: useId(), sort: useId() };

  const loadSummary = useCallback(async () => {
    setSummary({ state: 'loading' });
    try { setSummary({ state: 'ready', value: await b.getSupportSummary() }); }
    catch (error) { setSummary({ state: 'error', message: describeError(error) }); }
  }, []);
  useEffect(() => {
    if (!ready) return;
    void loadSummary();
    void b.listAssignees().then(setAssignees).catch(() => setAssignees([]));
  }, [ready, loadSummary]);
  const key = JSON.stringify(filters);
  const list = usePaged((offset) => (ready ? b.listTickets(filtersToQuery(filters, PAGE_SIZE, offset)) : Promise.resolve({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 })), [key, ready]);
  const refresh = () => { void list.first(); void loadSummary(); onChanged(); };

  if (support.state.kind === 'loading') return <Note>{t('biz.loading')}</Note>;
  if (support.state.kind === 'denied') return <Denied text={support.state.text} />;
  if (support.state.kind === 'error') return <Failed text={support.state.text} onRetry={() => void support.load()} />;
  const sup = support.state.value;
  const value = summary.state === 'ready' ? summary.value : null;
  const figures = [
    figure('open', t('biz.sup.sum.open'), summary, () => ({ value: formatNumber(value!.open) })),
    figure('new', t('biz.sup.sum.new'), summary, () => ({ value: formatNumber(value!.new), tone: value!.new ? 'warn' : '' })),
    figure('unassigned', t('biz.sup.sum.unassigned'), summary, () => ({ value: formatNumber(value!.unassigned), tone: value!.unassigned ? 'warn' : '' })),
    figure('escalated', t('biz.sup.sum.escalated'), summary, () => ({ value: formatNumber(value!.escalated), tone: value!.escalated ? 'danger' : '' })),
    figure('breached', t('biz.sup.sum.breached'), summary, () => ({ value: formatNumber(value!.breached), tone: value!.breached ? 'danger' : 'ok' })),
    figure('waiting', t('biz.sup.sum.waiting'), summary, () => ({ value: formatNumber(value!.waiting_on_customer) })),
  ];
  const apply = (next: TicketFilters) => { setDraft(next); setFilters(next); };
  const filtered = filtersActive(filters);
  const open = list.items.find((item) => item.id === openId) ?? null;

  return <section className="bzSection" aria-label={t('biz.tab.support')}>
    <PageSummary label={t('biz.sup.sum.label')} figures={figures} onRetry={() => void loadSummary()} />
    <p className="muted bzHint">{t('biz.sup.workingHoursOnly', { time: workingTimeText(sup.policy) })}</p>
    {value?.truncated && <Note>{t('biz.sup.sum.truncated')}</Note>}
    <div className="bzToolbar bzSupportToolbar">
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setFilters({ ...filters, q: draft.q.trim() }); }}>
        <label className="visuallyHidden" htmlFor={ids.q}>{t('biz.sup.search')}</label>
        <input id={ids.q} type="search" value={draft.q} placeholder={t('biz.sup.search')} onChange={(event) => setDraft({ ...draft, q: event.target.value })} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
      </form>
      <label className="bzCompact" htmlFor={ids.status}><span>{t('biz.sup.filter.status')}</span>
        <select id={ids.status} value={filters.status} onChange={(event) => apply({ ...filters, status: event.target.value as TicketFilters['status'] })}>
          <option value="open">{t('biz.sup.filter.openOnly')}</option><option value="all">{t('biz.sup.filter.all')}</option>
          {TICKET_STATUSES.map((item) => <option key={item} value={item}>{ticketStatusLabel(item)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.severity}><span>{t('biz.sup.field.severity')}</span>
        <select id={ids.severity} value={filters.severity} onChange={(event) => apply({ ...filters, severity: event.target.value as TicketFilters['severity'] })}>
          <option value="">{t('biz.sup.filter.anySeverity')}</option>{SEVERITIES.map((item) => <option key={item} value={item}>{severityLabel(item)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.who}><span>{t('biz.sup.filter.who')}</span>
        <select id={ids.who} value={filters.who} onChange={(event) => apply({ ...filters, who: event.target.value as TicketFilters['who'] })}>
          <option value="all">{t('biz.sup.filter.anyone')}</option><option value="me">{t('biz.sup.filter.me')}</option><option value="unassigned">{t('biz.sup.unassigned')}</option></select></label>
      <label className="bzCompact" htmlFor={ids.sort}><span>{t('biz.sup.sort.label')}</span>
        <select id={ids.sort} value={filters.sort} onChange={(event) => apply({ ...filters, sort: event.target.value as TicketFilters['sort'] })}>
          <option value="updated">{t('biz.sup.sort.updated')}</option><option value="due">{t('biz.sup.sort.due')}</option><option value="created">{t('biz.sup.sort.created')}</option></select></label>
      <label className="bzCheck"><input type="checkbox" checked={filters.escalated} onChange={(event) => apply({ ...filters, escalated: event.target.checked })} />{t('biz.sup.filter.escalated')}</label>
      {sup.can_write && <div className="bzToolbarEnd"><button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('biz.sup.new')}</button></div>}
    </div>
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => apply(defaultFilters())} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('biz.sup.emptyTitle')} text={t('biz.sup.emptyText')}
      action={<>{sup.can_write && <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('biz.sup.new')}</button>}
        <a className="button secondary" href="#email">{t('biz.sup.openEmail')}</a><a className="button secondary" href="#whatsapp">{t('biz.sup.openWhatsapp')}</a></>} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('biz.tab.support')}</caption>
        <thead><tr><th scope="col">{t('biz.sup.col.ticket')}</th><th scope="col">{t('biz.sup.field.severity')}</th><th scope="col">{t('biz.col.state')}</th>
          <th scope="col">{t('biz.sup.col.sla')}</th><th scope="col" className="bzHideSm">{t('biz.sup.col.requester')}</th><th scope="col" className="bzHideMd">{t('biz.sup.field.assignee')}</th></tr></thead>
        <tbody>{list.items.map((item) => <TicketRow key={item.id} item={item} selected={item.id === openId} assignees={assignees} onOpen={() => onOpen(item.id)} />)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {creating && <NewTicket sup={sup} products={products} assignees={assignees} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); refresh(); onOpen(id); }} />}
    {openId && <TicketDrawer setup={setup} sup={sup} products={products} assignees={assignees} initial={open} id={openId} onClose={() => onOpen(null)} onChanged={refresh} />}
  </section>;
}

function TicketRow({ item, selected, assignees, onOpen }: { item: SupportTicket; selected: boolean; assignees: SupportAssignee[]; onOpen: () => void }) {
  const sla = slaBadge(item.sla);
  return <tr className={selected ? 'selected' : undefined}>
    <th scope="row"><button type="button" className="linkButton" onClick={onOpen}><span className="bzRef">{item.reference}</span> <span dir="auto">{item.title}</span></button>
      <small dir="auto">{[item.account_name, item.escalated ? t('biz.sup.escalatedMark') : null].filter(Boolean).join(' · ') || sourceLabel(item.source)}</small></th>
    <td><Badge tone={severityTone(item.severity)}>{severityLabel(item.severity)}</Badge></td>
    <td><Badge tone={ticketStatusTone(item.status)}>{ticketStatusLabel(item.status)}</Badge></td>
    <td><Badge tone={sla.tone}>{sla.label}</Badge>{item.escalated && <Flag className="bzInlineIcon bzFlag" aria-label={t('biz.sup.escalatedMark')} />}</td>
    <td className="bzHideSm"><span dir="auto">{item.requester_name || item.requester_email || item.requester_phone || '-'}</span></td>
    <td className="bzHideMd">{assigneeName(assignees, item.assignee_id)}</td>
  </tr>;
}

// ---- link pickers (accounts, contacts, projects, support plans) ---------------------------------

function useLinkOptions(accountId: string) {
  const [accounts, setAccounts] = useState<{ items: BusinessAccountRow[]; total: number }>({ items: [], total: 0 });
  const [projects, setProjects] = useState<{ items: BusinessProject[]; total: number }>({ items: [], total: 0 });
  const [contacts, setContacts] = useState<BusinessContact[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void Promise.all([b.listAccounts({ limit: 100, offset: 0 }), b.listProjects({ limit: 100, offset: 0 })])
      .then(([a, p]) => { setAccounts({ items: a.items, total: a.total }); setProjects({ items: p.items, total: p.total }); })
      .catch((reason) => setError(describeError(reason)));
  }, []);
  useEffect(() => {
    if (!accountId) { setContacts([]); return; }
    void b.getAccount(accountId).then((detail) => setContacts(detail.contacts)).catch(() => setContacts([]));
  }, [accountId]);
  return { accounts, projects, contacts, error };
}

const plansOf = (products: BusinessProduct[]) => products.filter((item) => item.active_offer).map((item) => ({ id: item.active_offer!.id, label: `${item.name} v${item.active_offer!.version}` }));

function TicketFieldsForm({ draft, set, products, assignees, disabled }: {
  draft: TicketDraft; set: <K extends keyof TicketDraft>(key: K, value: TicketDraft[K]) => void; products: BusinessProduct[]; assignees: SupportAssignee[]; disabled?: boolean;
}) {
  const links = useLinkOptions(draft.accountId);
  const plans = plansOf(products);
  const projects = draft.accountId ? links.projects.items.filter((item) => item.account_id === draft.accountId) : links.projects.items;
  return <>
    <div className="bzGrid2">
      <label className="field bzSpan2"><span>{t('biz.sup.field.title')}</span><input value={draft.title} disabled={disabled} onChange={(event) => set('title', event.target.value)} required minLength={3} maxLength={200} data-autofocus /></label>
      <label className="field bzSpan2"><span>{t('biz.sup.field.description')}</span><textarea rows={3} value={draft.description} disabled={disabled} maxLength={4000} onChange={(event) => set('description', event.target.value)} /></label>
      <label className="field"><span>{t('biz.sup.field.severity')}</span><select value={draft.severity} disabled={disabled} onChange={(event) => set('severity', event.target.value as TicketSeverity)}>
        {SEVERITIES.map((item) => <option key={item} value={item}>{severityLabel(item)}</option>)}</select></label>
      <label className="field"><span>{t('biz.sup.field.assignee')}</span><select value={draft.assigneeId} disabled={disabled} onChange={(event) => set('assigneeId', event.target.value)}>
        <option value="">{t('biz.sup.unassigned')}</option>
        {assignees.map((item) => <option key={item.user_id} value={item.user_id}>{item.you ? t('biz.sup.you', { id: item.user_id }) : `${item.user_id} (${item.role})`}</option>)}
        {draft.assigneeId && !assignees.some((item) => item.user_id === draft.assigneeId) && <option value={draft.assigneeId}>{draft.assigneeId}</option>}</select></label>
    </div>
    <fieldset className="bzFieldset"><legend>{t('biz.sup.requester')}</legend>
      <div className="bzGrid3">
        <label className="field"><span>{t('biz.sup.field.requesterName')}</span><input value={draft.requesterName} disabled={disabled} onChange={(event) => set('requesterName', event.target.value)} maxLength={160} /></label>
        <label className="field"><span>{t('biz.field.email')}</span><input type="email" value={draft.requesterEmail} disabled={disabled} onChange={(event) => set('requesterEmail', event.target.value)} maxLength={254} /></label>
        <label className="field"><span>{t('biz.field.phone')}</span><input inputMode="tel" value={draft.requesterPhone} disabled={disabled} onChange={(event) => set('requesterPhone', event.target.value)} maxLength={40} /></label>
      </div>
      <p className="muted bzHint">{t('biz.sup.requesterHint')}</p>
    </fieldset>
    <fieldset className="bzFieldset"><legend>{t('biz.sup.links')}</legend>
      <div className="bzGrid3">
        <label className="field"><span>{t('biz.sup.field.account')}</span><select value={draft.accountId} disabled={disabled} onChange={(event) => { set('accountId', event.target.value); set('contactId', ''); set('projectId', ''); }}>
          <option value="">{t('biz.sup.none')}</option>{links.accounts.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field"><span>{t('biz.sup.field.contact')}</span><select value={draft.contactId} disabled={disabled || !draft.accountId} onChange={(event) => set('contactId', event.target.value)}>
          <option value="">{draft.accountId ? t('biz.sup.none') : t('biz.sup.pickAccountFirst')}</option>{links.contacts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field"><span>{t('biz.sup.field.project')}</span><select value={draft.projectId} disabled={disabled} onChange={(event) => set('projectId', event.target.value)}>
          <option value="">{t('biz.sup.none')}</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </div>
      {(links.accounts.total > 100 || links.projects.total > 100) && <p className="muted bzHint">{t('biz.sup.firstHundred')}</p>}
      {links.error && <Note error>{t('biz.sup.linksFailed', { message: links.error })}</Note>}
    </fieldset>
    <fieldset className="bzFieldset"><legend>{t('biz.sup.field.entitlement')}</legend>
      <div className="bzGrid3">
        <label className="field"><span>{t('biz.sup.field.coveredBy')}</span><select value={draft.entitlement} disabled={disabled} onChange={(event) => set('entitlement', event.target.value as TicketEntitlement)}>
          {ENTITLEMENTS.map((item) => <option key={item} value={item}>{entitlementLabel(item)}</option>)}</select></label>
        {draft.entitlement === 'support_plan' && <label className="field"><span>{t('biz.sup.field.plan')}</span><select value={draft.offerId} disabled={disabled} onChange={(event) => set('offerId', event.target.value)}>
          <option value="">{t('biz.sup.choose')}</option>{plans.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
        <label className="field"><span>{t('biz.sup.field.entitlementNote')}</span><input value={draft.entitlementNote} disabled={disabled} maxLength={300} onChange={(event) => set('entitlementNote', event.target.value)} /></label>
      </div>
      {draft.entitlement === 'support_plan' && plans.length === 0 && <p className="muted bzHint">{t('biz.sup.noPlans')}</p>}
      {draft.entitlement === 'project' && <p className="muted bzHint">{t('biz.sup.projectCovers')}</p>}
    </fieldset>
  </>;
}

function useDraft(initial: () => TicketDraft) {
  const [draft, setDraft] = useState<TicketDraft>(initial);
  const set = useCallback(<K extends keyof TicketDraft>(key: K, value: TicketDraft[K]) => setDraft((current) => ({ ...current, [key]: value })), []);
  return { draft, setDraft, set };
}

function NewTicket({ sup, products, assignees, onClose, onCreated }: { sup: SupportSetup; products: BusinessProduct[]; assignees: SupportAssignee[]; onClose: () => void; onCreated: (id: string) => void }) {
  const { draft, set } = useDraft(emptyTicketDraft);
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const target = sup.policy.targets[draft.severity];
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = ticketBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onCreated((await b.createTicket(body.value)).id); } catch (error) { setProblem(failure(error, t('biz.sup.new'))); setBusy(false); }
  }
  return <Modal title={t('biz.sup.new')} className="formDialog bzWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('biz.sup.new')} className="bzForm">
      <TicketFieldsForm draft={draft} set={set} products={products} assignees={assignees} />
      {target && <p className="muted bzHint">{t('biz.sup.targetHint', { response: target.response_hours, resolution: target.resolution_hours })}</p>}
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('biz.sup.create')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- one ticket --------------------------------------------------------------------------------

const draftOf = (item: SupportTicket): TicketDraft => ({
  title: item.title, description: item.description, severity: item.severity, requesterName: item.requester_name, requesterEmail: item.requester_email ?? '',
  requesterPhone: item.requester_phone ?? '', accountId: item.account_id ?? '', contactId: item.contact_id ?? '', projectId: item.project_id ?? '',
  entitlement: item.entitlement, offerId: item.support_offer_id ?? '', entitlementNote: item.entitlement_note, assigneeId: item.assignee_id ?? '',
});

/** Only the fields that changed, so the trail names what really changed. */
function changedFields(before: TicketDraft, body: TicketFields): TicketFields {
  const original = ticketBody(before);
  if (!original.ok) return body;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(body)) if ((original.value as unknown as Record<string, unknown>)[key] !== value) out[key] = value;
  return out as TicketFields;
}

function moveLabel(from: TicketStatus, to: TicketStatus) {
  if ((from === 'resolved' || from === 'closed') && to === 'in_progress') return t('biz.sup.move.reopen');
  return t(`biz.sup.move.${to}`);
}

function TicketDrawer({ setup, sup, products, assignees, initial, id, onClose, onChanged }: {
  setup: BusinessSetup; sup: SupportSetup; products: BusinessProduct[]; assignees: SupportAssignee[]; initial: SupportTicket | null; id: string; onClose: () => void; onChanged: () => void;
}) {
  const [item, setItem] = useState<TicketDetail | null>(null), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [asking, setAsking] = useState<'resolve' | 'escalate' | 'deescalate' | 'delete' | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const load = useCallback(() => { setLoadError(null); void b.getTicket(id).then(setItem).catch((error) => setLoadError(failure(error, t('biz.sup.load')))); }, [id]);
  useEffect(() => { load(); }, [load]);
  const title = item?.title ?? initial?.title ?? t('biz.sup.ticket');
  if (!item) return <Drawer title={title} onClose={onClose}>{loadError ? <><Note error>{loadError}</Note><div className="bzInline"><button type="button" className="secondary" onClick={load}>{t('common.tryAgain')}</button></div></> : <Note>{t('biz.loading')}</Note>}</Drawer>;
  const write = sup.can_write && setup.permissions.write;
  const closed = item.status === 'closed';
  const me = assignees.find((person) => person.you);

  async function run(what: string, fn: () => Promise<TicketDetail>, done?: string) {
    setBusy(true); setProblem(null); setMessage(null);
    try { setItem(await fn()); onChanged(); if (done) setMessage(done); } catch (error) { setProblem(failure(error, what)); } finally { setBusy(false); }
  }
  const sla = slaBadge(item.sla);
  const href = sourceHref(item.source);

  return <Drawer title={title} onClose={onClose}>
    <div className="bzDrawer">
      <div className="bzLeadHead">
        <span className="bzRef">{item.reference}</span>
        <Badge tone={severityTone(item.severity)}>{severityLabel(item.severity)}</Badge>
        <Badge tone={ticketStatusTone(item.status)}>{ticketStatusLabel(item.status)}</Badge>
        <Badge tone={sla.tone}>{t('biz.sup.slaBadge', { state: sla.label })}</Badge>
        {item.escalated && <span className="bzEscalated"><Flag aria-hidden="true" />{t('biz.sup.escalatedMark')}</span>}
      </div>
      {problem && <Note error>{problem}</Note>}
      {message && <Note>{message}</Note>}

      {write && <div className="bzInline" role="group" aria-label={t('biz.sup.actions')}>
        {item.allowed.map((to) => <button key={to} type="button" className={to === 'resolved' ? undefined : 'secondary'} disabled={busy}
          onClick={() => (to === 'resolved' ? setAsking('resolve') : void run(moveLabel(item.status, to), () => b.changeTicketStatus(item.id, { to })))}>{moveLabel(item.status, to)}</button>)}
        {me && item.assignee_id !== me.user_id && !closed && <button type="button" className="secondary" disabled={busy} onClick={() => void run(t('biz.sup.assignMe'), () => b.updateTicket(item.id, { assignee_id: me.user_id }))}><UserCheck aria-hidden="true" />{t('biz.sup.assignMe')}</button>}
        {sup.open_statuses.includes(item.status) && (item.escalated
          ? <button type="button" className="secondary" disabled={busy} onClick={() => setAsking('deescalate')}>{t('biz.sup.deescalate')}</button>
          : <button type="button" className="secondary" disabled={busy} onClick={() => setAsking('escalate')}><Flag aria-hidden="true" />{t('biz.sup.escalate')}</button>)}
      </div>}
      {write && closed && !sup.can_manage && <p className="muted bzHint">{t('biz.sup.reopenOwners')}</p>}
      {!write && <p className="muted">{t('biz.sup.readOnly')}</p>}
      {item.escalated && item.escalation_reason && <p className="bzHint" dir="auto">{t('biz.sup.escalatedBecause', { reason: item.escalation_reason, by: item.escalated_by ?? '-', when: when(item.escalated_at) })}</p>}
      {item.resolution_code && <p className="bzHint" dir="auto">{t('biz.sup.resolvedAs', { code: resolutionLabel(item.resolution_code), summary: item.resolution_summary })}</p>}

      <section className="bzBlock" aria-labelledby="bz-sla"><h3 id="bz-sla">{t('biz.sup.sla.title')}</h3>
        <dl className="bzFacts">
          <div><dt>{t('biz.sup.sla.reported')}</dt><dd>{when(item.reported_at)}</dd></div>
          <div><dt>{t('biz.sup.sla.responseDue')}</dt><dd>{when(item.response_due_at)} <Badge tone={slaTone(item.sla.response)}>{slaLabel(item.sla.response)}</Badge></dd></div>
          <div><dt>{t('biz.sup.sla.firstResponse')}</dt><dd>{item.first_responded_at ? when(item.first_responded_at) : t('biz.sup.sla.notYet')}</dd></div>
          <div><dt>{t('biz.sup.sla.resolutionDue')}</dt><dd>{when(item.resolution_due_at)} <Badge tone={slaTone(item.sla.resolution)}>{slaLabel(item.sla.resolution)}</Badge></dd></div>
          {item.sla_paused_at && <div><dt>{t('biz.sup.sla.pausedSince')}</dt><dd>{when(item.sla_paused_at)}</dd></div>}
          {item.sla_paused_minutes > 0 && <div><dt>{t('biz.sup.sla.pausedTotal')}</dt><dd>{t('biz.sup.sla.minutes', { count: item.sla_paused_minutes })}</dd></div>}
        </dl>
        {item.sla.breached && <Note error>{t('biz.sup.sla.breachedText')}</Note>}
        <p className="muted bzHint">{t('biz.sup.workingHoursOnly', { time: workingTimeText(sup.policy) })}</p>
      </section>

      <section className="bzBlock" aria-labelledby="bz-source"><h3 id="bz-source">{t('biz.sup.source.title')}</h3>
        {href ? <p className="bzHint"><a href={href}><ExternalLink className="bzInlineIcon" aria-hidden="true" />{t('biz.sup.source.open', { source: sourceLabel(item.source) })}</a> <span className="muted">{t('biz.sup.source.textStays')}</span></p>
          : <p className="muted bzHint">{t('biz.sup.source.manualText')}</p>}
      </section>

      <TicketDetails key={item.updated_at} item={item} products={products} assignees={assignees} disabled={!write || closed || busy}
        onSave={(body) => run(t('biz.sup.details'), () => b.updateTicket(item.id, body), t('biz.sup.saved'))} />

      <Replies item={item} sup={sup} write={write} busy={busy} setBusy={setBusy} onTicket={(next, done) => { setItem(next); onChanged(); setMessage(done ?? null); }} onProblem={setProblem} />

      <NoteForm disabled={!write || busy} onAdd={(text, responded) => run(t('biz.sup.note.add'), () => b.addTicketNote(item.id, text, responded), t('biz.sup.note.added'))} />

      <section className="bzBlock" aria-labelledby="bz-ticket-trail"><h3 id="bz-ticket-trail">{t('biz.activity.title')}</h3>
        {item.activities.length === 0 ? <p className="muted">{t('biz.activity.empty')}</p>
          : <ol className="bzTrail">{item.activities.map((row) => <li key={row.id}><span dir="auto">{row.summary}</span><small>{when(row.created_at)} · {row.actor}</small></li>)}</ol>}
      </section>

      {sup.can_manage && <div className="bzDanger"><button type="button" className="secondary" disabled={busy} onClick={() => setAsking('delete')}><Trash2 aria-hidden="true" />{t('biz.sup.delete')}</button></div>}
    </div>
    {asking === 'resolve' && <ResolveDialog onCancel={() => setAsking(null)} onConfirm={async (code, summary) => {
      setItem(await b.changeTicketStatus(item.id, { to: 'resolved', resolution_code: code, resolution_summary: summary })); setAsking(null); onChanged(); setMessage(t('biz.sup.resolve.done'));
    }} />}
    {asking === 'escalate' && <ReasonDialog title={t('biz.sup.escalate')} label={t('biz.sup.escalateWhy')} confirm={t('biz.sup.escalate')} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => { setItem(await b.setEscalation(item.id, true, reason)); setAsking(null); onChanged(); }} />}
    {asking === 'deescalate' && <ReasonDialog title={t('biz.sup.deescalate')} label={t('biz.sup.deescalateWhy')} confirm={t('biz.sup.deescalate')} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => { setItem(await b.setEscalation(item.id, false, reason)); setAsking(null); onChanged(); }} />}
    {asking === 'delete' && <ConfirmDialog title={t('biz.sup.deleteTitle', { ref: item.reference })} confirmLabel={t('biz.sup.delete')} danger busy={busy} onCancel={() => setAsking(null)}
      onConfirm={() => { setBusy(true); void b.deleteTicket(item.id).then(() => { setAsking(null); onChanged(); onClose(); }).catch((error) => { setAsking(null); setProblem(failure(error, t('biz.sup.delete'))); }).finally(() => setBusy(false)); }}>
      <p>{t('biz.sup.deleteText')}</p></ConfirmDialog>}
  </Drawer>;
}

function TicketDetails({ item, products, assignees, disabled, onSave }: { item: TicketDetail; products: BusinessProduct[]; assignees: SupportAssignee[]; disabled: boolean; onSave: (body: TicketFields) => Promise<void> }) {
  const before = draftOf(item);
  const { draft, set } = useDraft(() => before);
  const [problem, setProblem] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = ticketBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    const changes = changedFields(before, body.value);
    if (Object.keys(changes).length === 0) { setProblem(t('biz.sup.nothingChanged')); return; }
    setProblem(null);
    await onSave(changes);
  }
  return <section className="bzBlock" aria-labelledby="bz-ticket-details"><h3 id="bz-ticket-details">{t('biz.sup.details')}</h3>
    <form className="bzForm" onSubmit={(event) => void submit(event)} aria-label={t('biz.sup.details')}>
      <TicketFieldsForm draft={draft} set={set} products={products} assignees={assignees} disabled={disabled} />
      {item.support_plan_name && <p className="muted bzHint" dir="auto">{t('biz.sup.planNow', { plan: item.support_plan_name })}</p>}
      {item.requester_verified && <p className="muted bzHint">{t('biz.sup.verified')}</p>}
      {problem && <Note error>{problem}</Note>}
      {!disabled && <div className="actions"><button type="submit">{t('biz.sup.saveDetails')}</button></div>}
    </form>
  </section>;
}

function NoteForm({ disabled, onAdd }: { disabled: boolean; onAdd: (text: string, responded: boolean) => Promise<void> }) {
  const [text, setText] = useState(''), [responded, setResponded] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const field = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) { setProblem(t('biz.sup.note.required')); return; }
    setProblem(null);
    await onAdd(text.trim(), responded);
    setText(''); setResponded(false);
  }
  return <section className="bzBlock" aria-labelledby="bz-ticket-note"><h3 id="bz-ticket-note">{t('biz.sup.note.title')}</h3>
    <form className="bzForm" onSubmit={(event) => void submit(event)} aria-label={t('biz.sup.note.title')}>
      <label className="field" htmlFor={field}><span>{t('biz.sup.note.label')}</span><textarea id={field} rows={2} maxLength={500} value={text} disabled={disabled} onChange={(event) => setText(event.target.value)} /></label>
      <label className="bzCheck"><input type="checkbox" checked={responded} disabled={disabled} onChange={(event) => setResponded(event.target.checked)} />{t('biz.sup.note.responded')}</label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" className="secondary" disabled={disabled}>{t('biz.sup.note.add')}</button></div>
    </form>
  </section>;
}

function ResolveDialog({ onConfirm, onCancel }: { onConfirm: (code: ResolutionCode, summary: string) => Promise<void>; onCancel: () => void }) {
  const [code, setCode] = useState<ResolutionCode | ''>(''), [summary, setSummary] = useState(''), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const ids = { code: useId(), summary: useId() };
  async function submit(event: FormEvent) {
    event.preventDefault();
    const issue = resolveProblem(code, summary);
    if (issue) { setProblem(issue); return; }
    setBusy(true); setProblem(null);
    try { await onConfirm(code as ResolutionCode, summary.trim()); } catch (error) { setProblem(failure(error, t('biz.sup.move.resolved'))); setBusy(false); }
  }
  return <Modal title={t('biz.sup.resolve.title')} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('biz.sup.resolve.title')} className="bzForm">
      <label className="field" htmlFor={ids.code}><span>{t('biz.sup.resolve.code')}</span><select id={ids.code} value={code} onChange={(event) => setCode(event.target.value as ResolutionCode | '')} data-autofocus>
        <option value="">{t('biz.sup.choose')}</option>{RESOLUTION_CODES.map((item) => <option key={item} value={item}>{resolutionLabel(item)}</option>)}</select></label>
      <label className="field" htmlFor={ids.summary}><span>{t('biz.sup.resolve.summary')}</span><textarea id={ids.summary} rows={3} maxLength={2000} value={summary} onChange={(event) => setSummary(event.target.value)} /></label>
      <p className="muted bzHint">{t('biz.sup.resolve.hint', { min: MIN_RESOLUTION_SUMMARY })}</p>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('biz.sup.move.resolved')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- replies -----------------------------------------------------------------------------------

function Replies({ item, sup, write, busy, setBusy, onTicket, onProblem }: {
  item: TicketDetail; sup: SupportSetup; write: boolean; busy: boolean; setBusy: (busy: boolean) => void;
  onTicket: (next: TicketDetail, done?: string) => void; onProblem: (text: string | null) => void;
}) {
  const [body, setBody] = useState(''), [editing, setEditing] = useState<Record<string, string>>({});
  const field = useId();
  const visible = item.replies.filter((reply) => reply.status !== 'discarded');
  async function act(what: string, fn: () => Promise<TicketDetail>, done?: string) {
    setBusy(true); onProblem(null);
    try { onTicket(await fn(), done); return true; } catch (error) { onProblem(failure(error, what)); return false; } finally { setBusy(false); }
  }
  async function ask(reply: TicketReply) {
    setBusy(true); onProblem(null);
    try { const result = await b.requestReplyApproval(item.id, reply.id); onTicket(result.ticket, result.detail); }
    catch (error) { onProblem(failure(error, t('biz.sup.reply.ask'))); } finally { setBusy(false); }
  }
  const channel = item.reply_channel ? sourceLabel(item.reply_channel) : '';
  return <section className="bzBlock" aria-labelledby="bz-replies"><h3 id="bz-replies">{t('biz.sup.reply.title')}</h3>
    <p className="muted bzHint">{t('biz.sup.reply.howItWorks')}</p>
    {visible.length === 0 && <p className="muted">{t('biz.sup.reply.none')}</p>}
    {visible.length > 0 && <ol className="bzDrafts">{visible.map((reply) => {
      const editable = write && (reply.status === 'draft' || reply.status === 'failed') && item.status !== 'closed';
      const text = editing[reply.id];
      return <li key={reply.id} aria-label={t('biz.sup.reply.item', { status: replyStatusLabel(reply.status) })}>
        <div className="bzMeta"><Badge tone={replyStatusTone(reply.status)}>{replyStatusLabel(reply.status)}</Badge><span>{sourceLabel(reply.channel)}</span><span>{when(reply.updated_at)}</span></div>
        {text === undefined ? <p className="bzDraftBody" dir="auto">{reply.body}</p>
          : <label className="field"><span>{t('biz.sup.reply.edit')}</span><textarea rows={4} maxLength={4000} value={text} onChange={(event) => setEditing({ ...editing, [reply.id]: event.target.value })} /></label>}
        {reply.status === 'failed' && reply.error && <p className="bzBlocked" dir="auto">{t('biz.sup.reply.failed', { error: reply.error })}</p>}
        {reply.status === 'sent' && <p className="muted bzHint">{t('biz.sup.reply.sentAt', { when: when(reply.sent_at) })}</p>}
        {reply.status === 'pending_approval' && <div className="bzApproval" role="group" aria-label={t('biz.sup.reply.waitingLabel')}>
          <p className="bzApprovalTitle"><ShieldAlert aria-hidden="true" />{t('biz.sup.reply.waiting')}</p>
          {reply.approval?.payload_hash && <p className="bzHash"><Fingerprint aria-hidden="true" />{t('biz.approval.hash')} <code title={reply.approval.payload_hash}>{shortHash(reply.approval.payload_hash)}</code></p>}
          <p className="muted">{sup.can_decide ? t('biz.sup.reply.youDecide') : t('biz.sup.reply.ownerDecides')} <a href="#approvals">{t('biz.sup.reply.openApprovals')}</a></p>
        </div>}
        {editable && <div className="bzInline">
          {text === undefined ? <>
            <button type="button" disabled={busy} onClick={() => void ask(reply)}>{reply.status === 'failed' ? t('biz.sup.reply.askAgain') : t('biz.sup.reply.ask')}</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => setEditing({ ...editing, [reply.id]: reply.body })}>{t('biz.sup.reply.edit')}</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => void act(t('biz.sup.reply.discard'), () => b.discardReply(item.id, reply.id))}>{t('biz.sup.reply.discard')}</button>
          </> : <>
            <button type="button" disabled={busy || !text.trim()} onClick={() => void act(t('biz.sup.reply.save'), () => b.editReply(item.id, reply.id, text.trim())).then((ok) => { if (ok) { const next = { ...editing }; delete next[reply.id]; setEditing(next); } })}>{t('biz.sup.reply.save')}</button>
            <button type="button" className="secondary" onClick={() => { const next = { ...editing }; delete next[reply.id]; setEditing(next); }}>{t('common.cancel')}</button>
          </>}
        </div>}
      </li>;
    })}</ol>}
    {item.can_reply && write ? <form className="bzForm" aria-label={t('biz.sup.reply.new')} onSubmit={(event) => {
      event.preventDefault();
      if (!body.trim()) { onProblem(t('biz.sup.reply.required')); return; }
      void act(t('biz.sup.reply.new'), () => b.addReply(item.id, body.trim()), t('biz.sup.reply.drafted')).then((ok) => { if (ok) setBody(''); });
    }}>
      <label className="field" htmlFor={field}><span>{t('biz.sup.reply.label', { channel })}</span><textarea id={field} rows={4} maxLength={4000} value={body} disabled={busy} onChange={(event) => setBody(event.target.value)} /></label>
      <p className="muted bzHint">{t('biz.sup.reply.noPromises')}</p>
      <div className="actions"><button type="submit" className="secondary" disabled={busy}>{t('biz.sup.reply.saveDraft')}</button></div>
    </form> : <p className="muted bzHint">{item.status === 'closed' ? t('biz.sup.reply.closed') : item.reply_channel ? t('biz.sup.readOnly') : t('biz.sup.reply.manual')}</p>}
  </section>;
}

// ---- support policy (Settings) -------------------------------------------------------------------

export function SupportPolicySection() {
  const support = useSupportSetup();
  if (support.state.kind === 'loading') return <section className="surface bzCard" aria-label={t('biz.sup.policy.title')}><Note>{t('biz.loading')}</Note></section>;
  if (support.state.kind === 'denied') return <section className="surface bzCard" aria-labelledby="bz-policy-denied"><h2 id="bz-policy-denied">{t('biz.sup.policy.title')}</h2><p className="muted">{t('biz.denied', { message: support.state.text })}</p></section>;
  if (support.state.kind === 'error') return <section className="surface bzCard" aria-label={t('biz.sup.policy.title')}><Note error>{t('biz.failed', { what: t('biz.sup.policy.title'), message: support.state.text })} <button type="button" className="secondary" onClick={() => void support.load()}>{t('common.tryAgain')}</button></Note></section>;
  return <PolicyForm key={JSON.stringify(support.state.value.policy)} policy={support.state.value.policy} manage={support.state.value.can_manage} onSaved={() => void support.load()} />;
}

function PolicyForm({ policy, manage, onSaved }: { policy: SupportPolicy; manage: boolean; onSaved: () => void }) {
  const [draft, setDraft] = useState<PolicyDraft>(() => policyDraft(policy));
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    const body = policyBody(draft);
    if (!body.ok) { setMessage({ text: body.error, error: true }); return; }
    setBusy(true);
    try { await b.saveSupportPolicy(body.value); setMessage({ text: t('biz.sup.policy.saved') }); onSaved(); }
    catch (error) { setMessage({ text: failure(error, t('biz.sup.policy.title')), error: true }); } finally { setBusy(false); }
  }
  const toggle = (day: number) => setDraft({ ...draft, workDays: draft.workDays.includes(day) ? draft.workDays.filter((item) => item !== day) : [...draft.workDays, day] });
  const target = (severity: TicketSeverity, key: 'response' | 'resolution', value: string) => setDraft({ ...draft, targets: { ...draft.targets, [severity]: { ...draft.targets[severity], [key]: value } } });
  return <section className="surface bzCard bzNotes" aria-labelledby="bz-policy"><h2 id="bz-policy">{t('biz.sup.policy.title')}</h2>
    <p className="muted">{t('biz.sup.policy.help')}</p>
    <form onSubmit={(event) => void save(event)} aria-label={t('biz.sup.policy.title')} className="bzForm">
      <div className="bzGrid3">
        <label className="field"><span>{t('biz.sup.policy.timezone')}</span><input value={draft.timezone} disabled={!manage} maxLength={64} onChange={(event) => setDraft({ ...draft, timezone: event.target.value })} /></label>
        <label className="field"><span>{t('biz.sup.policy.start')}</span><input inputMode="numeric" value={draft.startHour} disabled={!manage} onChange={(event) => setDraft({ ...draft, startHour: event.target.value })} /></label>
        <label className="field"><span>{t('biz.sup.policy.end')}</span><input inputMode="numeric" value={draft.endHour} disabled={!manage} onChange={(event) => setDraft({ ...draft, endHour: event.target.value })} /></label>
      </div>
      <fieldset className="bzFieldset"><legend>{t('biz.sup.policy.days')}</legend>
        <div className="bzInline">{WEEKDAYS.map((day) => <label key={day} className="bzCheck"><input type="checkbox" checked={draft.workDays.includes(day)} disabled={!manage} onChange={() => toggle(day)} />{weekdayLabel(day)}</label>)}</div>
      </fieldset>
      <div className="dataTableWrap"><table className="dataTable">
        <caption className="visuallyHidden">{t('biz.sup.policy.targets')}</caption>
        <thead><tr><th scope="col">{t('biz.sup.field.severity')}</th><th scope="col">{t('biz.sup.policy.responseHours')}</th><th scope="col">{t('biz.sup.policy.resolutionHours')}</th></tr></thead>
        <tbody>{SEVERITIES.map((severity) => <tr key={severity}><th scope="row">{severityLabel(severity)}</th>
          <td><input inputMode="decimal" aria-label={t('biz.sup.policy.responseFor', { severity: severityLabel(severity) })} value={draft.targets[severity].response} disabled={!manage} onChange={(event) => target(severity, 'response', event.target.value)} /></td>
          <td><input inputMode="decimal" aria-label={t('biz.sup.policy.resolutionFor', { severity: severityLabel(severity) })} value={draft.targets[severity].resolution} disabled={!manage} onChange={(event) => target(severity, 'resolution', event.target.value)} /></td>
        </tr>)}</tbody></table></div>
      <label className="field"><span>{t('biz.sup.policy.note')}</span><textarea rows={2} maxLength={500} value={draft.note} disabled={!manage} onChange={(event) => setDraft({ ...draft, note: event.target.value })} /></label>
      <p className="bzHint"><AlertTriangle className="bzInlineIcon" aria-hidden="true" /> {t('biz.sup.policy.no247')}</p>
      {manage ? <div className="actions"><button type="submit" disabled={busy}>{t('biz.sup.policy.save')}</button></div> : <p className="muted">{t('biz.sup.policy.ownersOnly')}</p>}
      {message && <Note error={message.error}>{message.text}</Note>}
    </form>
  </section>;
}
