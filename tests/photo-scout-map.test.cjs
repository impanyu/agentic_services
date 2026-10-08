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
 const context={L,window:{maplibregl:{}},document:{body:{dataset:{}},getElementById:()=>element,querySelector:()=>element,querySelectorAll:()=>[]},location:{hostname:'test'},fetch:()=>new Promise(resolve=>requests.push(resolve))};
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
