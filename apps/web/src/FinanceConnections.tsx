// Finance, Connections and data capture (docs/finance.md#connections-and-data-capture). Its own lazy
// chunk: one page over the real sources that feed the ledger. ANUM has no live bank link (Open Finance
// is an owner action), so accounts show what really feeds them (forwarded SMS and Apple Pay, statement
// imports, or entries by hand) with their real figures; the iPhone SMS capture panel shows the device,
// the address, the token's hint (a token is shown once and never again: it can be rotated, never
// revealed), recent messages and the review policy. Tabs: Connected accounts, SMS capture (the full
// SMS tools), iPhone Shortcuts (Settings, Integrations, iPhone), Import history (CSV and PDF) and
// Permissions. Nothing here moves money.
import { Suspense, lazy, useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import {
  ArrowRight, Banknote, Briefcase, ChevronDown, Clock, Copy, CreditCard, FileText, FileUp, HandCoins, History, Info, Landmark, Layers, Lock,
  MessageSquareText, Pause, Pencil, Play, Plus, RefreshCw, ScrollText, Search, ShieldCheck, Smartphone, Trash2, TrendingUp, User, Workflow,
} from 'lucide-react';
import type {
  FinanceAccount, FinanceAccountKind, FinanceConnection, FinanceImport, FinancePage, FinanceSmsDevice, FinanceSmsDeviceToken, FinanceSmsMessage,
  FinanceSmsSettings, FinanceSmsStatusView, IphoneEvent, IphoneOverview,
} from '@anum/contracts';
import * as sms from './lib/financeSmsApi';
import * as iphone from './lib/iphoneApi';
import { deleteImport, listAccounts, listConnections, listImports } from './lib/financeApi';
import { apiBaseUrl } from './lib/api';
import {
  CONNECTIONS_TABS, accountSources, connectionsTabFor, importFormat, lastActivity, lastImportByAccount, maskedToken, searchAccounts, shortcutRoutes, smsCounts, sourceLabel,
  type ConnectionsTab,
} from './lib/financeConnections';
import { ingestUrl, kindLabel, reasonLabel } from './lib/financeSms';
import { codeLabel, outcomeLabel, scopeLabel } from './lib/iphoneIntegrations';
import type { FinanceTab } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { ConnectionsPanel } from './FinancePlan';
import { SmsPanel, TokenDialog } from './FinanceSms';
import './financeSms.css';
import './financeConnections.css';

const IphoneIntegrations = lazy(() => import('./IphoneIntegrations').then((m) => ({ default: m.IphoneIntegrations })));

const ACCOUNTS_PAGE = 6;
const IMPORTS_PAGE = 20;
const RECENT_MESSAGES = 5;
const when = (value: string | null | undefined) => (value ? formatDateTime(value) : '-');
/** Day, month and time without the year: the compact form the cards and tables use. */
const SHORT: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' };
const short = (value: string | null | undefined) => (value ? formatDateTime(value, SHORT) : '-');
const retentionText = (days: number) => (days === 0 ? t('fconn.policy.eraseOn') : t('fconn.perm.retentionText', { count: days }));

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(() => { load().then((value) => setState({ kind: 'ready', value }), (e) => setState(failed(e))); }, [load]);
  useEffect(() => { run(); }, [run]);
  return [state, run];
}
const ready = <T,>(state: Load<T>): T | null => (state.kind === 'ready' ? state.value : null);

const KIND_ICONS: Record<FinanceAccountKind, typeof Landmark> = { bank: Landmark, card: CreditCard, cash: Banknote, loan: HandCoins, investment: TrendingUp };
const loadImports = () => listImports(50, 0);

