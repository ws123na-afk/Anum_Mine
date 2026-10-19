// Business deals (docs/business.md#pipeline): opportunities with values and probabilities as the
// owner's assumptions, versioned proposals whose acceptance waits for an owner in Approvals (bound
// to the exact version), and projects with milestones started from an accepted proposal.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Download, FileText, Plus, Search, Send, Trash2 } from 'lucide-react';
import type {
  BusinessCurrency, BusinessOpportunity, BusinessPage, BusinessProject, BusinessProposal, BusinessSetup, LineItem, MilestonePlan, MilestoneStatus,
  OpportunityStage, ProjectStatus, ProposalContent,
} from '@anum/contracts';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';
import {
  CURRENCIES, MILESTONE_STATUSES, PAGE_SIZE, PROJECT_STATUSES, STAGES, formatMoney, formatPct, linesOf, milestoneStatusLabel, parseAmount, projectStatusLabel,
  proposalStatusLabel, proposalTone, proposalTotals, stageLabel, stageTone,
} from './lib/business';
import * as b from './lib/businessApi';
import { Drawer } from './Dialog';
import { AcceptanceApproval, Badge, Empty, Note, Pager, ReasonDialog, day, failure, when } from './BusinessParts';
import { ProjectMoney } from './BusinessProjectMoney';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; text: string };

/** A paged, searchable list: first page on filter change, "show more" after. */
export function usePaged<T>(fetch: (offset: number) => Promise<BusinessPage<T>>, deps: unknown[]) {
  const [items, setItems] = useState<T[]>([]), [total, setTotal] = useState(0), [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const request = useRef(0);
  const first = useCallback(async () => {
    const id = ++request.current;
    setLoad({ kind: 'loading' });
    try { const page = await fetch(0); if (id !== request.current) return; setItems(page.items); setTotal(page.total); setLoad({ kind: 'ready' }); }
    catch (error) { if (id === request.current) setLoad({ kind: 'error', text: isPermissionDenied(error) ? t('biz.denied', { message: describeError(error) }) : describeError(error) }); }
  }, deps);
  useEffect(() => { void first(); }, [first]);
  const loadMore = async () => {
    setMore({ busy: true, error: null });
    try { const page = await fetch(items.length); setItems((current) => [...current, ...page.items]); setTotal(page.total); setMore({ busy: false, error: null }); }
    catch (error) { setMore({ busy: false, error: describeError(error) }); }
  };
  return { items, total, load, more, first, loadMore };
}

function SearchBar({ label, value, onChange, onSubmit, children }: { label: string; value: string; onChange: (value: string) => void; onSubmit: () => void; children?: ReactNode }) {
  const id = useId();
  return <div className="bzToolbar">
    <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <label className="visuallyHidden" htmlFor={id}>{label}</label>
      <input id={id} type="search" required value={value} placeholder={label} onChange={(event) => onChange(event.target.value)} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
    </form>
    {children}
  </div>;
}

export function ListState({ load, empty, filtered, onRetry, onClear }: { load: Load; empty: boolean; filtered: boolean; onRetry: () => void; onClear: () => void } & { emptyTitle?: string }) {
  if (load.kind === 'loading') return <Note>{t('biz.loading')}</Note>;
  if (load.kind === 'error') return <Note error>{load.text} <button type="button" className="secondary" onClick={onRetry}>{t('common.tryAgain')}</button></Note>;
  if (empty && filtered) return <Empty title={t('biz.noMatch')} text={t('biz.noMatchText')} action={<button type="button" className="secondary" onClick={onClear}>{t('biz.clearFilters')}</button>} />;
  return null;
}

const money = (value: number | null, currency: BusinessCurrency) => formatMoney(value, currency);

// ---- pipeline ----------------------------------------------------------------------------------

export function PipelineTab({ setup, openId, onOpen, onOpenProposal, onGoLeads, onChanged }: {
  setup: BusinessSetup; openId: string | null; onOpen: (id: string | null) => void; onOpenProposal: (id: string) => void; onGoLeads: () => void; onChanged: () => void;
}) {
  const [q, setQ] = useState(''), [search, setSearch] = useState(''), [stage, setStage] = useState<OpportunityStage | ''>('');
  const list = usePaged((offset) => b.listOpportunities({ q: search, stage: stage ? [stage] : [], limit: PAGE_SIZE, offset }), [search, stage]);
  const stageId = useId();
  const filtered = Boolean(search || stage);
  return <section className="bzSection" aria-label={t('biz.tab.pipeline')}>
    <SearchBar label={t('biz.pipeline.search')} value={q} onChange={(value) => { setQ(value); if (!value) setSearch(''); }} onSubmit={() => setSearch(q.trim())}>
      <label className="bzCompact" htmlFor={stageId}><span>{t('biz.pipeline.stage')}</span><select id={stageId} value={stage} onChange={(event) => setStage(event.target.value as OpportunityStage | '')}>
        <option value="">{t('biz.pipeline.allStages')}</option>{STAGES.map((item) => <option key={item} value={item}>{stageLabel(item)}</option>)}</select></label>
    </SearchBar>
    <p className="muted bzHint">{t('biz.pipeline.assumptions')}</p>
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setQ(''); setSearch(''); setStage(''); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('biz.pipeline.emptyTitle')} text={t('biz.pipeline.emptyText')} action={<button type="button" className="secondary" onClick={onGoLeads}>{t('biz.pipeline.goLeads')}</button>} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('biz.tab.pipeline')}</caption>
        <thead><tr><th scope="col">{t('biz.col.opportunity')}</th><th scope="col">{t('biz.col.stage')}</th><th scope="col" className="num">{t('biz.col.value')}</th><th scope="col" className="num bzHideSm">{t('biz.col.probability')}</th><th scope="col" className="num bzHideMd">{t('biz.col.weighted')}</th><th scope="col" className="bzHideMd">{t('biz.col.decision')}</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => onOpen(item.id)}><span dir="auto">{item.name}</span></button><small dir="auto">{item.account_name}</small></th>
          <td><Badge tone={stageTone(item.stage)}>{stageLabel(item.stage)}</Badge></td>
          <td className="num">{money(item.value_amount, item.currency)}</td>
          <td className="num bzHideSm">{formatPct(item.probability_pct)}</td>
          <td className="num bzHideMd">{money(item.weighted_value, item.currency)}</td>
          <td className="bzHideMd nowrap">{item.expected_decision_on ? day(item.expected_decision_on) : t('biz.unknown')}</td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {openId && <OpportunityDrawer setup={setup} id={openId} onClose={() => onOpen(null)} onOpenProposal={onOpenProposal} onChanged={() => { void list.first(); onChanged(); }} />}
  </section>;
}

