'use strict';
let lastRemovedPoi=null,selectedPoiView=null,poiViewOverrides=new Map(),poiPreviewTimer=null;
let removedHistoryItems=new Set(),lastRemovedHistory=null;
let sharedPublicationId=null;
let publicationItems=[],publicationCursor=null;let ownPublications=new Map();
const hydratedSearches=new Set();
let tasksReady=false,taskRecords=[],taskRefreshBusy=false,taskRecoveryGeneration=0,taskRefreshTimer=null;
const api=location.hostname==='localhost'||location.hostname==='127.0.0.1'?'': 'https://api.aisoup.net';
const el=id=>document.getElementById(id), message=s=>{el('message').textContent=s;if(searchBusy||pipelineBusy||resolving)el('progress-detail').textContent=s};
// Keep overlays in the same world copy as the repeating basemap when crossing the date line.
const DEFAULT_LOCATION=[37.7749,-122.4194];
const MAP_VISIT_KEY='photo-scout-map-visit-v1';
function validMapPoint(point){return Array.isArray(point)&&point.length===2&&point.every(Number.isFinite)&&Math.abs(point[0])<=85&&Math.abs(point[1])<=180;}
let previousMapVisit=null;
try{const saved=JSON.parse(localStorage.getItem(MAP_VISIT_KEY)||'null');if(saved&&validMapPoint(saved.center)&&validMapPoint(saved.selected)&&Number.isFinite(saved.zoom)&&saved.zoom>=1&&saved.zoom<=19)previousMapVisit=saved;}catch{}
const INITIAL_LOCATION=previousMapVisit?.selected||DEFAULT_LOCATION;
const map=L.map('map',{zoomControl:false,worldCopyJump:true}).setView(previousMapVisit?.center||DEFAULT_LOCATION,previousMapVisit?.zoom??9);
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
let selected=L.marker(INITIAL_LOCATION,{draggable:true,title:'Selected location: drag to move',icon:L.divIcon({className:'scout-pin',html:'<svg viewBox="0 0 48 56" aria-hidden="true" focusable="false"><path d="M24 50C20 45 8 31 8 20a16 16 0 1 1 32 0c0 11-12 25-16 30Z" fill="#e24b47" stroke="#fff" stroke-width="2.5" stroke-linejoin="round"/><circle cx="24" cy="20" r="6" fill="#fff"/></svg>',iconSize:[48,56],iconAnchor:[24,50]})}).addTo(map), resultPins=[];let humanFreePreview=false, serviceAvailable=false, poiCatalog=null, catalogGeneration=0, searchBusy=false, pollGeneration=0, resolving=false, pipelineBusy=false;
const candidatePoiLayer=L.layerGroup().addTo(map),photoLocationLayer=L.layerGroup().addTo(map),otherPhotoLocationLayer=L.layerGroup().addTo(map);
const scanLayer=L.layerGroup().addTo(map);let scanTimer=null,scanIndex=0,scanDot=null,scanRunning=false;
function stopPoiScan(){clearInterval(scanTimer);scanTimer=null;scanRunning=false;scanIndex=0;scanLayer.clearLayers();if(scanDot)scanDot.setStyle({radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9});scanDot=null;el('map').classList.remove('reviewing-views');}
function startPoiScan(){if(scanRunning||!candidatePoiLayer.getLayers().length)return;scanRunning=true;el('map').classList.add('reviewing-views');const tick=()=>{if(document.hidden)return;const dots=candidatePoiLayer.getLayers();if(!dots.length){stopPoiScan();return;}if(scanDot)scanDot.setStyle({radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9});scanDot=dots[scanIndex++%dots.length];scanDot.setStyle({radius:9,color:'#fff',weight:3,fillColor:'#9762d1',fillOpacity:1});scanLayer.clearLayers();const name=scanDot.getTooltip()?.getContent()?.textContent||'Nearby place';L.circleMarker(scanDot.getLatLng(),{radius:21,color:'#a26ce1',weight:3,fillColor:'#b889ed',fillOpacity:.15,interactive:false,className:'photo-scan-ring'}).addTo(scanLayer).bindTooltip(node('span','Checking '+name+'…'),{permanent:true,direction:'top',offset:[0,-22],className:'photo-scan-label'}).openTooltip();};tick();scanTimer=setInterval(tick,1400);}
const searchArea=L.circle(INITIAL_LOCATION,{radius:Number(el('radius').value),color:'#375947',weight:1.5,dashArray:'5 7',fillColor:'#acd69a',fillOpacity:.12,interactive:false}).addTo(map);
const journeyLayer=L.layerGroup().addTo(map);let activeRouteGeometry=null,activeRouteBounds=null,restoredRoute=null,routeCreditShown=false;
const overlayControl=L.control.layers(null,{'Route':journeyLayer,'Search radius':searchArea,'Current search results':photoLocationLayer,'Other search history':otherPhotoLocationLayer},{collapsed:true}).addTo(map);
document.querySelector('.map-overlay-controls').append(overlayControl.getContainer());
function syncMapSelection(){const lat=Number(el('lat').value),lon=Number(el('lon').value);if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180)return;searchArea.setLatLng([lat,lon]).setRadius(Number(el('radius').value));el('map-selection').textContent=`${lat.toFixed(5)}, ${lon.toFixed(5)}`;el('coordinate-readout').textContent=el('map-selection').textContent;el('map-radius').textContent=`Searching within ${Number(el('radius').value)>=1000?Number(el('radius').value)/1000+' km':el('radius').value+' m'}`;}
selected.on('dragend',()=>{const p=selected.getLatLng();pick(p.lat,p.lng);});
function saveMapVisit(){
 try{const center=map.getCenter().wrap(),point=selected.getLatLng().wrap();localStorage.setItem(MAP_VISIT_KEY,JSON.stringify({center:[center.lat,center.lng],selected:[point.lat,point.lng],zoom:map.getZoom()}));}catch{}
}
el('lat').value=INITIAL_LOCATION[0];el('lon').value=INITIAL_LOCATION[1];syncMapSelection();
map.on('moveend zoomend',saveMapVisit);
window.addEventListener('pagehide',saveMapVisit);
document.addEventListener('visibilitychange',()=>{if(document.hidden)saveMapVisit();});


function coordinates(){const styles=[...el('style-options').querySelectorAll('input:checked')].map(i=>i.value).filter(s=>s!=='any');const params={lat:Number(el('lat').value),lon:Number(el('lon').value),radius:Number(el('radius').value),photoStyles:styles.length?styles:null};if(el('search-mode').value==='route'){const destination=el('route-destination').value.trim();if(!destination)throw new Error('Add a destination in Search settings.');const origin=el('route-origin').value.trim();const endpoint=(text,previous)=>previous&&text===(previous.query||previous.label)&&Number.isFinite(previous.lat)&&Number.isFinite(previous.lon)?{lat:previous.lat,lon:previous.lon,label:previous.label}:{query:text};params.route={origin:origin?endpoint(origin,restoredRoute?.origin):null,destination:endpoint(destination,restoredRoute?.destination),travelMode:el('route-travel').value,corridorMeters:Number(el('route-corridor').value)};}return params;}
function updateSearchMode(){const route=el('search-mode').value==='route';el('route-settings').hidden=!route;el('radius-setting').hidden=route;el('prompt-query').placeholder=route?'What would you like to photograph along the way?':'A place, a mood, a little adventure…';if(!route){activeRouteGeometry=null;journeyLayer.clearLayers();if(!map.hasLayer(searchArea))searchArea.addTo(map);}else map.removeLayer(searchArea);updateParameterSummary();}
function showRoute(route){const credit=Boolean(route?.geometry?.coordinates?.length);if(credit&&!routeCreditShown)map.attributionControl?.addAttribution('Route © Google Maps');if(!credit&&routeCreditShown)map.attributionControl?.removeAttribution('Route © Google Maps');routeCreditShown=credit;journeyLayer.clearLayers();activeRouteGeometry=route||null;activeRouteBounds=null;if(!route?.geometry?.coordinates?.length){if(el('search-mode').value!=='route'&&!map.hasLayer(searchArea))searchArea.addTo(map);return;}map.removeLayer(searchArea);const coords=route.geometry.coordinates.map(([lon,lat])=>[lat,lon]);for(let i=1;i<coords.length;i++)coords[i][1]+=360*Math.round((coords[i-1][1]-coords[i][1])/360);const polyline=L.polyline(coords,{color:'#8060bd',weight:5,opacity:.85}).addTo(journeyLayer);activeRouteBounds=polyline.getBounds();polyline.bindTooltip(node('span',`${route.travelMode==='walk'?'Walking':'Driving'} · ${(route.distanceMeters/1000).toFixed(1)} km · Google Maps`));for(const [index,label] of [[0,'Start'],[coords.length-1,'Finish']])L.circleMarker(coords[index],{radius:7,color:'#fff',weight:3,fillColor:'#8060bd',fillOpacity:1}).bindTooltip(node('span',label)).addTo(journeyLayer);if(!map.hasLayer(journeyLayer))journeyLayer.addTo(map);}

