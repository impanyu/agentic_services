const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const {File,Blob}=require('node:buffer');
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
function pageFixture(statusResponses){
 const elements=new Map(),requests=[],moves=[],circles=[],timers=[];let created=0;
 const defaults={lat:'41.8827',lon:'-87.6233',radius:'1000',limit:'3'};
 function element(id){if(!elements.has(id))elements.set(id,{value:defaults[id]||'',options:defaults[id]?[{value:defaults[id]}]:[],selectedOptions:[{textContent:'1 km'}],textContent:'',dataset:{},showModal(){this.open=true;},close(){this.open=false;},removeAttribute(name){delete this[name];},listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},querySelectorAll(){return [];},replaceChildren(){},append(){},classList:{add(){},remove(){}},reportValidity(){return true;},scrollIntoView(){}});return elements.get(id);}
 const layer=()=>({addTo(){return this;},on(){return this;},once(){return this;},setLatLng(p){this.position=p;return this;},setRadius(radius){this.radius=radius;return this;},clearLayers(){this.clears=(this.clears||0)+1;},bindTooltip(content){this.tooltip=content;return this;},setTooltipContent(content){this.tooltip=content;return this;},getLayers(){return [];},getBounds(){return this.position;},getContainer(){return {};}});
 const map={setView(){return this;},on(){},hasLayer(){return true;},removeLayer(){},fitBounds(bounds){moves.push(bounds);}};
 const L={map:()=>map,control:{zoom:()=>layer(),layers:()=>layer()},tileLayer:()=>layer(),marker:p=>{const m=layer();m.position=p;return m;},layerGroup:()=>layer(),circle:()=>{const c=layer();circles.push(c);return c;},divIcon:()=>({}),DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}}};
 const window={isSecureContext:true,addEventListener(){}},navigator={geolocation:{getCurrentPosition(success,error){requests.push({success,error});}}};
 class FileReader{readAsDataURL(blob){blob.arrayBuffer().then(bytes=>{this.result='data:'+blob.type+';base64,'+Buffer.from(bytes).toString('base64');this.onload();});}}
 const context={window,navigator,L,File,Blob,FileReader,document:{createElement:()=>element('created'+(++created)),body:{dataset:{},append(){}},getElementById:element,querySelector:element,querySelectorAll(){return [];},addEventListener(){}},location:{hostname:'test.invalid',search:''},localStorage:{removeItem(){}},sessionStorage:{},fetch:url=>{if(statusResponses&&url.endsWith('/status')){const response=statusResponses.shift();return response instanceof Error?Promise.reject(response):Promise.resolve({ok:true,json:async()=>response});}return new Promise(()=>{});},setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length;},clearTimeout(){},setInterval:()=>1,clearInterval(){},Number,URL,crypto:require('node:crypto').webcrypto,matchMedia:()=>({matches:true})};
 vm.runInNewContext(readFileSync('photo-scout-site/location.js','utf8'),context);
 vm.runInNewContext(readFileSync('photo-scout-site/app.js','utf8'),context);
 return {timers,submitAt(radius){element('radius').value=String(radius);vm.runInNewContext('serviceAvailable=true;humanFreePreview=true;void submitSearch({preventDefault(){}})',context);return {moves:[...moves],radius:circles[0].radius};},applyContext(area){context.areaFixture=area;vm.runInNewContext('applyTaskContext(areaFixture)',context);return {moves:[...moves],radius:circles[0].radius};},viewRecorded(area,result){context.matchMedia=()=>({matches:false});context.recordedArea=area;context.recordedResult=result;vm.runInNewContext('showHistorySearch(recordedResult,recordedArea,null)',context);return {hidden:element('results').hidden,shortlistEnabled:!element('toggle-results').disabled,moves:[...moves]};},focusRecorded(area,result,history){context.recordedArea=area;context.recordedResult=result;context.recordedHistory=history;vm.runInNewContext('focusHistorySearch(recordedArea,recordedResult,recordedHistory)',context);return {lat:element('lat').value,lon:element('lon').value,radius:element('radius').value,moves:[...moves]};},historyWith(records,tasks){context.historyFixture=records;context.taskFixture=tasks;return vm.runInNewContext('searchHistory=historyFixture;taskRecords=taskFixture;renderHistory();historyEntries()',context);},async settle(){await new Promise(resolve=>setImmediate(resolve));},async retryStatus(){await timers.find(t=>t.delay===5000).fn();},async restoreWith(items,reports){context.fetch=async(url,options)=>{context.recoveryRequests??=[];context.recoveryRequests.push({url,method:options?.method||'GET',headers:options?.headers});return {ok:true,json:async()=>url.endsWith('/tasks')?{items}:reports[url.split('/').at(-1)]};};await vm.runInNewContext('restoreTasks()',context);await new Promise(resolve=>setImmediate(resolve));return vm.runInNewContext('({ready:tasksReady,active:activeSearch,busy:searchBusy,history:searchHistory,studio:studioJob})',context);},recoveryRequests(){return context.recoveryRequests;},async output(blob){context.outputBlob=blob;await vm.runInNewContext('showStudioOutput(outputBlob)',context);return vm.runInNewContext('({saveHidden:studioSave.hidden,downloadHidden:studioDownload.hidden,preview:studioResult.src,file:studioOutputFile})',context);},shareSupport(canShare,share){navigator.canShare=canShare;navigator.share=share;},save(){return vm.runInNewContext('studioSave.listeners.click()',context);},saveState(){return vm.runInNewContext('({disabled:studioSave.disabled,hint:studioSaveHint.textContent,visible:!studioResult.hidden})',context);},clearOutput(){vm.runInNewContext('clearStudioOutput()',context);},activity(state,spot){context.activitySpot=spot;vm.runInNewContext('studioSpot=activitySpot;setStudioActivity('+JSON.stringify(state)+')',context);return vm.runInNewContext('({hidden:studioTask.hidden,state:studioTask.dataset.state,label:studioTaskLabel.textContent,position:studioActivityMarker?.position,clears:selfieActivityLayer.clears||0})',context);},closeStudio(){vm.runInNewContext('studioClose.listeners.click()',context);},openTask(){return vm.runInNewContext('studioTask.listeners.click();studio.open',context);},interceptSubmission:fn=>{context.submitSearch=fn;},topCount:context.photoTopCount,bearing:context.photoBearing,views:context.allPoiViews,element,requests,moves,circles,click:id=>element(id).listeners.click()};
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