export function ConnectionsPage({ from, onOpen }: { from: FinanceTab; onOpen: (tab: FinanceTab) => void }) {
  const initial = connectionsTabFor(from);
  const [tab, setTab] = useState<ConnectionsTab>(initial);
  useEffect(() => { setTab(initial); }, [initial]);
  const [status, reloadStatus] = useLoad(sms.smsStatus);
  const [settings, reloadSettings] = useLoad(sms.smsSettings);
  const [accounts, reloadAccounts] = useLoad(listAccounts);
  const [imports, reloadImports] = useLoad(loadImports);
  const [connections] = useLoad(listConnections);
  const [overview, reloadOverview] = useLoad(iphone.overview);
  const reload = useCallback(() => { reloadStatus(); reloadSettings(); reloadAccounts(); reloadImports(); reloadOverview(); },
    [reloadStatus, reloadSettings, reloadAccounts, reloadImports, reloadOverview]);
  const tabsId = useId();
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const keyNav = (event: ReactKeyboardEvent, index: number) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step && event.key !== 'Home' && event.key !== 'End') return;
    event.preventDefault();
    const rtl = document.documentElement.dir === 'rtl';
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? CONNECTIONS_TABS.length - 1 : (index + (rtl ? -step : step) + CONNECTIONS_TABS.length) % CONNECTIONS_TABS.length;
    setTab(CONNECTIONS_TABS[next]); tabRefs.current[CONNECTIONS_TABS[next]]?.focus();
  };
  if (status.kind === 'denied' || accounts.kind === 'denied') return <Denied />;
  const icons: Record<ConnectionsTab, ReactNode> = {
    accounts: <Landmark aria-hidden="true" />, sms: <MessageSquareText aria-hidden="true" />, shortcuts: <Layers aria-hidden="true" />,
    imports: <FileUp aria-hidden="true" />, permissions: <ShieldCheck aria-hidden="true" />,
  };
  return <div className="fconn">
    <header className="fconnHead">
      <div><h2 className="fconnTitle">{t('fconn.title')}</h2><p className="muted">{t('fconn.subtitle')}</p></div>
      <WorkScope settings={ready(settings)} onSaved={reloadSettings} />
    </header>
    <div className="fconnTabs" role="tablist" aria-label={t('fconn.tabs')}>
      {CONNECTIONS_TABS.map((id, index) => <button key={id} ref={(el) => { tabRefs.current[id] = el; }} type="button" role="tab" id={`${tabsId}-${id}`}
        aria-selected={tab === id} aria-controls={`${tabsId}-panel`} tabIndex={tab === id ? 0 : -1} onKeyDown={(e) => keyNav(e, index)} onClick={() => setTab(id)}>
        {icons[id]}{t(`fconn.tab.${id}` as MessageKey)}{id === 'sms' && (ready(status)?.review_count ?? 0) > 0 ? <span className="fconnCount">{ready(status)?.review_count}</span> : null}</button>)}
    </div>
    <Kpis status={status} accounts={accounts} imports={imports} connections={connections} overview={overview} onReview={() => setTab('sms')} />
    <div role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${tab}`} className="fconnPanel">
      {tab === 'accounts' && <>
        <div className="fconnGrid">
          <AccountsCard accounts={accounts} settings={ready(settings)} imports={ready(imports)?.items ?? []} reload={reloadAccounts} onOpen={onOpen} onTab={setTab} />
          <CapturePanel status={status} settings={settings} reload={reload} onTab={setTab} />
        </div>
        <ConnectionsPanel />
      </>}
      {tab === 'sms' && <SmsPanel />}
      {tab === 'shortcuts' && <Suspense fallback={<section className="surface cmPanel"><Notice busy>{t('iph.loading')}</Notice></section>}><IphoneIntegrations /></Suspense>}
      {tab === 'imports' && <ImportHistory accounts={ready(accounts) ?? []} onOpen={onOpen} onChanged={() => { reloadImports(); reloadAccounts(); }} />}
      {tab === 'permissions' && <Permissions settings={settings} overview={overview} onTab={setTab} />}
    </div>
  </div>;
}

// ---- head ------------------------------------------------------------------------------------

/** The ledger's kind for this workspace (`ledger_kind`, docs/finance-sms.md#dashboard): a label, not a filter.
 *  Personal and business books stay apart because each workspace has its own ledger. */
function WorkScope({ settings, onSaved }: { settings: FinanceSmsSettings | null; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const labelId = useId();
  const pick = async (kind: 'business' | 'personal') => {
    if (!settings || settings.ledger_kind === kind) return;
    setBusy(true); setError(null);
    try { await sms.saveSmsSettings({ version: settings.version, ledger_kind: kind }); onSaved(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <div className="fconnScope">
    <span id={labelId}>{t('fconn.scope')}</span>
    <div className="fconnSegment" role="group" aria-labelledby={labelId}>
      {(['business', 'personal'] as const).map((kind) => <button key={kind} type="button" aria-pressed={settings?.ledger_kind === kind} disabled={!settings || busy}
        onClick={() => void pick(kind)}>{kind === 'business' ? <Briefcase aria-hidden="true" /> : <User aria-hidden="true" />}{t(`fsms.ledgerKind.${kind}` as MessageKey)}</button>)}
    </div>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
  </div>;
}

function Kpis({ status, accounts, imports, connections, overview, onReview }: {
  status: Load<FinanceSmsStatusView>; accounts: Load<FinanceAccount[]>; imports: Load<FinancePage<FinanceImport>>; connections: Load<FinanceConnection[]>;
  overview: Load<IphoneOverview>; onReview: () => void;
}) {
  const live = (accounts.kind === 'ready' ? accounts.value.filter((a) => !a.archived) : null);
  const routes = overview.kind === 'ready' ? shortcutRoutes(overview.value) : null;
  const last = status.kind === 'ready' && imports.kind === 'ready'
    ? lastActivity(status.value, imports.value.items, connections.kind === 'ready' ? connections.value : []) : undefined;
  const pending = t('fconn.kpi.loading');
  const review = status.kind === 'ready' ? status.value.review_count : null;
  return <ul className="fconnKpis" aria-label={t('fconn.kpis')}>
    <Kpi icon={<Landmark aria-hidden="true" />} label={t('fconn.kpi.sources')} value={live === null ? '-' : String(live.length)}
      hint={live === null ? (accounts.kind === 'loading' ? pending : t('fconn.kpi.unavailable')) : t('fconn.kpi.sourcesHint')} />
    <Kpi icon={<Layers aria-hidden="true" />} label={t('fconn.kpi.routes')} value={routes === null ? '-' : String(routes.live)}
      hint={routes === null ? (overview.kind === 'loading' ? pending : t('fconn.kpi.unavailable')) : t('fconn.kpi.routesHint', { total: routes.total })} />
    <Kpi icon={<FileText aria-hidden="true" />} label={t('fconn.kpi.review')} value={review === null ? '-' : String(review)} tone={review ? 'warn' : undefined}
      hint={review === null ? pending : review ? undefined : t('fconn.kpi.reviewNone')}
      action={review ? <button type="button" className="cmLinkButton" onClick={onReview}>{t('fconn.kpi.reviewOpen')}</button> : undefined} />
    <Kpi icon={<RefreshCw aria-hidden="true" />} label={t('fconn.kpi.last')} value={last === undefined ? '-' : last ? formatDateTime(last.at, { timeStyle: 'short' }) : '-'}
      hint={last === undefined ? pending : last ? `${formatDateTime(last.at, { dateStyle: 'medium' })} · ${t(`fconn.kpi.last.${last.source}` as MessageKey)}` : t('fconn.kpi.lastNone')} />
  </ul>;
}

function Kpi({ icon, label, value, hint, tone, action }: { icon: ReactNode; label: string; value: string; hint?: string; tone?: 'warn'; action?: ReactNode }) {
  return <li className={`fconnKpi ${tone ?? ''}`}><span className="fconnKpiIcon">{icon}</span>
    <div><strong dir="ltr">{value}</strong><span>{label}</span>{hint && <small>{hint}</small>}{action}</div></li>;
}

// ---- connected accounts ----------------------------------------------------------------------

function AccountsCard({ accounts, settings, imports, reload, onOpen, onTab }: {
  accounts: Load<FinanceAccount[]>; settings: FinanceSmsSettings | null; imports: FinanceImport[]; reload: () => void;
  onOpen: (tab: FinanceTab) => void; onTab: (tab: ConnectionsTab) => void;
}) {
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const searchId = useId();
  const list = useMemo(() => (accounts.kind === 'ready' ? accounts.value.filter((a) => !a.archived) : []), [accounts]);
  const shown = searchAccounts(list, search);
  const lastImport = useMemo(() => lastImportByAccount(imports), [imports]);
  return <section className="surface cmPanel fconnCard" aria-labelledby="fconn-accounts">
    <p className="fconnReadOnly"><Info aria-hidden="true" />{t('fconn.readOnly')}</p>
    <div className="sectionHeader"><h2 id="fconn-accounts">{t('fconn.accounts.title', { count: list.length })}</h2>
      <AddSourceMenu onOpen={onOpen} onTab={onTab} /></div>
    <p className="muted fconnNote">{t('fconn.accounts.noBankLink')}</p>
    {accounts.kind === 'loading' && <Notice busy>{t('fconn.loading')}</Notice>}
    {accounts.kind === 'error' && <LoadError message={t('fconn.accounts.error', { message: accounts.message })} retry={reload} />}
    {accounts.kind === 'ready' && (list.length === 0
      ? <Empty icon={<Landmark aria-hidden="true" />} title={t('fconn.accounts.none')} text={t('fconn.accounts.noneText')}
        action={<button type="button" onClick={() => onOpen('accounts')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} />
      : <>
        {list.length > ACCOUNTS_PAGE && <label className="fconnSearch" htmlFor={searchId}><Search aria-hidden="true" /><span className="visuallyHidden">{t('fconn.accounts.search')}</span>
          <input id={searchId} type="search" value={search} placeholder={t('fconn.accounts.search')} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} /></label>}
        {shown.length === 0 ? <p className="muted">{t('fconn.accounts.noMatch')}</p>
          : <ul className="fconnAccounts">{shown.slice(offset, offset + ACCOUNTS_PAGE).map((a) =>
            <AccountRow key={a.id} account={a} sources={accountSources(a, settings, imports)} lastImport={lastImport[a.id] ?? null} onOpen={onOpen} onTab={onTab} />)}</ul>}
        {shown.length > ACCOUNTS_PAGE && <div className="cmPager"><button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - ACCOUNTS_PAGE))}>{t('fsms.prev')}</button>
          <span className="muted cmPagerText">{t('fsms.range', { from: offset + 1, to: Math.min(offset + ACCOUNTS_PAGE, shown.length), total: shown.length })}</span>
          <button type="button" className="secondary" disabled={offset + ACCOUNTS_PAGE >= shown.length} onClick={() => setOffset(offset + ACCOUNTS_PAGE)}>{t('fsms.next')}</button></div>}
      </>)}
  </section>;
}

function AccountRow({ account: a, sources, lastImport, onOpen, onTab }: {
  account: FinanceAccount; sources: ReturnType<typeof accountSources>; lastImport: string | null; onOpen: (tab: FinanceTab) => void; onTab: (tab: ConnectionsTab) => void;
}) {
  const Icon = KIND_ICONS[a.kind] ?? Landmark;
  const titleId = useId();
  return <li className="fconnAccount" aria-labelledby={titleId}>
    <div className="fconnAccountHead">
      <span className="fconnAccountIcon"><Icon aria-hidden="true" /></span>
      <div className="fconnAccountName"><strong id={titleId} dir="auto">{a.name}{a.institution ? <span className="muted"> · <bdi>{a.institution}</bdi></span> : null}</strong>
        <small>{t(`finance.kind.${a.kind}` as MessageKey)}{a.last4 ? <> · <bdi dir="ltr">•••• {a.last4}</bdi></> : null}</small></div>
      <div className="fconnChips">
        {sources.map((s) => <span key={s} className={`statusBadge ${s === 'manual' ? 'neutral' : 'success'}`}>{sourceLabel(s)}</span>)}
        <span className="fconnChip"><Lock aria-hidden="true" />{t('fconn.readOnlyChip')}</span>
      </div>
    </div>
    <dl className="fconnFacts">
      <div><dt><Clock aria-hidden="true" />{t('fconn.account.lastImport')}</dt><dd>{short(lastImport)}</dd></div>
      <div><dt><History aria-hidden="true" />{t('fconn.account.records')}</dt><dd dir="ltr">{a.transaction_count}</dd></div>
      <div><dt><Workflow aria-hidden="true" />{t('fconn.account.currency')}</dt><dd dir="ltr">{a.currency}</dd></div>
      <div><dt><Banknote aria-hidden="true" />{t('fconn.account.balance')}</dt><dd><Money value={a.balance} currency={a.currency} signed /></dd></div>
    </dl>
    <div className="fconnActions">
      {a.kind !== 'cash' && <button type="button" className="secondary" onClick={() => onOpen('import')} aria-label={t('fconn.account.importFor', { name: a.name })}><FileUp aria-hidden="true" />{t('fconn.account.import')}</button>}
      {!sources.includes('sms') && a.kind !== 'loan' && a.kind !== 'investment' && <button type="button" className="secondary" onClick={() => onTab('sms')}
        aria-label={t('fconn.account.smsFor', { name: a.name })}><Smartphone aria-hidden="true" />{t('fconn.account.sms')}</button>}
      <button type="button" className="secondary" onClick={() => onOpen('accounts')} aria-label={t('fconn.account.editFor', { name: a.name })}><Pencil aria-hidden="true" />{t('fconn.account.edit')}</button>
    </div>
  </li>;
}

/** "Add a source": the ways data really reaches the ledger. A live bank link is not one of them yet. */
function AddSourceMenu({ onOpen, onTab }: { onOpen: (tab: FinanceTab) => void; onTab: (tab: ConnectionsTab) => void }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close); document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  const go = (fn: () => void) => { setOpen(false); fn(); };
  return <div className="fconnMenu" ref={ref}>
    <button type="button" className="cmPrimary" aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(!open)}><Plus aria-hidden="true" />{t('fconn.add')}<ChevronDown aria-hidden="true" /></button>
    {open && <div id={menuId} className="fconnMenuList">
      <button type="button" onClick={() => go(() => onOpen('accounts'))}><Landmark aria-hidden="true" /><span><strong>{t('fconn.add.manual')}</strong><small>{t('fconn.add.manualText')}</small></span></button>
      <button type="button" onClick={() => go(() => onOpen('import'))}><FileUp aria-hidden="true" /><span><strong>{t('fconn.add.statement')}</strong><small>{t('fconn.add.statementText')}</small></span></button>
      <button type="button" onClick={() => go(() => onTab('sms'))}><Smartphone aria-hidden="true" /><span><strong>{t('fconn.add.sms')}</strong><small>{t('fconn.add.smsText')}</small></span></button>
      <p className="fconnMenuNote"><Lock aria-hidden="true" />{t('fconn.add.bankNote')}</p>
    </div>}
  </div>;
}

// ---- iPhone SMS capture ----------------------------------------------------------------------

function CapturePanel({ status, settings, reload, onTab }: {
  status: Load<FinanceSmsStatusView>; settings: Load<FinanceSmsSettings>; reload: () => void; onTab: (tab: ConnectionsTab) => void;
}) {
  const [devices, reloadDevices] = useLoad(sms.smsDevices);
  const refresh = () => { reload(); reloadDevices(); };
  return <section className="surface cmPanel fconnCard fconnCapture" aria-labelledby="fconn-capture">
    <div className="fconnCaptureHead">
      <span className="fconnAccountIcon"><Smartphone aria-hidden="true" /></span>
      <div><h2 id="fconn-capture">{t('fconn.capture.title')}</h2><p className="muted">{t('fconn.capture.subtitle')}</p></div>
      {status.kind === 'ready' && <span className={`statusBadge ${status.value.enabled ? 'success' : status.value.ready ? 'neutral' : 'warning'}`}>
        {status.value.enabled ? t('fconn.capture.active') : status.value.ready ? t('fconn.capture.off') : t('fconn.capture.needsCheck')}</span>}
    </div>
    {status.kind === 'loading' && <Notice busy>{t('fconn.loading')}</Notice>}
    {status.kind === 'error' && <LoadError message={t('fsms.error.load', { message: status.message })} retry={refresh} />}
    {status.kind === 'ready' && <>
      <CaptureStats status={status.value} devices={ready(devices)} />
      <h3 className="fconnSub">{t('fconn.how.title')}</h3>
      <ol className="fconnSteps">
        <li><Smartphone aria-hidden="true" /><span><strong>{t('fconn.how.1')}</strong><small>{t('fconn.how.1Text')}</small></span></li>
        <li><Lock aria-hidden="true" /><span><strong>{t('fconn.how.2')}</strong><small>{t('fconn.how.2Text')}</small></span></li>
        <li><FileText aria-hidden="true" /><span><strong>{t('fconn.how.3')}</strong><small>{t('fconn.how.3Text')}</small></span></li>
      </ol>
      <DeviceControls status={status.value} devices={devices} reload={refresh} onTab={onTab} />
      <RecentMessages onTab={onTab} />
      <div className="fconnPolicyRow">
        {settings.kind === 'ready' ? <ReviewPolicy settings={settings.value} onSaved={reload} />
          : settings.kind === 'error' ? <LoadError message={settings.message} retry={reload} /> : <Notice busy>{t('fconn.loading')}</Notice>}
        <aside className="fconnPrivacy" aria-labelledby="fconn-privacy"><h3 id="fconn-privacy"><ShieldCheck aria-hidden="true" />{t('fconn.privacy.title')}</h3>
          <p>{t('fconn.privacy.text')}</p>{settings.kind === 'ready' && <p>{retentionText(settings.value.body_retention_days)}</p>}</aside>
      </div>
    </>}
  </section>;
}

function CaptureStats({ status, devices }: { status: FinanceSmsStatusView; devices: FinanceSmsDevice[] | null }) {
  const counts = smsCounts(status);
  const active = devices?.filter((d) => d.status === 'active').length ?? status.devices_active;
  return <dl className="fconnStats">
    <div><dt><Smartphone aria-hidden="true" />{t('fconn.stat.shortcut')}</dt><dd>{active > 0 ? t('fconn.stat.shortcutOn', { count: active }) : t('fconn.stat.shortcutNone')}</dd></div>
    <div><dt><Clock aria-hidden="true" />{t('fconn.stat.lastReceived')}</dt><dd>{short(status.last_received_at)}</dd></div>
    <div><dt><MessageSquareText aria-hidden="true" />{t('fconn.stat.received')}</dt><dd dir="ltr">{counts.received}</dd></div>
    <div><dt><ShieldCheck aria-hidden="true" />{t('fconn.stat.recorded')}</dt><dd dir="ltr">{counts.recorded}</dd></div>
    <div className={counts.review ? 'warn' : undefined}><dt><Info aria-hidden="true" />{t('fconn.stat.review')}</dt><dd dir="ltr">{counts.review}</dd></div>
  </dl>;
}

function DeviceControls({ status, devices, reload, onTab }: { status: FinanceSmsStatusView; devices: Load<FinanceSmsDevice[]>; reload: () => void; onTab: (tab: ConnectionsTab) => void }) {
  const usable = useMemo(() => (devices.kind === 'ready' ? devices.value.filter((d) => d.status !== 'revoked') : []), [devices]);
  const [chosen, setChosen] = useState('');
  const device = usable.find((d) => d.id === chosen) ?? usable[0] ?? null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [issued, setIssued] = useState<FinanceSmsDeviceToken | null>(null);
  const [confirm, setConfirm] = useState<'rotate' | 'revoke' | null>(null);
  const [log, setLog] = useState(false);
  const deviceId = useId(), endpointId = useId(), tokenId = useId();
  const url = ingestUrl(apiBaseUrl, status.ingest_path);
  const act = async (what: 'pause' | 'resume' | 'revoke' | 'rotate') => {
    if (!device) return;
    setBusy(true); setError(null);
    try {
      if (what === 'rotate') setIssued(await sms.rotateDevice(device.id)); else await sms.changeDevice(device.id, what);
      reload();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); setConfirm(null); }
  };
  const copy = async () => {
    if (!url) return;
    try { await navigator.clipboard.writeText(url); setCopied(true); } catch { setError(t('fconn.copyFailed')); }
  };
  return <div className="fconnDevice">
    <div className="fconnFields">
      <div className="fconnField"><label htmlFor={endpointId}>{t('fconn.endpoint')}</label>
        {url ? <div className="fconnInputRow"><input id={endpointId} readOnly value={url} dir="ltr" onFocus={(e) => e.currentTarget.select()} />
          <button type="button" className="iconButton small" onClick={() => void copy()} aria-label={copied ? t('fconn.copied') : t('fconn.copyEndpoint')} title={copied ? t('fconn.copied') : t('fconn.copyEndpoint')}><Copy aria-hidden="true" /></button></div>
          : <p className="muted" id={endpointId}>{t('fsms.endpointHidden')}</p>}</div>
      <div className="fconnField"><label htmlFor={tokenId}>{t('fconn.token')}</label>
        {device ? <div className="fconnInputRow"><input id={tokenId} readOnly value={maskedToken(device.token_hint)} dir="ltr" aria-describedby={`${tokenId}-help`} />
          <button type="button" className="iconButton small" disabled={busy} onClick={() => setConfirm('rotate')} aria-label={t('fconn.rotate')} title={t('fconn.rotate')}><RefreshCw aria-hidden="true" /></button></div>
          : <p className="muted" id={tokenId}>{t('fconn.token.none')}</p>}
        {device && <small className="muted" id={`${tokenId}-help`}>{t('fconn.token.help')}</small>}</div>
    </div>
    {devices.kind === 'loading' && <Notice busy>{t('fconn.loading')}</Notice>}
    {devices.kind === 'error' && <LoadError message={devices.message} retry={reload} />}
    {devices.kind === 'ready' && (device ? <>
      <dl className="fconnDeviceFacts">
        <div><dt>{t('fconn.scopeLabel')}</dt><dd className="fconnChips">{device.scopes.map((s) => <span key={s} className="statusBadge success">{scopeLabel(s)}</span>)}</dd></div>
        <div><dt><label htmlFor={deviceId}>{t('fconn.device')}</label></dt><dd>{usable.length > 1
          ? <select id={deviceId} value={device.id} onChange={(e) => setChosen(e.target.value)}>{usable.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
          : <span id={deviceId} dir="auto"><Smartphone aria-hidden="true" className="cmInlineIcon" />{device.name}</span>}</dd></div>
        <div><dt>{t('fconn.lastCheckIn')}</dt><dd>{short(device.last_seen_at)}</dd></div>
        <div><dt>{t('fconn.deviceStatus')}</dt><dd><span className={`statusBadge ${device.status === 'active' ? 'success' : 'warning'}`}>{t(`fsms.device.${device.status}` as MessageKey)}</span></dd></div>
      </dl>
      <div className="fconnActions">
        {device.status === 'active'
          ? <button type="button" className="secondary" disabled={busy} onClick={() => void act('pause')}><Pause aria-hidden="true" />{t('fconn.pause')}</button>
          : <button type="button" className="secondary" disabled={busy} onClick={() => void act('resume')}><Play aria-hidden="true" />{t('fconn.resume')}</button>}
        <button type="button" className="secondary fconnDanger" disabled={busy} onClick={() => setConfirm('revoke')}><Trash2 aria-hidden="true" />{t('fconn.revoke')}</button>
        <button type="button" className="secondary fconnPushEnd" onClick={() => setLog(true)}><ScrollText aria-hidden="true" />{t('fconn.log')}</button>
      </div>
    </> : <Empty icon={<Smartphone aria-hidden="true" />} title={t('fsms.devices.none')} text={status.ready ? t('fsms.devices.noneText') : t('fsms.devices.needCheck')}
      action={<button type="button" onClick={() => onTab('sms')}>{t('fconn.setUp')}<ArrowRight aria-hidden="true" className="fconnFlip" /></button>} />)}
    {error && <p className="cmFieldError" role="alert">{error}</p>}
    {issued && <TokenDialog issued={issued} url={url} onClose={() => setIssued(null)} />}
    {confirm && device && <ConfirmDialog title={t(confirm === 'rotate' ? 'fconn.rotateTitle' : 'fsms.device.revokeTitle')} confirmLabel={t(confirm === 'rotate' ? 'fconn.rotate' : 'fsms.device.revoke')}
      danger={confirm === 'revoke'} busy={busy} onConfirm={() => void act(confirm)} onCancel={() => setConfirm(null)}>
      <p dir="auto">{confirm === 'rotate' ? t('fconn.rotateText', { name: device.name }) : t('fsms.device.revokeText', { name: device.name })}</p></ConfirmDialog>}
    {log && <SecurityLog devices={devices.kind === 'ready' ? devices.value : []} onClose={() => setLog(false)} />}
  </div>;
}

/** Refused and failed requests to the bank SMS and Apple Pay addresses (`GET /integrations/iphone/integrations/{key}/events`). */
function SecurityLog({ devices, onClose }: { devices: FinanceSmsDevice[]; onClose: () => void }) {
  const [key, setKey] = useState<'finance.sms' | 'finance.wallet'>('finance.sms');
  const [items, setItems] = useState<IphoneEvent[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const names = Object.fromEntries(devices.map((d) => [d.id, d.name]));
  const more = useCallback((offset: number) => {
    setError(null);
    iphone.events(key, offset, 20).then((page) => { setItems((old) => [...(offset ? old ?? [] : []), ...page.items]); setTotal(page.total); }, (e) => setError(describeError(e)));
  }, [key]);
  useEffect(() => { setItems(null); more(0); }, [more]);
  return <Modal title={t('fconn.log.title')} onClose={onClose} className="fconnLog">
    <p className="muted">{t('fconn.log.intro')}</p>
    <div className="fconnSegment" role="group" aria-label={t('fconn.log.which')}>
      {(['finance.sms', 'finance.wallet'] as const).map((k) => <button key={k} type="button" aria-pressed={key === k} onClick={() => setKey(k)}>{scopeLabel(k)}</button>)}
    </div>
    {error && <LoadError message={error} retry={() => more(0)} />}
    {!error && items === null && <Notice busy>{t('fconn.loading')}</Notice>}
    {items !== null && (items.length === 0 ? <p className="muted">{t('fconn.log.none')}</p> : <div className="cmTableWrap" tabIndex={0}><table className="cmTable">
      <thead><tr><th scope="col">{t('fconn.log.when')}</th><th scope="col">{t('fconn.device')}</th><th scope="col">{t('fconn.log.outcome')}</th></tr></thead>
      <tbody>{items.map((e) => <tr key={e.id}><td>{when(e.created_at)}</td><td dir="auto">{names[e.device_id] ?? '-'}</td><td>{outcomeLabel(e.outcome)}: {codeLabel(e.code)}</td></tr>)}</tbody>
    </table></div>)}
    {items !== null && items.length < total && <button type="button" className="secondary" onClick={() => more(items.length)}>{t('iph.events.more')}</button>}
    <div className="actions"><button type="button" onClick={onClose}>{t('fconn.close')}</button></div>
  </Modal>;
}

const STATUS_TONE: Record<string, string> = { posted: 'success', attached: 'success', review: 'warning', failed: 'danger', duplicate: 'neutral', dismissed: 'neutral', rejected: 'neutral' };

function RecentMessages({ onTab }: { onTab: (tab: ConnectionsTab) => void }) {
  const [page, reload] = useLoad(useCallback(() => sms.smsMessages('', RECENT_MESSAGES, 0), []));
  return <section className="fconnRecent" aria-labelledby="fconn-recent">
    <div className="sectionHeader"><h3 id="fconn-recent">{t('fconn.recent.title', { count: page.kind === 'ready' ? page.value.total : 0 })}</h3>
      <button type="button" className="cmLinkButton" onClick={() => onTab('sms')}>{t('fconn.recent.all')}<ArrowRight aria-hidden="true" className="fconnFlip" /></button></div>
    {page.kind === 'loading' && <Notice busy>{t('fconn.loading')}</Notice>}
    {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={page.message} retry={reload} />}
    {page.kind === 'ready' && (page.value.items.length === 0 ? <p className="muted">{t('fconn.recent.none')}</p>
      : <div className="cmTableWrap" tabIndex={0}><table className="cmTable fconnTable">
        <thead><tr><th scope="col">{t('fconn.recent.status')}</th><th scope="col">{t('fconn.recent.merchant')}</th><th scope="col">{t('fconn.recent.when')}</th>
          <th scope="col" className="num">{t('fconn.recent.amount')}</th><th scope="col">{t('fconn.recent.reading')}</th></tr></thead>
        <tbody>{page.value.items.map((m: FinanceSmsMessage) => <tr key={m.id}>
          <td><span className={`statusBadge ${STATUS_TONE[m.status] ?? 'neutral'}`}>{t(`fsms.status.${m.status}` as MessageKey)}</span></td>
          <td><strong dir="auto">{m.parsed.merchant || m.sender}</strong>{m.parsed.merchant && <small className="muted" dir="ltr">{m.sender}</small>}</td>
          <td>{short(m.received_at)}</td>
          <td className="num">{m.parsed.amount && m.parsed.currency ? <Money value={m.parsed.amount} currency={m.parsed.currency} /> : '-'}</td>
          <td><span>{m.parsed.kind ? kindLabel(m.parsed.kind) : '-'}</span>{m.reason && <small className="muted">{reasonLabel(m.reason)}</small>}</td>
        </tr>)}</tbody></table></div>)}
  </section>;
}

/** The real review settings: record confident messages on their own (`auto_post`) and how long the masked text is kept. */
function ReviewPolicy({ settings, onSaved }: { settings: FinanceSmsSettings; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (body: { auto_post?: boolean; body_retention_days?: number }) => {
    setBusy(true); setError(null);
    try { await sms.saveSmsSettings({ version: settings.version, ...body }); onSaved(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const erase = settings.body_retention_days === 0;
  return <section className="fconnPolicy" aria-labelledby="fconn-policy"><h3 id="fconn-policy">{t('fconn.policy.title')}</h3>
    <Toggle label={t('fconn.policy.alwaysReview')} hint={t('fconn.policy.alwaysReviewHint')} on={!settings.auto_post} busy={busy} onChange={(on) => void save({ auto_post: !on })} />
    <Toggle label={t('fconn.policy.erase')} hint={erase ? t('fconn.policy.eraseOn') : t('fconn.policy.eraseOff', { count: settings.body_retention_days })} on={erase} busy={busy}
      onChange={(on) => void save({ body_retention_days: on ? 0 : 30 })} />
    <p className="muted fconnNote">{settings.ai.enabled ? t('fconn.policy.aiOn') : t('fconn.policy.aiOff')}</p>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
  </section>;
}

function Toggle({ label, hint, on, busy, onChange }: { label: string; hint: string; on: boolean; busy: boolean; onChange: (on: boolean) => void }) {
  const hintId = useId();
  return <div className="fconnToggle"><span><span>{label}</span><small className="muted" id={hintId}>{hint}</small></span>
    <button type="button" role="switch" aria-checked={on} aria-label={label} aria-describedby={hintId} disabled={busy} onClick={() => onChange(!on)}><span aria-hidden="true" /></button></div>;
}

// ---- import history --------------------------------------------------------------------------

function ImportHistory({ accounts, onOpen, onChanged }: { accounts: FinanceAccount[]; onOpen: (tab: FinanceTab) => void; onChanged: () => void }) {
  const [offset, setOffset] = useState(0);
  const [page, reload] = useLoad(useCallback(() => listImports(IMPORTS_PAGE, offset), [offset]));
  const [undoing, setUndoing] = useState<FinanceImport | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const names = Object.fromEntries(accounts.map((a) => [a.id, a.name]));
  const undo = async () => {
    if (!undoing) return;
    setBusy(true);
    try { await deleteImport(undoing.id); setNotice({ text: t('finance.import.undone', { file: undoing.filename }) }); reload(); onChanged(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(false); setUndoing(null); }
  };
  return <section className="surface cmPanel" aria-labelledby="fconn-imports">
    <div className="sectionHeader"><h2 id="fconn-imports">{t('fconn.imports.title')}</h2>
      <button type="button" className="cmPrimary" onClick={() => onOpen('import')}><FileUp aria-hidden="true" />{t('fconn.imports.new')}</button></div>
    <p className="muted">{t('fconn.imports.intro')}</p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {page.kind === 'loading' && <Notice busy>{t('fconn.loading')}</Notice>}
    {page.kind === 'denied' && <Denied />}
    {page.kind === 'error' && <LoadError message={page.message} retry={reload} />}
    {page.kind === 'ready' && (page.value.items.length === 0
      ? <Empty icon={<FileUp aria-hidden="true" />} title={t('fconn.imports.none')} text={t('fconn.imports.noneText')}
        action={<button type="button" onClick={() => onOpen('import')}><FileUp aria-hidden="true" />{t('fconn.imports.new')}</button>} />
      : <>
        <div className="cmTableWrap" tabIndex={0}><table className="cmTable fconnTable">
          <thead><tr><th scope="col">{t('fconn.imports.file')}</th><th scope="col">{t('fconn.imports.format')}</th><th scope="col">{t('fconn.imports.account')}</th>
            <th scope="col">{t('fconn.imports.when')}</th><th scope="col" className="num">{t('fconn.imports.added')}</th><th scope="col" className="num">{t('fconn.imports.duplicates')}</th>
            <th scope="col" className="num">{t('fconn.imports.reconciled')}</th><th scope="col" className="num">{t('fconn.imports.review')}</th><th scope="col"><span className="visuallyHidden">{t('fconn.imports.actions')}</span></th></tr></thead>
          <tbody>{page.value.items.map((i) => <tr key={i.id}>
            <td><strong dir="auto">{i.filename}</strong>{i.error_count > 0 && <small className="finLoosens">{t('fconn.imports.errors', { count: i.error_count })}</small>}</td>
            <td><span className={`statusBadge ${i.preset.startsWith('pdf') ? 'success' : 'neutral'}`}>{importFormat(i.preset)}</span></td>
            <td dir="auto">{names[i.account_id] ?? '-'}</td>
            <td>{when(i.created_at)}</td>
            <td className="num" dir="ltr">{i.imported_count}/{i.row_count}</td>
            <td className="num" dir="ltr">{i.duplicate_count}</td>
            <td className="num" dir="ltr">{i.reconciled ?? 0}</td>
            <td className="num" dir="ltr">{i.held_for_review ?? 0}</td>
            <td><button type="button" className="secondary" onClick={() => setUndoing(i)} aria-label={t('fconn.imports.undoFor', { file: i.filename })}><Trash2 aria-hidden="true" />{t('fconn.imports.undo')}</button></td>
          </tr>)}</tbody></table></div>
        <div className="cmPager"><button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - IMPORTS_PAGE))}>{t('fsms.prev')}</button>
          <span className="muted cmPagerText">{t('fsms.range', { from: offset + 1, to: Math.min(offset + IMPORTS_PAGE, page.value.total), total: page.value.total })}</span>
          <button type="button" className="secondary" disabled={offset + IMPORTS_PAGE >= page.value.total} onClick={() => setOffset(offset + IMPORTS_PAGE)}>{t('fsms.next')}</button></div>
      </>)}
    {undoing && <ConfirmDialog title={t('fconn.imports.undoTitle')} confirmLabel={t('fconn.imports.undo')} danger busy={busy} onConfirm={() => void undo()} onCancel={() => setUndoing(null)}>
      <p dir="auto">{t('fconn.imports.undoText', { file: undoing.filename, count: undoing.imported_count })}</p></ConfirmDialog>}
  </section>;
}

// ---- permissions -----------------------------------------------------------------------------

function Permissions({ settings, overview, onTab }: { settings: Load<FinanceSmsSettings>; overview: Load<IphoneOverview>; onTab: (tab: ConnectionsTab) => void }) {
  const s = ready(settings);
  const devices = overview.kind === 'ready' ? overview.value.devices.filter((d) => d.status !== 'revoked') : [];
  return <div className="fconnPerms">
    <section className="surface cmPanel" aria-labelledby="fconn-can">
      <h2 id="fconn-can"><ShieldCheck aria-hidden="true" className="cmInlineIcon" />{t('fconn.perm.title')}</h2>
      <p className="fconnReadOnly"><Info aria-hidden="true" />{t('fconn.readOnly')}</p>
      <ul className="fconnPermList">
        {(['read', 'manage', 'own', 'bank', 'ai', 'retention'] as const).map((key) => <li key={key}>
          <strong>{t(`fconn.perm.${key}` as MessageKey)}</strong>
          <span className="muted">{key === 'ai' ? (s?.ai.enabled ? t('fconn.perm.aiOn', { date: when(s.ai.opted_in_at) }) : t('fconn.perm.aiOff'))
            : key === 'retention' ? (s ? retentionText(s.body_retention_days) : '-') : t(`fconn.perm.${key}Text` as MessageKey)}</span></li>)}
      </ul>
    </section>
    <section className="surface cmPanel" aria-labelledby="fconn-tokens">
      <div className="sectionHeader"><h2 id="fconn-tokens">{t('fconn.perm.devices')}</h2>
        <button type="button" className="secondary" onClick={() => onTab('shortcuts')}>{t('fconn.perm.manageDevices')}</button></div>
      <p className="muted">{t('fconn.perm.devicesText')}</p>
      {overview.kind === 'loading' && <Notice busy>{t('fconn.loading')}</Notice>}
      {(overview.kind === 'error' || overview.kind === 'denied') && <p className="muted">{overview.kind === 'denied' ? t('iph.denied') : t('iph.error.load', { message: overview.message })}</p>}
      {overview.kind === 'ready' && (devices.length === 0 ? <p className="muted">{t('fsms.devices.none')}</p>
        : <div className="cmTableWrap" tabIndex={0}><table className="cmTable">
          <thead><tr><th scope="col">{t('fconn.device')}</th><th scope="col">{t('fconn.deviceStatus')}</th><th scope="col">{t('fconn.perm.scopes')}</th><th scope="col">{t('fconn.token')}</th><th scope="col">{t('fconn.lastCheckIn')}</th></tr></thead>
          <tbody>{devices.map((d) => <tr key={d.id}><td dir="auto">{d.name}</td><td>{t(`fsms.device.${d.status}` as MessageKey)}</td>
            <td>{d.scopes.map((sc) => scopeLabel(sc)).join(', ')}</td><td dir="ltr">{maskedToken(d.token_hint)}</td><td>{when(d.last_seen_at)}</td></tr>)}</tbody></table></div>)}
    </section>
  </div>;
}
