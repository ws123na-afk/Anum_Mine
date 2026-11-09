// Finance, Accounts (docs/finance.md#register-and-account-overview): balances per account and where each one's
// data comes from. ANUM has no live bank connection: an account is fed by what the person types, forwards by SMS
// or Apple Pay, or imports from a statement, and its card says which. Card and account numbers are only ever the
// last four digits. Nothing here moves money.
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  AlertTriangle, ArrowUpRight, Archive, BarChart3, CalendarCheck, CreditCard, Download, Eye, EyeOff, FileText, Info, Landmark, ListChecks,
  MessageSquare, Pencil, PenLine, Plus, RefreshCw, Scale, Smartphone, Trash2, Undo2, Upload, Wallet,
} from 'lucide-react';
import type { FinanceAccount, FinanceAccountActivity, FinanceAccountOverview, FinanceAccountsOverview, FinanceAccountSource, FinanceRegisterPage, FinanceSettings } from '@anum/contracts';
import * as api from './lib/financeApi';
import { getAccountActivity, getAccountsOverview, getRegister } from './lib/financeRegisterApi';
import { fromMilli, toMilli } from './lib/financeStatements';
import { NO_REGISTER_FILTERS, accountTab, isAvailable, activityScale, compact, maskedNumber, percentOf, plainAmount, primarySource, type AccountsTab } from './lib/financeRegister';
import { amountTone, formatAmount, isolate, minorDigits, openingNeedsSign, parseAmount } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, getLocale, intlLocale, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import { AccountForm } from './FinanceView';
import { CategoryChip, LedgerScope } from './FinanceTransactionsPage';
import './financeRegister.css';

const SOURCE_ICON: Record<FinanceAccountSource, typeof Wallet> = { sms: MessageSquare, apple_wallet: Smartphone, statement: FileText, manual: PenLine };
const KIND_ICON = { bank: Landmark, card: CreditCard, cash: Wallet, loan: Scale, investment: BarChart3 } as const;
const RANGES = [7, 14, 30, 90] as const;

