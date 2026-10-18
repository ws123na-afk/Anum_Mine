// Business, the internal sales desk (docs/business.md): the owner's KPI summary, leads, the pipeline,
// proposals, projects, the catalog and the research caps. Tabs and open records are URL hashes
// (#business-leads/<id>), so Back, Forward and links work. Sending is off: nothing here sends.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import type { BusinessKpi, BusinessProduct, BusinessSetup } from '@anum/contracts';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatNumber, t } from './lib/i18n';
import { TABS, formatMoney, formatPct, stateLabel, tabFromHash, type BusinessTab } from './lib/business';
import * as b from './lib/businessApi';
import { figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { PageHeader, PageSummary } from './PageParts';
import { Denied, Empty, Failed, Note, failure, when } from './BusinessParts';
import { LeadsTab } from './BusinessLeads';
import { PipelineTab, ProjectsTab, ProposalsTab } from './BusinessDeals';
import { CatalogTab } from './BusinessCatalog';
import './business.css';

type State = { kind: 'loading' } | { kind: 'ready'; setup: BusinessSetup } | { kind: 'denied'; text: string } | { kind: 'error'; text: string };

function parse(hash: string): { tab: BusinessTab; id: string | null } {
  const tab = tabFromHash(hash) ?? 'overview';
  const id = hash.replace(/^#/, '').split('/')[1] ?? null;
  return { tab, id: id ? decodeURIComponent(id) : null };
}

export function BusinessView() {
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [route, setRoute] = useState(() => parse(location.hash));
  const [products, setProducts] = useState<BusinessProduct[]>([]), [productsLoad, setProductsLoad] = useState<{ loading: boolean; error: string | null }>({ loading: true, error: null });
  const [tick, setTick] = useState(0);

  const loadSetup = useCallback(async () => {
    try { setState({ kind: 'ready', setup: await b.getSetup() }); }
    catch (error) { setState(isPermissionDenied(error) ? { kind: 'denied', text: describeError(error) } : { kind: 'error', text: describeError(error) }); }
  }, []);
  const loadProducts = useCallback(async () => {
    setProductsLoad({ loading: true, error: null });
    try {
      const page = await b.listProducts({ limit: 100, offset: 0 });
      setProducts(page.items); setProductsLoad({ loading: false, error: null });
    } catch (error) { setProductsLoad({ loading: false, error: failure(error, t('biz.tab.catalog')) }); }
  }, []);
  useEffect(() => { void loadSetup(); }, [loadSetup]);
  useEffect(() => { if (state.kind === 'ready') void loadProducts(); }, [state.kind, loadProducts]);
  useEffect(() => {
    const sync = () => { if (location.hash.startsWith('#business')) setRoute(parse(location.hash)); };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync); };
  }, []);

  const go = (tab: BusinessTab, id: string | null = null) => {
    setRoute({ tab, id });
    const hash = tab === 'overview' ? '#business' : `#business-${tab}${id ? `/${encodeURIComponent(id)}` : ''}`;
    if (location.hash !== hash) history.pushState(null, '', hash);
  };
  const changed = () => setTick((value) => value + 1);

  const title = viewLabel('business');
  const subtitle = t('biz.subtitle');
  if (state.kind === 'loading') return <><PageHeader title={title} subtitle={subtitle} /><Note>{t('biz.loading')}</Note></>;
  if (state.kind === 'denied') return <><PageHeader title={title} subtitle={subtitle} /><Denied text={state.text} /></>;
  if (state.kind === 'error') return <><PageHeader title={title} subtitle={subtitle} /><Failed text={state.text} onRetry={() => { setState({ kind: 'loading' }); void loadSetup(); }} /></>;
  const setup = state.setup;
  const { tab, id } = route;
  return <div className="bzLayout">
    <PageHeader title={title} subtitle={subtitle} crumbs={tab === 'overview' ? undefined : [{ label: viewLabel('home'), href: '#home' }, { label: title, href: '#business', onClick: () => go('overview') }, { label: t(`biz.tab.${tab}`) }]} />
    <nav className="settingsTabs bzTabs" aria-label={t('biz.sections')}>{TABS.map((item) => <button key={item} type="button" aria-current={tab === item ? 'page' : undefined} onClick={() => go(item)}>{t(`biz.tab.${item}`)}</button>)}</nav>
    {tab === 'overview' && <Overview key={tick} setup={setup} products={products} onGo={go} />}
    {tab === 'leads' && <LeadsTab setup={setup} products={products} openLeadId={id} onOpenLead={(leadId) => go('leads', leadId)} onOpenOpportunity={(opportunityId) => go('pipeline', opportunityId)} onChanged={changed} />}
    {tab === 'pipeline' && <PipelineTab setup={setup} openId={id} onOpen={(item) => go('pipeline', item)} onOpenProposal={(item) => go('proposals', item)} onGoLeads={() => go('leads')} onChanged={changed} />}
    {tab === 'proposals' && <ProposalsTab setup={setup} openId={id} onOpen={(item) => go('proposals', item)} onOpenProject={(item) => go('projects', item)} onGoPipeline={() => go('pipeline')} onChanged={changed} />}
    {tab === 'projects' && <ProjectsTab setup={setup} openId={id} onOpen={(item) => go('projects', item)} onGoProposals={() => go('proposals')} />}
    {tab === 'catalog' && <CatalogTab setup={setup} products={products} loading={productsLoad.loading} error={productsLoad.error} onReload={() => void loadProducts()} />}
    {tab === 'settings' && <SettingsTab setup={setup} onSaved={() => void loadSetup()} />}
  </div>;
}

