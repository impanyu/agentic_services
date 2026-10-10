const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const {readFileSync}=require('node:fs');
function fixture(){
 const elements=new Map(),requests=[],opened=[];
 function node(tag,text,cls){return {tag,textContent:text,className:cls,dataset:{},children:[],hidden:false,setAttribute(key,value){this[key]=value;},append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},addEventListener(event,fn){this[event]=fn;},focus(){this.focused=true;}};}
 const el=id=>{if(!elements.has(id))elements.set(id,node('div'));return elements.get(id);};
 const tabs=['mine','liked','saved','commented'].map(tab=>{const b=node('button');b.dataset.photoTab=tab;return b;});
 const context={el,node,document:{querySelectorAll:()=>tabs},json:path=>new Promise((resolve,reject)=>requests.push({path,resolve,reject})),authUser:{name:'Alice'},URLSearchParams,Date,api:'https://api.test',openPublication:item=>opened.push(item)};
 const source=readFileSync('photo-scout-site/app.js','utf8');vm.runInNewContext(source.slice(source.indexOf("let photoLibraryTab='mine'")),context);
 return {context,el,tabs,requests,opened};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('photo tabs preserve personal history and ignore late responses from previous tabs',async()=>{
 const f=fixture();f.el('photo-items').children=['My existing photo'];f.context.selectPhotoLibraryTab('liked');f.context.selectPhotoLibraryTab('saved');
 assert.equal(f.el('photo-items').hidden,true);assert.deepEqual(f.el('photo-items').children,['My existing photo']);
 f.requests[1].resolve({items:[{id:'saved',title:'Saved view',thumbnailUrl:'/thumb',activityAt:1}],nextBefore:null});await settle();
 f.requests[0].resolve({items:[{id:'old',title:'Old liked view',activityAt:1}],nextBefore:null});await settle();
 assert.equal(f.el('photo-library-items').children.length,1);assert.equal(f.el('photo-library-items').children[0].children[1].children[0].textContent,'Saved view');
 f.el('photo-library-items').children[0].onclick();assert.equal(f.opened[0].id,'saved');assert.equal(f.el('photo-history').open,false);
 f.context.selectPhotoLibraryTab('mine');assert.equal(f.el('photo-items').hidden,false);assert.equal(f.el('photo-library-panel').hidden,true);assert.equal(f.tabs[0]['aria-selected'],'true');
});
test('photo activity pagination preserves the selected tab and appends items',async()=>{
 const f=fixture();f.context.selectPhotoLibraryTab('commented');f.requests[0].resolve({items:[{id:'one',title:'One',activityAt:100}],nextBefore:100});await settle();
 assert.equal(f.el('photo-library-more').hidden,false);const pending=f.context.loadPhotoLibrary(true);assert.match(f.requests[1].path,/tab=commented&before=100/);
 f.requests[1].resolve({items:[{id:'two',title:'Two',activityAt:90}],nextBefore:null});await pending;assert.equal(f.el('photo-library-items').children.length,2);assert.equal(f.el('photo-library-more').hidden,true);
});
