// Finance, Suggestions (docs/finance.md#suggestions, #rules-and-learning, #automation-policies): one queue of everything
// that waits for the owner, the owner's versioned rules with preview, apply and undo, merchant spellings and
// subcategories, and the automation switches. Every figure and decision is the API's; nothing changes until the owner
// accepts, approves or applies, and every change that touches earlier rows can be undone.
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { Bot, Check, History, Inbox, ListChecks, Pencil, Plus, RotateCcw, Search, Store, Tags, Trash2, Undo2, X } from 'lucide-react';
import type {
  FinanceAccount, FinanceAutomation, FinanceAutomationPolicy, FinanceCategory, FinanceMerchantAlias, FinanceRule, FinanceRuleBatchPage,
  FinanceRuleContent, FinanceRuleDetail, FinanceRulePage, FinanceRulePreview, FinanceSubcategory, FinanceSuggestion, FinanceSuggestionQueue,
  FinanceTransactionType,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as rx from './lib/financeRulesApi';
import { categoryLabel, formatAmount, isolate } from './lib/finance';
import { TRANSACTION_TYPES } from './lib/financeRegister';
import {
  EMPTY_RULE, NEVER_AUTOMATED, RULE_CATEGORIES, SUGGESTION_VIEWS, acceptMode, confidencePercent, confidenceTone, fromContent, kindChips, matchSummary,
  suggestionsViewFromHash, toContent, type RuleForm, type SuggestionsView,
} from './lib/financeSuggestions';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinanceTabs, FinancePageHead, StatusChip, tabPanel } from './FinanceShell';
import './financeSuggestions.css';

const PAGE = 20;
const typeLabel = (type: FinanceTransactionType) => t(`freg.type.${type}` as MessageKey);
const when = (value: string) => formatDateTime(value, { day: 'numeric', month: 'short', year: 'numeric' });
type Flash = { text: string; error?: boolean; undo?: string } | null;

export function SuggestionsPage({ onOpenHash }: { onOpenHash?: (hash: string) => void }) {
  const [view, setView] = useState<SuggestionsView>(() => suggestionsViewFromHash(typeof location === 'undefined' ? '' : location.hash));
  const [ruleSeed, setRuleSeed] = useState(0);
  return <div className="fsugPage">
    <FinancePageHead title={t('fsug.title')} subtitle={t('fsug.subtitle')} icon={<ListChecks />}
      tabs={<FinanceTabs label={t('fsug.tabs')} idPrefix="fsug" value={view} onChange={setView}
        items={SUGGESTION_VIEWS.map((id) => ({ id, label: t(`fsug.view.${id}` as MessageKey) }))} />} />
    <div {...tabPanel('fsug', view)}>
      {view === 'queue' && <QueueView onOpenHash={onOpenHash} onAddRule={() => { setView('rules'); setRuleSeed((n) => n + 1); }} />}
      {view === 'rules' && <RulesView openNew={ruleSeed} />}
      {view === 'merchants' && <MerchantsView />}
      {view === 'automation' && <AutomationView />}
    </div>
  </div>;
}

function useFlash(): [Flash, (f: Flash) => void] {
  const [flash, setFlash] = useState<Flash>(null);
  return [flash, setFlash];
}

function FlashLine({ flash, onUndo, busy }: { flash: Flash; onUndo?: (batchId: string) => void; busy?: boolean }) {
  if (!flash) return null;
  return <div className="fsugFlash">
    <Notice error={flash.error}>{flash.text}</Notice>
    {flash.undo && onUndo && <button type="button" className="secondary" disabled={busy} onClick={() => onUndo(flash.undo!)}><Undo2 aria-hidden="true" />{t('fsug.undo')}</button>}
  </div>;
}

function Pager({ total, offset, limit, onPage }: { total: number; offset: number; limit: number; onPage: (offset: number) => void }) {
  if (total <= limit) return null;
  return <nav className="fsugPager" aria-label={t('fsug.page', { from: offset + 1, to: Math.min(total, offset + limit), total })}>
    <button type="button" className="secondary" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>{t('fsug.prev')}</button>
    <span>{t('fsug.page', { from: offset + 1, to: Math.min(total, offset + limit), total })}</span>
    <button type="button" className="secondary" disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>{t('fsug.next')}</button>
  </nav>;
}

async function undo(batchId: string, setFlash: (f: Flash) => void, after: () => void) {
  try { const done = await rx.undoBatch(batchId); setFlash({ text: t('fsug.undone', { count: done.restored }) }); after(); }
  catch (e) { setFlash({ text: describeError(e), error: true }); }
}

// -- queue -----------------------------------------------------------------------------------------------

