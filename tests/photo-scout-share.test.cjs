const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
function fixture(){
 const element=(tag,text='',className='')=>({tag,textContent:text??'',className,children:[],dataset:{},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;}});
 const calls=[],destinations=[],published=new Map(),sharing=element('button'),target={location:{replace:url=>destinations.push(url)},close(){this.closed=true;}};
 const c={Blob,node:element,link:(label,url)=>({...element('a',label),href:url}),savedPhotoGeneration:1,savedPhotoFile:{name:'selfie.png'},savedPhotoImage:{src:'data:image/png;base64,preview'},savedPhotoSharing:sharing,photoShareDialog:element('dialog'),savedPhoto:{dataset:{photoId:'photo-1'}},navigator:{share(){assert.fail('unsupported browsers must not use file sharing');},clipboard:{writeText:async text=>calls.push({clipboard:text})}},window:{open:()=>target,location:{assign:url=>destinations.push(url)}},location:{origin:'https://aisoup.net'},ownPublications:published,publicationKey:(k,id)=>k+':'+id,csrfToken:null,json:async(path,opts)=>{calls.push(JSON.parse(opts.body));return {id:'pub-1',url:'https://aisoup.net/photo-scout/?published=pub-1'};},syncPublishButtons(){}};
 const source=readFileSync('photo-scout-site/app.js','utf8');vm.runInNewContext(source.slice(source.indexOf('function socialShareIcon('),source.indexOf('const publicationDialog=')),c);c.renderPhotoSharing({name:'Lake & art'});
 const body=c.photoShareDialog.children[2];return {c,calls,destinations,target,sharing,buttons:body.children[0].children,note:body.children[1],status:body.children[2]};
}
test('social sheet has only WeChat, Facebook and X without duplicate export/publish actions',()=>{const f=fixture();assert.deepEqual(f.buttons.map(b=>b.children[1].textContent),['WeChat','Facebook','X']);assert.equal(f.c.photoShareDialog.children[2].children.length,3);assert.match(f.note.textContent,/publishes this photo/);});
test('Share opens the focused three-destination dialog',()=>{const f=fixture();f.sharing.onclick();assert.equal(f.c.photoShareDialog.open,true);assert.equal(f.buttons[0].focused,true);});
test('Facebook publishes a private photo then opens its composer in the reserved tab',async()=>{const f=fixture();await f.buttons[1].onclick();assert.deepEqual(f.calls[0],{kind:'photo',id:'photo-1'});assert.equal(new URL(f.destinations[0]).searchParams.get('u'),'https://aisoup.net/photo-scout/?published=pub-1');assert.equal(f.target.opener,null);});
test('X preserves the public URL and photo text',async()=>{const f=fixture();await f.buttons[2].onclick();assert.equal(new URL(f.destinations[0]).hostname,'x.com');assert.match(new URL(f.destinations[0]).searchParams.get('text'),/Lake & art/);});
test('an already published photo shares without creating another publication',async()=>{const f=fixture();f.c.ownPublications.set('photo:photo-1',{url:'https://aisoup.net/photo-scout/?published=existing'});await f.buttons[1].onclick();assert.equal(f.calls.length,0);assert.equal(new URL(f.destinations[0]).searchParams.get('u'),'https://aisoup.net/photo-scout/?published=existing');});
test('WeChat fallback copies the link without opening an empty app',async()=>{const f=fixture();await f.buttons[0].onclick();assert.equal(f.calls[1].clipboard,'https://aisoup.net/photo-scout/?published=pub-1');assert.deepEqual(f.destinations,[]);assert.match(f.status.textContent,/paste it/);});
test('WeChat initiates promised ClipboardItem inside the click before publication finishes',async()=>{const f=fixture();let resolve,clipboardStarted=false;f.c.json=()=>new Promise(r=>resolve=r);f.c.ClipboardItem=class{constructor(data){this.data=data;}};f.c.navigator.clipboard.write=async items=>{clipboardStarted=true;const blob=await items[0].data['text/plain'];assert.equal(await blob.text(),'https://aisoup.net/photo-scout/?published=late');};const pending=f.buttons[0].onclick();assert.equal(clipboardStarted,true);resolve({url:'https://aisoup.net/photo-scout/?published=late'});await pending;assert.deepEqual(f.destinations,[]);});
test('clipboard failure provides manual link and does not open WeChat empty-handed',async()=>{const f=fixture();f.c.navigator.clipboard.writeText=async()=>{throw Error('denied');};await f.buttons[0].onclick();assert.equal(f.destinations.length,0);assert.equal(f.status.children[1].href,'https://aisoup.net/photo-scout/?published=pub-1');});
test('publication failure closes the reserved tab and never navigates to a bad social link',async()=>{const f=fixture();f.c.json=async()=>{throw Error('Photo unavailable');};await f.buttons[2].onclick();assert.equal(f.target.closed,true);assert.equal(f.status.textContent,'Photo unavailable');assert.equal(f.destinations.length,0);});
test('stale share controls cannot publish or share a newly opened photo',async()=>{const f=fixture();f.c.savedPhotoGeneration=2;await f.buttons[0].onclick();assert.equal(f.calls.length,0);assert.equal(f.destinations.length,0);});

for(const [index,label] of [[0,'WeChat'],[1,'Facebook']]){
 test(label+' shares the prepared PNG inside the click without publishing or empty app navigation',async()=>{
  const f=fixture();let resolve,shared;
  f.c.navigator.canShare=data=>data.files?.[0]===f.c.savedPhotoFile;
  f.c.navigator.share=data=>{shared=data;return new Promise(r=>resolve=r);};
  const pending=f.buttons[index].onclick();
  assert.equal(shared.files[0],f.c.savedPhotoFile);
  assert.equal(f.calls.length,0);assert.equal(f.destinations.length,0);
  assert.match(f.status.textContent,new RegExp('Choose '+label));
  resolve();await pending;
  assert.match(f.status.textContent,/Finish sending or posting/);
  assert.equal(f.calls.length,0);
 });
 test(label+' canceled file sharing does not publish or fall through to another share flow',async()=>{
  const f=fixture();f.c.navigator.canShare=()=>true;
  f.c.navigator.share=async()=>{throw Object.assign(new Error('canceled'),{name:'AbortError'});};
  await f.buttons[index].onclick();
  assert.equal(f.calls.length,0);assert.equal(f.destinations.length,0);
  assert.match(f.status.textContent,/Sharing canceled/);assert.equal(f.buttons[index].disabled,false);
 });
}
test('file share failure does not claim a post succeeded or open an empty app',async()=>{
 const f=fixture();f.c.navigator.canShare=()=>true;f.c.navigator.share=async()=>{throw new Error('denied');};
 await f.buttons[1].onclick();assert.match(f.status.textContent,/Could not share/);
 assert.equal(f.calls.length,0);assert.equal(f.destinations.length,0);
});
