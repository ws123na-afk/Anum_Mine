import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {projectOfficePresence,staleOfficePresence} from '../src/lib/officePresence.ts';
const agents=[{id:'a',name:'Researcher',status:'active' as const,department:'Research'},{id:'b',name:'Planner',status:'active' as const,department:null},{id:'c',name:'Paused',status:'paused' as const,department:'Ops'}];
const at='2026-10-21T08:00:00Z';
describe('office agent statuses reflect available evidence',()=>{
 it('distinguishes active work from queued and approvals',()=>{
  const s=projectOfficePresence({agents,tasks:[{id:'t1',agent_id:'a',status:'running'},{id:'t2',agent_id:'b',status:'queued'},{id:'t3',agent_id:'c',status:'running'}],complete:true,observedAt:at});
  assert.deepEqual(s.map(x=>x.status),['working','queued','paused']);
  assert.equal(s[0].taskIds[0],'t1');
 });
 it('does not claim idle if task pages are missing',()=>{
  const s=projectOfficePresence({agents:agents.slice(0,2),tasks:[],complete:false,observedAt:at});
  assert.deepEqual(s.map(x=>x.status),['unknown','unknown']);
  assert.equal(s[0].source,'incomplete');
 });
 it('correctly identifies approval and blocked state',()=>{
  const s=projectOfficePresence({agents:agents.slice(0,2),tasks:[{id:'t1',agent_id:'a',status:'waiting_approval'},{id:'t2',agent_id:'b',status:'blocked'}],complete:true,observedAt:at});
  assert.deepEqual(s.map(x=>x.status),['waiting_approval','blocked']);
 });
 it('does not count unrelated or completed tasks as working',()=>{
  const s=projectOfficePresence({agents:agents.slice(0,1),tasks:[{id:'t1',agent_id:'other',status:'running'},{id:'t2',agent_id:'a',status:'completed'}],complete:true,observedAt:at});
  assert.equal(s[0].status,'idle');
  assert.deepEqual(s[0].taskIds,[]);
 });
 it('marks stale snapshots and refuses invalid timestamps',()=>{
  const p=projectOfficePresence({agents:agents.slice(0,1),tasks:[],complete:true,observedAt:at})[0];
  assert.equal(staleOfficePresence(p,'2026-10-21T08:00:30Z'),false);
  assert.equal(staleOfficePresence(p,'2026-10-21T08:05:00Z'),true);
  assert.throws(()=>projectOfficePresence({agents,tasks:[],complete:true,observedAt:'bad'}),RangeError);
 });
});
