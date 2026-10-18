// Business, Prospecting (docs/business.md#prospecting-pipeline): each product's target customer profile,
// the signals an owner confirms, discovery and profile runs (estimate, confirm, progress, stop), the
// evidence and observations a run recorded, and a lead's product fit and score breakdown with
// deterministic suggestions a person applies. Nothing here contacts anyone.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Play, Square, Telescope } from 'lucide-react';
import type {
  BusinessLead, BusinessProduct, BusinessSetup, BusinessSignal, LeadProfile, PipelineRun, PipelineRunDetail, PipelineSettings, RunKind, SignalStatus, TargetProfile,
} from '@anum/contracts';
import { describeError } from './lib/errors';
import { formatNumber, t, type MessageKey } from './lib/i18n';
import { criterionLabel, stateLabel } from './lib/business';
import { SIZES, estimateLine, fitText, isActive, runStatusLabel, runTone, signalGroups, targetBody, targetDraft, wordsOf, type TargetDraft } from './lib/businessPipeline';
import * as b from './lib/businessApi';
import * as p from './lib/businessPipelineApi';
import { Badge, Denied, Empty, Failed, Note, Pager, day, failure, when } from './BusinessParts';

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string; denied?: boolean };
const key = (text: string) => text as MessageKey;

export function ProspectingTab({ setup, products, onOpenLead }: { setup: BusinessSetup; products: BusinessProduct[]; onOpenLead: (leadId: string) => void }) {
  const [settings, setSettings] = useState<Load<PipelineSettings>>({ state: 'loading' });
  const [runId, setRunId] = useState<string | null>(null), [profileLead, setProfileLead] = useState<string | null>(null);
  const [historyTick, setHistoryTick] = useState(0);
  const loadSettings = useCallback(async () => {
    try { setSettings({ state: 'ready', value: await p.getPipelineSettings() }); }
    catch (error) { setSettings({ state: 'error', message: describeError(error) }); }
  }, []);
  useEffect(() => { void loadSettings(); }, [loadSettings]);
  if (settings.state === 'loading') return <Note>{t('biz.loading')}</Note>;
  if (settings.state === 'error') return <Failed text={settings.message} onRetry={() => void loadSettings()} />;
  const value = settings.value;
  const write = setup.permissions.write;
  return <div className="bzSection">
    <Note>{t('biz.prospect.intro')}</Note>
    <section className="surface bzCard" aria-labelledby="bz-p-status"><h2 id="bz-p-status">{t('biz.prospect.status')}</h2>
      <dl className="bzFacts">
        <div><dt>{t('biz.prospect.webSearch')}</dt><dd>{value.tools.web_search ? t('biz.settings.on') : t('biz.settings.webSearchOff')}</dd></div>
        <div><dt>{t('biz.prospect.reader')}</dt><dd>{value.tools.page_fetch ? t('biz.prospect.readerOn') : t('biz.prospect.readerOff')}</dd></div>
        <div><dt>{t('biz.prospect.today')}</dt><dd>{t('biz.prospect.todayValue', { searches: value.usage_today.searches, max: value.research_searches_per_day, pages: value.usage_today.page_fetches, maxPages: value.fetches_per_day })}</dd></div>
        <div><dt>{t('biz.settings.model')}</dt><dd>{value.model_connected ? t('biz.settings.modelOn') : t('biz.settings.modelOff')}</dd></div>
      </dl>
      {value.paused && <Note error>{t('biz.prospect.paused', { reason: value.paused_reason || '-' })}</Note>}
      <details className="bzDetails"><summary>{t('biz.prospect.excludedTitle')}</summary><ul className="bzBullets">{value.excluded.map((item) => <li key={item}>{item}</li>)}</ul></details>
    </section>
    <TargetsCard canManage={value.can_manage} hasProducts={products.length > 0} />
    <SignalsCard canManage={value.can_manage} />
    {write ? <NewRun products={products} onRun={(id) => { setRunId(id); setHistoryTick((tick) => tick + 1); }} /> : <Note>{t('biz.prospect.readOnly')}</Note>}
    {runId && <RunPanel runId={runId} write={write} onOpenProfile={setProfileLead} onOpenLead={onOpenLead} onFinished={() => { setHistoryTick((tick) => tick + 1); void loadSettings(); }} />}
    {profileLead && <ProfilePanel leadId={profileLead} write={write} onClose={() => setProfileLead(null)} onOpenLead={onOpenLead} />}
    <History key={historyTick} onOpen={setRunId} />
  </div>;
}

