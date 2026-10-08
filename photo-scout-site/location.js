'use strict';
// Keep browser permission errors and unresolved permission prompts visible to the caller.
window.PhotoScoutLocation={request({geolocation,onState,onPosition,timeoutMs=20000}){
 let finished=false;
 const finish=(state,position)=>{if(finished)return;finished=true;clearTimeout(timer);onState(state);if(position)onPosition(position);};
 const timer=setTimeout(()=>finish({status:'error',message:'No location received. Check your browser and device location permissions, then try again.'}),timeoutMs);
 onState({status:'pending',message:'Finding your location… Allow location access if your browser asks.'});
 try{geolocation.getCurrentPosition(position=>{
  const {latitude,longitude}=position.coords;
  if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||Math.abs(latitude)>85||Math.abs(longitude)>180){finish({status:'error',message:'Your location is outside the supported map area. Please choose a point manually.'});return;}
  finish({status:'success',message:'Location selected. Review your pin on the map, then choose a photo mood.'},position);
 },error=>finish({status:'error',message:error.code===1?'Location access was denied. Enable it for this site in browser settings and check device Location Services, or choose a point on the map.':error.code===3?'Location request timed out. Try again outdoors, or select a point on the map.':'Your device could not determine its location. Check Location Services or choose a point on the map.'}),{enableHighAccuracy:false,timeout:15000,maximumAge:60000});}
 catch{finish({status:'error',message:'Location is unavailable in this browser. Choose a point on the map instead.'});}
 return ()=>{finished=true;clearTimeout(timer);};
}};
