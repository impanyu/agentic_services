'use strict';
let tasksReady=false,taskRecords=[],taskRefreshBusy=false,taskRecoveryGeneration=0,taskRefreshTimer=null;
const api=location.hostname==='localhost'||location.hostname==='127.0.0.1'?'': 'https://api.aisoup.net';
const el=id=>document.getElementById(id), message=s=>{el('message').textContent=s;if(searchBusy||pipelineBusy||resolving)el('progress-detail').textContent=s};
const map=L.map('map',{zoomControl:false}).setView([41.8827,-87.6233],15);
L.control.zoom({position:'bottomright'}).addTo(map);
const controls=el('map-controls');controls.open=false;L.DomEvent.disableClickPropagation(controls);L.DomEvent.disableScrollPropagation(controls);L.DomEvent.disableClickPropagation(document.querySelector('.map-toolbar'));L.DomEvent.disableClickPropagation(el('prompt-form'));L.DomEvent.disableScrollPropagation(document.querySelector('.prompt-panel'));L.DomEvent.disableClickPropagation(document.querySelector('.scout-dock'));L.DomEvent.disableScrollPropagation(document.querySelector('.scout-dock'));L.DomEvent.disableClickPropagation(el('center-pin'));
const streetTiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'});
let rasterLayer=null,previewLayer=null,previewTimer=null;
let vectorLayer=null,activeMapStyle='streets',mapStyleGeneration=0,vectorReady=false,mapLoadTimer=null;
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
 clearTimeout(mapLoadTimer);clearTimeout(previewTimer);vectorReady=false;activeMapStyle=style;document.body.dataset.mapStyle=style;const generation=++mapStyleGeneration;
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
  gl.once('idle',()=>{if(generation!==mapStyleGeneration||abandoned)return;gl.getCanvas().style.opacity='1';ready();clearTimeout(previewTimer);if(previewLayer===preview){map.removeLayer(preview);previewLayer=null;}if(map.hasLayer(streetTiles))map.removeLayer(streetTiles);});
  gl.on('error',()=>fallback('The selected basemap is unavailable.'));
  gl.on('webglcontextlost',()=>fallback('Map graphics were interrupted.'));
 }catch{fallback('The selected basemap is unavailable.');}
}
// Only one map popover is open; keep it clear of expanded search settings.
const mapMenus=[...document.querySelectorAll('.map-overlay-controls details')];
for(const menu of mapMenus)menu.addEventListener('toggle',()=>{if(!menu.open)return;for(const other of mapMenus)if(other!==menu)other.open=false;controls.open=false;});
controls.addEventListener('toggle',()=>{if(controls.open)for(const menu of mapMenus)menu.open=false;});
if(window.ResizeObserver)new window.ResizeObserver(entries=>{document.body.style.setProperty('--scout-dock-height',entries[0].target.getBoundingClientRect().height+'px');}).observe(document.querySelector('.scout-dock'));
document.querySelectorAll('[data-map-style]').forEach(b=>b.addEventListener('click',()=>switchMapStyle(b.dataset.mapStyle)));
switchMapStyle('minimal');
let selected=L.marker([41.8827,-87.6233],{draggable:true,title:'Selected location: drag to move',icon:L.divIcon({className:'scout-pin',html:'<span aria-hidden="true">✳</span>',iconSize:[44,44],iconAnchor:[22,22]})}).addTo(map), resultPins=[];let humanFreePreview=false, serviceAvailable=false, poiCatalog=null, catalogGeneration=0, searchBusy=false, pollGeneration=0, resolving=false, pipelineBusy=false;
const candidatePoiLayer=L.layerGroup().addTo(map),photoLocationLayer=L.layerGroup().addTo(map);
const scanLayer=L.layerGroup().addTo(map);let scanTimer=null,scanIndex=0,scanDot=null,scanRunning=false;
function stopPoiScan(){clearInterval(scanTimer);scanTimer=null;scanRunning=false;scanIndex=0;scanLayer.clearLayers();if(scanDot)scanDot.setStyle({radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9});scanDot=null;el('map').classList.remove('reviewing-views');}
function startPoiScan(){if(scanRunning||!candidatePoiLayer.getLayers().length)return;scanRunning=true;el('map').classList.add('reviewing-views');const tick=()=>{if(document.hidden)return;const dots=candidatePoiLayer.getLayers();if(!dots.length){stopPoiScan();return;}if(scanDot)scanDot.setStyle({radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9});scanDot=dots[scanIndex++%dots.length];scanDot.setStyle({radius:9,color:'#fff',weight:3,fillColor:'#9762d1',fillOpacity:1});scanLayer.clearLayers();const name=scanDot.getTooltip()?.getContent()?.textContent||'Nearby place';L.circleMarker(scanDot.getLatLng(),{radius:21,color:'#a26ce1',weight:3,fillColor:'#b889ed',fillOpacity:.15,interactive:false,className:'photo-scan-ring'}).addTo(scanLayer).bindTooltip(node('span','Checking '+name+'…'),{permanent:true,direction:'top',offset:[0,-22],className:'photo-scan-label'}).openTooltip();};tick();scanTimer=setInterval(tick,1400);}
const searchArea=L.circle([41.8827,-87.6233],{radius:1000,color:'#375947',weight:1.5,dashArray:'5 7',fillColor:'#acd69a',fillOpacity:.12,interactive:false}).addTo(map);
const overlayControl=L.control.layers(null,{'Search radius':searchArea,'Candidate places':candidatePoiLayer,'Recommended places':photoLocationLayer},{collapsed:true}).addTo(map);
document.querySelector('.map-overlay-controls').append(overlayControl.getContainer());
function syncMapSelection(){const lat=Number(el('lat').value),lon=Number(el('lon').value);if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180)return;searchArea.setLatLng([lat,lon]).setRadius(Number(el('radius').value));el('map-selection').textContent=`${lat.toFixed(5)}, ${lon.toFixed(5)}`;el('coordinate-readout').textContent=el('map-selection').textContent;el('map-radius').textContent=`Searching within ${Number(el('radius').value)>=1000?Number(el('radius').value)/1000+' km':el('radius').value+' m'}`;}
selected.on('dragend',()=>{const p=selected.getLatLng();pick(p.lat,p.lng);});

