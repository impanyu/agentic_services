const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const source=readFileSync('photo-scout-site/app.js','utf8');

test('a text search followed by a blank search sends only current visible inputs',async()=>{
 const requests=[],styles=[{value:'any',checked:true},{value:'urban',checked:false}];
 const inputs={lat:{value:'41.89'},lon:{value:'-87.63'},radius:{value:'2000',options:[{value:'2000'}]},'prompt-query':{value:'high rise buildings with glass wall'},'style-options':{querySelectorAll:selector=>selector==='input:checked'?styles.filter(i=>i.checked):styles},'prompt-status':{textContent:''}};
 const context={el:id=>{assert.notEqual(id,'preferences','never read the old hidden input');return inputs[id];},tasksReady:true,searchBusy:false,activeSearch:null,poiCatalog:null,
  crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789012'},
  invalidatePois(){context.poiCatalog=null;},updateSubmitState(){},setProgress(){},message(){},addSubmittedTask(){},poll:async()=>{},
  json:async(path,options)=>{requests.push(JSON.parse(options.body));return {jobId:'job-'+requests.length};},
  selected:{setLatLng(){}},syncMapSelection(){},updateParameterSummary(){},fitSearchRange(){},stopPoiScan(){},candidatePoiLayer:{clearLayers(){}}};
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
