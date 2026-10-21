/** Executive guided-tour model, pure and deterministic.
 * This module drives a staged *preview* and never assumes agents or humans are online.
 * Room transitions and floor changes are represented as stops, not physical pathfinding.
 */
export interface TourStop { id:string; name:string; floor:number; x:number; y:number; description:string; staff:readonly string[] }
export type TourPhase='ready'|'touring'|'paused'|'finished';
export interface TourEvent {at:string;kind:'arrival'|'visit'|'pause'|'resume'|'finish'|'redirect';roomId:string|null;description:string}
export interface GuidedTour {
 phase:TourPhase;
 site:string;
 index:number;
 stops:readonly TourStop[];
 visited:readonly string[];
 events:readonly TourEvent[];
 currentRoomId:string|null;
}
export type TourAction=
 | {type:'begin';at:string}
 | {type:'next';at:string}
 | {type:'pause';at:string}
 | {type:'resume';at:string}
 | {type:'redirect';roomId:string;at:string}
 | {type:'reset';site:string;stops:readonly TourStop[];at:string};
export class GuidedTourError extends Error {constructor(message:string){super(message);this.name='GuidedTourError'}}
function time(at:string){const t=Date.parse(at);if(!Number.isFinite(t))throw new GuidedTourError('Invalid event time');return new Date(t).toISOString()}
function log(state:GuidedTour,kind:TourEvent['kind'],at:string,description:string,roomId=state.currentRoomId):TourEvent[]{
 if(state.events.length&&Date.parse(at)<Date.parse(state.events[state.events.length-1].at))throw new GuidedTourError('Event timestamps must be monotonic');
 return [...state.events,{kind,at,description,roomId}];
}
export function createGuidedTour(site:string,stops:readonly TourStop[],at:string):GuidedTour {
 const t=time(at);
 const ids=new Set<string>();
 for(const stop of stops){
  if(!stop.id||!stop.name||!Number.isInteger(stop.floor)||stop.floor<0||!Number.isFinite(stop.x)||!Number.isFinite(stop.y)||ids.has(stop.id))throw new GuidedTourError('Invalid or duplicate tour stop');
  ids.add(stop.id);
 }
 return {phase:'ready',site,index:-1,stops:[...stops],currentRoomId:null,visited:[],events:[{at:t,kind:'arrival',roomId:null,description:'CEO arrived at the virtual office'}]};
}
function visit(state:GuidedTour,index:number,at:string,kind:'visit'|'redirect'='visit'):GuidedTour {
 const stop=state.stops[index];if(!stop)throw new GuidedTourError('Destination unavailable');
 const updated={...state,currentRoomId:stop.id,index,visited:state.visited.includes(stop.id)?state.visited:[...state.visited,stop.id],phase:'touring' as const};
 return {...updated,events:log(state,kind,at,'PA guided CEO to '+stop.name,stop.id)};
}
export function changeGuidedTour(state:GuidedTour,action:TourAction):GuidedTour {
 if(action.type==='reset')return createGuidedTour(action.site,action.stops,action.at);
 const at=time(action.at);
 if(action.type==='begin'){
  if(state.phase!=='ready')throw new GuidedTourError('Tour already started');
  if(!state.stops.length)return {...state,phase:'finished',events:log(state,'finish',at,'No rooms available for tour')};
  return visit(state,0,at);
 }
 if(action.type==='pause'){
  if(state.phase!=='touring')throw new GuidedTourError('Tour is not running');
  return {...state,phase:'paused',events:log(state,'pause',at,'CEO paused the guided tour')};
 }
 if(action.type==='resume'){
  if(state.phase!=='paused')throw new GuidedTourError('Tour was not paused');
  return {...state,phase:'touring',events:log(state,'resume',at,'PA resumed the guided tour')};
 }
 if(action.type==='redirect'){
  if(state.phase==='finished')throw new GuidedTourError('Restart tour before redirecting');
  const index=state.stops.findIndex(stop=>stop.id===action.roomId);
  if(index<0)throw new GuidedTourError('Room is not included in this building');
  return visit(state,index,at,'redirect');
 }
 if(action.type==='next'){
  if(state.phase==='ready')return changeGuidedTour(state,{type:'begin',at});
  if(state.phase!=='touring')throw new GuidedTourError('Resume the tour before continuing');
  if(state.index+1>=state.stops.length)return {...state,phase:'finished',events:log(state,'finish',at,'PA completed the office tour')};
  return visit(state,state.index+1,at);
 }
 return state;
}
export function tourProgress(state:GuidedTour):number{return state.stops.length?Math.round(100*state.visited.length/state.stops.length):100}
export function describeTourStop(stop:TourStop):string {
 const roles=stop.staff.length?' Example roles in this area: '+stop.staff.join(', ')+'.':'';
 return 'Welcome to '+stop.name+'. '+stop.description+'.'+roles+' This is a guided demonstration, not live employee activity.';
}
