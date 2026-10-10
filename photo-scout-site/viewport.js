/* Overlay coordinates follow the visible viewport, including Safari keyboard,
   browser chrome and zoom/pan. Keep the map's layout and camera unchanged. */
(function(){
 'use strict';
 function update(){
  const viewport=window.visualViewport,root=document.documentElement;
  const height=viewport?.height||window.innerHeight;
  const width=viewport?.width||window.innerWidth;
  const top=viewport?.offsetTop||0,left=viewport?.offsetLeft||0;
  // Safari's innerHeight may already shrink while fixed elements still use the
  // larger layout viewport. Measuring only innerHeight can put the dock offscreen.
  const layoutHeight=viewport?Math.max(root.clientHeight||0,window.innerHeight):window.innerHeight;
  const layoutWidth=viewport?Math.max(root.clientWidth||0,window.innerWidth):window.innerWidth;
  const bottom=Math.max(0,layoutHeight-height-top),right=Math.max(0,layoutWidth-width-left);
  for(const [name,value] of Object.entries({height,width,top,left,right,bottom}))root.style.setProperty('--scout-visible-'+name,value+'px');
  root.style.setProperty('--scout-keyboard-inset',bottom+'px');
  const focused=document.activeElement;
  const editing=focused?.matches?.('input:not([type=checkbox]):not([type=radio]),textarea,select');
  document.body.dataset.keyboardOpen=String(Boolean(editing&&bottom>120&&(viewport?.scale||1)<1.1));
  document.body.dataset.compactViewport=String(height<500);
 }
 window.addEventListener('resize',update,{passive:true});
 window.addEventListener('pageshow',update,{passive:true});
 document.addEventListener('focusin',update);
 document.addEventListener('focusout',update);
 window.visualViewport?.addEventListener('resize',update,{passive:true});
 window.visualViewport?.addEventListener('scroll',update,{passive:true});
 update();
})();
