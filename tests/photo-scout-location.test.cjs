const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
function fixture(){
 const timers=[],window={},states=[],positions=[];
 vm.runInNewContext(readFileSync('photo-scout-site/location.js','utf8'),{window,setTimeout:fn=>{const t={fn};timers.push(t);return t;},clearTimeout:t=>{t.cleared=true;},Number});
 return {timers,states,positions,start(geolocation){return window.PhotoScoutLocation.request({geolocation,onState:s=>states.push(s),onPosition:p=>positions.push(p)});}};
}
test('successful location updates once and ends the busy state',()=>{
 const f=fixture();f.start({getCurrentPosition(success,error,options){assert.equal(options.enableHighAccuracy,false);success({coords:{latitude:40.8,longitude:-96.7,accuracy:20}});error({code:1});}});
 assert.deepEqual(f.states.map(s=>s.status),['pending','success']);assert.equal(f.positions.length,1);assert.equal(f.timers[0].cleared,true);
});
test('permission denied and provider timeout produce actionable errors',()=>{
 for(const code of [1,3]){const f=fixture();f.start({getCurrentPosition(success,error){error({code});}});assert.equal(f.states.at(-1).status,'error');assert.equal(f.positions.length,0);assert.match(f.states.at(-1).message,code===1?/Safari website settings/:/timed out/);}
});
test('an unanswered permission prompt ends visibly and ignores a late position',()=>{
 const f=fixture();let late;f.start({getCurrentPosition(success){late=success;}});f.timers[0].fn();late({coords:{latitude:0,longitude:0}});assert.equal(f.states.at(-1).status,'error');assert.equal(f.positions.length,0);
});
test('unsupported coordinates and browser exceptions do not leave button busy',()=>{
 for(const geolocation of [{getCurrentPosition(success){success({coords:{latitude:NaN,longitude:0}});}},{getCurrentPosition(){throw Error('Unavailable');}}]){const f=fixture();f.start(geolocation);assert.equal(f.states.at(-1).status,'error');assert.equal(f.positions.length,0);}
});
test('unavailable coarse fix retries precisely and accepts the new coordinates',()=>{
 const f=fixture();let calls=0;f.start({getCurrentPosition(success,error,options){calls++;assert.equal(options.maximumAge,0);if(calls===1)error({code:2});else{assert.equal(options.enableHighAccuracy,true);success({coords:{latitude:40.8,longitude:-96.7,accuracy:8}});}}});assert.equal(calls,2);assert.equal(f.positions.length,1);assert.equal(f.states.at(-1).status,'success');
});
test('denied permission never retries or substitutes a default location',()=>{
 const f=fixture();let calls=0;f.start({getCurrentPosition(success,error){calls++;error({code:1});}});assert.equal(calls,1);assert.equal(f.positions.length,0);assert.match(f.states.at(-1).message,/not your detected location/);
});

// Run the actual page bindings: the map's location icon must request a device fix,
// rather than recentering the default Chicago selection.
function pageFixture(){
 const elements=new Map(),requests=[],moves=[],circles=[];
 const defaults={lat:'41.8827',lon:'-87.6233',radius:'1000',limit:'3'};
 function element(id){if(!elements.has(id))elements.set(id,{value:defaults[id]||'',textContent:'',listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},querySelectorAll(){return [];},replaceChildren(){},reportValidity(){return true;},scrollIntoView(){}});return elements.get(id);}
 const layer=()=>({addTo(){return this;},on(){return this;},once(){return this;},setLatLng(p){this.position=p;return this;},setRadius(){return this;},clearLayers(){},getBounds(){return this.position;}});
 const map={setView(){return this;},on(){},hasLayer(){return true;},removeLayer(){},fitBounds(bounds){moves.push(bounds);}};
 const L={map:()=>map,control:{zoom:()=>layer(),layers:()=>layer()},tileLayer:()=>layer(),marker:()=>layer(),layerGroup:()=>layer(),circle:()=>{const c=layer();circles.push(c);return c;},divIcon:()=>({}),DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}}};
 const window={isSecureContext:true,addEventListener(){}},navigator={geolocation:{getCurrentPosition(success,error){requests.push({success,error});}}};
 const context={window,navigator,L,document:{body:{dataset:{}},getElementById:element,querySelector:element,querySelectorAll(){return [];},addEventListener(){}},location:{hostname:'test.invalid',search:''},localStorage:{removeItem(){}},sessionStorage:{},fetch:()=>new Promise(()=>{}),setTimeout:()=>1,clearTimeout(){},Number,URL,matchMedia:()=>({matches:true})};
 vm.runInNewContext(readFileSync('photo-scout-site/location.js','utf8'),context);
 vm.runInNewContext(readFileSync('photo-scout-site/app.js','utf8'),context);
 return {bearing:context.photoBearing,element,requests,moves,circles,click:id=>element(id).listeners.click()};
}
test('top-right location button waits for device coordinates and moves away from Chicago',()=>{
 const f=pageFixture();f.click('center-pin');assert.equal(f.requests.length,1);assert.equal(f.moves.length,0);assert.equal(f.element('center-pin').disabled,true);
 f.requests[0].success({coords:{latitude:40.885231,longitude:-96.708139,accuracy:8}});
 assert.equal(f.element('lat').value,'40.885231');assert.equal(f.element('lon').value,'-96.708139');assert.equal(f.moves.length,1);assert.equal(f.element('center-pin').disabled,false);
});
test('both location buttons share pending state and denial never recenters Chicago',()=>{
 const f=pageFixture();f.click('center-pin');f.click('locate');assert.equal(f.requests.length,1);assert.equal(f.element('locate').disabled,true);
 f.requests[0].error({code:1});assert.equal(f.moves.length,0);assert.equal(f.element('locate').disabled,false);assert.match(f.element('map-notice').textContent,/not your detected location/);
 f.click('locate');assert.equal(f.requests.length,2);
});

test('photo bearing preserves north, normalizes rotations and never invents missing directions',()=>{
 const f=pageFixture();assert.equal(f.bearing({viewHeadingDegrees:0}).label,'Facing N · 0°');assert.equal(f.bearing({viewHeadingDegrees:90}).label,'Facing E · 90°');assert.equal(f.bearing({viewHeadingDegrees:-90}).heading,270);assert.equal(f.bearing({viewHeadingDegrees:360}).heading,0);for(const value of [null,undefined,NaN,'90'])assert.equal(f.bearing({viewHeadingDegrees:value}).heading,null);
});