function invalidatePois(){stopPoiScan();catalogGeneration++;candidatePoiLayer.clearLayers();poiCatalog=null;}
function pick(lat,lon){locationSelectionRevision++;invalidatePois();el('lat').value=lat.toFixed(6);el('lon').value=lon.toFixed(6);selected.setLatLng([lat,lon]);syncMapSelection();saveMapVisit();}
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
el('search-mode').addEventListener('change',()=>{invalidatePois();updateSearchMode();});for(const id of ['route-origin','route-destination','route-travel','route-corridor'])el(id).addEventListener('change',()=>{invalidatePois();updateParameterSummary();});
el('radius').addEventListener('change',()=>{invalidatePois();syncMapSelection();updateParameterSummary();});
function updateParameterSummary(){const moods=[...el('style-options').querySelectorAll('input:checked')].filter(i=>i.value!=='any');el('parameter-summary').textContent=(el('search-mode').value==='route'?(el('route-travel').value==='walk'?'Walk':'Drive')+' · route':el('radius').selectedOptions[0].textContent)+' · '+(moods.length?moods.map(i=>i.parentElement.querySelector('strong').textContent).join(' + '):'Any mood');}
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
 box.append(caption,node('p','Drag to look · ← → turn · ↑ ↓ tilt · + / − zoom','poi-view-hint'));return box;
}
const cameraSaves=new Map(),cameraRefreshes=new Map(),selfieViewOverrides=new Map();
function ownedViewSearchId(searchId){
 if(!searchId)return null;if(!searchId.startsWith('public:'))return searchId;
 return [...ownPublications.values()].find(p=>p.id===searchId.slice(7))?.sourceId||null;
}
function queueCameraSave(searchId,spot,view,scope='place',framing='current'){
 const ownerId=ownedViewSearchId(searchId);if(!ownerId)return;
 const key=ownerId+':'+scope+':'+poiHistoryKey(spot);let state=cameraSaves.get(key);
 if(!state){state={running:false,timer:null,payload:null};cameraSaves.set(key,state);}
 state.payload={searchId:ownerId,poiId:poiHistoryKey(spot),scope,framing,...view};clearTimeout(state.timer);
 state.timer=setTimeout(()=>flushCameraSave(state),650);
}
async function flushCameraSave(state){
 if(state.running||!state.payload)return;state.running=true;
 try{while(state.payload){const payload=state.payload;await json('/photo-scout/v1/poi-view',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify(payload),keepalive:true});if(state.payload===payload)state.payload=null;}persistHistory();}
 catch{el('history-note').textContent='This view could not be saved. Adjust it again to retry.';}
 finally{state.running=false;}
}
window.addEventListener('pagehide',()=>{for(const state of cameraSaves.values()){clearTimeout(state.timer);flushCameraSave(state);}});
function savedSelfieView(searchId,spot){
 const id=ownedViewSearchId(searchId)||searchId,key=poiHistoryKey(spot);
 return selfieViewOverrides.get(id+':'+key)||searchHistory.find(h=>h.id===id)?.result.selfieViews?.[key];
}
function applyStudioView(view,framing='current'){
 applyStreetView(studioSpot,view,null);
 const id=ownedViewSearchId(studioSearchId)||studioSearchId;
 const override={sourceUrl:studioSpot.sourceUrl,viewHeadingDegrees:view.heading,viewPitchDegrees:view.pitch,viewFovDegrees:view.fov,viewAdjusted:true,imageUrl:null,streetViewReference:null,framing};
 if(id){const key=poiHistoryKey(studioSpot);selfieViewOverrides.set(id+':'+key,override);const h=searchHistory.find(h=>h.id===id);if(h){h.result.selfieViews??={};h.result.selfieViews[key]=override;}}
 queueCameraSave(studioSearchId,studioSpot,view,'selfie',framing);
}
function applyStreetView(spot,view,searchId){
 const url=new URL(spot.sourceUrl);for(const [key,value] of Object.entries(view))url.searchParams.set(key,value);
 const override={sourceUrl:url.toString(),viewHeadingDegrees:view.heading,viewPitchDegrees:view.pitch,viewFovDegrees:view.fov,viewAdjusted:true,imageUrl:null,streetViewReference:null};Object.assign(spot,override);
 const ownerId=ownedViewSearchId(searchId),linkedIds=new Set([searchId,ownerId]);if(ownerId)for(const p of ownPublications.values())if(p.sourceId===ownerId&&p.kind!=='photo')linkedIds.add('public:'+p.id);
 for(const id of linkedIds)if(id)poiViewOverrides.set(id+':'+poiHistoryKey(spot),override);
 if(searchId){
  const ownerId=ownedViewSearchId(searchId),key=poiHistoryKey(spot);
  for(const history of searchHistory.filter(h=>h.id===searchId||h.id===ownerId))for(const field of ['spots','poiResults'])for(const original of history.result[field]||[])if(poiHistoryKey(original)===key)Object.assign(original,override);
  for(const surface of document.querySelectorAll('.street-view-preview')){const target=surface.streetViewSpot?.();if(surface.streetViewSearchId===searchId&&target&&poiHistoryKey(target)===key)Object.assign(target,override);}
  queueCameraSave(searchId,spot,view);
  const refreshKey=searchId+':'+key;clearTimeout(cameraRefreshes.get(refreshKey));cameraRefreshes.set(refreshKey,setTimeout(()=>{cameraRefreshes.delete(refreshKey);refreshStreetViewPreviews(spot,searchId);},350));
 }

 for(const marker of [...resultPins,...publicationLayer.getLayers()].filter(m=>m.photoSpot&&linkedIds.has(m.searchId)&&poiHistoryKey(m.photoSpot)===poiHistoryKey(spot))){Object.assign(marker.photoSpot,override);const index=allPoiViews(searchHistory.find(h=>h.id===searchId)?.result||{spots:[]},searchId).findIndex(s=>poiHistoryKey(s)===poiHistoryKey(spot));marker.setIcon(searchId===focusedSearchId?photographerIcon(spot,Math.max(0,index)):marker.photoIndex!=null?photographerIcon(spot,marker.photoIndex):directionDot(spot));}
 if(selectedPoiView?.searchId===searchId&&poiHistoryKey(selectedPoiView.spot)===poiHistoryKey(spot)){
  const selected=selectedPoiView;Object.assign(selected.spot,override);selected.marker.setIcon(searchId===focusedSearchId?photographerIcon(spot,selected.index):directionDot(spot));
  for(const link of selected.popup.querySelectorAll('a'))if(link.href.startsWith('https://www.google.com/maps/@'))link.href=spot.sourceUrl;
  const caption=selected.popup.querySelector('.poi-view-label');if(caption)caption.textContent=`View: ${photoBearing(spot).label} · tilt ${view.pitch}° · FOV ${view.fov}°`;
 }
}
function refreshStreetViewPreviews(spot,searchId){
 for(const surface of document.querySelectorAll('.street-view-preview')){const target=surface.streetViewSpot?.();if(surface.streetViewSearchId===searchId&&target&&poiHistoryKey(target)===poiHistoryKey(spot)){Object.assign(target,spot);surface.streetViewController?.refresh();if(window.PhotoScoutNativeStreetView?.refresh(surface))continue;const img=surface.querySelector('img'),link=surface.querySelector('a');if(link)link.href=spot.sourceUrl;if(img)refreshThumbnail(img,target);for(const original of surface.closest('.card,.photo-popup')?.querySelectorAll('a')||[])if(original.href.startsWith('https://www.google.com/maps/@'))original.href=spot.sourceUrl;}}
}
function bindStreetViewPreview(surface,getSpot,searchId,{onChange,onCommit,isDisabled}={}){
 if(typeof window.PhotoScoutStreetView?.bind!=='function')return;
 surface.streetViewSpot=getSpot;surface.streetViewSearchId=searchId;
 surface.streetViewController=window.PhotoScoutStreetView.bind(surface,{getSpot,isDisabled:()=>Boolean(isDisabled?.()),onChange:view=>{if(onChange)onChange(view);else applyStreetView(getSpot(),view,searchId);window.PhotoScoutNativeStreetView?.refresh(surface);},onCommit:()=>{if(onCommit)onCommit();else refreshStreetViewPreviews(getSpot(),searchId);}});
 // Start the reusable interactive panorama only for the surface being used.
 // Shortlist cards used to remain static until a gesture ended.
 let nativeLoading=false;
 const interact=()=>{if(nativeLoading||isDisabled?.()||window.PhotoScoutNativeStreetView?.isActive(surface)||getSpot()?.provider!=='google-street-view')return;nativeLoading=true;Promise.resolve(activateNativeStreetView(surface,getSpot,searchId,{isDisabled,onChange})).finally(()=>nativeLoading=false);};
 for(const event of ['pointerdown','focusin','wheel'])surface.addEventListener(event,interact,{capture:true,passive:true});
 L.DomEvent.disableClickPropagation(surface);L.DomEvent.disableScrollPropagation(surface);
}
function activateNativeStreetView(surface,getSpot,searchId,options={}){
 if(!window.PhotoScoutNativeStreetView)return;
 return window.PhotoScoutNativeStreetView.attach(surface,{getSpot,...options,onChange:view=>{if(options.onChange)options.onChange(view);else applyStreetView(getSpot(),view,searchId);surface.streetViewController?.refresh();}});
}
function fitPoiPopup(popup){
 if(!popup?.isOpen())return;
 const bounds=el('map').getBoundingClientRect(),dock=document.querySelector('.scout-dock').getBoundingClientRect();
 const top=bounds.width>760?96:72,bottom=Math.max(16,bounds.bottom-dock.top+16);
 const shortlist=el('results'),listBounds=shortlist.getBoundingClientRect();
 let left=16;
 if(bounds.width>760&&!shortlist.hidden&&listBounds.right>bounds.left)left=Math.ceil(listBounds.right-bounds.left)+16;
 if(bounds.width-left-16<260)left=16;
 popup.options.autoPan=true;popup.options.keepInView=true;
 popup.options.autoPanPaddingTopLeft=L.point(left,top);
 popup.options.autoPanPaddingBottomRight=L.point(16,bottom);
 popup.options.maxHeight=Math.max(80,Math.floor(bounds.height-top-bottom-64));
 popup.options.maxWidth=Math.max(210,Math.min(300,bounds.width-left-32));
 popup.update();
}
let visiblePoiPopup=null,poiPopupResize=null;
map.on('popupopen',event=>{
 visiblePoiPopup=event.popup;poiPopupResize?.disconnect();
 // Run after marker handlers; image loading and expanding lists can change the height.
 requestAnimationFrame(()=>fitPoiPopup(event.popup));
 if(window.ResizeObserver){poiPopupResize=new window.ResizeObserver(()=>fitPoiPopup(event.popup));const content=event.popup.getElement()?.querySelector('.photo-popup');if(content)poiPopupResize.observe(content);}
});
map.on('popupclose',event=>{if(visiblePoiPopup===event.popup){visiblePoiPopup=null;poiPopupResize?.disconnect();}});
window.addEventListener('resize',()=>{if(visiblePoiPopup)fitPoiPopup(visiblePoiPopup);});
map.on('popupclose',event=>{const popup=event.popup?.getElement?.();for(const surface of popup?.querySelectorAll('.street-view-preview')||[])window.PhotoScoutNativeStreetView?.detach(surface);});
function rotateSelectedPoiView(key){
 const selected=selectedPoiView,delta=window.PhotoScoutStreetView?.keyDelta(key);
 if(!selected||selected.spot.provider!=='google-street-view'||!delta)return false;
 applyStreetView(selected.spot,window.PhotoScoutStreetView.adjust(window.PhotoScoutStreetView.view(selected.spot),delta),selected.searchId);
 clearTimeout(poiPreviewTimer);poiPreviewTimer=setTimeout(()=>refreshStreetViewPreviews(selected.spot,selected.searchId),250);return true;
}
document.addEventListener('keydown',event=>{
 if(event.defaultPrevented||event.target?.closest?.('input,textarea,select,[contenteditable="true"],dialog[open],.street-view-preview'))return;
 if(rotateSelectedPoiView(event.key)){event.preventDefault();event.stopImmediatePropagation();}
},true);
function popupPhotoPreview(spot,searchId){
 const box=node('div',null,'popup-photo-preview');box.photoSpot=spot;
 if(spot.sourceUrl&&(spot.provider==='google-street-view'||spot.imageUrl||spot.imageReference)){
  const image=node('img');image.alt=spot.name||'Photo spot';image.referrerPolicy='no-referrer';
  const view=spot.provider==='google-street-view'?node('div'):link('',spot.sourceUrl);view.setAttribute('aria-label',spot.provider==='google-street-view'?'Open Google Street View':'Open original photo');view.append(image);box.append(view);if(spot.provider==='google-street-view')bindStreetViewPreview(view,()=>box.photoSpot,searchId);
  if(spot.provider==='google-street-view'||spot.provider==='google-places-photos')box.append(node('p','Google Maps','GMP-attribution'));if(spot.provider==='google-places-photos')for(const a of spot.authorAttributions||[])box.append(a.uri?link(a.displayName,a.uri):node('p',a.displayName,'small'));
 }else box.append(node('p','Photo preview unavailable. Open the original view below.','small'));
 return box;
}
function loadPopupPhoto(popup,force=false){
 for(const box of popup.querySelectorAll('.popup-photo-preview')){
  const image=box.querySelector('img'),spot=box.photoSpot;if(!image)continue;const surface=image.parentElement;if(spot.provider==='google-street-view'&&window.PhotoScoutNativeStreetView)activateNativeStreetView(surface,()=>box.photoSpot, surface.streetViewSearchId);
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
    if(['google-street-view','google-places-photos'].includes(box.photoSpot.provider)&&Number(box.dataset.retries||0)<1){
     box.dataset.retries=String(Number(box.dataset.retries||0)+1);refresh();
    }else{status.textContent='Preview unavailable — open the original view.';status.hidden=false;}
   });
  }
  if(!force&&box.dataset.loaded&&box.dataset.source===spot.sourceUrl)continue;
  box.dataset.retries='0';box.dataset.source=spot.sourceUrl;image.hidden=false;
  if(spot.provider==='google-street-view'||spot.imageReference)refresh();
  else if(spot.imageUrl)image.src=spot.imageUrl;
 }
}
function photoBackgroundInfo(context){
 const provider={'google-street-view':'Google Street View','google-places-photos':'Google Places Photos',panoramax:'Panoramax','wikimedia-commons':'Wikimedia Commons'}[context.provider]||context.provider||'Source unavailable';
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
function render(result,{scroll=true,save=true,mapUpdate=true,historyId=null,publication=null}={}){
 if(result.responseType==='feedback'){
  displayedResult=result;displayedSearchId=historyId;
  const root=el('results'),close=node('button','Close ×','back-button');close.type='button';close.onclick=()=>root.hidden=true;
  root.replaceChildren(close,node('h2',result.action==='help'?'Photo Scout help':result.action==='unsupported'?'What Photo Scout can do':'Try a photo search'),node('p',result.summary));root.hidden=false;el('toggle-results').disabled=false;return;
 }
 displayedSearchId=save?saveSearch(result):(historyId||searchHistory.find(h=>h.result===result)?.id||focusedSearchId);displayedResult=result;const visiblePois=allPoiViews(result,displayedSearchId);const root=el('results');controls.open=false;for(const menu of mapMenus)menu.open=false;root.hidden=false;el('toggle-results').disabled=false;
 const edit=node('button','Close ×','back-button');edit.type='button';edit.addEventListener('click',()=>{root.hidden=true;});
 const heading=node('div',null,'results-heading');heading.append(node('div','YOUR SHORTLIST','eyebrow'),edit);
 root.replaceChildren(heading,node('h2',historyId?.startsWith('public:')?'Published photo places':result.route?'Your route photo shortlist':'Your nearby photo shortlist'),node('p',result.route?`${allPoiViews(result).length} photo spots along your route, Explore each viewpoint below.`:result.summary));if(result.route){showRoute(result.route);root.append(node('p',`${result.route.origin.label} → ${result.route.destination.label} · ${(result.route.distanceMeters/1000).toFixed(1)} km · ${result.route.travelMode==='walk'?'Walking':'Driving'} · Within ${result.route.corridorMeters} m of the route · Route: Google Maps`,'small'));for(const warning of result.route.warnings||[])root.append(node('p',warning,'small'));const sorting=node('label',null,'route-result-sort');sorting.append(node('span','Sort by'));const select=node('select');select.setAttribute('aria-label','Sort route photo spots');for(const [value,label] of [['score','Score · highest first'],['route','Route · start to finish']]){const option=node('option',label);option.value=value;select.append(option);}select.value=resultSortBySearch.get(displayedSearchId)||'score';select.addEventListener('change',()=>{resultSortBySearch.set(displayedSearchId,select.value);render(result,{save:false,mapUpdate:false,historyId:displayedSearchId});drawHistoryMap();});sorting.append(select);root.append(sorting);} const interpreted=result.searchContext||result.interpretation;const interpretation=interpreted?.intentSummary;if(interpretation)root.append(node('p','Searching for: '+interpretation,'small'));for(const assumption of interpreted?.assumptions||[])root.append(node('p',assumption,'small'));if(!historyId?.startsWith('public:'))root.append(publishButton('search',displayedSearchId));
 if(lastRemovedPoi?.searchId===displayedSearchId){const notice=node('p',lastRemovedPoi.name+' removed from this search. ','remove-notice'),undo=node('button','Undo','remove-undo');undo.type='button';const removed={...lastRemovedPoi};undo.addEventListener('click',async()=>{undo.disabled=true;try{const data=await json('/photo-scout/v1/hidden-pois',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({...removed,hidden:false})});hiddenPoisBySearch=new Map(Object.entries(data.hiddenPois).map(([id,keys])=>[id,new Set(keys)]));lastRemovedPoi=null;drawHistoryMap();renderHistory();render(displayedResult,{save:false,mapUpdate:false,historyId:displayedSearchId});}catch(error){undo.disabled=false;message(error.message);}});notice.append(undo);root.append(notice);}
 if(result.photoStyles?.length)root.append(node('p',`Photo mood: ${result.photoStyles.map(s=>s.label).join(', ')}`,'tag'));
 if(result.candidatePoiCount!==undefined)root.append(node('p',`Found ${result.candidatePoiCount} named candidates${result.photoLocationCount?' and '+result.photoLocationCount+' photo locations outside named POIs':''}; showing ${visiblePois.length} with scored images.`,'small'),node('p',result.poiProvider==='google-places'?'Place data · Google Maps':'POI data © OpenStreetMap contributors · ODbL 1.0','small'));const cards=node('div',null,'cards');root.append(cards);
 for(const [i,originalSpot] of visiblePois.entries()){const s={...originalSpot,...poiViewOverrides.get(displayedSearchId+':'+poiHistoryKey(originalSpot))};const card=node('article',null,'card'),cardSearchId=displayedSearchId;card.id='photo-spot-'+String(i+1);card.addEventListener('click',event=>{if(!event.target.closest('button,input,select,textarea,summary,.street-view-preview,.comments-section'))focusMapSpot(s,{searchId:cardSearchId});});if(s.imageUrl||s.imageReference||s.provider==='google-street-view'){const img=node('img');if(s.provider==='google-street-view'||s.imageReference)refreshThumbnail(img,s);else if(s.imageUrl)img.src=s.imageUrl;img.alt=s.name;img.loading='eager';img.referrerPolicy='no-referrer';img.addEventListener('error',()=>{const attempts=Number(img.dataset.retryCount||0);if((s.provider==='google-street-view'||s.imageReference)&&attempts<2){img.dataset.retryCount=String(attempts+1);setTimeout(()=>{if(img.isConnected)refreshThumbnail(img,s);},attempts?65000:2000);}else img.replaceWith(node('span','Image unavailable — open the original view ↗','image-unavailable'));});const imageLink=s.provider==='google-street-view'?node('div'):link('',s.sourceUrl);imageLink.className='spot-image-link';imageLink.setAttribute('aria-label',(s.provider==='google-street-view'?'Open Google Street View for ':'Open original photo for ')+s.name);imageLink.append(img,...(s.provider==='google-street-view'?[]:[node('span','Open original photo ↗','image-open-label')]));if(s.provider==='google-street-view')bindStreetViewPreview(imageLink,()=>s,cardSearchId);card.append(imageLink);}else if(s.provider==='google-street-view'){const view=link('Explore this view in Google Street View ↗',s.sourceUrl);view.className='street-view-link';card.append(view);}const body=node('div',null,'body');body.append(...(['google-street-view','google-places-photos'].includes(s.provider)?[node('p','Google Maps','GMP-attribution')]:[]),node('h3',`${i+1}. ${s.name}`),node('span',s.score==null?'No verified image':`${s.score}/100 subjective photo score · ${s.confidence} confidence`,'tag'),...(s.recommend===false?[node('p','Model flagged suitability concerns — review the notes','small')]:[]),...(s.provider?[node('p',`Image source: ${s.provider}`,'small')]:[]),...(s.poi?[node('p',`${s.poi.category==='photo-location'?'Photo location':'Candidate place'}: ${s.poi.name}${s.poi.category&&s.poi.category!=='photo-location'?' · '+s.poi.category:''}`,'small')]:[]),node('p',s.visible_evidence),...(s.photo_tip?[node('p',`Photo idea: ${s.photo_tip}`)]:[]),...(s.viewHeadingDegrees!=null?[node('p',`Inspected camera direction: ${s.viewHeadingDegrees}°`,'small')]:[]),...(s.distanceMeters!=null?[node('p',s.routeProgressMeters!=null?`${(s.routeProgressMeters/1000).toFixed(2)} km from route start · ${s.routeOffsetMeters} m from route · ${s.locationType}`:`${s.distanceMeters} m straight-line distance · ${s.locationType}`)]:[]),...(s.uncertainty?[node('p',`Uncertainty: ${s.uncertainty}`)]:[]),node('p',s.coordinateWarning),...(s.sourceUrl?[link(s.provider==='google-street-view'?'View Google Street View ↗':'Original image ↗',s.sourceUrl)]:[]),...(s.author?[node('p',`${s.author} · ${s.license} · source date: ${s.sourceDate||s.capturedAt||'unknown'}`)]:[]),...(s.licenseUrl?[link('Image license ↗',s.licenseUrl)]:[]));const compose=node('button','📷 Take a selfie here','compose-photo');compose.type='button';compose.addEventListener('click',()=>openPhotoStudio({...s,...poiViewOverrides.get(cardSearchId+':'+poiHistoryKey(s))},cardSearchId));const title=body.querySelector('h3'),locate=node('button',`${i+1}. ${s.name}`,'shortlist-place-focus');locate.type='button';locate.setAttribute('aria-label','Show '+s.name+' on map');locate.addEventListener('click',()=>focusMapSpot(s,{searchId:cardSearchId}));title?.replaceChildren(locate);body.append(publicationBadge('place',displayedSearchId,s),compose,publishButton('place',displayedSearchId,s),removePoiButton(s,displayedSearchId),placeSelfies(s),commentSection('place',cardSearchId,s,publication,true));card.append(body);cards.append(card);}
 if(result.imageAssessments?.length){const detail=node('details',null,'score-details');detail.append(node('summary',`All ${result.imageAssessments.length} image checks`));const list=node('ul');for(const a of result.imageAssessments){const li=node('li');li.append(node('strong',`${a.matches_request===false?'Excluded':a.score+'/100'} · ${a.name}`),node('p',`${a.provider} · ${a.eligibleForRecommendation?'Suitable candidate':'Not recommended'}`,'small'),node('p',a.visible_evidence),...(a.exclusionReason?[node('p',`Why not shortlisted: ${a.exclusionReason}`)]:[]),node('p',`Uncertainty: ${a.uncertainty}`));list.append(li);}detail.append(list);root.append(detail);}

 if(result.sources){const coverage=node('p',Object.entries(result.sources).map(([name,s])=>`${name}: ${s.status==='ok'?(s.eligibleImages!==undefined?`${s.eligibleImages} eligible images${s.sampledImages!==undefined?`; ${s.sampledImages} selected for scoring`:""}`:'available'):'temporarily unavailable'}`).join(' · '),'small');root.append(coverage);}
 if(result.inspectedImages!==undefined)root.append(node('p',`${result.analysisMethod==='fixed-batch-scoring'?'Checked':'Inspected'} ${result.inspectedImages} images${result.inspectedImageSources?.length?" from "+result.inspectedImageSources.join(", "):""}. ${result.coverage||''}`,'small'));if(publication?.kind!=='place')root.append(commentSection('search',displayedSearchId,null,publication));if(mapUpdate)drawHistoryMap({fit:true});}