export function AccountsPage({ accounts, base, onChanged, onOpenTransactions, onImport }: {
  accounts: Load<FinanceAccount[]>; base: string; onChanged: () => void; onOpenTransactions: (accountId: string) => void; onImport: () => void;
}) {
  const [overview, setOverview] = useState<Load<FinanceAccountsOverview>>({ kind: 'loading' });
  const [tab, setTab] = useState<AccountsTab>('cash');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [editing, setEditing] = useState<FinanceAccount | 'new' | null>(null);
  const [opening, setOpening] = useState<FinanceAccount | null>(null);
  const [removing, setRemoving] = useState<FinanceAccount | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const loadOverview = useCallback(async () => {
    try { setOverview({ kind: 'ready', value: await getAccountsOverview() }); } catch (e) { setOverview(failed(e)); }
  }, []);
  useEffect(() => { void loadOverview(); }, [loadOverview, accounts]);
  const changed = () => { onChanged(); void loadOverview(); };

  const list = accounts.kind === 'ready' ? accounts.value : [];
  const info = useMemo(() => new Map((overview.kind === 'ready' ? overview.value.accounts : []).map((o) => [o.account_id, o])), [overview]);
  const grouped = useMemo(() => {
    const out: Record<Exclude<AccountsTab, 'sources'>, FinanceAccount[]> = { cash: [], cards: [], wallets: [], other: [], archived: [] };
    for (const a of list) out[accountTab(a, info.get(a.id))].push(a);
    return out;
  }, [list, info]);
  const shownList = tab === 'sources' ? list.filter((a) => !a.archived) : grouped[tab];
  const selected = shownList.find((a) => a.id === selectedId) ?? shownList[0] ?? null;
  const live = list.filter((a) => !a.archived);
  const currencies = [...new Set(live.map((a) => a.currency))].sort((a, b) => (a === base ? -1 : b === base ? 1 : a.localeCompare(b)));
  const reporting = currency && currencies.includes(currency) ? currency : currencies[0] ?? base;

  async function toggleArchive(account: FinanceAccount) {
    try { await api.updateAccount(account.id, { archived: !account.archived }); setNotice({ text: account.archived ? t('finance.accounts.restored', { name: account.name }) : t('finance.accounts.archived', { name: account.name }) }); changed(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteAccount(removing.id); setNotice({ text: t('finance.accounts.deleted', { name: removing.name }) }); setRemoving(null); changed(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); setRemoving(null); } finally { setBusy(false); }
  }
  async function statement(account: FinanceAccount) {
    try { await api.downloadTransactions({ account_id: account.id }); setNotice({ text: t('facct.action.downloaded', { name: account.name }) }); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }

  const ledgerKind = overview.kind === 'ready' ? overview.value.ledger_kind : 'personal';
  const tabs = [
    { id: 'cash' as const, label: t('facct.tab.cash'), badge: grouped.cash.length || undefined },
    { id: 'cards' as const, label: t('facct.tab.cards'), badge: grouped.cards.length || undefined },
    { id: 'wallets' as const, label: t('facct.tab.wallets'), badge: grouped.wallets.length || undefined },
    ...(grouped.other.length ? [{ id: 'other' as const, label: t('facct.tab.other'), badge: grouped.other.length }] : []),
    { id: 'sources' as const, label: t('facct.tab.sources') },
    { id: 'archived' as const, label: t('facct.tab.archived'), badge: grouped.archived.length || undefined },
  ];
  const head = <FinancePageHead title={t('facct.title')} subtitle={t('facct.subtitle')}
    actions={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>}
    controls={<>
      <LedgerScope kind={ledgerKind} />
      {currencies.length > 0 && <label className="finControl"><span className="finControlLabel">{t('facct.reporting')}</span>
        <select value={reporting} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
    </>}
    tabs={<FinanceTabs idPrefix="facct" label={t('facct.tabs')} value={tab} onChange={(next) => { setTab(next); setSelectedId(null); }} items={tabs} />} />;

  if (accounts.kind === 'denied' || overview.kind === 'denied') return <div className="cmLayout finLayout fregPage">{head}<Denied /></div>;
  return <div className="cmLayout finLayout fregPage">
    {head}
    <div {...tabPanel('facct', tab)}>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {accounts.kind === 'loading' && <Notice busy>{t('finance.accounts.loading')}</Notice>}
      {accounts.kind === 'error' && <LoadError message={t('finance.error.accounts', { message: accounts.message })} retry={onChanged} />}
      {overview.kind === 'error' && <LoadError message={t('facct.error.overview', { message: overview.message })} retry={() => void loadOverview()} />}
      {accounts.kind === 'ready' && list.length === 0 && <FinCard title={t('finance.accounts.none')}>
        <Empty icon={<Landmark aria-hidden="true" />} title={t('finance.accounts.none')} text={t('facct.emptyText')} action={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} />
      </FinCard>}
      {accounts.kind === 'ready' && list.length > 0 && <>
        <Kpis accounts={live} info={info} overview={overview.kind === 'ready' ? overview.value : null} currency={reporting} />
        <div className="facctLayout">
          <div className="facctMain">
            {tab === 'sources'
              ? <SourcesTable accounts={shownList} info={info} />
              : shownList.length === 0
                ? <FinCard title={t(`facct.tab.${tab}` as MessageKey)}><Empty icon={<Wallet aria-hidden="true" />} title={t(`facct.empty.${tab}` as MessageKey)} text={t(`facct.empty.${tab}Text` as MessageKey)}
                  action={tab === 'archived' ? undefined : <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} /></FinCard>
                : <ul className="facctCards" aria-label={t(`facct.tab.${tab}` as MessageKey)}>
                  {shownList.map((a) => <li key={a.id}><AccountCard account={a} info={info.get(a.id)} selected={selected?.id === a.id} onSelect={() => setSelectedId(a.id)} /></li>)}
                </ul>}
            {selected && tab !== 'sources' && <>
              <div className="facctActions" role="group" aria-label={t('facct.actions.label', { name: selected.name })}>
                <span className="facctActionsTitle">{t('facct.actions.title')} <span className="muted" dir="auto">({selected.name})</span></span>
                <button type="button" className="secondary" onClick={() => setEditing(selected)}><Pencil aria-hidden="true" />{t('facct.action.edit')}</button>
                <button type="button" className="secondary" onClick={() => setOpening(selected)}><Scale aria-hidden="true" />{t('facct.action.opening')}</button>
                <button type="button" className="secondary" onClick={() => void statement(selected)} disabled={selected.transaction_count === 0}><Download aria-hidden="true" />{t('facct.action.download')}</button>
                <button type="button" className="secondary" onClick={() => void toggleArchive(selected)}>{selected.archived ? <Undo2 aria-hidden="true" /> : <Archive aria-hidden="true" />}{selected.archived ? t('facct.action.restore') : t('facct.action.archive')}</button>
                <button type="button" className="secondary danger" onClick={() => setRemoving(selected)}><Trash2 aria-hidden="true" />{t('finance.delete')}</button>
              </div>
              <FinBanner tone="ok" icon={<Info />}>{t(`facct.notice.${primarySource(info.get(selected.id)?.sources)}` as MessageKey)}</FinBanner>
              <AccountRegister account={selected} onOpenTransactions={() => onOpenTransactions(selected.id)} />
            </>}
          </div>
          <div className="facctSide">
            {selected && tab !== 'sources' && <ReconcileCard account={selected} info={info.get(selected.id)} reviewCount={overview.kind === 'ready' ? overview.value.review_count : 0} onImport={onImport} />}
            <RulesCard onManage={() => { location.hash = '#finance-settings'; }} />
            {selected && tab !== 'sources' && <ActivityCard account={selected} />}
            <FinBanner icon={<Info />} title={t('facct.important.title')}>{t('facct.important.text')}</FinBanner>
          </div>
        </div>
      </>}
    </div>
    {editing && <Modal title={editing === 'new' ? t('finance.accounts.add') : t('finance.accounts.editTitle')} className="formDialog" onClose={() => setEditing(null)}>
      <AccountForm account={editing === 'new' ? null : editing} base={base} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); changed(); } }} />
    </Modal>}
    {opening && <Modal title={t('facct.opening.title', { name: opening.name })} className="formDialog" onClose={() => setOpening(null)}>
      <OpeningForm account={opening} onDone={(text) => { setOpening(null); if (text) { setNotice({ text }); changed(); } }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('finance.accounts.deleteTitle')} confirmLabel={t('finance.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p dir="auto">{t('finance.accounts.deleteText', { name: removing.name, count: removing.transaction_count })}</p>
    </ConfirmDialog>}
  </div>;
}

function Kpis({ accounts, info, overview, currency }: { accounts: FinanceAccount[]; info: Map<string, FinanceAccountOverview>; overview: FinanceAccountsOverview | null; currency: string }) {
  // Available money, the one definition Safe to spend uses (docs/finance.md#how-figures-are-counted): bank and cash accounts
  // that are not archived, in the reporting currency, added exactly in thousandths and shown with the currency's own digits.
  const available = accounts.filter((a) => a.currency === currency && isAvailable(a));
  const total = fromMilli(available.reduce((sum, a) => sum + toMilli(a.balance), 0n), currency);
  const others = [...new Set(accounts.filter((a) => a.currency !== currency && isAvailable(a)).map((a) => a.currency))];
  const attention = accounts.filter((a) => (info.get(a.id)?.needs_attention.length ?? 0) > 0).length;
  const last = overview?.last_reconciled_at;
  return <KpiRow label={t('facct.kpi.label')}>
    <KpiCard icon={<Wallet />} label={t('facct.kpi.available')} value={available.length ? <span dir="ltr">{formatAmount(total, currency)}</span> : '-'}
      hint={others.length ? t('facct.kpi.otherCurrencies', { list: others.join(', ') }) : t('facct.kpi.availableHint')} />
    <KpiCard icon={<Landmark />} label={t('facct.kpi.accounts')} value={accounts.length} />
    <KpiCard icon={<CalendarCheck />} label={t('facct.kpi.reconciled')} value={last ? formatDateTime(last, { dateStyle: 'medium' }) : t('facct.kpi.never')} hint={last ? undefined : t('facct.kpi.neverHint')} />
    <KpiCard icon={<AlertTriangle />} label={t('facct.kpi.attention')} value={attention}
      hint={overview?.review_count ? t('facct.kpi.reviewWaiting', { count: overview.review_count }) : undefined} tone={attention || overview?.review_count ? 'warn' : ''} />
  </KpiRow>;
}

function AccountCard({ account: a, info, selected, onSelect }: { account: FinanceAccount; info: FinanceAccountOverview | undefined; selected: boolean; onSelect: () => void }) {
  const [shown, setShown] = useState(false);
  const source = primarySource(info?.sources);
  const SourceIcon = SOURCE_ICON[source];
  const KindIcon = KIND_ICON[a.kind];
  const updated = info?.last_activity_on;
  return <div className={`facctCard ${selected ? 'selected' : ''}`}>
    <button type="button" className="facctCardSelect" aria-pressed={selected} onClick={onSelect}>
      <span className="facctCardTop"><span className="facctKind" aria-hidden="true"><KindIcon /></span>
        <span className="facctName"><strong dir="auto">{a.name}</strong><small dir="auto">{a.institution || t(`finance.kind.${a.kind}` as MessageKey)}</small></span>
        <span className={`facctSource src-${source}`}><SourceIcon aria-hidden="true" />{t(`facct.source.${source}` as MessageKey)}</span></span>
      <span className="facctBalance"><Money value={a.balance} currency={a.currency} signed={a.kind === 'card' || a.kind === 'loan'} /></span>
      {info?.credit_limit && <small className="facctMeta">{t('facct.card.limit', { amount: isolate(formatAmount(info.credit_limit, a.currency)) })}</small>}
      {info?.payment_due && <span className="facctDue"><CalendarCheck aria-hidden="true" />{t('facct.card.due', { date: info.payment_due.due_on, amount: isolate(formatAmount(info.payment_due.amount, info.payment_due.currency)) })}</span>}
      {info?.last_statement && info.last_statement.status !== 'unknown' && <small className="facctMeta" dir="auto">{info.last_statement.status === 'tied'
        ? ((info.last_statement.not_in_ledger ?? 0) > 0
          ? t('facct.statement.tiedMissing', { file: info.last_statement.filename, count: info.last_statement.not_in_ledger ?? 0 })
          : t('facct.statement.tied', { file: info.last_statement.filename }))
        : t('facct.statement.differs', { file: info.last_statement.filename, difference: isolate(formatAmount(info.last_statement.difference, a.currency)) })}</small>}
      {info && info.needs_attention.length > 0 && <span className="facctAttention"><AlertTriangle aria-hidden="true" />{info.needs_attention.map((n) => t(`facct.attention.${n}` as MessageKey)).join(' · ')}</span>}
    </button>
    <div className="facctCardFoot">
      <span className="facctNumber"><span>{t(a.kind === 'card' ? 'facct.card.number' : 'facct.account.number')}</span> <bdi dir="ltr">{maskedNumber(a.last4, shown)}</bdi>
        {a.last4 && <button type="button" className="iconButton small" aria-pressed={shown} aria-label={shown ? t('facct.number.hide', { name: a.name }) : t('facct.number.show', { name: a.name })} onClick={() => setShown(!shown)}>
          {shown ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>}</span>
      <small className="muted">{updated ? t('facct.updated', { date: updated }) : t('facct.noActivity')}</small>
    </div>
  </div>;
}

function AccountRegister({ account, onOpenTransactions }: { account: FinanceAccount; onOpenTransactions: () => void }) {
  const [page, setPage] = useState<Load<FinanceRegisterPage>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setPage({ kind: 'loading' });
    try { setPage({ kind: 'ready', value: await getRegister({ ...NO_REGISTER_FILTERS, account_id: account.id }, 10, 0) }); } catch (e) { setPage(failed(e)); }
  }, [account.id]);
  useEffect(() => { void load(); }, [load, account.balance]);
  const ready = page.kind === 'ready' ? page.value : null;
  return <FinCard title={t('facct.register.title', { name: account.name })} className="facctRegister"
    actions={<>{ready && ready.total > 0 && <span className="muted">{t('facct.register.showing', { shown: ready.items.length, total: ready.total })}</span>}
      <button type="button" className="secondary" onClick={onOpenTransactions}>{t('facct.register.all')}<ArrowUpRight aria-hidden="true" className="cmFlip" /></button></>}>
    {page.kind === 'loading' && <Notice busy>{t('finance.transactions.loading')}</Notice>}
    {page.kind === 'denied' && <Denied />}
    {page.kind === 'error' && <LoadError message={t('finance.error.transactions', { message: page.message })} retry={() => void load()} />}
    {ready && (ready.items.length === 0
      ? <Empty icon={<ListChecks aria-hidden="true" />} title={t('facct.register.empty')} text={t('facct.register.emptyText')} />
      : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('facct.register.title', { name: account.name })}>
        <table className="finGrid fregTable">
          <thead><tr><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('finance.col.description')}</th><th scope="col">{t('finance.col.category')}</th>
            <th scope="col" className="num">{t('facct.col.inflow', { currency: account.currency })}</th><th scope="col" className="num">{t('facct.col.outflow', { currency: account.currency })}</th>
            <th scope="col" className="num fregHideSm">{t('facct.col.balance', { currency: account.currency })}</th></tr></thead>
          <tbody>{ready.items.map((row) => {
            const inflow = Number(row.amount) > 0;
            const figure = (value: string, tone: string) => <span className={`finMoney ${tone}`} dir="ltr">{plainAmount(value, minorDigits(row.currency), intlLocale(getLocale()))}</span>;
            return <tr key={row.id}>
              <td className="fregDate"><bdi dir="ltr">{row.posted_on}</bdi></td>
              <td className="fregDesc"><strong dir="auto">{row.description}</strong></td>
              <td><CategoryChip category={row.category} /></td>
              <td className="num">{inflow ? figure(row.amount, amountTone(row.amount)) : <span className="muted">-</span>}</td>
              <td className="num">{inflow ? <span className="muted">-</span> : figure(row.amount, amountTone(row.amount))}</td>
              <td className="num fregHideSm">{row.running_balance ? figure(row.running_balance, '') : '-'}</td>
            </tr>;
          })}</tbody>
        </table></div>)}
  </FinCard>;
}

