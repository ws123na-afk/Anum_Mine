// Sales desk for WhatsApp leads. Every action hits the authenticated API.
// Quotes and follow-ups are drafts only, never silently sent to customers.
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowUpRight, ClipboardList, RefreshCcw } from 'lucide-react';
import { describeError } from './lib/errors';
import { formatDateTime, getLocale, subscribeLocale } from './lib/i18n';
import {
  createSalesQuote, getSalesOverview, getSalesLeadHistory, listSalesLeads, proposeSalesFollowup, requestSalesQuoteDelivery,
  recordSalesConsent, getSalesConsentHistory,
  transitionSalesLead, updateSalesLead,
  type SalesLead, type SalesOverview, type SalesStage, type SalesHistory,
} from './lib/salesApi';
import './salesDashboard.css';
import { SalesCampaignAndOperations } from './SalesCampaignAndOperations';
import { useIsOwner } from './useIsOwner';

const STAGES: SalesStage[] = ['new', 'qualifying', 'qualified', 'quoted', 'negotiating', 'won', 'lost', 'disqualified'];
const LABELS = {
  en: {
    title: 'Sales pipeline', subtitle: 'WhatsApp enquiries, marketing sources and quotations in one place',
    leads: 'Leads', qualified: 'Qualified', quoted: 'Quoted', won: 'Won', lost: 'Lost',
    search: 'Search by name, company or phone', all: 'All stages', client: 'Customer / company',
    service: 'Service', stage: 'Stage', owner: 'Assigned to', source: 'Campaign source',
    updated: 'Updated', details: 'Lead details', save: 'Save changes', company: 'Company',
    name: 'Contact name', phone: 'Phone', requirements: 'Requirements', min: 'Budget from',
    max: 'Budget to', assignment: 'Assigned user ID', change: 'Move to stage',
    quotation: 'Quotation draft', item: 'Service description', price: 'Amount', currency: 'Currency',
    terms: 'Terms', createQuote: 'Save quote draft', followup: 'Propose follow-up',
    due: 'Due date & time', channel: 'Channel', purpose: 'Reason', createFollowup: 'Save follow-up proposal',
    empty: 'No matching leads yet. Incoming eligible WhatsApp enquiries appear here after ingestion.',
    noSelection: 'Select a lead to review its history and next steps.',
    limited: 'Showing up to 100 recent leads. Totals may exclude older records.',
    consent: 'Marketing consent', consentNo: 'Not verified', consentYes: 'Verified',
    reload: 'Refresh', saved: 'Saved successfully', draftSaved: 'Draft saved. Approval is still required.',
    error: 'Unable to load the sales pipeline', quoteNotice: 'Draft only: not delivered to customer.',
    followNotice: 'Proposal only: no customer message will be sent.',
    next: 'Next action', openWhatsApp: 'WhatsApp inbox',
  },
  ar: {
    title: 'إدارة المبيعات', subtitle: 'طلبات واتساب ومصادر التسويق وعروض الأسعار في مكان واحد',
    leads: 'العملاء المحتملون', qualified: 'مؤهلون', quoted: 'عروض أسعار', won: 'مكتمل', lost: 'مفقود',
    search: 'بحث بالاسم أو الشركة أو الهاتف', all: 'جميع المراحل', client: 'العميل / الشركة',
    service: 'الخدمة', stage: 'المرحلة', owner: 'المسؤول', source: 'الحملة التسويقية',
    updated: 'آخر تحديث', details: 'تفاصيل العميل', save: 'حفظ التغييرات', company: 'الشركة',
    name: 'اسم العميل', phone: 'الهاتف', requirements: 'متطلبات المشروع', min: 'الميزانية من',
    max: 'الميزانية إلى', assignment: 'معرّف المسؤول', change: 'نقل للمرحلة',
    quotation: 'مسودة عرض سعر', item: 'وصف الخدمة', price: 'المبلغ', currency: 'العملة',
    terms: 'الشروط', createQuote: 'حفظ مسودة العرض', followup: 'اقتراح متابعة',
    due: 'تاريخ ووقت المتابعة', channel: 'القناة', purpose: 'السبب', createFollowup: 'حفظ اقتراح المتابعة',
    empty: 'لا توجد نتائج. تظهر استفسارات واتساب هنا بعد تسجيلها.',
    noSelection: 'اختر عميلاً لمراجعة بياناته وخطوات العمل التالية.',
    limited: 'يتم عرض آخر 100 عميل كحد أقصى وقد لا تشمل الأرقام السجلات الأقدم.',
    consent: 'موافقة التسويق', consentNo: 'غير مؤكدة', consentYes: 'مؤكدة',
    reload: 'تحديث', saved: 'تم الحفظ بنجاح', draftSaved: 'تم حفظ المسودة وتحتاج إلى الموافقة.',
    error: 'تعذر تحميل بيانات المبيعات', quoteNotice: 'مسودة فقط، لم تُرسل إلى العميل.',
    followNotice: 'اقتراح فقط، لم تُرسل أي رسالة إلى العميل.',
    next: 'الخطوة التالية', openWhatsApp: 'صندوق واتساب',
  },
} as const;
const STAGE_TEXT: Record<SalesStage, {en:string; ar:string}> = {
  new: {en:'New', ar:'جديد'}, qualifying:{en:'Qualifying',ar:'جارٍ التأهيل'},
  qualified:{en:'Qualified',ar:'مؤهل'}, quoted:{en:'Quoted',ar:'تم إعداد عرض'},
  negotiating:{en:'Negotiating',ar:'تفاوض'}, won:{en:'Won',ar:'ناجح'},
  lost:{en:'Lost',ar:'مفقود'}, disqualified:{en:'Disqualified',ar:'غير مؤهل'},
};
const SERVICE: Record<string, {en:string;ar:string}> = {
  website:{en:'Website',ar:'موقع إلكتروني'}, mobile_app:{en:'Mobile app',ar:'تطبيق جوال'},
  ecommerce:{en:'Online store',ar:'متجر إلكتروني'},
  business_software:{en:'Business software',ar:'نظام أعمال'},
  other:{en:'Other',ar:'أخرى'},
};

