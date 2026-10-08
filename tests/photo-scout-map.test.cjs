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
 const layer=()=>({addTo(){added.push(this);return this;}});
 const L={map:()=>map,control:{zoom:layer},tileLayer:layer,DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}},maplibreGL(options){styles.push(options.style);return {...layer(),getMaplibreMap(){return {once(){},on(){}};}};}};
 const context={L,window:{maplibregl:{}},document:{body:{dataset:{}},getElementById:()=>element,querySelector:()=>element,querySelectorAll:()=>[]},location:{hostname:'test'},setTimeout:()=>1,clearTimeout(){},fetch:()=>new Promise(resolve=>requests.push(resolve))};
 const source=readFileSync('photo-scout-site/app.js','utf8').split('let selected=')[0];
 vm.runInNewContext(source,context);
 assert.equal(added.length,1,'only the zoom control is added while style is loading');
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
 const L={map:()=>map,control:{zoom:layer},DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}},tileLayer(url){const l=layer();l.url=url;rasters.push(l);return l;},maplibreGL(options){const l=layer(),events={};l.gl={events,once(name,fn){events[name]=fn;},on(name,fn){events[name]=fn;},getStyle(){return options.style;},setLayoutProperty(){}};l.getMaplibreMap=()=>l.gl;vectors.push(l);return l;}};
 const context={L,window:{maplibregl:{}},document:{body:{dataset:{}},getElementById:el,querySelector:el,querySelectorAll:()=>[]},location:{hostname:'test'},fetch:()=>new Promise(resolve=>requests.push(resolve)),setTimeout(fn,delay){timers.push({fn,delay,cleared:false});return timers.length;},clearTimeout(id){if(timers[id-1])timers[id-1].cleared=true;}};
 vm.runInNewContext(readFileSync('photo-scout-site/app.js','utf8').split('let selected=')[0],context);
 return {context,layers,timers,requests,vectors,rasters,el,async resolve(index=0){requests[index]({ok:true,json:async()=>({version:8,sources:{},layers:[]})});await new Promise(resolve=>setImmediate(resolve));}};
}

test('a hanging style fetch falls back and its late response cannot replace the backup',async()=>{
 const f=loadingFixture();assert.match(f.el('map-notice').textContent,/Loading map/);assert.equal(f.timers[0].delay,12000);
 f.timers[0].fn();assert.ok(f.layers.includes(f.rasters[0]));assert.match(f.el('map-notice').textContent,/took too long/);
 await f.resolve();assert.equal(f.vectors.length,0);
});
test('a vector renderer that never completes loading is removed on timeout',async()=>{
 const f=loadingFixture();await f.resolve();assert.ok(f.layers.includes(f.vectors[0]));
 f.timers[0].fn();assert.ok(!f.layers.includes(f.vectors[0]));assert.ok(f.layers.includes(f.rasters[0]));
 f.vectors[0].gl.events.load();assert.match(f.el('map-notice').textContent,/standard street map/);
});
test('graphics context loss after a successful render falls back and allows a retry',async()=>{
 const f=loadingFixture();await f.resolve();f.vectors[0].gl.events.load();assert.equal(f.el('map-notice').textContent,'');assert.equal(f.timers[0].cleared,true);
 f.vectors[0].gl.events.webglcontextlost();assert.ok(f.layers.includes(f.rasters[0]));assert.ok(!f.layers.includes(f.vectors[0]));
 const retry=f.context.switchMapStyle('minimal');await f.resolve(1);await retry;f.vectors[1].gl.events.load();assert.ok(f.layers.includes(f.vectors[1]));assert.ok(!f.layers.includes(f.rasters[0]));assert.equal(f.el('map-notice').textContent,'');
});
test('a hanging U.S. raster layer also falls back, and an old timer cannot disrupt a new choice',async()=>{
 const f=loadingFixture();f.context.switchMapStyle('aerial');f.timers[0].fn();assert.ok(!f.layers.includes(f.rasters[0]));
 const aerial=f.rasters[1];f.timers[1].fn();assert.ok(!f.layers.includes(aerial));assert.ok(f.layers.includes(f.rasters[0]));
});
