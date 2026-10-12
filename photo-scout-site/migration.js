/* Transfer first-party browser preferences and cookie-owned history once. */
(()=>{
 if(location.hostname!=='photo-scout.aisoup.net'){window.PhotoScoutMigrationReady=Promise.resolve();return;}
 const initialKeys=new Set(Object.keys(localStorage)), marker='photo-scout-subdomain-migrated-v1';
 const base=location.origin+'/api/v1/session-migration/';
 const controller=new AbortController();
 async function post(url,body){
  const response=await fetch(url,{method:'POST',credentials:'include',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error('History transfer temporarily unavailable');
  return response.json();
 }
 function preferences(){return new Promise(resolve=>{
  const nonce=crypto.randomUUID(),frame=document.createElement('iframe');frame.hidden=true;
  let timer;
  function finish(){clearTimeout(timer);window.removeEventListener('message',receive);frame.remove();resolve();}
  function receive(event){
   if(event.origin!=='https://aisoup.net'||event.source!==frame.contentWindow||event.data?.nonce!==nonce||event.data?.type!=='photo-scout-preferences')return;
   for(const [key,value] of Object.entries(event.data.local||{})){
    if(key.startsWith('photo-scout')&&!initialKeys.has(key)&&typeof value==='string'&&value.length<2000000){try{localStorage.setItem(key,value);}catch{}}
   }
   for(const [key,value] of Object.entries(event.data.session||{})){
    if(key.startsWith('photo-scout')&&sessionStorage.getItem(key)===null&&typeof value==='string'&&value.length<2000000){try{sessionStorage.setItem(key,value);}catch{}}
   }
   window.dispatchEvent(new Event('photo-scout-preferences-migrated'));finish();
  }
  window.addEventListener('message',receive);frame.src='https://aisoup.net/photo-scout/migration.html';
  frame.onload=()=>frame.contentWindow.postMessage({type:'photo-scout-transfer-preferences',nonce},'https://aisoup.net');
  timer=setTimeout(finish,5000);document.body.append(frame);
 });}
 window.PhotoScoutMigrationReady=(async()=>{
  const timer=setTimeout(()=>controller.abort(),15000);
  try{
   if(localStorage.getItem(marker)!=='done')await preferences();
   // A legacy sign-in can happen after an earlier anonymous migration.
   // Preserve a current account; otherwise refresh the legacy identity bridge.
   const current=await fetch(location.origin+'/api/v1/auth/me',{credentials:'include',signal:controller.signal});
   if(!current.ok)throw Error('Account check unavailable');
   if((await current.json()).user){localStorage.setItem(marker,'done');return;}
   if(localStorage.getItem('photo-scout-legacy-signout-v1')==='1')return;
   const {state}=await post(base+'start',{});
   const {ticket}=await post('https://api.aisoup.net/photo-scout/v1/session-migration/export',{state});
   await post(base+'import',{state,ticket});localStorage.setItem(marker,'done');
  }catch(error){
   // Preserve data and retry on the next visit; no old records are deleted.
   console.warn('Photo Scout history transfer will retry on the next visit.');
  }finally{clearTimeout(timer);}
 })();
})();