function OpportunityDrawer({ setup, id, onClose, onOpenProposal, onChanged }: { setup: BusinessSetup; id: string; onClose: () => void; onOpenProposal: (id: string) => void; onChanged: () => void }) {
  const [item, setItem] = useState<BusinessOpportunity | null>(null), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ value: '', probability: '', note: '', next: '', decision: '', currency: 'SAR' as BusinessCurrency });
  const [losing, setLosing] = useState(false), [proposing, setProposing] = useState(false), [title, setTitle] = useState('');
  const reload = useCallback(async () => {
    try {
      const found = await b.getOpportunity(id);
      setItem(found);
      setForm({ value: found.value_amount?.toString() ?? '', probability: found.probability_pct?.toString() ?? '', note: found.assumption_note, next: found.next_step, decision: found.expected_decision_on ?? '', currency: found.currency });
      setTitle(found.name);
    } catch (error) { setProblem(failure(error, t('biz.pipeline.load'))); }
  }, [id]);
  useEffect(() => { void reload(); }, [reload]);
  if (!item) return <Drawer title={t('biz.pipeline.title')} onClose={onClose}>{problem ? <Note error>{problem}</Note> : <Note>{t('biz.loading')}</Note>}</Drawer>;
  const write = setup.permissions.write && item.stage !== 'won' && item.stage !== 'lost';
  async function save(event: FormEvent) {
    event.preventDefault();
    const value = form.value.trim() ? parseAmount(form.value) : null, probability = form.probability.trim() ? parseAmount(form.probability) : null;
    if ((form.value.trim() && value === null) || (form.probability.trim() && (probability === null || probability > 100))) { setProblem(t('biz.form.assumptionInvalid')); return; }
    setBusy(true); setProblem(null);
    try {
      setItem(await b.updateOpportunity(item!.id, { value_amount: value, probability_pct: probability, assumption_note: form.note, next_step: form.next, expected_decision_on: form.decision || null, currency: form.currency }));
      onChanged();
    } catch (error) { setProblem(failure(error, t('biz.pipeline.save'))); } finally { setBusy(false); }
  }
  async function move(stage: OpportunityStage) {
    setBusy(true); setProblem(null);
    try { setItem(await b.moveOpportunity(item!.id, stage)); onChanged(); } catch (error) { setProblem(failure(error, stageLabel(stage))); } finally { setBusy(false); }
  }
  async function propose(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try { const created = await b.createProposal(item!.id, title.trim() || item!.name); setProposing(false); onChanged(); onOpenProposal(created.id); }
    catch (error) { setProblem(failure(error, t('biz.proposal.create'))); setBusy(false); }
  }
  const next = STAGES.filter((stage) => stage !== item.stage && stage !== 'won' && stage !== 'lost');
  return <Drawer title={item.name} onClose={onClose}>
    <div className="bzDrawer">
      <div className="bzLeadHead"><Badge tone={stageTone(item.stage)}>{stageLabel(item.stage)}</Badge><span className="muted" dir="auto">{item.account_name}</span></div>
      {item.lost_reason && <p className="muted">{item.lost_reason}</p>}
      {problem && <Note error>{problem}</Note>}
      {write && <div className="bzInline" role="group" aria-label={t('biz.pipeline.moveTo')}>
        {next.map((stage) => <button key={stage} type="button" className="secondary" disabled={busy} onClick={() => void move(stage)}>{stageLabel(stage)}</button>)}
        <button type="button" className="secondary dangerText" disabled={busy} onClick={() => setLosing(true)}>{t('biz.pipeline.markLost')}</button>
      </div>}
      {item.stage === 'won' && <Note>{t('biz.pipeline.wonNote')}</Note>}
      <form className="bzBlock" onSubmit={(event) => void save(event)} aria-label={t('biz.pipeline.assumptionsTitle')}>
        <h3>{t('biz.pipeline.assumptionsTitle')}</h3>
        <div className="bzGrid2">
          <label className="field"><span>{t('biz.pipeline.value')}</span><input inputMode="decimal" value={form.value} disabled={!write} onChange={(event) => setForm({ ...form, value: event.target.value })} /></label>
          <label className="field"><span>{t('biz.field.currency')}</span><select value={form.currency} disabled={!write} onChange={(event) => setForm({ ...form, currency: event.target.value as BusinessCurrency })}>{CURRENCIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label className="field"><span>{t('biz.pipeline.probability')}</span><input inputMode="decimal" value={form.probability} disabled={!write} onChange={(event) => setForm({ ...form, probability: event.target.value })} /></label>
          <label className="field"><span>{t('biz.pipeline.decision')}</span><input type="date" value={form.decision} disabled={!write} onChange={(event) => setForm({ ...form, decision: event.target.value })} /></label>
          <label className="field bzSpan2"><span>{t('biz.pipeline.note')}</span><input value={form.note} disabled={!write} onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>
          <label className="field bzSpan2"><span>{t('biz.pipeline.next')}</span><input value={form.next} disabled={!write} onChange={(event) => setForm({ ...form, next: event.target.value })} /></label>
        </div>
        <p className="muted bzHint">{t('biz.pipeline.weighted', { value: money(item.weighted_value, item.currency) })}</p>
        {write && <div className="actions"><button type="submit" disabled={busy}>{t('biz.pipeline.save')}</button></div>}
      </form>
      <section className="bzBlock"><h3>{t('biz.tab.proposals')}</h3>
        <p className="muted">{t('biz.pipeline.proposalCount', { count: item.proposals })}</p>
        {write && (proposing ? <form onSubmit={(event) => void propose(event)} aria-label={t('biz.proposal.create')}>
          <label className="field"><span>{t('biz.proposal.titleField')}</span><input value={title} onChange={(event) => setTitle(event.target.value)} data-autofocus /></label>
          <div className="bzInline"><button type="submit" disabled={busy}>{t('biz.proposal.create')}</button><button type="button" className="secondary" onClick={() => setProposing(false)}>{t('common.cancel')}</button></div>
        </form> : <button type="button" onClick={() => setProposing(true)}><FileText aria-hidden="true" />{t('biz.proposal.create')}</button>)}
      </section>
    </div>
    {losing && <ReasonDialog title={t('biz.pipeline.markLost')} label={t('biz.form.reason')} confirm={t('biz.pipeline.markLost')} onCancel={() => setLosing(false)}
      onConfirm={async (reason) => { setItem(await b.moveOpportunity(item.id, 'lost', reason)); setLosing(false); onChanged(); }} />}
  </Drawer>;
}

