/**
 * ANUM MINE Reception Operations: deterministic local demonstration engine.
 *
 * This module intentionally does not write to a workspace API. Until the service
 * exists, visitor records remain in component memory only; refreshing clears them.
 * Visitor arrivals and escort steps are displayed as examples, never as live events.
 */
export type VisitKind = 'customer' | 'supplier' | 'candidate' | 'delivery' | 'guest';
export type VisitStage = 'arrived' | 'checked_in' | 'assigned' | 'escorting' | 'completed' | 'cancelled';
export type VisitAction = 'check_in' | 'assign' | 'escort' | 'complete' | 'cancel';
export type VisitPriority = 'normal' | 'urgent';
export interface VisitRecord {
  id:string;
  visitorName:string;
  company:string;
  kind:VisitKind;
  purpose:string;
  host:string|null;
  destination:string|null;
  status:VisitStage;
  priority:VisitPriority;
  arrivedAt:string;
  updatedAt:string;
  timeline:readonly {at:string;step:VisitStage;note:string}[];
}
export interface ReceptionState { visits: readonly VisitRecord[]; revision:number }
export type ReceptionEvent =
 | {type:'arrive';id:string;name:string;company:string;kind:VisitKind;purpose:string;priority:VisitPriority;at:string}
 | {type:'transition';id:string;action:VisitAction;at:string;host?:string;destination?:string}
 | {type:'reset'};
export type ReceptionErrorCode = 'invalid_input' | 'duplicate' | 'not_found' | 'invalid_transition';
export class ReceptionError extends Error {
 readonly code:ReceptionErrorCode;
 constructor(code:ReceptionErrorCode,message:string){super(message);this.code=code;this.name='ReceptionError'}
}
export const EMPTY_RECEPTION:ReceptionState={visits:[],revision:0};
const TRANSITIONS:Record<VisitAction,{from:readonly VisitStage[];to:VisitStage}> = {
  check_in:{from:['arrived'],to:'checked_in'},
  assign:{from:['checked_in'],to:'assigned'},
  escort:{from:['assigned'],to:'escorting'},
  complete:{from:['escorting'],to:'completed'},
  cancel:{from:['arrived','checked_in','assigned','escorting'],to:'cancelled'},
};
function requireText(value:string,label:string,max:number):string {
 const v=value.trim().replace(/\s+/g,' ');
 if(!v||v.length>max)throw new ReceptionError('invalid_input',label+' must be 1–'+max+' characters.');
 return v;
}
function requireTime(at:string):string {
 const d=new Date(at);
 if(!at||!Number.isFinite(d.getTime()))throw new ReceptionError('invalid_input','A valid timestamp is required.');
 return d.toISOString();
}
export function updateReception(state:ReceptionState,event:ReceptionEvent):ReceptionState {
 if(event.type==='reset')return EMPTY_RECEPTION;
 const at=requireTime(event.at);
 if(event.type==='arrive'){
  const id=requireText(event.id,'Visit ID',120);
  if(state.visits.some(v=>v.id===id))throw new ReceptionError('duplicate','This visit is already registered.');
  const name=requireText(event.name,'Visitor name',120);
  const company=requireText(event.company,'Company',120);
  const purpose=requireText(event.purpose,'Visit purpose',240);
  if(!(['customer','supplier','candidate','delivery','guest'] as string[]).includes(event.kind))throw new ReceptionError('invalid_input','Unsupported visitor category.');
  if(!(['urgent','normal'] as string[]).includes(event.priority))throw new ReceptionError('invalid_input','Unsupported priority.');
  const visit:VisitRecord={id,visitorName:name,company,kind:event.kind,purpose,host:null,destination:null,status:'arrived',priority:event.priority,arrivedAt:at,updatedAt:at,timeline:[{at,step:'arrived',note:'Visitor arrived at reception'}]};
  return {visits:[...state.visits,visit],revision:state.revision+1};
 }
 const index=state.visits.findIndex(v=>v.id===event.id);
 if(index<0)throw new ReceptionError('not_found','Visit could not be found.');
 const old=state.visits[index],rule=TRANSITIONS[event.action];
 if(!rule||!rule.from.includes(old.status))throw new ReceptionError('invalid_transition',`Cannot ${event.action.replace('_',' ')} a visit in ${old.status} state.`);
 if(Date.parse(at)<Date.parse(old.updatedAt))throw new ReceptionError('invalid_input','Event time cannot precede the previous visit event.');
 let host=old.host,destination=old.destination;
 if(event.action==='assign'){
  host=requireText(event.host??'','Host employee',120);
  destination=requireText(event.destination??'','Destination room',120);
 }
 const note:Record<VisitAction,string>={
  check_in:'Reception check-in completed',
  assign:'Assigned to '+(host??'host')+' in '+(destination??'room'),
  escort:'Escort started to '+(destination??'destination'),
  complete:'Visitor handover completed',
  cancel:'Visit cancelled at reception',
 };
 const updated:VisitRecord={...old,host,destination,status:rule.to,updatedAt:at,timeline:[...old.timeline,{at,step:rule.to,note:note[event.action]}]};
 const visits=[...state.visits];visits[index]=updated;
 return {visits,revision:state.revision+1};
}
export function receptionCounts(visits:readonly VisitRecord[]) {
 return {
  waiting:visits.filter(v=>['arrived','checked_in'].includes(v.status)).length,
  ready:visits.filter(v=>v.status==='assigned').length,
  escorting:visits.filter(v=>v.status==='escorting').length,
  completed:visits.filter(v=>v.status==='completed').length,
  urgent:visits.filter(v=>v.priority==='urgent'&&!['completed','cancelled'].includes(v.status)).length,
 };
}
export function receptionQueue(visits:readonly VisitRecord[]):VisitRecord[] {
 const rank:Record<VisitStage,number>={arrived:0,checked_in:1,assigned:2,escorting:3,completed:4,cancelled:5};
 return [...visits].sort((a,b)=>Number(b.priority==='urgent')-Number(a.priority==='urgent')||rank[a.status]-rank[b.status]||a.arrivedAt.localeCompare(b.arrivedAt));
}
export function nextReceptionAction(status:VisitStage):VisitAction|null{
 return status==='arrived'?'check_in':status==='checked_in'?'assign':status==='assigned'?'escort':status==='escorting'?'complete':null;
}
