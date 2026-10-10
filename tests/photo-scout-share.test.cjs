const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const source=readFileSync('photo-scout-site/app.js','utf8');
function fixture(studio=false){
 const button={addEventListener(type,fn){this.click=fn;},disabled:false},hint={textContent:''},image={scrollIntoView(){this.scrolled=true;}},file={name:'photo.png',type:'image/png'},calls=[];
 const c={navigator:{canShare:()=>true,share:async data=>calls.push(data)},studioSave:button,studioSaveHint:hint,studioResult:image,studioOutputFile:file,savedPhotoSave:button,savedPhotoHint:hint,savedPhotoImage:image,savedPhotoFile:file};
 const start=source.indexOf(studio?"studioSave.addEventListener('click'":"savedPhotoSave.addEventListener('click'");const end=source.indexOf(studio?'async function submitDurableSearch':'async function viewSavedTask',start);vm.runInNewContext(source.slice(start,end),c);return {c,button,hint,image,file,calls};
}
for(const studio of [false,true]){
 test(`${studio?'generated':'saved'} photo shares prepared image through one system menu`,async()=>{const f=fixture(studio);let started=false;f.c.navigator.share=data=>{started=true;f.calls.push(data);return Promise.resolve();};const pending=f.button.click();assert.equal(started,true);await pending;assert.equal(f.calls.length,1);assert.equal(f.calls[0].files[0],f.file);assert.deepEqual(Object.keys(f.calls[0]),['files']);assert.equal(f.button.disabled,false);});
 test(`${studio?'generated':'saved'} photo cancel keeps file and reenables button`,async()=>{const f=fixture(studio);f.c.navigator.share=async()=>{throw Object.assign(Error('cancel'),{name:'AbortError'});};await f.button.click();assert.equal(f.button.disabled,false);assert.equal(studio?f.c.studioOutputFile:f.c.savedPhotoFile,f.file);assert.doesNotMatch(f.hint.textContent,/could not open/);});
 test(`${studio?'generated':'saved'} unsupported browser offers download without public link`,async()=>{const f=fixture(studio);f.c.navigator.canShare=()=>false;await f.button.click();assert.equal(f.calls.length,0);assert.match(f.hint.textContent,/Download PNG/);assert.equal(f.image.scrolled,true);});
}
test('only system Share remains alongside explicit publication',()=>{assert.match(source,/savedPhotoSave=node\('button','Share'/);assert.match(source,/studioSave=node\('button','Share'/);assert.doesNotMatch(source,/photoShareDialog|socialShareIcon|photoSocialLinks|Send to/);assert.match(source,/const savedPhotoPublish=/);});
