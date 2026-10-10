'use strict';
let lastRemovedPoi=null,selectedPoiView=null,poiViewOverrides=new Map(),poiPreviewTimer=null;
let removedHistoryItems=new Set(),lastRemovedHistory=null;
let sharedPublicationId=null;
let publicationItems=[],publicationCursor=null;let ownPublications=new Map();
const hydratedSearches=new Set();
let tasksReady=false,taskRecords=[],taskRefreshBusy=false,taskRecoveryGeneration=0,taskRefreshTimer=null;
const api=location.hostname==='localhost'||location.hostname==='127.0.0.1'?'': 'https://api.aisoup.net';
const el=id=>document.getElementById(id), message=s=>{el('message').textContent=s;if(searchBusy||pipelineBusy||resolving)el('progress-detail').textContent=s};
const map=L.map('map',{zoomControl:false}).setView([41.8827,-87.6233],15);
L.control.zoom({position:'bottomright'}).addTo(map);
const controls=el('map-controls');controls.open=false;L.DomEvent.disableClickPropagation(controls);L.DomEvent.disableScrollPropagation(controls);L.DomEvent.disableClickPropagation(document.querySelector('.map-toolbar'));L.DomEvent.disableClickPropagation(el('prompt-form'));L.DomEvent.disableScrollPropagation(document.querySelector('.prompt-panel'));L.DomEvent.disableClickPropagation(document.querySelector('.scout-dock'));L.DomEvent.disableScrollPropagation(document.querySelector('.scout-dock'));L.DomEvent.disableClickPropagation(el('center-pin'));L.DomEvent.disableClickPropagation(document.querySelector('.map-account'));
const streetTiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'});
let rasterLayer=null,previewLayer=null,previewTimer=null;
let vectorLayer=null,activeMapStyle='streets',mapStyleGeneration=0,vectorReady=false,mapLoadTimer=null,vectorMoveHandler=null;
const mapDetails={names:true,roads:true,roadNames:false,buildings:false,greenery:false,boundaries:false};
let originalLayerVisibility=new Map();
function mapDetailGroup(layer){const source=layer['source-layer']||'';if(source==='transportation_name')return 'roadNames';if(layer.type==='symbol')return 'names';if(['transportation','aeroway'].includes(source))return 'roads';if(source==='building')return 'buildings';if(['park','landcover','landuse'].includes(source))return 'greenery';if(source==='boundary')return 'boundaries';return null;}
function applyMapDetails(){if(!vectorLayer||!vectorReady)return;const gl=vectorLayer.getMaplibreMap();for(const layer of gl.getStyle().layers){const group=mapDetailGroup(layer);if(!group)continue;const show=mapDetails[group]&&(group!=='roadNames'||mapDetails.roads);gl.setLayoutProperty(layer.id,'visibility',show?(originalLayerVisibility.get(layer.id)||'visible'):'none');}}
L.DomEvent.disableClickPropagation(el('map-details'));L.DomEvent.disableScrollPropagation(el('map-details'));
document.querySelectorAll('[data-map-detail]').forEach(input=>input.addEventListener('change',()=>{mapDetails[input.dataset.mapDetail]=input.checked;if(['aerial','topographic'].includes(activeMapStyle))switchMapStyle('minimal');else applyMapDetails();}));
let vectorRuntime=null;
function loadVectorRuntime(){
 if(window.maplibregl&&L.maplibreGL)return Promise.resolve();
 if(!vectorRuntime){
  const script=src=>new Promise((resolve,reject)=>{const tag=document.createElement('script');tag.src=src;tag.onload=resolve;tag.onerror=()=>reject(Error('Map renderer unavailable'));document.head.append(tag);});
  vectorRuntime=script('./vendor/maplibre-gl.js').then(()=>script('./vendor/leaflet-maplibre-gl.js')).catch(error=>{vectorRuntime=null;throw error;});
 }
 return vectorRuntime;
}
const mapStyles={streets:'liberty',minimal:'positron',night:'dark',bright:'bright'};
async function switchMapStyle(style){
 clearTimeout(mapLoadTimer);clearTimeout(previewTimer);if(vectorMoveHandler){map.off?.('movestart',vectorMoveHandler);vectorMoveHandler=null;}vectorReady=false;activeMapStyle=style;document.body.dataset.mapStyle=style;const generation=++mapStyleGeneration;
 document.querySelectorAll('[data-map-style]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mapStyle===style)));
 el('map-notice').textContent='Loading map…';
 if(vectorLayer){map.removeLayer(vectorLayer);vectorLayer=null;}
 if(rasterLayer){map.removeLayer(rasterLayer);rasterLayer=null;}
 if(map.hasLayer(streetTiles))map.removeLayer(streetTiles);
 if(previewLayer){map.removeLayer(previewLayer);previewLayer=null;}
 // Fetch a quiet, non-WebGL base immediately, independently of vector styles.
 // Its muted design avoids flashing a busy street map before filtering.
 const preview=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',{maxZoom:20,maxNativeZoom:16,zIndex:0,attribution:'Esri · HERE · Garmin · OpenStreetMap contributors'});
 previewLayer=preview;let previewLoaded=false,backupStarted=false;
 const backup=()=>{
  if(generation!==mapStyleGeneration||previewLoaded||backupStarted)return;
  backupStarted=true;clearTimeout(previewTimer);
  if(map.hasLayer(preview))map.removeLayer(preview);
  previewLayer=null;if(!map.hasLayer(streetTiles))streetTiles.addTo(map);
 };
 preview.on('tileload',()=>{previewLoaded=true;clearTimeout(previewTimer);});
 preview.on('tileerror',backup);preview.addTo(map);
 previewTimer=setTimeout(backup,4000);
 let abandoned=false;
 const fallback=note=>{
  if(generation!==mapStyleGeneration||abandoned)return;
  abandoned=true;
  if(!map.hasLayer(preview)&&!map.hasLayer(streetTiles)){previewLoaded=false;backupStarted=false;previewLayer=preview;preview.addTo(map);previewTimer=setTimeout(backup,4000);}
  clearTimeout(mapLoadTimer);vectorReady=false;
  if(vectorLayer){if(map.hasLayer(vectorLayer))map.removeLayer(vectorLayer);vectorLayer=null;}
  if(rasterLayer){if(map.hasLayer(rasterLayer))map.removeLayer(rasterLayer);rasterLayer=null;}
  el('map-notice').textContent=note+' Using the backup map. You can retry from Map layers.';
 };
 const ready=()=>{if(generation!==mapStyleGeneration)return;clearTimeout(mapLoadTimer);el('map-notice').textContent='';};
 // A stalled request may never emit an error. Bound the entire first render.
 mapLoadTimer=setTimeout(()=>fallback('The selected basemap took too long to load.'),12000);
 if(style==='aerial'||style==='topographic'){
  const name=style==='aerial'?'USGSImageryOnly':'USGSTopo';
  const layer=L.tileLayer('https://basemap.nationalmap.gov/arcgis/rest/services/'+name+'/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:style==='aerial'?'USDA · USGS The National Map — Orthoimagery':'USGS The National Map · USGS, USFS, NOAA and contributors'});rasterLayer=layer;
  let rasterLoaded=false;layer.on('tileload',()=>{rasterLoaded=true;});
  layer.once('load',()=>{if(!rasterLoaded||generation!==mapStyleGeneration||abandoned)return;ready();clearTimeout(previewTimer);if(previewLayer===preview){map.removeLayer(preview);previewLayer=null;}if(map.hasLayer(streetTiles))map.removeLayer(streetTiles);});
  layer.on('tileerror',()=>fallback('This U.S. basemap is unavailable here.'));
  layer.addTo(map);return;
 }
 try{await loadVectorRuntime();}catch{fallback('The selected map renderer is unavailable.');return;}
 if(generation!==mapStyleGeneration||abandoned)return;
 if(!window.maplibregl||!L.maplibreGL||(typeof window.maplibregl.supported==='function'&&!window.maplibregl.supported())){fallback('This browser cannot use the selected basemap.');return;}
 try{
  // Apply detail visibility before MapLibre paints its first frame.
  const response=await fetch('https://tiles.openfreemap.org/styles/'+mapStyles[style]);
  if(!response.ok)throw Error('Map style unavailable');
  const preparedStyle=await response.json();
  if(generation!==mapStyleGeneration||abandoned)return;
  originalLayerVisibility=new Map(preparedStyle.layers.map(l=>[l.id,l.layout?.visibility||'visible']));
  for(const l of preparedStyle.layers){const group=mapDetailGroup(l);if(group&&(!mapDetails[group]||(group==='roadNames'&&!mapDetails.roads)))l.layout={...l.layout,visibility:'none'};}
  const layer=L.maplibreGL({style:preparedStyle,attribution:'<a href="https://openfreemap.org/">OpenFreeMap</a> · <a href="https://openmaptiles.org/">OpenMapTiles</a> · © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',interactive:false});vectorLayer=layer;layer.addTo(map);
  const gl=layer.getMaplibreMap();
  gl.getContainer().style.zIndex='2';gl.getCanvas().style.opacity='0';
  gl.once('load',()=>{if(generation!==mapStyleGeneration||abandoned)return;vectorReady=true;applyMapDetails();});
  // 'load' alone does not establish a fully painted, current viewport.
  // Keep raster tiles underneath throughout the session. A first idle event
  // does not guarantee WebGL can paint the next mobile viewport.
  const painted=()=>{if(generation!==mapStyleGeneration||abandoned)return;if(gl.areTilesLoaded&&!gl.areTilesLoaded())return;gl.getCanvas().style.opacity='1';ready();};
  gl.on('idle',painted);
  vectorMoveHandler=()=>{if(generation!==mapStyleGeneration||abandoned)return;gl.getCanvas().style.opacity='0';clearTimeout(mapLoadTimer);mapLoadTimer=setTimeout(()=>fallback('Map graphics did not recover.'),6000);};
  map.on?.('movestart',vectorMoveHandler);
  gl.on('error',()=>fallback('The selected basemap is unavailable.'));
  gl.on('webglcontextlost',()=>fallback('Map graphics were interrupted.'));
 }catch{fallback('The selected basemap is unavailable.');}
}
// Only one map popover is open; keep it clear of expanded search settings.
const mapMenus=[...document.querySelectorAll('.map-overlay-controls details')];
for(const menu of mapMenus)menu.addEventListener('toggle',()=>{if(!menu.open)return;for(const other of mapMenus)if(other!==menu)other.open=false;controls.open=false;});
controls.addEventListener('toggle',()=>{if(controls.open)for(const menu of mapMenus)menu.open=false;});
function closeMenusOutside(target){
 for(const menu of mapMenus)if(menu.open&&!menu.contains(target))menu.open=false;
 // Keep the submit target still between pointerdown and pointerup.
 if(controls.open&&!controls.contains(target)&&!target.closest?.('#prompt-form'))controls.open=false;
 if(!target.closest?.('.leaflet-control-layers'))overlayControl.collapse?.();
}
// Capture before Leaflet stops propagation, including taps on the map.
document.addEventListener('pointerdown',event=>closeMenusOutside(event.target),true);
document.addEventListener('keydown',event=>{if(event.key==='Escape'){for(const menu of mapMenus)menu.open=false;controls.open=false;overlayControl.collapse?.();}});

if(window.ResizeObserver)new window.ResizeObserver(entries=>{document.body.style.setProperty('--scout-dock-height',entries[0].target.getBoundingClientRect().height+'px');}).observe(document.querySelector('.scout-dock'));
document.querySelectorAll('[data-map-style]').forEach(b=>b.addEventListener('click',()=>switchMapStyle(b.dataset.mapStyle)));
switchMapStyle('minimal');
let selected=L.marker([41.8827,-87.6233],{draggable:true,title:'Selected location: drag to move',icon:L.divIcon({className:'scout-pin',html:'<svg viewBox="0 0 48 56" aria-hidden="true" focusable="false"><path d="M24 50C20 45 8 31 8 20a16 16 0 1 1 32 0c0 11-12 25-16 30Z" fill="#e24b47" stroke="#fff" stroke-width="2.5" stroke-linejoin="round"/><circle cx="24" cy="20" r="6" fill="#fff"/></svg>',iconSize:[48,56],iconAnchor:[24,50]})}).addTo(map), resultPins=[];let humanFreePreview=false, serviceAvailable=false, poiCatalog=null, catalogGeneration=0, searchBusy=false, pollGeneration=0, resolving=false, pipelineBusy=false;
const candidatePoiLayer=L.layerGroup().addTo(map),photoLocationLayer=L.layerGroup().addTo(map),otherPhotoLocationLayer=L.layerGroup().addTo(map);
const scanLayer=L.layerGroup().addTo(map);let scanTimer=null,scanIndex=0,scanDot=null,scanRunning=false;
function stopPoiScan(){clearInterval(scanTimer);scanTimer=null;scanRunning=false;scanIndex=0;scanLayer.clearLayers();if(scanDot)scanDot.setStyle({radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9});scanDot=null;el('map').classList.remove('reviewing-views');}
function startPoiScan(){if(scanRunning||!candidatePoiLayer.getLayers().length)return;scanRunning=true;el('map').classList.add('reviewing-views');const tick=()=>{if(document.hidden)return;const dots=candidatePoiLayer.getLayers();if(!dots.length){stopPoiScan();return;}if(scanDot)scanDot.setStyle({radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9});scanDot=dots[scanIndex++%dots.length];scanDot.setStyle({radius:9,color:'#fff',weight:3,fillColor:'#9762d1',fillOpacity:1});scanLayer.clearLayers();const name=scanDot.getTooltip()?.getContent()?.textContent||'Nearby place';L.circleMarker(scanDot.getLatLng(),{radius:21,color:'#a26ce1',weight:3,fillColor:'#b889ed',fillOpacity:.15,interactive:false,className:'photo-scan-ring'}).addTo(scanLayer).bindTooltip(node('span','Checking '+name+'…'),{permanent:true,direction:'top',offset:[0,-22],className:'photo-scan-label'}).openTooltip();};tick();scanTimer=setInterval(tick,1400);}
const searchArea=L.circle([41.8827,-87.6233],{radius:Number(el('radius').value),color:'#375947',weight:1.5,dashArray:'5 7',fillColor:'#acd69a',fillOpacity:.12,interactive:false}).addTo(map);
const overlayControl=L.control.layers(null,{'Search radius':searchArea,'Current search results':photoLocationLayer,'Other search history':otherPhotoLocationLayer},{collapsed:true}).addTo(map);
document.querySelector('.map-overlay-controls').append(overlayControl.getContainer());
function syncMapSelection(){const lat=Number(el('lat').value),lon=Number(el('lon').value);if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180)return;searchArea.setLatLng([lat,lon]).setRadius(Number(el('radius').value));el('map-selection').textContent=`${lat.toFixed(5)}, ${lon.toFixed(5)}`;el('coordinate-readout').textContent=el('map-selection').textContent;el('map-radius').textContent=`Searching within ${Number(el('radius').value)>=1000?Number(el('radius').value)/1000+' km':el('radius').value+' m'}`;}
selected.on('dragend',()=>{const p=selected.getLatLng();pick(p.lat,p.lng);});

function coordinates(){const styles=[...el('style-options').querySelectorAll('input:checked')].map(i=>i.value).filter(s=>s!=='any');return {lat:Number(el('lat').value),lon:Number(el('lon').value),radius:Number(el('radius').value),photoStyles:styles.length?styles:null}}
function invalidatePois(){stopPoiScan();catalogGeneration++;candidatePoiLayer.clearLayers();poiCatalog=null;}
function pick(lat,lon){locationSelectionRevision++;invalidatePois();el('lat').value=lat.toFixed(6);el('lon').value=lon.toFixed(6);selected.setLatLng([lat,lon]);syncMapSelection();}
const locationStatus=(s,status)=>{el('location-status').textContent=s;if(status)el('location-status').dataset.state=status};
let locationPending=false,locationSelectionRevision=0,automaticLocationOwnsMap=false;
function locateCurrentPosition({automatic=false}={}){
 const revision=locationSelectionRevision;
 if(locationPending)return;
 if(!window.isSecureContext||!navigator.geolocation){locationStatus('Location is unavailable in this browser. Open the HTTPS website, or choose a point on the map.');return;}
 const button=el('locate'),mapButton=el('center-pin');
 window.PhotoScoutLocation.request({geolocation:navigator.geolocation,onState:state=>{
  locationPending=state.status==='pending';
  for(const control of [button,mapButton]){control.disabled=locationPending;control.setAttribute('aria-busy',String(locationPending));}
  button.textContent=locationPending?'⌖ Locating…':state.status==='success'?'✓ Located':'⌖ Near me';
  mapButton.textContent=locationPending?'…':'⌖';locationStatus(state.message,state.status);
  el('map-notice').textContent=state.message;
 },onPosition:position=>{
  window.PhotoScoutLocation.remember(localStorage);
  if(automatic&&(revision!==locationSelectionRevision||sharedPublicationId))return;
  automaticLocationOwnsMap=automatic;
  const {latitude:lat,longitude:lon,accuracy}=position.coords;
  pick(lat,lon);map.fitBounds(searchArea.getBounds(),{padding:[32,32],maxZoom:16});
  locationStatus(`Device location selected${Number.isFinite(accuracy)?` (accuracy ±${Math.ceil(accuracy)} m)`:''}. Click ↑ or press Enter to search.`);
  el('map-notice').textContent=`Your current location is selected${Number.isFinite(accuracy)?` · estimated accuracy ±${Math.ceil(accuracy)} m`:''}.`;
  el('map-heading').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
 }});
}
for(const id of ['locate','center-pin'])el(id).addEventListener('click',()=>locateCurrentPosition());
async function restoreDeviceLocation(){
 const revision=locationSelectionRevision;
 if(sharedPublicationId||!window.isSecureContext||!navigator.geolocation)return;
 if(await window.PhotoScoutLocation.canAutoLocate(navigator.permissions)){
  if(revision===locationSelectionRevision&&!sharedPublicationId)locateCurrentPosition({automatic:true});
 }
}
map.on('click',e=>pick(e.latlng.lat,e.latlng.lng));
map.on('dragstart',()=>{locationSelectionRevision++;});
el('radius').addEventListener('change',()=>{invalidatePois();syncMapSelection();updateParameterSummary();});
function updateParameterSummary(){const moods=[...el('style-options').querySelectorAll('input:checked')].filter(i=>i.value!=='any');el('parameter-summary').textContent=el('radius').selectedOptions[0].textContent+' · '+(moods.length?moods.map(i=>i.parentElement.querySelector('strong').textContent).join(' + '):'Any mood');}
function styleChanged(){updateParameterSummary();invalidatePois();message('Photo mood updated. Submit to explore around the selected pin.');}
function renderStyles(styles){
 const options=[{id:'any',label:'Surprise me',description:'Find distinctive photo opportunities across all moods.'},...styles];
 for(const style of options){const label=node('label',null,'style-choice'),input=document.createElement('input');input.type='checkbox';input.name='photo-style';input.value=style.id;input.checked=style.id==='any';input.addEventListener('change',()=>{const inputs=el('style-options').querySelectorAll('input');if(input.checked&&input.value==='any')inputs.forEach(i=>i.checked=i===input);else if(input.checked)inputs.forEach(i=>{if(i.value==='any')i.checked=false;});if(![...inputs].some(i=>i.checked))inputs.forEach(i=>{if(i.value==='any')i.checked=true;});styleChanged();});const text=node('span');const icons={any:'✳',nature:'❋',urban:'▥',vintage:'◷',iconic:'✦',artistic:'◈',waterside:'≈',minimal:'□',adventure:'△'};const icon=node('span',icons[style.id]||'✳','mood-icon');icon.setAttribute('aria-hidden','true');label.append(icon);text.append(node('strong',style.label),node('span',style.description,'style-description'));label.append(input,text);el('style-options').append(label);}
}
async function loadPois(poiQueries=[],plan={}){
 if(!el('search').reportValidity()||!serviceAvailable)return;
 invalidatePois();const generation=catalogGeneration,coords={...coordinates(),poiQueries,geographicKinds:plan.geographicKinds||[],searchProgram:plan.searchProgram||null,searchBranches:plan.searchBranches||[],osmFeatures:plan.osmFeatures||[],geographicCombination:plan.geographicCombination||'all',featureCombination:plan.featureCombination||'all',scoringIntent:plan.scoringIntent||'',preferences:plan.preferences||'Scenic, distinctive public places for photography'};setProgress(1,'Finding nearby places…','Looking for places that match your location and photo mood.');message('Finding nearby places…');
 try{const data=await json('/photo-scout/v1/pois',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(coords)});
  if(generation!==catalogGeneration)return;poiCatalog={...data,coords};
  for(const p of data.nearbyPois)L.circleMarker([p.lat,p.lon],{radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9}).bindTooltip(node('span',p.name)).addTo(candidatePoiLayer);
  updateSubmitState();if(data.nearbyPois.length)message(`Found ${data.nearbyPois.length} nearby places. Preparing their photos…`);else{const detail='Try a larger radius or another location.';message(detail);setProgress(1,'No nearby places found',detail,'error');}
 }catch(err){if(generation===catalogGeneration){message(err.message);setProgress(1,'Could not find nearby places',err.message,'error');}}
}


