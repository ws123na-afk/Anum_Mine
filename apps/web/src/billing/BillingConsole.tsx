// The platform owner's billing console (docs/billing.md#platform-console-platform_operator): revenue
// summary, every customer subscription with search, status filter and paging, one organisation's
// billing and invoices, the plan catalog, and the issuer's settings (bank details, tax, dunning,
// Stripe). Only platform operators; anyone else gets the API's refusal.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Lock, Play, Plus, ReceiptText, RefreshCw } from 'lucide-react';
import type { OrganisationSummary } from '@anum/contracts';
import * as b from '../lib/billingApi';
import { getOperatedOrganisations } from '../lib/teamApi';
import {
  PAGE_SIZE, SUBSCRIPTION_STATUSES, formatMoney, subscriptionTone,
  type AdminPlan, type ConsoleSubscription, type ConsoleSummary, type Page,
} from '../lib/billing';
import { figure } from '../lib/pageSummary';
import { describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { Modal } from '../Dialog';
import { PageSummary } from '../PageParts';
import { Badge, Empty, Note, Pager, date } from './parts';
import { ConsoleOrganisation } from './ConsoleOrganisation';
import { ConsolePlans } from './ConsolePlans';
import { ConsoleSettings } from './ConsoleSettings';

type Tab = 'subscriptions' | 'plans' | 'settings';
const TABS: Tab[] = ['subscriptions', 'plans', 'settings'];
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied' | 'error'; message: string };
const failed = (e: unknown): Load<never> => ({ state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e) });

export function BillingConsole() {
  const [tab, setTab] = useState<Tab>('subscriptions');
  const [summary, setSummary] = useState<Load<ConsoleSummary>>({ state: 'loading' });
  const [org, setOrg] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [running, setRunning] = useState(false), [starting, setStarting] = useState(false), [tick, setTick] = useState(0);
  const loadSummary = useCallback(async () => { try { setSummary({ state: 'ready', value: await b.getSummary() }); } catch (e) { setSummary(failed(e)); } }, []);
  useEffect(() => { void loadSummary(); }, [loadSummary, tick]);
  const refresh = () => setTick((n) => n + 1);
  const run = async () => {
    setRunning(true); setFlash(null);
    try { const r = await b.runBilling(); setFlash({ text: t('billing.console.ran', { organisations: r.organisations, invoices: r.invoices, reminders: r.reminders, readOnly: r.read_only, failed: r.failed }), error: r.failed > 0 }); refresh(); }
    catch (e) { setFlash({ text: t('billing.failed', { message: describeError(e) }), error: true }); }
    finally { setRunning(false); }
  };

  if (summary.state === 'denied') return <section className="surface adminForbidden blPanel" aria-labelledby="console-denied">
    <span className="adminIcon"><Lock aria-hidden="true" /></span>
    <div><h2 id="console-denied">{t('billing.console.deniedTitle')}</h2><p>{t('billing.console.deniedText')}</p><p className="muted">{summary.message}</p></div>
  </section>;
  const s = summary.state === 'ready' ? summary.value : null, cur = s?.currency ?? 'SAR';
  const load = { state: summary.state === 'ready' ? 'ready' as const : summary.state === 'loading' ? 'loading' as const : 'error' as const };
  const figures = [
    figure('mrr', t('billing.console.mrr'), load, () => ({ value: formatMoney(s!.mrr_minor, cur), hint: t('billing.console.arr', { amount: formatMoney(s!.arr_minor, cur) }) })),
    figure('subs', t('billing.console.active'), load, () => ({ value: String(s!.counts.active + s!.counts.trialing), hint: t('billing.console.trialing', { count: s!.counts.trialing }) })),
    figure('open', t('billing.console.open'), load, () => ({ value: formatMoney(s!.open_minor, cur), hint: t('billing.console.openInvoices', { count: s!.open_invoices }) })),
    figure('overdue', t('billing.console.overdue'), load, () => ({ value: formatMoney(s!.overdue_minor, cur), hint: t('billing.console.pastDue', { count: s!.counts.past_due, readOnly: s!.read_only }), tone: s!.overdue_minor ? 'danger' : 'ok' })),
    figure('collected', t('billing.console.collected'), load, () => ({ value: formatMoney(s!.collected_this_month_minor, cur) })),
  ];
  return <div className="blConsole">
    <PageSummary label={t('billing.console.summary')} figures={figures} onRetry={() => void loadSummary()} />
    <div className="blConsoleBar">
      <nav className="settingsTabs blTabs" aria-label={t('billing.console.sections')}>{TABS.map((id) => <button key={id} type="button" aria-current={tab === id && !org ? 'page' : undefined} onClick={() => { setTab(id); setOrg(null); }}>{t(`billing.console.tab.${id}` as MessageKey)}</button>)}</nav>
      <div className="actions blInline">
        <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setStarting(true)}><Plus aria-hidden="true" />{t('billing.console.start')}</button>
        <button type="button" disabled={running} onClick={() => void run()} aria-describedby="console-run-hint"><Play aria-hidden="true" />{running ? t('billing.console.running') : t('billing.console.run')}</button>
      </div>
    </div>
    <p className="muted blSmall" id="console-run-hint">{t('billing.console.runHint')}</p>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    {org ? <ConsoleOrganisation tenantId={org} onBack={() => setOrg(null)} onChanged={refresh} />
      : tab === 'subscriptions' ? <Subscriptions key={tick} onOpen={setOrg} onStart={() => setStarting(true)} />
      : tab === 'plans' ? <ConsolePlans /> : <ConsoleSettings />}
    {starting && <StartDialog onClose={() => setStarting(false)} onStarted={(name, tenantId) => { setStarting(false); setFlash({ text: t('billing.console.started', { name }) }); refresh(); setOrg(tenantId); }} />}
  </div>;
}

