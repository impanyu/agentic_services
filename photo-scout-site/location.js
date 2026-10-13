'use strict';
window.PhotoScoutLocation={
 // A remembered preference is not permission. Only a fresh browser permission
 // check can authorize a silent location request; never reuse old coordinates.
 remember(storage){try{storage.setItem('photo-scout-location-enabled','1');}catch{}},
 async canAutoLocate(permissions){
  try{return (await permissions.query({name:'geolocation'})).state==='granted';}catch{return false;}
 },
 request({geolocation,onState,onPosition,timeoutMs=35000,native=Boolean(window.PhotoScoutNative)}){
 let finished=false,retried=false;
 const finish=(state,position)=>{if(finished)return;finished=true;clearTimeout(timer);onState(state);if(position)onPosition(position);};
 const settings=native?'Enable Photo Scout in iPhone Settings → Privacy & Security → Location Services.':'Allow location for this website in your browser settings.';
 const timer=setTimeout(()=>finish({status:'error',message:'Device location timed out. '+settings+' You can also choose a point on the map.'}),timeoutMs);
 const success=position=>{const {latitude,longitude}=position.coords;if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||Math.abs(latitude)>85||Math.abs(longitude)>180){finish({status:'error',message:'Unsupported device coordinates. Choose a place on the map.'});return;}finish({status:'success',message:'Device location found.'},position);};
 const request=high=>{try{geolocation.getCurrentPosition(success,error=>{
  if(finished)return;
  if(error.code!==1&&!retried){retried=true;onState({status:'pending',message:'Trying a precise device location…'});request(true);return;}
  finish({status:'error',settingsRequired:error.code===1,message:error.code===1?'Location access denied. '+settings+' The displayed map is not your detected location.':error.code===3?'Device location timed out. No location was selected. Check device Location Services or type a place below.':'Device location unavailable. No location was selected. Check Location Services or type a place below.'});
 },{enableHighAccuracy:high,timeout:high?20000:10000,maximumAge:0});}catch{finish({status:'error',message:'Location is unavailable in this browser. Type a place below instead.'});}};
 onState({status:'pending',message:'Finding your device location… Allow location access if asked. The initial map is only a starting view.'});request(false);
 return ()=>{finished=true;clearTimeout(timer);};
}};
