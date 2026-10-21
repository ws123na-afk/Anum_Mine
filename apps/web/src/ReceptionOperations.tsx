import { useReducer, useState } from 'react';
import { CheckCircle2, ClipboardCheck, DoorOpen, RotateCcw, UserRoundCheck, X } from 'lucide-react';
import {
 EMPTY_RECEPTION, nextReceptionAction, receptionCounts, receptionQueue, ReceptionError,
 updateReception, type ReceptionState, type VisitAction, type VisitKind, type VisitPriority, type VisitRecord
} from './lib/receptionOperations';
import './receptionOperations.css';

const DEMO_ROOMS=['Reception','ANUM Command','Boardroom','Finance','Tyre Engineering','Customer Experience','Executive Office'];
const EXAMPLE_HOSTS=['Reception coordinator','ANUM - Chief of Staff','Finance Controller','Tyre Fleet Planner','Customer Care'];
const EXAMPLES:{name:string;company:string;kind:VisitKind;purpose:string;priority:VisitPriority}[]=[
 {name:'Sample Customer',company:'Example Fleet Co.',kind:'customer',purpose:'Review fleet services and requirements',priority:'normal'},
 {name:'Sample Supplier',company:'Example Tyre Supply',kind:'supplier',purpose:'Discuss tyre delivery appointment',priority:'normal'},
 {name:'Sample Delivery',company:'Example Logistics',kind:'delivery',purpose:'Hand over incoming goods',priority:'urgent'}
];
function initial():ReceptionState { return EMPTY_RECEPTION; }
function clock(date:string) { return new Date(date).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}); }
export function ReceptionOperations({onClose,onDestination}:{onClose:()=>void;onDestination:(name:string)=>void}) {
 const [state,dispatch]=useReducer(updateReception,undefined,initial);
 const [name,setName]=useState(''),[company,setCompany]=useState(''),[purpose,setPurpose]=useState('');
 const [kind,setKind]=useState<VisitKind>('customer'),[priority,setPriority]=useState<VisitPriority>('normal');
 const [host,setHost]=useState(EXAMPLE_HOSTS[0]),[room,setRoom]=useState(DEMO_ROOMS[0]);
 const [selected,setSelected]=useState<string|null>(null),[error,setError]=useState('');
 const [view,setView]=useState<'queue'|'history'>('queue');
 const counts=receptionCounts(state.visits);
 const queue=receptionQueue(state.visits).filter(v=>view==='history'?['completed','cancelled'].includes(v.status):!['completed','cancelled'].includes(v.status));
 const active=state.visits.find(v=>v.id===selected);
 function apply(action:()=>void){try{action();setError('')}catch(e){setError(e instanceof ReceptionError?e.message:'Action could not be completed.')}}
 const arrive=(data:{name:string;company:string;kind:VisitKind;purpose:string;priority:VisitPriority})=>apply(()=>{
  const id=globalThis.crypto?.randomUUID?.()??`demo-${Date.now()}-${state.revision}`;
  dispatch({type:'arrive',id,...data,at:new Date().toISOString()});setSelected(id);
  setName('');setCompany('');setPurpose('');
 });
 function transition(visit:VisitRecord,action:VisitAction){apply(()=>{
  dispatch({type:'transition',id:visit.id,action,at:new Date().toISOString(),host,destination:room});
  if(action==='escort'&&visit.destination)onDestination(visit.destination);
 })}
 return <section className="receptionModule" aria-label="Reception Operations demonstration">
  <header className="receptionHeader">
   <div><p className="receptionKicker">ANUM MINE / OPERATIONS</p><h2>Reception & Visitor Operations</h2><p>Local simulation · No real visitors, notifications or appointments</p></div>
   <button type="button" onClick={onClose}><X size={18}/> Return to office</button>
  </header>
  <div className="receptionMetrics" role="group" aria-label="Visit summary">
   {[['Waiting',counts.waiting],['Ready for escort',counts.ready],['Escorting',counts.escorting],['Completed',counts.completed],['Urgent',counts.urgent]].map(([label,value])=><div key={label}><span>{label}</span><strong>{value}</strong></div>)}
  </div>
  <div className="receptionBody">
   <section className="receptionNew"><h3>Visitor arrival</h3><form onSubmit={e=>{e.preventDefault();arrive({name,company,kind,purpose,priority})}}>
    <label>Visitor name<input required maxLength={120} value={name} onChange={e=>setName(e.target.value)} placeholder="Full name"/></label>
    <label>Company / organization<input required maxLength={120} value={company} onChange={e=>setCompany(e.target.value)} placeholder="Company or personal"/></label>
    <label>Visit category<select value={kind} onChange={e=>setKind(e.target.value as VisitKind)}>
     <option value="customer">Customer</option><option value="supplier">Supplier</option><option value="candidate">Candidate</option><option value="delivery">Delivery</option><option value="guest">Guest</option>
    </select></label>
    <label>Visit purpose<textarea required maxLength={240} rows={3} value={purpose} onChange={e=>setPurpose(e.target.value)} placeholder="What are they here for?"/></label>
    <label>Priority<select value={priority} onChange={e=>setPriority(e.target.value as VisitPriority)}><option value="normal">Normal</option><option value="urgent">Urgent</option></select></label>
    <button className="receptionPrimary" type="submit"><UserRoundCheck size={17}/> Register arrival</button>
   </form>
   <div className="receptionDemoSeeds"><p>Try the full workflow with sample visitors:</p>{EXAMPLES.map(x=><button key={x.name} type="button" onClick={()=>arrive(x)}>+ {x.name}</button>)}</div>
   </section>
   <section className="receptionQueue"><div className="receptionSectionTitle"><h3>Reception desk</h3><div role="group" aria-label="Visitor list"><button aria-pressed={view==='queue'} onClick={()=>setView('queue')}>Active queue</button><button aria-pressed={view==='history'} onClick={()=>setView('history')}>Completed history</button></div></div>
    {queue.length===0?<div className="receptionEmpty"><ClipboardCheck/><p>{view==='queue'?'No visitors are waiting.':'No completed visits yet.'}</p></div>:
    <div className="receptionVisitorList">{queue.map(v=><button key={v.id} type="button" className={selected===v.id?'selected':''} onClick={()=>setSelected(v.id)}>
      <div><strong>{v.visitorName}</strong><small>{v.company} · {v.kind}</small></div><span className={v.priority==='urgent'?'urgent':''}>{v.priority==='urgent'?'Urgent · ':''}{v.status.replaceAll('_',' ')}</span><small>{clock(v.arrivedAt)}</small>
    </button>)}</div>}
   </section>
   <aside className="receptionDetail"><h3>Visitor handover</h3>{active?<><strong>{active.visitorName}</strong><p>{active.purpose}</p><p className="receptionMeta">Status: {active.status.replaceAll('_',' ')}</p>
    {active.status==='checked_in'&&<><label>Assigned host<select value={host} onChange={e=>setHost(e.target.value)}>{EXAMPLE_HOSTS.map(h=><option key={h}>{h}</option>)}</select></label><label>Destination<select value={room} onChange={e=>setRoom(e.target.value)}>{DEMO_ROOMS.map(d=><option key={d}>{d}</option>)}</select></label></>}
    {active.host&&<p className="receptionMeta">Host: {active.host} · Room: {active.destination}</p>}
    <div className="receptionButtons">{nextReceptionAction(active.status)&&<button className="receptionPrimary" type="button" onClick={()=>transition(active,nextReceptionAction(active.status)!)}>
      {active.status==='arrived'?<ClipboardCheck size={17}/>:active.status==='assigned'?<DoorOpen size={17}/>:<CheckCircle2 size={17}/>}
      {{arrived:'Check in visitor',checked_in:'Assign host & room',assigned:'Start escort',escorting:'Complete handover',completed:'Done',cancelled:'Cancelled'}[active.status]}
     </button>}
     {!['completed','cancelled'].includes(active.status)&&<button type="button" onClick={()=>transition(active,'cancel')}>Cancel visit</button>}</div>
    <h4>Visit history</h4><ol className="receptionTimeline">{active.timeline.map((e,i)=><li key={i}><time>{clock(e.at)}</time><span>{e.note}</span></li>)}</ol>
   </>:<p>Select a visitor to manage check-in, host assignment and escort.</p>}
   </aside>
  </div>
  {error&&<p className="receptionError" role="alert">{error}</p>}
  <footer className="receptionNote"><span>Demo data stays in memory and disappears when you close or reload this module.</span><button type="button" onClick={()=>{dispatch({type:'reset'});setSelected(null);setError('')}}><RotateCcw size={14}/> Clear demo</button></footer>
 </section>;
}
