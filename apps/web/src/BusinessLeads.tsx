// Business leads (docs/business.md#leads): the list with search, state filter and paging; a new lead
// with duplicate detection; CSV import with a dry run; and the lead drawer (state machine, the six
// criteria score, evidence kept apart from inferences, research, drafts that are never sent, the
// activity trail, do-not-contact and the step to an opportunity).
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Ban, ExternalLink, FileUp, Plus, Search, Sparkles, Trash2 } from 'lucide-react';
import type {
  BusinessDuplicate, BusinessLead, BusinessLeadDetail, BusinessProduct, BusinessSetup, ConsentBasis, Criterion, DraftChannel, ImportResult, LeadState,
} from '@anum/contracts';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';
import {
  CHANNELS, CONSENT_BASES, CRITERIA, LEAD_STATES, PAGE_SIZE, channelLabel, consentLabel, criterionLabel, emptyLeadDraft, leadBody, manualTransitions,
  needsReason, scoreText, stateLabel, stateTone, type LeadDraft,
} from './lib/business';
import * as b from './lib/businessApi';
import { Drawer, Modal } from './Dialog';
import { Badge, Empty, Note, Pager, ReasonDialog, day, failure, when } from './BusinessParts';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'denied'; text: string } | { kind: 'error'; text: string };

export function LeadsTab({ setup, products, openLeadId, onOpenLead, onOpenOpportunity, onChanged }: {
  setup: BusinessSetup; products: BusinessProduct[]; openLeadId: string | null; onOpenLead: (id: string | null) => void;
  onOpenOpportunity: (id: string) => void; onChanged: () => void;
}) {
  const [items, setItems] = useState<BusinessLead[]>([]), [total, setTotal] = useState(0), [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [q, setQ] = useState(''), [search, setSearch] = useState(''), [states, setStates] = useState<LeadState[]>([]), [sort, setSort] = useState('updated');
  const [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [creating, setCreating] = useState(false), [importing, setImporting] = useState(false);
  const request = useRef(0);
  const ids = { q: useId(), state: useId(), sort: useId() };

  const fetchFirst = useCallback(async () => {
    const id = ++request.current;
    setLoad({ kind: 'loading' });
    try {
      const page = await b.listLeads({ q: search, state: states, sort, limit: PAGE_SIZE, offset: 0 });
      if (id !== request.current) return;
      setItems(page.items); setTotal(page.total); setLoad({ kind: 'ready' }); setMore({ busy: false, error: null });
    } catch (error) {
      if (id !== request.current) return;
      setLoad(isPermissionDenied(error) ? { kind: 'denied', text: describeError(error) } : { kind: 'error', text: describeError(error) });
    }
  }, [search, states, sort]);
  useEffect(() => { void fetchFirst(); }, [fetchFirst]);

  async function loadMore() {
    setMore({ busy: true, error: null });
    try {
      const page = await b.listLeads({ q: search, state: states, sort, limit: PAGE_SIZE, offset: items.length });
      setItems((current) => [...current, ...page.items]); setTotal(page.total); setMore({ busy: false, error: null });
    } catch (error) { setMore({ busy: false, error: failure(error, t('biz.leads.loadMore')) }); }
  }
  const changed = () => { void fetchFirst(); onChanged(); };
  const filtered = Boolean(search || states.length);

  return <section className="bzSection" aria-labelledby="bz-leads-title">
    <div className="bzToolbar">
      <h2 id="bz-leads-title" className="visuallyHidden">{t('biz.tab.leads')}</h2>
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setSearch(q.trim()); }}>
        <label className="visuallyHidden" htmlFor={ids.q}>{t('biz.search')}</label>
        <input id={ids.q} type="search" required value={q} placeholder={t('biz.leads.searchPlaceholder')} onChange={(event) => { setQ(event.target.value); if (!event.target.value) setSearch(''); }} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
      </form>
      <label className="bzCompact" htmlFor={ids.state}><span>{t('biz.leads.state')}</span>
        <select id={ids.state} value={states[0] ?? ''} onChange={(event) => setStates(event.target.value ? [event.target.value as LeadState] : [])}>
          <option value="">{t('biz.leads.allStates')}</option>{LEAD_STATES.map((state) => <option key={state} value={state}>{stateLabel(state)}</option>)}
        </select></label>
      <label className="bzCompact" htmlFor={ids.sort}><span>{t('biz.leads.sort')}</span>
        <select id={ids.sort} value={sort} onChange={(event) => setSort(event.target.value)}>
          <option value="updated">{t('biz.leads.sortUpdated')}</option><option value="score">{t('biz.leads.sortScore')}</option><option value="created">{t('biz.leads.sortCreated')}</option>
        </select></label>
      {setup.permissions.write && <div className="bzToolbarEnd">
        <button type="button" className="secondary" onClick={() => setImporting(true)}><FileUp aria-hidden="true" />{t('biz.import.open')}</button>
        <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('biz.leads.new')}</button>
      </div>}
    </div>
    {load.kind === 'loading' && <Note>{t('biz.leads.loading')}</Note>}
    {load.kind === 'denied' && <Note error>{t('biz.denied', { message: load.text })}</Note>}
    {load.kind === 'error' && <Note error>{t('biz.failed', { what: t('biz.tab.leads'), message: load.text })} <button type="button" className="secondary" onClick={() => void fetchFirst()}>{t('common.tryAgain')}</button></Note>}
    {load.kind === 'ready' && items.length === 0 && (filtered
      ? <Empty title={t('biz.leads.noMatch')} text={t('biz.leads.noMatchText')} action={<button type="button" className="secondary" onClick={() => { setQ(''); setSearch(''); setStates([]); }}>{t('biz.clearFilters')}</button>} />
      : <Empty title={t('biz.leads.emptyTitle')} text={t('biz.leads.emptyText')} action={setup.permissions.write ? <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('biz.leads.new')}</button> : undefined} />)}
    {load.kind === 'ready' && items.length > 0 && <>
      <p className="bzCount" role="status">{t('biz.leads.count', { count: total })}</p>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('biz.tab.leads')}</caption>
        <thead><tr><th scope="col">{t('biz.col.company')}</th><th scope="col" className="bzHideSm">{t('biz.col.lead')}</th><th scope="col">{t('biz.col.state')}</th><th scope="col" className="num">{t('biz.col.score')}</th><th scope="col" className="bzHideMd">{t('biz.col.offer')}</th><th scope="col" className="bzHideMd">{t('biz.col.updated')}</th></tr></thead>
        <tbody>{items.map((lead) => {
          const score = scoreText(lead);
          return <tr key={lead.id} className={lead.id === openLeadId ? 'selected' : undefined}>
            <th scope="row"><button type="button" className="linkButton" onClick={() => onOpenLead(lead.id)}><span dir="auto">{lead.account_name}</span></button>{lead.block_reason && <small className="bzBlocked"><Ban aria-hidden="true" className="bzInlineIcon" />{t('biz.leads.doNotContact')}</small>}</th>
            <td className="bzHideSm"><span dir="auto">{lead.title}</span></td>
            <td><Badge tone={stateTone(lead.state)}>{stateLabel(lead.state)}</Badge></td>
            <td className="num">{score.total}<small>{score.known}</small></td>
            <td className="bzHideMd"><span dir="auto">{lead.product_name ?? t('biz.leads.noOffer')}</span></td>
            <td className="bzHideMd nowrap">{when(lead.updated_at)}</td>
          </tr>;
        })}</tbody>
      </table></div>
      <Pager shown={items.length} total={total} busy={more.busy} error={more.error} onMore={() => void loadMore()} />
    </>}
    {creating && <NewLead products={products} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); changed(); onOpenLead(id); }} />}
    {importing && <ImportLeads products={products} onClose={() => setImporting(false)} onImported={changed} />}
    {openLeadId && <LeadDrawer setup={setup} leadId={openLeadId} products={products} onClose={() => onOpenLead(null)} onChanged={changed} onOpenOpportunity={onOpenOpportunity} />}
  </section>;
}

