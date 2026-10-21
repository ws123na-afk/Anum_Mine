import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {OFFICE_LOCATIONS,officeLightAt,solarAltitude} from '../src/lib/officeSolar.ts';
describe('Virtual office solar lighting',()=>{
 const riyadh=OFFICE_LOCATIONS['Asia/Riyadh'];
 it('shows night before sunrise and day at local noon',()=>{
   assert.equal(officeLightAt(new Date('2026-06-21T00:00:00Z'),riyadh),'night');
   assert.equal(officeLightAt(new Date('2026-06-21T09:00:00Z'),riyadh),'day');
   assert.equal(officeLightAt(new Date('2026-12-21T09:00:00Z'),riyadh),'day');
   assert.equal(officeLightAt(new Date('2026-12-21T21:00:00Z'),riyadh),'night');
 });
 it('distinguishes locations at the same instant',()=>{
   const moment=new Date('2026-06-21T02:00:00Z');
   assert.ok(solarAltitude(moment,24.7136,46.6753)<solarAltitude(moment,24.8607,67.0011));
 });
 it('refuses invalid parameters instead of displaying fabricated state',()=>{
   assert.throws(()=>solarAltitude(new Date('invalid'),24,46),RangeError);
   assert.throws(()=>solarAltitude(new Date(),91,46),RangeError);
 });
});
