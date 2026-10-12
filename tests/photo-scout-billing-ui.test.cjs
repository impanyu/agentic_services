const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function boot({user=null,available=0,native=false}={}){
 class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.open=false;this.textContent='';}
  append(...nodes){for(const n of nodes){if(n.parentElement)n.parentElement.children=n.parentElement.children.filter(x=>x!==n);n.parentElement=this;this.children.push(n)}}
  prepend(n){this.append(n);this.children.unshift(this.children.pop())}
  replaceChildren(){this.children=[]}
  setAttribute(){}
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

test('guest entry signs in directly and profile copy has no internal pricing controls',async()=>{const f=boot();await f.wallet.load();let prevented=false;f.root.listeners.click({preventDefault(){prevented=true}});assert.equal(prevented,true);assert.equal(f.logins(),1);assert.equal(f.menu.open,false);const texts=f.descendants(f.root).map(e=>e.textContent).join(' ' );assert.match(texts,/\$1 = 100 points/);assert.doesNotMatch(texts,/Maximum charge|recorded API|cost ×|point limit/);assert.equal(f.descendants(f.root).filter(e=>e.tagName==='input').length,0);const owner=boot({user:'owner'});await owner.wallet.load();assert.equal(owner.get('profile-title').textContent,'Profile');});
