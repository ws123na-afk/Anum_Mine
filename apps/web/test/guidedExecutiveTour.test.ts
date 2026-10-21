import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {changeGuidedTour,createGuidedTour,describeTourStop,GuidedTourError,tourProgress,type TourStop} from '../src/lib/guidedExecutiveTour.ts';
const stops:TourStop[]=[{id:'reception',name:'Reception',floor:0,x:20,y:80,description:'Welcomes visitors',staff:['Reception coordinator']},{id:'sales',name:'Sales Office',floor:1,x:70,y:20,description:'Handles commercial enquiries',staff:['Sales consultant']},{id:'executive',name:'CEO Office',floor:2,x:50,y:40,description:'Executive decisions',staff:[]}];
const stamp=(s:number)=>new Date(Date.UTC(2026,9,21,9,0,s)).toISOString();
describe('executive guided tour full lifecycle',()=>{
 it('welcomes CEO, tours floors and completes with an ordered timeline',()=>{
  let t=createGuidedTour('ANUM HQ',stops,stamp(0));
  assert.equal(t.phase,'ready');assert.equal(tourProgress(t),0);
  t=changeGuidedTour(t,{type:'begin',at:stamp(1)});
  assert.equal(t.currentRoomId,'reception');assert.equal(t.phase,'touring');
  t=changeGuidedTour(t,{type:'next',at:stamp(2)});
  assert.equal(t.currentRoomId,'sales');assert.equal(tourProgress(t),67);
  t=changeGuidedTour(t,{type:'pause',at:stamp(3)});
  assert.equal(t.phase,'paused');
  assert.throws(()=>changeGuidedTour(t,{type:'next',at:stamp(4)}),GuidedTourError);
  t=changeGuidedTour(t,{type:'resume',at:stamp(5)});
  t=changeGuidedTour(t,{type:'next',at:stamp(6)});
  assert.equal(t.currentRoomId,'executive');
  t=changeGuidedTour(t,{type:'next',at:stamp(7)});
  assert.equal(t.phase,'finished');assert.equal(tourProgress(t),100);
  assert.deepEqual(t.visited,['reception','sales','executive']);assert.equal(t.events.length,8);
 });
 it('allows interruption and direct room redirection',()=>{
  let t=createGuidedTour('ANUM HQ',stops,stamp(0));
  t=changeGuidedTour(t,{type:'redirect',roomId:'executive',at:stamp(1)});
  assert.equal(t.currentRoomId,'executive');assert.equal(t.phase,'touring');
  assert.equal(t.events[1].kind,'redirect');
  assert.throws(()=>changeGuidedTour(t,{type:'redirect',roomId:'private-data',at:stamp(2)}),GuidedTourError);
 });
 it('rejects duplicate rooms, invalid timestamps, invalid transitions and old events',()=>{
  assert.throws(()=>createGuidedTour('x',[stops[0],stops[0]],stamp(0)),GuidedTourError);
  assert.throws(()=>createGuidedTour('x',stops,'invalid'),GuidedTourError);
  const t=createGuidedTour('ANUM HQ',stops,stamp(2));
  assert.throws(()=>changeGuidedTour(t,{type:'pause',at:stamp(3)}),GuidedTourError);
  assert.throws(()=>changeGuidedTour(t,{type:'begin',at:stamp(1)}),GuidedTourError);
 });
 it('reset does not carry visit history into a new business',()=>{
  let t=createGuidedTour('ANUM HQ',stops,stamp(0));
  t=changeGuidedTour(t,{type:'begin',at:stamp(1)});
  t=changeGuidedTour(t,{type:'reset',site:'Tyre Pulse',stops:stops.slice(0,1),at:stamp(2)});
  assert.equal(t.site,'Tyre Pulse');assert.deepEqual(t.visited,[]);assert.equal(t.events.length,1);
 });
 it('does not claim example staff are actually working',()=>{
  assert.match(describeTourStop(stops[0]),/example roles/i);
  assert.match(describeTourStop(stops[0]),/not live employee activity/i);
 });
});
