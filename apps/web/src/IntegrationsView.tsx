// Integrations (docs/integrations-setup.md#the-integrations-page): one status summary with filter
// chips and search, integrations grouped into collapsible categories, one compact row per
// integration with its status and one primary action, and everything else (setup steps, missing
// server settings, callback URLs, checks, health detail, workspace switch and token) in a drawer.
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import {
  Activity, Bell, Bot, Bug, ChevronDown, Code, Database, FileAudio, FileText, GitBranch, Globe, HardDrive, KeyRound, LogIn, Mail,
  Megaphone, MemoryStick, MessageCircle, Mic, Plug, RefreshCw, Search, ShieldAlert, ShoppingCart, Truck, Video, Webhook, Workflow, Wrench,
} from 'lucide-react';
import { describeError, isPermissionDenied } from './lib/errors';
import { integrationBadge, integrationToolNote } from './lib/governance';
import { getIntegrationConfiguration, saveIntegrationConfiguration } from './lib/governanceApi';
import { canSwitchIntegration, credentialBody, credentialSource, lastErrorNote, type IntegrationState } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { copyText } from './lib/clipboard';
import { formatDateTime, t } from './lib/i18n';
import { checkSentence, itemName, statusBadge, type IntegrationSetup, type SetupItem } from './lib/integrationSetup';
import { checkIntegrationSetup, getIntegrationSetup } from './lib/integrationSetupApi';
import {
  PAGE_FILTERS, actionLabel, actionName, buildSections, categoryName, filterLabel, pageCounts, sectionStartsOpen, sectionSummary,
  serviceAction, serviceEntry, setupAction, setupEntry, type Category, type Entry, type PageFilter, type PrimaryAction,
} from './lib/integrationsPage';
import type { View } from './lib/shell';
import { useIsOwner } from './useIsOwner';
import { Drawer } from './Dialog';
import { Note, SetupDetails, type SetupFlash } from './ConnectionSetup';
import './governance.css';
import './catalog.css';
import './integrations.css';

const serviceIcons: Record<string, ReactNode> = { postgresql: <Database />, keycloak: <KeyRound />, nats: <GitBranch />, temporal: <Workflow />, valkey: <MemoryStick />, minio: <FileText />, s3: <FileText />, searxng: <Search />, 'external-webhook': <Webhook /> };
const kindIcons: Record<string, ReactNode> = { email: <Mail />, search: <Search />, tool: <Webhook />, database: <Database />, identity: <KeyRound />, cache: <MemoryStick />, object_storage: <FileText />, workflow: <Workflow />, event_bus: <GitBranch /> };
const setupIcons: Record<string, ReactNode> = {
  model: <Bot />, speech: <Mic />, transcription: <FileAudio />, web_search: <Globe />, email_gmail: <Mail />, email_outlook: <Mail />, email_zoho: <Mail />,
  whatsapp: <MessageCircle />, meetings_zoom: <Video />, meetings_teams: <Video />, push: <Bell />, notification_email: <Mail />, sign_in_google: <LogIn />,
  sign_in_apple: <LogIn />, error_reporting: <Bug />, files: <HardDrive />, external_webhook: <Webhook />, marketing_meta: <Megaphone />, marketing_linkedin: <Megaphone />,
  marketing_x: <Megaphone />, marketing_tiktok: <Megaphone />, marketing_google: <Megaphone />, commerce: <ShoppingCart />, fleet_data: <Truck />, developer: <Code />,
};
const failure = (error: unknown) => isPermissionDenied(error) ? t('integrations.notAllowed', { error: describeError(error) }) : describeError(error);
const when = (iso: string) => formatDateTime(iso);

type Row = (Entry<SetupItem> & { kind: 'setup' }) | (Entry<IntegrationState> & { kind: 'service' });
type Flash = { id: string; text: string; error?: boolean };
type Opened = { kind: 'setup' | 'service'; id: string } | null;

