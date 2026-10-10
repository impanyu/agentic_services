const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('photo-scout-site/viewport.js','utf8');
function setup(viewport){
 const properties={},events={},dataset={};
 const window={innerWidth:393,innerHeight:800,visualViewport:viewport,addEventListener:(name,fn)=>events[name]=fn};
 const document={activeElement:{matches:()=>true},addEventListener:(name,fn)=>events[name]=fn,documentElement:{clientWidth:393,clientHeight:800,style:{setProperty:(key,value)=>properties[key]=value}},body:{dataset}};
 vm.runInNewContext(source,{window,document});
 return {properties,events,dataset,window,document};
}
test('keyboard and viewport scrolling keep the composer above the obscured area',()=>{
 const callbacks={};
 const viewport={height:800,offsetTop:0,addEventListener:(name,fn)=>callbacks[name]=fn};
 const s=setup(viewport);
 assert.equal(s.properties['--scout-keyboard-inset'],'0px');
 viewport.height=450;viewport.offsetTop=20;callbacks.resize();
 assert.equal(s.properties['--scout-visible-height'],'450px');
 assert.equal(s.properties['--scout-keyboard-inset'],'330px');
 assert.equal(s.dataset.keyboardOpen,'true');
 viewport.height=800;viewport.offsetTop=0;callbacks.scroll();
 assert.equal(s.properties['--scout-keyboard-inset'],'0px');
 assert.equal(s.dataset.keyboardOpen,'false');
});
test('browsers without VisualViewport and restored pages remain usable',()=>{
 const s=setup(undefined);
 assert.equal(s.properties['--scout-visible-height'],'800px');
 s.window.innerHeight=640;s.events.pageshow();
 assert.equal(s.properties['--scout-visible-height'],'640px');
 assert.equal(s.properties['--scout-keyboard-inset'],'0px');
});

test('Safari layout viewport remains larger than innerHeight after browser UI/keyboard changes',()=>{
 const viewport={width:393,height:550,offsetTop:0,offsetLeft:0,scale:1,addEventListener:()=>{}};
 const s=setup(viewport);
 s.window.innerHeight=550;s.events.resize();
 assert.equal(s.properties['--scout-visible-bottom'],'250px');
 assert.equal(s.properties['--scout-keyboard-inset'],'250px');
});
test('zoom and pan produce visible-screen bounds, not a false keyboard state',()=>{
 const viewport={width:300,height:440,offsetTop:25,offsetLeft:30,scale:1.3,addEventListener:()=>{}};
 const s=setup(viewport);
 assert.equal(s.properties['--scout-visible-width'],'300px');
 assert.equal(s.properties['--scout-visible-left'],'30px');
 assert.equal(s.properties['--scout-visible-right'],'63px');
 assert.equal(s.properties['--scout-visible-bottom'],'335px');
 assert.equal(s.dataset.keyboardOpen,'false');
 assert.equal(s.dataset.compactViewport,'true');
});
test('restoring an unfocused page does not pretend the keyboard is open',()=>{
 const s=setup({width:393,height:550,offsetTop:0,scale:1,addEventListener:()=>{}});
 s.document.activeElement=null;s.events.focusout();
 assert.equal(s.dataset.keyboardOpen,'false');
});