async function refreshThumbnail(img,spot,onFailure){img.dataset.refreshed='true';const generation=Number(img.dataset.viewGeneration||0)+1;img.dataset.viewGeneration=String(generation);try{const data=await json('/photo-scout/v1/thumbnails',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sourceUrls:[spot.imageReference||spot.sourceUrl]})});if(img.dataset.viewGeneration!==String(generation))return;if(data.imageUrls[0])img.src=data.imageUrls[0];else throw Error('Unavailable');}catch{if(img.dataset.viewGeneration!==String(generation))return;if(onFailure)onFailure();else img.replaceWith(node('span','Image unavailable — open the original view ↗','image-unavailable'));}}
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
async function applyIntent(plan,place){if(plan.route)applyRouteControls(plan.route);pick(place.lat,place.lon);const radius=String(plan.radiusMeters);if(![...el('radius').options].some(o=>o.value===radius)){const option=node('option',radius+' m');option.value=radius;el('radius').append(option);}el('radius').value=radius;fitSearchRange();el('style-options').querySelectorAll('input').forEach(i=>i.checked=plan.photoStyles.length?plan.photoStyles.includes(i.value):i.value==='any');updateParameterSummary();el('location-options').replaceChildren();el('prompt-status').textContent=place.label+' · '+plan.explanation;await runSearchPipeline(plan.poiQueries||[],plan);}
async function submitSearch(e){e?.preventDefault();if(!serviceAvailable||resolving||pipelineBusy||searchBusy)return;controls.open=false;focusedSearchId=null;drawHistoryMap();fitSearchRange();const query=el('prompt-query').value.trim();try{coordinates();}catch(err){controls.open=true;message(err.message);el('route-destination').focus();return;}if(humanFreePreview){await submitDurableSearch(query);return;}const generation=++intentGeneration;resolving=true;updateSubmitState();setProgress(0,'Understanding your request…','Finding the location, photo mood and search radius in your message.');el('location-options').replaceChildren();el('prompt-status').textContent='Understanding your request and finding matching locations…';try{const c=coordinates();const plan=await json('/photo-scout/v1/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,route:c.route,lat:c.lat,lon:c.lon,radius:c.radius,photoStyles:c.photoStyles||[],preferences:'Scenic, distinctive public places for photography'})});if(generation!==intentGeneration)return;if(plan.clarification){el('prompt-status').textContent=plan.clarification;setProgress(0,'Could not interpret the request',plan.clarification,'error');return;}if(!plan.locations.length){el('prompt-status').textContent='No matching location. Please add a city or place name.';setProgress(0,'Could not find a location',el('prompt-status').textContent,'error');return;}el('prompt-status').textContent='Using '+plan.locations[0].label+'. Finding and scoring nearby views…';await applyIntent(plan,plan.locations[0]);}catch(err){el('prompt-status').textContent=err.message;setProgress(0,'Search could not be started',err.message,'error');}finally{resolving=false;updateSubmitState();}}
el('prompt-form').addEventListener('submit',submitSearch);

el('prompt-query').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();el('prompt-form').requestSubmit();}});

function showShortlist(){
 map.closePopup();controls.open=false;for(const menu of mapMenus)menu.open=false;
 el('results').hidden=false;
}
function toggleShortlist(){
 const obscured=Boolean(document.querySelector('.leaflet-popup'));
 if(obscured||el('results').hidden)showShortlist();else el('results').hidden=true;
}
el('toggle-results').addEventListener('click',toggleShortlist);
el('sample').addEventListener('click',async()=>{try{const r=await fetch('./sample.json');if(!r.ok)throw Error('Sample unavailable');const data=await r.json();render(data);}catch(e){message(e.message)}});

el('sample-paris').addEventListener('click',async()=>{try{const r=await fetch('./sample-paris.json');if(!r.ok)throw Error('Sample unavailable');render(await r.json());}catch(e){message(e.message)}});

el("sample-google").addEventListener("click",async()=>{try{const r=await fetch("./sample-google.json");if(!r.ok)throw Error("Sample unavailable");const data=await r.json();render(data);message(data.sampleNotice);}catch(e){message(e.message)}});

