'use strict';
const api=location.hostname==='localhost'||location.hostname==='127.0.0.1'?'': 'https://api.aisoup.net';
const el=id=>document.getElementById(id), message=s=>{el('message').textContent=s};
const map=L.map('map').setView([41.8827,-87.6233],15);
const streetTiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
let vectorLayer=null,activeMapStyle='streets',mapStyleGeneration=0;
const mapStyles={streets:'liberty',minimal:'positron',night:'dark'};
function switchMapStyle(style){
 activeMapStyle=style;const generation=++mapStyleGeneration;
 document.querySelectorAll('[data-map-style]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mapStyle===style)));
 el('map-notice').textContent='';
 if(vectorLayer){map.removeLayer(vectorLayer);vectorLayer=null;}
 if(!map.hasLayer(streetTiles))streetTiles.addTo(map);
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
const searchArea=L.circle([41.8827,-87.6233],{radius:1000,color:'#375947',weight:1.5,dashArray:'5 7',fillColor:'#acd69a',fillOpacity:.12,interactive:false}).addTo(map);
function syncMapSelection(){const lat=Number(el('lat').value),lon=Number(el('lon').value);if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180)return;searchArea.setLatLng([lat,lon]).setRadius(Number(el('radius').value));el('map-selection').textContent=`${lat.toFixed(5)}, ${lon.toFixed(5)}`;el('map-radius').textContent=`Searching within ${Number(el('radius').value)>=1000?Number(el('radius').value)/1000+' km':el('radius').value+' m'}`;}
selected.on('dragend',()=>{const p=selected.getLatLng();pick(p.lat,p.lng);});
el('center-pin').addEventListener('click',()=>map.fitBounds(searchArea.getBounds(),{padding:[32,32],maxZoom:16}));
function coordinates(){const style=el('style-options').querySelector('input:checked')?.value;return {lat:Number(el('lat').value),lon:Number(el('lon').value),radius:Number(el('radius').value),photoStyles:style&&style!=='any'?[style]:null}}
function updateSelection(){const count=el("poi-list").querySelectorAll("input:checked").length;el("poi-count").textContent=`${count} of ${poiCatalog?.nearbyPois.length||0} places selected`;el("submit").disabled=searchBusy||!serviceAvailable||!count;}
function invalidatePois(){catalogGeneration++;poiCatalog=null;el("poi-selection").hidden=true;el("poi-list").replaceChildren();el("submit").disabled=true;}
function pick(lat,lon){invalidatePois();el('lat').value=lat.toFixed(6);el('lon').value=lon.toFixed(6);selected.setLatLng([lat,lon]);syncMapSelection();}
const locationStatus=s=>{el('location-status').textContent=s};
el('locate').addEventListener('click',()=>{
 if(!window.isSecureContext||!navigator.geolocation){locationStatus('Location is unavailable in this browser. Select a point on the map or enter coordinates instead.');return;}
 el('locate').disabled=true;locationStatus('Requesting your location… Allow location access in your browser to continue.');
 const finish=()=>{el('locate').disabled=false};
 try{navigator.geolocation.getCurrentPosition(position=>{
  finish();const {latitude:lat,longitude:lon,accuracy}=position.coords;
  if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180){locationStatus('Your location is outside the supported map area. Please choose a location manually.');return;}
  pick(lat,lon);map.setView([lat,lon],15);el('consent').checked=false;
  locationStatus(`Current location selected${Number.isFinite(accuracy)?` (estimated accuracy ±${Math.ceil(accuracy)} m)`:''}. Review the map, agree to the search, then click Find nearby places.`);
 },error=>{finish();locationStatus(error.code===1?'Location permission was denied. You can allow it in browser settings, or choose a point manually.':error.code===3?'Location request timed out. Try again or select a point manually.':'Your location could not be determined. Try again or select a point manually.');},{enableHighAccuracy:true,timeout:15000,maximumAge:0});}
 catch{finish();locationStatus('Location is unavailable. Select a point on the map or enter coordinates instead.');}
});
map.on('click',e=>pick(e.latlng.lat,e.latlng.lng));
for(const id of ['lat','lon']) el(id).addEventListener('input',()=>{invalidatePois();const lat=Number(el('lat').value),lon=Number(el('lon').value);if(Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=85&&Math.abs(lon)<=180){selected.setLatLng([lat,lon]);map.setView([lat,lon],15);syncMapSelection()}});
el('radius').addEventListener('change',()=>{invalidatePois();syncMapSelection();});
function styleChanged(){invalidatePois();message('Photo mood updated. Find places to refresh the list.');}
function renderStyles(styles){
 const options=[{id:'any',label:'Surprise me',description:'Find distinctive photo opportunities across all moods.'},...styles];
 for(const style of options){const label=node('label',null,'style-choice'),input=document.createElement('input');input.type='radio';input.name='photo-style';input.value=style.id;input.checked=style.id==='any';input.addEventListener('change',styleChanged);const text=node('span');const icons={any:'✳',nature:'❋',urban:'▥',vintage:'◷',iconic:'✦',artistic:'◈',waterside:'≈',minimal:'□',adventure:'△'};const icon=node('span',icons[style.id]||'✳','mood-icon');icon.setAttribute('aria-hidden','true');label.append(icon);text.append(node('strong',style.label),node('span',style.description,'style-description'));label.append(input,text);el('style-options').append(label);}
}
el('find-pois').addEventListener('click',async()=>{
 if(!el('search').reportValidity()||!serviceAvailable)return;
 invalidatePois();const generation=catalogGeneration,coords=coordinates();el('find-pois').disabled=true;message('Finding nearby places…');
 try{const data=await json('/photo-scout/v1/pois',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(coords)});
  if(generation!==catalogGeneration)return;poiCatalog={...data,coords};
  for(const p of data.nearbyPois){const row=node('label',null,'poi-choice'),check=document.createElement('input');check.type='checkbox';check.value=p.id;check.checked=true;check.addEventListener('change',updateSelection);row.append(check,node('span',`${p.name} · ${p.category} · ${p.distanceMeters} m`));el('poi-list').append(row);}
  el('poi-selection').hidden=false;updateSelection();message(data.nearbyPois.length?'Select the places you want the model to compare, then analyze their views.':'No candidate places found. Try a larger radius or another location.');
 }catch(err){if(generation===catalogGeneration)message(err.message);}finally{el('find-pois').disabled=!serviceAvailable;}
});
for(const [id,checked] of [['poi-all',true],['poi-none',false]])el(id).addEventListener('click',()=>{el('poi-list').querySelectorAll('input').forEach(c=>c.checked=checked);updateSelection();});
function node(tag,txt,cls){const n=document.createElement(tag);if(txt)n.textContent=txt;if(cls)n.className=cls;return n;}
function link(label,url){const n=node('a',label);try{const u=new URL(url);if(u.protocol!=='https:')return node('span',label);n.href=u.href;n.target='_blank';n.rel='noopener noreferrer';}catch{return node('span',label)}return n;}
function render(result,{scroll=true}={}){const root=el('results');
 const edit=node('a','Adjust location or style ↑','back-button');edit.href='#map-heading';
 const heading=node('div',null,'results-heading');heading.append(node('div','YOUR SHORTLIST','eyebrow'),edit);
 root.replaceChildren(heading,node('h2','Your nearby photo shortlist'),node('p',result.summary));
 if(result.photoStyles?.length)root.append(node('p',`Photo mood: ${result.photoStyles.map(s=>s.label).join(', ')}`,'tag'));
 if(result.candidatePoiCount!==undefined)root.append(node('p',`Found ${result.candidatePoiCount} candidate places; rankings below are based on scored images.`,'small'));const cards=node('div',null,'cards');root.append(cards);resultPins.forEach(p=>p.remove());resultPins=[];
 for(const [i,s] of (result.spots||[]).entries()){const card=node('article',null,'card');if(s.imageUrl){const img=node('img');img.src=s.imageUrl;img.alt=s.name;img.loading='lazy';img.referrerPolicy='no-referrer';img.addEventListener('error',()=>{img.replaceWith(link('Image unavailable — open the original view ↗',s.sourceUrl));});card.append(img);}else if(s.provider==='google-street-view'){const view=link('Explore this view in Google Street View ↗',s.sourceUrl);view.className='street-view-link';card.append(view);}const body=node('div',null,'body');body.append(...(s.provider==='google-street-view'?[node('p','Google Maps','GMP-attribution')]:[]),node('h3',`${i+1}. ${s.name}`),node('span',`${s.score}/100 subjective photo score · ${s.confidence} confidence`,'tag'),node('p',`Image source: ${s.provider}`,'small'),...(s.poi?[node('p',`Candidate place: ${s.poi.name} · ${s.poi.category}`,'small')]:[]),node('p',s.visible_evidence),node('p',`Photo idea: ${s.photo_tip}`),...(s.viewHeadingDegrees!=null?[node('p',`Inspected camera direction: ${s.viewHeadingDegrees}°`,'small')]:[]),node('p',`${s.distanceMeters} m straight-line distance · ${s.locationType}`),node('p',`Uncertainty: ${s.uncertainty}`),node('p',s.coordinateWarning),link(s.provider==='google-street-view'?'View Google Street View ↗':'Original image ↗',s.sourceUrl),node('p',`${s.author} · ${s.license} · source date: ${s.sourceDate||s.capturedAt||'unknown'}`),link('Image license ↗',s.licenseUrl));card.append(body);cards.append(card);if(s.provider!=='google-street-view'){const p=L.marker([s.lat,s.lon]).addTo(map).bindPopup(node('span',s.name));resultPins.push(p);}}
 if(result.imageAssessments?.length){const detail=node('details',null,'score-details');detail.append(node('summary',`All ${result.imageAssessments.length} image scores`));const list=node('ul');for(const a of result.imageAssessments){const li=node('li');li.append(node('strong',`${a.score}/100 · ${a.name}`),node('p',`${a.provider} · ${a.eligibleForRecommendation?'Suitable candidate':'Not recommended'}`,'small'),node('p',a.visible_evidence),...(a.exclusionReason?[node('p',`Why not shortlisted: ${a.exclusionReason}`)]:[]),node('p',`Uncertainty: ${a.uncertainty}`));list.append(li);}detail.append(list);root.append(detail);}
 if(result.nearbyPois?.length){root.append(node('h3','Nearby candidate places'),node('p','These OpenStreetMap candidates guided image discovery. Inclusion here does not mean the model recommended them.'));const list=node('ul');for(const p of result.nearbyPois){const li=node('li');li.append(link(`${p.name} · ${p.category} · ${p.distanceMeters} m ↗`,p.sourceUrl));list.append(li);}root.append(list,node('p','© OpenStreetMap contributors · ODbL 1.0','small'));}
 if(result.sources){const coverage=node('p',Object.entries(result.sources).map(([name,s])=>`${name}: ${s.status==='ok'?(s.eligibleImages!==undefined?`${s.eligibleImages} eligible images${s.sampledImages!==undefined?`; ${s.sampledImages} selected for scoring`:""}`:'available'):'temporarily unavailable'}`).join(' · '),'small');root.append(coverage);}
 root.append(node('p',`${result.analysisMethod==='fixed-batch-scoring'?'Scored':'Inspected'} ${result.inspectedImages} images${result.inspectedImageSources?.length?" from "+result.inspectedImageSources.join(", "):""}. ${result.coverage}`,'small'));if(scroll)root.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}
