// A project's money (docs/business.md#change-orders): change orders that amend the accepted contract
// only when an owner approves the exact change, the client usage and cost ledger, and the
// profitability view with revenue and cash collected kept apart. Shown in the project drawer.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Bot, CheckCircle2, Fingerprint, Plus, Send, Trash2 } from 'lucide-react';
import type { BusinessChangeOrder, BusinessProfitability, BusinessProject, BusinessSetup, BusinessUsageEntry, ChangeOrderLine, LineItem } from '@anum/contracts';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';
import { CURRENCIES } from './lib/business';
import {
  DASH, MANUAL_USAGE_KINDS, aiImportBody, changeBody, changeDraftOf, changeStatusLabel, changeTone, emptyChangeDraft, emptyChangeLine, emptyUsageDraft,
  lastFinishedMonth, moneyOrDash, numberOrDash, pctOrDash, signedMoney, timelineText, usageAmount, usageBody, usageKindLabel, type ChangeDraft, type UsageDraft,
} from './lib/businessChanges';
import { shortHash } from './lib/approvals';
import * as b from './lib/businessApi';
import { Modal } from './Dialog';
import { PortalDecisionNote } from './BusinessPortalAdmin';
import { Badge, Empty, Note, Pager, ReasonDialog, day, failure, when } from './BusinessParts';

const PAGE = 25;

/** The three money blocks of one project. Approving a change order reloads the project. */
export function ProjectMoney({ setup, project, onProjectChanged }: { setup: BusinessSetup; project: BusinessProject; onProjectChanged: () => void }) {
  const [reload, setReload] = useState(0);
  const bump = useCallback(() => setReload((value) => value + 1), []);
  return <>
    <ProfitabilityBlock project={project} reload={reload} />
    <ChangeOrdersBlock setup={setup} project={project} onContractChanged={() => { bump(); onProjectChanged(); }} />
    <LedgerBlock setup={setup} project={project} onChanged={bump} />
  </>;
}

// ---- profitability -----------------------------------------------------------------------------

type Loaded<T> = { kind: 'loading' } | { kind: 'ready'; data: T } | { kind: 'error'; text: string };

function ProfitabilityBlock({ project, reload }: { project: BusinessProject; reload: number }) {
  const [state, setState] = useState<Loaded<BusinessProfitability>>({ kind: 'loading' });
  const heading = useId();
  const load = useCallback(() => {
    setState({ kind: 'loading' });
    b.getProfitability(project.id).then((data) => setState({ kind: 'ready', data })).catch((error) => setState({ kind: 'error', text: failure(error, t('biz.profit.load')) }));
  }, [project.id]);
  useEffect(() => { load(); }, [load, reload, project.contract_total]);
  return <section className="bzBlock" aria-labelledby={heading}>
    <h3 id={heading}>{t('biz.profit.title')}</h3>
    {state.kind === 'loading' && <Note>{t('biz.loading')}</Note>}
    {state.kind === 'error' && <Note error>{state.text} <button type="button" className="secondary" onClick={load}>{t('common.tryAgain')}</button></Note>}
    {state.kind === 'ready' && <ProfitabilityFacts data={state.data} />}
  </section>;
}