// ---- proposals ---------------------------------------------------------------------------------

export function ProposalsTab({ setup, openId, onOpen, onOpenProject, onGoPipeline, onChanged }: {
  setup: BusinessSetup; openId: string | null; onOpen: (id: string | null) => void; onOpenProject: (id: string) => void; onGoPipeline: () => void; onChanged: () => void;
}) {
  const [q, setQ] = useState(''), [search, setSearch] = useState('');
  const list = usePaged((offset) => b.listProposals({ q: search, limit: PAGE_SIZE, offset }), [search]);
  return <section className="bzSection" aria-label={t('biz.tab.proposals')}>
    <SearchBar label={t('biz.proposal.search')} value={q} onChange={(value) => { setQ(value); if (!value) setSearch(''); }} onSubmit={() => setSearch(q.trim())} />
    <ListState load={list.load} empty={list.items.length === 0} filtered={Boolean(search)} onRetry={() => void list.first()} onClear={() => { setQ(''); setSearch(''); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !search && <Empty title={t('biz.proposal.emptyTitle')} text={t('biz.proposal.emptyText')} action={<button type="button" className="secondary" onClick={onGoPipeline}>{t('biz.proposal.goPipeline')}</button>} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('biz.tab.proposals')}</caption>
        <thead><tr><th scope="col">{t('biz.col.proposal')}</th><th scope="col">{t('biz.col.state')}</th><th scope="col" className="num">{t('biz.col.total')}</th><th scope="col" className="num bzHideSm">{t('biz.col.version')}</th><th scope="col" className="bzHideMd">{t('biz.col.updated')}</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => onOpen(item.id)}><span dir="auto">{item.title}</span></button><small dir="auto">{item.account_name}</small></th>
          <td><Badge tone={proposalTone(item.status)}>{proposalStatusLabel(item.status)}</Badge></td>
          <td className="num">{money(item.current.total, item.current.currency)}</td>
          <td className="num bzHideSm">{item.current_version}</td>
          <td className="bzHideMd nowrap">{when(item.updated_at)}</td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {openId && <ProposalDrawer setup={setup} id={openId} onClose={() => onOpen(null)} onOpenProject={onOpenProject} onChanged={() => { void list.first(); onChanged(); }} />}
  </section>;
}