function TargetsCard({ canManage, hasProducts }: { canManage: boolean; hasProducts: boolean }) {
  const [targets, setTargets] = useState<Load<TargetProfile[]>>({ state: 'loading' }), [editing, setEditing] = useState<TargetProfile | null>(null);
  const load = useCallback(async () => {
    try { setTargets({ state: 'ready', value: await p.listTargets() }); } catch (error) { setTargets({ state: 'error', message: failure(error, t('biz.target.title')) }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return <section className="surface bzCard" aria-labelledby="bz-targets"><h2 id="bz-targets">{t('biz.target.title')}</h2>
    <p className="muted bzHint">{t('biz.target.help')}</p>
    {targets.state === 'loading' && <Note>{t('biz.loading')}</Note>}
    {targets.state === 'error' && <Note error>{targets.message}</Note>}
    {targets.state === 'ready' && !hasProducts && <Empty title={t('biz.target.noProducts')} text={t('biz.target.noProductsText')} />}
    {targets.state === 'ready' && targets.value.length > 0 && <div className="dataTableWrap"><table className="dataTable bzTable"><thead><tr>
      <th scope="col">{t('biz.target.product')}</th><th scope="col">{t('biz.target.industries')}</th><th scope="col" className="bzHideSm">{t('biz.target.regions')}</th>
      <th scope="col" className="bzHideMd">{t('biz.target.pitch')}</th><th scope="col"><span className="srOnly">{t('biz.target.edit')}</span></th></tr></thead>
      <tbody>{targets.value.map((item) => <tr key={item.product_id}>
        <th scope="row" dir="auto">{item.product_name}{!item.saved && <><br /><small className="muted">{t('biz.target.notSaved')}</small></>}</th>
        <td dir="auto">{item.industries.join(', ') || '-'}</td><td className="bzHideSm" dir="auto">{item.regions.join(', ') || '-'}</td>
        <td className="bzHideMd" dir="auto">{item.value_proposition || '-'}</td>
        <td>{canManage ? <button type="button" className="secondary" onClick={() => setEditing(item)}>{t('biz.target.edit')}</button> : null}</td>
      </tr>)}</tbody></table></div>}
    {!canManage && <p className="muted bzHint">{t('biz.target.ownersOnly')}</p>}
    {editing && <TargetForm target={editing} onDone={() => { setEditing(null); void load(); }} />}
  </section>;
}

function TargetForm({ target, onDone }: { target: TargetProfile; onDone: () => void }) {
  const [draft, setDraft] = useState<TargetDraft>(() => targetDraft(target)), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const base = useId();
  const set = (name: keyof TargetDraft, value: string | string[]) => setDraft((current) => ({ ...current, [name]: value }));
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try { await p.saveTarget(target.product_id, targetBody(draft)); onDone(); } catch (error) { setProblem(failure(error, t('biz.target.title'))); setBusy(false); }
  }
  const text = (name: 'industries' | 'regions' | 'roles' | 'keywords' | 'disqualifiers') => <label className="field" htmlFor={`${base}-${name}`}>
    <span>{t(key(`biz.target.${name}`))}</span><input id={`${base}-${name}`} dir="auto" value={draft[name]} onChange={(event) => set(name, event.target.value)} /></label>;
  return <form className="bzBlock" onSubmit={(event) => void save(event)} aria-label={t('biz.target.editing', { name: target.product_name })}>
    <h3>{t('biz.target.editing', { name: target.product_name })}</h3>
    <p className="muted bzHint">{t('biz.target.listHelp')}</p>
    <div className="bzGrid2">{text('industries')}{text('regions')}{text('roles')}{text('keywords')}{text('disqualifiers')}
      <fieldset className="field"><legend>{t('biz.target.sizes')}</legend><div className="bzInline">{SIZES.map((size) => <label key={size} className="bzCheck">
        <input type="checkbox" checked={draft.sizes.includes(size)} onChange={(event) => set('sizes', event.target.checked ? [...draft.sizes, size] : draft.sizes.filter((item) => item !== size))} />{t(key(`biz.size.${size}`))}</label>)}</div></fieldset>
    </div>
    <label className="field" htmlFor={`${base}-pitch`}><span>{t('biz.target.pitch')}</span><textarea id={`${base}-pitch`} rows={2} dir="auto" value={draft.pitch} onChange={(event) => set('pitch', event.target.value)} /></label>
    <label className="field" htmlFor={`${base}-pitchAr`}><span>{t('biz.target.pitchAr')}</span><textarea id={`${base}-pitchAr`} rows={2} dir="rtl" value={draft.pitchAr} onChange={(event) => set('pitchAr', event.target.value)} /></label>
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('biz.target.save')}</button><button type="button" className="secondary" onClick={onDone}>{t('common.cancel')}</button></div>
  </form>;
}

function SignalsCard({ canManage }: { canManage: boolean }) {
  const [signals, setSignals] = useState<Load<BusinessSignal[]>>({ state: 'loading' }), [pending, setPending] = useState<Record<string, SignalStatus>>({});
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setSignals({ state: 'ready', value: await p.listSignals() }); } catch (error) { setSignals({ state: 'error', message: failure(error, t('biz.signal.title')) }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function save() {
    setBusy(true); setMessage(null);
    try { setSignals({ state: 'ready', value: await p.decideSignals(pending) }); setPending({}); setMessage({ text: t('biz.signal.saved') }); }
    catch (error) { setMessage({ text: failure(error, t('biz.signal.title')), error: true }); } finally { setBusy(false); }
  }
  const confirmed = signals.state === 'ready' ? signals.value.filter((item) => (pending[item.code] ?? item.status) === 'confirmed').length : 0;
  return <section className="surface bzCard" aria-labelledby="bz-signals"><h2 id="bz-signals">{t('biz.signal.title')}</h2>
    <p className="muted bzHint">{t('biz.signal.help')}</p>
    {signals.state === 'loading' && <Note>{t('biz.loading')}</Note>}
    {signals.state === 'error' && <Note error>{signals.message}</Note>}
    {signals.state === 'ready' && <>
      {confirmed === 0 && <Note>{t('biz.signal.noneConfirmed')}</Note>}
      {signalGroups(signals.value).map((group) => <fieldset key={group.category} className="bzSignals"><legend>{t(key(`biz.signal.group.${group.category}`))}</legend>
        {group.items.map((item) => {
          const status = pending[item.code] ?? item.status;
          return <label key={item.code} className="bzSignal">
            <input type="checkbox" checked={status === 'confirmed'} disabled={!canManage} onChange={(event) => setPending((current) => ({ ...current, [item.code]: event.target.checked ? 'confirmed' : 'rejected' }))} />
            <span><strong>{item.label}</strong> <Badge tone={status === 'confirmed' ? 'success' : status === 'rejected' ? 'dangerTone' : 'neutral'}>{t(key(`biz.signal.status.${status}`))}</Badge>
              <br /><small className="muted">{item.description} {item.cost_note}</small></span>
          </label>;
        })}
      </fieldset>)}
      {canManage ? <div className="actions"><button type="button" disabled={busy || Object.keys(pending).length === 0} onClick={() => void save()}>{busy ? t('dialog.working') : t('biz.signal.save')}</button></div>
        : <p className="muted bzHint">{t('biz.signal.ownersOnly')}</p>}
      {message && <Note error={message.error}>{message.text}</Note>}
    </>}
  </section>;
}

function NewRun({ products, onRun }: { products: BusinessProduct[]; onRun: (runId: string) => void }) {
  const [kind, setKind] = useState<RunKind>('discover'), [productId, setProductId] = useState(''), [terms, setTerms] = useState(''), [places, setPlaces] = useState('');
  const [maxNew, setMaxNew] = useState('10'), [leads, setLeads] = useState<Load<BusinessLead[]>>({ state: 'loading' }), [chosen, setChosen] = useState<string[]>([]);
  const [estimate, setEstimate] = useState<PipelineRun | null>(null), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const base = useId();
  useEffect(() => {
    if (kind !== 'profile') return;
    let live = true;
    b.listLeads({ state: ['discovered', 'researched', 'qualified'], limit: 50, offset: 0 })
      .then((page) => { if (live) setLeads({ state: 'ready', value: page.items }); })
      .catch((error: unknown) => { if (live) setLeads({ state: 'error', message: failure(error, t('biz.tab.leads')) }); });
    return () => { live = false; };
  }, [kind]);
  async function plan(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    if (kind === 'discover' && !productId) { setProblem(t('biz.run.needProduct')); return; }
    if (kind === 'profile' && chosen.length === 0) { setProblem(t('biz.run.needLeads')); return; }
    setBusy(true);
    try {
      setEstimate(await p.estimateRun(kind === 'discover'
        ? { kind, product_id: productId, terms: wordsOf(terms, 5), places: wordsOf(places, 5), max_new_leads: Math.max(1, Math.min(25, Number(maxNew) || 10)) }
        : { kind, lead_ids: chosen }));
    } catch (error) { setProblem(failure(error, t('biz.run.estimate'))); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!estimate) return;
    setBusy(true); setProblem(null);
    try { const run = await p.confirmRun(estimate.id); setEstimate(null); onRun(run.id); } catch (error) { setProblem(failure(error, t('biz.run.confirm'))); } finally { setBusy(false); }
  }
  async function cancel() {
    if (!estimate) return;
    try { await p.stopRun(estimate.id); } catch { /* an estimate that cannot be cancelled is simply left unconfirmed */ }
    setEstimate(null);
  }
  return <section className="surface bzCard" aria-labelledby="bz-newrun"><h2 id="bz-newrun"><Telescope className="bzInlineIcon" aria-hidden="true" /> {t('biz.run.new')}</h2>
    <form onSubmit={(event) => void plan(event)} aria-label={t('biz.run.new')}>
      <fieldset className="field"><legend>{t('biz.run.kind')}</legend><div className="bzInline">
        {(['discover', 'profile'] as const).map((item) => <label key={item} className="bzCheck"><input type="radio" name={`${base}-kind`} checked={kind === item} onChange={() => { setKind(item); setEstimate(null); }} />{t(key(`biz.run.kind.${item}`))}</label>)}
      </div><small className="muted">{t(key(`biz.run.kindHelp.${kind}`))}</small></fieldset>
      {kind === 'discover' ? <div className="bzGrid2">
        <label className="field" htmlFor={`${base}-product`}><span>{t('biz.run.product')}</span>
          <select id={`${base}-product`} value={productId} onChange={(event) => setProductId(event.target.value)}><option value="">{t('biz.run.chooseProduct')}</option>
            {products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field" htmlFor={`${base}-max`}><span>{t('biz.run.maxNew')}</span><input id={`${base}-max`} inputMode="numeric" value={maxNew} onChange={(event) => setMaxNew(event.target.value)} /></label>
        <label className="field" htmlFor={`${base}-terms`}><span>{t('biz.run.terms')}</span><input id={`${base}-terms`} dir="auto" placeholder={t('biz.run.termsHint')} value={terms} onChange={(event) => setTerms(event.target.value)} /></label>
        <label className="field" htmlFor={`${base}-places`}><span>{t('biz.run.places')}</span><input id={`${base}-places`} dir="auto" placeholder={t('biz.run.placesHint')} value={places} onChange={(event) => setPlaces(event.target.value)} /></label>
      </div> : <fieldset className="field"><legend>{t('biz.run.leads')}</legend>
        {leads.state === 'loading' && <Note>{t('biz.loading')}</Note>}
        {leads.state === 'error' && <Note error>{leads.message}</Note>}
        {leads.state === 'ready' && leads.value.length === 0 && <p className="muted">{t('biz.run.noLeads')}</p>}
        {leads.state === 'ready' && <div className="bzPick">{leads.value.map((lead) => <label key={lead.id} className="bzCheck">
          <input type="checkbox" checked={chosen.includes(lead.id)} disabled={!lead.account_domain || (!chosen.includes(lead.id) && chosen.length >= 20)}
            onChange={(event) => setChosen((current) => (event.target.checked ? [...current, lead.id] : current.filter((item) => item !== lead.id)))} />
          <span dir="auto">{lead.account_name} <small className="muted">{lead.account_domain ?? t('biz.run.noWebsite')} · {stateLabel(lead.state)}</small></span></label>)}</div>}
      </fieldset>}
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy && !estimate ? t('dialog.working') : t('biz.run.estimate')}</button></div>
    </form>
    {estimate && <div className="bzBlock" role="group" aria-label={t('biz.run.estimateTitle')}>
      <h3>{t('biz.run.estimateTitle')}</h3>
      <p>{estimateLine(estimate.estimate)}</p>
      {estimate.plan.queries && <p className="muted bzHint" dir="auto">{t('biz.run.queries', { list: estimate.plan.queries.join(' · ') })}</p>}
      <p className="muted bzHint">{t('biz.run.left', { searches: estimate.estimate.searches_left_today, pages: estimate.estimate.fetches_left_today,
        budget: estimate.estimate.budget_cost_left_usd === null ? t('biz.run.noLimit') : `USD ${estimate.estimate.budget_cost_left_usd.toFixed(2)}` })}</p>
      {estimate.estimate.problems.map((item) => <Note key={item} error>{item}</Note>)}
      {estimate.estimate.notes.map((item) => <Note key={item}>{item}</Note>)}
      <div className="actions">
        <button type="button" disabled={busy || !estimate.estimate.fits} onClick={() => void confirm()}><Play aria-hidden="true" />{t('biz.run.confirm')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void cancel()}>{t('biz.run.discard')}</button>
      </div>
    </div>}
  </section>;
}

function RunPanel({ runId, write, onOpenProfile, onOpenLead, onFinished }: { runId: string; write: boolean; onOpenProfile: (leadId: string) => void; onOpenLead: (leadId: string) => void; onFinished: () => void }) {
  const [run, setRun] = useState<Load<PipelineRunDetail>>({ state: 'loading' }), [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setRun({ state: 'ready', value: await p.getRun(runId) }); } catch (error) { setRun({ state: 'error', message: failure(error, t('biz.run.title')) }); }
  }, [runId]);
  useEffect(() => { setRun({ state: 'loading' }); void load(); }, [load]);
  const active = run.state === 'ready' && isActive(run.value);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => { void load(); }, 2000);
    return () => { window.clearInterval(timer); finished.current(); };
  }, [active, load]);
  if (run.state === 'loading') return <Note>{t('biz.loading')}</Note>;
  if (run.state === 'error') return <Note error>{run.message}</Note>;
  const value = run.value;
  async function stop() {
    setProblem(null);
    try { await p.stopRun(value.id); await load(); } catch (error) { setProblem(failure(error, t('biz.run.stop'))); }
  }
  return <section className="surface bzCard" aria-labelledby="bz-run" aria-busy={active}>
    <div className="bzLeadHead"><h2 id="bz-run">{t(key(`biz.run.kind.${value.kind}`))}</h2><Badge tone={runTone(value.status)}>{runStatusLabel(value.status)}</Badge>
      <span className="muted bzHint">{when(value.created_at)}</span>
      {active && write && <button type="button" className="secondary" onClick={() => void stop()}><Square aria-hidden="true" />{t('biz.run.stop')}</button>}</div>
    <p className="bzHint">{t('biz.run.used', { searches: value.used.searches, maxSearches: value.estimate.searches, pages: value.used.page_fetches, maxPages: value.estimate.page_fetches, calls: value.used.model_calls })}</p>
    {value.stop_reason && <Note error={value.status === 'failed'}>{value.stop_reason}</Note>}
    {problem && <Note error>{problem}</Note>}
    {value.steps.length > 0 && <ol className="bzSteps" aria-label={t('biz.run.progress')}>{value.steps.slice(-30).map((step, index) => <li key={`${step.at}-${index}`}>
      <Badge tone={step.status === 'done' ? 'success' : step.status === 'skipped' ? 'neutral' : 'warning'}>{t(key(`biz.run.step.${step.status}`))}</Badge> <span dir="auto">{step.target ? `${step.target}: ` : ''}{step.summary}</span></li>)}</ol>}
    {value.leads.length > 0 && <div className="bzBlock"><h3>{t('biz.run.leadsFound', { count: value.leads.length })}</h3>
      <ul className="bzList">{value.leads.map((lead) => <li key={lead.id}><span dir="auto">{lead.account_name}</span> <small className="muted">{lead.domain ?? '-'} · {stateLabel(lead.state as never)}</small>
        <span className="bzInline"><button type="button" className="linkButton" onClick={() => onOpenProfile(lead.id)}>{t('biz.run.openProfile')}</button><button type="button" className="linkButton" onClick={() => onOpenLead(lead.id)}>{t('biz.run.openLead')}</button></span></li>)}</ul></div>}
    {(value.results.possible_duplicates ?? []).length > 0 && <Note>{t('biz.run.duplicates', { list: (value.results.possible_duplicates ?? []).map((item) => `${item.name} (${item.domain})`).join(', ') })}</Note>}
    {value.evidence.length > 0 && <div className="bzBlock"><h3>{t('biz.run.evidence')}</h3>
      <ul className="bzList">{value.evidence.slice(0, 40).map((item) => <li key={item.id}><a href={item.url} target="_blank" rel="noopener noreferrer nofollow" dir="auto">{item.title || item.url}</a>
        <small className="muted"> · {day(item.created_at)}</small><br /><small className="muted" dir="auto">{item.excerpt || '-'}</small></li>)}</ul></div>}
    {value.observations.length > 0 && <div className="bzBlock"><h3>{t('biz.run.observations', { count: formatNumber(value.observations.length) })}</h3>
      <div className="dataTableWrap"><table className="dataTable bzTable"><thead><tr><th scope="col">{t('biz.obs.signal')}</th><th scope="col">{t('biz.obs.value')}</th><th scope="col" className="bzHideSm">{t('biz.obs.excerpt')}</th><th scope="col" className="bzHideMd">{t('biz.obs.day')}</th></tr></thead>
        <tbody>{value.observations.slice(0, 80).map((item) => <tr key={item.id}><th scope="row">{item.signal_code}</th>
          <td dir="auto">{item.value_text || (item.value_num === null ? '-' : formatNumber(item.value_num))}</td>
          <td className="bzHideSm" dir="auto"><a href={item.url} target="_blank" rel="noopener noreferrer nofollow">{item.excerpt || item.url}</a></td><td className="bzHideMd">{day(item.observed_on)}</td></tr>)}</tbody></table></div></div>}
  </section>;
}

