/** Honest office presence projection from already-authorized workspace records.
 * This is a pure view model, not a new source of truth or presence heartbeat.
 * Callers MUST load records in their existing tenant/workspace context.
 */
export type OfficeStatus='working'|'queued'|'waiting_approval'|'blocked'|'paused'|'idle'|'unknown';
export interface OfficeAgent {id:string;name:string;status:'active'|'paused';department:string|null}
export interface OfficeTask {id:string;agent_id:string|null;status:string}
export interface OfficePresence {agentId:string;name:string;department:string|null;status:OfficeStatus;taskIds:string[];observedAt:string;source:'workspace_tasks'|'agent_profile'|'incomplete';}
export interface PresenceInput {
 agents:readonly OfficeAgent[];
 tasks:readonly OfficeTask[];
 observedAt:string;
 /** True only if all relevant open-task pages were loaded successfully. */
 complete:boolean;
 /** Waiting-for-approval tasks are recorded even if the approvals API is unavailable. */
}
const attention=new Set(['blocked']);
const activeStates=new Set(['running','executing']);
const queuedStates=new Set(['created','queued','planning']);
export function projectOfficePresence(input:PresenceInput):OfficePresence[] {
 if(!Number.isFinite(Date.parse(input.observedAt)))throw new RangeError('observedAt must be an ISO timestamp');
 return input.agents.map(agent=>{
   const tasks=input.tasks.filter(t=>t.agent_id===agent.id);
   let status:OfficeStatus='idle',source:OfficePresence['source']='workspace_tasks';
   if(agent.status==='paused'){status='paused';source='agent_profile'}
   else if(tasks.some(t=>activeStates.has(t.status)))status='working';
   else if(tasks.some(t=>t.status==='waiting_approval'))status='waiting_approval';
   else if(tasks.some(t=>attention.has(t.status)))status='blocked';
   else if(tasks.some(t=>queuedStates.has(t.status)))status='queued';
   else if(!input.complete){status='unknown';source='incomplete'}
   else status='idle';
   // Never pretend that the presence of a queued task proves active model execution.
   const taskIds=tasks.filter(t=>activeStates.has(t.status)||queuedStates.has(t.status)||t.status==='waiting_approval'||attention.has(t.status)).map(t=>t.id);
   return {agentId:agent.id,name:agent.name,department:agent.department,status,taskIds,observedAt:input.observedAt,source};
 });
}
export function staleOfficePresence(presence:OfficePresence,now:string,limitMs=90_000):boolean {
 const a=Date.parse(presence.observedAt),b=Date.parse(now);
 return !Number.isFinite(a)||!Number.isFinite(b)||b<a||b-a>limitMs;
}
