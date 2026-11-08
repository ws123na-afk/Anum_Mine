// Business books, second release (docs/business-books.md#web): the consolidated books of the businesses an owner owns
// (a column per business, the eliminations and the total, in a reporting currency), recurring bills (the schedule, the
// bills coming, pause, edit for future bills, end) and automatic exchange rates from the ECB (an opt-in per business).
// The API is the authority: it converts only with each business's own dated rates and never adds what it cannot convert.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Download, Plus, RefreshCw, Search } from 'lucide-react';
import type {
  BooksBasis, BooksFrequency, BooksGroup, BooksGroupRow, BooksRate, BooksRateFeed, BooksSection, BooksSetup, BooksTemplate, BooksTemplateStatus,
  BooksUpcomingBill, BooksVendor,
} from '@anum/contracts';
import { BOOKS_FREQUENCIES, BOOKS_SECTIONS } from '@anum/contracts';
import * as api from './lib/businessBooksApi';
import {
  BOOKS_PAGE, BOOK_CURRENCIES, PERIOD_PRESETS, basisLabel, emptyTemplate, frequencyHasInterval, frequencyLabel, groupNames, groupRowIsResult, groupRowLabel,
  groupRowsShown, isNegative, lineLabel, money, presetPeriod, sectionLabel, templateBody, templateDraftFrom, templateProblem, templateStatusTone, todayIso,
  validAmount, type PeriodPreset, type TemplateDraft,
} from './lib/businessBooks';
import { describeError, isPermissionDenied } from './lib/errors';
import { minorDigits } from './lib/finance';
import { t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { Badge, Denied, Empty, Failed, Note, Pager, day, failure } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';
import { Actions, BillDrawer, CentreSelect, CurrencySelect, useProjects } from './BusinessBooksForms';

const label = (key: string) => t(key as MessageKey);
const dayText = (value: string | null | undefined) => (value ? day(value) : '-');
const stamp = (value: string | null | undefined) => (value ? day(value.slice(0, 10)) : '-');

// ---- consolidated books ---------------------------------------------------------------------------------------------

type GroupLoad = { state: 'loading' } | { state: 'ready'; value: BooksGroup } | { state: 'denied'; message: string } | { state: 'error'; message: string };

export function GroupSection({ setup }: { setup: BooksSetup }) {
  const settings = setup.settings!;
  const [period, setPeriod] = useState(() => ({ from: setup.default_period?.from ?? '', to: setup.default_period?.to ?? '' }));
  const [preset, setPreset] = useState<PeriodPreset | ''>('this_year');
  const [basis, setBasis] = useState<BooksBasis>(settings.basis);
  const [currency, setCurrency] = useState(settings.base_currency);
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [load, setLoad] = useState<GroupLoad>({ state: 'loading' });
  const [exporting, setExporting] = useState<{ text: string; error?: boolean } | null>(null);
  const ids = { preset: useId(), from: useId(), to: useId(), basis: useId(), currency: useId() };
  const params: api.GroupQuery = { ...period, basis, currency, workspaces: chosen ?? undefined };
  const fetchGroup = useCallback(async () => {
    setLoad({ state: 'loading' });
    try { setLoad({ state: 'ready', value: await api.getGroup(params) }); }
    catch (error) { setLoad(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) }); }
  }, [period.from, period.to, basis, currency, (chosen ?? []).join(',')]);
  useEffect(() => { void fetchGroup(); }, [fetchGroup]);
  const choose = (value: PeriodPreset | '') => { setPreset(value); if (value) setPeriod(presetPeriod(value, new Date(), settings.fiscal_year_start_month)); };
  const group = load.state === 'ready' ? load.value : null;
  const available = group?.available ?? [];
  const included = new Set(chosen ?? (group?.businesses ?? []).map((item) => item.workspace_id));
  const limit = group?.limit ?? 20;
  const toggle = (workspace: string) => {
    const next = new Set(included);
    if (next.has(workspace)) next.delete(workspace); else next.add(workspace);
    setChosen(available.map((item) => item.workspace_id).filter((item) => next.has(item)));
  };
  async function exportCsv() {
    setExporting(null);
    try { await api.exportGroup(params); setExporting({ text: t('books.exported') }); } catch (error) { setExporting({ text: failure(error, t('books.export')), error: true }); }
  }
  const currencies = Array.from(new Set([...BOOK_CURRENCIES, ...(group?.businesses ?? []).map((item) => item.base_currency)]));
  return <>
    <p className="muted bzHint">{t('books.group.help')}</p>
    <div className="bzToolbar bkPeriod">
      <label className="bzCompact" htmlFor={ids.preset}><span>{t('books.period')}</span>
        <select id={ids.preset} value={preset} onChange={(event) => choose(event.target.value as PeriodPreset | '')}>
          <option value="">{t('books.preset.custom')}</option>{PERIOD_PRESETS.map((item) => <option key={item} value={item}>{label(`books.preset.${item}`)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.from}><span>{t('books.from')}</span><input id={ids.from} type="date" value={period.from} max={period.to || undefined}
        onChange={(event) => { setPreset(''); setPeriod({ ...period, from: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.to}><span>{t('books.to')}</span><input id={ids.to} type="date" value={period.to} min={period.from || undefined}
        onChange={(event) => { setPreset(''); setPeriod({ ...period, to: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.basis}><span>{t('books.basis')}</span>
        <select id={ids.basis} value={basis} onChange={(event) => setBasis(event.target.value as BooksBasis)}>
          {(['accrual', 'cash'] as const).map((item) => <option key={item} value={item}>{basisLabel(item)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.currency}><span>{t('books.group.currency')}</span>
        <select id={ids.currency} value={currency} onChange={(event) => setCurrency(event.target.value)}>{currencies.map((code) => <option key={code} value={code}>{code}</option>)}</select></label>
      <div className="bzToolbarEnd"><button type="button" className="secondary" disabled={!group?.enough} onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('books.export')}</button></div>
    </div>
    {available.length > 1 && <fieldset className="bkChoice bkBusinesses"><legend>{t('books.group.included')}</legend>
      {available.map((item) => <label key={item.workspace_id} className="bzCheck"><input type="checkbox" checked={included.has(item.workspace_id)}
        disabled={included.has(item.workspace_id) ? included.size <= 2 : included.size >= limit} onChange={() => toggle(item.workspace_id)} /><span dir="auto">{item.name || item.workspace_id}</span></label>)}
    </fieldset>}
    {exporting && <Note error={exporting.error}>{exporting.text}</Note>}
    {load.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {load.state === 'denied' && <Denied text={t('books.group.denied', { message: load.message })} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void fetchGroup()} />}
    {group && !group.enough && <Empty title={t('books.group.notEnoughTitle')} text={group.skipped.length > 0 ? t('books.group.notEnoughSetup') : t('books.group.notEnoughOwner')}
      action={<button type="button" className="secondary" onClick={() => void fetchGroup()}>{t('books.group.checkAgain')}</button>} />}
    {group && (group.excluded ?? []).length > 0 && <Note>{t('books.group.excluded', { limit, names: group.excluded.map((item) => item.name || item.workspace_id).join(', ') })}</Note>}
    {group && group.skipped.length > 0 && <Note>{t('books.group.skipped', { names: group.skipped.map((item) => item.name || item.workspace_id).join(', ') })}</Note>}
    {group && group.enough && <GroupBody group={group} />}
  </>;
}

function GroupTable({ group, section, title, id }: { group: BooksGroup; section: 'pnl' | 'balances' | 'cash_flow'; title: string; id: string }) {
  const names = groupNames(group);
  const rows = groupRowsShown(group[section]);
  const code = group.currency!;
  return <section className="surface bzCard bkWide" aria-labelledby={id}><h2 id={id}>{title}</h2>
    {section === 'balances' && <p className="muted bzHint">{t('books.group.balancesHelp', { day: dayText(group.as_of) })}</p>}
    {section === 'cash_flow' && <p className="muted bzHint">{t('books.group.cashHelp')}</p>}
    {rows.length === 0 ? <p className="muted">{t('books.group.nothing')}</p> : <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}>
      <table className={`dataTable bkGroup bkGroup-${section}`}><caption className="visuallyHidden">{title}</caption>
        <thead><tr><th scope="col">{t('books.col.line')}</th>{group.businesses.map((item) => <th key={item.workspace_id} scope="col" className="num"><span dir="auto">{names.get(item.workspace_id)}</span></th>)}
          <th scope="col" className="num">{t('books.group.eliminations')}</th><th scope="col" className="num">{t('books.group.total', { currency: code })}</th></tr></thead>
        <tbody>{rows.map((row: BooksGroupRow) => <tr key={row.key} className={groupRowIsResult(row.key) ? 'bk-result' : 'bk-line'}><th scope="row">{groupRowLabel(section, row.key)}</th>
          {group.businesses.map((item) => { const value = row.businesses[item.workspace_id] ?? '0'; return <td key={item.workspace_id} className={`num ${isNegative(value) ? 'bkNeg' : ''}`}>{money(value, code)}</td>; })}
          <td className={`num ${isNegative(row.eliminations) ? 'bkNeg' : ''}`}>{Number(row.eliminations) === 0 ? '-' : money(row.eliminations, code)}</td>
          <td className={`num bkTotalCell ${isNegative(row.total) ? 'bkNeg' : ''}`}>{money(row.total, code)}</td></tr>)}</tbody></table></div>}
  </section>;
}

function GroupBody({ group }: { group: BooksGroup }) {
  const names = groupNames(group);
  const missing = [...(group.not_converted?.pnl ?? []).map((item) => ({ ...item, section: 'pnl' as const })),
    ...(group.not_converted?.balances ?? []).map((item) => ({ ...item, section: 'balances' as const })),
    ...(group.not_converted?.cash ?? []).map((item) => ({ ...item, section: 'cash_flow' as const }))].filter((item) => item.workspace_id !== null);
  const net = group.pnl?.find((row) => row.key === 'net_profit');
  return <div className="bzOverview bkGrid">
    <section className="surface bzCard bkWide" aria-labelledby="bk-group-sum"><h2 id="bk-group-sum">{t('books.group.title', { currency: group.currency ?? '' })}</h2>
      <p className="muted bzHint">{t(group.period?.basis === 'cash' ? 'books.pnl.cashHelp' : 'books.pnl.accrualHelp', { from: dayText(group.period?.from), to: dayText(group.period?.to) })}</p>
      <dl className="bzFacts">
        <div><dt>{t('books.group.netTotal')}</dt><dd><strong className={isNegative(net?.total) ? 'bkNeg' : ''}>{money(net?.total ?? '0', group.currency ?? '')}</strong></dd></div>
        <div><dt>{t('books.group.businesses')}</dt><dd dir="auto">{group.businesses.map((item) => `${item.name || item.workspace_id} (${item.base_currency})`).join(', ')}</dd></div>
      </dl>
      <p className="bzHint">{group.eliminated ? t('books.group.eliminatedHelp') : t('books.group.noEliminations')}</p>
      {!group.complete && <div className="bzGaps" role="note"><strong>{t('books.notConverted.title')}</strong>
        <ul>{missing.map((item) => <li key={`${item.section}-${item.workspace_id}-${item.row}-${item.currency}`}>{t('books.group.missingLine', { name: names.get(item.workspace_id!) ?? '', row: groupRowLabel(item.section, item.row), amount: money(item.amount, item.currency) })}</li>)}</ul>
        <p className="bzHint">{t('books.group.notConvertedHelp')}</p></div>}
    </section>
    <GroupTable group={group} section="pnl" title={t('books.group.pnlTitle')} id="bk-group-pnl" />
    <GroupTable group={group} section="balances" title={t('books.group.balancesTitle')} id="bk-group-bal" />
    <GroupTable group={group} section="cash_flow" title={t('books.group.cashTitle')} id="bk-group-cash" />
    <section className="surface bzCard bkWide" aria-labelledby="bk-group-rates"><h2 id="bk-group-rates">{t('books.rates.used')}</h2>
      {(group.conversions ?? []).length === 0 ? <p className="muted">{t('books.rates.noneUsed', { currency: group.currency ?? '' })}</p> : <ul className="bkList">
        {(group.conversions ?? []).map((row) => <li key={`${row.workspace_id}-${row.from_currency}-${row.as_of}-${row.rate}`}><span>{t('books.group.rateLine', { name: names.get(row.workspace_id) ?? row.workspace_id, line: t('books.rates.line', { from: row.from_currency, rate: row.rate, to: row.to_currency }) })}</span>
          <small dir="auto">{t('books.rates.source', { day: dayText(row.as_of), source: row.source, count: row.figures })}</small></li>)}</ul>}
    </section>
  </div>;
}

// ---- recurring bills ----------------------------------------------------------------------------------------------------

const TEMPLATE_FILTERS: BooksTemplateStatus[] = ['active', 'paused', 'ended'];

export function Upcoming({ items, limit = 8 }: { items: BooksUpcomingBill[]; limit?: number }) {
  if (items.length === 0) return <p className="muted">{t('books.rec.noUpcoming')}</p>;
  return <><ul className="bkList">{items.slice(0, limit).map((row) => <li key={`${row.template_id}-${row.bill_date}`}><span><span dir="auto">{row.vendor_name}</span> <small dir="auto">{row.description}</small></span>
    <span>{money(row.total, row.currency)} <small>{t('books.rec.on', { day: dayText(row.bill_date) })}</small>{row.create_as === 'draft' && <> <Badge tone="neutral">{t('books.payState.draft')}</Badge></>}</span></li>)}</ul>
    {items.length > limit && <p className="muted bzHint">{t('books.rec.moreUpcoming', { count: items.length - limit })}</p>}</>;
}

export function Recurring({ setup }: { setup: BooksSetup }) {
  const [q, setQ] = useState(''), [search, setSearch] = useState(''), [status, setStatus] = useState<BooksTemplateStatus | ''>('');
  const [tick, setTick] = useState(0), [creating, setCreating] = useState(false), [openId, setOpenId] = useState<string | null>(null);
  const [upcoming, setUpcoming] = useState<BooksUpcomingBill[] | null>(null);
  const list = usePaged(async (offset) => { const page = await api.listTemplates(status, search, offset, BOOKS_PAGE); setUpcoming(page.upcoming); return page; }, [search, status, tick]);
  const statusId = useId(), searchId = useId();
  const filtered = Boolean(search || status);
  const write = setup.permissions.write;
  return <>
    <p className="muted bzHint">{t('books.rec.help')}</p>
    <div className="bzToolbar">
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setSearch(q.trim()); }}>
        <label className="visuallyHidden" htmlFor={searchId}>{t('books.rec.search')}</label>
        <input id={searchId} type="search" value={q} placeholder={t('books.rec.search')} onChange={(event) => { setQ(event.target.value); if (!event.target.value) setSearch(''); }} />
        <button type="submit" className="secondary" disabled={q.trim() === search}><Search aria-hidden="true" />{t('biz.find')}</button>
      </form>
      <label className="bzCompact" htmlFor={statusId}><span>{t('books.rec.filter')}</span>
        <select id={statusId} value={status} onChange={(event) => setStatus(event.target.value as BooksTemplateStatus | '')}>
          <option value="">{t('books.rec.all')}</option>{TEMPLATE_FILTERS.map((item) => <option key={item} value={item}>{label(`books.rec.status.${item}`)}</option>)}</select></label>
      <div className="bzToolbarEnd">{write && <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('books.rec.new')}</button>}</div>
    </div>
    {!write && <Note>{t('books.readOnly')}</Note>}
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setQ(''); setSearch(''); setStatus(''); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('books.rec.emptyTitle')} text={write ? t('books.rec.emptyText') : t('books.readOnly')}
      action={write ? <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('books.rec.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <div className="bzOverview bkGrid">
      <section className="surface bzCard bkWide" aria-labelledby="bk-rec-list"><h2 id="bk-rec-list" className="visuallyHidden">{t('books.tab.recurring')}</h2>
        <p className="bzCount" aria-live="polite">{t('books.rec.count', { count: list.total })}</p>
        <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('books.tab.recurring')}</caption>
          <thead><tr><th scope="col">{t('books.col.bill')}</th><th scope="col">{t('books.rec.schedule')}</th><th scope="col" className="num">{t('books.col.total')}</th><th scope="col" className="bzHideMd">{t('books.rec.next')}</th></tr></thead>
          <tbody>{list.items.map((row: BooksTemplate) => <tr key={row.id} className={row.id === openId ? 'selected' : undefined}>
            <th scope="row"><button type="button" className="linkButton" onClick={() => setOpenId(row.id)}><span dir="auto">{row.vendor_name}</span></button><small dir="auto">{row.description}</small></th>
            <td>{frequencyLabel(row.frequency, row.interval)} <Badge tone={templateStatusTone(row.status)}>{label(`books.rec.status.${row.status}`)}</Badge></td>
            <td className="num">{money(row.total, row.currency)}<small>{t('books.recurring.perMonth', { amount: money(row.per_month, row.currency) })}</small></td>
            <td className="bzHideMd nowrap">{row.status === 'active' ? dayText(row.next_date) : '-'}</td></tr>)}</tbody></table></div>
        <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
      </section>
      <section className="surface bzCard bkWide" aria-labelledby="bk-rec-upcoming"><h2 id="bk-rec-upcoming">{t('books.rec.upcomingTitle')}</h2>
        <Upcoming items={upcoming ?? []} limit={12} /></section>
    </div>}
    {creating && <TemplateDialog setup={setup} onClose={() => setCreating(false)} onSaved={(item) => { setCreating(false); setTick((n) => n + 1); setOpenId(item.id); }} />}
    {openId && <TemplateDrawer setup={setup} templateId={openId} onClose={() => setOpenId(null)} onChanged={() => setTick((n) => n + 1)} />}
  </>;
}

function TemplateDialog({ setup, template, onClose, onSaved }: { setup: BooksSetup; template?: BooksTemplate; onClose: () => void; onSaved: (item: BooksTemplate) => void }) {
  const base = setup.settings!.base_currency;
  const [draft, setDraft] = useState<TemplateDraft>(() => (template ? templateDraftFrom(template) : emptyTemplate(todayIso(), base)));
  const [vendors, setVendors] = useState<BooksVendor[] | null>(null), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const projects = useProjects();
  const ids = { vendor: useId(), ref: useId(), description: useId(), currency: useId(), net: useId(), tax: useId(), category: useId(), section: useId(), centre: useId(),
    project: useId(), life: useId(), schedule: useId(), frequency: useId(), interval: useId(), start: useId(), due: useId(), end: useId(), count: useId() };
  useEffect(() => {
    api.listVendors('', false, 0, 200).then((page) => {
      const own = template && !page.items.some((item) => item.id === template.vendor_id) ? [{ id: template.vendor_id, name: template.vendor_name } as BooksVendor] : [];
      setVendors([...own, ...page.items]);
    }, (error) => { setVendors([]); setProblem(describeError(error)); });
  }, []);
  const set = (patch: Partial<TemplateDraft>) => setDraft((current) => ({ ...current, ...patch }));
  const pickVendor = (id: string) => {
    const vendor = vendors?.find((item) => item.id === id);
    set({ vendor_id: id, ...(vendor && !draft.category && vendor.default_category ? { category: vendor.default_category } : {}),
      ...(vendor && !draft.section && vendor.default_section ? { section: vendor.default_section } : {}) });
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    const issue = templateProblem(draft);
    if (issue) { setProblem(t(issue)); return; }
    setBusy(true); setProblem(null);
    try { onSaved(template ? await api.updateTemplate(template.id, { ...templateBody(draft), version: template.version }) : await api.createTemplate(templateBody(draft))); }
    catch (error) { setProblem(failure(error, t('books.rec.title'))); setBusy(false); }
  }
  const total = validAmount(draft.subtotal, true) && validAmount(draft.tax || '0', true) ? (Number(draft.subtotal) + Number(draft.tax || '0')).toFixed(minorDigits(draft.currency)) : null;
  const title = template ? t('books.rec.edit') : t('books.rec.new');
  return <Modal title={title} className="formDialog bkWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={title}>
      {template && <p className="muted bzHint">{t('books.rec.editHelp')}</p>}
      {vendors !== null && vendors.length === 0 && <Note>{t('books.bills.noVendors')}</Note>}
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.vendor}><span>{t('books.col.vendor')}</span><select id={ids.vendor} value={draft.vendor_id} onChange={(event) => pickVendor(event.target.value)} data-autofocus>
          <option value="">{vendors === null ? t('books.reading') : t('books.bills.pickVendor')}</option>{(vendors ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field" htmlFor={ids.ref}><span>{t('books.bills.reference')}</span><input id={ids.ref} dir="auto" value={draft.reference} maxLength={80} onChange={(event) => set({ reference: event.target.value })} /></label>
      </div>
      <label className="field" htmlFor={ids.description}><span>{t('books.bills.description')}</span><input id={ids.description} dir="auto" value={draft.description} maxLength={300} onChange={(event) => set({ description: event.target.value })} /></label>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.currency}><span>{t('books.col.currency')}</span><CurrencySelect id={ids.currency} value={draft.currency} onChange={(value) => set({ currency: value })} /></label>
        <label className="field" htmlFor={ids.net}><span>{t('books.bills.net')}</span><input id={ids.net} inputMode="decimal" value={draft.subtotal} onChange={(event) => set({ subtotal: event.target.value })} /></label>
        <label className="field" htmlFor={ids.tax}><span>{t('books.bills.tax')}</span><input id={ids.tax} inputMode="decimal" value={draft.tax} onChange={(event) => set({ tax: event.target.value })} /></label>
        <p className="bkTotal" aria-live="polite">{t('books.bills.total', { amount: total ? money(total, draft.currency) : '-' })}</p>
      </div>
      <div className="bkFieldset" role="group" aria-labelledby={ids.schedule}><h3 id={ids.schedule} className="bkSub">{t('books.rec.scheduleTitle')}</h3>
        <div className="bzGrid2">
          <label className="field" htmlFor={ids.frequency}><span>{t('books.rec.repeats')}</span><select id={ids.frequency} value={draft.frequency} onChange={(event) => set({ frequency: event.target.value as BooksFrequency })}>
            {BOOKS_FREQUENCIES.map((item) => <option key={item} value={item}>{label(`books.freq.pick.${item}`)}</option>)}</select></label>
          {frequencyHasInterval(draft.frequency) && <label className="field" htmlFor={ids.interval}><span>{label(`books.rec.every.${draft.frequency}`)}</span>
            <input id={ids.interval} inputMode="numeric" value={draft.interval} onChange={(event) => set({ interval: event.target.value })} /></label>}
          <label className="field" htmlFor={ids.start}><span>{t('books.rec.start')}</span><input id={ids.start} type="date" value={draft.start_date} onChange={(event) => set({ start_date: event.target.value })} /></label>
          <label className="field" htmlFor={ids.due}><span>{t('books.rec.dueDays')}</span><input id={ids.due} inputMode="numeric" value={draft.due_days} onChange={(event) => set({ due_days: event.target.value })} /></label>
        </div>
        <div className="bkChoice" role="radiogroup" aria-label={t('books.rec.ends')}>
          <span className="bkChoiceLabel">{t('books.rec.ends')}</span>
          {(['never', 'on', 'after'] as const).map((item) => <label key={item} className="bzCheck"><input type="radio" name="ends" checked={draft.ends === item} onChange={() => set({ ends: item })} />{label(`books.rec.ends.${item}`)}</label>)}
        </div>
        {draft.ends === 'on' && <label className="field" htmlFor={ids.end}><span>{t('books.rec.endDate')}</span><input id={ids.end} type="date" value={draft.end_date} min={draft.start_date} onChange={(event) => set({ end_date: event.target.value })} /></label>}
        {draft.ends === 'after' && <label className="field" htmlFor={ids.count}><span>{t('books.rec.count.label')}</span><input id={ids.count} inputMode="numeric" value={draft.max_count} onChange={(event) => set({ max_count: event.target.value })} /></label>}
        <div className="bkChoice" role="radiogroup" aria-label={t('books.rec.createAs')}>
          <span className="bkChoiceLabel">{t('books.rec.createAs')}</span>
          {(['open', 'draft'] as const).map((item) => <label key={item} className="bzCheck"><input type="radio" name="create_as" checked={draft.create_as === item} onChange={() => set({ create_as: item })} />{label(`books.rec.createAs.${item}`)}</label>)}
        </div>
      </div>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.category}><span>{t('books.col.category')}</span><input id={ids.category} value={draft.category} placeholder="office_rent" onChange={(event) => set({ category: event.target.value })} /></label>
        <label className="field" htmlFor={ids.section}><span>{t('books.col.countsAs')}</span><select id={ids.section} value={draft.section} onChange={(event) => set({ section: event.target.value as BooksSection | '' })}>
          <option value="">{t('books.bills.sectionDefault')}</option>{BOOKS_SECTIONS.map((item) => <option key={item} value={item}>{sectionLabel(item)}</option>)}</select></label>
        <label className="field" htmlFor={ids.centre}><span>{t('books.col.costCentre')}</span><CentreSelect id={ids.centre} setup={setup} value={draft.cost_centre_id} onChange={(value) => set({ cost_centre_id: value })} /></label>
        <label className="field" htmlFor={ids.project}><span>{t('books.col.project')}</span><select id={ids.project} value={draft.project_id} onChange={(event) => set({ project_id: event.target.value })}>
          <option value="">{t('books.none')}</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {draft.section === 'capital' && <label className="field" htmlFor={ids.life}><span>{t('books.bills.life')}</span><input id={ids.life} inputMode="numeric" value={draft.useful_life_months} onChange={(event) => set({ useful_life_months: event.target.value })} /></label>}
      </div>
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.save')} onCancel={onClose} />
    </form>
  </Modal>;
}

function TemplateDrawer({ setup, templateId, onClose, onChanged }: { setup: BooksSetup; templateId: string; onClose: () => void; onChanged: () => void }) {
  const [item, setItem] = useState<BooksTemplate | null>(null), [error, setError] = useState<string | null>(null), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [mode, setMode] = useState<'edit' | 'end' | null>(null), [busy, setBusy] = useState(false), [billId, setBillId] = useState<string | null>(null);
  const load = useCallback(async () => { setError(null); try { setItem(await api.getTemplate(templateId)); } catch (caught) { setError(describeError(caught)); } }, [templateId]);
  useEffect(() => { void load(); }, [load]);
  async function act(action: 'pause' | 'resume' | 'end') {
    if (!item) return;
    setBusy(true); setMessage(null);
    try { setItem(await api.templateAction(item.id, action, item.version)); setMode(null); setMessage({ text: label(`books.rec.done.${action}`) }); onChanged(); }
    catch (caught) { setMessage({ text: failure(caught, t('books.rec.title')), error: true }); setMode(null); } finally { setBusy(false); }
  }
  return <Drawer title={item ? `${item.vendor_name}, ${item.description}` : t('books.rec.title')} onClose={onClose}>
    {error && <Note error>{error} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {!item && !error && <Note>{t('books.reading')}</Note>}
    {item && <div className="bzDrawer">
      <div className="bzLeadHead"><Badge tone={templateStatusTone(item.status)}>{label(`books.rec.status.${item.status}`)}</Badge><Badge tone="neutral">{frequencyLabel(item.frequency, item.interval)}</Badge>
        {item.create_as === 'draft' && <Badge tone="neutral">{t('books.rec.makesDrafts')}</Badge>}</div>
      <dl className="bzFacts">
        <div><dt>{t('books.col.total')}</dt><dd><strong>{money(item.total, item.currency)}</strong> <small>{t('books.rec.netTax', { net: money(item.subtotal, item.currency), tax: money(item.tax, item.currency) })}</small></dd></div>
        <div><dt>{t('books.recurring.title')}</dt><dd>{t('books.recurring.perMonth', { amount: money(item.per_month, item.currency) })}</dd></div>
        <div><dt>{t('books.rec.start')}</dt><dd>{dayText(item.start_date)}</dd></div>
        <div><dt>{t('books.rec.dueDays')}</dt><dd>{item.due_days}</dd></div>
        <div><dt>{t('books.rec.ends')}</dt><dd>{item.end_date ? t('books.rec.endsOn', { day: dayText(item.end_date) }) : item.max_count ? t('books.rec.endsAfter', { count: item.max_count }) : t('books.rec.ends.never')}</dd></div>
        <div><dt>{t('books.rec.made')}</dt><dd>{item.created_count}</dd></div>
        <div><dt>{t('books.rec.next')}</dt><dd>{item.status === 'active' ? (item.upcoming.length ? item.upcoming.map((value) => dayText(value)).join(', ') : '-') : label(`books.rec.status.${item.status}`)}</dd></div>
        <div><dt>{t('books.col.category')}</dt><dd>{lineLabel(item.category)}, {sectionLabel(item.section)}</dd></div>
        {item.status === 'ended' && <div><dt>{t('books.rec.endedWhy')}</dt><dd>{label(`books.rec.endReason.${item.end_reason || 'owner'}`)}</dd></div>}
      </dl>
      <div className="bzInline">
        {item.can.edit && <button type="button" className="secondary" onClick={() => setMode('edit')}>{t('books.rec.edit')}</button>}
        {item.can.pause && <button type="button" className="secondary" disabled={busy} onClick={() => void act('pause')}>{t('books.rec.pause')}</button>}
        {item.can.resume && <button type="button" disabled={busy} onClick={() => void act('resume')}>{t('books.rec.resume')}</button>}
        {item.can.end && <button type="button" className="secondary danger" onClick={() => setMode('end')}>{t('books.rec.end')}</button>}
      </div>
      {item.status === 'paused' && <p className="muted bzHint">{t('books.rec.pausedHelp')}</p>}
      {message && <Note error={message.error}>{message.text}</Note>}
      <section className="bzBlock" aria-labelledby="bk-rec-bills"><h3 id="bk-rec-bills">{t('books.rec.billsMade')}</h3>
        {(item.bills ?? []).length === 0 ? <p className="muted">{t('books.rec.noBills')}</p> : <ul className="bkList">{(item.bills ?? []).map((bill) => <li key={bill.id}>
          <span><button type="button" className="linkButton" onClick={() => setBillId(bill.id)}>{dayText(bill.bill_date)}</button> <small>{t('books.rec.dueOn', { day: dayText(bill.due_date) })}</small></span>
          <span>{money(bill.total, bill.currency)} <Badge tone={bill.payment_state === 'paid' ? 'success' : bill.payment_state === 'void' || bill.payment_state === 'draft' ? 'neutral' : 'warning'}>{label(`books.payState.${bill.payment_state}`)}</Badge></span></li>)}</ul>}
        {(item.bills_total ?? 0) > (item.bills ?? []).length && <p className="muted bzHint">{t('books.rec.moreBills', { count: (item.bills_total ?? 0) - (item.bills ?? []).length })}</p>}
      </section>
    </div>}
    {item && mode === 'edit' && <TemplateDialog setup={setup} template={item} onClose={() => setMode(null)} onSaved={(next) => { setItem(next); setMode(null); setMessage({ text: t('books.rec.saved') }); onChanged(); }} />}
    {item && mode === 'end' && <ConfirmDialog title={t('books.rec.endTitle')} confirmLabel={t('books.rec.end')} danger busy={busy} onConfirm={() => void act('end')} onCancel={() => setMode(null)}>
      <p>{t('books.rec.endText')}</p></ConfirmDialog>}
    {billId && <BillDrawer setup={setup} billId={billId} onClose={() => setBillId(null)} onChanged={() => { void load(); onChanged(); }} />}
  </Drawer>;
}

// ---- automatic exchange rates ----------------------------------------------------------------------------------------------

export function RateFeedCard({ setup }: { setup: BooksSetup }) {
  const base = setup.settings!.base_currency;
  const [feed, setFeed] = useState<BooksRateFeed | null>(null), [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<{ enabled: boolean; currencies: string[] } | null>(null);
  const [latest, setLatest] = useState<BooksRate[]>([]);
  const [busy, setBusy] = useState<'save' | 'fetch' | null>(null), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try {
      const [value, page] = await Promise.all([api.getRateFeed(), api.listRates(0, 8, 'ecb')]);
      setFeed(value); setForm({ enabled: value.enabled, currencies: value.currencies }); setLatest(page.items);
    } catch (caught) { setError(describeError(caught)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const manage = setup.permissions.manage;
  const options = BOOK_CURRENCIES.filter((code) => code !== base && code !== 'EUR');
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form || !feed) return;
    setBusy('save'); setMessage(null);
    try { const next = await api.saveRateFeed({ enabled: form.enabled, currencies: form.currencies, version: feed.version }); setFeed(next); setForm({ enabled: next.enabled, currencies: next.currencies }); setMessage({ text: t('books.feed.saved') }); }
    catch (caught) { setMessage({ text: failure(caught, t('books.feed.title')), error: true }); } finally { setBusy(null); }
  }
  async function fetchNow() {
    setBusy('fetch'); setMessage(null);
    try {
      const next = await api.refreshRateFeed(); setFeed(next);
      setMessage(next.last_error ? { text: t('books.feed.failed', { error: next.last_error }), error: true } : { text: t('books.feed.fetched', { count: next.added ?? 0 }) });
      setLatest((await api.listRates(0, 8, 'ecb')).items);
    } catch (caught) { setMessage({ text: failure(caught, t('books.feed.fetch')), error: true }); } finally { setBusy(null); }
  }
  return <section className="surface bzCard" aria-labelledby="bk-feed"><h2 id="bk-feed">{t('books.feed.title')}</h2>
    <p className="muted bzHint">{t('books.feed.help', { currency: base })}</p>
    {error && <Note error>{error} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {!feed && !error && <Note>{t('books.reading')}</Note>}
    {feed && <>
      <dl className="bzFacts">
        <div><dt>{t('books.feed.state')}</dt><dd>{feed.enabled ? <Badge tone="success">{t('books.feed.on')}</Badge> : <Badge tone="neutral">{t('books.feed.off')}</Badge>}</dd></div>
        {feed.enabled && <div><dt>{t('books.feed.followed')}</dt><dd>{feed.followed.join(', ') || '-'}</dd></div>}
        <div><dt>{t('books.feed.lastUpdated')}</dt><dd>{feed.last_success_at ? t('books.feed.lastValue', { when: stamp(feed.last_success_at), day: dayText(feed.last_as_of) }) : t('books.feed.never')}</dd></div>
      </dl>
      {feed.last_error && <Note error>{t('books.feed.lastFailed', { when: stamp(feed.last_attempt_at), error: feed.last_error })}</Note>}
      {feed.unavailable.length > 0 && <Note>{t('books.feed.unavailable', { codes: feed.unavailable.join(', ') })}</Note>}
      {latest.length > 0 && <ul className="bkList">{latest.map((row) => <li key={row.id}><span>{t('books.rates.line', { from: row.base, rate: row.rate, to: row.quote })} <Badge tone="neutral">{t('books.rates.ecb')}</Badge></span>
        <small dir="auto">{t('books.rates.recorded', { day: dayText(row.as_of), source: row.source })}</small></li>)}</ul>}
      {manage && form ? <form onSubmit={(event) => void save(event)} aria-label={t('books.feed.title')}>
        <label className="bzCheck"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />{t('books.feed.enable')}</label>
        <fieldset className="bkChoice" disabled={!form.enabled}><legend>{t('books.feed.currencies', { currency: base })}</legend>
          {options.map((code) => <label key={code} className="bzCheck"><input type="checkbox" checked={form.currencies.includes(code)}
            onChange={(event) => setForm({ ...form, currencies: event.target.checked ? [...form.currencies, code] : form.currencies.filter((item) => item !== code) })} />{code}</label>)}
        </fieldset>
        <p className="muted bzHint">{t('books.feed.typedWins')}</p>
        <div className="actions"><button type="submit" disabled={busy !== null}>{busy === 'save' ? t('dialog.working') : t('books.save')}</button>
          {feed.enabled && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void fetchNow()}><RefreshCw aria-hidden="true" />{busy === 'fetch' ? t('dialog.working') : t('books.feed.fetch')}</button>}</div>
      </form> : <p className="muted">{t('books.ownersOnly')}</p>}
      {message && <Note error={message.error}>{message.text}</Note>}
    </>}
  </section>;
}
