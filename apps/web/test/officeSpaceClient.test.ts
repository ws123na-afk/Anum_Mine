import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {createOfficeSpaceClient,listOfficeRecords,OfficeApiError,officeResourcePath,type OfficeRequester} from '../src/lib/officeSpaceClient.ts';
const scope=()=>({tenantId:'t',workspaceId:'w'});
const mk=(id:string)=>({id,revision:1,tenant_id:'t',workspace_id:'w',created_at:'2026-10-21T00:00:00Z',updated_at:'2026-10-21T00:00:00Z'});
describe('office registry API adapter',()=>{
 it('paginates all records and transmits only scoped read requests',async()=>{
  const paths:string[]=[];
  const request:OfficeRequester=async<T>(path:string)=>{
   paths.push(path);
   const payload=path.includes('cursor=')?{items:[mk('c')],next_cursor:null}:{items:[mk('a'),mk('b')],next_cursor:'b'};
   return payload as T;
  };
  const rows=await listOfficeRecords(request,'rooms',scope);
  assert.deepEqual(rows.map(x=>x.id),['a','b','c']);
  assert.equal(paths.length,2);
  assert.ok(paths.every(x=>x.startsWith('/api/v1/office-spaces/rooms?')));
 });
 it('rejects cross-tenant, duplicate and looping pages',async()=>{
  await assert.rejects(()=>listOfficeRecords(async()=>({items:[{...mk('a'),tenant_id:'other'}],next_cursor:null}),'rooms',scope),OfficeApiError);
  await assert.rejects(()=>listOfficeRecords(async()=>({items:[mk('a'),mk('a')],next_cursor:null}),'rooms',scope),OfficeApiError);
  await assert.rejects(()=>listOfficeRecords(async()=>({items:[mk('a')],next_cursor:'a'}),'rooms',scope),OfficeApiError);
 });
 it('cancels a response from a different selected workspace',async()=>{
  let workspace='w';
  const dynamic=()=>({tenantId:'t',workspaceId:workspace});
  const request:OfficeRequester=async<T>()=>{workspace='changed';return {items:[],next_cursor:null} as T};
  await assert.rejects(()=>listOfficeRecords(request,'desks',dynamic),OfficeApiError);
 });
 it('encodes writes and revision preconditions safely',async()=>{
  const calls:{path:string;init:RequestInit}[]=[];
  const request:OfficeRequester=async<T>(path,init)=>{calls.push({path,init});return mk('a') as T};
  const remove=async(path:string,init:RequestInit)=>{calls.push({path,init})};
  const client=createOfficeSpaceClient(request,remove,scope);
  await client.post('buildings',{id:'a',name:'HQ',timezone:'Asia/Riyadh'});
  await client.rename('r','Finance',2);
  await client.assign('desk',null,3);
  await client.deleteRoom('r',4);
  assert.deepEqual(calls.map(x=>x.init.method),['POST','PATCH','PATCH','DELETE']);
  assert.match(String(calls[1].init.body),/"expected_revision":2/);
  assert.ok(calls[3].path.includes('expected_revision=4'));
  assert.throws(()=>officeResourcePath('rooms','../../x'),OfficeApiError);
 });
});