function headText(item: FinanceSuggestion): string {
  const p = item.params ?? {};
  // A reconciliation exception reads with the reconciliation page's own words for its kind.
  if (item.kind === 'reconciliation') return t('fsug.head.reconciliation', { account: p.account ?? '', exception: t(`frec.kind.${p.exception}` as MessageKey) });
  const vars: Record<string, string | number> = { ...p, category: p.category ? categoryLabel(p.category as FinanceCategory) : '', current: p.current ? categoryLabel(p.current as FinanceCategory) : '' };
  if (item.kind === 'bill_due') vars.count = Number(p.days ?? 0);
  // The forecast's shortage (docs/finance.md#forecast) names the day it expects the balance to go below zero or the reserve.
  if (item.kind === 'cash_shortage' && (p.reason === 'below_zero' || p.reason === 'below_reserve')) {
    return t(`fsug.head.cash_shortage.${p.reason}` as MessageKey, { currency: p.currency ?? '', date: isolate(p.date ?? '') });
  }
  return t(`fsug.head.${item.kind}` as MessageKey, vars);
}

function whyText(item: FinanceSuggestion): string {
  if (item.kind === 'external') return item.why;
  if (item.kind === 'reconciliation') {
    const p = item.params ?? {};
    if (p.exception === 'missing_period') return t('fsug.why.reconciliation.missing', { month: p.month ?? '' });
    return t(p.severity === 'blocking' ? 'fsug.why.reconciliation.blocking' : 'fsug.why.reconciliation.warning', { start: p.start ?? '', end: p.end ?? '' });
  }
  const p = item.params ?? {};
  if (item.kind === 'cash_shortage' && (p.reason === 'below_zero' || p.reason === 'below_reserve')) {
    return t(`fsug.why.cash_shortage.${p.reason}` as MessageKey, { amount: isolate(formatAmount(p.threshold ?? '0', p.currency ?? 'AED')) });
  }
  return t(`fsug.why.${item.kind}` as MessageKey, { ...p, category: p.category ? categoryLabel(p.category as FinanceCategory) : '', current: p.current ? categoryLabel(p.current as FinanceCategory) : '' });
}