function Subscriptions({ onOpen, onStart }: { onOpen: (tenantId: string) => void; onStart: () => void }) {
  const [page, setPage] = useState<Load<Page<ConsoleSubscription>>>({ state: 'loading' });
  const [query, setQuery] = useState(''), [status, setStatus] = useState(''), [offset, setOffset] = useState(0);
  const searchId = useId(), statusId = useId();
  const load = useCallback(async () => {
    setPage({ state: 'loading' });
    try { setPage({ state: 'ready', value: await b.listSubscriptions({ q: query, status, limit: PAGE_SIZE, offset }) }); } catch (e) { setPage(failed(e)); }
  }, [query, status, offset]);
  useEffect(() => { const timer = setTimeout(() => void load(), query ? 250 : 0); return () => clearTimeout(timer); }, [load, query]);
  const filtered = Boolean(query.trim() || status);
  return <section className="surface blPanel" aria-labelledby="console-subs-title">
    <div className="blHead"><div><h2 id="console-subs-title">{t('billing.console.subscriptions')}</h2><p className="muted">{t('billing.console.subscriptionsLead')}</p></div>
      <button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('billing.refresh')}</button></div>
    <div className="blToolbar" role="search">
      <label className="field blSearch" htmlFor={searchId}><span>{t('billing.console.search')}</span><input id={searchId} type="search" value={query} onChange={(e) => { setQuery(e.target.value); setOffset(0); }} /></label>
      <label className="field blFilter" htmlFor={statusId}><span>{t('billing.console.statusFilter')}</span>
        <select id={statusId} value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          <option value="">{t('billing.all')}</option>{SUBSCRIPTION_STATUSES.map((v) => <option key={v} value={v}>{t(`billing.status.${v}` as MessageKey)}</option>)}<option value="read_only">{t('billing.console.readOnly')}</option>
        </select></label>
    </div>
    {page.state === 'loading' && <Note>{t('billing.console.loadingSubs')}</Note>}
    {(page.state === 'error' || page.state === 'denied') && <div className="blRetry"><Note error>{t('billing.console.subsFailed', { message: page.message })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {page.state === 'ready' && (page.value.items.length === 0
      ? <Empty icon={<ReceiptText aria-hidden="true" />} title={filtered ? t('billing.console.noMatch') : t('billing.console.none')} text={filtered ? t('billing.console.noMatchText') : t('billing.console.noneText')}
        action={filtered ? <button type="button" className="secondary" onClick={() => { setQuery(''); setStatus(''); }}>{t('billing.clearFilter')}</button> : <button type="button" onClick={onStart}><Plus aria-hidden="true" />{t('billing.console.start')}</button>} />
      : <div className="pane dataTableWrap"><table className="dataTable blTable">
        <caption className="visuallyHidden">{t('billing.console.subscriptions')}</caption>
        <thead><tr><th scope="col">{t('billing.col.organisation')}</th><th scope="col" className="optional">{t('billing.col.plan')}</th><th scope="col">{t('billing.col.status')}</th>
          <th scope="col" className="num">{t('billing.col.mrr')}</th><th scope="col" className="num optional">{t('billing.col.balance')}</th><th scope="col" className="optional">{t('billing.col.periodEnd')}</th>
          <th scope="col"><span className="visuallyHidden">{t('billing.col.actions')}</span></th></tr></thead>
        <tbody>{page.value.items.map((row) => <tr key={row.tenant_id}>
          <th scope="row"><strong dir="auto">{row.display_name}</strong><small dir="ltr">{row.tenant_id}</small></th>
          <td dir="auto" className="optional">{row.plan_name}</td>
          <td><Badge tone={subscriptionTone(row.status)}>{t(`billing.status.${row.status}` as MessageKey)}</Badge>{row.state === 'read_only' && <small className="blDanger">{t('billing.console.readOnly')}</small>}</td>
          <td className="num">{formatMoney(row.mrr_minor, row.currency)}</td>
          <td className={`num optional ${row.open_balance_minor ? 'blDanger' : ''}`}>{formatMoney(row.open_balance_minor, row.currency)}</td>
          <td className="optional nowrap">{date(row.current_period_end)}</td>
          <td><div className="cellActions"><button type="button" className="secondary" onClick={() => onOpen(row.tenant_id)} aria-label={t('billing.console.openOf', { name: row.display_name })}>{t('billing.console.openOrg')}</button></div></td>
        </tr>)}</tbody>
      </table></div>)}
    {page.state === 'ready' && <Pager total={page.value.total} offset={offset} shown={page.value.items.length} onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))} onNext={() => setOffset(offset + PAGE_SIZE)} />}
  </section>;
}