test('save to Photos shares the actual prepared PNG immediately from the click',async()=>{
 const f=pageFixture(),blob=new Blob(['generated-photo'],{type:'image/png'}),ready=await f.output(blob);let handed;
 assert.match(ready.preview,/^data:image\/png;base64,/);assert.equal(ready.saveHidden,false);assert.equal(ready.downloadHidden,false);
 f.shareSupport(data=>data.files[0]===ready.file,data=>{handed=data;return Promise.resolve();});
 const done=f.save();assert.equal(handed.files[0],ready.file);await done;
 assert.equal(handed.files[0].name,'photo-scout-ai-photo.png');assert.equal(handed.files[0].type,'image/png');assert.equal(await handed.files[0].text(),'generated-photo');assert.equal(f.saveState().disabled,false);
});
test('save fallback and canceled or failed shares preserve the photo',async()=>{
 for(const mode of ['unsupported','canceled','failed']){
  const f=pageFixture();await f.output(new Blob(['photo'],{type:'image/png'}));let calls=0;
  f.shareSupport(()=>mode!=='unsupported',()=>{calls++;return Promise.reject(Object.assign(new Error('test'),{name:mode==='canceled'?'AbortError':'NotAllowedError'}));});
  await f.save();assert.equal(calls,mode==='unsupported'?0:1);assert.equal(f.saveState().visible,true);assert.equal(f.saveState().disabled,mode==='unsupported'?undefined:false);
  if(mode!=='canceled')assert.match(f.saveState().hint,/Press and hold/);
  f.clearOutput();await f.save();assert.equal(calls,mode==='unsupported'?0:1);
 }
});


test('reload recovery imports completed search results without any POST or model rerun',async()=>{
 const f=pageFixture(),context={lat:41.8827,lon:-87.6233,radius:1000,query:'Saved search'},task={id:'saved-job',kind:'search',state:'complete',created:100,context};
 const state=await f.restoreWith([task],{'saved-job':{state:'complete',context,result:{spots:[],summary:'Backend result'}}});
 assert.equal(state.ready,true);assert.equal(state.history[0].id,'saved-job');assert.equal(state.history[0].label,'Saved search');
 assert.ok(f.recoveryRequests().every(r=>r.method==='GET'));assert.equal(state.busy,false);
 const again=await f.restoreWith([task],{'saved-job':{state:'complete',context,result:{spots:[]}}});assert.equal(again.history.length,1);
});
test('reload recovery resumes a queued search using its cookie instead of a stored token',async()=>{
 const f=pageFixture(),context={lat:41.8827,lon:-87.6233,radius:1000,query:'Queued search'};
 const state=await f.restoreWith([{id:'queued-job',kind:'search',state:'queued',created:100,context}],{'queued-job':{state:'queued',context}});
 assert.equal(state.active.jobId,'queued-job');assert.equal(state.busy,true);
 const request=f.recoveryRequests().find(r=>r.url.endsWith('/queued-job'));assert.ok(request);assert.equal(request.headers?.['X-Report-Token'],undefined);assert.ok(f.recoveryRequests().every(r=>r.method==='GET'));
});