const HISTORY_KEY='photo-scout-search-history-v1';let searchHistory=[],focusedSearchId=null,displayedResult=null,displayedSearchId=null,hiddenPoisBySearch=new Map(),authUser=null,csrfToken=null;const pendingHistory=new Map(),resultSortBySearch=new Map();let syncingHistory=false;
function hasScoredImage(spot){return Number.isFinite(spot.score)&&spot.assessmentStatus!=='no_verified_view'&&Boolean(spot.verifiedImageAvailable||spot.imageUrl||spot.imageReference||spot.streetViewReference||(spot.provider==='google-street-view'&&spot.sourceUrl));}
function poiHistoryKey(spot){return String(spot.poi?.id||spot.id||`geo:${spot.poi?.lat},${spot.poi?.lon}:${spot.name}`);}
function allPoiViews(result,searchId=searchHistory.find(h=>h.result===result)?.id){
 const hidden=hiddenPoisBySearch.get(searchId)||new Set(),seen=new Set();
 // Pick the highest-scored direction for each place before changing visit order.
 const places=[...(result.poiResults||[]),...(result.spots||[])].filter(s=>hasScoredImage(s)&&!hidden.has(poiHistoryKey(s))).sort((a,b)=>b.score-a.score).filter(s=>{const id=s.poi?.id||s.id;if(seen.has(id))return false;seen.add(id);return true;});
 if(result.route&&resultSortBySearch.get(searchId)==='route')places.sort((a,b)=>(Number.isFinite(a.routeProgressMeters)?a.routeProgressMeters:Infinity)-(Number.isFinite(b.routeProgressMeters)?b.routeProgressMeters:Infinity)||b.score-a.score);
 return places;
}
function photoTopCount(result){return allPoiViews(result).length;}
function directionDot(spot){const b=photoBearing(spot);return L.divIcon({className:'direction-dot'+(b.heading===null?' no-bearing':''),html:`<span style="transform:rotate(${b.heading??0}deg)"><i></i><b></b></span><small class="dot-bearing">${b.heading===null?'?':b.heading+'°'}</small>`,iconSize:[36,48],iconAnchor:[18,16],popupAnchor:[0,-16]});}
function storedHistory(){return searchHistory.map(h=>({...h,result:{...h.result,imageAssessments:[],spots:(h.result.spots||[]).map(stripHistoryImage),poiResults:(h.result.poiResults||[]).map(stripHistoryImage)}}));}
function persistHistory(){const records=storedHistory();if(authUser){queueHistory(records);return;}try{sessionStorage.setItem(HISTORY_KEY,JSON.stringify(records));}catch{el('history-note').textContent='Session storage is unavailable; history stays in this page.';}}
async function queueHistory(records){for(const h of records)pendingHistory.set(h.id,h);if(syncingHistory)return;syncingHistory=true;try{while(pendingHistory.size&&authUser){const [id,item]=pendingHistory.entries().next().value;await json('/photo-scout/v1/history',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},body:JSON.stringify(item)});if(pendingHistory.get(id)===item)pendingHistory.delete(id);}}catch{el('history-note').textContent='Account history could not sync. Keep this page open and try again.';}finally{syncingHistory=false;}}
async function loadAccount(){
 try{
  const state=await json('/photo-scout/v1/auth/me');authUser=state.user;csrfToken=state.csrfToken;
  el('account-login').disabled=!state.configured;el('account-login').textContent='Sign in';el('account-login').title=state.configured?'Sign in with Google':'Google sign-in is not configured yet';el('account-login').hidden=Boolean(authUser);el('account-logout').hidden=!authUser;el('account-name').textContent=authUser?authUser.name:'Guest session';
  if(authUser){
   const guest=searchHistory,remote=await json('/photo-scout/v1/history'),merged=new Map(remote.items.map(h=>[h.id,h]));
   for(const h of guest)merged.set(h.id,h);
   searchHistory=[...merged.values()].sort((a,b)=>b.created-a.created);
   if(remote.hiddenPois)hiddenPoisBySearch=new Map(Object.entries(remote.hiddenPois).map(([id,keys])=>[id,new Set(keys)]));
   el('history-note').textContent='Your searches and generated photos are saved permanently to your account.';
   renderHistory();drawHistoryMap();
   // Show saved places before waiting for background guest-history uploads.
   if(guest.length){
    const userId=authUser.id;
    queueHistory(guest.map(h=>({...h,result:{...h.result,imageAssessments:[],spots:(h.result.spots||[]).map(stripHistoryImage),poiResults:(h.result.poiResults||[]).map(stripHistoryImage)}}))).then(()=>{
     if(authUser?.id===userId&&!pendingHistory.size)sessionStorage.removeItem(HISTORY_KEY);
    });
   }
  }else el('history-note').textContent='Guest searches and photos are deleted after 7 days without a visit. Sign in to keep them permanently.';
 }catch{el('account-name').textContent='Guest session';el('account-login').disabled=true;el('history-note').textContent='Sign-in is temporarily unavailable. Guest history stays in this session.';}
 refreshComments();refreshPhotoLibraryIfOpen();
}
el('account-login').addEventListener('click',()=>{persistHistory();location.href=api+'/photo-scout/v1/auth/login';});
el('account-logout').addEventListener('click',async()=>{try{await json('/photo-scout/v1/auth/logout',{method:'POST',headers:{'X-CSRF-Token':csrfToken}});authUser=null;csrfToken=null;pendingHistory.clear();searchHistory=[];sessionStorage.removeItem(HISTORY_KEY);el('results').hidden=true;el('toggle-results').disabled=true;renderHistory();drawHistoryMap();resetTaskRecovery();await loadAccount();await restoreTasks();await loadPublications();}catch{el('history-note').textContent='Sign-out failed. Please try again.';}});
function stripHistoryImage(s){const copy={...s,verifiedImageAvailable:hasScoredImage(s)};delete copy.imageUrl;delete copy.streetViewReference;return copy;}
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
   copy.append(node('strong',entry.label),publicationBadge('photo',entry.id),node('small',`${new Date(entry.created).toLocaleString()} · Selfie · ${entry.state}`));item.append(thumb,copy);item.type='button';item.dataset.state=entry.state;item.title=['queued','checking','running'].includes(entry.state)?'Selfie in progress':'View saved photo';item.addEventListener('click',()=>viewSavedTask(entry.task));const row=node('div',null,'photo-history-row');row.append(item,removeHistoryButton(entry));photosRoot.append(row);continue;
  }
  const h=entry.history,row=node('div',null,'history-item search-history-item'),label=node('span',null,'history-copy'),area=h?.result.responseType==='feedback'?null:window.PhotoScoutHistoryMap?.area(entry);if(area&&window.PhotoScoutHistoryMap)label.append(window.PhotoScoutHistoryMap.thumbnail(area));
  if(h&&h.result.responseType!=='feedback'){const check=node('input');check.type='checkbox';check.checked=h.checked;check.setAttribute('aria-label','Show search: '+entry.label);check.addEventListener('change',()=>{h.checked=check.checked;persistHistory();renderHistory();drawHistoryMap({fit:true});});label.append(check);}
  const text=node('span'),detail=h?(h.result.responseType==='feedback'?(h.result.action==='help'?'Help':'Response'):`${h.result.route?(h.result.route.distanceMeters/1000).toFixed(1)+' km '+h.result.route.travelMode+' route':h.radius/1000+' km'} · ${allPoiViews(h.result).length} places`):(entry.kind==='portrait'?'Selfie · ':'')+(entry.state==='running'&&entry.kind==='search'?({sources:'Finding photos',exploring:'Exploring viewpoints',scoring:'Checking photos'}[entry.task?.context?.stage]||'Resolving location'):entry.state);
  const openEntry=()=>h?showHistorySearch(h.result,h.result.searchContext,h):viewSavedTask(entry.task);
  text.setAttribute('role','button');text.tabIndex=0;text.className='history-open';bindHistoryRow(row,openEntry);text.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openEntry();}});
  text.append(node('strong',entry.label),publicationBadge('search',entry.id));if(area){const place=node('small',window.PhotoScoutHistoryMap.peek(area)||area.label||`Around ${area.lat.toFixed(4)}, ${area.lon.toFixed(4)}`,'history-location');place.dataset.lat=area.lat;place.dataset.lon=area.lon;text.append(place);}text.append(node('small',`${new Date(entry.created).toLocaleString()} · ${detail}`));label.append(text);
  const pending=['queued','running','checking'].includes(entry.state),view=node('button',pending?'Progress':entry.state==='failed'?'Details':entry.kind==='portrait'?'View photo':'View');view.type='button';
  view.addEventListener('click',openEntry);
  const actions=node('div',null,'history-entry-actions');actions.append(view,removeHistoryButton(entry));row.append(label,actions);(entry.kind==='portrait'?photosRoot:root).append(row);
 }
 if(el('search-history').open)window.PhotoScoutHistoryMap?.hydrate(root);
}
function drawHistoryMap({fit=false}={}){photoLocationLayer.clearLayers();otherPhotoLocationLayer.clearLayers();candidatePoiLayer.clearLayers();resultPins=[];for(const h of searchHistory.filter(h=>h.checked).sort((a,b)=>Number(a.id===focusedSearchId)-Number(b.id===focusedSearchId))){const focused=h.id===focusedSearchId;for(const [i,originalSpot] of allPoiViews(h.result,h.id).entries()){const s={...originalSpot,...poiViewOverrides.get(h.id+':'+poiHistoryKey(originalSpot))};const pos=s.poi;if(!pos||!Number.isFinite(pos.lat)||!Number.isFinite(pos.lon))continue;const b=photoBearing(s),popup=node('div',null,'photo-popup');popup.append(node('strong',s.name),publicationBadge('place',h.id,s),node('p',s.score==null?'No verified image':`${s.score}/100 · ${b.label}`),node('p',h.label));const show=node('button','View place','popup-shortlist');show.type='button';const reveal=()=>{showShortlist();render(h.result,{save:false,mapUpdate:false,historyId:h.id});el('photo-spot-'+String(i+1))?.scrollIntoView({block:'nearest',behavior:'smooth'});};show.addEventListener('click',reveal);const selfie=node('button','📷 Take a selfie here','compose-photo popup-selfie');selfie.type='button';selfie.addEventListener('click',()=>openPhotoStudio(s,h.id));const actions=node('div',null,'popup-actions');actions.append(show,publishButton('place',h.id,s),removePoiButton(s,h.id));popup.append(popupPhotoPreview(s,h.id),popupViewControls(s),actions,selfie,placeSelfies(s),commentSection('place',h.id,s,null,true));if(s.sourceUrl)popup.append(link(s.provider==='google-street-view'?'Open Street View ↗':'Open original photo ↗',s.sourceUrl));const marker=L.marker([pos.lat,pos.lon],{title:`${s.name} · ${b.label} · ${h.label}`,icon:focused?photographerIcon(s,i):directionDot(s)}).addTo(focused?photoLocationLayer:otherPhotoLocationLayer).bindPopup(popup).on('popupopen',()=>{selectedPoiView={marker,popup,spot:s,searchId:h.id,index:i};loadPopupPhoto(popup);refreshVisiblePlaceSelfies();for(const box of popup.querySelectorAll('.place-selfies'))refreshPlaceSelfies(box);}).on('popupclose',()=>{if(selectedPoiView?.marker===marker)selectedPoiView=null;});marker.photoSpot=s;marker.searchId=h.id;resultPins.push(marker);}}if(fit&&resultPins.length)map.fitBounds(L.featureGroup(resultPins).getBounds().pad(.2),{paddingTopLeft:[40,80],paddingBottomRight:[40,250],maxZoom:16});}
el('history-all').addEventListener('change',()=>{for(const h of searchHistory)h.checked=el('history-all').checked;persistHistory();renderHistory();drawHistoryMap({fit:true});});
try{const legacy=localStorage.getItem(HISTORY_KEY);if(legacy&&!sessionStorage.getItem(HISTORY_KEY))sessionStorage.setItem(HISTORY_KEY,legacy);localStorage.removeItem(HISTORY_KEY);const lastVisit=Number(sessionStorage.getItem(HISTORY_KEY+'-last-visit'));if(lastVisit&&lastVisit<Date.now()-7*86400000)sessionStorage.removeItem(HISTORY_KEY);const saved=JSON.parse(sessionStorage.getItem(HISTORY_KEY)||'[]');if(Array.isArray(saved))searchHistory=saved.filter(h=>h&&typeof h.id==='string'&&typeof h.label==='string'&&h.result&&Array.isArray(h.result.spots));}catch{}renderHistory();drawHistoryMap();loadAccount().finally(()=>restoreTasks());

