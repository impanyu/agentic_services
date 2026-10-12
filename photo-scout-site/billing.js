/* Point balances and itemized task bills; purchases settle only on the server. */
window.PhotoScoutBilling=class {
 constructor({request,session,signIn}){
  Object.assign(this,{request,session,signIn});this.epoch=0;this.searchLimit=250;this.portraitLimit=100;this.data=null;this.busy=false;this.applePrices={};
  try{const limits=JSON.parse(localStorage.getItem('photo-scout-task-limits')||'{}');for(const key of ['searchLimit','portraitLimit'])if(Number.isInteger(limits[key])&&limits[key]>=10&&limits[key]<=2000)this[key]=limits[key];}catch{}
  this.menu=document.getElementById('points-menu');this.root=this.menu.querySelector('.points-content');
  this.menu.addEventListener('toggle',()=>{if(this.menu.open)this.load();});this.render();
  window.addEventListener('focus',()=>{if(this.menu.open)this.load();});
 }
 headers(){return {'Content-Type':'application/json','X-CSRF-Token':this.session().csrf||''};}
 async load(before){
  const ticket=++this.epoch,user=this.session().id;
  try{const data=await this.request('/photo-scout/v1/billing'+(before?'?before='+encodeURIComponent(before):''));if(ticket!==this.epoch||user!==this.session().id)return;
   if(data.applePurchaseReady&&window.PhotoScoutNative?.pointProducts){try{const apple=await window.PhotoScoutNative.pointProducts();if(ticket!==this.epoch)return;this.applePrices=apple.prices||{};}catch{}}
   if(before)data.bills=[...(this.data?.bills||[]),...data.bills];this.data=data;this.render();window.dispatchEvent(new Event('photo-scout-credit-limits'));
  }catch(error){if(ticket===this.epoch)this.render(error.message);}
 }
 reset(){this.epoch++;this.data=null;this.render();this.load();}
 taskOptions(kind){return {creditLimitPoints:kind==='search'?this.searchLimit:this.portraitLimit};}
 async prepare(kind){
  if(!this.data)await this.load();
  if(!this.data)throw Error('Could not load your point balance. Please retry.');
  if(!this.data.enabled)return;
  await this.load();
  const limit=this.taskOptions(kind).creditLimitPoints;
  if(this.data.signInRequired){this.menu.open=true;throw Error('Sign in, then open Points & bills to buy points.');}
  if(this.data.availablePoints<limit){this.menu.open=true;throw Error(`This task reserves up to ${limit} points. Top up or lower its limit in Points & bills.`);}
 }
 async confirmReturn(){
  const params=new URLSearchParams(location.search),id=params.get('credit_session');if(!id||!this.session().id)return;
  this.menu.open=true;
  try{const result=await this.request('/photo-scout/v1/billing/confirm',{method:'POST',headers:this.headers(),body:JSON.stringify({sessionId:id})});await this.load();if(result.state!=='paid')this.render('Payment is still processing. Points appear after payment is confirmed.');else{params.delete('credit_session');history.replaceState(null,'',location.pathname+(params.size?'?'+params:'')+location.hash);}}
  catch(error){this.render(error.message);}
 }
 render(message=''){
  const root=this.root;root.replaceChildren();const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=text;if(cls)e.className=cls;return e;},data=this.data,fmt=n=>Number(n||0).toLocaleString(undefined,{maximumFractionDigits:3});
  const profile=this.session();document.querySelector('.profile-identity').hidden=!profile.id;document.getElementById('profile-title').textContent=profile.id?'Profile':'Sign in';document.getElementById('profile-name').textContent=profile.name||'';document.getElementById('profile-email').textContent=profile.email||'';document.getElementById('profile-avatar').textContent=profile.name?profile.name.trim().slice(0,1).toUpperCase():'◎';
  root.append(make('h3','Points & bills'));const status=make('p',message,'points-status');status.setAttribute('role','status');root.append(status);
  const native=/PhotoScout-iOS\//.test(navigator.userAgent);
  if(!data){if(message){const retry=make('button','Retry loading points');retry.type='button';retry.onclick=()=>this.load();root.append(retry);}else root.append(make('p','Loading points…','small'));return;}
  document.querySelector('.free-tag').textContent=data.enabled?'PAY WITH POINTS':'FREE PREVIEW';
  const test=document.getElementById('test-notice');test.textContent=data.enabled?'Tasks use points. Switch tabs while they run; results and bills are saved.':'Free test. Switch tabs while it runs; completed searches are saved in this browser.';
  if(data.signInRequired){root.append(make('p','Browse public places and examples without signing in. Sign in before starting paid tasks or buying points. Existing guest history and uploads transfer to your account.','small'));}
  else{const balance=make('div',null,'points-balance');balance.append(make('strong',fmt(data.availablePoints)+' points available'),make('span',fmt(data.balancePoints)+' total · '+fmt(data.heldPoints)+' reserved'));root.append(balance);}
  root.append(make('p','$1 buys 100 points. Task charge = recorded API cost × 3 × 100 points. Unused points carry over; purchased points do not expire. Failed tasks are not charged.','small'));
  const limits=make('section',null,'points-limits');limits.append(make('h4','Maximum charge per task'));
  for(const [key,label] of [['searchLimit','Search'],['portraitLimit','Generate photo']]){const field=make('label',label+' · points'),input=make('input');input.type='number';input.min='10';input.max='2000';input.step='1';input.value=this[key];input.setAttribute('aria-label',label+' point limit');input.onchange=()=>{const value=Number(input.value);if(!Number.isInteger(value)||value<10||value>2000){input.value=this[key];return;}this[key]=value;try{localStorage.setItem('photo-scout-task-limits',JSON.stringify({searchLimit:this.searchLimit,portraitLimit:this.portraitLimit}));}catch{}window.dispatchEvent(new Event('photo-scout-credit-limits'));};field.append(input);limits.append(field);}
  limits.append(make('p','We reserve this limit before starting, then release the difference after the itemized bill is settled. Charges never exceed the limit.','small'));root.append(limits);
  for(const mode of ['payment','subscription']){const section=make('section',null,'points-products');section.append(make('h4',mode==='payment'?'Buy points':'Monthly points · automatic renewal'));
   for(const product of data.catalog.filter(p=>p.mode===mode)){const row=make('div',null,'points-product'),copy=make('div');copy.append(make('strong',fmt(product.points)+' points'),make('span',(native?(this.applePrices['net.aisoup.photoscout.'+product.id.replace('-','_')]||'Unavailable'):'$'+(product.cents/100).toFixed(2))+(mode==='subscription'?' / month · '+Math.round((product.points/product.cents-1)*100)+'% bonus':'')));const buy=make('button',mode==='subscription'?'Subscribe':'Buy');buy.type='button';buy.disabled=this.busy||data.signInRequired||(!native&&!data.purchaseReady)||(native&&(!data.applePurchaseReady||!window.PhotoScoutNative?.purchasePoints||!this.applePrices['net.aisoup.photoscout.'+product.id.replace('-','_')]));
    buy.onclick=async()=>{const identity=this.session().id;this.busy=true;this.render();let errorMessage='';try{
      if(native){await window.PhotoScoutNative.purchasePoints(product.id);await this.load();}
      else{const result=await this.request('/photo-scout/v1/billing/checkout',{method:'POST',headers:this.headers(),body:JSON.stringify({product:product.id,requestToken:crypto.randomUUID().replaceAll('-','')})});if(identity!==this.session().id)return;if(!result.checkoutUrl?.startsWith('https://checkout.stripe.com/'))throw Error('Invalid checkout link');location.assign(result.checkoutUrl);}
    }catch(error){if(identity===this.session().id)errorMessage=error.message;}finally{this.busy=false;if(identity===this.session().id)this.render(errorMessage);}};row.append(copy,buy);section.append(row);}
   if(mode==='subscription')section.append(make('p','A finite point allowance is added after each successful monthly payment. Cancel future renewal at any time; remaining points stay in your wallet.','small'));root.append(section);
  }
  if(native&&(!data.applePurchaseReady||!window.PhotoScoutNative?.purchasePoints||!this.applePrices['net.aisoup.photoscout.'+product.id.replace('-','_')]))root.append(make('p','Point purchases in this iOS build are not available yet. Your existing account points and bills work here.','small'));
  else if(!data.purchaseReady&&!native)root.append(make('p','Purchases are being configured. You can use any existing points.','small'));
  if(native&&data.applePurchaseReady&&window.PhotoScoutNative?.restorePoints){for(const [label,action] of [['Restore purchases','restorePoints'],['Manage Apple subscription','managePoints']]){const button=make('button',label);button.type='button';button.onclick=async()=>{try{await window.PhotoScoutNative[action]();await this.load();}catch(error){status.textContent=error.message;}};root.append(button);}}
  if(data.subscriptions?.length){root.append(make('p',data.subscriptions.map(s=>s.product+' · '+s.state).join('\n'),'small'));}
  if(data.ledger?.some(e=>e.reference.startsWith('stripe-'))&&!native){const manage=make('button','Manage subscription & payment receipts');manage.type='button';manage.onclick=async()=>{try{const result=await this.request('/photo-scout/v1/billing/portal',{method:'POST',headers:this.headers()});location.assign(result.url);}catch(error){status.textContent=error.message;}};root.append(manage);}
  if(data.pending?.length){const section=make('section');section.append(make('h4','Reserved for running tasks'));for(const task of data.pending)section.append(make('p',task.kind+' · '+fmt(task.limitPoints)+' points reserved · '+task.job,'small'));root.append(section);}
  const bills=make('section',null,'points-bills');bills.append(make('h4','Task bills'));
  for(const bill of data.bills||[]){const detail=make('details'),summary=make('summary',new Date(bill.created*1000).toLocaleString()+' · '+(bill.kind==='search'?'Search':'Photo')+' · '+fmt(bill.points)+' points');detail.append(summary,make('p',bill.jobId+' · '+bill.state+' · '+fmt(bill.releasedPoints)+' reserved points released','small'));let populated=false;detail.addEventListener('toggle',()=>{if(!detail.open||populated)return;populated=true;const table=make('table'),head=make('tr');for(const label of ['Item','Calls','Points'])head.append(make('th',label));table.append(head);
   for(const item of bill.items){const row=make('tr');row.append(make('td',this.stageName(item.stage)+(item.model?' · '+item.model:'')+(item.note?' — '+item.note:'')),make('td',item.requests),make('td',fmt(item.points)));table.append(row);}
   detail.append(table,make('p',bill.basis,'small'));});bills.append(detail);
  }
  if(!data.bills?.length)bills.append(make('p','Bills appear after your next point-funded task. Earlier free tasks are not charged retrospectively.','small'));
  if(data.nextCursor){const more=make('button','Load older bills');more.type='button';more.onclick=()=>this.load(data.nextCursor);bills.append(more);}root.append(bills);
  if(data.ledger?.length){const log=make('details'),summary=make('summary','Recent wallet activity');log.append(summary);for(const entry of data.ledger)log.append(make('p',new Date(entry.created*1000).toLocaleString()+' · '+entry.reason+' · '+(entry.points>0?'+':'')+fmt(entry.points)+' points','small'));root.append(log);}
 }
 stageName(stage){return {'intent':'Search planning','image-planning':'Photo planning','scoring':'Image assessment','image-generation':'Photo generation','subject-check':'Person check','background-review':'Background analysis','places-text':'Place search','places-details-pro':'Place details','places-details-ids':'Place lookup','places-photo':'Place image','streetview-tile-z0':'Street View image','streetview-tile-z1':'Street View image','static-streetview':'Street View image','routes-essentials':'Route planning','task-limit-adjustment':'Task limit discount'}[stage]||stage;}
};
