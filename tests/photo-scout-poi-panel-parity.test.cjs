const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('photo-scout-site/app.js','utf8');
function fixture(user){
 const actions=[];function node(tag,text,cls){return {tag,text,cls,children:[],events:{},append(...items){this.children.push(...items);},addEventListener(name,fn){this.events[name]=fn;}};}
 const scope={authUser:user,node,link:(text)=>node('a',text),publicationBadge:()=>node('span'),photoBearing:()=>({label:'Facing W'}),photoBackgroundInfo:()=> 'Google Street View',popupPhotoPreview:()=>node('preview'),popupViewControls:()=>node('controls'),publishButton:()=>node('button','Publish place'),removePoiButton:()=>node('button','Remove'),placeSelfies:()=>node('section','Your selfies here'),commentSection:()=>node('section','Social actions'),openPhotoStudio:(spot,id)=>actions.push({spot,id})};
 vm.runInNewContext(source.slice(source.indexOf('function createPoiPanel('),source.indexOf('function drawHistoryMap(')),scope);return {scope,actions};
}
const spot={name:'Public place',score:81,provider:'google-street-view',sourceUrl:'https://www.google.com/maps/@?pano=fixture',poi:{lat:40,lon:-96}};
test('the same public POI has an identical full panel before and after login',()=>{
 const visitor=fixture(null),member=fixture({id:'member'}),options={searchId:'public:fixture',label:'Published place',publication:{id:'fixture',url:'https://photo-scout.aisoup.net/?public=fixture'},onView(){}};
 const panels=[visitor,member].map(f=>f.scope.createPoiPanel(spot,options));
 const shape=n=>({tag:n.tag,text:n.text,cls:n.cls,children:n.children.map(shape)});
 assert.deepEqual(shape(panels[0]),shape(panels[1]));
 for(let i=0;i<2;i++){const panel=panels[i];assert.ok(panel.children.some(n=>n.tag==='preview'));assert.ok(panel.children.some(n=>n.tag==='controls'));const selfie=panel.children.find(n=>n.text==='Take a selfie here');assert.ok(selfie);assert.notEqual(selfie.disabled,true);selfie.events.click();assert.equal([visitor,member][i].actions[0].id,'public:fixture');}
});
test('photo-background places also retain preview, view controls and the selfie entry without a search',()=>{const f=fixture(null),panel=f.scope.createPoiPanel(spot);assert.ok(panel.children.some(n=>n.tag==='preview'));assert.ok(panel.children.some(n=>n.tag==='controls'));panel.children.find(n=>n.text==='Take a selfie here').events.click();assert.equal(f.actions[0].spot,spot);assert.equal(f.actions[0].id,null);});
