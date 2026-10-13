const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function boot({user=null,available=0,native=false}={}){
 class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.open=false;this.textContent='';}
  append(...nodes){for(const n of nodes){if(n.parentElement)n.parentElement.children=n.parentElement.children.filter(x=>x!==n);n.parentElement=this;this.children.push(n)}}
  prepend(n){this.append(n);this.children.unshift(this.children.pop())}
  replaceChildren(){this.children=[]}
  setAttribute(name,value){this.attributes??={};this.attributes[name]=value}
  addEventListener(name,fn){this.listeners[name]=fn}
  querySelector(){return root}
  showModal(){this.open=true}
  close(){this.open=false;this.listeners.close?.()}
 }
 const menu=new Element('details'),home=new Element('div'),root=new Element('div');menu.append(home);home.append(root);
 const ids={'points-menu':menu};const get=id=>ids[id]??=(new Element('div'));
 const data={enabled:true,purchaseReady:true,signInRequired:!user,availablePoints:available,balancePoints:available,heldPoints:0,catalog:[{id:'points-500',mode:'payment',points:500,cents:500}],bills:[]};
 const calls=[];let logins=0;const window={addEventListener(){},dispatchEvent(){}};
 const context={window,document:{getElementById:get,querySelector:get,createElement:t=>new Element(t),body:new Element('body')},localStorage:{getItem(){return null}},navigator:{userAgent:native?'PhotoScout-iOS/1.0':'Chrome'},Event:class{},URLSearchParams,location:{search:''},crypto:require('node:crypto').webcrypto};
 vm.runInNewContext(fs.readFileSync('photo-scout-site/billing.js','utf8'),context);
 const wallet=new window.PhotoScoutBilling({session:()=>({id:user}),request:async path=>{calls.push(path);return data},signIn:()=>logins++});
 const descendants=e=>[e,...e.children.flatMap(descendants)];
 return {wallet,root,home,menu,calls,get,logins:()=>logins,descendants};
}
test('guest sees wallet controls and paid submission opens an account page without submitting a task',async()=>{
 const f=boot();await f.wallet.load();assert.equal(f.get('profile-title').textContent,'Sign in');
 assert.ok(f.descendants(f.root).some(e=>e.textContent==='0 points available'));
 assert.equal(await f.wallet.prepare('search'),false);assert.equal(f.wallet.access.open,true);assert.equal(f.root.parentElement,f.wallet.access);assert.equal(f.logins(),0);assert.ok(f.calls.every(p=>p==='/photo-scout/v1/billing'));
 f.wallet.access.close();assert.equal(f.root.parentElement,f.home);
});
test('guest purchase action opens login page, preserving the account catalog',async()=>{
 const f=boot();await f.wallet.load();const buy=f.descendants(f.root).find(e=>e.textContent==='Buy');assert.equal(buy.disabled,false);await buy.onclick();assert.equal(f.wallet.access.open,true);assert.equal(f.logins(),0);f.wallet.accessLogin.onclick();assert.equal(f.logins(),1);
});
test('funded users proceed and insufficient balances open top-up without login',async()=>{
 const funded=boot({user:'owner',available:100000});assert.equal(await funded.wallet.prepare('search'),true);assert.equal(funded.wallet.access.open,false);
 const empty=boot({user:'owner'});assert.equal(await empty.wallet.prepare('portrait'),false);assert.equal(empty.wallet.access.open,true);assert.equal(empty.wallet.accessLogin.hidden,true);empty.wallet.finishSignIn();assert.equal(empty.wallet.access.open,false);assert.equal(empty.menu.open,false);
});
test('native wallet with unavailable products renders without a reference error',async()=>{const f=boot({native:true});await f.wallet.load();assert.ok(f.descendants(f.root).some(e=>e.textContent.includes('In-app purchases are coming soon')))});

test('guest entry signs in directly and profile copy has no internal pricing controls',async()=>{const f=boot();await f.wallet.load();let prevented=false;f.root.listeners.click({preventDefault(){prevented=true}});assert.equal(prevented,true);assert.equal(f.logins(),0);assert.equal(f.wallet.access.open,true);f.wallet.accessLogin.onclick();assert.equal(f.logins(),1);assert.equal(f.menu.open,false);const texts=f.descendants(f.root).map(e=>e.textContent).join(' ' );assert.match(texts,/\$1 = 100 points/);assert.doesNotMatch(texts,/Maximum charge|recorded API|cost ×|point limit/);assert.equal(f.descendants(f.root).filter(e=>e.tagName==='input').length,0);const owner=boot({user:'owner'});await owner.wallet.load();assert.equal(owner.get('profile-title').textContent,'Profile');assert.equal(owner.get('account-logout').hidden,false);assert.equal(owner.get('profile-settings').hidden,false);assert.equal(f.get('account-logout').hidden,true);assert.equal(owner.menu.attributes['data-authenticated'],'true');assert.equal(f.menu.attributes['data-authenticated'],'false');});

