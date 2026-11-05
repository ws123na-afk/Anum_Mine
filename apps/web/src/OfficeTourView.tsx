import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ArrowLeft, Building2, ChevronLeft, ChevronRight, Compass, Expand, Minimize, Moon, RotateCcw, Sun, Volume2, VolumeX, ZoomIn, ZoomOut } from 'lucide-react';
import { OfficeWorkstations } from './OfficeWorkstations';
import { OfficeWebglScene, getRoomHit } from './OfficeWebglScene';
import { OfficeSpaceLiveManager } from './OfficeSpaceLiveManager';
import { nextTourRoom, parseTourDirection } from './lib/officeTourNavigation';
import { OFFICE_LOCATIONS, officeLightAt } from './lib/officeSolar';
import './officeTour.css';

import { ROOMS, SITE_ROOMS, type Dept } from './lib/conceptOfficeData';
// Explicit links to existing authenticated ANUM modules; these are not simulated room actions.
const MODULE_DESTINATIONS: Readonly<Record<string,string>> = {
  command:'command', meeting:'meetings', boardroom:'meetings', finance:'finance',
  sales:'marketing', marketing:'marketing', support:'team', care:'team',
  tyre:'tyre', inspection:'tyre', workshop:'tyre', fleet:'tyre',
  operations:'tasks', orders:'commerce', procurement:'commerce', warehouse:'commerce',
  systems:'knowledge', library:'knowledge', executive:'command', strategy:'command',
  career:'tasks', focus:'tasks',
};
function moduleDestination(roomId: string): string | null {
  return MODULE_DESTINATIONS[roomId] ?? null;
}
const FLOORS = ['Ground · Welcome', 'First · Departments', 'Second · Executive'];
const SITES = ['ANUM Headquarters', 'Tyre Pulse HQ', 'ANUM360 Commerce', 'Personal Office'];

