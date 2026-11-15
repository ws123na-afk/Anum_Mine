// Finance, Suggestions, Categories (docs/finance.md#category-ontology, #learned-merchants): the groups, categories and
// subcategories (built in and the owner's own) with this month's spending per group from the API's shared rule, adding
// the owner's subcategories, and the learned merchants with switch off and forget. Nothing here changes a recorded row.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { Plus, Search, Store, Tags, Trash2 } from 'lucide-react';
import type { FinanceCategory, FinanceCategoryMapping, FinanceCategoryMappingPage, FinanceCategoryNode, FinanceCategoryTree } from '@anum/contracts';
import * as b from './lib/financeBudgetsApi';
import { filterTree, nodeName } from './lib/financeBudgets';
import { saveSubcategory } from './lib/financeRulesApi';
import { categoryLabel, formatAmount, isolate } from './lib/finance';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import './financeSuggestions.css';
import './financeBudgets.css';

const PAGE = 25;

export function CategoriesView() {
  const month = new Date().toISOString().slice(0, 7);
  const [tree, setTree] = useState<Load<FinanceCategoryTree>>({ kind: 'loading' });
  const [q, setQ] = useState('');
  const [adding, setAdding] = useState<FinanceCategoryNode | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const searchId = useId();
  const load = useCallback(async () => {
    try { setTree({ kind: 'ready', value: await b.categoryTree(month) }); } catch (e) { setTree(failed(e)); }
  }, [month]);
  useEffect(() => { void load(); }, [load]);
  const groups = useMemo(() => (tree.kind === 'ready' ? filterTree(tree.value, q) : []), [tree, q]);

  return <div className="fsugPage">
    <FinCard title={t('fcat.title')} subtitle={t('fcat.subtitle')} icon={<Tags />}>
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      {tree.kind === 'loading' && <Notice busy>{t('fcat.loading')}</Notice>}
      {tree.kind === 'denied' && <Denied />}
      {tree.kind === 'error' && <LoadError message={t('fcat.error', { message: tree.message })} retry={() => void load()} />}
      {tree.kind === 'ready' && <>
        <label className="fsugSearch cmSearch" htmlFor={searchId}><Search aria-hidden="true" /><span className="visuallyHidden">{t('fcat.search')}</span>
          <input id={searchId} type="search" value={q} placeholder={t('fcat.search')} onChange={(e) => setQ(e.target.value)} /></label>
        {groups.length === 0 && <p className="muted">{t('fcat.noMatch', { query: q })}</p>}
        <ul className="fcatGroups">{groups.map((g) => {
          const totals = Object.entries(tree.value.group_totals[g.id] ?? {});
          return <li key={g.id} className="fcatGroup">
            <div className="fsugCardHead"><h3>{nodeName(g)}</h3>
              {totals.length > 0 && <small className="muted">{t('fcat.spentThisMonth')}: {totals.map(([cur, amount]) => isolate(formatAmount(amount, cur))).join(' · ')}</small>}</div>
            {g.id === 'debt' && <p className="muted">{t('fcat.debtTypes')}</p>}
            {g.categories.map((c) => <div key={c.id} className="fcatCategory">
              <div className="fcatCategoryHead"><strong>{nodeName(c)}</strong>
                {c.essential && <StatusChip tone="info">{t('fcat.essential')}</StatusChip>}
                {!c.spending && <StatusChip>{t('fcat.notSpending')}</StatusChip>}
                <button type="button" className="secondary" onClick={() => setAdding(c)} aria-label={t('fcat.addTo', { category: nodeName(c) })}><Plus aria-hidden="true" />{t('fcat.add')}</button></div>
              {c.subcategories.length > 0 && <ul className="fsugChips">{c.subcategories.map((s) => <li key={s.id}>
                <span className={`fsugChip ${s.built_in ? '' : 'mine'}`} dir="auto">{nodeName(s)}
                  <span className="visuallyHidden"> ({s.built_in ? t('fcat.builtIn') : t('fcat.yours')}{s.archived ? `, ${t('fcat.archived')}` : ''})</span></span></li>)}</ul>}
            </div>)}
          </li>;
        })}</ul>
      </>}
    </FinCard>
    <LearnedMerchants />
    {adding && <AddSubcategory category={adding.id} label={nodeName(adding)} onClose={(name) => { setAdding(null); if (name) { setFlash({ text: t('fcat.added', { name }) }); void load(); } }} />}
  </div>;
}

