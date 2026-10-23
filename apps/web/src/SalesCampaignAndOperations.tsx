import { useCallback, useEffect, useState } from 'react';
import { describeError } from './lib/errors';
import { formatDateTime } from './lib/i18n';
import { useIsOwner } from './useIsOwner';
import * as wa from './lib/whatsappApi';
import type { WaTemplate } from './lib/whatsapp';
import {
  approveSalesFollowup, cancelSalesFollowup,
  getSalesAutomationSettings, getSalesAutomationReadiness, getSalesCampaignConversions, getSalesOperations,
  saveSalesAutomationSettings,
  type SalesAutomationSettings, type SalesAutomationReadiness, type SalesCampaignConversions, type SalesOperationsQueue,
} from './lib/salesApi';

export function SalesCampaignAndOperations({locale}:{locale:'ar'|'en'}) {
  const ar=locale==='ar';
  const owner=useIsOwner()===true;
  const [campaigns,setCampaigns]=useState<SalesCampaignConversions|null>(null);
  const [operations,setOperations]=useState<SalesOperationsQueue|null>(null);
  const [settings,setSettings]=useState<SalesAutomationSettings|null>(null);
  const [readiness,setReadiness]=useState<SalesAutomationReadiness|null>(null);
  const [enabled,setEnabled]=useState(false);
  const [paused,setPaused]=useState(true);
  const [dailyCap,setDailyCap]=useState(0);
  const [templates,setTemplates]=useState<WaTemplate[]>([]);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState(false);

  const load=useCallback(async()=>{
    setLoading(true);
    const [campaignResult,queueResult,settingsResult,readinessResult] = await Promise.allSettled([
      getSalesCampaignConversions(), getSalesOperations(), getSalesAutomationSettings(),
      getSalesAutomationReadiness(),
    ]);
    if(campaignResult.status==='fulfilled')setCampaigns(campaignResult.value);
    else setError(describeError(campaignResult.reason));
    if(queueResult.status==='fulfilled')setOperations(queueResult.value);
    else setError(describeError(queueResult.reason));
    if(settingsResult.status==='fulfilled'){
      const policy=settingsResult.value;
      setSettings(policy);setEnabled(policy.enabled);setPaused(policy.paused);
      setDailyCap(policy.max_sends_per_day);
    }
    if(readinessResult.status==='fulfilled')setReadiness(readinessResult.value);
    setLoading(false);
  },[]);
  useEffect(()=>{void load();},[load]);
  useEffect(()=>{
    if(!owner)return;
    let active=true;
    void wa.listTemplates().then(found=>{if(active)setTemplates(found);})
      .catch(()=>{/* WhatsApp is not necessarily connected yet. */});
    return ()=>{active=false;};
  },[owner]);

  const action=async(run:()=>Promise<unknown>,description:string)=>{
    setBusy(true);setError('');setNotice('');
    try{await run();setNotice(description);await load();}
    catch(cause){setError(describeError(cause));}
    finally{setBusy(false);}
  };

  const approvedTemplates=templates.filter(item=>item.auto_send && item.status==='APPROVED');
  return <section className="salesReporting" aria-label={ar?'التسويق وعمليات المبيعات':'Marketing and sales operations'}>
    <div className="salesReportHeader">
      <h3>{ar?'أداء الحملات التسويقية':'Marketing campaign conversions'}</h3>
      <p>{ar?'هذه الإحصائيات لكل سجلات مساحة العمل، لكن مصدر الحملة يحدده نص العميل ولا يمثل تتبعاً إعلانياً مؤكداً.':
        'Counts cover the workspace; campaign markers supplied by customers are not verified ad attribution.'}</p>
    </div>
    {loading&&<p role="status">{ar?'جار التحميل':'Loading reports...'}</p>}
    {error&&<p role="alert" className="salesError">{error}</p>}
    {notice&&<p role="status" className="salesSuccess">{notice}</p>}
    {campaigns&&<div className="salesReportScroll"><table>
      <thead><tr><th>{ar?'الحملة':'Campaign'}</th><th>{ar?'الطلبات':'Leads'}</th>
        <th>{ar?'مؤهلون':'Qualified'}</th><th>{ar?'مكتمل':'Won'}</th>
        <th>{ar?'مفقود':'Lost'}</th></tr></thead>
      <tbody>{campaigns.campaigns.map(row=><tr key={row.campaign}>
        <td>{row.campaign}</td><td>{row.leads}</td><td>{row.qualified}</td>
        <td>{row.won}</td><td>{row.lost}</td></tr>)}
        {campaigns.campaigns.length===0&&<tr><td colSpan={5}>{ar?'لا توجد بيانات بعد':'No campaign enquiries yet'}</td></tr>}
      </tbody>
    </table></div>}

    <div className="salesReportHeader">
      <h3>{ar?'التحكم في وكلاء المبيعات':'Sales agent execution controls'}</h3>
      <p>{ar?'الإرسال متوقف افتراضياً. يتطلب الإرسال الآلي موافقة المالك والقالب المعتمد وموافقة التسويق والتحقق من قائمة الإيقاف.':
        'Automation starts off. Sending requires an owner-approved template, verified consent, suppression checks and a daily cap.'}</p>
    </div>
    {settings&&<div className="salesControlForm">
      <label><input type="checkbox" checked={enabled} disabled={!owner||busy}
        onChange={event=>setEnabled(event.target.checked)}/>
        {ar?'تمكين تنفيذ متابعة العملاء':'Enable scheduled follow-up execution'}</label>
      <label><input type="checkbox" checked={paused} disabled={!owner||busy}
        onChange={event=>setPaused(event.target.checked)}/>
        {ar?'إيقاف جميع المتابعات مؤقتاً':'Pause follow-up execution (kill switch)'}</label>
      <label>{ar?'الحد الأقصى اليومي للرسائل':'Maximum messages per day'}
        <input type="number" min={0} max={100} value={dailyCap}
          disabled={!owner||busy} onChange={event=>setDailyCap(Number(event.target.value))}/></label>
      {owner&&<button type="button" disabled={busy||loading||dailyCap<0||dailyCap>100||enabled&&dailyCap===0}
        onClick={()=>void action(()=>saveSalesAutomationSettings({
          enabled,paused,max_sends_per_day:dailyCap,
        }),ar?'تم حفظ إعدادات التنفيذ':'Automation controls saved')}>
        {ar?'حفظ إعدادات الوكلاء':'Save agent controls'}
      </button>}
      {!owner&&<p className="salesHint">{ar?'هذه الإعدادات للمالك فقط':'Only the workspace owner can change these settings.'}</p>}
    </div>}

    {owner&&readiness&&<div className="salesReadiness">
      <h4>{ar?'جاهزية التشغيل والإرسال':'Automation readiness checklist'}</h4>
      <ul>{readiness.checks.map(check=><li key={check.id}>
        <span aria-label={check.ok?'ready':'needs-setup'}>{check.ok?'✓':'○'}</span>
        {check.label}
      </li>)}</ul>
      <p>{ar?'تؤكد هذه القائمة إعدادات النظام فقط. يلزم اختبار فعلي عبر ميتا والتحقق من النشر.':
        'These are configuration checks only. Live Meta delivery and VM deployment must still be tested.'}</p>
      {readiness.last_webhook_at&&<small>
        {ar?'آخر إشعار واتساب':'Last WhatsApp webhook'}: {formatDateTime(readiness.last_webhook_at)}
      </small>}
    </div>}
    <div className="salesReportHeader"><h3>{ar?'سجل تنفيذ المبيعات':'Sales operations review'}</h3>
      <p>{ar?'عروض الأسعار ترسل عبر مسار موافقة واتساب من صفحة العميل. المتابعات لا تنفذ إلا بعد اعتماد القالب والإعدادات.':
        'Quotation sending is requested from the lead detail and uses WhatsApp approval. Follow-ups run only after owner template approval and an enabled policy.'}</p></div>
    {operations&&<>
      <h4>{ar?'عروض الأسعار':'Quotation requests'} ({operations.quotes.length})</h4>
      <div className="salesReportScroll"><table><thead><tr>
        <th>{ar?'العميل':'Lead'}</th><th>{ar?'المبلغ':'Total'}</th>
        <th>{ar?'الحالة':'Status'}</th><th>{ar?'طلب الموافقة':'Approval'}</th></tr></thead>
        <tbody>{operations.quotes.map(q=><tr key={q.id}>
          <td>{q.lead_id}</td><td>{q.currency} {q.total}</td>
          <td>{q.status}</td><td>{q.approval_id||'—'}</td></tr>)}
          {operations.quotes.length===0&&<tr><td colSpan={4}>{ar?'لا توجد عروض':'No quotations waiting'}</td></tr>}
        </tbody></table></div>
      <h4>{ar?'المتابعات المجدولة':'Scheduled follow-ups'} ({operations.followups.length})</h4>
      <div className="salesReportScroll"><table><thead><tr>
        <th>{ar?'السبب':'Purpose'}</th><th>{ar?'الوقت':'Due'}</th>
        <th>{ar?'الحالة':'Status'}</th><th>{ar?'الإجراءات':'Actions'}</th></tr></thead>
        <tbody>{operations.followups.map(item=><tr key={item.id}>
          <td><strong>{item.purpose}</strong>
            {item.last_error&&<p className="salesError">{item.last_error}</p>}
            {item.template_name&&<small>{item.template_name}</small>}</td>
          <td>{formatDateTime(item.due_at)}</td>
          <td>{item.status}</td>
          <td>
            {item.status==='proposed'&&owner&&<FollowupApproval
              locale={locale} templates={approvedTemplates} busy={busy}
              onApprove={(template,parameters)=>void action(
                ()=>approveSalesFollowup(item.lead_id,item.id,
                  template.name,template.language,parameters),
                ar?'تم اعتماد المتابعة للموعد المحدد':'Follow-up approved for its due time')}/>}
            {owner&&['proposed','approved','held'].includes(item.status)&&<button
              type="button" disabled={busy}
              onClick={()=>void action(()=>cancelSalesFollowup(item.lead_id,item.id),
                ar?'تم إلغاء المتابعة':'Follow-up cancelled')}>
              {ar?'إلغاء':'Cancel'}
            </button>}
            {item.sent_wamid&&<small>{ar?'قبله مزود واتساب':'Meta accepted'} · {item.sent_wamid}</small>}
          </td>
        </tr>)}
        {operations.followups.length===0&&<tr><td colSpan={4}>{ar?'لا توجد متابعات':'No follow-ups pending'}</td></tr>}
        </tbody></table></div>
    </>}
  </section>;
}