export function IntegrationsView({ onGo }: { onGo?: (view: View) => void } = {}) {
  // Deployment services (live health, per-workspace switch and token).
  const [services, setServices] = useState<IntegrationState[]>([]);
  const [servicesLoading, setServicesLoading] = useState(true);
  const [servicesError, setServicesError] = useState<string | null>(null);
  // Connection setup (owner-facing integrations).
  const [setup, setSetup] = useState<IntegrationSetup | null>(null);
  const [setupState, setSetupState] = useState<'loading' | 'ready' | 'denied' | 'error'>('loading');
  const [setupError, setSetupError] = useState('');
  // Page controls.
  const [filter, setFilter] = useState<PageFilter>('all');
  const [query, setQuery] = useState('');
  const [toggled, setToggled] = useState<Partial<Record<Category, boolean>>>({});
  const [opened, setOpened] = useState<Opened>(null);
  const [checking, setChecking] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const owner = useIsOwner();
  const id = useId();

  const loadServices = useCallback(async () => {
    setServicesLoading(true);
    try { setServices(await catalog.getIntegrationStates()); setServicesError(null); }
    catch (e) { setServicesError(t('integrations.loadFailed', { error: failure(e) })); }
    finally { setServicesLoading(false); }
  }, []);
  const loadSetup = useCallback(async () => {
    setSetupState((old) => (old === 'ready' ? 'ready' : 'loading'));
    try { setSetup(await getIntegrationSetup()); setSetupState('ready'); }
    catch (e) {
      if (isPermissionDenied(e)) { setSetupState('denied'); return; }
      setSetupError(describeError(e)); setSetupState('error');
    }
  }, []);
  useEffect(() => { void loadServices(); void loadSetup(); }, [loadServices, loadSetup]);
  const refresh = () => { void loadServices(); void loadSetup(); };

  const rows = useMemo<Row[]>(() => [
    ...(setup?.items ?? []).map((item) => ({ ...setupEntry(item), kind: 'setup' as const })),
    ...services.map((item) => ({ ...serviceEntry(item), kind: 'service' as const })),
  ], [setup, services]);
  const counts = pageCounts(rows);
  const narrowed = filter !== 'all' || query.trim() !== '';
  const sections = buildSections(rows, filter, query);
  const shownCount = sections.reduce((n, s) => n + s.entries.length, 0);
  // A section keeps the open state it started with while statuses change under it (a fixed
  // integration does not fold its section away); a new filter or search starts over.
  useEffect(() => {
    setToggled((old) => {
      const missing = sections.filter((s) => old[s.category] === undefined);
      if (!missing.length) return old;
      return { ...old, ...Object.fromEntries(missing.map((s) => [s.category, sectionStartsOpen(s, narrowed)])) };
    });
  });
  const changeFilter = (next: PageFilter) => { setFilter(next); setToggled({}); };
  const changeQuery = (next: string) => { setQuery(next); setToggled({}); };
  const clear = () => { setFilter('all'); setQuery(''); setToggled({}); };

  const open = (row: Row) => { setFlash(null); setOpened({ kind: row.kind, id: row.value.id }); };
  const openSetup = opened?.kind === 'setup' ? setup?.items.find((x) => x.id === opened.id) ?? null : null;
  const openService = opened?.kind === 'service' ? services.find((x) => x.id === opened.id) ?? null : null;

  const check = async (item: SetupItem) => {
    setChecking(item.id); setFlash(null);
    try {
      const result = await checkIntegrationSetup(item.id);
      setFlash({ id: `setup:${item.id}`, text: `${checkSentence(result, itemName(item.id))} ${formatDateTime(result.checked_at)}`, error: !result.reachable });
    } catch (e) { setFlash({ id: `setup:${item.id}`, text: describeError(e), error: true }); }
    finally { setChecking(null); }
  };
  const copy = async (item: SetupItem, value: string) => {
    const ok = await copyText(value);
    setFlash({ id: `setup:${item.id}`, text: ok ? t('setup.copied', { value }) : t('setup.copyFailed'), error: !ok });
  };
  const test = async (item: IntegrationState) => {
    setTesting(item.id); setFlash(null);
    try {
      const result = await catalog.testIntegration(item.id);
      setServices((old) => old.map((x) => (x.id === result.id ? result : x)));
      const text = result.status === 'connected' ? t('integrations.testOk', { name: item.name, ms: result.latency_ms ?? 0 })
        : result.status === 'degraded' ? t('integrations.testDegraded', { name: item.name, detail: result.detail })
          : t('integrations.testUnused', { name: item.name, detail: result.detail });
      setFlash({ id: `service:${item.id}`, text, error: result.status === 'degraded' });
    } catch (e) { setFlash({ id: `service:${item.id}`, text: failure(e), error: true }); } finally { setTesting(null); }
  };
  const go = onGo ? (view: View) => { setOpened(null); onGo(view); } : undefined;

  const loadingAny = servicesLoading || setupState === 'loading';
  const hasRows = rows.length > 0;
  return <div className="intPage">
    <section className="moduleIntro"><div><p className="eyebrow">{t('integrations.eyebrow')}</p><h2>{t('integrations.title')}</h2><p>{t('integrations.intro')}</p></div>
      <button type="button" onClick={refresh} disabled={loadingAny}><RefreshCw aria-hidden="true" />{t('integrations.refresh')}</button></section>

    <section className="surface intToolbar" aria-label={t('setup.toolbar')}>
      <fieldset className="intChips">
        <legend className="visuallyHidden">{t('setup.filter.label')}</legend>
        {PAGE_FILTERS.map((value) => <label key={value} className={`intChip intChip-${value} ${filter === value ? 'active' : ''}`}>
          <input type="radio" className="intChipInput" name={`${id}-filter`} value={value} checked={filter === value} onChange={() => changeFilter(value)} />
          <span>{filterLabel(value)}</span><span className="intChipCount">{value === 'all' ? counts.total : counts[value]}</span>
        </label>)}
      </fieldset>
      <label className="intSearch"><span className="visuallyHidden">{t('setup.search')}</span>
        <Search aria-hidden="true" /><input type="search" value={query} placeholder={t('setup.searchPlaceholder')} onChange={(e) => changeQuery(e.target.value)} />
      </label>
      <p className="intSummary" aria-live="polite">{hasRows ? (narrowed ? t('setup.showing', { shown: shownCount, total: counts.total }) : t('setup.pageSummary', counts)) : ''}</p>
    </section>

    {setupState === 'ready' && setup && !setup.can_view_setup && <Note>{t('setup.memberNotice')}</Note>}
    {setupState === 'ready' && setup?.can_view_setup && !setup.can_check && <Note>{t('setup.ownerNotice')}</Note>}
    {setupState === 'loading' && <Note>{t('setup.loading')}</Note>}
    {setupState === 'denied' && <p className="setupState"><ShieldAlert aria-hidden="true" />{t('setup.forbidden')}</p>}
    {setupState === 'error' && <div className="intProblem"><Note error>{t('setup.loadFailed', { error: setupError })}</Note><button type="button" className="secondary" onClick={() => void loadSetup()}>{t('setup.tryAgain')}</button></div>}
    {setupState === 'ready' && setup && setup.items.length === 0 && <div className="emptyState"><Plug aria-hidden="true" /><h3>{t('setup.emptyTitle')}</h3><p>{t('setup.emptyText')}</p><button type="button" onClick={() => void loadSetup()}>{t('setup.tryAgain')}</button></div>}
    {servicesLoading && !services.length && <Note>{t('integrations.loading')}</Note>}
    {servicesError && <Note error>{servicesError}</Note>}
    {!servicesLoading && !servicesError && !services.length && <div className="emptyState"><Plug aria-hidden="true" /><h3>{t('integrations.emptyTitle')}</h3><p>{t('integrations.emptyText')}</p></div>}

    {hasRows && shownCount === 0 && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('setup.noMatchTitle')}</h3><p>{t('setup.noMatchText')}</p><button type="button" className="secondary" onClick={clear}>{t('setup.clear')}</button></div>}

    {sections.map((section) => {
      const isOpen = toggled[section.category] ?? sectionStartsOpen(section, narrowed);
      const headId = `${id}-${section.category}`;
      return <section className="surface intSection" key={section.category} aria-labelledby={headId} data-category={section.category}>
        <h3 className="intSectionHead"><button type="button" id={headId} aria-expanded={isOpen} aria-controls={`${headId}-list`} onClick={() => setToggled((old) => ({ ...old, [section.category]: !isOpen }))}>
          <ChevronDown aria-hidden="true" className="intChevron" /><span className="intSectionName">{categoryName(section.category)}</span>
          <span className="intSectionCount">{sectionSummary(section.counts)}</span>
        </button></h3>
        <ul className="intList" id={`${headId}-list`} hidden={!isOpen}>{section.entries.map((row) => <IntegrationRow key={row.key} row={row} canViewSetup={setup?.can_view_setup ?? false} owner={owner === true} onOpen={() => open(row)} />)}</ul>
      </section>;
    })}
    {setupState === 'ready' && setup && setup.items.length > 0 && <p className="setupFoot">{t('setup.serverOnly')}</p>}

    {openSetup && setup && <Drawer title={itemName(openSetup.id)} onClose={() => setOpened(null)}>
      <SetupDetails item={openSetup} setup={setup} onGo={go} checking={checking} flash={flash?.id === `setup:${openSetup.id}` ? flash as SetupFlash : null}
        onCheck={() => void check(openSetup)} onCopy={(value) => void copy(openSetup, value)} />
    </Drawer>}
    {openService && <Drawer title={openService.name} onClose={() => setOpened(null)}>
      <ServiceDetails item={openService} owner={owner} testing={testing} flash={flash?.id === `service:${openService.id}` ? flash : null} onTest={() => void test(openService)} onChanged={() => void loadServices()} />
    </Drawer>}
  </div>;
}

