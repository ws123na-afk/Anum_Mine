import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {projectWorkstationSnapshot,workstationDepartment} from '../src/lib/officeWorkstations.ts';
import type {OrgSources} from '../src/lib/orgChart.ts';
const scope={tenantId:'tenant-A',workspaceId:'workspace-1'};
const agent=(id:string,department:string|null='Tyre Engineering',tenant_id='tenant-A',workspace_id='workspace-1')=>({id,name:id,department,tenant_id,workspace_id,status:'active'}) as OrgSources['agents'][number];
const task=(id:string,agent_id:string,tenant_id='tenant-A',workspace_id='workspace-1',status='running')=>({id,agent_id,tenant_id,workspace_id,title:'Task '+id,status}) as OrgSources['tasks'][number];
const snapshot=(agents:OrgSources['agents'],tasks:OrgSources['tasks'],partial:OrgSources['partial']=[])=>projectWorkstationSnapshot({agents,tasks,partial},scope,'2026-10-21T08:00:00Z');
describe('authenticated office workstation projection',()=>{
 it('shows only caller workspace agents and tasks, never other tenant content',()=>{
  const s=snapshot([agent('authorised'),agent('secret','Finance','tenant-B','workspace-9')],[task('task1','authorised'),task('private','secret','tenant-B','workspace-9')]);
  assert.equal(s.records.length,1);assert.equal(s.records[0].name,'authorised');
  assert.deepEqual(s.records[0].taskTitles,['Task task1']);
  assert.equal(s.records[0].status,'working');
  assert.deepEqual(s.scope,scope);
 });
 it('marks idle only when complete and unknown when a task source failed',()=>{
  const idle=snapshot([agent('a')],[]);
  assert.equal(idle.records[0].status,'idle');
  const partial=snapshot([agent('a')],[],[{source:'tasks',error:new Error('403')}]);
  assert.equal(partial.records[0].status,'unknown');
  assert.equal(partial.complete,false);
  assert.equal(partial.records[0].source,'incomplete');
 });
 it('never mistakes approval waiting or queued for active execution',()=>{
  const s=snapshot([agent('a'),agent('b','Finance')],[task('a1','a','tenant-A','workspace-1','queued'),task('b1','b','tenant-A','workspace-1','waiting_approval')]);
  assert.deepEqual(s.records.map(a=>a.status),['queued','waiting_approval']);
 });
 it('maps real departments and retains unrecognized rooms without inventing physical desks',()=>{
  assert.equal(workstationDepartment('fleet maintenance'),'Tyre Engineering');
  assert.equal(workstationDepartment('customer support'),'Customer Experience');
  assert.equal(workstationDepartment('Accounts'),'Finance');
  assert.equal(workstationDepartment(''), 'Unassigned');
 });
 it('does not assume complete after bounded task pagination reaches its limit',()=>{
  const tasks=Array.from({length:500},(_,i)=>task('t'+i,'a','tenant-A','workspace-1','queued'));
  const s=snapshot([agent('a'),agent('b')],tasks);
  assert.equal(s.complete,false);
  assert.equal(s.records[0].status,'queued');
  assert.equal(s.records[1].status,'unknown');
 });
});
