const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');

test('first vector frame uses selected details without a full street-map placeholder',async()=>{
 const added=[],requests=[],styles=[],original={version:8,sources:{},layers:[
  {id:'road',type:'line','source-layer':'transportation'},
  {id:'name',type:'symbol','source-layer':'place'},
  {id:'road-name',type:'symbol','source-layer':'transportation_name'},
  {id:'building',type:'fill','source-layer':'building'},
  {id:'park',type:'fill','source-layer':'park'},
  {id:'boundary',type:'line','source-layer':'boundary'},
  {id:'hidden-name',type:'symbol','source-layer':'place',layout:{visibility:'none'}}]};
 const element={open:false,addEventListener(){},setAttribute(){},textContent:''};
 const map={setView(){return this;},hasLayer:l=>added.includes(l),removeLayer:l=>{const i=added.indexOf(l);if(i>=0)added.splice(i,1);}};
 const layer=()=>({on(){},addTo(){added.push(this);return this;}});
 const L={map:()=>map,control:{zoom:layer},tileLayer:layer,DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}},maplibreGL(options){styles.push(options.style);return {...layer(),getMaplibreMap(){return {getContainer:()=>({style:{}}),getCanvas:()=>({style:{}}),once(){},on(){}};}};}};
 const context={L,window:{maplibregl:{}},document:{body:{dataset:{}},getElementById:()=>element,querySelector:()=>element,querySelectorAll:()=>[]},location:{hostname:'test'},setTimeout:()=>1,clearTimeout(){},fetch:()=>new Promise(resolve=>requests.push(resolve))};
 const source=readFileSync('photo-scout-site/app.js','utf8').split('let selected=')[0];
 vm.runInNewContext(source,context);
 assert.equal(added.length,2,'a quiet raster base is added immediately while vector style loads');
 requests[0]({ok:true,json:async()=>structuredClone(original)});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(styles.length,1);
 const visibility=Object.fromEntries(styles[0].layers.map(l=>[l.id,l.layout?.visibility||'visible']));
 assert.deepEqual(visibility,{road:'visible',name:'visible','road-name':'none',building:'none',park:'none',boundary:'none','hidden-name':'none'});
 // An older style response must never paint over a newer choice.
 const old=context.switchMapStyle('night'),latest=context.switchMapStyle('bright');
 requests[2]({ok:true,json:async()=>structuredClone(original)});await latest;
 requests[1]({ok:true,json:async()=>structuredClone(original)});await old;
 assert.equal(styles.length,2);
});

function loadingFixture(){
 const layers=[],timers=[],requests=[],vectors=[],rasters=[];
 const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{open:false,textContent:'',addEventListener(){},setAttribute(){}});return elements.get(id);};
 const map={setView(){return this;},hasLayer:l=>layers.includes(l),removeLayer(l){layers.splice(layers.indexOf(l),1);}};
 const layer=()=>{const events={};return {events,addTo(){layers.push(this);return this;},once(name,fn){events[name]=fn;},on(name,fn){events[name]=fn;}};};
 const L={map:()=>map,control:{zoom:layer},DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}},tileLayer(url){const l=layer();l.url=url;rasters.push(l);return l;},maplibreGL(options){const l=layer(),events={};l.gl={events,container:{style:{}},canvas:{style:{}},getContainer(){return this.container;},getCanvas(){return this.canvas;},once(name,fn){events[name]=fn;},on(name,fn){events[name]=fn;},getStyle(){return options.style;},setLayoutProperty(){}};l.getMaplibreMap=()=>l.gl;vectors.push(l);return l;}};
 const context={L,window:{maplibregl:{}},document:{body:{dataset:{}},getElementById:el,querySelector:el,querySelectorAll:()=>[]},location:{hostname:'test'},fetch:()=>new Promise(resolve=>requests.push(resolve)),setTimeout(fn,delay){timers.push({fn,delay,cleared:false});return timers.length;},clearTimeout(id){if(timers[id-1])timers[id-1].cleared=true;}};
 vm.runInNewContext(readFileSync('photo-scout-site/app.js','utf8').split('let selected=')[0],context);
 return {context,layers,timers,requests,vectors,rasters,el,async resolve(index=0){requests[index]({ok:true,json:async()=>({version:8,sources:{},layers:[]})});await new Promise(resolve=>setImmediate(resolve));}};
}

