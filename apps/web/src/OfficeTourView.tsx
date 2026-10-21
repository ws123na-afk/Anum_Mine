import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ArrowLeft, Building2, ChevronLeft, ChevronRight, Compass, Expand, Minimize, Moon, RotateCcw, Sun, Volume2, VolumeX, ZoomIn, ZoomOut } from 'lucide-react';
import './officeTour.css';

type Dept = { id: string; name: string; role: string; people: string[]; x: number; y: number; width: number; height: number; floor: number; accent: string };
const ROOMS: Dept[] = [
  { id: 'reception', name: 'Reception', role: 'Visitors and scheduling', people: ['Reception coordinator', 'Security'], x: 6, y: 65, width: 27, height: 26, floor: 0, accent: '#e8ba74' },
  { id: 'command', name: 'ANUM Command', role: 'Chief of Staff and task oversight', people: ['ANUM - Chief of Staff', 'Automation Guardian'], x: 36, y: 39, width: 30, height: 30, floor: 0, accent: '#4eccc3' },
  { id: 'meeting', name: 'Boardroom', role: 'Live and scheduled meetings', people: ['Meeting coordinator'], x: 70, y: 65, width: 24, height: 25, floor: 0, accent: '#80a7ec' },
  { id: 'finance', name: 'Finance', role: 'Budgets, reconciliation and approvals', people: ['Finance Controller'], x: 6, y: 5, width: 27, height: 29, floor: 1, accent: '#edc88d' },
  { id: 'sales', name: 'Sales & Marketing', role: 'Proposals, pipeline and campaigns', people: ['Fleet Sales Pipeline', 'Fleet Proposal Consultant', 'Marketing Studio'], x: 36, y: 5, width: 30, height: 29, floor: 1, accent: '#89c1fd' },
  { id: 'support', name: 'Customer Experience', role: 'Customer queries and service', people: ['Customer Care', 'Product Support'], x: 70, y: 5, width: 24, height: 29, floor: 1, accent: '#f2b0b6' },
  { id: 'tyre', name: 'Tyre Engineering', role: 'Planning and fleet inspection', people: ['Tyre Fleet Planner', 'Tyre Analyst'], x: 6, y: 40, width: 27, height: 25, floor: 1, accent: '#7dc9a5' },
  { id: 'operations', name: 'Operations', role: 'Warehouse and asset operations', people: ['Operations Assistant'], x: 36, y: 40, width: 30, height: 25, floor: 1, accent: '#9dc6ed' },
  { id: 'systems', name: 'Systems & Knowledge', role: 'Knowledge and automation', people: ['Knowledge Steward', 'Automation Guardian'], x: 70, y: 40, width: 24, height: 25, floor: 1, accent: '#c0a4ef' },
  { id: 'executive', name: 'Executive Office', role: 'Owner and Chief of Staff', people: ['ANUM - Chief of Staff'], x: 6, y: 8, width: 42, height: 38, floor: 2, accent: '#edce93' },
  { id: 'strategy', name: 'Strategy Room', role: 'Planning and decisions', people: ['ANUM - Chief of Staff', 'Finance Controller'], x: 53, y: 8, width: 41, height: 38, floor: 2, accent: '#9bbbe5' },
  { id: 'lounge', name: 'Executive Lounge', role: 'Visitor conversations', people: [], x: 27, y: 56, width: 47, height: 30, floor: 2, accent: '#90bba9' },
];
const FLOORS = ['Ground · Welcome', 'First · Departments', 'Second · Executive'];
const SITES = ['ANUM Headquarters', 'Tyre Pulse HQ', 'ANUM360 Commerce', 'Personal Office'];
// These layouts are illustrative previews, not mappings of actual workspace agents or task statuses.
const SITE_ROOMS: Dept[][] = [ROOMS, [
  {id:'reception',name:'Fleet Reception',role:'Customer enquiries and inspection appointments',people:['Reception coordinator'],x:5,y:61,width:27,height:28,floor:0,accent:'#f1c689'},
  {id:'inspection',name:'Inspection Control',role:'Tyre condition, serial position and inspection schedules',people:['Tyre Fleet Planner','Tyre Analyst'],x:36,y:8,width:30,height:40,floor:0,accent:'#8bdfd2'},
  {id:'workshop',name:'Workshop Control',role:'Service bays, defects and maintenance coordination',people:['Operations Assistant'],x:70,y:9,width:24,height:38,floor:0,accent:'#90adf0'},
  {id:'fleet',name:'Fleet Planning',role:'Tyre rotations and fleet utilization',people:['Tyre Fleet Planner'],x:6,y:8,width:27,height:38,floor:1,accent:'#84dca2'},
  {id:'procurement',name:'Procurement',role:'Tyre requests and supplier proposals',people:['Fleet Proposal Consultant'],x:37,y:12,width:28,height:39,floor:1,accent:'#efd39e'},
  {id:'analysis',name:'Performance Analysis',role:'Tyre life, cost per kilometre and failure patterns',people:['Tyre Analyst'],x:70,y:10,width:24,height:38,floor:1,accent:'#a4bffc'},
  {id:'boardroom',name:'Fleet Boardroom',role:'Performance reviews and inspection decisions',people:['ANUM - Chief of Staff'],x:28,y:62,width:44,height:28,floor:2,accent:'#e7bc98'}
], [
  {id:'reception',name:'Customer Reception',role:'Customer enquiries and order enquiries',people:['Customer Care'],x:7,y:64,width:29,height:28,floor:0,accent:'#f7c798'},
  {id:'care',name:'Customer Care',role:'Multichannel customer support',people:['Customer Care','Product Support'],x:41,y:8,width:26,height:42,floor:0,accent:'#88e0d2'},
  {id:'orders',name:'Orders Desk',role:'Order review, fulfilment and returns',people:['Commerce Coordinator'],x:71,y:8,width:23,height:42,floor:0,accent:'#a8cbf4'},
  {id:'sales',name:'Marketplace Sales',role:'Sales channels, campaigns and lead follow-up',people:['Marketing Studio','Commerce Coordinator'],x:5,y:9,width:28,height:37,floor:1,accent:'#ecbaeb'},
  {id:'warehouse',name:'Dispatch & Logistics',role:'Packing handovers and shipping coordination',people:['Commerce Coordinator'],x:37,y:12,width:30,height:38,floor:1,accent:'#bddca1'},
  {id:'strategy',name:'Commerce Review',role:'Customer performance and business reviews',people:['ANUM - Chief of Staff'],x:29,y:59,width:43,height:32,floor:2,accent:'#eac58e'}
], [
  {id:'welcome',name:'Personal Reception',role:'Private scheduling and guest welcome',people:['Personal assistant'],x:6,y:59,width:32,height:32,floor:0,accent:'#e8c390'},
  {id:'focus',name:'Work & Focus',role:'Personal planning and daily priorities',people:['Personal assistant'],x:42,y:9,width:51,height:37,floor:0,accent:'#a6d6c6'},
  {id:'career',name:'Career Office',role:'Employment responsibilities and professional goals',people:['Personal assistant'],x:6,y:8,width:29,height:35,floor:1,accent:'#b3c9f3'},
  {id:'finance',name:'Personal Finance',role:'Budgeting, costs and financial reviews',people:['Finance Controller'],x:42,y:8,width:52,height:37,floor:1,accent:'#e8ce90'},
  {id:'library',name:'Private Knowledge',role:'Notes, documents and research',people:['Knowledge Steward'],x:27,y:55,width:46,height:33,floor:2,accent:'#aec9e6'}
]];

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
  const [site,setSite]=useState(0),[floor,setFloor]=useState(0),[angle,setAngle]=useState(-9),[tilt,setTilt]=useState(11),[zoom,setZoom]=useState(1);
  const [pan,setPan]=useState({x:0,y:0});
  const drag=useRef<{id:number;x:number;y:number;startX:number;startY:number;angle:number;tilt:number;panX:number;panY:number;mode:'rotate'|'pan';moved:boolean}|null>(null);
  const [dragged,setDragged]=useState(false);
  const [zone,setZone]=useState('Asia/Riyadh'),[,setNow]=useState(()=>Date.now());
  const [mode,setMode]=useState<'auto'|'day'|'night'>('auto');
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
  const dark=mode==='night'||(mode==='auto'&&(hourAt(activeZone)<6||hourAt(activeZone)>=18));
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
    const itinerary=site===0?order:rooms.map(r=>r.id);const next=rooms.find(r=>r.id===itinerary[(itinerary.indexOf(selected)+1)%itinerary.length])!;
    setTour(true);guide(next);
  };
  const submit=()=>{
    const phrase=command.trim().toLowerCase();if(!phrase)return;
    setCommand('');
    if(/stop|quiet|mute/.test(phrase)){setTour(false);setSpeaking(false);setMessage('Tour paused. Voice muted.');window.speechSynthesis?.cancel();return;}
    if(/next|tour|guide|continue/.test(phrase)){follow();return;}
    if(/floor (one|1)|first floor/.test(phrase)){setFloor(1);setMessage('Showing the first floor.');return;}
    if(/floor (two|2)|second floor/.test(phrase)){setFloor(2);setMessage('Showing the executive floor.');return;}
    const found=rooms.find(r=>phrase.includes(r.id)||phrase.includes(r.name.toLowerCase().split(' ')[0]));
    if(found){guide(found);return;}
    setMessage('I can navigate this preview to rooms or floors. Actual agent commands will use the governed ANUM Command service after integration.');
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
  const onScenePointerUp=(e:React.PointerEvent<HTMLDivElement>)=>{if(drag.current?.id===e.pointerId){drag.current=null;if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);setDragged(false)}};
  const onSceneWheel=(e:React.WheelEvent<HTMLDivElement>)=>{if((e.target as HTMLElement).closest('button'))return;setZoom(v=>Math.max(.65,Math.min(1.65,v-e.deltaY*.001))) };
  const onOfficeKeys=(e:React.KeyboardEvent<HTMLDivElement>)=>{if((e.target as HTMLElement).closest('input,select,button,textarea'))return;const k=e.key.toLowerCase();if(k==='arrowleft')setAngle(a=>a-10);else if(k==='arrowright')setAngle(a=>a+10);else if(k==='arrowup')setTilt(v=>Math.min(35,v+3));else if(k==='arrowdown')setTilt(v=>Math.max(0,v-3));else if(k==='+'||k==='=')setZoom(v=>Math.min(1.65,v+.1));else if(k==='-')setZoom(v=>Math.max(.65,v-.1));else if(k==='escape'&&document.fullscreenElement)void document.exitFullscreen();else return;e.preventDefault()};
  const toggleFull=async()=>{if(document.fullscreenElement){await document.exitFullscreen()}else await ref.current?.requestFullscreen()};
  return <div ref={ref} className={`anumOfficeTour ${dark?'night':'day'}`} tabIndex={0} onKeyDown={onOfficeKeys}>
    <header className="officeTop">
      <div className="officeBrand"><strong>ANUM <span>MINE</span></strong><small>Virtual Headquarters · Interactive Preview</small></div>
      <div className="officeTopCenter"><Building2 size={16}/><select aria-label="Office building" value={site} onChange={e=>{const next=Number(e.target.value);setSite(next);setFloor(0);setSelected(SITE_ROOMS[next]?.find(r=>r.floor===0)?.id??'reception');setMessage('Welcome to '+SITES[next]+'. Select a room or ask for the next stop. This is an illustrative layout.')}}>{SITES.map((s,i)=><option key={s} value={i}>{s}</option>)}</select><span className="officeDemo">DEMO</span></div>
      <div className="officeRight"><label>Office time zone <select value={zone} onChange={e=>setZone(e.target.value)}><option value="Asia/Riyadh">Riyadh</option><option value="Asia/Dubai">Dubai</option><option value="Asia/Karachi">Karachi</option><option value="browser">Browser</option></select></label><strong>{officeTime(activeZone)}</strong><button title="Exit office" onClick={()=>{window.location.hash='#agents/team'}}><ArrowLeft size={18}/> Back</button></div>
    </header>
    <main className="officeWorld">
      <div className={`officeScene ${dragged?'isDragging':''}`} role="region" aria-label="Office camera: drag to rotate, Shift-drag to pan, scroll to zoom, arrow keys to rotate and tilt" onPointerDown={onScenePointerDown} onPointerMove={onScenePointerMove} onPointerUp={onScenePointerUp} onPointerCancel={onScenePointerUp} onWheel={onSceneWheel} onContextMenu={e=>e.preventDefault()} style={{'--office-angle':`${angle}deg`,'--office-tilt':`${tilt}deg`,'--office-zoom':zoom,'--office-pan-x':`${pan.x}px`,'--office-pan-y':`${pan.y}px`} as CSSProperties}>
        <div className="officePlatform">
          <div className="officeAtrium"><span>ANUM MINE</span><small>{FLOORS[floor]}</small></div>
          {visible.map(r=><button key={r.id} className={`officeRoom ${selected===r.id?'picked':''}`} style={{left:`${r.x}%`,top:`${r.y}%`,width:`${r.width}%`,height:`${r.height}%`,'--room-accent':r.accent} as CSSProperties} onClick={()=>guide(r)} aria-label={`Visit ${r.name}`}>
            <span className="officeRoomTitle">{r.name}</span><span className="officeRoomRole">{r.role}</span>
            <span className="officeRoomFurniture">{r.id==='reception'?'▤   ▤':r.id==='lounge'?'▰  ▰':'▣   ▣   ▣'}</span>
            {r.people.slice(0,4).map((p,i)=><span key={p+i} className="officePerson" style={seededSpot(i)} title={p}><span>●</span></span>)}
          </button>)}
          <div className="officePaPresence" style={paSpot}><span aria-hidden="true">✦</span> PA · Escorting you</div>
        </div>
      </div>
      <aside className="officeFloorPanel"><h2>Floors</h2>{FLOORS.map((f,i)=><button className={i===floor?'active':''} key={f} onClick={()=>{setFloor(i);setSelected(rooms.find(r=>r.floor===i)?.id??rooms[0].id)}}>{i===0?'G':i} <span>{f}</span></button>)}<div className="officeMode"><label>Lighting</label><select value={mode} onChange={e=>setMode(e.target.value as 'auto'|'day'|'night')}><option value="auto">Auto · Office time</option><option value="day">Day preview</option><option value="night">Night preview</option></select>{dark?<Moon size={16}/>:<Sun size={16}/>}</div></aside>
      <aside className="officeInfo"><h2>{active?.name||'Select a room'}</h2><p>{active?.role}</p><strong>Example employee roles</strong>{active?.people.length?active.people.map(p=><div className="officePersonLabel" key={p}><span>◉</span> {p}</div>):<small>Visitor and collaboration space</small>}<small>{MANUAL}</small></aside>
      <div className="officePa"><div className="officePaAvatar" aria-hidden="true">✦</div><div className="officePaMessage"><strong>ANUM · Personal Assistant</strong><p>{message}</p><div className="officePaActions"><button onClick={follow}>Take me to the next stop <ChevronRight size={14}/></button><button onClick={()=>{setTour(false);window.speechSynthesis?.cancel();setMessage('Tour paused. Select a department whenever you are ready.')}}>Pause tour</button><button onClick={()=>{setSpeaking(v=>!v);if(speaking)window.speechSynthesis?.cancel()}} title={speaking?'Mute PA voice':'Enable PA voice'}>{speaking?<Volume2 size={16}/>:<VolumeX size={16}/>}</button></div><form onSubmit={e=>{e.preventDefault();submit()}}><input value={command} onChange={e=>setCommand(e.target.value)} aria-label="Give the PA a direction" placeholder="Take me to finance, next room, first floor..." /><button type="button" aria-label={listening ? "Stop listening" : "Dictate navigation"} aria-pressed={listening} onClick={listen}>{listening ? "Stop mic" : "Mic"}</button><button type="submit">Go</button></form></div></div>
      <div className="officeControls"><button onClick={()=>setAngle(a=>a-15)} title="Rotate left"><ChevronLeft/> Rotate</button><button onClick={()=>setAngle(a=>a+15)} title="Rotate right"><ChevronRight/></button><button onClick={()=>setTilt(v=>Math.min(35,v+5))} title="Tilt up"><Compass/></button><button onClick={()=>setTilt(v=>Math.max(0,v-5))} title="Tilt down"><RotateCcw/></button><button onClick={()=>setZoom(v=>Math.max(.65,v-.12))} title="Zoom out"><ZoomOut/></button><button onClick={()=>setZoom(v=>Math.min(1.65,v+.12))} title="Zoom in"><ZoomIn/></button><button onClick={()=>{setAngle(-9);setTilt(11);setZoom(1);setPan({x:0,y:0})}}>Reset</button><button onClick={()=>void toggleFull()} title="Toggle full screen">{full?<Minimize/>:<Expand/>} Full screen</button></div>
    </main><footer className="officeFooter"><span>{tour?'Guided tour active':'Ready for your visit'}</span><span>Preview · Illustrative staff and rooms · No real task execution</span><span>{SITES[site]+' · layout concept'}</span></footer>
  </div>;
}