function node(tag,txt,cls){const n=document.createElement(tag);if(txt)n.textContent=txt;if(cls)n.className=cls;return n;}
function link(label,url){const n=node('a',label);try{const u=new URL(url);if(u.protocol!=='https:')return node('span',label);n.href=u.href;n.target='_blank';n.rel='noopener noreferrer';}catch{return node('span',label)}return n;}
function photoBearing(spot){const raw=spot.viewHeadingDegrees;if(typeof raw!=='number'||!Number.isFinite(raw))return {heading:null,label:'Direction unavailable'};const heading=Math.round(((raw%360)+360)%360)%360;const compass=['N','NE','E','SE','S','SW','W','NW'][Math.round(heading/45)%8];return {heading,label:`Facing ${compass} · ${heading}°`};}
function photographerIcon(spot,index){const bearing=photoBearing(spot);return L.divIcon({className:'photographer-pin'+(bearing.heading===null?' direction-unknown':''),html:`<span class="photographer-turn" style="transform:rotate(${bearing.heading??0}deg)"><span class="camera-cone"></span><img src="./photographer.svg" width="48" height="56" alt=""></span><span class="photo-rank">${index+1}</span><span class="photo-bearing">${bearing.heading===null?'?':bearing.heading+'°'}</span>`,iconSize:[64,76],iconAnchor:[32,40],popupAnchor:[0,-38]});}
// Task records are scoped by the server to this account or guest session.
// Match the POI, not its panorama: neighboring places may share Street View.
function samePhotoPlace(spot,context){
 const normalize=value=>String(value||'').trim().toLocaleLowerCase().replace(/\s+/g,' ');
 const a=spot?.poi,b=context?.poi;
 if(a?.id&&b?.id)return a.id===b.id;
 if(!a||!b||![a.lat,a.lon,b.lat,b.lon].every(Number.isFinite))return false;
 return Math.abs(a.lat-b.lat)<0.000001&&Math.abs(a.lon-b.lon)<0.000001&&normalize(a.name||spot.name)===normalize(b.name||context.name);
}
function selfiesAtPlace(spot){
 return taskRecords.filter(task=>task.kind==='portrait'&&task.state==='complete'&&
  !(task.expiresAt!=null&&task.expiresAt<=Date.now()/1000)&&samePhotoPlace(spot,task.context||{})).sort((a,b)=>b.created-a.created);
}
function refreshDisplayedShortlist(){if(!displayedResult)return;const hidden=el('results').hidden;render(displayedResult,{save:false,mapUpdate:false,historyId:displayedSearchId});el('results').hidden=hidden;}
function removePoiButton(spot,searchId){
 const button=node('button','Remove','remove-poi');button.type='button';button.hidden=!searchId||searchId.startsWith('public:');button.title='Remove this place from this search history';
 button.addEventListener('click',async()=>{
  if(button.disabled)return;button.disabled=true;button.textContent='Removing…';
  try{
   const data=await json('/photo-scout/v1/hidden-pois',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({searchId,poiId:poiHistoryKey(spot),hidden:true})});
   hiddenPoisBySearch=new Map(Object.entries(data.hiddenPois).map(([id,keys])=>[id,new Set(keys)]));lastRemovedPoi={searchId,poiId:poiHistoryKey(spot),name:spot.name};
   drawHistoryMap();renderHistory();refreshDisplayedShortlist();
   message('Place removed from this search history.');await loadPublications();
  }catch(error){button.disabled=false;button.textContent='Remove';message(error.message);}
 });return button;
}
function popupViewControls(spot){
 const box=node('div',null,'poi-view-controls');if(spot.provider!=='google-street-view'){box.hidden=true;return box;}
 const caption=node('p',null,'poi-view-label');caption.textContent=`View: ${photoBearing(spot).label} · tilt ${spot.viewPitchDegrees||0}°`;
 caption.title='Use arrow keys to choose a view. Score refers to the original reviewed angle.';
 box.append(caption,node('p','← → turn · ↑ ↓ tilt','poi-view-hint'));return box;
}
function rotateSelectedPoiView(key){
 const selected=selectedPoiView;if(!selected||selected.spot.provider!=='google-street-view'||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(key))return false;
 const spot=selected.spot;let heading=photoBearing(spot).heading??0,pitch=spot.viewPitchDegrees||0;
 if(key==='ArrowLeft')heading=(heading+345)%360;if(key==='ArrowRight')heading=(heading+15)%360;
 if(key==='ArrowUp')pitch=Math.min(90,pitch+10);if(key==='ArrowDown')pitch=Math.max(-90,pitch-10);
 const url=new URL(spot.sourceUrl);url.searchParams.set('heading',heading);url.searchParams.set('pitch',pitch);
 const override={sourceUrl:url.toString(),viewHeadingDegrees:heading,viewPitchDegrees:pitch,imageUrl:null,streetViewReference:null};Object.assign(spot,override);poiViewOverrides.set(selected.searchId+':'+poiHistoryKey(spot),override);
 selected.marker.setIcon(selected.searchId===focusedSearchId?photographerIcon(spot,selected.index):directionDot(spot));
 selected.popup.querySelector('.poi-view-label').textContent=`View: ${photoBearing(spot).label} · tilt ${pitch}°`;
 for(const link of selected.popup.querySelectorAll('a')){if(link.href.startsWith('https://www.google.com/maps/@'))link.href=spot.sourceUrl;}
 const preview=selected.popup.querySelector('.popup-photo-preview');if(preview){preview.photoSpot=spot;const link=preview.querySelector('a');if(link)link.href=spot.sourceUrl;}
 clearTimeout(poiPreviewTimer);poiPreviewTimer=setTimeout(()=>{
  if(preview)loadPopupPhoto(selected.popup,true);
  if(displayedSearchId===selected.searchId&&!el('results').hidden){const card=el('photo-spot-'+(selected.index+1)),image=card?.querySelector('img'),link=card?.querySelector('.spot-image-link');if(link)link.href=spot.sourceUrl;if(image)refreshThumbnail(image,spot);}
 },200);return true;
}
document.addEventListener('keydown',event=>{
 if(event.defaultPrevented||event.target?.closest?.('input,textarea,select,[contenteditable="true"],dialog[open]'))return;
 if(rotateSelectedPoiView(event.key)){event.preventDefault();event.stopImmediatePropagation();}
},true);
function popupPhotoPreview(spot){
 const box=node('div',null,'popup-photo-preview');box.photoSpot=spot;
 if(spot.sourceUrl&&(spot.provider==='google-street-view'||spot.imageUrl)){
  const image=node('img');image.alt=spot.name||'Photo spot';image.referrerPolicy='no-referrer';
  const view=link('',spot.sourceUrl);view.setAttribute('aria-label',spot.provider==='google-street-view'?'Open Google Street View':'Open original photo');view.append(image);box.append(view);
  if(spot.provider==='google-street-view')box.append(node('p','Google Maps','GMP-attribution'));
 }else box.append(node('p','Photo preview unavailable. Open the original view below.','small'));
 return box;
}
function loadPopupPhoto(popup,force=false){
 for(const box of popup.querySelectorAll('.popup-photo-preview')){
  const image=box.querySelector('img'),spot=box.photoSpot;if(!image)continue;
  if(!box.previewStatus){box.previewStatus=node('span','','image-unavailable');box.previewStatus.hidden=true;box.append(box.previewStatus);}
  const status=box.previewStatus;
  function refresh(){
   box.dataset.loaded='';status.textContent='Loading preview…';status.hidden=false;
   refreshThumbnail(image,box.photoSpot,()=>{image.hidden=true;status.textContent='Preview unavailable — open the original view.';status.hidden=false;});
  }
  if(!box.dataset.bound){
   box.dataset.bound='true';
   image.addEventListener('load',()=>{image.hidden=false;status.hidden=true;box.dataset.loaded='true';});
   image.addEventListener('error',()=>{
    box.dataset.loaded='';image.hidden=true;
    if(box.photoSpot.provider==='google-street-view'&&Number(box.dataset.retries||0)<1){
     box.dataset.retries=String(Number(box.dataset.retries||0)+1);refresh();
    }else{status.textContent='Preview unavailable — open the original view.';status.hidden=false;}
   });
  }
  if(!force&&box.dataset.loaded&&box.dataset.source===spot.sourceUrl)continue;
  box.dataset.retries='0';box.dataset.source=spot.sourceUrl;image.hidden=false;
  if(spot.provider==='google-street-view')refresh();
  else if(spot.imageUrl)image.src=spot.imageUrl;
 }
}
function photoBackgroundInfo(context){
 const provider={'google-street-view':'Google Street View',panoramax:'Panoramax','wikimedia-commons':'Wikimedia Commons'}[context.provider]||context.provider||'Source unavailable';
 return [context.poi?.category,provider,photoBearing(context).label].filter(Boolean).join(' · ');
}
function focusMapSpot(spot,{searchId=null,openPopup=true}={}){
 const pos=spot?.poi||spot;
 if(!pos||![pos.lat,pos.lon].every(Number.isFinite)||Math.abs(pos.lat)>90||Math.abs(pos.lon)>180)return false;
 const marker=resultPins.find(m=>(!searchId||m.searchId===searchId)&&samePhotoPlace(m.photoSpot,spot));
 if(marker){const layer=marker.searchId===focusedSearchId?photoLocationLayer:otherPhotoLocationLayer;if(!map.hasLayer(layer))layer.addTo(map);}
 map.setView([pos.lat,pos.lon],17);
 if(marker&&openPopup)marker.openPopup();
 return true;
}
function focusPhotoPlace(task){
 const context=task.context||{},pos=context.poi;
 if(!pos||![pos.lat,pos.lon].every(Number.isFinite))return;
 for(const menu of mapMenus)menu.open=false;el('results').hidden=true;
 const history=searchHistory.find(h=>allPoiViews(h.result).some(s=>samePhotoPlace(s,context)));
 if(history){history.checked=true;persistHistory();renderHistory();drawHistoryMap();}
 let marker=resultPins.find(m=>samePhotoPlace(m.photoSpot,context));
 if(!marker){
  const popup=node('div',null,'photo-popup');popup.append(node('strong',context.name||'Photo background'),node('p',photoBackgroundInfo(context)),popupPhotoPreview(context),placeSelfies(context));
  if(context.sourceUrl)popup.append(link(context.provider==='google-street-view'?'Open Street View ↗':'Open original photo ↗',context.sourceUrl));
  marker=L.marker([pos.lat,pos.lon],{title:(context.name||'Photo background')+' · '+photoBearing(context).label,icon:directionDot(context)}).addTo(otherPhotoLocationLayer).bindPopup(popup);marker.on('popupopen',()=>loadPopupPhoto(popup));marker.photoSpot=context;resultPins.push(marker);
 }
 const layer=marker.searchId===focusedSearchId?photoLocationLayer:otherPhotoLocationLayer;if(!map.hasLayer(layer))layer.addTo(map);
 map.setView([pos.lat,pos.lon],17);marker.openPopup();
}
function refreshPlaceSelfies(box){
 const photos=selfiesAtPlace(box.photoSpot),signature=(tasksReady?'ready:':'loading:')+photos.map(t=>t.id).join(',');
 if(box.dataset.photos===signature)return;
 box.dataset.photos=signature;box.hidden=false;
 box.replaceChildren(node('p',`Your selfies here (${photos.length})`,'place-selfie-heading'));
 const list=node('div',null,'place-selfie-list');
 if(!photos.length)list.append(node('p',tasksReady?'No saved selfies for this place in your account or guest session.':'Loading your saved selfies…','place-selfie-empty'));
 for(const task of photos){
  const button=node('button',null,'place-selfie-link');button.type='button';
  const style=portraitStyles.find(s=>s[0]===task.context?.generation?.style)?.[1];
  button.append(node('span','View selfie ↗'),node('small',[new Date(task.created*1000).toLocaleString(),style,photoBearing(task.context||{}).label].filter(Boolean).join(' · ')));
  button.addEventListener('click',()=>viewSavedTask(task));list.append(button);
 }
 box.append(list);
}
function placeSelfies(spot){const box=node('section',null,'place-selfies');box.photoSpot=spot;refreshPlaceSelfies(box);return box;}
function refreshVisiblePlaceSelfies(){for(const box of document.querySelectorAll('.place-selfies'))refreshPlaceSelfies(box);}
function render(result,{scroll=true,save=true,mapUpdate=true,historyId=null}={}){displayedSearchId=save?saveSearch(result):(historyId||searchHistory.find(h=>h.result===result)?.id||focusedSearchId);displayedResult=result;const visiblePois=allPoiViews(result,displayedSearchId);const root=el('results');controls.open=false;for(const menu of mapMenus)menu.open=false;root.hidden=false;el('toggle-results').disabled=false;
 const edit=node('button','Close ×','back-button');edit.type='button';edit.addEventListener('click',()=>{root.hidden=true;});
 const heading=node('div',null,'results-heading');heading.append(node('div','YOUR SHORTLIST','eyebrow'),edit);
 root.replaceChildren(heading,node('h2',historyId?.startsWith('public:')?'Published photo places':'Your nearby photo shortlist'),node('p',result.summary));if(!historyId?.startsWith('public:'))root.append(publishButton('search',displayedSearchId));
 if(lastRemovedPoi?.searchId===displayedSearchId){const notice=node('p',lastRemovedPoi.name+' removed from this search. ','remove-notice'),undo=node('button','Undo','remove-undo');undo.type='button';const removed={...lastRemovedPoi};undo.addEventListener('click',async()=>{undo.disabled=true;try{const data=await json('/photo-scout/v1/hidden-pois',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({...removed,hidden:false})});hiddenPoisBySearch=new Map(Object.entries(data.hiddenPois).map(([id,keys])=>[id,new Set(keys)]));lastRemovedPoi=null;drawHistoryMap();renderHistory();render(displayedResult,{save:false,mapUpdate:false,historyId:displayedSearchId});}catch(error){undo.disabled=false;message(error.message);}});notice.append(undo);root.append(notice);}
 if(result.photoStyles?.length)root.append(node('p',`Photo mood: ${result.photoStyles.map(s=>s.label).join(', ')}`,'tag'));
 if(result.candidatePoiCount!==undefined)root.append(node('p',`Found ${result.candidatePoiCount} named candidates${result.photoLocationCount?' and '+result.photoLocationCount+' photo locations outside named POIs':''}; showing ${visiblePois.length} with scored images.`,'small'),node('p',result.poiProvider==='google-places'?'Place data · Google Maps':'POI data © OpenStreetMap contributors · ODbL 1.0','small'));const cards=node('div',null,'cards');root.append(cards);
 for(const [i,originalSpot] of visiblePois.entries()){const s={...originalSpot,...poiViewOverrides.get(displayedSearchId+':'+poiHistoryKey(originalSpot))};const card=node('article',null,'card'),cardSearchId=displayedSearchId;card.id='photo-spot-'+String(i+1);card.addEventListener('click',event=>{if(!event.target.closest('button,input,select,textarea,summary'))focusMapSpot(s,{searchId:cardSearchId});});if(s.imageUrl||s.provider==='google-street-view'){const img=node('img');if(s.provider==='google-street-view')refreshThumbnail(img,s);else if(s.imageUrl)img.src=s.imageUrl;img.alt=s.name;img.loading='eager';img.referrerPolicy='no-referrer';img.addEventListener('error',()=>{const attempts=Number(img.dataset.retryCount||0);if(s.provider==='google-street-view'&&attempts<2){img.dataset.retryCount=String(attempts+1);setTimeout(()=>{if(img.isConnected)refreshThumbnail(img,s);},attempts?65000:2000);}else img.replaceWith(node('span','Image unavailable — open the original view ↗','image-unavailable'));});const imageLink=link('',s.sourceUrl);imageLink.className='spot-image-link';imageLink.setAttribute('aria-label',(s.provider==='google-street-view'?'Open Google Street View for ':'Open original photo for ')+s.name);imageLink.append(img,node('span',s.provider==='google-street-view'?'Open Street View ↗':'Open original photo ↗','image-open-label'));card.append(imageLink);}else if(s.provider==='google-street-view'){const view=link('Explore this view in Google Street View ↗',s.sourceUrl);view.className='street-view-link';card.append(view);}const body=node('div',null,'body');body.append(...(s.provider==='google-street-view'?[node('p','Google Maps','GMP-attribution')]:[]),node('h3',`${i+1}. ${s.name}`),node('span',s.score==null?'No verified image':`${s.score}/100 subjective photo score · ${s.confidence} confidence`,'tag'),...(s.recommend===false?[node('p','Model flagged suitability concerns — review the notes','small')]:[]),...(s.provider?[node('p',`Image source: ${s.provider}`,'small')]:[]),...(s.poi?[node('p',`${s.poi.category==='photo-location'?'Photo location':'Candidate place'}: ${s.poi.name}${s.poi.category&&s.poi.category!=='photo-location'?' · '+s.poi.category:''}`,'small')]:[]),node('p',s.visible_evidence),...(s.photo_tip?[node('p',`Photo idea: ${s.photo_tip}`)]:[]),...(s.viewHeadingDegrees!=null?[node('p',`Inspected camera direction: ${s.viewHeadingDegrees}°`,'small')]:[]),...(s.distanceMeters!=null?[node('p',`${s.distanceMeters} m straight-line distance · ${s.locationType}`)]:[]),...(s.uncertainty?[node('p',`Uncertainty: ${s.uncertainty}`)]:[]),node('p',s.coordinateWarning),...(s.sourceUrl?[link(s.provider==='google-street-view'?'View Google Street View ↗':'Original image ↗',s.sourceUrl)]:[]),...(s.author?[node('p',`${s.author} · ${s.license} · source date: ${s.sourceDate||s.capturedAt||'unknown'}`)]:[]),...(s.licenseUrl?[link('Image license ↗',s.licenseUrl)]:[]));const compose=node('button','📷 Take a selfie here','compose-photo');compose.type='button';compose.addEventListener('click',()=>openPhotoStudio({...s,...poiViewOverrides.get(displayedSearchId+':'+poiHistoryKey(s))}));const title=body.querySelector('h3'),locate=node('button',`${i+1}. ${s.name}`,'shortlist-place-focus');locate.type='button';locate.setAttribute('aria-label','Show '+s.name+' on map');locate.addEventListener('click',()=>focusMapSpot(s,{searchId:cardSearchId}));title?.replaceChildren(locate);body.append(compose,publishButton('place',displayedSearchId,s),removePoiButton(s,displayedSearchId),placeSelfies(s));card.append(body);cards.append(card);}
 if(result.imageAssessments?.length){const detail=node('details',null,'score-details');detail.append(node('summary',`All ${result.imageAssessments.length} image checks`));const list=node('ul');for(const a of result.imageAssessments){const li=node('li');li.append(node('strong',`${a.matches_request===false?'Excluded':a.score+'/100'} · ${a.name}`),node('p',`${a.provider} · ${a.eligibleForRecommendation?'Suitable candidate':'Not recommended'}`,'small'),node('p',a.visible_evidence),...(a.exclusionReason?[node('p',`Why not shortlisted: ${a.exclusionReason}`)]:[]),node('p',`Uncertainty: ${a.uncertainty}`));list.append(li);}detail.append(list);root.append(detail);}

 if(result.sources){const coverage=node('p',Object.entries(result.sources).map(([name,s])=>`${name}: ${s.status==='ok'?(s.eligibleImages!==undefined?`${s.eligibleImages} eligible images${s.sampledImages!==undefined?`; ${s.sampledImages} selected for scoring`:""}`:'available'):'temporarily unavailable'}`).join(' · '),'small');root.append(coverage);}
 if(result.inspectedImages!==undefined)root.append(node('p',`${result.analysisMethod==='fixed-batch-scoring'?'Checked':'Inspected'} ${result.inspectedImages} images${result.inspectedImageSources?.length?" from "+result.inspectedImageSources.join(", "):""}. ${result.coverage||''}`,'small'));if(mapUpdate)drawHistoryMap({fit:true});}
