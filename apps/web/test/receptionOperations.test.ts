import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {EMPTY_RECEPTION,nextReceptionAction,receptionCounts,receptionQueue,ReceptionError,updateReception,type ReceptionState} from '../src/lib/receptionOperations.ts';
const at='2026-10-21T08:00:00.000Z';
function arrive(id='v1',priority:'normal'|'urgent'='normal'){return {type:'arrive' as const,id,name:'Visitor',company:'Example Co',kind:'customer' as const,purpose:'Fleet appointment',priority,at};}
function change(s:ReceptionState,action:'check_in'|'assign'|'escort'|'complete'|'cancel',at='2026-10-21T08:01:00.000Z'){return updateReception(s,{type:'transition',id:'v1',action,at,host:'Reception team',destination:'ANUM Command'})}
describe('reception lifecycle',()=>{
 it('handles full arrival to successful escorted handover',()=>{
  let s=updateReception(EMPTY_RECEPTION,arrive());
  assert.equal(s.visits[0].status,'arrived');
  s=change(s,'check_in');assert.equal(s.visits[0].status,'checked_in');
  s=change(s,'assign');assert.equal(s.visits[0].status,'assigned');
  assert.equal(s.visits[0].destination,'ANUM Command');
  s=change(s,'escort');assert.equal(s.visits[0].status,'escorting');
  s=change(s,'complete');assert.equal(s.visits[0].status,'completed');
  assert.equal(s.visits[0].timeline.length,5);
  assert.deepEqual(receptionCounts(s.visits),{waiting:0,ready:0,escorting:0,completed:1,urgent:0});
  assert.equal(nextReceptionAction('completed'),null);
 });
 it('rejects invalid transitions and missing host information',()=>{
  let s=updateReception(EMPTY_RECEPTION,arrive());
  assert.throws(()=>change(s,'escort'),(e:unknown)=>e instanceof ReceptionError&&e.code==='invalid_transition');
  s=change(s,'check_in');
  assert.throws(()=>updateReception(s,{type:'transition',id:'v1',action:'assign',at,host:'',destination:'Reception'}),(e:unknown)=>e instanceof ReceptionError&&e.code==='invalid_input');
 });
 it('supports cancellation but not reopening a cancelled visitor',()=>{
  const s=change(updateReception(EMPTY_RECEPTION,arrive()),'cancel');
  assert.equal(s.visits[0].status,'cancelled');
  assert.throws(()=>change(s,'check_in'),ReceptionError);
 });
 it('prioritizes urgent arrivals without mutating state',()=>{
  const one=updateReception(EMPTY_RECEPTION,arrive());
  const two=updateReception(one,arrive('v2','urgent'));
  assert.deepEqual(receptionQueue(two.visits).map(x=>x.id),['v2','v1']);
  assert.equal(two.visits[0].id,'v1');
  assert.equal(receptionCounts(two.visits).urgent,1);
 });
 it('enforces idempotency boundary, monotonic timestamps and reset',()=>{
  const s=updateReception(EMPTY_RECEPTION,arrive());
  assert.throws(()=>updateReception(s,arrive()),ReceptionError);
  assert.throws(()=>change(s,'check_in','2026-10-20T08:00:00Z'),ReceptionError);
  assert.deepEqual(updateReception(s,{type:'reset'}),EMPTY_RECEPTION);
 });
 it('rejects empty fields and invalid visitor categories',()=>{
  assert.throws(()=>updateReception(EMPTY_RECEPTION,{...arrive(),name:'  '}),ReceptionError);
  assert.throws(()=>updateReception(EMPTY_RECEPTION,{...arrive(),kind:'invalid' as 'customer'}),ReceptionError);
  assert.throws(()=>updateReception(EMPTY_RECEPTION,{...arrive(),at:'invalid'}),ReceptionError);
 });
});
