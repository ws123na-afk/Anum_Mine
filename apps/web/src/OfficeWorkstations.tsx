import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeft, Clock3, RefreshCw, Search, ShieldCheck, Users, X } from 'lucide-react';
import type { OrgSources } from './lib/orgChart.ts';
import { profileHash } from './lib/orgChart.ts';
import * as api from './lib/api';
import { loadWorkspaceWorkstations, officeEventAffectsWorkstations, workstationLoadProblem, type WorkstationSnapshot, type WorkstationRecord } from './lib/officeWorkstations.ts';
import { staleOfficePresence, type OfficeStatus } from './lib/officePresence.ts';
import './officeWorkstations.css';
const statuses:readonly ('all'|OfficeStatus)[]=['all','working','queued','waiting_approval','blocked','paused','idle','unknown'];
const REFRESH_MS=30_000;
function label(status:OfficeStatus):string{return status.replaceAll('_',' ')}
function readable(when:string):string{return new Date(when).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'medium'})}
function Evidence({record,now,forceStale,onOpenProfile}:{record:WorkstationRecord;now:string;forceStale:boolean;onOpenProfile:()=>void}){
 const stale=forceStale||staleOfficePresence(record,now);
 return <section className="workstationEvidence" aria-label="Selected employee evidence">
  <div className="workstationEvidenceHeading"><div><h3>{record.name}</h3><p>{record.department||'No assigned department'}</p></div><span className={`workstationStatus ${stale?'unknown':record.status}`}>{stale?'Stale':label(record.status)}</span></div>
  <dl>
   <div><dt>Profile</dt><dd>{record.profileStatus}</dd></div>
   <div><dt>Office department</dt><dd>{record.suggestedDepartment}</dd></div>
   <div><dt>Task evidence</dt><dd>{record.taskIds.length} open task(s)</dd></div>
   <div><dt>Observed</dt><dd>{readable(record.observedAt)}</dd></div>
   <div><dt>Source</dt><dd>{record.source.replaceAll('_',' ')}</dd></div>
  </dl>
  <button type="button" onClick={onOpenProfile}>Open agent profile in ANUM</button>
  <h4>Assigned open tasks</h4>
  {record.taskIds.length?<ul className="workstationTaskEvidence">{record.taskIds.map((id,i)=><li key={id}><strong>{record.taskTitles[i]||'Task title unavailable'}</strong><small>ID: {id}</small></li>)}</ul>:<p>No open task evidence available in this snapshot.</p>}
  <p className="workstationPrivacy"><ShieldCheck size={15}/> This view uses only records permitted by the signed-in workspace. It does not reveal or invent agent private activity.</p>
 </section>;
}
export function OfficeWorkstations({onClose}:{onClose:()=>void}){
 const [data,setData]=useState<WorkstationSnapshot|null>(null);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [selected,setSelected]=useState<string|null>(null);
 const [search,setSearch]=useState(''),[department,setDepartment]=useState('all'),[status,setStatus]=useState<'all'|OfficeStatus>('all');
 const [tick,setTick]=useState(()=>new Date().toISOString());
 const sourcesRef=useRef<OrgSources|null>(null),seqRef=useRef(0),aliveRef=useRef(true);
 const load=useCallback(async()=>{
  const seq=++seqRef.current;setBusy(true);
  try{const result=await loadWorkspaceWorkstations(sourcesRef.current);
   if(!aliveRef.current||seq!==seqRef.current)return;
   sourcesRef.current=result.sources;setData(result.snapshot);setTick(new Date().toISOString());setError('');
   setSelected(old=>old&&result.snapshot.records.some(r=>r.agentId===old)?old:null);
  }catch(e){if(aliveRef.current&&seq===seqRef.current){
   const problem=workstationLoadProblem(e);setError(problem.message);
   if(problem.clearSnapshot){setData(null);setSelected(null);sourcesRef.current=null;}
  }}
  finally{if(aliveRef.current&&seq===seqRef.current)setBusy(false);}
 },[]);
 useEffect(()=>{aliveRef.current=true;void load();const poll=window.setInterval(()=>{if(!document.hidden)void load()},REFRESH_MS);
  const onVisible=()=>{if(!document.hidden)void load()};
  document.addEventListener('visibilitychange',onVisible);
  const tickInterval=window.setInterval(()=>setTick(new Date().toISOString()),10000);
  return()=>{aliveRef.current=false;seqRef.current++;window.clearInterval(poll);window.clearInterval(tickInterval);document.removeEventListener('visibilitychange',onVisible)};
 },[load]);
 // Reuse the signed-in workspace event stream. A quiet periodic refresh remains
 // the fallback when the browser disconnects or the stream is unavailable.
 useEffect(()=>{
  const controller=new AbortController();
  let pending:number|undefined;
  const onEvent=(event:{type:string})=>{
   if(!officeEventAffectsWorkstations(event.type)||document.hidden)return;
   if(pending)window.clearTimeout(pending);
   pending=window.setTimeout(()=>{pending=undefined;void load()},900);
  };
  void api.streamEvents(onEvent,controller.signal,()=>undefined).catch(()=>undefined);
  return()=>{controller.abort();if(pending)window.clearTimeout(pending)};
 },[load]);

 const departments=[...new Set((data?.records??[]).map(x=>x.suggestedDepartment))].sort();
 const matches=(data?.records??[]).filter(r=>{
  if(department!=='all'&&r.suggestedDepartment!==department)return false;
  if(status!=='all'&&r.status!==status)return false;
  const q=search.trim().toLowerCase();return !q||[r.name,r.department??'',r.suggestedDepartment,...r.taskTitles].some(v=>v.toLowerCase().includes(q));
 });
 const detail=data?.records.find(r=>r.agentId===selected)??null;
 const stale=data?Boolean(error)||Date.parse(tick)-Date.parse(data.observedAt)>90000:true;
 return <section className="officeWorkstations" role="dialog" aria-label="Live Employee Workstations" aria-modal="false">
  <header className="workstationsHeader"><div><small>ANUM MINE / LIVE WORKSPACES</small><h2>Employee Workstations</h2><p>Authenticated task evidence, not simulated avatar activity</p></div><button onClick={onClose}><X size={17}/> Close</button></header>
  <div className="workstationToolbar">
   <label><Search size={15}/><input aria-label="Search employees or tasks" placeholder="Search employee, department or task" value={search} onChange={e=>setSearch(e.target.value)}/></label>
   <label>Department<select aria-label="Filter workstations by department" value={department} onChange={e=>setDepartment(e.target.value)}><option value="all">All departments</option>{departments.map(d=><option key={d}>{d}</option>)}</select></label>
   <label>Status<select aria-label="Filter workstations by status" value={status} onChange={e=>setStatus(e.target.value as 'all'|OfficeStatus)}>{statuses.map(s=><option key={s} value={s}>{s==='all'?'All statuses':label(s)}</option>)}</select></label>
   <button onClick={()=>void load()} disabled={busy}><RefreshCw size={15}/>{busy?'Refreshing':'Refresh'}</button>
  </div>
  <div className="workstationStatusBar" role="status" aria-live="polite">
   <span><Users size={15}/> {data?.records.length??0} visible agent profiles</span>
   <span><Clock3 size={15}/> {data?`Observed ${readable(data.observedAt)}`:'Awaiting first snapshot'}</span>
   {data&&<span className={stale?'workstationWarn':'workstationOk'}>{stale?'Snapshot stale':'Snapshot current'}</span>}
   {data&&!data.complete&&<span className="workstationWarn"><AlertTriangle size={15}/> Incomplete task results</span>}
  </div>
  {error&&<div className="workstationError" role="alert"><AlertTriangle size={18}/><div><strong>Could not refresh employee data</strong><p>{error}</p>{data&&<p>Showing the last successful snapshot; it is stale until Refresh succeeds.</p>}</div></div>}
  {data&&data.partialSources.length>0&&<div className="workstationWarning" role="status">Some source requests were unavailable: {data.partialSources.join(', ')}. Statuses based on missing data are not assumed idle.</div>}
  <div className="workstationBody">
   <section className="workstationList" aria-label="Workstation directory">
    {busy&&!data?<p>Loading authenticated office data…</p>:!data?<p>Unable to display employees. Use Refresh to try again.</p>:matches.length===0?<p>No employees match your filters.</p>:
    <ul>{matches.map(r=><li key={r.agentId}><button aria-current={selected===r.agentId?'true':undefined} onClick={()=>setSelected(r.agentId)}>
     <span className="workstationAvatar" aria-hidden="true">{r.name.slice(0,1).toUpperCase()}</span>
     <span className="workstationPersonInfo"><strong>{r.name}</strong><small>{r.suggestedDepartment}</small></span>
     <span className={`workstationStatus ${stale?'unknown':r.status}`}>{stale?'stale':label(r.status)}</span>
    </button></li>)}</ul>}
   </section>
   <aside className="workstationDetail">{detail?<Evidence record={detail} now={tick} forceStale={stale} onOpenProfile={()=>{onClose();window.location.hash=profileHash({kind:'agent',id:detail.agentId})}}/>:<div className="workstationPlaceholder"><Users size={35}/><h3>Select an employee</h3><p>See their latest authorized task evidence, role, department and observation time.</p></div>}</aside>
  </div>
  <footer className="workstationFooter"><p>Task-derived agent activity is not proof that a human is present or online. Desk assignments are not yet stored by the backend.</p><button onClick={onClose}><ArrowLeft size={15}/> Return to office</button></footer>
 </section>;
}
