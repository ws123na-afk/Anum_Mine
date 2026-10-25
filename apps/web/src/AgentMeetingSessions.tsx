import {useCallback,useEffect,useState} from 'react';
import * as api from './lib/api';
import {describeError} from './lib/errors';
import {t} from './lib/i18n';
type Meeting={id:string;title:string;status:string;revision:number;room_id?:string|null};
export type MeetingRoomChoice={id:string;label:string};
type Turn={id:string;sequence:number;speaker_id:string;role:string;body:string};
const root='/api/v1/agent-meetings';
export function AgentMeetingSessions({onClose,rooms=[],initialRoomId=null}:{onClose:()=>void;rooms?:readonly MeetingRoomChoice[];initialRoomId?:string|null}){
 const [list,setList]=useState<Meeting[]>([]),[chosen,setChosen]=useState<string|null>(null);
 const [turns,setTurns]=useState<Turn[]>([]),[title,setTitle]=useState(''),[error,setError]=useState('');
 const [working,setWorking]=useState(false);
 const [turnsNote,setTurnsNote]=useState('');
 const [agentId,setAgentId]=useState(''),[roomId,setRoomId]=useState<string>(initialRoomId??'');
 const roomName=(id:string|null|undefined)=>id?rooms.find(r=>r.id===id)?.label??id:null;
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
 useEffect(()=>{
  let active=true;
  const update=()=>{if(active&&document.visibilityState==='visible')void refresh().catch(e=>{if(active)setError(describeError(e))})};
  update();
  const interval=window.setInterval(update,10_000);
  const onVisible=()=>update();
  document.addEventListener('visibilitychange',onVisible);
  return()=>{active=false;window.clearInterval(interval);document.removeEventListener('visibilitychange',onVisible)};
 },[refresh]);
 useEffect(()=>{
  if(!chosen){setTurns([]);setTurnsNote('');return}
  let mounted=true;const scope=api.currentScope();
  void api.apiRequest<{items:Turn[]}>(root+'/'+encodeURIComponent(chosen)+'/turns?limit=100',{method:'GET'})
  .then(value=>{const next=api.currentScope();if(mounted&&scope.tenantId===next.tenantId&&scope.workspaceId===next.workspaceId){setTurns(value.items);setTurnsNote('')}})
  .catch(e=>{if(!mounted)return;
   // Transcripts are for workspace owners only: say so instead of showing a failure.
   if((e as {status?:number}).status===403){setTurns([]);setTurnsNote(describeError(e))}else setError(describeError(e))});
  return()=>{mounted=false};
 },[chosen,list]);
 const selected=list.find(m=>m.id===chosen);
 return <section aria-label="Agent meeting sessions" style={{padding:24,maxWidth:960,margin:'auto'}}>
  <header style={{display:'flex',justifyContent:'space-between',gap:16}}>
   <div><h2>Agent meeting sessions</h2><p>Persistent meetings in your authenticated workspace.</p></div>
   <button type="button" onClick={onClose}>Close</button>
  </header>
  <p role="status">Generate recorded AI meeting contributions using your configured model. All runs require an active meeting and are limited by the saved meeting budget. No external actions are executed.</p>
  <form onSubmit={e=>{e.preventDefault();void run(async()=>{
   const m=await api.apiRequest<Meeting>(root,{method:'POST',body:JSON.stringify({title:title.trim(),...(roomId?{room_id:roomId}:{})})});
   setChosen(m.id);setTitle('');
  })}} style={{display:'flex',gap:8}}>
   <input value={title} maxLength={200} required aria-label="New meeting title" onChange={e=>setTitle(e.target.value)}/>
   <select value={roomId} aria-label={t('hq.meetings.room')} title={t('hq.meetings.roomHint')} onChange={e=>setRoomId(e.target.value)}>
    <option value="">{t('hq.meetings.noRoom')}</option>
    {rooms.map(r=><option key={r.id} value={r.id}>{r.label}</option>)}
   </select>
   <button disabled={working||!title.trim()}>Create meeting</button>
   <button type="button" disabled={working} onClick={()=>void run(async()=>{})}>Refresh</button>
  </form>
  {error&&<p role="alert">{error}</p>}
  <div style={{display:'grid',gridTemplateColumns:'minmax(180px,1fr) minmax(260px,2fr)',gap:20,marginTop:16}}>
   <div>{list.map(m=><button type="button" key={m.id} aria-pressed={chosen===m.id} onClick={()=>setChosen(m.id)}
    style={{display:'block',width:'100%',textAlign:'left',marginBottom:8}}>{m.title} ({m.status}){m.room_id?` · ${roomName(m.room_id)}`:''}</button>)}
    {!list.length&&<p>No meetings saved yet.</p>}
   </div>
   <div>{selected?<><h3>{selected.title}</h3><p>Status: {selected.status}</p>{selected.room_id&&<p data-testid="meeting-room-link">{t('hq.meetings.inRoom',{name:roomName(selected.room_id)??''})}</p>}
    {(['active','paused','finished','cancelled'] as const).map(status=>
     <button type="button" key={status} disabled={working||status===selected.status||['finished','cancelled','failed'].includes(selected.status)}
      onClick={()=>void run(()=>api.apiRequest(root+'/'+encodeURIComponent(selected.id)+'/state',
       {method:'POST',body:JSON.stringify({status,expected_revision:selected.revision})}))}>{status}</button>)}
    {selected.status==='scheduled'&&<form onSubmit={e=>{e.preventDefault();void run(async()=>{
      await api.apiRequest(root+'/'+encodeURIComponent(selected.id)+'/agents',{
       method:'POST',body:JSON.stringify({agent_id:agentId.trim(),expected_revision:selected.revision})
      });setAgentId('');
     })}}><input value={agentId} onChange={e=>setAgentId(e.target.value)} maxLength={160}
      aria-label="Agent participant ID" placeholder="Agent participant ID" required/>
     <button disabled={working||!agentId.trim()}>Add agent</button></form>}
    {selected.status==='active'&&<button type="button" disabled={working}
      onClick={()=>void run(async()=>{
       const id=selected.id;
       await api.apiRequest(root+'/'+encodeURIComponent(id)+'/execute-turn',{
        method:'POST',body:JSON.stringify({request_key:'turn_'+id.slice(0,30)+'_'+Date.now().toString(36)})
       });
      })}>Generate next agent turn</button>}
    {selected.status==='active'&&<button type="button" disabled={working}
      onClick={()=>void run(()=>api.apiRequest(root+'/'+encodeURIComponent(selected.id)+'/run',{
       method:'POST',body:JSON.stringify({run_key:'round_'+Date.now().toString(36)+'_meeting',turns:3})
      }))}>Run agent meeting round (up to 3 turns)</button>}
    <h4>Saved meeting turns</h4>{turnsNote?<p role="note">{turnsNote}</p>:turns.length?<ol>{turns.map(t=><li key={t.id}><strong>{t.speaker_id}:</strong> {t.body}</li>)}</ol>:<p>No recorded turns.</p>}
   </>:<p>Select a meeting to view its details.</p>}</div>
  </div>
 </section>;
}
