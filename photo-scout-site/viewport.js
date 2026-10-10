/* Mobile browsers keep a layout viewport behind the keyboard. Anchor overlays
   to the visible viewport without resizing or resetting the map. */
(function(){
 'use strict';
 function update(){
  const viewport=window.visualViewport;
  const height=viewport?.height||window.innerHeight;
  const inset=viewport?Math.max(0,window.innerHeight-height-viewport.offsetTop):0;
  document.documentElement.style.setProperty('--scout-visible-height',height+'px');
  document.documentElement.style.setProperty('--scout-keyboard-inset',inset+'px');
  document.body.dataset.keyboardOpen=String(inset>120);
 }
 window.addEventListener('resize',update,{passive:true});
 window.addEventListener('pageshow',update,{passive:true});
 window.visualViewport?.addEventListener('resize',update,{passive:true});
 window.visualViewport?.addEventListener('scroll',update,{passive:true});
 update();
})();