// ---- new lead ------------------------------------------------------------------------------------

function NewLead({ products, onClose, onCreated }: { products: BusinessProduct[]; onClose: () => void; onCreated: (id: string) => void }) {
  const [draft, setDraft] = useState<LeadDraft>(emptyLeadDraft), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [dupes, setDupes] = useState<BusinessDuplicate[]>([]);
  const set = (key: keyof LeadDraft) => (event: { target: { value: string } }) => setDraft((current) => ({ ...current, [key]: event.target.value }));
  async function check() {
    if (draft.company.trim().length < 2 && !draft.website.trim() && !draft.email.trim()) { setDupes([]); return; }
    try { setDupes(await b.duplicates({ name: draft.company.trim() || undefined, website: draft.website.trim() || undefined, email: draft.email.trim() || undefined, product_id: draft.productId || undefined })); }
    catch { setDupes([]); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = leadBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onCreated((await b.createLead(body.value)).lead.id); } catch (error) { setProblem(failure(error, t('biz.leads.new'))); setBusy(false); }
  }
  return <Modal title={t('biz.leads.new')} className="formDialog bzWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('biz.leads.new')} className="bzForm">
      <fieldset className="bzFieldset"><legend>{t('biz.leads.company')}</legend>
        <div className="bzGrid2">
          <label className="field"><span>{t('biz.field.company')}</span><input value={draft.company} onChange={set('company')} onBlur={() => void check()} required data-autofocus /></label>
          <label className="field"><span>{t('biz.field.website')}</span><input value={draft.website} onChange={set('website')} onBlur={() => void check()} inputMode="url" placeholder="https://" /></label>
          <label className="field"><span>{t('biz.field.industry')}</span><input value={draft.industry} onChange={set('industry')} /></label>
          <label className="field"><span>{t('biz.field.city')}</span><input value={draft.city} onChange={set('city')} /></label>
        </div>
      </fieldset>
      <fieldset className="bzFieldset"><legend>{t('biz.leads.contactOptional')}</legend>
        <div className="bzGrid2">
          <label className="field"><span>{t('biz.field.contactName')}</span><input value={draft.contactName} onChange={set('contactName')} /></label>
          <label className="field"><span>{t('biz.field.email')}</span><input value={draft.email} onChange={set('email')} onBlur={() => void check()} type="email" /></label>
          <label className="field"><span>{t('biz.field.phone')}</span><input value={draft.phone} onChange={set('phone')} inputMode="tel" /></label>
          <label className="field"><span>{t('biz.field.consent')}</span><select value={draft.consent} onChange={set('consent')}>{CONSENT_BASES.map((basis) => <option key={basis} value={basis}>{consentLabel(basis)}</option>)}</select></label>
        </div>
        <p className="muted bzHint">{t('biz.leads.lawfulHint')}</p>
      </fieldset>
      <div className="bzGrid2">
        <label className="field"><span>{t('biz.field.leadTitle')}</span><input value={draft.title} onChange={set('title')} placeholder={draft.company || undefined} /></label>
        <label className="field"><span>{t('biz.field.offer')}</span><select value={draft.productId} onChange={(event) => { set('productId')(event); }}>
          <option value="">{t('biz.leads.noOffer')}</option>{products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </div>
      {dupes.length > 0 && <div className="notice bzDupes" role="status"><strong>{t('biz.leads.possibleDuplicates')}</strong><ul>{dupes.map((item) => <li key={`${item.kind}-${item.id}`}><span dir="auto">{item.label}</span>: {item.reason}</li>)}</ul></div>}
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('biz.leads.add')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- CSV import ---------------------------------------------------------------------------------

function ImportLeads({ products, onClose, onImported }: { products: BusinessProduct[]; onClose: () => void; onImported: () => void }) {
  const [csv, setCsv] = useState(''), [productId, setProductId] = useState(''), [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const fileId = useId(), textId = useId();
  async function run(dryRun: boolean) {
    if (!csv.trim()) { setProblem(t('biz.import.empty')); return; }
    setBusy(true); setProblem(null);
    try { const next = await b.importLeads(csv, dryRun, productId); setResult(next); if (!dryRun) onImported(); } catch (error) { setProblem(failure(error, t('biz.import.open'))); } finally { setBusy(false); }
  }
  return <Modal title={t('biz.import.title')} className="formDialog bzWideDialog" onClose={onClose}>
    <p className="muted">{t('biz.import.help')}</p>
    <code className="bzColumns">company, website, industry, country, city, contact_name, role, email, phone, consent_basis, lead_title, notes</code>
    <label className="field" htmlFor={fileId}><span>{t('biz.import.file')}</span><input id={fileId} type="file" accept=".csv,text/csv" onChange={(event) => { const file = event.target.files?.[0]; if (file) void file.text().then((text) => { setCsv(text); setResult(null); }); }} /></label>
    <label className="field" htmlFor={textId}><span>{t('biz.import.paste')}</span><textarea id={textId} rows={5} value={csv} onChange={(event) => { setCsv(event.target.value); setResult(null); }} dir="ltr" /></label>
    <label className="field"><span>{t('biz.field.offer')}</span><select value={productId} onChange={(event) => setProductId(event.target.value)}><option value="">{t('biz.leads.noOffer')}</option>{products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    {problem && <Note error>{problem}</Note>}
    {result && <div className="bzImportResult" role="status">
      <p><strong>{result.dry_run ? t('biz.import.preview') : t('biz.import.done')}</strong> {t('biz.import.counts', { created: result.created, duplicates: result.duplicates, suppressed: result.suppressed, invalid: result.invalid })}</p>
      <div className="dataTableWrap"><table className="dataTable"><thead><tr><th scope="col" className="num">{t('biz.import.row')}</th><th scope="col">{t('biz.col.company')}</th><th scope="col">{t('biz.col.state')}</th><th scope="col">{t('biz.import.message')}</th></tr></thead>
        <tbody>{result.rows.map((row) => <tr key={row.row}><td className="num">{row.row}</td><td dir="auto">{row.company}</td><td><Badge tone={row.status === 'created' ? 'success' : row.status === 'invalid' ? 'dangerTone' : 'warning'}>{t(`biz.import.status.${row.status}`)}</Badge></td><td>{row.message}</td></tr>)}</tbody></table></div>
    </div>}
    <div className="actions">
      <button type="button" className="secondary" disabled={busy} onClick={() => void run(true)}>{t('biz.import.check')}</button>
      <button type="button" disabled={busy || !result?.dry_run || result.created + result.suppressed === 0} onClick={() => void run(false)}>{t('biz.import.run')}</button>
      <button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button>
    </div>
  </Modal>;
}

// ---- lead drawer ----------------------------------------------------------------------------------

function LeadDrawer({ setup, leadId, products, onClose, onChanged, onOpenOpportunity }: {
  setup: BusinessSetup; leadId: string; products: BusinessProduct[]; onClose: () => void; onChanged: () => void; onOpenOpportunity: (id: string) => void;
}) {
  const [detail, setDetail] = useState<BusinessLeadDetail | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null), [busy, setBusy] = useState<string | null>(null), [asking, setAsking] = useState<LeadState | 'dnc' | null>(null);
  const reload = useCallback(async () => {
    try { setDetail(await b.getLead(leadId)); setProblem(null); } catch (error) { setProblem(failure(error, t('biz.lead.load'))); }
  }, [leadId]);
  useEffect(() => { void reload(); }, [reload]);

  async function act(name: string, fn: () => Promise<BusinessLeadDetail | void>, success?: string) {
    setBusy(name); setNotice(null); setProblem(null);
    try { const next = await fn(); if (next) setDetail(next); else await reload(); if (success) setNotice(success); onChanged(); }
    catch (error) { setProblem(failure(error, name)); } finally { setBusy(null); }
  }

  if (!detail) return <Drawer title={t('biz.lead.title')} onClose={onClose}>{problem ? <Note error>{problem}</Note> : <Note>{t('biz.lead.loading')}</Note>}</Drawer>;
  const { lead, account, contact } = detail;
  const write = setup.permissions.write;
  const moves = manualTransitions(detail.allowed_transitions);
  const open = !['won', 'lost', 'suppressed'].includes(lead.state);
  return <Drawer title={account.name} onClose={onClose}>
    <div className="bzDrawer">
      <div className="bzLeadHead">
        <Badge tone={stateTone(lead.state)}>{stateLabel(lead.state)}</Badge>
        <span className="bzScore">{scoreText(lead).total} <small>{scoreText(lead).known}</small></span>
        <span className="muted" dir="auto">{lead.title}</span>
      </div>
      {lead.block_reason && <Note error>{lead.block_reason}</Note>}
      {(lead.lost_reason || lead.suppressed_reason) && <p className="muted">{lead.lost_reason || lead.suppressed_reason}</p>}
      {notice && <Note>{notice}</Note>}
      {problem && <Note error>{problem}</Note>}
      {write && moves.length > 0 && <div className="bzInline" role="group" aria-label={t('biz.lead.moveTo')}>
        {moves.map((state) => <button key={state} type="button" className={state === 'lost' || state === 'suppressed' ? 'secondary dangerText' : 'secondary'} disabled={busy !== null}
          onClick={() => (needsReason(state) ? setAsking(state) : void act(stateLabel(state), () => b.transitionLead(lead.id, state)))}>{t('biz.lead.move', { state: stateLabel(state) })}</button>)}
        {detail.opportunity_id
          ? <button type="button" onClick={() => onOpenOpportunity(detail.opportunity_id!)}>{t('biz.lead.openOpportunity')}</button>
          : (['qualified', 'contacted', 'replied', 'meeting', 'offered'] as LeadState[]).includes(lead.state) && <button type="button" disabled={busy !== null}
            onClick={() => void act(t('biz.lead.createOpportunity'), async () => { const created = await b.createOpportunity({ lead_id: lead.id }); onOpenOpportunity(created.id); })}>{t('biz.lead.createOpportunity')}</button>}
      </div>}
      {lead.state === 'researched' || lead.state === 'discovered' ? detail.qualification_gaps.length > 0 && <div className="bzGaps"><strong>{t('biz.lead.toQualify')}</strong><ul>{detail.qualification_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul></div> : null}

      <section className="bzBlock" aria-labelledby="bz-company"><h3 id="bz-company">{t('biz.lead.companyContact')}</h3>
        <dl className="bzFacts">
          <div><dt>{t('biz.field.website')}</dt><dd>{account.website ? <a href={account.website} target="_blank" rel="noopener noreferrer" dir="ltr">{account.domain}<ExternalLink aria-hidden="true" className="bzInlineIcon" /></a> : t('biz.unknown')}</dd></div>
          <div><dt>{t('biz.field.industry')}</dt><dd dir="auto">{account.industry || t('biz.unknown')}</dd></div>
          <div><dt>{t('biz.field.city')}</dt><dd dir="auto">{[account.city, account.country].filter(Boolean).join(', ') || t('biz.unknown')}</dd></div>
          <div><dt>{t('biz.field.offer')}</dt><dd>{write ? <select aria-label={t('biz.field.offer')} value={lead.product_id ?? ''} disabled={busy !== null} onChange={(event) => void act(t('biz.field.offer'), () => b.updateLead(lead.id, { product_id: event.target.value || null }))}>
            <option value="">{t('biz.leads.noOffer')}</option>{products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select> : <span dir="auto">{lead.product_name ?? t('biz.leads.noOffer')}</span>}</dd></div>
        </dl>
        {contact ? <div className="bzContact">
          <p><strong dir="auto">{contact.name}</strong>{contact.role_title && <span className="muted" dir="auto">, {contact.role_title}</span>}</p>
          <p className="muted" dir="ltr">{[contact.email, contact.phone].filter(Boolean).join(' · ') || t('biz.lead.noChannel')}</p>
          {contact.opted_out.length > 0 && <p className="bzBlocked">{t('biz.lead.optedOut', { contacts: contact.opted_out.join(', ') })}</p>}
          <label className="field bzCompactField"><span>{t('biz.field.consent')}</span><select value={contact.consent_basis} disabled={!write || busy !== null}
            onChange={(event) => void act(t('biz.field.consent'), async () => { await b.updateContact(contact.id, { consent_basis: event.target.value as ConsentBasis }); })}>
            {CONSENT_BASES.map((basis) => <option key={basis} value={basis}>{consentLabel(basis)}</option>)}</select></label>
          {detail.consent_gap && <p className="muted bzHint">{detail.consent_gap}</p>}
          {write && (!contact.do_not_contact
            ? <button type="button" className="secondary dangerText" disabled={busy !== null} onClick={() => setAsking('dnc')}><Ban aria-hidden="true" />{t('biz.lead.markDnc')}</button>
            : setup.permissions.manage && <button type="button" className="secondary" disabled={busy !== null} onClick={() => setAsking('dnc')}>{t('biz.lead.clearDnc')}</button>)}
        </div> : <p className="muted">{t('biz.lead.noContact')}</p>}
      </section>

      <ScoreBlock detail={detail} write={write} busy={busy !== null} onSave={(criteria) => void act(t('biz.score.title'), () => b.scoreLead(lead.id, criteria), t('biz.score.saved'))} />
      <EvidenceBlock detail={detail} write={write} busy={busy !== null} act={act} />

      <section className="bzBlock" aria-labelledby="bz-research"><h3 id="bz-research">{t('biz.research.title')}</h3>
        <p className="muted bzHint">{t('biz.research.help', { runs: setup.settings.research_runs_per_day, searches: setup.settings.research_searches_per_day })}</p>
        {!setup.web_search && <Note>{t('biz.research.unavailable')}</Note>}
        {write && open && <button type="button" className="secondary" disabled={busy !== null || !setup.web_search} onClick={() => void act(t('biz.research.run'), () => b.researchLead(lead.id), t('biz.research.done'))}><Sparkles aria-hidden="true" />{busy === t('biz.research.run') ? t('biz.research.running') : t('biz.research.run')}</button>}
        {detail.research_runs[0] && <ol className="bzSteps">{detail.research_runs[0].steps.map((step) => <li key={step.step}><Badge tone={step.status === 'done' ? 'success' : step.status === 'skipped' ? 'neutral' : 'warning'}>{t(`biz.research.step.${step.step}`)}</Badge> {step.summary}</li>)}</ol>}
      </section>

      <DraftsBlock detail={detail} write={write && open} busy={busy !== null} act={act} onDraftNote={setNotice} />

      <section className="bzBlock" aria-labelledby="bz-activity"><h3 id="bz-activity">{t('biz.activity.title')}</h3>
        {write && <ActivityForm leadId={lead.id} blocked={Boolean(lead.block_reason)} onLogged={() => void reload().then(onChanged)} />}
        {detail.activities.length === 0 ? <p className="muted">{t('biz.activity.empty')}</p>
          : <ol className="bzTrail">{detail.activities.map((item) => <li key={item.id}><span dir="auto">{item.summary}</span><small>{when(item.created_at)} · {item.actor}</small></li>)}</ol>}
      </section>

      {setup.permissions.manage && !detail.opportunity_id && <div className="bzDanger"><button type="button" className="secondary dangerText" disabled={busy !== null}
        onClick={() => void act(t('biz.lead.delete'), async () => { await b.deleteLead(lead.id); onClose(); })}><Trash2 aria-hidden="true" />{t('biz.lead.delete')}</button></div>}
    </div>
    {asking && asking !== 'dnc' && <ReasonDialog title={t('biz.lead.move', { state: stateLabel(asking) })} label={t('biz.form.reason')} confirm={t('biz.lead.move', { state: stateLabel(asking) })}
      onCancel={() => setAsking(null)} onConfirm={async (reason) => { setDetail(await b.transitionLead(lead.id, asking, reason)); setAsking(null); onChanged(); }} />}
    {asking === 'dnc' && contact && <ReasonDialog title={contact.do_not_contact ? t('biz.lead.clearDnc') : t('biz.lead.markDnc')} label={t('biz.form.reason')} confirm={contact.do_not_contact ? t('biz.lead.clearDnc') : t('biz.lead.markDnc')}
      onCancel={() => setAsking(null)} onConfirm={async (reason) => { await b.contactDoNotContact(contact.id, !contact.do_not_contact, reason); setAsking(null); await reload(); onChanged(); }} />}
  </Drawer>;
}

type Act = (name: string, fn: () => Promise<BusinessLeadDetail | void>, success?: string) => Promise<void>;

function ScoreBlock({ detail, write, busy, onSave }: { detail: BusinessLeadDetail; write: boolean; busy: boolean; onSave: (criteria: Partial<Record<Criterion, { score: number | null; reason: string; evidence_ids: string[] }>>) => void }) {
  const initial = () => Object.fromEntries(CRITERIA.map((criterion) => {
    const value = detail.lead.score[criterion];
    return [criterion, { score: value?.score ?? null, reason: value?.reason ?? '', evidence_ids: value?.evidence_ids ?? [] }];
  })) as Record<Criterion, { score: number | null; reason: string; evidence_ids: string[] }>;
  const [form, setForm] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => { setForm(initial()); }, [detail.lead.updated_at]);
  const conversation = new Set(['urgency_confirmed', 'authorized_buyer']);
  function save(event: FormEvent) {
    event.preventDefault();
    const missing = CRITERIA.find((criterion) => form[criterion].score !== null && !form[criterion].reason.trim());
    if (missing) { setProblem(t('biz.score.reasonNeeded', { criterion: criterionLabel(missing) })); return; }
    setProblem(null);
    onSave(form);
  }
  return <section className="bzBlock" aria-labelledby="bz-score"><h3 id="bz-score">{t('biz.score.title')}</h3>
    <p className="muted bzHint">{t('biz.score.help')}</p>
    <form onSubmit={save} aria-label={t('biz.score.title')}>
      <div className="bzCriteria">{CRITERIA.map((criterion) => <fieldset key={criterion} className="bzCriterion">
        <legend>{criterionLabel(criterion)}{conversation.has(criterion) && <small className="muted"> {t('biz.score.conversationOnly')}</small>}</legend>
        <select aria-label={t('biz.score.valueOf', { criterion: criterionLabel(criterion) })} value={form[criterion].score ?? ''} disabled={!write}
          onChange={(event) => setForm((current) => ({ ...current, [criterion]: { ...current[criterion], score: event.target.value === '' ? null : Number(event.target.value) } }))}>
          <option value="">{t('biz.unknown')}</option>{[0, 1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}</select>
        <input aria-label={t('biz.score.reasonOf', { criterion: criterionLabel(criterion) })} value={form[criterion].reason} disabled={!write} placeholder={t('biz.score.reasonPlaceholder')}
          onChange={(event) => setForm((current) => ({ ...current, [criterion]: { ...current[criterion], reason: event.target.value } }))} />
        {detail.evidence.length > 0 && <details className="bzSources"><summary>{t('biz.score.sources', { count: form[criterion].evidence_ids.length })}</summary>
          {detail.evidence.map((item) => <label key={item.id} className="bzCheck"><input type="checkbox" disabled={!write} checked={form[criterion].evidence_ids.includes(item.id)}
            onChange={(event) => setForm((current) => ({ ...current, [criterion]: { ...current[criterion], evidence_ids: event.target.checked ? [...current[criterion].evidence_ids, item.id] : current[criterion].evidence_ids.filter((x) => x !== item.id) } }))} />
            <span dir="auto">{item.title || item.url}</span></label>)}</details>}
      </fieldset>)}</div>
      {problem && <Note error>{problem}</Note>}
      {write && <div className="actions"><button type="submit" disabled={busy}>{t('biz.score.save')}</button></div>}
    </form>
  </section>;
}

function EvidenceBlock({ detail, write, busy, act }: { detail: BusinessLeadDetail; write: boolean; busy: boolean; act: Act }) {
  const [url, setUrl] = useState(''), [title, setTitle] = useState(''), [excerpt, setExcerpt] = useState(''), [published, setPublished] = useState(''), [confidence, setConfidence] = useState('medium');
  const evidenceTitle = (id: string) => detail.evidence.find((item) => item.id === id)?.title || t('biz.evidence.removedSource');
  async function add(event: FormEvent) {
    event.preventDefault();
    await act(t('biz.evidence.add'), async () => {
      const next = await b.addEvidence(detail.lead.id, { url: url.trim(), title: title.trim(), excerpt: excerpt.trim(), published_on: published || null, confidence });
      setUrl(''); setTitle(''); setExcerpt(''); setPublished('');
      return next;
    });
  }
  return <>
    <section className="bzBlock" aria-labelledby="bz-evidence"><h3 id="bz-evidence">{t('biz.evidence.title')}</h3>
      <p className="muted bzHint">{t('biz.evidence.help')}</p>
      {detail.evidence.length === 0 ? <p className="muted">{t('biz.evidence.empty')}</p> : <ul className="bzEvidence">{detail.evidence.map((item) => <li key={item.id}>
        <a href={item.url} target="_blank" rel="noopener noreferrer nofollow" dir="auto">{item.title || item.url}<ExternalLink aria-hidden="true" className="bzInlineIcon" /></a>
        <span className="bzMeta"><Badge tone={item.confidence === 'high' ? 'success' : item.confidence === 'medium' ? 'warning' : 'neutral'}>{t(`biz.confidence.${item.confidence}`)}</Badge>
          {t('biz.evidence.published', { date: item.published_on ? day(item.published_on) : t('biz.unknown') })} · {t(`biz.source.${item.source}`)}</span>
        {item.excerpt && <blockquote dir="auto">{item.excerpt}</blockquote>}
        {write && <button type="button" className="iconButton" aria-label={t('biz.evidence.remove', { title: item.title || item.url })} disabled={busy} onClick={() => void act(t('biz.evidence.removeAction'), () => b.removeEvidence(item.id))}><Trash2 aria-hidden="true" /></button>}
      </li>)}</ul>}
      {write && <form className="bzEvidenceForm" onSubmit={(event) => void add(event)} aria-label={t('biz.evidence.add')}>
        <label className="field"><span>{t('biz.evidence.url')}</span><input value={url} onChange={(event) => setUrl(event.target.value)} required inputMode="url" placeholder="https://" dir="ltr" /></label>
        <label className="field"><span>{t('biz.evidence.titleField')}</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label className="field"><span>{t('biz.evidence.date')}</span><input type="date" value={published} onChange={(event) => setPublished(event.target.value)} /></label>
        <label className="field"><span>{t('biz.evidence.confidence')}</span><select value={confidence} onChange={(event) => setConfidence(event.target.value)}>{(['low', 'medium', 'high'] as const).map((value) => <option key={value} value={value}>{t(`biz.confidence.${value}`)}</option>)}</select></label>
        <label className="field bzSpan2"><span>{t('biz.evidence.excerpt')}</span><textarea rows={2} value={excerpt} onChange={(event) => setExcerpt(event.target.value)} /></label>
        <div className="actions bzSpan2"><button type="submit" className="secondary" disabled={busy}><Plus aria-hidden="true" />{t('biz.evidence.add')}</button></div>
      </form>}
    </section>
    <section className="bzBlock" aria-labelledby="bz-inferences"><h3 id="bz-inferences">{t('biz.inference.title')}</h3>
      <p className="muted bzHint">{t('biz.inference.help')}</p>
      {detail.inferences.length === 0 ? <p className="muted">{t('biz.inference.empty')}</p> : <ul className="bzInferences">{detail.inferences.map((item) => <li key={item.id}>
        <p dir="auto">{item.statement}</p>
        <span className="bzMeta"><Badge tone={item.status === 'accepted' ? 'success' : item.status === 'rejected' ? 'dangerTone' : 'warning'}>{t(`biz.inference.status.${item.status}`)}</Badge>
          {item.criterion && <span>{criterionLabel(item.criterion)}</span>}<span>{t(`biz.inference.by.${item.generator}`)}</span>
          <span>{t('biz.inference.basis', { sources: item.basis_evidence_ids.map(evidenceTitle).join(', ') || t('biz.unknown') })}</span></span>
        {write && item.status === 'proposed' && <span className="bzInline"><button type="button" className="secondary" disabled={busy} onClick={() => void act(t('biz.inference.accept'), () => b.reviewInference(item.id, 'accepted'))}>{t('biz.inference.accept')}</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void act(t('biz.inference.reject'), () => b.reviewInference(item.id, 'rejected'))}>{t('biz.inference.reject')}</button></span>}
      </li>)}</ul>}
    </section>
  </>;
}

function DraftsBlock({ detail, write, busy, act, onDraftNote }: { detail: BusinessLeadDetail; write: boolean; busy: boolean; act: Act; onDraftNote: (text: string | null) => void }) {
  const [channel, setChannel] = useState<DraftChannel>('email'), [language, setLanguage] = useState<'en' | 'ar'>('en');
  const [editing, setEditing] = useState<{ id: string; subject: string; body: string } | null>(null);
  const drafts = detail.drafts.filter((item) => item.status !== 'discarded');
  return <section className="bzBlock" aria-labelledby="bz-drafts"><h3 id="bz-drafts">{t('biz.draft.title')}</h3>
    <Note>{t('biz.draft.sendingOff')}</Note>
    {write && !detail.lead.block_reason && <div className="bzInline">
      <label className="bzCompact"><span>{t('biz.draft.channel')}</span><select value={channel} onChange={(event) => setChannel(event.target.value as DraftChannel)}>{CHANNELS.map((item) => <option key={item} value={item}>{channelLabel(item)}</option>)}</select></label>
      <label className="bzCompact"><span>{t('biz.draft.language')}</span><select value={language} onChange={(event) => setLanguage(event.target.value as 'en' | 'ar')}><option value="en">English</option><option value="ar">العربية</option></select></label>
      <button type="button" className="secondary" disabled={busy} onClick={() => void act(t('biz.draft.write'), async () => { const result = await b.createDraft(detail.lead.id, channel, language); onDraftNote(result.note); return result.lead; })}><Sparkles aria-hidden="true" />{t('biz.draft.write')}</button>
    </div>}
    {drafts.length === 0 ? <p className="muted">{t('biz.draft.empty')}</p> : <ul className="bzDrafts">{drafts.map((item) => <li key={item.id}>
      <span className="bzMeta"><Badge tone={item.status === 'approved' ? 'success' : 'neutral'}>{t(`biz.draft.status.${item.status}`)}</Badge><span>{channelLabel(item.channel)}</span><span>{t(`biz.draft.by.${item.generator}`)}</span></span>
      {editing?.id === item.id ? <form onSubmit={(event) => { event.preventDefault(); void act(t('biz.draft.save'), async () => { await b.updateDraft(item.id, editing.subject, editing.body); setEditing(null); }); }} aria-label={t('biz.draft.edit')}>
        {item.channel === 'email' && <label className="field"><span>{t('biz.draft.subject')}</span><input value={editing.subject} onChange={(event) => setEditing({ ...editing, subject: event.target.value })} dir="auto" /></label>}
        <label className="field"><span>{t('biz.draft.body')}</span><textarea rows={6} value={editing.body} onChange={(event) => setEditing({ ...editing, body: event.target.value })} dir="auto" /></label>
        <div className="bzInline"><button type="submit" disabled={busy}>{t('biz.draft.save')}</button><button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button></div>
      </form> : <>
        {item.subject && <p className="bzDraftSubject" dir="auto">{item.subject}</p>}
        <p className="bzDraftBody" dir="auto">{item.body}</p>
      </>}
      {item.issues.length > 0 && <ul className="bzIssues">{item.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      {write && editing?.id !== item.id && <span className="bzInline">
        <button type="button" className="secondary" disabled={busy} onClick={() => setEditing({ id: item.id, subject: item.subject, body: item.body })}>{t('biz.draft.edit')}</button>
        {item.status === 'draft' && <button type="button" className="secondary" disabled={busy || item.issues.length > 0} onClick={() => void act(t('biz.draft.approve'), async () => { await b.approveDraft(item.id); }, t('biz.draft.approved'))}>{t('biz.draft.approve')}</button>}
        <button type="button" className="secondary" disabled={busy} onClick={() => void act(t('biz.draft.discard'), async () => { await b.discardDraft(item.id); })}>{t('biz.draft.discard')}</button>
      </span>}
    </li>)}</ul>}
  </section>;
}

function ActivityForm({ leadId, blocked, onLogged }: { leadId: string; blocked: boolean; onLogged: () => void }) {
  const [kind, setKind] = useState('note'), [summary, setSummary] = useState(''), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (summary.trim().length < 2) { setProblem(t('biz.activity.required')); return; }
    setBusy(true); setProblem(null);
    try { await b.logActivity(leadId, kind, summary.trim(), key.current); key.current = crypto.randomUUID(); setSummary(''); onLogged(); }
    catch (error) { setProblem(failure(error, t('biz.activity.log'))); } finally { setBusy(false); }
  }
  const kinds = blocked ? (['note'] as const) : (['note', 'contact_logged', 'call_logged', 'meeting_logged', 'reply_logged'] as const);
  return <form className="bzActivityForm" onSubmit={(event) => void submit(event)} aria-label={t('biz.activity.log')}>
    <label className="bzCompact"><span>{t('biz.activity.kind')}</span><select value={kind} onChange={(event) => setKind(event.target.value)}>{kinds.map((item) => <option key={item} value={item}>{t(`biz.activity.kind.${item}`)}</option>)}</select></label>
    <label className="field bzGrow"><span className="visuallyHidden">{t('biz.activity.summary')}</span><input value={summary} onChange={(event) => setSummary(event.target.value)} placeholder={t('biz.activity.placeholder')} /></label>
    <button type="submit" className="secondary" disabled={busy}>{t('biz.activity.log')}</button>
    {problem && <Note error>{problem}</Note>}
  </form>;
}