function ProfitabilityFacts({ data }: { data: BusinessProfitability }) {
  const money = (value: number | null) => moneyOrDash(value, data.currency);
  return <>
    <dl className="bzTotals" aria-label={t('biz.profit.revenueGroup')}>
      <div><dt>{t('biz.profit.accepted', { version: data.accepted_version })}</dt><dd>{money(data.accepted_revenue)}</dd></div>
      <div><dt>{t('biz.profit.changeOrders', { count: data.approved_change_orders })}</dt><dd>{signedMoney(data.change_orders_revenue, data.currency)}</dd></div>
      <div><dt>{t('biz.profit.revenue')}</dt><dd><strong>{money(data.revenue)}</strong></dd></div>
      <div><dt>{t('biz.profit.tax')}</dt><dd>{money(data.tax)}</dd></div>
      <div><dt>{t('biz.profit.contract', { version: data.current_version })}</dt><dd>{money(data.contract_total)}</dd></div>
    </dl>
    <dl className="bzTotals" aria-label={t('biz.profit.cashGroup')}>
      <div><dt>{t('biz.profit.cash')}</dt><dd>{money(data.cash_collected)}</dd></div>
      <div><dt>{t('biz.profit.outstanding')}</dt><dd>{money(data.outstanding)}</dd></div>
    </dl>
    <dl className="bzTotals" aria-label={t('biz.profit.costGroup')}>
      <div><dt>{t('biz.profit.labourHours')}</dt><dd>{numberOrDash(data.labour_hours)}</dd></div>
      <div><dt>{t('biz.profit.labour')}</dt><dd>{money(data.labour_cost)}</dd></div>
      <div><dt>{t('biz.profit.direct')}</dt><dd>{money(data.direct_costs)}</dd></div>
      <div><dt>{t('biz.profit.billable')}</dt><dd>{money(data.billable_costs)}</dd></div>
      <div><dt>{t('biz.profit.nonBillable')}</dt><dd>{money(data.non_billable_costs)}</dd></div>
      <div><dt>{t('biz.profit.margin')}</dt><dd><strong>{data.gross_margin === null ? DASH : signedMoney(data.gross_margin, data.currency)}</strong></dd></div>
      <div><dt>{t('biz.profit.marginPct')}</dt><dd>{pctOrDash(data.gross_margin_pct)}</dd></div>
    </dl>
    {data.costs.length > 0 && <p className="muted bzHint">{data.costs.map((line) => `${usageKindLabel(line.kind)}: ${money(line.amount)}`).join(', ')}</p>}
    {data.other_currency.length > 0 && <p className="muted bzHint">{t('biz.profit.otherCurrency', {
      list: data.other_currency.map((item) => `${moneyOrDash(item.amount, item.currency)} (${t('biz.profit.entries', { count: item.entries })})`).join(', '),
    })}</p>}
    {!data.reconciled && <Note error>{t('biz.profit.notReconciled')}</Note>}
    <ul className="bzBullets muted bzHint">{data.notes.map((note) => <li key={note}>{note}</li>)}</ul>
  </>;
}

// ---- change orders -----------------------------------------------------------------------------

