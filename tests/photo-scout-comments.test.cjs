const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const {readFileSync}=require('node:fs');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.hidden=false;this.value='';this.events={};}
 append(...children){for(const c of children){c.parent=this;this.children.push(c);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(){} addEventListener(event,fn){this.events[event]=fn;}
 remove(){this.parent.children=this.parent.children.filter(c=>c!==this);}
}
function fixture(options={}){const context={window:{},document:{createElement:tag=>new Element(tag)},queueMicrotask,URLSearchParams,Date};vm.runInNewContext(readFileSync('photo-scout-site/comments.js','utf8'),context);const requests=[];const box=context.window.PhotoScoutComments.create({resolve:()=>({id:'pub',poi:'lake'}),request:async(path,opts)=>{requests.push({path,opts});return path.endsWith('/social')?{threads:{},canReact:true}:{items:[],canComment:true,nextBefore:null};},headers:()=>({'X-CSRF-Token':'csrf'}),signIn(){},...options});return {box:box.thread,root:box,requests};}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('compact threads load only when opened, and text is never interpreted as HTML',async()=>{
 const {box,requests}=fixture({compact:true,request:async(path,opts)=>{requests.push({path,opts});if(path.endsWith('/social'))return {threads:{},canReact:true};return {items:[{id:'one',name:'<img>',text:'<script>bad</script>',created:1,canDelete:true}],canComment:true};}});
 await settle();assert.equal(requests.length,1);box.open=true;box.events.toggle();await settle();
 assert.equal(requests.length,2);assert.match(requests[1].path,/poiId=lake/);
 const list=box.children.find(c=>c.className==='comment-list');assert.equal(list.children[0].children[1].textContent,'<script>bad</script>');assert.equal(list.children[0].children[1].children.length,0);
 await list.children[0].children[2].onclick();assert.equal(requests[2].opts.method,'DELETE');assert.equal(requests[2].opts.headers['X-CSRF-Token'],'csrf');assert.equal(list.children.length,0);
});
test('posting uses the thread scope, CSRF and only clears a draft after success',async()=>{
 const {box,requests}=fixture();await settle();const form=box.children.find(c=>c.tag==='form');form.children[0].value='  Nice view!  ';
 await form.onsubmit({preventDefault(){}});assert.equal(requests.find(r=>r.opts?.method==='POST').opts.method,'POST');assert.equal(requests.find(r=>r.opts?.method==='POST').opts.headers['X-CSRF-Token'],'csrf');assert.deepEqual(JSON.parse(requests.find(r=>r.opts?.method==='POST').opts.body),{text:'Nice view!',poiId:'lake'});assert.equal(form.children[0].value,'');
 const failure=fixture({request:async()=>{throw Error('Offline');}});await settle();const failedForm=failure.box.children.find(c=>c.tag==='form');failedForm.children[0].value='Keep my draft';await failedForm.onsubmit({preventDefault(){}});assert.equal(failedForm.children[0].value,'Keep my draft');
});
test('unpublishing hides a thread and late responses cannot restore it',async()=>{
 let target={id:'pub',poi:''},pending;const {box}=fixture({resolve:()=>target,request:path=>path.endsWith('/social')?Promise.resolve({threads:{},canReact:false}):new Promise(resolve=>pending=resolve)});await settle();target=null;box.refreshComments();pending({items:[{name:'Old',text:'Old comment',created:1}],canComment:true});await settle();
 assert.equal(box.children.find(c=>c.tag==='form').hidden,true);assert.equal(box.children.find(c=>c.className==='comment-list').children.length,0);assert.match(box.children.find(c=>c.className==='comment-status').textContent,/Publish/);
});

test('likes and private saves toggle reversibly using explicit idempotent state',async()=>{
 const requests=[],states={likes:0,liked:false,favorited:false};const {root}=fixture({request:async(path,options)=>{requests.push({path,options});if(path.endsWith('/reactions')){const body=JSON.parse(options.body);assert.equal(body.poiId,'lake');if(body.kind==='like'){states.likes=body.active?1:0;states.liked=body.active;}else states.favorited=body.active;}return {threads:{lake:{...states}},canReact:true,items:[],canComment:true};}});await settle();const [like,favorite]=root.children[0].children;
 await like.onclick();assert.match(like.textContent,/♥ Liked · 1/);await like.onclick();assert.equal(like.textContent,'♡ Like');await favorite.onclick();assert.equal(favorite.textContent,'★ Saved');await favorite.onclick();assert.equal(favorite.textContent,'☆ Save');assert.equal(requests.filter(r=>r.options?.method==='POST').length,4);
});

test('place and search widgets keep likes and saves without loading or displaying comments',async()=>{
 const {root,box,requests}=fixture({commentsEnabled:false,compact:true});await settle();
 assert.equal(box.hidden,true);assert.equal(requests.length,1);assert.match(requests[0].path,/\/social$/);
 box.open=true;box.events.toggle();await settle();assert.equal(requests.length,1);
 assert.equal(root.children[0].children.length,2);
});