function coordinates(){const styles=[...el('style-options').querySelectorAll('input:checked')].map(i=>i.value).filter(s=>s!=='any');return {lat:Number(el('lat').value),lon:Number(el('lon').value),radius:Number(el('radius').value),photoStyles:styles.length?styles:null}}
function invalidatePois(){stopPoiScan();catalogGeneration++;candidatePoiLayer.clearLayers();poiCatalog=null;}
function pick(lat,lon){invalidatePois();el('lat').value=lat.toFixed(6);el('lon').value=lon.toFixed(6);selected.setLatLng([lat,lon]);syncMapSelection();}
const locationStatus=(s,status)=>{el('location-status').textContent=s;if(status)el('location-status').dataset.state=status};
let locationPending=false;
function locateCurrentPosition(){
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
  const {latitude:lat,longitude:lon,accuracy}=position.coords;
  pick(lat,lon);map.fitBounds(searchArea.getBounds(),{padding:[32,32],maxZoom:16});
  locationStatus(`Device location selected${Number.isFinite(accuracy)?` (accuracy ±${Math.ceil(accuracy)} m)`:''}. Click ↑ or press Enter to search.`);
  el('map-notice').textContent=`Your current location is selected${Number.isFinite(accuracy)?` · estimated accuracy ±${Math.ceil(accuracy)} m`:''}.`;
  el('map-heading').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
 }});
}
for(const id of ['locate','center-pin'])el(id).addEventListener('click',locateCurrentPosition);
map.on('click',e=>pick(e.latlng.lat,e.latlng.lng));
el('radius').addEventListener('change',()=>{invalidatePois();syncMapSelection();updateParameterSummary();});
el('limit').addEventListener('change',updateParameterSummary);
function updateParameterSummary(){const moods=[...el('style-options').querySelectorAll('input:checked')].filter(i=>i.value!=='any');el('parameter-summary').textContent=el('radius').selectedOptions[0].textContent+' · Top '+el('limit').value+' · '+(moods.length?moods.map(i=>i.parentElement.querySelector('strong').textContent).join(' + '):'Any mood');}
function styleChanged(){updateParameterSummary();invalidatePois();message('Photo mood updated. Submit to explore around the selected pin.');}
function renderStyles(styles){
 const options=[{id:'any',label:'Surprise me',description:'Find distinctive photo opportunities across all moods.'},...styles];
 for(const style of options){const label=node('label',null,'style-choice'),input=document.createElement('input');input.type='checkbox';input.name='photo-style';input.value=style.id;input.checked=style.id==='any';input.addEventListener('change',()=>{const inputs=el('style-options').querySelectorAll('input');if(input.checked&&input.value==='any')inputs.forEach(i=>i.checked=i===input);else if(input.checked)inputs.forEach(i=>{if(i.value==='any')i.checked=false;});if(![...inputs].some(i=>i.checked))inputs.forEach(i=>{if(i.value==='any')i.checked=true;});styleChanged();});const text=node('span');const icons={any:'✳',nature:'❋',urban:'▥',vintage:'◷',iconic:'✦',artistic:'◈',waterside:'≈',minimal:'□',adventure:'△'};const icon=node('span',icons[style.id]||'✳','mood-icon');icon.setAttribute('aria-hidden','true');label.append(icon);text.append(node('strong',style.label),node('span',style.description,'style-description'));label.append(input,text);el('style-options').append(label);}
}
async function loadPois(){
 if(!el('search').reportValidity()||!serviceAvailable)return;
 invalidatePois();const generation=catalogGeneration,coords=coordinates();setProgress(1,'Finding nearby places…','Looking for places that match your location and photo mood.');message('Finding nearby places…');
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
function render(result,{scroll=true,save=true,mapUpdate=true}={}){const root=el('results');controls.open=false;for(const menu of mapMenus)menu.open=false;root.hidden=false;el('toggle-results').disabled=false;
 const edit=node('button','Close ×','back-button');edit.type='button';edit.addEventListener('click',()=>{root.hidden=true;});
 const heading=node('div',null,'results-heading');heading.append(node('div','YOUR SHORTLIST','eyebrow'),edit);
 root.replaceChildren(heading,node('h2','Your nearby photo shortlist'),node('p',result.summary));
 if(result.photoStyles?.length)root.append(node('p',`Photo mood: ${result.photoStyles.map(s=>s.label).join(', ')}`,'tag'));
 if(result.candidatePoiCount!==undefined)root.append(node('p',`Found ${result.candidatePoiCount} candidate places; showing ${allPoiViews(result).length} with scored images.`,'small'),node('p','POI data © OpenStreetMap contributors · ODbL 1.0','small'));const cards=node('div',null,'cards');root.append(cards);
 for(const [i,s] of allPoiViews(result).entries()){const card=node('article',null,'card');card.id='photo-spot-'+String(i+1);if(s.imageUrl||s.provider==='google-street-view'){const img=node('img');if(s.imageUrl)img.src=s.imageUrl;else refreshThumbnail(img,s);img.alt=s.name;img.loading='eager';img.referrerPolicy='no-referrer';img.addEventListener('error',()=>{const attempts=Number(img.dataset.retryCount||0);if(s.provider==='google-street-view'&&attempts<2){img.dataset.retryCount=String(attempts+1);setTimeout(()=>{if(img.isConnected)refreshThumbnail(img,s);},attempts?65000:2000);}else img.replaceWith(node('span','Image unavailable — open the original view ↗','image-unavailable'));});const imageLink=link('',s.sourceUrl);imageLink.className='spot-image-link';imageLink.setAttribute('aria-label',(s.provider==='google-street-view'?'Open Google Street View for ':'Open original photo for ')+s.name);imageLink.append(img,node('span',s.provider==='google-street-view'?'Open Street View ↗':'Open original photo ↗','image-open-label'));card.append(imageLink);}else if(s.provider==='google-street-view'){const view=link('Explore this view in Google Street View ↗',s.sourceUrl);view.className='street-view-link';card.append(view);}const body=node('div',null,'body');body.append(...(s.provider==='google-street-view'?[node('p','Google Maps','GMP-attribution')]:[]),node('h3',`${i+1}. ${s.name}`),node('span',s.score==null?'No verified image':`${s.score}/100 subjective photo score · ${s.confidence} confidence`,'tag'),...(s.recommend===false?[node('p','Model flagged suitability concerns — review the notes','small')]:[]),...(s.provider?[node('p',`Image source: ${s.provider}`,'small')]:[]),...(s.poi?[node('p',`Candidate place: ${s.poi.name}${s.poi.category?' · '+s.poi.category:''}`,'small')]:[]),node('p',s.visible_evidence),...(s.photo_tip?[node('p',`Photo idea: ${s.photo_tip}`)]:[]),...(s.viewHeadingDegrees!=null?[node('p',`Inspected camera direction: ${s.viewHeadingDegrees}°`,'small')]:[]),...(s.distanceMeters!=null?[node('p',`${s.distanceMeters} m straight-line distance · ${s.locationType}`)]:[]),...(s.uncertainty?[node('p',`Uncertainty: ${s.uncertainty}`)]:[]),node('p',s.coordinateWarning),...(s.sourceUrl?[link(s.provider==='google-street-view'?'View Google Street View ↗':'Original image ↗',s.sourceUrl)]:[]),...(s.author?[node('p',`${s.author} · ${s.license} · source date: ${s.sourceDate||s.capturedAt||'unknown'}`)]:[]),...(s.licenseUrl?[link('Image license ↗',s.licenseUrl)]:[]));const compose=node('button','📷 Take a selfie here','compose-photo');compose.type='button';compose.addEventListener('click',()=>openPhotoStudio(s));body.append(compose);card.append(body);cards.append(card);}
 if(result.imageAssessments?.length){const detail=node('details',null,'score-details');detail.append(node('summary',`All ${result.imageAssessments.length} image scores`));const list=node('ul');for(const a of result.imageAssessments){const li=node('li');li.append(node('strong',`${a.score}/100 · ${a.name}`),node('p',`${a.provider} · ${a.eligibleForRecommendation?'Suitable candidate':'Not recommended'}`,'small'),node('p',a.visible_evidence),...(a.exclusionReason?[node('p',`Why not shortlisted: ${a.exclusionReason}`)]:[]),node('p',`Uncertainty: ${a.uncertainty}`));list.append(li);}detail.append(list);root.append(detail);}

 if(result.sources){const coverage=node('p',Object.entries(result.sources).map(([name,s])=>`${name}: ${s.status==='ok'?(s.eligibleImages!==undefined?`${s.eligibleImages} eligible images${s.sampledImages!==undefined?`; ${s.sampledImages} selected for scoring`:""}`:'available'):'temporarily unavailable'}`).join(' · '),'small');root.append(coverage);}
 root.append(node('p',`${result.analysisMethod==='fixed-batch-scoring'?'Rated':'Inspected'} ${result.inspectedImages} images${result.inspectedImageSources?.length?" from "+result.inspectedImageSources.join(", "):""}. ${result.coverage}`,'small'));if(save)saveSearch(result);if(mapUpdate)drawHistoryMap({fit:true});}
async function refreshThumbnail(img,spot){img.dataset.refreshed='true';try{const data=await json('/photo-scout/v1/thumbnails',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sourceUrls:[spot.sourceUrl]})});if(data.imageUrls[0])img.src=data.imageUrls[0];else throw Error('Unavailable');}catch{img.replaceWith(node('span','Image unavailable — open the original view ↗','image-unavailable'));}}
async function json(url,options){const r=await fetch(api+url,{credentials:'include',...options}),data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Request failed');return data;}
// Remove legacy active-job tokens; completed history is stored separately.
try{localStorage.removeItem('photo-scout-active-search');for(const key of Object.keys(sessionStorage)){if(key.startsWith('photo-scout:'))sessionStorage.removeItem(key);}}catch{}
if(location.search){const clean=new URL(location.href);clean.search='';history.replaceState(null,'',clean);}
let activeSearch=null;
async function poll(job,token){const generation=++pollGeneration;searchBusy=true;updateSubmitState();
 while(generation===pollGeneration){
  try{const d=await json('/photo-scout/v1/report/'+encodeURIComponent(job),{headers:token?{'X-Report-Token':token}:{}});if(generation!==pollGeneration)return;
   if(d.context){activeSearch.context=d.context;applyTaskContext(d.context);}
   if(d.state==='complete'){render(d.result,{mapUpdate:false});drawHistoryMap();fitSearchRange();setProgress(2,'Your shortlist is ready',`${d.result.spots?.length||0} recommended spots. View the photos and pins on the map.`,'complete');message('Your shortlist is ready on the map. Saved in your browser history.');activeSearch=null;searchBusy=false;updateSubmitState();restoreTasks();return;}
   if(d.state==='failed'){setProgress(2,'Search could not be completed',d.error||'Please try again.','error');message(d.error||'Search failed. Please try again.');activeSearch=null;searchBusy=false;updateSubmitState();restoreTasks();return;}
   const stage=d.context?.stage,interpreting=d.state==='running'&&!stage&&Boolean(d.context?.query),finding=d.state==='running'&&stage==='sources';
   setProgress(interpreting?0:finding?1:2,d.state==='queued'?'Your search is queued…':interpreting?'Understanding your request…':finding?'Finding nearby places & photos…':'Reviewing & ranking photos…','Your task is saved on the server. You can refresh or leave this page and return later.');
   if(d.state==='running'&&stage==='scoring')startPoiScan();else stopPoiScan();
   message(d.state==='queued'?'Your request is waiting to start. You can refresh or leave this page; your task is saved.':'AI is reviewing nearby photos and ranking the best spots. This may take a few minutes; you can switch tabs.');
  }catch(e){if(generation!==pollGeneration)return;if(/not found|expired/i.test(e.message)){setProgress(2,'Search is unavailable',e.message,'error');message(e.message);activeSearch=null;searchBusy=false;updateSubmitState();restoreTasks();return;}message('Connection interrupted. Your task continues on the server; reconnecting…');}
  await new Promise(r=>setTimeout(r,document.hidden?15000:5000));
 }
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&activeSearch)poll(activeSearch.jobId,activeSearch.reportToken);});
window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
async function analyzePlaces(){if(searchBusy||!poiCatalog||!humanFreePreview)return;const selectedPoiIds=poiCatalog.nearbyPois.map(p=>p.id);if(!selectedPoiIds.length){message('No nearby places found. Try a larger radius.');return;}searchBusy=true;updateSubmitState();setProgress(2,'Starting your photo review…',`Preparing images from ${selectedPoiIds.length} nearby places for AI review.`);message('Submitting background search…');const payload={...poiCatalog.coords,selectedPoiIds,poiCatalogToken:poiCatalog.poiCatalogToken,limit:Number(el('limit').value),preferences:el('preferences').value};try{activeSearch=await json('/photo-scout/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-Request-Token':crypto.randomUUID().replaceAll('-','')},body:JSON.stringify(payload)});await poll(activeSearch.jobId,activeSearch.reportToken);}catch(err){setProgress(2,'Search could not be started',err.message,'error');message(err.message);}finally{searchBusy=Boolean(activeSearch);updateSubmitState();}}
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
function setProgress(step,title,detail,state='running'){if(state!=='running'||step!==2)stopPoiScan();controls.open=false;el('search-progress').hidden=false;el('search-progress').setAttribute('data-state',state);el('progress-title').textContent=title;el('progress-detail').textContent=detail;document.querySelectorAll('.progress-steps [data-step]').forEach(item=>item.setAttribute('data-state',Number(item.dataset.step)<step?'done':Number(item.dataset.step)===step?'active':'waiting'));if(state==='running'){el('message').textContent='';el('prompt-status').textContent='';el('results').hidden=true;}}
async function runSearchPipeline(){if(pipelineBusy||searchBusy)return;pipelineBusy=true;updateSubmitState();try{await loadPois();if(poiCatalog?.nearbyPois.length)await analyzePlaces();}finally{pipelineBusy=false;updateSubmitState();}}
async function applyIntent(plan,place){pick(place.lat,place.lon);const radius=String(plan.radiusMeters);if(![...el('radius').options].some(o=>o.value===radius)){const option=node('option',radius+' m');option.value=radius;el('radius').append(option);}el('radius').value=radius;fitSearchRange();el('style-options').querySelectorAll('input').forEach(i=>i.checked=plan.photoStyles.length?plan.photoStyles.includes(i.value):i.value==='any');const count=String(plan.limit);if(![...el('limit').options].some(o=>o.value===count)){const option=node('option','Top '+count);option.value=count;el('limit').append(option);}el('limit').value=count;el('preferences').value=plan.preferences;updateParameterSummary();el('location-options').replaceChildren();el('prompt-status').textContent=place.label+' · '+plan.explanation;await runSearchPipeline();}
async function submitSearch(e){e?.preventDefault();if(!serviceAvailable||resolving||pipelineBusy||searchBusy)return;fitSearchRange();const query=el('prompt-query').value.trim();if(humanFreePreview){await submitDurableSearch(query);return;}if(!query){if(poiCatalog)await analyzePlaces();else await runSearchPipeline();return;}const generation=++intentGeneration;resolving=true;updateSubmitState();setProgress(0,'Understanding your request…','Finding the location, photo mood and search radius in your message.');el('location-options').replaceChildren();el('prompt-status').textContent='Understanding your request and finding matching locations…';try{const c=coordinates();const plan=await json('/photo-scout/v1/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,lat:c.lat,lon:c.lon,radius:c.radius,limit:Number(el('limit').value),photoStyles:c.photoStyles||[],preferences:el('preferences').value})});if(generation!==intentGeneration)return;if(plan.clarification){el('prompt-status').textContent=plan.clarification;setProgress(0,'Could not interpret the request',plan.clarification,'error');return;}if(!plan.locations.length){el('prompt-status').textContent='No matching location. Please add a city or place name.';setProgress(0,'Could not find a location',el('prompt-status').textContent,'error');return;}el('prompt-status').textContent='Using '+plan.locations[0].label+'. Finding and scoring nearby views…';await applyIntent(plan,plan.locations[0]);}catch(err){el('prompt-status').textContent=err.message;setProgress(0,'Search could not be started',err.message,'error');}finally{resolving=false;updateSubmitState();}}
el('prompt-form').addEventListener('submit',submitSearch);

el('prompt-query').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();el('prompt-form').requestSubmit();}});