function ChangeOrdersBlock({ setup, project, onContractChanged }: { setup: BusinessSetup; project: BusinessProject; onContractChanged: () => void }) {
  const [items, setItems] = useState<BusinessChangeOrder[]>([]), [total, setTotal] = useState(0);
  const [state, setState] = useState<Loaded<null>>({ kind: 'loading' }), [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [editing, setEditing] = useState<BusinessChangeOrder | 'new' | null>(null), [asking, setAsking] = useState<{ kind: 'reject' | 'withdraw'; item: BusinessChangeOrder } | null>(null);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null), [done, setDone] = useState<string | null>(null);
  const heading = useId();
  const request = useRef(0);
  const write = setup.permissions.write, canDecide = setup.permissions.decide, open = project.status !== 'closed';
  const load = useCallback(async () => {
    const id = ++request.current;
    setState({ kind: 'loading' });
    try { const page = await b.listChangeOrders(project.id, { limit: PAGE, offset: 0 }); if (id !== request.current) return; setItems(page.items); setTotal(page.total); setState({ kind: 'ready', data: null }); }
    catch (error) { if (id === request.current) setState({ kind: 'error', text: failure(error, t('biz.change.load')) }); }
  }, [project.id]);
  useEffect(() => { void load(); }, [load]);
  async function loadMore() {
    setMore({ busy: true, error: null });
    try { const page = await b.listChangeOrders(project.id, { limit: PAGE, offset: items.length }); setItems((current) => [...current, ...page.items]); setTotal(page.total); setMore({ busy: false, error: null }); }
    catch (error) { setMore({ busy: false, error: describeError(error) }); }
  }
  async function run(what: string, fn: () => Promise<BusinessChangeOrder>, message?: string) {
    setBusy(true); setProblem(null); setDone(null);
    try {
      const updated = await fn();
      setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      if (message) setDone(message);
      if (updated.status === 'approved') { onContractChanged(); void load(); }
    } catch (error) { setProblem(failure(error, what)); } finally { setBusy(false); }
  }
  const newButton = write && open ? <button type="button" className="secondary" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('biz.change.new')}</button> : null;
  return <section className="bzBlock" aria-labelledby={heading}>
    <div className="bzInline"><h3 id={heading} className="bzGrow">{t('biz.change.title')}</h3>{state.kind === 'ready' && items.length > 0 && newButton}</div>
    <p className="muted bzHint">{t('biz.change.hint')}</p>
    {problem && <Note error>{problem}</Note>}
    {done && <Note>{done}</Note>}
    {state.kind === 'loading' && <Note>{t('biz.loading')}</Note>}
    {state.kind === 'error' && <Note error>{state.text} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {state.kind === 'ready' && items.length === 0 && <Empty title={t('biz.change.emptyTitle')} text={open ? t('biz.change.emptyText') : t('biz.change.closedText')} action={newButton ?? undefined} />}
    {state.kind === 'ready' && items.length > 0 && <ol className="bzMilestones" aria-label={t('biz.change.title')}>{items.map((item) => <li key={item.id}>
      <div className="bzMilestoneHead"><strong dir="auto">{t('biz.change.heading', { number: item.number, title: item.title })}</strong><Badge tone={changeTone(item.status)}>{changeStatusLabel(item.status)}</Badge></div>
      <div className="bzMeta">
        <span>{t('biz.change.price', { amount: signedMoney(item.total_delta, item.currency) })}</span>
        <span>{t('biz.change.timeline', { text: timelineText(item.timeline_days) })}</span>
        {item.internal_cost_delta !== 0 && <span>{t('biz.change.cost', { amount: signedMoney(item.internal_cost_delta, item.currency) })}</span>}
        {item.requested_by && <span dir="auto">{t('biz.change.requestedBy', { name: item.requested_by })}</span>}
      </div>
      {item.request && <p className="muted" dir="auto">{item.request}</p>}
      {item.lines.length > 0 && <ul className="bzBullets">{item.lines.map((line, index) => <li key={index} dir="auto">
        {t(line.action === 'remove' ? 'biz.change.lineRemoved' : 'biz.change.lineAdded', { description: line.description, quantity: line.quantity, price: moneyOrDash(line.unit_price, item.currency) })}</li>)}</ul>}
      <PortalDecisionNote decision={item.portal_decision ?? null} />
      {item.issues.length > 0 && <ul className="bzIssues">{item.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      {item.status === 'pending_approval' && !item.stale && <div className="bzApproval" role="group" aria-label={t('biz.change.approvalLabel', { number: item.number })}>
        <p className="bzHash"><Fingerprint aria-hidden="true" />{t('biz.change.binds', { version: item.base_version ?? 0 })} <code title={item.content_hash ?? ''}>{shortHash(item.content_hash)}</code></p>
        {canDecide ? <div className="bzInline">
          <button type="button" disabled={busy} onClick={() => void run(t('biz.change.approve'), () => b.approveChangeOrder(item.id, item.content_hash ?? ''), t('biz.change.approved', { number: item.number }))}><CheckCircle2 aria-hidden="true" />{t('biz.change.approve')}</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'reject', item })}>{t('biz.change.reject')}</button>
        </div> : <p className="muted">{t('biz.change.ownerDecides')}</p>}
      </div>}
      {item.submitted_at && <small className="muted">{t('biz.change.sentBy', { by: item.submitted_by ?? '', when: when(item.submitted_at) })}</small>}
      {item.decided_at && <small className="muted">{t(item.status === 'approved' ? 'biz.change.approvedBy' : 'biz.change.rejectedBy', { by: item.decided_by ?? '', when: when(item.decided_at), version: item.result_version ?? 0 })}</small>}
      {item.decision_note && <small className="muted" dir="auto">{t('biz.change.note', { note: item.decision_note })}</small>}
      {write && (item.status === 'draft' || item.status === 'pending_approval') && <div className="bzInline">
        {item.status === 'draft' && <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(item)}>{t('biz.change.edit')}</button>}
        {(item.status === 'draft' || item.stale) && <button type="button" disabled={busy || item.issues.length > 0 && !item.stale} onClick={() => void run(t('biz.change.submit'), () => b.submitChangeOrder(item.id), t('biz.change.submitted', { number: item.number }))}><Send aria-hidden="true" />{item.stale ? t('biz.change.resubmit') : t('biz.change.submit')}</button>}
        <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'withdraw', item })}>{t('biz.change.withdraw')}</button>
      </div>}
    </li>)}</ol>}
    <Pager shown={items.length} total={total} busy={more.busy} error={more.error} onMore={() => void loadMore()} />
    {editing && <ChangeOrderForm projectId={project.id} item={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
      onSaved={(saved) => { setEditing(null); setItems((current) => (current.some((item) => item.id === saved.id) ? current.map((item) => (item.id === saved.id ? saved : item)) : [saved, ...current])); setTotal((value) => (editing === 'new' ? value + 1 : value)); }} />}
    {asking && <ReasonDialog title={t(asking.kind === 'reject' ? 'biz.change.rejectTitle' : 'biz.change.withdrawTitle', { number: asking.item.number })} label={t('biz.change.reason')}
      confirm={t(asking.kind === 'reject' ? 'biz.change.reject' : 'biz.change.withdraw')} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => {
        const updated = asking.kind === 'reject' ? await b.rejectChangeOrder(asking.item.id, reason) : await b.withdrawChangeOrder(asking.item.id, reason);
        setItems((current) => current.map((item) => (item.id === updated.id ? updated : item))); setAsking(null);
      }} />}
  </section>;
}