async function refreshThumbnail(img,spot,onFailure){img.dataset.refreshed='true';const generation=Number(img.dataset.viewGeneration||0)+1;img.dataset.viewGeneration=String(generation);try{const data=await json('/photo-scout/v1/thumbnails',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sourceUrls:[spot.sourceUrl]})});if(img.dataset.viewGeneration!==String(generation))return;if(data.imageUrls[0])img.src=data.imageUrls[0];else throw Error('Unavailable');}catch{if(img.dataset.viewGeneration!==String(generation))return;if(onFailure)onFailure();else img.replaceWith(node('span','Image unavailable — open the original view ↗','image-unavailable'));}}
async function json(url,options){const r=await fetch(api+url,{credentials:'include',...options}),data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Request failed');return data;}
// Remove legacy active-job tokens; completed history is stored separately.
try{localStorage.removeItem('photo-scout-active-search');for(const key of Object.keys(sessionStorage)){if(key.startsWith('photo-scout:'))sessionStorage.removeItem(key);}}catch{}
if(location.search){const clean=new URL(location.href);sharedPublicationId=clean.searchParams.get('published');clean.search='';if(sharedPublicationId)clean.searchParams.set('published',sharedPublicationId);history.replaceState(null,'',clean);}
let activeSearch=null;
// The task list monitors every job; activeSearch only chooses the map's focus.
async function poll(job,token){if(!activeSearch||activeSearch.jobId!==job)activeSearch={jobId:job,reportToken:token};await restoreTasks();}
window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
async function analyzePlaces(){if(searchBusy||!poiCatalog||!humanFreePreview)return;const selectedPoiIds=poiCatalog.nearbyPois.map(p=>p.id);if(!selectedPoiIds.length){message('No nearby places found. Try a larger radius.');return;}searchBusy=true;updateSubmitState();setProgress(2,'Starting your photo review…',`Preparing images from ${selectedPoiIds.length} nearby places for AI review.`);message('Submitting background search…');const payload={...poiCatalog.coords,selectedPoiIds,poiCatalogToken:poiCatalog.poiCatalogToken,preferences:poiCatalog.coords.preferences||'Scenic, distinctive public places for photography'};try{activeSearch=await json('/photo-scout/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-Request-Token':crypto.randomUUID().replaceAll('-','')},body:JSON.stringify(payload)});activeSearch.context=payload;activeSearch.draft=searchDraft();addSubmittedTask('search',activeSearch,payload);searchBusy=false;updateSubmitState();await poll(activeSearch.jobId,activeSearch.reportToken);}catch(err){setProgress(2,'Search could not be started',err.message,'error');message(err.message);}finally{searchBusy=false;updateSubmitState();}}
el('search').addEventListener('submit',e=>{e.preventDefault();el('prompt-form').requestSubmit();});
let serviceStatusBusy=false,serviceStatusTimer=null,serviceStylesReady=false;
async function refreshServiceStatus(){
 if(serviceStatusBusy)return;serviceStatusBusy=true;clearTimeout(serviceStatusTimer);
 const note='Reconnecting to Photo Scout… Search will become available automatically.';
 try{const s=await json('/photo-scout/v1/status');humanFreePreview=s.humanFreePreview===true;if(!serviceStylesReady){renderStyles(s.photoStyles||[]);serviceStylesReady=true;}updateParameterSummary();serviceAvailable=Boolean(s.enabled&&humanFreePreview);el('test-notice').hidden=!humanFreePreview;if(serviceAvailable&&el('message').textContent===note)message('');}
 catch{serviceAvailable=false;if(!searchBusy)message(note);}
 finally{serviceStatusBusy=false;updateSubmitState();if(!serviceAvailable)serviceStatusTimer=setTimeout(refreshServiceStatus,document.hidden?30000:5000);}
}
refreshServiceStatus();
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!serviceAvailable)refreshServiceStatus();});
let intentGeneration=0;
function updateSubmitState(){const busy=resolving||pipelineBusy||searchBusy;el('resolve-query').disabled=!serviceAvailable||!tasksReady||busy;el('resolve-query').setAttribute('aria-busy',String(busy));el('resolve-query').setAttribute('aria-label',busy?'Search in progress':'Find photo spots');el('submit-arrow').hidden=busy;el('submit-spinner').hidden=!busy;el('prompt-form').setAttribute('aria-busy',String(busy));}
function setProgress(step,title,detail,state='running',preservePanels=false){if(state!=='running'||step!==2)stopPoiScan();if(!preservePanels)controls.open=false;el('search-progress').hidden=false;el('search-progress').setAttribute('data-state',state);el('progress-title').textContent=title;el('progress-detail').textContent=detail;document.querySelectorAll('.progress-steps [data-step]').forEach(item=>item.setAttribute('data-state',Number(item.dataset.step)<step?'done':Number(item.dataset.step)===step?'active':'waiting'));if(state==='running'&&!preservePanels){el('message').textContent='';el('prompt-status').textContent='';el('results').hidden=true;}}
async function runSearchPipeline(poiQueries=[],plan={}){if(pipelineBusy||searchBusy)return;pipelineBusy=true;updateSubmitState();try{await loadPois(poiQueries,plan);if(poiCatalog?.nearbyPois.length)await analyzePlaces();}finally{pipelineBusy=false;updateSubmitState();}}
async function applyIntent(plan,place){pick(place.lat,place.lon);const radius=String(plan.radiusMeters);if(![...el('radius').options].some(o=>o.value===radius)){const option=node('option',radius+' m');option.value=radius;el('radius').append(option);}el('radius').value=radius;fitSearchRange();el('style-options').querySelectorAll('input').forEach(i=>i.checked=plan.photoStyles.length?plan.photoStyles.includes(i.value):i.value==='any');updateParameterSummary();el('location-options').replaceChildren();el('prompt-status').textContent=place.label+' · '+plan.explanation;await runSearchPipeline(plan.poiQueries||[],plan);}
async function submitSearch(e){e?.preventDefault();if(!serviceAvailable||resolving||pipelineBusy||searchBusy)return;controls.open=false;focusedSearchId=null;drawHistoryMap();fitSearchRange();const query=el('prompt-query').value.trim();if(humanFreePreview){await submitDurableSearch(query);return;}const generation=++intentGeneration;resolving=true;updateSubmitState();setProgress(0,'Understanding your request…','Finding the location, photo mood and search radius in your message.');el('location-options').replaceChildren();el('prompt-status').textContent='Understanding your request and finding matching locations…';try{const c=coordinates();const plan=await json('/photo-scout/v1/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,lat:c.lat,lon:c.lon,radius:c.radius,photoStyles:c.photoStyles||[],preferences:'Scenic, distinctive public places for photography'})});if(generation!==intentGeneration)return;if(plan.clarification){el('prompt-status').textContent=plan.clarification;setProgress(0,'Could not interpret the request',plan.clarification,'error');return;}if(!plan.locations.length){el('prompt-status').textContent='No matching location. Please add a city or place name.';setProgress(0,'Could not find a location',el('prompt-status').textContent,'error');return;}el('prompt-status').textContent='Using '+plan.locations[0].label+'. Finding and scoring nearby views…';await applyIntent(plan,plan.locations[0]);}catch(err){el('prompt-status').textContent=err.message;setProgress(0,'Search could not be started',err.message,'error');}finally{resolving=false;updateSubmitState();}}
el('prompt-form').addEventListener('submit',submitSearch);

el('prompt-query').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();el('prompt-form').requestSubmit();}});

