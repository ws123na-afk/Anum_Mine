// The tabs of the Integrations page beyond Connected apps (docs/integrations-setup.md#the-integrations-page):
// the KPI row, Data sync (connected accounts per integration), API and webhooks (callback addresses
// and the event services), Permissions (credential, scopes and expiry per service) and Logs (the
// integration audit trail). Every figure and row comes from the API; nothing here is a sample.
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, Copy, History, KeyRound, Link2, Plug, RefreshCw, ShieldCheck, Webhook } from 'lucide-react';
import type { AuditRecord } from '@anum/contracts';
import { getAuditPage } from './lib/adminApi';
import type { IntegrationState } from './lib/catalog';
import { integrationBadge } from './lib/governance';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import type { SetupItem } from './lib/integrationSetup';
import {
  callbackRows, eventServices, hubFigures, logActionLabel, logOutcomeLabel, logTargetName, permissionRows, sourceLabel, syncRows, type ExpiryState, type SyncState,
} from './lib/integrationsHub';
import { describeError, isPermissionDenied } from './lib/errors';
import { Empty, LoadError, Notice } from './FinanceParts';
import { FinBanner, FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';

type Chip = 'ok' | 'warn' | 'bad' | 'info' | 'muted';
const when = (iso: string) => formatDateTime(iso);

/** The four figures over every tab: connected, health, issues and the latest check. */
export function HubKpis({ items, services, loading }: { items: readonly SetupItem[]; services: readonly IntegrationState[]; loading: boolean }) {
  const f = hubFigures(items, services);
  const wait = <span className="muted">{t('intHub.loading')}</span>;
  const empty = loading && !items.length && !services.length;
  return <KpiRow label={t('intHub.kpis')}>
    <KpiCard icon={<Link2 />} label={t('intHub.kpi.connected')} value={empty ? wait : formatNumber(f.connected)} hint={empty ? undefined : t('intHub.kpi.connectedHint', { count: f.inUse })} tone={f.connected ? 'good' : ''} />
    <KpiCard icon={<Activity />} label={t('intHub.kpi.health')} value={empty ? wait : f.healthPercent === null ? '-' : t('intHub.kpi.pct', { pct: f.healthPercent })}
      hint={empty ? undefined : t('intHub.kpi.healthHint')} tone={f.healthPercent === null ? '' : f.healthPercent >= 99 ? 'good' : 'warn'} />
    <KpiCard icon={<AlertTriangle />} label={t('intHub.kpi.issues')} value={empty ? wait : formatNumber(f.issues)}
      hint={empty ? undefined : f.issues ? t('intHub.kpi.issuesHint') : t('intHub.kpi.issuesNone', { count: f.toSetUp })} tone={f.issues ? 'bad' : ''} />
    <KpiCard icon={<RefreshCw />} label={t('intHub.kpi.lastCheck')} value={empty ? wait : f.lastChecked ? <span dir="auto">{formatDateTime(f.lastChecked, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span> : '-'}
      hint={empty ? undefined : f.lastChecked ? t('intHub.kpi.lastCheckHint') : t('intHub.kpi.lastCheckNone')} />
  </KpiRow>;
}

const syncChip: Record<SyncState, Chip> = { ok: 'ok', failing: 'bad', none: 'muted' };

/** Data sync: integrations that keep connected accounts, how many there are and how many fail. */
export function SyncTab({ items, setupState, onOpen }: { items: readonly SetupItem[]; setupState: string; onOpen: (id: string) => void }) {
  const rows = syncRows(items);
  const total = rows.reduce((n, r) => n + r.connections, 0), failing = rows.reduce((n, r) => n + r.failing, 0);
  return <FinCard title={t('intHub.sync.title')} icon={<RefreshCw />} subtitle={t('intHub.sync.sub')}>
    {setupState === 'loading' && <Notice busy>{t('intHub.loading')}</Notice>}
    {setupState === 'denied' && <Notice>{t('setup.forbidden')}</Notice>}
    {setupState === 'ready' && rows.length === 0 && <Empty icon={<Plug aria-hidden="true" />} title={t('intHub.sync.emptyTitle')} text={t('intHub.sync.emptyText')} />}
    {rows.length > 0 && <>
      {failing > 0 ? <FinBanner tone="warn" icon={<AlertTriangle />} title={t('intHub.sync.failingTitle', { count: failing })}>{t('intHub.sync.failingText')}</FinBanner>
        : total > 0 ? <FinBanner tone="ok" icon={<CheckCircle2 />} title={t('intHub.sync.allOk', { count: total })} /> : null}
      <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('intHub.sync.title')}>
        <table className="finGrid intHubTable">
          <thead><tr><th scope="col">{t('intHub.col.integration')}</th><th scope="col" className="num">{t('intHub.col.accounts')}</th><th scope="col" className="num">{t('intHub.col.failing')}</th><th scope="col">{t('intHub.col.status')}</th><th scope="col"><span className="visuallyHidden">{t('intHub.col.actions')}</span></th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id} data-sync-row={row.id}>
            <th scope="row">{row.name}</th>
            <td className="num">{formatNumber(row.connections)}</td>
            <td className="num">{formatNumber(row.failing)}</td>
            <td><StatusChip tone={syncChip[row.state]}>{t(`intHub.sync.state.${row.state}`)}</StatusChip></td>
            <td><button type="button" className="secondary intHubSmall" aria-haspopup="dialog" aria-label={t('intHub.openFor', { name: row.name })} onClick={() => onOpen(row.id)}>{t('intHub.open')}</button></td>
          </tr>)}</tbody>
        </table>
      </div>
    </>}
  </FinCard>;
}