function ChangeOrderForm({ projectId, item, onClose, onSaved }: { projectId: string; item: BusinessChangeOrder | null; onClose: () => void; onSaved: (item: BusinessChangeOrder) => void }) {
  const [form, setForm] = useState<ChangeDraft>(() => (item ? changeDraftOf(item) : emptyChangeDraft()));
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const title = item ? t('biz.change.editTitle', { number: item.number }) : t('biz.change.new');
  const setLine = (index: number, patch: Partial<ChangeDraft['lines'][number]>) => setForm({ ...form, lines: form.lines.map((line, i) => (i === index ? { ...line, ...patch } : line)) });
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = changeBody(form);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onSaved(item ? await b.updateChangeOrder(item.id, body.value) : await b.createChangeOrder(projectId, body.value)); }
    catch (error) { setProblem(failure(error, title)); setBusy(false); }
  }
  return <Modal title={title} className="formDialog bzWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={title}>
      <div className="bzGrid2">
        <label className="field"><span>{t('biz.change.titleField')}</span><input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} dir="auto" data-autofocus /></label>
        <label className="field"><span>{t('biz.change.requestedByField')}</span><input value={form.requestedBy} onChange={(event) => setForm({ ...form, requestedBy: event.target.value })} dir="auto" /></label>
      </div>
      <label className="field"><span>{t('biz.change.requestField')}</span><textarea rows={3} value={form.request} onChange={(event) => setForm({ ...form, request: event.target.value })} dir="auto" /></label>
      <fieldset className="bzFieldset">
        <legend>{t('biz.change.scope')}</legend>
        <p className="muted bzHint">{t('biz.change.scopeHint')}</p>
        <div className="bzRows">{form.lines.map((line, index) => <div key={index} className="bzRow bzChangeRow">
          <label className="field"><span>{t('biz.change.action')}</span><select value={line.action} onChange={(event) => setLine(index, { action: event.target.value as ChangeOrderLine['action'] })}>
            <option value="add">{t('biz.change.add')}</option><option value="remove">{t('biz.change.remove')}</option></select></label>
          <label className="field"><span>{t('biz.proposal.item')}</span><input value={line.description} onChange={(event) => setLine(index, { description: event.target.value })} dir="auto" /></label>
          <label className="field"><span>{t('biz.proposal.kind')}</span><select value={line.kind} onChange={(event) => setLine(index, { kind: event.target.value as LineItem['kind'] })}>
            {(['service', 'labor', 'external_cost', 'other'] as const).map((kind) => <option key={kind} value={kind}>{t(`biz.lineKind.${kind}`)}</option>)}</select></label>
          <label className="field"><span>{t('biz.proposal.quantity')}</span><input inputMode="decimal" value={line.quantity} onChange={(event) => setLine(index, { quantity: event.target.value })} /></label>
          <label className="field"><span>{t('biz.proposal.unitPrice')}</span><input inputMode="decimal" value={line.unit_price} onChange={(event) => setLine(index, { unit_price: event.target.value })} /></label>
          <button type="button" className="iconButton" aria-label={t('biz.proposal.removeLine', { n: index + 1 })} onClick={() => setForm({ ...form, lines: form.lines.filter((_, i) => i !== index) })}><Trash2 aria-hidden="true" /></button>
        </div>)}</div>
        <button type="button" className="secondary" onClick={() => setForm({ ...form, lines: [...form.lines, emptyChangeLine()] })}><Plus aria-hidden="true" />{t('biz.proposal.addLine')}</button>
      </fieldset>
      <div className="bzGrid2">
        <label className="field"><span>{t('biz.change.timelineField')}</span><input inputMode="numeric" value={form.timelineDays} onChange={(event) => setForm({ ...form, timelineDays: event.target.value })} /></label>
        <label className="field"><span>{t('biz.change.costField')}</span><input inputMode="decimal" value={form.costDelta} onChange={(event) => setForm({ ...form, costDelta: event.target.value })} /></label>
      </div>
      <p className="muted bzHint">{t('biz.change.formHint')}</p>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('biz.change.save')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- usage and cost ledger ---------------------------------------------------------------------

