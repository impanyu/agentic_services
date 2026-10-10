// One reusable Google panorama. Static thumbnails remain the fallback and list view.
(function(global){
 const MIN_ZOOM=Math.log2(180/120),MAX_ZOOM=Math.log2(180/30);
 let loading=null,panorama=null,root=null,active=null,requestedSurface=null,generation=0,applying=false;
 const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
 const zoomForFov=fov=>Math.log2(180/clamp(fov,30,120));
 const fovForZoom=zoom=>Math.round(clamp(180/2**zoom,30,120));
 function load(){
  if(global.google?.maps?.StreetViewPanorama)return Promise.resolve(global.google.maps);
  if(loading)return loading;
  loading=new Promise((resolve,reject)=>{const key=global.PhotoScoutMapsConfig?.key;if(!key){reject(Error('Interactive Street View unavailable'));return;}
   const script=document.createElement('script'),timeout=setTimeout(()=>reject(Error('Interactive Street View timed out')),15000);
   global.photoScoutMapsReady=()=>{clearTimeout(timeout);resolve(global.google.maps);};
   global.gm_authFailure=()=>{clearTimeout(timeout);reject(Error('Interactive Street View authorization failed'));detach();};
   script.src='https://maps.googleapis.com/maps/api/js?'+new URLSearchParams({key,v:'weekly',loading:'async',callback:'photoScoutMapsReady',language:'en'});script.async=true;script.onerror=()=>{clearTimeout(timeout);reject(Error('Interactive Street View unavailable'));};document.head.append(script);
  });return loading;
 }
 function detach(surface){if(surface&&active?.surface!==surface&&requestedSurface!==surface)return;generation++;requestedSurface=null;if(active){active.surface.classList.remove('native-street-view-active');active.onDetach?.();active=null;}if(panorama)panorama.setVisible(false);if(root)root.remove();}
 function nativeView(){const pov=panorama.getPov();return {heading:Math.round(((pov.heading%360)+360)%360),pitch:Math.round(clamp(pov.pitch,-90,90)),fov:fovForZoom(panorama.getZoom())};}
 function sync(){if(!active||applying||active.isDisabled?.())return;const current=global.PhotoScoutStreetView.view(active.getSpot()),v=nativeView();if(Math.abs(panorama.getZoom()-clamp(panorama.getZoom(),MIN_ZOOM,MAX_ZOOM))>.001){applying=true;panorama.setZoom(clamp(panorama.getZoom(),MIN_ZOOM,MAX_ZOOM));applying=false;}if(current.heading!==v.heading||current.pitch!==v.pitch||current.fov!==v.fov)active.onChange(v);caption();}
 function caption(){if(!active)return;const v=global.PhotoScoutStreetView.view(active.getSpot());active.caption.textContent=`${v.heading}° · tilt ${v.pitch}° · FOV ${v.fov}°`;}
 function refresh(surface){if(!panorama||active?.surface!==surface)return false;const spot=active.getSpot(),view=global.PhotoScoutStreetView.view(spot),pano=new URL(spot.sourceUrl).searchParams.get('pano');applying=true;if(panorama.getPano()!==pano)panorama.setPano(pano);panorama.setPov({heading:view.heading,pitch:view.pitch});panorama.setZoom(zoomForFov(view.fov));applying=false;caption();return true;}
 async function attach(surface,options){
  if(options.getSpot()?.provider!=='google-street-view')return false;
  const ticket=++generation;requestedSurface=surface;surface.classList.add('native-street-view-loading');
  try{const maps=await load();if(ticket!==generation||!surface.isConnected)return false;
   if(active&&active.surface!==surface){active.surface.classList.remove('native-street-view-active');active.onDetach?.();}
   if(!root){root=document.createElement('div');root.className='native-street-view';root.addEventListener('keydown',event=>{const delta=global.PhotoScoutStreetView.keyDelta(event.key);if(!active||!delta||active.isDisabled?.())return;event.preventDefault();event.stopImmediatePropagation();active.onChange(global.PhotoScoutStreetView.adjust(global.PhotoScoutStreetView.view(active.getSpot()),delta));refresh(active.surface);},{capture:true});}
   surface.append(root);let label=surface.querySelector('.native-view-caption');if(!label){label=document.createElement('span');label.className='native-view-caption';surface.append(label);}active={...options,surface,caption:label};surface.classList.add('native-street-view-active');
   if(!panorama){const v=global.PhotoScoutStreetView.view(options.getSpot());panorama=new maps.StreetViewPanorama(root,{pano:new URL(options.getSpot().sourceUrl).searchParams.get('pano'),pov:{heading:v.heading,pitch:v.pitch},zoom:zoomForFov(v.fov),visible:true,addressControl:false,linksControl:false,clickToGo:false,panControl:false,zoomControl:true,fullscreenControl:false,motionTracking:false,motionTrackingControl:false,showRoadLabels:false,keyboardShortcuts:false,scrollwheel:true,controlSize:24});panorama.addListener('pov_changed',sync);panorama.addListener('zoom_changed',sync);panorama.addListener('status_changed',()=>{if(active&&panorama.getStatus()!==maps.StreetViewStatus.OK){detach();return;}if(active)refresh(active.surface);});}
   else{panorama.setVisible(true);refresh(surface);maps.event.trigger(panorama,'resize');}
   caption();return true;
  }catch{if(ticket===generation){surface.dataset.nativeUnavailable='true';if(active?.surface===surface)detach(surface);}return false;}
  finally{surface.classList.remove('native-street-view-loading');}
 }
 global.PhotoScoutNativeStreetView={load,attach,detach,refresh,isActive:surface=>active?.surface===surface,zoomForFov,fovForZoom};
})(window);
