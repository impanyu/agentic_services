const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const source=readFileSync('photo-scout-site/app.js','utf8');
function fixture(){
 const requests=[],timers=new Map();let timerId=0,paints=0;
 const ctx={taskRecoveryGeneration:0,hydratedSearches:new Set(),removedHistoryItems:new Set(),searchHistory:[],
  json:url=>new Promise((resolve,reject)=>requests.push({id:url.split('/').pop(),resolve,reject})),
  setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
  drawHistoryMap:()=>paints++,renderHistory:()=>{}};
 vm.runInNewContext(source.slice(source.indexOf('async function restoreCompletedSearches('),source.indexOf('async function restoreTasks(){')),ctx);
 return {ctx,requests,paint:()=>{for(const [id,fn] of [...timers]){timers.delete(id);fn();}},paints:()=>paints,
  run:tasks=>ctx.restoreCompletedSearches(tasks,ctx.taskRecoveryGeneration)};
}
const task=id=>({id,kind:'search',state:'complete',created:Number(id)||1,context:{query:'Search '+id,lat:1,lon:2,radius:5000}});
const result={result:{spots:[{name:'A',score:80,poi:{lat:1,lon:2}}]}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('four reports in flight; faster reports draw while an earlier request is stalled',async()=>{
 const f=fixture(),run=f.run(['1','2','3','4','5'].map(task));
 assert.equal(f.requests.length,4);f.requests[1].resolve(result);await tick();
 assert.equal(f.requests.length,5);f.paint();assert.equal(f.paints(),1);assert.equal(f.ctx.searchHistory[0].id,'2');
 for(const r of f.requests)r.resolve(result);assert.equal(await run,true);assert.equal(f.ctx.searchHistory.length,5);
});
test('a failed report does not block other records, and remains eligible for retry',async()=>{
 const f=fixture(),run=f.run(['1','2'].map(task));f.requests[0].reject(Error('offline'));f.requests[1].resolve(result);
 await run;assert.equal(f.ctx.searchHistory.length,1);assert.equal(f.ctx.hydratedSearches.has('1'),false);
 const retry=f.run(['1','2'].map(task));assert.equal(f.requests.length,3);f.requests[2].resolve(result);await retry;
 assert.equal(f.ctx.searchHistory.length,2);
});
test('logout during recovery cannot restore places belonging to the previous account',async()=>{
 const f=fixture(),run=f.run(['1','2'].map(task));f.ctx.taskRecoveryGeneration++;
 for(const r of f.requests)r.resolve(result);assert.equal(await run,false);f.paint();assert.equal(f.ctx.searchHistory.length,0);assert.equal(f.paints(),0);
});
test('removed searches stay removed; unchecked searches stay unchecked',async()=>{
 const f=fixture();f.ctx.removedHistoryItems.add('search:1');f.ctx.searchHistory.push({id:'2',checked:false});
 const run=f.run(['1','2'].map(task));for(const r of f.requests)r.resolve(result);await run;
 assert.equal(f.ctx.searchHistory.length,1);assert.equal(f.ctx.searchHistory[0].checked,false);
});
test('account map and task recovery are not blocked by slow guest uploads',async()=>{
 let paints=0,finished=false;const elements=new Map(),ctx={authUser:null,csrfToken:null,
  searchHistory:[{id:'guest',created:1,result:{spots:[]}}],pendingHistory:new Map(),HISTORY_KEY:'history',
  json:async url=>url.endsWith('/auth/me')?{configured:true,user:{id:'alice'},csrfToken:'test'}:{items:[{id:'remote',created:2,result:{spots:[]}}]},
  el:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},renderHistory:()=>{},drawHistoryMap:()=>paints++,
  queueHistory:()=>new Promise(()=>{}),stripHistoryImage:s=>s,refreshComments:()=>{},refreshPhotoLibraryIfOpen:()=>{}};
 vm.runInNewContext(source.slice(source.indexOf('async function loadAccount(){'),source.indexOf("el('account-login').addEventListener")),ctx);
 ctx.loadAccount().then(()=>finished=true);await tick();
 assert.equal(finished,true);assert.equal(paints,1);assert.equal(ctx.searchHistory.length,2);
});
test('temporary image URLs are not required to draw a previously verified saved place',()=>{
 const ctx={};vm.runInNewContext(source.slice(source.indexOf('function hasScoredImage('),source.indexOf('function poiHistoryKey(')),ctx);
 assert.equal(ctx.hasScoredImage({score:80,verifiedImageAvailable:true}),true);
 assert.equal(ctx.hasScoredImage({score:80,verifiedImageAvailable:false}),false);
 assert.equal(ctx.hasScoredImage({score:null,verifiedImageAvailable:true}),false);
 assert.equal(ctx.hasScoredImage({score:80,verifiedImageAvailable:true,assessmentStatus:'no_verified_view'}),false);
});