/** API and webhooks: the addresses to give each provider, and the services that carry events. */
export function WebhooksTab({ items, services, servicesFailed = false, setupState, canViewSetup, onCopy, onOpenService, flash }: {
  items: readonly SetupItem[]; services: readonly IntegrationState[]; servicesFailed?: boolean; setupState: string; canViewSetup: boolean;
  onCopy: (value: string) => void; onOpenService: (id: string) => void; flash: { text: string; error?: boolean } | null;
}) {
  const urls = callbackRows(items);
  const events = eventServices(services);
  return <div className="intHubGrid">
    <FinCard title={t('intHub.hooks.title')} icon={<Webhook />} subtitle={t('intHub.hooks.sub')}>
      {setupState === 'loading' && <Notice busy>{t('intHub.loading')}</Notice>}
      {setupState === 'denied' && <Notice>{t('setup.forbidden')}</Notice>}
      {setupState === 'ready' && !canViewSetup && <Notice>{t('intHub.hooks.ownersOnly')}</Notice>}
      {setupState === 'ready' && canViewSetup && urls.length === 0 && <Empty icon={<Link2 aria-hidden="true" />} title={t('intHub.hooks.emptyTitle')} text={t('intHub.hooks.emptyText')} />}
      {urls.length > 0 && <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('intHub.hooks.title')}>
        <table className="finGrid intHubTable">
          <thead><tr><th scope="col">{t('intHub.col.integration')}</th><th scope="col">{t('intHub.col.address')}</th><th scope="col"><span className="visuallyHidden">{t('intHub.col.actions')}</span></th></tr></thead>
          <tbody>{urls.map((row) => <tr key={`${row.id}:${row.url}`}>
            <th scope="row">{row.name}</th>
            <td><code className="intHubUrl" dir="ltr">{row.url}</code></td>
            <td><button type="button" className="secondary intHubSmall" aria-label={t('intHub.hooks.copyFor', { name: row.name })} onClick={() => onCopy(row.url)}><Copy aria-hidden="true" />{t('intHub.hooks.copy')}</button></td>
          </tr>)}</tbody>
        </table>
      </div>}
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    </FinCard>
    <FinCard title={t('intHub.events.title')} icon={<Activity />} subtitle={t('intHub.events.sub')}>
      {events.length === 0 ? (servicesFailed ? null : <Empty icon={<Webhook aria-hidden="true" />} title={t('intHub.events.emptyTitle')} text={t('intHub.events.emptyText')} />)
        : <ul className="intHubList">{events.map((s) => {
          const badge = integrationBadge(s.status);
          return <li key={s.id} data-event-service={s.id}>
            <div><strong>{s.name}</strong><small className="muted" dir="ltr">{s.endpoint}</small>
              <small className="muted">{s.checked_at ? (s.latency_ms === null ? t('intHub.events.checkedNoMs', { when: when(s.checked_at) }) : t('intHub.events.checked', { when: when(s.checked_at), ms: s.latency_ms })) : t('intHub.events.notChecked')}</small></div>
            <span className={`statusBadge ${badge.tone}`}>{badge.label}</span>
            <button type="button" className="secondary intHubSmall" aria-haspopup="dialog" aria-label={t('intHub.openFor', { name: s.name })} onClick={() => onOpenService(s.id)}>{t('intHub.open')}</button>
          </li>;
        })}</ul>}
    </FinCard>
  </div>;
}