function rowBadge(row: Row) {
  return row.kind === 'setup' ? statusBadge(row.value.status) : integrationBadge(row.value.status);
}

function IntegrationRow({ row, canViewSetup, owner, onOpen }: { row: Row; canViewSetup: boolean; owner: boolean; onOpen: () => void }) {
  const badge = rowBadge(row);
  const action: PrimaryAction = row.kind === 'setup' ? setupAction(row.value, canViewSetup) : serviceAction(row.value, owner);
  const icon = row.kind === 'setup' ? setupIcons[row.value.id] : serviceIcons[row.value.id] ?? kindIcons[row.value.kind];
  const nameId = `int-${row.kind}-${row.value.id}`;
  return <li className={`intRow intRow-${row.bucket}`} data-integration-row={row.value.id} aria-labelledby={nameId}>
    <span className="intIcon" aria-hidden="true">{icon ?? <Plug />}</span>
    <div className="intMain">
      <h4 id={nameId}>{row.name}</h4>
      {row.purpose && <p className="intPurpose" dir="auto" title={row.purpose}>{row.purpose}</p>}
    </div>
    <span className={`statusBadge ${badge.tone}`}>{badge.label}</span>
    <button type="button" className={action === 'fix' || action === 'connect' ? 'intAction intActionPrimary' : 'intAction secondary'}
      aria-haspopup="dialog" aria-label={actionName(action, row.name)} onClick={onOpen}>{actionLabel(action)}</button>
  </li>;
}