el('toggle-results').addEventListener('click',()=>{el('results').hidden=!el('results').hidden;});
el('sample').addEventListener('click',async()=>{try{const r=await fetch('./sample.json');if(!r.ok)throw Error('Sample unavailable');const data=await r.json();render(data);}catch(e){message(e.message)}});

el('sample-paris').addEventListener('click',async()=>{try{const r=await fetch('./sample-paris.json');if(!r.ok)throw Error('Sample unavailable');render(await r.json());}catch(e){message(e.message)}});

el("sample-google").addEventListener("click",async()=>{try{const r=await fetch("./sample-google.json");if(!r.ok)throw Error("Sample unavailable");const data=await r.json();render(data);message(data.sampleNotice);}catch(e){message(e.message)}});

const HISTORY_KEY='photo-scout-search-history-v1';let searchHistory=[],focusedSearchId=null,displayedResult=null,displayedSearchId=null,hiddenPoisBySearch=new Map(),authUser=null,csrfToken=null;const pendingHistory=new Map();let syncingHistory=false;
function hasScoredImage(spot){return Number.isFinite(spot.score)&&spot.assessmentStatus!=='no_verified_view'&&Boolean(spot.imageUrl||spot.streetViewReference||(spot.provider==='google-street-view'&&spot.sourceUrl));}
function poiHistoryKey(spot){return String(spot.poi?.id||spot.id||`geo:${spot.poi?.lat},${spot.poi?.lon}:${spot.name}`);}
function allPoiViews(result,searchId=searchHistory.find(h=>h.result===result)?.id){const hidden=hiddenPoisBySearch.get(searchId)||new Set();const seen=new Set();return [...(result.poiResults||[]),...(result.spots||[])].filter(s=>hasScoredImage(s)&&!hidden.has(poiHistoryKey(s))).sort((a,b)=>b.score-a.score).filter(s=>{const id=s.poi?.id||s.id;if(seen.has(id))return false;seen.add(id);return true;});}
function photoTopCount(result){return allPoiViews(result).length;}
function directionDot(spot){const b=photoBearing(spot);return L.divIcon({className:'direction-dot'+(b.heading===null?' no-bearing':''),html:`<span style="transform:rotate(${b.heading??0}deg)"><i></i><b></b></span><small class="dot-bearing">${b.heading===null?'?':b.heading+'°'}</small>`,iconSize:[36,48],iconAnchor:[18,16],popupAnchor:[0,-16]});}
function storedHistory(){return searchHistory.map(h=>({...h,result:{...h.result,imageAssessments:[],spots:(h.result.spots||[]).map(stripHistoryImage),poiResults:(h.result.poiResults||[]).map(stripHistoryImage)}}));}
function persistHistory(){const records=storedHistory();if(authUser){queueHistory(records);return;}try{sessionStorage.setItem(HISTORY_KEY,JSON.stringify(records));}catch{el('history-note').textContent='Session storage is unavailable; history stays in this page.';}}
async function queueHistory(records){for(const h of records)pendingHistory.set(h.id,h);if(syncingHistory)return;syncingHistory=true;try{while(pendingHistory.size&&authUser){const [id,item]=pendingHistory.entries().next().value;await json('/photo-scout/v1/history',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},body:JSON.stringify(item)});if(pendingHistory.get(id)===item)pendingHistory.delete(id);}}catch{el('history-note').textContent='Account history could not sync. Keep this page open and try again.';}finally{syncingHistory=false;}}
async function loadAccount(){try{const state=await json('/photo-scout/v1/auth/me');authUser=state.user;csrfToken=state.csrfToken;el('account-login').disabled=!state.configured;el('account-login').textContent='Sign in';el('account-login').title=state.configured?'Sign in with Google':'Google sign-in is not configured yet';el('account-login').hidden=Boolean(authUser);el('account-logout').hidden=!authUser;el('account-name').textContent=authUser?authUser.name:'Guest session';if(authUser){const guest=searchHistory,remote=await json('/photo-scout/v1/history');const merged=new Map(remote.items.map(h=>[h.id,h]));for(const h of guest)merged.set(h.id,h);searchHistory=[...merged.values()].sort((a,b)=>b.created-a.created);if(guest.length){await queueHistory(guest.map(h=>({...h,result:{...h.result,imageAssessments:[],spots:(h.result.spots||[]).map(stripHistoryImage),poiResults:(h.result.poiResults||[]).map(stripHistoryImage)}})));if(!pendingHistory.size)sessionStorage.removeItem(HISTORY_KEY);}el('history-note').textContent='Your searches and generated photos are saved permanently to your account.';renderHistory();drawHistoryMap({fit:!locationPending&&!automaticLocationOwnsMap});}else el('history-note').textContent='Guest searches and photos are deleted after 7 days without a visit. Sign in to keep them permanently.';}catch{el('account-name').textContent='Guest session';el('account-login').disabled=true;el('history-note').textContent='Sign-in is temporarily unavailable. Guest history stays in this session.';}}
el('account-login').addEventListener('click',()=>{persistHistory();location.href=api+'/photo-scout/v1/auth/login';});
el('account-logout').addEventListener('click',async()=>{try{await json('/photo-scout/v1/auth/logout',{method:'POST',headers:{'X-CSRF-Token':csrfToken}});authUser=null;csrfToken=null;pendingHistory.clear();searchHistory=[];sessionStorage.removeItem(HISTORY_KEY);el('results').hidden=true;el('toggle-results').disabled=true;renderHistory();drawHistoryMap();resetTaskRecovery();await loadAccount();await restoreTasks();await loadPublications();}catch{el('history-note').textContent='Sign-out failed. Please try again.';}});
function stripHistoryImage(s){const copy={...s};delete copy.imageUrl;delete copy.streetViewReference;return copy;}
function saveSearch(result){const id=activeSearch?.jobId||crypto.randomUUID();focusedSearchId=id;if(searchHistory.some(h=>h.id===id))return id;const context=activeSearch?.context;searchHistory.unshift({id,created:Date.now(),label:context?.query||context?.locationLabel||el('prompt-query').value.trim()||`Around ${Number(el('lat').value).toFixed(4)}, ${Number(el('lon').value).toFixed(4)}`,radius:context?.radius||Number(el('radius').value),checked:true,result:{...result,searchContext:{...(context||coordinates())}}});persistHistory();renderHistory();return id;}
function searchDraft(){return JSON.stringify({...coordinates(),query:el('prompt-query').value.trim(),preferences:'Scenic, distinctive public places for photography'});}
function addSubmittedTask(kind,job,context){const id=job.id||job.jobId;taskRecords=[{id,kind,created:Date.now()/1000,state:'queued',context,localPending:true},...taskRecords.filter(t=>t.id!==id||t.kind!==kind)];renderHistory();}
function historyEntries(){
 const merged=new Map(searchHistory.map(h=>['search:'+h.id,{id:h.id,kind:'search',created:h.created,label:h.label,history:h,state:'complete'}]));
 for(const task of taskRecords){const key=task.kind+':'+task.id,existing=merged.get(key),c=task.context||{};merged.set(key,{...existing,id:task.id,kind:task.kind,created:task.created*1000,label:c.query||existing?.label||c.name||c.locationLabel||(Number.isFinite(c.lat)&&Number.isFinite(c.lon)?`Around ${c.lat.toFixed(4)}, ${c.lon.toFixed(4)}`:'Photo search'),task,state:existing?.history?'complete':task.state});}
 const entries=[...merged.values()].filter(e=>!removedHistoryItems.has(e.kind+':'+e.id)).sort((a,b)=>b.created-a.created);
 const searches=entries.filter(e=>e.kind==='search'),photos=entries.filter(e=>e.kind==='portrait');
 return [...searches,...photos].sort((a,b)=>b.created-a.created);
}
function removeHistoryButton(entry){
 const button=node('button','Remove','history-remove');button.type='button';
 button.setAttribute('aria-label','Remove '+(entry.kind==='portrait'?'photo: ':'search: ')+entry.label);
 button.addEventListener('click',async()=>{
  button.disabled=true;
  try{
   await json('/photo-scout/v1/removed-items',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({kind:entry.kind,id:entry.id,removed:true})});
   removedHistoryItems.add(entry.kind+':'+entry.id);lastRemovedHistory=entry;pendingHistory.delete(entry.id);
   taskRecords=taskRecords.filter(t=>t.kind!==entry.kind||t.id!==entry.id);
   if(entry.kind==='search'){
    searchHistory=searchHistory.filter(h=>h.id!==entry.id);
    if(focusedSearchId===entry.id)focusedSearchId=null;
    if(displayedSearchId===entry.id){el('results').hidden=true;displayedResult=null;displayedSearchId=null;}
    persistHistory();drawHistoryMap();
   }
   renderHistory();refreshVisiblePlaceSelfies();await loadPublications();
  }catch(error){button.disabled=false;message(error.message);}
 });
 return button;
}
function bindHistoryRow(row,openEntry){
 row.classList.add('history-row-open');row.addEventListener('click',event=>{if(event.target.closest('button,input,a,label,select,textarea,summary'))return;openEntry();});
}
function renderHistory(){
 el('photo-retention-note').textContent=authUser?'Photos are saved permanently to your account.':'Guest photos are deleted after 7 days without a visit. Sign in to keep them permanently.';
 const root=el('history-items'),photosRoot=el('photo-items'),entries=historyEntries(),searches=entries.filter(e=>e.kind==='search'),photos=entries.filter(e=>e.kind==='portrait');root.replaceChildren();photosRoot.replaceChildren();const active=taskRecords.filter(t=>['queued','checking','running'].includes(t.state)).length;root.append(node('p',active+' / 5 active tasks','small'));el('history-count').textContent=String(searches.length);el('photo-count').textContent=String(photos.length);
 if(lastRemovedHistory){
  const removed=lastRemovedHistory,notice=node('p','Removed '+removed.label+'. ','remove-notice'),undo=node('button','Undo','remove-undo');undo.type='button';
  undo.addEventListener('click',async()=>{
   undo.disabled=true;
   try{
    await json('/photo-scout/v1/removed-items',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({kind:removed.kind,id:removed.id,removed:false})});
    removedHistoryItems.delete(removed.kind+':'+removed.id);lastRemovedHistory=null;
    if(removed.history&&!searchHistory.some(h=>h.id===removed.id)){searchHistory.push(removed.history);searchHistory.sort((a,b)=>b.created-a.created);persistHistory();}
    if(removed.task&&!taskRecords.some(t=>t.id===removed.id&&t.kind===removed.kind))taskRecords.push(removed.task);
    renderHistory();drawHistoryMap();refreshVisiblePlaceSelfies();restoreTasks();
   }catch(error){undo.disabled=false;message(error.message);}
  });notice.append(undo);(removed.kind==='portrait'?photosRoot:root).append(notice);
 }
 const completed=entries.filter(e=>e.kind==='search'&&e.history).map(e=>e.history);
 el('history-all').disabled=!completed.length;el('history-all').checked=completed.length>0&&completed.every(h=>h.checked);el('history-all').indeterminate=completed.some(h=>h.checked)&&!completed.every(h=>h.checked);
 if(!searches.length)root.append(node('p','Your searches will appear here.','small'));if(!photos.length)photosRoot.append(node('p','Your selfies will appear here.','small'));
 for(const entry of entries){
  if(entry.kind==='portrait'){
   const item=node('button',null,'photo-history-entry'),thumb=node('span',null,'photo-history-thumb'),copy=node('span',null,'photo-history-copy');
   thumb.setAttribute('aria-hidden','true');
   if(entry.state==='complete'){const image=node('img');image.alt='';image.loading='lazy';image.decoding='async';image.src=api+'/photo-scout/v1/portraits/'+encodeURIComponent(entry.id)+'/thumbnail';image.addEventListener('error',()=>{image.hidden=true;thumb.textContent='📷';});thumb.append(image);}else{thumb.textContent=['queued','checking','running'].includes(entry.state)?'✦':'📷';thumb.dataset.state=entry.state;}
   copy.append(node('strong',entry.label),node('small',`${new Date(entry.created).toLocaleString()} · Selfie · ${entry.state}`));item.append(thumb,copy);item.type='button';item.dataset.state=entry.state;item.title=['queued','checking','running'].includes(entry.state)?'Selfie in progress':'View saved photo';item.addEventListener('click',()=>viewSavedTask(entry.task));const row=node('div',null,'photo-history-row');row.append(item,removeHistoryButton(entry));photosRoot.append(row);continue;
  }
  const h=entry.history,row=node('div',null,'history-item'),label=node('span',null,'history-copy');
  if(h){const check=node('input');check.type='checkbox';check.checked=h.checked;check.setAttribute('aria-label','Show search: '+entry.label);check.addEventListener('change',()=>{h.checked=check.checked;persistHistory();renderHistory();drawHistoryMap({fit:true});});label.append(check);}
  const text=node('span'),detail=h?`${h.radius/1000} km · ${allPoiViews(h.result).length} places`:(entry.kind==='portrait'?'Selfie · ':'')+(entry.state==='running'&&entry.kind==='search'?({sources:'Finding photos',exploring:'Exploring viewpoints',scoring:'Checking photos'}[entry.task?.context?.stage]||'Resolving location'):entry.state);
  const openEntry=()=>h?showHistorySearch(h.result,h.result.searchContext,h):viewSavedTask(entry.task);
  text.setAttribute('role','button');text.tabIndex=0;text.className='history-open';bindHistoryRow(row,openEntry);text.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openEntry();}});
  text.append(node('strong',entry.label),node('small',`${new Date(entry.created).toLocaleString()} · ${detail}`));label.append(text);
  const pending=['queued','running','checking'].includes(entry.state),view=node('button',pending?'Progress':entry.state==='failed'?'Details':entry.kind==='portrait'?'View photo':'View');view.type='button';
  view.addEventListener('click',openEntry);
  const actions=node('div',null,'history-entry-actions');actions.append(view,...(h?[publishButton('search',entry.id)]:[]),removeHistoryButton(entry));row.append(label,actions);(entry.kind==='portrait'?photosRoot:root).append(row);
 }
}
function drawHistoryMap({fit=false}={}){photoLocationLayer.clearLayers();otherPhotoLocationLayer.clearLayers();candidatePoiLayer.clearLayers();resultPins=[];for(const h of searchHistory.filter(h=>h.checked).sort((a,b)=>Number(a.id===focusedSearchId)-Number(b.id===focusedSearchId))){const focused=h.id===focusedSearchId;for(const [i,originalSpot] of allPoiViews(h.result,h.id).entries()){const s={...originalSpot,...poiViewOverrides.get(h.id+':'+poiHistoryKey(originalSpot))};const pos=s.poi;if(!pos||!Number.isFinite(pos.lat)||!Number.isFinite(pos.lon))continue;const b=photoBearing(s),popup=node('div',null,'photo-popup');popup.append(node('strong',s.name),node('p',s.score==null?'No verified image':`${s.score}/100 · ${b.label}`),node('p',h.label));const show=node('button','View place','popup-shortlist');show.type='button';const reveal=()=>{render(h.result,{save:false,mapUpdate:false,historyId:h.id});el('photo-spot-'+String(i+1))?.scrollIntoView({block:'nearest',behavior:'smooth'});};show.addEventListener('click',reveal);const selfie=node('button','📷 Take a selfie here','compose-photo popup-selfie');selfie.type='button';selfie.addEventListener('click',()=>openPhotoStudio(s));const actions=node('div',null,'popup-actions');actions.append(show,publishButton('place',h.id,s),removePoiButton(s,h.id));popup.append(popupPhotoPreview(s),popupViewControls(s),actions,selfie,placeSelfies(s));if(s.sourceUrl)popup.append(link(s.provider==='google-street-view'?'Open Street View ↗':'Open original photo ↗',s.sourceUrl));const marker=L.marker([pos.lat,pos.lon],{title:`${s.name} · ${b.label} · ${h.label}`,icon:focused?photographerIcon(s,i):directionDot(s)}).addTo(focused?photoLocationLayer:otherPhotoLocationLayer).bindPopup(popup).on('popupopen',()=>{focusMapSpot(s,{searchId:h.id,openPopup:false});selectedPoiView={marker,popup,spot:s,searchId:h.id,index:i};loadPopupPhoto(popup);refreshVisiblePlaceSelfies();for(const box of popup.querySelectorAll('.place-selfies'))refreshPlaceSelfies(box);}).on('popupclose',()=>{if(selectedPoiView?.marker===marker)selectedPoiView=null;});marker.photoSpot=s;marker.searchId=h.id;resultPins.push(marker);}}if(fit&&resultPins.length)map.fitBounds(L.featureGroup(resultPins).getBounds().pad(.2),{paddingTopLeft:[40,80],paddingBottomRight:[40,250],maxZoom:16});}
el('history-all').addEventListener('change',()=>{for(const h of searchHistory)h.checked=el('history-all').checked;persistHistory();renderHistory();drawHistoryMap({fit:true});});
try{const legacy=localStorage.getItem(HISTORY_KEY);if(legacy&&!sessionStorage.getItem(HISTORY_KEY))sessionStorage.setItem(HISTORY_KEY,legacy);localStorage.removeItem(HISTORY_KEY);const lastVisit=Number(sessionStorage.getItem(HISTORY_KEY+'-last-visit'));if(lastVisit&&lastVisit<Date.now()-7*86400000)sessionStorage.removeItem(HISTORY_KEY);const saved=JSON.parse(sessionStorage.getItem(HISTORY_KEY)||'[]');if(Array.isArray(saved))searchHistory=saved.filter(h=>h&&typeof h.id==='string'&&typeof h.label==='string'&&h.result&&Array.isArray(h.result.spots));}catch{}renderHistory();drawHistoryMap({fit:true});loadAccount().finally(()=>restoreTasks());

