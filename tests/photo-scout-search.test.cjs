const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const source=readFileSync('photo-scout-site/app.js','utf8');

test('a text search followed by a blank search sends only current visible inputs',async()=>{
 const requests=[],styles=[{value:'any',checked:true},{value:'urban',checked:false}];
 const inputs={'search-mode':{value:'nearby'},lat:{value:'41.89'},lon:{value:'-87.63'},radius:{value:'2000',options:[{value:'2000'}]},'prompt-query':{value:'high rise buildings with glass wall'},'style-options':{querySelectorAll:selector=>selector==='input:checked'?styles.filter(i=>i.checked):styles},'prompt-status':{textContent:''}};
 const context={el:id=>{assert.notEqual(id,'preferences','never read the old hidden input');return inputs[id];},locationSelectionRevision:0,tasksReady:true,searchBusy:false,activeSearch:null,poiCatalog:null,
  crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789012'},
  invalidatePois(){context.poiCatalog=null;},updateSubmitState(){},setProgress(){},message(){},addSubmittedTask(){},poll:async()=>{},
  json:async(path,options)=>{requests.push(JSON.parse(options.body));return {jobId:'job-'+requests.length};},
  activeRouteGeometry:null,applyRouteControls(){},showRoute(){},selected:{setLatLng(){}},syncMapSelection(){},updateParameterSummary(){},fitSearchRange(){},stopPoiScan(){},candidatePoiLayer:{clearLayers(){}}};
 vm.runInNewContext(source.slice(source.indexOf('function coordinates()'),source.indexOf('function invalidatePois()')),context);
 vm.runInNewContext(source.slice(source.indexOf('function searchDraft()'),source.indexOf('function addSubmittedTask(')),context);
 vm.runInNewContext(source.slice(source.indexOf('async function submitDurableSearch('),source.indexOf('function fitSearchRange(')),context);
 vm.runInNewContext(source.slice(source.indexOf('function applyTaskContext('),source.indexOf('function focusHistorySearch(')),context);
 await context.submitDurableSearch(inputs['prompt-query'].value);
 // The first result carries model-generated criteria. They must remain in the
 // report, not become an invisible default for the next user submission.
 context.applyTaskContext({...requests[0],preferences:'Show glass facades.',scoringIntent:'Glass towers',poiQueries:['skyscrapers'],photoStyles:['urban']});
 inputs['prompt-query'].value='';styles[0].checked=true;styles[1].checked=false;
 context.poiCatalog={coords:{preferences:'Show glass facades.',poiQueries:['skyscrapers']}};
 await context.submitDurableSearch('');
 assert.deepEqual(requests[1],{lat:41.89,lon:-87.63,radius:2000,photoStyles:null,query:''});
 assert.equal(context.poiCatalog,null);
 inputs['prompt-query'].value='old words';context.applyTaskContext({...requests[1],preferences:'Old hidden conditions'});
 assert.equal(inputs['prompt-query'].value,'','restoring a blank history query clears earlier text');
});
test('route submission sends visible endpoints and walking mode without circle conditions',()=>{const inputs={lat:{value:'37.8'},lon:{value:'-122.4'},radius:{value:'5000'},'style-options':{querySelectorAll:()=>[]},'search-mode':{value:'route'},'route-origin':{value:''},'route-destination':{value:'Golden Gate Bridge, San Francisco'},'route-travel':{value:'walk'},'route-corridor':{value:'300'}};const context={el:id=>inputs[id],restoredRoute:null};vm.runInNewContext(source.slice(source.indexOf('function coordinates()'),source.indexOf('function updateSearchMode()')),context);assert.deepEqual(JSON.parse(JSON.stringify(context.coordinates().route)),{origin:null,destination:{query:'Golden Gate Bridge, San Francisco'},travelMode:'walk',corridorMeters:300});inputs['search-mode'].value='nearby';assert.equal(context.coordinates().route,undefined);});
test('route submission can fit its initial view while the circle layer is detached',()=>{const inputs={lat:{value:'37.8'},lon:{value:'-122.4'},radius:{value:'5000'}};let fitted=null;const context={el:id=>inputs[id],activeRouteGeometry:null,syncMapSelection(){},searchArea:{getBounds(){throw Error('detached circle');}},map:{hasLayer(){return false;},fitBounds(bounds){fitted=bounds;}},document:{querySelector(){return {getBoundingClientRect(){return {height:100};}};}}};vm.runInNewContext(source.slice(source.indexOf('function fitSearchRange('),source.indexOf('function applyRouteControls(')),context);context.fitSearchRange();assert.equal(fitted.length,2);assert.ok(fitted[0][0]<37.8&&fitted[1][0]>37.8);});
