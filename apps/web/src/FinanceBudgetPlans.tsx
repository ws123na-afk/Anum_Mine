// Finance, Bills and budgets, Budgets: the owner's own budgets and the salary allocation
// (docs/finance.md#budgets-and-planning). Spending, savings into a goal (the emergency reserve, a sinking fund), money
// home and debt payments, monthly, weekly or over a custom period. Spent, coming bills, the forecast and the risk are the
// API's, from linked transactions and unpaid bills, each counted once; the page never adds money across currencies.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { Layers, Pencil, PiggyBank, Plus } from 'lucide-react';
import type {
  FinanceAllocationReport, FinanceBudgetKind, FinanceBudgetOverspend, FinanceBudgetPeriod, FinanceBudgetRollover, FinanceCategory, FinanceCategoryTree,
  FinanceDebt, FinanceGoal, FinancePlanBudgetInput, FinancePlanBudgetLine, FinancePlanReport,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as b from './lib/financeBudgetsApi';
import { categoryLabel, formatAmount, isolate } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import { Progress, RowMenu } from './FinancePlanningParts';
import { budgetFormError, budgetInput, emptyBudget, meterValue, nodeName, riskTone, rolloverText, shortRollover, showsRollover } from './lib/financeBudgets';
import './financeBudgets.css';

const KINDS: readonly FinanceBudgetKind[] = ['spending', 'savings', 'emergency', 'sinking_fund', 'remittance', 'debt'];
const PERIODS: readonly FinanceBudgetPeriod[] = ['monthly', 'weekly', 'custom'];
const BUCKETS = ['housing', 'food', 'transport', 'family', 'debts', 'cards', 'savings', 'remittances', 'other'] as const;
const ALERTS = [50, 60, 70, 75, 80, 85, 90, 95, 99];
const SAVING: readonly FinanceBudgetKind[] = ['savings', 'emergency', 'sinking_fund'];
const ROLLOVERS: readonly FinanceBudgetRollover[] = ['none', 'surplus', 'capped'];
const OVERSPENDS: readonly FinanceBudgetOverspend[] = ['absorb', 'carry'];
const shortDate = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' });

export function PlanBudgetsSection({ base }: { base: string }) {
  const [report, setReport] = useState<Load<FinancePlanReport>>({ kind: 'loading' });
  const [alloc, setAlloc] = useState<Load<FinanceAllocationReport>>({ kind: 'loading' });
  const [editing, setEditing] = useState<FinancePlanBudgetLine | 'new' | null>(null);
  const [removing, setRemoving] = useState<FinancePlanBudgetLine | null>(null), [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    try { setReport({ kind: 'ready', value: await b.planBudgets(null, true) }); } catch (e) { setReport(failed(e)); }
    try { setAlloc({ kind: 'ready', value: await b.budgetAllocation() }); } catch (e) { setAlloc(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function remove() {
    if (!removing) return;
    setBusy(true);
    try { await b.deletePlanBudget(removing.id); setFlash({ text: t('fbud.deleted', { name: removing.name }) }); await load(); }
    catch (e) { setFlash({ error: true, text: describeError(e) }); } finally { setBusy(false); setRemoving(null); }
  }
  async function toggle(line: FinancePlanBudgetLine) {
    try { await b.updatePlanBudget(line.id, { ...budgetInput(line), active: !line.active }); await load(); }
    catch (e) { setFlash({ error: true, text: describeError(e) }); }
  }
  const add = <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('fbud.new')}</button>;
  return <>
    <FinCard title={t('fbud.title')} subtitle={t('fbud.subtitle')} icon={<Layers />} actions={report.kind === 'ready' && report.value.budgets.length > 0 ? add : undefined}>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      {report.kind === 'loading' && <Notice busy>{t('fbud.loading')}</Notice>}
      {report.kind === 'denied' && <Denied />}
      {report.kind === 'error' && <LoadError message={t('fbud.error', { message: report.message })} retry={() => void load()} />}
      {report.kind === 'ready' && (report.value.budgets.length === 0
        ? <Empty icon={<Layers aria-hidden="true" />} title={t('fbud.empty')} text={t('fbud.emptyText')} action={add} />
        : <>
          {report.value.totals.map((row) => <p key={row.currency} className="muted fbudTotals">{t('fbud.totals', { currency: row.currency,
            spent: isolate(formatAmount(row.spent, row.currency)), budget: isolate(formatAmount(row.available ?? row.budget, row.currency)), coming: isolate(formatAmount(row.coming, row.currency)) })}</p>)}
          <ul className="fbudList">{report.value.budgets.map((line) => <BudgetRow key={line.id} line={line} onEdit={() => setEditing(line)}
            onToggle={() => void toggle(line)} onRemove={() => setRemoving(line)} />)}</ul>
        </>)}
    </FinCard>
    <AllocationCard alloc={alloc} retry={() => void load()} />
    {editing && <BudgetDialog base={base} line={editing === 'new' ? null : editing} onClose={(saved) => {
      setEditing(null); if (saved) { setFlash({ text: t('fbud.saved', { name: saved }) }); void load(); }
    }} />}
    {removing && <ConfirmDialog title={t('fbud.deleteTitle', { name: removing.name })} confirmLabel={t('fbud.delete', { name: removing.name })} danger busy={busy}
      onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>{t('fbud.deleteText')}</ConfirmDialog>}
  </>;
}

function BudgetRow({ line, onEdit, onToggle, onRemove }: { line: FinancePlanBudgetLine; onEdit: () => void; onToggle: () => void; onRemove: () => void }) {
  const tone = riskTone(line.risk);
  const meterTone = tone === 'bad' ? 'bad' : tone === 'warn' ? 'warn' : 'ok';
  return <li className={`fbudItem ${line.active ? '' : 'paused'}`}>
    <div className="fbudHead">
      <strong dir="auto">{line.name}</strong>
      <StatusChip tone={tone}>{t(`fbud.risk.${line.risk}` as MessageKey)}</StatusChip>
      {!line.active && <StatusChip>{t('fbud.paused')}</StatusChip>}
      <RowMenu label={t('fplan.menu', { name: line.name })} items={[
        { label: t('fbud.edit', { name: line.name }), onSelect: onEdit },
        { label: line.active ? t('fbud.pause') : t('fbud.resume'), onSelect: onToggle },
        { label: t('fbud.delete', { name: line.name }), onSelect: onRemove, danger: true },
      ]} />
    </div>
    <small className="muted">{t(`fbud.kind.${line.kind}` as MessageKey)} · {t(`fbud.periodKind.${line.period}` as MessageKey)} · {t('fbud.period', { from: isolate(shortDate(line.period_start)), to: isolate(shortDate(line.period_end)) })}
      {line.kind === 'spending' && <> · {line.fixed ? t('fbud.fixed') : t('fbud.variable')} · {line.essential ? t('fbud.essential') : t('fbud.optional')}</>}
      {line.reserve_in_safe_to_spend && <> · {t('fbud.keptBack')}</>}</small>
    {line.categories.length > 0 && <small className="muted">{line.categories.map((c) => categoryLabel(c)).join(' · ')}</small>}
    {line.spent !== null ? <>
      <Progress percent={meterValue(line)} tone={meterTone} label={t('fbud.meter', { name: line.name, percent: line.percent ?? 0 })} />
      <div className="fbudFigures">
        <span>{t('fbud.spentOf', { spent: isolate(formatAmount(line.spent, line.currency)), amount: isolate(formatAmount(line.available ?? line.amount, line.currency)) })}</span>
        {Number(line.over) > 0 ? <span className="bad">{t('fbud.over', { amount: isolate(formatAmount(line.over, line.currency)) })}</span>
          : line.remaining !== null && <span>{t('fbud.left', { amount: isolate(formatAmount(line.remaining, line.currency)) })}</span>}
        {Number(line.coming) > 0 && <span>{t('fbud.coming', { amount: isolate(formatAmount(line.coming, line.currency)) })}</span>}
        {line.forecast !== null && line.kind === 'spending' && line.days_left > 0
          && <span>{t('fbud.forecast', { date: isolate(shortDate(line.period_end)), amount: isolate(formatAmount(line.forecast, line.currency)) })}</span>}
        <span className="muted">{t('fbud.rows', { count: line.rows })}</span>
      </div>
      {line.kind === 'spending' && showsRollover(line) && <Rollover line={line} />}
      {line.coming_items.length > 0 && <ul className="fbudComing">{line.coming_items.map((c) => <li key={`${c.bill_id}-${c.due_on}`}>
        <span dir="auto">{c.name}</span><small>{isolate(shortDate(c.due_on))}</small><Money value={c.amount} currency={line.currency} /></li>)}</ul>}
    </> : <p className="muted">{t('fbud.noRecord', { saved: isolate(formatAmount(line.goal_saved ?? '0', line.currency)), target: isolate(formatAmount(line.goal_target ?? '0', line.currency)) })}</p>}
  </li>;
}

function Rollover({ line }: { line: FinancePlanBudgetLine }) {
  const money = (value: string) => isolate(formatAmount(value, line.currency));
  const carried = Number(line.carried_in);
  return <div className="fbudRoll">
    <p className="fbudRollFigures">
      <span>{carried < 0 ? t('fbud.roll.deficitIn', { amount: money(String(-carried)) }) : t('fbud.roll.carriedIn', { amount: money(line.carried_in) })}</span>
      <span>{t('fbud.roll.available', { amount: money(line.available ?? line.amount), base: money(line.amount) })}</span>
    </p>
    <small className="muted">{rolloverText({ rollover: line.rollover, cap: line.rollover_cap, overspend: line.overspend }, line.currency)}</small>
    {line.chain.length > 0 && <details className="fbudChain">
      <summary>{t('fbud.roll.chain', { count: line.chain_periods })}</summary>
      <ol className="fbudChainList" aria-label={t('fbud.roll.chainCaption', { name: line.name })}>{[...line.chain].reverse().map((p) => <li key={p.start}>
        <div className="fbudChainHead"><strong>{t('fbud.period', { from: isolate(shortDate(p.start)), to: isolate(shortDate(p.end)) })}</strong>
          <small className="muted">{shortRollover({ rollover: p.rollover, cap: p.cap, overspend: p.overspend }, p.currency)}{p.restarted && <> · {t('fbud.roll.restarted')}</>}</small></div>
        <dl className="fbudChainFigures">
          <div><dt>{t('fbud.roll.col.in')}</dt><dd><Money value={p.carried_in} currency={p.currency} /></dd></div>
          <div><dt>{t('fbud.roll.col.available')}</dt><dd><Money value={p.available} currency={p.currency} /></dd></div>
          <div><dt>{t('fbud.roll.col.spent')}</dt><dd><Money value={p.spent} currency={p.currency} /></dd></div>
          <div><dt>{t('fbud.roll.col.out')}</dt><dd>{p.carried_out === null ? t('fbud.roll.open') : <Money value={p.carried_out} currency={p.currency} />}</dd></div>
        </dl>
      </li>)}</ol>
      {line.chain_periods > line.chain.length && <small className="muted">{t('fbud.roll.older', { count: line.chain_periods - line.chain.length })}</small>}
    </details>}
  </div>;
}

function AllocationCard({ alloc, retry }: { alloc: Load<FinanceAllocationReport>; retry: () => void }) {
  return <FinCard title={t('fbud.alloc.title')} subtitle={t('fbud.alloc.subtitle')} icon={<PiggyBank />}>
    {alloc.kind === 'loading' && <Notice busy>{t('fbud.loading')}</Notice>}
    {alloc.kind === 'denied' && <Denied />}
    {alloc.kind === 'error' && <LoadError message={alloc.message} retry={retry} />}
    {alloc.kind === 'ready' && (alloc.value.currencies.length === 0 ? <p className="muted">{t('fbud.alloc.empty')}</p>
      : alloc.value.currencies.map((view) => <div key={view.currency} className="fbudAlloc">
        {view.income !== null
          ? <p><strong>{t('fbud.alloc.income', { amount: isolate(formatAmount(view.income, view.currency)) })}</strong>
            {view.income_source && <small className="muted"> · {t(`fbud.alloc.incomeFrom.${view.income_source}` as MessageKey)}</small>}</p>
          : <Notice>{t('fbud.alloc.noIncome', { currency: view.currency })}</Notice>}
        <table className="finGrid fbudAllocTable"><tbody>
          {BUCKETS.filter((k) => Number(view.buckets[k] ?? 0) > 0).map((k) => <tr key={k}><th scope="row">{t(`fbud.bucket.${k}` as MessageKey)}</th>
            <td className="num"><Money value={view.buckets[k]} currency={view.currency} /></td></tr>)}
        </tbody></table>
        {view.unallocated !== null && (Number(view.unallocated) >= 0
          ? <p>{t('fbud.alloc.unallocated', { amount: isolate(formatAmount(view.unallocated, view.currency)) })}</p>
          : <Notice error>{t('fbud.alloc.overplanned', { amount: isolate(formatAmount(String(-Number(view.unallocated)), view.currency)) })}</Notice>)}
        {view.left_out.map((l) => <p key={l.budget_id} className="muted">{t('fbud.alloc.leftOut', { name: l.name })}</p>)}
      </div>))}
  </FinCard>;
}

function BudgetDialog({ base, line, onClose }: { base: string; line: FinancePlanBudgetLine | null; onClose: (saved: string | null) => void }) {
  const [form, setForm] = useState<FinancePlanBudgetInput>(() => (line ? budgetInput(line) : emptyBudget(base)));
  const [tree, setTree] = useState<FinanceCategoryTree | null>(null);
  const [goals, setGoals] = useState<FinanceGoal[]>([]), [debts, setDebts] = useState<FinanceDebt[]>([]);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const ids = { name: useId(), kind: useId(), period: useId(), currency: useId(), amount: useId(), start: useId(), end: useId(), goal: useId(), alloc: useId(), alert: useId(), notes: useId(),
    rollover: useId(), cap: useId(), overspend: useId(), rollHelp: useId() };
  useEffect(() => {
    void b.categoryTree().then(setTree).catch(() => setTree(null));
    void api.listGoals().then(setGoals).catch(() => setGoals([]));
    void api.listDebts().then(setDebts).catch(() => setDebts([]));
  }, []);
  const set = (patch: Partial<FinancePlanBudgetInput>) => setForm((f) => ({ ...f, ...patch }));
  const spendingCategories = useMemo(() => tree ? tree.groups.flatMap((g) => g.categories.filter((c) => c.spending).map((c) => ({ group: nodeName(g), node: c }))) : [], [tree]);
  const chosenSubs = useMemo(() => spendingCategories.filter((c) => form.categories.includes(c.node.id)).flatMap((c) => c.node.subcategories.filter((s) => !s.archived)), [spendingCategories, form.categories]);
  const toggleCategory = (c: FinanceCategory) => set({ categories: form.categories.includes(c) ? form.categories.filter((x) => x !== c) : [...form.categories, c] });
  const toggleSub = (sub: string) => set({ subcategory_ids: form.subcategory_ids.includes(sub) ? form.subcategory_ids.filter((x) => x !== sub) : [...form.subcategory_ids, sub] });
  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = budgetFormError(form);
    if (problem) { setError(problem); return; }
    setBusy(true); setError(null);
    try {
      const body: FinancePlanBudgetInput = { ...form, name: form.name.trim(), amount: form.amount.trim(), currency: form.currency.trim().toUpperCase(),
        ...(form.kind === 'spending' ? { rollover_cap: form.rollover === 'capped' ? (form.rollover_cap ?? '').trim() : null }
          : { rollover: null, rollover_cap: null, overspend: null }) };
      const saved = line ? await b.updatePlanBudget(line.id, body) : await b.createPlanBudget(body);
      onClose(saved.name);
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const saving = SAVING.includes(form.kind);
  return <Modal title={line ? t('fbud.edit', { name: line.name }) : t('fbud.new')} className="formDialog fbudDialog" onClose={() => onClose(null)}>
    <form onSubmit={(e) => void submit(e)} noValidate className="fbudForm">
      <label className="field" htmlFor={ids.name}><span>{t('fbud.form.name')}</span><input id={ids.name} value={form.name} maxLength={80} onChange={(e) => set({ name: e.target.value })} /></label>
      <div className="fbudRow">
        <label className="field" htmlFor={ids.kind}><span>{t('fbud.form.kind')}</span>
          <select id={ids.kind} value={form.kind} onChange={(e) => set({ kind: e.target.value as FinanceBudgetKind })}>
            {KINDS.map((k) => <option key={k} value={k}>{t(`fbud.kind.${k}` as MessageKey)}</option>)}</select></label>
        <label className="field" htmlFor={ids.period}><span>{t('fbud.form.period')}</span>
          <select id={ids.period} value={form.period} onChange={(e) => set({ period: e.target.value as FinanceBudgetPeriod })}>
            {PERIODS.map((p) => <option key={p} value={p}>{t(`fbud.periodKind.${p}` as MessageKey)}</option>)}</select></label>
      </div>
      <div className="fbudRow">
        <label className="field" htmlFor={ids.amount}><span>{t('fbud.form.amount')}</span>
          <input id={ids.amount} inputMode="decimal" value={form.amount} onChange={(e) => set({ amount: e.target.value })} dir="ltr" /></label>
        <label className="field" htmlFor={ids.currency}><span>{t('fbud.form.currency')}</span>
          <input id={ids.currency} value={form.currency} maxLength={3} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} dir="ltr" /></label>
      </div>
      {(form.period === 'custom' || form.period === 'weekly') && <div className="fbudRow">
        <label className="field" htmlFor={ids.start}><span>{form.period === 'weekly' ? t('fbud.form.weekStart') : t('fbud.form.startsOn')}</span>
          <input id={ids.start} type="date" value={form.starts_on ?? ''} onChange={(e) => set({ starts_on: e.target.value || null })} /></label>
        {form.period === 'custom' && <label className="field" htmlFor={ids.end}><span>{t('fbud.form.endsOn')}</span>
          <input id={ids.end} type="date" value={form.ends_on ?? ''} onChange={(e) => set({ ends_on: e.target.value || null })} /></label>}
      </div>}
      {form.kind === 'spending' && <fieldset className="fbudPick"><legend>{t('fbud.form.categories')}</legend>
        {!tree && <Notice busy>{t('fcat.loading')}</Notice>}
        <div className="fbudChecks">{spendingCategories.map(({ node }) => <label key={node.id} className="fbudCheck">
          <input type="checkbox" checked={form.categories.includes(node.id)} onChange={() => toggleCategory(node.id)} />{categoryLabel(node.id)}</label>)}</div>
        {chosenSubs.length > 0 && <><p className="muted">{t('fbud.form.subcategories')}</p>
          <div className="fbudChecks">{chosenSubs.map((s) => <label key={s.id} className="fbudCheck">
            <input type="checkbox" checked={form.subcategory_ids.includes(s.id)} onChange={() => toggleSub(s.id)} /><span dir="auto">{nodeName(s)}</span></label>)}</div></>}
        <label className="fbudCheck"><input type="checkbox" checked={form.fixed} onChange={(e) => set({ fixed: e.target.checked })} />{t('fbud.form.fixed')}</label>
        <label className="fbudCheck"><input type="checkbox" checked={form.essential ?? true} onChange={(e) => set({ essential: e.target.checked })} />{t('fbud.form.essential')}</label>
        <label className="fbudCheck"><input type="checkbox" checked={form.reserve_in_safe_to_spend} onChange={(e) => set({ reserve_in_safe_to_spend: e.target.checked })} />{t('fbud.form.reserve')}</label>
      </fieldset>}
      {form.kind === 'spending' && <fieldset className="fbudPick" aria-describedby={ids.rollHelp}><legend>{t('fbud.form.rollover')}</legend>
        <div className="fbudRow">
          <label className="field" htmlFor={ids.rollover}><span>{t('fbud.form.rolloverLeft')}</span>
            <select id={ids.rollover} value={form.rollover ?? 'none'} onChange={(e) => set({ rollover: e.target.value as FinanceBudgetRollover })}>
              {ROLLOVERS.map((r) => <option key={r} value={r}>{t(`fbud.roll.mode.${r}` as MessageKey)}</option>)}</select></label>
          {form.rollover === 'capped' && <label className="field" htmlFor={ids.cap}><span>{t('fbud.form.rolloverCap')}</span>
            <input id={ids.cap} inputMode="decimal" value={form.rollover_cap ?? ''} onChange={(e) => set({ rollover_cap: e.target.value || null })} dir="ltr" /></label>}
          <label className="field" htmlFor={ids.overspend}><span>{t('fbud.form.overspend')}</span>
            <select id={ids.overspend} value={form.overspend ?? 'absorb'} onChange={(e) => set({ overspend: e.target.value as FinanceBudgetOverspend })}>
              {OVERSPENDS.map((o) => <option key={o} value={o}>{t(`fbud.roll.overOpt.${o}` as MessageKey)}</option>)}</select></label>
        </div>
        <small id={ids.rollHelp} className="muted">{t('fbud.form.rolloverHelp')}</small>
      </fieldset>}
      {saving && (goals.length === 0 ? <Notice>{t('fbud.form.noGoals')}</Notice>
        : <label className="field" htmlFor={ids.goal}><span>{t('fbud.form.goal')}</span>
          <select id={ids.goal} value={form.goal_id ?? ''} onChange={(e) => { const g = goals.find((x) => x.id === e.target.value); set({ goal_id: e.target.value || null, ...(g ? { currency: g.currency } : {}) }); }}>
            <option value="">{t('fbud.form.goalNone')}</option>
            {goals.map((g) => <option key={g.id} value={g.id}>{g.name} ({g.currency})</option>)}</select></label>)}
      {form.kind === 'debt' && debts.length > 0 && <fieldset className="fbudPick"><legend>{t('fbud.form.debts')}</legend>
        <div className="fbudChecks">{debts.filter((d) => d.currency === form.currency).map((d) => <label key={d.id} className="fbudCheck">
          <input type="checkbox" checked={form.debt_ids.includes(d.id)} onChange={() => set({ debt_ids: form.debt_ids.includes(d.id) ? form.debt_ids.filter((x) => x !== d.id) : [...form.debt_ids, d.id] })} />
          <span dir="auto">{d.name}</span></label>)}</div></fieldset>}
      <div className="fbudRow">
        <label className="field" htmlFor={ids.alloc}><span>{t('fbud.form.allocation')}</span>
          <select id={ids.alloc} value={form.allocation ?? ''} onChange={(e) => set({ allocation: (e.target.value || null) as FinancePlanBudgetInput['allocation'] })}>
            <option value="">{t('fbud.form.allocationAuto')}</option>
            {BUCKETS.map((k) => <option key={k} value={k}>{t(`fbud.bucket.${k}` as MessageKey)}</option>)}</select></label>
        <label className="field" htmlFor={ids.alert}><span>{t('fbud.form.alert')}</span>
          <select id={ids.alert} value={form.alert_percent} onChange={(e) => set({ alert_percent: Number(e.target.value) })}>
            {ALERTS.map((p) => <option key={p} value={p}>{t('fbud.form.alertPercent', { percent: p })}</option>)}</select></label>
      </div>
      <label className="fbudCheck"><input type="checkbox" checked={form.active} onChange={(e) => set({ active: e.target.checked })} />{t('fbud.form.active')}</label>
      <label className="field" htmlFor={ids.notes}><span>{t('fbud.form.notes')}</span>
        <textarea id={ids.notes} value={form.notes ?? ''} maxLength={500} rows={2} onChange={(e) => set({ notes: e.target.value || null })} /></label>
      {error && <Notice error>{error}</Notice>}
      <div className="dialogActions"><button type="button" className="secondary" onClick={() => onClose(null)}>{t('common.cancel')}</button>
        <button type="submit" disabled={busy}><Pencil aria-hidden="true" />{t('fbud.form.save')}</button></div>
    </form>
  </Modal>;
}