// The photo studio keeps personal uploads and generated images out of search history.
let studioBusy=false,studioActivityMarker=null;
let studioJob=null,studioFile=null,studioSpot=null,studioUrl=null,studioPreviewUrl=null,studioTimer=null,studioPrepared=null,studioUploadGeneration=0,studioOutputFile=null,studioOutputGeneration=0;
const studio=node('dialog',null,'photo-studio');studio.setAttribute('aria-label','Photo studio');
const studioTop=node('div',null,'studio-heading'),studioTitle=node('h2','Take a selfie here'),studioClose=node('button','Close ×');studioClose.type='button';studioTop.append(studioTitle,studioClose);
const studioPlace=node('p',null,'small'),studioImages=node('div',null,'studio-inputs'),scenePreview=node('img'),personPreview=node('img');scenePreview.alt='Selected background';personPreview.alt='Your uploaded photo';personPreview.hidden=true;
const studioScene=node('figure',null,'studio-image-card'),studioPerson=node('figure',null,'studio-image-card'),studioPersonFrame=node('button',null,'studio-person-frame'),personPlaceholder=node('img',null,'studio-person-placeholder');
studioPersonFrame.type='button';studioPersonFrame.setAttribute('aria-label','Upload or replace your selfie');studioPersonFrame.title='Choose your selfie photo';
personPlaceholder.src='./selfie-placeholder.svg';personPlaceholder.alt='A person silhouette showing where your selfie goes';
studioScene.append(scenePreview,node('figcaption','Background'));studioPersonFrame.append(personPlaceholder,personPreview);studioPerson.append(studioPersonFrame,node('figcaption','Your selfie · Tap to upload'));studioImages.append(studioScene,studioPerson);
const studioIntro=node('p','Upload a selfie or portrait of yourself. We’ll combine your photo with the background on the left to create a natural-looking photo of you in this scene.','studio-intro');
const uploadLabel=node('label','Upload your selfie · JPG, PNG, WebP or HEIC, up to 20 MB · automatically resized','studio-upload'),studioUpload=node('input');studioUpload.type='file';studioUpload.accept='image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif';uploadLabel.append(studioUpload);studioPersonFrame.addEventListener('click',()=>{if(!studioUpload.disabled)studioUpload.click();});
const studioCameraButton=node('button','Take a photo','studio-camera-button');studioCameraButton.type='button';
let studioCamera=null;studioCameraButton.addEventListener('click',()=>{if(studioBusy)return;studioCamera??=new PhotoScoutCamera(file=>{studioUpload.value='';acceptStudioPhoto(file);});studioCamera.open();});
uploadLabel.append(studioCameraButton);
let studioStyle='natural';
const studioStyles=node('fieldset',null,'studio-styles'),studioStyleLegend=node('legend','Photo style'),studioStyleGrid=node('div',null,'studio-style-grid'),studioStyleHint=node('p','Relaxed pose, soft smile, your original outfit.','small');
const portraitStyles=[['natural','Natural','Relaxed pose, soft smile, your original outfit.'],['street','Street style','Confident pose, contemporary urban clothing, candid expression.'],['cinematic','Cinematic','Expressive pose, understated clothing, a thoughtful look.'],['vacation','Vacation','Relaxed holiday pose, comfortable clothing, a cheerful smile.'],['editorial','Editorial','Elegant pose, refined clothing, a polished magazine look.']];
for(const [id,label,hint] of portraitStyles){const choice=node('label',null,'studio-style'),input=node('input');input.type='radio';input.name='portrait-style';input.value=id;input.checked=id==='natural';input.addEventListener('change',()=>{if(input.checked){studioStyle=id;studioStyleHint.textContent=hint;}});choice.append(input,node('span',label));studioStyleGrid.append(choice);}
studioStyles.append(studioStyleLegend,studioStyleGrid,studioStyleHint,node('p','Your location stays the same. Styles adjust your look; weather can change the light and atmosphere.','small'));
const studioOptions=node('fieldset',null,'studio-options'),studioOptionsLegend=node('legend','Make it yours'),studioOptionsGrid=node('div',null,'studio-options-grid');
function studioSelect(label,name,options){const wrapper=node('label',label),select=node('select');select.name=name;select.setAttribute('aria-label',label);for(const [value,text] of options){const option=node('option',text);option.value=value;select.append(option);}select.value=options[0][0];wrapper.append(select);studioOptionsGrid.append(wrapper);return select;}
const studioPosture=studioSelect('Posture','posture',[['auto','Auto'],['standing','Relaxed standing'],['walking','Candid walking'],['sitting','Seated'],['looking_back','Looking back'],['playful','Playful']]);
const studioWeather=studioSelect('Weather & light','weather',[['original','Keep original'],['daytime','Daytime'],['night','Night'],['sunny','Sunny daylight'],['golden_hour','Golden hour'],['overcast','Soft overcast'],['rainy','Gentle rain'],['snowy','Gentle snow']]);
const studioExpression=studioSelect('Expression','expression',[['auto','Auto'],['soft_smile','Soft smile'],['big_smile','Big smile'],['thoughtful','Thoughtful'],['serious','Confident & serious'],['surprised','Playful surprise']]);
const studioFraming=studioSelect('Street View framing','framing',[['auto','Auto · compare 90°, 60°, 45°'],['90','90° · Wide'],['60','60° · Natural'],['45','45° · Close']]);
studioOptions.append(studioOptionsLegend,studioOptionsGrid);
const poseLabel=node('label','Additional directions (optional)','studio-upload'),studioPose=node('textarea');studioPose.maxLength=500;studioPose.placeholder='For example: standing naturally, full body, a relaxed smile';poseLabel.append(studioPose);
const studioNote=node('p','Your photo and this view will be sent to OpenAI to create an AI composite. Your upload is removed after processing; signed-in results are saved permanently; guest results are deleted after 7 days without a visit.','small');
const studioGenerate=node('button','Create my photo · Free test','studio-generate');studioGenerate.type='button';studioGenerate.disabled=true;
const studioStatus=node('p',null,'studio-status');studioStatus.setAttribute('role','status');studioStatus.setAttribute('aria-live','polite');
const studioResult=node('img',null,'studio-result');studioResult.alt='AI-generated travel photo';studioResult.hidden=true;
const studioSave=node('button','Send to','studio-save');studioSave.type='button';studioSave.hidden=true;
const studioSaveHint=node('p',null,'studio-save-hint small');studioSaveHint.setAttribute('role','status');studioSaveHint.hidden=true;
const studioDownload=node('a','Download PNG','studio-download');studioDownload.hidden=true;studioDownload.download='photo-scout-ai-photo.png';
const studioPending=node('div',null,'studio-job-progress');studioPending.hidden=true;studioPending.setAttribute('role','status');studioPending.setAttribute('aria-live','polite');
studio.append(studioTop,studioPlace,studioPending,studioIntro,studioImages,uploadLabel,studioStyles,studioOptions,poseLabel,studioNote,studioGenerate,studioStatus,studioResult,studioSave,studioDownload,studioSaveHint);document.body.append(studio);
// This separate map layer survives shortlist/history redraws and dialog closure.
const selfieActivityLayer=L.layerGroup().addTo(map);
const studioTask=node('button',null,'selfie-task');studioTask.type='button';studioTask.hidden=true;studioTask.setAttribute('aria-live','polite');
const studioTaskSpark=node('span','✦','selfie-task-spark'),studioTaskCopy=node('span',null,'selfie-task-copy'),studioTaskLabel=node('strong'),studioTaskPlace=node('small');studioTaskCopy.append(studioTaskLabel,studioTaskPlace);studioTask.append(studioTaskSpark,studioTaskCopy);document.querySelector('.map-stage').append(studioTask);L.DomEvent.disableClickPropagation(studioTask);studioTask.addEventListener('click',()=>{if(taskRecords.some(t=>t.kind==='portrait'&&['queued','checking','running'].includes(t.state))){if(studio.open)studio.close();el('photo-history').open=true;}else studio.showModal();});
function setStudioActivity(state){
 const labels={uploading:'Uploading your photo…',queued:'Selfie queued…',checking:'Checking your photo…',running:'Creating your selfie…',reconnecting:'Reconnecting…',complete:'Your selfie is ready · View',failed:'Selfie needs attention · View'};
 studioTask.hidden=false;studioTask.dataset.state=state;studioTaskLabel.textContent=labels[state]||labels.running;studioTaskPlace.textContent=studioSpot?.name||'Photo studio';studioTask.setAttribute('aria-label',studioTaskLabel.textContent+' · '+studioTaskPlace.textContent+' · Open photo studio');
 const active=!['complete','failed'].includes(state);
 if(!active){selfieActivityLayer.clearLayers();studioActivityMarker=null;studioTaskSpark.textContent=state==='complete'?'✓':'!';return;}
 studioTaskSpark.textContent='✦';
 const pos=studioSpot?.poi;
 if(!studioActivityMarker&&pos&&Number.isFinite(pos.lat)&&Number.isFinite(pos.lon)){
  studioActivityMarker=L.marker([pos.lat,pos.lon],{title:'Selfie in progress · '+studioSpot.name,keyboard:true,zIndexOffset:1200,icon:L.divIcon({className:'selfie-activity-pin',html:'<span class="selfie-glow"></span><span class="selfie-star star-one">✦</span><span class="selfie-star star-two">✧</span><span class="selfie-star star-three">✦</span>',iconSize:[64,64],iconAnchor:[32,32]})}).addTo(selfieActivityLayer).bindTooltip(node('span',studioTaskLabel.textContent),{permanent:true,direction:'top',offset:[0,-30],className:'selfie-map-tooltip'}).on('click',()=>studio.showModal());
 }else if(studioActivityMarker)studioActivityMarker.setTooltipContent(node('span',studioTaskLabel.textContent));
}
function openPhotoStudio(spot){focusMapSpot(spot,{openPopup:false});if(savedPhoto.open)savedPhoto.close();if(portraitProgress.open)portraitProgress.close();if(studioBusy){studio.showModal();return;}if(!studioBusy){if(!taskRecords.some(t=>t.kind==='portrait'&&['queued','checking','running'].includes(t.state)))studioTask.hidden=true;studioSpot=spot;studioFraming.disabled=spot.provider!=='google-street-view';studioFraming.parentElement.hidden=spot.provider!=='google-street-view';studioPlace.textContent=spot.name+(photoBearing(spot).heading!=null?' · '+photoBearing(spot).label:'');scenePreview.removeAttribute('src');if(spot.provider==='google-street-view')refreshThumbnail(scenePreview,spot);else if(spot.imageUrl)scenePreview.src=spot.imageUrl;studioStatus.textContent='Upload a clear image of a person, cartoon character or animal. Groups are welcome; a clearly visible subject works best.';clearStudioOutput();studioGenerate.disabled=!studioPrepared;}syncStudioProgress();studio.showModal();}
studioClose.addEventListener('click',()=>studio.close());
studioUpload.addEventListener('change',()=>{const file=studioUpload.files[0];if(file)acceptStudioPhoto(file);});
async function acceptStudioPhoto(file){const generation=++studioUploadGeneration;studioFile=file||null;studioPrepared=null;personPreview.hidden=true;personPreview.removeAttribute('src');studioGenerate.disabled=true;if(studioPreviewUrl)URL.revokeObjectURL(studioPreviewUrl);if(!studioFile)return;if(studioFile.size>20000000){studioStatus.textContent='Choose a photo up to 20 MB.';studioFile=null;return;}if(!['image/jpeg','image/png','image/webp','image/heic','image/heif',''].includes(studioFile.type)&&!/\.(jpe?g|png|webp|heic|heif)$/i.test(studioFile.name)){studioStatus.textContent='Choose a JPG, PNG, WebP or HEIC photo.';studioFile=null;return;}studioStatus.textContent='Preparing your photo…';try{const prepared=await prepareStudioPhoto(studioFile);if(generation!==studioUploadGeneration)return;studioPrepared=prepared.data;personPreview.src=prepared.data;personPreview.hidden=!prepared.preview;studioGenerate.disabled=studioBusy;studioStatus.textContent=prepared.preview?'Your photo is ready. It has been resized for upload.':'Your phone photo will be converted securely when you submit. This browser cannot display its original format.';}catch(error){if(generation!==studioUploadGeneration)return;studioFile=null;studioStatus.textContent=error.message;}}
async function prepareStudioPhoto(file){const data=await readPhoto(file);return new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>{try{if(image.naturalWidth*image.naturalHeight>80000000)throw Error('This photo exceeds 80 megapixels. Export a smaller copy.');const scale=Math.min(1,2048/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);resolve({data:canvas.toDataURL('image/jpeg',.92),preview:true});}catch(error){reject(error);}};image.onerror=()=>resolve({data,preview:false});image.src=data;});}
function readPhoto(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('Could not read the photo'));reader.readAsDataURL(file);});}
studioGenerate.addEventListener('click',async()=>{
 if(!tasksReady||!studioPrepared||!studioSpot||studioBusy)return;studioBusy=true;studioGenerate.disabled=true;studioCameraButton.disabled=studioPersonFrame.disabled=studioUpload.disabled=true;studioPose.disabled=true;studioStyles.disabled=true;studioOptions.disabled=true;studioStatus.classList.add('working');studioStatus.textContent='Uploading your photo…';
 const spot=studioSpot,payload={portrait:studioPrepared,background:spot.provider==='google-street-view'?spot.sourceUrl:spot.imageUrl,provider:spot.provider,place:spot.name,pose:studioPose.value.trim(),style:studioStyle,posture:studioPosture.value,weather:studioWeather.value,expression:studioExpression.value,framing:studioFraming.value,lat:spot.poi?.lat??null,lon:spot.poi?.lon??null};
 try{const job=await json('/photo-scout/v1/portraits',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});addSubmittedTask('portrait',job,{...spot,generation:{style:payload.style,posture:payload.posture,weather:payload.weather,expression:payload.expression,framing:payload.framing,directions:payload.pose}});studioStatus.textContent='Your selfie is generating in the background. You can close this window or create another photo.';syncPortraitActivities();studioPending.scrollIntoView({block:'nearest',behavior:'smooth'});restoreTasks();}
 catch(error){studioStatus.textContent=error.message;}
 finally{studioBusy=false;studioGenerate.disabled=!studioPrepared;studioCameraButton.disabled=studioPersonFrame.disabled=studioUpload.disabled=false;studioPose.disabled=false;studioStyles.disabled=false;studioOptions.disabled=false;studioStatus.classList.remove('working');}
});
function finishStudio(error){studioBusy=false;studioJob=null;setStudioActivity(error?'failed':'complete');studioGenerate.disabled=!studioPrepared;studioCameraButton.disabled=studioPersonFrame.disabled=studioUpload.disabled=false;studioPose.disabled=false;studioStyles.disabled=false;studioOptions.disabled=false;studioStatus.classList.remove('working');studioStatus.textContent=error||'Your AI photo is ready. Lighting and placement are generated; this is not a record of a real visit.';restoreTasks();}