function ReconcileCard({ account, info, reviewCount, onImport }: { account: FinanceAccount; info: FinanceAccountOverview | undefined; reviewCount: number; onImport: () => void }) {
  const imported = info?.imported ?? 0, matched = info?.matched ?? 0;
  const share = percentOf(matched, imported);
  return <FinCard title={t('facct.recon.title')} icon={<RefreshCw />}
    subtitle={info?.last_import_at ? t('facct.recon.lastImport', { date: formatDateTime(info.last_import_at) }) : t('facct.recon.noImport')}>
    <dl className="facctReconFigures">
      <div><dt>{t('facct.recon.imported')}</dt><dd>{imported}</dd></div>
      <div><dt>{t('facct.recon.matched')}</dt><dd className="ok">{matched}{share !== null && <small> ({share}%)</small>}</dd></div>
      <div><dt>{t('facct.recon.pending')}</dt><dd className={reviewCount ? 'warn' : ''}>{reviewCount}</dd></div>
    </dl>
    {info?.difference && info.difference !== '0' && Number(info.difference) !== 0 && <FinBanner tone="warn" icon={<AlertTriangle />}>
      {t('facct.recon.difference', { stated: isolate(formatAmount(info.observed_balance ?? '0', account.currency)), difference: isolate(formatAmount(info.difference, account.currency)) })}</FinBanner>}
    <p className="muted facctReconNote">{t('facct.recon.note')}</p>
    <button type="button" className="facctWide" onClick={onImport}><Upload aria-hidden="true" />{t('facct.recon.now')}</button>
  </FinCard>;
}

