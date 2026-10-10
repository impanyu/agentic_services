const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const window={};vm.runInNewContext(readFileSync('photo-scout-site/history-map.js','utf8'),{window,Map,Number,Math,Date,Set,Promise,JSON,localStorage:{getItem(){return null;}}});const maps=window.PhotoScoutHistoryMap;
test('history previews use the resolved search center, not the current map or original task center',()=>{const a=maps.area({history:{radius:5000,result:{searchContext:{lat:41.8,lon:-87.6,radius:5000}}},task:{context:{lat:30.6,lon:104,radius:20000}}});assert.equal(a.lat,30.6);assert.equal(a.radius,20000);assert.equal(maps.area({task:{context:{lat:null,lon:0}}}),null);});
test('range rings fit the thumbnail for supported radii and latitudes, including the date line',()=>{for(const lat of [-85,0,41.8,85])for(const lon of [-180,0,180])for(const radius of [100,500,5000,20000]){const g=maps.geometry({lat,lon,radius});assert.ok(g.radius>0&&g.radius<=16.001);assert.ok(g.tiles.length>0&&g.tiles.length<=4);assert.ok(g.tiles.every(t=>t.x>=0&&t.x<2**t.z&&t.y>=0&&t.y<2**t.z));}});
test('city metadata uses administrative city, rather than a nearby attraction name',()=>{assert.equal(maps.cityLabel({name:'Cloud Gate',city:'Chicago',state:'Illinois',country:'United States'}),'Chicago, Illinois, United States');assert.equal(maps.cityLabel({name:'Unknown statue',country:'China'}),'');assert.equal(maps.cityLabel({name:'Chengdu',osm_value:'city',country:'China'}),'Chengdu, China');});

test('history preview leaves more context around the search circle',()=>{const g=maps.geometry({lat:41.8827,lon:-87.6233,radius:5000});assert.equal(g.width,96);assert.equal(g.height,72);assert.ok(g.radius<=16);});

test('failed city lookup releases the row so reopening history can retry',async()=>{
 const scope={};vm.runInNewContext(readFileSync('photo-scout-site/history-map.js','utf8'),{window:scope,Map,Number,Math,Date,Set,Promise,JSON,URLSearchParams,AbortController,localStorage:{getItem(){return null;},setItem(){}},fetch:async()=>{throw new Error('offline');},setTimeout(){return 1;},clearTimeout(){}});
 const item={dataset:{lat:'41.8',lon:'-87.6'},isConnected:true,textContent:'Chicago'},root={querySelectorAll(){return [item];}};
 scope.PhotoScoutHistoryMap.hydrate(root);assert.equal(item.dataset.loading,'true');await new Promise(resolve=>setImmediate(resolve));assert.equal(item.dataset.loading,undefined);assert.equal(item.textContent,'Chicago');
});
