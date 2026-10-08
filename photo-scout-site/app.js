'use strict';
const api=location.hostname==='localhost'||location.hostname==='127.0.0.1'?'': 'https://api.aisoup.net';
const el=id=>document.getElementById(id), message=s=>{el('message').textContent=s};
const map=L.map('map',{zoomControl:false}).setView([41.8827,-87.6233],15);
L.control.zoom({position:'bottomright'}).addTo(map);
const controls=el('map-controls');controls.open=true;L.DomEvent.disableClickPropagation(controls);L.DomEvent.disableScrollPropagation(controls);L.DomEvent.disableClickPropagation(document.querySelector('.map-toolbar'));
const streetTiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
let rasterLayer=null;
let vectorLayer=null,activeMapStyle='streets',mapStyleGeneration=0;
const mapStyles={streets:'liberty',minimal:'positron',night:'dark',bright:'bright'};
function switchMapStyle(style){
 activeMapStyle=style;const generation=++mapStyleGeneration;
 document.querySelectorAll('[data-map-style]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mapStyle===style)));
 el('map-notice').textContent='';
 if(vectorLayer){map.removeLayer(vectorLayer);vectorLayer=null;}
 if(rasterLayer){map.removeLayer(rasterLayer);rasterLayer=null;}
 if(!map.hasLayer(streetTiles))streetTiles.addTo(map);
 if(style==='aerial'||style==='topographic'){
  const name=style==='aerial'?'USGSImageryOnly':'USGSTopo';
  const layer=L.tileLayer('https://basemap.nationalmap.gov/arcgis/rest/services/'+name+'/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:style==='aerial'?'USDA · USGS The National Map — Orthoimagery':'USGS The National Map · USGS, USFS, NOAA and contributors'});rasterLayer=layer;
  let failed=false;
  layer.once('load',()=>{if(generation===mapStyleGeneration&&!failed&&map.hasLayer(streetTiles))map.removeLayer(streetTiles);});
  layer.on('tileerror',()=>{if(generation!==mapStyleGeneration||failed)return;failed=true;map.removeLayer(layer);rasterLayer=null;el('map-notice').textContent='This U.S. basemap is unavailable here. Showing the standard street map.';if(!map.hasLayer(streetTiles))streetTiles.addTo(map);});
  el('map-notice').textContent='U.S. coverage. Imagery and topography dates vary; these layers are for choosing a location, not live conditions.';
  layer.addTo(map);return;
 }
 if(!window.maplibregl||!L.maplibreGL){el('map-notice').textContent='This browser is using the standard street map.';return;}
 try{
  const layer=L.maplibreGL({style:'https://tiles.openfreemap.org/styles/'+mapStyles[style],attribution:'<a href="https://openfreemap.org/">OpenFreeMap</a> · <a href="https://openmaptiles.org/">OpenMapTiles</a> · © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',interactive:false}).addTo(map);vectorLayer=layer;
  const gl=layer.getMaplibreMap();gl.once('load',()=>{if(generation===mapStyleGeneration&&map.hasLayer(streetTiles))map.removeLayer(streetTiles);});
  gl.on('error',()=>{if(generation!==mapStyleGeneration)return;mapStyleGeneration++;if(map.hasLayer(layer))map.removeLayer(layer);vectorLayer=null;if(!map.hasLayer(streetTiles))streetTiles.addTo(map);el('map-notice').textContent='Map style unavailable. Showing the standard street map.';});
 }catch{el('map-notice').textContent='Map style unavailable. Showing the standard street map.';}
}
document.querySelectorAll('[data-map-style]').forEach(b=>b.addEventListener('click',()=>switchMapStyle(b.dataset.mapStyle)));
switchMapStyle('streets');
let selected=L.marker([41.8827,-87.6233],{draggable:true,title:'Selected location: drag to move',icon:L.divIcon({className:'scout-pin',html:'<span aria-hidden="true">✳</span>',iconSize:[44,44],iconAnchor:[22,22]})}).addTo(map), resultPins=[];let humanFreePreview=false, serviceAvailable=false, poiCatalog=null, catalogGeneration=0, searchBusy=false, pollGeneration=0;
const candidatePoiLayer=L.layerGroup().addTo(map),photoLocationLayer=L.layerGroup().addTo(map);
const searchArea=L.circle([41.8827,-87.6233],{radius:1000,color:'#375947',weight:1.5,dashArray:'5 7',fillColor:'#acd69a',fillOpacity:.12,interactive:false}).addTo(map);
L.control.layers(null,{'Search radius':searchArea,'Candidate places':candidatePoiLayer,'Recommended places':photoLocationLayer},{collapsed:true}).addTo(map);
function syncMapSelection(){const lat=Number(el('lat').value),lon=Number(el('lon').value);if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180)return;searchArea.setLatLng([lat,lon]).setRadius(Number(el('radius').value));el('map-selection').textContent=`${lat.toFixed(5)}, ${lon.toFixed(5)}`;el('coordinate-readout').textContent=el('map-selection').textContent;el('map-radius').textContent=`Searching within ${Number(el('radius').value)>=1000?Number(el('radius').value)/1000+' km':el('radius').value+' m'}`;}
selected.on('dragend',()=>{const p=selected.getLatLng();pick(p.lat,p.lng);});
el('center-pin').addEventListener('click',()=>map.fitBounds(searchArea.getBounds(),{padding:[32,32],maxZoom:16}));
function coordinates(){const styles=[...el('style-options').querySelectorAll('input:checked')].map(i=>i.value).filter(s=>s!=='any');return {lat:Number(el('lat').value),lon:Number(el('lon').value),radius:Number(el('radius').value),photoStyles:styles.length?styles:null}}
function updateSelection(){const count=el("poi-list").querySelectorAll("input:checked").length;el("poi-count").textContent=`${count} of ${poiCatalog?.nearbyPois.length||0} places selected`;el("submit").disabled=searchBusy||!serviceAvailable||!count;}
function invalidatePois(){catalogGeneration++;candidatePoiLayer.clearLayers();poiCatalog=null;el("poi-selection").hidden=true;el("poi-list").replaceChildren();el("submit").disabled=true;}
function pick(lat,lon){invalidatePois();el('lat').value=lat.toFixed(6);el('lon').value=lon.toFixed(6);selected.setLatLng([lat,lon]);syncMapSelection();}
const locationStatus=s=>{el('location-status').textContent=s;el('map-notice').textContent=s};
el('locate').addEventListener('click',()=>{
 if(!window.isSecureContext||!navigator.geolocation){locationStatus('Location is unavailable in this browser. Open the HTTPS website, or choose a point on the map.');return;}
 const button=el('locate');controls.open=true;
 window.PhotoScoutLocation.request({geolocation:navigator.geolocation,onState:state=>{
  button.disabled=state.status==='pending';button.setAttribute('aria-busy',String(state.status==='pending'));button.textContent=state.status==='pending'?'Finding your location…':state.status==='success'?'✓ Location selected · locate again':'⌖ Find spots near my location';locationStatus(state.message);
 },onPosition:position=>{
  const {latitude:lat,longitude:lon,accuracy}=position.coords;
  pick(lat,lon);map.fitBounds(searchArea.getBounds(),{padding:[32,32],maxZoom:16});
  locationStatus(`Device location selected${Number.isFinite(accuracy)?` (accuracy ±${Math.ceil(accuracy)} m)`:''}. Finding nearby places…`);
  el('map-notice').textContent=`Your current location is selected${Number.isFinite(accuracy)?` · estimated accuracy ±${Math.ceil(accuracy)} m`:''}.`;
  el('map-heading').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  runSearchPipeline();
 }});
});
map.on('click',e=>pick(e.latlng.lat,e.latlng.lng));
el('radius').addEventListener('change',()=>{invalidatePois();syncMapSelection();});
function styleChanged(){invalidatePois();message('Photo mood updated. Find places to refresh the list.');}
function renderStyles(styles){
 const options=[{id:'any',label:'Surprise me',description:'Find distinctive photo opportunities across all moods.'},...styles];
 for(const style of options){const label=node('label',null,'style-choice'),input=document.createElement('input');input.type='checkbox';input.name='photo-style';input.value=style.id;input.checked=style.id==='any';input.addEventListener('change',()=>{const inputs=el('style-options').querySelectorAll('input');if(input.checked&&input.value==='any')inputs.forEach(i=>i.checked=i===input);else if(input.checked)inputs.forEach(i=>{if(i.value==='any')i.checked=false;});if(![...inputs].some(i=>i.checked))inputs.forEach(i=>{if(i.value==='any')i.checked=true;});styleChanged();});const text=node('span');const icons={any:'✳',nature:'❋',urban:'▥',vintage:'◷',iconic:'✦',artistic:'◈',waterside:'≈',minimal:'□',adventure:'△'};const icon=node('span',icons[style.id]||'✳','mood-icon');icon.setAttribute('aria-hidden','true');label.append(icon);text.append(node('strong',style.label),node('span',style.description,'style-description'));label.append(input,text);el('style-options').append(label);}
}
async function loadPois(){
 if(!el('search').reportValidity()||!serviceAvailable)return;
 invalidatePois();const generation=catalogGeneration,coords=coordinates();el('find-pois').disabled=true;message('Finding nearby places…');
 try{const data=await json('/photo-scout/v1/pois',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(coords)});
  if(generation!==catalogGeneration)return;poiCatalog={...data,coords};
  for(const p of data.nearbyPois){L.circleMarker([p.lat,p.lon],{radius:6,color:'#fff',weight:2,fillColor:'#456951',fillOpacity:.9}).bindTooltip(node('span',p.name)).addTo(candidatePoiLayer);const row=node('label',null,'poi-choice'),check=document.createElement('input');check.type='checkbox';check.value=p.id;check.checked=true;check.addEventListener('change',updateSelection);row.append(check,node('span',`${p.name} · ${p.category} · ${p.distanceMeters} m`));el('poi-list').append(row);}
  el('poi-selection').hidden=false;updateSelection();message(data.nearbyPois.length?'Select the places you want the model to compare, then analyze their views.':'No candidate places found. Try a larger radius or another location.');
 }catch(err){if(generation===catalogGeneration)message(err.message);}finally{el('find-pois').disabled=!serviceAvailable;}
}
el('find-pois').addEventListener('click',loadPois);
for(const [id,checked] of [['poi-all',true],['poi-none',false]])el(id).addEventListener('click',()=>{el('poi-list').querySelectorAll('input').forEach(c=>c.checked=checked);updateSelection();});
function node(tag,txt,cls){const n=document.createElement(tag);if(txt)n.textContent=txt;if(cls)n.className=cls;return n;}
function link(label,url){const n=node('a',label);try{const u=new URL(url);if(u.protocol!=='https:')return node('span',label);n.href=u.href;n.target='_blank';n.rel='noopener noreferrer';}catch{return node('span',label)}return n;}
function render(result,{scroll=true}={}){const root=el('results');
 const edit=node('a','Adjust location or style ↑','back-button');edit.href='#map-heading';
 const heading=node('div',null,'results-heading');heading.append(node('div','YOUR SHORTLIST','eyebrow'),edit);
 root.replaceChildren(heading,node('h2','Your nearby photo shortlist'),node('p',result.summary));
 if(result.photoStyles?.length)root.append(node('p',`Photo mood: ${result.photoStyles.map(s=>s.label).join(', ')}`,'tag'));
 if(result.candidatePoiCount!==undefined)root.append(node('p',`Found ${result.candidatePoiCount} candidate places; rankings below are based on scored images.`,'small'));const cards=node('div',null,'cards');root.append(cards);photoLocationLayer.clearLayers();resultPins=[];
 for(const [i,s] of (result.spots||[]).entries()){const card=node('article',null,'card');if(s.imageUrl){const img=node('img');img.src=s.imageUrl;img.alt=s.name;img.loading='lazy';img.referrerPolicy='no-referrer';img.addEventListener('error',()=>{img.replaceWith(link('Image unavailable — open the original view ↗',s.sourceUrl));});card.append(img);}else if(s.provider==='google-street-view'){const view=link('Explore this view in Google Street View ↗',s.sourceUrl);view.className='street-view-link';card.append(view);}const body=node('div',null,'body');body.append(...(s.provider==='google-street-view'?[node('p','Google Maps','GMP-attribution')]:[]),node('h3',`${i+1}. ${s.name}`),node('span',`${s.score}/100 subjective photo score · ${s.confidence} confidence`,'tag'),node('p',`Image source: ${s.provider}`,'small'),...(s.poi?[node('p',`Candidate place: ${s.poi.name} · ${s.poi.category}`,'small')]:[]),node('p',s.visible_evidence),node('p',`Photo idea: ${s.photo_tip}`),...(s.viewHeadingDegrees!=null?[node('p',`Inspected camera direction: ${s.viewHeadingDegrees}°`,'small')]:[]),node('p',`${s.distanceMeters} m straight-line distance · ${s.locationType}`),node('p',`Uncertainty: ${s.uncertainty}`),node('p',s.coordinateWarning),link(s.provider==='google-street-view'?'View Google Street View ↗':'Original image ↗',s.sourceUrl),node('p',`${s.author} · ${s.license} · source date: ${s.sourceDate||s.capturedAt||'unknown'}`),link('Image license ↗',s.licenseUrl));card.append(body);cards.append(card);if(s.poi||s.provider!=='google-street-view'){const pos=s.poi||s;const p=L.marker([pos.lat,pos.lon],{icon:L.divIcon({className:'rank-pin',html:'<span>'+String(i+1)+'</span>',iconSize:[34,34],iconAnchor:[17,34]})}).addTo(photoLocationLayer).bindPopup(node('span',pos.name));resultPins.push(p);}}
 if(result.imageAssessments?.length){const detail=node('details',null,'score-details');detail.append(node('summary',`All ${result.imageAssessments.length} image scores`));const list=node('ul');for(const a of result.imageAssessments){const li=node('li');li.append(node('strong',`${a.score}/100 · ${a.name}`),node('p',`${a.provider} · ${a.eligibleForRecommendation?'Suitable candidate':'Not recommended'}`,'small'),node('p',a.visible_evidence),...(a.exclusionReason?[node('p',`Why not shortlisted: ${a.exclusionReason}`)]:[]),node('p',`Uncertainty: ${a.uncertainty}`));list.append(li);}detail.append(list);root.append(detail);}
 if(result.nearbyPois?.length){root.append(node('h3','Nearby candidate places'),node('p','These OpenStreetMap candidates guided image discovery. Inclusion here does not mean the model recommended them.'));const list=node('ul');for(const p of result.nearbyPois){const li=node('li');li.append(link(`${p.name} · ${p.category} · ${p.distanceMeters} m ↗`,p.sourceUrl));list.append(li);}root.append(list,node('p','© OpenStreetMap contributors · ODbL 1.0','small'));}
 if(result.sources){const coverage=node('p',Object.entries(result.sources).map(([name,s])=>`${name}: ${s.status==='ok'?(s.eligibleImages!==undefined?`${s.eligibleImages} eligible images${s.sampledImages!==undefined?`; ${s.sampledImages} selected for scoring`:""}`:'available'):'temporarily unavailable'}`).join(' · '),'small');root.append(coverage);}
 root.append(node('p',`${result.analysisMethod==='fixed-batch-scoring'?'Scored':'Inspected'} ${result.inspectedImages} images${result.inspectedImageSources?.length?" from "+result.inspectedImageSources.join(", "):""}. ${result.coverage}`,'small'));if(resultPins.length)map.fitBounds(L.featureGroup([selected,...resultPins]).getBounds().pad(.2),{maxZoom:16});if(scroll)root.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}
