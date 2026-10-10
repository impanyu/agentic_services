const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('photo-scout-site/viewport.js','utf8');
function setup(viewport){
 const properties={},events={},dataset={};
 const window={innerHeight:800,visualViewport:viewport,addEventListener:(name,fn)=>events[name]=fn};
 const document={documentElement:{style:{setProperty:(key,value)=>properties[key]=value}},body:{dataset}};
 vm.runInNewContext(source,{window,document});
 return {properties,events,dataset,window};
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
