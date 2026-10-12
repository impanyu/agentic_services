const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
test('Leaflet layout updates reuse the opened POI content instead of replacing its loaded street view',()=>{
 const source=fs.readFileSync('photo-scout-site/app.js','utf8'),markers=[],loads=[];
 function node(tag,text,cls){return {tag,text,cls,children:[],append(...children){this.children.push(...children)},addEventListener(){},querySelectorAll(){return []}}}
 const spot={name:'Place',provider:'google-street-view',sourceUrl:'https://www.google.com/maps/@?pano=fixture',poi:{lat:1,lon:2}};
 const layer={clearLayers(){}},scope={photoLocationLayer:layer,otherPhotoLocationLayer:layer,candidatePoiLayer:layer,resultPins:[],searchHistory:[{id:'search',checked:true,result:{spots:[spot]},label:'Saved search'}],focusedSearchId:'search',poiViewOverrides:new Map(),allPoiViews:r=>r.spots,poiHistoryKey:()=> 'place',photoBearing:()=>({heading:90,label:'Facing E'}),photographerIcon:()=>({}),directionDot:()=>({}),node,link:()=>node('a'),publicationBadge:()=>node('span'),publishButton:()=>node('button'),removePoiButton:()=>node('button'),placeSelfies:()=>node('section'),commentSection:()=>node('section'),popupViewControls:()=>node('div'),popupPhotoPreview:()=>node('preview'),loadPopupPhoto:p=>loads.push(p),refreshVisiblePlaceSelfies(){},refreshPlaceSelfies(){},L:{marker(){const marker={handlers:{},addTo(){return this},bindPopup(factory){this.factory=factory;return this},on(name,fn){this.handlers[name]=fn;return this}};markers.push(marker);return marker}}};
 vm.runInNewContext(source.slice(source.indexOf('function drawHistoryMap('),source.indexOf("el('history-all').addEventListener",source.indexOf('function drawHistoryMap('))),scope);
 scope.drawHistoryMap();const marker=markers[0],original=marker.factory();original.children.find(c=>c.tag==='preview').loaded=true;marker.handlers.popupopen();
 for(let i=0;i<5;i++){const content=marker.factory();assert.equal(content,original);assert.equal(content.children.find(c=>c.tag==='preview').loaded,true)}
 assert.equal(scope.selectedPoiView.popup,original);assert.deepEqual(loads,[original]);marker.handlers.popupclose();marker.factory();marker.handlers.popupopen();assert.deepEqual(loads,[original,original]);
});

function positionFixture(){
 const source=fs.readFileSync('photo-scout-site/app.js','utf8'),events={},frames=[],observers=[],windowEvents={};
 const scope={el:id=>({hidden:true,getBoundingClientRect:()=>id==='map'?{width:390,height:844,bottom:844}:{right:0}}),document:{querySelector:()=>({getBoundingClientRect:()=>({top:742})})},L:{point:(x,y)=>({x,y})},map:{on(name,fn){(events[name]??=[]).push(fn)}},requestAnimationFrame:fn=>frames.push(fn),window:{addEventListener:(name,fn)=>windowEvents[name]=fn,ResizeObserver:class{constructor(fn){observers.push(fn)}observe(){}disconnect(){}}}};
 vm.runInNewContext(source.slice(source.indexOf('function fitPoiPopup('),source.indexOf("map.on('popupclose',event=>{const popup=")),scope);
 const makePopup=()=>({options:{},pans:0,updates:0,isOpen:()=>true,getElement:()=>({querySelector:()=>({})}),update(){this.updates++;if(this.options.autoPan)this.pans++}});
 return {makePopup,fire:(name,event={})=>(events[name]||[]).forEach(fn=>fn(event)),flush:()=>frames.splice(0).forEach(fn=>fn()),resize:()=>{observers.at(-1)();windowEvents.resize()},scope};
}
test('POI opening pans once; late content and viewport resizing never recenter after a drag',()=>{
 const f=positionFixture(),popup=f.makePopup();f.fire('popupopen',{popup});f.flush();assert.equal(popup.pans,1);assert.equal(popup.options.keepInView,false);assert.equal(popup.options.autoPan,false);
 f.fire('dragstart');for(let i=0;i<5;i++)f.resize();assert.equal(popup.pans,1);assert.ok(popup.updates>1);
 f.fire('popupclose',{popup});f.fire('popupopen',{popup});f.flush();assert.equal(popup.pans,2);
});
test('user gestures cancel pending positioning and a stale popup frame cannot pan a newer selection',()=>{
 for(const gesture of ['dragstart','zoomstart']){const f=positionFixture(),popup=f.makePopup();f.fire('popupopen',{popup});f.fire(gesture);f.flush();assert.equal(popup.pans,0);}
 const f=positionFixture(),old=f.makePopup(),current=f.makePopup();f.fire('popupopen',{popup:old});f.fire('popupopen',{popup:current});f.flush();assert.equal(old.pans,0);assert.equal(current.pans,1);
});