function FollowupApproval({locale,templates,busy,onApprove}:{
  locale:'ar'|'en';templates:WaTemplate[];busy:boolean;
  onApprove:(template:WaTemplate,parameters:string[])=>void;
}){
  const ar=locale==='ar';
  const [key,setKey]=useState('');
  const [values,setValues]=useState<string[]>([]);
  const selected=templates.find(t=>`${t.name}:${t.language}`===key);
  const count=selected?.parameter_count||0;
  return <div className="salesApprovalForm">
    <select aria-label={ar?'قالب واتساب':'WhatsApp template'}
      value={key} disabled={busy}
      onChange={event=>{setKey(event.target.value);setValues([]);}}>
      <option value="">{ar?'اختر القالب':'Select template'}</option>
      {templates.map(template=><option key={`${template.name}:${template.language}`}
        value={`${template.name}:${template.language}`}>{template.name} ({template.language})</option>)}
    </select>
    {selected&&Array.from({length:count},(_,i)=><input
      key={i} aria-label={`${ar?'المعامل':'Parameter'} ${i+1}`}
      maxLength={1000} value={values[i]||''}
      onChange={event=>setValues(current=>{
        const next=[...current];next[i]=event.target.value;return next;
      })}/>)}
    <button type="button" disabled={!selected||busy||values.length<count||
      Array.from({length:count},(_,i)=>values[i]||'').some(x=>!x.trim())}
      onClick={()=>{if(selected)onApprove(selected,
        Array.from({length:count},(_,i)=>values[i]||''));}}>
      {ar?'اعتماد التنفيذ':'Approve scheduled send'}
    </button>
    {templates.length===0&&<small>{ar?'فعّل قالباً معتمداً في إعدادات واتساب أولاً':
      'Allowlist an approved Meta template in WhatsApp Setup first.'}</small>}
  </div>;
}
