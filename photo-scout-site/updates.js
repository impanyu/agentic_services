/* Check the deployment version on foreground, without losing an active editor. */
window.PhotoScoutUpdates={start(busy){
 let version=document.querySelector('meta[name="photo-scout-version"]')?.content||null,checking=false,lastCheck=0,notice=null;
 const check=async()=>{if(document.hidden||checking||Date.now()-lastCheck<10000)return;checking=true;lastCheck=Date.now();
  try{const response=await fetch('./version.json',{cache:'no-store'});if(!response.ok)return;const data=await response.json();if(!/^[a-f0-9]{16}$/.test(data.version))return;if(version===null){version=data.version;return;}if(data.version===version)return;
   if(!busy()){location.reload();return;}
   if(!notice){notice=document.createElement('div');notice.className='app-update-notice';notice.setAttribute('role','status');const text=document.createElement('span');text.textContent='An update is ready. Finish your current edit, then refresh.';const button=document.createElement('button');button.type='button';button.textContent='Refresh';button.onclick=()=>{if(busy()){const okay=confirm('Refresh Photo Scout? Any unsent text or selected photos will be cleared.');if(!okay)return;}location.reload();};notice.append(text,button);document.body.append(notice);}
  }catch{}finally{checking=false;}
 };
 window.addEventListener('focus',check);window.addEventListener('pageshow',check);document.addEventListener('visibilitychange',check);setInterval(check,60000);check();
}};
