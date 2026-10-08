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
 const elements=new Map(),requests=[],moves=[],circles=[];let created=0;
 const defaults={lat:'41.8827',lon:'-87.6233',radius:'1000',limit:'3'};
 function element(id){if(!elements.has(id))elements.set(id,{value:defaults[id]||'',textContent:'',dataset:{},showModal(){this.open=true;},close(){this.open=false;},listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},querySelectorAll(){return [];},replaceChildren(){},append(){},classList:{add(){},remove(){}},reportValidity(){return true;},scrollIntoView(){}});return elements.get(id);}
 const layer=()=>({addTo(){return this;},on(){return this;},once(){return this;},setLatLng(p){this.position=p;return this;},setRadius(){return this;},clearLayers(){this.clears=(this.clears||0)+1;},bindTooltip(content){this.tooltip=content;return this;},setTooltipContent(content){this.tooltip=content;return this;},getLayers(){return [];},getBounds(){return this.position;},getContainer(){return {};}});
 const map={setView(){return this;},on(){},hasLayer(){return true;},removeLayer(){},fitBounds(bounds){moves.push(bounds);}};
 const L={map:()=>map,control:{zoom:()=>layer(),layers:()=>layer()},tileLayer:()=>layer(),marker:p=>{const m=layer();m.position=p;return m;},layerGroup:()=>layer(),circle:()=>{const c=layer();circles.push(c);return c;},divIcon:()=>({}),DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}}};
 const window={isSecureContext:true,addEventListener(){}},navigator={geolocation:{getCurrentPosition(success,error){requests.push({success,error});}}};
 const context={window,navigator,L,document:{createElement:()=>element('created'+(++created)),body:{dataset:{},append(){}},getElementById:element,querySelector:element,querySelectorAll(){return [];},addEventListener(){}},location:{hostname:'test.invalid',search:''},localStorage:{removeItem(){}},sessionStorage:{},fetch:()=>new Promise(()=>{}),setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},Number,URL,matchMedia:()=>({matches:true})};
 vm.runInNewContext(readFileSync('photo-scout-site/location.js','utf8'),context);
 vm.runInNewContext(readFileSync('photo-scout-site/app.js','utf8'),context);
 return {activity(state,spot){context.activitySpot=spot;vm.runInNewContext('studioSpot=activitySpot;setStudioActivity('+JSON.stringify(state)+')',context);return vm.runInNewContext('({hidden:studioTask.hidden,state:studioTask.dataset.state,label:studioTaskLabel.textContent,position:studioActivityMarker?.position,clears:selfieActivityLayer.clears||0})',context);},closeStudio(){vm.runInNewContext('studioClose.listeners.click()',context);},openTask(){return vm.runInNewContext('studioTask.listeners.click();studio.open',context);},interceptSubmission:fn=>{context.submitSearch=fn;},bearing:context.photoBearing,views:context.allPoiViews,element,requests,moves,circles,click:id=>element(id).listeners.click()};
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

test('location buttons select coordinates without submitting a search or altering the query',()=>{
 for(const id of ['center-pin','locate']){
  const f=pageFixture();f.element('prompt-query').value='Urban shots within 20 km';let submissions=0;f.interceptSubmission(()=>{submissions++;});f.click(id);f.requests[0].success({coords:{latitude:40.8,longitude:-96.7,accuracy:8}});
  assert.equal(submissions,0);assert.equal(f.element('prompt-query').value,'Urban shots within 20 km');assert.equal(f.element('lat').value,'40.800000');assert.equal(f.element('lon').value,'-96.700000');assert.equal(f.moves.length,1);assert.match(f.element('location-status').textContent,/press Enter to search/);
 }
});

test('map and shortlist retain scored images and their best heading, excluding unverified POIs',()=>{
 const f=pageFixture(),best={id:'image1',poi:{id:'poi1'},score:92,provider:'google-street-view',sourceUrl:'https://www.google.com/maps/@?pano=example',viewHeadingDegrees:225};
 const other={...best,id:'image2',poi:{id:'poi2'},score:61,viewHeadingDegrees:90};
 const missing={poi:{id:'poi3'},score:null,assessmentStatus:'no_verified_view'};
 const result=f.views({spots:[best],poiResults:[best,other,missing]});assert.equal(result.length,2);assert.equal(result[1].viewHeadingDegrees,90);
 assert.equal(f.views({spots:[],nearbyPois:[{id:'poi3'}]}).length,0);
});


test('selfie map activity uses the chosen place and survives dialog closure until completion',()=>{
 const f=pageFixture(),spot={name:'Selected garden',poi:{lat:40.83,lon:-96.67}};
 for(const state of ['uploading','queued','checking','running']){const view=f.activity(state,spot);assert.equal(view.hidden,false);assert.equal(view.state,state);assert.equal(view.position[0],40.83);assert.equal(view.position[1],-96.67);assert.equal(view.clears,0);}
 f.closeStudio();assert.equal(f.openTask(),true);
 const ready=f.activity('complete',spot);assert.equal(ready.position,undefined);assert.equal(ready.clears,1);assert.match(ready.label,/ready/);assert.equal(ready.hidden,false);
 const failed=f.activity('failed',spot);assert.equal(failed.position,undefined);assert.match(failed.label,/attention/);
});