interface ContentForm { summary: string; scope: string; deliverables: string; exclusions: string; assumptions: string; criteria: string; lines: { description: string; quantity: string; unit_price: string; kind: LineItem['kind'] }[]; milestones: { title: string; amount: string; due: string; acceptance: string }[]; tax: string; cost: string; terms: string; currency: BusinessCurrency; tax_note: string; valid_until: string }

function toForm(content: ProposalContent): ContentForm {
  return {
    summary: content.summary, scope: content.scope, deliverables: content.deliverables.join('\n'), exclusions: content.exclusions.join('\n'), assumptions: content.assumptions.join('\n'),
    criteria: content.acceptance_criteria.join('\n'), lines: content.line_items.map((item) => ({ description: item.description, quantity: String(item.quantity), unit_price: String(item.unit_price), kind: item.kind })),
    milestones: content.milestones.map((item) => ({ title: item.title, amount: String(item.amount), due: item.due_in_days === null ? '' : String(item.due_in_days), acceptance: item.acceptance })),
    tax: String(content.tax_rate_pct), cost: String(content.internal_cost), terms: content.payment_terms, currency: content.currency, tax_note: content.tax_note, valid_until: content.valid_until ?? '',
  };
}

function fromForm(form: ContentForm): { ok: true; value: ProposalContent } | { ok: false; error: string } {
  const lines: LineItem[] = [];
  for (const line of form.lines) {
    if (!line.description.trim()) continue;
    const quantity = parseAmount(line.quantity || '1'), price = parseAmount(line.unit_price || '0');
    if (quantity === null || quantity <= 0 || price === null) return { ok: false, error: t('biz.proposal.lineInvalid') };
    lines.push({ description: line.description.trim(), kind: line.kind, quantity, unit_price: price });
  }
  const milestones: MilestonePlan[] = [];
  for (const item of form.milestones) {
    if (!item.title.trim()) continue;
    const amount = parseAmount(item.amount || '0'), due = item.due.trim() ? Number(item.due) : null;
    if (amount === null || (due !== null && (!Number.isInteger(due) || due < 0))) return { ok: false, error: t('biz.proposal.milestoneInvalid') };
    milestones.push({ title: item.title.trim(), amount, due_in_days: due, acceptance: item.acceptance.trim() });
  }
  const tax = parseAmount(form.tax || '0'), cost = parseAmount(form.cost || '0');
  if (tax === null || tax > 100 || cost === null) return { ok: false, error: t('biz.proposal.numbersInvalid') };
  return { ok: true, value: {
    currency: form.currency, summary: form.summary.trim(), scope: form.scope.trim(), deliverables: linesOf(form.deliverables), exclusions: linesOf(form.exclusions),
    assumptions: linesOf(form.assumptions), acceptance_criteria: linesOf(form.criteria), line_items: lines, milestones, tax_rate_pct: tax, tax_note: form.tax_note.trim(),
    internal_cost: cost, payment_terms: form.terms.trim(), valid_until: form.valid_until || null,
  } };
}

