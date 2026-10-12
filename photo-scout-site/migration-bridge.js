'use strict';
window.addEventListener('message',event=>{
 if(event.origin!=='https://photo-scout.aisoup.net'||event.source!==parent||event.data?.type!=='photo-scout-transfer-preferences'||typeof event.data.nonce!=='string')return;
 const collect=storage=>Object.fromEntries(Object.keys(storage).filter(key=>key.startsWith('photo-scout')).map(key=>[key,storage.getItem(key)]));
 parent.postMessage({type:'photo-scout-preferences',nonce:event.data.nonce,local:collect(localStorage),session:collect(sessionStorage)},event.origin);
});