function clearStudioOutput(){
 studioOutputGeneration++;
 if(studioUrl)URL.revokeObjectURL(studioUrl);studioUrl=null;studioOutputFile=null;
 studioResult.hidden=true;studioResult.removeAttribute('src');studioSave.hidden=true;studioDownload.hidden=true;studioDownload.removeAttribute('href');studioSaveHint.hidden=true;
}
async function showStudioOutput(blob){
 clearStudioOutput();const generation=studioOutputGeneration;studioUrl=URL.createObjectURL(blob);
 // Prepare the actual file before the click, preserving native-share user activation.
 studioOutputFile=new File([blob],'photo-scout-ai-photo.png',{type:'image/png'});
 // Data URLs are allowed by the page image policy and support long-press saving.
 const preview=await readPhoto(blob);if(generation!==studioOutputGeneration)return;studioResult.src=preview;studioResult.hidden=false;studioDownload.href=studioUrl;studioDownload.hidden=false;studioSave.hidden=false;studioSaveHint.hidden=false;
 studioSaveHint.textContent='Tap Send to to choose an app or Save Image in the system menu. You can also press and hold the photo to save it. Downloads may go to Files on your device.';
}
studioSave.addEventListener('click',async()=>{
 if(!studioOutputFile)return;
 const files=[studioOutputFile];let supported=false;
 try{supported=typeof navigator.share==='function'&&typeof navigator.canShare==='function'&&navigator.canShare({files});}catch{}
 if(!supported){studioSaveHint.textContent='Press and hold the photo above, then choose Save to Photos / Save Image. Or use Download PNG; your browser may save it to Files or Downloads.';studioResult.scrollIntoView({block:'center',behavior:'smooth'});return;}
 studioSave.disabled=true;studioSaveHint.textContent='In your device’s share menu, choose Save Image / Photos, or a gallery app. The available options depend on your device.';
 try{await navigator.share({files});}
 catch(error){if(error.name!=='AbortError')studioSaveHint.textContent='The share menu could not open. Press and hold the photo to save it, or use Download PNG.';}
 finally{studioSave.disabled=false;}
});

