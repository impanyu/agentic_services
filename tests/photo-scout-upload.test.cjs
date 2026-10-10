const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
function fixture(width,height,decode=true){
 const canvas={getContext:()=>({fillRect(){},drawImage(){}}),toDataURL:(type,quality)=>{assert.equal(type,'image/jpeg');assert.equal(quality,.92);return 'data:image/jpeg;base64,prepared';}};
 class Image{naturalWidth=width;naturalHeight=height;set src(value){queueMicrotask(()=>decode?this.onload():this.onerror());}}
 class FileReader{readAsDataURL(){this.result='data:image/heic;base64,original';this.onload();}}
 const source=readFileSync('photo-scout-site/app.js','utf8'),context={Image,FileReader,document:{createElement:()=>canvas}};
 vm.runInNewContext(source.slice(source.indexOf('async function prepareStudioPhoto('),source.indexOf("studioGenerate.addEventListener('click'")),context);
 return {canvas,prepare:context.prepareStudioPhoto};
}
test('48MP phone image is resized before upload and a decoded preview is used',async()=>{
 const f=fixture(8064,6048),result=await f.prepare({});assert.equal(f.canvas.width,2048);assert.equal(f.canvas.height,1536);assert.equal(result.preview,true);assert.equal(result.data,'data:image/jpeg;base64,prepared');
});
test('unsupported browser image codec retains the file for server conversion without broken preview',async()=>{
 const result=await fixture(0,0,false).prepare({});assert.equal(result.preview,false);assert.equal(result.data,'data:image/heic;base64,original');
});
test('oversized decoded image fails visibly before submission',async()=>{
 await assert.rejects(fixture(10000,10000).prepare({}),/80 megapixels/);
});
function draftFixture(){const source=readFileSync('photo-scout-site/app.js','utf8'),c={studioSearchId:'search-a',studioInputPlaceKey:JSON.stringify(['search-a','poi-a',1,2,'Park']),studioUploadGeneration:5,studioFile:{name:'old-selfie.png'},studioPrepared:'old-photo',studioUpload:{value:'old'},personPreview:{hidden:false,removeAttribute(){}},studioGenerate:{disabled:false},studioPreviewUrl:null};vm.runInNewContext(source.slice(source.indexOf('function resetStudioInputForPlace('),source.indexOf('function openPhotoStudio(')),c);return c;}
test('changing the photo background clears the previous portrait and invalidates pending preparation',()=>{const c=draftFixture();c.resetStudioInputForPlace({sourceUrl:'new'});assert.equal(c.studioFile,null);assert.equal(c.studioPrepared,null);assert.equal(c.studioUpload.value,'');assert.equal(c.personPreview.hidden,true);assert.equal(c.studioGenerate.disabled,true);assert.equal(c.studioUploadGeneration,6);});
test('reopening the same scene retains the explicitly chosen portrait draft',()=>{const c=draftFixture();c.resetStudioInputForPlace({poi:{id:'poi-a',lat:1,lon:2,name:'Park'},sourceUrl:'old'});assert.equal(c.studioPrepared,'old-photo');assert.equal(c.studioUploadGeneration,5);});

test('different POIs sharing one panorama cannot reuse an uploaded portrait',()=>{const c=draftFixture();c.resetStudioInputForPlace({poi:{id:'poi-b',lat:1,lon:2,name:'Park'},sourceUrl:'old'});assert.equal(c.studioPrepared,null);assert.equal(c.personPreview.hidden,true);});
test('camera adjustment at the same POI retains its portrait, but another search cannot reuse it',()=>{const c=draftFixture();const spot={poi:{id:'poi-a',lat:1,lon:2,name:'Park'},sourceUrl:'different-heading'};c.resetStudioInputForPlace(spot);assert.equal(c.studioPrepared,'old-photo');c.studioSearchId='search-b';c.resetStudioInputForPlace(spot);assert.equal(c.studioPrepared,null);});
