import { useEffect, useState } from 'react';
import { describeError } from './lib/errors';
import { getSalesCampaignConversions, getSalesOperations,
  type SalesCampaignConversions, type SalesOperationsQueue } from './lib/salesApi';

export function SalesCampaignAndOperations({locale}:{locale:'ar'|'en'}) {
  const [campaigns,setCampaigns] = useState<SalesCampaignConversions|null>(null);
  const [operations,setOperations] = useState<SalesOperationsQueue|null>(null);
  const [error,setError] = useState('');
  const [loading,setLoading] = useState(true);
  const ar=locale==='ar';
  useEffect(()=>{
    let alive=true;
    Promise.allSettled([getSalesCampaignConversions(),getSalesOperations()]).then(([a,b])=>{
      if(!alive)return;
      if(a.status==='fulfilled')setCampaigns(a.value);
      else setError(describeError(a.reason));
      if(b.status==='fulfilled')setOperations(b.value);
      else setError(previous=>[previous,describeError(b.reason)].filter(Boolean).join(' · '));
    }).finally(()=>{if(alive)setLoading(false);});
    return ()=>{alive=false;};
  },[]);
  return <section className="salesReporting" aria-label={ar?'التسويق وعمليات المبيعات':'Marketing and sales operations'}>
    <div className="salesReportHeader"><h3>{ar?'أداء الحملات التسويقية':'Marketing campaign conversions'}</h3>
      <p>{ar?'مصدر الحملة مذكور في رسالة العميل وليس تتبعاً إعلانياً مؤكداً':
        'Campaign sources come from customer messages and are not verified ad attribution.'}</p></div>
    {loading&&<p role="status">{ar?'جار التحميل':'Loading reports...'}</p>}
    {error&&<p role="alert" className="salesError">{error}</p>}
    {campaigns&&<div className="salesReportScroll"><table><thead><tr>
      <th>{ar?'الحملة':'Campaign'}</th><th>{ar?'طلبات':'Leads'}</th>
      <th>{ar?'مؤهلون':'Qualified'}</th><th>{ar?'مكتمل':'Won'}</th>
      <th>{ar?'مفقود':'Lost'}</th></tr></thead><tbody>
      {campaigns.campaigns.map(row=><tr key={row.campaign}>
        <td>{row.campaign}</td><td>{row.leads}</td><td>{row.qualified}</td>
        <td>{row.won}</td><td>{row.lost}</td></tr>)}
      {campaigns.campaigns.length===0&&<tr><td colSpan={5}>{ar?'لا توجد حملات':'No campaign enquiries yet'}</td></tr>}
    </tbody></table></div>}
    <div className="salesReportHeader"><h3>{ar?'مركز متابعة المبيعات':'Sales operations review'}</h3>
      <p>{ar?'العروض والمتابعات تحتاج مسار اعتماد وإرسال مفعل. لم يتم إرسال أي شيء من هذه الشاشة.':
        'Quotes and follow-ups require a connected approval and delivery workflow. This screen never sends messages.'}</p></div>
    {operations&&<>
      <p className="salesHint">{operations.delivery_enabled||operations.execution_enabled
        ? (ar?'تحقق من إعدادات التنفيذ':'Check live execution settings')
        : (ar?'الإرسال التلقائي متوقف':'Automatic delivery is disabled')}</p>
      <h4>{ar?'عروض تحتاج متابعة':'Quotes requiring review'} ({operations.quotes.length})</h4>
      <div className="salesReportScroll"><table><thead><tr><th>{ar?'العميل':'Lead'}</th>
        <th>{ar?'المبلغ':'Total'}</th><th>{ar?'الحالة':'Status'}</th></tr></thead>
        <tbody>{operations.quotes.map(q=><tr key={q.id}>
          <td>{q.lead_id}</td><td>{q.currency} {q.total}</td><td>{q.status}</td></tr>)}
        {operations.quotes.length===0&&<tr><td colSpan={3}>{ar?'لا توجد عروض':'No quotations waiting'}</td></tr>}
        </tbody></table></div>
      <h4>{ar?'المتابعات المقترحة':'Follow-up proposals'} ({operations.followups.length})</h4>
      <div className="salesReportScroll"><table><thead><tr><th>{ar?'السبب':'Purpose'}</th>
        <th>{ar?'الموعد':'Due'}</th><th>{ar?'الحالة':'Status'}</th></tr></thead><tbody>
        {operations.followups.map(f=><tr key={f.id}>
          <td>{f.purpose}</td><td>{f.due_at}</td><td>{f.status}</td></tr>)}
        {operations.followups.length===0&&<tr><td colSpan={3}>{ar?'لا توجد متابعات':'No follow-ups waiting'}</td></tr>}
      </tbody></table></div>
    </>}
  </section>;
}
