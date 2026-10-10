const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
function fixture(){
 const element=(tag,text='',className='')=>({tag,textContent:text??'',className,children:[],dataset:{},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;}});
 const calls=[],published=new Map(),sharing=element('details'),file={name:'selfie.png'};
 const c={node:element,link:(label,url)=>({...element('a',label),href:url}),savedPhotoGeneration:1,savedPhotoFile:file,savedPhotoSharing:sharing,photoShareDialog:element("dialog"),savedPhoto:{dataset:{photoId:'photo-1'}},navigator:{canShare:()=>true,share:async data=>{calls.push(data);},clipboard:{writeText:async text=>calls.push(text)}},location:{origin:'https://aisoup.net'},ownPublications:published,publicationKey:(k,id)=>k+':'+id,csrfToken:null,json:async(path,opts)=>{calls.push(JSON.parse(opts.body));return {id:'pub-1',url:'https://aisoup.net/photo-scout/?published=pub-1'};},syncPublishButtons(){}};
 const source=readFileSync('photo-scout-site/app.js','utf8');vm.runInNewContext(source.slice(source.indexOf('function photoSocialLinks('),source.indexOf("const publicationDialog=")),c);
 c.renderPhotoSharing({name:'Lake & art'});return {c,calls,sharing,file,buttons:()=>{const b=c.photoShareDialog.children[1].children;return [b[0].children[1],b[0].children[0],...b.slice(1)];}};
}
test('native image sharing keeps an unpublished photo private',async()=>{
 const f=fixture();await f.buttons()[0].onclick();assert.equal(f.calls.length,1);assert.equal(f.calls[0].files[0],f.file);assert.equal(f.c.ownPublications.size,0);assert.equal(f.calls[0].url,undefined);
});
test('public link sharing is an explicit publish action and URLs preserve query text',async()=>{
 const f=fixture();assert.equal(f.buttons()[2].children.length,2);assert.equal(f.buttons()[2].children[0].tag,"button");await f.buttons()[4].onclick();assert.equal(f.calls[0].kind,'photo');assert.equal(f.calls[0].id,'photo-1');
 const anchors=f.buttons()[2].children;assert.equal(anchors.length,2);assert.equal(new URL(anchors[0].href).searchParams.get('u'),'https://aisoup.net/photo-scout/?published=pub-1');assert.match(new URL(anchors[1].href).searchParams.get('text'),/Lake & art/);
});
test('a stale photo share control cannot share the newly opened photo',async()=>{
 const f=fixture();f.c.savedPhotoGeneration=2;f.c.savedPhotoFile={name:'other.png'};await f.buttons()[0].onclick();assert.equal(f.calls.length,0);
});
test('WeChat without native sharing gives a download fallback without publishing',async()=>{
 const f=fixture();f.c.navigator.share=undefined;await f.buttons()[1].onclick();assert.equal(f.calls.length,0);assert.match(f.buttons()[6].textContent,/open WeChat/);
});
test('native share cancellation is not reported as a failed share',async()=>{
 const f=fixture();f.c.navigator.share=async()=>{throw {name:'AbortError'};};await f.buttons()[0].onclick();assert.equal(f.buttons()[6].textContent,'');
});

test('Share focuses a separate dialog without an inline accordion',()=>{const f=fixture();f.sharing.onclick();assert.equal(f.c.photoShareDialog.open,true);assert.equal(f.buttons()[1].focused,true);});
test('unpublished Facebook click opens a composer after publishing without another app explanation step',async()=>{
 const f=fixture(),destinations=[];const target={location:{replace:url=>destinations.push(url)},close(){this.closed=true;}};f.c.window={open:(url)=>{assert.equal(url,'about:blank');return target;},location:{assign:url=>destinations.push(url)}};await f.buttons()[2].children[0].onclick();assert.equal(f.calls[0].kind,'photo');assert.equal(f.calls[0].id,'photo-1');assert.equal(new URL(destinations[0]).hostname,'www.facebook.com');assert.equal(new URL(destinations[0]).searchParams.get('u'),'https://aisoup.net/photo-scout/?published=pub-1');assert.equal(target.opener,null);
});
test('publication failure closes the pending share tab and never navigates to a bad social link',async()=>{
 const f=fixture(),target={close(){this.closed=true;},location:{replace(){assert.fail('must not navigate');}}};f.c.window={open:()=>target};f.c.json=async()=>{throw Error('Photo unavailable');};await f.buttons()[2].children[1].onclick();assert.equal(target.closed,true);assert.equal(f.buttons()[6].textContent,'Photo unavailable');
});