function QueueView({ onOpenHash, onAddRule }: { onOpenHash?: (hash: string) => void; onAddRule: () => void }) {
  const [kind, setKind] = useState('all');
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<Load<FinanceSuggestionQueue>>({ kind: 'loading' });
  const [busy, setBusy] = useState('');
  const [flash, setFlash] = useFlash();
  const load = useCallback(() => {
    setState((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    rx.listSuggestions({ kind: kind === 'all' ? null : kind, limit: PAGE, offset }).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [kind, offset]);
  useEffect(() => { load(); }, [load]);
  const decide = async (item: FinanceSuggestion, accept: boolean) => {
    setBusy(item.key); setFlash(null);
    try {
      if (accept) {
        const done = await rx.acceptSuggestion(item.key, item.payload_hash);
        setFlash(done.batch ? { text: t('fsug.accepted', { count: done.batch.changed }), undo: done.batch.id } : { text: t('fsug.acknowledged') });
      } else {
        await rx.rejectSuggestion(item.key);
        setFlash({ text: t('fsug.dismissed') });
      }
      load();
    } catch (e) { setFlash({ text: describeError(e), error: true }); load(); } finally { setBusy(''); }
  };
  const open = (href: string | null) => {
    if (!href) return;
    if (onOpenHash) onOpenHash(href); else location.hash = href;
  };
  if (state.kind === 'loading') return <Notice busy>{t('fsug.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={state.message} retry={load} />;
  const q = state.value;
  const chips = kindChips(q);
  return <div className="fsugQueue">
    <div className="fsugChips" role="group" aria-label={t('fsug.kinds')}>
      {chips.map((c) => <button key={c.kind} type="button" className={`fsugChip ${kind === c.kind ? 'on' : ''}`} aria-pressed={kind === c.kind}
        onClick={() => { setKind(c.kind); setOffset(0); }}>{t(`fsug.kind.${c.kind}` as MessageKey)}<span className="finTabBadge">{c.count}</span></button>)}
    </div>
    {q.unavailable.length > 0 && <FinBanner tone="warn">{t('fsug.unavailable', { names: q.unavailable.join(', ') })}</FinBanner>}
    <FlashLine flash={flash} busy={Boolean(busy)} onUndo={(id) => void undo(id, setFlash, load)} />
    {q.items.length === 0
      ? (kind === 'all'
        ? <Empty icon={<Inbox aria-hidden="true" />} title={t('fsug.empty.title')} text={t('fsug.empty.text')}
          action={<button type="button" onClick={onAddRule}><Plus aria-hidden="true" />{t('fsug.empty.action')}</button>} />
        : <p className="muted">{t('fsug.emptyKind')}</p>)
      : <ul className="fsugList">{q.items.map((item) => <SuggestionCard key={item.key} item={item} busy={busy === item.key} disabled={Boolean(busy)}
        onAccept={() => void decide(item, true)} onReject={() => void decide(item, false)} onOpen={() => open(item.href)} />)}</ul>}
    <Pager total={q.total} offset={offset} limit={PAGE} onPage={setOffset} />
    {q.dismissed > 0 && <p className="muted fsugFoot">{t('fsug.decidedCount', { count: q.dismissed })}</p>}
  </div>;
}

function SuggestionCard({ item, busy, disabled, onAccept, onReject, onOpen }: {
  item: FinanceSuggestion; busy: boolean; disabled: boolean; onAccept: () => void; onReject: () => void; onOpen: () => void;
}) {
  const headingId = useId();
  const mode = acceptMode(item);
  const pct = confidencePercent(item.confidence);
  return <li className={`fsugCard kind-${item.kind}`} aria-labelledby={headingId}>
    <div className="fsugCardHead">
      <span className="fsugKind">{t(`fsug.kind.${item.kind}` as MessageKey)}</span>
      {pct !== null && <StatusChip tone={confidenceTone(item.confidence)}>{t('fsug.confidence', { percent: pct })}</StatusChip>}
      {item.occurred_on && <small className="muted"><bdi dir="ltr">{item.occurred_on}</bdi></small>}
    </div>
    <h3 id={headingId} dir="auto">{headText(item)}</h3>
    <p className="fsugWhy" dir="auto">{whyText(item)}</p>
    {item.effect.length > 0 && <p className="fsugEffect">{item.effect.map((e, i) => <strong key={i} dir="ltr">{isolate(formatAmount(e.direction === 'out' ? `-${e.amount}` : e.amount, e.currency))}</strong>)}</p>}
    {item.evidence.length > 0 && <details className="fsugEvidence"><summary>{t('fsug.evidence')}</summary>
      <ul>{item.evidence.map((ev, i) => <li key={i}><span dir="auto">{ev.label}</span>{ev.value && <small className="muted" dir="ltr"> {ev.value}</small>}</li>)}</ul></details>}
    <div className="fsugActions">
      {mode !== 'none' && <button type="button" disabled={disabled} onClick={onAccept}><Check aria-hidden="true" />
        {busy ? t('finance.saving') : t(mode === 'apply' ? 'fsug.accept.apply' : 'fsug.accept.acknowledge')}</button>}
      {item.href && <button type="button" className="secondary" disabled={disabled} onClick={onOpen}>{t('fsug.review')}</button>}
      <button type="button" className="secondary" disabled={disabled} onClick={onReject}><X aria-hidden="true" />{t('fsug.dismiss')}</button>
    </div>
  </li>;
}

// -- rules ------------------------------------------------------------------------------------------------

function RulesView({ openNew }: { openNew: number }) {
  const [state, setState] = useState<Load<FinanceRulePage>>({ kind: 'loading' });
  const [batches, setBatches] = useState<Load<FinanceRuleBatchPage>>({ kind: 'loading' });
  const [filter, setFilter] = useState('all'), [q, setQ] = useState(''), [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<{ rule?: FinanceRule } | null>(openNew > 0 ? {} : null);
  const [versions, setVersions] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [busy, setBusy] = useState(''), [flash, setFlash] = useFlash();
  const searchId = useId(), stateId = useId();
  const load = useCallback(() => {
    setState((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    rx.listRules({ state: filter, q: q.trim() || undefined, limit: PAGE, offset }).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
    rx.listBatches({ limit: 10, offset: 0 }).then((value) => setBatches({ kind: 'ready', value })).catch((e) => setBatches(failed(e)));
  }, [filter, q, offset]);
  useEffect(() => { load(); }, [load]);
  const decide = async (rule: FinanceRule, approve: boolean) => {
    const pending = rule.pending;
    if (!pending) return;
    setBusy(rule.rule_id); setFlash(null);
    try {
      if (approve) await rx.approveRule(pending); else await rx.rejectRule(rule.rule_id, pending.version);
      setFlash({ text: t(approve ? 'fsug.rules.approved' : 'fsug.rules.rejected') }); load();
    } catch (e) { setFlash({ text: describeError(e), error: true }); } finally { setBusy(''); }
  };
  return <div className="fsugRules">
    <FinCard title={t('fsug.rules.title')} icon={<Tags />} subtitle={t('fsug.rules.subtitle')}
      actions={<button type="button" onClick={() => setEditing({})}><Plus aria-hidden="true" />{t('fsug.rules.new')}</button>}>
      <div className="fsugFilters">
        <label htmlFor={searchId} className="fsugSearch"><Search aria-hidden="true" /><span className="visuallyHidden">{t('fsug.rules.search')}</span>
          <input id={searchId} type="search" placeholder={t('fsug.rules.search')} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} /></label>
        <label htmlFor={stateId}><span>{t('fsug.rules.state')}</span>
          <select id={stateId} value={filter} onChange={(e) => { setFilter(e.target.value); setOffset(0); }}>
            {['all', 'active', 'pending', 'disabled'].map((s) => <option key={s} value={s}>{t(`fsug.rules.state.${s}` as MessageKey)}</option>)}
          </select></label>
      </div>
      <FlashLine flash={flash} busy={Boolean(busy)} onUndo={(id) => void undo(id, setFlash, load)} />
      {state.kind === 'loading' && <Notice busy>{t('fsug.loading')}</Notice>}
      {state.kind === 'denied' && <Denied />}
      {state.kind === 'error' && <LoadError message={state.message} retry={load} />}
      {state.kind === 'ready' && <>
        {state.value.legacy_rules > 0 && <FinBanner>{t('fsug.rules.legacy', { count: state.value.legacy_rules })}</FinBanner>}
        {state.value.items.length === 0
          ? <Empty icon={<Tags aria-hidden="true" />} title={t('fsug.rules.empty.title')} text={t('fsug.rules.empty.text')}
            action={<button type="button" onClick={() => setEditing({})}><Plus aria-hidden="true" />{t('fsug.rules.new')}</button>} />
          : <ul className="fsugRuleList">{state.value.items.map((rule) => <RuleRow key={rule.rule_id} rule={rule} busy={busy === rule.rule_id} disabled={Boolean(busy)}
            onApprove={() => void decide(rule, true)} onReject={() => void decide(rule, false)} onEdit={() => setEditing({ rule })}
            onVersions={() => setVersions(rule.rule_id)} onApply={() => setApplying(rule.rule_id)} />)}</ul>}
        <Pager total={state.value.total} offset={offset} limit={PAGE} onPage={setOffset} />
      </>}
    </FinCard>
    <FinCard title={t('fsug.batches.title')} icon={<History />}>
      {batches.kind === 'loading' && <Notice busy>{t('fsug.loading')}</Notice>}
      {(batches.kind === 'error' || batches.kind === 'denied') && <LoadError message={batches.message} retry={load} />}
      {batches.kind === 'ready' && (batches.value.items.length === 0 ? <p className="muted">{t('fsug.batches.empty')}</p>
        : <ul className="fsugBatches">{batches.value.items.map((b) => <li key={b.id}>
          <span>{t('fsug.batches.rows', { count: b.changed })}</span>
          <small className="muted">{t(`fsug.batches.origin.${b.origin}` as MessageKey)}, {when(b.created_at)}</small>
          {b.status === 'undone' ? <StatusChip>{t('fsug.batches.undone')}</StatusChip>
            : <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void undo(b.id, setFlash, load)}><Undo2 aria-hidden="true" />{t('fsug.undo')}</button>}
        </li>)}</ul>)}
    </FinCard>
    {editing && <RuleEditor rule={editing.rule} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setFlash({ text: t('fsug.rules.saved') }); load(); }} />}
    {versions && <VersionsDialog ruleId={versions} onClose={() => setVersions(null)} onChanged={(text) => { setVersions(null); setFlash({ text }); load(); }} />}
    {applying && <ApplyDialog ruleId={applying} onClose={() => setApplying(null)}
      onApplied={(count, batchId) => { setApplying(null); setFlash({ text: t('fsug.preview.applied', { count }), undo: batchId }); load(); }} />}
  </div>;
}

function RuleRow({ rule, busy, disabled, onApprove, onReject, onEdit, onVersions, onApply }: {
  rule: FinanceRule; busy: boolean; disabled: boolean; onApprove: () => void; onReject: () => void; onEdit: () => void; onVersions: () => void; onApply: () => void;
}) {
  const shown = rule.current ?? rule.pending!;
  const c = shown.content, m = c.match;
  return <li className="fsugRule">
    <div className="fsugRuleMain">
      <strong dir="auto">{matchSummary(m).join(', ')}</strong>
      <small className="muted">{t(`fsug.dir.${m.direction ?? 'any'}` as MessageKey)}
        {m.min_amount || m.max_amount ? `, ${t('fsug.rules.range', { currency: m.currency ?? '', min: m.min_amount ?? '0', max: m.max_amount ?? '-' })}` : ''}</small>
      <span className="fsugRuleFiles">{categoryLabel(c.category)}{c.transaction_type ? `, ${typeLabel(c.transaction_type)}` : ''}</span>
      <small className="muted">{t(`fsug.prov.${shown.provenance}` as MessageKey)}, {t('fsug.rules.col.version')} {shown.version}</small>
      {rule.current && c.enabled === false && <StatusChip>{t('fsug.rules.off')}</StatusChip>}
      {rule.pending && <StatusChip tone="warn">{t('fsug.rules.pending', { version: rule.pending.version })}</StatusChip>}
    </div>
    <div className="fsugRuleActions">
      {rule.pending && <><button type="button" disabled={disabled} onClick={onApprove}><Check aria-hidden="true" />{busy ? t('finance.saving') : t('fsug.rules.approve')}</button>
        <button type="button" className="secondary" disabled={disabled} onClick={onReject}>{t('fsug.rules.reject')}</button></>}
      {rule.current && c.enabled !== false && <button type="button" className="secondary" disabled={disabled} onClick={onApply}>{t('fsug.rules.applyEarlier')}</button>}
      <button type="button" className="secondary" disabled={disabled} onClick={onEdit}><Pencil aria-hidden="true" />{t('fsug.rules.edit')}</button>
      <button type="button" className="secondary" disabled={disabled} onClick={onVersions}><History aria-hidden="true" />{t('fsug.rules.versions')}</button>
    </div>
  </li>;
}

export function PreviewBlock({ preview }: { preview: FinanceRulePreview }) {
  return <div className="fsugPreview" aria-live="polite">
    <p>{preview.changes ? t('fsug.preview.counts', { changes: preview.changes, unchanged: preview.unchanged, manual: preview.manual_kept, sms: preview.sms_kept })
      : t('fsug.preview.none')}</p>
    {preview.truncated && <p className="muted">{t('fsug.preview.truncated')}</p>}
    {preview.rows.length > 0 && <div className="fsugPreviewTable"><table>
      <thead><tr><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('finance.col.description')}</th><th scope="col">{t('fsug.preview.from')}</th><th scope="col">{t('fsug.preview.to')}</th></tr></thead>
      <tbody>{preview.rows.map((r) => <tr key={r.transaction_id}>
        <td><bdi dir="ltr">{r.posted_on}</bdi></td>
        <td dir="auto">{r.description}<br /><small dir="ltr">{isolate(formatAmount(r.amount, r.currency))}</small></td>
        <td>{r.before_type === 'uncategorized' ? typeLabel('uncategorized') : categoryLabel(r.before_category)}</td>
        <td>{categoryLabel(r.after_category)}</td>
      </tr>)}</tbody>
    </table></div>}
  </div>;
}

function RuleEditor({ rule, onClose, onSaved }: { rule?: FinanceRule; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<RuleForm>(rule ? fromContent((rule.pending ?? rule.current)!.content) : EMPTY_RULE);
  const [accounts, setAccounts] = useState<FinanceAccount[]>([]);
  const [subs, setSubs] = useState<FinanceSubcategory[]>([]);
  const [preview, setPreview] = useState<FinanceRulePreview | null>(null);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const ids = { merchant: useId(), words: useId(), reference: useId(), direction: useId(), min: useId(), max: useId(), currency: useId(), account: useId(),
    category: useId(), sub: useId(), type: useId(), err: useId() };
  useEffect(() => {
    api.listAccounts().then(setAccounts).catch(() => setAccounts([]));
    rx.listSubcategories().then(setSubs).catch(() => setSubs([]));
  }, []);
  const set = <K extends keyof RuleForm>(key: K, value: RuleForm[K]) => { setForm((f) => ({ ...f, [key]: value })); setPreview(null); };
  const content = (): FinanceRuleContent | null => {
    const made = toContent(form);
    if ('error' in made) { setError(t(`fsug.editor.error.${made.error}` as MessageKey)); return null; }
    setError(null); return made.content;
  };
  const doPreview = async () => {
    const c = content(); if (!c) return;
    setBusy(true);
    try { setPreview(await rx.previewContent(c)); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const c = content(); if (!c) return;
    setBusy(true);
    try { await rx.saveAndApprove(c, { ruleId: rule?.rule_id }); onSaved(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const field = (id: string, label: string, input: ReactNode, hint?: string) => <div className="field"><label htmlFor={id}>{label}</label>{input}{hint && <small className="muted">{hint}</small>}</div>;
  return <Modal title={t(rule ? 'fsug.editor.edit' : 'fsug.editor.new')} className="fsugEditor" onClose={onClose}>
    <form onSubmit={save} aria-describedby={error ? ids.err : undefined} noValidate>
      <div className="fsugGrid">
        {field(ids.merchant, t('fsug.editor.merchant'), <input id={ids.merchant} value={form.merchant} maxLength={80} onChange={(e) => set('merchant', e.target.value)} />, t('fsug.editor.merchantHint'))}
        {field(ids.words, t('fsug.editor.words'), <input id={ids.words} value={form.words} maxLength={400} onChange={(e) => set('words', e.target.value)} />, t('fsug.editor.wordsHint'))}
        {field(ids.reference, t('fsug.editor.reference'), <input id={ids.reference} value={form.reference} maxLength={60} onChange={(e) => set('reference', e.target.value)} />)}
        {field(ids.direction, t('fsug.editor.direction'), <select id={ids.direction} value={form.direction} onChange={(e) => set('direction', e.target.value as RuleForm['direction'])}>
          {(['any', 'in', 'out'] as const).map((d) => <option key={d} value={d}>{t(`fsug.dir.${d}` as MessageKey)}</option>)}</select>)}
        {field(ids.min, t('fsug.editor.minAmount'), <input id={ids.min} inputMode="decimal" value={form.minAmount} onChange={(e) => set('minAmount', e.target.value)} />)}
        {field(ids.max, t('fsug.editor.maxAmount'), <input id={ids.max} inputMode="decimal" value={form.maxAmount} onChange={(e) => set('maxAmount', e.target.value)} />)}
        {field(ids.currency, t('fsug.editor.currency'), <input id={ids.currency} value={form.currency} maxLength={3} dir="ltr" onChange={(e) => set('currency', e.target.value.toUpperCase())} />)}
        {field(ids.account, t('fsug.editor.account'), <select id={ids.account} value={form.accountId} onChange={(e) => set('accountId', e.target.value)}>
          <option value="">{t('fsug.editor.anyAccount')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>)}
        {field(ids.category, t('fsug.editor.category'), <select id={ids.category} value={form.category} onChange={(e) => { set('category', e.target.value); set('subcategoryId', ''); }}>
          {RULE_CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select>)}
        {field(ids.sub, t('fsug.editor.subcategory'), <select id={ids.sub} value={form.subcategoryId} onChange={(e) => set('subcategoryId', e.target.value)}>
          <option value="">{t('fsug.editor.none')}</option>{subs.filter((s) => s.parent === form.category && !s.archived).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>)}
        {field(ids.type, t('fsug.editor.type'), <select id={ids.type} value={form.transactionType} onChange={(e) => set('transactionType', e.target.value)}>
          <option value="">{t('fsug.editor.typeAuto')}</option>{TRANSACTION_TYPES.filter((k) => k !== 'uncategorized').map((k) => <option key={k} value={k}>{typeLabel(k)}</option>)}</select>)}
      </div>
      <label className="fsugCheck"><input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />{t('fsug.editor.enabled')}</label>
      <label className="fsugCheck"><input type="checkbox" checked={form.autoApply} onChange={(e) => set('autoApply', e.target.checked)} />{t('fsug.editor.autoApply')}</label>
      {error && <p id={ids.err} className="cmFieldError" role="alert">{error}</p>}
      {preview && <PreviewBlock preview={preview} />}
      <div className="actions">
        <button type="submit" disabled={busy}>{busy ? t('finance.saving') : t('fsug.editor.save')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void doPreview()}>{t('fsug.editor.preview')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button>
      </div>
    </form>
  </Modal>;
}

function VersionsDialog({ ruleId, onClose, onChanged }: { ruleId: string; onClose: () => void; onChanged: (text: string) => void }) {
  const [state, setState] = useState<Load<FinanceRuleDetail>>({ kind: 'loading' });
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { rx.getRule(ruleId).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e))); }, [ruleId]);
  useEffect(() => { load(); }, [load]);
  const restore = async (version: number) => {
    setBusy(true); setError(null);
    try { const made = await rx.revertRule(ruleId, version); await rx.approveRule(made); onChanged(t('fsug.versions.restored')); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const current = state.kind === 'ready' ? Math.max(0, ...state.value.versions.filter((v) => v.status === 'approved').map((v) => v.version)) : 0;
  return <Modal title={t('fsug.versions.title')} className="fsugVersions" onClose={onClose}>
    {state.kind === 'loading' && <Notice busy>{t('fsug.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={state.message} retry={load} />}
    {state.kind === 'ready' && <ol className="fsugVersionList">{state.value.versions.map((v) => <li key={v.version}>
      <div><strong>{t('fsug.rules.col.version')} {v.version}</strong> <StatusChip tone={v.status === 'approved' ? 'ok' : v.status === 'proposed' ? 'warn' : 'muted'}>{t(`fsug.versions.status.${v.status}` as MessageKey)}</StatusChip>
        <p dir="auto">{matchSummary(v.content.match).join(', ')}: {categoryLabel(v.content.category)}</p>
        <small className="muted">{t(`fsug.prov.${v.provenance}` as MessageKey)}, {when(v.created_at)}{v.reverted_from ? `, ${t('fsug.versions.restoredFrom', { version: v.reverted_from })}` : ''}</small></div>
      {v.status === 'approved' && v.version !== current && <button type="button" className="secondary" disabled={busy} onClick={() => void restore(v.version)}><RotateCcw aria-hidden="true" />{t('fsug.versions.restore')}</button>}
    </li>)}</ol>}
    {error && <Notice error>{error}</Notice>}
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
  </Modal>;
}

function ApplyDialog({ ruleId, onClose, onApplied }: { ruleId: string; onClose: () => void; onApplied: (count: number, batchId: string) => void }) {
  const [state, setState] = useState<Load<FinanceRulePreview>>({ kind: 'loading' });
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { setState({ kind: 'loading' }); rx.previewRule(ruleId).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e))); }, [ruleId]);
  useEffect(() => { load(); }, [load]);
  const apply = async (preview: FinanceRulePreview) => {
    setBusy(true); setError(null);
    try { const batch = await rx.applyRule(ruleId, preview.preview_hash); onApplied(batch.changed, batch.id); } catch (e) { setError(describeError(e)); load(); } finally { setBusy(false); }
  };
  return <Modal title={t('fsug.preview.title')} className="fsugApply" onClose={onClose}>
    {state.kind === 'loading' && <Notice busy>{t('fsug.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={state.message} retry={load} />}
    {state.kind === 'ready' && <PreviewBlock preview={state.value} />}
    {error && <Notice error>{error}</Notice>}
    <div className="actions">
      {state.kind === 'ready' && state.value.changes > 0 && <button type="button" disabled={busy} onClick={() => void apply(state.value)}>{busy ? t('finance.saving') : t('fsug.preview.apply', { count: state.value.changes })}</button>}
      <button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button>
    </div>
  </Modal>;
}

// -- merchants and subcategories ------------------------------------------------------------------------------

function MerchantsView() {
  const [aliases, setAliases] = useState<Load<FinanceMerchantAlias[]>>({ kind: 'loading' });
  const [subs, setSubs] = useState<Load<FinanceSubcategory[]>>({ kind: 'loading' });
  const [alias, setAlias] = useState(''), [merchant, setMerchant] = useState('');
  const [parent, setParent] = useState<string>('groceries'), [name, setName] = useState('');
  const [removing, setRemoving] = useState<FinanceMerchantAlias | null>(null);
  const [busy, setBusy] = useState(false), [flash, setFlash] = useFlash();
  const ids = { alias: useId(), merchant: useId(), parent: useId(), name: useId() };
  const load = useCallback(() => {
    rx.listAliases().then((v) => setAliases({ kind: 'ready', value: v })).catch((e) => setAliases(failed(e)));
    rx.listSubcategories().then((v) => setSubs({ kind: 'ready', value: v })).catch((e) => setSubs(failed(e)));
  }, []);
  useEffect(() => { load(); }, [load]);
  const run = async (work: () => Promise<unknown>, text: string) => {
    setBusy(true); setFlash(null);
    try { await work(); setFlash({ text }); load(); return true; } catch (e) { setFlash({ text: describeError(e), error: true }); return false; } finally { setBusy(false); }
  };
  return <div className="fsugMerchants">
    <FinCard title={t('fsug.alias.title')} icon={<Store />} subtitle={t('fsug.alias.subtitle')}>
      <form className="fsugInline" onSubmit={(e) => { e.preventDefault(); if (alias.trim() && merchant.trim()) void run(() => rx.saveAlias({ alias, merchant }), t('fsug.alias.saved')).then((ok) => { if (ok) { setAlias(''); } }); }}>
        <div className="field"><label htmlFor={ids.alias}>{t('fsug.alias.alias')}</label><input id={ids.alias} dir="auto" value={alias} maxLength={80} onChange={(e) => setAlias(e.target.value)} /></div>
        <div className="field"><label htmlFor={ids.merchant}>{t('fsug.alias.merchant')}</label><input id={ids.merchant} dir="auto" value={merchant} maxLength={80} onChange={(e) => setMerchant(e.target.value)} /></div>
        <button type="submit" disabled={busy || alias.trim().length < 2 || merchant.trim().length < 2}><Plus aria-hidden="true" />{t('fsug.alias.add')}</button>
      </form>
      <FlashLine flash={flash} />
      {aliases.kind === 'loading' && <Notice busy>{t('fsug.loading')}</Notice>}
      {aliases.kind === 'denied' && <Denied />}
      {aliases.kind === 'error' && <LoadError message={aliases.message} retry={load} />}
      {aliases.kind === 'ready' && (aliases.value.length === 0 ? <p className="muted">{t('fsug.alias.empty')}</p>
        : <ul className="fsugAliasList">{aliases.value.map((a) => <li key={a.id}><span dir="auto">{a.alias}</span><span aria-hidden="true">:</span><strong dir="auto">{a.merchant}</strong>
          <button type="button" className="iconButton" disabled={busy} aria-label={`${t('fsug.alias.remove')} ${a.alias}`} onClick={() => setRemoving(a)}><Trash2 aria-hidden="true" /></button></li>)}</ul>)}
    </FinCard>
    <FinCard title={t('fsug.sub.title')} icon={<Tags />} subtitle={t('fsug.sub.subtitle')}>
      <form className="fsugInline" onSubmit={(e) => { e.preventDefault(); if (name.trim()) void run(() => rx.saveSubcategory({ parent: parent as FinanceCategory, name, archived: false }), t('fsug.sub.saved')).then((ok) => { if (ok) setName(''); }); }}>
        <div className="field"><label htmlFor={ids.parent}>{t('fsug.sub.parent')}</label><select id={ids.parent} value={parent} onChange={(e) => setParent(e.target.value)}>
          {RULE_CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></div>
        <div className="field"><label htmlFor={ids.name}>{t('fsug.sub.name')}</label><input id={ids.name} dir="auto" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} /></div>
        <button type="submit" disabled={busy || !name.trim()}><Plus aria-hidden="true" />{t('fsug.sub.add')}</button>
      </form>
      {subs.kind === 'loading' && <Notice busy>{t('fsug.loading')}</Notice>}
      {(subs.kind === 'error' || subs.kind === 'denied') && <LoadError message={subs.message} retry={load} />}
      {subs.kind === 'ready' && (subs.value.length === 0 ? <p className="muted">{t('fsug.sub.empty')}</p>
        : <ul className="fsugAliasList">{subs.value.map((s) => <li key={s.id} className={s.archived ? 'archived' : ''}><span>{categoryLabel(s.parent)}</span><span aria-hidden="true">:</span><strong dir="auto">{s.name}</strong>
          {s.archived && <StatusChip>{t('fsug.sub.archived')}</StatusChip>}
          <button type="button" className="secondary" disabled={busy} onClick={() => void run(() => rx.saveSubcategory({ parent: s.parent, name: s.name, archived: !s.archived }, s.id), t('fsug.sub.saved'))}>
            {t(s.archived ? 'fsug.sub.unarchive' : 'fsug.sub.archive')}</button></li>)}</ul>)}
    </FinCard>
    {removing && <ConfirmDialog title={t('fsug.alias.remove')} confirmLabel={t('fsug.alias.remove')} danger busy={busy}
      onCancel={() => setRemoving(null)} onConfirm={() => void run(() => rx.deleteAlias(removing.id), t('fsug.alias.removed')).then(() => setRemoving(null))}>
      <p dir="auto">{removing.alias}: {removing.merchant}</p>
    </ConfirmDialog>}
  </div>;
}

// -- automation ------------------------------------------------------------------------------------------------

function AutomationView() {
  const [state, setState] = useState<Load<FinanceAutomation>>({ kind: 'loading' });
  const [policy, setPolicy] = useState<FinanceAutomationPolicy | null>(null);
  const [busy, setBusy] = useState(false), [flash, setFlash] = useFlash();
  const ids = { min: useId(), notifyMin: useId() };
  const load = useCallback(() => {
    rx.getAutomation().then((value) => { setState({ kind: 'ready', value }); setPolicy(value.policy); }).catch((e) => setState(failed(e)));
  }, []);
  useEffect(() => { load(); }, [load]);
  if (state.kind === 'loading' || (state.kind === 'ready' && !policy)) return <Notice busy>{t('fsug.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={state.message} retry={load} />;
  const p = policy!;
  const set = (patch: Partial<FinanceAutomationPolicy>) => setPolicy({ ...p, ...patch });
  const toggleSource = (source: 'rule' | 'history', on: boolean) => set({ sources: on ? [...new Set([...p.sources, source])] : p.sources.filter((s) => s !== source) });
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setFlash(null);
    try { const saved = await rx.putAutomation(p); setPolicy(saved.policy); setFlash({ text: t('fsug.auto.saved') }); } catch (e) { setFlash({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  return <div className="fsugAutomation">
    <FinCard title={t('fsug.auto.title')} icon={<Bot />} subtitle={t('fsug.auto.subtitle')}>
      <form onSubmit={save}>
        <label className="fsugCheck"><input type="checkbox" checked={p.auto_classify} onChange={(e) => set({ auto_classify: e.target.checked })} />{t('fsug.auto.classify')}</label>
        <fieldset className="fsugFieldset" disabled={!p.auto_classify}>
          <div className="field"><label htmlFor={ids.min}>{t('fsug.auto.minConfidence')}</label>
            <input id={ids.min} type="number" min={80} max={100} step={1} value={Math.round(Number(p.min_confidence) * 100)}
              onChange={(e) => set({ min_confidence: (Math.min(100, Math.max(80, Number(e.target.value) || 80)) / 100).toFixed(2) })} /></div>
          <legend>{t('fsug.auto.sources')}</legend>
          <label className="fsugCheck"><input type="checkbox" checked={p.sources.includes('history')} onChange={(e) => toggleSource('history', e.target.checked)} />{t('fsug.auto.source.history')}</label>
          <label className="fsugCheck"><input type="checkbox" checked={p.sources.includes('rule')} onChange={(e) => toggleSource('rule', e.target.checked)} />{t('fsug.auto.source.rule')}</label>
        </fieldset>
        <label className="fsugCheck"><input type="checkbox" checked={p.notify_unresolved} onChange={(e) => set({ notify_unresolved: e.target.checked })} />{t('fsug.auto.notify')}</label>
        <div className="field"><label htmlFor={ids.notifyMin}>{t('fsug.auto.notifyMin')}</label>
          <input id={ids.notifyMin} type="number" min={1} max={500} disabled={!p.notify_unresolved} value={p.notify_min_items}
            onChange={(e) => set({ notify_min_items: Math.min(500, Math.max(1, Number(e.target.value) || 1)) })} /></div>
        <FlashLine flash={flash} />
        <div className="actions"><button type="submit" disabled={busy}>{busy ? t('finance.saving') : t('fsug.auto.save')}</button></div>
      </form>
    </FinCard>
    <FinCard title={t('fsug.auto.never')} icon={<X />}>
      <p className="muted">{t('fsug.auto.neverText')}</p>
      <ul className="fsugNever">{NEVER_AUTOMATED.map((k) => <li key={k}>{t(`fsug.never.${k}` as MessageKey)}</li>)}</ul>
    </FinCard>
  </div>;
}