async function json(url,options){const r=await fetch(api+url,options),data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Request failed');return data;}
const savedSearchKey='photo-scout-active-search';
function savedSearch(){try{const d=JSON.parse(localStorage.getItem(savedSearchKey));if(d&&d.expiresAt>Date.now())return d;localStorage.removeItem(savedSearchKey);}catch{}return null;}
function saveSearch(d){localStorage.setItem(savedSearchKey,JSON.stringify(d));}
async function submitSaved(d){const result=await json('/photo-scout/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-Request-Token':d.token},body:JSON.stringify(d.payload)});d={jobId:result.jobId,token:result.reportToken,expiresAt:result.expiresAt*1000};saveSearch(d);const url=new URL(location.href);url.searchParams.set('job',d.jobId);history.replaceState(null,'',url);return d;}
function offerSavedReport(job,token){el('saved-report').hidden=false;el('view-last-report').onclick=()=>poll(job,token,true);}
async function poll(job,privateToken,showCompleted=true){const generation=++pollGeneration;const token=privateToken||sessionStorage.getItem('photo-scout:'+job);if(!token){message('Open this report in the browser where you submitted the search.');return;}searchBusy=true;updateSelection();
 while(generation===pollGeneration){
  try{const d=await json('/photo-scout/v1/report/'+encodeURIComponent(job),{headers:{'X-Report-Token':token}});if(generation!==pollGeneration)return;
   if(d.state==='complete'){offerSavedReport(job,token);render(d.result,{scroll:showCompleted});message(showCompleted?'Your report is ready below. Adjust your search above whenever you like.':'Your last report is available below. The map is ready for a new search.');searchBusy=false;updateSelection();return;}
   showCompleted=true;
   if(d.state==='failed'){message(d.error||'Search failed. Please try again.');searchBusy=false;updateSelection();return;}
   message(d.state==='queued'?'Search queued. You can leave this page; processing continues on the server.':'Scoring nearby images on the server. You can switch tabs or return later.');
  }catch(e){if(generation!==pollGeneration)return;if(/not found|expired/i.test(e.message)){message(e.message);searchBusy=false;updateSelection();return;}message('Connection interrupted. Your server task continues; reconnecting…');}
  await new Promise(r=>setTimeout(r,document.hidden?15000:5000));
 }
}
async function resumeSearch(){const requested=new URLSearchParams(location.search).get('job');const saved=savedSearch();const d=saved&&(!requested||!saved.jobId||saved.jobId===requested)?saved:null;try{if(d){const submitted=d.jobId?d:await submitSaved(d);await poll(submitted.jobId,submitted.token,false);}else{const job=new URLSearchParams(location.search).get('job');if(job)await poll(job,undefined,new URLSearchParams(location.search).has('session_id'));}}catch(e){searchBusy=false;updateSelection();message('Could not confirm submission. Return to this page to retry the same saved request.');}}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!el('results').querySelector('.cards'))resumeSearch();});
window.addEventListener('pageshow',e=>{if(e.persisted)resumeSearch();});
el('search').addEventListener('submit',async e=>{e.preventDefault();if(searchBusy||!poiCatalog)return;const selectedPoiIds=[...el('poi-list').querySelectorAll('input:checked')].map(c=>c.value);if(!selectedPoiIds.length){message('Select at least one place.');return;}searchBusy=true;el('saved-report').hidden=true;updateSelection();message(humanFreePreview?'Submitting background search…':'Checking image coverage before checkout…');const payload={...poiCatalog.coords,selectedPoiIds,poiCatalogToken:poiCatalog.poiCatalogToken,limit:Number(el('limit').value),preferences:el('preferences').value};try{if(humanFreePreview){const d={token:crypto.randomUUID().replaceAll('-',''),payload,expiresAt:Date.now()+86400000};saveSearch(d);const submitted=await submitSaved(d);await poll(submitted.jobId,submitted.token);}else{const d=await json('/photo-scout/v1/checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});sessionStorage.setItem('photo-scout:'+d.jobId,d.reportToken);location.assign(d.checkoutUrl);}}catch(err){message(err.message+' Your saved request can be resumed by refreshing this page.');}finally{searchBusy=false;updateSelection();}});
(async()=>{try{const s=await json('/photo-scout/v1/status');humanFreePreview=s.humanFreePreview===true;renderStyles(s.photoStyles||[]);el('submit').textContent=s.enabled&&humanFreePreview?'Analyze selected places · Free test':s.enabled&&s.humanPriceUsd?`Analyze selected places · $${s.humanPriceUsd}`:'Exploration coming soon';serviceAvailable=Boolean(s.enabled&&(humanFreePreview||s.humanPriceUsd));updateSelection();el('find-pois').disabled=!serviceAvailable;el('test-notice').hidden=!humanFreePreview;await resumeSearch();}catch{message('The service is temporarily unavailable.');}})();

el('sample').addEventListener('click',async()=>{try{const r=await fetch('./sample.json');if(!r.ok)throw Error('Sample unavailable');const data=await r.json();render(data);}catch(e){message(e.message)}});

el('sample-paris').addEventListener('click',async()=>{try{const r=await fetch('./sample-paris.json');if(!r.ok)throw Error('Sample unavailable');render(await r.json());}catch(e){message(e.message)}});

el("sample-google").addEventListener("click",async()=>{try{const r=await fetch("./sample-google.json");if(!r.ok)throw Error("Sample unavailable");const data=await r.json();render(data);message(data.sampleNotice);}catch(e){message(e.message)}});
