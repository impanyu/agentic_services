const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
test('preset catalog has at least 1000 characters with lightweight local thumbnails',()=>{
 const items=JSON.parse(fs.readFileSync('photo-scout-site/avatars/presets.json')).items;
 assert.ok(items.length>=1000);assert.ok(new Set(items.map(x=>x.name.split(' · ')[0].toLowerCase())).size>=1000);assert.equal(new Set(items.map(x=>x.name.split(' · ')[0].toLowerCase()+'|'+(x.visualStyle||'unspecified'))).size,items.length);assert.equal(new Set(items.map(x=>x.id)).size,items.length);
 for(const item of items){for(const url of [item.imageUrl,item.thumbnailUrl]){assert.match(url,/^\.\/avatars\/(characters|thumbnails)\/[\w-]+\.(png|webp)$/);assert.ok(fs.existsSync('photo-scout-site/'+url.slice(2)),item.name);}assert.ok(fs.statSync('photo-scout-site/'+item.thumbnailUrl.slice(2)).size<100000,item.name);}
});
test('avatar detail opens before selecting its full image for a selfie',async()=>{
 class Node{constructor(){this.children=[];this.style={};}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this[k]=v;}querySelector(){return new Node();}prepend(...children){this.children.unshift(...children);}showModal(){this.open=true;}close(){this.closed=true;}}
 const panel=new Node(),menu=new Node(),requests=[],chosen=[];menu.querySelector=()=>panel;
 const context={window:{},document:{getElementById:()=>menu,createElement:()=>new Node(),body:new Node()},fetch:async url=>{requests.push(url);return {ok:true,blob:async()=>({type:'image/png'})};},File:class{constructor(parts,name,options){Object.assign(this,{parts,name,...options});}},URL};vm.runInNewContext(fs.readFileSync('photo-scout-site/avatars.js','utf8'),context);
 const library=new context.window.PhotoScoutAvatars({api:'',request:async()=>({items:[]}),prepare:()=>{},session:()=>({id:'guest'}),canSelect:()=>true,getPlaces:()=>[{searchId:'a',searchLabel:'Search A',spot:{name:'Park'}},{searchId:'a',searchLabel:'Search A',spot:{name:'Lake'}},{searchId:'b',searchLabel:'Search B',spot:{name:'Tower'}}],onSelect:async file=>chosen.push(file)});
 library.presetItems=[{id:'preset:test',name:'Test person',imageUrl:'./avatars/characters/test.png',thumbnailUrl:'./avatars/thumbnails/test.webp',category:'Science'}];library.render(panel);
 const grid=panel.children.find(x=>x.className==='avatar-grid');const pick=grid.children[0].children[0];assert.equal(pick.children[0].src,'./avatars/thumbnails/test.webp');await pick.onclick();assert.equal(requests.length,0);const options=library.details.children.find(x=>x.className==='avatar-selfie-options');const searchMenu=options.children[1],placeMenu=options.children[2];assert.equal(searchMenu.children.length,3);searchMenu.value='search:a';searchMenu.change();assert.deepEqual(placeMenu.children.map(x=>x.textContent),['Park','Lake']);searchMenu.value='search:b';searchMenu.change();assert.deepEqual(placeMenu.children.map(x=>x.textContent),['Tower']);assert.equal(placeMenu.value,'2');searchMenu.value='current';searchMenu.change();options.children[2].value='current';await options.children[4].onclick();assert.deepEqual(requests,['./avatars/characters/test.png']);assert.equal(chosen[0].name,'Test person.png');
});
test('visual style and category filters combine without replacing the selected full asset',()=>{
 class Node{constructor(){this.children=[];this.style={};this.value='';}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this[k]=v;}querySelector(){return new Node();}close(){}}
 const panel=new Node(),menu=new Node();menu.querySelector=()=>panel;
 const context={window:{},document:{getElementById:()=>menu,createElement:()=>new Node(),body:new Node()}};vm.runInNewContext(fs.readFileSync('photo-scout-site/avatars.js','utf8'),context);
 const library=new context.window.PhotoScoutAvatars({session:()=>({id:'guest'})});
 library.presetItems=[{name:'Alice',category:'Storybook',visualStyle:'Watercolor'},{name:'Holmes',category:'Storybook',visualStyle:'Clay stop-motion'},{name:'Totoro',category:'Cartoons',visualStyle:'Plush & felt'}];library.render(panel);
 const filters=panel.children.find(x=>x.className==='avatar-filters'),grid=panel.children.find(x=>x.className==='avatar-grid');
 const category=filters.children.find(x=>x['aria-label']==='Character category'),style=filters.children.find(x=>x['aria-label']==='Avatar visual style');
 style.value='Watercolor';style.change();assert.deepEqual(grid.children.map(x=>x.children[0].children[1].textContent),['Alice']);
 category.value='Cartoons';category.change();assert.equal(grid.children.length,0);
 style.value='all';style.change();assert.deepEqual(grid.children.map(x=>x.children[0].children[1].textContent),['Totoro']);
 library.render(panel);const rerendered=panel.children.find(x=>x.className==='avatar-filters');assert.equal(rerendered.children.find(x=>x['aria-label']==='Character category').value,'Cartoons');
});
test('featured presets lead the grid and still filter correctly without reordering private uploads',()=>{
 class Node{constructor(){this.children=[];this.style={};this.value='';}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this[k]=v;}querySelector(){return new Node();}close(){}}
 const panel=new Node(),menu=new Node();menu.querySelector=()=>panel;
 const context={window:{},document:{getElementById:()=>menu,createElement:()=>new Node(),body:new Node()}};vm.runInNewContext(fs.readFileSync('photo-scout-site/avatars.js','utf8'),context);
 let observerCallback;context.IntersectionObserver=class{constructor(callback){observerCallback=callback;}observe(){}disconnect(){}};
 const library=new context.window.PhotoScoutAvatars({session:()=>({id:'guest'})});
 library.presetItems=[{name:'Older scholar',category:'Chinese history'},{name:'Furina',category:'Game characters',featured:true,aliases:['原神']},{name:'New anime',category:'Anime',featured:true}];library.render(panel);
 const names=()=>panel.children.find(x=>x.className==='avatar-grid').children.map(x=>x.children[0].children[1].textContent);
 assert.deepEqual(names(),['Furina','New anime','Older scholar']);assert.deepEqual(library.presetItems.map(x=>x.name),['Older scholar','Furina','New anime']);
 const search=panel.children.find(x=>x.className==='avatar-filters').children[0];search.value='原神';search.input();assert.deepEqual(names(),['Furina']);
 library.presetItems=Array.from({length:1121},(_,i)=>({name:'Person '+i}));library.query='';library.render(panel);assert.equal(names().length,48);const firstCard=panel.children.find(x=>x.className==='avatar-grid').children[0];observerCallback([{isIntersecting:true}]);assert.equal(names().length,96);assert.equal(panel.children.find(x=>x.className==='avatar-grid').children[0],firstCard);assert.ok(!panel.children.some(x=>x.className==='avatar-load-more'));assert.equal(panel.children.find(x=>x.className?.includes('avatar-count')).textContent,'');
 library.tab='mine';library.items=[{name:'Latest upload'},{name:'Earlier upload',featured:true}];library.render(panel);assert.deepEqual(names(),['Latest upload','Earlier upload']);
});
test('guest avatar selfie action remains available and prompts login before fetching the full image',async()=>{
 class Node{constructor(){this.children=[];this.style={};this.value='';}append(...v){this.children.push(...v);}prepend(...v){this.children.unshift(...v);}replaceChildren(...v){this.children=v;}setAttribute(k,v){this[k]=v;}addEventListener(k,fn){this[k]=fn;}querySelector(){return new Node();}close(){}showModal(){this.open=true;}}
 const menu=new Node(),c={window:{},document:{getElementById:()=>menu,createElement:()=>new Node(),body:new Node()}};vm.runInNewContext(fs.readFileSync('photo-scout-site/avatars.js','utf8'),c);let prompts=0,images=0;const a=new c.window.PhotoScoutAvatars({api:'',request:async()=>({items:[]}),session:()=>({id:'guest',epoch:0}),isSignedIn:()=>false,requireSignIn:()=>{prompts++;return false;},getPlaces:()=>[]});a.avatarFile=async()=>{images++;};a.showDetails({id:'preset',name:'Totoro',imageUrl:'./avatar.png'},menu,false);const descendants=n=>[n,...n.children.flatMap(descendants)],use=descendants(a.details).find(n=>n.textContent==='Use this avatar · Take a selfie');assert.equal(use.disabled,false);await use.onclick();assert.equal(prompts,1);assert.equal(images,0);
});

test('Totoro retains both cel and plush treatments while pose-only duplicates stay hidden',()=>{const items=JSON.parse(fs.readFileSync('photo-scout-site/avatars/presets.json')).items;assert.deepEqual(items.filter(x=>x.name.split(' · ')[0]==='Totoro').map(x=>x.visualStyle),['Cel animation','Plush & felt']);for(const id of ['einstein-documentary','hepburn-editorial','doraemon-cel','monkey-king-3d'])assert.ok(!items.some(x=>x.id==='preset:'+id));});
