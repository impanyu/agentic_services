'use strict';
window.PhotoScoutLocation={request({geolocation,onState,onPosition,timeoutMs=35000}){
 let finished=false,retried=false;
 const finish=(state,position)=>{if(finished)return;finished=true;clearTimeout(timer);onState(state);if(position)onPosition(position);};
 const timer=setTimeout(()=>finish({status:'error',message:'No device location received. The map has not been located. On iPhone: Settings → Privacy & Security → Location Services → Safari Websites → While Using. Also allow location for this website, then retry.'}),timeoutMs);
 const success=position=>{const {latitude,longitude}=position.coords;if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||Math.abs(latitude)>85||Math.abs(longitude)>180){finish({status:'error',message:'Unsupported device coordinates. Choose a place on the map.'});return;}finish({status:'success',message:'Device location found.'},position);};
 const request=high=>{try{geolocation.getCurrentPosition(success,error=>{
  if(finished)return;
  if(error.code!==1&&!retried){retried=true;onState({status:'pending',message:'Trying a precise device location…'});request(true);return;}
  finish({status:'error',message:error.code===1?'Location access denied. On iPhone, allow Safari Websites in Settings → Privacy & Security → Location Services, and allow location for aisoup.net in Safari website settings. The displayed map is not your detected location.':error.code===3?'Device location timed out. No location was selected. Check device Location Services or type a place below.':'Device location unavailable. No location was selected. Check Location Services or type a place below.'});
 },{enableHighAccuracy:high,timeout:high?20000:10000,maximumAge:0});}catch{finish({status:'error',message:'Location is unavailable in this browser. Type a place below instead.'});}};
 onState({status:'pending',message:'Finding your device location… Allow location access if asked. The initial map is only a starting view.'});request(false);
 return ()=>{finished=true;clearTimeout(timer);};
}};