const DAYS = [7, 30, 90, 365];

function Overview({ setup, products, onGo }: { setup: BusinessSetup; products: BusinessProduct[]; onGo: (tab: BusinessTab) => void }) {
  const [days, setDays] = useState(30), [kpi, setKpi] = useState<{ state: 'loading' } | { state: 'ready'; value: BusinessKpi } | { state: 'error'; message: string }>({ state: 'loading' });
  const daysId = useId();
  const load = useCallback(async () => {
    setKpi({ state: 'loading' });
    try { setKpi({ state: 'ready', value: await b.getKpi(days) }); } catch (error) { setKpi({ state: 'error', message: describeError(error) }); }
  }, [days]);
  useEffect(() => { void load(); }, [load]);
  const value = kpi.state === 'ready' ? kpi.value : null;
  const money = (list: { currency: 'SAR' | 'AED' | 'USD'; amount: number }[]) => (list.length ? list.map((item) => formatMoney(item.amount, item.currency)).join(' · ') : formatMoney(0, 'SAR'));
  const qualified = value?.funnel.find((step) => step.stage === 'qualified');
  const figures = [
    figure('leads', t('biz.kpi.leads'), kpi, () => ({ value: formatNumber(value!.leads_created), hint: t('biz.kpi.inDays', { days }) })),
    figure('qualified', t('biz.kpi.qualified'), kpi, () => ({ value: formatPct(qualified?.rate_pct ?? null), hint: t('biz.kpi.ofLeads', { reached: qualified?.reached ?? 0, of: qualified?.of ?? 0 }) })),
    figure('pipeline', t('biz.kpi.pipeline'), kpi, () => ({ value: money(value!.pipeline_assumed), hint: t('biz.kpi.openAssumed', { count: value!.open_opportunities }) })),
    figure('contracted', t('biz.kpi.contracted'), kpi, () => ({ value: money(value!.contracted), hint: t('biz.kpi.accepted', { count: value!.proposals_accepted }), tone: 'accent' })),
    figure('cash', t('biz.kpi.cash'), kpi, () => ({ value: money(value!.cash_collected), hint: t('biz.kpi.cashHint'), tone: 'ok' })),
  ];
  const nothing = value && value.leads_created === 0 && value.open_opportunities === 0;
  return <>
    <PageSummary label={t('biz.kpi.label')} figures={figures} onRetry={() => void load()} footer={<label className="bzCompact" htmlFor={daysId}><span>{t('biz.kpi.window')}</span>
      <select id={daysId} value={days} onChange={(event) => setDays(Number(event.target.value))}>{DAYS.map((item) => <option key={item} value={item}>{t('biz.kpi.days', { days: item })}</option>)}</select></label>} />
    <Note>{t('biz.sendingOff')}</Note>
    {kpi.state === 'error' && <Note error>{t('biz.failed', { what: t('biz.kpi.label'), message: kpi.message })}</Note>}
    {nothing && <Empty title={products.length ? t('biz.overview.noLeadsTitle') : t('biz.overview.startTitle')} text={products.length ? t('biz.overview.noLeadsText') : t('biz.overview.startText')}
      action={<button type="button" onClick={() => onGo(products.length || !setup.permissions.manage ? 'leads' : 'catalog')}>{products.length || !setup.permissions.manage ? t('biz.overview.goLeads') : t('biz.overview.goCatalog')}</button>} />}
    {value && !nothing && <div className="bzOverview">
      <section className="surface bzCard" aria-labelledby="bz-funnel"><h2 id="bz-funnel">{t('biz.kpi.funnel')}</h2>
        <p className="muted bzHint">{t('biz.kpi.funnelHelp', { from: when(value.window_from), to: when(value.window_to) })}</p>
        <div className="dataTableWrap"><table className="dataTable"><thead><tr><th scope="col">{t('biz.kpi.step')}</th><th scope="col" className="num">{t('biz.kpi.reached')}</th><th scope="col" className="num">{t('biz.kpi.ofCreated')}</th><th scope="col" className="num">{t('biz.kpi.fromPrevious')}</th></tr></thead>
          <tbody>{value.funnel.map((step) => <tr key={step.stage}><th scope="row">{t(`biz.funnel.${step.stage}`)}</th><td className="num">{t('biz.kpi.reachedOf', { reached: step.reached, of: step.of })}</td><td className="num">{formatPct(step.rate_pct)}</td><td className="num">{step.from_previous_pct === null ? '-' : formatPct(step.from_previous_pct)}</td></tr>)}</tbody></table></div>
      </section>
      <section className="surface bzCard" aria-labelledby="bz-deals"><h2 id="bz-deals">{t('biz.kpi.deals')}</h2>
        <dl className="bzFacts">
          <div><dt>{t('biz.kpi.weighted')}</dt><dd>{money(value.pipeline_weighted)}</dd></div>
          <div><dt>{t('biz.kpi.wonLost')}</dt><dd>{t('biz.kpi.wonLostValue', { won: value.opportunities_won, lost: value.opportunities_lost })}</dd></div>
          <div><dt>{t('biz.kpi.proposals')}</dt><dd>{t('biz.kpi.proposalsValue', { sent: value.proposals_sent, accepted: value.proposals_accepted })}</dd></div>
          <div><dt>{t('biz.kpi.research')}</dt><dd>{t('biz.kpi.researchValue', { runs: value.research_runs, searches: value.web_searches })}</dd></div>
        </dl>
        {Object.keys(value.by_state).length > 0 && <p className="muted">{Object.entries(value.by_state).map(([key, count]) => `${stateLabel(key as never)}: ${count}`).join(' · ')}</p>}
      </section>
      <section className="surface bzCard bzNotes" aria-labelledby="bz-notes"><h2 id="bz-notes">{t('biz.kpi.howRead')}</h2><ul>{value.notes.map((note) => <li key={note}>{note}</li>)}</ul>
        {value.truncated && <Note>{t('biz.kpi.truncated')}</Note>}</section>
    </div>}
  </>;
}