function RulesCard({ onManage }: { onManage: () => void }) {
  const [state, setState] = useState<Load<FinanceSettings>>({ kind: 'loading' });
  useEffect(() => { void api.getSettings().then((value) => setState({ kind: 'ready', value }), (e) => setState(failed(e))); }, []);
  const rules = state.kind === 'ready' ? state.value.category_rules : [];
  return <FinCard title={t('facct.rules.title')} icon={<ListChecks />} subtitle={t('facct.rules.subtitle')}
    actions={rules.length > 0 ? <StatusChip tone="ok">{t('facct.rules.active', { count: rules.length })}</StatusChip> : undefined}>
    {state.kind === 'loading' && <Notice busy>{t('common.loading')}</Notice>}
    {state.kind === 'error' && <LoadError message={state.message} retry={() => setState({ kind: 'loading' })} />}
    {state.kind === 'ready' && (rules.length === 0
      ? <p className="muted">{t('facct.rules.none')}</p>
      : <ul className="facctRules">{rules.slice(0, 5).map((r) => <li key={`${r.match}:${r.category}`}><span dir="auto">{r.match}</span><ArrowUpRight aria-hidden="true" className="facctArrow cmFlip" /><CategoryChip category={r.category} /></li>)}
        {rules.length > 5 && <li className="muted">{t('facct.rules.more', { count: rules.length - 5 })}</li>}</ul>)}
    <button type="button" className="secondary facctWide" onClick={onManage}>{t('facct.rules.manage')}</button>
  </FinCard>;
}