async function submitDurableSearch(query){
 if(!tasksReady){message('Connecting your task session. Please try again shortly.');return;}
 searchBusy=true;invalidatePois();updateSubmitState();setProgress(0,'Saving your search…','Your search will continue on the server, even if you refresh or leave this page.');
 const payload={...coordinates(),query};
 try{activeSearch=await json('/photo-scout/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-Request-Token':crypto.randomUUID().replaceAll('-','')},body:JSON.stringify(payload)});activeSearch.context=payload;activeSearch.draft=searchDraft();addSubmittedTask('search',activeSearch,payload);searchBusy=false;updateSubmitState();await poll(activeSearch.jobId,activeSearch.reportToken);}
 catch(error){message(error.message);setProgress(0,'Search could not be started',error.message,'error');}
 finally{searchBusy=false;updateSubmitState();}
}
function fitSearchRange(context){
 if(!context)syncMapSelection();
 const radius=context?.radius||1000,latDelta=radius/111320,lonDelta=latDelta/Math.cos((context?.lat||0)*Math.PI/180);
 const bounds=context?[[context.lat-latDelta,context.lon-lonDelta],[context.lat+latDelta,context.lon+lonDelta]]:searchArea.getBounds();
 const dockHeight=document.querySelector('.scout-dock').getBoundingClientRect?.().height||120;
 map.fitBounds(bounds,{paddingTopLeft:[24,80],paddingBottomRight:[24,Math.ceil(dockHeight)+24],maxZoom:18});
}
function applyTaskContext(context){
 locationSelectionRevision++;
 if(!Number.isFinite(context.lat)||!Number.isFinite(context.lon))return;
 const moved=Number(el('lat').value)!==context.lat||Number(el('lon').value)!==context.lon||(context.radius&&Number(el('radius').value)!==context.radius);
 el('lat').value=String(context.lat);el('lon').value=String(context.lon);selected.setLatLng([context.lat,context.lon]);
 if(context.radius){const value=String(context.radius);if(![...el('radius').options].some(o=>o.value===value)){const option=node('option',context.radius+' m');option.value=value;el('radius').append(option);}el('radius').value=value;}
 if(typeof context.query==='string')el('prompt-query').value=context.query;
 el('style-options').querySelectorAll('input').forEach(i=>i.checked=context.photoStyles?.length?context.photoStyles.includes(i.value):i.value==='any');
 syncMapSelection();updateParameterSummary();if(moved)fitSearchRange();
 const checkingPoints=context.sampledViewLocations||context.nearbyPois,scanKey=JSON.stringify(checkingPoints);
 if(checkingPoints&&poiCatalog?.scanKey!==scanKey){stopPoiScan();poiCatalog={nearbyPois:context.nearbyPois||[],coords:context,scanKey};candidatePoiLayer.clearLayers();for(const p of checkingPoints)L.circleMarker([p.lat,p.lon],{radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9}).bindTooltip(node('span',p.name)).addTo(candidatePoiLayer);}
}
function focusHistorySearch(context,result,history){
 if(!result){focusedSearchId=null;drawHistoryMap();}
 let area=context||result?.searchContext;
 if(!area||!Number.isFinite(area.lat)||!Number.isFinite(area.lon)){
  const match=history?.label.match(/^Around\s+(-?[\d.]+),\s*(-?[\d.]+)/);
  if(match)area={lat:Number(match[1]),lon:Number(match[2]),radius:history.radius};
 }
 if(area&&Number.isFinite(area.lat)&&Number.isFinite(area.lon)){
  applyTaskContext({...area,radius:area.radius||history?.radius||1000,nearbyPois:undefined});el('prompt-query').value=area.query||'';
  // Always refit, including repeated View clicks after manually panning away.
  map.fitBounds(searchArea.getBounds(),{paddingTopLeft:[24,72],paddingBottomRight:[24,140],maxZoom:18});
 }else{
  const places=allPoiViews(result||{}).filter(s=>Number.isFinite(s.poi?.lat)&&Number.isFinite(s.poi?.lon));
  if(places.length)map.fitBounds(L.featureGroup(places.map(s=>L.circleMarker([s.poi.lat,s.poi.lon]))).getBounds().pad(.15),{padding:[40,40],maxZoom:16});
 }
}
function showHistorySearch(result,context,history){
 el('search-progress').hidden=true;el('message').textContent='';
 activeSearch=null;stopPoiScan();focusedSearchId=history?.id||searchHistory.find(h=>h.result===result)?.id||null;
 el('search-history').open=false;
 if(history&&!history.checked){history.checked=true;persistHistory();renderHistory();}
 render(result,{save:false,mapUpdate:false,historyId:history?.id});drawHistoryMap();
 // Show the recorded area together with its complete shortlist.
 el('results').hidden=false;
 focusHistorySearch(context,result,history);
}
const savedPhoto=node('dialog',null,'photo-studio saved-photo');savedPhoto.setAttribute('aria-label','Saved photo');
const savedPhotoTop=node('div',null,'studio-heading'),savedPhotoTitle=node('h2','Your saved selfie'),savedPhotoClose=node('button','Close ×');savedPhotoClose.type='button';savedPhotoTop.append(savedPhotoTitle,savedPhotoClose);
const savedPhotoPlace=node('p',null,'small'),savedPhotoImage=node('img',null,'studio-result'),savedPhotoStatus=node('p',null,'studio-status'),savedPhotoParams=node('div',null,'saved-photo-params'),savedPhotoSave=node('button','Send to','studio-save'),savedPhotoDownload=node('a','Download PNG','studio-save studio-download'),savedPhotoHint=node('p',null,'small');savedPhotoImage.alt='Saved AI-generated travel photo';savedPhotoStatus.setAttribute('role','status');savedPhotoDownload.download='photo-scout-ai-photo.png';
const savedPhotoBackground=node('section',null,'saved-photo-background');
const savedPhotoNav=node('nav',null,'saved-photo-nav');savedPhotoNav.setAttribute('aria-label','Navigate to this photo location');
savedPhoto.append(savedPhotoTop,savedPhotoPlace,savedPhotoImage,savedPhotoStatus,savedPhotoBackground,savedPhotoSave,savedPhotoDownload,savedPhotoHint);document.body.append(savedPhoto);
let savedPhotoGeneration=0,savedPhotoTimer=null,savedPhotoFile=null,savedPhotoUrl=null;
function savedPhotoNavigation(context){
 const {lat,lon}=context?.poi||{};
 if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180)return [];
 const destination=lat+','+lon;
 return [['Navigate here · Google Maps','https://www.google.com/maps/dir/?api=1&destination='+encodeURIComponent(destination)],['Apple Maps','https://maps.apple.com/?daddr='+encodeURIComponent(destination)]];
}
function renderSavedPhotoParams(context,created){
 const pos=context?.poi,place=node('button','⌖ '+(context?.name||'Show background on map'),'photo-place-link');place.type='button';place.title='Show background location on map';place.disabled=!pos||![pos.lat,pos.lon].every(Number.isFinite);place.addEventListener('click',()=>{savedPhoto.close();focusPhotoPlace({context});});
 savedPhotoBackground.replaceChildren(node('h3','Background info'),place,node('p',photoBackgroundInfo(context||{}),'small'));
 if(pos&&[pos.lat,pos.lon].every(Number.isFinite))savedPhotoBackground.append(node('p',`${pos.lat.toFixed(5)}, ${pos.lon.toFixed(5)}`,'small'));
 if(context?.sourceUrl)savedPhotoBackground.append(link(context.provider==='google-street-view'?'Open original Street View ↗':'Open original photo ↗',context.sourceUrl));
 const routes=savedPhotoNavigation(context);savedPhotoNav.replaceChildren(...routes.map(([label,url])=>link(label,url)));savedPhotoNav.hidden=!routes.length;
 savedPhotoPlace.textContent=(context?.name||'Saved selfie')+(context?.viewHeadingDegrees!=null?' · '+photoBearing(context).label:'');
 const fields=[['Created',new Date(created*1000).toLocaleString()]];
 if(context?.generation&&Object.keys(context.generation).length){const g=context.generation,label=(select,value)=>[...select.options].find(o=>o.value===value)?.textContent||value;
  fields.push(['Style',portraitStyles.find(s=>s[0]===g.style)?.[1]||g.style],['Posture',label(studioPosture,g.posture)],['Weather & light',label(studioWeather,g.weather)],['Expression',label(studioExpression,g.expression)],['Street View framing',label(studioFraming,g.framing||'auto')],['Your directions',g.directions||'None']);
 }
 if(context?.backgroundPreparation){const p=context.backgroundPreparation;fields.push(['Background framing',`${p.fovDegrees}° field of view · ${p.distortion} distortion`],['Background selection',p.reason]);}
 const list=node('dl');for(const [label,value] of fields.filter(([,value])=>value!==undefined&&value!==null))list.append(node('dt',label),node('dd',value));
 savedPhotoParams.replaceChildren(list,...(context?.generation?[]:[node('p','The generation settings were not saved for this older photo.','small')]));
 savedPhotoBackground.append(savedPhotoNav,savedPhotoParams);
}
// Progress and completed-photo viewing have independent dialogs and requests.
const portraitProgress=node('dialog',null,'photo-studio portrait-progress');portraitProgress.setAttribute('aria-label','Selfie progress');
const portraitProgressTop=node('div',null,'studio-heading'),portraitProgressTitle=node('h2','Creating your selfie'),portraitProgressClose=node('button','Close ×'),portraitProgressPlace=node('p',null,'small'),portraitProgressStatus=node('p',null,'studio-status');portraitProgressClose.type='button';portraitProgressStatus.setAttribute('role','status');portraitProgressTop.append(portraitProgressTitle,portraitProgressClose);portraitProgress.append(portraitProgressTop,portraitProgressPlace,portraitProgressStatus,node('p','This task continues in the background. You can close this window and view other photos in History.','small'));document.body.append(portraitProgress);
let portraitProgressGeneration=0,portraitProgressTimer=null;
portraitProgressClose.addEventListener('click',()=>portraitProgress.close());portraitProgress.addEventListener('close',()=>{if(portraitProgress.open)return;portraitProgressGeneration++;clearTimeout(portraitProgressTimer);});
async function viewPortraitProgress(task){
 if(studio.open)studio.close();if(savedPhoto.open)savedPhoto.close();
 const generation=++portraitProgressGeneration;clearTimeout(portraitProgressTimer);portraitProgressPlace.textContent=task.context?.name||'Selfie';portraitProgressStatus.textContent='Loading task progress…';if(!portraitProgress.open)portraitProgress.showModal();
 const refresh=async()=>{try{
  const report=await json('/photo-scout/v1/portraits/'+encodeURIComponent(task.id));if(generation!==portraitProgressGeneration)return;
  portraitProgressPlace.textContent=report.context?.name||task.context?.name||'Selfie';
  if(report.state==='complete'){portraitProgressStatus.textContent='Your selfie is ready. Open View photo in History to see it.';restoreTasks();return;}
  if(report.state==='failed'){portraitProgressStatus.textContent=report.error||'This photo could not be created.';return;}
  portraitProgressStatus.textContent=report.state==='queued'?'Your selfie is queued…':report.state==='checking'?'Checking your uploaded subject…':'Creating your selfie…';
 }catch(error){if(generation!==portraitProgressGeneration)return;portraitProgressStatus.textContent='Could not refresh progress. Reconnecting…';}
 portraitProgressTimer=setTimeout(refresh,4000);};await refresh();
}
async function viewSavedPhoto(task,publication=null){
 focusMapSpot(publication?.context||task.context,{openPopup:false});
 if(!publication&&['queued','checking','running'].includes(task.state)){await viewPortraitProgress(task);return;}
 delete savedPhoto.dataset.publication;delete savedPhoto.dataset.photoId;
 const owned=publication?[...ownPublications.values()].find(p=>p.id===publication.id):null;
 const sourceId=publication?owned?.sourceId:task.id;
 if(publication)savedPhoto.dataset.publication=publication.id;
 if(sourceId)savedPhoto.dataset.photoId=sourceId;
 savedPhotoSharing.hidden=true;savedPhotoTitle.textContent='Photo';
 savedPhotoPublish.replaceChildren(...(sourceId?[publishButton('photo',sourceId)]:[]));
 if(studio.open)studio.close();if(portraitProgress.open)portraitProgress.close();
 const generation=++savedPhotoGeneration;clearTimeout(savedPhotoTimer);if(savedPhotoUrl)URL.revokeObjectURL(savedPhotoUrl);savedPhotoUrl=null;savedPhotoFile=null;
 savedPhotoImage.hidden=true;savedPhotoImage.removeAttribute('src');savedPhotoSave.hidden=true;savedPhotoDownload.hidden=true;savedPhotoHint.textContent='';savedPhotoStatus.textContent='Loading your saved photo…';renderSavedPhotoParams(task.context,task.created);if(!savedPhoto.open)savedPhoto.showModal();
 const refresh=async()=>{try{
  const report=publication?{state:'complete',context:publication.context}:await json('/photo-scout/v1/portraits/'+encodeURIComponent(task.id));if(generation!==savedPhotoGeneration)return;
  renderSavedPhotoParams(report.context||task.context,task.created);focusMapSpot(report.context||task.context,{openPopup:false});
  if(report.state==='complete'){
   const response=await fetch(publication?publication.imageUrl:api+'/photo-scout/v1/portraits/'+encodeURIComponent(task.id)+'/image',{credentials:'include'});if(!response.ok)throw Error('Could not load your saved photo');const blob=await response.blob(),preview=await readPhoto(blob);if(generation!==savedPhotoGeneration)return;
   savedPhotoFile=new File([blob],'photo-scout-ai-photo.png',{type:'image/png'});savedPhotoUrl=URL.createObjectURL(blob);savedPhotoImage.src=preview;savedPhotoImage.hidden=false;savedPhotoSave.hidden=false;savedPhotoDownload.href=savedPhotoUrl;savedPhotoDownload.hidden=false;savedPhotoStatus.textContent=publication?'AI-generated photo · Published publicly.':task.expiresAt===null||authUser?'AI-generated photo · Saved permanently to your account.':'AI-generated photo · Guest photo kept until 7 days after your last visit.';savedPhotoHint.textContent='Use Send to to choose an app or save the image. You can also press and hold the photo to save it.';renderPhotoSharing(report.context||task.context);return;
  }
  if(report.state==='failed'){savedPhotoStatus.textContent=report.error||'This photo could not be created.';return;}
  savedPhotoStatus.textContent=report.state==='queued'?'Your selfie is queued…':'Your selfie is being created…';savedPhotoTimer=setTimeout(refresh,4000);
 }catch(error){if(generation===savedPhotoGeneration)savedPhotoStatus.textContent=error.message;}};
 await refresh();
}
savedPhotoClose.addEventListener('click',()=>savedPhoto.close());savedPhoto.addEventListener('close',()=>{if(savedPhoto.open)return;savedPhotoGeneration++;photoShareDialog.close();clearTimeout(savedPhotoTimer);if(savedPhotoUrl)URL.revokeObjectURL(savedPhotoUrl);savedPhotoUrl=null;savedPhotoFile=null;savedPhotoImage.removeAttribute('src');});
savedPhotoSave.addEventListener('click',async()=>{
 if(!savedPhotoFile)return;
 const files=[savedPhotoFile];let supported=false;try{supported=typeof navigator.share==='function'&&typeof navigator.canShare==='function'&&navigator.canShare({files});}catch{}
 if(!supported){savedPhotoHint.textContent='Press and hold the photo to save it, or use Download PNG.';savedPhotoImage.scrollIntoView({block:'center',behavior:'smooth'});return;}
 savedPhotoSave.disabled=true;try{await navigator.share({files});}catch(error){if(error.name!=='AbortError')savedPhotoHint.textContent='The share menu could not open. Use Download PNG or press and hold the photo.';}finally{savedPhotoSave.disabled=false;}
});
async function viewSavedTask(task){
 el('search-history').open=false;el('photo-history').open=false;
 if(task.kind==='search')el('results').hidden=true;
 if(task.kind==='portrait'){await viewSavedPhoto(task);return;}
 try{
  const report=await json('/photo-scout/v1/report/'+encodeURIComponent(task.id)),context=report.context||task.context;
  if(report.state==='complete'){let history=searchHistory.find(h=>h.id===task.id);if(!history){history={id:task.id,created:task.created*1000,label:context.query||context.locationLabel||`Around ${context.lat}, ${context.lon}`,radius:context.radius||1000,checked:true,result:{...report.result,searchContext:context}};searchHistory.push(history);persistHistory();renderHistory();}showHistorySearch(history.result,context,history);return;}
  focusHistorySearch(context,null,null);
  if(report.state==='failed')setProgress(2,'Search could not be completed',report.error||'Please try a new search.','error');
  else{activeSearch={jobId:task.id,context,draft:searchDraft()};restoreTasks();}
 }
 catch(error){message(error.message);}
}
function syncStudioProgress(){
 const related=taskRecords.filter(task=>task.kind==='portrait'&&samePhotoPlace(studioSpot||{},task.context||{})).sort((a,b)=>b.created-a.created),task=related.find(t=>['queued','checking','running'].includes(t.state))||related[0];
 studioPending.hidden=!task;if(!task)return;studioPending.dataset.state=task.state;const pending=['queued','checking','running'].includes(task.state);
 const title=pending?(task.state==='queued'?'Your selfie is queued…':task.state==='checking'?'Checking your uploaded photo…':'Creating your selfie…'):(task.state==='complete'?'Your selfie is ready':'Photo generation failed');
 const icon=node('span',pending?'✦':task.state==='complete'?'✓':'!','studio-progress-icon'),copy=node('div');copy.append(node('strong',title),node('p',pending?'You can close this window. Generation continues in the background.':task.error||(task.state==='complete'?'Open your finished photo below.':'Please try again.'),'small'));const view=node('button',pending?'View progress':'View photo');view.type='button';view.addEventListener('click',()=>viewSavedPhoto(task));studioPending.replaceChildren(icon,copy,view);
}
function syncPortraitActivities(){
 syncStudioProgress();
 const photos=taskRecords.filter(t=>t.kind==='portrait'&&['queued','checking','running'].includes(t.state));
 selfieActivityLayer.clearLayers();studioActivityMarker=null;if(!photos.length){studioTask.hidden=true;return;}
 studioTask.hidden=false;studioTask.dataset.state='running';studioTaskSpark.textContent='✦';studioTaskLabel.textContent=photos.length===1?'Selfie in progress · History':photos.length+' selfies in progress · History';studioTaskPlace.textContent=photos.length===1?photos[0].context?.name||'Photo studio':'View each task’s progress';studioTask.setAttribute('aria-label',studioTaskLabel.textContent);
 for(const task of photos){const spot=task.context,pos=spot?.poi;if(!Number.isFinite(pos?.lat)||!Number.isFinite(pos?.lon))continue;
  L.marker([pos.lat,pos.lon],{title:'Selfie in progress · '+spot.name,zIndexOffset:1200,icon:L.divIcon({className:'selfie-activity-pin',html:'<span class="selfie-glow"></span><span class="selfie-star star-one">✦</span><span class="selfie-star star-two">✧</span>',iconSize:[64,64],iconAnchor:[32,32]})}).addTo(selfieActivityLayer).bindTooltip(node('span',spot.name+' · '+(task.state==='queued'?'Queued':'Creating selfie…')),{direction:'top',className:'selfie-map-tooltip'}).on('click',()=>viewSavedPhoto(task));
 }
}
function resetTaskRecovery(){
 if(studioCamera?.dialog.open)studioCamera.dialog.close();studioCamera?.stop();
 if(savedPhoto.open)savedPhoto.close();if(portraitProgress.open)portraitProgress.close();
 focusedSearchId=null;selectedPoiView=null;lastRemovedPoi=null;lastRemovedHistory=null;removedHistoryItems.clear();poiViewOverrides.clear();taskRecoveryGeneration++;hydratedSearches.clear();clearTimeout(taskRefreshTimer);taskRecords=[];hiddenPoisBySearch.clear();displayedResult=null;displayedSearchId=null;tasksReady=false;activeSearch=null;pollGeneration++;searchBusy=false;studioBusy=false;studioJob=null;clearTimeout(studioTimer);clearStudioOutput();studioPrepared=null;studioFile=null;studioUpload.value='';studioCameraButton.disabled=studioPersonFrame.disabled=studioUpload.disabled=false;studioPose.disabled=false;studioStyles.disabled=false;studioOptions.disabled=false;studioGenerate.disabled=true;personPreview.hidden=true;personPreview.removeAttribute('src');studioTask.hidden=true;selfieActivityLayer.clearLayers();renderHistory();updateSubmitState();
}
async function restoreTasks(){
 if(taskRefreshBusy)return;taskRefreshBusy=true;const generation=taskRecoveryGeneration,initial=!tasksReady;let changed=false;
 try{
  const visiting=!document.hidden;const data=await json('/photo-scout/v1/tasks'+(visiting?'?visit=true':''));if(visiting){try{sessionStorage.setItem(HISTORY_KEY+'-last-visit',String(Date.now()));}catch{}}if(generation!==taskRecoveryGeneration)return;tasksReady=true;data.items=data.items.filter(t=>!removedHistoryItems.has(t.kind+':'+t.id));
  const incomingHidden=new Map(Object.entries(data.hiddenPois||{}).map(([id,keys])=>[id,new Set(keys)]));
  if(JSON.stringify([...incomingHidden].map(([id,keys])=>[id,[...keys]]))!==JSON.stringify([...hiddenPoisBySearch].map(([id,keys])=>[id,[...keys]]))){hiddenPoisBySearch=incomingHidden;changed=true;refreshDisplayedShortlist();}
  const returned=new Set(data.items.map(t=>t.kind+':'+t.id));taskRecords=[...data.items,...taskRecords.filter(t=>t.localPending&&!returned.has(t.kind+':'+t.id))].sort((a,b)=>b.created-a.created);renderHistory();refreshVisiblePlaceSelfies();
  for(const task of taskRecords.filter(t=>t.kind==='search'&&t.state==='complete')){
   if(hydratedSearches.has(task.id))continue;
   const report=await json('/photo-scout/v1/report/'+encodeURIComponent(task.id));if(generation!==taskRecoveryGeneration)return;
   if(report.result&&!removedHistoryItems.has('search:'+task.id)){changed=true;hydratedSearches.add(task.id);const existing=searchHistory.find(h=>h.id===task.id),record={id:task.id,created:task.created*1000,label:task.context.query||task.context.locationLabel||`Around ${task.context.lat}, ${task.context.lon}`,radius:task.context.radius||1000,checked:existing?.checked??true,result:{...report.result,searchContext:task.context}};if(existing)Object.assign(existing,record);else searchHistory.push(record);}
  }
  searchHistory.sort((a,b)=>b.created-a.created);if(changed){persistHistory();drawHistoryMap();}renderHistory();syncPortraitActivities();refreshVisiblePlaceSelfies();
  if(initial&&!activeSearch){const task=taskRecords.find(t=>t.kind==='search'&&['queued','running'].includes(t.state));if(task){activeSearch={jobId:task.id,context:task.context};applyTaskContext(task.context);activeSearch.draft=searchDraft();}}
  const focus=taskRecords.find(t=>t.kind==='search'&&t.id===activeSearch?.jobId);
  if(focus){
   const follow=!activeSearch.draft||activeSearch.draft===searchDraft();activeSearch.context=focus.context;
   if(follow){applyTaskContext(focus.context);activeSearch.draft=searchDraft();}
   if(focus.state==='complete'){
    const h=searchHistory.find(h=>h.id===focus.id);if(h){focusedSearchId=h.id;drawHistoryMap();render(h.result,{save:false,mapUpdate:false});el('results').hidden=false;fitSearchRange(focus.context);setProgress(2,'Your shortlist is ready','Results are shown in Shortlist and saved in Search history.','complete');}activeSearch=null;stopPoiScan();
   }else if(focus.state==='failed'){setProgress(2,'Search could not be completed',focus.error||'View details in History.','error');activeSearch=null;stopPoiScan();}
   else if(follow){const stage=focus.context?.stage;setProgress(stage==='scoring'?2:(stage==='sources'||stage==='exploring')?1:0,focus.state==='queued'?'Your search is queued…':stage==='scoring'?'Reviewing & ranking photos…':stage==='exploring'?'Exploring viewpoints…':stage==='sources'?'Finding nearby places & photos…':'Understanding your request…','Progress is saved in History. You can start another search.','running',true);if(stage==='scoring')startPoiScan();}
  }
 }catch{if(!tasksReady)el('history-note').textContent='Could not reconnect to saved tasks. Retrying shortly.';}
 finally{taskRefreshBusy=false;updateSubmitState();clearTimeout(taskRefreshTimer);const pending=taskRecords.some(t=>['queued','running','checking'].includes(t.state));taskRefreshTimer=setTimeout(restoreTasks,document.hidden?(pending?15000:60000):(pending?5000:15000));}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){restoreTasks();}});

