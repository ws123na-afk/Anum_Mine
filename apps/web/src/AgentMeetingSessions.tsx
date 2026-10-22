import {useCallback,useEffect,useState} from 'react';
import * as api from './lib/api';
import {describeError} from './lib/errors';
type Meeting={id:string;title:string;status:string;revision:number};
type Turn={id:string;sequence:number;speaker_id:string;role:string;body:string};
const root='/api/v1/agent-meetings';
export function AgentMeetingSessions({onClose}:{onClose:()=>void}){
 const [list,setList]=useState<Meeting[]>([]),[chosen,setChosen]=useState<string|null>(null);
 const [turns,setTurns]=useState<Turn[]>([]),[title,setTitle]=useState(''),[error,setError]=useState('');
 const [working,setWorking]=useState(false);
 const refresh=useCallback(async()=>{
  const initial=api.currentScope();let cursor:string|null=null;const found:Meeting[]=[];
  for(let n=0;n<100;n++){
   const params=new URLSearchParams({limit:'100'});if(cursor)params.set('cursor',cursor);
   const result=await api.apiRequest<{items:Meeting[];next_cursor:string|null}>(root+'?'+params,{method:'GET'});
   const current=api.currentScope();
   if(current.tenantId!==initial.tenantId||current.workspaceId!==initial.workspaceId)throw Error('Workspace switched while loading');
   if(!Array.isArray(result.items))throw Error('Invalid meeting response');
   found.push(...result.items);
   if(!result.next_cursor){setList(found);return}
   if(cursor===result.next_cursor)throw Error('Meeting cursor did not advance');
   cursor=result.next_cursor;
  }
  throw Error('Meeting page limit reached');
 },[]);
 const run=async(action:()=>Promise<unknown>)=>{
  setWorking(true);setError('');
  try{await action();await refresh()}catch(e){setError(describeError(e))}finally{setWorking(false)}
 };
 useEffect(()=>{void refresh().catch(e=>setError(describeError(e)))},[refresh]);
 useEffect(()=>{
  if(!chosen){setTurns([]);return}
  let mounted=true;const scope=api.currentScope();
  void api.apiRequest<{items:Turn[]}>(root+'/'+encodeURIComponent(chosen)+'/turns?limit=100',{method:'GET'})
  .then(value=>{const next=api.currentScope();if(mounted&&scope.tenantId===next.tenantId&&scope.workspaceId===next.workspaceId)setTurns(value.items)})
  .catch(e=>{if(mounted)setError(describeError(e))});
  return()=>{mounted=false};
 },[chosen,list]);
 const selected=list.find(m=>m.id===chosen);
 return <section aria-label="Agent meeting sessions" style={{padding:24,maxWidth:960,margin:'auto'}}>
  <header style={{display:'flex',justifyContent:'space-between',gap:16}}>
   <div><h2>Agent meeting sessions</h2><p>Persistent meetings in your authenticated workspace.</p></div>
   <button type="button" onClick={onClose}>Close</button>
  </header>
  <p role="status">Meeting management is available. Autonomous agent execution has not yet been enabled.</p>
  <form onSubmit={e=>{e.preventDefault();void run(async()=>{
   const m=await api.apiRequest<Meeting>(root,{method:'POST',body:JSON.stringify({title:title.trim()})});
   setChosen(m.id);setTitle('');
  })}} style={{display:'flex',gap:8}}>
   <input value={title} maxLength={200} required aria-label="New meeting title" onChange={e=>setTitle(e.target.value)}/>
   <button disabled={working||!title.trim()}>Create meeting</button>
   <button type="button" disabled={working} onClick={()=>void run(async()=>{})}>Refresh</button>
  </form>
  {error&&<p role="alert">{error}</p>}
  <div style={{display:'grid',gridTemplateColumns:'minmax(180px,1fr) minmax(260px,2fr)',gap:20,marginTop:16}}>
   <div>{list.map(m=><button type="button" key={m.id} aria-pressed={chosen===m.id} onClick={()=>setChosen(m.id)}
    style={{display:'block',width:'100%',textAlign:'left',marginBottom:8}}>{m.title} ({m.status})</button>)}
    {!list.length&&<p>No meetings saved yet.</p>}
   </div>
   <div>{selected?<><h3>{selected.title}</h3><p>Status: {selected.status}</p>
    {(['active','paused','finished','cancelled'] as const).map(status=>
     <button type="button" key={status} disabled={working||status===selected.status||['finished','cancelled','failed'].includes(selected.status)}
      onClick={()=>void run(()=>api.apiRequest(root+'/'+encodeURIComponent(selected.id)+'/state',
       {method:'POST',body:JSON.stringify({status,expected_revision:selected.revision})}))}>{status}</button>)}
    <h4>Saved meeting turns</h4>{turns.length?<ol>{turns.map(t=><li key={t.id}><strong>{t.speaker_id}:</strong> {t.body}</li>)}</ol>:<p>No recorded turns.</p>}
   </>:<p>Select a meeting to view its details.</p>}</div>
  </div>
 </section>;
}
