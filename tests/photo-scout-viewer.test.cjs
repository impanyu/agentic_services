const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const {readFileSync}=require('node:fs');
function fixture(){
 const e=()=>({dataset:{},hidden:false,open:false,children:[],replaceChildren(...c){this.children=c;},removeAttribute(key){delete this[key];},setAttribute(key,value){this[key]=value;},showModal(){this.open=true;},close(){this.open=false;}}),calls=[];
 const c={focusMapSpot:(spot)=>calls.push(["focus",spot?.poi]),savedPhoto:e(),savedPhotoComments:e(),commentSection:()=>e(),savedPhotoSharing:e(),savedPhotoTitle:e(),savedPhotoPublish:e(),savedPhotoImage:e(),savedPhotoSave:e(),savedPhotoDownload:e(),savedPhotoHint:e(),savedPhotoStatus:e(),studio:e(),portraitProgress:e(),savedPhotoGeneration:0,savedPhotoTimer:null,savedPhotoUrl:null,savedPhotoFile:null,ownPublications:new Map(),publishButton:(kind,id)=>({kind,id}),viewPortraitProgress:async()=>{},clearTimeout(){},setTimeout(){},URL:{revokeObjectURL(){},createObjectURL:()=> 'blob:viewer'},File:class{constructor(parts,name){this.name=name;}},api:'https://api.aisoup.net',authUser:null,readPhoto:async()=> 'data:image/png;base64,test',renderSavedPhotoParams:(context)=>calls.push(['context',context?.name]),renderPhotoSharing:()=>calls.push(['sharing']),fetch:async url=>{calls.push(['image',url]);return {ok:true,blob:async()=>({})};},json:async path=>{calls.push(['report',path]);return {state:'complete',context:{name:'Private spot'}};}};
 const s=readFileSync('photo-scout-site/app.js','utf8');vm.runInNewContext(s.slice(s.indexOf('async function viewSavedPhoto('),s.indexOf("savedPhotoClose.addEventListener")),c);return {c,calls};
}
test('public photos use the same viewer and prepare a file for Share without private API access',async()=>{
 const {c,calls}=fixture();await c.viewSavedPhoto({id:'pub',created:100},{id:'pub',context:{name:'Public spot'},imageUrl:'https://api.aisoup.net/public-image'});
 assert.equal(c.savedPhoto.open,true);assert.equal(c.savedPhotoSave.hidden,false);assert.equal(c.savedPhotoFile.name,'photo-scout-ai-photo.png');assert.equal(c.savedPhoto.dataset.publication,'pub');assert.equal(c.savedPhotoPublish.children.length,0);assert.equal(calls.some(x=>x[0]==='report'),false);assert.equal(calls.find(x=>x[0]==='image')[1],'https://api.aisoup.net/public-image');
});
test('own public photo retains the publication toggle and switching to history clears public identity',async()=>{
 const {c}=fixture();c.ownPublications.set('key',{id:'pub',sourceId:'private-photo'});await c.viewSavedPhoto({id:'pub',created:100},{id:'pub',context:{name:'Public spot'},imageUrl:'https://api.aisoup.net/public-image'});assert.equal(c.savedPhotoPublish.children[0].id,'private-photo');await c.viewSavedPhoto({id:'history-photo',created:100,state:'complete'});assert.equal(c.savedPhoto.dataset.publication,undefined);assert.equal(c.savedPhoto.dataset.photoId,'history-photo');assert.equal(c.savedPhotoTitle.textContent,'Photo');assert.equal(c.savedPhotoSave.hidden,false);
});
test('late public image download cannot overwrite a subsequently opened history photo',async()=>{
 const {c,calls}=fixture();c.json=async()=>({state:'complete',context:{name:'Private spot',poi:{lat:40,lon:-96}}});let release;c.fetch=async url=>{if(url.endsWith('/public-image'))await new Promise(resolve=>{release=resolve;});return {ok:true,blob:async()=>({})};};const pending=c.viewSavedPhoto({id:'pub',created:100},{id:'pub',context:{name:'Public spot'},imageUrl:'https://api.aisoup.net/public-image'});await new Promise(resolve=>setImmediate(resolve));await c.viewSavedPhoto({id:'history-photo',created:100,state:'complete'});release();await pending;assert.equal(c.savedPhoto.dataset.photoId,'history-photo');assert.match(c.savedPhotoStatus.textContent,/Guest photo/);assert.equal(calls.filter(c=>c[0]==='focus').at(-1)[1].lat,40);
});

test('actions appear immediately while image loads, with file actions disabled until ready',async()=>{
 const {c}=fixture();let release;c.fetch=async()=>{await new Promise(resolve=>release=resolve);return {ok:true,blob:async()=>({})};};
 const pending=c.viewSavedPhoto({id:'pub',created:100},{id:'pub',context:{name:'Public spot'},imageUrl:'https://api.aisoup.net/public-image'});
 assert.equal(c.savedPhoto.open,true);assert.equal(c.savedPhotoSave.hidden,false);assert.equal(c.savedPhotoSave.disabled,true);assert.equal(c.savedPhotoDownload.hidden,false);assert.equal(c.savedPhotoDownload['aria-disabled'],'true');assert.equal(c.savedPhotoDownload.href,undefined);
 release();await pending;assert.equal(c.savedPhotoSave.disabled,false);assert.equal(c.savedPhotoDownload['aria-disabled'],undefined);assert.equal(c.savedPhotoDownload.href,'blob:viewer');
});
test('switching photos removes previous download while next image loads',async()=>{
 const {c}=fixture();await c.viewSavedPhoto({id:'one',created:100});let release;c.fetch=async()=>{await new Promise(resolve=>release=resolve);return {ok:true,blob:async()=>({})};};
 const pending=c.viewSavedPhoto({id:'two',created:100});assert.equal(c.savedPhotoDownload.href,undefined);assert.equal(c.savedPhotoSave.disabled,true);await new Promise(resolve=>setImmediate(resolve));release();await pending;
});