async function json(url,options){const r=await fetch(api+url,options),data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Request failed');return data;}
// Remove only legacy Photo Scout browser state; never restore a previous search.
try{localStorage.removeItem('photo-scout-active-search');for(const key of Object.keys(sessionStorage)){if(key.startsWith('photo-scout:'))sessionStorage.removeItem(key);}}catch{}
if(location.search){const clean=new URL(location.href);clean.search='';history.replaceState(null,'',clean);}
let activeSearch=null;
async function poll(job,token){const generation=++pollGeneration;searchBusy=true;updateSelection();
 while(generation===pollGeneration){
  try{const d=await json('/photo-scout/v1/report/'+encodeURIComponent(job),{headers:{'X-Report-Token':token}});if(generation!==pollGeneration)return;
   if(d.state==='complete'){render(d.result);message('Your report is ready below. Refreshing starts fresh.');activeSearch=null;searchBusy=false;updateSelection();return;}
   if(d.state==='failed'){message(d.error||'Search failed. Please try again.');activeSearch=null;searchBusy=false;updateSelection();return;}
   message(d.state==='queued'?'Search queued on the server. You can switch tabs; keep this page open.':'Scoring nearby images. You can switch tabs; refreshing will clear this search.');
  }catch(e){if(generation!==pollGeneration)return;if(/not found|expired/i.test(e.message)){message(e.message);activeSearch=null;searchBusy=false;updateSelection();return;}message('Connection interrupted. Your task continues on the server; reconnecting…');}
  await new Promise(r=>setTimeout(r,document.hidden?15000:5000));
 }
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&activeSearch)poll(activeSearch.jobId,activeSearch.reportToken);});
window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
async function analyzePlaces(){if(searchBusy||!poiCatalog||!humanFreePreview)return;const selectedPoiIds=[...el('poi-list').querySelectorAll('input:checked')].map(c=>c.value);if(!selectedPoiIds.length){message('Select at least one place.');return;}searchBusy=true;updateSelection();message('Submitting background search…');const payload={...poiCatalog.coords,selectedPoiIds,poiCatalogToken:poiCatalog.poiCatalogToken,limit:Number(el('limit').value),preferences:el('preferences').value};try{activeSearch=await json('/photo-scout/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-Request-Token':crypto.randomUUID().replaceAll('-','')},body:JSON.stringify(payload)});await poll(activeSearch.jobId,activeSearch.reportToken);}catch(err){message(err.message);}finally{searchBusy=Boolean(activeSearch);updateSelection();}}
el('search').addEventListener('submit',e=>{e.preventDefault();analyzePlaces();});
(async()=>{try{const s=await json('/photo-scout/v1/status');humanFreePreview=s.humanFreePreview===true;renderStyles(s.photoStyles||[]);el('submit').textContent=s.enabled&&humanFreePreview?'Analyze selected places · Free test':'Website testing unavailable';serviceAvailable=Boolean(s.enabled&&humanFreePreview);updateSelection();el('find-pois').disabled=!serviceAvailable;el('resolve-query').disabled=!serviceAvailable;el('test-notice').hidden=!humanFreePreview;}catch{message('The service is temporarily unavailable.');}})();
let intentGeneration=0,pipelineBusy=false;
async function runSearchPipeline(){if(pipelineBusy||searchBusy)return;pipelineBusy=true;try{await loadPois();if(poiCatalog?.nearbyPois.length)await analyzePlaces();}finally{pipelineBusy=false;}}
async function applyIntent(plan,place){pick(place.lat,place.lon);const radius=String(plan.radiusMeters);if(![...el('radius').options].some(o=>o.value===radius)){const option=node('option',radius+' m');option.value=radius;el('radius').append(option);}el('radius').value=radius;syncMapSelection();map.fitBounds(searchArea.getBounds(),{padding:[40,40],maxZoom:16});el('style-options').querySelectorAll('input').forEach(i=>i.checked=plan.photoStyles.length?plan.photoStyles.includes(i.value):i.value==='any');const count=String(plan.limit);if(![...el('limit').options].some(o=>o.value===count)){const option=node('option','Top '+count);option.value=count;el('limit').append(option);}el('limit').value=count;el('preferences').value=plan.preferences;controls.open=true;el('location-options').replaceChildren();el('prompt-status').textContent=place.label+' · '+plan.explanation;await runSearchPipeline();}
el('prompt-form').addEventListener('submit',async e=>{e.preventDefault();if(!serviceAvailable)return;const generation=++intentGeneration;el('resolve-query').disabled=true;el('location-options').replaceChildren();el('prompt-status').textContent='Understanding your request and finding matching locations…';try{const c=coordinates();const plan=await json('/photo-scout/v1/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:el('prompt-query').value,lat:c.lat,lon:c.lon,radius:c.radius,limit:Number(el('limit').value),photoStyles:c.photoStyles||[],preferences:el('preferences').value})});if(generation!==intentGeneration)return;if(plan.clarification){el('prompt-status').textContent=plan.clarification;return;}if(!plan.locations.length){el('prompt-status').textContent='No matching location. Please add a city or place name.';return;}el('prompt-status').textContent='Using '+plan.locations[0].label+'. Finding and scoring nearby views…';await applyIntent(plan,plan.locations[0]);}catch(err){el('prompt-status').textContent=err.message;}finally{el('resolve-query').disabled=!serviceAvailable;}});

el('sample').addEventListener('click',async()=>{try{const r=await fetch('./sample.json');if(!r.ok)throw Error('Sample unavailable');const data=await r.json();render(data);}catch(e){message(e.message)}});

el('sample-paris').addEventListener('click',async()=>{try{const r=await fetch('./sample-paris.json');if(!r.ok)throw Error('Sample unavailable');render(await r.json());}catch(e){message(e.message)}});

el("sample-google").addEventListener("click",async()=>{try{const r=await fetch("./sample-google.json");if(!r.ok)throw Error("Sample unavailable");const data=await r.json();render(data);message(data.sampleNotice);}catch(e){message(e.message)}});