// The photo studio keeps personal uploads and generated images out of search history.
let studioBusy=false,studioActivityMarker=null,avatarLibrary=null;
let studioJob=null,studioFile=null,studioSpot=null,studioUrl=null,studioPreviewUrl=null,studioTimer=null,studioPrepared=null,studioUploadGeneration=0,studioInputPlaceKey=null,studioOutputFile=null,studioOutputGeneration=0;
const studio=node('dialog',null,'photo-studio');studio.setAttribute('aria-label','Photo studio');
const studioTop=node('div',null,'studio-heading'),studioTitle=node('h2','Take a selfie here'),studioClose=node('button','Close ×');studioClose.type='button';studioTop.append(studioTitle,studioClose);
const studioPlace=node('p',null,'small'),studioImages=node('div',null,'studio-inputs'),scenePreview=node('img'),personPreview=node('img');scenePreview.alt='Selected background';personPreview.alt='Your uploaded photo';personPreview.hidden=true;
const studioScene=node('figure',null,'studio-image-card'),studioPerson=node('figure',null,'studio-image-card'),studioPersonFrame=node('button',null,'studio-person-frame'),personPlaceholder=node('img',null,'studio-person-placeholder');
studioPersonFrame.type='button';studioPersonFrame.setAttribute('aria-label','Choose your selfie photo');studioPersonFrame.setAttribute('aria-expanded','false');studioPersonFrame.setAttribute('aria-controls','studio-photo-source');studioPersonFrame.title='Upload, take a photo or choose from Avatar Library';
personPlaceholder.src='./selfie-placeholder.svg';personPlaceholder.alt='A person silhouette showing where your selfie goes';
const studioSceneView=node('div',null,'studio-scene-view');studioSceneView.append(scenePreview);studioScene.append(studioSceneView,node('figcaption','Background'));studioPersonFrame.append(personPlaceholder,personPreview);studioPerson.append(studioPersonFrame,node('figcaption','Your selfie · Tap to choose'));studioImages.append(studioScene,studioPerson);
const studioIntro=node('p','For best results, upload a clear selfie or portrait showing only you or your intended group, ideally against a simple background. Avoid unrelated people, statues or busy scenery. We’ll extract the main subjects and place them in the background on the left.','studio-intro');
const uploadLabel=node('label','Upload your selfie · JPG, PNG, WebP or HEIC, up to 20 MB · automatically resized','studio-upload'),studioUpload=node('input');studioUpload.type='file';studioUpload.accept='image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif';uploadLabel.append(studioUpload);studioPersonFrame.addEventListener('click',()=>{if(!studioUpload.disabled)setStudioPhotoSourceOpen(studioPhotoSource.hidden);});
const studioCameraButton=node('button','Take a photo','studio-camera-button');studioCameraButton.type='button';
let studioCamera=null;studioCameraButton.addEventListener('click',()=>{if(studioBusy)return;studioCamera??=new PhotoScoutCamera(file=>{studioUpload.value='';acceptStudioPhoto(file);});studioCamera.open();});
const studioAvatarButton=node('button','Choose from Avatar Library','studio-camera-button');studioAvatarButton.type='button';studioAvatarButton.addEventListener('click',()=>{if(!studioBusy)avatarLibrary?.open();});uploadLabel.append(studioCameraButton,studioAvatarButton);
const studioPhotoSource=node('div',null,'studio-photo-source');studioPhotoSource.id='studio-photo-source';studioPhotoSource.hidden=true;studioPhotoSource.setAttribute('role','group');studioPhotoSource.setAttribute('aria-label','Choose photo source');studioPerson.classList.add('studio-person-choice');studioPerson.append(studioPhotoSource);
function setStudioPhotoSourceOpen(open){studioPhotoSource.hidden=!open;studioPersonFrame.setAttribute('aria-expanded',String(open));if(open)studioPhotoSource.querySelector('button')?.focus();}
for(const [label,action] of [['Upload photo',()=>studioUpload.click()],['Take a photo',()=>studioCameraButton.click()],['Choose from Avatar Library',()=>studioAvatarButton.click()]]){const button=node('button',label);button.type='button';button.addEventListener('click',()=>{setStudioPhotoSourceOpen(false);if(!studioBusy)action();});studioPhotoSource.append(button);}
studio.addEventListener('click',event=>{if(!studioPerson.contains(event.target))setStudioPhotoSourceOpen(false);});studio.addEventListener('keydown',event=>{if(event.key==='Escape'&&!studioPhotoSource.hidden){event.preventDefault();event.stopPropagation();setStudioPhotoSourceOpen(false);studioPersonFrame.focus();}});studio.addEventListener('close',()=>setStudioPhotoSourceOpen(false));