el('toggle-results').addEventListener('click',()=>{el('results').hidden=!el('results').hidden;});
el('sample').addEventListener('click',async()=>{try{const r=await fetch('./sample.json');if(!r.ok)throw Error('Sample unavailable');const data=await r.json();render(data);}catch(e){message(e.message)}});

el('sample-paris').addEventListener('click',async()=>{try{const r=await fetch('./sample-paris.json');if(!r.ok)throw Error('Sample unavailable');render(await r.json());}catch(e){message(e.message)}});

el("sample-google").addEventListener("click",async()=>{try{const r=await fetch("./sample-google.json");if(!r.ok)throw Error("Sample unavailable");const data=await r.json();render(data);message(data.sampleNotice);}catch(e){message(e.message)}});

const HISTORY_KEY='photo-scout-search-history-v1';let searchHistory=[],authUser=null,csrfToken=null;const pendingHistory=new Map();let syncingHistory=false;
function hasScoredImage(spot){return Number.isFinite(spot.score)&&spot.assessmentStatus!=='no_verified_view'&&Boolean(spot.imageUrl||spot.streetViewReference||(spot.provider==='google-street-view'&&spot.sourceUrl));}
function allPoiViews(result){const seen=new Set();return [...(result.poiResults||[]),...(result.spots||[])].filter(hasScoredImage).sort((a,b)=>b.score-a.score).filter(s=>{const id=s.poi?.id||s.id;if(seen.has(id))return false;seen.add(id);return true;});}
function photoTopCount(result){return Math.min(allPoiViews(result).length,Number(result.topLimit||result.searchContext?.limit)||((result.spots||[]).length===5?5:3));}
function directionDot(spot){const b=photoBearing(spot);return L.divIcon({className:'direction-dot'+(b.heading===null?' no-bearing':''),html:`<span style="transform:rotate(${b.heading??0}deg)"><i></i><b></b></span><small class="dot-bearing">${b.heading===null?'?':b.heading+'°'}</small>`,iconSize:[36,48],iconAnchor:[18,16],popupAnchor:[0,-16]});}
function storedHistory(){return searchHistory.map(h=>({...h,result:{...h.result,imageAssessments:[],spots:(h.result.spots||[]).map(stripHistoryImage),poiResults:(h.result.poiResults||[]).map(stripHistoryImage)}}));}
function persistHistory(){const records=storedHistory();if(authUser){queueHistory(records);return;}try{sessionStorage.setItem(HISTORY_KEY,JSON.stringify(records));}catch{el('history-note').textContent='Session storage is unavailable; history stays in this page.';}}
async function queueHistory(records){for(const h of records)pendingHistory.set(h.id,h);if(syncingHistory)return;syncingHistory=true;try{while(pendingHistory.size&&authUser){const [id,item]=pendingHistory.entries().next().value;await json('/photo-scout/v1/history',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},body:JSON.stringify(item)});if(pendingHistory.get(id)===item)pendingHistory.delete(id);}}catch{el('history-note').textContent='Account history could not sync. Keep this page open and try again.';}finally{syncingHistory=false;}}
async function loadAccount(){try{const state=await json('/photo-scout/v1/auth/me');authUser=state.user;csrfToken=state.csrfToken;el('account-login').disabled=!state.configured;el('account-login').textContent=state.configured?'Sign in with Google':'Google sign-in · setup pending';el('account-login').hidden=Boolean(authUser);el('account-logout').hidden=!authUser;el('account-name').textContent=authUser?authUser.name:'Guest session';if(authUser){const guest=searchHistory,remote=await json('/photo-scout/v1/history');const merged=new Map(remote.items.map(h=>[h.id,h]));for(const h of guest)merged.set(h.id,h);searchHistory=[...merged.values()].sort((a,b)=>b.created-a.created).slice(0,30);if(guest.length){await queueHistory(guest.map(h=>({...h,result:{...h.result,imageAssessments:[],spots:(h.result.spots||[]).map(stripHistoryImage),poiResults:(h.result.poiResults||[]).map(stripHistoryImage)}})));if(!pendingHistory.size)sessionStorage.removeItem(HISTORY_KEY);}el('history-note').textContent='Last 30 searches, saved to your account. Images are not stored.';renderHistory();drawHistoryMap({fit:true});}else el('history-note').textContent='Tasks are saved for this browser for 30 days. Sign in to access them on other devices.';}catch{el('account-name').textContent='Guest session';el('account-login').disabled=true;el('history-note').textContent='Sign-in is temporarily unavailable. Guest history stays in this session.';}}
el('account-login').addEventListener('click',()=>{persistHistory();location.href=api+'/photo-scout/v1/auth/login';});
el('account-logout').addEventListener('click',async()=>{try{await json('/photo-scout/v1/auth/logout',{method:'POST',headers:{'X-CSRF-Token':csrfToken}});authUser=null;csrfToken=null;pendingHistory.clear();searchHistory=[];sessionStorage.removeItem(HISTORY_KEY);el('results').hidden=true;el('toggle-results').disabled=true;renderHistory();drawHistoryMap();resetTaskRecovery();await loadAccount();await restoreTasks();}catch{el('history-note').textContent='Sign-out failed. Please try again.';}});
function stripHistoryImage(s){const copy={...s};delete copy.imageUrl;delete copy.streetViewReference;return copy;}
function saveSearch(result){const id=activeSearch?.jobId||crypto.randomUUID();if(searchHistory.some(h=>h.id===id))return;const context=activeSearch?.context;searchHistory.unshift({id,created:Date.now(),label:context?.query||context?.locationLabel||el('prompt-query').value.trim()||`Around ${Number(el('lat').value).toFixed(4)}, ${Number(el('lon').value).toFixed(4)}`,radius:context?.radius||Number(el('radius').value),checked:true,result:{...result,searchContext:{...(context||coordinates())}}});searchHistory=searchHistory.slice(0,30);persistHistory();renderHistory();}
function historyEntries(){
 const merged=new Map(searchHistory.map(h=>['search:'+h.id,{id:h.id,kind:'search',created:h.created,label:h.label,history:h,state:'complete'}]));
 for(const task of taskRecords){const key=task.kind+':'+task.id,existing=merged.get(key),c=task.context||{};merged.set(key,{...existing,id:task.id,kind:task.kind,created:task.created*1000,label:existing?.label||c.query||c.name||c.locationLabel||(Number.isFinite(c.lat)&&Number.isFinite(c.lon)?`Around ${c.lat.toFixed(4)}, ${c.lon.toFixed(4)}`:'Photo search'),task,state:existing?.history?'complete':task.state});}
 const entries=[...merged.values()].sort((a,b)=>b.created-a.created);
 const searches=entries.filter(e=>e.kind==='search').slice(0,30),photos=entries.filter(e=>e.kind==='portrait');
 return [...searches,...photos].sort((a,b)=>b.created-a.created);
}
function renderHistory(){
 const root=el('history-items'),entries=historyEntries();root.replaceChildren();el('history-count').textContent=String(entries.length);
 const completed=entries.filter(e=>e.kind==='search'&&e.history).map(e=>e.history);
 el('history-all').disabled=!completed.length;el('history-all').checked=completed.length>0&&completed.every(h=>h.checked);el('history-all').indeterminate=completed.some(h=>h.checked)&&!completed.every(h=>h.checked);
 if(!entries.length)root.append(node('p','Your searches will appear here.','small'));
 for(const entry of entries){
  const h=entry.history,row=node('div',null,'history-item'),label=node(h?'label':'span',null,'history-copy');
  if(h){const check=node('input');check.type='checkbox';check.checked=h.checked;check.setAttribute('aria-label','Show search: '+entry.label);check.addEventListener('change',()=>{h.checked=check.checked;persistHistory();renderHistory();drawHistoryMap({fit:true});});label.append(check);}
  const text=node('span'),detail=h?`${h.radius/1000} km · ${allPoiViews(h.result).length} places`:(entry.kind==='portrait'?'Selfie · ':'')+entry.state;
  text.append(node('strong',entry.label),node('small',`${new Date(entry.created).toLocaleString()} · ${detail}`));label.append(text);
  const pending=['queued','running','checking'].includes(entry.state),view=node('button',pending?'Progress':entry.state==='failed'?'Details':entry.kind==='portrait'?'View photo':'View');view.type='button';
  view.addEventListener('click',()=>{if(entry.task)viewSavedTask(entry.task);else{showHistorySearch(h.result,h.result.searchContext,h);}});
  row.append(label,view);root.append(row);
 }
}
function drawHistoryMap({fit=false}={}){photoLocationLayer.clearLayers();candidatePoiLayer.clearLayers();resultPins=[];for(const h of searchHistory.filter(h=>h.checked)){for(const [i,s] of allPoiViews(h.result).entries()){const pos=s.poi;if(!pos||!Number.isFinite(pos.lat)||!Number.isFinite(pos.lon))continue;const b=photoBearing(s),popup=node('div',null,'photo-popup');popup.append(node('strong',s.name),node('p',s.score==null?'No verified image':`${s.score}/100 · ${b.label}`),node('p',h.label));const show=node('button','View place','popup-shortlist');show.type='button';const reveal=()=>{render(h.result,{save:false,mapUpdate:false});el('photo-spot-'+String(i+1))?.scrollIntoView({block:'nearest',behavior:'smooth'});};show.addEventListener('click',reveal);popup.append(show);if(s.sourceUrl)popup.append(link(s.provider==='google-street-view'?'Open Street View ↗':'Open original photo ↗',s.sourceUrl));const marker=L.marker([pos.lat,pos.lon],{title:`${s.name} · ${b.label} · ${h.label}`,icon:i<photoTopCount(h.result)?photographerIcon(s,i):directionDot(s)}).addTo(photoLocationLayer).bindPopup(popup).on('click',reveal);resultPins.push(marker);}}if(fit&&resultPins.length)map.fitBounds(L.featureGroup(resultPins).getBounds().pad(.2),{paddingTopLeft:[40,80],paddingBottomRight:[40,250],maxZoom:16});}
el('history-all').addEventListener('change',()=>{for(const h of searchHistory)h.checked=el('history-all').checked;persistHistory();renderHistory();drawHistoryMap({fit:true});});
try{const legacy=localStorage.getItem(HISTORY_KEY);if(legacy&&!sessionStorage.getItem(HISTORY_KEY))sessionStorage.setItem(HISTORY_KEY,legacy);localStorage.removeItem(HISTORY_KEY);const saved=JSON.parse(sessionStorage.getItem(HISTORY_KEY)||'[]');if(Array.isArray(saved))searchHistory=saved.filter(h=>h&&typeof h.id==='string'&&typeof h.label==='string'&&h.result&&Array.isArray(h.result.spots)).slice(0,30);}catch{}renderHistory();drawHistoryMap({fit:true});loadAccount().finally(()=>restoreTasks());

