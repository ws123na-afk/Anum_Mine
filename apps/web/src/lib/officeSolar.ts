/** Lightweight solar-lighting model for a virtual office (no weather or geolocation tracking).
 * Uses astronomical approximation; UTC timestamps stay canonical on the server.
 * Coordinates are a *configured office location*, never inferred from IP.
 */
export type OfficeSiteLocation={city:string;zone:string;latitude:number;longitude:number};
export const OFFICE_LOCATIONS:Readonly<Record<string,OfficeSiteLocation>>={
  'Asia/Riyadh':{city:'Riyadh',zone:'Asia/Riyadh',latitude:24.7136,longitude:46.6753},
  'Asia/Dubai':{city:'Dubai',zone:'Asia/Dubai',latitude:25.2048,longitude:55.2708},
  'Asia/Karachi':{city:'Karachi',zone:'Asia/Karachi',latitude:24.8607,longitude:67.0011},
};
const rad=(n:number)=>n*Math.PI/180;
const dayOfYear=(d:Date)=>{
 const year=d.getUTCFullYear(),yearStart=Date.UTC(year,0,1);
 return Math.floor((Date.UTC(year,d.getUTCMonth(),d.getUTCDate())-yearStart)/86400000)+1;
};
/** Solar altitude in degrees; approx 1-degree accuracy, suitable only for aesthetic lighting. */
export function solarAltitude(at:Date,latitude:number,longitude:number):number {
 if(!Number.isFinite(at.getTime())||Math.abs(latitude)>90||Math.abs(longitude)>180)throw new RangeError('Invalid solar parameters');
 const n=dayOfYear(at),hour=at.getUTCHours()+at.getUTCMinutes()/60+at.getUTCSeconds()/3600;
 const gamma=2*Math.PI/365*(n-1+(hour-12)/24);
 const eqTime=229.18*(.000075+.001868*Math.cos(gamma)-.032077*Math.sin(gamma)-.014615*Math.cos(2*gamma)-.040849*Math.sin(2*gamma));
 const decl=.006918-.399912*Math.cos(gamma)+.070257*Math.sin(gamma)-.006758*Math.cos(2*gamma)+.000907*Math.sin(2*gamma)-.002697*Math.cos(3*gamma)+.00148*Math.sin(3*gamma);
 const trueSolarMinutes=hour*60+eqTime+longitude*4;
 const ha=rad((trueSolarMinutes/4-180)%360);
 const lat=rad(latitude);
 const sinAltitude=Math.sin(lat)*Math.sin(decl)+Math.cos(lat)*Math.cos(decl)*Math.cos(ha);
 return Math.asin(Math.min(1,Math.max(-1,sinAltitude)))*180/Math.PI;
}
export function officeLightAt(at:Date,location:OfficeSiteLocation):'day'|'dusk'|'night' {
 const altitude=solarAltitude(at,location.latitude,location.longitude);
 return altitude>=0?'day':altitude>=-6?'dusk':'night';
}
