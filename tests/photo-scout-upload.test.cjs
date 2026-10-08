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