function ProfilePanel({ leadId, write, onClose, onOpenLead }: { leadId: string; write: boolean; onClose: () => void; onOpenLead: (leadId: string) => void }) {
  const [profile, setProfile] = useState<Load<LeadProfile>>({ state: 'loading' }), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    try { setProfile({ state: 'ready', value: await p.getLeadProfile(leadId) }); }
    catch (error) { setProfile({ state: 'error', message: failure(error, t('biz.profile.title')) }); }
  }, [leadId]);
  useEffect(() => { setProfile({ state: 'loading' }); void load(); }, [load]);
  async function apply(line: LeadProfile['breakdown'][number]) {
    if (!line.suggestion) return;
    setMessage(null);
    try {
      await b.scoreLead(leadId, { [line.criterion]: { score: line.suggestion.score, reason: line.suggestion.reason, evidence_ids: line.suggestion.evidence_ids } });
      setMessage({ text: t('biz.profile.applied', { criterion: criterionLabel(line.criterion) }) }); await load();
    } catch (error) { setMessage({ text: failure(error, t('biz.profile.title')), error: true }); }
  }
  return <section className="surface bzCard" aria-labelledby="bz-profile">
    <div className="bzLeadHead"><h2 id="bz-profile">{t('biz.profile.title')}</h2>
      <button type="button" className="linkButton" onClick={() => onOpenLead(leadId)}>{t('biz.run.openLead')}</button>
      <button type="button" className="secondary" onClick={onClose}>{t('biz.close')}</button></div>
    {profile.state === 'loading' && <Note>{t('biz.loading')}</Note>}
    {profile.state === 'error' && <Note error>{profile.message}</Note>}
    {profile.state === 'ready' && <>
      <div className="bzBlock"><h3>{t('biz.fit.title')}</h3>
        {profile.value.best_fit ? <p>{t('biz.fit.best', { name: profile.value.best_fit.product_name, score: profile.value.best_fit.score ?? 0 })}</p> : <p className="muted">{t('biz.fit.none')}</p>}
        {profile.value.product_fits.length === 0 ? <p className="muted">{t('biz.fit.noProducts')}</p> : <ul className="bzList">{profile.value.product_fits.map((fit) => <li key={fit.product_id}>
          <strong dir="auto">{fit.product_name}</strong> <Badge tone={fit.disqualified ? 'dangerTone' : (fit.score ?? 0) >= 3 ? 'success' : 'neutral'}>{fitText(fit)}</Badge>
          {fit.current && <small className="muted"> · {t('biz.fit.current')}</small>}<br /><small className="muted" dir="auto">{fit.reasons.join(' ')}</small></li>)}</ul>}
      </div>
      <div className="bzBlock"><h3>{t('biz.profile.score', { total: profile.value.score_total, known: profile.value.score_known })}</h3>
        <div className="dataTableWrap"><table className="dataTable bzTable"><thead><tr><th scope="col">{t('biz.profile.criterion')}</th><th scope="col" className="num">{t('biz.profile.now')}</th><th scope="col">{t('biz.profile.suggested')}</th></tr></thead>
          <tbody>{profile.value.breakdown.map((line) => <tr key={line.criterion}><th scope="row">{criterionLabel(line.criterion)}</th>
            <td className="num">{line.known ? line.score : t('biz.unknown')}</td>
            <td>{line.conversation_only ? <small className="muted">{t('biz.profile.conversationOnly')}</small>
              : line.suggestion ? <span className="bzInline"><Badge tone="warning">{t('biz.fit.score', { score: line.suggestion.score })}</Badge><small dir="auto">{line.suggestion.reason}</small>
                {write && <button type="button" className="secondary" onClick={() => void apply(line)}>{t('biz.profile.apply')}</button>}</span>
              : <small className="muted">{t('biz.profile.noSuggestion')}</small>}</td></tr>)}</tbody></table></div>
        <p className="muted bzHint">{t('biz.profile.rules')}</p>
        {message && <Note error={message.error}>{message.text}</Note>}
      </div>
      <div className="bzBlock"><h3>{t('biz.profile.facts', { count: profile.value.observations.length + profile.value.facts.length })}</h3>
        {profile.value.observations.length === 0 ? <p className="muted">{t('biz.profile.noFacts')}</p> : <ul className="bzList">{profile.value.observations.slice(0, 40).map((item) => <li key={item.id}>
          <strong>{profile.value.signal_labels[item.signal_code] ?? item.signal_code}</strong>: <span dir="auto">{item.value_text || (item.value_num === null ? '-' : formatNumber(item.value_num))}</span>
          <br /><small className="muted" dir="auto"><a href={item.url} target="_blank" rel="noopener noreferrer nofollow">{item.excerpt || item.url}</a> · {day(item.observed_on)}</small></li>)}</ul>}
      </div>
      <div className="bzBlock"><h3>{t('biz.profile.inferences', { count: profile.value.inferences.length })}</h3>
        {profile.value.inferences.length === 0 ? <p className="muted">{t('biz.profile.noInferences')}</p> : <ul className="bzList">{profile.value.inferences.map((item) => <li key={item.id}>
          <span dir="auto">{item.statement}</span> <Badge tone={item.status === 'accepted' ? 'success' : item.status === 'rejected' ? 'dangerTone' : 'neutral'}>{t(key(`biz.profile.inference.${item.status}`))}</Badge></li>)}</ul>}
      </div>
    </>}
  </section>;
}