function refreshMapViewport(){if(document.hidden)return;map.invalidateSize?.({pan:false});if(vectorLayer){vectorMoveHandler?.();const gl=vectorLayer.getMaplibreMap();gl.resize?.();gl.triggerRepaint?.();}}
window.addEventListener('resize',refreshMapViewport);window.addEventListener('pageshow',refreshMapViewport);
document.addEventListener('visibilitychange',refreshMapViewport);
if(window.ResizeObserver)new window.ResizeObserver(refreshMapViewport).observe(el('map'));

// Explicit publication creates a public snapshot; private histories stay private.
const publicationLayer=L.layerGroup().addTo(map);
overlayControl.addOverlay(publicationLayer,'Published places & selfies');
const savedPhotoPublish=node('div',null,'publication-actions');savedPhoto.append(savedPhotoPublish);
const savedPhotoSharing=node('button','Share','studio-save photo-share-trigger');savedPhotoSharing.type='button';savedPhotoSharing.hidden=true;savedPhoto.append(savedPhotoSharing);
const photoShareDialog=node('dialog',null,'photo-studio photo-share-dialog');photoShareDialog.setAttribute('aria-label','Share photo');document.body.append(photoShareDialog);
photoShareDialog.addEventListener('click',event=>{if(event.target===photoShareDialog){const r=photoShareDialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)photoShareDialog.close();}});
function socialShareIcon(kind){
 const paths={wechat:'<path d="M13 4C6.9 4 2 7.8 2 12.5c0 2.6 1.5 4.9 4 6.5l-1 3 4-2c1.2.4 2.6.6 4 .6 6.1 0 11-3.8 11-8.1S19.1 4 13 4Z"/><path d="M23 14c-4.4 0-8 2.9-8 6.5s3.6 6.5 8 6.5c1 0 2-.2 3-.5l3 1.5-.8-2.4c1.8-1.2 2.8-3 2.8-5.1S27.4 14 23 14Z"/><circle cx="9" cy="11" r="1" fill="currentColor" stroke="none"/><circle cx="17" cy="11" r="1" fill="currentColor" stroke="none"/>',facebook:'<path d="M20 5h-4c-4 0-6 2-6 6v4H6v5h4v12h6V20h4l1-5h-5v-4c0-1.4.6-2 2-2h2Z" fill="currentColor" stroke="none"/>',x:'<path d="M6 5h6l16 24h-6ZM27 5 5 29"/>',more:'<path d="M16 23V4m-6 6 6-6 6 6M8 16H5v14h22V16h-3"/>',copy:'<rect x="11" y="11" width="16" height="18" rx="3"/><path d="M21 7V4H5v19h3"/>',download:'<path d="M16 4v18m-6-6 6 6 6-6M5 24v6h22v-6"/>'};
 const icon=node('span',null,'social-icon social-'+kind);icon.setAttribute('aria-hidden','true');icon.innerHTML='<svg viewBox="0 0 34 34" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'+paths[kind]+'</svg>';return icon;
}
function photoSocialLinks(url,text){
 return [['Facebook','https://www.facebook.com/sharer/sharer.php?u='+encodeURIComponent(url)],['X','https://x.com/intent/tweet?url='+encodeURIComponent(url)+'&text='+encodeURIComponent(text)]];
}
function renderPhotoSharing(context){
 const generation=savedPhotoGeneration,photoId=savedPhoto.dataset.photoId,publicId=savedPhoto.dataset.publication;
 const current=()=>generation===savedPhotoGeneration;
 const publicItem=()=>publicId?{url:location.origin+'/photo-scout/?published='+encodeURIComponent(publicId)}:ownPublications.get(publicationKey('photo',photoId));
 const text='My AI-generated selfie at '+(context?.name||'a photo spot')+' · Photo Scout';
 const heading=node('div',null,'studio-heading'),close=node('button','Close ×'),body=node('div',null,'photo-share-body'),status=node('p',null,'small'),note=node('p',null,'small');close.type='button';close.onclick=()=>photoShareDialog.close();heading.append(node('h2','Share photo'),close);status.setAttribute('role','status');
 const destinations=node('div',null,'photo-social-links social-destinations');destinations.setAttribute('aria-label','Share destination');
 const buttons=[];let busy=false;
 async function ensurePublic(){
  const existing=publicItem();if(existing)return existing;
  if(!photoId)throw Error('This photo is not available to share.');
  const item=await json('/photo-scout/v1/publications',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({kind:'photo',id:photoId})});
  ownPublications.set(publicationKey('photo',photoId),item);syncPublishButtons();return item;
 }
 function refresh(){note.textContent=publicItem()?'Shares your public photo link. WeChat copies the link and attempts to open the app; paste it in your chat.':'Choosing a destination publishes this photo and its background info on Photo Scout, then shares its link. WeChat: copy link, open app, paste in chat.';}
 for(const [kind,label] of [['wechat','WeChat'],['facebook','Facebook'],['x','X']]){
  const button=node('button',null,'social-tile');button.type='button';button.append(socialShareIcon(kind),node('strong',label),node('small',kind==='wechat'?'Copy link & open':'Open post'));buttons.push(button);destinations.append(button);
  button.onclick=async()=>{
   if(!current()||busy)return;busy=true;buttons.forEach(b=>b.disabled=true);status.textContent='Preparing your photo link…';
   // Reserve social composers inside the user gesture, before publishing awaits.
   const target=kind==='wechat'?null:window.open('about:blank','_blank');if(target)target.opener=null;
   const pendingItem=ensurePublic();let clipboardResult;
   if(kind==='wechat'){
    // Safari requires initiating clipboard write inside the original click.
    try{clipboardResult=typeof ClipboardItem!=='undefined'&&typeof navigator.clipboard?.write==='function'
      ?navigator.clipboard.write([new ClipboardItem({'text/plain':pendingItem.then(item=>new Blob([item.url],{type:'text/plain'}))})])
      :pendingItem.then(item=>{if(!navigator.clipboard?.writeText)throw Error('Clipboard unavailable');return navigator.clipboard.writeText(item.url);});
     clipboardResult=clipboardResult.then(()=>true,()=>false);
    }catch{clipboardResult=Promise.resolve(false);}
   }
   try{
    const item=await pendingItem;
    if(!current()){if(target&&!target.closed)target.close();return;}
    if(kind==='wechat'){
     const copied=await clipboardResult;if(!current())return;
     if(!copied){status.replaceChildren(node('span','Copy this link, then open WeChat: '),link(item.url,item.url));return;}
     status.textContent='Link copied. Opening WeChat… Paste the link into your chat. If it does not open, open WeChat yourself.';
     // Launch only; ordinary websites cannot prefill a WeChat conversation.
     window.location.assign('weixin://');
    }else{
     const url=photoSocialLinks(item.url,text).find(([name])=>name===label)[1];
     if(target&&!target.closed)target.location.replace(url);else window.location.assign(url);
     photoShareDialog.close();
    }
   }catch(error){if(target&&!target.closed)target.close();if(current())status.textContent=error.message;}
   finally{busy=false;buttons.forEach(b=>b.disabled=false);refresh();}
  };
 }
 const preview=node('div',null,'share-photo-preview'),thumb=node('img'),description=node('div');thumb.src=savedPhotoImage.src;thumb.alt='Photo to share';description.append(node('strong',context?.name||'Your photo'),node('span','AI-generated travel photo'));preview.append(thumb,description);
 body.append(destinations,note,status);photoShareDialog.replaceChildren(heading,preview,body);savedPhotoSharing.hidden=false;
 savedPhotoSharing.refresh=refresh;savedPhotoSharing.onclick=()=>{if(!current())return;refresh();if(!photoShareDialog.open)photoShareDialog.showModal();buttons[0].focus();};refresh();
}

const publicationDialog=node('dialog',null,'photo-studio publication-dialog');publicationDialog.setAttribute('aria-label','Publication link');document.body.append(publicationDialog);
function showPublicationLink(item){
 const heading=node('div',null,'studio-heading'),close=node('button','Close ×');close.type='button';close.addEventListener('click',()=>publicationDialog.close());heading.append(node('h2','Published'),close);
 const url=link(item.url,item.url);url.className='publication-url';
 const copy=node('button','Copy link','studio-save');copy.type='button';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(item.url);copy.textContent='Link copied';}catch{copy.textContent='Select the link above to copy it';}});
 publicationDialog.replaceChildren(heading,node('p','Anyone can view this publication, including visitors who are not signed in.','small'),url,copy);if(!publicationDialog.open)publicationDialog.showModal();
}
function publicationKey(kind,id,poi=''){return JSON.stringify([kind,id,poi]);}
function syncPublishButtons(){for(const button of document.querySelectorAll('button[data-publication-key]')){const published=ownPublications.has(button.dataset.publicationKey);button.textContent=published?'Published ✓ · Unpublish':button.dataset.publishLabel;button.setAttribute('aria-pressed',String(published));}savedPhotoSharing.refresh?.();}
function publishButton(kind,id,spot){
 const label='Publish'+(kind==='place'?' place':kind==='photo'?' photo':''),button=node('button',label,'publish-button');button.type='button';button.hidden=!id||id.startsWith('public:');button.dataset.publishLabel=label;button.dataset.publicationKey=publicationKey(kind,id,spot?poiHistoryKey(spot):'');button.setAttribute('aria-pressed',String(ownPublications.has(button.dataset.publicationKey)));if(ownPublications.has(button.dataset.publicationKey))button.textContent='Published ✓ · Unpublish';
 button.onclick=async()=>{const existing=ownPublications.get(button.dataset.publicationKey);button.disabled=true;button.textContent=existing?'Unpublishing…':'Publishing…';try{
  if(existing){await json('/photo-scout/v1/publications/withdraw',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({id:existing.id})});}
  else{const item=await json('/photo-scout/v1/publications',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({kind,id,...(spot?{poiId:poiHistoryKey(spot)}:{})})});showPublicationLink(item);}
  await loadPublications();
 }catch(error){message(error.message);if(savedPhoto.open)savedPhotoStatus.textContent=error.message;}finally{button.disabled=false;syncPublishButtons();}};return button;
}
async function loadPublications(more=false){
 try{const [data,mine]=await Promise.all([json('/photo-scout/v1/publications'+(more&&publicationCursor?'?before='+publicationCursor:'')),json('/photo-scout/v1/publications/mine')]);ownPublications=new Map(mine.items.map(item=>[publicationKey(item.kind,item.sourceId,item.poiId),item]));syncPublishButtons();const selection=new Map(publicationItems.map(item=>[item.id,item.checked]));const incoming=data.items.map(item=>({...item,checked:selection.get(item.id)!==false}));publicationItems=more?[...publicationItems,...incoming]:incoming;publicationCursor=data.nextBefore;renderPublications();drawPublications();}
 catch{el('published-items').replaceChildren(node('p','Could not load published items. Reopen this menu to retry.','small'));}
}
function renderPublications(){
 el('published-count').textContent=publicationItems.length;el('published-more').hidden=!publicationCursor;const root=el('published-items');root.replaceChildren();
 if(!publicationItems.length)root.append(node('p','Nothing published yet. Use Publish in Search history, a photo place, or a saved photo.','small'));
 for(const item of publicationItems){const row=node('div',null,'history-item'),copy=node('span',null,'history-copy'),check=node('input');check.type='checkbox';check.checked=item.checked!==false;check.setAttribute('aria-label','Show publication: '+item.title);check.addEventListener('change',()=>{item.checked=check.checked;drawPublications();});
 const text=node('span');text.append(node('strong',item.title),node('small',item.kind==='photo'?'AI selfie':item.kind==='place'?'Photo place':'Search · '+(item.result?.spots?.length||0)+' places'));copy.append(check,text);
 const actions=node('div',null,'history-entry-actions'),view=node('button','View');view.type='button';view.addEventListener('click',()=>openPublication(item));actions.append(view,link('Share ↗',item.url));
 if(item.mine){const withdraw=node('button','Unpublish');withdraw.type='button';withdraw.addEventListener('click',async()=>{withdraw.disabled=true;try{await json('/photo-scout/v1/publications/withdraw',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({id:item.id})});if(displayedSearchId==='public:'+item.id){el('results').hidden=true;displayedResult=null;}if(savedPhoto.dataset.publication===item.id)savedPhoto.close();await loadPublications();}catch(error){withdraw.disabled=false;message(error.message);}});actions.append(withdraw);}
 row.append(copy,actions);root.append(row);}
}
function publishedSelfieIcon(item){return L.divIcon({className:'published-selfie-pin',html:'<span><img src="'+api+'/photo-scout/v1/publications/'+encodeURIComponent(item.id)+'/thumbnail" alt=""><b>✦</b></span>',iconSize:[48,58],iconAnchor:[24,55],popupAnchor:[0,-48]});}
function drawPublications(){
 publicationLayer.clearLayers();for(const item of publicationItems.filter(i=>i.checked!==false)){const spots=item.kind==='photo'?[item.context]:item.result?.spots||[];for(const [index,spot] of spots.entries()){const pos=spot?.poi;if(!pos||![pos.lat,pos.lon].every(Number.isFinite))continue;
 const marker=L.marker([pos.lat,pos.lon],{zIndexOffset:item.kind==='photo'?1100:0,title:(item.kind==='photo'?'Published AI selfie · ':'Published place · ')+spot.name,icon:item.kind==='photo'?publishedSelfieIcon(item):photographerIcon(spot,index)}).addTo(publicationLayer);
 if(item.kind==='photo')marker.on('click',()=>openPublication(item));else{const popup=node('div',null,'photo-popup');popup.append(node('strong',spot.name),node('p',spot.score+'/100 · '+photoBearing(spot).label),popupPhotoPreview(spot));const view=node('button','View published place');view.type='button';view.addEventListener('click',()=>openPublication(item));popup.append(view,link('Share ↗',item.url));marker.bindPopup(popup).on('popupopen',()=>loadPopupPhoto(popup));}
 }}
}
async function openPublication(item){
 el('published-menu').open=false;for(const menu of mapMenus)menu.open=false;
 try{const data=await json('/photo-scout/v1/publications/'+encodeURIComponent(item.id));
  if(data.kind==='photo'){await viewSavedPhoto({id:data.id,state:'complete',context:data.context,created:data.created},data);}
  else{render(data.result,{save:false,mapUpdate:false,historyId:'public:'+data.id});const spots=data.result.spots||[],coords=spots.filter(s=>s.poi).map(s=>[s.poi.lat,s.poi.lon]);if(coords.length)map.fitBounds(L.latLngBounds(coords).pad(.25),{paddingTopLeft:[30,80],paddingBottomRight:[30,160],maxZoom:16});if(!spots.length)message('This published search has no photo places.');}
  const existing=publicationItems.find(p=>p.id===data.id);if(existing)existing.checked=true;else publicationItems.push(data);publicationLayer.addTo(map);renderPublications();drawPublications();
 }catch(error){message(error.message);}
}
el('published-menu').addEventListener('toggle',()=>{if(el('published-menu').open)loadPublications();});el('published-more').addEventListener('click',()=>loadPublications(true));
loadPublications().then(()=>{if(sharedPublicationId)openPublication({id:sharedPublicationId});});

// Run after share-link parsing and initial history rendering.
restoreDeviceLocation();
