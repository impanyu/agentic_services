const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
test('preset catalog has over 200 distinct images with lightweight local thumbnails',()=>{
 const items=JSON.parse(fs.readFileSync('photo-scout-site/avatars/presets.json')).items;
 assert.ok(items.length>200);assert.equal(new Set(items.map(x=>x.id)).size,items.length);
 for(const item of items){for(const url of [item.imageUrl,item.thumbnailUrl]){assert.match(url,/^\.\/avatars\/(characters|thumbnails)\/[\w-]+\.(png|webp)$/);assert.ok(fs.existsSync('photo-scout-site/'+url.slice(2)),item.name);}assert.ok(fs.statSync('photo-scout-site/'+item.thumbnailUrl.slice(2)).size<100000,item.name);}
});
test('selecting a preset uses its full image while the card uses its thumbnail',async()=>{
 class Node{constructor(){this.children=[];this.style={};}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this[k]=v;}querySelector(){return new Node();}close(){this.closed=true;}}
 const panel=new Node(),menu=new Node(),requests=[],chosen=[];menu.querySelector=()=>panel;
 const context={window:{},document:{getElementById:()=>menu,createElement:()=>new Node(),body:new Node()},fetch:async url=>{requests.push(url);return {ok:true,blob:async()=>({type:'image/png'})};},File:class{constructor(parts,name,options){Object.assign(this,{parts,name,...options});}},URL};vm.runInNewContext(fs.readFileSync('photo-scout-site/avatars.js','utf8'),context);
 const library=new context.window.PhotoScoutAvatars({api:'',request:async()=>({items:[]}),prepare:()=>{},session:()=>({id:'guest'}),canSelect:()=>true,onSelect:async file=>chosen.push(file)});
 library.presetItems=[{id:'preset:test',name:'Test person',imageUrl:'./avatars/characters/test.png',thumbnailUrl:'./avatars/thumbnails/test.webp',category:'Science'}];library.render(panel);
 const grid=panel.children.find(x=>x.className==='avatar-grid');const pick=grid.children[0].children[0];assert.equal(pick.children[0].src,'./avatars/thumbnails/test.webp');await pick.onclick();assert.deepEqual(requests,['./avatars/characters/test.png']);assert.equal(chosen[0].name,'Test person.png');
});
test('visual style and category filters combine without replacing the selected full asset',()=>{
 class Node{constructor(){this.children=[];this.style={};this.value='';}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this[k]=v;}querySelector(){return new Node();}close(){}}
 const panel=new Node(),menu=new Node();menu.querySelector=()=>panel;
 const context={window:{},document:{getElementById:()=>menu,createElement:()=>new Node(),body:new Node()}};vm.runInNewContext(fs.readFileSync('photo-scout-site/avatars.js','utf8'),context);
 const library=new context.window.PhotoScoutAvatars({session:()=>({id:'guest'})});
 library.presetItems=[{name:'Alice',category:'Storybook',visualStyle:'Watercolor'},{name:'Holmes',category:'Storybook',visualStyle:'Clay stop-motion'},{name:'Totoro',category:'Cartoons',visualStyle:'Plush & felt'}];library.render(panel);
 const filters=panel.children.find(x=>x.className==='avatar-filters'),grid=panel.children.find(x=>x.className==='avatar-grid');
 const category=filters.children.find(x=>x['aria-label']==='Character category'),style=filters.children.find(x=>x['aria-label']==='Avatar visual style');
 style.value='Watercolor';style.change();assert.deepEqual(grid.children.map(x=>x.hidden),[false,true,true]);
 category.value='Cartoons';category.change();assert.ok(grid.children.every(x=>x.hidden));
 style.value='all';style.change();assert.deepEqual(grid.children.map(x=>x.hidden),[true,true,false]);
 library.render(panel);const rerendered=panel.children.find(x=>x.className==='avatar-filters');assert.equal(rerendered.children.find(x=>x['aria-label']==='Character category').value,'Cartoons');
});
