const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('photo-scout-site/updates.js','utf8');
function setup(busy){
 const notices=[],listeners={};let reloads=0,now=20000;
 const node=()=>({append(...children){this.children=children},setAttribute(){}});
 const document={hidden:false,querySelector:()=>({content:'aaaaaaaaaaaaaaaa'}),createElement:node,body:{append:n=>notices.push(n)},addEventListener:(type,fn)=>listeners[type]=fn};
 const window={addEventListener:(type,fn)=>listeners[type]=fn};
 const context={window,document,location:{reload:()=>reloads++},Date:{now:()=>now},setInterval(){},confirm:()=>false,fetch:async()=>({ok:true,json:async()=>({version:'bbbbbbbbbbbbbbbb'})})};
 vm.runInNewContext(source,context);window.PhotoScoutUpdates.start(busy);
 return {notices,listeners,context,reloads:()=>reloads,advance:()=>now+=20000};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('idle foreground update reloads the app',async()=>{const app=setup(()=>false);await flush();assert.equal(app.reloads(),1);assert.equal(app.notices.length,0)});
test('active editor gets one notice and cannot lose edits without confirmation',async()=>{const app=setup(()=>true);await flush();assert.equal(app.reloads(),0);assert.equal(app.notices.length,1);app.advance();await app.listeners.focus();assert.equal(app.notices.length,1);const button=app.notices[0].children[1];button.onclick();assert.equal(app.reloads(),0);app.context.confirm=()=>true;button.onclick();assert.equal(app.reloads(),1)});
