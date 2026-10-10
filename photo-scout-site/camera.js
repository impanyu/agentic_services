/* Camera capture stays on the device until the user submits the composite. */
window.PhotoScoutCamera=class {
 constructor(onPhoto){
  this.onPhoto=onPhoto;this.stream=null;this.generation=0;
  this.dialog=document.createElement('dialog');this.dialog.className='photo-studio camera-dialog';this.dialog.setAttribute('aria-label','Take your selfie');
  this.dialog.innerHTML='<div class="studio-heading"><h2>Take your selfie</h2><button type="button" data-camera="close">Close ×</button></div><video autoplay muted playsinline aria-label="Live camera preview"></video><p class="camera-status small" role="status" aria-live="polite"></p><div class="camera-actions"><button type="button" class="studio-generate" data-camera="capture" disabled>Take photo</button><button type="button" class="studio-download" data-camera="native">Use device camera</button></div><p class="small">Your camera preview stays on this device. The photo is sent only when you create your composite.</p>';
  document.body.append(this.dialog);this.video=this.dialog.querySelector('video');this.status=this.dialog.querySelector('.camera-status');this.capture=this.dialog.querySelector('[data-camera=capture]');
  this.native=document.createElement('input');this.native.type='file';this.native.accept='image/*';this.native.setAttribute('capture','user');this.native.hidden=true;this.dialog.append(this.native);
  this.dialog.querySelector('[data-camera=close]').onclick=()=>{this.stop();this.dialog.close();};this.dialog.addEventListener('close',()=>{if(!this.dialog.open)this.stop();});this.dialog.addEventListener('cancel',()=>this.stop());
  this.video.addEventListener('loadeddata',()=>{this.capture.disabled=!this.video.videoWidth;});
  this.capture.onclick=()=>this.takePhoto();this.dialog.querySelector('[data-camera=native]').onclick=()=>{this.stop();this.status.textContent='Use your device camera to take a photo, or close this window to upload one.';this.native.value='';this.native.click();};
  this.native.onchange=()=>{const file=this.native.files?.[0];if(file){this.dialog.close();this.onPhoto(file);}};
 }
 stop(){this.generation++;for(const track of this.stream?.getTracks()||[])track.stop();this.stream=null;this.video.srcObject=null;this.capture.disabled=true;}
 async open(){
  this.stop();const generation=this.generation;this.status.textContent='Opening camera… Allow camera access when your browser asks.';this.dialog.showModal();
  if(!navigator.mediaDevices?.getUserMedia){this.status.textContent='Live camera is unavailable in this browser. Use device camera or upload a photo.';return;}
  try{
   const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'user'},width:{ideal:1280},height:{ideal:1280}},audio:false});
   if(generation!==this.generation||!this.dialog.open){for(const track of stream.getTracks())track.stop();return;}
   this.stream=stream;this.video.srcObject=stream;await this.video.play();
   if(generation!==this.generation)return;
   this.capture.disabled=!this.video.videoWidth;this.status.textContent='Frame your selfie, then tap Take photo.';
  }catch(error){
   if(generation!==this.generation)return;this.stop();
   this.status.textContent=error.name==='NotAllowedError'?'Camera access was not granted. Enable it in your browser settings, or use device camera / upload a photo.':'Could not open the camera. Use device camera or upload a photo.';
  }
 }
 takePhoto(){
  if(!this.stream||!this.video.videoWidth||this.capture.disabled)return;
  this.capture.disabled=true;this.status.textContent='Preparing your photo…';const generation=this.generation;
  const scale=Math.min(1,2048/Math.max(this.video.videoWidth,this.video.videoHeight));const canvas=document.createElement('canvas');canvas.width=Math.round(this.video.videoWidth*scale);canvas.height=Math.round(this.video.videoHeight*scale);canvas.getContext('2d').drawImage(this.video,0,0,canvas.width,canvas.height);
  canvas.toBlob(blob=>{
   if(generation!==this.generation||!this.dialog.open)return;
   if(!blob){this.status.textContent='Could not capture the photo. Please try again.';this.capture.disabled=false;return;}
   this.dialog.close();this.stop();this.onPhoto(new File([blob],'selfie-camera.jpg',{type:'image/jpeg'}));
  },'image/jpeg',.92);
 }
};