function ActivityCard({ account }: { account: FinanceAccount }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(14);
  const [state, setState] = useState<Load<FinanceAccountActivity>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try { setState({ kind: 'ready', value: await getAccountActivity(account.id, days) }); } catch (e) { setState(failed(e)); }
  }, [account.id, days]);
  useEffect(() => { void load(); }, [load, account.balance]);
  return <FinCard title={t('facct.activity.title')} icon={<BarChart3 />}
    actions={<label className="finControl"><span className="visuallyHidden">{t('facct.activity.range')}</span>
      <select value={days} onChange={(e) => setDays(Number(e.target.value) as (typeof RANGES)[number])}>{RANGES.map((r) => <option key={r} value={r}>{t('facct.activity.days', { count: r })}</option>)}</select></label>}>
    {state.kind === 'loading' && <Notice busy>{t('facct.activity.loading')}</Notice>}
    {state.kind === 'error' && <LoadError message={state.message} retry={() => void load()} />}
    {state.kind === 'ready' && <ActivityChart activity={state.value} />}
  </FinCard>;
}

/** Grouped bars, money in and money out per day. Inline SVG with a data table for screen readers. */
export function ActivityChart({ activity }: { activity: FinanceAccountActivity }) {
  const { bars, top, ticks, empty } = activityScale(activity);
  const locale = getLocale() === 'ar' ? 'ar' : 'en';
  if (empty) return <p className="muted">{t('facct.activity.empty')}</p>;
  const width = 320, height = 150, left = 34, bottom = 18, plot = height - bottom - 6;
  const slot = (width - left) / bars.length;
  const bar = Math.max(2, Math.min(10, slot / 2 - 1));
  const y = (v: number) => 6 + plot - (v / top) * plot;
  const labelEvery = Math.ceil(bars.length / 5);
  return <figure className="facctChart">
    <div className="facctLegend" aria-hidden="true"><span className="in" />{t('facct.activity.inflow')}<span className="out" />{t('facct.activity.outflow')}</div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t('facct.activity.aria', { count: bars.length, currency: activity.currency })}>
      {ticks.map((tick) => <g key={tick}><line x1={left} x2={width} y1={y(tick)} y2={y(tick)} className="grid" />
        <text x={left - 4} y={y(tick) + 3} textAnchor="end" className="tick">{compact(tick, locale)}</text></g>)}
      {bars.map((b, i) => {
        const x = left + i * slot + slot / 2;
        return <g key={b.day}>
          {b.inflow > 0 && <rect x={x - bar - 0.5} y={y(b.inflow)} width={bar} height={Math.max(1, 6 + plot - y(b.inflow))} rx={1.5} className="in"><title>{`${b.day}: ${formatAmount(String(b.inflow), activity.currency)}`}</title></rect>}
          {b.outflow > 0 && <rect x={x + 0.5} y={y(b.outflow)} width={bar} height={Math.max(1, 6 + plot - y(b.outflow))} rx={1.5} className="out"><title>{`${b.day}: -${formatAmount(String(b.outflow), activity.currency)}`}</title></rect>}
          {i % labelEvery === 0 && <text x={x} y={height - 4} textAnchor="middle" className="tick">{b.day.slice(5)}</text>}
        </g>;
      })}
    </svg>
    <table className="visuallyHidden"><caption>{t('facct.activity.title')}</caption>
      <thead><tr><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('facct.activity.inflow')}</th><th scope="col">{t('facct.activity.outflow')}</th></tr></thead>
      <tbody>{bars.filter((b) => b.inflow || b.outflow).map((b) => <tr key={b.day}><td>{b.day}</td><td>{formatAmount(String(b.inflow), activity.currency)}</td><td>{formatAmount(String(b.outflow), activity.currency)}</td></tr>)}</tbody>
    </table>
  </figure>;
}