const today = () => new Date().toISOString().slice(0, 10);

function LedgerBlock({ setup, project, onChanged }: { setup: BusinessSetup; project: BusinessProject; onChanged: () => void }) {
  const [items, setItems] = useState<BusinessUsageEntry[]>([]), [total, setTotal] = useState(0);
  const [state, setState] = useState<Loaded<null>>({ kind: 'loading' }), [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [form, setForm] = useState<UsageDraft>(() => emptyUsageDraft(project.currency, today()));
  const [month, setMonth] = useState(() => lastFinishedMonth(new Date())), [share, setShare] = useState('100'), [aiBillable, setAiBillable] = useState(false);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null), [done, setDone] = useState<string | null>(null);
  const heading = useId(), formId = useId(), aiId = useId();
  const request = useRef(0);
  const write = setup.permissions.write, manage = setup.permissions.manage;
  const load = useCallback(async () => {
    const id = ++request.current;
    setState({ kind: 'loading' });
    try { const page = await b.listUsage({ project_id: project.id, limit: PAGE, offset: 0 }); if (id !== request.current) return; setItems(page.items); setTotal(page.total); setState({ kind: 'ready', data: null }); }
    catch (error) { if (id === request.current) setState({ kind: 'error', text: failure(error, t('biz.usage.load')) }); }
  }, [project.id]);
  useEffect(() => { void load(); }, [load]);
  async function loadMore() {
    setMore({ busy: true, error: null });
    try { const page = await b.listUsage({ project_id: project.id, limit: PAGE, offset: items.length }); setItems((current) => [...current, ...page.items]); setTotal(page.total); setMore({ busy: false, error: null }); }
    catch (error) { setMore({ busy: false, error: describeError(error) }); }
  }
  async function run(what: string, fn: () => Promise<unknown>, message: string) {
    setBusy(true); setProblem(null); setDone(null);
    try { await fn(); setDone(message); await load(); onChanged(); return true; } catch (error) { setProblem(failure(error, what)); return false; } finally { setBusy(false); }
  }
  async function add(event: FormEvent) {
    event.preventDefault();
    const body = usageBody(project.id, form);
    if (!body.ok) { setProblem(body.error); return; }
    if (await run(t('biz.usage.add'), () => b.createUsage(body.value), t('biz.usage.added'))) setForm({ ...emptyUsageDraft(project.currency, today()), kind: form.kind, currency: form.currency });
  }
  async function importAi(event: FormEvent) {
    event.preventDefault();
    const body = aiImportBody(project.id, month, share, aiBillable, new Date());
    if (!body.ok) { setProblem(body.error); return; }
    await run(t('biz.usage.import'), () => b.importAiUsage(body.value), t('biz.usage.imported', { month }));
  }
  const labour = form.kind === 'labour';
  const quantity = Number(form.quantity), unit = Number(form.unitCost);
  const preview = Number.isFinite(quantity) && Number.isFinite(unit) && form.unitCost.trim() ? moneyOrDash(usageAmount(quantity, unit), form.currency) : DASH;
  return <section className="bzBlock" aria-labelledby={heading}>
    <h3 id={heading}>{t('biz.usage.title')}</h3>
    <p className="muted bzHint">{t('biz.usage.hint')}</p>
    {problem && <Note error>{problem}</Note>}
    {done && <Note>{done}</Note>}
    {write && <form className="bzForm bzGrid3" onSubmit={(event) => void add(event)} aria-labelledby={formId}>
      <h4 id={formId} className="bzSpan2 bzSubhead">{t('biz.usage.add')}</h4>
      <label className="field"><span>{t('biz.usage.kind')}</span><select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as UsageDraft['kind'] })}>
        {MANUAL_USAGE_KINDS.map((kind) => <option key={kind} value={kind}>{usageKindLabel(kind)}</option>)}</select></label>
      <label className="field"><span>{t('biz.usage.description')}</span><input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} dir="auto" /></label>
      <label className="field"><span>{t('biz.usage.date')}</span><input type="date" value={form.occurredOn} onChange={(event) => setForm({ ...form, occurredOn: event.target.value })} /></label>
      <label className="field"><span>{labour ? t('biz.usage.hours') : t('biz.usage.quantity')}</span><input inputMode="decimal" value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })} /></label>
      <label className="field"><span>{labour ? t('biz.usage.hourlyCost') : t('biz.usage.unitCost')}</span><input inputMode="decimal" value={form.unitCost} onChange={(event) => setForm({ ...form, unitCost: event.target.value })} /></label>
      <label className="field"><span>{t('biz.field.currency')}</span><select value={form.currency} onChange={(event) => setForm({ ...form, currency: event.target.value as UsageDraft['currency'] })}>
        {CURRENCIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      <label className="bzCheck"><input type="checkbox" checked={form.billable} onChange={(event) => setForm({ ...form, billable: event.target.checked })} />{t('biz.usage.billable')}</label>
      <p className="muted bzHint">{t('biz.usage.amountPreview', { amount: preview })}</p>
      <div className="bzInline"><button type="submit" disabled={busy}><Plus aria-hidden="true" />{t('biz.usage.add')}</button></div>
    </form>}
    {write && <form className="bzForm bzGrid3" onSubmit={(event) => void importAi(event)} aria-labelledby={aiId}>
      <h4 id={aiId} className="bzSpan2 bzSubhead">{t('biz.usage.import')}</h4>
      <p className="muted bzHint bzSpan2">{t('biz.usage.importHint')}</p>
      <label className="field"><span>{t('biz.usage.month')}</span><input type="month" value={month} max={lastFinishedMonth(new Date())} onChange={(event) => setMonth(event.target.value)} /></label>
      <label className="field"><span>{t('biz.usage.share')}</span><input inputMode="decimal" value={share} onChange={(event) => setShare(event.target.value)} /></label>
      <label className="bzCheck"><input type="checkbox" checked={aiBillable} onChange={(event) => setAiBillable(event.target.checked)} />{t('biz.usage.billable')}</label>
      <div className="bzInline"><button type="submit" className="secondary" disabled={busy}><Bot aria-hidden="true" />{t('biz.usage.importButton')}</button></div>
    </form>}
    {state.kind === 'loading' && <Note>{t('biz.loading')}</Note>}
    {state.kind === 'error' && <Note error>{state.text} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {state.kind === 'ready' && items.length === 0 && <Empty title={t('biz.usage.emptyTitle')} text={write ? t('biz.usage.emptyText') : t('biz.usage.emptyReadOnly')} />}
    {state.kind === 'ready' && items.length > 0 && <div className="pane dataTableWrap"><table className="dataTable bzTable">
      <caption className="visuallyHidden">{t('biz.usage.title')}</caption>
      <thead><tr><th scope="col">{t('biz.usage.description')}</th><th scope="col" className="bzHideSm">{t('biz.usage.date')}</th><th scope="col" className="num bzHideMd">{t('biz.usage.quantityCol')}</th><th scope="col" className="num">{t('biz.usage.amount')}</th><th scope="col">{t('biz.usage.billableCol')}</th>{manage && <th scope="col"><span className="visuallyHidden">{t('biz.usage.actions')}</span></th>}</tr></thead>
      <tbody>{items.map((item) => <tr key={item.id}>
        <th scope="row"><span dir="auto">{item.description}</span><small>{usageKindLabel(item.kind)}{item.source === 'model_budget' ? ` · ${t('biz.usage.fromBudget')}` : ''}</small></th>
        <td className="bzHideSm">{day(item.occurred_on)}</td>
        <td className="num bzHideMd">{`${numberOrDash(item.quantity)} × ${moneyOrDash(item.unit_cost, item.currency)}`}</td>
        <td className="num">{moneyOrDash(item.amount, item.currency)}</td>
        <td>{write ? <label className="bzCheck"><input type="checkbox" checked={item.billable} disabled={busy} aria-label={t('biz.usage.billableFor', { description: item.description })}
          onChange={(event) => void run(t('biz.usage.billable'), () => b.updateUsage(item.id, { billable: event.target.checked }), t('biz.usage.saved'))} /></label>
          : (item.billable ? t('biz.usage.yes') : t('biz.usage.no'))}</td>
        {manage && <td><button type="button" className="iconButton" disabled={busy} aria-label={t('biz.usage.delete', { description: item.description })}
          onClick={() => void run(t('biz.usage.deleteTitle'), () => b.deleteUsage(item.id), t('biz.usage.deleted'))}><Trash2 aria-hidden="true" /></button></td>}
      </tr>)}</tbody></table></div>}
    <Pager shown={items.length} total={total} busy={more.busy} error={more.error} onMore={() => void loadMore()} />
  </section>;
}
