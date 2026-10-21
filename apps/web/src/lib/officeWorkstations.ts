/** Workspace-scoped adapter; it reuses the authorization, request and pagination
 * rules of the existing Org Chart, rather than creating a second privileged API.
 */
import type { OrgSources } from './orgChart.ts';
import { loadOrgSources } from './orgChart.ts';
import { projectOfficePresence, type OfficePresence, type OfficeAgent, type OfficeTask } from './officePresence.ts';
import * as api from './api.ts';
export interface WorkstationRecord extends OfficePresence {
  taskTitles:readonly string[];
  profileStatus:string;
  /** Room assignment is a suggestion for the view, not a persisted desk location. */
  suggestedDepartment:string;
}
export interface WorkstationSnapshot {
 scope:{tenantId:string;workspaceId:string};
 observedAt:string;
 records:readonly WorkstationRecord[];
 partialSources:readonly string[];
 complete:boolean;
}
export function workstationDepartment(department:string|null):string {
  const d=(department??'').trim().toLocaleLowerCase('en');
  if(/tyre|fleet/.test(d))return 'Tyre Engineering';
  if(/sales|marketing/.test(d))return 'Sales & Marketing';
  if(/finance|account/.test(d))return 'Finance';
  if(/support|customer|care/.test(d))return 'Customer Experience';
  if(/ops|operation|warehouse|logistic/.test(d))return 'Operations';
  if(/system|engineering|knowledge|developer|technology/.test(d))return 'Systems & Knowledge';
  return 'Unassigned';
}
export function projectWorkstationSnapshot(sources:Pick<OrgSources,'agents'|'tasks'|'partial'>,scope:{tenantId:string;workspaceId:string},observedAt:string):WorkstationSnapshot {
  // Defensive scope check even though the authenticated API is workspace-scoped.
  const agents=sources.agents.filter(a=>a.tenant_id===scope.tenantId&&a.workspace_id===scope.workspaceId);
  const tasks=sources.tasks.filter(t=>t.tenant_id===scope.tenantId&&t.workspace_id===scope.workspaceId);
  const partialSources=[...new Set(sources.partial.map(p=>p.source))];
  if(agents.length>=300)partialSources.push('agent_pagination_limit');
  if(tasks.filter(t=>['created','queued','running','waiting_approval'].includes(t.status)).length>=500)partialSources.push('task_pagination_limit');
  // Paginated open-task loader is bounded; treat the result as potentially incomplete
  // at its 500-record ceiling even if no API fetch threw an error.
  const openTasks=tasks.filter(t=>['created','queued','running','waiting_approval'].includes(t.status));
  const complete=!partialSources.includes('tasks')&&openTasks.length<500&&agents.length<300;
  const inputAgents:OfficeAgent[]=agents.map(a=>({id:a.id,name:a.name,status:a.status==='active'?'active':'paused',department:a.department??null}));
  const inputTasks:OfficeTask[]=tasks.map(t=>({id:t.id,agent_id:t.agent_id??null,status:t.status}));
  const projected=projectOfficePresence({agents:inputAgents,tasks:inputTasks,observedAt,complete});
  const records=projected.map(p=>{
   const agent=agents.find(a=>a.id===p.agentId)!;
   const matching=tasks.filter(t=>p.taskIds.includes(t.id));
   return {...p,taskTitles:matching.map(t=>t.title),profileStatus:agent.status,suggestedDepartment:workstationDepartment(p.department)};
  });
  return {scope,observedAt,records,partialSources,complete};
}
export async function loadWorkspaceWorkstations(previous:OrgSources|null=null):Promise<{snapshot:WorkstationSnapshot;sources:OrgSources}> {
  const scope=api.currentScope();
  const now=Date.now();
  const sources=await loadOrgSources(api.apiRequest,now,previous);
  // If a user changes workspaces during the request, discard the old response.
  const after=api.currentScope();
  if(scope.workspaceId!==after.workspaceId||scope.tenantId!==after.tenantId)throw new Error('Workspace changed while loading. Refresh the office.');
  return {sources,snapshot:projectWorkstationSnapshot(sources,scope,new Date(now).toISOString())};
}
