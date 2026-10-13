/* Shared private-library UI, with an explicit selection for each selfie scene. */
window.PhotoScoutAvatars=class {
 constructor({api,request,prepare,onSelect,session,canSelect,getPlaces,renderPlacePreview,isSignedIn=()=>true,requireSignIn}){
  Object.assign(this,{api,request,prepare,onSelect,session,canSelect,getPlaces,renderPlacePreview,isSignedIn,requireSignIn});this.generation=0;this.items=[];this.presetItems=[];this.tab='presets';this.query='';this.category='all';this.visualStyle='all';this.loaded=false;this.presetNames={};this.observers=new Map();
  this.menu=document.getElementById('avatar-library');this.panel=this.menu.querySelector('.avatar-content');this.dialog=document.createElement('dialog');this.dialog.className='photo-studio avatar-picker';this.dialog.setAttribute('aria-label','Choose an avatar');document.body.append(this.dialog);
  this.detailGeneration=0;this.details=document.createElement('dialog');this.details.className='photo-studio avatar-detail';this.details.setAttribute('aria-label','Avatar details');this.details.addEventListener('close',()=>{this.detailGeneration++;});document.body.append(this.details);
  this.menu.addEventListener('toggle',()=>{if(this.menu.open){this.tab='presets';this.query='';this.category='all';this.visualStyle='all';this.load(this.panel);}});this.dialog.addEventListener('close',()=>{this.generation++;});this.render(this.panel);if(this.menu.open)this.load(this.panel);
 }
 reset(){this.generation++;this.details.close();this.presetNames={};this.items=[];this.tab='presets';this.query='';this.category='all';this.visualStyle='all';this.loaded=false;this.dialog.close();this.render(this.panel);}
 async load(root,{force=false}={}){const identity=this.session().id;if(!force&&this.loaded&&this.loadedIdentity===identity&&Date.now()-this.loadedAt<30000){this.render(root);return;}const generation=++this.generation;this.render(root,this.loaded?'':'Loading your library…');try{const [data,presets]=await Promise.all([this.request('/photo-scout/v1/avatars').catch(()=>({items:[],unavailable:true})),this.presetItems.length?Promise.resolve({items:this.presetItems}):fetch('./avatars/presets.json?v=20261012stylebalance').then(r=>{if(!r.ok)throw Error('Preset library unavailable');return r.json();})]);if(generation!==this.generation)return;this.items=data.items;this.presetNames=data.presetNames||{};this.presetItems=presets.items;this.loaded=!data.unavailable;this.loadedIdentity=identity;this.loadedAt=Date.now();this.render(root,data.unavailable?'Your uploads could not be loaded. You can still choose a character.':'');}catch(error){if(generation===this.generation)this.render(root,error.message);}}
 open(){this.menu.open=false;this.tab='presets';this.query='';this.category='all';this.visualStyle='all';this.render(this.dialog);this.dialog.showModal();this.load(this.dialog);}
 render(root,message=''){
  this.observers.get(root)?.disconnect();this.observers.delete(root);root.replaceChildren();if(root===this.dialog){const top=document.createElement('div');top.className='studio-heading';top.innerHTML='<h2>Choose an avatar</h2><button type="button">Close ×</button>';top.querySelector('button').onclick=()=>this.dialog.close();root.append(top);}
  const tabs=document.createElement('div');tabs.className='avatar-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Avatar collections');
  for(const [id,title] of [['presets','Characters'],['mine','My uploads']]){const button=document.createElement('button');button.type='button';button.textContent=title;button.setAttribute('role','tab');button.setAttribute('aria-selected',String(this.tab===id));button.onclick=()=>{this.tab=id;this.render(root);};tabs.append(button);}root.append(tabs);
  const status=document.createElement('p');status.className='avatar-status small';status.setAttribute('role','status');status.textContent=message;root.append(status);
  if(this.tab==='mine'){
   const form=document.createElement('form');form.className='avatar-upload';const name=document.createElement('input');name.type='text';name.placeholder='Avatar name (optional)';name.maxLength=80;name.setAttribute('aria-label','Avatar name');const file=document.createElement('input');file.type='file';file.accept='image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif';file.setAttribute('aria-label','Upload an avatar');const submit=document.createElement('button');submit.type='submit';submit.textContent='Upload avatar';form.append(name,file,submit);root.append(form);
   form.onsubmit=async event=>{event.preventDefault();if(!file.files[0]){status.textContent='Choose a photo first.';return;}const generation=this.generation,identity=this.session().id;submit.disabled=true;status.textContent='Preparing and saving your avatar…';try{const source=file.files[0];if(source.size>20000000)throw Error('Choose a photo up to 20 MB.');const prepared=await this.prepare(source);if(!prepared.preview)throw Error('Export this image as JPG or PNG, then upload it.');if(generation!==this.generation||identity!==this.session().id)return;await this.request('/photo-scout/v1/avatars',{method:'POST',headers:this.headers(),body:JSON.stringify({name:name.value.trim()||source.name.replace(/\.[^.]+$/,'' ).slice(0,80)||'My avatar',image:prepared.data})});if(generation===this.generation&&identity===this.session().id)await this.load(root,{force:true});}catch(error){if(generation===this.generation)status.textContent=error.message;}finally{submit.disabled=false;}};
  }
  const grid=document.createElement('div');grid.className='avatar-grid';const items=this.tab==='mine'?this.items:[...this.presetItems].sort((a,b)=>Number(b.featured===true)-Number(a.featured===true));
  let filterCharacters=()=>{};let filtered=items,limit=48,shown=0;const trigger=document.createElement('div');trigger.className='avatar-load-trigger';trigger.setAttribute('aria-hidden','true');trigger.style.height='1px';let count;
  const drawCards=(reset=true)=>{if(reset){grid.replaceChildren();shown=0;}for(const item of filtered.slice(shown,limit))grid.append(makeCard(item));shown=Math.min(limit,filtered.length);trigger.hidden=filtered.length<=shown;if(count){count.hidden=!!filtered.length;count.textContent=filtered.length?'':'No matching characters. Try another name, category or visual style.';}};
  const loadMore=()=>{if(shown>=filtered.length)return;limit+=48;drawCards(false);};
  if(this.tab==='presets'){
   const filters=document.createElement('div');filters.className='avatar-filters';
   const search=document.createElement('input');search.type='search';search.placeholder='Find a character…';search.setAttribute('aria-label','Search characters');search.value=this.query;
   const category=document.createElement('select');category.setAttribute('aria-label','Character category');
   for(const name of ['all',...new Set(items.map(item=>item.category||'Classics'))]){const option=document.createElement('option');option.value=name;option.textContent=name==='all'?'All characters':name;category.append(option);}category.value=this.category;
   const visualStyle=document.createElement('select');visualStyle.setAttribute('aria-label','Avatar visual style');
   for(const name of ['all',...new Set(items.map(item=>item.visualStyle||'Original versions'))]){const option=document.createElement('option');option.value=name;option.textContent=name==='all'?'All visual styles':name;visualStyle.append(option);}visualStyle.value=this.visualStyle;
   count=document.createElement('p');count.className='small avatar-count';count.setAttribute('role','status');
   filterCharacters=()=>{const query=search.value.trim().toLocaleLowerCase();this.query=search.value;this.category=category.value;this.visualStyle=visualStyle.value;limit=48;filtered=items.filter(item=>(!query||[this.displayName(item),item.name,...(item.aliases||[])].join(' ').toLocaleLowerCase().includes(query))&&(category.value==='all'||(item.category||'Classics')===category.value)&&(visualStyle.value==='all'||(item.visualStyle||'Original versions')===visualStyle.value));drawCards();};
   search.addEventListener('input',filterCharacters);category.addEventListener('change',filterCharacters);visualStyle.addEventListener('change',filterCharacters);filters.append(search,category,visualStyle);root.append(filters,count);
  }
  const makeCard=item=>{const card=document.createElement('div');card.className='avatar-card';const pick=document.createElement('button');pick.type='button';pick.className='avatar-pick';pick.title='View '+this.displayName(item);const img=document.createElement('img');img.alt=this.displayName(item);img.loading='lazy';img.src=this.tab==='mine'?this.api+item.imageUrl:(item.thumbnailUrl||item.imageUrl);img.decoding='async';const title=document.createElement('span');title.textContent=this.displayName(item).split(' · ')[0];pick.append(img,title);if(this.tab==='presets'&&item.visualStyle){const badge=document.createElement('span');badge.className='avatar-style-badge';badge.textContent=item.visualStyle;pick.append(badge);}card.append(pick);
   const mine=this.tab==='mine';pick.onclick=()=>this.showDetails(item,root,mine);
   if(this.tab==='mine'){const remove=document.createElement('button');remove.type='button';remove.className='avatar-remove';remove.textContent='Remove';remove.setAttribute('aria-label','Remove '+item.name);remove.onclick=async()=>{remove.disabled=true;const generation=this.generation;try{await this.request('/photo-scout/v1/avatars/'+encodeURIComponent(item.id),{method:'DELETE',headers:this.headers()});if(generation===this.generation)await this.load(root,{force:true});}catch(error){if(generation===this.generation)status.textContent=error.message;}finally{remove.disabled=false;}};card.append(remove);}return card;
  };root.append(grid,trigger);if(this.tab==='presets')filterCharacters();else drawCards();
  if(typeof IntersectionObserver!=='undefined'){const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting))loadMore();},{root:root===this.dialog?this.dialog:this.menu.querySelector('.history-menu'),rootMargin:'240px 0px'});observer.observe(trigger);this.observers.set(root,observer);}else{const scrollRoot=root===this.dialog?this.dialog:this.menu.querySelector('.history-menu');scrollRoot?.addEventListener('scroll',()=>{if(scrollRoot.scrollHeight-scrollRoot.scrollTop-scrollRoot.clientHeight<240)loadMore();},{passive:true});}
  if(!items.length&&!message){status.textContent=this.tab==='mine'?'Your library is empty. Upload a selfie, a character or an animal.':'Preset characters are loading.';}
  const note=document.createElement('p');note.className='small';note.textContent=this.tab==='mine'?'Your uploads are private. Signed-in libraries are saved permanently; guest libraries expire after 7 days without a visit. Up to 50 images.':'AI-generated avatars, including imagined likenesses of familiar people and characters. Your selection only applies to the current photo spot.';root.append(note);
 }
 displayName(item){return this.presetNames?.[item.id]||item.name;}
 async avatarFile(item,mine){
  const response=await fetch(mine?this.api+item.imageUrl:item.imageUrl,{credentials:mine?'include':'same-origin'});
  if(!response.ok)throw Error('Could not load this avatar. Please try again.');
  const blob=await response.blob(),image=blob.type.includes('svg')?await this.rasterize(blob):blob;
  return new File([image],this.displayName(item)+(image.type==='image/png'?'.png':image.type==='image/webp'?'.webp':'.jpg'),{type:image.type});
 }
 showDetails(item,root,mine){
  const dialog=this.details;dialog.replaceChildren();const ticket=++this.detailGeneration,session=this.session();
  const current=()=>ticket===this.detailGeneration&&session.id===this.session().id&&session.epoch===this.session().epoch;
  const make=(tag,text,cls)=>{const element=document.createElement(tag);if(text)element.textContent=text;if(cls)element.className=cls;return element;};
  const heading=make('div',null,'studio-heading'),title=make('h2',this.displayName(item)),close=make('button','Close ×');close.type='button';close.onclick=()=>dialog.close();heading.append(title,close);
  const image=make('img',null,'avatar-detail-image');image.src=mine?this.api+item.imageUrl:item.imageUrl;image.alt=this.displayName(item);
  const intro=make('p',item.description||(mine?'Your privately uploaded reference photo. Use it on its own or add it to a group selfie.':'AI-generated avatar of '+item.name+'.'),'avatar-detail-intro');
  const meta=make('p',[item.category,item.visualStyle,item.aliases?.length?'Also known as: '+item.aliases.join(', '):null,mine&&item.created?'Uploaded '+new Date(item.created*1000).toLocaleDateString():null].filter(Boolean).join(' · '),'small');
  const form=make('form',null,'avatar-name-form'),label=make('label','Avatar name'),name=make('input');name.value=this.displayName(item);name.maxLength=80;name.required=true;name.setAttribute('aria-label','Avatar name');label.append(name);const save=make('button','Save name');save.type='submit';form.append(label,save);
  const note=make('p',mine?'This name is saved in your private library.':'Your name for this character is private; the shared catalog keeps its original name.','small');
  const status=make('p',null,'avatar-status');status.setAttribute('role','status');
  form.onsubmit=async event=>{event.preventDefault();const value=name.value.trim();if(!value){status.textContent='Enter an avatar name.';return;}save.disabled=true;try{const saved=await this.request('/photo-scout/v1/avatars/'+encodeURIComponent(item.id)+'/name',{method:'POST',headers:this.headers(),body:JSON.stringify({name:value})});if(!current())return;if(mine)item.name=saved.name;else this.presetNames[item.id]=saved.name;title.textContent=image.alt=saved.name;status.textContent='Name saved.';this.render(root);}catch(error){if(current())status.textContent=error.message;}finally{save.disabled=false;}};
  const section=make('section',null,'avatar-selfie-options'),subtitle=make('h3','Take a selfie'),places=this.getPlaces?.()||[];
  const select=make('select');select.setAttribute('aria-label','Choose an existing place');
  const searches=make('select');searches.setAttribute('aria-label','Choose a search');const groups=new Map();
  if(this.canSelect?.())groups.set('current',{label:'Current selfie location',places:[{value:'current',label:'Current location'}]});
  for(const [index,place] of places.entries()){const key='search:'+place.searchId;if(!groups.has(key))groups.set(key,{label:place.searchLabel||place.label,places:[]});groups.get(key).places.push({value:String(index),label:place.spot?.name||place.label});}
  for(const [key,group] of groups){const option=make('option',group.label);option.value=key;searches.append(option);}
  searches.value=groups.keys().next().value||'';
  const populatePlaces=()=>{select.replaceChildren();for(const place of groups.get(searches.value)?.places||[]){const option=make('option',place.label);option.value=place.value;select.append(option);}select.value=select.children[0]?.value||'';};
  populatePlaces();
  const use=make('button','Use this avatar · Take a selfie','compose-photo');use.type='button';use.disabled=!select.children.length&&this.isSignedIn();
  if(!select.children.length){select.hidden=true;section.append(make('p','No saved photo locations yet. Search for a place on the map first, then return here.','small'));}
  use.onclick=async()=>{if(!current()||this.requireSignIn?.()===false)return;use.disabled=true;save.disabled=true;status.textContent='Preparing your avatar…';try{const file=await this.avatarFile(item,mine);if(!current())return;const place=select.value==='current'?null:places[Number(select.value)];if(!place&&!this.canSelect?.())throw Error('The current selfie is unavailable. Choose another location.');await this.onSelect(file,place);if(!current())return;dialog.close();if(root===this.dialog)this.dialog.close();else this.menu.open=false;}catch(error){if(current())status.textContent=error.message;}finally{use.disabled=!select.children.length&&this.isSignedIn();save.disabled=false;}};
  const preview=make('section',null,'avatar-place-preview');preview.setAttribute('aria-label','Selected photo location');
  const updatePreview=()=>{preview.replaceChildren();if(!select.children.length||!this.renderPlacePreview){preview.hidden=true;return;}preview.hidden=false;this.renderPlacePreview(select.value==='current'?null:places[Number(select.value)],preview);};
  select.addEventListener('change',updatePreview);
  searches.addEventListener('change',()=>{populatePlaces();updatePreview();});searches.hidden=!groups.size;
  section.prepend(subtitle);section.append(searches,select,preview,use);updatePreview();dialog.append(heading,image,intro,meta,form,note,section,status);if(!dialog.open)dialog.showModal();
 }
 async saveUpload(file,prepared){
  const session=this.session(),identity=session.id,epoch=session.epoch;
  const item=await this.request('/photo-scout/v1/avatars',{method:'POST',headers:this.headers(),body:JSON.stringify({name:file.name.replace(/\.[^.]+$/,'').slice(0,80)||'My avatar',image:prepared.data})});
  const current=this.session();if(identity!==current.id||epoch!==current.epoch)return null;
  this.items=[item,...this.items.filter(existing=>existing.id!==item.id)];
  if(this.tab==='mine'){if(this.menu.open)this.render(this.panel);if(this.dialog.open)this.render(this.dialog);}
  return item;
 }
 headers(){return {'Content-Type':'application/json',...(this.session().csrf?{'X-CSRF-Token':this.session().csrf}:{})};}
 async rasterize(blob){const image=new Image(),url=URL.createObjectURL(blob);try{await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error('Could not load this character'));image.src=url;});const canvas=document.createElement('canvas');canvas.width=canvas.height=768;const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,768,768);context.drawImage(image,0,0,768,768);return await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('Could not prepare this character')),'image/png'));}finally{URL.revokeObjectURL(url);}}
};
