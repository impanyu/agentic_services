/* Point balances and itemized task bills; purchases settle only on the server. */
window.PhotoScoutBilling=class {
 constructor({request,session,signIn}){
  Object.assign(this,{request,session,signIn});this.epoch=0;this.searchLimit=250;this.portraitLimit=100;this.data=null;this.busy=false;this.applePrices={};

  this.menu=document.getElementById('points-menu');this.root=this.menu.querySelector('.points-content');
  this.menu.querySelector('summary').addEventListener('click',event=>{if(!this.session().id){event.preventDefault();this.signIn();}});this.menu.addEventListener('toggle',()=>{if(this.menu.open)this.load();});this.createAccessPage();this.render();
  window.addEventListener('focus',()=>{if(this.menu.open)this.load();});
 }
 createAccessPage(){
  this.access=document.createElement('dialog');this.access.className='billing-access';this.access.setAttribute('aria-label','Account & points');
  const heading=document.createElement('header');heading.className='billing-access-heading';const title=document.createElement('h2');title.textContent='Account & points';const close=document.createElement('button');close.type='button';close.textContent='Back';close.onclick=()=>this.access.close();heading.append(title,close);
  this.accessReason=document.createElement('p');this.accessReason.className='billing-access-reason';this.accessReason.setAttribute('role','status');
  this.accessLogin=document.createElement('button');this.accessLogin.type='button';this.accessLogin.className='billing-access-login';this.accessLogin.textContent='Sign in with Google';this.accessLogin.onclick=()=>this.signIn();
  this.access.append(heading,this.accessReason,this.accessLogin);document.body.append(this.access);this.home=this.root.parentElement;
  const pageHeader=document.createElement('header');pageHeader.className='profile-page-heading';const profileTitle=document.createElement('h2');profileTitle.textContent='Profile';const back=document.createElement('button');back.type='button';back.textContent='Back';back.onclick=()=>this.menu.open=false;pageHeader.append(profileTitle,back);this.home.prepend(pageHeader);
  this.access.addEventListener('close',()=>{this.home.append(this.root);const settings=document.querySelector('.profile-settings');if(settings)this.home.append(settings);});
 }
 openAccess(reason){this.accessReason.textContent=reason;this.accessLogin.hidden=Boolean(this.session().id);this.menu.open=false;this.access.append(this.root);if(!this.access.open)this.access.showModal();}
 finishSignIn(){if(this.access.open)this.access.close();this.menu.open=false;}
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
  if(this.data.signInRequired){this.openAccess('Sign in to start this task. Your current search and selected photos stay here.');return false;}
  if(this.data.availablePoints<limit){this.openAccess('Not enough points for this task. Add points to continue.');return false;}
  return true;
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
  this.accessLogin.hidden=Boolean(profile.id);
  {const balance=make('div',null,'points-balance');balance.append(make('strong',fmt(data.availablePoints)+' points available'),make('span',fmt(data.balancePoints)+' total · '+fmt(data.heldPoints)+' reserved'));root.append(balance);}
  root.append(make('p','$1 = 100 points','points-rate'));
  for(const mode of ['payment','subscription']){const section=make('section',null,'points-products');section.append(make('h4',mode==='payment'?'Buy points':'Monthly plans'));
   for(const product of data.catalog.filter(p=>p.mode===mode)){const row=make('div',null,'points-product'),copy=make('div');copy.append(make('strong',fmt(product.points)+' points'),make('span',(native?(this.applePrices['net.aisoup.photoscout.'+product.id.replace('-','_')]||'Unavailable'):'$'+(product.cents/100).toFixed(2))+(mode==='subscription'?' / month · '+Math.round((product.points/product.cents-1)*100)+'% bonus':'')));const buy=make('button',mode==='subscription'?'Subscribe':'Buy');buy.type='button';buy.disabled=this.busy||(!native&&!data.purchaseReady)||(native&&(!data.applePurchaseReady||!window.PhotoScoutNative?.purchasePoints||!this.applePrices['net.aisoup.photoscout.'+product.id.replace('-','_')]));
    buy.onclick=async()=>{if(!this.session().id){this.openAccess('Sign in to add points to your account.');return;}const identity=this.session().id;this.busy=true;this.render();let errorMessage='';try{
      if(native){await window.PhotoScoutNative.purchasePoints(product.id);await this.load();}
      else{const result=await this.request('/photo-scout/v1/billing/checkout',{method:'POST',headers:this.headers(),body:JSON.stringify({product:product.id,requestToken:crypto.randomUUID().replaceAll('-','')})});if(identity!==this.session().id)return;if(!result.checkoutUrl?.startsWith('https://checkout.stripe.com/'))throw Error('Invalid checkout link');location.assign(result.checkoutUrl);}
    }catch(error){if(identity===this.session().id)errorMessage=error.message;}finally{this.busy=false;if(identity===this.session().id)this.render(errorMessage);}};row.append(copy,buy);section.append(row);}
   if(mode==='subscription')section.append(make('p','Renews monthly. Cancel anytime. Unused points carry over.','small'));root.append(section);
  }
  if(native&&(!data.applePurchaseReady||!window.PhotoScoutNative?.purchasePoints||!Object.keys(this.applePrices).length))root.append(make('p','In-app purchases are coming soon. Your existing points work here.','small'));
  else if(!data.purchaseReady&&!native)root.append(make('p','Purchases are being configured. You can use any existing points.','small'));
  if(native&&data.applePurchaseReady&&window.PhotoScoutNative?.restorePoints){for(const [label,action] of [['Restore purchases','restorePoints'],['Manage Apple subscription','managePoints']]){const button=make('button',label);button.type='button';button.onclick=async()=>{try{await window.PhotoScoutNative[action]();await this.load();}catch(error){status.textContent=error.message;}};root.append(button);}}
  if(data.subscriptions?.length){root.append(make('p',data.subscriptions.map(s=>s.product+' · '+s.state).join('\n'),'small'));}
  if(data.ledger?.some(e=>e.reference.startsWith('stripe-'))&&!native){const manage=make('button','Manage subscription & payment receipts');manage.type='button';manage.onclick=async()=>{try{const result=await this.request('/photo-scout/v1/billing/portal',{method:'POST',headers:this.headers()});location.assign(result.url);}catch(error){status.textContent=error.message;}};root.append(manage);}
  if(data.pending?.length){const section=make('section');section.append(make('h4','Reserved for running tasks'));for(const task of data.pending)section.append(make('p',task.kind+' · '+fmt(task.limitPoints)+' points reserved · '+task.job,'small'));root.append(section);}
  const bills=make('section',null,'points-bills');bills.append(make('h4','Task bills'));
  for(const bill of data.bills||[]){const detail=make('details'),summary=make('summary',new Date(bill.created*1000).toLocaleString()+' · '+(bill.kind==='search'?'Search':'Photo')+' · '+fmt(bill.points)+' points');detail.append(summary);let populated=false;detail.addEventListener('toggle',()=>{if(!detail.open||populated)return;populated=true;const table=make('table'),head=make('tr');for(const label of ['Item','Points'])head.append(make('th',label));table.append(head);
   for(const item of bill.items){const row=make('tr');row.append(make('td',this.stageName(item.stage)),make('td',fmt(item.points)));table.append(row);}
   detail.append(table);});bills.append(detail);
  }
  if(!data.bills?.length)bills.append(make('p','No bills yet.','small'));
  if(data.nextCursor){const more=make('button','Load older bills');more.type='button';more.onclick=()=>this.load(data.nextCursor);bills.append(more);}root.append(bills);
  if(data.ledger?.length){const log=make('details'),summary=make('summary','Recent wallet activity');log.append(summary);for(const entry of data.ledger)log.append(make('p',new Date(entry.created*1000).toLocaleString()+' · '+entry.reason+' · '+(entry.points>0?'+':'')+fmt(entry.points)+' points','small'));root.append(log);}
 }
 stageName(stage){return {'intent':'Search planning','image-planning':'Photo planning','scoring':'Image assessment','image-generation':'Photo generation','subject-check':'Person check','background-review':'Background analysis','places-text':'Place search','places-details-pro':'Place details','places-details-ids':'Place lookup','places-photo':'Place image','streetview-tile-z0':'Street View image','streetview-tile-z1':'Street View image','static-streetview':'Street View image','routes-essentials':'Route planning','task-limit-adjustment':'Task limit discount'}[stage]||stage;}
};