let studioStyle='natural',studioAdjustedView=null,studioSearchId=null;
const studioStyles=node('fieldset',null,'studio-styles'),studioStyleLegend=node('legend','Photo style'),studioStyleGrid=node('div',null,'studio-style-grid'),studioStyleHint=node('p','Relaxed pose, soft smile, your original outfit.','small');
const portraitStyles=[['natural','Natural','Relaxed pose, soft smile, your original outfit.'],['street','Street style','Confident pose, contemporary urban clothing, candid expression.'],['cinematic','Cinematic','Expressive pose, understated clothing, a thoughtful look.'],['vacation','Vacation','Relaxed holiday pose, comfortable clothing, a cheerful smile.'],['editorial','Editorial','Elegant pose, refined clothing, a polished magazine look.']];
for(const [id,label,hint] of portraitStyles){const choice=node('label',null,'studio-style'),input=node('input');input.type='radio';input.name='portrait-style';input.value=id;input.checked=id==='natural';input.addEventListener('change',()=>{if(input.checked){studioStyle=id;studioStyleHint.textContent=hint;}});choice.append(input,node('span',label));studioStyleGrid.append(choice);}
studioStyles.append(studioStyleLegend,studioStyleGrid,studioStyleHint,node('p','Your location stays the same. Styles adjust your look; weather can change the light and atmosphere.','small'));
const studioOptions=node('fieldset',null,'studio-options'),studioOptionsLegend=node('legend','Make it yours'),studioOptionsGrid=node('div',null,'studio-options-grid');
function studioSelect(label,name,options){const wrapper=node('label',label),select=node('select');select.name=name;select.setAttribute('aria-label',label);for(const [value,text] of options){const option=node('option',text);option.value=value;select.append(option);}select.value=options[0][0];wrapper.append(select);studioOptionsGrid.append(wrapper);return select;}
const studioComposition=studioSelect('Subject framing','composition',[['auto','Auto'],['full_body','Full body'],['half_body','Half body'],['close_up','Close-up']]);
const studioPosture=studioSelect('Posture','posture',[['auto','Auto'],['standing','Relaxed standing'],['walking','Candid walking'],['sitting','Seated'],['looking_back','Looking back'],['playful','Playful']]);
const studioWeather=studioSelect('Weather & light','weather',[['original','Keep original'],['daytime','Daytime'],['night','Night'],['sunny','Sunny daylight'],['golden_hour','Golden hour'],['overcast','Soft overcast'],['rainy','Gentle rain'],['snowy','Gentle snow']]);
const studioExpression=studioSelect('Expression','expression',[['auto','Auto'],['soft_smile','Soft smile'],['big_smile','Big smile'],['thoughtful','Thoughtful'],['serious','Confident & serious'],['surprised','Playful surprise']]);
const studioFraming=studioSelect('Street View framing','framing',[['auto','Auto · choose 90°, 60° or 45°'],['90','90° · Wide'],['60','60° · Natural'],['45','45° · Close'],['current','Use adjusted view']]);
const studioFramingHint=node('p',null,'small');function updateStudioFramingHint(){studioFramingHint.hidden=studioSpot?.provider!=='google-street-view';studioFramingHint.textContent=studioFraming.value==='auto'?'Preview shows 90°. When generating, AI compares 90°, 60° and 45° and chooses the best framing.':'The background preview shows your selected framing.';}studioOptions.append(studioOptionsLegend,studioOptionsGrid,studioFramingHint);
const poseLabel=node('label','Additional directions (optional)','studio-upload'),studioPose=node('textarea');studioPose.maxLength=500;studioPose.placeholder='For example: standing naturally, full body, a relaxed smile';poseLabel.append(studioPose);
const studioNote=node('p','Your photo and this view will be sent to OpenAI to create an AI composite. Your upload is removed after processing; signed-in results are saved permanently; guest results are deleted after 7 days without a visit.','small');
const studioGenerate=node('button','Create my photo · Free test','studio-generate');studioGenerate.type='button';studioGenerate.disabled=true;
const studioStatus=node('p',null,'studio-status');studioStatus.setAttribute('role','status');studioStatus.setAttribute('aria-live','polite');
const studioResult=node('img',null,'studio-result');studioResult.alt='AI-generated travel photo';studioResult.hidden=true;
const studioSave=node('button','Share','studio-save');studioSave.type='button';studioSave.hidden=true;
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
function resetStudioInputForPlace(spot){
 const key=JSON.stringify([studioSearchId,spot.poi?.id||spot.id||null,spot.poi?.lat??spot.lat??null,spot.poi?.lon??spot.lon??null,spot.poi?.name||spot.name||null]);
 if(key===studioInputPlaceKey)return;
 studioInputPlaceKey=key;studioUploadGeneration++;studioFile=null;studioPrepared=null;studioUpload.value='';personPreview.hidden=true;personPreview.removeAttribute('src');studioGenerate.disabled=true;
 if(studioPreviewUrl){URL.revokeObjectURL(studioPreviewUrl);studioPreviewUrl=null;}
}
function openPhotoStudio(spot,searchId=null){studioSearchId=searchId;spot={...spot,...poiViewOverrides.get(searchId+':'+poiHistoryKey(spot))};focusMapSpot(spot,{openPopup:false});if(savedPhoto.open)savedPhoto.close();if(portraitProgress.open)portraitProgress.close();{if(!taskRecords.some(t=>t.kind==='portrait'&&['queued','checking','running'].includes(t.state)))studioTask.hidden=true;resetStudioInputForPlace(spot);const savedView=savedSelfieView(studioSearchId,spot);studioSpot={...spot,...savedView};studioFraming.value=savedView?.framing||(spot.viewAdjusted?'current':'auto');studioAdjustedView=spot.provider==='google-street-view'?window.PhotoScoutStreetView.view(studioSpot):null;if(studioAdjustedView&&!studioSpot.viewAdjusted)applyStreetView(studioSpot,{...studioAdjustedView,fov:90},null);studioSceneView.streetViewSearchId=null;studioSceneView.streetViewController?.reset();studioFraming.disabled=spot.provider!=='google-street-view';studioFraming.parentElement.hidden=spot.provider!=='google-street-view';updateStudioFramingHint();studioPlace.textContent=studioSpot.name+(photoBearing(studioSpot).heading!=null?' · '+photoBearing(studioSpot).label:'');scenePreview.removeAttribute('src');if(spot.provider==='google-street-view'||spot.imageReference)refreshThumbnail(scenePreview,studioSpot);else if(spot.imageUrl)scenePreview.src=spot.imageUrl;studioStatus.textContent='Upload a clear image of a person, cartoon character or animal. Groups are welcome; a clearly visible subject works best.';clearStudioOutput();studioGenerate.disabled=studioBusy||!studioPrepared;if(studioBusy)studioStatus.textContent='Another photo is being uploaded. This place has its own photo; you can upload it when that submission finishes.';}syncStudioProgress();studio.showModal();if(window.PhotoScoutNativeStreetView)activateNativeStreetView(studioSceneView,()=>studioSpot,null,{isDisabled:()=>studioBusy,onChange:view=>{studioAdjustedView={...view};applyStudioView(view);studioFraming.value='current';updateStudioFramingHint();studioPlace.textContent=studioSpot.name+' · '+photoBearing(studioSpot).label;}});}
studioClose.addEventListener('click',()=>studio.close());studio.addEventListener('close',()=>{if(!studio.open)window.PhotoScoutNativeStreetView?.detach(studioSceneView);});
bindStreetViewPreview(studioSceneView,()=>studioSpot,null,{isDisabled:()=>studioBusy,onChange:view=>{studioAdjustedView={...view};applyStudioView(view);studioFraming.value='current';updateStudioFramingHint();studioPlace.textContent=studioSpot.name+' · '+photoBearing(studioSpot).label;},onCommit:()=>refreshThumbnail(scenePreview,studioSpot)});
studioFraming.addEventListener('change',()=>{
 if(studioBusy||studioSpot?.provider!=='google-street-view')return;
 studioSceneView.streetViewController?.reset();
 const current=window.PhotoScoutStreetView.view(studioSpot),view=studioFraming.value==='current'?(studioAdjustedView||current):{...current,fov:studioFraming.value==='auto'?90:Number(studioFraming.value)};
 applyStudioView(view,studioFraming.value);updateStudioFramingHint();studioSceneView.streetViewController?.refresh();if(!window.PhotoScoutNativeStreetView?.refresh(studioSceneView))refreshThumbnail(scenePreview,studioSpot);
});
studioUpload.addEventListener('change',()=>{const file=studioUpload.files[0];if(file)acceptStudioPhoto(file);});
async function acceptStudioPhoto(file){const generation=++studioUploadGeneration;studioFile=file||null;studioPrepared=null;personPreview.hidden=true;personPreview.removeAttribute('src');studioGenerate.disabled=true;if(studioPreviewUrl)URL.revokeObjectURL(studioPreviewUrl);if(!studioFile)return;if(studioFile.size>20000000){studioStatus.textContent='Choose a photo up to 20 MB.';studioFile=null;return;}if(!['image/jpeg','image/png','image/webp','image/heic','image/heif',''].includes(studioFile.type)&&!/\.(jpe?g|png|webp|heic|heif)$/i.test(studioFile.name)){studioStatus.textContent='Choose a JPG, PNG, WebP or HEIC photo.';studioFile=null;return;}studioStatus.textContent='Preparing your photo…';try{const prepared=await prepareStudioPhoto(studioFile);if(generation!==studioUploadGeneration)return;studioPrepared=prepared.data;personPreview.src=prepared.data;personPreview.hidden=!prepared.preview;studioGenerate.disabled=studioBusy;studioStatus.textContent=prepared.preview?'Your photo is ready. It has been resized for upload.':'Your phone photo will be converted securely when you submit. This browser cannot display its original format.';}catch(error){if(generation!==studioUploadGeneration)return;studioFile=null;studioStatus.textContent=error.message;}}
async function prepareStudioPhoto(file){const data=await readPhoto(file);return new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>{try{if(image.naturalWidth*image.naturalHeight>80000000)throw Error('This photo exceeds 80 megapixels. Export a smaller copy.');const scale=Math.min(1,2048/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);resolve({data:canvas.toDataURL('image/jpeg',.92),preview:true});}catch(error){reject(error);}};image.onerror=()=>resolve({data,preview:false});image.src=data;});}
function readPhoto(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('Could not read the photo'));reader.readAsDataURL(file);});}
studioGenerate.addEventListener('click',async()=>{
 if(!tasksReady||!studioPrepared||!studioSpot||studioBusy)return;studioBusy=true;studioGenerate.disabled=true;studioCameraButton.disabled=studioPersonFrame.disabled=studioUpload.disabled=true;studioPose.disabled=true;studioStyles.disabled=true;studioOptions.disabled=true;studioStatus.classList.add('working');studioStatus.textContent='Uploading your photo…';
 const submissionPlaceKey=studioInputPlaceKey,submissionUploadGeneration=studioUploadGeneration,submissionSession=taskRecoveryGeneration;const spot={...studioSpot},payload={portrait:studioPrepared,background:spot.provider==='google-street-view'?spot.sourceUrl:(spot.imageReference||spot.imageUrl),provider:spot.provider,place:spot.name,pose:studioPose.value.trim(),style:studioStyle,composition:studioComposition.value,posture:studioPosture.value,weather:studioWeather.value,expression:studioExpression.value,framing:studioFraming.value,lat:spot.poi?.lat??null,lon:spot.poi?.lon??null};
 try{const job=await json('/photo-scout/v1/portraits',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(submissionSession!==taskRecoveryGeneration)return;addSubmittedTask('portrait',job,{...spot,generation:{style:payload.style,composition:payload.composition,posture:payload.posture,weather:payload.weather,expression:payload.expression,framing:payload.framing,directions:payload.pose}});if(submissionSession!==taskRecoveryGeneration)return;if(submissionPlaceKey===studioInputPlaceKey&&submissionUploadGeneration===studioUploadGeneration){studioStatus.textContent='Your selfie is generating in the background. You can close this window or create another photo.';studioPending.scrollIntoView({block:'nearest',behavior:'smooth'});}syncPortraitActivities();restoreTasks();}
 catch(error){if(submissionSession===taskRecoveryGeneration&&submissionPlaceKey===studioInputPlaceKey&&submissionUploadGeneration===studioUploadGeneration)studioStatus.textContent=error.message;}
 finally{if(submissionSession!==taskRecoveryGeneration)return;studioBusy=false;if(submissionPlaceKey!==studioInputPlaceKey&&studioStatus.textContent.startsWith('Another photo is being uploaded.'))studioStatus.textContent=studioPrepared?'Your photo is ready.':'Upload a clear image of a person, cartoon character or animal. Groups are welcome; a clearly visible subject works best.';studioGenerate.disabled=!studioPrepared;studioCameraButton.disabled=studioPersonFrame.disabled=studioUpload.disabled=false;studioPose.disabled=false;studioStyles.disabled=false;studioOptions.disabled=false;studioStatus.classList.remove('working');}
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
 studioSaveHint.textContent='Tap Share to choose an app or Save Image in the system menu. You can also press and hold the photo to save it. Downloads may go to Files on your device.';
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
 const route=context?.routeGeometry||(context?.route?.geometry?context.route:null)||(!context?activeRouteGeometry:null);if(route?.geometry?.coordinates?.length){showRoute(route);const dockHeight=document.querySelector('.scout-dock').getBoundingClientRect?.().height||120;map.fitBounds(activeRouteBounds.pad(.18),{paddingTopLeft:[window.innerWidth>900&&!el('results').hidden?Math.ceil(el('results').getBoundingClientRect().width)+48:32,80],paddingBottomRight:[32,Math.ceil(dockHeight)+24],maxZoom:17});return;}
 const usePinBounds=!context&&map.hasLayer(searchArea);if(!context){syncMapSelection();context={lat:Number(el('lat').value),lon:Number(el('lon').value),radius:Number(el('radius').value)};}
 const radius=context.radius||1000,latDelta=radius/111320,lonDelta=latDelta/Math.cos((context.lat||0)*Math.PI/180);
 const bounds=usePinBounds?searchArea.getBounds():[[context.lat-latDelta,context.lon-lonDelta],[context.lat+latDelta,context.lon+lonDelta]];
 const dockHeight=document.querySelector('.scout-dock').getBoundingClientRect?.().height||120;
 map.fitBounds(bounds,{paddingTopLeft:[24,80],paddingBottomRight:[24,Math.ceil(dockHeight)+24],maxZoom:18});
}
function applyRouteControls(route){restoredRoute=route||null;el('search-mode').value=route?'route':'nearby';el('route-origin').value=route?.origin?.query||(route?.origin?.label==='Selected map location'?'':route?.origin?.label)||'';el('route-destination').value=route?.destination?.query||route?.destination?.label||'';el('route-travel').value=route?.travelMode||'walk';const corridor=String(route?.corridorMeters||50);if(![...el('route-corridor').options].some(o=>o.value===corridor)){const option=node('option','Within '+corridor+' m');option.value=corridor;el('route-corridor').append(option);}el('route-corridor').value=corridor;updateSearchMode();}
function applyTaskContext(context){
 applyRouteControls(context.route);const routeChanged=JSON.stringify(activeRouteGeometry?.geometry)!==JSON.stringify(context.routeGeometry?.geometry);if(routeChanged){showRoute(context.routeGeometry);if(context.routeGeometry)fitSearchRange(context);}
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
 let area=context||result?.searchContext;if(!area?.route&&!result?.route){showRoute(null);applyRouteControls(null);}
 if(!area||!Number.isFinite(area.lat)||!Number.isFinite(area.lon)){
  const match=history?.label.match(/^Around\s+(-?[\d.]+),\s*(-?[\d.]+)/);
  if(match)area={lat:Number(match[1]),lon:Number(match[2]),radius:history.radius};
 }
 if(area&&Number.isFinite(area.lat)&&Number.isFinite(area.lon)){
  applyTaskContext({...area,routeGeometry:area.routeGeometry||result?.route,radius:area.radius||history?.radius||1000,nearbyPois:undefined});el('prompt-query').value=area.query||'';
  // Always refit, including repeated View clicks after manually panning away.
  if(area.routeGeometry||result?.route)fitSearchRange({...area,routeGeometry:area.routeGeometry||result?.route});else map.fitBounds(searchArea.getBounds(),{paddingTopLeft:[24,72],paddingBottomRight:[24,140],maxZoom:18});
 }else{
  const places=allPoiViews(result||{}).filter(s=>Number.isFinite(s.poi?.lat)&&Number.isFinite(s.poi?.lon));
  if(places.length)map.fitBounds(L.featureGroup(places.map(s=>L.circleMarker([s.poi.lat,s.poi.lon]))).getBounds().pad(.15),{padding:[40,40],maxZoom:16});
 }
}
function showHistorySearch(result,context,history){
 el('search-progress').hidden=true;el('message').textContent='';
 if(result.responseType==='feedback'){activeSearch=null;stopPoiScan();el('search-history').open=false;render(result,{save:false,mapUpdate:false});return;}
 activeSearch=null;stopPoiScan();focusedSearchId=history?.id||searchHistory.find(h=>h.result===result)?.id||null;
 el('search-history').open=false;
 if(history&&!history.checked){history.checked=true;persistHistory();renderHistory();}
 render(result,{save:false,mapUpdate:false,historyId:history?.id});drawHistoryMap();
 // Show the recorded area together with its complete shortlist.
 el('results').hidden=false;
 focusHistorySearch(context,result,history);
}
const savedPhoto=node('dialog',null,'photo-studio saved-photo');savedPhoto.setAttribute('aria-label','Saved photo');
let savedPhotoBackdropPressed=false;
function outsideSavedPhoto(event){const r=savedPhoto.getBoundingClientRect();return event.target===savedPhoto&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom);}
savedPhoto.addEventListener('pointerdown',event=>{savedPhotoBackdropPressed=event.isPrimary&&event.button===0&&outsideSavedPhoto(event);});
savedPhoto.addEventListener('pointercancel',()=>{savedPhotoBackdropPressed=false;});
savedPhoto.addEventListener('click',event=>{const dismiss=savedPhotoBackdropPressed&&outsideSavedPhoto(event);savedPhotoBackdropPressed=false;if(dismiss){event.preventDefault();event.stopPropagation();savedPhoto.close();}});
savedPhoto.addEventListener('close',()=>{savedPhotoBackdropPressed=false;});
const savedPhotoTop=node('div',null,'studio-heading'),savedPhotoTitle=node('h2','Your saved selfie'),savedPhotoClose=node('button','Close ×');savedPhotoClose.type='button';savedPhotoTop.append(savedPhotoTitle,savedPhotoClose);
const savedPhotoPlace=node('p',null,'small'),savedPhotoImage=node('img',null,'studio-result'),savedPhotoStatus=node('p',null,'studio-status'),savedPhotoParams=node('div',null,'saved-photo-params'),savedPhotoSave=node('button','Share','studio-save'),savedPhotoDownload=node('a','Download PNG','studio-save studio-download'),savedPhotoHint=node('p',null,'small');savedPhotoImage.alt='Saved AI-generated travel photo';savedPhotoStatus.setAttribute('role','status');savedPhotoDownload.download='photo-scout-ai-photo.png';savedPhotoDownload.addEventListener('click',event=>{if(!savedPhotoFile)event.preventDefault();});
const savedPhotoComments=node('div',null,'saved-photo-comments');
const savedPhotoBackground=node('section',null,'saved-photo-background');
const savedPhotoActions=node('div',null,'saved-photo-actions');savedPhotoActions.setAttribute('role','group');savedPhotoActions.setAttribute('aria-label','Photo actions');savedPhotoActions.append(savedPhotoSave,savedPhotoDownload);
const savedPhotoNav=node('nav',null,'saved-photo-nav');savedPhotoNav.setAttribute('aria-label','Navigate to this photo location');
savedPhoto.append(savedPhotoTop,savedPhotoPlace,savedPhotoImage,savedPhotoStatus,savedPhotoComments,savedPhotoBackground,savedPhotoActions,savedPhotoHint);document.body.append(savedPhoto);
let savedPhotoGeneration=0,savedPhotoTimer=null,savedPhotoFile=null,savedPhotoUrl=null;
function savedPhotoNavigation(context){
 const {lat,lon}=context?.poi||{};
 if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180)return [];
 const destination=lat+','+lon;
 return [['Google Maps','https://www.google.com/maps/dir/?api=1&destination='+encodeURIComponent(destination)],['Apple Maps','https://maps.apple.com/?daddr='+encodeURIComponent(destination)]];
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
  fields.push(['Style',portraitStyles.find(s=>s[0]===g.style)?.[1]||g.style],['Subject framing',label(studioComposition,g.composition||'auto')],['Posture',label(studioPosture,g.posture)],['Weather & light',label(studioWeather,g.weather)],['Expression',label(studioExpression,g.expression)],['Street View framing',label(studioFraming,g.framing||'auto')],['Your directions',g.directions||'None']);
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
 savedPhotoTitle.textContent='Photo';savedPhotoComments.replaceChildren(commentSection('photo',sourceId,null,publication,true));
 savedPhotoPublish.replaceChildren(...(sourceId?[publishButton('photo',sourceId)]:[]));
 if(studio.open)studio.close();if(portraitProgress.open)portraitProgress.close();
 const generation=++savedPhotoGeneration;clearTimeout(savedPhotoTimer);if(savedPhotoUrl)URL.revokeObjectURL(savedPhotoUrl);savedPhotoUrl=null;savedPhotoFile=null;
 savedPhotoImage.hidden=true;savedPhotoImage.removeAttribute('src');savedPhotoSave.hidden=false;savedPhotoSave.disabled=true;savedPhotoDownload.hidden=false;savedPhotoDownload.removeAttribute('href');savedPhotoDownload.setAttribute('aria-disabled','true');savedPhotoDownload.setAttribute('tabindex','-1');savedPhotoHint.textContent='Share and download will be ready when the photo finishes loading.';savedPhotoStatus.textContent='Loading your saved photo…';renderSavedPhotoParams(task.context,task.created);if(!savedPhoto.open)savedPhoto.showModal();
 const refresh=async()=>{try{
  const report=publication?{state:'complete',context:publication.context}:await json('/photo-scout/v1/portraits/'+encodeURIComponent(task.id));if(generation!==savedPhotoGeneration)return;
  renderSavedPhotoParams(report.context||task.context,task.created);focusMapSpot(report.context||task.context,{openPopup:false});
  if(report.state==='complete'){
   const response=await fetch(publication?publication.imageUrl:api+'/photo-scout/v1/portraits/'+encodeURIComponent(task.id)+'/image',{credentials:'include'});if(!response.ok)throw Error('Could not load your saved photo');const blob=await response.blob(),preview=await readPhoto(blob);if(generation!==savedPhotoGeneration)return;
   savedPhotoFile=new File([blob],'photo-scout-ai-photo.png',{type:'image/png'});savedPhotoUrl=URL.createObjectURL(blob);savedPhotoImage.src=preview;savedPhotoImage.hidden=false;savedPhotoSave.hidden=false;savedPhotoSave.disabled=false;savedPhotoDownload.href=savedPhotoUrl;savedPhotoDownload.removeAttribute('aria-disabled');savedPhotoDownload.removeAttribute('tabindex');savedPhotoDownload.hidden=false;savedPhotoStatus.textContent=publication?'AI-generated photo · Published publicly.':task.expiresAt===null||authUser?'AI-generated photo · Saved permanently to your account.':'AI-generated photo · Guest photo kept until 7 days after your last visit.';savedPhotoHint.textContent='Use Share to choose an app or save the image. You can also press and hold the photo to save it.';return;
  }
  if(report.state==='failed'){savedPhotoStatus.textContent=report.error||'This photo could not be created.';return;}
  savedPhotoStatus.textContent=report.state==='queued'?'Your selfie is queued…':'Your selfie is being created…';savedPhotoTimer=setTimeout(refresh,4000);
 }catch(error){if(generation===savedPhotoGeneration)savedPhotoStatus.textContent=error.message;}};
 await refresh();
}
savedPhotoClose.addEventListener('click',()=>savedPhoto.close());savedPhoto.addEventListener('close',()=>{if(savedPhoto.open)return;savedPhotoGeneration++;clearTimeout(savedPhotoTimer);if(savedPhotoUrl)URL.revokeObjectURL(savedPhotoUrl);savedPhotoUrl=null;savedPhotoFile=null;savedPhotoImage.removeAttribute('src');});
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
function resetTaskRecovery(){avatarLibrary?.reset();
 photoLibraryGeneration++;el('photo-library-items').replaceChildren();selectPhotoLibraryTab('mine');
 if(studioCamera?.dialog.open)studioCamera.dialog.close();studioCamera?.stop();
 if(savedPhoto.open)savedPhoto.close();if(portraitProgress.open)portraitProgress.close();
 focusedSearchId=null;selectedPoiView=null;lastRemovedPoi=null;lastRemovedHistory=null;removedHistoryItems.clear();poiViewOverrides.clear();selfieViewOverrides.clear();for(const save of cameraSaves.values()){clearTimeout(save.timer);save.payload=null;}cameraSaves.clear();for(const timer of cameraRefreshes.values())clearTimeout(timer);cameraRefreshes.clear();studioSearchId=null;taskRecoveryGeneration++;hydratedSearches.clear();clearTimeout(taskRefreshTimer);taskRecords=[];hiddenPoisBySearch.clear();displayedResult=null;displayedSearchId=null;tasksReady=false;activeSearch=null;pollGeneration++;searchBusy=false;studioBusy=false;studioJob=null;clearTimeout(studioTimer);clearStudioOutput();studioPrepared=null;studioFile=null;studioInputPlaceKey=null;studioUploadGeneration++;studioUpload.value='';studioCameraButton.disabled=studioPersonFrame.disabled=studioUpload.disabled=false;studioPose.disabled=false;studioStyles.disabled=false;studioOptions.disabled=false;studioGenerate.disabled=true;personPreview.hidden=true;personPreview.removeAttribute('src');studioTask.hidden=true;selfieActivityLayer.clearLayers();renderHistory();updateSubmitState();
}
// Bounded parallel recovery; paint each available group without waiting for a slow report.
async function restoreCompletedSearches(tasks,generation){
 const pending=tasks.filter(t=>t.kind==='search'&&t.state==='complete'&&!hydratedSearches.has(t.id));
 let next=0,changed=false,paintTimer=null;
 const paint=()=>{paintTimer=null;if(generation!==taskRecoveryGeneration)return;searchHistory.sort((a,b)=>b.created-a.created);drawHistoryMap();renderHistory();};
 async function worker(){
  while(next<pending.length&&generation===taskRecoveryGeneration){
   const task=pending[next++];
   try{
    const report=await json('/photo-scout/v1/report/'+encodeURIComponent(task.id));
    if(generation!==taskRecoveryGeneration)return;
    if(!report.result||removedHistoryItems.has('search:'+task.id))continue;
    const existing=searchHistory.find(h=>h.id===task.id),record={id:task.id,created:task.created*1000,label:task.context.query||task.context.locationLabel||`Around ${task.context.lat}, ${task.context.lon}`,radius:task.context.radius||1000,checked:existing?.checked??true,result:{...report.result,searchContext:task.context}};
    if(existing)Object.assign(existing,record);else searchHistory.push(record);
    hydratedSearches.add(task.id);changed=true;
    if(paintTimer===null)paintTimer=setTimeout(paint,50);
   }catch{/* Retry this report on the next refresh; other saved places can still appear. */}
  }
 }
 await Promise.allSettled(Array.from({length:Math.min(4,pending.length)},()=>worker()));
 clearTimeout(paintTimer);if(changed)paint();return changed&&generation===taskRecoveryGeneration;
}
async function restoreTasks(){
 if(taskRefreshBusy)return;taskRefreshBusy=true;const generation=taskRecoveryGeneration,initial=!tasksReady;let changed=false;
 try{
  const visiting=!document.hidden;const data=await json('/photo-scout/v1/tasks'+(visiting?'?visit=true':''));if(visiting){try{sessionStorage.setItem(HISTORY_KEY+'-last-visit',String(Date.now()));}catch{}}if(generation!==taskRecoveryGeneration)return;tasksReady=true;data.items=data.items.filter(t=>!removedHistoryItems.has(t.kind+':'+t.id));
  const incomingHidden=new Map(Object.entries(data.hiddenPois||{}).map(([id,keys])=>[id,new Set(keys)]));
  if(JSON.stringify([...incomingHidden].map(([id,keys])=>[id,[...keys]]))!==JSON.stringify([...hiddenPoisBySearch].map(([id,keys])=>[id,[...keys]]))){hiddenPoisBySearch=incomingHidden;changed=true;refreshDisplayedShortlist();}
  const returned=new Set(data.items.map(t=>t.kind+':'+t.id));taskRecords=[...data.items,...taskRecords.filter(t=>t.localPending&&!returned.has(t.kind+':'+t.id))].sort((a,b)=>b.created-a.created);renderHistory();refreshVisiblePlaceSelfies();
  changed=(await restoreCompletedSearches(taskRecords,generation))||changed;if(generation!==taskRecoveryGeneration)return;
  searchHistory.sort((a,b)=>b.created-a.created);if(changed){persistHistory();drawHistoryMap();}renderHistory();syncPortraitActivities();refreshVisiblePlaceSelfies();
  if(initial&&!activeSearch){const task=taskRecords.find(t=>t.kind==='search'&&['queued','running'].includes(t.state));if(task){activeSearch={jobId:task.id,context:task.context};applyTaskContext(task.context);activeSearch.draft=searchDraft();}}
  const focus=taskRecords.find(t=>t.kind==='search'&&t.id===activeSearch?.jobId);
  if(focus){
   const follow=!activeSearch.draft||activeSearch.draft===searchDraft();activeSearch.context=focus.context;
   if(follow){applyTaskContext(focus.context);activeSearch.draft=searchDraft();}
   if(focus.state==='complete'){
    const h=searchHistory.find(h=>h.id===focus.id);if(h){if(h.result.responseType!=='feedback'){focusedSearchId=h.id;drawHistoryMap();}render(h.result,{save:false,mapUpdate:false});el('results').hidden=false;if(h.result.responseType!=='feedback'){fitSearchRange(focus.context);setProgress(2,'Your shortlist is ready','Results are shown in Shortlist and saved in Search history.','complete');}else{el('search-progress').hidden=true;message(h.result.summary);}}activeSearch=null;stopPoiScan();
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
const savedPhotoPublish=node('div',null,'publication-actions');savedPhotoActions.append(savedPhotoPublish);
const publicationDialog=node('dialog',null,'photo-studio publication-dialog');publicationDialog.setAttribute('aria-label','Publication link');document.body.append(publicationDialog);
function showPublicationLink(item){
 const heading=node('div',null,'studio-heading'),close=node('button','Close ×');close.type='button';close.addEventListener('click',()=>publicationDialog.close());heading.append(node('h2','Published'),close);
 const url=link(item.url,item.url);url.className='publication-url';
 const copy=node('button','Copy link','studio-save');copy.type='button';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(item.url);copy.textContent='Link copied';}catch{copy.textContent='Select the link above to copy it';}});
 publicationDialog.replaceChildren(heading,node('p','Anyone can view this publication, including visitors who are not signed in.','small'),url,copy);if(!publicationDialog.open)publicationDialog.showModal();
}
function commentSection(kind,id,spot=null,publication=null,compact=false){
 return window.PhotoScoutComments.create({compact,commentsEnabled:kind==='photo',request:json,headers:()=>csrfToken?{'X-CSRF-Token':csrfToken}:{},onReaction:()=>{for(const box of document.querySelectorAll('.comments-section'))box.refreshComments?.();if(el('search-history').open)loadFavorites();refreshPhotoLibraryIfOpen();},signIn:()=>{if(el('account-login').disabled){message('Sign-in is temporarily unavailable. Please try again.');return;}persistHistory();location.href=api+'/photo-scout/v1/auth/login';},resolve:()=>{
  if(publication)return {id:publication.id,poi:spot&&publication.kind==='search'?poiHistoryKey(spot):''};
  if(id?.startsWith('public:')){const item=publicationItems.find(p=>p.id===id.slice(7));return {id:id.slice(7),poi:spot&&item?.kind!=='place'?poiHistoryKey(spot):''};}
  const own=ownPublications.get(publicationKey(kind,id,spot?poiHistoryKey(spot):''));
  if(own)return {id:own.id,poi:''};
  const search=spot&&ownPublications.get(publicationKey('search',id));return search?{id:search.id,poi:poiHistoryKey(spot)}:null;
 }});
}
function refreshComments(){window.PhotoScoutComments.clear();for(const box of document.querySelectorAll('.comments-section'))box.refreshComments?.(true);}
function publicationKey(kind,id,poi=''){return JSON.stringify([kind,id,poi]);}
function publicationBadge(kind,id,spot){const badge=node('span','Published ✓','publication-badge');badge.dataset.publicationBadge=publicationKey(kind,id,spot?poiHistoryKey(spot):'');badge.hidden=!ownPublications.has(badge.dataset.publicationBadge);return badge;}
function syncPublishButtons(){for(const badge of document.querySelectorAll('[data-publication-badge]'))badge.hidden=!ownPublications.has(badge.dataset.publicationBadge);for(const button of document.querySelectorAll('button[data-publication-key]')){const published=ownPublications.has(button.dataset.publicationKey);button.textContent=published?'Published ✓ · Unpublish':button.dataset.publishLabel;button.setAttribute('aria-pressed',String(published));}}
function publishButton(kind,id,spot){
 const label='Publish'+(kind==='place'?' place':kind==='photo'?' photo':''),button=node('button',label,'publish-button');button.type='button';button.hidden=!id||id.startsWith('public:');button.dataset.publishLabel=label;button.dataset.publicationKey=publicationKey(kind,id,spot?poiHistoryKey(spot):'');button.setAttribute('aria-pressed',String(ownPublications.has(button.dataset.publicationKey)));if(ownPublications.has(button.dataset.publicationKey))button.textContent='Published ✓ · Unpublish';
 button.onclick=async()=>{const existing=ownPublications.get(button.dataset.publicationKey);button.disabled=true;button.textContent=existing?'Unpublishing…':'Publishing…';try{
  if(existing){await json('/photo-scout/v1/publications/withdraw',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({id:existing.id})});}
  else{const item=await json('/photo-scout/v1/publications',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({kind,id,...(spot?{poiId:poiHistoryKey(spot)}:{})})});showPublicationLink(item);}
  await loadPublications();
 }catch(error){message(error.message);if(savedPhoto.open)savedPhotoStatus.textContent=error.message;}finally{button.disabled=false;syncPublishButtons();}};return button;
}
async function loadPublications(more=false){
 try{const [data,mine]=await Promise.all([json('/photo-scout/v1/publications'+(more&&publicationCursor?'?before='+publicationCursor:'')),json('/photo-scout/v1/publications/mine')]);ownPublications=new Map(mine.items.map(item=>[publicationKey(item.kind,item.sourceId,item.poiId),item]));syncPublishButtons();const selection=new Map(publicationItems.map(item=>[item.id,item.checked]));const incoming=data.items.map(item=>({...item,checked:selection.get(item.id)!==false}));publicationItems=more?[...publicationItems,...incoming]:incoming;publicationCursor=data.nextBefore;renderPublications();drawPublications();refreshComments();}
 catch{el('published-items').replaceChildren(node('p','Could not load public items. Reopen this menu to retry.','small'));}
}
function renderPublications(){
 el('published-more').hidden=!publicationCursor;const root=el('published-items');root.replaceChildren();
 if(!publicationItems.some(item=>item.kind!=='photo'))root.append(node('p','No public searches or places yet.','small'));
 for(const item of publicationItems.filter(item=>item.kind!=='photo')){const row=node('div',null,'history-item'),copy=node('span',null,'history-copy'),check=node('input');check.type='checkbox';check.checked=item.checked!==false;check.setAttribute('aria-label','Show publication: '+item.title);check.addEventListener('change',()=>{item.checked=check.checked;drawPublications();});
 const text=node('span');text.append(node('strong',item.title),node('span','Published ✓','publication-badge'),node('small',item.kind==='photo'?'AI selfie':item.kind==='place'?'Photo place':'Search · '+(item.result?.spots?.length||0)+' places'));copy.append(check,text);
 const actions=node('div',null,'history-entry-actions'),view=node('button','View');view.type='button';view.addEventListener('click',()=>openPublication(item));actions.append(view,link('Share ↗',item.url));
 if(item.mine){const withdraw=node('button','Unpublish');withdraw.type='button';withdraw.addEventListener('click',async()=>{withdraw.disabled=true;try{await json('/photo-scout/v1/publications/withdraw',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({id:item.id})});if(displayedSearchId==='public:'+item.id){el('results').hidden=true;displayedResult=null;}if(savedPhoto.dataset.publication===item.id)savedPhoto.close();await loadPublications();}catch(error){withdraw.disabled=false;message(error.message);}});actions.append(withdraw);}
 row.append(copy,actions);root.append(row);}
}
function publishedSelfieIcon(item){return L.divIcon({className:'published-selfie-pin',html:'<span><img src="'+api+'/photo-scout/v1/publications/'+encodeURIComponent(item.id)+'/thumbnail" alt=""><b>✦</b></span>',iconSize:[48,58],iconAnchor:[24,55],popupAnchor:[0,-48]});}
function drawPublications(){
 publicationLayer.clearLayers();for(const item of publicationItems.filter(i=>i.checked!==false)){const spots=item.kind==='photo'?[item.context]:item.result?.spots||[];for(const [index,spot] of spots.entries()){const pos=spot?.poi;if(!pos||![pos.lat,pos.lon].every(Number.isFinite))continue;
 const marker=L.marker([pos.lat,pos.lon],{zIndexOffset:item.kind==='photo'?1100:0,title:(item.kind==='photo'?'Published AI selfie · ':'Published place · ')+spot.name,icon:item.kind==='photo'?publishedSelfieIcon(item):photographerIcon(spot,index)}).addTo(publicationLayer);
 if(item.kind==='photo')marker.on('click',()=>openPublication(item));else{const popup=node('div',null,'photo-popup');popup.append(node('strong',spot.name),node('p',spot.score+'/100 · '+photoBearing(spot).label),popupPhotoPreview(spot,'public:'+item.id));marker.photoSpot=spot;marker.searchId='public:'+item.id;marker.photoIndex=index;const view=node('button','View published place');view.type='button';view.addEventListener('click',()=>openPublication(item));popup.append(view,link('Share ↗',item.url),commentSection('place','public:'+item.id,spot,item,true));marker.bindPopup(popup).on('popupopen',()=>loadPopupPhoto(popup));}
 }}
}
async function openPublication(item){
 for(const menu of mapMenus)menu.open=false;
 try{const data=await json('/photo-scout/v1/publications/'+encodeURIComponent(item.id));
  if(data.kind==='photo'){await viewSavedPhoto({id:data.id,state:'complete',context:data.context,created:data.created},data);}
  else{render(data.result,{save:false,mapUpdate:false,historyId:'public:'+data.id,publication:data});const spots=data.result.spots||[],coords=spots.filter(s=>s.poi).map(s=>[s.poi.lat,s.poi.lon]);if(coords.length)map.fitBounds(L.latLngBounds(coords).pad(.25),{paddingTopLeft:[30,80],paddingBottomRight:[30,160],maxZoom:16});if(item.poiId){const index=spots.findIndex(s=>poiHistoryKey(s)===item.poiId);if(index>=0){focusMapSpot(spots[index],{openPopup:false});el('photo-spot-'+String(index+1))?.scrollIntoView({block:'nearest'});}}if(!spots.length)message('This published search has no photo places.');}
  const existing=publicationItems.find(p=>p.id===data.id);if(existing)existing.checked=true;else publicationItems.push(data);publicationLayer.addTo(map);renderPublications();drawPublications();
 }catch(error){message(error.message);}
}
el('search-history').addEventListener('toggle',()=>{if(el('search-history').open)window.PhotoScoutHistoryMap?.hydrate(el('history-items'));});
for(const id of ['search-history','photo-history'])el(id).addEventListener('toggle',()=>{if(el(id).open)loadPublications();});el('published-more').addEventListener('click',()=>loadPublications(true));
loadPublications().then(()=>{if(sharedPublicationId)openPublication({id:sharedPublicationId});});