// The photo studio keeps personal uploads and generated images out of search history.
let studioBusy=false,studioActivityMarker=null;
let studioJob=null,studioFile=null,studioSpot=null,studioUrl=null,studioPreviewUrl=null,studioTimer=null,studioPrepared=null,studioUploadGeneration=0,studioOutputFile=null,studioOutputGeneration=0;
const studio=node('dialog',null,'photo-studio');studio.setAttribute('aria-label','Photo studio');
const studioTop=node('div',null,'studio-heading'),studioTitle=node('h2','Take a selfie here'),studioClose=node('button','Close ×');studioClose.type='button';studioTop.append(studioTitle,studioClose);
const studioPlace=node('p',null,'small'),studioImages=node('div',null,'studio-inputs'),scenePreview=node('img'),personPreview=node('img');scenePreview.alt='Selected background';personPreview.alt='Your uploaded photo';personPreview.hidden=true;studioImages.append(scenePreview,personPreview);
const uploadLabel=node('label','Your photo · JPG, PNG, WebP or HEIC, up to 20 MB · automatically resized','studio-upload'),studioUpload=node('input');studioUpload.type='file';studioUpload.accept='image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif';uploadLabel.append(studioUpload);
let studioStyle='natural';
const studioStyles=node('fieldset',null,'studio-styles'),studioStyleLegend=node('legend','Photo style'),studioStyleGrid=node('div',null,'studio-style-grid'),studioStyleHint=node('p','Relaxed pose, soft smile, your original outfit.','small');
const portraitStyles=[['natural','Natural','Relaxed pose, soft smile, your original outfit.'],['street','Street style','Confident pose, contemporary urban clothing, candid expression.'],['cinematic','Cinematic','Expressive pose, understated clothing, a thoughtful look.'],['vacation','Vacation','Relaxed holiday pose, comfortable clothing, a cheerful smile.'],['editorial','Editorial','Elegant pose, refined clothing, a polished magazine look.']];
for(const [id,label,hint] of portraitStyles){const choice=node('label',null,'studio-style'),input=node('input');input.type='radio';input.name='portrait-style';input.value=id;input.checked=id==='natural';input.addEventListener('change',()=>{if(input.checked){studioStyle=id;studioStyleHint.textContent=hint;}});choice.append(input,node('span',label));studioStyleGrid.append(choice);}
studioStyles.append(studioStyleLegend,studioStyleGrid,studioStyleHint,node('p','Your location stays the same. Styles adjust your look; weather can change the light and atmosphere.','small'));
const studioOptions=node('fieldset',null,'studio-options'),studioOptionsLegend=node('legend','Make it yours'),studioOptionsGrid=node('div',null,'studio-options-grid');
function studioSelect(label,name,options){const wrapper=node('label',label),select=node('select');select.name=name;select.setAttribute('aria-label',label);for(const [value,text] of options){const option=node('option',text);option.value=value;select.append(option);}select.value=options[0][0];wrapper.append(select);studioOptionsGrid.append(wrapper);return select;}
const studioPosture=studioSelect('Posture','posture',[['auto','Auto'],['standing','Relaxed standing'],['walking','Candid walking'],['sitting','Seated'],['looking_back','Looking back'],['playful','Playful']]);
const studioWeather=studioSelect('Weather & light','weather',[['original','Keep original'],['sunny','Sunny daylight'],['golden_hour','Golden hour'],['overcast','Soft overcast'],['rainy','Gentle rain'],['snowy','Gentle snow']]);
const studioExpression=studioSelect('Expression','expression',[['auto','Auto'],['soft_smile','Soft smile'],['big_smile','Big smile'],['thoughtful','Thoughtful'],['serious','Confident & serious'],['surprised','Playful surprise']]);
studioOptions.append(studioOptionsLegend,studioOptionsGrid);
const poseLabel=node('label','Additional directions (optional)','studio-upload'),studioPose=node('textarea');studioPose.maxLength=500;studioPose.placeholder='For example: standing naturally, full body, a relaxed smile';poseLabel.append(studioPose);
const studioNote=node('p','Your photo and this view will be sent to OpenAI to create an AI composite. Your upload is removed after processing; the result is saved for seven days.','small');
const studioGenerate=node('button','Create my photo · Free test','studio-generate');studioGenerate.type='button';studioGenerate.disabled=true;
const studioStatus=node('p',null,'studio-status');studioStatus.setAttribute('role','status');studioStatus.setAttribute('aria-live','polite');
const studioResult=node('img',null,'studio-result');studioResult.alt='AI-generated travel photo';studioResult.hidden=true;
const studioSave=node('button','Save to Photos','studio-save');studioSave.type='button';studioSave.hidden=true;
const studioSaveHint=node('p',null,'studio-save-hint small');studioSaveHint.setAttribute('role','status');studioSaveHint.hidden=true;
const studioDownload=node('a','Download PNG','studio-download');studioDownload.hidden=true;studioDownload.download='photo-scout-ai-photo.png';
studio.append(studioTop,studioPlace,studioImages,uploadLabel,studioStyles,studioOptions,poseLabel,studioNote,studioGenerate,studioStatus,studioResult,studioSave,studioDownload,studioSaveHint);document.body.append(studio);
// This separate map layer survives shortlist/history redraws and dialog closure.
const selfieActivityLayer=L.layerGroup().addTo(map);
const studioTask=node('button',null,'selfie-task');studioTask.type='button';studioTask.hidden=true;studioTask.setAttribute('aria-live','polite');
const studioTaskSpark=node('span','✦','selfie-task-spark'),studioTaskCopy=node('span',null,'selfie-task-copy'),studioTaskLabel=node('strong'),studioTaskPlace=node('small');studioTaskCopy.append(studioTaskLabel,studioTaskPlace);studioTask.append(studioTaskSpark,studioTaskCopy);document.querySelector('.map-stage').append(studioTask);L.DomEvent.disableClickPropagation(studioTask);studioTask.addEventListener('click',()=>studio.showModal());
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
function openPhotoStudio(spot){if(studioBusy&&studioSpot!==spot){studio.showModal();studioStatus.textContent='A photo is already being created. Please wait for it to finish.';return;}if(!studioBusy){studioTask.hidden=true;studioSpot=spot;studioPlace.textContent=spot.name+(photoBearing(spot).heading!=null?' · '+photoBearing(spot).label:'');scenePreview.removeAttribute('src');if(spot.provider==='google-street-view')refreshThumbnail(scenePreview,spot);else if(spot.imageUrl)scenePreview.src=spot.imageUrl;studioStatus.textContent='Upload a clear image of a person, cartoon character or animal. Groups are welcome; a clearly visible subject works best.';clearStudioOutput();studioGenerate.disabled=!studioPrepared;}studio.showModal();}
studioClose.addEventListener('click',()=>studio.close());
studioUpload.addEventListener('change',async()=>{const generation=++studioUploadGeneration;studioFile=studioUpload.files[0]||null;studioPrepared=null;personPreview.hidden=true;personPreview.removeAttribute('src');studioGenerate.disabled=true;if(studioPreviewUrl)URL.revokeObjectURL(studioPreviewUrl);if(!studioFile)return;if(studioFile.size>20000000){studioStatus.textContent='Choose a photo up to 20 MB.';studioFile=null;return;}if(!['image/jpeg','image/png','image/webp','image/heic','image/heif',''].includes(studioFile.type)&&!/\.(jpe?g|png|webp|heic|heif)$/i.test(studioFile.name)){studioStatus.textContent='Choose a JPG, PNG, WebP or HEIC photo.';studioFile=null;return;}studioStatus.textContent='Preparing your photo…';try{const prepared=await prepareStudioPhoto(studioFile);if(generation!==studioUploadGeneration)return;studioPrepared=prepared.data;personPreview.src=prepared.data;personPreview.hidden=!prepared.preview;studioGenerate.disabled=Boolean(studioJob);studioStatus.textContent=prepared.preview?'Your photo is ready. It has been resized for upload.':'Your phone photo will be converted securely when you submit. This browser cannot display its original format.';}catch(error){if(generation!==studioUploadGeneration)return;studioFile=null;studioStatus.textContent=error.message;}});
async function prepareStudioPhoto(file){const data=await readPhoto(file);return new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>{try{if(image.naturalWidth*image.naturalHeight>80000000)throw Error('This photo exceeds 80 megapixels. Export a smaller copy.');const scale=Math.min(1,2048/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);resolve({data:canvas.toDataURL('image/jpeg',.92),preview:true});}catch(error){reject(error);}};image.onerror=()=>resolve({data,preview:false});image.src=data;});}
function readPhoto(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('Could not read the photo'));reader.readAsDataURL(file);});}
studioGenerate.addEventListener('click',async()=>{if(!tasksReady||!studioPrepared||!studioSpot||studioBusy)return;studioBusy=true;clearStudioOutput();setStudioActivity('uploading');studioGenerate.disabled=true;studioUpload.disabled=true;studioPose.disabled=true;studioStyles.disabled=true;studioOptions.disabled=true;studioStatus.classList.add('working');studioStatus.textContent='Uploading your photo…';try{const portrait=studioPrepared;studioJob=await json('/photo-scout/v1/portraits',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({portrait,background:studioSpot.provider==='google-street-view'?studioSpot.sourceUrl:studioSpot.imageUrl,provider:studioSpot.provider,place:studioSpot.name,pose:studioPose.value.trim(),style:studioStyle,posture:studioPosture.value,weather:studioWeather.value,expression:studioExpression.value,lat:studioSpot.poi?.lat??null,lon:studioSpot.poi?.lon??null})});studioStatus.textContent='Queued for the photo studio. You can switch tabs while it works.';setStudioActivity('queued');pollStudio();}catch(error){finishStudio(error.message);}});
function finishStudio(error){studioBusy=false;studioJob=null;setStudioActivity(error?'failed':'complete');studioGenerate.disabled=!studioPrepared;studioUpload.disabled=false;studioPose.disabled=false;studioStyles.disabled=false;studioOptions.disabled=false;studioStatus.classList.remove('working');studioStatus.textContent=error||'Your AI photo is ready. Lighting and placement are generated; this is not a record of a real visit.';restoreTasks();}
async function pollStudio(){if(!studioJob)return;const job=studioJob;try{const state=await json('/photo-scout/v1/portraits/'+studioJob.id,{headers:job.token?{'X-Report-Token':job.token}:{}});if(studioJob!==job)return;if(state.state==='complete'){const r=await fetch(api+'/photo-scout/v1/portraits/'+studioJob.id+'/image',{credentials:'include',headers:job.token?{'X-Report-Token':job.token}:{}});if(studioJob!==job)return;if(!r.ok)throw Error('Could not download your photo');const blob=await r.blob();if(studioJob!==job)return;await showStudioOutput(blob);if(studioJob!==job)return;finishStudio();return;}if(state.state==='failed'){finishStudio(state.error||'Could not create the photo. Please try again.');return;}setStudioActivity(state.state);studioStatus.textContent=state.state==='checking'?'Checking your person, character or animal…':state.state==='running'?'Creating your photo: matching light, perspective and shadows… This may take a few minutes.':'Queued for the photo studio. You can switch tabs while it works.';}catch(error){if(studioJob!==job)return;if(/expired|unavailable or expired/i.test(error.message)){finishStudio(error.message);return;}setStudioActivity('reconnecting');studioStatus.textContent='Connection interrupted; checking your photo again shortly.';}studioTimer=setTimeout(pollStudio,4000);}

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
 studioSaveHint.textContent='On iPhone, tap Save to Photos, then choose Save Image. You can also press and hold the photo to save it. Downloads may go to Files on your device.';
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
 const payload={...coordinates(),limit:Number(el('limit').value),preferences:el('preferences').value,query};
 try{activeSearch=await json('/photo-scout/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-Request-Token':crypto.randomUUID().replaceAll('-','')},body:JSON.stringify(payload)});restoreTasks();await poll(activeSearch.jobId,activeSearch.reportToken);}
 catch(error){message(error.message);setProgress(0,'Search could not be started',error.message,'error');}
 finally{searchBusy=Boolean(activeSearch);updateSubmitState();}
}
function fitSearchRange(){
 syncMapSelection();
 const dockHeight=document.querySelector('.scout-dock').getBoundingClientRect?.().height||120;
 map.fitBounds(searchArea.getBounds(),{paddingTopLeft:[24,80],paddingBottomRight:[24,Math.ceil(dockHeight)+24],maxZoom:18});
}
function applyTaskContext(context){
 if(!Number.isFinite(context.lat)||!Number.isFinite(context.lon))return;
 const moved=Number(el('lat').value)!==context.lat||Number(el('lon').value)!==context.lon||(context.radius&&Number(el('radius').value)!==context.radius);
 el('lat').value=String(context.lat);el('lon').value=String(context.lon);selected.setLatLng([context.lat,context.lon]);
 if(context.radius){const value=String(context.radius);if(![...el('radius').options].some(o=>o.value===value)){const option=node('option',context.radius+' m');option.value=value;el('radius').append(option);}el('radius').value=value;}
 if(context.limit)el('limit').value=String(context.limit);
 if(context.query)el('prompt-query').value=context.query;
 if(context.preferences)el('preferences').value=context.preferences;
 el('style-options').querySelectorAll('input').forEach(i=>i.checked=context.photoStyles?.length?context.photoStyles.includes(i.value):i.value==='any');
 syncMapSelection();updateParameterSummary();if(moved)fitSearchRange();
 if(context.nearbyPois&&!poiCatalog){poiCatalog={nearbyPois:context.nearbyPois,coords:context};candidatePoiLayer.clearLayers();for(const p of context.nearbyPois)L.circleMarker([p.lat,p.lon],{radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9}).bindTooltip(node('span',p.name)).addTo(candidatePoiLayer);}
}
function focusHistorySearch(context,result,history){
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
 el('search-history').open=false;
 if(history&&!history.checked){history.checked=true;persistHistory();renderHistory();}
 render(result,{save:false,mapUpdate:false});drawHistoryMap();
 // History View focuses the map on every screen; open Shortlist only on request.
 el('results').hidden=true;
 focusHistorySearch(context,result,history);
}
const savedPhoto=node('dialog',null,'photo-studio saved-photo');savedPhoto.setAttribute('aria-label','Saved photo');
const savedPhotoTop=node('div',null,'studio-heading'),savedPhotoTitle=node('h2','Your saved selfie'),savedPhotoClose=node('button','Close ×');savedPhotoClose.type='button';savedPhotoTop.append(savedPhotoTitle,savedPhotoClose);
const savedPhotoPlace=node('p',null,'small'),savedPhotoImage=node('img',null,'studio-result'),savedPhotoStatus=node('p',null,'studio-status'),savedPhotoParams=node('div',null,'saved-photo-params'),savedPhotoSave=node('button','Save to Photos','studio-save'),savedPhotoDownload=node('a','Download PNG','studio-save studio-download'),savedPhotoHint=node('p',null,'small');savedPhotoImage.alt='Saved AI-generated travel photo';savedPhotoStatus.setAttribute('role','status');savedPhotoDownload.download='photo-scout-ai-photo.png';
savedPhoto.append(savedPhotoTop,savedPhotoPlace,savedPhotoImage,savedPhotoStatus,savedPhotoParams,savedPhotoSave,savedPhotoDownload,savedPhotoHint);document.body.append(savedPhoto);
let savedPhotoGeneration=0,savedPhotoTimer=null,savedPhotoFile=null,savedPhotoUrl=null;
function renderSavedPhotoParams(context,created){
 savedPhotoPlace.textContent=(context?.name||'Saved selfie')+(context?.viewHeadingDegrees!=null?' · '+photoBearing(context).label:'');
 const fields=[['Created',new Date(created*1000).toLocaleString()]];
 if(context?.generation){const g=context.generation,label=(select,value)=>[...select.options].find(o=>o.value===value)?.textContent||value;
  fields.push(['Style',portraitStyles.find(s=>s[0]===g.style)?.[1]||g.style],['Posture',label(studioPosture,g.posture)],['Weather & light',label(studioWeather,g.weather)],['Expression',label(studioExpression,g.expression)],['Your directions',g.directions||'None']);
 }
 const list=node('dl');for(const [label,value] of fields)list.append(node('dt',label),node('dd',value));
 savedPhotoParams.replaceChildren(node('h3','Generation details'),list,...(context?.generation?[]:[node('p','The generation settings were not saved for this older photo.','small')]));
}
async function viewSavedPhoto(task){
 const generation=++savedPhotoGeneration;clearTimeout(savedPhotoTimer);if(savedPhotoUrl)URL.revokeObjectURL(savedPhotoUrl);savedPhotoUrl=null;savedPhotoFile=null;
 savedPhotoImage.hidden=true;savedPhotoImage.removeAttribute('src');savedPhotoSave.hidden=true;savedPhotoDownload.hidden=true;savedPhotoHint.textContent='';savedPhotoStatus.textContent='Loading your saved photo…';renderSavedPhotoParams(task.context,task.created);savedPhoto.showModal();
 const refresh=async()=>{try{
  const report=await json('/photo-scout/v1/portraits/'+encodeURIComponent(task.id));if(generation!==savedPhotoGeneration)return;
  renderSavedPhotoParams(report.context||task.context,task.created);
  if(report.state==='complete'){
   const response=await fetch(api+'/photo-scout/v1/portraits/'+encodeURIComponent(task.id)+'/image',{credentials:'include'});if(!response.ok)throw Error('Could not load your saved photo');const blob=await response.blob(),preview=await readPhoto(blob);if(generation!==savedPhotoGeneration)return;
   savedPhotoFile=new File([blob],'photo-scout-ai-photo.png',{type:'image/png'});savedPhotoUrl=URL.createObjectURL(blob);savedPhotoImage.src=preview;savedPhotoImage.hidden=false;savedPhotoSave.hidden=false;savedPhotoDownload.href=savedPhotoUrl;savedPhotoDownload.hidden=false;savedPhotoStatus.textContent='AI-generated photo · Saved for seven days.';savedPhotoHint.textContent='On iPhone, use Save to Photos or press and hold the photo to save it.';return;
  }
  if(report.state==='failed'){savedPhotoStatus.textContent=report.error||'This photo could not be created.';return;}
  savedPhotoStatus.textContent=report.state==='queued'?'Your selfie is queued…':'Your selfie is being created…';savedPhotoTimer=setTimeout(refresh,4000);
 }catch(error){if(generation===savedPhotoGeneration)savedPhotoStatus.textContent=error.message;}};
 await refresh();
}
savedPhotoClose.addEventListener('click',()=>savedPhoto.close());savedPhoto.addEventListener('close',()=>{savedPhotoGeneration++;clearTimeout(savedPhotoTimer);if(savedPhotoUrl)URL.revokeObjectURL(savedPhotoUrl);savedPhotoUrl=null;savedPhotoFile=null;savedPhotoImage.removeAttribute('src');});
savedPhotoSave.addEventListener('click',async()=>{
 if(!savedPhotoFile)return;
 const files=[savedPhotoFile];let supported=false;try{supported=typeof navigator.share==='function'&&typeof navigator.canShare==='function'&&navigator.canShare({files});}catch{}
 if(!supported){savedPhotoHint.textContent='Press and hold the photo to save it, or use Download PNG.';savedPhotoImage.scrollIntoView({block:'center',behavior:'smooth'});return;}
 savedPhotoSave.disabled=true;try{await navigator.share({files});}catch(error){if(error.name!=='AbortError')savedPhotoHint.textContent='The share menu could not open. Use Download PNG or press and hold the photo.';}finally{savedPhotoSave.disabled=false;}
});
async function viewSavedTask(task){
 el('search-history').open=false;
 if(task.kind==='search')el('results').hidden=true;
 if(task.kind==='portrait'){await viewSavedPhoto(task);return;}
 try{
  const report=await json('/photo-scout/v1/report/'+encodeURIComponent(task.id)),context=report.context||task.context;
  if(report.state==='complete'){showHistorySearch(report.result,context,searchHistory.find(h=>h.id===task.id));return;}
  focusHistorySearch(context,null,null);
  if(report.state==='failed')setProgress(2,'Search could not be completed',report.error||'Please try a new search.','error');
  else if(!activeSearch){activeSearch={jobId:task.id,context};poll(task.id);}
 }
 catch(error){message(error.message);}
}
function recoverStudio(task){
 clearStudioOutput();studioSpot=task.context;studioPlace.textContent=studioSpot.name||'Saved selfie';scenePreview.removeAttribute('src');personPreview.hidden=true;personPreview.removeAttribute('src');studioPrepared=null;
 studioBusy=true;studioJob={id:task.id};studioGenerate.disabled=true;studioUpload.disabled=true;studioPose.disabled=true;studioStyles.disabled=true;studioOptions.disabled=true;setStudioActivity(task.state);pollStudio();
}
function resetTaskRecovery(){
 if(savedPhoto.open)savedPhoto.close();
 taskRecoveryGeneration++;clearTimeout(taskRefreshTimer);taskRecords=[];tasksReady=false;activeSearch=null;pollGeneration++;searchBusy=false;studioBusy=false;studioJob=null;clearTimeout(studioTimer);clearStudioOutput();studioPrepared=null;studioFile=null;studioUpload.value='';studioUpload.disabled=false;studioPose.disabled=false;studioStyles.disabled=false;studioOptions.disabled=false;studioGenerate.disabled=true;personPreview.hidden=true;personPreview.removeAttribute('src');studioTask.hidden=true;selfieActivityLayer.clearLayers();renderHistory();updateSubmitState();
}
async function restoreTasks(){
 if(taskRefreshBusy)return;taskRefreshBusy=true;const generation=taskRecoveryGeneration;
 try{
  const data=await json('/photo-scout/v1/tasks');if(generation!==taskRecoveryGeneration)return;tasksReady=true;taskRecords=data.items;renderHistory();
  for(const task of taskRecords.filter(t=>t.kind==='search'&&t.state==='complete')){
   if(searchHistory.some(h=>h.id===task.id)||activeSearch?.jobId===task.id)continue;
   const report=await json('/photo-scout/v1/report/'+encodeURIComponent(task.id));if(generation!==taskRecoveryGeneration)return;
   if(report.result){searchHistory.push({id:task.id,created:task.created*1000,label:task.context.query||task.context.locationLabel||`Around ${task.context.lat}, ${task.context.lon}`,radius:task.context.radius||1000,checked:true,result:{...report.result,searchContext:task.context}});}
  }
  searchHistory.sort((a,b)=>b.created-a.created);searchHistory=searchHistory.slice(0,30);persistHistory();renderHistory();if(!searchBusy)drawHistoryMap();
  const search=taskRecords.find(t=>t.kind==='search'&&['queued','running'].includes(t.state));if(search&&!activeSearch){activeSearch={jobId:search.id,context:search.context};applyTaskContext(search.context);poll(search.id);}
  const photo=taskRecords.find(t=>t.kind==='portrait'&&['queued','checking','running'].includes(t.state));if(photo&&!studioBusy)recoverStudio(photo);
 }catch{if(!tasksReady)el('history-note').textContent='Could not reconnect to saved tasks. Retrying shortly.';}
 finally{taskRefreshBusy=false;updateSubmitState();clearTimeout(taskRefreshTimer);taskRefreshTimer=setTimeout(restoreTasks,document.hidden?60000:15000);}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){restoreTasks();if(studioJob)pollStudio();}});
