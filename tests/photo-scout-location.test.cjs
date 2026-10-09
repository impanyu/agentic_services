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
 const layer=()=>({addTo(){return this;},on(){return this;},once(){return this;},setLatLng(p){this.position=p;return this;},setRadius(radius){this.radius=radius;return this;},clearLayers(){this.clears=(this.clears||0)+1;},bindPopup(content){this.popup=content;return this;},openPopup(){this.opened=true;return this;},bindTooltip(content){this.tooltip=content;return this;},setTooltipContent(content){this.tooltip=content;return this;},getLayers(){return [];},getBounds(){return this.position;},getContainer(){return {};}});
 const map={setView(position,zoom){this.focus={position,zoom};return this;},on(){},hasLayer(){return true;},removeLayer(){},fitBounds(bounds){moves.push(bounds);}};
 const L={map:()=>map,control:{zoom:()=>layer(),layers:()=>layer()},tileLayer:()=>layer(),marker:(p,options)=>{const m=layer();m.options=options;m.position=p;return m;},layerGroup:()=>layer(),circle:()=>{const c=layer();circles.push(c);return c;},divIcon:options=>options,DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}}};
 const window={isSecureContext:true,addEventListener(){}},navigator={geolocation:{getCurrentPosition(success,error){requests.push({success,error});}}};
 class FileReader{readAsDataURL(blob){blob.arrayBuffer().then(bytes=>{this.result='data:'+blob.type+';base64,'+Buffer.from(bytes).toString('base64');this.onload();});}}
 const context={window,navigator,L,File,Blob,FileReader,document:{createElement:()=>element('created'+(++created)),body:{dataset:{},append(){}},getElementById:element,querySelector:element,querySelectorAll(){return [];},addEventListener(){}},location:{hostname:'test.invalid',search:''},localStorage:{removeItem(){}},sessionStorage:{getItem(){return null;},setItem(){},removeItem(){}},fetch:url=>{if(statusResponses&&url.endsWith('/status')){const response=statusResponses.shift();return response instanceof Error?Promise.reject(response):Promise.resolve({ok:true,json:async()=>response});}return new Promise(()=>{});},setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length;},clearTimeout(){},setInterval:()=>1,clearInterval(){},Number,URL,crypto:require('node:crypto').webcrypto,matchMedia:()=>({matches:true})};
 vm.runInNewContext(readFileSync('photo-scout-site/location.js','utf8'),context);
 vm.runInNewContext(readFileSync('photo-scout-site/app.js','utf8'),context);
 return {timers,setHistoryExclusions(hidden){context.hiddenFixture=hidden;vm.runInNewContext('hiddenPoisBySearch=new Map(Object.entries(hiddenFixture).map(([id,keys])=>[id,new Set(keys)]))',context);},rotateView(spot,keys){context.viewFixture=spot;context.keyFixture=keys;return vm.runInNewContext('focusedSearchId="chosen";selectedPoiView={spot:{...viewFixture},searchId:"chosen",index:3,marker:{setIcon(icon){this.icon=icon;}},popup:{querySelectorAll(){return [];},querySelector(selector){return selector===".poi-view-label"?{}:null;}}};keyFixture.forEach(rotateSelectedPoiView);({spot:selectedPoiView.spot,icon:selectedPoiView.marker.icon})',context);},rankedHistory(records,selected){context.rankedFixture=records;context.selectedFixture=selected;return vm.runInNewContext('searchHistory=rankedFixture;focusedSearchId=selectedFixture;drawHistoryMap();resultPins.map(m=>({searchId:m.searchId,icon:m.options.icon}))',context);},setHidden(hidden){context.document.hidden=hidden;},focusPhoto(task){context.focusPhotoFixture=task;vm.runInNewContext('searchHistory=[];taskRecords=[focusPhotoFixture];focusPhotoPlace(focusPhotoFixture)',context);return {...map.focus,...vm.runInNewContext('({opened:resultPins.at(-1).opened,spot:resultPins.at(-1).photoSpot,shortlistHidden:el("results").hidden,photosOpen:Boolean(el("photo-history").open)})',context)};},photosAt(spot,tasks){context.placeFixture=spot;context.taskFixture=tasks;return Array.from(vm.runInNewContext('taskRecords=taskFixture;selfiesAtPlace(placeFixture).map(t=>t.id)',context));},async independentPhotoPanels(){let release;context.fetch=async url=>{if(url.endsWith('/pending-photo'))return new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({state:'running',context:{name:'New scene'}})});});return {ok:true,json:async()=>({state:'complete',context:{name:'Old scene'}}),blob:async()=>new Blob(['old image'],{type:'image/png'})};};vm.runInNewContext('studioBusy=true;studioJob={id:"pending-photo"};studioSpot={name:"New scene"}',context);const pending=vm.runInNewContext('viewSavedPhoto({id:"pending-photo",state:"running",context:{name:"New scene"}})',context);await vm.runInNewContext('viewSavedPhoto({id:"old-photo",state:"complete",created:100,context:{name:"Old scene"}})',context);vm.runInNewContext('portraitProgress.listeners.close()',context);release();await pending;const before=vm.runInNewContext('({place:savedPhotoPlace.textContent,visible:!savedPhotoImage.hidden,viewerOpen:savedPhoto.open,progressOpen:portraitProgress.open,job:studioJob.id,busy:studioBusy})',context);vm.runInNewContext('savedPhoto.close();savedPhoto.showModal();savedPhoto.listeners.close()',context);return {...before,afterStaleClose:vm.runInNewContext('!savedPhotoImage.hidden&&Boolean(savedPhotoFile)',context)};},async generateMultiple(){const submitted=[],items=[];context.fetch=async(url,options)=>{if(options?.method==='POST'){const payload=JSON.parse(options.body),id='photo-'+(submitted.length+1);submitted.push(payload);items.push({id,kind:'portrait',state:'queued',created:submitted.length,context:{name:payload.place,poi:{lat:payload.lat,lon:payload.lon}}});return {ok:true,json:async()=>({id})};}return {ok:true,json:async()=>({items})};};vm.runInNewContext('tasksReady=true;studioPrepared="data:image/png;base64,fixture"',context);for(const lat of [41,40]){context.photoLat=lat;await vm.runInNewContext('studioSpot={name:"Park "+photoLat,provider:"google-street-view",sourceUrl:"https://www.google.com/maps/@?map_action=pano&pano=abc",poi:{lat:photoLat,lon:-96}};studioGenerate.listeners.click()',context);await new Promise(resolve=>setImmediate(resolve));}return {submitted,...vm.runInNewContext('({busy:studioBusy,records:taskRecords,disabled:studioGenerate.disabled})',context)};},async submitMultiple(){const submitted=[],items=[];context.fetch=async(url,options)=>{if(options?.method==='POST'){const payload=JSON.parse(options.body),jobId='job-'+(submitted.length+1);submitted.push(payload);items.push({id:jobId,kind:'search',state:'queued',created:submitted.length,context:payload});return {ok:true,json:async()=>({jobId})};}return {ok:true,json:async()=>({items})};};vm.runInNewContext('serviceAvailable=true;humanFreePreview=true;tasksReady=true',context);for(const lat of [41,40]){element('lat').value=String(lat);await vm.runInNewContext('submitSearch({preventDefault(){}})',context);}return {submitted,...vm.runInNewContext('({busy:searchBusy,active:activeSearch,records:taskRecords,disabled:el("resolve-query").disabled})',context)};},async savedWith(task,report){context.savedTaskFixture=task;context.fetch=async url=>({ok:true,json:async()=>report,blob:async()=>new Blob(['photo'],{type:'image/png'})});await vm.runInNewContext('viewSavedTask(savedTaskFixture)',context);return vm.runInNewContext('({viewerOpen:savedPhoto.open,studioOpen:studio.open,studioBusy,studioJob,file:savedPhotoFile?.name,imageVisible:!savedPhotoImage.hidden})',context);},submitAt(radius){element('radius').value=String(radius);vm.runInNewContext('serviceAvailable=true;humanFreePreview=true;void submitSearch({preventDefault(){}})',context);return {moves:[...moves],radius:circles[0].radius};},applyContext(area){context.areaFixture=area;vm.runInNewContext('applyTaskContext(areaFixture)',context);return {moves:[...moves],radius:circles[0].radius};},viewRecorded(area,result){context.matchMedia=()=>({matches:false});context.recordedArea=area;context.recordedResult=result;vm.runInNewContext('showHistorySearch(recordedResult,recordedArea,null)',context);return {hidden:element('results').hidden,shortlistEnabled:!element('toggle-results').disabled,moves:[...moves]};},focusRecorded(area,result,history){context.recordedArea=area;context.recordedResult=result;context.recordedHistory=history;vm.runInNewContext('focusHistorySearch(recordedArea,recordedResult,recordedHistory)',context);return {lat:element('lat').value,lon:element('lon').value,radius:element('radius').value,moves:[...moves]};},historyWith(records,tasks){context.historyFixture=records;context.taskFixture=tasks;return vm.runInNewContext('searchHistory=historyFixture;taskRecords=taskFixture;renderHistory();historyEntries()',context);},async settle(){await new Promise(resolve=>setImmediate(resolve));},async retryStatus(){await timers.find(t=>t.delay===5000).fn();},async restoreWith(items,reports){context.fetch=async(url,options)=>{context.recoveryRequests??=[];context.recoveryRequests.push({url,method:options?.method||'GET',headers:options?.headers});return {ok:true,json:async()=>url.split('?')[0].endsWith('/tasks')?{items}:reports[url.split('/').at(-1)]};};await vm.runInNewContext('restoreTasks()',context);await new Promise(resolve=>setImmediate(resolve));return vm.runInNewContext('({ready:tasksReady,active:activeSearch,busy:searchBusy,history:searchHistory,studio:studioJob,portraitProgress:studioTaskLabel.textContent})',context);},recoveryRequests(){return context.recoveryRequests;},async output(blob){context.outputBlob=blob;await vm.runInNewContext('showStudioOutput(outputBlob)',context);return vm.runInNewContext('({saveHidden:studioSave.hidden,downloadHidden:studioDownload.hidden,preview:studioResult.src,file:studioOutputFile})',context);},shareSupport(canShare,share){navigator.canShare=canShare;navigator.share=share;},save(){return vm.runInNewContext('studioSave.listeners.click()',context);},saveState(){return vm.runInNewContext('({disabled:studioSave.disabled,hint:studioSaveHint.textContent,visible:!studioResult.hidden})',context);},clearOutput(){vm.runInNewContext('clearStudioOutput()',context);},activity(state,spot){context.activitySpot=spot;vm.runInNewContext('studioSpot=activitySpot;setStudioActivity('+JSON.stringify(state)+')',context);return vm.runInNewContext('({hidden:studioTask.hidden,state:studioTask.dataset.state,label:studioTaskLabel.textContent,position:studioActivityMarker?.position,clears:selfieActivityLayer.clears||0})',context);},closeStudio(){vm.runInNewContext('studioClose.listeners.click()',context);},openTask(){return vm.runInNewContext('studioTask.listeners.click();studio.open',context);},interceptSubmission:fn=>{context.submitSearch=fn;},navigation:context.savedPhotoNavigation,topCount:context.photoTopCount,bearing:context.photoBearing,views:context.allPoiViews,element,requests,moves,circles,click:id=>element(id).listeners.click()};
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
test('reload recovery monitors a queued search without locking new submissions',async()=>{
 const f=pageFixture(),context={lat:41.8827,lon:-87.6233,radius:1000,query:'Queued search'};
 const state=await f.restoreWith([{id:'queued-job',kind:'search',state:'queued',created:100,context}],{'queued-job':{state:'queued',context}});
 assert.equal(state.active.jobId,'queued-job');assert.equal(state.busy,false);
 const request=f.recoveryRequests().find(r=>r.url.split('?')[0].endsWith('/tasks'));assert.ok(request);assert.equal(request.headers?.['X-Report-Token'],undefined);assert.ok(f.recoveryRequests().every(r=>r.method==='GET'));
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
test('photos and failed searches appear in separate menus with independent counts',()=>{
 const f=pageFixture(),entries=f.historyWith([],[{id:'photo',kind:'portrait',created:30,state:'complete',context:{name:'Park'}},{id:'failed',kind:'search',created:20,state:'failed',context:{lat:40,lon:-96}}]);
 assert.equal(f.element('history-count').textContent,'1');assert.equal(f.element('photo-count').textContent,'1');assert.equal(entries.length,2);assert.equal(entries[0].kind,'portrait');assert.equal(entries[0].label,'Park');assert.match(entries[1].label,/Around 40/);
});


test('historical View always refits its recorded area, even if its selected coordinates are unchanged',()=>{
 const f=pageFixture(),area={lat:40.8589,lon:-96.6792,radius:20000,query:'Lincoln'};
 const first=f.focusRecorded(area,{spots:[]},null);assert.equal(first.lat,'40.8589');assert.equal(first.lon,'-96.6792');assert.equal(first.radius,'20000');assert.deepEqual(Array.from(first.moves.at(-1)),[40.8589,-96.6792]);
 const again=f.focusRecorded(area,{spots:[]},null);assert.equal(again.moves.length,first.moves.length+1);assert.deepEqual(Array.from(again.moves.at(-1)),[40.8589,-96.6792]);
 const legacy=f.focusRecorded(null,{spots:[]},{label:'Around 41.8827, -87.6233',radius:500});assert.equal(legacy.radius,'500');assert.deepEqual(Array.from(legacy.moves.at(-1)),[41.8827,-87.6233]);
});

test('history View on desktop focuses the map and opens Shortlist',()=>{
 const f=pageFixture(),view=f.viewRecorded({lat:41.8827,lon:-87.6233,radius:500},{spots:[],sources:{},summary:'Saved report'});
 assert.equal(view.hidden,false);assert.equal(view.shortlistEnabled,true);assert.deepEqual(Array.from(view.moves.at(-1)),[41.8827,-87.6233]);
});

test('all scored POIs remain ranked even when unsuitable or low scoring, retaining the requested top-count metadata',()=>{
 const f=pageFixture(),poiResults=Array.from({length:7},(_,i)=>({id:String(i),poi:{id:String(i)},score:i, recommend:false,imageUrl:'https://example.test/'+i}));
 const result={topLimit:5,poiResults,spots:[]};assert.equal(f.views(result).length,7);assert.deepEqual(Array.from(f.views(result),s=>s.score),[6,5,4,3,2,1,0]);assert.equal(f.topCount(result),5);assert.equal(f.topCount({...result,topLimit:3}),3);
});

test('submitting a search immediately fits its current radius even when coordinates have not changed',()=>{
 const f=pageFixture(),result=f.submitAt(20000);assert.equal(result.moves.length,1);assert.deepEqual(Array.from(result.moves[0]),[41.8827,-87.6233]);assert.equal(result.radius,20000);
});
test('text-resolved radius changes refit the map without repeatedly moving it on unchanged polls',()=>{
 const f=pageFixture(),area={lat:41.8827,lon:-87.6233,radius:20000};const first=f.applyContext(area);assert.equal(first.moves.length,1);assert.equal(first.radius,20000);assert.equal(f.applyContext(area).moves.length,1);const second=f.applyContext({...area,lat:40.8,lon:-96.7,radius:500});assert.equal(second.moves.length,2);assert.equal(second.radius,500);
});

test('View photo opens its independent viewer without changing the generation task',async()=>{
 const f=pageFixture(),view=await f.savedWith({id:'saved',kind:'portrait',created:123,context:{name:'Park'}},{state:'complete',context:{name:'Park'}});
 assert.equal(view.viewerOpen,true);assert.notEqual(view.studioOpen,true);assert.equal(view.studioBusy,false);assert.equal(view.studioJob,null);assert.equal(view.file,'photo-scout-ai-photo.png');assert.equal(view.imageVisible,true);
});

test('saved photo navigation uses the recorded POI rather than the currently selected map location',()=>{
 const f=pageFixture(),routes=f.navigation({poi:{lat:40.813,lon:-96.693}});assert.equal(routes.length,2);
 assert.equal(new URL(routes[0][1]).searchParams.get('destination'),'40.813,-96.693');assert.equal(new URL(routes[1][1]).searchParams.get('daddr'),'40.813,-96.693');
 for(const poi of [undefined,{lat:null,lon:null},{lat:Infinity,lon:0},{lat:40,lon:181}])assert.equal(f.navigation({poi}).length,0);
});


test('multiple searches and selfies retain independent progress while completed searches enter history once',async()=>{
 const f=pageFixture(),a={lat:41.88,lon:-87.62,radius:500,query:'Search A'},b={lat:40.85,lon:-96.67,radius:1000,query:'Search B'};
 const items=[{id:'b',kind:'search',state:'running',created:200,context:b},{id:'a',kind:'search',state:'queued',created:100,context:a},{id:'p1',kind:'portrait',state:'running',created:300,context:{name:'Park A',poi:{lat:41.88,lon:-87.62}}},{id:'p2',kind:'portrait',state:'queued',created:400,context:{name:'Park B',poi:{lat:40.85,lon:-96.67}}}];
 const initial=await f.restoreWith(items,{});assert.equal(initial.busy,false);assert.equal(initial.active.jobId,'b');assert.match(initial.portraitProgress,/2 selfies/);
 items[1]={...items[1],state:'complete'};
 const next=await f.restoreWith(items,{a:{state:'complete',result:{spots:[],summary:'Result A'}}});
 assert.equal(next.active.jobId,'b');assert.equal(next.history.length,1);assert.equal(next.history[0].label,'Search A');assert.equal(next.history[0].result.searchContext.lat,a.lat);
 const again=await f.restoreWith(items,{});assert.equal(again.history.length,1);assert.equal(again.busy,false);
});

test('a second search can be submitted while the first remains queued',async()=>{
 const f=pageFixture(),state=await f.submitMultiple();
 assert.equal(state.submitted.length,2);assert.deepEqual(state.submitted.map(p=>p.lat),[41,40]);
 assert.equal(state.records.length,2);assert.equal(state.active.jobId,'job-2');assert.equal(state.busy,false);assert.equal(state.disabled,false);
});

test('a second selfie can be submitted while the first remains queued',async()=>{
 const state=await pageFixture().generateMultiple();assert.equal(state.submitted.length,2);
 assert.deepEqual(state.submitted.map(p=>p.lat),[41,40]);assert.equal(state.records.length,2);
 assert.equal(state.busy,false);assert.equal(state.disabled,false);
});

test('pending-photo progress cannot overwrite a completed photo viewer or cancel generation',async()=>{
 const state=await pageFixture().independentPhotoPanels();assert.equal(state.place,'Old scene');assert.equal(state.visible,true);assert.equal(state.viewerOpen,true);assert.equal(state.progressOpen,false);assert.equal(state.job,'pending-photo');assert.equal(state.busy,true);assert.equal(state.afterStaleClose,true);
});

test('place selfies group directions but exclude nearby places sharing a panorama and unavailable photos',()=>{
 const f=pageFixture(),spot={name:'Garden',poi:{id:'node/1',name:'Garden',lat:40.8,lon:-96.7}};
 const photo=(id,overrides={})=>({id,kind:'portrait',state:'complete',created:100,context:{name:'Garden',poi:{lat:40.8,lon:-96.7},sourceUrl:'same-panorama',viewHeadingDegrees:45},...overrides});
 const tasks=[photo('NE'),photo('SW',{created:200,context:{name:'Garden',poi:{lat:40.8,lon:-96.7},viewHeadingDegrees:225}}),photo('nearby',{context:{name:'Garden',poi:{lat:40.80001,lon:-96.7},sourceUrl:'same-panorama'}}),photo('other-place',{context:{name:'Cafe',poi:{lat:40.8,lon:-96.7}}}),photo('other-id',{context:{name:'Garden',poi:{id:'node/2',lat:40.8,lon:-96.7}}}),photo('expired',{expiresAt:1}),photo('pending',{state:'running'}),photo('failed',{state:'failed'})];
 assert.deepEqual(f.photosAt(spot,tasks),['SW','NE']);
});

test('Photos background link focuses the saved coordinates even without its search history',()=>{
 const f=pageFixture(),task={id:'photo',kind:'portrait',state:'complete',created:30,context:{name:'Park',provider:'google-street-view',poi:{lat:40.8,lon:-96.7},viewHeadingDegrees:225}};
 const state=f.focusPhoto(task);assert.deepEqual(Array.from(state.position),[40.8,-96.7]);assert.equal(state.zoom,17);assert.equal(state.opened,true);assert.equal(state.spot.name,'Park');assert.equal(state.shortlistHidden,true);assert.equal(state.photosOpen,false);
});


test('only visible page visits renew guest retention; hidden polling leaves its deadline alone',async()=>{
 const f=pageFixture();await f.restoreWith([],{});
 assert.equal(f.recoveryRequests().at(-1).url.endsWith('/tasks?visit=true'),true);
 f.setHidden(true);await f.restoreWith([],{});
 assert.equal(f.recoveryRequests().at(-1).url.endsWith('/tasks'),true);
 f.setHidden(false);await f.restoreWith([],{});
 assert.equal(f.recoveryRequests().at(-1).url.endsWith('/tasks?visit=true'),true);
});


test('only the selected search numbers every scored POI; other history stays unnumbered',()=>{
 const f=pageFixture();
 const records=['a','b'].map(id=>({id,checked:true,label:id,result:{topLimit:3,spots:[],poiResults:Array.from({length:6},(_,i)=>({id:id+i,name:id+i,score:i,imageUrl:'https://example.test/image',viewHeadingDegrees:90,poi:{id:id+i,lat:40+i*.01,lon:-96}}))}}));
 let markers=f.rankedHistory(records,null);
 assert.equal(markers.length,12);assert.ok(markers.every(m=>m.icon.className.startsWith('direction-dot')&&!m.icon.html.includes('photo-rank')));
 for(const selected of ['a','b']){
  markers=f.rankedHistory(records,selected);
  const focused=markers.filter(m=>m.searchId===selected),other=markers.filter(m=>m.searchId!==selected);
  assert.equal(focused.length,6);focused.forEach((m,i)=>{assert.match(m.icon.className,/photographer-pin/);assert.ok(m.icon.html.includes('photo-rank">'+(i+1)+'</span>'));});
  assert.ok(other.every(m=>m.icon.className.startsWith('direction-dot')&&!m.icon.html.includes('photo-rank')));
 }
});


test('removing a POI hides only that search history; a later search still shows the same POI',()=>{
 const f=pageFixture(),spot={id:'shared',name:'Shared place',score:90,imageUrl:'https://example.test/image',poi:{id:'shared',lat:40,lon:-96}};
 const records=['old','new'].map(id=>({id,checked:true,label:id,result:{spots:[spot]}}));
 f.setHistoryExclusions({old:['shared']});const markers=f.rankedHistory(records,'new');
 assert.equal(markers.length,1);assert.equal(markers[0].searchId,'new');assert.equal(records[0].result.spots.length,1);
});
test('arrow view controls wrap bearing, clamp tilt, and preserve the original scored view',()=>{
 const f=pageFixture(),spot={id:'view',provider:'google-street-view',sourceUrl:'https://www.google.com/maps/@?map_action=pano&pano=fixture&heading=350',viewHeadingDegrees:350,viewPitchDegrees:80,score:90};
 const changed=f.rotateView(spot,['ArrowRight','ArrowUp','ArrowUp']);
 assert.equal(changed.spot.viewHeadingDegrees,5);assert.equal(changed.spot.viewPitchDegrees,90);assert.match(changed.spot.sourceUrl,/heading=5/);assert.match(changed.spot.sourceUrl,/pitch=90/);assert.match(changed.icon.html,/photo-rank">4/);
 assert.equal(spot.viewHeadingDegrees,350);assert.equal(spot.viewPitchDegrees,80);assert.equal(changed.spot.score,90);
 const left=f.rotateView({...spot,viewHeadingDegrees:0,viewPitchDegrees:-90},['ArrowLeft','ArrowDown']);assert.equal(left.spot.viewHeadingDegrees,345);assert.equal(left.spot.viewPitchDegrees,-90);
});


test('finishing the selected search automatically opens its shortlist',async()=>{
 const f=pageFixture(),context={lat:41.88,lon:-87.62,radius:500,query:'Current search'},task={id:'current',kind:'search',state:'queued',created:100,context};
 await f.restoreWith([task],{});
 await f.restoreWith([{...task,state:'complete'}],{current:{state:'complete',context,result:{spots:[],summary:'Done'}}});
 assert.equal(f.element('results').hidden,false);assert.equal(f.element('toggle-results').disabled,false);
});

test('Google popup refreshes expired previews, bounds retries and recovers on reopening',async()=>{
 const source=readFileSync('photo-scout-site/app.js','utf8');
 const handlers={},image={dataset:{},addEventListener(type,fn){handlers[type]=fn;},replaceWith(){throw Error('Preview image must remain retryable');}};
 const box={dataset:{},photoSpot:{provider:'google-street-view',sourceUrl:'https://www.google.com/maps/@?pano=test',imageUrl:'expired'},append(){},querySelector(){return image;}};
 const popup={querySelectorAll(){return [box];}};let calls=0,fail=false;
 const context={node(){return {};},async json(){calls++;if(fail)throw Error('Transient failure');return {imageUrls:['fresh-'+calls]};}};
 vm.runInNewContext(source.slice(source.indexOf('function loadPopupPhoto('),source.indexOf('function photoBackgroundInfo'))+
 source.slice(source.indexOf('async function refreshThumbnail('),source.indexOf('async function json('))+';this.open=loadPopupPhoto;',context);
 const settle=async()=>{await Promise.resolve();await Promise.resolve();};
 context.open(popup);await settle();assert.equal(image.src,'fresh-1');
 handlers.error();await settle();assert.equal(image.src,'fresh-2');
 handlers.error();await settle();assert.equal(calls,2);assert.equal(box.previewStatus.hidden,false);
 context.open(popup);await settle();assert.equal(image.src,'fresh-3');handlers.load();assert.equal(box.previewStatus.hidden,true);
 fail=true;context.open(popup,true);await settle();assert.equal(image.hidden,true);
 fail=false;context.open(popup);await settle();assert.equal(image.src,'fresh-5');handlers.load();assert.equal(image.hidden,false);
 box.photoSpot.sourceUrl+='&heading=90';context.open(popup,true);await settle();assert.equal(image.src,'fresh-6');
});

test('pressing the search button leaves expanded settings stable until submit',()=>{
 const source=readFileSync('photo-scout-site/app.js','utf8');
 const controls={open:true,contains:()=>false},menu={open:true,contains:()=>false};let collapsed=0;
 const context={controls,mapMenus:[menu],overlayControl:{collapse(){collapsed++;}}};
 vm.runInNewContext(source.slice(source.indexOf('function closeMenusOutside('),source.indexOf('// Capture before Leaflet')),context);
 const submitTarget={closest:selector=>selector==='#prompt-form'?{}:null};
 context.closeMenusOutside(submitTarget);
 assert.equal(controls.open,true,'pointerdown must not move the button before pointerup');
 assert.equal(menu.open,false);
 context.closeMenusOutside({closest:()=>null});
 assert.equal(controls.open,false,'an ordinary outside click still closes settings');
 assert.equal(collapsed,2);
});