test('customer billing hides raw stage names, task IDs, ledger notes and pricing metadata',async()=>{
 const f=boot({user:'owner',available:1000});await f.wallet.load();
 f.wallet.data.pending=[{kind:'search',limitPoints:250,job:'private-job-id'}];
 f.wallet.data.ledger=[{created:1,reason:'Recorded API cost ×3 ×100',reference:'private-reference',points:100}];
 f.wallet.data.subscriptions=[{product:'internal-plan-id',state:'active'}];
 f.wallet.data.bills=[{created:1,kind:'search',points:3,basis:'Recorded API cost ×3 ×100',pricing:{multiplier:3},items:[{stage:'private-provider-model-cost',points:3}]}];
 f.wallet.render();
 const detail=f.descendants(f.root).find(e=>e.tagName==='details'&&e.children.some(c=>c.tagName==='summary'&&c.textContent.includes('Search')));detail.open=true;detail.listeners.toggle();
 const text=f.descendants(f.root).map(e=>e.textContent).join(' ');
 assert.doesNotMatch(text,/Recorded API|×3|×100|private-|internal-plan|multiplier/);
 assert.match(text,/Place search · 250 points reserved/);assert.match(text,/Other services/);assert.match(text,/Monthly plan · Active/);assert.match(text,/Points added/);
});

test('guest task attempts require login before wallet requests even when billing is disabled',async()=>{
 for(const kind of ['search','portrait']){const f=boot();f.wallet.data={enabled:false};assert.equal(await f.wallet.prepare(kind),false);assert.equal(f.calls.length,0);assert.equal(f.wallet.access.open,true);assert.equal(f.wallet.accessLogin.hidden,false);assert.equal(f.root.hidden,true);f.wallet.access.close();assert.equal(f.root.hidden,false);}
});
test('guest search and generation entry points stop before parsing, scene changes or uploads',async()=>{
 const source=fs.readFileSync('photo-scout-site/app.js','utf8');
 const search=boot();const scope={pointWallet:search.wallet,serviceAvailable:true,resolving:false,pipelineBusy:false,searchBusy:false};
 vm.runInNewContext(source.slice(source.indexOf('async function submitSearch('),source.indexOf("el('prompt-form').addEventListener('submit',submitSearch)")),scope);await scope.submitSearch();assert.equal(search.wallet.access.open,true);assert.equal(search.calls.length,0);
 const portrait=boot();let generate;const studioScope={pointWallet:portrait.wallet,tasksReady:true,studioPrepared:'prepared',studioSpot:{name:'Selected place'},studioBusy:false,studioAdding:0,studioGenerate:{addEventListener(_,fn){generate=fn;}}};
 vm.runInNewContext(source.slice(source.indexOf("studioGenerate.addEventListener('click'"),source.indexOf('function finishStudio(')),studioScope);await generate();assert.equal(portrait.wallet.access.open,true);assert.equal(studioScope.studioBusy,false);assert.equal(portrait.calls.length,0);
});

test('login offers Google and Apple while unavailable providers cannot start',async()=>{
 const f=boot();const providers=[];f.wallet.signIn=p=>providers.push(p);f.wallet.setAuthProviders({google:true,apple:false});f.wallet.openAccess('Sign in');assert.equal(f.wallet.accessApple.disabled,true);assert.equal(f.wallet.appleNote.hidden,false);f.wallet.setAuthProviders({google:true,apple:true});assert.equal(f.wallet.accessApple.disabled,false);f.wallet.accessLogin.onclick();f.wallet.accessApple.onclick();assert.deepEqual(providers,['google','apple']);
 const native=boot({native:true});native.wallet.setAuthProviders({google:true,apple:true});assert.equal(native.wallet.accessApple.disabled,true);
});
test('opening the selfie feature as guest opens login before changing the scene',()=>{const source=fs.readFileSync('photo-scout-site/app.js','utf8'),calls=[],c={pointWallet:{requireSignIn:kind=>{calls.push(kind);return false;}}};vm.runInNewContext(source.slice(source.indexOf('function openPhotoStudio('),source.indexOf("studioClose.addEventListener('click'")),c);assert.equal(c.openPhotoStudio({name:'Place',poi:{lat:1,lon:2}}),false);assert.deepEqual(calls,['selfie']);});
test('selfie entry presents a clear login reason without displaying guest purchase controls',async()=>{const f=boot();await f.wallet.load();assert.equal(f.wallet.requireSignIn('selfie'),false);assert.equal(f.wallet.access.open,true);assert.equal(f.wallet.accessReason.textContent,'Sign in to take a selfie.');assert.equal(f.root.hidden,true);});