function SettingsTab({ setup, onSaved }: { setup: BusinessSetup; onSaved: () => void }) {
  const [runs, setRuns] = useState(String(setup.settings.research_runs_per_day)), [searches, setSearches] = useState(String(setup.settings.research_searches_per_day));
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState(false);
  const manage = setup.permissions.manage;
  async function save(event: FormEvent) {
    event.preventDefault();
    const r = Number(runs), s = Number(searches);
    if (!Number.isInteger(r) || r < 0 || r > 100 || !Number.isInteger(s) || s < 0 || s > 500) { setMessage({ text: t('biz.settings.invalid'), error: true }); return; }
    setBusy(true);
    try { await b.saveSettings({ research_runs_per_day: r, research_searches_per_day: s }); setMessage({ text: t('biz.settings.saved') }); onSaved(); }
    catch (error) { setMessage({ text: failure(error, t('biz.settings.title')), error: true }); } finally { setBusy(false); }
  }
  return <div className="bzOverview">
    <section className="surface bzCard" aria-labelledby="bz-caps"><h2 id="bz-caps">{t('biz.settings.title')}</h2>
      <p className="muted">{t('biz.settings.help')}</p>
      <form onSubmit={(event) => void save(event)} aria-label={t('biz.settings.title')}>
        <div className="bzGrid2">
          <label className="field"><span>{t('biz.settings.runs')}</span><input inputMode="numeric" value={runs} disabled={!manage} onChange={(event) => setRuns(event.target.value)} /></label>
          <label className="field"><span>{t('biz.settings.searches')}</span><input inputMode="numeric" value={searches} disabled={!manage} onChange={(event) => setSearches(event.target.value)} /></label>
        </div>
        <p className="muted bzHint">{t('biz.settings.usage', { runs: setup.usage.runs_today, searches: setup.usage.searches_today })}</p>
        {manage ? <div className="actions"><button type="submit" disabled={busy}>{t('biz.settings.save')}</button></div> : <p className="muted">{t('biz.settings.ownersOnly')}</p>}
        {message && <Note error={message.error}>{message.text}</Note>}
      </form>
    </section>
    <section className="surface bzCard" aria-labelledby="bz-status"><h2 id="bz-status">{t('biz.settings.status')}</h2>
      <dl className="bzFacts">
        <div><dt>{t('biz.settings.sending')}</dt><dd>{t('biz.settings.sendingOff')}</dd></div>
        <div><dt>{t('biz.settings.webSearch')}</dt><dd>{setup.web_search ? t('biz.settings.on') : t('biz.settings.webSearchOff')}</dd></div>
        <div><dt>{t('biz.settings.model')}</dt><dd>{setup.model_connected ? t('biz.settings.modelOn') : t('biz.settings.modelOff')}</dd></div>
        <div><dt>{t('biz.settings.decide')}</dt><dd>{setup.permissions.decide ? t('biz.settings.youDecide') : t('biz.settings.ownerDecides')}</dd></div>
      </dl>
    </section>
  </div>;
}