test('transient startup failure reconnects and enables search without a reload',async()=>{
 const f=pageFixture([Error('temporary backend outage'),{enabled:true,humanFreePreview:true,photoStyles:[]}]);
 await f.settle();assert.equal(f.element('resolve-query').disabled,true);assert.match(f.element('message').textContent,/Reconnecting/);
 await f.retryStatus();await f.restoreWith([],{});assert.equal(f.element('resolve-query').disabled,false);assert.equal(f.element('message').textContent,'');
});


test('history merges backend tasks and results by ID, preserving each distinct search and its checkbox',()=>{
 const f=pageFixture(),result={spots:[]};
 const tasks=[{id:'first',kind:'search',created:10,state:'complete',context:{query:'Chicago'}},{id:'second',kind:'search',created:20,state:'running',context:{query:'Chicago'}}];
 let entries=f.historyWith([{id:'first',created:11000,label:'Chicago',radius:500,checked:false,result}],tasks);
 assert.equal(entries.length,2);assert.equal(f.element('history-count').textContent,'2');assert.equal(entries.find(e=>e.id==='first').history.checked,false);assert.equal(entries[0].id,'second');assert.equal(entries[0].state,'running');
 tasks[1].state='complete';entries=f.historyWith([{id:'first',created:11000,label:'Chicago',radius:500,checked:false,result},{id:'second',created:21000,label:'Chicago',radius:500,checked:true,result}],tasks);
 assert.equal(entries.length,2);assert.ok(entries.every(e=>e.history));assert.equal(entries[0].state,'complete');
});
test('selfies and failed searches remain accessible in the single history list',()=>{
 const f=pageFixture(),entries=f.historyWith([],[{id:'photo',kind:'portrait',created:30,state:'complete',context:{name:'Park'}},{id:'failed',kind:'search',created:20,state:'failed',context:{lat:40,lon:-96}}]);
 assert.equal(entries.length,2);assert.equal(entries[0].kind,'portrait');assert.equal(entries[0].label,'Park');assert.match(entries[1].label,/Around 40/);
});


test('historical View always refits its recorded area, even if its selected coordinates are unchanged',()=>{
 const f=pageFixture(),area={lat:40.8589,lon:-96.6792,radius:20000,query:'Lincoln'};
 const first=f.focusRecorded(area,{spots:[]},null);assert.equal(first.lat,'40.8589');assert.equal(first.lon,'-96.6792');assert.equal(first.radius,'20000');assert.deepEqual(Array.from(first.moves.at(-1)),[40.8589,-96.6792]);
 const again=f.focusRecorded(area,{spots:[]},null);assert.equal(again.moves.length,first.moves.length+1);assert.deepEqual(Array.from(again.moves.at(-1)),[40.8589,-96.6792]);
 const legacy=f.focusRecorded(null,{spots:[]},{label:'Around 41.8827, -87.6233',radius:500});assert.equal(legacy.radius,'500');assert.deepEqual(Array.from(legacy.moves.at(-1)),[41.8827,-87.6233]);
});

test('history View on desktop focuses the map and leaves Shortlist closed but available',()=>{
 const f=pageFixture(),view=f.viewRecorded({lat:41.8827,lon:-87.6233,radius:500},{spots:[],sources:{},summary:'Saved report'});
 assert.equal(view.hidden,true);assert.equal(view.shortlistEnabled,true);assert.deepEqual(Array.from(view.moves.at(-1)),[41.8827,-87.6233]);
});

test('all scored POIs remain ranked even when unsuitable or low scoring, and Top 5 sets five large markers',()=>{
 const f=pageFixture(),poiResults=Array.from({length:7},(_,i)=>({id:String(i),poi:{id:String(i)},score:i, recommend:false,imageUrl:'https://example.test/'+i}));
 const result={topLimit:5,poiResults,spots:[]};assert.equal(f.views(result).length,7);assert.deepEqual(Array.from(f.views(result),s=>s.score),[6,5,4,3,2,1,0]);assert.equal(f.topCount(result),5);assert.equal(f.topCount({...result,topLimit:3}),3);
});

test('submitting a search immediately fits its current radius even when coordinates have not changed',()=>{
 const f=pageFixture(),result=f.submitAt(20000);assert.equal(result.moves.length,1);assert.deepEqual(Array.from(result.moves[0]),[41.8827,-87.6233]);assert.equal(result.radius,20000);
});
test('text-resolved radius changes refit the map without repeatedly moving it on unchanged polls',()=>{
 const f=pageFixture(),area={lat:41.8827,lon:-87.6233,radius:20000};const first=f.applyContext(area);assert.equal(first.moves.length,1);assert.equal(first.radius,20000);assert.equal(f.applyContext(area).moves.length,1);const second=f.applyContext({...area,lat:40.8,lon:-96.7,radius:500});assert.equal(second.moves.length,2);assert.equal(second.radius,500);
});
