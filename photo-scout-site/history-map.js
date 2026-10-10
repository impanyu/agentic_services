// Small raster previews; no extra interactive map/WebGL instances in history rows.
(function(global){
 const W=72,H=64,CIRC=40075016.686,cache=new Map(),pending=new Map();let queue=Promise.resolve();
 const valid=n=>typeof n==='number'&&Number.isFinite(n);
 function area(entry){const c={...(entry.history?.result?.searchContext||{}),...(entry.task?.context||{})};return valid(c.lat)&&valid(c.lon)&&Math.abs(c.lat)<=85&&Math.abs(c.lon)<=180?{lat:c.lat,lon:c.lon,radius:Number(c.radius||entry.history?.radius||1000),label:c.locationLabel||''}:null;}
 function geometry(a){const cos=Math.cos(a.lat*Math.PI/180),radius=Math.max(100,Math.min(20000,a.radius||1000)),z=Math.max(0,Math.min(16,Math.floor(Math.log2(CIRC*cos*22/(256*radius))))),size=256*2**z,sin=Math.sin(a.lat*Math.PI/180),x=(a.lon+180)/360*size,y=(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*size,left=x-W/2,top=y-H/2,tiles=[];
  for(let tx=Math.floor(left/256);tx<=Math.floor((left+W)/256);tx++)for(let ty=Math.floor(top/256);ty<=Math.floor((top+H)/256);ty++)if(ty>=0&&ty<2**z)tiles.push({z,x:((tx%2**z)+2**z)%2**z,y:ty,left:tx*256-left,top:ty*256-top});
  return {tiles,radius:radius/(CIRC*cos/size),width:W,height:H};
 }
 function thumbnail(a){const box=document.createElement('span');box.className='search-history-thumb';box.setAttribute('aria-hidden','true');if(!a){box.textContent='⌖';return box;}const g=geometry(a);
  for(const tile of g.tiles){const img=document.createElement('img');img.alt='';img.loading='lazy';img.decoding='async';img.src=`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/${tile.z}/${tile.y}/${tile.x}`;img.style.left=tile.left+'px';img.style.top=tile.top+'px';img.addEventListener('error',()=>{img.hidden=true;box.classList.add('map-unavailable');});box.append(img);}
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  for(const [radius,cls] of [[g.radius,'history-range-ring'],[3,'history-center-dot']]){const circle=document.createElementNS(ns,'circle');circle.setAttribute('cx',W/2);circle.setAttribute('cy',H/2);circle.setAttribute('r',radius);circle.setAttribute('class',cls);svg.append(circle);}box.append(svg);return box;
 }
 function key(a){return a.lat.toFixed(5)+','+a.lon.toFixed(5);}
 try{for(const [k,v] of JSON.parse(sessionStorage.getItem('photo-scout-history-cities-v1')||'[]'))if(v.label&&v.expires>Date.now())cache.set(k,v);}catch{}
 function peek(a){return cache.get(key(a))?.label||'';}
 function cityLabel(p){const city=p.city||(['city','town','village','hamlet'].includes(p.osm_value)?p.name:'');return city?[...new Set([city,p.state,p.country].filter(Boolean))].join(', '):'';}
 function city(a){const k=key(a),existing=cache.get(k);if(existing&&existing.expires>Date.now())return Promise.resolve(existing.label);if(pending.has(k))return pending.get(k);
  const task=queue.then(async()=>{let label='';try{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);try{const response=await fetch('https://photon.komoot.io/reverse?'+new URLSearchParams({lat:a.lat,lon:a.lon,lang:'en',limit:3}),{signal:controller.signal,credentials:'omit',referrerPolicy:'no-referrer'});if(response.ok){const data=await response.json();label=(data.features||[]).map(f=>cityLabel(f.properties||{})).find(Boolean)||'';}}finally{clearTimeout(timer);}}catch{}
   cache.set(k,{label,expires:Date.now()+(label?7*86400000:60000)});while(cache.size>300)cache.delete(cache.keys().next().value);try{sessionStorage.setItem('photo-scout-history-cities-v1',JSON.stringify([...cache]));}catch{}return label;
  });pending.set(k,task);queue=task.then(()=>new Promise(resolve=>setTimeout(resolve,1000)));task.finally(()=>pending.delete(k));return task;
 }
 function hydrate(root){for(const item of root.querySelectorAll('.history-location[data-lat]')){if(item.dataset.loading)continue;item.dataset.loading='true';const a={lat:Number(item.dataset.lat),lon:Number(item.dataset.lon)};city(a).then(label=>{if(label&&item.isConnected)item.textContent=label;});}}
 global.PhotoScoutHistoryMap={area,geometry,thumbnail,peek,cityLabel,hydrate};
})(window);