function ProposalDrawer({ setup, id, onClose, onOpenProject, onChanged }: { setup: BusinessSetup; id: string; onClose: () => void; onOpenProject: (id: string) => void; onChanged: () => void }) {
  const [item, setItem] = useState<BusinessProposal | null>(null), [form, setForm] = useState<ContentForm | null>(null);
  const [problem, setProblem] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false), [starting, setStarting] = useState(false), [start, setStart] = useState('');
  const show = (next: BusinessProposal) => { setItem(next); setForm(toForm(next.current)); };
  const reload = useCallback(async () => { try { show(await b.getProposal(id)); setProblem(null); } catch (error) { setProblem(failure(error, t('biz.proposal.load'))); } }, [id]);
  useEffect(() => { void reload(); }, [reload]);
  if (!item || !form) return <Drawer title={t('biz.proposal.title')} onClose={onClose}>{problem ? <Note error>{problem}</Note> : <Note>{t('biz.loading')}</Note>}</Drawer>;
  const editable = setup.permissions.write && (item.status === 'draft' || item.status === 'pending_approval');
  const parsed = fromForm(form);
  const preview = parsed.ok ? proposalTotals(parsed.value) : null;
  async function run(what: string, fn: () => Promise<BusinessProposal | void>, success?: string) {
    setBusy(true); setProblem(null); setNotice(null);
    try { const next = await fn(); if (next) show(next); else await reload(); if (success) setNotice(success); onChanged(); }
    catch (error) { setProblem(failure(error, what)); } finally { setBusy(false); }
  }
  const save = () => { if (!parsed.ok) { setProblem(parsed.error); return; } void run(t('biz.proposal.save'), () => b.saveProposal(item.id, parsed.value), t('biz.proposal.saved')); };
  const setField = (key: keyof ContentForm) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const current = item.current;
  return <Drawer title={item.title} onClose={onClose}>
    <div className="bzDrawer">
      <div className="bzLeadHead"><Badge tone={proposalTone(item.status)}>{proposalStatusLabel(item.status)}</Badge><span className="muted" dir="auto">{item.account_name}</span>
        <span className="muted">{t('biz.proposal.versionOf', { version: item.current_version, count: item.versions.length })}</span></div>
      {item.decision_note && <Note>{item.decision_note}</Note>}
      {notice && <Note>{notice}</Note>}
      {problem && <Note error>{problem}</Note>}
      {item.status === 'accepted' && <Note>{t('biz.proposal.acceptedNote', { version: item.accepted_version ?? 0, by: item.accepted_by ?? '', when: when(item.accepted_at) })}</Note>}
      {item.approval && <AcceptanceApproval approval={item.approval} canDecide={setup.permissions.decide} onDecided={(message) => { setNotice(message); void reload(); onChanged(); }} />}
      {current.locked && editable && <p className="muted bzHint">{t('biz.proposal.lockedHint')}</p>}
      <form className="bzProposalForm" onSubmit={(event) => { event.preventDefault(); save(); }} aria-label={t('biz.proposal.editor')}>
        <fieldset disabled={!editable} className="bzFieldset">
          <legend>{t('biz.proposal.scope')}</legend>
          <label className="field"><span>{t('biz.proposal.summary')}</span><textarea rows={2} value={form.summary} onChange={setField('summary')} dir="auto" /></label>
          <label className="field"><span>{t('biz.proposal.scopeField')}</span><textarea rows={3} value={form.scope} onChange={setField('scope')} dir="auto" /></label>
          <div className="bzGrid2">
            <label className="field"><span>{t('biz.proposal.deliverables')}</span><textarea rows={3} value={form.deliverables} onChange={setField('deliverables')} dir="auto" /></label>
            <label className="field"><span>{t('biz.proposal.exclusions')}</span><textarea rows={3} value={form.exclusions} onChange={setField('exclusions')} dir="auto" /></label>
            <label className="field"><span>{t('biz.proposal.assumptions')}</span><textarea rows={3} value={form.assumptions} onChange={setField('assumptions')} dir="auto" /></label>
            <label className="field"><span>{t('biz.proposal.criteria')}</span><textarea rows={3} value={form.criteria} onChange={setField('criteria')} dir="auto" /></label>
          </div>
          <p className="muted bzHint">{t('biz.proposal.oneLine')}</p>
        </fieldset>
        <fieldset disabled={!editable} className="bzFieldset">
          <legend>{t('biz.proposal.price')}</legend>
          <div className="bzRows">{form.lines.map((line, index) => <div key={index} className="bzRow bzLineRow">
            <label className="field"><span>{t('biz.proposal.item')}</span><input value={line.description} onChange={(event) => setForm({ ...form, lines: form.lines.map((x, i) => (i === index ? { ...x, description: event.target.value } : x)) })} dir="auto" /></label>
            <label className="field"><span>{t('biz.proposal.kind')}</span><select value={line.kind} onChange={(event) => setForm({ ...form, lines: form.lines.map((x, i) => (i === index ? { ...x, kind: event.target.value as LineItem['kind'] } : x)) })}>
              {(['service', 'labor', 'external_cost', 'other'] as const).map((kind) => <option key={kind} value={kind}>{t(`biz.lineKind.${kind}`)}</option>)}</select></label>
            <label className="field"><span>{t('biz.proposal.quantity')}</span><input inputMode="decimal" value={line.quantity} onChange={(event) => setForm({ ...form, lines: form.lines.map((x, i) => (i === index ? { ...x, quantity: event.target.value } : x)) })} /></label>
            <label className="field"><span>{t('biz.proposal.unitPrice')}</span><input inputMode="decimal" value={line.unit_price} onChange={(event) => setForm({ ...form, lines: form.lines.map((x, i) => (i === index ? { ...x, unit_price: event.target.value } : x)) })} /></label>
            <button type="button" className="iconButton" aria-label={t('biz.proposal.removeLine', { n: index + 1 })} onClick={() => setForm({ ...form, lines: form.lines.filter((_, i) => i !== index) })}><Trash2 aria-hidden="true" /></button>
          </div>)}</div>
          <button type="button" className="secondary" onClick={() => setForm({ ...form, lines: [...form.lines, { description: '', quantity: '1', unit_price: '', kind: 'service' }] })}><Plus aria-hidden="true" />{t('biz.proposal.addLine')}</button>
          <div className="bzGrid3">
            <label className="field"><span>{t('biz.field.currency')}</span><select value={form.currency} onChange={(event) => setForm({ ...form, currency: event.target.value as BusinessCurrency })}>{CURRENCIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
            <label className="field"><span>{t('biz.proposal.tax')}</span><input inputMode="decimal" value={form.tax} onChange={setField('tax')} /></label>
            <label className="field"><span>{t('biz.proposal.cost')}</span><input inputMode="decimal" value={form.cost} onChange={setField('cost')} /></label>
          </div>
          <label className="field"><span>{t('biz.proposal.taxNote')}</span><input value={form.tax_note} onChange={setField('tax_note')} dir="auto" /></label>
        </fieldset>
        <fieldset disabled={!editable} className="bzFieldset">
          <legend>{t('biz.proposal.milestones')}</legend>
          <div className="bzRows">{form.milestones.map((row, index) => <div key={index} className="bzRow bzMilestoneRow">
            <label className="field"><span>{t('biz.proposal.milestone')}</span><input value={row.title} onChange={(event) => setForm({ ...form, milestones: form.milestones.map((x, i) => (i === index ? { ...x, title: event.target.value } : x)) })} dir="auto" /></label>
            <label className="field"><span>{t('biz.proposal.amount')}</span><input inputMode="decimal" value={row.amount} onChange={(event) => setForm({ ...form, milestones: form.milestones.map((x, i) => (i === index ? { ...x, amount: event.target.value } : x)) })} /></label>
            <label className="field"><span>{t('biz.proposal.dueDays')}</span><input inputMode="numeric" value={row.due} onChange={(event) => setForm({ ...form, milestones: form.milestones.map((x, i) => (i === index ? { ...x, due: event.target.value } : x)) })} /></label>
            <label className="field"><span>{t('biz.proposal.acceptance')}</span><input value={row.acceptance} onChange={(event) => setForm({ ...form, milestones: form.milestones.map((x, i) => (i === index ? { ...x, acceptance: event.target.value } : x)) })} dir="auto" /></label>
            <button type="button" className="iconButton" aria-label={t('biz.proposal.removeMilestone', { n: index + 1 })} onClick={() => setForm({ ...form, milestones: form.milestones.filter((_, i) => i !== index) })}><Trash2 aria-hidden="true" /></button>
          </div>)}</div>
          <button type="button" className="secondary" onClick={() => setForm({ ...form, milestones: [...form.milestones, { title: '', amount: '', due: '', acceptance: '' }] })}><Plus aria-hidden="true" />{t('biz.proposal.addMilestone')}</button>
          <div className="bzGrid2">
            <label className="field"><span>{t('biz.proposal.terms')}</span><input value={form.terms} onChange={setField('terms')} dir="auto" /></label>
            <label className="field"><span>{t('biz.proposal.validUntil')}</span><input type="date" value={form.valid_until} onChange={setField('valid_until')} /></label>
          </div>
        </fieldset>
        <dl className="bzTotals" aria-label={t('biz.proposal.totals')}>
          <div><dt>{t('biz.proposal.subtotal')}</dt><dd>{money(preview?.subtotal ?? current.subtotal, form.currency)}</dd></div>
          <div><dt>{t('biz.proposal.taxAmount')}</dt><dd>{money(preview?.tax ?? current.tax, form.currency)}</dd></div>
          <div><dt>{t('biz.proposal.total')}</dt><dd><strong>{money(preview?.total ?? current.total, form.currency)}</strong></dd></div>
          <div><dt>{t('biz.proposal.margin')}</dt><dd>{money(preview?.margin ?? current.margin, form.currency)}</dd></div>
        </dl>
        {preview && !preview.milestonesMatch && <p className="bzIssues">{t('biz.proposal.milestonesMismatch', { milestones: money(preview.milestoneTotal, form.currency), total: money(preview.total, form.currency) })}</p>}
        {current.issues.length > 0 && <ul className="bzIssues" aria-label={t('biz.proposal.issues')}>{current.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
        {current.warnings.length > 0 && <ul className="bzIssues bzWarnings">{current.warnings.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
        <div className="bzInline">
          {editable && <button type="submit" className="secondary" disabled={busy}>{current.locked ? t('biz.proposal.saveNewVersion') : t('biz.proposal.save')}</button>}
          {setup.permissions.write && item.status === 'draft' && <button type="button" disabled={busy || current.issues.length > 0} onClick={() => void run(t('biz.proposal.request'), () => b.requestAcceptance(item.id), t('biz.proposal.requested'))}><Send aria-hidden="true" />{t('biz.proposal.request')}</button>}
          <button type="button" className="secondary" disabled={busy} onClick={() => void run(t('biz.proposal.export'), async () => { setNotice(t('biz.proposal.exported', { name: await b.exportProposal(item.id) })); })}><Download aria-hidden="true" />{t('biz.proposal.export')}</button>
          {setup.permissions.write && (item.status === 'draft' || item.status === 'pending_approval') && <button type="button" className="secondary dangerText" disabled={busy} onClick={() => setWithdrawing(true)}>{t('biz.proposal.withdraw')}</button>}
        </div>
      </form>
      {item.status === 'accepted' && (item.project_id
        ? <button type="button" onClick={() => onOpenProject(item.project_id!)}>{t('biz.proposal.openProject')}</button>
        : setup.permissions.write && (starting ? <form className="bzInline" onSubmit={(event) => { event.preventDefault(); void run(t('biz.proposal.startProject'), async () => { const project = await b.createProject(item.id, start || null); onOpenProject(project.id); }); }} aria-label={t('biz.proposal.startProject')}>
          <label className="field"><span>{t('biz.project.startOn')}</span><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
          <button type="submit" disabled={busy}>{t('biz.proposal.startProject')}</button></form>
          : <button type="button" onClick={() => setStarting(true)}>{t('biz.proposal.startProject')}</button>))}
      <details className="bzVersions"><summary>{t('biz.proposal.versions', { count: item.versions.length })}</summary>
        <ul>{item.versions.map((version) => <li key={version.version}>{t('biz.proposal.versionLine', { version: version.version, total: money(version.total, current.currency), when: when(version.created_at) })}{version.locked && <Badge tone="neutral">{t('biz.proposal.locked')}</Badge>}<code dir="ltr">{version.content_hash.slice(0, 12)}</code></li>)}</ul>
      </details>
    </div>
    {withdrawing && <ReasonDialog title={t('biz.proposal.withdraw')} label={t('biz.form.reason')} confirm={t('biz.proposal.withdraw')} onCancel={() => setWithdrawing(false)}
      onConfirm={async (reason) => { show(await b.withdrawProposal(item.id, reason)); setWithdrawing(false); onChanged(); }} />}
  </Drawer>;
}

// ---- projects ----------------------------------------------------------------------------------

export function ProjectsTab({ setup, openId, onOpen, onGoProposals }: { setup: BusinessSetup; openId: string | null; onOpen: (id: string | null) => void; onGoProposals: () => void }) {
  const [q, setQ] = useState(''), [search, setSearch] = useState('');
  const list = usePaged((offset) => b.listProjects({ q: search, limit: PAGE_SIZE, offset }), [search]);
  const open = list.items.find((item) => item.id === openId) ?? null;
  return <section className="bzSection" aria-label={t('biz.tab.projects')}>
    <SearchBar label={t('biz.project.search')} value={q} onChange={(value) => { setQ(value); if (!value) setSearch(''); }} onSubmit={() => setSearch(q.trim())} />
    <ListState load={list.load} empty={list.items.length === 0} filtered={Boolean(search)} onRetry={() => void list.first()} onClear={() => { setQ(''); setSearch(''); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !search && <Empty title={t('biz.project.emptyTitle')} text={t('biz.project.emptyText')} action={<button type="button" className="secondary" onClick={onGoProposals}>{t('biz.project.goProposals')}</button>} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('biz.tab.projects')}</caption>
        <thead><tr><th scope="col">{t('biz.col.project')}</th><th scope="col">{t('biz.col.state')}</th><th scope="col" className="num">{t('biz.col.contract')}</th><th scope="col" className="num bzHideSm">{t('biz.col.milestones')}</th><th scope="col" className="num bzHideMd">{t('biz.col.paid')}</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => onOpen(item.id)}><span dir="auto">{item.name}</span></button><small dir="auto">{item.account_name}</small></th>
          <td><Badge tone={item.status === 'delivered' || item.status === 'closed' ? 'success' : item.status === 'on_hold' ? 'warning' : 'neutral'}>{projectStatusLabel(item.status)}</Badge></td>
          <td className="num">{money(item.contract_total, item.currency)}</td>
          <td className="num bzHideSm">{t('biz.project.accepted', { accepted: item.accepted_milestones, total: item.milestones.length })}</td>
          <td className="num bzHideMd">{money(item.paid_value, item.currency)}</td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {openId && <ProjectDrawer setup={setup} initial={open} id={openId} onClose={() => onOpen(null)} onChanged={() => void list.first()} />}
  </section>;
}

function ProjectDrawer({ setup, initial, id, onClose, onChanged }: { setup: BusinessSetup; initial: BusinessProject | null; id: string; onClose: () => void; onChanged: () => void }) {
  const [item, setItem] = useState<BusinessProject | null>(initial), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [paid, setPaid] = useState<Record<string, string>>({});
  useEffect(() => { void b.getProject(id).then(setItem).catch((error) => setProblem(failure(error, t('biz.project.load')))); }, [id]);
  if (!item) return <Drawer title={t('biz.project.title')} onClose={onClose}>{problem ? <Note error>{problem}</Note> : <Note>{t('biz.loading')}</Note>}</Drawer>;
  const write = setup.permissions.write;
  async function run(what: string, fn: () => Promise<BusinessProject>) {
    setBusy(true); setProblem(null);
    try { setItem(await fn()); onChanged(); } catch (error) { setProblem(failure(error, what)); } finally { setBusy(false); }
  }
  return <Drawer title={item.name} onClose={onClose}>
    <div className="bzDrawer">
      <div className="bzLeadHead"><span className="muted" dir="auto">{item.account_name}</span><span>{money(item.contract_total, item.currency)}</span><span className="muted">{t('biz.project.fromVersion', { version: item.proposal_version })}</span></div>
      {problem && <Note error>{problem}</Note>}
      <label className="field bzCompactField"><span>{t('biz.project.status')}</span><select value={item.status} disabled={!write || busy} onChange={(event) => void run(t('biz.project.status'), () => b.updateProject(item.id, { status: event.target.value as ProjectStatus }))}>
        {PROJECT_STATUSES.map((value) => <option key={value} value={value}>{projectStatusLabel(value)}</option>)}</select></label>
      {item.contract_limits && <p className="muted" dir="auto">{item.contract_limits}</p>}
      <dl className="bzTotals"><div><dt>{t('biz.project.acceptedValue')}</dt><dd>{money(item.accepted_value, item.currency)}</dd></div><div><dt>{t('biz.project.paidValue')}</dt><dd>{money(item.paid_value, item.currency)}</dd></div></dl>
      <section className="bzBlock" aria-labelledby="bz-milestones"><h3 id="bz-milestones">{t('biz.proposal.milestones')}</h3>
        {item.milestones.length === 0 ? <p className="muted">{t('biz.project.noMilestones')}</p> : <ol className="bzMilestones">{item.milestones.map((row) => <li key={row.id}>
          <div className="bzMilestoneHead"><strong dir="auto">{row.title}</strong><span>{money(row.amount, row.currency)}</span><span className="muted">{t('biz.project.due', { date: day(row.due_on) })}</span></div>
          {row.acceptance_criteria && <p className="muted" dir="auto">{row.acceptance_criteria}</p>}
          <div className="bzInline">
            <label className="bzCompact"><span>{t('biz.project.milestoneStatus')}</span><select value={row.status} disabled={!write || busy} onChange={(event) => void run(t('biz.project.milestoneStatus'), () => b.updateMilestone(row.id, { status: event.target.value as MilestoneStatus }))}>
              {MILESTONE_STATUSES.map((value) => <option key={value} value={value}>{milestoneStatusLabel(value)}</option>)}</select></label>
            {write && <form className="bzInline" onSubmit={(event) => { event.preventDefault(); const value = parseAmount(paid[row.id] ?? ''); if (value === null) { setProblem(t('biz.project.paidInvalid')); return; } void run(t('biz.project.recordPayment'), () => b.updateMilestone(row.id, { paid_amount: value })); }} aria-label={t('biz.project.recordPaymentFor', { title: row.title })}>
              <label className="bzCompact"><span>{t('biz.project.paid')}</span><input inputMode="decimal" value={paid[row.id] ?? (row.paid_amount?.toString() ?? '')} onChange={(event) => setPaid({ ...paid, [row.id]: event.target.value })} /></label>
              <button type="submit" className="secondary" disabled={busy}>{t('biz.project.recordPayment')}</button></form>}
          </div>
          {row.accepted_at && <small className="muted">{t('biz.project.acceptedBy', { by: row.accepted_by ?? '', when: when(row.accepted_at) })}</small>}
          {row.paid_on && <small className="muted">{t('biz.project.paidOn', { date: day(row.paid_on) })}</small>}
        </li>)}</ol>}
      </section>
      <ProjectMoney setup={setup} project={item} onProjectChanged={() => { void b.getProject(item.id).then(setItem).catch((error) => setProblem(failure(error, t('biz.project.load')))); onChanged(); }} />
    </div>
  </Drawer>;
}