function ServiceDetails({ item, owner, testing, flash, onTest, onChanged }: {
  item: IntegrationState; owner: boolean | null; testing: string | null; flash: Flash | null; onTest: () => void; onChanged: () => void;
}) {
  const badge = integrationBadge(item.status);
  const tool = integrationToolNote(item.kind, item.status);
  const last = lastErrorNote(item, when);
  return <div className="setupDetail serviceDetail" data-service-detail={item.id}>
    <div className="setupDetailStatus">
      <span className={`statusBadge ${badge.tone}`}>{badge.label}</span>
      <p className="setupSay" dir="auto">{item.latency_ms !== null ? t('integrations.latency', { detail: item.detail, ms: item.latency_ms }) : item.detail}</p>
    </div>
    <dl className="serviceFacts">
      <div><dt>{t('setup.endpoint')}</dt><dd dir="ltr" className="catalogLtr">{item.endpoint}</dd></div>
      {item.checked_at && <div><dt>{t('setup.checkedAt')}</dt><dd>{when(item.checked_at)}</dd></div>}
    </dl>
    {tool && <p className={`integrationTool ${tool.available ? 'available' : ''}`}>{tool.text}</p>}
    {last && <p className="integrationLastError">{last}</p>}
    {item.remediation && <p className="integrationFix"><Wrench aria-hidden="true" /><span><strong>{t('integrations.howToFix')}</strong><span dir="auto">{item.remediation}</span></span></p>}
    {item.accepts_workspace_credential && <p className="integrationCredentialLine"><KeyRound aria-hidden="true" />{credentialSource(item)}</p>}
    {owner === false && <Note>{t('integrations.memberNotice')}</Note>}
    {owner && <div className="setupActions"><button type="button" className="secondary" disabled={testing !== null || item.status === 'disabled'} title={item.status === 'disabled' ? t('integrations.testDisabled') : undefined} onClick={onTest}><Activity aria-hidden="true" />{testing === item.id ? t('integrations.testing') : t('integrations.test')}</button></div>}
    {flash && <div className="setupFlash"><Note error={flash.error}>{flash.text}</Note></div>}
    {owner && <ConfigurePanel integration={item} onChanged={onChanged} />}
  </div>;
}