// First visits start in the Bay Area; returning visits resume their last map view.

let favoriteCursor=null;
async function loadFavorites(more=false){
 const root=el('favorite-items');if(!more)root.replaceChildren(node('p','Loading saved items…','small'));
 try{const data=await json('/photo-scout/v1/favorites'+(more&&favoriteCursor?'?before='+favoriteCursor:''));if(!more)root.replaceChildren();favoriteCursor=data.nextBefore;el('favorites-more').hidden=!favoriteCursor;
  if(!data.items.some(item=>item.kind!=='photo')&&!more)root.append(node('p','Your saved searches and places will appear here. Photo collections are in My Photos → Saved.','small'));
  for(const item of data.items.filter(item=>item.kind!=='photo')){const row=node('div',null,'favorite-row'),view=node('button',null,'favorite-view');view.type='button';view.append(node('strong',item.title),node('small',(item.kind==='photo'?'Photo':item.kind==='place'?'Place':'Search')+' · Saved '+new Date(item.savedAt*1000).toLocaleDateString()));view.onclick=()=>{el('search-history').open=false;openPublication(item);};const remove=node('button','Remove','favorite-remove');remove.type='button';remove.onclick=async()=>{remove.disabled=true;try{await json('/photo-scout/v1/publications/'+encodeURIComponent(item.id)+'/reactions',{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-CSRF-Token':csrfToken}:{})},body:JSON.stringify({kind:'favorite',active:false,poiId:item.poiId||''})});refreshComments();await loadFavorites();}catch(error){remove.disabled=false;message(error.message);}};row.append(view,remove);root.append(row);}
 }catch(error){if(!more)root.replaceChildren(node('p',error.message,'small'));el('favorites-more').hidden=true;}
}
el('search-history').addEventListener('toggle',()=>{if(el('search-history').open)loadFavorites();});el('favorites-more').onclick=()=>loadFavorites(true);

let photoLibraryTab='mine',photoLibraryCursor=null,photoLibraryGeneration=0;
function selectPhotoLibraryTab(tab){
 photoLibraryTab=tab;photoLibraryGeneration++;photoLibraryCursor=null;
 for(const button of document.querySelectorAll('[data-photo-tab]')){const selected=button.dataset.photoTab===tab;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;}
 el('photo-items').hidden=tab!=='mine';el('photo-retention-note').hidden=tab!=='mine';el('photo-library-panel').hidden=tab==='mine';el('photo-library-panel').setAttribute('aria-labelledby','photo-tab-'+tab);
 if(tab!=='mine')loadPhotoLibrary();
}
function refreshPhotoLibraryIfOpen(){if(el('photo-history').open&&photoLibraryTab!=='mine')loadPhotoLibrary();}
async function loadPhotoLibrary(more=false){
 if(photoLibraryTab==='mine')return;
 const generation=++photoLibraryGeneration,tab=photoLibraryTab,root=el('photo-library-items'),button=el('photo-library-more');button.disabled=true;
 if(!more){root.replaceChildren(node('p','Loading photos…','small'));button.hidden=true;}
 try{const data=await json('/photo-scout/v1/photo-library?'+new URLSearchParams({tab,...(more&&photoLibraryCursor?{before:String(photoLibraryCursor)}:{})}));if(generation!==photoLibraryGeneration)return;
  if(!more)root.replaceChildren();photoLibraryCursor=data.nextBefore;button.hidden=!photoLibraryCursor;
  if(!data.items.length&&!more)root.append(node('p',{liked:'Photos you like will appear here.',saved:'Photos you save will appear here.',commented:'Photos you comment on will appear here.'}[tab],'small'));
  for(const item of data.items){const entry=node('button',null,'photo-history-entry'),thumb=node('span',null,'photo-history-thumb'),image=node('img'),copy=node('span',null,'photo-history-copy');entry.type='button';image.alt='';image.loading='lazy';image.decoding='async';image.src=item.thumbnailUrl;image.onerror=()=>{image.hidden=true;thumb.textContent='📷';};thumb.setAttribute('aria-hidden','true');thumb.append(image);copy.append(node('strong',item.title),node('small',{liked:'Liked',saved:'Saved',commented:'Commented'}[tab]+' · '+new Date(item.activityAt*1000).toLocaleString()));entry.append(thumb,copy);entry.onclick=()=>{el('photo-history').open=false;openPublication(item);};root.append(entry);}
 }catch(error){if(generation!==photoLibraryGeneration)return;if(more)root.append(node('p',error.message,'small'));else root.replaceChildren(node('p',error.message,'small'));button.hidden=true;if(!authUser){const login=node('button','Sign in with Google');login.type='button';login.onclick=()=>el('account-login').click();root.append(login);}}
 finally{if(generation===photoLibraryGeneration)button.disabled=false;}
}
for(const button of document.querySelectorAll('[data-photo-tab]')){
 button.addEventListener('click',()=>selectPhotoLibraryTab(button.dataset.photoTab));
 button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...document.querySelectorAll('[data-photo-tab]')],index=tabs.indexOf(button),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();selectPhotoLibraryTab(tabs[next].dataset.photoTab);});
}
el('photo-history').addEventListener('toggle',refreshPhotoLibraryIfOpen);el('photo-library-more').onclick=()=>loadPhotoLibrary(true);

if(typeof window!=='undefined'&&window.PhotoScoutAvatars)avatarLibrary=new window.PhotoScoutAvatars({api,request:json,prepare:prepareStudioPhoto,session:()=>({id:authUser?.id||'guest',csrf:csrfToken}),canSelect:()=>studio.open&&!studioBusy,onSelect:async file=>{studioUpload.value='';await acceptStudioPhoto(file);}});