test('a quiet base loads immediately and survives a hanging vector style',async()=>{
 const f=loadingFixture(),preview=f.rasters[1];assert.ok(f.layers.includes(preview));assert.match(preview.url,/light_nolabels/);
 preview.events.tileload();const timeout=f.timers.find(t=>t.delay===12000);timeout.fn();
 assert.ok(f.layers.includes(preview));assert.match(f.el('map-notice').textContent,/backup map/);
 await f.resolve();assert.equal(f.vectors.length,0);
});
test('a stalled preview switches to an independent raster source',()=>{
 const f=loadingFixture();f.timers.find(t=>t.delay===4000).fn();
 assert.ok(!f.layers.includes(f.rasters[1]));assert.ok(f.layers.includes(f.rasters[0]));
});
test('vector canvas cannot cover the preview before the current viewport is painted',async()=>{
 const f=loadingFixture();await f.resolve();const vector=f.vectors[0],preview=f.rasters[1];
 assert.equal(vector.gl.canvas.style.opacity,'0');vector.gl.events.load();
 assert.ok(f.layers.includes(preview));assert.equal(vector.gl.canvas.style.opacity,'0');
 vector.gl.events.idle();assert.equal(vector.gl.canvas.style.opacity,'1');assert.ok(!f.layers.includes(preview));assert.equal(f.el('map-notice').textContent,'');
});
test('a vector renderer that never paints is removed, retaining its working preview',async()=>{
 const f=loadingFixture();await f.resolve();const preview=f.rasters[1];preview.events.tileload();
 f.timers.find(t=>t.delay===12000).fn();assert.ok(!f.layers.includes(f.vectors[0]));assert.ok(f.layers.includes(preview));
 f.vectors[0].gl.events.load();f.vectors[0].gl.events.idle();assert.equal(f.vectors[0].gl.canvas.style.opacity,'0');
});
test('graphics context loss restores a quiet base and a retry replaces it only after idle',async()=>{
 const f=loadingFixture();await f.resolve();f.vectors[0].gl.events.load();f.vectors[0].gl.events.idle();
 f.vectors[0].gl.events.webglcontextlost();assert.ok(f.layers.includes(f.rasters[1]));assert.ok(!f.layers.includes(f.vectors[0]));
 const retry=f.context.switchMapStyle('minimal');await f.resolve(1);await retry;f.vectors[1].gl.events.load();f.vectors[1].gl.events.idle();
 assert.ok(f.layers.includes(f.vectors[1]));assert.equal(f.el('map-notice').textContent,'');
});
test('stale preview and vector timers cannot disrupt a new choice',()=>{
 const f=loadingFixture(),oldTimers=[...f.timers];f.context.switchMapStyle('aerial');
 for(const t of oldTimers)t.fn();const aerial=f.rasters.at(-1),preview=f.rasters.at(-2);
 assert.ok(f.layers.includes(aerial));assert.ok(f.layers.includes(preview));
 f.timers.at(-1).fn();assert.ok(!f.layers.includes(aerial));assert.ok(f.layers.includes(preview));
});
test('a raster load error keeps the preview instead of leaving the map empty',()=>{
 const f=loadingFixture();f.context.switchMapStyle('topographic');const raster=f.rasters.at(-1),preview=f.rasters.at(-2);
 preview.events.tileload();raster.events.tileerror();assert.ok(f.layers.includes(preview));assert.ok(!f.layers.includes(raster));
 raster.events.tileload();raster.events.load();assert.ok(f.layers.includes(preview),'late success cannot remove the backup');
});

test('raster completion with no successful images never clears the backup',()=>{
 const f=loadingFixture();f.context.switchMapStyle('aerial');const raster=f.rasters.at(-1),preview=f.rasters.at(-2);
 raster.events.load();assert.ok(f.layers.includes(preview));assert.match(f.el('map-notice').textContent,/Loading/);
 raster.events.tileload();raster.events.load();assert.ok(!f.layers.includes(preview));
});