/** Start billing an organisation the operator onboarded: plan, trial days and seats. */
function StartDialog({ onClose, onStarted }: { onClose: () => void; onStarted: (name: string, tenantId: string) => void }) {
  const [orgs, setOrgs] = useState<OrganisationSummary[] | null>(null), [plans, setPlans] = useState<AdminPlan[] | null>(null);
  const [tenantId, setTenantId] = useState(''), [planId, setPlanId] = useState(''), [trial, setTrial] = useState(''), [seats, setSeats] = useState('1');
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  useEffect(() => {
    void getOperatedOrganisations().then(setOrgs).catch((e) => { setOrgs([]); setProblem(t('billing.failed', { message: describeError(e) })); });
    void b.listCatalog().then((list) => setPlans(list.filter((p) => p.active))).catch((e) => { setPlans([]); setProblem(t('billing.failed', { message: describeError(e) })); });
  }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trialDays = trial.trim() ? Number(trial) : undefined, seatCount = Number(seats);
    if (!tenantId || !planId) { setProblem(t('billing.console.startPick')); return; }
    if (trialDays !== undefined && (!Number.isInteger(trialDays) || trialDays < 0 || trialDays > 365)) { setProblem(t('billing.console.trialRange')); return; }
    if (!Number.isInteger(seatCount) || seatCount < 1) { setProblem(t('billing.console.seatsRange')); return; }
    setBusy(true); setProblem(null);
    try { await b.startSubscription(tenantId, { plan_id: planId, seats: seatCount, ...(trialDays === undefined ? {} : { trial_days: trialDays }) }); onStarted(orgs?.find((o) => o.tenant_id === tenantId)?.display_name ?? tenantId, tenantId); }
    catch (e) { setProblem(t('billing.failed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <Modal title={t('billing.console.start')} className="formDialog" onClose={onClose}>
    <form onSubmit={(e) => void submit(e)} noValidate>
      <p className="muted">{t('billing.console.startLead')}</p>
      {(orgs === null || plans === null) && <Note>{t('common.loading')}</Note>}
      {orgs && orgs.length === 0 && <Note>{t('billing.console.noOrgs')}</Note>}
      {plans && plans.length === 0 && <Note>{t('billing.console.noPlansYet')}</Note>}
      <label className="field" htmlFor={`${formId}-org`}><span>{t('billing.col.organisation')}</span>
        <select id={`${formId}-org`} value={tenantId} onChange={(e) => setTenantId(e.target.value)} required><option value="">{t('billing.console.pickOrg')}</option>{(orgs ?? []).map((o) => <option key={o.tenant_id} value={o.tenant_id}>{o.display_name} ({o.tenant_id})</option>)}</select></label>
      <label className="field" htmlFor={`${formId}-plan`}><span>{t('billing.col.plan')}</span>
        <select id={`${formId}-plan`} value={planId} onChange={(e) => setPlanId(e.target.value)} required><option value="">{t('billing.console.pickPlan')}</option>{(plans ?? []).map((p) => <option key={p.id} value={p.id}>{p.name} ({formatMoney(p.price_minor, p.currency)})</option>)}</select></label>
      <div className="blTwo">
        <label className="field" htmlFor={`${formId}-trial`}><span>{t('billing.console.trialDays')}</span><input id={`${formId}-trial`} type="number" min={0} max={365} inputMode="numeric" value={trial} placeholder={t('billing.console.trialDefault')} onChange={(e) => setTrial(e.target.value)} /></label>
        <label className="field" htmlFor={`${formId}-seats`}><span>{t('billing.console.seats')}</span><input id={`${formId}-seats`} type="number" min={1} inputMode="numeric" value={seats} onChange={(e) => setSeats(e.target.value)} /></label>
      </div>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy || !orgs?.length || !plans?.length}>{busy ? t('common.loading') : t('billing.console.startConfirm')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}