const expiryChip: Record<ExpiryState, Chip> = { none: 'muted', ok: 'ok', soon: 'warn', expired: 'bad' };

/** Permissions: each service's credential source, scopes and expiry, and whether a workspace token is stored. */
export function PermissionsTab({ services, loading, failed = false, onOpen }: { services: readonly IntegrationState[]; loading: boolean; failed?: boolean; onOpen: (id: string) => void }) {
  const rows = permissionRows(services);
  const scoped = rows.filter((r) => r.configured);
  const expiring = rows.filter((r) => r.expiry === 'soon' || r.expiry === 'expired').length;
  return <FinCard title={t('intHub.perm.title')} icon={<ShieldCheck />} subtitle={t('intHub.perm.sub')}>
    {loading && !rows.length && <Notice busy>{t('intHub.loading')}</Notice>}
    {!loading && !failed && !rows.length && <Empty icon={<KeyRound aria-hidden="true" />} title={t('intHub.perm.emptyTitle')} text={t('intHub.perm.emptyText')} />}
    {rows.length > 0 && <>
      <FinBanner tone={expiring ? 'warn' : 'ok'} icon={expiring ? <AlertTriangle /> : <ShieldCheck />}
        title={expiring ? t('intHub.perm.expiring', { count: expiring }) : t('intHub.perm.noneExpiring')}>
        {t('intHub.perm.summary', { scoped: scoped.length, total: rows.length })}
      </FinBanner>
      <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('intHub.perm.title')}>
        <table className="finGrid intHubTable">
          <thead><tr><th scope="col">{t('intHub.col.integration')}</th><th scope="col">{t('intHub.col.credential')}</th><th scope="col">{t('intHub.col.scopes')}</th>
            <th scope="col">{t('intHub.col.expiry')}</th><th scope="col">{t('intHub.col.workspaceToken')}</th><th scope="col"><span className="visuallyHidden">{t('intHub.col.actions')}</span></th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id} data-permission-row={row.id}>
            <th scope="row">{row.name}</th>
            <td>{sourceLabel(row.source, row.configured)}</td>
            <td>{row.scopes.length ? <span className="intHubScopes">{row.scopes.map((scope) => <code key={scope} dir="ltr">{scope}</code>)}</span> : '-'}</td>
            <td><StatusChip tone={expiryChip[row.expiry]}>{row.expiresAt && row.expiry !== 'none' ? t(`intHub.perm.expiry.${row.expiry}`, { when: when(row.expiresAt) }) : t('intHub.perm.expiry.none')}</StatusChip></td>
            <td>{row.workspaceToken === null ? '-' : row.workspaceToken ? t('intHub.perm.tokenStored') : t('intHub.perm.tokenNone')}</td>
            <td><button type="button" className="secondary intHubSmall" aria-haspopup="dialog" aria-label={t('intHub.openFor', { name: row.name })} onClick={() => onOpen(row.id)}>{t('intHub.open')}</button></td>
          </tr>)}</tbody>
        </table>
      </div>
    </>}
  </FinCard>;
}

type LogLoad = { kind: 'loading' } | { kind: 'ready'; items: AuditRecord[]; next: string | null } | { kind: 'denied' } | { kind: 'error'; message: string };
const LOG_PAGE = 25;