function AddSubcategory({ category, label, onClose }: { category: FinanceCategory; label: string; onClose: (added: string | null) => void }) {
  const [name, setName] = useState(''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const nameId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    const clean = name.trim();
    if (!clean) { setError(t('fbud.form.needName')); return; }
    setBusy(true); setError(null);
    try { await saveSubcategory({ parent: category, name: clean, archived: false }); onClose(clean); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('fcat.addTo', { category: label })} className="formDialog" onClose={() => onClose(null)}>
    <form onSubmit={(e) => void submit(e)} noValidate>
      <label className="field" htmlFor={nameId}><span>{t('fcat.name')}</span>
        <input id={nameId} value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-invalid={Boolean(error)} autoFocus /></label>
      {error && <Notice error>{error}</Notice>}
      <div className="dialogActions"><button type="button" className="secondary" onClick={() => onClose(null)}>{t('common.cancel')}</button>
        <button type="submit" disabled={busy}>{t('fcat.add')}</button></div>
    </form>
  </Modal>;
}

function LearnedMerchants() {
  const [page, setPage] = useState<Load<FinanceCategoryMappingPage>>({ kind: 'loading' });
  const [q, setQ] = useState(''), [limit, setLimit] = useState(PAGE);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [forgetting, setForgetting] = useState<FinanceCategoryMapping | null>(null), [busy, setBusy] = useState(false);
  const searchId = useId();
  const load = useCallback(async () => {
    try { setPage({ kind: 'ready', value: await b.listMappings({ q: q.trim() || undefined, limit, offset: 0 }) }); } catch (e) { setPage(failed(e)); }
  }, [q, limit]);
  useEffect(() => { void load(); }, [load]);
  async function toggle(m: FinanceCategoryMapping) {
    try { await b.switchMapping(m, !m.enabled); await load(); } catch (e) { setFlash({ error: true, text: describeError(e) }); }
  }
  async function forget() {
    if (!forgetting) return;
    setBusy(true);
    try { await b.deleteMapping(forgetting); setFlash({ text: t('fcat.learned.removed') }); await load(); }
    catch (e) { setFlash({ error: true, text: describeError(e) }); } finally { setBusy(false); setForgetting(null); }
  }
  return <FinCard title={t('fcat.learned.title')} subtitle={t('fcat.learned.subtitle')} icon={<Store />}>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <label className="fsugSearch cmSearch" htmlFor={searchId}><Search aria-hidden="true" /><span className="visuallyHidden">{t('fcat.learned.search')}</span>
      <input id={searchId} type="search" value={q} placeholder={t('fcat.learned.search')} onChange={(e) => setQ(e.target.value)} /></label>
    {page.kind === 'loading' && <Notice busy>{t('fcat.loading')}</Notice>}
    {page.kind === 'denied' && <Denied />}
    {page.kind === 'error' && <LoadError message={page.message} retry={() => void load()} />}
    {page.kind === 'ready' && (page.value.items.length === 0
      ? <Empty icon={<Store aria-hidden="true" />} title={t('fcat.learned.empty')} text={t('fcat.learned.emptyText')} />
      : <><ul className="cmList frecList">{page.value.items.map((m) => <li key={`${m.direction}:${m.merchant}`}>
        <div><strong dir="auto">{m.merchant}</strong>
          <small>{categoryLabel(m.category)} · {t(`fcat.learned.money.${m.direction}` as MessageKey)} · {t('fcat.learned.times', { count: m.confirmations })}</small>
          {m.example && <small className="muted" dir="auto">{m.example}</small>}</div>
        <div className="finItemEnd"><StatusChip tone={m.enabled ? 'ok' : 'muted'}>{m.enabled ? t('fcat.learned.on') : t('fcat.learned.off')}</StatusChip>
          <button type="button" className="secondary" onClick={() => void toggle(m)}>{m.enabled ? t('fcat.learned.switchOff') : t('fcat.learned.switchOn')}</button>
          <button type="button" className="secondary danger" onClick={() => setForgetting(m)}><Trash2 aria-hidden="true" />{t('fcat.learned.remove')}</button></div>
      </li>)}</ul>
        {page.value.total > page.value.items.length && <button type="button" className="secondary" onClick={() => setLimit((n) => n + PAGE)}>{t('fcat.learned.more')}</button>}</>)}
    {forgetting && <ConfirmDialog title={`${t('fcat.learned.remove')}: ${forgetting.merchant}`} confirmLabel={t('fcat.learned.remove')} danger busy={busy}
      onConfirm={() => void forget()} onCancel={() => setForgetting(null)}>{t('fcat.learned.forgetText')}</ConfirmDialog>}
  </FinCard>;
}