function History({ onOpen }: { onOpen: (runId: string) => void }) {
  const [runs, setRuns] = useState<PipelineRun[]>([]), [total, setTotal] = useState(0), [state, setState] = useState<'loading' | 'ready' | 'denied'>('loading');
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const load = useCallback(async (offset: number) => {
    setBusy(true); setError(null);
    try {
      const page = await p.listRuns(20, offset);
      setRuns((current) => (offset === 0 ? page.items : [...current, ...page.items])); setTotal(page.total); setState('ready');
    } catch (problem) { setError(failure(problem, t('biz.run.history'))); setState((current) => (current === 'loading' ? 'ready' : current)); } finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(0); }, [load]);
  if (state === 'denied') return <Denied text={error ?? ''} />;
  return <section className="surface bzCard" aria-labelledby="bz-history"><h2 id="bz-history">{t('biz.run.history')}</h2>
    {state === 'loading' && <Note>{t('biz.loading')}</Note>}
    {state === 'ready' && runs.length === 0 && !error && <Empty title={t('biz.run.noneTitle')} text={t('biz.run.noneText')} />}
    {runs.length > 0 && <div className="dataTableWrap"><table className="dataTable bzTable"><thead><tr><th scope="col">{t('biz.run.kind')}</th><th scope="col">{t('biz.run.statusCol')}</th>
      <th scope="col" className="bzHideSm">{t('biz.run.usedCol')}</th><th scope="col" className="bzHideMd">{t('biz.run.when')}</th></tr></thead>
      <tbody>{runs.map((run) => <tr key={run.id}><th scope="row"><button type="button" className="linkButton" onClick={() => onOpen(run.id)}>{t(key(`biz.run.kind.${run.kind}`))}</button></th>
        <td><Badge tone={runTone(run.status)}>{runStatusLabel(run.status)}</Badge></td>
        <td className="bzHideSm">{t('biz.run.usedShort', { searches: run.used.searches, pages: run.used.page_fetches })}</td><td className="bzHideMd">{when(run.created_at)}</td></tr>)}</tbody></table></div>}
    <Pager shown={runs.length} total={total} busy={busy} error={error} onMore={() => void load(runs.length)} />
  </section>;
}