export function SalesDashboard() {
  const [locale, setLocale] = useState(getLocale);
  const [data, setData] = useState<SalesLead[]>([]);
  const [overview, setOverview] = useState<SalesOverview | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<SalesStage | 'all'>('all');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const labels = LABELS[locale];
  const selected = data.find((lead) => lead.id === selectedId) ?? null;
  useEffect(() => subscribeLocale(setLocale), []);

  const refresh = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [result, summary] = await Promise.all([listSalesLeads(), getSalesOverview()]);
      setData(result.items); setOverview(summary);
    } catch (cause) { setError(describeError(cause)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const visible = useMemo(() => data.filter((lead) => {
    if (filter !== 'all' && lead.status !== filter) return false;
    const search = [lead.company_name, lead.contact_name, lead.phone_e164, lead.assigned_to, lead.campaign_hint]
      .filter(Boolean).join(' ').toLocaleLowerCase();
    return search.includes(query.trim().toLocaleLowerCase());
  }), [data, filter, query]);

  const action = async (operation: () => Promise<unknown>, message = labels.saved) => {
    setBusy(true); setError(''); setNotice('');
    try { await operation(); setNotice(message); await refresh(); }
    catch (cause) { setError(describeError(cause)); }
    finally { setBusy(false); }
  };
  return <section className="salesDesk" dir={locale === 'ar' ? 'rtl' : 'ltr'} aria-label={labels.title}>
    <header className="salesHero">
      <div><h2>{labels.title}</h2><p>{labels.subtitle}</p></div>
      <div className="salesActions"><a href="#whatsapp"><ArrowUpRight size={16}/>{labels.openWhatsApp}</a>
        <button type="button" onClick={() => void refresh()} disabled={busy || loading}><RefreshCcw size={16}/>{labels.reload}</button></div>
    </header>
    {error && <p role="alert" className="salesError"><AlertCircle size={18}/>{error}</p>}
    {notice && <p role="status" className="salesSuccess">{notice}</p>}
    <div className="salesKpis">
      {([
        [labels.leads, overview?.leads ?? 0], [labels.qualified, overview?.qualified ?? 0],
        [labels.quoted, overview?.quoted ?? 0], [labels.won, overview?.won ?? 0],
        [labels.lost, overview?.lost ?? 0],
      ] as const).map(([label, count]) => <div className="salesKpi" key={label}><span>{label}</span><strong>{count}</strong></div>)}
    </div>
    <p className="salesHint">{labels.limited}</p>
    <SalesCampaignAndOperations locale={locale}/>
    <div className="salesSplit">
      <section className="salesListing" aria-label={labels.leads}>
        <div className="salesFilters"><input aria-label={labels.search} placeholder={labels.search}
          value={query} onChange={(e) => setQuery(e.target.value)}/>
          <select aria-label={labels.stage} value={filter} onChange={(e) => setFilter(e.target.value as SalesStage | 'all')}>
            <option value="all">{labels.all}</option>
            {STAGES.map((stage) => <option key={stage} value={stage}>{STAGE_TEXT[stage][locale]}</option>)}
          </select></div>
        {loading ? <p className="salesEmpty" role="status">...</p> :
          visible.length === 0 ? <p className="salesEmpty">{labels.empty}</p> :
          <div className="salesList" role="list">{visible.map((lead) => <button type="button" role="listitem" key={lead.id}
            aria-pressed={lead.id === selectedId} onClick={() => {setSelectedId(lead.id);setError('');setNotice('');}}
            className={lead.id === selectedId ? 'salesLead selected' : 'salesLead'}>
            <span className="salesLeadTop"><strong>{lead.company_name || lead.contact_name || lead.phone_e164 || lead.contact_id}</strong>
              <small>{STAGE_TEXT[lead.status][locale]}</small></span>
            <span className="salesLeadBottom"><span>{SERVICE[lead.intent]?.[locale] || lead.intent}</span>
              <time dateTime={lead.updated_at}>{formatDateTime(lead.updated_at)}</time></span>
            {lead.campaign_hint && <span className="salesCampaign">{labels.source}: {lead.campaign_hint}</span>}
          </button>)}</div>}
      </section>
      <section className="salesDetail" aria-label={labels.details}>
        {selected ? <LeadEditor key={selected.id + ':' + selected.revision} lead={selected}
          locale={locale} disabled={busy} save={(fields) => action(() => updateSalesLead(selected, fields))}
          changeStage={(stage) => action(() => transitionSalesLead(selected, stage))}
          createQuote={(items, currency, terms) => action(() => createSalesQuote(selected.id, items, currency, terms), labels.draftSaved)}
          followUp={(due, channel, purpose) => action(() => proposeSalesFollowup(selected.id, due, channel, purpose), labels.draftSaved)}
          recordConsent={(granted, note, evidenceId) => action(() => recordSalesConsent(selected.id, granted, note, evidenceId))} /> :
          <div className="salesEmpty"><ClipboardList size={36}/><p>{labels.noSelection}</p></div>}
      </section>
    </div>
  </section>;
}

function LeadEditor({lead, locale, disabled, save, changeStage, createQuote, followUp, recordConsent}: {
  lead: SalesLead; locale:'ar'|'en'; disabled:boolean;
  save:(value:Record<string,unknown>)=>void; changeStage:(value:SalesStage)=>void;
  createQuote:(items:{description:string;amount:string}[],currency:string,terms:string)=>void;
  followUp:(due:string,channel:string,purpose:string)=>void;
  recordConsent:(granted:boolean,note:string,evidenceId:string)=>void;
}) {
  const l = LABELS[locale];
  const [company,setCompany] = useState(lead.company_name);
  const [name,setName] = useState(lead.contact_name);
  const [requirements,setRequirements] = useState(lead.requirements);
  const [assignment,setAssignment] = useState(lead.assigned_to || '');
  const [budgetMin,setBudgetMin] = useState(lead.budget_min || '');
  const [budgetMax,setBudgetMax] = useState(lead.budget_max || '');
  const [nextStage,setNextStage] = useState<SalesStage>(lead.status);
  const [quoteDesc,setQuoteDesc] = useState('');
  const [quoteAmount,setQuoteAmount] = useState('');
  const [quoteTerms,setQuoteTerms] = useState('');
  const [due,setDue] = useState('');
  const [channel,setChannel] = useState('whatsapp');
  const [purpose,setPurpose] = useState('');
  const submitDetails = (event:FormEvent) => {
    event.preventDefault();
    save({company_name:company, contact_name:name, requirements,
      ...(assignment ? {assigned_to:assignment} : {}),
      ...(budgetMin ? {budget_min:budgetMin} : {}),
      ...(budgetMax ? {budget_max:budgetMax} : {})});
  };
  return <div className="salesEditor">
    <div className="salesDetailHeading"><h3>{l.details}</h3><span>{SERVICE[lead.intent]?.[locale] || lead.intent}</span></div>
    <dl className="salesMetadata"><div><dt>{l.phone}</dt><dd><bdi>{lead.phone_e164 || '—'}</bdi></dd></div>
      <div><dt>{l.consent}</dt><dd>{lead.marketing_consent ? l.consentYes : l.consentNo}</dd></div>
      <div><dt>{l.source}</dt><dd>{lead.campaign_hint || '—'}</dd></div></dl>
    <form onSubmit={submitDetails} className="salesForm">
      <div className="salesFields"><label>{l.company}<input value={company} maxLength={200} onChange={e=>setCompany(e.target.value)}/></label>
        <label>{l.name}<input value={name} maxLength={200} onChange={e=>setName(e.target.value)}/></label>
        <label>{l.assignment}<input value={assignment} maxLength={160} onChange={e=>setAssignment(e.target.value)}/></label>
        <label>{l.min}<input type="number" min="0" step="0.01" value={budgetMin} onChange={e=>setBudgetMin(e.target.value)}/></label>
        <label>{l.max}<input type="number" min="0" step="0.01" value={budgetMax} onChange={e=>setBudgetMax(e.target.value)}/></label>
      </div>
      <label>{l.requirements}<textarea rows={3} maxLength={4000} value={requirements} onChange={e=>setRequirements(e.target.value)}/></label>
      <button disabled={disabled} type="submit">{l.save}</button>
    </form>
    <div className="salesWorkflow"><label>{l.change}<select value={nextStage} onChange={e=>setNextStage(e.target.value as SalesStage)}>
        {STAGES.map(stage=><option key={stage} value={stage}>{STAGE_TEXT[stage][locale]}</option>)}
      </select></label><button type="button" disabled={disabled || nextStage === lead.status} onClick={()=>changeStage(nextStage)}>{l.change}</button></div>
    <section className="salesSubForm"><h4>{l.quotation}</h4><p>{l.quoteNotice}</p>
      <form onSubmit={e=>{e.preventDefault();createQuote([{description:quoteDesc,amount:quoteAmount}],lead.currency,quoteTerms);}}>
        <label>{l.item}<input required maxLength={200} value={quoteDesc} onChange={e=>setQuoteDesc(e.target.value)}/></label>
        <label>{l.price}<input required min="0" step="0.01" type="number" value={quoteAmount} onChange={e=>setQuoteAmount(e.target.value)}/></label>
        <label>{l.terms}<textarea maxLength={4000} rows={2} value={quoteTerms} onChange={e=>setQuoteTerms(e.target.value)}/></label>
        <button disabled={disabled} type="submit">{l.createQuote} ({lead.currency})</button>
      </form>
    </section>
    <section className="salesSubForm"><h4>{l.followup}</h4><p>{l.followNotice}</p>
      <form onSubmit={e=>{e.preventDefault();if(due)followUp(new Date(due).toISOString(),channel,purpose);}}>
        <label>{l.due}<input required type="datetime-local" value={due} onChange={e=>setDue(e.target.value)}/></label>
        <label>{l.channel}<select value={channel} onChange={e=>setChannel(e.target.value)}>
          <option value="whatsapp">WhatsApp</option><option value="email">Email</option><option value="call">{locale==='ar'?'مكالمة':'Call'}</option>
        </select></label>
        <label>{l.purpose}<input required maxLength={500} value={purpose} onChange={e=>setPurpose(e.target.value)}/></label>
        <button disabled={disabled} type="submit">{l.createFollowup}</button>
      </form>
    </section>
    <SalesConsentPanel leadId={lead.id} locale={locale} verified={lead.marketing_consent}
      disabled={disabled} onRecord={recordConsent}/>
    <SalesHistoryPanel leadId={lead.id} locale={locale} revision={lead.revision}/>
  </div>;
}

function SalesHistoryPanel({leadId,locale,revision}:{leadId:string;locale:'en'|'ar';revision:number}) {
  const [data,setData]=useState<SalesHistory|null>(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  const [reload,setReload]=useState(0);
  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');
    void getSalesLeadHistory(leadId).then(value=>{if(active)setData(value);})
      .catch(cause=>{if(active)setError(describeError(cause));})
      .finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[leadId,revision,reload]);
  const ar=locale==='ar';
  return <section className="salesSubForm" aria-label={ar?'سجل العميل':'Customer timeline'}>
    <h4>{ar?'سجل العميل وعروض الأسعار والمتابعات':'Customer history, quotations and follow-ups'}</h4>
    {loading && <p role="status">{ar?'جار التحميل':'Loading history...'}</p>}
    {error && <p role="alert" className="salesError">{error}</p>}
    {notice && <p role="status" className="salesSuccess">{notice}</p>}
    {data && <>
      <h5>{ar?'عروض الأسعار':'Quotation drafts'} ({data.quotes.length})</h5>
      {data.quotes.length===0 && <p>{ar?'لا توجد عروض':'No quotations yet'}</p>}
      {data.quotes.map(q=><article className="salesHistoryRow" key={q.id}>
        <strong>{q.currency} {q.total}</strong><span>{q.status}</span>
        <small>{formatDateTime(q.created_at)}</small>
        {q.status==='draft' && <button type="button" disabled={busy} onClick={()=>{
          setBusy(true);setError('');setNotice('');
          void requestSalesQuoteDelivery(leadId,q.id).then(result=>{
            setNotice(result.detail);setReload(x=>x+1);
          }).catch(cause=>setError(describeError(cause))).finally(()=>setBusy(false));
        }}>{ar?'طلب الموافقة على إرسال العرض عبر واتساب':'Request approved WhatsApp quotation delivery'}</button>}
        <ul>{q.line_items.map((item,i)=><li key={i}>{item.description}: {item.amount}</li>)}</ul>
      </article>)}
      <h5>{ar?'المتابعات':'Follow-up proposals'} ({data.followups.length})</h5>
      {data.followups.length===0 && <p>{ar?'لا توجد متابعات':'No follow-ups yet'}</p>}
      {data.followups.map(item=><article className="salesHistoryRow" key={item.id}>
        <strong>{item.purpose}</strong><span>{item.channel} · {item.status}</span>
        <small>{formatDateTime(item.due_at)}</small>
      </article>)}
      <h5>{ar?'سجل التغييرات':'Activity history'} ({data.events.length})</h5>
      {data.events.map(item=><article className="salesHistoryRow" key={item.id}>
        <strong>{item.event}</strong><span>{item.status??''}</span>
        <small>{formatDateTime(item.occurred_at)} · {item.actor_id}</small>
      </article>)}
    </>}
  </section>;
}

function SalesConsentPanel({leadId,locale,verified,disabled,onRecord}:{
  leadId:string;locale:'en'|'ar';verified:boolean;disabled:boolean;
  onRecord:(granted:boolean,note:string,evidenceId:string)=>void;
}) {
  const owner=useIsOwner()===true;
  const ar=locale==='ar';
  const [note,setNote]=useState('');
  const [evidenceId,setEvidenceId]=useState('');
  const [granted,setGranted]=useState(false);
  const [history,setHistory]=useState<Array<{
    granted:boolean;recorded_by:string;recorded_at:string;
    evidence_note:string;evidence_message_id:string|null;
  }>>([]);
  const [error,setError]=useState('');
  useEffect(()=>{
    if(!owner)return;
    let active=true;
    void getSalesConsentHistory(leadId).then(result=>{if(active)setHistory(result.items);})
      .catch(cause=>{if(active)setError(describeError(cause));});
    return()=>{active=false;};
  },[leadId,owner,verified]);
  return <section className="salesConsentPanel" aria-label={ar?'إثبات موافقة التسويق':'Marketing consent evidence'}>
    <h4>{ar?'إثبات موافقة التسويق':'Marketing consent evidence'}</h4>
    <p>{verified ? (ar?'موافقة مثبتة':'Verified consent on record') :
      (ar?'لم يتم إثبات موافقة تسويقية بعد':'Marketing consent not yet verified')}</p>
    {owner&&<form onSubmit={event=>{
      event.preventDefault();onRecord(granted,note,evidenceId);
    }}>
      <label>{ar?'الحالة المطلوب تسجيلها':'Consent decision'}
        <select value={granted?'granted':'revoked'} onChange={e=>setGranted(e.target.value==='granted')}>
          <option value="revoked">{ar?'سحب الموافقة':'Withdraw consent'}</option>
          <option value="granted">{ar?'تأكيد موافقة صريحة':'Verify explicit opt-in'}</option>
        </select></label>
      <label>{ar?'معرّف رسالة واتساب من العميل':'Evidence: customer WhatsApp message ID'}
        <input value={evidenceId} onChange={e=>setEvidenceId(e.target.value)}
          maxLength={80} placeholder={ar?'مطلوب لتأكيد الموافقة':'Required to verify opt-in'}/></label>
      <label>{ar?'وصف الدليل والسبب':'Evidence and reason'}
        <textarea value={note} onChange={e=>setNote(e.target.value)} required
          minLength={3} maxLength={500} rows={2}/></label>
      <button disabled={disabled||note.trim().length<3||granted&&!evidenceId.trim()}
        type="submit">{ar?'تسجيل قرار الموافقة':'Record verified consent decision'}</button>
    </form>}
    {error&&<p role="alert" className="salesError">{error}</p>}
    {history.length>0&&<details><summary>{ar?'سجل قرارات الموافقة':'Consent decision history'} ({history.length})</summary>
      {history.map((item,i)=><div className="salesHistoryRow" key={i}>
        <strong>{item.granted?(ar?'تم منح الموافقة':'Granted'):(ar?'سحب الموافقة':'Withdrawn')}</strong>
        <span>{item.evidence_note}</span>
        <small>{formatDateTime(item.recorded_at)} · {item.recorded_by}</small>
      </div>)}
    </details>}
  </section>;
}
