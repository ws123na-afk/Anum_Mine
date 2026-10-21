import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {nextTourRoom,parseTourDirection} from '../src/lib/officeTourNavigation.ts';
const rooms=[{id:'reception',name:'Reception',floor:0},{id:'command',name:'ANUM Command',floor:0},{id:'finance',name:'Finance Controller',floor:1},{id:'executive',name:'Executive Office',floor:2}];
describe('PA office navigation',()=>{
  it('understands common instructions without touching agent tools',()=>{
    assert.deepEqual(parseTourDirection('Please take me to the Executive Office',rooms),{kind:'room',id:'executive'});
    assert.deepEqual(parseTourDirection('Go to reception',rooms),{kind:'room',id:'reception'});
    assert.deepEqual(parseTourDirection('Show the second floor',rooms),{kind:'floor',floor:2});
    assert.deepEqual(parseTourDirection('level one please',rooms),{kind:'floor',floor:1});
    assert.deepEqual(parseTourDirection('ground floor',rooms),{kind:'floor',floor:0});
    assert.deepEqual(parseTourDirection('continue the tour',rooms),{kind:'next'});
    assert.deepEqual(parseTourDirection('pause tour now',rooms),{kind:'pause'});
    assert.deepEqual(parseTourDirection('start over',rooms),{kind:'restart'});
  });
  it('does not mistake unsupported operational commands for navigation',()=>{
    assert.deepEqual(parseTourDirection('Send customer invoice and transfer money',rooms),{kind:'help'});
    assert.deepEqual(parseTourDirection('',rooms),{kind:'help'});
  });
  it('follows a preferred itinerary and includes other rooms',()=>{
    assert.equal(nextTourRoom(rooms,'reception',['reception','finance'])?.id,'finance');
    assert.equal(nextTourRoom(rooms,'finance',['reception','finance'])?.id,'command');
    assert.equal(nextTourRoom(rooms,'executive',['reception','finance'])?.id,'reception');
    assert.equal(nextTourRoom([], 'x'),null);
  });
});
