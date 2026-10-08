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