function ConfigurePanel({ integration, onChanged }: { integration: IntegrationState; onChanged: () => void }) {
  const [enabled, setEnabled] = useState(true);
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const switchable = canSwitchIntegration(integration);
  useEffect(() => {
    let live = true;
    getIntegrationConfiguration(integration.id)
      .then((c) => { if (live) { setEnabled(c.enabled); setEndpoint(c.endpoint); setReady(true); } })
      .catch((e) => { if (live) setFlash({ text: failure(e), error: true }); });
    return () => { live = false; };
  }, [integration.id]);
  const run = async (action: () => Promise<string>) => {
    setBusy(true); setFlash(null);
    try { setFlash({ text: await action() }); onChanged(); } catch (e) { setFlash({ text: failure(e), error: true }); } finally { setBusy(false); }
  };
  const save = () => run(async () => {
    // The endpoint is kept as stored: it is informational and never connected to.
    const saved = await saveIntegrationConfiguration(integration.id, { enabled, endpoint });
    return t(saved.enabled ? 'integrations.saved.on' : 'integrations.saved.off', { name: integration.name, workspace: saved.workspace_id });
  });
  const storeSecret = () => {
    const body = credentialBody(secret);
    if (!body.ok) { setFlash({ text: body.error, error: true }); return; }
    void run(async () => { const saved = await catalog.saveIntegrationCredential(integration.id, body.value.secret); setSecret(''); return t('integrations.tokenStored', { hint: saved.hint ?? '' }); });
  };
  const removeSecret = () => void run(async () => { await catalog.deleteIntegrationCredential(integration.id); setConfirmRemove(false); return t('integrations.tokenRemoved'); });
  const stored = integration.workspace_credential?.configured;
  return <div className="integrationConfig">
    <form aria-label={t('integrations.configLabel', { name: integration.name })} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {switchable
        ? <label className="integrationToggle"><input type="checkbox" checked={enabled} disabled={!ready} onChange={(e) => setEnabled(e.target.checked)} />{t('integrations.useIn', { name: integration.name })}</label>
        : <p className="featureMeta">{integration.environment_variable ? t('integrations.notConfiguredSetting', { setting: integration.environment_variable }) : t('integrations.notConfigured')}</p>}
      {switchable && <div className="actions"><button type="submit" disabled={!ready || busy}>{t('integrations.save')}</button></div>}
    </form>
    {integration.accepts_workspace_credential && <form className="integrationCredential" aria-label={t('integrations.tokenForm', { name: integration.name })} onSubmit={(e) => { e.preventDefault(); storeSecret(); }}>
      <label className="field"><span>{stored ? t('integrations.tokenReplace') : t('integrations.tokenNew')}</span><input type="password" dir="ltr" autoComplete="off" spellCheck={false} value={secret} maxLength={4096} onChange={(e) => setSecret(e.target.value)} aria-describedby={`token-help-${integration.id}`} /></label>
      <p className="featureMeta" id={`token-help-${integration.id}`}>{t('integrations.tokenHelp')}</p>
      <div className="actions">
        <button type="submit" disabled={busy || !secret}>{stored ? t('integrations.tokenReplaceButton') : t('integrations.tokenStore')}</button>
        {stored && (confirmRemove
          ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={removeSecret}>{t('integrations.tokenRemove')}</button><button type="button" className="secondary" onClick={() => setConfirmRemove(false)}>{t('common.cancel')}</button></span>
          : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirmRemove(true)}>{t('integrations.tokenRemoveAsk')}</button>)}
      </div>
    </form>}
    {flash && <Note error={flash.error}>{flash.text}</Note>}
  </div>;
}