/** Logs: the integration audit trail (tests, configuration, tokens, setup checks), newest first. */
export function LogsTab({ items, services }: { items: readonly SetupItem[]; services: readonly IntegrationState[] }) {
  const [load, setLoad] = useState<LogLoad>({ kind: 'loading' });
  const [target, setTarget] = useState('');
  const [more, setMore] = useState(false);
  const selectId = useId();
  // Each first-page load starts a new generation; a reply (first page or Load more) from an older generation is dropped,
  // so a slow answer for a previous filter never overwrites the rows or cursor of the filter picked now.
  const generation = useRef(0);
  const fetchFirst = useCallback(async () => {
    const mine = ++generation.current;
    setLoad({ kind: 'loading' });
    setMore(false);
    try {
      const page = await getAuditPage({ action: 'integration.*', target: target || undefined }, null, LOG_PAGE);
      if (mine === generation.current) setLoad({ kind: 'ready', items: page.items, next: page.nextCursor });
    } catch (e) { if (mine === generation.current) setLoad(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', message: describeError(e) }); }
  }, [target]);
  useEffect(() => { void fetchFirst(); }, [fetchFirst]);
  const loadMore = async () => {
    if (load.kind !== 'ready' || !load.next) return;
    const mine = generation.current;
    setMore(true);
    try {
      const page = await getAuditPage({ action: 'integration.*', target: target || undefined }, load.next, LOG_PAGE);
      if (mine === generation.current) setLoad({ kind: 'ready', items: [...load.items, ...page.items], next: page.nextCursor });
    } catch (e) { if (mine === generation.current) setLoad({ kind: 'error', message: describeError(e) }); }
    finally { if (mine === generation.current) setMore(false); }
  };
  const targets = [...services.map((s) => ({ id: s.id, name: s.name })), ...items.map((i) => ({ id: i.id, name: logTargetName(i.id, items, services) }))]
    .sort((a, b) => a.name.localeCompare(b.name));
  return <FinCard title={t('intHub.log.title')} icon={<History />} subtitle={t('intHub.log.sub')}
    actions={<button type="button" className="secondary intHubSmall" disabled={load.kind === 'loading'} onClick={() => void fetchFirst()}><RefreshCw aria-hidden="true" />{t('intHub.refresh')}</button>}>
    <label className="field intHubFilter" htmlFor={selectId}><span>{t('intHub.log.filter')}</span>
      <select id={selectId} value={target} onChange={(e) => setTarget(e.target.value)}>
        <option value="">{t('intHub.log.all')}</option>
        {targets.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
      </select></label>
    {load.kind === 'loading' && <Notice busy>{t('intHub.loading')}</Notice>}
    {load.kind === 'denied' && <Notice>{t('intHub.log.denied')}</Notice>}
    {load.kind === 'error' && <LoadError message={t('intHub.log.failed', { error: load.message })} retry={() => void fetchFirst()} />}
    {load.kind === 'ready' && load.items.length === 0 && <Empty icon={<History aria-hidden="true" />} title={t('intHub.log.emptyTitle')} text={t('intHub.log.emptyText')} />}
    {load.kind === 'ready' && load.items.length > 0 && <>
      <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('intHub.log.title')}>
        <table className="finGrid intHubTable intHubLog">
          <thead><tr><th scope="col">{t('intHub.col.time')}</th><th scope="col">{t('intHub.col.event')}</th><th scope="col">{t('intHub.col.integration')}</th><th scope="col">{t('intHub.col.by')}</th><th scope="col">{t('intHub.col.outcome')}</th></tr></thead>
          <tbody>{load.items.map((record) => <tr key={record.id} data-log-row={record.id}>
            <td>{when(record.createdAt)}</td>
            <th scope="row">{logActionLabel(record.action)}</th>
            <td>{logTargetName(record.target, items, services)}</td>
            <td dir="ltr" className="intHubActor">{record.actor}</td>
            <td><StatusChip tone={record.outcome === 'success' ? 'ok' : record.outcome === 'failed' ? 'bad' : 'muted'}>{logOutcomeLabel(record.outcome)}</StatusChip></td>
          </tr>)}</tbody>
        </table>
      </div>
      {load.next && <div className="actions"><button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('intHub.loading') : t('intHub.log.more')}</button></div>}
    </>}
  </FinCard>;
}