function SourcesTable({ accounts, info }: { accounts: FinanceAccount[]; info: Map<string, FinanceAccountOverview> }) {
  return <FinCard title={t('facct.sources.title')} subtitle={t('facct.sources.subtitle')}
    actions={<><a className="button secondary" href="#finance-connections">{t('facct.sources.connections')}</a><a className="button secondary" href="#finance-sms">{t('facct.sources.sms')}</a></>}>
    <FinBanner icon={<Info />}>{t('facct.sources.honest')}</FinBanner>
    {accounts.length === 0 ? <p className="muted">{t('finance.accounts.none')}</p> : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('facct.sources.title')}>
      <table className="finGrid">
        <thead><tr><th scope="col">{t('finance.col.account')}</th><th scope="col">{t('facct.sources.feeds')}</th><th scope="col">{t('facct.sources.lastImport')}</th><th scope="col">{t('facct.sources.stated')}</th></tr></thead>
        <tbody>{accounts.map((a) => {
          const o = info.get(a.id);
          return <tr key={a.id}><td dir="auto"><strong>{a.name}</strong></td>
            <td><span className="facctSourceList">{(o?.sources ?? ['manual']).map((s) => { const Icon = SOURCE_ICON[s]; return <span key={s} className={`facctSource src-${s}`}><Icon aria-hidden="true" />{t(`facct.source.${s}` as MessageKey)}</span>; })}</span></td>
            <td>{o?.last_import_at ? formatDateTime(o.last_import_at) : '-'}</td>
            <td>{o?.observed_balance ? <><Money value={o.observed_balance} currency={a.currency} /> <small className="muted">{o.observed_at ? formatDateTime(o.observed_at) : ''}</small></> : '-'}</td></tr>;
        })}</tbody>
      </table></div>}
  </FinCard>;
}

function OpeningForm({ account, onDone }: { account: FinanceAccount; onDone: (message: string | null) => void }) {
  const [value, setValue] = useState(account.opening_balance);
  const [inCredit, setInCredit] = useState(false);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const owes = account.kind === 'card' || account.kind === 'loan';
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = parseAmount(value || '0', account.currency);
    if (!checked.ok) { setError(checked.error); return; }
    if (openingNeedsSign(account.kind, checked.value, inCredit)) { setError(t('finance.error.openingOwed')); return; }
    setBusy(true); setError(null);
    try { await api.updateAccount(account.id, { opening_balance: checked.value, credit_balance: owes && inCredit }); onDone(t('facct.opening.saved', { name: account.name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)}>
    <p className="muted">{t('facct.opening.text')}</p>
    <label className="field"><span>{t('finance.form.opening', { currency: account.currency })}</span><input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" dir="ltr" autoFocus />
      {owes && <small className="muted">{t('finance.form.openingOwedHelp')}</small>}</label>
    {owes && <label className="finCheck"><input type="checkbox" checked={inCredit} onChange={(e) => setInCredit(e.target.checked)} /><span>{t('finance.form.inCredit')}</span></label>}
    <FormError id={`opening-${account.id}`} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

