import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Building2, ChevronDown, Compass, DoorOpen, HardDrive, Layers,
  LockKeyhole, Maximize2, Minimize2, Moon, RefreshCcw, RotateCcw, Search,
  Sun, Users, Wifi, WifiOff, X, ZoomIn, ZoomOut } from 'lucide-react';
import * as api from './lib/api.ts';
import { createOfficeSpaceClient } from './lib/officeSpaceClient.ts';
import { loadWorkspaceWorkstations } from './lib/officeWorkstations.ts';
import { HeadquartersController, adaptOfficeRegistry, type OfficeState } from './lib/headquartersController.ts';
import { dolly, focusRoom, initialCamera, orbit } from './lib/headquartersSpatial.ts';
import { type AgentEvidence, type RoomGeometry, type SceneFloor } from './lib/headquartersProjection.ts';
import { HeadquartersWebGL } from './HeadquartersWebGL';
import { OfficeSpaceLiveManager } from './OfficeSpaceLiveManager';
import { OfficeWorkstations } from './OfficeWorkstations';
import { ReceptionOperations } from './ReceptionOperations';
import { GuidedExecutiveTour } from './GuidedExecutiveTour';
import { AgentMeetingSessions } from './AgentMeetingSessions';
import './headquartersLive.css';

const officeApi=createOfficeSpaceClient(api.apiRequest,api.apiRequestVoid,api.currentScope);
const moduleRoutes:Readonly<Record<string,string>>={
 meeting:'meetings',reception:'inbox',office:'tasks',workshop:'tyre',
 warehouse:'commerce',lounge:'calendar',
};
function readStatus(evidence:AgentEvidence|null):string{
 if(!evidence)return 'No current activity evidence';
 switch(evidence.status){
  case 'running':return 'Active task evidence';
  case 'waiting':return 'Waiting on a task or approval';
  case 'idle':return 'No active task observed';
  default:return 'State not available';
 }
}
function readTime(time:string|null):string{
 if(!time)return 'No successful sync';
 const date=new Date(time);return Number.isFinite(date.getTime())?date.toLocaleTimeString(): 'Unknown';
}
function roomIcon(kind:string){
 switch(kind){
  case 'meeting':return <Users size={16}/>;
  case 'reception':return <DoorOpen size={16}/>;
  case 'warehouse':return <HardDrive size={16}/>;
  default:return <Building2 size={16}/>;
 }
}
type Overlay = 'none'|'spaces'|'workstations'|'reception'|'tour'|'agentMeetings';
export function HeadquartersLive({onClose}:{onClose:()=>void}){
 const [state,setState]=useState<OfficeState|null>(null);
 const [overlay,setOverlay]=useState<Overlay>('none');
 const [query,setQuery]=useState('');
 const [light,setLight]=useState<'day'|'night'>('day');
 const [full,setFull]=useState(false);
 const [filters,setFilters]=useState<'all'|'occupied'|'meeting'>('all');
 const host=useRef<HTMLDivElement>(null);
 const controller=useMemo(()=>new HeadquartersController({
  scope:()=>api.currentScope(),
  registry:async()=>adaptOfficeRegistry(await officeApi.load()),
  agents:async()=>{
   const result=await loadWorkspaceWorkstations();
   const current=api.currentScope();
   if(result.snapshot.scope.tenantId!==current.tenantId||result.snapshot.scope.workspaceId!==current.workspaceId){
    throw new Error('Workspace changed during agent evidence load');
   }
   return result.snapshot.records.map((agent):AgentEvidence=>({
    id:agent.agentId,name:agent.name,
    status:agent.status==='working'?'running':
     ['queued','waiting_approval','blocked'].includes(agent.status)?'waiting':
     agent.status==='idle'&&result.snapshot.complete?'idle':'unknown',
    observedAt:result.snapshot.observedAt,taskIds:agent.taskIds,
    source:'authenticated-agent-snapshot',
   }));
  },
 },10_000),[]);
 useEffect(()=>{
  const abort=new AbortController();
  let active=true;
  let reconnect=1000;
  let refreshTimer:ReturnType<typeof setTimeout>|null=null;
  const refreshFromEvents=()=>{
   if(refreshTimer!==null)return;
   refreshTimer=setTimeout(()=>{refreshTimer=null;if(active&&!abort.signal.aborted)void controller.refresh()},300);
  };
  const scope=api.currentScope();
  const watch=async()=>{
   while(active&&!abort.signal.aborted){
    try{
     await api.streamEvents(event=>{
      if(!active)return;
      const current=api.currentScope();
      if(current.tenantId!==scope.tenantId||current.workspaceId!==scope.workspaceId){
       abort.abort();return;
      }
      if(/^(task|agent_run|agent|approval|agent_meeting|office)\\./.test(event.type)){
       refreshFromEvents();
      }
     },abort.signal,()=>{reconnect=1000});
    }catch{ /* SSE may disconnect; scoped periodic refresh remains active. */ }
    if(!active||abort.signal.aborted)break;
    await new Promise<void>(resolve=>window.setTimeout(resolve,reconnect));
    reconnect=Math.min(reconnect*2,30000);
   }
  };
  void watch();
  return()=>{active=false;abort.abort();if(refreshTimer!==null)clearTimeout(refreshTimer)};
 },[controller]);
 useEffect(()=>{
  const refreshVisible=()=>{if(document.visibilityState==='visible')void controller.refresh()};
  const refreshFocused=()=>{void controller.refresh()};
  window.addEventListener('focus',refreshFocused);
  document.addEventListener('visibilitychange',refreshVisible);
  return()=>{window.removeEventListener('focus',refreshFocused);document.removeEventListener('visibilitychange',refreshVisible)};
 },[controller]);
 useEffect(()=>{
  const unsubscribe=controller.subscribe(setState);
  void controller.initialize();controller.scheduleRefresh();
  return()=>{unsubscribe();controller.dispose()};
 },[controller]);
 useEffect(()=>{
  const handler=()=>setFull(document.fullscreenElement===host.current);
  document.addEventListener('fullscreenchange',handler);
  return()=>document.removeEventListener('fullscreenchange',handler);
 },[]);
 const projection=state?.projection;
 const selected=state?.selected;
 const building=projection?.buildings.find(b=>b.id===selected?.buildingId);
 const floor=building?.floors.find(f=>f.id===selected?.floorId);
 const room=floor?.rooms.find(r=>r.id===selected?.roomId);
 const desks=floor?.desks.filter(d=>d.roomId===room?.id)??[];
 const occupied=desks.filter(d=>d.assignmentVisible).length;
 const hasServerData=(projection?.buildings.length??0)>0;
 const filteredRooms=(floor?.rooms??[]).filter(r=>{
  if(query&&![r.label,r.kind].some(v=>v.toLowerCase().includes(query.trim().toLowerCase())))return false;
  if(filters==='meeting'&&r.kind!=='meeting')return false;
  if(filters==='occupied'&&!floor?.desks.some(d=>d.roomId===r.id&&d.assignmentVisible))return false;
  return true;
 });
 const currentCamera=state?.camera??(floor?initialCamera(floor):null);
 const toggleFullscreen=async()=>{
  if(document.fullscreenElement)await document.exitFullscreen();
  else if(host.current)await host.current.requestFullscreen();
 };
 const visit=(r:RoomGeometry)=>{
  controller.selectRoom(r.id);
  if(currentCamera)controller.setCamera(focusRoom(currentCamera,r));
 };
 const openRoute=(kind:string)=>{
  window.location.hash='#'+(moduleRoutes[kind]??'command');
 };
 const tourStops=(floor?.rooms??[]).map(r=>({
  id:r.id,name:r.label,floor:floor?.level??0,
  x:r.x,y:r.z,description:r.kind,staff:[],
 }));
 return <section className={`hqLive ${light==='night'?'hqNight':'hqDay'}`} ref={host} aria-label="ANUM MINE live headquarters">
  <header className="hqHeader">
   <div className="hqIdentity"><div className="hqMark"><Building2 size={26}/></div><div>
    <span className="hqEyebrow">ANUM OS / YOUR WORKSPACES</span>
    <h1>ANUM <em>MINE</em></h1><p>Virtual Headquarters · Connected Operations</p>
   </div></div>
   <div className="hqHeaderTools">
    <div className="hqConnection" role="status">
     {state?.status==='ready'?<Wifi size={16}/>:<WifiOff size={16}/>}
     <span>{state?.status==='ready'?'Authenticated workspace':state?.status==='refreshing'?'Updating workspace':state?.status??'Connecting'}</span>
    </div>
    <button onClick={()=>setLight(v=>v==='day'?'night':'day')} title="Switch office lighting">
     {light==='day'?<Moon size={18}/>:<Sun size={18}/>}<span>{light==='day'?'Night':'Day'}</span>
    </button>
    <button onClick={()=>void toggleFullscreen()} title="Toggle fullscreen">{full?<Minimize2 size={17}/>:<Maximize2 size={17}/>}</button>
    <button onClick={onClose}><ArrowLeft size={18}/> <span>Return to ANUM OS</span></button>
   </div>
  </header>
  <div className="hqBody">
   <nav className="hqDirectory" aria-label="Office building and floor navigation">
    <div className="hqSectionTitle"><span>HQ DIRECTORY</span><button onClick={()=>void controller.refresh()} disabled={state?.busy} title="Refresh authenticated records"><RefreshCcw size={16}/></button></div>
    <div className="hqDirectoryTitle">Your buildings</div>
    <div className="hqBuildings">
     {(projection?.buildings??[]).map(b=><button type="button" key={b.id}
      className={b.id===building?.id?'active':''} aria-current={b.id===building?.id?'true':undefined}
      onClick={()=>controller.selectBuilding(b.id)}>
      <Building2 size={16}/><span>{b.name}</span><ChevronDown size={14}/>
     </button>)}
    </div>
    <div className="hqDirectoryTitle">Floors</div>
    <div className="hqFloors">
     {(building?.floors??[]).map(f=><button type="button" key={f.id}
      className={f.id===floor?.id?'active':''} aria-current={f.id===floor?.id?'true':undefined}
      onClick={()=>controller.selectFloor(f.id)}><Layers size={17}/><span><strong>{f.name}</strong><small>Level {f.level} · {f.rooms.length} rooms</small></span></button>)}
    </div>
    <div className="hqDirectoryTitle">Office operations</div>
    <div className="hqOperations">
     <button onClick={()=>setOverlay('spaces')}><Building2 size={17}/>Manage spaces</button>
     <button onClick={()=>setOverlay('workstations')}><Users size={17}/>Live workstations</button>
     <button onClick={()=>setOverlay('agentMeetings')}><Users size={17}/>Agent meeting sessions</button>
     <button onClick={()=>setOverlay('reception')}><DoorOpen size={17}/>Reception desk</button>
     <button onClick={()=>setOverlay('tour')}><Compass size={17}/>Guided visit</button>
    </div>
    <div className="hqDirectoryFooter">
     <LockKeyhole size={16}/><p>Building data is scoped to your signed-in organization and workspace. No employee presence is simulated.</p>
    </div>
   </nav>
   <main className="hqViewport">
    <div className="hqViewportTop">
     <div className="hqBreadcrumb"><span>{building?.name??'Choose a building'}</span><span>/</span><strong>{floor?.name??'No floor selected'}</strong></div>
     <div className="hqCameraTools">
      <button disabled={!currentCamera} onClick={()=>{if(currentCamera)controller.setCamera(orbit(currentCamera,-14,0))}} title="Orbit left"><RotateCcw size={15}/></button>
      <button disabled={!currentCamera} onClick={()=>{if(currentCamera)controller.setCamera(orbit(currentCamera,14,0))}} title="Orbit right"><Compass size={15}/></button>
      <button disabled={!currentCamera} onClick={()=>{if(currentCamera)controller.setCamera(dolly(currentCamera,-100))}} title="Zoom in"><ZoomIn size={15}/></button>
      <button disabled={!currentCamera} onClick={()=>{if(currentCamera)controller.setCamera(dolly(currentCamera,100))}} title="Zoom out"><ZoomOut size={15}/></button>
      <button disabled={!floor} onClick={()=>{if(floor)controller.setCamera(initialCamera(floor))}}>Reset camera</button>
     </div>
    </div>
    {floor&&currentCamera?<HeadquartersWebGL floor={floor} selectedRoomId={room?.id??null}
     camera={currentCamera} setCamera={next=>controller.setCamera(next)}
     onSelectRoom={id=>{const found=floor.rooms.find(r=>r.id===id);if(found)visit(found)}} night={light==='night'}/>:
     <section className="hqEmptyScene" role="status"><Building2 size={42}/>
      <h2>{state?.busy?'Loading your workspace':hasServerData?'Select a floor':'Your virtual office is ready to configure'}</h2>
      <p>{state?.busy?'Reading authenticated room and desk records.':
       'No saved office layout is available yet. Create a building and rooms using Office Space Management.'}</p>
      <button onClick={()=>setOverlay('spaces')}>Manage office layout</button>
     </section>}
    <div className="hqViewportFooter"><span>{state?.status==='ready'?'Connected registry':'Live data unavailable'}</span><span>Last verified: {readTime(state?.updatedAt??null)}</span></div>
   </main>
   <aside className="hqInspector" aria-label="Room details and operations">
    <div className="hqSectionTitle"><span>SPACE INSPECTOR</span><DoorOpen size={17}/></div>
    <div className="hqRoomSearch"><Search size={15}/><input aria-label="Find office room" placeholder="Search rooms" value={query} onChange={e=>setQuery(e.target.value)}/></div>
    <div className="hqFilters">
     {(['all','occupied','meeting'] as const).map(v=><button key={v} aria-pressed={filters===v} onClick={()=>setFilters(v)}>{v==='all'?'All':v==='occupied'?'Assigned':'Meetings'}</button>)}
    </div>
    <div className="hqRoomList" role="list">
     {filteredRooms.map(r=><button key={r.id} type="button" role="listitem" className={r.id===room?.id?'active':''}
      onClick={()=>visit(r)}>{roomIcon(r.kind)}<span><strong>{r.label}</strong><small>{r.kind} · capacity {r.capacity}</small></span></button>)}
     {!filteredRooms.length&&<p className="hqMuted">No rooms match your filters.</p>}
    </div>
    <div className="hqRoomDetails">
     <span className="hqEyebrow">SELECTED DEPARTMENT</span>
     <h2>{room?.label??'Choose a room'}</h2>
     <p>{room?room.kind.charAt(0).toUpperCase()+room.kind.slice(1)+' · '+room.capacity+' person capacity':'Select a room to see its desks and connected operations.'}</p>
     {room&&<>
      <div className="hqMetrics"><div><strong>{desks.length}</strong><span>Workstations</span></div><div><strong>{occupied}</strong><span>Assigned</span></div></div>
      <div className="hqDeskEvidence">
       {desks.map(d=><div key={d.id} className="hqDeskRow">
        <div><strong>{d.label}</strong><span>{d.agent?.name??(d.agentId?'Assigned agent (activity unavailable)':'Vacant')}</span></div>
        <small>{readStatus(d.agent)}</small>
       </div>)}
       {!desks.length&&<p className="hqMuted">No desks have been configured for this room.</p>}
      </div>
      <button className="hqPrimaryAction" onClick={()=>openRoute(room.kind)}>Open {room.kind} workspace <ArrowLeft size={17}/></button>
     </>}
    </div>
    {projection&&projection.issues.length>0&&<div className="hqWarnings" role="status"><strong>{projection.issues.length} data checks need attention</strong><p>Open Office Space Management to review room layout, assignments and stale activity.</p></div>}
   </aside>
  </div>
  {state?.error&&<div className="hqToast" role="alert"><WifiOff size={16}/>{state.error.message}<button onClick={()=>void controller.refresh()} disabled={!state.error.canRetry}>Retry</button></div>}
  {overlay!=='none'&&<div className="hqModalHost"><button className="hqModalClose" aria-label="Close office panel" onClick={()=>setOverlay('none')}><X size={18}/></button>
   {overlay==='spaces'?<OfficeSpaceLiveManager onClose={()=>{setOverlay('none');void controller.refresh()}}/>:
    overlay==='workstations'?<OfficeWorkstations onClose={()=>setOverlay('none')}/>:
    overlay==='agentMeetings'?<AgentMeetingSessions onClose={()=>setOverlay('none')}/>:
    overlay==='reception'?<ReceptionOperations onClose={()=>setOverlay('none')} onDestination={name=>{const target=floor?.rooms.find(r=>r.label===name);if(target){visit(target);setOverlay('none')}}}/>:
    <GuidedExecutiveTour site={building?.name??'ANUM MINE'} stops={tourStops} onClose={()=>setOverlay('none')}
     onArrive={id=>{const destination=floor?.rooms.find(r=>r.id===id);if(destination)visit(destination)}}/>}
  </div>}
 </section>;
}
