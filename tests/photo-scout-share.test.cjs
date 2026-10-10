const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
function fixture(){
 const element=(tag,text='',className='')=>({tag,textContent:text??'',className,children:[],dataset:{},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(){}});
 const calls=[],published=new Map(),sharing=element('details'),file={name:'selfie.png'};
 const c={node:element,link:(label,url)=>({...element('a',label),href:url}),savedPhotoGeneration:1,savedPhotoFile:file,savedPhotoSharing:sharing,savedPhoto:{dataset:{photoId:'photo-1'}},navigator:{canShare:()=>true,share:async data=>{calls.push(data);},clipboard:{writeText:async text=>calls.push(text)}},location:{origin:'https://aisoup.net'},ownPublications:published,publicationKey:(k,id)=>k+':'+id,csrfToken:null,json:async(path,opts)=>{calls.push(JSON.parse(opts.body));return {id:'pub-1',url:'https://aisoup.net/photo-scout/?published=pub-1'};},syncPublishButtons(){}};
 const source=readFileSync('photo-scout-site/app.js','utf8');vm.runInNewContext(source.slice(source.indexOf('function photoSocialLinks('),source.indexOf("const publicationDialog=")),c);
 c.renderPhotoSharing({name:'Lake & art'});return {c,calls,sharing,file,buttons:()=>sharing.children[1].children};
}
test('native image sharing keeps an unpublished photo private',async()=>{
 const f=fixture();await f.buttons()[0].onclick();assert.equal(f.calls.length,1);assert.equal(f.calls[0].files[0],f.file);assert.equal(f.c.ownPublications.size,0);assert.equal(f.calls[0].url,undefined);
});
test('public link sharing is an explicit publish action and URLs preserve query text',async()=>{
 const f=fixture();assert.equal(f.buttons()[2].children.length,0);await f.buttons()[4].onclick();assert.equal(f.calls[0].kind,'photo');assert.equal(f.calls[0].id,'photo-1');
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