const MANUAL = 'Demonstration mode: floor geometry, avatars, and room activity are illustrative, not live employee status.';
function officeTime(zone: string) { try {return new Intl.DateTimeFormat('en-GB', {timeZone: zone,weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date())}catch{return '--:--'} }
function hourAt(zone: string) { try {return Number(new Intl.DateTimeFormat('en-US',{timeZone:zone,hour:'numeric',hourCycle:'h23'}).format(new Date()))}catch{return 12} }
function say(text: string, enabled: boolean) {
  if (!enabled || !('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'en-US'; utterance.rate = 0.96;
  window.speechSynthesis.speak(utterance);
}
function seededSpot(index:number){return {left:`${18 + (index%3)*25}%`,top:`${38 + Math.floor(index/3)*24}%`};}

export function OfficeTourView() {
  const ref = useRef<HTMLDivElement>(null);
  const [workstationsOpen,setWorkstationsOpen]=useState(false),[spaceRegistryOpen,setSpaceRegistryOpen]=useState(false);
  const [sceneMode,setSceneMode]=useState<'webgl'|'plan'>('webgl');
  const [site,setSite]=useState(0),[floor,setFloor]=useState(0),[angle,setAngle]=useState(-13),[tilt,setTilt]=useState(8),[zoom,setZoom]=useState(1);
  const [pan,setPan]=useState({x:0,y:0});
  const drag=useRef<{id:number;x:number;y:number;startX:number;startY:number;angle:number;tilt:number;panX:number;panY:number;mode:'rotate'|'pan';moved:boolean}|null>(null);
  const [dragged,setDragged]=useState(false);
  const [zone,setZone]=useState('Asia/Riyadh'),[,setNow]=useState(()=>Date.now());
  const [mode,setMode]=useState<'auto'|'day'|'night'>('auto');
  const [roomList,setRoomList]=useState(()=>window.matchMedia('(max-width: 620px)').matches);
  const [selected,setSelected]=useState<string>('reception'),[speaking,setSpeaking]=useState(true),[tour,setTour]=useState(false);
  const [message,setMessage]=useState('Welcome to ANUM MINE. I can guide you to reception, the command center, or a meeting room.');
  const [command,setCommand]=useState(''); const [full,setFull]=useState(false);
  const [listening,setListening]=useState(false);
  const recognitionRef=useRef<{stop:()=>void}|null>(null);
  const rooms=SITE_ROOMS[site]??ROOMS;
  const visible=useMemo(()=>rooms.filter(r=>r.floor===floor),[rooms,floor]);
  const active=rooms.find(r=>r.id===selected);
  const paSpot=active && active.floor===floor ? {left: `${active.x + active.width / 2}%`, top: `${active.y + active.height / 2}%`} : {left:'50%',top:'80%'};
  const activeZone = zone === 'browser' ? Intl.DateTimeFormat().resolvedOptions().timeZone : zone;
  const officeLocation=OFFICE_LOCATIONS[activeZone];
  const solarMode=officeLocation?officeLightAt(new Date(),officeLocation):hourAt(activeZone)>=6&&hourAt(activeZone)<18?'day':'night';
  const dark=mode==='night'||(mode==='auto'&&solarMode!=='day');
  useEffect(()=>{const t=window.setInterval(()=>setNow(Date.now()),30000);return()=>window.clearInterval(t)},[]);
  useEffect(()=>{const onChange=()=>setFull(document.fullscreenElement===ref.current);document.addEventListener('fullscreenchange',onChange);return()=>document.removeEventListener('fullscreenchange',onChange)},[]);
  useEffect(()=>()=>{if('speechSynthesis' in window)window.speechSynthesis.cancel();recognitionRef.current?.stop()},[]);
  const guide=(room: Dept)=>{
    setFloor(room.floor);setSelected(room.id);setTour(true);
    const phrase=`We're now visiting ${room.name}. This department handles ${room.role.toLowerCase()}. ${room.people.length ? 'The example staff positions shown here are ' + room.people.join(', ') + '.' : 'This space is for visitors.'} The office preview does not yet show real-time work.`;
    setMessage(phrase); say(phrase,speaking);
  };
  const follow=()=>{
    const order=['reception','command','meeting','sales','tyre','finance','executive'];
    const next=nextTourRoom(rooms,selected,site===0?order:[]);
    if(next){const found=rooms.find(r=>r.id===next.id);if(found)guide(found)}
  };
  const submit=()=>{
    if(!command.trim())return;
    const instruction=parseTourDirection(command,rooms);setCommand('');
    if(instruction.kind==='pause'){setTour(false);setSpeaking(false);setMessage('Tour paused. Voice muted.');window.speechSynthesis?.cancel();return}
    if(instruction.kind==='restart'){const first=rooms.find(r=>r.floor===0)??rooms[0];if(first)guide(first);return}
    if(instruction.kind==='next'){follow();return}
    if(instruction.kind==='floor'){
      const first=rooms.find(r=>r.floor===instruction.floor);
      if(first){setFloor(instruction.floor);setSelected(first.id);setMessage('Showing '+FLOORS[instruction.floor]+'. Select a department or continue the tour.')}
      else setMessage('That floor is not available in this building.');
      return;
    }
    if(instruction.kind==='room'){const found=rooms.find(r=>r.id===instruction.id);if(found)guide(found);return}
    setMessage('This preview only understands office directions. For real tasks, use ANUM Command and its approval controls.');
  };
  const listen=()=>{
    type RecognitionResult={results:ArrayLike<ArrayLike<{transcript:string}>>};
    type Recognition={start:()=>void;stop:()=>void;lang:string;interimResults:boolean;onresult:((event:RecognitionResult)=>void)|null;onend:(()=>void)|null;onerror:(()=>void)|null};
    type RecognitionCtor=new()=>Recognition;
    const host=window as Window & {SpeechRecognition?:RecognitionCtor;webkitSpeechRecognition?:RecognitionCtor};
    const Ctor=host.SpeechRecognition??host.webkitSpeechRecognition;
    if(!Ctor){setMessage('Voice recognition is unavailable in this browser. Use the text instruction box.');return;}
    if(listening){recognitionRef.current?.stop();setListening(false);return;}
    if('speechSynthesis' in window)window.speechSynthesis.cancel();
    const recognition=new Ctor();recognitionRef.current=recognition;recognition.lang='en-US';recognition.interimResults=false;
    recognition.onresult=e=>{const words=e.results[0]?.[0]?.transcript??'';setCommand(words);setMessage('Heard: '+words+'. Review and press Go to navigate.');};
    recognition.onend=()=>setListening(false);
    recognition.onerror=()=>{setListening(false);setMessage('Microphone recognition failed. You can type the instruction instead.');};
    try{recognition.start();setListening(true)}catch{setListening(false);setMessage('Microphone is unavailable. Please type the instruction.');}
  };
  const onScenePointerDown=(e:React.PointerEvent<HTMLDivElement>)=>{if(e.button!==0&&e.button!==2)return;if((e.target as HTMLElement).closest('button'))return;drag.current={id:e.pointerId,x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,angle,tilt,panX:pan.x,panY:pan.y,mode:e.shiftKey||e.button===2?'pan':'rotate',moved:false};e.currentTarget.setPointerCapture(e.pointerId)};
  const onScenePointerMove=(e:React.PointerEvent<HTMLDivElement>)=>{const d=drag.current;if(!d||d.id!==e.pointerId)return;const dx=e.clientX-d.x,dy=e.clientY-d.y;if(Math.abs(e.clientX-d.startX)+Math.abs(e.clientY-d.startY)>4){d.moved=true;setDragged(true)}if(d.mode==='pan'){setPan({x:Math.max(-300,Math.min(300,d.panX+dx)),y:Math.max(-220,Math.min(220,d.panY+dy))})}else{setAngle(d.angle+dx*.32);setTilt(Math.max(0,Math.min(35,d.tilt-dy*.075)))}};
  const onScenePointerUp=(e:React.PointerEvent<HTMLDivElement>)=>{const pointer=drag.current;if(pointer?.id===e.pointerId){if(!pointer.moved&&sceneMode==='webgl'){const canvas=e.currentTarget.querySelector<HTMLCanvasElement>('.officeWebglCanvas');if(canvas){const box=canvas.getBoundingClientRect();const hit=getRoomHit(visible,((e.clientX-box.left)/box.width-.5)*2,(.5-(e.clientY-box.top)/box.height)*2,angle,tilt,zoom);const room=visible.find(r=>r.id===hit);if(room)guide(room)}}drag.current=null;if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);setDragged(false)}};
  const onSceneWheel=(e:React.WheelEvent<HTMLDivElement>)=>{if((e.target as HTMLElement).closest('button'))return;setZoom(v=>Math.max(.65,Math.min(1.65,v-e.deltaY*.001))) };
  const onOfficeKeys=(e:React.KeyboardEvent<HTMLDivElement>)=>{if(roomList||(e.target as HTMLElement).closest('input,select,button,textarea'))return;const k=e.key.toLowerCase();if(k==='arrowleft')setAngle(a=>a-10);else if(k==='arrowright')setAngle(a=>a+10);else if(k==='arrowup')setTilt(v=>Math.min(35,v+3));else if(k==='arrowdown')setTilt(v=>Math.max(0,v-3));else if(k==='+'||k==='=')setZoom(v=>Math.min(1.65,v+.1));else if(k==='-')setZoom(v=>Math.max(.65,v-.1));else if(k==='escape'&&document.fullscreenElement)void document.exitFullscreen();else return;e.preventDefault()};
  const toggleFull=async()=>{if(document.fullscreenElement){await document.exitFullscreen()}else await ref.current?.requestFullscreen()};
  return <div ref={ref} className={`anumOfficeTour ${dark?'night':'day'} ${roomList?'listMode':''}`} tabIndex={0} onKeyDown={onOfficeKeys}>
    <header className="officeTop">
      <div className="officeBrand"><strong>ANUM <span>MINE</span></strong><small>Virtual Headquarters <span className="officeLiveDivider">/</span> Immersive Workspace</small></div>
      <div className="officeTopCenter"><Building2 size={16}/><select aria-label="Office building" value={site} onChange={e=>{const next=Number(e.target.value);setSite(next);setFloor(0);setSelected(SITE_ROOMS[next]?.find(r=>r.floor===0)?.id??'reception');setMessage('Welcome to '+SITES[next]+'. Select a room or ask for the next stop. This is an illustrative layout.')}}>{SITES.map((s,i)=><option key={s} value={i}>{s}</option>)}</select><span className="officeDemo">CONCEPT VIEW</span><button type="button" className="office3dToggle" aria-pressed={sceneMode==='webgl'} onClick={()=>setSceneMode(v=>v==='webgl'?'plan':'webgl')}>{sceneMode==='webgl'?'3D Scene · Switch to Plan':'Plan · Switch to 3D'}</button></div>
      <div className="officeRight"><label>Office time zone <select value={zone} onChange={e=>setZone(e.target.value)}><option value="Asia/Riyadh">Riyadh</option><option value="Asia/Dubai">Dubai</option><option value="Asia/Karachi">Karachi</option><option value="browser">Browser</option></select></label><strong>{officeTime(activeZone)}</strong><button title="Exit office" onClick={()=>{window.location.hash='#home'}}><ArrowLeft size={18}/> Back</button></div>
    </header>
    <main className="officeWorld">
      {!roomList && <div className={`officeScene ${dragged?'isDragging':''}`} role="region" aria-label="Office camera: drag to rotate, Shift-drag to pan, scroll to zoom, arrow keys to rotate and tilt" onPointerDown={onScenePointerDown} onPointerMove={onScenePointerMove} onPointerUp={onScenePointerUp} onPointerCancel={onScenePointerUp} onWheel={onSceneWheel} onContextMenu={e=>e.preventDefault()} style={{'--office-angle':`${angle}deg`,'--office-tilt':`${tilt}deg`,'--office-zoom':zoom,'--office-pan-x':`${pan.x}px`,'--office-pan-y':`${pan.y}px`} as CSSProperties}>
        {sceneMode==='webgl'?<OfficeWebglScene rooms={visible} selected={selected} night={dark} angle={angle} tilt={tilt} zoom={zoom} onSelect={id=>{if(dragged)return;const room=visible.find(r=>r.id===id);if(room)guide(room)}}/>:<div className="officePlatform">
          <div className="officeCentralWalkway" aria-hidden="true"/><div className="officePlanter officePlanterOne" aria-hidden="true"/><div className="officePlanter officePlanterTwo" aria-hidden="true"/>
          <div className="officeAtrium"><span>ANUM <em>MINE</em></span><small>{SITES[site]} · {FLOORS[floor]}</small></div>
          {visible.map(r=><button key={r.id} className={`officeRoom ${selected===r.id?'picked':''}`} style={{left:`${r.x}%`,top:`${r.y}%`,width:`${r.width}%`,height:`${r.height}%`,'--room-accent':r.accent} as CSSProperties} onClick={()=>guide(r)} aria-label={`Visit ${r.name}`}>
            <span className="officeRoomTitle"><span className="officeRoomStatusDot" aria-hidden="true"/>{r.name}</span><span className="officeRoomRole">{r.role}</span>
            <span className="officeRoomInterior" aria-hidden="true">
              <span className="officeGlassWall"/><span className="officeFurnitureFurniture">
                <span className="officeDeskFurniture"><span className="officeMonitor"/><span className="officeKeyboard"/></span>
                <span className="officeChairFurniture"/>
                <span className="officeDeskFurniture secondary"><span className="officeMonitor"/><span className="officeKeyboard"/></span>
                <span className="officeChairFurniture secondary"/>
              </span>
              <span className="officeRoomLighting"/>
            </span>
            {r.people.slice(0,4).map((p,i)=><span key={p+i} className="officePerson" style={seededSpot(i)} title={`Example: ${p}`}><span>●</span></span>)}
          </button>)}
          <div className="officePaPresence" style={paSpot}><span aria-hidden="true">✦</span> PA · Escorting you</div>
        </div>}
      </div>}
      <aside className="officeFloorPanel"><div className="officePanelEyebrow">BUILDING DIRECTORY</div><h2>Explore the Office</h2><button type="button" className="officeViewToggle" aria-pressed={roomList} onClick={()=>setRoomList(v=>!v)}>{roomList?'Show 3D scene':'Show room list'}</button>
      <div className="officeModuleButtons">
        <button type="button" onClick={()=>{setWorkstationsOpen(true);setSpaceRegistryOpen(false)}}>Live Employee Workstations</button>
        <button type="button" onClick={()=>{setSpaceRegistryOpen(true);setWorkstationsOpen(false)}}>Office Space Management</button>
      </div>{FLOORS.map((f,i)=><button className={i===floor?'active':''} key={f} onClick={()=>{setFloor(i);setSelected(rooms.find(r=>r.floor===i)?.id??rooms[0].id)}}>{i===0?'G':i} <span>{f}</span></button>)}<div className="officeMode"><label>Lighting</label><select value={mode} onChange={e=>setMode(e.target.value as 'auto'|'day'|'night')}><option value="auto">Auto · Sun position</option><option value="day">Day preview</option><option value="night">Night preview</option></select>{dark?<Moon size={16}/>:<Sun size={16}/>}</div></aside>
      {roomList && <section className="officeRoomList" aria-label={`${FLOORS[floor]} rooms`}><h2>Rooms on {FLOORS[floor]}</h2><p>Illustrative room layout. Select a room to explore its purpose.</p><div>{visible.map(r=><button type="button" key={r.id} aria-pressed={selected===r.id} onClick={()=>guide(r)}><strong>{r.name}</strong><span>{r.role}</span></button>)}</div></section>}
      <aside className="officeInfo"><h2>{active?.name||'Select a room'}</h2><p>{active?.role}</p><strong>Example employee roles</strong>{active?.people.length?active.people.map(p=><div className="officePersonLabel" key={p}><span>◉</span> {p}</div>):<small>Visitor and collaboration space</small>}{active&&moduleDestination(active.id)&&<button type="button" className="officeOpenModule" onClick={()=>{window.location.hash='#'+moduleDestination(active.id)}}>Open {active.name} workspace <ChevronRight size={16}/></button>}<small>{MANUAL}</small></aside>
      <div className="officePa"><div className="officePaAvatar" aria-hidden="true">✦</div><div className="officePaMessage"><strong>ANUM · Personal Assistant</strong><p>{message}</p><div className="officePaActions"><button onClick={follow}>Take me to the next stop <ChevronRight size={14}/></button><button onClick={()=>{setTour(false);window.speechSynthesis?.cancel();setMessage('Tour paused. Select a department whenever you are ready.')}}>Pause tour</button><button onClick={()=>{setSpeaking(v=>!v);if(speaking)window.speechSynthesis?.cancel()}} title={speaking?'Mute PA voice':'Enable PA voice'}>{speaking?<Volume2 size={16}/>:<VolumeX size={16}/>}</button></div><form onSubmit={e=>{e.preventDefault();submit()}}><input value={command} onChange={e=>setCommand(e.target.value)} aria-label="Give the PA a direction" placeholder="Take me to finance, next room, first floor..." /><button type="button" aria-label={listening ? "Stop listening" : "Dictate navigation"} aria-pressed={listening} onClick={listen}>{listening ? "Stop mic" : "Mic"}</button><button type="submit">Go</button></form></div></div>
      <div className="officeControls"><button onClick={()=>setAngle(a=>a-15)} title="Rotate left"><ChevronLeft/> Rotate</button><button onClick={()=>setAngle(a=>a+15)} title="Rotate right"><ChevronRight/></button><button onClick={()=>setTilt(v=>Math.min(35,v+5))} title="Tilt up"><Compass/></button><button onClick={()=>setTilt(v=>Math.max(0,v-5))} title="Tilt down"><RotateCcw/></button><button onClick={()=>setZoom(v=>Math.max(.65,v-.12))} title="Zoom out"><ZoomOut/></button><button onClick={()=>setZoom(v=>Math.min(1.65,v+.12))} title="Zoom in"><ZoomIn/></button><button onClick={()=>{setAngle(-9);setTilt(11);setZoom(1);setPan({x:0,y:0})}}>Reset</button><button onClick={()=>void toggleFull()} title="Toggle full screen">{full?<Minimize/>:<Expand/>} Full screen</button></div>
      {workstationsOpen&&<OfficeWorkstations onClose={()=>setWorkstationsOpen(false)}/>}
      {spaceRegistryOpen&&<OfficeSpaceLiveManager onClose={()=>setSpaceRegistryOpen(false)}/>}
    </main><footer className="officeFooter"><span>{tour?'Guided tour active':'Ready for your visit'}</span><span>Preview · Illustrative staff and rooms · No real task execution</span><span>{SITES[site]+' · layout concept'}</span></footer>
  </div>;
}
